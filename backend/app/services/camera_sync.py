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

    # Watermark-ыг УРАГШ нь л зөөнө (боловсруулсан хамгийн сүүлийн event хүртэл)
    if not dry_run:
        new_mark = max([e["time"] for e in entries], default=horizon)
        new_mark = max(new_mark, watermark)
        state[site.id] = new_mark.isoformat()
        set_state(db, CAMSYNC_STATE, state)
        db.commit()
    return {"site": site.name, "created": created, "skipped": skipped,
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
