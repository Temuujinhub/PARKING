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
                      auth_block_remaining, barrier_is_waiting, camera_client,
                      camera_sick_remaining)
from .device_auth import camera_credentials

log = logging.getLogger("parking.camera_who")

# device_id -> {"sessions": [{"user","ip","last"}], "checked_at": iso, "supported": bool,
#               "error": str|None, "attempted_at": iso, "skipped": str|None}
# checked_at нь ЗӨВХӨН амжилттай хэмжилтэд бичигдэнэ (admin_router түүнийг
# probe_ok_at = «сервер→камер RPC ажиллаж байна»-гийн баримт болгож ашигладаг).
_state: dict[str, dict] = {}
# Хяналт огт ажиллахгүй болсон шалтгаан (mock/унтраалттай) — UI-д «—» гэдэг нь
# ЦЭВЭР гэсэн үг үү, ХЭМЖЭЭГҮЙ гэсэн үг үү гэдгийг ялгахад хэрэгтэй.
_off_reason: str | None = "хараахан эхлээгүй"


def foreign_info(device_id: str) -> dict | None:
    """Тухайн камерт сүүлд илэрсэн гадны хандалтууд (UI-д харуулахад)."""
    return _state.get(device_id)


def measurement_status() -> dict:
    """ХЭМЖИЛТ ӨӨРӨӨ ажиллаж байна уу.

    2026-08-20: Системийн эрүүл мэнд хуудсанд 22 камер бүгд «—» харуулж байсан
    нь «гадны хандалт алга» мэт уншигдаж байв — үнэндээ хэмжилт нь амжилттай
    болсон эсэх нь ХААНА Ч харагддаггүй байсан (алдаа нь зөвхөн debug логт).
    Хэмжигдээгүйг цэвэрээс ялгахын тулд энэ төлөвийг API-аар гаргана."""
    measured = [v["checked_at"] for v in _state.values() if v.get("checked_at")]
    return {"enabled": _off_reason is None,
            "reason": _off_reason,
            "cameras": len(_state),
            "measured": len(measured),
            "failing": sum(1 for v in _state.values() if v.get("error")),
            "last_ok_at": max(measured, default=None),
            "window_min": settings.camera_sessions_window_min,
            "period_sec": settings.camera_sessions_check_sec}


def _note(device_id: str, **fields) -> None:
    """Өмнөх амжилттай хэмжилтийг УСТГАЛГҮЙ төлөв шинэчилнэ."""
    st = _state.setdefault(device_id, {"sessions": [], "supported": None,
                                       "checked_at": None, "error": None,
                                       "skipped": None})
    st.update(fields)
    st["attempted_at"] = datetime.utcnow().isoformat()


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
    # to_camera_epoch нь UTC хүлээдэг — серверийн TZ UTC биш болмогц
    # datetime.now() чимээгүй эвдэрнэ (2026-08-31 цагийн аудит)
    end = datetime.utcnow() + timedelta(hours=1)
    start = datetime.utcnow() - timedelta(minutes=settings.camera_sessions_window_min)
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
    # Таслуур идэвхтэй / хаалт хүлээж буй / камер саяхан timeout-оор унасан үед
    # энэ (заавал биш) шалгалт камерын нөөцийг булаахгүй — алгасна.
    if auth_block_remaining(ip):
        _note(device_id, skipped="нэвтрэлтийн таслуур идэвхтэй")
        return
    if barrier_is_waiting(ip) or camera_sick_remaining(ip):
        _note(device_id, skipped="хаалт/камер завгүй")
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
    _note(device_id, sessions=sessions, supported=supported, error=None, skipped=None,
          checked_at=datetime.utcnow().isoformat())
    if not supported:
        # Нэвтрэлт болсон ч log.startFind хариу өгөөгүй — энэ камер дээр хэмжилт
        # БОЛОМЖГҮЙ (firmware/эрх). «Гадны хандалт алга» гэж уншигдах ёсгүй.
        _note(device_id, error="камер нэвтрэлтийн лог өгөхгүй (эрх/firmware)")
    fresh = [s for s in sessions if (s["user"], s["ip"]) not in prev]
    if fresh:
        log.warning("%s: камерт МАНАЙХААС ӨӨР хандалт илэрлээ: %s "
                    "(өөр систем зэрэг ашиглаж байгаагийн баримт)", ip,
                    ", ".join(f"{s['user']}@{s['ip']}" for s in fresh))


async def supervisor():
    """Идэвхтэй зогсоолын камер бүрийг тойрч шалгана (алдаа нэгийг нь зогсоохгүй)."""
    global _off_reason
    if settings.barrier_mock:
        _off_reason = "BARRIER_MOCK=true — камерт хандахгүй"
        log.info("гадны хандалтын хяналт унтраалттай: %s", _off_reason)
        return
    if settings.camera_sessions_check_sec <= 0:
        _off_reason = "CAMERA_SESSIONS_CHECK_SEC=0 — унтраасан"
        log.info("гадны хандалтын хяналт унтраалттай: %s", _off_reason)
        return
    await asyncio.sleep(90)   # startup — эхлээд поллер/хаалт тогтвортой болог
    _off_reason = None
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
                # Өмнө нь debug байсан тул production дээр хэмжилт бүтэн унасан ч
                # хаана ч харагддаггүй, UI нь «гадны хандалт алга» гэж уншигддаг
                # байв. Одоо төлөвт бичиж, ШИНЭ алдаа бүрийг WARNING логлоно.
                err = (f"{type(e).__name__}: {e}" if str(e) else type(e).__name__)[:120]
                was = (_state.get(did) or {}).get("error")
                _note(did, error=err, skipped=None)
                if was != err:
                    log.warning("%s: гадны хандалтын хэмжилт амжилтгүй — %s", ip, err)
            await asyncio.sleep(2)   # камеруудыг зэрэг цохихгүй
        await asyncio.sleep(settings.camera_sessions_check_sec)
