"""Системийн эрүүл мэнд (System Health) — админд зориулсан бүрэн мониторинг.

Сервер (CPU/RAM/диск/сүлжээ/халалт) + сервисүүд (systemd/nginx/postgres/docker/kernel)
+ database статистик + харилцан холболт (камер/хаалт TCP амьд, QPay API хүрэх, WebSocket).
Хүнд ажиллагаа байхгүй — бүгд хурдан, timeout-той, алдаа гарвал 'unknown' болж уначихгүй.
"""
import asyncio
import os
import re
import shutil
import socket
import subprocess
import time

from fastapi import APIRouter, Depends
from sqlalchemy import text

from ..auth import get_current_user, require_role
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


def _db_storage(db, snapshots: dict | None = None) -> dict:
    """Өгөгдлийн сан дахь хүснэгтүүдийн эзлэх зайг ангиллаар бүлэглэж хувиар гаргана.
    snapshots-ийг thread дээр урьдчилан тооцсон утгаар өгнө (os.walk удаан)."""
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
            "snapshots": snapshots}

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


def _restart_history(hours: int = 24) -> dict | None:
    """Backend хэдэн удаа, ЯАГААД дахин эхэлснийг journald-аас гаргана.

    2026-08-10: 48 цагт 100 удаа дахин эхэлсэн нь илэрсэн — тэр агшин бүрд
    орж байсан машин бүртгэгдэлгүй алдагдсан. Тиймээс энэ нь зөвхөн
    «сонирхолтой» мэдээлэл биш, ОРЛОГЫН алдагдлын шууд шалтгаан."""
    if not shutil.which("journalctl"):
        return None
    try:
        out = subprocess.run(
            ["journalctl", "-u", "parking-backend", "--since", f"-{hours}h",
             "-o", "short-iso", "--no-pager"],
            capture_output=True, text=True, timeout=25).stdout
    except Exception:  # noqa: BLE001
        return None

    # Шалтгааныг RESTART ТУС БҮРД нь тогтооно. «Stopped»/«Deactivated» гэсэн
    # мөрүүд зогсолт бүрд гардаг тул тэдгээрийг зүгээр тоолвол давхардаж
    # хуурамч дүр зураг өгдөг (2026-08-10: 45 restart → «deploy 92» гэж гарсан).
    # Тиймээс restart бүрийн ӨМНӨХ цонхыг хараад ганц шалтгаан оноодог.
    lines = out.splitlines()
    causes = [
        ("Санах ой дүүрсэн (OOM)", ("out of memory", "oom-kill", "oom_reaper")),
        ("Watchdog албадан дахин эхлүүлэв", ("watchdog", "sigkill", "killing process")),
        ("Алдаагаар унасан", ("failed with result", "core-dump", "main process exited")),
        ("Шинэчлэлт (autodeploy/update.sh)", ("[autodeploy", "update.sh")),
    ]
    starts, reasons = [], {}
    for i, ln in enumerate(lines):
        if "started parking-backend" not in ln.lower():
            continue
        ts = re.match(r"^(\S+)", ln)
        ts = ts.group(1) if ts else ""
        # Дараалсан Starting+Started-ыг нэг гэж үзнэ (минутын нарийвчлалаар)
        if starts and ts[:16] == starts[-1][:16]:
            continue
        starts.append(ts)
        window = "\n".join(lines[max(0, i - 40):i]).lower()
        label = "Гараар дахин эхлүүлсэн"     # тодорхой шалтгаан олдоогүй бол
        for name, keys in causes:
            if any(k in window for k in keys):
                label = name
                break
        reasons[label] = reasons.get(label, 0) + 1
    uniq = starts
    top = sorted(reasons.items(), key=lambda kv: -kv[1])
    return {
        "hours": hours,
        "restarts": len(uniq),
        "last": uniq[-1] if uniq else None,
        "reasons": [{"label": k, "count": v} for k, v in top[:5]],
        # Хэвийн эсэх: өдөрт 3-аас олон бол анхаарал татна
        "level": "ok" if len(uniq) <= 3 else ("warn" if len(uniq) <= 12 else "bad"),
    }


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


def _blocking_probe() -> dict:
    """Удаан/blocking шалгалтууд НЭГ дор — тусдаа thread дээр ажиллана.

    psutil.cpu_percent (0.3с унтдаг), subprocess (git/systemctl), os.walk
    (snapshot хавтаст хэдэн арван мянган зураг байж болно), SSL handshake —
    эдгээрийг event loop дээр шууд дуудвал нэг админ Health хуудас нээхэд
    хаалт/LPR боловсруулалт хэдэн секунд царцдаг байсан."""
    return {
        "system": _system_metrics(),
        "kernel": _kernel(),
        "reboot_required": _reboot_required(),
        "services": [{"name": n, "status": _service_status(s)} for n, s in _service_list()],
        "restarts": _restart_history(),
        "version": _git_version(),
        "ssl": _ssl_expiry(),
        "snapshots": _snapshot_storage(),
    }


@router.get("/system")
async def system_health(db=Depends(get_db), user: User = Depends(require_role("ADMIN", "SUPER_ADMIN"))):
    now = time.time()

    # Blocking хэсгүүд thread дээр — event loop-ыг чөлөөтэй үлдээнэ
    probe = await asyncio.to_thread(_blocking_probe)

    # ── Database статистик ──
    database = {"ok": False}
    try:
        size = db.execute(text("SELECT pg_database_size(current_database())")).scalar()
        conns = db.execute(text("SELECT count(*) FROM pg_stat_activity")).scalar()
        maxc = db.execute(text("SELECT setting FROM pg_settings WHERE name='max_connections'")).scalar()
        # Health = ажиллагааны мониторинг: санхүү/session тоо биш, харин САНГИЙН эрүүл мэнд —
        # хэмжээ, холболт, ямар төрлийн датагаар хэдэн хувь дүүрсэн (storage breakdown)
        database = {"ok": True, "size_bytes": int(size or 0), "active_connections": int(conns or 0),
                    "max_connections": int(maxc or 0), "storage": _db_storage(db, probe["snapshots"])}
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
            "version": probe["version"],
            "uptime_seconds": int(now - _START),
            "started_at": int(_START),  # backend хамгийн сүүлд restart хийсэн epoch
            "debug": settings.debug,
            # e-Barimt: QR-аар (QPay ebarimt_v3) бодит баримт үүсдэг тул qpay_ebarimt асаалттай
            # + qpay бодит үед "бодит" гэж үзнэ (локал PosAPI mock нь зөвхөн картын нөөц суваг)
            "mock": {"qpay": settings.qpay_mock, "barrier": settings.barrier_mock,
                     "ebarimt": settings.ebarimt_mock and not (settings.qpay_ebarimt and not settings.qpay_mock),
                     "simulate": settings.allow_simulate},
        },
        "system": probe["system"],
        "kernel": probe["kernel"],
        "reboot_required": probe["reboot_required"],
        "services": probe["services"],
        "restarts": probe.get("restarts"),
        "database": database,
        "integrations": {
            "cameras": cameras,
            "barriers": barriers,
            "qpay": await _qpay_reachable(),
            "websocket_clients": ws_clients,
        },
        # Үйл ажиллагаа / хамгаалалт — SSL, backup, ТЕГ авто-илгээлт
        "ops": {
            "ssl": probe["ssl"],
            "backup": _pg_backup(db),
            "ebarimt_last_send": ebarimt.last_send_at(),
        },
        "generated_at": int(now),
    }


# ─── Камерын гүйцэтгэл (1ц/6ц) — бүгд DB + санах ойгоос, камерт хандахгүй ─────
# Дүн шинжилгээ 2026-07-28: асуудлыг илрүүлэхэд амжилтын %, RPC p95, LED %,
# чимээгүй завсар хамгийн их хэрэгтэй байсан. Энэ endpoint тэдгээрийг зогсоол/
# камер бүрээр тооцож, босго давсныг alerts болгож буцаана (Dashboard-д банер).

@router.get("/anpr-bridge")
def anpr_bridge_stats(user: User = Depends(get_current_user)):
    """ANPR гүүрийн төлөв: хэдэн уншилт ирсэн, хэд нь манайд БАЙХГҮЙ байсан.

    `loss_pct` = тэдний харсан уншилтын хэдэн хувь нь манайд ирээгүй вэ —
    манай стримийн бодит алдагдал. `unmapped_cams` нь зураглаагүй камерууд
    (Тохиргоо → Төхөөрөмж дээр `extra.anpr_camera_id` бичнэ)."""
    from ..services.anpr_bridge import snapshot
    return snapshot()


@router.get("/cameras")
def camera_performance(db=Depends(get_db), user: User = Depends(get_current_user)):
    from collections import defaultdict
    from datetime import datetime, timedelta

    from ..models import BarrierCommand, LprEvent, ParkingSite
    from ..services.barrier import screen_stats
    from ..services.camera_sessions import foreign_info, measurement_status

    now = datetime.utcnow()
    h1, h6 = now - timedelta(hours=1), now - timedelta(hours=6)

    sites = {s.id: s for s in db.query(ParkingSite).filter(ParkingSite.is_active.is_(True)).all()}
    devs = db.query(Device).filter(Device.status == "active").all()
    cams = [d for d in devs if d.device_type == "camera" and d.site_id in sites]
    bars = [d for d in devs if d.device_type == "barrier" and d.site_id in sites]

    ev_rows = (db.query(LprEvent.device_id, LprEvent.created_at)
               .filter(LprEvent.created_at >= h6)
               .order_by(LprEvent.created_at).all())
    ev_by_dev = defaultdict(list)
    for did, ts in ev_rows:
        ev_by_dev[did].append(ts)
    cmd_rows = (db.query(BarrierCommand)
                .filter(BarrierCommand.created_at >= h6,
                        BarrierCommand.command.in_(["open", "force_open"])).all())
    cmd_by_dev = defaultdict(list)
    for c in cmd_rows:
        cmd_by_dev[c.device_id].append(c)

    def _p95(vals):
        if not vals:
            return None
        vals = sorted(vals)
        return vals[max(0, min(len(vals) - 1, int(round(0.95 * (len(vals) - 1)))))]

    def _cmd_stats(cl):
        tot = len(cl)
        ok = sum(1 for c in cl if c.status == "SUCCESS")
        durs = [c.duration_ms for c in cl if c.status == "SUCCESS" and c.duration_ms]
        fails = [c for c in cl if c.status == "FAILED"]
        last_fail = max((c.created_at for c in fails), default=None)
        return {"total": tot, "ok": ok,
                "success_pct": round(ok * 100 / tot) if tot else None,
                "p95_ms": _p95(durs),
                "last_fail_at": last_fail.isoformat() if last_fail else None}

    def _barrier_for(cam):
        same = [b for b in bars if b.site_id == cam.site_id
                and b.lane_no == cam.lane_no and b.lane_dir == cam.lane_dir]
        if same:
            return same[0]
        site_bars = [b for b in bars if b.site_id == cam.site_id]
        return site_bars[0] if site_bars else None

    rows, alerts = [], []
    for cam in cams:
        site = sites[cam.site_id]
        evs = ev_by_dev.get(cam.id, [])
        gaps = [(b - a).total_seconds() for a, b in zip(evs, evs[1:])]
        gap_max = max(gaps) if gaps else None
        gap_now = (now - evs[-1]).total_seconds() if evs else None
        last_seen_age = (now - cam.last_seen).total_seconds() if cam.last_seen else None

        b = _barrier_for(cam)
        bc = cmd_by_dev.get(b.id, []) if b else []
        cmd1 = _cmd_stats([c for c in bc if c.created_at >= h1])
        cmd6 = _cmd_stats(bc)
        led1_ok, led1_fail = screen_stats(cam.ip_address, h1)
        led6_ok, led6_fail = screen_stats(cam.ip_address, h6)
        who = foreign_info(cam.id) or {}

        row = {
            "site_code": site.site_code, "site_name": site.name,
            "camera": cam.name, "ip": cam.ip_address, "lane_dir": cam.lane_dir,
            "barrier": b.name if b else None,
            "events_1h": sum(1 for t in evs if t >= h1), "events_6h": len(evs),
            "gap_max_min": round(gap_max / 60) if gap_max else None,
            "gap_now_min": round(gap_now / 60) if gap_now else None,
            "last_seen_age_sec": int(last_seen_age) if last_seen_age is not None else None,
            "cmd_1h": cmd1, "cmd_6h": cmd6,
            "led_1h": {"ok": led1_ok, "fail": led1_fail},
            "led_6h": {"ok": led6_ok, "fail": led6_fail},
            "foreign_sessions": who.get("sessions") or [],
            # Хэмжигдээгүйг ЦЭВЭРЭЭС ялгах (UI «—» гэж хоёуланг ижил харуулдаг байв)
            "foreign_checked_at": who.get("checked_at"),
            "foreign_error": who.get("error") or who.get("skipped"),
        }
        rows.append(row)

        label = f"{site.site_code} {cam.name}"
        # Стрим (heartbeat) 5+ мин алга = камер жинхэнээсээ хариугүй — улаан
        if last_seen_age is not None and last_seen_age > 300:
            alerts.append({"level": "red", "site_code": site.site_code,
                           "text": f"{label}: камер {int(last_seen_age / 60)} мин хариугүй (стрим тасарсан)"})
        # Хаалтны амжилт <90% (сүүлийн 1ц, 5+ команд)
        if cmd1["total"] >= 5 and cmd1["success_pct"] is not None and cmd1["success_pct"] < 90:
            alerts.append({"level": "red", "site_code": site.site_code,
                           "text": f"{label}: хаалтны амжилт {cmd1['success_pct']}% (сүүлийн 1ц, {cmd1['total']} команд)"})
        # LED <50% (сүүлийн 1ц, 5+ оролдлого)
        if (led1_ok + led1_fail) >= 5 and led1_ok * 2 < (led1_ok + led1_fail):
            alerts.append({"level": "red", "site_code": site.site_code,
                           "text": f"{label}: LED дэлгэц {led1_ok}/{led1_ok + led1_fail} амжилттай (сүүлийн 1ц)"})
        # 30+ мин уншилтгүй — шар (шөнө машингүй үед хэвийн байж болно)
        if gap_now is not None and gap_now > 1800 and (last_seen_age or 0) <= 300:
            alerts.append({"level": "yellow", "site_code": site.site_code,
                           "text": f"{label}: {int(gap_now / 60)} мин дугаар уншаагүй (камер онлайн — машингүй байж болно)"})

    # Гадны хандалтын ХЭМЖИЛТ өөрөө зогссон бол тэр нь «гадны хандалт алга»
    # гэсэн үг БИШ — мэдэхгүй гэсэн үг. Түүнийг ил анхааруулга болгоно.
    fs = measurement_status()
    if not fs["enabled"]:
        alerts.append({"level": "yellow", "site_code": None,
                       "text": f"Гадны хандалт ХЭМЖИГДЭХГҮЙ байна: {fs['reason']}"})
    elif cams and fs["measured"] == 0:
        alerts.append({"level": "yellow", "site_code": None,
                       "text": "Гадны хандалт нэг ч камер дээр хэмжигдээгүй "
                               "(камер лог өгөхгүй эсвэл нэвтрэлт амжилтгүй) — "
                               "«—» нь «цэвэр» гэсэн үг биш"})
    elif fs["failing"]:
        alerts.append({"level": "yellow", "site_code": None,
                       "text": f"Гадны хандалт {fs['failing']}/{fs['cameras']} камер дээр "
                               f"хэмжигдэхгүй байна (доорх хүснэгтээс шалтгааныг хар)"})

    return {"rows": rows, "alerts": alerts, "foreign_status": fs,
            "generated_at": now.isoformat()}
