"""Түүхийн Excel экспорт — мөр бүтээх нэг эх + том экспортын АРЫН ПРОЦЕСС.

2026-10-22 (system test №62): синхрон `/api/sessions/excel` 20000 мөрөөр
таслагддаг байсан (Хангарьд ганцаараа сард ~23k бүртгэлтэй, 3-р эмнэлэг ~42k).
Хязгаарыг зүгээр өсгөж болохгүй: openpyxl 125k мөрийг санах ойд ~35с, ~600MB-аар
үүсгэдэг, uvicorn ганц worker-тэй тул тэр хооронд камерын event / хаалтын
команд удааширна, nginx 60с-д 504 өгнө.

Шийдэл — том экспорт ТУСДАА процесс:
  • `start_job` нь шүүлтүүрийг JSON файлд бичээд `python -m app.services.history_export
    <job.json>`-ийг `nice` (анхдагч 15)-тэй эхлүүлж шууд буцна → API хүлээхгүй;
  • процесс нь DB-ээс `yield_per`-ээр 2000 мөрөөр урсгаж, openpyxl `write_only`
    (санах ой тогтмол) XLSX-ийг файлд бичнэ, явцаа JSON-д шинэчилнэ;
  • UI `/api/sessions/export/{id}`-ийг 2с тутам асууж, бэлэн болмогц татна;
  • нэг зэрэг НЭГ л экспорт, файл `history_export_keep_hours`-ийн дараа устна;
  • сервер дахин ачаалагдвал (autodeploy) процесс тасарна → төлөв «тасарсан»
    болж, хэрэглэгч дахин эхлүүлнэ (хагас файл үлдэхгүй — эцэст нь rename).

Мөрийн агуулга синхрон Excel-тэй ЯГ ижил (`HEADERS`, `build_rows`) — хоёр зам
өөр багана гаргахгүй.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta
from itertools import islice
from pathlib import Path

from ..config import settings

log = logging.getLogger("parking.history_export")

TZ = timedelta(hours=settings.tz_offset_hours)
CHUNK = 2000
BACKEND_DIR = Path(__file__).resolve().parents[2]   # .../backend (.env энд)

HEADERS = ["Дугаар", "Зогсоол", "Орсон", "Гарсан", "Орох камер", "Гарах камер",
           "Хугацаа (мин)", "Дүн (₮)", "Төлсөн (₮)", "Төлбөрийн хэрэгсэл", "Хөнгөлөлт",
           "Төлөв", "Хаасан", "Шалтгаан", "Тэмдэглэл", "Төлбөр хүлээж эхэлсэн"]
WIDTHS = [11, 18, 17, 17, 16, 16, 13, 12, 12, 20, 14, 16, 26, 28, 30, 19]

PAY_LABEL = {"QPAY": "QPay QR", "POS": "Карт (ПОС)", "CASH": "Бэлэн",
             "TRANSFER": "Дансаар", "WALLET": "Данс (хэтэвч)"}
STATUS_LABEL = {"OPEN": "Зогсож буй", "AWAITING_PAYMENT": "Төлбөр хүлээж буй",
                "PAID": "Төлсөн", "CLOSED": "Гарсан", "FREE": "Үнэгүй",
                "MANUAL_CLOSED": "Хаасан"}


def _loc(dt) -> str:
    return (dt + TZ).strftime("%Y-%m-%d %H:%M") if dt else ""


def session_columns():
    """Экспортод хэрэгтэй багана л — ORM объект биш (300k мөрөнд identity map
    дүүрэхгүй, joined site/discount мөр бүрд давтагдахгүй)."""
    from ..models import ParkingSession as S
    return (S.id, S.plate_number, S.site_id, S.entry_time, S.exit_time,
            S.entry_device_id, S.exit_device_id, S.duration_minutes, S.total_fee,
            S.discount_id, S.status, S.note, S.payment_wait_started_at)


def _names(db) -> tuple[dict, dict]:
    from ..models import Discount, ParkingSite
    cache = db.info.get("_export_names")
    if cache is None:
        cache = ({i: n for i, n in db.query(ParkingSite.id, ParkingSite.name).all()},
                 {i: n for i, n in db.query(Discount.id, Discount.name).all()})
        db.info["_export_names"] = cache
    return cache


def build_rows(db, rows) -> list[list]:
    """Түүхийн Excel-ийн мөрүүд. `rows` нь ParkingSession эсвэл `session_columns()`-ийн
    Row (attribute-аар хандана). Төлбөр, хаалтын мэдээллийг НЭГ багц query-ээр."""
    from ..models import Payment
    from ..routers.sessions_router import _MANUAL_FREE, _MANUAL_PAID, _close_map, _device_names
    if not rows:
        return []
    ids = [r.id for r in rows]
    pays: dict[str, list] = {}
    for sid, provider, amount in (db.query(Payment.session_id, Payment.provider, Payment.amount)
                                  .filter(Payment.session_id.in_(ids), Payment.status == "PAID")):
        pays.setdefault(sid, []).append((provider, float(amount or 0)))
    closed = _close_map(db, ids)
    devices = _device_names(db)
    sites, discounts = _names(db)
    out = []
    for r in rows:
        plist = pays.get(r.id, [])
        c = closed.get(r.id)
        c_label = c_reason = ""
        if c:
            label = c["label"]
            if c["action"] == "MANUAL_EXIT":
                label = _MANUAL_PAID if plist else _MANUAL_FREE
            c_label = f"{label} — {'Систем' if c['auto'] else c['by']}"
            c_reason = c.get("reason_label") or c.get("reason") or ""
        out.append([
            r.plate_number,
            sites.get(r.site_id) or "",
            _loc(r.entry_time), _loc(r.exit_time),
            (devices.get(r.entry_device_id) or {}).get("name") or "",
            (devices.get(r.exit_device_id) or {}).get("name") or "",
            int(r.duration_minutes or 0),
            float(r.total_fee or 0),
            float(sum(a for _, a in plist)),
            ", ".join(PAY_LABEL.get(p, p) for p, _ in plist),
            discounts.get(r.discount_id) or "",
            STATUS_LABEL.get(r.status, r.status),
            c_label, c_reason,
            (r.note or "")[:200],
            _loc(r.payment_wait_started_at),
        ])
    return out


def total_row(n_rows: int, fee_sum: float, paid_sum: float) -> list:
    row = [""] * len(HEADERS)
    row[0], row[7], row[8], row[11] = "Нийт", fee_sum, paid_sum, f"{n_rows} мөр"
    return row


# ─────────────────────────── Арын ажлын төлөв ───────────────────────────
# Төлөв нь ФАЙЛД (<dir>/<id>.json) — API процесс дахин ачаалагдсан ч ажлын
# жагсаалт алдагдахгүй. Popen объектыг зөвхөн zombie-гүй цуглуулахад барина.
_PROCS: dict[str, subprocess.Popen] = {}


def export_dir() -> Path:
    d = Path(settings.history_export_dir)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _job_path(job_id: str) -> Path:
    if not job_id or not all(c in "0123456789abcdef" for c in job_id) or len(job_id) != 32:
        raise ValueError("job_id")
    return export_dir() / f"{job_id}.json"


def read_job(job_id: str) -> dict | None:
    try:
        return json.loads(_job_path(job_id).read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def _write_job(job: dict) -> None:
    path = _job_path(job["id"])
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(job, ensure_ascii=False, default=str), encoding="utf-8")
    os.replace(tmp, path)


def _pid_alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
    except (OSError, TypeError, ValueError):
        return False
    return True


def refresh(job: dict) -> dict:
    """«Явагдаж буй» гэсэн атлаа процесс нь үхсэн ажлыг «тасарсан» болгоно
    (autodeploy restart, OOM, гэнэтийн алдаа)."""
    if job.get("status") not in ("queued", "running"):
        return job
    proc = _PROCS.get(job["id"])
    if proc is not None:
        code = proc.poll()
        if code is None:
            return job
        _PROCS.pop(job["id"], None)
        dead = True
    else:
        dead = not _pid_alive(job.get("pid"))
    if dead:
        latest = read_job(job["id"]) or job      # процесс дуусахын өмнөхөн бичсэн байж болно
        if latest.get("status") in ("queued", "running"):
            latest.update(status="failed", finished_at=datetime.utcnow().isoformat(),
                          error="Экспортын процесс тасарсан (сервер дахин ачаалагдсан байж магадгүй) — дахин эхлүүлнэ үү")
            _write_job(latest)
        return latest
    return job


def cleanup() -> None:
    """Хадгалах хугацаа өнгөрсөн файлуудыг устгана (json, xlsx, log)."""
    cutoff = time.time() - settings.history_export_keep_hours * 3600
    try:
        for p in export_dir().iterdir():
            if p.suffix in (".json", ".xlsx", ".log", ".tmp") and p.stat().st_mtime < cutoff:
                job = read_job(p.stem) if p.suffix == ".json" else None
                if job and job.get("status") in ("queued", "running") and _pid_alive(job.get("pid")):
                    continue
                p.unlink(missing_ok=True)
    except OSError as e:
        log.warning("history export cleanup: %s", e)


def list_jobs(user_id: str | None = None) -> list[dict]:
    jobs = []
    for p in export_dir().glob("*.json"):
        job = read_job(p.stem)
        if job and (user_id is None or job.get("user_id") == user_id):
            jobs.append(refresh(job))
    return sorted(jobs, key=lambda j: j.get("created_at") or "", reverse=True)


def public(job: dict) -> dict:
    keys = ("id", "status", "created_at", "started_at", "finished_at", "total", "done",
            "truncated", "max_rows", "filename", "size_bytes", "error", "filters", "sort", "username")
    return {k: job.get(k) for k in keys}


def _lower_priority() -> None:     # pragma: no cover — хүүхэд процесст ажиллана
    try:
        os.nice(max(0, min(19, int(settings.history_export_nice))))
    except OSError:
        pass


def start_job(user, filters: dict, sort: str, filename: str) -> dict:
    """Ажлыг бүртгээд процессыг эхлүүлнэ. Аль хэдийн явагдаж байвал ValueError
    (тэр ажлын public төлөвтэй) — сервер нэг зэрэг НЭГ л том экспорт хийнэ."""
    cleanup()
    busy = [j for j in list_jobs() if j.get("status") in ("queued", "running")]
    if busy:
        raise RuntimeError(busy[0])
    job_id = uuid.uuid4().hex
    job = {"id": job_id, "status": "queued", "user_id": user.id, "username": user.username,
           "created_at": datetime.utcnow().isoformat(), "filters": filters, "sort": sort,
           "filename": filename, "total": None, "done": 0, "truncated": False,
           "max_rows": settings.history_export_max_rows}
    _write_job(job)
    d = export_dir()
    with open(d / f"{job_id}.log", "ab") as errlog:
        proc = subprocess.Popen(
            [sys.executable, "-m", "app.services.history_export", str(_job_path(job_id))],
            cwd=str(BACKEND_DIR), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=errlog, start_new_session=True, preexec_fn=_lower_priority)
    _PROCS[job_id] = proc
    job["pid"] = proc.pid
    latest = read_job(job_id) or job
    latest["pid"] = proc.pid
    _write_job(latest)
    return latest


def run(job_path: str) -> int:
    """Хүүхэд процессын бие. 0 = амжилттай."""
    from openpyxl import Workbook
    from openpyxl.cell import WriteOnlyCell
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter

    from ..database import SessionLocal
    from ..models import User
    from ..routers.sessions_router import _session_order, _sessions_query

    job = json.loads(Path(job_path).read_text(encoding="utf-8"))
    job.update(status="running", started_at=datetime.utcnow().isoformat(), pid=os.getpid())
    _write_job(job)
    out = export_dir() / f"{job['id']}.xlsx"
    tmp = out.with_suffix(".xlsx.tmp")
    db = SessionLocal()
    try:
        user = db.get(User, job["user_id"])
        if user is None or not user.is_active:
            raise PermissionError("Хэрэглэгч олдсонгүй эсвэл идэвхгүй")
        q = _sessions_query(db, user, **job["filters"])
        cap = int(job.get("max_rows") or settings.history_export_max_rows)
        total = q.count()
        job.update(total=min(total, cap), truncated=total > cap)
        _write_job(job)

        wb = Workbook(write_only=True)
        ws = wb.create_sheet("Түүх")
        for i, w in enumerate(WIDTHS, 1):
            ws.column_dimensions[get_column_letter(i)].width = w
        bold = Font(bold=True)

        def _bold_row(values):
            cells = []
            for v in values:
                c = WriteOnlyCell(ws, value=v)
                c.font = bold
                cells.append(c)
            return cells

        ws.append(_bold_row(HEADERS))
        stream = (q.with_entities(*session_columns())
                  .order_by(*_session_order(job.get("sort")))
                  .limit(cap).yield_per(CHUNK))
        it = iter(stream)
        done, fee_sum, paid_sum = 0, 0.0, 0.0
        while True:
            chunk = list(islice(it, CHUNK))
            if not chunk:
                break
            for row in build_rows(db, chunk):
                fee_sum += row[7]
                paid_sum += row[8]
                ws.append(row)
            done += len(chunk)
            job["done"] = done
            _write_job(job)
            time.sleep(0.05)      # DB/диск руу тасралтгүй цохихгүй
        ws.append(_bold_row(total_row(done, fee_sum, paid_sum)))
        if job["truncated"]:
            ws.append([f"⚠ {total} мөрөөс эхний {cap} л орсон — огнооны мужийг богиносгоно уу"])
        wb.save(tmp)
        os.replace(tmp, out)
        job.update(status="done", done=done, finished_at=datetime.utcnow().isoformat(),
                   size_bytes=out.stat().st_size)
        _write_job(job)
        return 0
    except Exception as e:  # noqa: BLE001 — ямар ч алдааг төлөвт бичиж UI-д харуулна
        log.exception("history export %s failed", job.get("id"))
        tmp.unlink(missing_ok=True)
        job.update(status="failed", finished_at=datetime.utcnow().isoformat(),
                   error=f"{type(e).__name__}: {e}"[:300])
        _write_job(job)
        return 1
    finally:
        db.close()


if __name__ == "__main__":     # pragma: no cover
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sys.exit(run(sys.argv[1]))
