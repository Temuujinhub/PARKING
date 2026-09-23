"""Түншийн интеграцийн API (/api/v1) — wallet/апп-уудын B2B холболт.

tokI, Easy Wallet зэрэг гадаад төлбөрийн систем энэ API-аар:
  1. GET  /api/v1/sites                     — зогсоолууд + сул байрны тоо
  2. GET  /api/v1/sessions?plate=           — дугаараар хайж төлөх дүнг авах
  3. POST /api/v1/payments                  — төлбөрийн intent үүсгэх (PENDING)
  4. POST /api/v1/payments/{id}/confirm     — өөрийн талд амжилттай болмогц баталгаажуулах
                                              → систем PAID болгож ХААЛТ НЭЭНЭ
  5. GET  /api/v1/payments/{id}             — төлөв шалгах

Нэвтрэлт: X-API-Key толгой. Түлхүүр ХОЁР эх үүсвэртэй:
  1. partner_keys хүснэгт (Тохиргоо → Холболт → Гадаад API-гаас удирдана,
     restart шаардлагагүй; DB-д зөвхөн SHA-256 hash хадгалагдана)
  2. .env-ийн PARKING_PARTNER_KEYS — хуучин fallback, одоогийн партнерууд тасрахгүй
Түншийн нэр Payment.provider болж тайланд ялгарна.
"""
import hashlib
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models import AuditLog, ParkingSession, ParkingSite, PartnerKey, Payment
from ..ratelimit import throttle
from ..session_logic import amount_due, normalize_plate, session_fee_info

router = APIRouter(prefix="/api/v1", tags=["integration"])
log = __import__("logging").getLogger("parking.integration")


def receipt_info(db: Session, payment: Payment) -> dict | None:
    """Төлбөрийн e-Barimt мэдээлэл — түнш (Easy Wallet) аппдаа жолоочид харуулна.
    ДДТД, сугалаа, дүн/НӨАТ, төлөв, QR (түр санах ойд байвал). Баримт үүсээгүй/
    амжилтгүй бол status FAILED + шалтгаан — түнш дараа GET-ээр дахин асууж болно."""
    from ..models import VatReceipt
    from ..services import ebarimt as _eb
    r = (db.query(VatReceipt).filter(VatReceipt.payment_id == payment.id)
         .order_by(VatReceipt.created_at.desc()).first())
    if r is None:
        return None
    return {"ddtd": r.ebarimt_id, "lottery": r.lottery_code,
            "amount": float(r.amount or 0), "vat_amount": float(r.vat_amount or 0),
            "status": r.status, "provider": r.provider,
            "error": (r.receipt_url or "")[:200] if r.status != "SENT" else None,
            "qr_data": _eb.get_cached_qr(payment.id),
            "created_at": r.created_at.isoformat() if r.created_at else None}


def _payment_event(db: Session, payment: Payment, event: str) -> dict:
    from .payments_router import settlement_info
    sess = db.get(ParkingSession, payment.session_id) if payment.session_id else None
    site = db.get(ParkingSite, sess.site_id) if sess else None
    return {"event": event, "payment_id": payment.id, "status": payment.status,
            "transaction_id": payment.provider_payment_id,
            "amount": float(payment.amount), "vat_amount": float(payment.vat_amount or 0),
            "paid_at": payment.paid_at.isoformat() if payment.paid_at else None,
            "plate": sess.plate_number if sess else None,
            "site_code": site.site_code if site else None,
            "site_name": site.name if site else None,
            "ebarimt": receipt_info(db, payment), **settlement_info(db, payment)}


async def push_partner_webhook(url: str, payload: dict, partner: str,
                               timeout: float = 6.0) -> dict:
    """Түншийн сервер рүү POST. Буцаана: {ok, status_code, body, error} — аудит/туршилтад.
    Алдаа хэзээ ч дуудагчийг унагаахгүй (төлбөр/хаалт аль хэдийн хийгдсэн)."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.post(url, json=payload,
                             headers={"Content-Type": "application/json",
                                      "X-Parking-Partner": partner,
                                      "X-Parking-Event": payload.get("event", "")})
        ok = 200 <= r.status_code < 300
        return {"ok": ok, "status_code": r.status_code, "body": (r.text or "")[:300], "error": None}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "status_code": None, "body": None, "error": str(e)[:200]}


def _webhook_url_for(db: Session, partner: str) -> str | None:
    key_id = getattr(partner, "key_id", None)
    q = db.query(PartnerKey).filter(PartnerKey.name == partner, PartnerKey.is_active.is_(True))
    if key_id:
        q = q.filter(PartnerKey.id == key_id)
    else:
        q = q.filter(PartnerKey.site_id.is_(None))
    rows = q.limit(2).all()
    return (rows[0].webhook_url or "").strip() or None if len(rows) == 1 else None


OPEN_STATUSES = ("OPEN", "AWAITING_PAYMENT", "PAID")


class PartnerAuth(str):
    """Танигдсан партнер. str-ээс удамшдаг тул Payment.provider,
    f"partner:{...}" зэрэг одоогийн бүх хэрэглээ өөрчлөгдөхгүй; дээр нь
    scopes/site_id хязгаарлалтыг авч явна (.env түлхүүрт хязгаарлалтгүй)."""
    scopes: str = "read,pay"
    site_id: str | None = None
    key_id: str | None = None

    def can_pay(self) -> bool:
        return "pay" in {scope.strip() for scope in self.scopes.split(",")}


def _auth_fail(request: Request):
    # Брут-форсоос хамгаалах: буруу түлхүүрийн оролдлогыг IP-ээр хязгаарлана
    ip = request.client.host if request.client else "?"
    if throttle(f"partner-bad:{ip}", limit=20, window=60):
        raise HTTPException(429, "Хэт олон буруу оролдлого")
    raise HTTPException(401, "API түлхүүр буруу")


def require_partner(request: Request, x_api_key: str = Header(default=""),
                    db: Session = Depends(get_db)) -> PartnerAuth:
    """X-API-Key-ээр түншийг таньж нэрийг нь буцаана (Payment.provider болно).
    Эхлээд DB (partner_keys, hash тулгана), дараа нь .env fallback."""
    key = x_api_key or ""
    if key:
        h = hashlib.sha256(key.encode()).hexdigest()
        row = (db.query(PartnerKey)
               .filter(PartnerKey.key_hash == h, PartnerKey.is_active.is_(True)).first())
        if row:
            # last_used_at-ыг минут тутам л шинэчилнэ — хүсэлт бүрд UPDATE хийхгүй
            now = datetime.utcnow()
            if not row.last_used_at or now - row.last_used_at > timedelta(seconds=60):
                row.last_used_at = now
                db.commit()
            auth = PartnerAuth(row.name)
            auth.scopes = row.scopes or "read"
            auth.site_id = row.site_id
            auth.key_id = row.id
            return auth
    partners = settings.partner_map()
    if not partners and not db.query(PartnerKey.id).first():
        raise HTTPException(503, "Интеграцийн API идэвхгүй (түлхүүр бүртгэгдээгүй)")
    name = partners.get(key)
    if not name:
        _auth_fail(request)
    return PartnerAuth(name)   # .env түлхүүр — бүрэн эрхтэй (хуучин зан төлөв)


def _require_pay(partner: PartnerAuth):
    if str(partner).strip().upper() in {"QPAY", "POS", "CASH", "TRANSFER", "WALLET"}:
        raise HTTPException(403, "Түншийн нэр дотоод төлбөрийн хэрэгслийн нэртэй давхцсан; админ засна уу")
    if not partner.can_pay():
        raise HTTPException(403, "Энэ түлхүүр зөвхөн лавлах эрхтэй (төлбөрийн эрхгүй)")


def _check_site_scope(partner: PartnerAuth, site_id: str | None):
    if partner.site_id and partner.site_id != site_id:
        raise HTTPException(403, "Энэ түлхүүр өөр зогсоолын мэдээлэлд хандах эрхгүй")


def _check_payment_scope(db, partner, payment):
    if str(payment.provider).upper() in {"QPAY", "POS", "CASH", "TRANSFER", "WALLET"}:
        raise HTTPException(403, "Дотоод төлбөрийн хэрэгслийг түншийн API-аар удирдахгүй")
    if payment.provider != partner:
        raise HTTPException(403, "Энэ төлбөр өөр түншийнх")
    if payment.partner_key_id and payment.partner_key_id != partner.key_id:
        raise HTTPException(403, "Энэ төлбөр өөр түншийн түлхүүрт харьяалагдана")
    session = db.get(ParkingSession, payment.session_id) if payment.session_id else None
    if not session:
        raise HTTPException(404, "Зогсолтын бүртгэл олдсонгүй")
    _check_site_scope(partner, session.site_id)


def _session_payload(db: Session, s: ParkingSession) -> dict:
    """Түншид өгөх session-ий бүрэн мэдээлэл: хугацаа, тариф, төлөх үлдэгдэл."""
    fee = session_fee_info(db, s)
    due = amount_due(db, s, fee)
    return {
        "session_id": s.id,
        "plate_number": s.plate_number,
        "site_code": s.site.site_code if s.site else None,
        "site_name": s.site.name if s.site else None,
        "entry_time": s.entry_time.isoformat() if s.entry_time else None,
        "duration_minutes": fee["duration_minutes"],
        "base_fee": fee["base_fee"],
        "vat_amount": fee["vat_amount"],
        "discount_amount": fee["discount_amount"],
        "total_fee": fee["total_fee"],
        "amount_due": due,          # ← wallet ЭНЭ дүнг нэхэмжилнэ
        "is_free": fee["is_free"],
        "status": s.status,
        "paid": s.status == "PAID" and due <= 0,
    }


@router.get("/sites")
def list_sites(db: Session = Depends(get_db), partner: PartnerAuth = Depends(require_partner)):
    """Идэвхтэй зогсоолууд + багтаамж, эзэлсэн, сул байрны тоо."""
    occupied_by_site = dict(
        db.query(ParkingSession.site_id, func.count())
        .filter(ParkingSession.status.in_(OPEN_STATUSES))
        .group_by(ParkingSession.site_id).all())
    out = []
    q = db.query(ParkingSite).filter(ParkingSite.is_active.is_(True))
    if partner.site_id:   # зогсоолоор хязгаарлагдсан түлхүүр зөвхөн өөрийнхөө зогсоолыг харна
        q = q.filter(ParkingSite.id == partner.site_id)
    for site in q.all():
        occupied = occupied_by_site.get(site.id, 0)
        out.append({
            "site_code": site.site_code, "name": site.name,
            "zone_code": site.zone_code, "address": site.address or "",
            "capacity": site.capacity, "occupied": occupied,
            # capacity=0 → дүүргэлт хянадаггүй зогсоол (сул тоо null)
            "free": max(0, site.capacity - occupied) if site.capacity else None,
        })
    return {"sites": out}


@router.get("/sessions")
def find_sessions(plate: str, site_code: str = "",
                  db: Session = Depends(get_db), partner: PartnerAuth = Depends(require_partner)):
    """Дугаараар нээлттэй session хайна. site_code өгөхгүй бол БҮХ зогсоолоос —
    жолооч аль зогсоолд байгааг wallet апп мэдэхгүй байж болно."""
    plate = normalize_plate(plate)
    if len(plate) < 4:
        raise HTTPException(400, "Дугаар дутуу байна (4+ тэмдэгт)")
    q = (db.query(ParkingSession)
         .filter(ParkingSession.plate_number == plate,
                 ParkingSession.status.in_(OPEN_STATUSES)))
    if partner.site_id:   # түлхүүрийн зогсоолын хязгаар — бусад зогсоолын зогсолт харагдахгүй
        q = q.filter(ParkingSession.site_id == partner.site_id)
    if site_code:
        site = db.query(ParkingSite).filter(ParkingSite.site_code == site_code).first()
        if not site:
            raise HTTPException(404, "Зогсоол олдсонгүй")
        _check_site_scope(partner, site.id)
        q = q.filter(ParkingSession.site_id == site.id)
    sessions = q.order_by(ParkingSession.entry_time.desc()).limit(5).all()
    return {"sessions": [_session_payload(db, s) for s in sessions]}


@router.get("/recent-exits")
def recent_exits_all_sites(minutes: int = 5, site_code: str = "",
                           include_completed: bool = False,
                           db: Session = Depends(get_db),
                           partner: PartnerAuth = Depends(require_partner)):
    """БҮХ зогсоолын сүүлийн гарцын урсгал (Easy Wallet-ийн хүсэлтээр, 2026-09-01).

    Гарах камерт сүүлийн `minutes` (1..60, default 5) минутад уншигдаад ТӨЛБӨР
    ХҮЛЭЭЖ буй машинууд — wallet апп хэрэглэгчийнхээ машиныг гарц дээр ирмэгц
    таньж, төлбөрийн санал шууд харуулахад зориулав. Мөр бүрд `amount_due`
    (нэхэмжлэх дүн) явна; төлөх бол ердийн POST /payments → /confirm урсгал.

    include_completed=true — мөн хугацаанд ГАРЧ ДУУССАН (төлөгдсөн/үнэгүй)
    машинуудыг `completed` жагсаалтаар нэмж өгнө (тайлан/нотолгооны хэрэглээ).

    Түлхүүр зогсоолоор хязгаарлагдсан бол зөвхөн тэр зогсоолынх; site_code-оор
    нэг зогсоол руу шүүж болно. 30с тутам polling-д зориулагдсан (rate limit-д
    багтана); мөрүүд exit уншилтын цагаар шинээс хуучин руу эрэмбэлэгдэнэ.
    """
    minutes = max(1, min(int(minutes or 5), 60))
    since = datetime.utcnow() - timedelta(minutes=minutes)
    site_ids = None
    if site_code:
        site = db.query(ParkingSite).filter(ParkingSite.site_code == site_code).first()
        if not site:
            raise HTTPException(404, "Зогсоол олдсонгүй")
        _check_site_scope(partner, site.id)
        site_ids = [site.id]
    elif partner.site_id:
        site_ids = [partner.site_id]

    def _rows(statuses):
        q = (db.query(ParkingSession)
             .filter(ParkingSession.status.in_(statuses),
                     # Гарах уншилттай (exit камерт танигдсан) машинууд л —
                     # exit_time нь гарах оролдлогын цаг; хуучин бичлэгт updated_at
                     ParkingSession.exit_device_id.isnot(None),
                     func.coalesce(ParkingSession.exit_time,
                                   ParkingSession.updated_at) >= since))
        if site_ids:
            q = q.filter(ParkingSession.site_id.in_(site_ids))
        return (q.order_by(func.coalesce(ParkingSession.exit_time,
                                         ParkingSession.updated_at).desc())
                .limit(200).all())

    def _row(s: ParkingSession) -> dict:
        exit_at = s.exit_time or s.updated_at
        return _session_payload(db, s) | {
            "exit_read_at": exit_at.isoformat() if exit_at else None,
            "exit_time": s.exit_time.isoformat() if s.exit_time else None,
        }

    out = {"minutes": minutes,
           "waiting": [_row(s) for s in _rows(["AWAITING_PAYMENT"])]}
    if include_completed:
        out["completed"] = [_row(s) for s in _rows(["PAID", "CLOSED", "FREE"])]
    return out


@router.post("/payments")
def create_payment_intent(body: dict, db: Session = Depends(get_db),
                          partner: PartnerAuth = Depends(require_partner)):
    """Төлбөрийн intent үүсгэнэ. body: {session_id}.
    Буцаах amount-ыг wallet өөрийн талд хэрэглэгчээс нэхэмжилж, амжилттай
    болмогц /payments/{id}/confirm-ыг дуудна. Идэвхтэй PENDING intent
    байвал (дүн зөрөөгүй бол) шинээр үүсгэлгүй түүнийгээ буцаана."""
    from .payments_router import _create_payment
    from ..services.checkout import lock_session
    _require_pay(partner)
    session = lock_session(db, body.get("session_id", ""))
    _check_site_scope(partner, session.site_id)
    if session.status not in ("OPEN", "AWAITING_PAYMENT"):
        raise HTTPException(400, f"Session төлөв буруу: {session.status}")

    existing = (db.query(Payment)
                .filter(Payment.session_id == session.id, Payment.provider == partner,
                        Payment.status == "PENDING").first())
    if existing:
        _check_payment_scope(db, partner, existing)
        current_due = amount_due(db, session, session_fee_info(db, session))
        if abs(float(existing.amount) - current_due) > 0.01:
            raise HTTPException(409, "Үнэ өөрчлөгдсөн. Өмнөх wallet оролдлогыг баталгаажуулах эсвэл цуцална уу")
        return {"payment_id": existing.id, "amount": float(existing.amount),
                "vat_amount": float(existing.vat_amount), "status": "PENDING"}

    payment = _create_payment(db, session, partner, "WALLET")
    payment.partner_key_id = partner.key_id
    payment.source = "WALLET"
    db.add(AuditLog(username=f"partner:{partner}", action="WALLET_INTENT", entity="payment",
                    entity_id=payment.id,
                    detail={"plate": session.plate_number, "amount": float(payment.amount)}))
    db.commit()
    return {"payment_id": payment.id, "amount": float(payment.amount),
            "vat_amount": float(payment.vat_amount), "status": "PENDING",
            "plate_number": session.plate_number}


@router.post("/payments/{payment_id}/cancel")
def cancel_payment_intent(payment_id: str, body: dict, db: Session = Depends(get_db),
                          partner: PartnerAuth = Depends(require_partner)):
    """Release an unpaid intent only after the partner confirms no debit occurred."""
    from .payments_router import _lock_payment
    _require_pay(partner)
    payment = db.get(Payment, payment_id)
    if not payment:
        raise HTTPException(404, "Payment олдсонгүй")
    _check_payment_scope(db, partner, payment)
    if body.get("outcome") != "NOT_CHARGED":
        raise HTTPException(422, "Wallet талаас мөнгө татаагүйг NOT_CHARGED гэж батална уу")
    payment = _lock_payment(db, payment.id)
    if payment is None:
        raise HTTPException(409, "Төлбөр боловсруулагдаж байна")
    if payment.status == "PAID":
        raise HTTPException(409, "Төлөгдсөн төлбөрийг intent цуцлалтаар өөрчлөхгүй")
    payment.status = "CANCELLED"
    db.add(AuditLog(username=f"partner:{partner}", action="WALLET_INTENT_CANCELLED",
                    entity="payment", entity_id=payment.id,
                    detail={"outcome": "NOT_CHARGED"}))
    db.commit()
    return {"payment_id": payment.id, "status": payment.status}


@router.post("/payments/{payment_id}/confirm")
async def confirm_payment(payment_id: str, body: dict, db: Session = Depends(get_db),
                          partner: PartnerAuth = Depends(require_partner)):
    """Wallet өөрийн талд төлбөрийг амжилттай авсныг баталгаажуулна.
    body: {transaction_id, amount}. Дүн зөрвөл татгалзана (буруу дүнгээр хаалт
    нээгдэхгүй). Idempotent — давхар дуудахад алдаа өгөхгүй PAID буцаана."""
    from .payments_router import _finalize_paid, _lock_payment, payment_outcome
    _require_pay(partner)
    payment = db.get(Payment, payment_id)
    if not payment:
        raise HTTPException(404, "Payment олдсонгүй")
    _check_payment_scope(db, partner, payment)
    from ..services.payment_validation import money, transaction_reference, claim_reference
    paid_amount = money(body.get("amount"))
    reference = transaction_reference(body.get("transaction_id"))
    # Мөрийг түгжинэ — wallet-ийн давхар confirm/retry зэрэг ирвэл нэг нь л finalize хийнэ
    payment = _lock_payment(db, payment.id)
    if payment is None:
        raise HTTPException(409, "Төлбөр боловсруулагдаж байна — дахин оролдоно уу")
    if payment.status == "PAID":
        if payment.provider_payment_id != reference or paid_amount != money(payment.amount):
            raise HTTPException(409, "Өмнөх баталгаажуулалтын дугаар эсвэл дүн зөрлөө")
        return {"status": "PAID", "payment_id": payment.id, **payment_outcome(db, payment)}
    if payment.status not in ("PENDING", "CANCELLED", "UNKNOWN", "REVIEW"):
        raise HTTPException(400, f"Төлбөрийн төлөв буруу: {payment.status}")
    if paid_amount != money(payment.amount):
        raise HTTPException(400, f"Дүн зөрүүтэй: систем {float(payment.amount)}₮ хүлээж байна")

    claim_reference(db, payment, partner.key_id or f"legacy:{partner}", reference)
    hook = _webhook_url_for(db, partner)
    await _finalize_paid(db, payment, raw={"partner": partner, **{
        k: v for k, v in body.items() if k in ("transaction_id", "amount", "wallet_user")}},
        partner_notification=(hook, partner) if hook else None)
    db.add(AuditLog(username=f"partner:{partner}", action="WALLET_PAID", entity="payment",
                    entity_id=payment.id, detail={"transaction_id": payment.provider_payment_id,
                                                  "amount": float(payment.amount)}))
    db.commit()
    # The webhook intent was committed with settlement, before opening a gate.
    return {"status": "PAID", "payment_id": payment.id, "paid_at":
            payment.paid_at.isoformat() if payment.paid_at else datetime.utcnow().isoformat(),
            "ebarimt": receipt_info(db, payment), **payment_outcome(db, payment)}


@router.get("/payments/{payment_id}")
def payment_status(payment_id: str, db: Session = Depends(get_db),
                   partner: PartnerAuth = Depends(require_partner)):
    from .payments_router import payment_outcome
    payment = db.get(Payment, payment_id)
    if not payment or payment.provider != partner:
        raise HTTPException(404, "Payment олдсонгүй")
    _check_payment_scope(db, partner, payment)
    return {"payment_id": payment.id, "status": payment.status,
            "amount": float(payment.amount),
            "paid_at": payment.paid_at.isoformat() if payment.paid_at else None,
            # e-Barimt (ДДТД, сугалаа, QR) — PAID болсны дараа; FAILED бол шалтгаан
            "ebarimt": receipt_info(db, payment), **payment_outcome(db, payment)}
