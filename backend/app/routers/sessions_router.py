"""Session удирдлага: жагсаалт, хайлт, шалгах, түүх, гараар хаах."""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..auth import require
from ..database import get_db
from ..models import AuditLog, Device, LprEvent, ParkingSession, User
from ..serializers import to_dict
from ..session_logic import get_open_session, normalize_plate, session_fee_info
from ..services.barrier import open_barrier
from ..ws import manager

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


def _session_out(db: Session, s: ParkingSession, with_fee: bool = False) -> dict:
    extra = {"site_name": s.site.name if s.site else None,
             "discount_name": s.discount.name if s.discount else None}
    if with_fee and s.status in ("OPEN", "AWAITING_PAYMENT"):
        extra["fee"] = session_fee_info(db, s)
    return to_dict(s, extra=extra)


@router.get("")
def list_sessions(
    site_id: str | None = None, status: str | None = None, plate: str | None = None,
    date_from: str | None = None, date_to: str | None = None,
    limit: int = 100, offset: int = 0, with_fee: bool = False,
    db: Session = Depends(get_db), user: User = Depends(require("history", "cashier", "check")),
):
    q = db.query(ParkingSession)
    if site_id:
        q = q.filter(ParkingSession.site_id == site_id)
    if status:
        q = q.filter(ParkingSession.status.in_(status.split(",")))
    if plate:
        q = q.filter(ParkingSession.plate_number.ilike(f"%{normalize_plate(plate)}%"))
    if date_from:
        q = q.filter(ParkingSession.entry_time >= datetime.fromisoformat(date_from))
    if date_to:
        q = q.filter(ParkingSession.entry_time < datetime.fromisoformat(date_to) + timedelta(days=1))
    total = q.count()
    rows = q.order_by(ParkingSession.entry_time.desc()).offset(offset).limit(min(limit, 500)).all()
    return {"total": total, "rows": [_session_out(db, s, with_fee=with_fee) for s in rows]}


@router.get("/check")
def check_plate(plate: str, site_id: str | None = None,
                db: Session = Depends(get_db), user: User = Depends(require("check", "cashier"))):
    """Шалгах/касс: дугаарын ЭХНИЙ хэсгээр нээлттэй session хайна (live хайлт, 2+ тэмдэгт)."""
    plate = normalize_plate(plate)
    if len(plate) < 2:
        return []
    q = db.query(ParkingSession).filter(
        ParkingSession.plate_number.ilike(f"{plate}%"),
        ParkingSession.status.in_(["OPEN", "AWAITING_PAYMENT", "PAID"]),
    )
    if site_id:
        q = q.filter(ParkingSession.site_id == site_id)
    sessions = q.order_by(ParkingSession.updated_at.desc()).limit(10).all()
    return [_session_out(db, s, with_fee=True) for s in sessions]


@router.get("/recent-exits")
def recent_exits(site_id: str, minutes: int = 30,
                 db: Session = Depends(get_db), user: User = Depends(require("cashier"))):
    """Касс/PAX: сүүлд гарах камерт уншигдсан, төлбөр хүлээж буй машинууд."""
    since = datetime.utcnow() - timedelta(minutes=minutes)
    sessions = (
        db.query(ParkingSession)
        .filter(ParkingSession.site_id == site_id,
                ParkingSession.status == "AWAITING_PAYMENT",
                ParkingSession.updated_at >= since)
        .order_by(ParkingSession.updated_at.desc()).limit(20).all()
    )
    # Нөхөн төлбөрийн өртэй машиныг касс дээр улаанаар тэмдэглэнэ (JGA спек)
    from ..models import Compensation
    debt_plates = {p for (p,) in db.query(Compensation.plate_number)
                   .filter(Compensation.status == "PENDING").all()}
    return [_session_out(db, s, with_fee=True) | {"has_debt": s.plate_number in debt_plates}
            for s in sessions]


@router.get("/today-exits")
def today_exits(site_id: str, db: Session = Depends(get_db), user: User = Depends(require("cashier"))):
    """Касс: ӨНӨӨДӨР гарах камерт уншигдсан бүх машин (төлбөр аваагүй/үнэгүй гарсныг ч).
    + зогсоолын багтаамж/эзэлсэн тоолуур. Бичилтэнд: дугаар, орсон/гарсан цаг, хугацаа,
    машины төрөл, төлбөрийн хэрэгсэл, төлсөн эсэх, e-Barimt өгсөн эсэх."""
    from sqlalchemy import or_
    from ..models import ParkingSite, Payment, VatReceipt
    site = db.get(ParkingSite, site_id)
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    occupied = db.query(ParkingSession).filter(
        ParkingSession.site_id == site_id,
        ParkingSession.status.in_(["OPEN", "AWAITING_PAYMENT", "PAID"])).count()
    sessions = (db.query(ParkingSession)
                .filter(ParkingSession.site_id == site_id,
                        or_(ParkingSession.exit_time >= today,
                            ParkingSession.status == "AWAITING_PAYMENT"))
                .order_by(ParkingSession.updated_at.desc()).limit(200).all())
    ids = [s.id for s in sessions]
    pays = {}
    if ids:
        for p in db.query(Payment).filter(Payment.session_id.in_(ids), Payment.status == "PAID").all():
            pays.setdefault(p.session_id, p)
    recs = {r.session_id for r in db.query(VatReceipt.session_id)
            .filter(VatReceipt.session_id.in_(ids), VatReceipt.status == "SENT").all()} if ids else set()
    prov_mn = {"CASH": "Бэлэн", "QPAY": "QPay", "POS": "Банкны карт"}
    rows = []
    for s in sessions:
        p = pays.get(s.id)
        car_type = "Гэрээт" if s.is_registered else ("Хөнгөлөлттэй" if s.discount_id else "Энгийн")
        rows.append({
            "session_id": s.id, "plate_number": s.plate_number,
            "entry_time": s.entry_time.isoformat() if s.entry_time else None,
            "exit_time": s.exit_time.isoformat() if s.exit_time else None,
            "duration_minutes": s.duration_minutes,
            "car_type": car_type, "discount_name": s.discount.name if s.discount else None,
            "total_fee": float(s.total_fee or 0),
            "provider": prov_mn.get(p.provider, p.provider) if p else None,
            "paid": s.status == "PAID" or bool(p),
            "status": s.status,
            "ebarimt": s.id in recs,
        })
    return {"capacity": site.capacity if site else 0, "occupied": occupied,
            "free": max(0, (site.capacity if site else 0) - occupied), "rows": rows}


@router.post("/manual-entry")
async def manual_entry(body: dict, db: Session = Depends(get_db),
                       user: User = Depends(require("cashier"))):
    """Орох талд уншигдалгүй орсон машиныг ажилтан гараар бүртгэнэ.
    (2 цаг тутмын эргүүлээр илэрсэн машин г.м.)
    body: {site_id, plate_number, entry_time?: ISO datetime — эргүүлээр тааварлаж
           оруулах бол орсон гэж үзэх цаг, default = одоо}"""
    from ..session_logic import find_registered, is_blacklisted, is_valid_plate
    plate = normalize_plate(body.get("plate_number", ""))
    site_id = body.get("site_id")
    if not plate or not site_id:
        raise HTTPException(400, "plate_number болон site_id шаардлагатай")
    # force=true — дипломат/тусгай дугаар (стандарт форматад тохирохгүй) гэдгийг оператор баталгаажуулсан
    if not is_valid_plate(plate) and not body.get("force"):
        raise HTTPException(400, f"«{plate}» дугаарын формат буруу байна. "
                                 "Зөв формат: 4 орон + 3 кирилл үсэг (жишээ: 1234УБА). "
                                 "Дипломат/тусгай дугаар бол force=true илгээнэ.")

    existing = get_open_session(db, plate, site_id)
    if existing:
        raise HTTPException(400, f"{plate} дугаартай машин аль хэдийн бүртгэлтэй байна "
                                 f"(орсон: {existing.entry_time:%Y-%m-%d %H:%M})")

    entry_time = (datetime.fromisoformat(body["entry_time"])
                  if body.get("entry_time") else datetime.utcnow())
    registered = find_registered(db, plate, site_id)
    black = is_blacklisted(db, plate)

    s = ParkingSession(
        site_id=site_id, plate_number=plate, entry_time=entry_time,
        is_registered=registered is not None, status="OPEN",
    )
    db.add(s)
    db.flush()
    db.add(AuditLog(username=user.username, action="MANUAL_ENTRY", entity="session",
                    entity_id=s.id, detail={"plate": plate, "entry_time": entry_time.isoformat()}))
    db.commit()
    await manager.broadcast(site_id, "ENTRY_EVENT", {
        "session_id": s.id, "plate": plate, "entry_time": s.entry_time.isoformat(),
        "registered": registered is not None, "blacklisted": black is not None,
        "barrier_opened": False, "manual": True, "by": user.username,
    })
    return _session_out(db, s, with_fee=True)


@router.post("/test-awaiting")
async def test_awaiting(body: dict, db: Session = Depends(get_db),
                        user: User = Depends(require("cashier"))):
    """ТЕСТ: камергүйгээр 'Гарах машинууд (төлбөр хүлээж буй)' листэд машин нэмнэ.
    Зөвхөн тест горим (PARKING_ALLOW_SIMULATE=true) дээр ажиллана."""
    import random
    from ..config import settings
    if not settings.allow_simulate:
        raise HTTPException(403, "Тест горим идэвхгүй (production)")
    site_id = body.get("site_id")
    if not site_id:
        raise HTTPException(400, "site_id шаардлагатай")
    letters = "АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЭЮЯӨҮ"
    plate = normalize_plate(body.get("plate") or
                            f"{random.randint(1000, 9999)}{''.join(random.choice(letters) for _ in range(3))}")
    minutes = int(body.get("minutes") or random.randint(35, 130))
    now = datetime.utcnow()
    s = ParkingSession(site_id=site_id, plate_number=plate, entry_time=now - timedelta(minutes=minutes),
                       status="AWAITING_PAYMENT")
    db.add(s)
    db.flush()
    fee = session_fee_info(db, s, at=now)
    s.duration_minutes = fee["duration_minutes"]
    s.base_fee, s.vat_amount, s.total_fee = fee["base_fee"], fee["vat_amount"], fee["total_fee"]
    db.add(AuditLog(username=user.username, action="TEST_AWAITING", entity="session",
                    entity_id=s.id, detail={"plate": plate}))
    db.commit()
    await manager.broadcast(site_id, "EXIT_LPR_EVENT", {
        "session_id": s.id, "plate": plate, "entry_time": s.entry_time.isoformat(),
        "duration_minutes": fee["duration_minutes"], "total_fee": fee["total_fee"], "test": True,
    })
    return _session_out(db, s, with_fee=True)


@router.get("/{session_id}")
def get_session(session_id: str, db: Session = Depends(get_db),
                user: User = Depends(require("history", "cashier"))):
    s = db.get(ParkingSession, session_id)
    if not s:
        raise HTTPException(404, "Session олдсонгүй")
    return _session_out(db, s, with_fee=True)


@router.post("/{session_id}/apply-discount")
def apply_discount(session_id: str, body: dict, db: Session = Depends(get_db),
                   user: User = Depends(require("cashier", "discounts"))):
    s = db.get(ParkingSession, session_id)
    if not s:
        raise HTTPException(404, "Session олдсонгүй")
    if s.status not in ("OPEN", "AWAITING_PAYMENT"):
        raise HTTPException(400, "Зөвхөн нээлттэй session-д хөнгөлөлт хэрэглэнэ")
    s.discount_id = body.get("discount_id")
    fee = session_fee_info(db, s)
    s.discount_amount = fee["discount_amount"]
    if s.status == "AWAITING_PAYMENT":
        s.base_fee, s.vat_amount, s.total_fee = fee["base_fee"], fee["vat_amount"], fee["total_fee"]
    # Хөнгөлөлт хэрэглэсэн тайлбар (шалтгаан)-ыг аудитад хадгална
    db.add(AuditLog(username=user.username, action="APPLY_DISCOUNT", entity="session",
                    entity_id=session_id,
                    detail={"discount_id": body.get("discount_id"), "note": body.get("note", "")}))
    db.commit()
    return _session_out(db, s, with_fee=True)


@router.put("/{session_id}/plate")
async def edit_plate(session_id: str, body: dict, db: Session = Depends(get_db),
                     user: User = Depends(require("cashier"))):
    """Камер алдаатай уншсан дугаарыг засах (easy-park UAT items 18, 21, 24).
    Зассаны дараа төлбөр/хайлт шинэ дугаараар хэвийн ажиллана."""
    from ..session_logic import is_valid_plate
    s = db.get(ParkingSession, session_id)
    if not s:
        raise HTTPException(404, "Session олдсонгүй")
    if s.status not in ("OPEN", "AWAITING_PAYMENT", "PAID"):
        raise HTTPException(400, "Зөвхөн нээлттэй session-ий дугаарыг засна")
    new_plate = normalize_plate(body.get("plate_number", ""))
    if not is_valid_plate(new_plate) and not body.get("force"):
        raise HTTPException(400, f"«{new_plate}» формат буруу. Зөв: 4 орон + 3 кирилл үсэг (1234УБА). "
                                 "Дипломат/тусгай дугаар бол force=true илгээнэ.")
    dup = get_open_session(db, new_plate, s.site_id)
    if dup and dup.id != s.id:
        raise HTTPException(400, f"{new_plate} дугаартай өөр нээлттэй бүртгэл байна")
    old_plate = s.plate_number
    s.plate_number = new_plate
    db.add(AuditLog(username=user.username, action="EDIT_PLATE", entity="session",
                    entity_id=session_id, detail={"old": old_plate, "new": new_plate}))
    db.commit()
    await manager.broadcast(s.site_id, "PLATE_EDITED", {
        "session_id": s.id, "old_plate": old_plate, "plate": new_plate, "by": user.username,
    })
    return _session_out(db, s, with_fee=True)


@router.post("/{session_id}/manual-exit")
async def manual_exit(session_id: str, body: dict, db: Session = Depends(get_db),
                      user: User = Depends(require("cashier"))):
    """Оператор гараар гаргах (төлбөргүйгээр эсвэл асуудал шийдсэний дараа).
    body: {open_barrier: bool, device_id?: str, reason: str, create_compensation?: bool}
    create_compensation=true бол төлөгдөөгүй дүнгээр нөхөн төлбөрийн нэхэмжлэл үүснэ."""
    s = db.get(ParkingSession, session_id)
    if not s:
        raise HTTPException(404, "Session олдсонгүй")
    now = datetime.utcnow()
    fee = session_fee_info(db, s, at=now)
    s.exit_time = now
    s.duration_minutes = fee["duration_minutes"]
    if s.total_fee is None:
        s.base_fee, s.vat_amount, s.total_fee = fee["base_fee"], fee["vat_amount"], fee["total_fee"]
    s.status = "CLOSED" if s.paid_at else "MANUAL_CLOSED"

    # Төлбөргүй гаргаж буй бол нөхөн төлбөрийн нэхэмжлэл үүсгэх сонголт
    if body.get("create_compensation") and not s.paid_at and not fee["is_free"]:
        from .compensations_router import create_compensation
        create_compensation(db, s, body.get("reason") or "unpaid_exit", user.username)

    barrier_opened = False
    if body.get("open_barrier"):
        device = db.get(Device, body.get("device_id")) if body.get("device_id") else None
        if not device:
            device = (db.query(Device).filter(Device.site_id == s.site_id,
                                              Device.device_type == "barrier",
                                              Device.lane_dir == "exit").first()
                      or db.query(Device).filter(Device.site_id == s.site_id,
                                                 Device.device_type == "barrier").first())
        if device:
            cmd = await open_barrier(db, device, s.id, "manual", issued_by=user.username,
                                     plate=s.plate_number)
            barrier_opened = cmd.status == "SUCCESS"

    db.add(AuditLog(username=user.username, action="MANUAL_EXIT", entity="session",
                    entity_id=session_id, detail={"reason": body.get("reason", ""), **body}))
    db.commit()
    await manager.broadcast(s.site_id, "EXIT_COMPLETED", {
        "session_id": s.id, "plate": s.plate_number, "status": s.status,
        "barrier_opened": barrier_opened, "manual": True,
    })
    return _session_out(db, s)
