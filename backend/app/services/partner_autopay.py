"""Wallet-ийн автомат төлбөр гарах хаалтан дээр (2026-11-03).

УРСГАЛ:
  1. Түнш (MonPay, SocialPay, Easy Wallet …) хэрэглэгчийнхээ машиныг бүртгүүлэхдээ
     `autoPay: true` гэж (эсвэл дараа нь /api/v1/vehicles/autopay-аар) асаана — «энэ машины
     зогсоолын төлбөрийг манай wallet-аас автоматаар төлнө» гэсэн хэрэглэгчийн сонголт.
  2. Машин гарах камерт ЯГ дугаараараа уншигдаж төлбөр хүлээх (AWAITING_PAYMENT) болмогц
     `request_on_exit` (handle_exit-ээс, commit-ийн дараа): түншийн нэрээр PENDING Payment
     (intent — `POST /api/v1/payments`-тай ИЖИЛ `_create_payment`, өр багтана) үүсгэж, хүсэлтийн
     мөрийг (partner_autopay_requests) бичнэ. HTTP хаалт/LPR-ийн замд ХИЙГДЭХГҮЙ.
  3. `schedule_delivery` ард нь түншийн төлбөрийн webhook URL (partner_keys.webhook_url —
     payment.paid / receipt.ready-тэй ижил URL) руу `payment.requested` POST хийнэ.
  4. Түнш хэрэглэгчээс мөнгө татаад ердийн `POST /api/v1/payments/{id}/confirm` дуудна →
     `_finalize_paid` → сешн PAID, машин гарцан дээр байвал ХААЛТ ШУУД нээгдэнэ, e-Barimt.
     Татаж чадаагүй бол `POST /api/v1/payments/{id}/cancel {"outcome":"NOT_CHARGED"}`.
  5. `partner_autopay_timeout_sec` (60с) дотор хариу ирэхгүй бол intent CANCELLED болж
     (QR/касс дахин нээгдэнэ) түнш рүү `payment.request_expired` явна. Хожуу ирсэн confirm-ыг
     ердийнхөөр хүлээж авна (CANCELLED intent-ийг confirm хийж болдог хуучин дүрэм) — жолооч
     энэ хооронд өөрөөр төлсөн бол илүү төлөлт PAYMENT_OVERPAYMENT-аар буцаалтад тэмдэглэгдэнэ.

ДҮРЭМ (зөв логик):
  • Зөвхөн ЯГ дугаар: тааж тохоосон (OCR/тайралт) сешн, уншсан дугаар ≠ сешний дугаар бол
    хүсэлт явахгүй — гарцад байгаа машин тэр хэрэглэгчийнх биш байж болно.
  • Нэг айлчлалд нэг л оролдлого: татгалзсан/хугацаа дууссан бол дахин нэхэхгүй (жолооч
    QR/кассаар). Өмнөх нь PAID (төлсөн ч grace хэтэрсэн) бол шинэ зөрүүд дахин явна.
  • Өөр идэвхтэй төлбөрийн оролдлого (QPay нэхэмжлэл, POS …) байвал явахгүй — давхар татахгүй.
  • Нэг машиныг хэд хэдэн түнш/хэрэглэгч autoPay-тай бүртгүүлсэн бол ХАМГИЙН СҮҮЛД
    сонгосон (auto_pay_updated_at) нэг рүү л явна — хоёр wallet-аас зэрэг татахгүй.
  • Түлхүүр: идэвхтэй, `pay` + `vehicles` эрхтэй, webhook URL-тэй, зогсоол түлхүүрийн хүрээнд.
  • Илгээлт `partner_autopay_enabled` БА `partner_vehicle_notify` хоёулаа асаалттай үед л
    (прод dump-аас сэргээсэн TEST жинхэнэ wallet-аас мөнгө татуулахгүй). Хугацаа дуусгалт
    (intent-ийг суллах) ҮРГЭЛЖ ажиллана — эс бөгөөс сешн PENDING intent-д гацна.
"""
import asyncio
import json
import logging
import re
import uuid
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import and_, func, or_

from ..config import settings
from ..database import SessionLocal
from ..models import (AuditLog, ParkingSession, PartnerAutopayRequest, PartnerKey, PartnerVehicle,
                      Payment)
from ..session_logic import normalize_plate

log = logging.getLogger("parking.partner_autopay")

R = PartnerAutopayRequest
EVENT_REQUESTED = "payment.requested"
EVENT_EXPIRED = "payment.request_expired"
OPEN = ("PENDING", "SENDING", "SENT")          # түнш хариу өгөх боломжтой
TERMINAL = ("PAID", "DECLINED", "EXPIRED", "FAILED", "REVIEW")
INTERNAL_NAMES = {"QPAY", "POS", "CASH", "TRANSFER", "WALLET"}
RETRY_DELAYS = (2, 4, 8, 15, 20)               # хүргэлтийн давталт (сек) — хугацаан дотор
NOTICE_DELAYS = (15, 60, 300, 900)             # хугацаа дууссаны мэдэгдлийн давталт (сек)
LEASE = timedelta(seconds=20)
_REASON_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,60}$", re.ASCII)
_TASKS: set = set()                            # ард ажиллаж буй илгээлт (GC-ээс хамгаална)


def enabled() -> bool:
    return bool(settings.partner_autopay_enabled and settings.partner_vehicle_notify)


def timeout_seconds() -> int:
    return max(15, min(600, int(settings.partner_autopay_timeout_sec or 60)))


def _scopes(key) -> set:
    return {s.strip() for s in (getattr(key, "scopes", "") or "").split(",") if s.strip()}


def key_problem(key) -> str | None:
    """autoPay ажиллахад түлхүүр бэлэн эсэх: None = бэлэн, эс бөгөөс шалтгаан (монголоор)."""
    if key is None or not key.is_active:
        return "Түлхүүр идэвхгүй"
    if str(key.name or "").strip().upper() in INTERNAL_NAMES:
        return "Түншийн нэр дотоод төлбөрийн хэрэгслийн нэртэй давхцсан"
    missing = [s for s in ("pay", "vehicles") if s not in _scopes(key)]
    if missing:
        return f"Түлхүүрт {', '.join(missing)} эрх олгоогүй"
    if not (key.webhook_url or "").strip():
        return "Түлхүүрийн төлбөрийн webhook URL тохируулаагүй (Тохиргоо → Холболт → Гадаад API)"
    return None


def parse_reason(value) -> str | None:
    """cancel-ийн `reason` (заавал биш): INSUFFICIENT_FUNDS, USER_DECLINED г.м машинаар уншигдах код."""
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not _REASON_RE.match(value.strip()):
        raise HTTPException(422, "reason нь 1–60 тэмдэгт (латин үсэг, цифр, _ . : -) код байна")
    return value.strip().upper()


def iso(dt: datetime | None) -> str | None:
    """/api/v1-ийн бусад цагтай ИЖИЛ хэлбэр: бүсгүй ISO, UTC."""
    return dt.isoformat() if dt else None


# ─────────────────────────── Сонголт ───────────────────────────

def choose(db, session: ParkingSession):
    """(PartnerKey, PartnerVehicle) — энэ сешнд автомат төлбөр хийх түнш, эсвэл None.
    Хамгийн сүүлд autoPay сонгосон бүртгэл түрүүлнэ; түлхүүр бэлэн биш/хүрээнээс гадуур бол дараагийнх."""
    from .partner_scope import allows, key_site_ids
    rows = (db.query(PartnerVehicle, PartnerKey)
            .join(PartnerKey, PartnerKey.id == PartnerVehicle.partner_key_id)
            .filter(PartnerVehicle.plate_number == session.plate_number,
                    PartnerVehicle.deleted_at.is_(None), PartnerVehicle.auto_pay.is_(True),
                    PartnerKey.is_active.is_(True))
            .order_by(func.coalesce(PartnerVehicle.auto_pay_updated_at,
                                    PartnerVehicle.created_at).desc(),
                      PartnerVehicle.id)
            .limit(20).all())
    for vehicle, key in rows:
        if key_problem(key) is not None:
            continue
        if not allows(key_site_ids(db, key), session.site_id):
            continue
        return key, vehicle
    return None


# ─────────────────────────── Хүсэлт үүсгэх (handle_exit) ───────────────────────────

def build_payload(db, req, payment: Payment, s: ParkingSession, key: PartnerKey) -> dict:
    from ..routers.integration_router import payment_breakdown
    site = s.site
    snap = payment.fee_snapshot or {}
    base = (settings.public_base_url or "").rstrip("/")
    return {
        "event": EVENT_REQUESTED, "event_id": req.id, "request_id": req.id,
        "payment_id": payment.id, "session_id": s.id,
        "plate": s.plate_number, "user_id": req.external_user_id,
        "site_code": site.site_code if site else None, "site_name": site.name if site else None,
        "entry_time": iso(s.entry_time),
        "exit_read_at": iso(s.last_exit_seen_at or s.exit_time),
        "duration_minutes": snap.get("duration_minutes", s.duration_minutes),
        "amount": float(payment.amount), "vat_amount": float(payment.vat_amount or 0),
        "currency": "MNT",
        **payment_breakdown(db, payment),          # parking_amount, debt_amount, debt_count, debts[]
        "expires_at": iso(req.expires_at), "expires_in_seconds": timeout_seconds(),
        "confirm_url": f"{base}/api/v1/payments/{payment.id}/confirm",
        "cancel_url": f"{base}/api/v1/payments/{payment.id}/cancel",
    }


def request_on_exit(db, session: ParkingSession, read_plate: str, *, fuzzy: bool = False,
                    now: datetime | None = None) -> dict | None:
    """Гарах камерын AWAITING_PAYMENT commit-ийн ДАРАА дуудагдана. Зөвхөн DB (HTTP-гүй):
    intent + хүсэлтийн мөр үүсгэж commit хийнэ. Үүсгэвэл {request_id, payment_id, partner,
    amount}, эс бөгөөс None. Түгжээ/төлөвийн зөрчилд HTTPException (дуудагч барина)."""
    if not enabled() or fuzzy or session is None:
        return None
    plate = session.plate_number
    if not plate or normalize_plate(read_plate or "") != plate:
        return None
    if session.status != "AWAITING_PAYMENT":
        return None
    picked = choose(db, session)
    if picked is None:
        return None
    key, vehicle = picked
    from .checkout import active_attempts, lock_session
    from ..routers.payments_router import _create_payment
    s = lock_session(db, session.id)          # NOWAIT — зэрэгцээ төлбөртэй бол 409
    if s.status != "AWAITING_PAYMENT":
        db.rollback()
        return None
    prior = [st for (st,) in db.query(R.status).filter(R.session_id == s.id).all()]
    if any(st != "PAID" for st in prior):
        db.rollback()                         # нэг айлчлалд нэг оролдлого
        return None
    if active_attempts(db, s.id):
        db.rollback()                         # QPay нэхэмжлэл/POS г.м аль хэдийн явж байна
        return None
    payment = _create_payment(db, s, key.name, "WALLET", include_debts=True)
    payment.partner_key_id = key.id
    payment.source = "WALLET"
    payment.raw_payload = {**(payment.raw_payload or {}), "autopay": True,
                           "external_user_id": vehicle.external_user_id}
    now = now or datetime.utcnow()
    req = R(id=str(uuid.uuid4()), payment_id=payment.id, partner_key_id=key.id, session_id=s.id,
            plate_number=plate, external_user_id=vehicle.external_user_id, status="PENDING",
            attempts=0, next_attempt_at=now, notice_attempts=0,
            expires_at=now + timedelta(seconds=timeout_seconds()), payload={})
    req.payload = build_payload(db, req, payment, s, key)
    db.add(req)
    db.add(AuditLog(username=f"partner:{key.name}"[:60], action="WALLET_AUTOPAY_REQUEST",
                    entity="payment", entity_id=payment.id,
                    detail={"plate": plate, "amount": float(payment.amount),
                            "debt_amount": req.payload.get("debt_amount", 0),
                            "user_id": vehicle.external_user_id, "request_id": req.id,
                            "expires_at": iso(req.expires_at)}))
    db.commit()
    log.info("[autopay] %s → %s: %s₮ (payment=%s)", plate, key.name, float(payment.amount), payment.id)
    return {"request_id": req.id, "payment_id": payment.id, "partner": key.name,
            "amount": float(payment.amount), "expires_at": iso(req.expires_at)}


# ─────────────────────────── Төлөв (confirm / cancel) ───────────────────────────

def _lock_by_payment(db, payment_id: str):
    return (db.query(R).filter(R.payment_id == payment_id).populate_existing()
            .with_for_update().first())


def mark_paid(db, payment_id: str, now: datetime | None = None) -> None:
    """confirm амжилттай — commit-ийг дуудагч хийнэ. Хожуу confirm (хугацаа дууссаны дараа)
    ирвэл хүргэгдээгүй мэдэгдлийг цуцална (төлөгдсөн тул «бүү тат» гэх утгагүй)."""
    r = _lock_by_payment(db, payment_id)
    if r is None or r.status == "PAID":
        return
    r.status, r.resolved_at = "PAID", now or datetime.utcnow()
    r.lease_token = r.lease_until = None
    if r.notice_status in ("PENDING", "SENDING"):
        r.notice_status = None


def mark_declined(db, payment_id: str, reason: str | None, now: datetime | None = None) -> None:
    """Түнш NOT_CHARGED-аар цуцалсан — commit-ийг дуудагч хийнэ."""
    r = _lock_by_payment(db, payment_id)
    if r is None or r.status not in OPEN:
        return
    r.status, r.resolved_at = "DECLINED", now or datetime.utcnow()
    r.decline_reason = reason
    r.lease_token = r.lease_until = None


def status_for(db, payment_id: str) -> dict | None:
    """GET /payments/{id} ба bill-д: энэ intent автомат төлбөрийн хүсэлт эсэх + төлөв."""
    r = db.query(R).filter(R.payment_id == payment_id).first()
    if r is None:
        return None
    return {"request_id": r.id, "status": r.status, "expires_at": iso(r.expires_at),
            "delivered_at": iso(r.delivered_at), "decline_reason": r.decline_reason}


def _sync_from_payment(r, pay, now) -> None:
    """Payment-ийн төлөв хүсэлтээс түрүүлсэн (confirm/cancel/тулгалт) — хүсэлтийг тааруулна."""
    st = pay.status if pay is not None else "CANCELLED"
    if st == "PAID":
        r.status = "PAID"
    elif st in ("CANCELLED", "FAILED"):
        r.status = "DECLINED" if r.status in OPEN else r.status
    else:                                   # UNKNOWN/REVIEW — санхүүгийн тулгалтад
        r.status = "REVIEW"
    r.resolved_at = r.resolved_at or now
    r.lease_token = r.lease_until = None


# ─────────────────────────── Хүргэлт ───────────────────────────

def claim_due(db, now: datetime | None = None, limit: int = 20,
              request_id: str | None = None) -> list[dict]:
    """Илгээх хүсэлтүүдийг SENDING болгож lease-тэй буцаана (commit хийнэ)."""
    now = now or datetime.utcnow()
    q = db.query(R).filter(or_(and_(R.status == "PENDING", R.next_attempt_at <= now),
                               and_(R.status == "SENDING", R.lease_until <= now)),
                           R.expires_at > now)
    if request_id:
        q = q.filter(R.id == request_id)
    rows = q.order_by(R.next_attempt_at, R.created_at).limit(limit).with_for_update(skip_locked=True).all()
    out = []
    for r in rows:
        pay = db.get(Payment, r.payment_id)
        if pay is None or pay.status != "PENDING":
            _sync_from_payment(r, pay, now)
            continue
        key = db.get(PartnerKey, r.partner_key_id)
        url = (key.webhook_url or "").strip() if key is not None else ""
        if key is None or not key.is_active or not url:
            # Түр тохиргооны өөрчлөлт — хугацаа дуустал хүлээнэ (дараа нь FAILED)
            r.status, r.next_attempt_at = "PENDING", now + timedelta(seconds=RETRY_DELAYS[0])
            r.last_error = "Түлхүүр идэвхгүй эсвэл webhook URL хоосон"
            continue
        r.status = "SENDING"
        r.attempts = (r.attempts or 0) + 1
        r.lease_token = str(uuid.uuid4())
        r.lease_until = now + LEASE
        out.append({"id": r.id, "token": r.lease_token, "url": url, "payload": r.payload,
                    "event": EVENT_REQUESTED, "event_id": r.id, "partner": key.name})
    db.commit()
    return out


async def post(url: str, payload: dict, event_id: str, event: str, partner: str,
               transport=None) -> dict:
    """Түншийн webhook руу POST. Амжилт = 2xx (payment.paid-тэй ижил гэрээ); body-д
    {"Success": false} ирвэл амжилтгүй. Хэзээ ч raise хийхгүй, URL/нууц логлохгүй."""
    import httpx
    if not enabled():
        return {"ok": False, "status_code": None, "error": "DISABLED"}
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/json",
               "X-Parking-Partner": str(partner), "X-Parking-Event": event,
               "X-Parking-Event-Id": event_id, "Idempotency-Key": event_id}
    t = httpx.Timeout(float(settings.partner_autopay_http_timeout), connect=3.0)
    try:
        if transport is not None:                    # тест: MockTransport
            async with httpx.AsyncClient(transport=transport, follow_redirects=False) as client:
                resp = await client.post(url, content=body, headers=headers, timeout=t)
        else:
            from .partner_vehicles import _http_client   # нэг хуваалцсан клиент (SSL context нэг удаа)
            client, sem = await _http_client()
            async with sem:
                resp = await client.post(url, content=body, headers=headers, timeout=t)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "status_code": None, "error": type(exc).__name__}
    ok = 200 <= resp.status_code < 300
    error = None if ok else f"HTTP {resp.status_code}"
    if ok:
        try:
            data = resp.json()
        except ValueError:
            data = None
        if isinstance(data, dict):
            success = data.get("Success", data.get("success"))
            if success is False or (isinstance(success, str) and success.strip().lower() == "false"):
                ok, error = False, "Success=false"
    return {"ok": ok, "status_code": resp.status_code, "error": error}


def _retry_delay(attempts: int) -> int:
    return RETRY_DELAYS[min(max(attempts, 1), len(RETRY_DELAYS)) - 1]


def record_result(db, request_id: str, token: str, res: dict, now: datetime | None = None) -> str | None:
    now = now or datetime.utcnow()
    r = db.query(R).filter(R.id == request_id).populate_existing().with_for_update().first()
    if r is None or r.lease_token != token or r.status != "SENDING":
        db.rollback()             # confirm/cancel/хугацаа дуусгалт түрүүлсэн
        return None
    r.lease_token = r.lease_until = None
    r.last_http_status = res.get("status_code")
    if res.get("ok"):
        r.status, r.delivered_at, r.last_error = "SENT", now, None
    else:
        r.last_error = (res.get("error") or "алдаа")[:500]
        r.status = "PENDING"
        r.next_attempt_at = min(now + timedelta(seconds=_retry_delay(r.attempts)), r.expires_at)
        log.warning("[autopay] хүргэлт амжилтгүй (%d-р оролдлого): %s — %s",
                    r.attempts, r.plate_number, r.last_error)
    db.commit()
    return r.status


async def deliver_due(factory=None, limit: int = 20, request_id: str | None = None,
                      transport=None) -> int:
    """claim (thread) → HTTP зэрэг (async) → үр дүн (thread). HTTP хүлээх хооронд DB
    транзакц нээлттэй үлдэхгүй."""
    factory = factory or SessionLocal

    def _claim():
        with factory() as db:
            return claim_due(db, limit=limit, request_id=request_id)

    items = await asyncio.to_thread(_claim)
    if not items:
        return 0
    results = await asyncio.gather(*(post(it["url"], it["payload"], it["event_id"], it["event"],
                                          it["partner"], transport=transport) for it in items))

    def _record():
        with factory() as db:
            for it, res in zip(items, results):
                record_result(db, it["id"], it["token"], res)

    await asyncio.to_thread(_record)
    return len(items)


def schedule_delivery(request_id: str) -> None:
    """Хүсэлтийг ОДОО ард нь илгээнэ (handle_exit-ийг хүлээлгэхгүй). Loop байхгүй бол
    supervisor ≤2с-д илгээнэ."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(deliver_due(request_id=request_id, limit=1))
    _TASKS.add(task)

    def _done(t):
        _TASKS.discard(t)
        if not t.cancelled() and t.exception() is not None:
            log.error("[autopay] шууд илгээлт алдаатай: %r", t.exception())
    task.add_done_callback(_done)


# ─────────────────────────── Хугацаа дуусгалт ───────────────────────────

def expire_due(db, now: datetime | None = None, limit: int = 50) -> int:
    """Хугацаа дууссан хүсэлт: PENDING intent-ийг CANCELLED (QR/касс нээгдэнэ), түнш рүү
    мэдэгдэл. HTTP явж буй (lease хүчинтэй) мөрийг дараагийн удаад үлдээнэ."""
    now = now or datetime.utcnow()
    rows = (db.query(R).filter(R.status.in_(OPEN), R.expires_at <= now)
            .order_by(R.expires_at).limit(limit).with_for_update(skip_locked=True).all())
    n = 0
    for r in rows:
        if r.status == "SENDING" and r.lease_until and r.lease_until > now:
            continue
        pay = (db.query(Payment).filter(Payment.id == r.payment_id).enable_eagerloads(False)
               .populate_existing().with_for_update().first())
        if pay is None or pay.status != "PENDING":
            _sync_from_payment(r, pay, now)
            continue
        pay.status = "CANCELLED"
        pay.raw_payload = {**(pay.raw_payload or {}), "autopay_expired_at": now.isoformat()}
        r.status = "EXPIRED" if r.delivered_at else "FAILED"
        r.resolved_at = now
        r.lease_token = r.lease_until = None
        if r.attempts:            # түнш хүлээж авсан байж болзошгүй — «бүү тат» мэдэгдэл
            r.notice_status, r.notice_next_at = "PENDING", now
        db.add(AuditLog(username="system", action="WALLET_AUTOPAY_EXPIRED", entity="payment",
                        entity_id=pay.id,
                        detail={"request_id": r.id, "plate": r.plate_number,
                                "delivered": bool(r.delivered_at), "attempts": r.attempts,
                                "last_error": r.last_error}))
        n += 1
    db.commit()
    if n:
        log.info("[autopay] %d хүсэлтийн хугацаа дуусч intent цуцлагдлаа", n)
    return n


def notice_payload(r) -> dict:
    return {"event": EVENT_EXPIRED, "event_id": f"{r.id}:expired", "request_id": r.id,
            "payment_id": r.payment_id, "session_id": r.session_id, "plate": r.plate_number,
            "user_id": r.external_user_id, "status": "CANCELLED", "reason": "TIMEOUT",
            "expired_at": iso(r.resolved_at or r.expires_at)}


def claim_notices(db, now: datetime | None = None, limit: int = 20) -> list[dict]:
    now = now or datetime.utcnow()
    rows = (db.query(R).filter(R.notice_status.in_(("PENDING", "SENDING")), R.notice_next_at <= now)
            .order_by(R.notice_next_at).limit(limit).with_for_update(skip_locked=True).all())
    out = []
    for r in rows:
        pay = db.get(Payment, r.payment_id)
        key = db.get(PartnerKey, r.partner_key_id)
        url = (key.webhook_url or "").strip() if key is not None else ""
        if pay is not None and pay.status == "PAID":
            r.notice_status = None           # хожуу confirm ирсэн — мэдэгдэх шаардлагагүй
            continue
        if key is None or not key.is_active or not url:
            r.notice_status, r.last_error = "DEAD", "Мэдэгдэл: түлхүүр идэвхгүй эсвэл URL хоосон"
            continue
        r.notice_status = "SENDING"
        r.notice_attempts = (r.notice_attempts or 0) + 1
        r.lease_token = str(uuid.uuid4())
        r.notice_next_at = now + LEASE
        payload = notice_payload(r)
        out.append({"id": r.id, "token": r.lease_token, "url": url, "payload": payload,
                    "event": EVENT_EXPIRED, "event_id": payload["event_id"], "partner": key.name})
    db.commit()
    return out


def record_notice(db, request_id: str, token: str, res: dict, now: datetime | None = None) -> str | None:
    now = now or datetime.utcnow()
    r = db.query(R).filter(R.id == request_id).populate_existing().with_for_update().first()
    if r is None or r.lease_token != token or r.notice_status != "SENDING":
        db.rollback()
        return None
    r.lease_token = None
    if res.get("ok"):
        r.notice_status = "DONE"
    elif (r.notice_attempts or 0) >= len(NOTICE_DELAYS):
        r.notice_status = "DEAD"
        r.last_error = f"Мэдэгдэл: {(res.get('error') or 'алдаа')[:200]}"
    else:
        r.notice_status = "PENDING"
        r.notice_next_at = now + timedelta(seconds=NOTICE_DELAYS[r.notice_attempts - 1])
    db.commit()
    return r.notice_status


async def deliver_notices(factory=None, limit: int = 20, transport=None) -> int:
    factory = factory or SessionLocal

    def _claim():
        with factory() as db:
            return claim_notices(db, limit=limit)

    items = await asyncio.to_thread(_claim)
    if not items:
        return 0
    results = await asyncio.gather(*(post(it["url"], it["payload"], it["event_id"], it["event"],
                                          it["partner"], transport=transport) for it in items))

    def _record():
        with factory() as db:
            for it, res in zip(items, results):
                record_notice(db, it["id"], it["token"], res)

    await asyncio.to_thread(_record)
    return len(items)


async def supervisor():
    """main.py-ийн background task. Хугацаа дуусгалт ҮРГЭЛЖ (intent гацахгүй); хүсэлтгүй үед
    индекстэй 2 хөнгөн query."""
    await asyncio.sleep(10)

    def _expire():
        with SessionLocal() as db:
            return expire_due(db)

    while True:
        try:
            await asyncio.to_thread(_expire)
            if enabled():
                await deliver_due()
                await deliver_notices()
        except Exception:  # noqa: BLE001
            log.exception("[autopay] мөчлөгийн алдаа")
        await asyncio.sleep(max(1, int(settings.partner_autopay_scan_sec or 2)))
