"""ANPR системийн (172.16.100.20) SSE урсгалыг сонсох гүүр.

ЯАГААД: тэр систем ЯГ ТЭР камеруудаас уншилт авдаг бөгөөд `/api/events` нь
нэвтрэлтгүй SSE-ээр бүх зогсоолын уншилтыг шууд дамжуулдаг. Бидний камерын
стрим «чимээгүй үхэх» бүрд машин алдагддаг — тэдний урсгал нь ГЭРЧ болно.

ГУРВАН ГОРИМ (`anpr_bridge_mode`):
  off      — огт холбогдохгүй (анхдагч)
  shadow   — сонсоод ЗӨВХӨН БҮРТГЭНЭ. Манайд ирээгүй уншилтыг «алдсан» гэж
             тоолж, статистик хөтөлнө. Session/хаалт/төлбөрт ОГТ хүрэхгүй.
             Нэг өдөр ажиллуулж алдагдлаа хэмжихэд зориулав.
  witness  — shadow + манай стрим үхсэн болохыг НОТОЛСОН үед тухайн камерын
             холболтыг ШУУД тасалж дахин холбуулна (таймер хүлээхгүй).
  inject   — уншилтыг манай урсгал руу ОРУУЛНА (log_tail-тай ижил зам).
             Зөвхөн зураглал бүрэн батлагдсаны дараа асаана.

Зураглал: тэдний `parkingCameraId` → манай `Device`. Тохиргоо → Төхөөрөмж дээрх
төхөөрөмжийн `extra.anpr_camera_id`-д бичнэ. Зураглаагүй камерын уншилтыг
алгасах бөгөөд тоолж харуулна (юуг зураглах шаардлагатайг өөрөө хэлнэ).
"""
import asyncio
import json
import logging
import time
from collections import defaultdict
from datetime import datetime, timedelta

import httpx

from ..config import settings
from ..database import SessionLocal
from ..models import Device, LprEvent
from ..session_logic import normalize_plate, plates_ocr_similar

log = logging.getLogger("parking.anpr_bridge")

# Статистик — /api/health/anpr-bridge-ээр харуулна
stats: dict = {
    "connected": False, "since": None, "events": 0, "mapped": 0,
    "matched": 0, "missing": 0, "unmapped_cams": defaultdict(int),
    "missing_by_site": defaultdict(int), "last_event_at": None, "error": None,
}


def _camera_map(db) -> dict:
    """ANPR камерын id → манай Device. `extra.anpr_camera_id`-аар холбоно."""
    out = {}
    for d in db.query(Device).filter(Device.device_type == "camera",
                                     Device.status != "deleted").all():
        cid = (d.extra or {}).get("anpr_camera_id")
        if cid is not None:
            out[str(cid)] = d
    return out


def seen_by_us(db, device_id: str, plate: str, at: datetime, window_sec: float) -> bool:
    """Тэдний уншилт манайд бүртгэгдсэн үү (OCR-ойролцоог ч тооцно)."""
    lo, hi = at - timedelta(seconds=window_sec), at + timedelta(seconds=window_sec)
    rows = (db.query(LprEvent.plate_number)
            .filter(LprEvent.device_id == device_id,
                    LprEvent.created_at >= lo, LprEvent.created_at <= hi).all())
    return any(p == plate or plates_ocr_similar(p, plate) for (p,) in rows)


async def _handle(raw: dict):
    """Нэг event: зураглал → тулгалт → (горимоос хамаарч) арга хэмжээ."""
    plate = normalize_plate(str(raw.get("plateNumber") or ""))
    cam_id = str(raw.get("parkingCameraId") or "")
    if not plate or not cam_id:
        return
    stats["events"] += 1
    stats["last_event_at"] = datetime.utcnow().isoformat(timespec="seconds")

    db = SessionLocal()
    try:
        dev = _camera_map(db).get(cam_id)
        if dev is None:
            stats["unmapped_cams"][f"{cam_id}·{raw.get('parkingLotName') or '?'}"] += 1
            return
        stats["mapped"] += 1
        # Тэдний timestamp нь камерын цаг — гулсдаг тул цонх өргөн авна
        try:
            at = datetime.fromisoformat(str(raw["timestamp"]).replace("Z", "+00:00"))
            at = at.replace(tzinfo=None)
        except Exception:  # noqa: BLE001
            at = datetime.utcnow()
        if seen_by_us(db, dev.id, plate, at, settings.anpr_bridge_match_window_sec):
            stats["matched"] += 1
            return
        stats["missing"] += 1
        stats["missing_by_site"][dev.site.name if dev.site else dev.site_id] += 1
        log.warning("[anpr] МАНАЙД БАЙХГҮЙ: %s · %s · камер %s (%s)",
                    plate, raw.get("parkingLotName"), cam_id, dev.name)

        mode = settings.anpr_bridge_mode
        if mode == "witness":
            # Манай стрим тэр камер дээр үхсэн нь НОТЛОГДЛОО — дахин холбуулна
            from .cgi_poller import force_reconnect
            force_reconnect(dev.id)
        elif mode == "inject":
            from ..session_logic import handle_entry, handle_exit, handle_inner_pass
            raw_ev = {"anpr_bridge": True, "source_id": raw.get("id"),
                      "TrafficCar": {"PlateNumber": plate}}
            if dev.nested_inner:
                await handle_inner_pass(db, dev, plate, 100.0, raw_ev)
            elif dev.lane_dir == "exit":
                await handle_exit(db, dev, plate, 100.0, raw_ev)
            else:
                await handle_entry(db, dev, plate, 100.0, raw_ev)
            log.warning("[anpr] ОРУУЛАВ: %s %s (%s)", dev.lane_dir, plate, dev.name)
    except Exception as e:  # noqa: BLE001 — нэг event урсгалыг зогсоохгүй
        log.error("[anpr] боловсруулах алдаа: %r", e)
    finally:
        db.close()


async def supervisor():
    """SSE урсгалыг тасралтгүй сонсоно (reconnect-тэй)."""
    if settings.anpr_bridge_mode == "off" or not settings.anpr_bridge_url:
        return
    url = settings.anpr_bridge_url
    log.info("ANPR гүүр идэвхжлээ (%s) — горим: %s", url, settings.anpr_bridge_mode)
    while True:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10, read=None)) as c:
                async with c.stream("GET", url, headers={"Accept": "text/event-stream"}) as r:
                    r.raise_for_status()
                    stats["connected"] = True
                    stats["since"] = datetime.utcnow().isoformat(timespec="seconds")
                    stats["error"] = None
                    async for line in r.aiter_lines():
                        if not line.startswith("data:"):
                            continue          # `event:`/comment мөрүүд
                        body = line[5:].strip()
                        if not body or body.startswith("{\"maxEvents\""):
                            continue          # config мессеж
                        try:
                            await _handle(json.loads(body))
                        except json.JSONDecodeError:
                            continue
        except Exception as e:  # noqa: BLE001
            stats["connected"] = False
            stats["error"] = f"{type(e).__name__}: {e}"[:200]
            log.warning("ANPR гүүр тасарлаа — %sс дараа дахин холбоно (%s)",
                        settings.anpr_bridge_reconnect_sec, stats["error"])
        await asyncio.sleep(max(1.0, settings.anpr_bridge_reconnect_sec))


def snapshot() -> dict:
    """Статистикийн хуулбар (JSON-д тохирсон)."""
    out = dict(stats)
    out["unmapped_cams"] = dict(sorted(stats["unmapped_cams"].items(),
                                       key=lambda kv: -kv[1])[:20])
    out["missing_by_site"] = dict(sorted(stats["missing_by_site"].items(),
                                         key=lambda kv: -kv[1]))
    out["mode"] = settings.anpr_bridge_mode
    out["loss_pct"] = round(100 * stats["missing"] / stats["mapped"], 1) if stats["mapped"] else 0
    return out
