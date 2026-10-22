"""Түншийн газрын зурагт (/api/v1/parkings, /api/v1/sites) EV цэнэглэгч — 2026-10-22.

Wallet апп (MonPay, SocialPay, Easy Wallet) зураг дээр цэнэглэгчтэй зогсоолыг ялгаж,
цэнэглэгч бүрийн чадал (кВт), бууцны тоо, амьд төлвийг харуулна.

Эх сурвалж:
  • бүртгэл (нэр, кВт, бууцны тоо) — core-ийн `ev_chargers` (идэвхтэй мөр л). Цэнэглэгч
    `devices` БИШ.
  • амьд төлөв — EV hub (`/internal/chargers`): хүсэлт бүрт биш, процесс доторх 30с кэш,
    НЭГ batched дуудлага, хатуу ~1.5с timeout. Hub тохируулаагүй/унтарсан/алдаатай бол
    төлөв UNKNOWN — газрын зураг hub-ийн улмаас хэзээ ч 5xx өгөхгүй, удаашрахгүй.
    Цэнэглэгчгүй үед hub руу огт хандахгүй.

OCPP 1.6 бууцны төлөв → газрын зургийн энгийн төлөв:
  Available                                    → AVAILABLE  сул, залгаж болно
  Preparing, Charging, SuspendedEV,
  SuspendedEVSE, Finishing, Reserved           → CHARGING   эзэлсэн (машин залгаастай /
                                                            цэнэглэж буй / дуусгаж буй / захиалгатай)
  Faulted                                      → FAULTED    гэмтэлтэй
  Unavailable                                  → OFFLINE    ашиглалтаас гаргасан
  бусад / ирээгүй                              → UNKNOWN
Цэнэглэгч hub-д холбогдоогүй (online=false эсвэл hub-д огт алга) бол бүх бууц OFFLINE.
Connector 0 (цэнэглэгч бүхэлдээ) Faulted/Unavailable бол сул/үл мэдэгдэх бууцнууд мөн
FAULTED/OFFLINE болно (ашиглах боломжгүй).
Цэнэглэгчийн төлөв = бууцнуудын хамгийн «сайн» нь: AVAILABLE > CHARGING > UNKNOWN > FAULTED > OFFLINE.

cp_id, charger_key (QR түлхүүр), дотоод id ГАДАГШ ГАРАХГҮЙ. Бүртгэхэд нэр нь анхдагчаар
cp_id болдог тул тийм нэрийг «Цэнэглэгч N» болгоно.
"""
import logging
import threading
import time
from decimal import Decimal

import httpx

from ..config import settings
from . import ev_hub

log = logging.getLogger("parking.ev_map")

AVAILABLE, CHARGING, FAULTED, OFFLINE, UNKNOWN = (
    "AVAILABLE", "CHARGING", "FAULTED", "OFFLINE", "UNKNOWN")

OCPP_TO_MAP = {
    "Available": AVAILABLE,
    "Preparing": CHARGING, "Charging": CHARGING, "SuspendedEV": CHARGING,
    "SuspendedEVSE": CHARGING, "Finishing": CHARGING, "Reserved": CHARGING,
    "Faulted": FAULTED,
    "Unavailable": OFFLINE,
}
_RANK = {AVAILABLE: 0, CHARGING: 1, UNKNOWN: 2, FAULTED: 3, OFFLINE: 4}

# connect 0.5с + унших 1.0с ⇒ хамгийн ихдээ ~1.5с (GET тул бичих хугацаа өчүүхэн)
HUB_TIMEOUT = httpx.Timeout(1.0, connect=0.5)
CACHE_SEC = 30.0

_cache: dict = {"at": None, "data": None}
_lock = threading.Lock()


def map_status(ocpp_status) -> str:
    return OCPP_TO_MAP.get(str(ocpp_status or ""), UNKNOWN)


def invalidate_cache() -> None:
    _cache["at"], _cache["data"] = None, None


def _fresh() -> bool:
    return _cache["at"] is not None and time.monotonic() - _cache["at"] < CACHE_SEC


def live_chargers() -> dict | None:
    """cp_id → hub мөр (online, connectors[]). Hub тохируулаагүй/алдаатай бол None (= UNKNOWN).
    Алдааг ч 30с кэшлэнэ — hub унтарсан үед хүсэлт бүр timeout хүлээхгүй; зэрэг ирсэн
    хүсэлтүүд hub руу ганц дуудлага хийнэ (lock)."""
    if _fresh():
        return _cache["data"]
    with _lock:
        if _fresh():
            return _cache["data"]
        data = None
        if settings.evhub_url:
            try:
                rows = ev_hub.list_chargers_sync(HUB_TIMEOUT)
                if not isinstance(rows, list):
                    raise TypeError(f"hub хариу жагсаалт биш: {type(rows).__name__}")
                data = {str(h["cp_id"]): h for h in rows if isinstance(h, dict) and h.get("cp_id")}
            except Exception as e:    # газрын зураг hub-ийн ямар ч алдаагаар унахгүй
                log.warning("ev_map: hub-ийн төлөв авч чадсангүй (UNKNOWN): %s", e)
                data = None
        _cache["at"], _cache["data"] = time.monotonic(), data
        return data


def charger_live_status(charger, live: dict | None) -> tuple[str, list[tuple[int, str]]]:
    """(цэнэглэгчийн төлөв, [(бууц, төлөв)]) — бууцнууд 1..connector_count (core бүртгэл)."""
    ids = range(1, max(int(charger.connector_count or 0), 0) + 1)
    if live is None:
        return UNKNOWN, [(i, UNKNOWN) for i in ids]
    h = live.get(str(charger.cp_id))
    if not h or not h.get("online"):
        return OFFLINE, [(i, OFFLINE) for i in ids]
    by_id: dict[int, str] = {}
    for x in h.get("connectors") or []:
        if not isinstance(x, dict):
            continue
        try:
            by_id[int(x.get("connector_id"))] = map_status(x.get("status"))
        except (TypeError, ValueError):
            continue
    whole = by_id.get(0)
    conns = []
    for i in ids:
        st = by_id.get(i, UNKNOWN)
        if whole in (FAULTED, OFFLINE) and st in (AVAILABLE, UNKNOWN):
            st = whole
        conns.append((i, st))
    if not conns:
        return (whole if whole in (FAULTED, OFFLINE) else UNKNOWN), conns
    return min((s for _, s in conns), key=_RANK.__getitem__), conns


def _kw(value):
    if value is None:
        return None
    d = Decimal(str(value))
    return int(d) if d == d.to_integral_value() else float(d)


def ev_site_summary(db, sites) -> dict:
    """site_id → {has_ev_charger, ev_chargers[], ev_connectors_total, ev_connectors_free}.

    Зөвхөн ГАДНА зогсоол (доторх зогсоол газрын зурагт тусдаа цэг биш); идэвхгүй цэнэглэгч
    алгасна. ev_connectors_free = AVAILABLE бууцны тоо; hub-ийн төлөв мэдэгдэхгүй бол None."""
    from ..models import EvCharger
    outer = [s.id for s in sites if not s.parent_site_id]
    rows = (db.query(EvCharger)
            .filter(EvCharger.site_id.in_(outer), EvCharger.is_active.is_(True))
            .order_by(EvCharger.created_at, EvCharger.id).all()) if outer else []
    live = live_chargers() if rows else None
    by_site: dict = {}
    for c in rows:
        by_site.setdefault(c.site_id, []).append(c)
    out = {}
    for s in sites:
        items, free = [], 0
        for n, c in enumerate(by_site.get(s.id, []), 1):
            status, conns = charger_live_status(c, live)
            free += sum(1 for _, st in conns if st == AVAILABLE)
            name = (c.name or "").strip()
            items.append({
                "name": name if name and name != c.cp_id else f"Цэнэглэгч {n}",
                "power_kw": _kw(c.power_kw),
                "connector_count": len(conns),
                "status": status,
                "connectors": [{"connector_id": i, "status": st} for i, st in conns],
            })
        out[s.id] = {
            "has_ev_charger": bool(items),
            "ev_chargers": items,
            "ev_connectors_total": sum(i["connector_count"] for i in items),
            "ev_connectors_free": (free if live is not None else None) if items else 0,
        }
    return out
