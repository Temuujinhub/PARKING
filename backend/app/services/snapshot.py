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
import os
import re
from datetime import datetime

import httpx

from ..config import settings
from ..database import SessionLocal

_SAFE = re.compile(r"[^0-9A-ZА-ЯЁӨҮ]")


def _payload_picture(raw: dict) -> bytes | None:
    """ITSAPI payload-аас base64 зураг хайна (боломжит бүх байрлал)."""
    if not isinstance(raw, dict):
        return None
    pic = raw.get("Picture") or {}
    candidates = [
        (pic.get("NormalPic") or {}).get("Content"),
        (pic.get("CutoutPic") or {}).get("Content"),
        pic.get("Content"),
        raw.get("NormalPic", {}).get("Content") if isinstance(raw.get("NormalPic"), dict) else None,
        raw.get("PicData"),
    ]
    for c in candidates:
        if isinstance(c, str) and len(c) > 1000:
            try:
                return base64.b64decode(c)
            except Exception:
                continue
    return None


async def _fetch_from_camera(ip: str) -> bytes | None:
    """Камерын snapshot.cgi-ээс одоогийн кадрыг татна (digest auth).

    Камер event-ийн дараахан завгүй (encoder ачаалалтай) үед нэг удаагийн
    оролдлого амархан бүтэлгүйтдэг тул богино зайтай 3 удаа оролдоно —
    машин хаалтан дээр зогсож байгаа тул 1-2 секундын дотор кадр хүчинтэй хэвээр."""
    url = f"http://{ip}/cgi-bin/snapshot.cgi"
    auth = httpx.DigestAuth(settings.camera_username, settings.camera_password)
    last_err = ""
    for attempt in range(1, 4):
        try:
            async with httpx.AsyncClient(timeout=6) as client:
                r = await client.get(url, auth=auth)
                if r.status_code == 200 and r.content[:2] == b"\xff\xd8":  # JPEG magic
                    return r.content
                last_err = f"HTTP {r.status_code} эсвэл JPEG биш ({len(r.content)}b)"
        except Exception as e:
            last_err = str(e)
        if attempt < 3:
            await asyncio.sleep(1.5)
    print(f"[snapshot] {ip}: 3 оролдлогод татаж чадсангүй ({last_err})")
    return None


def _save(data: bytes, plate: str, lane_dir: str) -> str | None:
    """Зургийг диск рүү бичээд snapshot_dir-ээс хамаарах замыг буцаана."""
    now = datetime.utcnow()
    day = now.strftime("%Y%m%d")
    safe_plate = _SAFE.sub("", plate.upper()) or "UNKNOWN"
    rel = os.path.join(day, f"{safe_plate}_{now.strftime('%H%M%S')}_{lane_dir}.jpg")
    full = os.path.join(settings.snapshot_dir, rel)
    try:
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as f:
            f.write(data)
        return rel
    except OSError as e:
        print(f"[snapshot] хадгалж чадсангүй: {e}")
        return None


async def _capture_and_store(session_id: str, camera_ip: str, plate: str,
                             lane_dir: str, raw: dict):
    data = _payload_picture(raw)
    source = "payload"
    if data is None and camera_ip:
        data = await _fetch_from_camera(camera_ip)
        source = "snapshot.cgi"
    if data is None:
        print(f"[snapshot] {plate} {lane_dir}: зураг ОЛДСОНГҮЙ (payload-д алга, камер {camera_ip or '-'})")
        return
    rel = _save(data, plate, lane_dir)
    if not rel:
        return
    # Session мөр commit хийгдэж амжаагүй байж болзошгүй (payload зурагтай үед
    # capture агшин зуур дуусдаг) — олдохгүй бол багахан хүлээгээд дахин оролдоно.
    from ..models import ParkingSession
    for attempt in range(3):
        db = SessionLocal()
        try:
            s = db.get(ParkingSession, session_id)
            if s:
                # snap_puller (жинхэнэ event зураг) түрүүлж бичсэн бол дарж бичихгүй —
                # snapshot.cgi нь ердөө "одоогийн кадр" тул чанараар дутуу
                existing = s.exit_snapshot if lane_dir == "exit" else s.entry_snapshot
                if existing:
                    print(f"[snapshot] {plate} {lane_dir}: event зураг аль хэдийн бий — {source} алгасав")
                    return
                if lane_dir == "exit":
                    s.exit_snapshot = rel
                else:
                    s.entry_snapshot = rel
                db.commit()
                print(f"[snapshot] {plate} {lane_dir}: OK ({source}, {len(data)}b) → {rel}")
                return
        finally:
            db.close()
        await asyncio.sleep(1)
    print(f"[snapshot] {plate} {lane_dir}: session {session_id} DB-д олдсонгүй — зам бичигдээгүй")


def schedule_capture(session_id: str | None, camera_ip: str | None, plate: str,
                     lane_dir: str, raw: dict):
    """Event боловсруулалтын дараа дуудна — зургийг ард нь татаж хадгална.
    Хаалт нээх/WS broadcast-ыг хэзээ ч хүлээлгэхгүй."""
    if not settings.snapshot_enabled or not session_id:
        return
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return  # event loop-гүй орчин (тест г.м) — алгасна
    asyncio.create_task(_capture_and_store(session_id, camera_ip or "", plate, lane_dir, raw))
