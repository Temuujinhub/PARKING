"""Орох/гарах урсгалын гол логик — LPR event-ээс session үүсгэх, хаах, barrier нээх."""
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from .billing import calculate_fee
from .config import settings
from .models import (
    BlacklistEntry, Device, LprEvent, ParkingSession, ParkingSite, Payment,
    RegisteredDriver,
)
from .services.barrier import open_barrier
from .ws import manager


import re

# Монгол улсын дугаарын формат: 4 орон + 3 кирилл үсэг (Ө, Ү орно). Жишээ: 1234УБА
PLATE_RE = re.compile(r"^\d{4}[А-ЯЁӨҮ]{3}$")


def normalize_plate(plate: str) -> str:
    return (plate or "").upper().replace(" ", "").replace("-", "").strip()


def is_valid_plate(plate: str) -> bool:
    return bool(PLATE_RE.match(normalize_plate(plate)))


def find_registered(db: Session, plate: str, site_id: str) -> RegisteredDriver | None:
    now = datetime.utcnow()
    q = (
        db.query(RegisteredDriver)
        .filter(
            RegisteredDriver.plate_number == plate,
            RegisteredDriver.is_active.is_(True),
            RegisteredDriver.valid_from <= now,
            RegisteredDriver.valid_to >= now,
        )
    )
    return q.filter((RegisteredDriver.site_id == site_id) | (RegisteredDriver.site_id.is_(None))).first()


def is_blacklisted(db: Session, plate: str) -> BlacklistEntry | None:
    return (
        db.query(BlacklistEntry)
        .filter(BlacklistEntry.plate_number == plate, BlacklistEntry.is_active.is_(True))
        .first()
    )


def get_open_session(db: Session, plate: str, site_id: str) -> ParkingSession | None:
    return (
        db.query(ParkingSession)
        .filter(
            ParkingSession.plate_number == plate,
            ParkingSession.site_id == site_id,
            ParkingSession.status.in_(["OPEN", "AWAITING_PAYMENT", "PAID"]),
        )
        .order_by(ParkingSession.entry_time.desc())
        .first()
    )


def session_fee_info(db: Session, s: ParkingSession, at: datetime | None = None) -> dict:
    site: ParkingSite = s.site
    template = site.tariff_template if site else None
    return calculate_fee(
        template, s.entry_time, at or s.exit_time or datetime.utcnow(),
        discount=s.discount, is_registered=s.is_registered,
    )


def paid_total(db: Session, s: ParkingSession) -> float:
    """Session-д аль хэдийн төлөгдсөн нийт дүн (PAID төлбөрүүдийн нийлбэр)."""
    rows = db.query(Payment.amount).filter(Payment.session_id == s.id,
                                           Payment.status == "PAID").all()
    return float(sum(float(r[0]) for r in rows))


def amount_due(db: Session, s: ParkingSession, fee: dict) -> float:
    """Одоо төлөх ёстой үлдэгдэл: нийт тооцоолсон дүнгээс төлснийг хассан.
    Grace хугацаа хэтэрч дахин тооцоход өмнөх төлбөрийг ДАВХАРДУУЛЖ нэхэхгүй."""
    return max(0.0, round(fee["total_fee"] - paid_total(db, s), 2))


async def handle_entry(db: Session, device: Device, plate: str, confidence: float, raw: dict) -> dict:
    """Орох камерын event: session нээж, barrier нээнэ (blacklist биш бол)."""
    site_id = device.site_id
    now = datetime.utcnow()

    # Давхар event хамгаалалт
    recent = (
        db.query(LprEvent)
        .filter(
            LprEvent.plate_number == plate, LprEvent.site_id == site_id,
            LprEvent.lane_dir == "entry", LprEvent.accepted.is_(True),
            LprEvent.created_at >= now - timedelta(seconds=settings.lpr_dedup_seconds),
        ).first()
    )
    if recent:
        return {"action": "dedup", "plate": plate}

    black = is_blacklisted(db, plate)
    registered = find_registered(db, plate, site_id)

    existing = get_open_session(db, plate, site_id)
    if existing:
        session = existing  # давхар орох event — session хэвээр
    else:
        session = ParkingSession(
            site_id=site_id, plate_number=plate, entry_time=now,
            entry_device_id=device.id, confidence_entry=confidence,
            is_registered=registered is not None, status="OPEN",
        )
        db.add(session)
        db.flush()

    db.add(LprEvent(site_id=site_id, device_id=device.id, plate_number=plate,
                    lane_dir="entry", confidence=confidence, accepted=True, raw=raw))
    db.commit()

    barrier_opened = False
    if black:
        await manager.broadcast(site_id, "BLACKLIST_ALERT", {
            "plate": plate, "reason": black.reason, "lane": "entry",
        })
    elif device.auto_open:
        barrier = _find_barrier(db, site_id, device)
        if barrier:
            source = "whitelist" if registered else "auto_entry"
            cmd = await open_barrier(db, barrier, session.id, source, plate=plate)
            barrier_opened = cmd.status == "SUCCESS"

    await manager.broadcast(site_id, "ENTRY_EVENT", {
        "session_id": session.id, "plate": plate, "entry_time": session.entry_time.isoformat(),
        "registered": registered is not None, "blacklisted": black is not None,
        "barrier_opened": barrier_opened,
    })
    return {"action": "entry", "session_id": session.id, "barrier_opened": barrier_opened}


async def handle_exit(db: Session, device: Device, plate: str, confidence: float, raw: dict) -> dict:
    """Гарах камерын event:
    - Төлсөн (grace хугацаанд) эсвэл үнэгүй/гэрээт бол barrier нээж session хаана.
    - Үгүй бол AWAITING_PAYMENT болгож касс/PAX/QR руу мэдэгдэнэ.
    """
    site_id = device.site_id
    now = datetime.utcnow()

    # Давхар event хамгаалалт — камер нэг машиныг хэдэн секундын зайтай дахин
    # уншихад давхар broadcast/нээх команд явуулахгүй (орох талтай ижил дүрэм)
    recent = (
        db.query(LprEvent)
        .filter(
            LprEvent.plate_number == plate, LprEvent.site_id == site_id,
            LprEvent.lane_dir == "exit", LprEvent.accepted.is_(True),
            LprEvent.created_at >= now - timedelta(seconds=settings.lpr_dedup_seconds),
        ).first()
    )
    if recent:
        return {"action": "dedup", "plate": plate}

    session = get_open_session(db, plate, site_id)
    db.add(LprEvent(site_id=site_id, device_id=device.id, plate_number=plate,
                    lane_dir="exit", confidence=confidence, accepted=True, raw=raw))

    if not session:
        # Session олдсонгүй — оператор шийднэ (гараар нээх боломжтой)
        db.commit()
        await manager.broadcast(site_id, "EXIT_NO_SESSION", {"plate": plate})
        return {"action": "no_session", "plate": plate}

    session.exit_device_id = device.id
    session.confidence_exit = confidence

    fee = session_fee_info(db, session, at=now)

    # Төлчихсөн — grace хугацаа дотор гарч байна
    if session.status == "PAID":
        if not session.exit_deadline or now <= session.exit_deadline:
            return await _close_and_open(db, device, session, now, fee, source="auto_exit")
        # Grace хэтэрсэн — нэмэлт төлбөр шаардана (доор үлдэгдлээр шалгана)
        session.status = "AWAITING_PAYMENT"

    if fee["is_free"]:
        session.status = "PAID"  # үнэгүй тул шууд гаргана
        return await _close_and_open(db, device, session, now, fee, source="auto_exit")

    # Үлдэгдэл тооцох: өмнө нь төлсөн бол (grace хэтэрсэн тохиолдол) зөвхөн зөрүүг нэхнэ.
    # Тарифын шатлал ахиагүй бол зөрүү 0 — нэмэлт төлбөргүйгээр гаргана.
    due = amount_due(db, session, fee)
    if due <= 0 and session.paid_at:
        session.status = "PAID"
        return await _close_and_open(db, device, session, now, fee, source="auto_exit")

    # Төлбөртэй — төлбөр хүлээнэ
    session.status = "AWAITING_PAYMENT"
    session.duration_minutes = fee["duration_minutes"]
    session.base_fee = fee["base_fee"]
    session.vat_amount = fee["vat_amount"]
    session.total_fee = fee["total_fee"]
    db.commit()

    await manager.broadcast(site_id, "EXIT_LPR_EVENT", {
        "session_id": session.id, "plate": plate,
        "entry_time": session.entry_time.isoformat(),
        "duration_minutes": fee["duration_minutes"], "total_fee": fee["total_fee"],
        "amount_due": due,
    })
    return {"action": "awaiting_payment", "session_id": session.id,
            "total_fee": fee["total_fee"], "amount_due": due}


async def _close_and_open(db: Session, exit_device: Device, session: ParkingSession,
                          now: datetime, fee: dict, source: str) -> dict:
    session.exit_time = now
    session.duration_minutes = fee["duration_minutes"]
    if session.total_fee is None:
        session.base_fee = fee["base_fee"]
        session.vat_amount = fee["vat_amount"]
        session.total_fee = fee["total_fee"]
    session.status = "FREE" if (fee["is_free"] and not session.paid_at) else "CLOSED"

    barrier = _find_barrier(db, session.site_id, exit_device)
    barrier_opened = False
    if barrier:
        cmd = await open_barrier(db, barrier, session.id, source, plate=session.plate_number)
        barrier_opened = cmd.status == "SUCCESS"
    db.commit()

    await manager.broadcast(session.site_id, "EXIT_COMPLETED", {
        "session_id": session.id, "plate": session.plate_number,
        "status": session.status, "barrier_opened": barrier_opened,
        "total_fee": float(session.total_fee or 0),
    })
    return {"action": "exit_completed", "session_id": session.id, "barrier_opened": barrier_opened}


def _find_barrier(db: Session, site_id: str, near_device: Device) -> Device | None:
    """Тухайн lane-ийн barrier төхөөрөмжийг олно (ижил lane_no, эсвэл эхний barrier)."""
    q = db.query(Device).filter(
        Device.site_id == site_id, Device.device_type == "barrier", Device.status == "active",
    )
    barrier = q.filter(Device.lane_no == near_device.lane_no,
                       Device.lane_dir == near_device.lane_dir).first()
    return barrier or q.first()


async def mark_paid_and_open(db: Session, session: ParkingSession, grace_minutes: int | None = None) -> None:
    """Төлбөр амжилттай болмогц дуудагдана: session-ийг PAID болгож, exit lane-ийн barrier нээнэ."""
    now = datetime.utcnow()
    session.paid_at = now
    site: ParkingSite = session.site
    template = site.tariff_template if site else None
    g = grace_minutes if grace_minutes is not None else (template.grace_minutes if template else 15)
    session.exit_deadline = now + timedelta(minutes=g)
    session.status = "PAID"

    # Гарах гэж зогсож байгаа бол (exit камерт аль хэдийн уншигдсан) шууд нээнэ
    if session.exit_device_id:
        exit_device = db.get(Device, session.exit_device_id)
        if exit_device:
            fee = session_fee_info(db, session, at=now)
            await _close_and_open(db, exit_device, session, now, fee, source="payment")
            return
    db.commit()
    await manager.broadcast(session.site_id, "PAYMENT_COMPLETED", {
        "session_id": session.id, "plate": session.plate_number,
        "exit_deadline": session.exit_deadline.isoformat(),
    })
