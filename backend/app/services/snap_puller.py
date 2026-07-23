"""Камерын ЖИНХЭНЭ event зургийг татах — snapshot.cgi-ийн "одоогийн кадр"-аас
ялгаатай нь эдгээр нь камерын web UI-ийн ANPR жагсаалтад харагддаг, event-ийн
агшинд буулгасан зургууд. Хоёр механизм:

1. ЛАЙВ СТРИМ: /cgi-bin/snapManager.cgi?action=attachFileProc — камер ANPR
   event болмогц зургаа multipart стримээр түлхэж өгнө (сервер→камер чиглэлээр
   татдаг тул камер талд тохиргоо хэрэггүй, eventManager attach-тай ижил зарчим).
   Web 5.0 клиент өөрөө snapManager.attachFileProc-оор яг ингэж авдаг
   (docs/barrier_test3 клиент JS-ээс батлагдсан).

2. НӨХӨН ТАТАЛТ: /cgi-bin/mediaFileFind.cgi + /cgi-bin/RPC_Loadfile — камерын
   санах ойд хадгалагдсан зургийг цаг хугацааны мужаар хайж татна. Session-д
   зураг дутуу үлдсэн тохиолдолд (жиш: сервер унтарсан хооронд орсон машин)
   UI-ийн "Камераас татах" товчоор нөхөж болно.

Стрим формат (multipart/x-mixed-replace): event бүрд text/plain хэсэг
(Events[0].TrafficCar.PlateNumber=... мөрүүд) дараа нь 1..N image/jpeg хэсэг
(бүтэн кадр + дугаарын тайрмал). Хамгийн томыг нь (бүтэн кадр) хадгална.
"""
import asyncio
import re
from datetime import datetime, timedelta

import httpx

from ..config import settings
from ..database import SessionLocal
from ..models import Device, ParkingSession

_tasks: dict[str, asyncio.Task] = {}

_PLATE_RE = re.compile(r"PlateNumber=([^\r\n;,\"]+)")
_CLEN_RE = re.compile(r"content-length:\s*(\d+)")
_BOUNDARY_RE = re.compile(r'boundary="?([^";,\s]+)')


class MultipartParser:
    """Dahua multipart стримийг инкрементал задлагч. feed(chunk) нь бэлэн болсон
    (content_type, body) хэсгүүдийг буцаана — зураг chunk дундуур таслагдаж
    ирдэг тул Content-Length-ээр бүрэн болтол нь хүлээнэ."""

    def __init__(self, boundary: str):
        self.boundary = b"--" + boundary.encode()
        self.buf = bytearray()

    def feed(self, chunk: bytes) -> list[tuple[str, bytes]]:
        self.buf += chunk
        parts = []
        while True:
            start = self.buf.find(self.boundary)
            if start < 0:
                # boundary огт алга — зураг стримийн дунд байна; буфер хэтрэхээс хамгаална
                if len(self.buf) > 8 * 1024 * 1024:
                    del self.buf[: -len(self.boundary)]
                return parts
            hdr_end = self.buf.find(b"\r\n\r\n", start)
            if hdr_end < 0:
                return parts  # header бүрэн ирээгүй
            headers = bytes(self.buf[start:hdr_end]).decode("latin-1", "ignore").lower()
            body_start = hdr_end + 4
            m = _CLEN_RE.search(headers)
            if m:
                length = int(m.group(1))
                if len(self.buf) < body_start + length:
                    return parts  # body бүрэн ирээгүй — дараагийн chunk хүлээнэ
                body = bytes(self.buf[body_start:body_start + length])
                del self.buf[:body_start + length]
            else:
                nxt = self.buf.find(self.boundary, body_start)
                if nxt < 0:
                    return parts
                body = bytes(self.buf[body_start:nxt]).rstrip(b"\r\n")
                del self.buf[:nxt]
            ctype = "image/jpeg" if "image/jpeg" in headers else "text/plain"
            parts.append((ctype, body))


def _save_jpeg(data: bytes, plate: str, lane_dir: str) -> str | None:
    from .snapshot import _save
    return _save(data, plate, lane_dir)


async def _attach_to_session(device_id: str, plate: str, lane_dir: str, data: bytes):
    """Зургийг хадгалаад тухайн дугаарын хамгийн сүүлийн session-д холбоно.
    Event боловсруулалт (cgi_poller) зургаас хоцорч болзошгүй тул хэдэнтээ оролдоно."""
    from ..session_logic import normalize_plate
    plate_n = normalize_plate(plate) or plate.strip().upper()
    rel = _save_jpeg(data, plate_n, lane_dir)
    if not rel:
        return
    for attempt in range(5):
        db = SessionLocal()
        try:
            device = db.get(Device, device_id)
            if not device:
                return
            s = (db.query(ParkingSession)
                 .filter(ParkingSession.site_id == device.site_id,
                         ParkingSession.plate_number == plate_n,
                         ParkingSession.entry_time >= datetime.utcnow() - timedelta(hours=48))
                 .order_by(ParkingSession.entry_time.desc()).first())
            if s:
                if lane_dir == "exit":
                    s.exit_snapshot = rel
                else:
                    s.entry_snapshot = rel
                db.commit()
                print(f"[snap_pull] {plate_n} {lane_dir}: event зураг OK ({len(data)}b) → {rel}")
                return
        except Exception as e:
            print(f"[snap_pull] {plate_n}: session холбох алдаа: {e}")
        finally:
            db.close()
        await asyncio.sleep(1.5)
    # Session олдоогүй ч файл диск дээр үлдэнэ — гараар/нөхөн таталтаар олдоно
    print(f"[snap_pull] {plate_n} {lane_dir}: session олдсонгүй, файл {rel} хадгалагдав")


async def _pull_one(device_id: str, ip: str, lane_dir: str):
    """Нэг камерын зургийн стримийг тасралтгүй сонсоно (reconnect-тэй)."""
    url = (f"http://{ip}/cgi-bin/snapManager.cgi?action=attachFileProc"
           f"&Flags[0]=Event&Events=[All]&heartbeat=5")
    auth = httpx.DigestAuth(settings.camera_username, settings.camera_password)
    while True:
        pending_plate: str | None = None
        pending_jpegs: list[bytes] = []

        async def flush():
            nonlocal pending_plate, pending_jpegs
            if pending_plate and pending_jpegs:
                best = max(pending_jpegs, key=len)
                asyncio.create_task(_attach_to_session(device_id, pending_plate, lane_dir, best))
            pending_plate, pending_jpegs = None, []

        try:
            # read=30: cgi_poller-тай ижил — үхсэн холболтыг 30с-д мэдэрч reconnect
            async with httpx.AsyncClient(timeout=httpx.Timeout(10, read=30)) as client:
                async with client.stream("GET", url, auth=auth) as resp:
                    if resp.status_code != 200:
                        print(f"[snap_pull] {ip}: HTTP {resp.status_code} — firmware "
                              f"snapManager дэмжихгүй бол snapshot.cgi fallback хэвээр. 60с дараа дахин")
                        await asyncio.sleep(60)
                        continue
                    ctype = resp.headers.get("content-type", "")
                    bm = _BOUNDARY_RE.search(ctype)
                    parser = MultipartParser(bm.group(1) if bm else "myboundary")
                    print(f"[snap_pull] {ip}: зургийн стрим ХОЛБОГДЛОО (200)")
                    async for chunk in resp.aiter_bytes():
                        for part_type, body in parser.feed(chunk):
                            if part_type == "image/jpeg" and body[:2] == b"\xff\xd8":
                                pending_jpegs.append(body)
                                if len(pending_jpegs) >= 6:  # хамгаалалт: text хэсэггүй овоорвол
                                    await flush()
                            else:
                                text = body.decode("utf-8", "ignore")
                                await flush()  # шинэ event/heartbeat = өмнөх бүлэг дууссан
                                m = _PLATE_RE.search(text)
                                if m:
                                    pending_plate = m.group(1).strip()
        except Exception as e:
            await flush()
            print(f"[snap_pull] {ip}: стрим тасарлаа ({e}) — 15с дараа дахин")
            await asyncio.sleep(15)


async def supervisor():
    """Идэвхтэй камер бүрд зургийн стрим task ажиллуулна (cgi_poller-тай ижил хэв маяг)."""
    if not settings.snap_pull:
        return
    print("[snap_pull] идэвхжлээ — камеруудаас event зураг татаж эхэлж байна")
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
                    _tasks[c.id] = asyncio.create_task(
                        _pull_one(c.id, c.ip_address, c.lane_dir or "entry"))
                    print(f"[snap_pull] {c.name} ({c.ip_address}) зургийн стрим эхэллээ")
            for did in list(_tasks):
                if did not in active:
                    _tasks[did].cancel()
                    del _tasks[did]
        except Exception as e:
            print(f"[snap_pull] supervisor алдаа: {e}")
        finally:
            db.close()
        await asyncio.sleep(60)


# ─── Нөхөн таталт: камерт хадгалагдсан зургийг хайж татах ────────────────────

_ITEM_RE = re.compile(r"items\[(\d+)\]\.(\w+)=(.*)")


def parse_find_items(text: str) -> list[dict]:
    """findNextFile-ийн текст хариуг items жагсаалт болгоно."""
    items: dict[int, dict] = {}
    for line in text.splitlines():
        m = _ITEM_RE.match(line.strip())
        if m:
            items.setdefault(int(m.group(1)), {})[m.group(2)] = m.group(3).strip()
    return [items[i] for i in sorted(items)]


async def fetch_stored_picture(ip: str, start: datetime, end: datetime) -> tuple[bytes | None, str]:
    """Камерын санах ойгоос [start, end] (КАМЕРЫН ЦАГААР) мужийн хамгийн том
    jpg-г татна. Буцаах: (зураг|None, алдааны тайлбар)."""
    auth = httpx.DigestAuth(settings.camera_username, settings.camera_password)
    fmt = "%Y-%m-%d %H:%M:%S"
    base = f"http://{ip}/cgi-bin/mediaFileFind.cgi"
    token = None
    try:
        async with httpx.AsyncClient(timeout=20, auth=auth) as client:
            r = await client.get(base, params={"action": "factory.create"})
            m = re.search(r"result=(\d+)", r.text)
            if not m:
                return None, f"factory.create: HTTP {r.status_code} {r.text[:80]}"
            token = m.group(1)
            items = []
            # Firmware-ээс хамаарч суваг 1 эсвэл 0 — хоёуланг нь оролдоно
            for channel in (1, 0):
                r = await client.get(base, params={
                    "action": "findFile", "object": token,
                    "condition.Channel": channel,
                    "condition.StartTime": start.strftime(fmt),
                    "condition.EndTime": end.strftime(fmt),
                    "condition.Types[0]": "jpg",
                })
                if "ok" not in r.text.lower():
                    continue
                r = await client.get(base, params={"action": "findNextFile",
                                                   "object": token, "count": 64})
                items = [i for i in parse_find_items(r.text) if i.get("FilePath")]
                if items:
                    break
            if not items:
                return None, "энэ мужид камерт хадгалагдсан зураг олдсонгүй"
            # Хамгийн том файл = бүтэн кадр (тайрмал жижиг байдаг)
            best = max(items, key=lambda i: int(i.get("Length") or 0))
            r = await client.get(f"http://{ip}/cgi-bin/RPC_Loadfile{best['FilePath']}")
            if r.status_code == 200 and r.content[:2] == b"\xff\xd8":
                return r.content, ""
            return None, f"RPC_Loadfile: HTTP {r.status_code} ({len(r.content)}b)"
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:120]}"
    finally:
        if token:
            try:
                async with httpx.AsyncClient(timeout=5, auth=auth) as client:
                    await client.get(base, params={"action": "close", "object": token})
                    await client.get(base, params={"action": "destroy", "object": token})
            except Exception:
                pass
