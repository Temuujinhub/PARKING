"""EV цэнэглэлт — админ, нийтийн (жолооч) болон hub интеграцийн API (§8).

Гурван бүлэг:
  /api/admin/ev/*            — цэнэглэгч, тариф, session-ы удирдлага (эрхтэй)
  /api/public/ev/*           — жолоочийн QR урсгал (нэвтрэлтгүй, throttle-той)
  /api/integration/evhub/*   — hub → core үйл явдал + Authorize (Bearer түлхүүр)
"""
import hmac
import logging
import secrets
from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from ..auth import require
from ..config import settings
from ..database import get_db
from ..models import (AppSetting, AuditLog, ChargeSession, EvCharger,
                      EvPricePlan, ParkingSite, User, Wallet, WalletLedger)
from ..ratelimit import throttle
from ..serializers import to_dict
from ..services import ev_billing, ev_hub
from ..services import wallet as wallet_svc
from ..session_logic import normalize_plate

log = logging.getLogger("parking.ev")
router = APIRouter(tags=["ev"])


def _audit(db, username, action, entity_id, detail=None):
    db.add(AuditLog(username=username, action=action, entity="ev",
                    entity_id=str(entity_id), detail=detail or {}))


def _mask_phone(phone: str) -> str:
    p = phone or ""
    return f"{p[:2]}****{p[-2:]}" if len(p) >= 6 else "****"


# ═══════════════════════════════════════════════════════════════════════════
# HUB ИНТЕГРАЦИ — hub → core (Bearer PARKING_EVHUB_EVENTS_KEY)
# ═══════════════════════════════════════════════════════════════════════════

def require_hub_key(authorization: str | None = Header(default=None)):
    key = settings.evhub_events_key
    if not key:
        raise HTTPException(403, "PARKING_EVHUB_EVENTS_KEY тохируулаагүй")
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(403, "Bearer түлхүүр шаардлагатай")
    if not hmac.compare_digest(authorization.split(" ", 1)[1].strip(), key):
        raise HTTPException(403, "түлхүүр буруу")


def _sync_state(db: Session) -> AppSetting:
    row = db.get(AppSetting, "evhub_sync")
    if not row:
        row = AppSetting(key="evhub_sync", value={"last_event_id": 0})
        db.add(row)
        db.flush()
    return row


@router.post("/api/integration/evhub/events")
async def evhub_events(body: dict, db: Session = Depends(get_db),
                       _=Depends(require_hub_key)):
    """Hub-ийн үйл явдлын урсгал (at-least-once). Давхардлыг event id-ээр
    шүүнэ; боловсруулагчид өөрсдөө ч idempotent (§6.5)."""
    ev_id = int(body.get("id") or 0)
    kind = str(body.get("kind") or "")
    payload = body.get("payload") or {}
    state = _sync_state(db)
    last = int((state.value or {}).get("last_event_id") or 0)
    # Давхардлын шүүлт: зөвхөн СҮҮЛИЙН цонхонд (100k) — hub DB дахин
    # суулгагдаж id 1-ээс эхэлсэн ч core гацахгүй; хуучин давхардал дахин
    # ирвэл боловсруулагчид өөрсдөө idempotent тул аюулгүй (§6.5).
    if ev_id and last - 100_000 < ev_id <= last:
        return {"ok": True, "duplicate": True}
    if kind == "ev.tx.started":
        await ev_billing.on_tx_started(db, payload)
    elif kind == "ev.meter":
        await ev_billing.on_meter(db, payload)
    elif kind == "ev.tx.stopped":
        await ev_billing.on_tx_stopped(db, payload)
    elif kind in ("ev.status", "ev.boot", "ev.offline"):
        pass  # амьд төлвийг hub-аас шууд асуудаг — энд бүртгэл шаардлагагүй
    else:
        log.warning("evhub: үл мэдэх event kind=%s", kind)
    if ev_id:
        # Зөвхөн урагшаа хөдөлнө; hub дахин суулгагдсан (id буцаж багассан)
        # үед шинэ цувааг дагана
        new_last = ev_id if ev_id > last or ev_id <= last - 100_000 else last
        state.value = {**(state.value or {}), "last_event_id": new_last}
        state.updated_at = datetime.utcnow()
    db.commit()
    return {"ok": True}


@router.post("/api/integration/evhub/authorize")
def evhub_authorize(body: dict, db: Session = Depends(get_db),
                    _=Depends(require_hub_key)):
    """Authorize (§6.1/6): зөвхөн БИДНИЙ үүсгэсэн, хүлээгдэж буй/идэвхтэй
    session-ы id_tag зөвшөөрөгдөнө — урьдчилсан төлбөрт загвар."""
    id_tag = str(body.get("id_tag") or "")
    s = (db.query(ChargeSession)
         .filter(ChargeSession.id_tag == id_tag,
                 ChargeSession.status.in_(("PENDING_START", "RUNNING")))
         .first())
    return {"accepted": bool(s)}


# ═══════════════════════════════════════════════════════════════════════════
# НИЙТИЙН (жолооч) — §8
# ═══════════════════════════════════════════════════════════════════════════

def _throttle_public(request: Request, name: str, limit: int = 30):
    ip = request.client.host if request.client else "?"
    if throttle(f"ev:{name}:{ip}", limit=limit):
        raise HTTPException(429, "Хэт олон хүсэлт — түр хүлээнэ үү")


def _parse_key(key: str) -> tuple[str, int]:
    """QR түлхүүр: 'A7K2' (connector 1) эсвэл 'A7K2-2' (connector 2)."""
    if "-" in key:
        base, _, conn = key.rpartition("-")
        if conn.isdigit():
            return base, int(conn)
    return key, 1


def _charger_by_key(db: Session, key: str) -> tuple[EvCharger, int]:
    base, connector_id = _parse_key(key)
    c = (db.query(EvCharger)
         .filter(EvCharger.charger_key == base, EvCharger.is_active.is_(True))
         .first())
    if not c:
        raise HTTPException(404, "Цэнэглэгч олдсонгүй")
    return c, connector_id


@router.get("/api/public/ev/{key}")
async def public_ev_info(key: str, request: Request, db: Session = Depends(get_db)):
    """§6.1/2 — төлөв, ₮/кВт.ц, залгаастай эсэх."""
    _throttle_public(request, "info")
    charger, connector_id = _charger_by_key(db, key)
    plan = ev_billing.default_plan(db, charger)
    price = ev_billing.price_per_wh_at(plan)
    try:
        conn = await ev_hub.connector_status(charger.cp_id, connector_id)
    except ev_hub.HubError:
        conn = {"status": "Unknown", "online": False}
    return {
        "name": charger.name or charger.cp_id,
        "site": charger.site.name if charger.site else "",
        "connector_id": connector_id,
        "online": conn.get("online", False),
        "status": conn.get("status"),
        "plugged": conn.get("status") in ev_billing.STARTABLE_STATUSES,
        "busy": bool(conn.get("active_tx_id")),
        "price_per_kwh": float(price * 1000),
        "min_amount": float(plan.min_amount or 0),
        "max_amount": float(plan.max_amount or 0),
    }


@router.post("/api/public/ev/{key}/lookup")
def public_ev_lookup(key: str, body: dict, request: Request,
                     db: Session = Depends(get_db)):
    """§6.1/3 — дугаар+утас → данс (олдоно/шинээр). Үлдэгдэл буцаана."""
    _throttle_public(request, "lookup", limit=20)
    charger, _ = _charger_by_key(db, key)
    plate = normalize_plate(str(body.get("plate") or ""))
    phone = wallet_svc.normalize_phone(str(body.get("phone") or ""))
    if not plate or len(plate) < 4:
        raise HTTPException(422, "Машины дугаараа зөв оруулна уу")
    if len(phone) < 8:
        raise HTTPException(422, "Утасны дугаараа зөв оруулна уу")
    tenant_id = charger.site.tenant_id if charger.site else None
    w = wallet_svc.get_or_create(db, tenant_id, plate, phone)
    db.commit()
    return {"plate": w.plate_number, "phone": _mask_phone(w.phone),
            "balance": float(w.balance or 0), "wallet_token": w.public_token}


@router.post("/api/public/ev/{key}/start")
async def public_ev_start(key: str, body: dict, request: Request,
                          db: Session = Depends(get_db)):
    """§6.1/5 — физик шалгалт → hold → RemoteStart."""
    _throttle_public(request, "start", limit=10)
    charger, connector_id = _charger_by_key(db, key)
    plate = str(body.get("plate") or "")
    phone = str(body.get("phone") or "")
    try:
        amount = float(body.get("amount") or 0)
    except (TypeError, ValueError):
        raise HTTPException(422, "Дүн буруу")
    try:
        session = await ev_billing.start_charge(
            db, charger, connector_id, plate, phone, amount)
    except wallet_svc.InsufficientBalance as e:
        raise HTTPException(402, str(e))
    except (ev_billing.EvError, wallet_svc.WalletError) as e:
        raise HTTPException(409, str(e))
    return {"session_token": session.public_token, "wh_limit": session.wh_limit,
            "authorized_amount": float(session.authorized_amount)}


def _session_by_token(db: Session, token: str) -> ChargeSession:
    s = (db.query(ChargeSession)
         .filter(ChargeSession.public_token == token).first())
    if not s:
        raise HTTPException(404, "Session олдсонгүй")
    return s


@router.get("/api/public/ev/session/{token}")
def public_ev_session(token: str, request: Request, db: Session = Depends(get_db)):
    """§6.1 — амьд явц (Wh, ₮, SOC, үлдсэн)."""
    _throttle_public(request, "session", limit=120)
    s = _session_by_token(db, token)
    price = ev_billing.D(str(s.price_per_wh))
    energy = int(s.energy_wh if s.energy_wh is not None else s.last_energy_wh or 0)
    spent = float(ev_billing.energy_amount_for(energy, price))
    w = db.get(Wallet, s.wallet_id)
    return {
        "status": s.status, "energy_wh": energy,
        "wh_limit": s.wh_limit, "spent": min(spent, float(s.authorized_amount)),
        "authorized_amount": float(s.authorized_amount),
        "soc": float(s.last_soc) if s.last_soc is not None else None,
        "max_power_w": float(s.max_power_w) if s.max_power_w is not None else None,
        "total_amount": float(s.total_amount) if s.total_amount is not None else None,
        "stop_reason": s.stop_reason,
        "balance": float(w.balance or 0) if w else None,
        "started_at": s.started_at.isoformat() if s.started_at else None,
        "stopped_at": s.stopped_at.isoformat() if s.stopped_at else None,
    }


@router.post("/api/public/ev/session/{token}/stop")
async def public_ev_stop(token: str, request: Request, db: Session = Depends(get_db)):
    """Жолооч гараар зогсоох."""
    _throttle_public(request, "stop", limit=10)
    s = _session_by_token(db, token)
    if s.status != "RUNNING" or not s.ocpp_tx_id:
        raise HTTPException(409, f"Зогсоох боломжгүй төлөв: {s.status}")
    charger = db.get(EvCharger, s.charger_id)
    try:
        await ev_hub.remote_stop(charger.cp_id, int(s.ocpp_tx_id))
    except ev_hub.HubError as e:
        raise HTTPException(502, f"Команд илгээгдсэнгүй: {e}")
    return {"ok": True}


# ═══════════════════════════════════════════════════════════════════════════
# АДМИН — §8
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/api/admin/ev/chargers")
async def admin_chargers(db: Session = Depends(get_db),
                         user: User = Depends(require("settings", "devices"))):
    """Бүртгэл (core) + амьд төлөв (hub) нэгтгэсэн жагсаалт."""
    rows = db.query(EvCharger).order_by(EvCharger.cp_id).all()
    live: dict[str, dict] = {}
    hub_error = None
    try:
        for h in await ev_hub.list_chargers():
            live[h["cp_id"]] = h
    except ev_hub.HubError as e:
        hub_error = str(e)
    out = []
    for c in rows:
        h = live.get(c.cp_id, {})
        out.append({
            "id": c.id, "cp_id": c.cp_id, "name": c.name,
            "charger_key": c.charger_key, "site_id": c.site_id,
            "site_name": c.site.name if c.site else "",
            "connector_count": c.connector_count,
            "price_plan_id": c.price_plan_id, "is_active": c.is_active,
            "online": h.get("online", False), "hub_status": h.get("status"),
            "vendor": h.get("vendor"), "model": h.get("model"),
            "fw_version": h.get("fw_version"),
            "last_heartbeat_at": h.get("last_heartbeat_at"),
            "connectors": h.get("connectors", []),
        })
    # hub дээр байгаа ч core-д бүртгэгдээгүй (шинэ) цэнэглэгчид
    known = {c.cp_id for c in rows}
    unregistered = [h for k, h in live.items() if k not in known]
    return {"chargers": out, "unregistered": unregistered, "hub_error": hub_error}


@router.post("/api/admin/ev/chargers", status_code=201)
async def admin_charger_create(body: dict, db: Session = Depends(get_db),
                               user: User = Depends(require("settings"))):
    cp_id = str(body.get("cp_id") or "").strip()
    site_id = body.get("site_id")
    if not cp_id or not site_id:
        raise HTTPException(422, "cp_id, site_id шаардлагатай")
    if not db.get(ParkingSite, site_id):
        raise HTTPException(404, "Зогсоол олдсонгүй")
    if db.query(EvCharger).filter(EvCharger.cp_id == cp_id).first():
        raise HTTPException(409, "Энэ cp_id бүртгэлтэй байна")
    # Таамаглагдашгүй богино QR түлхүүр (§7.1)
    key = None
    for _ in range(20):
        cand = secrets.token_urlsafe(3).replace("-", "A").replace("_", "B")[:4].upper()
        if not db.query(EvCharger).filter(EvCharger.charger_key == cand).first():
            key = cand
            break
    if not key:
        raise HTTPException(500, "charger_key үүсгэж чадсангүй")
    c = EvCharger(site_id=site_id, cp_id=cp_id, name=str(body.get("name") or cp_id),
                  charger_key=key,
                  connector_count=int(body.get("connector_count") or 2),
                  price_plan_id=body.get("price_plan_id"))
    db.add(c)
    _audit(db, user.username, "EV_CHARGER_CREATE", cp_id, {"key": key})
    db.commit()
    # Hub талд идэвхжүүлэлт + нууц үг (өгсөн бол) — best-effort
    hub_note = None
    upd = {"status": "ACTIVE", "core_ref": c.id, "name": c.name}
    if body.get("auth_password"):
        upd["auth_password"] = str(body["auth_password"])
    try:
        await ev_hub.update_charger(cp_id, upd)
    except ev_hub.HubError as e:
        hub_note = f"hub идэвхжүүлэлт хийгдсэнгүй: {e}"
    return {"id": c.id, "charger_key": key, "hub_note": hub_note}


@router.put("/api/admin/ev/chargers/{charger_id}")
async def admin_charger_update(charger_id: str, body: dict,
                               db: Session = Depends(get_db),
                               user: User = Depends(require("settings"))):
    c = db.get(EvCharger, charger_id)
    if not c:
        raise HTTPException(404, "Цэнэглэгч олдсонгүй")
    for f in ("name", "connector_count", "price_plan_id", "is_active", "site_id"):
        if f in body:
            setattr(c, f, body[f])
    _audit(db, user.username, "EV_CHARGER_UPDATE", c.cp_id, body)
    db.commit()
    if body.get("auth_password") or "is_active" in body:
        upd = {"status": "ACTIVE" if c.is_active else "DISABLED"}
        if body.get("auth_password"):
            upd["auth_password"] = str(body["auth_password"])
        try:
            await ev_hub.update_charger(c.cp_id, upd)
        except ev_hub.HubError as e:
            return {"ok": True, "hub_note": str(e)}
    return {"ok": True}


@router.post("/api/admin/ev/chargers/{charger_id}/command")
async def admin_charger_command(charger_id: str, body: dict,
                                db: Session = Depends(get_db),
                                user: User = Depends(require("settings", "devices"))):
    """Алсын команд: reset | unlock | stop-tx | config | raw (§8)."""
    c = db.get(EvCharger, charger_id)
    if not c:
        raise HTTPException(404, "Цэнэглэгч олдсонгүй")
    kind = str(body.get("command") or "")
    try:
        if kind == "reset":
            cmd_id = await ev_hub.send_command(
                c.cp_id, "Reset", {"type": body.get("type") or "Soft"},
                requested_by=user.username)
        elif kind == "unlock":
            cmd_id = await ev_hub.send_command(
                c.cp_id, "UnlockConnector",
                {"connectorId": int(body.get("connector_id") or 1)},
                requested_by=user.username)
        elif kind == "stop-tx":
            cmd_id = await ev_hub.remote_stop(c.cp_id, int(body.get("ocpp_tx_id")))
        elif kind == "config":
            cmd_id = await ev_hub.send_command(
                c.cp_id, "ChangeConfiguration",
                {"key": str(body.get("key")), "value": str(body.get("value"))},
                requested_by=user.username)
        else:
            raise HTTPException(422, "command: reset|unlock|stop-tx|config")
    except ev_hub.HubError as e:
        raise HTTPException(502, str(e))
    _audit(db, user.username, "EV_COMMAND", c.cp_id, {"command": kind})
    db.commit()
    return {"command_id": cmd_id}


@router.get("/api/admin/ev/sessions")
def admin_ev_sessions(status: str | None = None, plate: str | None = None,
                      limit: int = 100, db: Session = Depends(get_db),
                      user: User = Depends(require("reports", "cashier"))):
    q = db.query(ChargeSession).order_by(ChargeSession.created_at.desc())
    if status:
        q = q.filter(ChargeSession.status == status)
    if plate:
        q = q.filter(ChargeSession.plate_number == normalize_plate(plate))
    rows = q.limit(min(limit, 500)).all()
    return [{
        **to_dict(s),
        "charger_name": s.charger.name if s.charger else "",
        "cp_id": s.charger.cp_id if s.charger else "",
    } for s in rows]


@router.get("/api/admin/ev/price-plans")
def list_price_plans(db: Session = Depends(get_db),
                     user: User = Depends(require("settings", "tariffs"))):
    return [to_dict(p) for p in
            db.query(EvPricePlan).order_by(EvPricePlan.created_at).all()]


@router.post("/api/admin/ev/price-plans", status_code=201)
def create_price_plan(body: dict, db: Session = Depends(get_db),
                      user: User = Depends(require("settings", "tariffs"))):
    p = EvPricePlan(
        name=str(body.get("name") or "Тариф"),
        price_per_wh=body.get("price_per_wh") or 1,
        night_price_per_wh=body.get("night_price_per_wh"),
        night_from=body.get("night_from"), night_to=body.get("night_to"),
        min_amount=body.get("min_amount") or 1000,
        max_amount=body.get("max_amount") or 200000,
        idle_grace_min=body.get("idle_grace_min") or 10,
        idle_fee_per_min=body.get("idle_fee_per_min") or 0,
        parking_exempt_mode=body.get("parking_exempt_mode") or "NONE",
        parking_exempt_cap_min=body.get("parking_exempt_cap_min") or 120,
        tenant_id=body.get("tenant_id"), site_id=body.get("site_id"))
    db.add(p)
    _audit(db, user.username, "EV_PLAN_CREATE", p.name, body)
    db.commit()
    return to_dict(p)


@router.put("/api/admin/ev/price-plans/{plan_id}")
def update_price_plan(plan_id: str, body: dict, db: Session = Depends(get_db),
                      user: User = Depends(require("settings", "tariffs"))):
    p = db.get(EvPricePlan, plan_id)
    if not p:
        raise HTTPException(404, "Тариф олдсонгүй")
    for f in ("name", "price_per_wh", "night_price_per_wh", "night_from",
              "night_to", "min_amount", "max_amount", "idle_grace_min",
              "idle_fee_per_min", "parking_exempt_mode",
              "parking_exempt_cap_min", "is_active"):
        if f in body:
            setattr(p, f, body[f])
    _audit(db, user.username, "EV_PLAN_UPDATE", plan_id, body)
    db.commit()
    return to_dict(p)
