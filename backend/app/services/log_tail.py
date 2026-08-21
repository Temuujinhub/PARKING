"""Камерын логийн БОГИНО МӨЧЛӨГИЙН таталт — event стрим чимээгүй үед нөөц зам.

ЯАГААД: `cgi_poller` нь камерт `eventManager.cgi?action=attach`-аар холбогдож
event хүлээдэг. Камерыг ӨӨР СИСТЕМ зэрэг ашиглаж байвал (Рашбулаг ЭТТ,
2026-08-16: дөрвүүлээ `admin@172.16.100.20`-той хуваалцаж байна) холболт
`200 OK` авсан хэрнээ event огт ирэхгүй «чимээгүй attach» болдог. Тэр үед:
  • хаалт автоматаар нээгдэхгүй → оператор гараар нээнэ
  • LED юу ч бичихгүй, төлбөр нэхэгдэхгүй
  • 30 минутын дараа `camera_sync` бүртгэлийг үүсгээд ШУУД хааж, машин аль
    хэдийн явчихсан байдаг (2026-08-16: 3 хоногт 337,000₮ аваагүй)

Камерын ӨӨРИЙН доторх бичлэг (RecordFinder) эдгээр уншилтыг МЭДДЭГ. Энэ
модуль түүнийг богино мөчлөгөөр (default 20с) татаж, амьд event-тэй ЯГ ИЖИЛ
замаар (`handle_entry`/`handle_exit`/`handle_inner_pass`) боловсруулна —
хаалт нээгдэж, LED бичигдэж, төлбөр нэхэгдэнэ.

ЗАРЧИМ:
  1. ЗӨВХӨН ЧИМЭЭГҮЙ камерт. Стрим ажиллаж байгаа камерыг огт хөндөхгүй —
     камерын нөөц хязгаартай бөгөөд илүү хандалт нь өөрөө асуудал үүсгэдэг.
  2. ЦАГИЙН МУЖААР БИШ, сүүлийн бичлэгээр. Камерын цаг NTP-гүй бол гулсдаг
     (Рашбулаг: +32 минут) тул нарийн муж асуувал юу ч олдохгүй. Өргөн муж
     асууж, ХАМГИЙН СҮҮЛИЙН бичлэгүүдийг авна.
  3. ДАВХАРДАХГҮЙ. Боловсруулсан бичлэгийг (камер+дугаар+камерын цаг) санана;
     нэмээд `lpr_events`-д ойрын мөр байвал алгасана (стрим сэргэсэн байж
     болно). Хоёр давхар хамгаалалт — давхар session үүсгэх нь өр/тайланг
     эвддэг.
"""
import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

from ..config import settings
from ..database import SessionLocal
from ..models import Device, LprEvent, ParkingSite
from ..session_logic import (handle_entry, handle_exit, handle_inner_pass,
                             normalize_plate)
from .camera_records import fetch_snap_events, normalized_plate
from .device_auth import camera_credentials

log = logging.getLogger("parking.log_tail")

# device_id → сүүлд боловсруулсан бичлэгүүдийн түлхүүр (санах ойд, хязгаартай)
_seen: dict[str, set] = {}
_SEEN_MAX = 400
# device_id → сүүлийн амжилттай таталтын monotonic үе (лог хэт бичихгүйн тулд)
_last_pull: dict[str, float] = {}


def _key(plate: str, t: datetime) -> str:
    return f"{plate}@{int(t.timestamp())}"


def may_open(gap_sec: float, interval_sec: float | None = None) -> bool:
    """Логоос сэргээсэн уншилтад ХААЛТ НЭЭХ эрх өгөх үү.

    gap_sec = энэ камерыг сүүлд амжилттай татсанаас хойшх хугацаа. Хэвийн
    ажиллагаанд энэ нь ~1 мөчлөг (20с) тул уншилт шинэхэн — жолооч гарцад
    хүлээж байгаа байх магадлалтай, хаалт нээгдэх ЁСТОЙ. Завсар том бол
    (сервис саяхан асcан, камер удаан хүрэхгүй байсан) уншилт хэдэн минутын
    өмнөх байж болно — машин яваад өгсөн, хаалт нээх нь эзэнгүй онгорхой
    хаалт үүсгэнэ (уншуулалгүй нэвтрэх нүх).

    Босго нь `log_tail_open_max_lag_sec` (default 90с) — гэхдээ 3 мөчлөгөөс
    доош БУУХГҮЙ: чимээгүй камер олон болж мөчлөг удаашрахад (concurrency=3)
    хэвийн ажиллагаа «хуучин» гэж ангилагдаад хаалт огт нээхгүй болох вий.
    """
    iv = settings.log_tail_interval_sec if interval_sec is None else interval_sec
    return gap_sec <= max(settings.log_tail_open_max_lag_sec, max(1.0, iv) * 3)


def _remember(device_id: str, key: str) -> None:
    s = _seen.setdefault(device_id, set())
    if len(s) >= _SEEN_MAX:
        s.clear()   # энгийн эргэлт — цонх богино тул алдагдал үүсгэхгүй
    s.add(key)


async def _silent_devices(db, site_id: str | None = None) -> list[Device]:
    """Стрим нь чимээгүй байгаа камерууд. «Чимээгүй» = СТРИМЭЭР сүүлд ирсэн
    уншилтаас хойш `log_tail_silence_sec` өнгөрсөн. Шөнө машин ирэхгүй үед ч
    чимээгүй болох тул энэ нь ГАЦСАН гэсэн үг биш — гэхдээ логийг татах нь
    хямд, машин байхгүй бол хоосон буцна.

    ЧУХАЛ: ӨӨРИЙНХӨӨ оруулсан уншилтыг (raw.log_tail=true) тооцохгүй. Өмнө нь
    тооцдог байсан тул log_tail нэг уншилт оруулмагц камер «амьд» болж харагдаж,
    дараагийн таталт `silence_sec` (180с) хүлээдэг байв — үр дүнд нь мөчлөг нь
    20 секунд БИШ, 180+20=200 секунд болж:
      • бодит машины уншилт 200 секунд ХОЦРОН ирж, хаалт нь машин яваад
        өгсний ДАРАА нээгддэг («машин байхгүй атал хаалт нээгдэж байна»),
      • мөчлөг тутамд НЭГ уншилт боловсруулдаг тул дараалал үүсч,
        `log_tail_fresh_sec` (240с)-ээс хуучирсан уншилтууд ХАЯГДАЖ, машин
        огт бүртгэгдэхгүй үлддэг байв (2026-08-21 Рашбулаг ЭТТ дээр
        хаалтны лог яг 200 секундын алхамтай байснаар илэрсэн).
    """
    from sqlalchemy import func, or_

    cams = (db.query(Device).join(ParkingSite, Device.site_id == ParkingSite.id)
            .filter(Device.device_type == "camera", Device.status == "active",
                    ParkingSite.is_active.is_(True),
                    Device.ip_address.isnot(None), Device.ip_address != "")
            .all())
    if site_id:
        cams = [c for c in cams if c.site_id == site_id]
    if not cams:
        return []
    cutoff = datetime.utcnow() - timedelta(seconds=settings.log_tail_silence_sec)
    _injected = LprEvent.raw["log_tail"].as_string()
    last = dict(db.query(LprEvent.device_id, func.max(LprEvent.created_at))
                .filter(LprEvent.device_id.in_([c.id for c in cams]),
                        LprEvent.accepted.is_(True),
                        or_(_injected.is_(None), _injected != "true"))
                .group_by(LprEvent.device_id).all())
    return [c for c in cams if (last.get(c.id) or datetime.min) < cutoff]


async def _pull_one(device_id: str, ip: str, creds, name: str) -> int:
    """Нэг камерын сүүлийн бичлэгийг татаж, ШИНЭ уншилтыг боловсруулна."""
    now = datetime.now(timezone.utc)
    tol = timedelta(minutes=settings.log_tail_skew_tolerance_min)
    win = timedelta(minutes=settings.log_tail_window_min)
    try:
        recs = await asyncio.wait_for(
            fetch_snap_events(ip, creds[0], creds[1], now - win - tol, now + tol),
            timeout=settings.log_tail_timeout_sec)
    except Exception as e:  # noqa: BLE001 — камер завгүй байж болно, дараагийн мөчлөгт
        log.debug("%s (%s): лог татагдсангүй — %s", name, ip, type(e).__name__)
        return 0

    # ── ХААЛТ НЭЭХ ЭРХ: зөвхөн уншилт ШИНЭХЭН бол ───────────────────────────
    # Энэ зам нь хаалт нээдэг (жолооч гарцад хүлээж байгаа бол нээгдэх ЁСТОЙ).
    # Гэвч уншилт хуучин бол машин аль хэдийн яваад өгсөн байна — тэр үед хаалт
    # нээх нь (1) «машин байхгүй атал нээгдэж байна» гэж харагдана, (2) эзэнгүй
    # онгорхой хаалт нь уншуулалгүй нэвтрэх нүх үүсгэнэ.
    #
    # Хуучин эсэхийг КАМЕРЫН ЦАГААР шалгаж БОЛОХГҮЙ — NTP-гүй камерын цаг
    # гулсдаг (Рашбулаг: +32 мин) тул шинэхэн уншилт «хуучин» болж харагдана.
    # Оронд нь ӨӨРИЙН мөчлөгийн завсраар хэмжинэ: сүүлийн амжилттай таталтаас
    # хойш 1 мөчлөгийн зайд байвал уншилт хамгийн ихдээ тэр төдий хуучин.
    # Сервис саяхан асcан/камер удаан хүрэхгүй байсан бол завсар том — тэр үед
    # уншилтыг БҮРТГЭНЭ, харин хаалт НЭЭХГҮЙ.
    _mono = time.monotonic()
    gap = _mono - _last_pull.get(device_id, 0.0)
    _last_pull[device_id] = _mono
    allow_open = may_open(gap)

    rows = []
    for r in recs:
        p = normalized_plate(r)
        t = r.get("Time")
        if not p or not isinstance(t, (int, float)):
            continue
        rows.append((datetime.fromtimestamp(t, tz=timezone.utc).replace(tzinfo=None),
                     normalize_plate(p), r))
    rows.sort()

    # ── ЭХНИЙ УДАА: зөвхөн ТЭМДЭГЛЭНЭ, боловсруулахгүй ──────────────────────
    # Лог 20 минутын мужийг буцаадаг тул сервис ассан даруйдаа хуучин бүх
    # уншилтыг «шинэ» гэж үзэн НЭГ АГШИНД боловсруулах эрсдэлтэй. Тэр нь
    # `handle_entry`-ийн burst логикт (6с дотор ирсэн орох уншилтууд = НЭГ
    # машин) орж, 20 машиныг нэг session болгон нийлүүлж дугаарыг нь дараалан
    # дарж бичдэг (2026-08-16 прод: 42 уншилт → 20 `plate_autocorrect`).
    if device_id not in _seen:
        _seen[device_id] = {_key(p, t) for t, p, _r in rows}
        log.info("%s (%s): анхны таталт — %d хуучин бичлэгийг тэмдэглэв "
                 "(боловсруулаагүй)", name, ip, len(rows))
        return 0

    seen = _seen[device_id]
    # ── ЗӨВХӨН ШИНЭХЭН, ЗӨВХӨН НЭГ ──────────────────────────────────────────
    # Хуучин уншилт нь `camera_sync`-ийн ажил (тэр цагийг нь зөв бичдэг). Энд
    # ЗӨВХӨН саяхны уншилтыг авна — хаалт нээх утга нь тэр л уншилтад байна.
    # Мөн мөчлөг тутамд НЭГ л уншилт: burst цонх (6с) нь серверийн цагаар
    # ажилладаг тул хэд хэдэн уншилтыг зэрэг өгвөл дахин нийлүүлнэ.
    fresh_cut = datetime.utcnow() - timedelta(seconds=settings.log_tail_fresh_sec)
    todo = [r for r in rows if _key(r[1], r[0]) not in seen]
    if len(todo) > 1:
        # ЧИМЭЭГҮЙ БҮҮ ХАЯ: эдгээр нь бодит машинууд бөгөөд энд боловсруулагдахгүй
        # бол хаалт нь нээгдэхгүй, session нь үүсэхгүй. Дараалал үүсч байгаа нь
        # мөчлөг хэт удаан эсвэл урсгал их гэсэн дохио — тоогоор нь харуулна.
        log.warning("[лог-нөөц] %s: нэг мөчлөгт %d уншилт хуримтлагдсан — зөвхөн "
                    "хамгийн сүүлийнхийг боловсруулна, үлдсэн %d нь ХАЯГДАНА (%s)",
                    name, len(todo), len(todo) - 1,
                    ", ".join(p for _t, p, _r in todo[:-1])[:200])
    for t, plate, raw in todo[:-1]:
        # Хуучирсан/илүү уншилтыг ДАХИН авахгүйгээр тэмдэглээд өнгөрнө
        _remember(device_id, _key(plate, t))
    rows = todo[-1:] if todo else []
    if rows:
        # Бичлэгийн НАС нь КАМЕРЫН цагаар хэмжигдэнэ. Камерын цаг серверийнхээс
        # гулссан бол энэ харьцуулалт утгагүй болно — тиймээс ХОЁР ТАЛААС нь
        # шалгана:
        #   • хэт ХУУЧИН  → camera_sync хариуцна (тэр цагийг нь зөв бичдэг)
        #   • ИРЭЭДҮЙН   → камерын цаг гулссан (ж: камер УБ локал цагаар, сервер
        #     UTC-ээр явж байна). Өмнө нь зөвхөн «хуучин»-г шалгадаг байсан тул
        #     ирээдүйн огноотой бичлэг ХЭЗЭЭ Ч хуучин гэж тооцогдохгүй, 6 цагийн
        #     өмнөх уншилт «шинэхэн» гэж орж ирээд ХААЛТЫГ НЭЭДЭГ байв
        #     (2026-08-22 Рашбулаг: дотоод камер +8ц, 19:5x-ийн уншилтууд шөнийн
        #     01:5x-д тоглогдож эзэнгүй хаалт нээсэн).
        age = (datetime.utcnow() - rows[0][0]).total_seconds()
        if age > settings.log_tail_fresh_sec:
            _remember(device_id, _key(rows[0][1], rows[0][0]))
            log.debug("%s: сүүлийн бичлэг хэт хуучин (%s) — camera_sync хариуцна",
                      name, rows[0][0])
            rows = []
        elif age < -settings.log_tail_clock_skew_max_sec:
            _remember(device_id, _key(rows[0][1], rows[0][0]))
            log.warning("[лог-нөөц] %s: КАМЕРЫН ЦАГ ГУЛССАН — бичлэгийн огноо %s нь "
                        "серверийн цагаас %d минут ИРЭЭДҮЙД байна. Бичлэгийн бодит "
                        "нас тодорхойгүй тул хаалт НЭЭХГҮЙ (camera_sync хариуцна). "
                        "Камерын NTP/цагийн бүсийг тааруулна уу.",
                        name, rows[0][0], int(-age / 60))
            rows = []

    done = 0
    for t, plate, raw in rows:
        _remember(device_id, _key(plate, t))
        db = SessionLocal()
        try:
            device = db.get(Device, device_id)
            if not device or device.status != "active":
                return done
            # Стрим сэргээд ижил уншилтыг аль хэдийн боловсруулсан байж болно.
            # Камерын цаг гулсдаг тул СЕРВЕРИЙН цагаар (одоогоос буцаж) хайна —
            # энэ мөчлөг нь бараг бодит цагийн ажиллагаа тул зөв ойролцоолол.
            fresh = datetime.utcnow() - timedelta(
                seconds=settings.log_tail_dedup_sec)
            if db.query(LprEvent.id).filter(
                    LprEvent.device_id == device_id,
                    LprEvent.plate_number == plate,
                    LprEvent.created_at >= fresh).first():
                continue
            raw_ev = {"log_tail": True, "camera_time": t.isoformat(),
                      "TrafficCar": {"PlateNumber": plate}, "opened": allow_open}
            if device.nested_inner:
                res = await handle_inner_pass(db, device, plate, 100.0, raw_ev,
                                              allow_open=allow_open)
            elif device.lane_dir == "exit":
                res = await handle_exit(db, device, plate, 100.0, raw_ev,
                                        allow_open=allow_open)
            else:
                res = await handle_entry(db, device, plate, 100.0, raw_ev,
                                         allow_open=allow_open)
            done += 1
            log.warning("[лог-нөөц] %s %s → %s (стрим чимээгүй байсан тул "
                        "камерын логоос авав)%s", device.lane_dir, plate,
                        res.get("action", "?"),
                        "" if allow_open else
                        f" [мөчлөгийн завсар {gap:.0f}с — уншилт хуучин байж "
                        f"болзошгүй тул ХААЛТ НЭЭГЭЭГҮЙ]")
        except Exception as e:  # noqa: BLE001 — нэг уншилт бусдыг зогсоохгүй
            log.error("[лог-нөөц] %s боловсруулах алдаа: %r", plate, e)
        finally:
            db.close()
    return done


async def run_once(site_id: str | None = None) -> dict:
    """Нэг мөчлөг — чимээгүй камер бүрээс лог татна. Хураангуй буцаана.

    `site_id` өгвөл зөвхөн тэр зогсоолд ажиллана (гараар турших/оношлоход)."""
    db = SessionLocal()
    try:
        cams = await _silent_devices(db, site_id)
        targets = [(c.id, c.ip_address, camera_credentials(c), c.name or c.ip_address)
                   for c in cams]
    finally:
        db.close()
    if not targets:
        return {"silent": 0, "recovered": 0}
    # Камерын нөөцийг хамгаална — зэрэг цөөн хандалт
    sem = asyncio.Semaphore(settings.log_tail_concurrency)

    async def _guarded(t):
        async with sem:
            return await _pull_one(*t)

    got = await asyncio.gather(*(_guarded(t) for t in targets), return_exceptions=True)
    n = sum(x for x in got if isinstance(x, int))
    if n:
        log.warning("лог-нөөц: %d камер чимээгүй, %d уншилтыг логоос сэргээв",
                    len(targets), n)
    return {"silent": len(targets), "recovered": n}


async def supervisor():
    if not settings.log_tail_enabled:
        return
    log.info("идэвхжлээ — чимээгүй камерын логийг %.0fс тутам татна "
             "(чимээгүйн босго %.0fс)", settings.log_tail_interval_sec,
             settings.log_tail_silence_sec)
    # Эхлэхдээ түр хүлээнэ — cgi_poller стримээ барих хугацаа өгнө, эс бол
    # ассан даруйдаа БҮХ камер «чимээгүй» гэж тооцогдоно.
    await asyncio.sleep(settings.log_tail_silence_sec)
    while True:
        t0 = time.monotonic()
        try:
            await run_once()
        except Exception as e:  # noqa: BLE001
            log.error("лог-нөөцийн мөчлөгийн алдаа: %r", e)
        await asyncio.sleep(max(1.0, settings.log_tail_interval_sec
                                - (time.monotonic() - t0)))
