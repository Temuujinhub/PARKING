"""LPR event-ийн зураг (snapshot) хадгалах.

Хоёр эх сурвалж:
1. ITSAPI push payload доторх base64 зураг (камер "Picture Upload" идэвхтэй үед)
2. Камерын /cgi-bin/snapshot.cgi — event ирмэгц серверээс татна (CGI poll горимд ч ажиллана)

Хаалт нээх хурдыг удаашруулахгүйн тулд зургийг АРД НЬ (asyncio task) татаж,
бэлэн болмогц session-ий entry_snapshot/exit_snapshot баганад замыг бичнэ.
Файл: {snapshot_dir}/YYYYMMDD/{plate}_{HHMMSS}_{entry|exit}.jpg
"""
import asyncio
import base64
import logging
import os
import re
from datetime import datetime

import httpx

from ..config import settings
from .device_auth import camera_credentials
from ..database import SessionLocal

log = logging.getLogger("parking.snapshot")

_SAFE = re.compile(r"[^0-9A-ZА-ЯЁӨҮ]")


def _payload_picture(raw: dict) -> bytes | None:
    """ITSAPI payload-аас base64 зураг хайна (боломжит бүх байрлал)."""
    if not isinstance(raw, dict):
        return None
    def content(node):
        return node.get("Content") if isinstance(node, dict) else None

    pic = raw.get("Picture")
    pic = pic if isinstance(pic, dict) else {}
    candidates = [content(pic.get("NormalPic")), content(raw.get("NormalPic")),
                  content(pic), raw.get("PicData"), content(pic.get("CutoutPic"))]
    for candidate in candidates:
        if not isinstance(candidate, str):
            continue
        try:
            data = base64.b64decode(candidate, validate=True)
        except (ValueError, TypeError):
            continue
        if valid_jpeg(data):
            return data
    return None


# Камер тус бүрийн snapshot.cgi төлөв: аль URL хувилбар ажилладаг, дараалсан
# бүтэлгүйтлийн тоо, хэдий хүртэл түр зогсоосон. Камерын лог/сешнийг дэмий
# эзлэхгүйн тулд «ажиллахгүй бол оролдохоо болих» зарчмыг хэрэгжүүлнэ.
_CGI_STATE: dict[str, dict] = {}


def cgi_state() -> dict:
    """Оношилгоонд: камер бүрийн snapshot.cgi төлөв."""
    import time as _t
    now = _t.monotonic()
    return {ip: {"url": v["url"], "fails": v["fails"],
                 "quiet_sec": max(0, round(v["quiet_until"] - now))}
            for ip, v in _CGI_STATE.items()}


async def _fetch_from_camera(ip: str, creds: tuple[str, str] | None = None) -> bytes | None:
    """One bounded CGI capture per camera; never bypass the control lock."""
    import time
    from .barrier import _rpc_lock, barrier_is_waiting, camera_client, note_rpc_done

    st = _CGI_STATE.setdefault(ip, {"url": None, "fails": 0, "quiet_until": 0.0})
    if time.monotonic() < st["quiet_until"] or ip in _cgi_inflight:
        return None
    _cgi_inflight.add(ip)
    lock = _rpc_lock(ip)
    held = False
    attempted = False
    last_error = "capture deadline exceeded"
    try:
        async with asyncio.timeout(settings.snapshot_cgi_budget_sec):
            deadline = time.monotonic() + settings.snapshot_barrier_wait_sec
            while barrier_is_waiting(ip):
                if time.monotonic() >= deadline:
                    return None
                await asyncio.sleep(0.1)
            try:
                await asyncio.wait_for(lock.acquire(), settings.snapshot_lock_wait_sec)
                held = True
            except TimeoutError:
                return None
            if barrier_is_waiting(ip):
                return None
            auth = httpx.DigestAuth(*(creds or camera_credentials(None)))
            timeout = httpx.Timeout(connect=3.0, read=settings.snapshot_cgi_budget_sec,
                                    write=3.0, pool=3.0)
            urls = [st["url"]] if st["url"] else [
                f"http://{ip}/cgi-bin/snapshot.cgi?channel=1",
                f"http://{ip}/cgi-bin/snapshot.cgi",
                f"http://{ip}/cgi-bin/snapshot.cgi?channel=0"]
            for url in urls:
                if barrier_is_waiting(ip):
                    return None
                attempted = True
                response = await camera_client(ip).get(url, auth=auth, timeout=timeout)
                if response.status_code == 200 and valid_jpeg(response.content):
                    st["url"], st["fails"] = url, 0
                    return response.content
                last_error = f"HTTP {response.status_code} ({len(response.content)} bytes)"
                # Only a rejected channel/URL justifies trying another form.
                if response.status_code not in (400, 404):
                    break
    except (TimeoutError, httpx.HTTPError) as exc:
        last_error = type(exc).__name__
    finally:
        if held:
            note_rpc_done(ip)
            lock.release()
        _cgi_inflight.discard(ip)
    if attempted:
        st["url"] = None
        st["fails"] += 1
        if st["fails"] >= settings.snapshot_cgi_max_fails:
            st["quiet_until"] = time.monotonic() + settings.snapshot_cgi_quiet_minutes * 60
            st["fails"] = 0
        log.warning("%s: snapshot.cgi failed: %s", ip, last_error)
    return None


_cgi_inflight: set[str] = set()


# Зургийн доод хэмжээ. ANPR-Viewer клиентийн туршлагаас (docs/CAMERA_IMAGE_CAPTURE.md):
# камер завгүй үедээ хэдэн зуун байтын хагас/эвдэрсэн JPEG өгдөг — тэдгээрийг
# хадгалбал кассын дэлгэцэнд «эвдэрсэн зураг» дүрс гарч, нотолгооны үнэ цэнэгүй.
_MIN_JPEG_BYTES = 1000


def jpeg_score(data: bytes | None) -> tuple[int, float] | None:
    """Decode completely and rank by usable resolution then edge detail, not bytes.

    Bounded to 20 MB / 16 MP. Padding cannot improve the score. This is an
    image-quality heuristic; it does not assert OCR correctness.
    """
    if not data or len(data) > 20_000_000 or data[:2] != b"\xff\xd8":
        return None
    from io import BytesIO
    from PIL import Image, ImageFilter, ImageStat, UnidentifiedImageError
    import warnings
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as im:
                if im.format != "JPEG" or min(im.size) < 32 or im.width * im.height > 16_000_000:
                    return None
                im.load()  # Reject truncated/corrupt entropy data, not just the SOI marker.
                area = im.width * im.height
                gray = im.convert("L")
                gray.thumbnail((256, 256))
                detail = ImageStat.Stat(gray.filter(ImageFilter.FIND_EDGES)).var[0]
                return area, detail
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError,
            Image.DecompressionBombWarning):
        return None


def valid_jpeg(data: bytes | None) -> bool:
    return jpeg_score(data) is not None


def attach_saved(db, session, lane_dir: str, rel: str, source: str) -> bool:
    """One atomic attachment shared by push, comet, WS and CGI adapters.

    Event pictures may replace an unverified live CGI frame. Otherwise the first
    selected event picture wins. A stale writer never overwrites a newer path.
    """
    from ..models import ParkingSession
    field = "exit_snapshot" if lane_dir == "exit" else "entry_snapshot"
    source_field = field + "_source"
    old = getattr(session, field)
    old_source = getattr(session, source_field)
    if old and not (old_source == "snapshot.cgi" and source != "snapshot.cgi"):
        return False
    column = getattr(ParkingSession, field)
    count = (db.query(ParkingSession).filter(ParkingSession.id == session.id,
             column == old).update({field: rel, source_field: source}, synchronize_session=False))
    if count != 1:
        return False
    db.commit()
    if old and old != rel:
        discard_saved(old)
    return True


def _save(data: bytes, plate: str, lane_dir: str) -> str | None:
    """Зургийг диск рүү бичээд snapshot_dir-ээс хамаарах замыг буцаана.

    БҮХ эх сурвалж (payload/стрим/comet/WS/snapshot.cgi/нөхөн таталт) энэ
    функцээр дамждаг тул валидаци энд төвлөрнө:
      • JPEG биш / хэт жижиг өгөгдлийг хадгалахгүй (эвдэрсэн зураг session-д
        холбогдвол нотолгоо алдагдана — байхгүй нь дээр, нөхөн татаж болно)
      • tmp файлд бичээд rename хийнэ — бичилтийн дундуур унасан ч хагас
        файл session-д холбогдохгүй (rename нь атомар)
      • нэрэнд богино санамсаргүй дагавар — нэг секундэд ижил дугаар хоёр
        уншигдвал (орох+гарах камер зэрэг) файл дарж бичихгүй"""
    if not valid_jpeg(data):
        log.warning("%s %s: эвдэрсэн/дутуу зураг (%dб) — хадгалсангүй",
                    plate, lane_dir, len(data or b""))
        return None
    now = datetime.utcnow()
    day = now.strftime("%Y%m%d")
    safe_plate = _SAFE.sub("", plate.upper()) or "UNKNOWN"
    suffix = os.urandom(2).hex()
    rel = os.path.join(day, f"{safe_plate}_{now.strftime('%H%M%S')}_{suffix}_{lane_dir}.jpg")
    full = os.path.join(settings.snapshot_dir, rel)
    try:
        os.makedirs(os.path.dirname(full), exist_ok=True)
        tmp = full + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, full)
        return rel
    except OSError as e:
        log.error(f"хадгалж чадсангүй: {e}")
        return None


def discard_saved(rel: str | None) -> None:
    """Session-д холбогдоогүй (давхар болсон) зургийг диск дээрээс арилгана —
    retention хүртэл орфон файл хэвтүүлэхгүй."""
    if not rel:
        return
    try:
        os.remove(os.path.join(settings.snapshot_dir, rel))
    except OSError:
        pass


# ─── CGI event стримээр ирсэн зураг ─────────────────────────────────────────
# Dahua eventManager.cgi?action=attach нь multipart стрим: `data={...}` JSON-ы
# ХАЖУУГААР тухайн event-ийн ЖИНХЭНЭ кадрыг binary JPEG хэсгээр илгээдэг. Өмнө нь
# стримийг ТЕКСТЭЭР уншдаг байсан тул тэр зураг мөхөж, бид үргэлж snapshot.cgi
# рүү унадаг байв. Үр дагавар нь хоёр талдаа муу:
#   • snapshot.cgi нь камер дээр «Manual Snapshot» бичлэг үүсгэдэг — сүлжээний
#     инженер «танай систем давхар manual event үүсгэж байна» гэж зөв хэлсэн;
#   • тэр нь ОДООГИЙН кадр тул машин хаалганаас гарсны дараа авагдаж, зураг дээр
#     машин байхгүй/сүүлээрээ харагддаг (2026-08-09).
# Одоо cgi_poller стримээс JPEG-ийг таслан авч энд өгнө; capture нь эхлээд
# үүнийг хүлээгээд, зөвхөн ирээгүй үед snapshot.cgi рүү унана.
_stream_seen: dict[str, float] = {}                   # ip → сүүлд зураг ирсэн үе

# Эх сурвалж бүрээр session-д ХАДГАЛАГДСАН зургийн тоо. «Шинэ суваг ажиллаж
# байна уу» гэдгийг лог ухалгүй тоогоор хариулна (/api/admin/cameras/snap-state).
_src_counts: dict[str, int] = {}


def note_source(src: str) -> None:
    _src_counts[src] = _src_counts.get(src, 0) + 1


def source_counts() -> dict:
    return dict(sorted(_src_counts.items(), key=lambda kv: -kv[1]))

# Камер зураг өгдөг гэдгээ нэг удаа баталсны дараа энэ хугацаанд «өгдөг» гэж
# итгэнэ. Итгэхгүй бол хүлээхгүй → зан төлөв хуучнаараа (шууд snapshot.cgi).
_STREAM_TRUST_SEC = 3600.0
# Зураг нь JSON event-ээс ӨМНӨ ирж болно — event-ийн цагаас өмнөх энэ мужийг зөвшөөрнө


def offer_stream_image(ip: str, data: bytes, src: str = "event-stream") -> None:
    """Record image-channel health; unidentified JPEG bytes are never attached."""
    import time as _time
    if not ip or not data:
        return
    now = _time.monotonic()
    # Record channel health only; do not cache an unidentified frame.
    if ip not in _stream_seen:
        log.info("%s: %s image channel observed (%d bytes); unidentified frame ignored",
                 ip, src, len(data))
    _stream_seen[ip] = now


def stream_delivers(ip: str) -> bool:
    """Энэ камер event стримээрээ зураг өгдөг нь батлагдсан уу."""
    import time as _time
    return _time.monotonic() - _stream_seen.get(ip, -1e9) < _STREAM_TRUST_SEC


async def _snapshot_written(session_id: str, lane_dir: str) -> bool:
    """snap_puller энэ session-д зургаа аль хэдийн холбосон эсэх (DB-ээс)."""
    from ..models import ParkingSession
    db = SessionLocal()
    try:
        s = db.get(ParkingSession, session_id)
        return bool(s and (s.exit_snapshot if lane_dir == "exit" else s.entry_snapshot))
    except Exception:  # noqa: BLE001
        return False
    finally:
        db.close()


def image_channel_alive(ip: str) -> bool:
    """Энэ камераас event зураг ӨӨРӨӨ ирэх боломжтой юу — ямар нэг зургийн
    суваг (CGI стрим / comet / WS) АМЬД байна уу.

    2026-09-14: зогсоолуудаас «event бүрд давхар Manual Snapshot үүсгэж байна»
    гэсэн гомдол. Шалтгаан: snapshot.cgi-г хойшлуулах эсэхийг «сүүлийн 30 мин /
    1 цагт зураг ӨГСӨН үү» гэж хэмждэг байсан тул шөнийн чимээгүй байдлын дараах
    ЭХНИЙ машин, backend дахин ассаны дараах эхний машин бүрд суваг холбоотой
    атлаа snapshot.cgi ШУУД дуудагдаж, дараа нь comet-ийн жинхэнэ зураг давхар
    ирдэг байв. Одоо сувгийн ХОЛБОЛТ (attach) амьд бол зураг өгсөн түүхгүй ч
    хүлээнэ; суваггүй камерт зан төлөв хэвээр (шууд snapshot.cgi — тэнд өөр
    зам байхгүй, хойшлуулбал машин өнгөрчихнө)."""
    if not ip:
        return False
    if stream_delivers(ip):
        return True
    from .snap_puller import comet_attached, puller_delivers
    return comet_attached(ip) or puller_delivers(ip)


async def _wait_camera_image(session_id: str, camera_ip: str, lane_dir: str,
                             t0: float) -> bool | None:
    """Wait once for a correlated comet/WS event image to be attached."""
    import time as _time
    deadline = _time.monotonic() + max(settings.snapshot_wait_event_sec,
                                       settings.snapshot_stream_wait_sec)
    tick = 0
    while True:
        if tick % 4 == 0 and await _snapshot_written(session_id, lane_dir):
            return True
        if _time.monotonic() >= deadline:
            return None
        tick += 1
        await asyncio.sleep(min(0.25, max(0, deadline - _time.monotonic())))


async def _capture_and_store(session_id: str, camera_ip: str, plate: str,
                             lane_dir: str, raw: dict,
                             creds: tuple[str, str] | None = None):
    import time as _time
    t0 = _time.monotonic()
    data = _payload_picture(raw)
    source = "payload"
    # Логоос НӨХСӨН (log_tail) event: машин аль хэдийн өнгөрсөн тул амьд кадр
    # (snapshot.cgi) нь тэр машин биш — хуурамч нотолгоо + камер дээр илүүц
    # Manual Snapshot. Payload-д зураг байхгүй бол зураггүй үлдээнэ.
    if data is None and isinstance(raw, dict) and raw.get("log_tail"):
        return
    # Энэ session-д (энэ чиглэлд) зураг АЛЬ ХЭДИЙН байвал юу ч хийхгүй — гарах
    # хаалтан дээр төлбөр хүлээж зогссон машин dedup цонхны дараа ДАХИН уншигдах
    # бүрд snapshot.cgi дуудагдаж, дараа нь «аль хэдийн бий» гээд хаядаг байв
    # (камер дээр Manual Snapshot бичлэг л үлддэг байсан).
    if data is None and await _snapshot_written(session_id, lane_dir):
        return
    if data is None and camera_ip:
        if image_channel_alive(camera_ip):
            # 1) Камер ӨӨРӨӨ зургаа илгээх суваг амьд — түүнийг НЭГ цонхонд
            #    хүлээнэ (CGI стрим/comet санамж + comet/WS-ийн session холболт).
            #    Энэ нь хамгийн зөв зураг: машин яг хаалганы өмнө байх агшны
            #    кадр бөгөөд камер дээр ямар ч нэмэлт бичлэг үүсгэхгүй.
            got = await _wait_camera_image(session_id, camera_ip, lane_dir, t0)
            if got is True:
                log.info(f"{plate} {lane_dir}: event зураг сувгаар ирлээ — snapshot.cgi алгасав")
                return
            log.info("%s %s: event image deadline expired", plate, lane_dir)
    if data is None and camera_ip:
        # 2) Эцсийн арга — snapshot.cgi. Энэ нь камер дээр «Manual Snapshot»
        #    бичлэг үүсгэдэг БӨГӨӨД амьд кадр тул машин аль хэдийн өнгөрсөн байж
        #    болно. Стримийн зураг ажиллаж эхэлсэн зогсоолд .env-ээс
        #    PARKING_SNAPSHOT_CGI_FALLBACK=false гэж бүрмөсөн унтраана.
        if not settings.snapshot_cgi_fallback:
            log.info(f"{plate} {lane_dir}: event зураг ирсэнгүй, snapshot.cgi унтраалттай "
                     f"— зураггүй үлдлээ (камер {camera_ip})")
            return
        # Хүлээх хугацаанд өөр суваг холбочихсон байж болно — сүүлчийн шалгалт
        # snapshot.cgi-г дуудахаас ӨМНӨ (өмнө нь ДАРАА нь шалгаад хаядаг байв)
        if await _snapshot_written(session_id, lane_dir):
            log.info(f"{plate} {lane_dir}: event зураг аль хэдийн бий — snapshot.cgi алгасав")
            return
        data = await _fetch_from_camera(camera_ip, creds)
        source = "snapshot.cgi"
    if data is None:
        log.warning(f"{plate} {lane_dir}: зураг ОЛДСОНГҮЙ (payload-д алга, камер {camera_ip or '-'})")
        return
    # Дискэнд бичихийн ӨМНӨ: snap_puller/comet энэ хооронд жинхэнэ event зургаа
    # холбочихсон байж болно — тэгвэл энд юу ч бичилгүй гарна (өмнө нь давхар
    # файл бичээд ДАРАА нь «аль хэдийн бий» гэж шалгадаг байсан тул retention
    # хүртэл орфон файлууд хуримтлагддаг байв).
    if await _snapshot_written(session_id, lane_dir):
        log.info(f"{plate} {lane_dir}: event зураг аль хэдийн бий — {source} алгасав")
        return
    # Дискний бичилт (≈1MB JPEG) нь SYNC — event loop дээр шууд хийвэл тэр хугацаанд
    # дараагийн машины хаалт нээх команд ХҮЛЭЭДЭГ (1 vCPU дээр мэдэгдэхүйц).
    # Тусдаа thread дээр бичнэ.
    rel = await asyncio.to_thread(_save, data, plate, lane_dir)
    if not rel:
        return
    # Session мөр commit хийгдэж амжаагүй байж болзошгүй (payload зурагтай үед
    # capture агшин зуур дуусдаг) — олдохгүй бол багахан хүлээгээд дахин оролдоно.
    from ..models import ParkingSession
    # Мөрийн ТҮГЖЭЭ: hand_exit/pos_confirm нь session мөрийг өөрчлөөд хаалт нээх/
    # e-Barimt үүсгэх хугацаанд транзакцаа нээлттэй барьдаг. Тэр үед энэ UPDATE
    # lock_timeout (10с)-д унадаг байв — зураг хадгалагдсан ч замыг нь бичиж
    # чадахгүй, task чимээгүй уначихдаг. Одоо түгжээний алдааг тусад нь барьж
    # хүлээгээд дахин оролдоно (оролдлого бүрд хүлээх хугацаа уртсана).
    from sqlalchemy.exc import OperationalError
    attempts = 5
    for attempt in range(attempts):
        db = SessionLocal()
        try:
            s = db.get(ParkingSession, session_id)
            if s:
                # snap_puller (жинхэнэ event зураг) түрүүлж бичсэн бол дарж бичихгүй —
                # snapshot.cgi нь ердөө "одоогийн кадр" тул чанараар дутуу
                site_id = s.site_id
                if not attach_saved(db, s, lane_dir, rel, source):
                    await asyncio.to_thread(discard_saved, rel)
                    return
                note_source(source)
                log.info(f"{plate} {lane_dir}: OK ({source}, {len(data)}b) → {rel}")
                # UI-д зураг бэлэн болсныг мэдэгдэнэ (ANPR-Viewer-ийн imageUpdate
                # SSE-тэй ижил санаа): касс дээр аль хэдийн нээгдсэн машины
                # зургийг хуудас refresh хийлгүй харуулна
                from ..ws import notify
                notify(site_id, "SNAPSHOT_READY",
                       {"session_id": session_id, "kind": lane_dir, "plate": plate})
                return
        except OperationalError as e:
            # Түгжээ чөлөөлөгдөхийг хүлээнэ (хаалт нээх/e-Barimt дуустал)
            db.rollback()
            if attempt + 1 >= attempts:
                log.warning("%s %s: session мөр %d удаа түгжээтэй байлаа — зам бичигдээгүй "
                            "(зураг диск дээр хадгалагдсан: %s)", plate, lane_dir, attempts, rel)
                await asyncio.to_thread(discard_saved, rel)
                return
            log.info("%s %s: session мөр түгжээтэй — %dс хүлээгээд дахин оролдоно (%d/%d)",
                     plate, lane_dir, attempt + 2, attempt + 1, attempts)
        finally:
            db.close()
        await asyncio.sleep(attempt + 1)   # 1, 2, 3, 4с — түгжээ ихэвчлэн 15с дотор тайлагдана
    await asyncio.to_thread(discard_saved, rel)
    log.warning(f"{plate} {lane_dir}: matching session not found")


def schedule_capture(session_id: str | None, camera_ip: str | None, plate: str,
                     lane_dir: str, raw: dict, creds: tuple[str, str] | None = None):
    """Event боловсруулалтын дараа дуудна — зургийг ард нь татаж хадгална.
    Хаалт нээх/WS broadcast-ыг хэзээ ч хүлээлгэхгүй."""
    if not settings.snapshot_enabled or not session_id:
        return
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return  # event loop-гүй орчин (тест г.м) — алгасна
    key = (session_id, lane_dir)
    if key in _capture_tasks:
        return
    task = asyncio.create_task(_capture_and_store(session_id, camera_ip or "", plate, lane_dir, raw, creds))
    _capture_tasks[key] = task
    def done(finished):
        _capture_tasks.pop(key, None)
        if not finished.cancelled() and finished.exception():
            log.error("Snapshot capture failed for %s/%s: %s", *key, finished.exception())
    task.add_done_callback(done)


_capture_tasks: dict[tuple[str, str], asyncio.Task] = {}
