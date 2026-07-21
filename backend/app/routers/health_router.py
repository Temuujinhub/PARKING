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
from ..models import Device, User
from ..services import ebarimt
from ..ws import manager

try:
    import psutil  # серверийн metrics; суулгаагүй бол degrade
except Exception:  # noqa: BLE001
    psutil = None

router = APIRouter(prefix="/api/health", tags=["health"])

# Хүснэгт → ангилал (өгөгдлийн сан ямар төрлийн датагаар хэдэн хувь дүүрснийг харуулна)
TABLE_CATEGORY = {
    # Зогсолт ба төлбөр (session, гүйлгээ, баримт, тооцоо)
    "payments": "Зогсолт/төлбөр", "parking_sessions": "Зогсолт/төлбөр",
    "vat_receipts": "Зогсолт/төлбөр", "compensations": "Зогсолт/төлбөр",
    "daily_settlements": "Зогсолт/төлбөр", "cashier_shifts": "Зогсолт/төлбөр",
    # Камер, хаалтын мэдээлэл (LPR event, хаалтны команд)
    "lpr_events": "Камер/хаалт", "barrier_commands": "Камер/хаалт",
    # Лог / түүх
    "audit_logs": "Лог/түүх",
    # Техникийн тохиргоо
    "users": "Тохиргоо", "parking_sites": "Тохиргоо", "devices": "Тохиргоо",
    "tariff_templates": "Тохиргоо", "tariff_tiers": "Тохиргоо", "discounts": "Тохиргоо",
    "registered_drivers": "Тохиргоо", "blacklist": "Тохиргоо",
}
CATEGORY_ORDER = ["Зогсолт/төлбөр", "Камер/хаалт", "Лог/түүх", "Тохиргоо", "Бусад"]


def _snapshot_storage() -> dict | None:
    """LPR snapshot зургийн хавтасны хэмжээ (файлын тоо + байт)."""
    from ..config import settings as cfg
    root = cfg.snapshot_dir
    if not os.path.isdir(root):
        return None
    total, files = 0, 0
    try:
        for dirpath, _dirs, names in os.walk(root):
            for n in names:
                try:
                    total += os.path.getsize(os.path.join(dirpath, n))
                    files += 1
                except OSError:
                    continue
    except OSError:
        return None
    return {"bytes": total, "files": files}


def _db_storage(db) -> dict:
    """Өгөгдлийн сан дахь хүснэгтүүдийн эзлэх зайг ангиллаар бүлэглэж хувиар гаргана."""
    try:
        rows = db.execute(text(
            "SELECT relname, pg_total_relation_size(relid) AS bytes "
            "FROM pg_stat_user_tables")).all()
    except Exception:  # noqa: BLE001
        return {}
    cats, tops, total = {}, [], 0
    for relname, b in rows:
        b = int(b or 0)
        total += b
        cats[TABLE_CATEGORY.get(relname, "Бусад")] = cats.get(TABLE_CATEGORY.get(relname, "Бусад"), 0) + b
        tops.append({"table": relname, "bytes": b})
    categories = [{"name": c, "bytes": cats[c], "percent": round(cats[c] * 100 / total, 1) if total else 0}
                  for c in CATEGORY_ORDER if c in cats]
    tops.sort(key=lambda x: x["bytes"], reverse=True)
    for t in tops:
        t["percent"] = round(t["bytes"] * 100 / total, 1) if total else 0
    return {"total_bytes": total, "categories": categories, "top_tables": tops[:6],
            "snapshots": _snapshot_storage()}

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


def _service_list() -> list[tuple[str, str]]:
    """Шалгах systemd сервисүүд. Docker-ийг ЗӨВХӨН суулгасан үед нэмнэ
    (энэ систем docker ашигладаггүй, systemd-ээр шууд ажилладаг тул суулгаагүй бол харуулахгүй)."""
    svc = [("Backend (API)", "parking-backend"), ("Database (PostgreSQL)", "postgresql"),
           ("Web (nginx)", "nginx")]
    if shutil.which("docker"):
        svc.append(("Docker", "docker"))
    return svc


def _kernel() -> str:
    try:
        return os.uname().release
    except Exception:  # noqa: BLE001
        return "unknown"


def _reboot_required() -> bool:
    # Ubuntu/Debian: kernel/багц шинэчилсний дараа энэ файл үүснэ
    return os.path.exists("/var/run/reboot-required") or os.path.exists("/run/reboot-required")


def _ssl_expiry() -> dict | None:
    """nginx-ийн serve хийж буй SSL сертификатын дуусах хугацаа (Let's Encrypt)."""
    from urllib.parse import urlparse
    host = urlparse(settings.public_base_url).hostname
    if not host or host in ("localhost", "127.0.0.1"):
        return None
    import socket
    import ssl
    from datetime import datetime, timezone
    try:
        ctx = ssl.create_default_context()
        # nginx локалдоо ижил сертификатыг serve хийдэг тул 127.0.0.1 руу холбогдоно —
        # дотоод сүлжээнээс гадаад IP руу hairpin NAT ажиллахгүй байсан ч зөв шалгана
        with socket.create_connection(("127.0.0.1", 443), timeout=3) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as ss:
                cert = ss.getpeercert()
        exp = datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
        return {"host": host, "expires_at": exp.isoformat(),
                "days_left": (exp - datetime.now(timezone.utc)).days}
    except Exception:  # noqa: BLE001
        return None


def _pg_backup(db) -> dict | None:
    """Сүүлийн DB backup (update.sh-ийн /root/parking-backup-*.sql) + replication төлөв."""
    import glob
    info = {}
    try:
        files = glob.glob("/root/parking-backup-*.sql")
        if files:
            newest = max(files, key=os.path.getmtime)
            info["file"] = os.path.basename(newest)
            info["age_sec"] = int(time.time() - os.path.getmtime(newest))
            info["size_bytes"] = os.path.getsize(newest)
    except Exception:  # noqa: BLE001
        pass
    try:
        info["replicas"] = int(db.execute(text("SELECT count(*) FROM pg_stat_replication")).scalar() or 0)
    except Exception:  # noqa: BLE001
        pass
    return info or None


def _fd_stats() -> dict | None:
    """Системд нээлттэй файл дескрипторын тоо (ачаалал)."""
    try:
        with open("/proc/sys/fs/file-nr") as f:
            alloc, _, mx = f.read().split()
        return {"allocated": int(alloc), "max": int(mx)}
    except Exception:  # noqa: BLE001
        return None


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
    try:
        backend_rss = psutil.Process().memory_info().rss
    except Exception:  # noqa: BLE001
        backend_rss = None
    try:
        proc_count = len(psutil.pids())
    except Exception:  # noqa: BLE001
        proc_count = None
    try:
        dio = psutil.disk_io_counters()
        disk_io = {"read_bytes": dio.read_bytes, "write_bytes": dio.write_bytes} if dio else None
    except Exception:  # noqa: BLE001
        disk_io = None
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
        "backend_rss": backend_rss,
        "processes": proc_count,
        "disk_io": disk_io,
        "open_files": _fd_stats(),
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
        maxc = db.execute(text("SELECT setting FROM pg_settings WHERE name='max_connections'")).scalar()
        # Health = ажиллагааны мониторинг: санхүү/session тоо биш, харин САНГИЙН эрүүл мэнд —
        # хэмжээ, холболт, ямар төрлийн датагаар хэдэн хувь дүүрсэн (storage breakdown)
        database = {"ok": True, "size_bytes": int(size or 0), "active_connections": int(conns or 0),
                    "max_connections": int(maxc or 0), "storage": _db_storage(db)}
    except Exception as e:  # noqa: BLE001
        database = {"ok": False, "error": str(e)[:120]}

    # ── Харилцан холболт: камер/хаалт TCP амьд + last_seen ──
    devices = db.query(Device).filter(Device.status == "active").all()
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
            "started_at": int(_START),  # backend хамгийн сүүлд restart хийсэн epoch
            "debug": settings.debug,
            # e-Barimt: QR-аар (QPay ebarimt_v3) бодит баримт үүсдэг тул qpay_ebarimt асаалттай
            # + qpay бодит үед "бодит" гэж үзнэ (локал PosAPI mock нь зөвхөн картын нөөц суваг)
            "mock": {"qpay": settings.qpay_mock, "barrier": settings.barrier_mock,
                     "ebarimt": settings.ebarimt_mock and not (settings.qpay_ebarimt and not settings.qpay_mock),
                     "simulate": settings.allow_simulate},
        },
        "system": _system_metrics(),
        "kernel": _kernel(),
        "reboot_required": _reboot_required(),
        "services": [{"name": n, "status": _service_status(s)} for n, s in _service_list()],
        "database": database,
        "integrations": {
            "cameras": cameras,
            "barriers": barriers,
            "qpay": await _qpay_reachable(),
            "websocket_clients": ws_clients,
        },
        # Үйл ажиллагаа / хамгаалалт — SSL, backup, ТЕГ авто-илгээлт
        "ops": {
            "ssl": _ssl_expiry(),
            "backup": _pg_backup(db),
            "ebarimt_last_send": ebarimt.last_send_at(),
        },
        "generated_at": int(now),
    }
