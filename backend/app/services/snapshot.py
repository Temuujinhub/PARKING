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


async def _fetch_from_camera(ip: str, creds: tuple[str, str] | None = None) -> bytes | None:
    """Камерын snapshot.cgi-ээс одоогийн кадрыг татна (digest auth).

    Энэ firmware дээр snapshot.cgi найдвартай ажилладаг нь production дээр
    батлагдсан (~600KB бүрэн JPEG). ГЭХДЭЭ event-ийн дараахан камер завгүй
    (ANPR боловсруулалт + encoder ачаалалтай) үед кадр рендерлэх нь удааширдаг
    тул уншилтын timeout-ыг ӨГӨӨМӨР (25с) авна — өмнө нь 6с байсан тул бүх
    оролдлого timeout болж, орох/гарах зураг огт хадгалагддаггүй байв."""
    auth = httpx.DigestAuth(*(creds or camera_credentials(None)))
    # холболт хурдан, харин зураг татах уншилт удаан байж болно
    timeout = httpx.Timeout(connect=5.0, read=25.0, write=5.0, pool=5.0)
    # Firmware/тохиргооноос хамаарч channel параметр шаардаж болзошгүй — хувилбаруудыг
    # дараалан оролдоно (channel-гүй, channel=1, channel=0). Аль нэг нь JPEG өгвөл хангалттай.
    urls = [f"http://{ip}/cgi-bin/snapshot.cgi",
            f"http://{ip}/cgi-bin/snapshot.cgi?channel=1",
            f"http://{ip}/cgi-bin/snapshot.cgi?channel=0"]
    last_err = ""
    # Зургийн таталт нь digest auth-тай — камерын хувьд ЭНЭ Ч БАС нэвтрэлт.
    # Гарах үед зураг татах ба дэлгэц бичих нь ЯГ НЭГ агшинд тохиолддог тул
    # мөргөлдөж «User or password not valid» (remainLoginTimes буурах) үүсгэдэг
    # байв (2026-07-29). Тиймээс таталтыг ЗАВСРЫН дүрэмд оруулна: зураг нь
    # цаг мэдрэмтгий (кадр өөрчлөгдөнө) тул ХҮЛЭЭЛГЭХГҮЙ, харин дэлгэц үүний
    # дараа завсар барина.
    from .barrier import camera_client, note_rpc_done
    note_rpc_done(ip)
    try:
      for attempt in range(1, 4):
        for url in urls:
            try:
                # Хуваалцсан клиент — машин бүрд шинэ TCP холболт нээвэл камерын
                # холболтын сан дүүрч хаалтны команд ч хариу авахаа болино
                client = camera_client(ip)
                r = await client.get(url, auth=auth, timeout=timeout)
                if r.status_code == 200 and r.content[:2] == b"\xff\xd8":  # JPEG magic
                    if attempt > 1 or url != urls[0]:
                        log.info(f"{ip}: OK ({len(r.content)}b) ← {url.split('cgi-bin/')[-1]}")
                    return r.content
                last_err = f"{url.split('cgi-bin/')[-1]} → HTTP {r.status_code} ({len(r.content)}b)"
            except Exception as e:
                last_err = f"{type(e).__name__}: {str(e)[:50]}"
        if attempt < 3:
            await asyncio.sleep(1.5)
    finally:
        note_rpc_done(ip)   # дэлгэц энэ агшнаас хойш завсар барина
    log.error(f"{ip}: snapshot.cgi бүх хувилбар бүтэлгүйтэв ({last_err})")
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
        log.error(f"хадгалж чадсангүй: {e}")
        return None


async def _capture_and_store(session_id: str, camera_ip: str, plate: str,
                             lane_dir: str, raw: dict,
                             creds: tuple[str, str] | None = None):
    data = _payload_picture(raw)
    source = "payload"
    if data is None and camera_ip:
        data = await _fetch_from_camera(camera_ip, creds)
        source = "snapshot.cgi"
    if data is None:
        log.warning(f"{plate} {lane_dir}: зураг ОЛДСОНГҮЙ (payload-д алга, камер {camera_ip or '-'})")
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
                existing = s.exit_snapshot if lane_dir == "exit" else s.entry_snapshot
                if existing:
                    log.info(f"{plate} {lane_dir}: event зураг аль хэдийн бий — {source} алгасав")
                    return
                if lane_dir == "exit":
                    s.exit_snapshot = rel
                else:
                    s.entry_snapshot = rel
                db.commit()
                log.info(f"{plate} {lane_dir}: OK ({source}, {len(data)}b) → {rel}")
                return
        except OperationalError as e:
            # Түгжээ чөлөөлөгдөхийг хүлээнэ (хаалт нээх/e-Barimt дуустал)
            db.rollback()
            if attempt + 1 >= attempts:
                log.warning("%s %s: session мөр %d удаа түгжээтэй байлаа — зам бичигдээгүй "
                            "(зураг диск дээр хадгалагдсан: %s)", plate, lane_dir, attempts, rel)
                return
            log.info("%s %s: session мөр түгжээтэй — %dс хүлээгээд дахин оролдоно (%d/%d)",
                     plate, lane_dir, attempt + 2, attempt + 1, attempts)
        finally:
            db.close()
        await asyncio.sleep(attempt + 1)   # 1, 2, 3, 4с — түгжээ ихэвчлэн 15с дотор тайлагдана
    log.warning(f"{plate} {lane_dir}: session {session_id} DB-д олдсонгүй — зам бичигдээгүй")


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
    asyncio.create_task(_capture_and_store(session_id, camera_ip or "", plate, lane_dir, raw, creds))
