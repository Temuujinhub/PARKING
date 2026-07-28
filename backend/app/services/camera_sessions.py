"""Камерын нэвтрэлтийн логоос МАНАЙХААС ӨӨР хэрэглэгч/IP-г илрүүлнэ.

Юуны учир (2026-07-28): хуучин систем камеруудад зэрэг хандаж нөөцийг булааж,
хаалтны 15с timeout үүсгэдэг гэсэн таамгийг БАРИМТ болгох. camera_who.py-ийн
туршилтаар энэ firmware UserManager.getActiveUserInfo* ДЭМЖДЭГГҮЙ (Method not
found) харин log.startFind/doFind нэвтрэлтийн логоо IP-тэй нь өгдөг нь батлагдсан
(жишээ: user=Meguun ip=172.10.0.18 5 мин тутам Login/Logout). Тиймээс 5 минут
тутам сүүлийн цагийн Login/Logout логийг татаж, манай серверийн IP-ээс өөр
хандалтыг:
  • Тохиргоо → Төхөөрөмж хүснэгтийн «Гадны хандалт» баганад харуулна
  • Шинээр илэрвэл WARNING логлоно (нотолгоо өөрөө хуримтлагдана)

Болгоомжлол: IP тус бүрийн rpc lock ашиглана, хаалтны команд хүлээж байвал
алгасна, нэвтрэлтийн таслуур идэвхтэй үед хандахгүй, barrier_mock үед унтарна.
"""
import asyncio
import logging
import socket
from datetime import datetime, timedelta

from ..config import settings
from ..database import SessionLocal
from ..models import Device, ParkingSite
from .barrier import (DahuaRpc, _auth_failed, _auth_ok, _is_auth_error, _rpc_lock,
                      auth_block_remaining, barrier_is_waiting, camera_client)
from .device_auth import camera_credentials

log = logging.getLogger("parking.camera_who")

# device_id -> {"sessions": [{"user","ip","last"}], "checked_at": iso, "supported": bool}
_state: dict[str, dict] = {}


def foreign_info(device_id: str) -> dict | None:
    """Тухайн камерт сүүлд илэрсэн гадны хандалтууд (UI-д харуулахад)."""
    return _state.get(device_id)


def _our_ip_toward(cam_ip: str) -> str:
    """Тухайн камер руу гарахад ашиглагддаг МАНАЙ интерфэйсийн IP."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect((cam_ip, 80))
            return s.getsockname()[0]
        finally:
            s.close()
    except Exception:  # noqa: BLE001
        return ""


async def _fetch_log_entries(rpc: DahuaRpc) -> tuple[bool, dict]:
    """Сүүлийн цагийн Login/Logout бичлэгүүд: (user, ip) -> сүүлийн цаг.
    Цагийн муж: camera_who туршилтаар серверийн datetime.now()-д суурилсан муж
    ажиллаж байсан тул мөн адил; төгсгөлд +1ц маржин (камерын цагийн зөрүүд)."""
    end = datetime.now() + timedelta(hours=1)
    start = datetime.now() - timedelta(minutes=settings.camera_sessions_window_min)
    cond = {"StartTime": start.strftime("%Y-%m-%d %H:%M:%S"),
            "EndTime": end.strftime("%Y-%m-%d %H:%M:%S"),
            "Translate": True, "Types": ["Login", "Logout"]}
    r = await rpc._call("log.startFind", {"condition": cond})
    token = (r.get("params") or {}).get("token")
    if not token:
        cond.pop("Types", None)
        r = await rpc._call("log.startFind", {"condition": cond})
        token = (r.get("params") or {}).get("token")
    if not token:
        return False, {}
    entries: dict[tuple[str, str], str] = {}
    try:
        for _ in range(6):   # хамгийн ихдээ 600 бичлэг — хангалттай
            rr = await rpc._call("log.doFind", {"token": token, "count": 100})
            items = (rr.get("params") or {}).get("items") or []
            for it in items:
                typ = str(it.get("Type", "")).lower()
                if "login" not in typ and "logout" not in typ:
                    continue
                user = str(it.get("User") or it.get("user") or "?")
                det = it.get("Detail") or {}
                rip = str(det.get("RemoteIP") or det.get("Address")
                          or it.get("RemoteIP") or "").strip()
                if rip:
                    key = (user, rip)
                    t = str(it.get("Time", ""))
                    if t > entries.get(key, ""):
                        entries[key] = t
            if len(items) < 100:
                break
    finally:
        try:
            await rpc._call("log.stopFind", {"token": token})
        except Exception:  # noqa: BLE001
            pass
    return True, entries


async def _check_one(device_id: str, ip: str, creds: tuple[str, str]) -> None:
    if auth_block_remaining(ip) or barrier_is_waiting(ip):
        return
    async with _rpc_lock(ip):
        rpc = DahuaRpc(camera_client(ip), ip, *creds)
        await rpc.login()
        try:
            supported, entries = await _fetch_log_entries(rpc)
        finally:
            await rpc.logout()
    _auth_ok(ip)
    ours = {_our_ip_toward(ip), ip, "127.0.0.1", ""}
    sessions = [{"user": u, "ip": i, "last": t}
                for (u, i), t in sorted(entries.items(), key=lambda kv: kv[1], reverse=True)
                if i not in ours][:5]
    prev = {(s["user"], s["ip"]) for s in (_state.get(device_id) or {}).get("sessions", [])}
    _state[device_id] = {"sessions": sessions, "supported": supported,
                         "checked_at": datetime.utcnow().isoformat()}
    fresh = [s for s in sessions if (s["user"], s["ip"]) not in prev]
    if fresh:
        log.warning("%s: камерт МАНАЙХААС ӨӨР хандалт илэрлээ: %s "
                    "(өөр систем зэрэг ашиглаж байгаагийн баримт)", ip,
                    ", ".join(f"{s['user']}@{s['ip']}" for s in fresh))


async def supervisor():
    """Идэвхтэй зогсоолын камер бүрийг тойрч шалгана (алдаа нэгийг нь зогсоохгүй)."""
    if settings.barrier_mock or settings.camera_sessions_check_sec <= 0:
        return
    await asyncio.sleep(90)   # startup — эхлээд поллер/хаалт тогтвортой болог
    log.info("камерын нэвтрэлтийн хяналт идэвхжлээ (%dс тутам, сүүлийн %d мин цонх)",
             settings.camera_sessions_check_sec, settings.camera_sessions_window_min)
    while True:
        db = SessionLocal()
        try:
            cams = (db.query(Device).join(ParkingSite, Device.site_id == ParkingSite.id)
                    .filter(Device.device_type == "camera", Device.status == "active",
                            ParkingSite.is_active.is_(True),
                            Device.ip_address.isnot(None), Device.ip_address != "")
                    .all())
            cam_list = [(c.id, c.ip_address, camera_credentials(c)) for c in cams]
        except Exception as e:  # noqa: BLE001
            cam_list = []
            log.error("камерын жагсаалт уншиж чадсангүй: %r", e)
        finally:
            db.close()
        for did, ip, creds in cam_list:
            try:
                await asyncio.wait_for(_check_one(did, ip, creds), timeout=15)
            except Exception as e:  # noqa: BLE001
                if _is_auth_error(e):
                    _auth_failed(ip)
                log.debug("%s: нэвтрэлтийн лог шалгалт амжилтгүй (%s)", ip, type(e).__name__)
            await asyncio.sleep(2)   # камеруудыг зэрэг цохихгүй
        await asyncio.sleep(settings.camera_sessions_check_sec)
