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
    """Камерын snapshot.cgi-ээс одоогийн кадрыг татна (digest auth)."""
    url = f"http://{ip}/cgi-bin/snapshot.cgi"
    auth = httpx.DigestAuth(settings.camera_username, settings.camera_password)
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(url, auth=auth)
            if r.status_code == 200 and r.content[:2] == b"\xff\xd8":  # JPEG magic
                return r.content
            print(f"[snapshot] {ip}: HTTP {r.status_code} эсвэл JPEG биш")
    except Exception as e:
        print(f"[snapshot] {ip}: татаж чадсангүй ({e})")
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
    if data is None and camera_ip:
        data = await _fetch_from_camera(camera_ip)
    if data is None:
        return
    rel = _save(data, plate, lane_dir)
    if not rel:
        return
    db = SessionLocal()
    try:
        from ..models import ParkingSession
        s = db.get(ParkingSession, session_id)
        if s:
            if lane_dir == "exit":
                s.exit_snapshot = rel
            else:
                s.entry_snapshot = rel
            db.commit()
    finally:
        db.close()


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
