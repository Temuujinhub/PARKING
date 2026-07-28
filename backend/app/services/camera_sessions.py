"""Камерын идэвхтэй сессүүдийг үе үе асууж, МАНАЙХААС ӨӨР IP-г илрүүлнэ.

Юуны учир (2026-07-28): хуучин easy-park систем камеруудад зэрэг хандаж нөөцийг
нь булааж, бүртгэл түгжиж байгаа нь хаалтны 15с timeout-уудын эх үүсвэр гэж
үзэж байгаа. Үүнийг таамаг биш БАРИМТ болгохын тулд камераас өөрөөс нь
(UserManager.getActiveUserInfo*) «яг одоо хэн холбогдсон бэ» гэдгийг 5 минут
тутам асууж, манай серверийнхээс өөр IP илэрвэл:
  • Тохиргоо → Төхөөрөмж хүснэгтэд (Callback түлхүүрийн доор) улаанаар харуулна
  • Лог дээр WARNING бичнэ (админд өгөх нотолгоо өөрөө хуримтлагдана)

Болгоомжлол: камерын RPC нөөц ховор тул (1) IP тус бүрийн rpc lock-ийг ашиглана,
(2) хаалтны команд хүлээж байвал шалгалтыг алгасна, (3) нэвтрэлтийн таслуур
(auth circuit breaker) идэвхтэй үед огт хандахгүй, (4) barrier_mock үед унтарна.
"""
import asyncio
import json
import logging
import re
import socket
from datetime import datetime

from ..config import settings
from ..database import SessionLocal
from ..models import Device, ParkingSite
from .barrier import (DahuaRpc, _auth_failed, _is_auth_error, _auth_ok, _rpc_lock,
                      auth_block_remaining, barrier_is_waiting, camera_client)
from .device_auth import camera_credentials

log = logging.getLogger("parking.camera_who")

# device_id -> {"ips": [...], "checked_at": iso, "supported": bool}
_state: dict[str, dict] = {}

_IP_RE = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b")


def foreign_info(device_id: str) -> dict | None:
    """Тухайн камерт сүүлд илэрсэн гадны IP-үүд (UI-д харуулахад)."""
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


async def _check_one(device_id: str, ip: str, creds: tuple[str, str]) -> None:
    if auth_block_remaining(ip) or barrier_is_waiting(ip):
        return
    ips: set[str] = set()
    supported = False
    async with _rpc_lock(ip):
        rpc = DahuaRpc(camera_client(ip), ip, *creds)
        await rpc.login()
        try:
            for method in ("UserManager.getActiveUserInfoAll",
                           "UserManager.getActiveUserInfo"):
                res = await rpc._call(method)
                if res.get("result"):
                    supported = True
                    ips.update(_IP_RE.findall(json.dumps(res.get("params") or {})))
                    break
        finally:
            await rpc.logout()
    _auth_ok(ip)
    ours = {_our_ip_toward(ip), ip, "127.0.0.1", "0.0.0.0"}
    foreign = sorted(i for i in ips
                     if i not in ours and not i.startswith(("255.", "224."))
                     and not i.endswith(".255"))
    prev = (_state.get(device_id) or {}).get("ips")
    _state[device_id] = {"ips": foreign, "supported": supported,
                         "checked_at": datetime.utcnow().isoformat()}
    if foreign and foreign != prev:
        log.warning("%s: камерт МАНАЙХААС ӨӨР IP холбогдсон байна: %s "
                    "(өөр систем зэрэг ашиглаж байгаагийн баримт)", ip, ", ".join(foreign))


async def supervisor():
    """Идэвхтэй зогсоолын камер бүрийг тойрч шалгана (алдаа нэгийг нь зогсоохгүй)."""
    if settings.barrier_mock or settings.camera_sessions_check_sec <= 0:
        return
    await asyncio.sleep(90)   # startup — эхлээд поллер/хаалт тогтвортой болог
    log.info("камерын сессийн хяналт идэвхжлээ (%dс тутам)", settings.camera_sessions_check_sec)
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
                await asyncio.wait_for(_check_one(did, ip, creds), timeout=10)
            except Exception as e:  # noqa: BLE001
                if _is_auth_error(e):
                    _auth_failed(ip)
                log.debug("%s: сесс шалгалт амжилтгүй (%s)", ip, type(e).__name__)
            await asyncio.sleep(2)   # камеруудыг зэрэг цохихгүй
        await asyncio.sleep(settings.camera_sessions_check_sec)
