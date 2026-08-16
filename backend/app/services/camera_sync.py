"""Камерын дотоод логоос алдагдсан бүртгэлийг WATERMARK-аар нөхөх.

Яагаад watermark вэ: 2026-08-10-нд 48 цагийн логийг бүхлээр нь дахин уншиж
нөхөж бүртгэхэд тэр мужид АЛЬ ХЭДИЙН шийдэгдсэн (өмнө нь өр болоод цуцлагдсан,
төлөгдсөн, эсвэл цэвэрлэгдсэн) машинууд «системд байхгүй» гэж дахин танигдаж
ДАВХАР өр үүсгэсэн. Тиймээс энэ сервис нь:

  • Зогсоол бүрд СҮҮЛД боловсруулсан event-ийн цагийг (watermark) хадгална
  • Зөвхөн түүнээс ХОЙШХИ event-ийг боловсруулна — нэг event хоёр удаа орохгүй
  • Watermark хэзээ ч ухрахгүй (зөвхөн урагшилна)
  • Хамгийн сүүлийн min_age_minutes-ийн event-д хүрэхгүй (яг явж буй машин)

Тохиргоо: Тохиргоо → Авто цэвэрлэгээ → «Камерын лог нөхөлт» (app_settings).
"""
import logging
import threading
from datetime import datetime, timedelta

from ..database import SessionLocal
from ..models import AuditLog, Compensation, ParkingSession, ParkingSite
from ..session_logic import close_session_forced, is_valid_plate, normalize_plate
from .app_settings import CAMSYNC_STATE, get_camsync_rules, get_state, set_state
from .camera_records import site_camera_events

log = logging.getLogger("parking.camera_sync")

# Хуваарь ба гар ажиллагаа ДАВХЦАХААС хамгаалах цорго
_LOCK = threading.Lock()

BURST_SEC = 600          # нэг дугаарын дараалсан уншилтыг нэгтгэх цонх
ACTIVE = ("OPEN", "AWAITING_PAYMENT", "PAID")


def _sync_inner(db, site, cam: dict, watermark, horizon, rules: dict,
                dry_run: bool) -> tuple[int, int]:
    """ДОТООД (дамжин) хаалтны логийг нөхөх — ЭНГИЙН ДҮРЭМ ЭНД ҮЙЛЧЛЭХГҮЙ.

    Бусад зогсоолд «орох уншилт = зогсолт нээ, гарах уншилт = зогсолт хаа»
    гэсэн дүрэм үйлчилдэг. Дамжин зогсоолд (Рашбулаг ЭТТ: Орох → Орох 2 →
    Гарах 2 → Гарах) ДУНДАХ хоёр уншилт нь зогсоолд орох/гарахыг ОГТ
    илэрхийлэхгүй — машин манай талбайд байсаар, зөвхөн төлбөр тоологдохгүй
    шороон зогсоол руу орж гарч байна. Тиймээс энд ЗӨВХӨН тоолуур зогсоох/
    үргэлжлүүлэх үйлдэл хийнэ: session ҮҮСГЭХГҮЙ, ХААХГҮЙ, өр ҮҮСГЭХГҮЙ.

    ДАВХАР ТООЛОХООС хамгаалах: амьд урсгал (cgi_poller/lpr_router) тухайн
    уншилтыг аль хэдийн боловсруулсан бол `lpr_events`-д мөр үлдсэн байна —
    тэр тохиолдолд логийн хуулбарыг алгасна. Эс бол «орлоо» хоёр удаа бичигдэж
    хасагдах минут давхарлана.
    """
    inner = [e for e in (cam.get("inner_events") or [])
             if e.get("plate") and watermark < e["time"] <= horizon]
    if not inner:
        return 0, 0
    from ..models import LprEvent
    from ..session_logic import plates_ocr_similar
    from .nested import _ACTIVE, pause_cap_minutes, pause_session, resume_session

    inner.sort(key=lambda e: e["time"])
    cap = pause_cap_minutes(db, site.id)
    paused_n = resumed_n = 0
    for ev in inner:
        p = normalize_plate(ev["plate"])
        if rules["skip_invalid_plate"] and not is_valid_plate(p):
            continue
        entering = (ev.get("lane_dir") or "entry") != "exit"
        # Амьд урсгал үүнийг аль хэдийн үзсэн үү (±90с) — үзсэн бол алгасна
        if db.query(LprEvent.id).filter(
                LprEvent.site_id == site.id, LprEvent.plate_number == p,
                LprEvent.accepted.is_(True),
                LprEvent.created_at >= ev["time"] - timedelta(seconds=90),
                LprEvent.created_at <= ev["time"] + timedelta(seconds=90)).first():
            continue
        s = (db.query(ParkingSession)
             .filter(ParkingSession.site_id == site.id,
                     ParkingSession.plate_number == p,
                     ParkingSession.status.in_(_ACTIVE))
             .order_by(ParkingSession.entry_time.desc()).first())
        if s is None:
            # Шороон зогсоолын тоос/өнцгөөс болж дотоод камер ӨӨР уншсан байж
            # магадгүй (9920ҮИН ↔ 9920УНН). ЯГ НЭГ нэр дэвшигч байвал зөвшөөрнө —
            # олон бол буруу машины тоолуурыг зогсоох эрсдэлтэй тул хүрэхгүй.
            cands = [x for x in db.query(ParkingSession)
                     .filter(ParkingSession.site_id == site.id,
                             ParkingSession.status.in_(_ACTIVE)).all()
                     if plates_ocr_similar(p, x.plate_number)]
            if len(cands) != 1:
                continue
            s = cands[0]
        if dry_run:
            paused_n, resumed_n = paused_n + entering, resumed_n + (not entering)
            continue
        try:
            if entering:
                if pause_session(s, ev["time"]):
                    paused_n += 1
            elif resume_session(s, ev["time"], cap):
                resumed_n += 1
            db.commit()
        except Exception as e:  # noqa: BLE001 — нэг уншилт бусдыг зогсоохгүй
            db.rollback()
            log.warning("%s %s: дотоод уншилт нөхөж чадсангүй — %r", site.name, p, e)
    if paused_n or resumed_n:
        log.info("%s: дотоод логоос тоолуур %d зогсоов, %d үргэлжлүүлэв "
                 "(session үүсгээгүй/хаагаагүй)", site.name, paused_n, resumed_n)
    return paused_n, resumed_n


def sync_site(db, site: ParkingSite, rules: dict, dry_run: bool = False) -> dict:
    """Нэг зогсоолын логийг watermark-аас хойш нөхнө. Хураангуй буцаана."""
    state = get_state(db, CAMSYNC_STATE)
    now = datetime.utcnow()
    horizon = now - timedelta(minutes=rules["min_age_minutes"])
    floor = now - timedelta(hours=rules["lookback_hours"])

    mark_raw = state.get(site.id)
    try:
        watermark = datetime.fromisoformat(mark_raw) if mark_raw else floor
    except ValueError:
        watermark = floor
    # Хэт ухрахаас хамгаална (сервис удаан унтарсан бол логийн эхнээс биш)
    watermark = max(watermark, floor)
    if watermark >= horizon:
        return {"site": site.name, "created": 0, "skipped": 0, "note": "шинэ event алга"}

    # Камерын лог — watermark-аас хойшхийг багтаах хэмжээний цонхоор татна
    hours = max(1.0, (now - watermark).total_seconds() / 3600 + 0.5)
    cam = site_camera_events(db, site.id, hours=hours)
    if all(c["error"] for c in cam["cameras"]) and cam["cameras"]:
        return {"site": site.name, "created": 0, "skipped": 0,
                "note": "камерууд холбогдсонгүй"}

    entries = [e for e in cam["events"]
               if e["lane_dir"] == "entry" and e["plate"]
               and watermark < e["time"] <= horizon]
    exits_by_plate: dict[str, list] = {}
    for e in cam["events"]:
        if e["lane_dir"] == "exit" and e["plate"]:
            exits_by_plate.setdefault(e["plate"], []).append(e["time"])
    for lst in exits_by_plate.values():
        lst.sort()

    # burst нэгтгэх
    entries.sort(key=lambda e: (e["plate"], e["time"]))
    uniq = []
    for e in entries:
        if uniq and uniq[-1]["plate"] == e["plate"] \
                and (e["time"] - uniq[-1]["time"]).total_seconds() < BURST_SEC:
            continue
        uniq.append(e)

    created, skipped, debt_total = 0, 0, 0.0
    for ev in uniq:
        plate = normalize_plate(ev["plate"])
        if rules["skip_invalid_plate"] and not is_valid_plate(plate):
            skipped += 1
            continue
        # Идэвхтэй бүртгэлтэй бол шинийг үүсгэхгүй (uq_active_session)
        if db.query(ParkingSession.id).filter(
                ParkingSession.site_id == site.id,
                ParkingSession.plate_number == plate,
                ParkingSession.status.in_(ACTIVE)).first():
            skipped += 1
            continue
        # Тухайн цагийн ±1 цагт бүртгэл байвал давхардуулахгүй
        if db.query(ParkingSession.id).filter(
                ParkingSession.site_id == site.id,
                ParkingSession.plate_number == plate,
                ParkingSession.entry_time >= ev["time"] - timedelta(hours=1),
                ParkingSession.entry_time <= ev["time"] + timedelta(hours=1)).first():
            skipped += 1
            continue
        # Event нь БАРИМТЖСАН зогсолтын хугацаанд багтаж байвал давхардуулахгүй:
        # орох→гарах хоёулаа бодит уншилттай бүртгэлийн ДОТОР гарсан «орох» уншилт
        # нь тухайн машины давтан/эргэлзээтэй уншилт болохоос шинэ зогсолт биш.
        # ЗӨВХӨН exit_confirmed=true бүртгэлийг тооцно: албадан хаалт нь гарах цагт
        # «одоо» гэж бичдэг тул 12 цагийн ХУУРАМЧ цонх үүсгэдэг ба түүгээр шүүвэл
        # тэр цонхонд багтсан ЖИНХЭНЭ дараагийн зогсолтууд алдагдана.
        if db.query(ParkingSession.id).filter(
                ParkingSession.site_id == site.id,
                ParkingSession.plate_number == plate,
                ParkingSession.exit_confirmed.is_(True),
                ParkingSession.entry_time <= ev["time"],
                ParkingSession.exit_time >= ev["time"]).first():
            skipped += 1
            continue
        if dry_run:
            created += 1
            continue
        try:
            s = ParkingSession(site_id=site.id, plate_number=plate,
                               entry_time=ev["time"], status="OPEN",
                               note="камерын логоос нөхөж бүртгэв (авто sync)")
            ex = next((t for t in exits_by_plate.get(plate, []) if t > ev["time"]), None)
            if ex:
                s.exit_time = ex
                s.exit_confirmed = True   # камерын логийн бодит бичлэг
                s.status = "AWAITING_PAYMENT"
            db.add(s)
            db.flush()
            due = 0.0
            if ex:
                due = close_session_forced(db, s, "camera_sync", "system",
                                           create_comp=rules["create_debt"])
                debt_total += due
            db.add(AuditLog(username="system", action="CAMERA_SYNC", entity="session",
                            entity_id=s.id,
                            detail={"plate": plate, "entry": ev["time"].isoformat(),
                                    "exit": ex.isoformat() if ex else None, "debt": due}))
            db.commit()
            created += 1
        except Exception as e:  # noqa: BLE001
            db.rollback()
            skipped += 1
            log.warning("%s %s: нөхөж бүртгэж чадсангүй — %r", site.name, plate, e)

    # ── ГАРАХ уншилтаар НЭЭЛТТЭЙ бүртгэлийг хаах ─────────────────────────────
    # Өмнө нь гарах event-ийг ЗӨВХӨН шинэ бүртгэл үүсгэхэд ашигладаг байв —
    # камерын лог «машин 17:51-д гарсан» гэж хэлж байхад бидний DB «зогсож
    # байна» гэж үздэг. Үр дагавар нь: зогсоолын багтаамжаас олон машин
    # «дотор» харагдах (1,878), 24ц+ «зогссон» машин, гарах ЗУРАГТАЙ атлаа
    # «Зогсож байна» төлөвтэй бүртгэл, эцэст нь 12 цагийн авто хаалт хуурамч
    # хугацаагаар хаах. Одоо логийн гарах уншилтыг БОДИТ гарсан цаг гэж
    # хүлээн авч бүртгэлийг ТЭР ЦАГААР хаана.
    closed_by_log, log_fee = 0, 0.0
    for raw_plate, times in exits_by_plate.items():
        p = normalize_plate(raw_plate)
        if rules["skip_invalid_plate"] and not is_valid_plate(p):
            continue
        for t in times:
            if not (watermark < t <= horizon):
                continue          # зөвхөн энэ мужийн шинэ уншилт
            # AWAITING_PAYMENT-д ХҮРЭХГҮЙ: тэр машиныг систем аль хэдийн гарцад
            # харсан, төлбөр хүлээж байна — auto_close-ийн 2 цагийн дүрэм
            # (unpaid_exit өртэй) түүнийг зөв шийднэ.
            s = (db.query(ParkingSession)
                 .filter(ParkingSession.site_id == site.id,
                         ParkingSession.plate_number == p,
                         ParkingSession.status.in_(("OPEN", "PAID")),
                         ParkingSession.entry_time < t)
                 .order_by(ParkingSession.entry_time.desc()).first())
            if not s:
                continue
            try:
                # close_session_forced нь AWAITING_PAYMENT + exit_time үед
                # төлбөрийг ТЭР ЦАГТ царцаадаг — логийн цагийг хүчинтэй болгоно
                s.exit_time = t
                s.exit_confirmed = True
                if s.status == "OPEN":
                    s.status = "AWAITING_PAYMENT"
                due = close_session_forced(db, s, "camera_sync_exit", "system",
                                           create_comp=rules["create_debt"])
                db.add(AuditLog(username="system", action="CAMERA_SYNC_EXIT",
                                entity="session", entity_id=s.id,
                                detail={"plate": p, "exit": t.isoformat(),
                                        "entry": s.entry_time.isoformat(), "debt": due}))
                db.commit()
                closed_by_log += 1
                log_fee += float(s.total_fee or 0)
            except Exception as e:  # noqa: BLE001
                db.rollback()
                log.warning("%s %s: логийн гарах уншилтаар хааж чадсангүй — %r",
                            site.name, p, e)
    if closed_by_log:
        log.info("%s: логийн ГАРАХ уншилтаар %d бүртгэл хаагдлаа (%.0f₮)",
                 site.name, closed_by_log, log_fee)

    paused_n, resumed_n = _sync_inner(db, site, cam, watermark, horizon, rules, dry_run)

    # Watermark-ыг УРАГШ нь л зөөнө (боловсруулсан хамгийн сүүлийн event хүртэл)
    if not dry_run:
        # ЧУХАЛ: ОРОХ камер уншигдаагүй бол watermark-ыг УРАГШЛУУЛАХГҮЙ.
        # Өмнө нь `default=horizon` байсан тул орох камер алдаа өгөхөд (эсвэл
        # гацахад) `entries` хоосон болж, watermark нь «одоо» руу үсэрдэг байв —
        # уншиж амжаагүй БҮХ орох event үүрд алдагдана (watermark хэзээ ч ухрахгүй).
        # 2026-08-12-нд 22 камерын 9 нь гацсан байхад энэ нь идэвхтэй ажиллаж
        # байсан: нөхөлт хийх ёстой хэрэгсэл өөрөө өгөгдлөө алдаж байв.
        entry_cams = [c for c in cam["cameras"] if (c.get("lane_dir") or "entry") != "exit"]
        entry_broken = [c for c in entry_cams if c.get("error")]
        if entry_broken and not entries:
            log.warning("%s: ОРОХ камер уншигдсангүй (%s) — watermark хэвээр "
                        "үлдээв, дараагийн удаа дахин оролдоно",
                        site.name, ", ".join(c["ip"] for c in entry_broken))
            new_mark = watermark
        else:
            new_mark = max([e["time"] for e in entries],
                           default=(watermark if entry_broken else horizon))
        new_mark = max(new_mark, watermark)
        state[site.id] = new_mark.isoformat()
        set_state(db, CAMSYNC_STATE, state)
        db.commit()
    return {"site": site.name, "created": created, "skipped": skipped,
            "closed_by_log": closed_by_log,
            "inner_paused": paused_n, "inner_resumed": resumed_n,
            "debt": debt_total, "from": watermark.isoformat(),
            "to": horizon.isoformat()}


def run_once(dry_run: bool = False) -> list:
    """Бүх идэвхтэй зогсоолыг нэг удаа sync хийнэ.

    ЧУХАЛ: энэ функц дотроо `asyncio.run` ашигладаг (камер руу зэрэг хандахад)
    тул event loop-ийн ДОТРООС дуудаж БОЛОХГҮЙ — supervisor нь
    `asyncio.to_thread`-ээр дуудна. Ингэснээр ачаалалтай үед хаалт нээх/LPR
    боловсруулалт саатахгүй (камерын хүсэлт 15с хүртэл үргэлжилдэг).

    Мөн ДАВХЦАХААС хамгаална: хуваарийн ажиллагаа явж байхад оператор
    «Яг одоо нөхөх» дарвал хоёр дахь нь шууд буцна (нэг event хоёр
    процессоор боловсруулагдаж давхар бүртгэл үүсэхээс сэргийлнэ)."""
    if not _LOCK.acquire(blocking=False):
        log.info("камерын лог нөхөлт аль хэдийн ажиллаж байна — алгаслаа")
        return [{"site": "-", "created": 0, "skipped": 0,
                 "note": "аль хэдийн ажиллаж байна"}]
    try:
        return _run_once_locked(dry_run)
    finally:
        _LOCK.release()


def _run_once_locked(dry_run: bool = False) -> list:
    db = SessionLocal()
    out = []
    try:
        rules = get_camsync_rules(db)
        if not rules["enabled"] and not dry_run:
            return []
        for site in db.query(ParkingSite).filter(ParkingSite.is_active.is_(True)).all():
            try:
                out.append(sync_site(db, site, rules, dry_run=dry_run))
            except Exception as e:  # noqa: BLE001
                db.rollback()
                log.error("%s sync алдаа: %r", site.name, e)
                out.append({"site": site.name, "error": str(e)[:200]})
        total = sum(r.get("created", 0) for r in out)
        if total:
            log.info("камерын лог нөхөлт: %d бүртгэл нэмэгдлээ", total)
    finally:
        db.close()
    return out


def reset_watermark(site_id: str | None = None):
    """Watermark-ыг тэглэнэ (дахин уншуулах шаардлагатай үед — болгоомжтой)."""
    db = SessionLocal()
    try:
        state = get_state(db, CAMSYNC_STATE)
        if site_id:
            state.pop(site_id, None)
        else:
            state = {}
        set_state(db, CAMSYNC_STATE, state)
        db.commit()
    finally:
        db.close()
