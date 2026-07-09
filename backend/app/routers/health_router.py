"""Системийн эрүүл мэнд (System Health) — админд зориулсан бүрэн мониторинг.

Сервер (CPU/RAM/диск/сүлжээ/халалт) + сервисүүд (systemd/nginx/postgres/docker/kernel)
+ database статистик + харилцан холболт (камер/хаалт TCP амьд, QPay API хүрэх, WebSocket).
Хүнд ажиллагаа байхгүй — бүгд хурдан, timeout-той, алдаа гарвал 'unknown' болж уначихгүй.
"""
import asyncio
import os
import shutil
import socket
import subprocess
import time

from fastapi import APIRouter, Depends
from sqlalchemy import text

from ..auth import require_role
from ..config import settings
from ..database import get_db
from ..models import Device, ParkingSession, User
from ..ws import manager

try:
    import psutil  # серверийн metrics; суулгаагүй бол degrade
except Exception:  # noqa: BLE001
    psutil = None

router = APIRouter(prefix="/api/health", tags=["health"])

_START = time.time()  # backend асаасан цаг (uptime тооцох)
_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def _git_version() -> str | None:
    try:
        out = subprocess.run(["git", "-C", _REPO, "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=2)
        return out.stdout.strip() or None
    except Exception:  # noqa: BLE001
        return None


def _service_status(name: str) -> str:
    """systemd сервисийн төлөв: active / inactive / unknown (systemctl байхгүй/эрхгүй үед)."""
    if not shutil.which("systemctl"):
        return "unknown"
    try:
        out = subprocess.run(["systemctl", "is-active", name],
                             capture_output=True, text=True, timeout=3)
        return out.stdout.strip() or "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


def _kernel() -> str:
    try:
        return os.uname().release
    except Exception:  # noqa: BLE001
        return "unknown"


def _reboot_required() -> bool:
    # Ubuntu/Debian: kernel/багц шинэчилсний дараа энэ файл үүснэ
    return os.path.exists("/var/run/reboot-required") or os.path.exists("/run/reboot-required")


def _cpu_temperature() -> float | None:
    """CPU температур (°C). Cloud VM/droplet ихэвчлэн sensor нээдэггүй тул None байж болно."""
    if not psutil or not hasattr(psutil, "sensors_temperatures"):
        return None
    try:
        temps = psutil.sensors_temperatures()
    except Exception:  # noqa: BLE001
        return None
    for key in ("coretemp", "k10temp", "cpu_thermal", "acpitz"):
        if temps.get(key):
            return round(temps[key][0].current, 1)
    for entries in temps.values():  # ямар нэг мэдрэгч байвал эхнийхийг авна
        if entries:
            return round(entries[0].current, 1)
    return None


def _system_metrics() -> dict:
    if not psutil:
        return {"available": False}
    vm = psutil.virtual_memory()
    sm = psutil.swap_memory()
    try:
        load = os.getloadavg()
    except Exception:  # noqa: BLE001
        load = (0, 0, 0)
    disks = []
    for part in psutil.disk_partitions(all=False):
        try:
            u = psutil.disk_usage(part.mountpoint)
        except Exception:  # noqa: BLE001
            continue
        disks.append({"mount": part.mountpoint, "total": u.total, "used": u.used,
                      "free": u.free, "percent": u.percent})
    net = psutil.net_io_counters()
    return {
        "available": True,
        "cpu_percent": psutil.cpu_percent(interval=0.3),
        "cpu_count": psutil.cpu_count(),
        "load_avg": [round(x, 2) for x in load],
        "memory": {"total": vm.total, "used": vm.used, "available": vm.available, "percent": vm.percent},
        "swap": {"total": sm.total, "used": sm.used, "percent": sm.percent},
        "disks": disks,
        "network": {"bytes_sent": net.bytes_sent, "bytes_recv": net.bytes_recv,
                    "packets_sent": net.packets_sent, "packets_recv": net.packets_recv},
        "temperature_c": _cpu_temperature(),
        "boot_time": psutil.boot_time(),
        "uptime_seconds": int(time.time() - psutil.boot_time()),
    }


async def _tcp_alive(host: str, port: int = 80, timeout: float = 2.0) -> bool:
    """TCP холболт нээгдэх эсэхээр төхөөрөмж амьд эсэхийг шалгана (камер/хаалт)."""
    try:
        fut = asyncio.open_connection(host, port)
        reader, writer = await asyncio.wait_for(fut, timeout=timeout)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:  # noqa: BLE001
            pass
        return True
    except Exception:  # noqa: BLE001
        return False


async def _qpay_reachable() -> dict:
    """QPay API хүрэх эсэх (бодит HTTP). Mock үед шалгахгүй."""
    if settings.qpay_mock:
        return {"ok": None, "note": "mock горим"}
    import httpx
    url = settings.qpay_base_url
    t0 = time.time()
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(url)
        return {"ok": r.status_code < 500, "status_code": r.status_code,
                "ms": int((time.time() - t0) * 1000)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:120], "ms": int((time.time() - t0) * 1000)}


@router.get("/system")
async def system_health(db=Depends(get_db), user: User = Depends(require_role("ADMIN", "SUPER_ADMIN"))):
    now = time.time()

    # ── Database статистик ──
    database = {"ok": False}
    try:
        size = db.execute(text("SELECT pg_database_size(current_database())")).scalar()
        conns = db.execute(text("SELECT count(*) FROM pg_stat_activity")).scalar()
        open_sessions = db.query(ParkingSession).filter(
            ParkingSession.status.in_(["OPEN", "AWAITING_PAYMENT", "PAID"])).count()
        sessions_today = db.execute(text(
            "SELECT count(*) FROM parking_sessions WHERE entry_time >= date_trunc('day', now())")).scalar()
        pay_today = db.execute(text(
            "SELECT count(*), COALESCE(SUM(amount),0) FROM payments "
            "WHERE status='PAID' AND paid_at >= date_trunc('day', now())")).first()
        database = {"ok": True, "size_bytes": int(size or 0), "active_connections": int(conns or 0),
                    "sessions_open": open_sessions, "sessions_today": int(sessions_today or 0),
                    "payments_today": int(pay_today[0] or 0), "revenue_today": float(pay_today[1] or 0)}
    except Exception as e:  # noqa: BLE001
        database = {"ok": False, "error": str(e)[:120]}

    # ── Харилцан холболт: камер/хаалт TCP амьд + last_seen ──
    devices = db.query(Device).filter(Device.is_active.is_(True)).all()
    dev_targets = [(d, d.ip_address) for d in devices if d.ip_address]
    alive_map = {}
    if dev_targets:
        results = await asyncio.gather(*[_tcp_alive(ip) for _, ip in dev_targets])
        alive_map = {d.id: r for (d, _), r in zip(dev_targets, results)}

    def _dev_row(d):
        age = int(now - d.last_seen.timestamp()) if d.last_seen else None
        return {"id": d.id, "name": d.name, "site_id": d.site_id, "ip": d.ip_address,
                "lane_dir": d.lane_dir, "reachable": alive_map.get(d.id),
                "last_seen_age_sec": age}

    cameras = [_dev_row(d) for d in devices if d.device_type == "camera"]
    barriers = [_dev_row(d) for d in devices if d.device_type == "barrier"]
    ws_clients = sum(len(s) for s in manager.connections.values())

    return {
        "app": {
            "name": settings.app_name,
            "version": _git_version(),
            "uptime_seconds": int(now - _START),
            "debug": settings.debug,
            "mock": {"qpay": settings.qpay_mock, "barrier": settings.barrier_mock,
                     "ebarimt": settings.ebarimt_mock, "simulate": settings.allow_simulate},
        },
        "system": _system_metrics(),
        "kernel": _kernel(),
        "reboot_required": _reboot_required(),
        "services": [{"name": n, "status": _service_status(s)} for n, s in [
            ("Backend (API)", "parking-backend"), ("Database (PostgreSQL)", "postgresql"),
            ("Web (nginx)", "nginx"), ("Docker", "docker"),
        ]],
        "database": database,
        "integrations": {
            "cameras": cameras,
            "barriers": barriers,
            "qpay": await _qpay_reachable(),
            "websocket_clients": ws_clients,
        },
        "generated_at": int(now),
    }
