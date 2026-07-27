"""Орох/гарах урсгалын гол логик — LPR event-ээс session үүсгэх, хаах, barrier нээх."""
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from .billing import calculate_fee
from .config import settings
from .services.device_auth import barrier_credentials, camera_credentials
from .models import (
    AuditLog, BlacklistEntry, Device, LprEvent, ParkingSession, ParkingSite,
    Payment, RegisteredDriver,
)
from .services.barrier import open_barrier, render_screen_text, schedule_display
from .services.snapshot import schedule_capture
from .ws import manager


import re

# Монгол улсын дугаарын формат: 4 орон + 3 кирилл үсэг (Ө, Ү орно). Жишээ: 1234УБА
PLATE_RE = re.compile(r"^\d{4}[А-ЯЁӨҮ]{3}$")


def normalize_plate(plate: str) -> str:
    return (plate or "").upper().replace(" ", "").replace("-", "").strip()


def strip_images(raw):
    """LprEvent.raw-д хадгалахын өмнө том base64 зургийг хасна — push-аар ирсэн зураг
    (~1MB) event бүрд DB-д хуримтлагдвал сан хэт томордог. Capture нь ТУСДАА бүрэн
    raw-г ашигладаг тул энэ нь зөвхөн ЛОГД хадгалах хувилбар."""
    import copy
    if not isinstance(raw, (dict, list)):
        return raw
    out = copy.deepcopy(raw)

    def scrub(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(v, str) and len(v) > 2000:
                    node[k] = f"<{len(v)}b image stripped>"
                else:
                    scrub(v)
        elif isinstance(node, list):
            for v in node:
                scrub(v)
    scrub(out)
    return out


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


# OCR-т амархан андуурагддаг Кирилл/цифр хосууд — жигдэлмэгц ижил болгож харьцуулна
_OCR_CANON = str.maketrans({
    "О": "0", "O": "0", "В": "Б", "Ь": "Б", "Ё": "Е", "Э": "З",
    "Ү": "У", "Ұ": "У", "Й": "И", "П": "Н", "Ц": "Ч", "І": "1", "l": "1",
})


def _ocr_canon(p: str) -> str:
    return (p or "").translate(_OCR_CANON)


def plates_ocr_similar(a: str, b: str) -> bool:
    """Хоёр дугаар OCR-ийн зөрүүтэй ижил машинйх байж болох эсэх:
    - яг ижил
    - ижил урттай бөгөөд НЭГ л байрлалд зөрүүтэй (substitution)
    - андуурагддаг тэмдэгтүүдийг (О/0, Б/В г.м.) жигдэлмэгц ижил
    - нэг нь нөгөөгийнхөө ТАЙРАГДСАН уншилт: камер эхний/сүүлийн цифрийг
      алгасч уншдаг (ж: 7524УБТ → 524УБТ) — богино нь стандарт формат биш
      бол л (жинхэнэ өөр машин байх боломжгүй) ижил гэж үзнэ."""
    if a == b:
        return True
    if len(a) != len(b):
        long_p, short_p = (a, b) if len(a) > len(b) else (b, a)
        return (len(long_p) - len(short_p) <= 2 and len(short_p) >= 5
                and not is_valid_plate(short_p)
                and (long_p.endswith(short_p) or long_p.startswith(short_p)))
    if _ocr_canon(a) == _ocr_canon(b):
        return True
    return sum(1 for x, y in zip(a, b) if x != y) == 1


def match_open_session(db: Session, plate: str, site_id: str) -> tuple[ParkingSession | None, bool]:
    """Гарах талд session хайх: эхлээд ЯГ таарах, олдохгүй бол OCR-ийн зөрүүтэй
    (үсэг андуурч уншсан) session-ийг олно. Буцаах: (session|None, fuzzy_эсэх).

    Аюулгүй байдал: OCR-ойролцоо нэр дэвшигч ЯГ НЭГ байвал л зөвшөөрнө — 2+ бол
    сэжигтэй тул None буцааж, оператор гараар шийднэ (буруу машинд төлбөр тохохгүй)."""
    exact = get_open_session(db, plate, site_id)
    if exact:
        return exact, False
    opens = (db.query(ParkingSession)
             .filter(ParkingSession.site_id == site_id,
                     ParkingSession.status.in_(["OPEN", "AWAITING_PAYMENT", "PAID"]))
             .all())
    close = [s for s in opens if plates_ocr_similar(plate, s.plate_number)]
    if len(close) == 1:
        return close[0], True
    return None, False


def session_fee_info(db: Session, s: ParkingSession, at: datetime | None = None) -> dict:
    site: ParkingSite = s.site
    template = site.tariff_template if site else None
    if at is None:
        # AWAITING_PAYMENT машин зогсоолд байгаа хэвээр (гарч чадаагүй) тул төлбөр
        # exit_time дээр царцахгүй — одоог хүртэл үргэлжлэн бодогдоно.
        if s.status in ("OPEN", "AWAITING_PAYMENT"):
            at = datetime.utcnow()
        else:
            at = s.exit_time or datetime.utcnow()
    # Гэрээт эсэхийг ОРОХ үед л тогтоож session дээр хөлдөөдөг байсан тул, машин
    # орсны ДАРАА жагсаалтад нэмэгдвэл (ж: Excel импорт, гараар бүртгэх) төлбөртэй
    # хэвээр үлдэж, оператор гараар чөлөөлөх шаардлагатай болдог байв. Зогсоолд
    # байгаа машины төлбөрийг тооцох бүрд ДАХИН шалгаснаар шинээр бүртгэсэн машин
    # ямар ч гар ажиллагаагүйгээр шууд гарна (fee.is_free → хаалт авто нээгдэнэ).
    registered = s.is_registered
    if not registered and db is not None and s.status in ("OPEN", "AWAITING_PAYMENT"):
        registered = find_registered(db, s.plate_number, s.site_id) is not None
        if registered:
            # Session дээр нь тэмдэглэнэ — жагсаалтад "Гэрээт" гэж зөв харагдана
            # (дараагийн commit-той хамт хадгалагдана; read-only хүсэлтэд хадгалагдахгүй
            # ч тооцоолол зөв хэвээр).
            s.is_registered = True

    return calculate_fee(
        template, s.entry_time, at,
        discount=s.discount, is_registered=registered,
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


def close_session_forced(db: Session, s: ParkingSession, reason: str, username: str,
                         create_comp: bool = True) -> float:
    """Админ/авто цэвэрлэгээ: гацсан session-ийг хааж, төлөгдөөгүй дүнгээр өр үүсгэнэ.

    Өрийн дүнгийн дүрэм: гарах оролдлоготой (AWAITING_PAYMENT + exit_time) машин
    тэр үедээ л төлөлгүй явсан гэж үзэж ТЭР ҮЕИЙН дүнгээр (шударга дүн, exit_time хэвээр);
    гарах оролдлогогүй (OPEN) бол одоог хүртэлх дүнгээр (daily_cap хамгаална).
    Буцаах: үүсгэсэн өрийн дүн (0 бол өр үүсээгүй). commit хийхгүй — caller хийнэ."""
    now = datetime.utcnow()
    if s.status == "PAID" and s.paid_at:
        # Төлчихсөн машин — grace дотор гарсан гэж үзэж төлбөрийг ТӨЛСӨН/deadline
        # үедээ царцаана. Эс бол одоог хүртэлх хугацаагаар хэт нэхэж, худал өр үүснэ.
        at = s.exit_deadline or s.paid_at
    elif s.status == "AWAITING_PAYMENT":
        # Гарах оролдлоготой машин: exit_time (байвал), үгүй бол сүүлд гарах хаалтанд
        # харагдсан үе (updated_at) дээр төлбөрийг царцаана — дагаж гарсан машинд
        # алга болсноос хойшхи цагийг нэхэхгүй (шударга дүн).
        at = s.exit_time or s.updated_at or now
    else:
        at = now
    fee = session_fee_info(db, s, at=at)
    due = amount_due(db, s, fee)
    s.exit_time = at
    s.duration_minutes = fee["duration_minutes"]
    s.base_fee, s.vat_amount, s.total_fee = fee["base_fee"], fee["vat_amount"], fee["total_fee"]
    s.status = "CLOSED" if s.paid_at else "MANUAL_CLOSED"
    if create_comp and due > 0 and not fee["is_free"]:
        from .routers.compensations_router import create_compensation
        comp = create_compensation(db, s, reason, username)
        comp.amount = due
        return due
    return 0.0


async def handle_entry(db: Session, device: Device, plate: str, confidence: float, raw: dict) -> dict:
    """Орох камерын event: session нээж, barrier нээнэ (blacklist биш бол)."""
    site_id = device.site_id
    now = datetime.utcnow()

    # Давхар event хамгаалалт — OCR зөрүүтэй уншилтыг ч барина. Орох камер нэг
    # машиныг хэдэн секундын зайтай 2 удаа өөр дугаараар (Х/К, О/0 г.м. андуурч)
    # уншихад 2 тусдаа session үүсдэг байсныг (ж: 5155УХК + 5155УКК) зогсооно.
    recent_plates = [
        rp for (rp,) in db.query(LprEvent.plate_number).filter(
            LprEvent.site_id == site_id, LprEvent.lane_dir == "entry",
            LprEvent.accepted.is_(True),
            LprEvent.created_at >= now - timedelta(seconds=settings.lpr_dedup_seconds),
        ).all()
    ]
    if any(plates_ocr_similar(plate, rp) for rp in recent_plates):
        return {"action": "dedup", "plate": plate}

    # ЦУВРАЛ уншилт: burst цонхонд (default 6с) энэ зогсоолын орох камерт өөр event
    # аль хэдийн ирсэн бол физикийн хувьд НЭГ машин (хаалтаар 6 секундэд 2 машин
    # орохгүй) — огт өөр уншигдсан ч шинэ session ҮҮСГЭХГҮЙ. Шинэ уншилт зөв
    # форматтай бол өмнөх session-ий дугаарыг сүүлийн (хамгийн ойрын, ихэвчлэн
    # хамгийн зөв) уншилтаар засна: 1101ЭН → 1310ХЭН → 7370ХЭН гэж нийлдэг.
    burst_prev = (db.query(LprEvent)
                  .filter(LprEvent.site_id == site_id, LprEvent.lane_dir == "entry",
                          LprEvent.accepted.is_(True),
                          LprEvent.created_at >= now - timedelta(seconds=settings.entry_burst_seconds))
                  .order_by(LprEvent.created_at.desc()).first())
    if burst_prev:
        if is_valid_plate(plate) and not get_open_session(db, plate, site_id):
            prev_session = get_open_session(db, burst_prev.plate_number, site_id)
            # Зөвхөн саяхан (энэ burst-д) үүссэн session-ийг л засна
            if prev_session and prev_session.entry_time >= now - timedelta(seconds=60):
                old_plate = prev_session.plate_number
                prev_session.plate_number = plate
                db.add(LprEvent(site_id=site_id, device_id=device.id, plate_number=plate,
                                lane_dir="entry", confidence=confidence, accepted=True,
                                raw=strip_images(raw)))
                db.add(AuditLog(username="system", action="PLATE_AUTOCORRECT", entity="session",
                                entity_id=prev_session.id,
                                detail={"old": old_plate, "new": plate,
                                        "reason": "цуврал уншилт — сүүлийн зөв уншилтаар"}))
                db.commit()
                print(f"[entry] цуврал уншилт: {old_plate} → {plate} (session {prev_session.id})")
                await manager.broadcast(site_id, "PLATE_EDITED", {
                    "session_id": prev_session.id, "old_plate": old_plate, "plate": plate,
                    "by": "system:autocorrect"})
                return {"action": "plate_autocorrect", "session_id": prev_session.id,
                        "old": old_plate, "new": plate}
        return {"action": "burst_dedup", "plate": plate}

    black = is_blacklisted(db, plate)
    registered = find_registered(db, plate, site_id)

    existing = get_open_session(db, plate, site_id)
    if existing and existing.exit_device_id and existing.status == "AWAITING_PAYMENT":
        # Машин өмнө нь гарах камерт уншигдаад ТӨЛБӨРГҮЙ гарсан байж — одоо дахин орж ирэв.
        # Хуучин session дээр наалдвал шинэ зогсолт огт бүртгэгдэхгүй (7/12, 7/20-ны гацаа).
        # Тиймээс: хуучныг өр (нөхөн төлбөр) үүсгэн хааж, шинэ session нээнэ.
        from .routers.compensations_router import create_compensation
        existing.exit_time = existing.updated_at or now
        old_fee = session_fee_info(db, existing, at=existing.exit_time)
        existing.duration_minutes = old_fee["duration_minutes"]
        if existing.total_fee is None:
            existing.base_fee = old_fee["base_fee"]
            existing.vat_amount = old_fee["vat_amount"]
            existing.total_fee = old_fee["total_fee"]
        existing.status = "MANUAL_CLOSED"
        due = amount_due(db, existing, old_fee)
        if due > 0:
            comp = create_compensation(db, existing, "unpaid_exit", "system")
            comp.amount = due
        # uq_active_session: шинэ OPEN session оруулахын ӨМНӨ хаалтыг DB-д тулгана
        db.flush()
        if due > 0:
            await manager.broadcast(site_id, "DEBT_ALERT", {
                "plate": plate, "debt_count": 1, "debt_amount": float(due),
                "note": "Төлбөргүй гарсан машин дахин орж ирлээ — өр үүсгэв",
            })
        existing = None
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
                    lane_dir="entry", confidence=confidence, accepted=True, raw=strip_images(raw)))
    db.commit()
    # Зургийг ард нь татаж хадгална (хаалт нээхийг хүлээлгэхгүй)
    schedule_capture(session.id, device.ip_address, plate, "entry", raw,
                             camera_credentials(device))

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
    # Орох LED дэлгэцэнд дугаар + мэндчилгээ (Managed горимд камер өөрөө харуулахгүй тул
    # сервер илгээнэ; blacklist бол харуулахгүй). Хаалт нээхийг хүлээлгэхгүй, ард нь.
    if not black:
        schedule_display(device.ip_address,
                         render_screen_text(settings.screen_welcome_text, plate=plate),
                         barrier_credentials(device))
    return {"action": "entry", "session_id": session.id, "barrier_opened": barrier_opened}


async def handle_exit(db: Session, device: Device, plate: str, confidence: float, raw: dict) -> dict:
    """Гарах камерын event:
    - Төлсөн (grace хугацаанд) эсвэл үнэгүй/гэрээт бол barrier нээж session хаана.
    - Үгүй бол AWAITING_PAYMENT болгож касс/PAX/QR руу мэдэгдэнэ.
    """
    site_id = device.site_id
    now = datetime.utcnow()

    # Давхар event хамгаалалт — OCR зөрүүтэй уншилтыг ч барина. Камер гарах дугаарыг
    # хэдэн секундын зайтай 2 дахь удаа арай ӨӨР уншихад (жишээ 2 дахь уншилт session-
    # тэй таарахгүй) LED-д "бүртгэлгүй" текст илгээж, төлбөрийн текстийг дардаг байсныг
    # зогсооно (орох талтай ижил OCR-тэсвэртэй дүрэм).
    recent_plates = [
        rp for (rp,) in db.query(LprEvent.plate_number).filter(
            LprEvent.site_id == site_id, LprEvent.lane_dir == "exit",
            LprEvent.accepted.is_(True),
            LprEvent.created_at >= now - timedelta(seconds=settings.lpr_dedup_seconds),
        ).all()
    ]
    if any(plates_ocr_similar(plate, rp) for rp in recent_plates):
        return {"action": "dedup", "plate": plate}

    session, fuzzy = match_open_session(db, plate, site_id)
    db.add(LprEvent(site_id=site_id, device_id=device.id, plate_number=plate,
                    lane_dir="exit", confidence=confidence, accepted=True, raw=strip_images(raw)))
    if session and fuzzy:
        # Гарах камер орох дугаараас өөр уншсан (OCR зөрүү) — ойролцоо session-д
        # тохоов. Ил тод байдлын үүднээс тэмдэглэж, аудитад бичнэ.
        from .models import AuditLog
        note = f"Гарах OCR зөрүү: уншсан «{plate}» → «{session.plate_number}»"
        session.note = f"{session.note + ' | ' if session.note else ''}{note}"[:1000]
        db.add(AuditLog(username="system", action="EXIT_OCR_MATCH", entity="session",
                        entity_id=session.id,
                        detail={"read_plate": plate, "matched_plate": session.plate_number}))
        print(f"[exit] OCR зөрүү тохов: уншсан {plate} → session {session.plate_number}")

    # #6 Өртэй машин — гарах камерт уншигдмагц касст шууд сануулах
    from .models import Compensation
    debts = db.query(Compensation).filter(Compensation.plate_number == plate,
                                          Compensation.status == "PENDING").all()
    debt_amount = float(sum(c.amount for c in debts))
    if debts:
        await manager.broadcast(site_id, "DEBT_ALERT", {
            "plate": plate, "debt_count": len(debts), "debt_amount": debt_amount})

    if not session:
        # Session олдсонгүй — оператор шийднэ (гараар нээх боломжтой)
        db.commit()
        await manager.broadcast(site_id, "EXIT_NO_SESSION",
                                {"plate": plate, "has_debt": bool(debts), "debt_amount": debt_amount})
        schedule_display(device.ip_address,
                         render_screen_text(settings.screen_nosession_text, plate=plate),
                         barrier_credentials(device))
        return {"action": "no_session", "plate": plate}

    session.exit_device_id = device.id
    session.confidence_exit = confidence
    # Гарах зургийг ард нь татаж хадгална (маргаан/нотолгоонд — ялангуяа төлбөргүй гарсан үед)
    schedule_capture(session.id, device.ip_address, plate, "exit", raw,
                             camera_credentials(device))

    fee = session_fee_info(db, session, at=now)

    # #7 3+ төлөгдөөгүй өртэй машин — автоматаар хаалт нээхгүй, оператор өрийг цуглуулна
    if len(debts) >= 3:
        session.status = "AWAITING_PAYMENT"
        session.duration_minutes = fee["duration_minutes"]
        session.base_fee, session.vat_amount, session.total_fee = (
            fee["base_fee"], fee["vat_amount"], fee["total_fee"])
        db.commit()
        due_now = amount_due(db, session, fee)
        await manager.broadcast(site_id, "EXIT_LPR_EVENT", {
            "session_id": session.id, "plate": plate,
            "entry_time": session.entry_time.isoformat(),
            "duration_minutes": fee["duration_minutes"], "total_fee": fee["total_fee"],
            "amount_due": due_now,
            "has_debt": True, "debt_amount": debt_amount, "blocked": True})
        # Дэлгэцэнд өнөөдрийн төлбөр + өмнөх өрийн нийлбэрийг харуулна
        schedule_display(device.ip_address,
                         render_screen_text(settings.screen_fee_text,
                                            amount=due_now + debt_amount, plate=plate),
                         render_screen_text(settings.screen_fee_text,
                                            amount=due_now + debt_amount, plate=plate)
                         if settings.screen_voice else None,
                         barrier_credentials(device))
        return {"action": "debt_blocked", "plate": plate, "debt_amount": debt_amount}

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
        "has_debt": bool(debts), "debt_amount": debt_amount,
    })
    # Гарах хаалтны LED дэлгэцэнд төлөх дүнг харуулна (ард нь, урсгалыг хүлээлгэхгүй).
    # Өртэй машинд ӨМНӨХ ӨРИЙГ НИЙЛҮҮЛЖ нэхэмжилнэ (жолооч нийт дүнгээ шууд харна).
    fee_text = render_screen_text(settings.screen_fee_text,
                                  amount=due + debt_amount, plate=plate)
    schedule_display(device.ip_address, fee_text,
                     fee_text if settings.screen_voice else None,
                         barrier_credentials(device))
    return {"action": "awaiting_payment", "session_id": session.id,
            "total_fee": fee["total_fee"], "amount_due": due,
            "debt_amount": debt_amount}


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
    # Дэлгэцэнд мэндчилгээ (төлбөр төлөгдсөн/үнэгүй — хаалт нээгдэж байна)
    schedule_display(exit_device.ip_address,
                     render_screen_text(settings.screen_bye_text,
                                        plate=session.plate_number),
                         barrier_credentials(exit_device))
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
