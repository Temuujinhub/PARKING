"""CGI event pull — сервер камераас ANPR event-ийг ТАТАЖ авна (хуучин easy-park шиг).

Механизм: Dahua eventManager.cgi?action=attach&codes=[TrafficJunction] руу байнгын
HTTP холболт нээж, камерын дугаар таних event-ийн урсгалыг (multipart) уншина.
Сервер→камер чиглэлээр ажилладаг тул камер→сервер firewall/config шаардлагагүй.

Идэвхжүүлэх: PARKING_CGI_POLL=true, PARKING_CAMERA_USERNAME/PASSWORD (камерын admin).
Камер бүр Тохиргоо→Төхөөрөмж дээр IP-тэй бүртгэгдсэн байх ёстой.
"""
import asyncio
import json
import time
from datetime import datetime

import httpx

from ..config import settings
from ..database import SessionLocal
from ..models import Device, LprEvent
from ..session_logic import handle_entry, handle_exit, normalize_plate

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


async def _process_event(device_id: str, data: dict):
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
        if device.lane_dir == "exit":
            await handle_exit(db, device, plate, conf, data)
        else:
            await handle_entry(db, device, plate, conf, data)
    except Exception as e:
        print(f"[cgi_poll] event боловсруулах алдаа: {e}")
    finally:
        db.close()


async def _poll_one(device_id: str, ip: str):
    """Нэг камерын event stream-ийг тасралтгүй сонсоно (reconnect-тэй).
    codes=[All] — камерын бүх event-ийг авч, дугаартайг нь л боловсруулна (дибагт хялбар)."""
    url = f"http://{ip}/cgi-bin/eventManager.cgi?action=attach&codes=[All]&heartbeat=5"
    auth = httpx.DigestAuth(settings.camera_username, settings.camera_password)
    while True:
        buffer = ""
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10, read=None)) as client:
                async with client.stream("GET", url, auth=auth) as resp:
                    if resp.status_code != 200:
                        print(f"[cgi_poll] {ip}: HTTP {resp.status_code} "
                              f"({'нууц үг буруу' if resp.status_code == 401 else 'алдаа'}) — 15с дараа дахин")
                        await asyncio.sleep(15)
                        continue
                    print(f"[cgi_poll] {ip}: ХОЛБОГДЛОО (200), event хүлээж байна")
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
                                print(f"[cgi_poll] {ip} event: {line[:90]}")
                        blocks, buffer = _extract_json_blocks(buffer)
                        for data in blocks:
                            await _process_event(device_id, data)
        except Exception as e:
            print(f"[cgi_poll] {ip}: холболт тасарлаа ({e}) — 15с дараа дахин")
            await asyncio.sleep(15)


async def supervisor():
    """Идэвхтэй камер бүрд poller task эхлүүлж, тасарсныг сэргээнэ."""
    if not settings.cgi_poll:
        return
    print("[cgi_poll] идэвхжлээ — камеруудаас ANPR татаж эхэлж байна")
    while True:
        db = SessionLocal()
        try:
            cams = db.query(Device).filter(
                Device.device_type == "camera", Device.status == "active",
                Device.ip_address.isnot(None), Device.ip_address != "",
            ).all()
            active = set()
            for c in cams:
                active.add(c.id)
                if c.id not in _tasks or _tasks[c.id].done():
                    _tasks[c.id] = asyncio.create_task(_poll_one(c.id, c.ip_address))
                    print(f"[cgi_poll] {c.name} ({c.ip_address}) сонсож эхэллээ")
            for did in list(_tasks):
                if did not in active:
                    _tasks[did].cancel()
                    del _tasks[did]
        except Exception as e:
            print(f"[cgi_poll] supervisor алдаа: {e}")
        finally:
            db.close()
        await asyncio.sleep(60)
