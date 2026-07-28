"""CGI event pull — сервер камераас ANPR event-ийг ТАТАЖ авна (хуучин easy-park шиг).

Механизм: Dahua eventManager.cgi?action=attach&codes=[TrafficJunction] руу байнгын
HTTP холболт нээж, камерын дугаар таних event-ийн урсгалыг (multipart) уншина.
Сервер→камер чиглэлээр ажилладаг тул камер→сервер firewall/config шаардлагагүй.

Идэвхжүүлэх: PARKING_CGI_POLL=true, PARKING_CAMERA_USERNAME/PASSWORD (камерын admin).
Камер бүр Тохиргоо→Төхөөрөмж дээр IP-тэй бүртгэгдсэн байх ёстой.
"""
import asyncio
import json
import logging
import time
from datetime import datetime

import httpx

from ..config import settings
from .device_auth import camera_credentials
from ..database import SessionLocal
from ..models import Device, LprEvent
from ..session_logic import handle_entry, handle_exit, normalize_plate

log = logging.getLogger("parking.cgi_poller")

_tasks: dict[str, asyncio.Task] = {}


def _touch(device_id: str):
    """Стрим амьд байгааг last_seen-д тэмдэглэнэ — событиегүй ч онлайн гэж зөв харагдана."""
    db = SessionLocal()
    try:
        d = db.get(Device, device_id)
        if d:
            d.last_seen = datetime.utcnow()
            db.commit()
    finally:
        db.close()


def _plate_from(data: dict) -> tuple[str, float]:
    """Dahua event data-аас дугаар + итгэлцүүр (олон боломжит байрлал)."""
    for c in (data.get("Plate"), data.get("TrafficCar"),
              (data.get("Picture") or {}).get("Plate"), data):
        if isinstance(c, dict):
            num = c.get("PlateNumber") or c.get("PlateNo") or c.get("plateNumber")
            if num:
                try:
                    conf = float(c.get("Confidence") or c.get("Accuracy") or data.get("Confidence") or 100)
                except (ValueError, TypeError):
                    conf = 100.0
                return str(num), conf
    return "", 0.0


def _extract_json_blocks(buffer: str):
    """buffer-ээс `data={...}` бүрэн JSON блокуудыг гаргаж, үлдэгдэл буфер буцаана."""
    blocks = []
    while True:
        i = buffer.find("data={")
        if i < 0:
            # Буфер хэт томрохоос сэргийлж таслах
            return blocks, buffer[-4096:] if len(buffer) > 8192 else buffer
        start = i + len("data=")
        depth, j, end = 0, start, -1
        while j < len(buffer):
            ch = buffer[j]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = j + 1
                    break
            j += 1
        if end < 0:
            return blocks, buffer[i:]  # бүрэн бус — цааш хүлээнэ
        raw = buffer[start:end]
        try:
            blocks.append(json.loads(raw))
        except Exception:
            pass
        buffer = buffer[end:]


# ─── Producer-Consumer дараалал ──────────────────────────────────────────────
# Стрим уншигч (producer) event-ийг дараалалд шидэж ШУУД цааш уншина; боловсруулалт
# (consumer) нь ард нь явна. Ингэснээр DB-ийн саатал камерын стримийг гацаахгүй.
# Камер бүрд аль codes хувилбар ажилласныг санана (дахин холбогдоход шууд түүгээр)
_codes_ok: dict[str, int] = {}
_queue: asyncio.Queue | None = None
_workers: list = []


def _enqueue(device_id: str, data: dict):
    """Дараалал дүүрсэн ч стримийг гацаахгүй — хамгийн хуучныг хаяна
    (шинэ event нь хуучнаас чухал: машин хаалганы өмнө байна)."""
    global _queue
    if _queue is None:
        _queue = asyncio.Queue(maxsize=settings.camera_event_queue_size)
    item = (device_id, data, time.monotonic())
    try:
        _queue.put_nowait(item)
    except asyncio.QueueFull:
        try:
            _queue.get_nowait()
            _queue.task_done()
            _queue.put_nowait(item)
            log.warning("event дараалал дүүрлээ — хамгийн хуучин event хаягдав")
        except Exception:  # noqa: BLE001
            log.error("event дараалалд нэмж чадсангүй — event АЛДАГДЛАА")


async def _event_worker(idx: int):
    """Дараалалаас авч боловсруулна. Алдаа гарсан ч worker ҮХЭХГҮЙ."""
    global _queue
    if _queue is None:
        _queue = asyncio.Queue(maxsize=settings.camera_event_queue_size)
    log.info("event worker #%d эхэллээ", idx)
    while True:
        device_id, data, ts = await _queue.get()
        try:
            # Дараалалд гацаж ХОЦОРСОН event хаалт нээх эрхгүй: машиныг ажилтан
            # аль хэдийн гараар оруулчихсан байхад хожуу ирсэн команд хоосон
            # зам руу хаалт онгойлгодог байв (Monnis: оруулснаас 20с дараа).
            age = time.monotonic() - ts
            allow_open = age <= settings.camera_event_stale_open_sec
            if not allow_open:
                log.warning("event %.1fс хоцорчээ — бүртгэнэ, хаалт НЭЭХГҮЙ", age)
            await _process_event(device_id, data, allow_open=allow_open)
        except Exception as e:  # noqa: BLE001 — нэг event-ийн алдаа бусдыг зогсоохгүй
            log.error("event боловсруулахад алдаа: %r", e)
        finally:
            _queue.task_done()


def ensure_workers():
    """Worker-уудыг нэг л удаа асаана (supervisor-оос дуудна)."""
    global _workers
    _workers = [w for w in _workers if not w.done()]
    while len(_workers) < settings.camera_event_workers:
        _workers.append(asyncio.create_task(_event_worker(len(_workers) + 1)))


async def _process_event(device_id: str, data: dict, allow_open: bool = True):
    """Нэг ANPR event-ийг боловсруулж session үүсгэнэ."""
    db = SessionLocal()
    try:
        device = db.get(Device, device_id)
        if not device:
            return
        device.last_seen = datetime.utcnow()
        db.commit()  # ямар ч event ирвэл камер онлайн болно
        raw_plate, conf = _plate_from(data)
        plate = normalize_plate(raw_plate)
        if not plate:
            # Traffic/ANPR event боловч дугаар олдоогүй бол л логлоно (heartbeat г.м-ийг алгасна)
            is_traffic = any(k in data for k in ("Plate", "TrafficCar", "Vehicle", "PlateNumber"))
            if is_traffic:
                db.add(LprEvent(site_id=device.site_id, device_id=device.id, plate_number="?",
                                lane_dir=device.lane_dir, confidence=0, accepted=False,
                                reject_reason="CGI: plate not parsed", raw=data))
                db.commit()
            return
        if conf < settings.lpr_min_confidence:
            db.add(LprEvent(site_id=device.site_id, device_id=device.id, plate_number=plate,
                            lane_dir=device.lane_dir, confidence=conf, accepted=False,
                            reject_reason=f"confidence<{settings.lpr_min_confidence}", raw=data))
            db.commit()
            return
        # Шийдвэрийг ЗААВАЛ логлоно — push горимын lpr_router шиг. Үгүй бол
        # «гарах уншилт яагаад хаалт нээгээгүй вэ» гэдгийг лог дээрээс мөшгих
        # аргагүй байв (2026-07-28 Monnis-ийн оношилгооны цоорхой).
        _t0 = time.monotonic()
        if device.lane_dir == "exit":
            res = await handle_exit(db, device, plate, conf, data, allow_open=allow_open)
        else:
            res = await handle_entry(db, device, plate, conf, data, allow_open=allow_open)
        _ms = int((time.monotonic() - _t0) * 1000)
        (log.warning if _ms >= settings.lpr_slow_warn_ms else log.info)(
            "lpr %s %s → %s: %dмс%s", device.lane_dir, plate, res.get("action", "?"), _ms,
            "" if allow_open else " [хоцорсон event — хаалт нээгээгүй]")
    except Exception as e:
        log.error(f"event боловсруулах алдаа: {e}")
    finally:
        db.close()


async def _poll_one(device_id: str, ip: str, creds: tuple[str, str] | None = None):
    """Нэг камерын event stream-ийг тасралтгүй сонсоно (reconnect-тэй).
    codes=[All] — камерын бүх event-ийг авч, дугаартайг нь л боловсруулна (дибагт хялбар)."""
    hb = settings.camera_event_heartbeat_sec
    # codes-ыг .env-ээс тохируулна. Default [All] — firmware бүр өөр код
    # ашигладаг тул юу ч алдахгүй (дибагт ч хялбар). Ачаалал ихтэй эсвэл
    # камерыг ӨӨР СИСТЕМТЭЙ хуваалцаж байгаа үед зөвхөн ANPR-ийг сонсоно:
    #   PARKING_CAMERA_EVENT_CODES=[TrafficJunction,TrafficSnapPicture]
    # Firmware бүр өөр codes хүлээж авдаг. Шинэ firmware (2025+) нь [All]-ыг
    # ТАТГАЛЗАЖ HTTP 400 буцаадаг нь production дээр илэрсэн (2026-07-28, MONNIS
    # гарах камер: 400 Bad Request → стрим огт холбогдоогүй → 9 цаг машин
    # танихгүй). Тиймээс 400 ирвэл дараагийн хувилбар руу ӨӨРӨӨ шилжинэ.
    # Ажилласан хувилбарыг санаж дараа нь шууд түүгээр холбогдоно.
    # Хувилбар бүр нь БҮТЭН URL — codes-оос гадна дараах зүйлс firmware хооронд
    # ялгаатай байдаг: [ ] хаалт URL-encode хийгдсэн эсэх (шинэ firmware хатуу
    # шалгадаг), heartbeat параметр байгаа эсэх/утга нь.
    from urllib.parse import quote as _q

    def _urls(code_list: list[str]) -> list[str]:
        base = f"http://{ip}/cgi-bin/eventManager.cgi?action=attach&codes="
        out = []
        for c in code_list:
            out.append(f"{base}{c}&heartbeat={hb}")          # хэвийн
            out.append(f"{base}{_q(c, safe='')}&heartbeat={hb}")  # хаалт encode хийсэн
            out.append(f"{base}{c}")                          # heartbeat-гүй
            out.append(f"{base}{_q(c, safe='')}")             # encode + heartbeat-гүй
        return out

    codes_list = [settings.camera_event_codes] + [
        v for v in ("[TrafficJunction,TrafficSnapPicture,TrafficControl]",
                    "[TrafficJunction]", "[TrafficSnapPicture]", "[All]")
        if v != settings.camera_event_codes]
    variants = _urls(codes_list)
    vi = _codes_ok.get(device_id, 0)
    cycle_start = vi
    auth = httpx.DigestAuth(*(creds or camera_credentials(None)))
    while True:
        url = variants[vi % len(variants)]
        codes = url.split("codes=")[1].split("&")[0]
        buffer = ""
        try:
            # read timeout: энэ хугацаанд юу ч ирэхгүй бол холболт үхсэн гэж үзэж
            # ReadTimeout шиднэ → reconnect. read=None үед TCP чимээгүй тасрахад
            # (сүлжээний блип, NAT reset) aiter_text мөнхөд хүлээж, камер "Офлайн"
            # болоод хэзээ ч сэргэдэггүй байв.
            # ЧУХАЛ: камер нэгэн зэрэг өөр систем рүү дата илгээж байвал heartbeat
            # саатдаг тул энэ утга ӨГӨӨМӨР байх ёстой (.env-ээс тохируулна).
            async with httpx.AsyncClient(
                    timeout=httpx.Timeout(10, read=settings.camera_event_read_timeout_sec)) as client:
                async with client.stream("GET", url, auth=auth) as resp:
                    if resp.status_code == 400:
                        # Камер ЯАГААД татгалзсаныг өөрөөс нь асууна — Dahua ихэвчлэн
                        # "Error\r\nInvalid param" гэх мэт тодорхой шалтгаан бичдэг.
                        try:
                            body = (await resp.aread()).decode("utf-8", "replace").strip()
                        except Exception:  # noqa: BLE001
                            body = ""
                        vi += 1
                        # Бүх хувилбарыг нэг тойрог туршиж дууссан бол УДААН завсарлана —
                        # эс бол камерыг 2 секунд тутам мөнхөд цохино
                        full_cycle = (vi - cycle_start) >= len(variants)
                        log.warning("%s: татгалзлаа (HTTP 400) codes=%s%s — хариу: %r",
                                    ip, codes,
                                    " [heartbeat-гүй]" if "heartbeat" not in url else "",
                                    body[:200])
                        _codes_ok[device_id] = vi
                        if full_cycle:
                            cycle_start = vi
                            log.error("%s: eventManager.cgi-ийн БҮХ хувилбарыг татгалзлаа. "
                                      "Энэ камер CGI attach дэмжихгүй байж магадгүй — "
                                      "камерын веб → Тохиргоо → Сүлжээ → Платформ хэсгээс "
                                      "HTTP push (Alarm Server) тохируулах шаардлагатай. "
                                      "%dс дараа дахин оролдоно.",
                                      ip, settings.camera_event_reconnect_sec)
                            await asyncio.sleep(settings.camera_event_reconnect_sec)
                        else:
                            await asyncio.sleep(2)
                        continue
                    if resp.status_code != 200:
                        # 401 үед ХУРДАН давтаж болохгүй: Dahua камер хэд хэдэн
                        # буруу оролдлогын дараа бүртгэлийг ТҮГЖДЭГ. 15с тутам
                        # давтвал зөв нууц үг оруулсны дараа ч түгжээ тайлагдахгүй
                        # (хаалт нээх RPC2 нь "remainLockSecond" алдаа өгсөөр байна).
                        if resp.status_code in (401, 403):
                            # ЧУХАЛ: энэ Dahua firmware түгжигдсэн/хориглосон үед 401
                            # БИШ 403 буцаадаг. Зөвхөн 401-ийг шалгаж байсан тул
                            # 15с тутам дайрсаар түгжээг тасралтгүй сунгадаг байв.
                            why = ("нууц үг буруу" if resp.status_code == 401 else
                                   "камер энэ IP-г ТҮР ХОРИГЛОСОН (олон удаа буруу "
                                   "нэвтрэлт эсвэл холболтын хязгаар дүүрсэн)")
                            log.error(f"{ip}: HTTP {resp.status_code} — {why}. "
                                      f"Түгжээг уртасгахгүйн тулд "
                                      f"{settings.camera_auth_retry_sec}с хүлээнэ.")
                            await asyncio.sleep(settings.camera_auth_retry_sec)
                        else:
                            log.warning(f"{ip}: HTTP {resp.status_code} — "
                                        f"{settings.camera_event_reconnect_sec}с дараа дахин")
                            await asyncio.sleep(settings.camera_event_reconnect_sec)
                        continue
                    _codes_ok[device_id] = vi   # энэ хувилбар ажиллалаа — санана
                    cycle_start = vi
                    log.info(f"{ip}: ХОЛБОГДЛОО (200, codes={codes}), event хүлээж байна")
                    last_touch = 0.0
                    async for chunk in resp.aiter_text():
                        buffer += chunk
                        # Стрим heartbeat 5с тутам ирдэг — событиегүй ч 60с тутам онлайн тэмдэглэнэ
                        if time.monotonic() - last_touch > 60:
                            last_touch = time.monotonic()
                            _touch(device_id)
                        # Дибаг: Code= мөр бүрийг логд харуулна (камер юу илгээж байгааг харах)
                        for line in chunk.splitlines():
                            if line.startswith("Code="):
                                log.info(f"{ip} event: {line[:90]}")
                        blocks, buffer = _extract_json_blocks(buffer)
                        for data in blocks:
                            # Producer: event-ийг ДАРААЛАЛД шидээд шууд дараагийн
                            # chunk-аа уншина. Өмнө нь энд `await _process_event(...)`
                            # байсан тул DB бичих хугацаанд стрим зогсож, камерын
                            # буферт event хуримтлагдаж саатал үүсгэдэг байв.
                            _enqueue(device_id, data)
        except Exception as e:
            log.warning(f"{ip}: холболт тасарлаа ({type(e).__name__}: {e}) — "
                        f"{settings.camera_event_reconnect_sec}с дараа дахин")
            await asyncio.sleep(settings.camera_event_reconnect_sec)


async def supervisor():
    """Идэвхтэй камер бүрд poller task эхлүүлж, тасарсныг сэргээнэ."""
    if not settings.cgi_poll:
        return
    log.info("идэвхжлээ — камеруудаас ANPR татаж эхэлж байна")
    ensure_workers()   # боловсруулах worker-ууд (стримээс тусдаа)
    while True:
        ensure_workers()   # унасан worker байвал сэргээнэ
        db = SessionLocal()
        try:
            # ИДЭВХГҮЙ болгосон зогсоолын камерыг сонсохгүй — тухайн талбай өөр
            # системд шилжсэн/засвартай үед манай систем түүнд огт хүрэхгүй байх
            # ёстой (эс бол хоёр систем нэг камерыг зэрэг ашиглана).
            from ..models import ParkingSite
            cams = (db.query(Device).join(ParkingSite, Device.site_id == ParkingSite.id)
                    .filter(Device.device_type == "camera", Device.status == "active",
                            ParkingSite.is_active.is_(True),
                            Device.ip_address.isnot(None), Device.ip_address != "")
                    .all())
            active = set()
            for c in cams:
                active.add(c.id)
                if c.id not in _tasks or _tasks[c.id].done():
                    # creds-ийг session амьд байхад мөр болгож шийднэ
                    _tasks[c.id] = asyncio.create_task(
                        _poll_one(c.id, c.ip_address, camera_credentials(c)))
                    log.info(f"{c.name} ({c.ip_address}) сонсож эхэллээ")
            for did in list(_tasks):
                if did not in active:
                    _tasks[did].cancel()
                    del _tasks[did]
        except Exception as e:
            log.error(f"supervisor алдаа: {e}")
        finally:
            db.close()
        await asyncio.sleep(60)
