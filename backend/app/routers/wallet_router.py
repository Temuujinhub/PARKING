"""Данс (wallet) — админ/кассын удирдлага + нийтийн (жолооч) хуудас (§8).

  /api/admin/wallets/*        — хайлт, гар засвар, бэлнээр буцаах (audit-той)
  /api/public/wallet/*        — үлдэгдэл, түүх, QPay-ээр цэнэглэх

QPay цэнэглэлт: Payment(kind=WALLET_TOPUP, session_id=NULL) — §5.1.
Данс цэнэглэхэд e-Barimt ҮҮСГЭХГҮЙ: баримт нь үйлчилгээ (цэнэглэлт/зогсоол)
бодитоор ХЭРЭГЛЭГДЭХ үед бодит дүнгээр гарна (§Шат 4).
"""
import logging
import secrets
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..auth import require
from ..config import settings
from ..database import get_db
from ..models import AuditLog, Payment, User, Wallet, WalletLedger
from ..ratelimit import throttle
from ..serializers import to_dict
from ..services import qpay
from ..services import wallet as wallet_svc
from ..session_logic import normalize_plate

log = logging.getLogger("parking.wallet")
router = APIRouter(tags=["wallet"])


def _audit(db, username, action, entity_id, detail=None):
    db.add(AuditLog(username=username, action=action, entity="wallet",
                    entity_id=str(entity_id), detail=detail or {}))


def _throttle(request: Request, name: str, limit: int = 30):
    ip = request.client.host if request.client else "?"
    if throttle(f"wallet:{name}:{ip}", limit=limit):
        raise HTTPException(429, "Хэт олон хүсэлт — түр хүлээнэ үү")


def _ledger_dict(r: WalletLedger) -> dict:
    return {"direction": r.direction, "amount": float(r.amount),
            "balance_after": float(r.balance_after), "kind": r.kind,
            "note": r.note or "", "created_at": r.created_at.isoformat()}


# ═══════════════════════════════════════════════════════════════════════════
# НИЙТИЙН — /api/public/wallet/{token}
# ═══════════════════════════════════════════════════════════════════════════

def _wallet_by_token(db: Session, token: str) -> Wallet:
    w = db.query(Wallet).filter(Wallet.public_token == token).first()
    if not w:
        raise HTTPException(404, "Данс олдсонгүй")
    return w


@router.get("/api/public/wallet/{token}")
def public_wallet(token: str, request: Request, db: Session = Depends(get_db)):
    """Үлдэгдэл + сүүлийн 20 хөдөлгөөн (§8)."""
    _throttle(request, "info", limit=60)
    w = _wallet_by_token(db, token)
    moves = (db.query(WalletLedger)
             .filter(WalletLedger.wallet_id == w.id)
             .order_by(WalletLedger.created_at.desc()).limit(20).all())
    return {"plate": w.plate_number, "balance": float(w.balance or 0),
            "status": w.status, "ledger": [_ledger_dict(r) for r in moves]}


@router.post("/api/public/wallet/{token}/topup")
async def public_wallet_topup(token: str, body: dict, request: Request,
                              db: Session = Depends(get_db)):
    """QPay нэхэмжлэх үүсгэх (§6.1/4b). Payment(kind=WALLET_TOPUP)."""
    _throttle(request, "topup", limit=10)
    w = _wallet_by_token(db, token)
    if w.status != "ACTIVE":
        raise HTTPException(409, "Данс идэвхгүй байна")
    try:
        amount = int(float(body.get("amount") or 0))
    except (TypeError, ValueError):
        raise HTTPException(422, "Дүн буруу")
    if amount < settings.ev_min_topup:
        raise HTTPException(422, f"Доод дүн {settings.ev_min_topup}₮")
    if amount > 1_000_000:
        raise HTTPException(422, "Дээд дүн 1,000,000₮")
    webhook_token = secrets.token_urlsafe(24)
    payment = Payment(
        session_id=None, kind="WALLET_TOPUP", wallet_id=w.id,
        provider="QPAY", payment_method="QR", source="QR",
        sender_invoice_no=f"WT-{w.plate_number}-{secrets.token_hex(4).upper()}",
        amount=amount, vat_amount=0, status="PENDING",
        raw_payload={"webhook_token": webhook_token})
    db.add(payment)
    db.flush()
    callback = (f"{settings.public_base_url}/api/public/wallet/webhook"
                f"?payment_id={payment.id}&token={webhook_token}")
    # Данс цэнэглэлт: e-Barimt-гүй энгийн мөр (баримт хэрэглээний үед гарна)
    lines = [{"line_description": f"Данс цэнэглэх — {w.plate_number}",
              "line_quantity": "1.00", "line_unit_price": f"{amount}.00",
              "amount": amount, "taxes": []}]
    try:
        inv = await qpay.create_invoice(
            payment.sender_invoice_no,
            f"EasyParking данс цэнэглэх {w.plate_number}",
            w.phone or "terminal", callback, lines)
    except Exception as e:  # noqa: BLE001
        db.rollback()
        log.warning("wallet topup invoice алдаа: %s", e)
        raise HTTPException(502, "QPay нэхэмжлэх үүсгэж чадсангүй")
    payment.provider_invoice_id = inv.get("invoice_id")
    payment.qr_text = inv.get("qr_text")
    payment.deep_link = inv.get("deep_link")
    db.commit()
    return {"payment_id": payment.id, "amount": amount,
            "qr_text": inv.get("qr_text"), "qr_image": inv.get("qr_image"),
            "deep_link": inv.get("deep_link"), "urls": inv.get("urls", [])}


def _credit_if_paid(db: Session, payment: Payment) -> bool:
    """PENDING → PAID + данс цэнэглэх. Idempotent: мөрийн түгжээтэй,
    зөвхөн PENDING төлөвөөс шилжинэ (давхар webhook/чек хамгаалагдана)."""
    locked = (db.query(Payment).filter(Payment.id == payment.id)
              .with_for_update().first())
    if not locked or locked.status == "PAID":
        return locked is not None and locked.status == "PAID"
    locked.status = "PAID"
    locked.paid_at = datetime.utcnow()
    wallet_svc.credit_topup(db, locked.wallet_id, locked.amount, locked.id,
                            note="QPay цэнэглэлт")
    db.commit()
    log.info("wallet topup PAID: %s %s₮", locked.sender_invoice_no, locked.amount)
    return True


async def _verify_and_credit(db: Session, payment: Payment) -> bool:
    if payment.status == "PAID":
        return True
    if not payment.provider_invoice_id:
        return False
    chk = await qpay.check_payment(payment.provider_invoice_id)
    if chk.get("paid"):
        if chk.get("payment_id"):
            payment.provider_payment_id = str(chk["payment_id"])
        return _credit_if_paid(db, payment)
    return False


@router.get("/api/public/wallet/webhook")
@router.post("/api/public/wallet/webhook")
async def wallet_topup_webhook(payment_id: str = "", token: str = "",
                               db: Session = Depends(get_db)):
    """QPay callback. Мөнгө орсныг ЗААВАЛ /payment/check-ээр баталгаажуулна
    (callback нь зөвхөн дохио — итгэхгүй)."""
    payment = db.get(Payment, payment_id)
    if not payment or payment.kind != "WALLET_TOPUP":
        return "SUCCESS"  # QPay-д алдаа буцаахгүй (дахин илгээсээр байдаг)
    saved = (payment.raw_payload or {}).get("webhook_token", "")
    import hmac as _hmac
    if not (saved and token and _hmac.compare_digest(saved, token)):
        log.warning("wallet webhook token буруу: payment=%s", payment_id)
        return "SUCCESS"
    await _verify_and_credit(db, payment)
    return "SUCCESS"


@router.post("/api/public/wallet/{token}/topup/{payment_id}/check")
async def wallet_topup_check(token: str, payment_id: str, request: Request,
                             db: Session = Depends(get_db)):
    """Жолоочийн хуудасны поллинг (webhook хоцорсон/алдагдсан үед)."""
    _throttle(request, "check", limit=60)
    w = _wallet_by_token(db, token)
    payment = db.get(Payment, payment_id)
    if not payment or payment.wallet_id != w.id or payment.kind != "WALLET_TOPUP":
        raise HTTPException(404, "Төлбөр олдсонгүй")
    paid = await _verify_and_credit(db, payment)
    db.refresh(w)
    return {"paid": paid, "balance": float(w.balance or 0)}


# ═══════════════════════════════════════════════════════════════════════════
# АДМИН / КАСС
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/api/admin/wallets")
def admin_wallets(q: str = "", limit: int = 50, db: Session = Depends(get_db),
                  user: User = Depends(require("cashier", "reports"))):
    """Хайлт: дугаар эсвэл утас (§8)."""
    query = db.query(Wallet).order_by(Wallet.updated_at.desc())
    s = (q or "").strip()
    if s:
        p = normalize_plate(s)
        digits = wallet_svc.normalize_phone(s)
        conds = [Wallet.plate_number.like(f"%{p}%")] if p else []
        if digits:
            conds.append(Wallet.phone.like(f"%{digits}%"))
        if conds:
            query = query.filter(or_(*conds))
    rows = query.limit(min(limit, 200)).all()
    return [{"id": w.id, "plate": w.plate_number, "phone": w.phone,
             "name": w.name, "balance": float(w.balance or 0),
             "status": w.status, "tenant_id": w.tenant_id,
             "created_at": w.created_at.isoformat()} for w in rows]


@router.get("/api/admin/wallets/{wallet_id}")
def admin_wallet_detail(wallet_id: str, db: Session = Depends(get_db),
                        user: User = Depends(require("cashier", "reports"))):
    w = db.get(Wallet, wallet_id)
    if not w:
        raise HTTPException(404, "Данс олдсонгүй")
    moves = (db.query(WalletLedger).filter(WalletLedger.wallet_id == w.id)
             .order_by(WalletLedger.created_at.desc()).limit(100).all())
    return {**to_dict(w), "balance": float(w.balance or 0),
            "ledger": [_ledger_dict(r) for r in moves]}


@router.post("/api/admin/wallets/{wallet_id}/adjust")
def admin_wallet_adjust(wallet_id: str, body: dict, db: Session = Depends(get_db),
                        user: User = Depends(require("cashier"))):
    """Гар засвар — ЗААВАЛ тайлбартай, audit log-той (§8)."""
    direction = str(body.get("direction") or "").upper()
    amount = body.get("amount")
    note = str(body.get("note") or "").strip()
    if direction not in ("CREDIT", "DEBIT"):
        raise HTTPException(422, "direction: CREDIT|DEBIT")
    if not note:
        raise HTTPException(422, "Тайлбар заавал")
    try:
        w = wallet_svc.adjust(db, wallet_id, direction, amount, user.id, note)
    except wallet_svc.InsufficientBalance as e:
        raise HTTPException(402, str(e))
    except wallet_svc.WalletError as e:
        raise HTTPException(422, str(e))
    _audit(db, user.username, "WALLET_ADJUST", wallet_id,
           {"direction": direction, "amount": float(amount), "note": note})
    db.commit()
    return {"balance": float(w.balance)}


@router.post("/api/admin/wallets/{wallet_id}/cash-out")
def admin_wallet_cashout(wallet_id: str, body: dict, db: Session = Depends(get_db),
                         user: User = Depends(require("cashier"))):
    """Бэлнээр буцаах — оператор баталгаажуулна (§1.2, §8)."""
    amount = body.get("amount")
    note = str(body.get("note") or "")
    try:
        w = wallet_svc.cash_out(db, wallet_id, amount, user.id, note)
    except wallet_svc.InsufficientBalance as e:
        raise HTTPException(402, str(e))
    except wallet_svc.WalletError as e:
        raise HTTPException(422, str(e))
    _audit(db, user.username, "WALLET_CASH_OUT", wallet_id,
           {"amount": float(amount), "note": note})
    db.commit()
    return {"balance": float(w.balance)}


@router.post("/api/admin/wallets/{wallet_id}/block")
def admin_wallet_block(wallet_id: str, body: dict, db: Session = Depends(get_db),
                       user: User = Depends(require("cashier"))):
    w = db.get(Wallet, wallet_id)
    if not w:
        raise HTTPException(404, "Данс олдсонгүй")
    w.status = "BLOCKED" if body.get("blocked", True) else "ACTIVE"
    _audit(db, user.username, "WALLET_BLOCK", wallet_id, {"status": w.status})
    db.commit()
    return {"status": w.status}
