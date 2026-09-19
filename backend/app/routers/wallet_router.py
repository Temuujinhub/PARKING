"""Данс (wallet) — админ/кассын удирдлага + нийтийн (жолооч) хуудас (§8).

  /api/admin/wallets/*        — хайлт, гар засвар, бэлнээр буцаах (audit-той)
  /api/public/wallet/*        — үлдэгдэл, түүх, QPay-ээр цэнэглэх

QPay цэнэглэлт: Payment(kind=WALLET_TOPUP, session_id=NULL) — §5.1.
Данс цэнэглэхэд e-Barimt ҮҮСГЭХГҮЙ: баримт нь үйлчилгээ (цэнэглэлт/зогсоол)
бодитоор ХЭРЭГЛЭГДЭХ үед бодит дүнгээр гарна (§Шат 4).
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..auth import require
from ..services.resource_scope import tenant_filter, enforce_tenant
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
    from ..services.qpay_recheck import request_verification, finish_verification
    stamp = request_verification(db, payment)
    if not payment.provider_invoice_id:
        raise HTTPException(503, "Нэхэмжлэл хадгалагдаж байна; callback хадгалсан")
    await _verify_and_credit(db, payment)
    finish_verification(db, payment.id, stamp, completed=True)
    return "SUCCESS"


@router.get("/api/public/wallet/{token}")
def public_wallet(token: str, request: Request, db: Session = Depends(get_db)):
    """Үлдэгдэл + сүүлийн 20 хөдөлгөөн (§8)."""
    _throttle(request, "info", limit=60)
    w = _wallet_by_token(db, token)
    moves = (db.query(WalletLedger)
             .filter(WalletLedger.wallet_id == w.id)
             .order_by(WalletLedger.created_at.desc()).limit(20).all())
    from ..services.wallet_topup import ACTIVE, invoice_response
    active = (db.query(Payment).filter(Payment.wallet_id == w.id, Payment.kind == "WALLET_TOPUP",
        Payment.status.in_(ACTIVE)).order_by(Payment.created_at, Payment.id).first())
    return {"pending_topup": invoice_response(active) if active else None,
            "plate": w.plate_number, "balance": float(w.balance or 0),
            "status": w.status, "ledger": [_ledger_dict(r) for r in moves]}


@router.post("/api/public/wallet/{token}/topup")
async def public_wallet_topup(token: str, body: dict, request: Request,
                              db: Session = Depends(get_db)):
    """QPay нэхэмжлэх үүсгэх (§6.1/4b). Payment(kind=WALLET_TOPUP)."""
    _throttle(request, "topup", limit=10)
    w = _wallet_by_token(db, token)
    if w.status != "ACTIVE":
        raise HTTPException(409, "Данс идэвхгүй байна")
    from ..services.wallet_topup import create_topup
    return await create_topup(db, w.id, body.get("amount"), body.get("request_key"))


# Keep the established import points for callers and concurrency regressions.
from ..services.wallet_topup import (credit_if_paid as _credit_if_paid,
                                     verify_and_credit as _verify_and_credit)


@router.post("/api/public/wallet/{token}/topup/{payment_id}/check")
async def wallet_topup_check(token: str, payment_id: str, request: Request,
                             db: Session = Depends(get_db)):
    """Жолоочийн хуудасны поллинг (webhook хоцорсон/алдагдсан үед)."""
    _throttle(request, "check", limit=60)
    w = _wallet_by_token(db, token)
    payment = db.get(Payment, payment_id)
    if not payment or payment.wallet_id != w.id or payment.kind != "WALLET_TOPUP":
        raise HTTPException(404, "Төлбөр олдсонгүй")
    paid = payment.status == "PAID"
    db.refresh(w)
    return {"paid": paid, "status": payment.status, "balance": float(w.balance or 0)}


# ═══════════════════════════════════════════════════════════════════════════
# АДМИН / КАСС
# ═══════════════════════════════════════════════════════════════════════════

def _admin_wallet(db, user, wallet_id):
    w = db.get(Wallet, wallet_id)
    if not w:
        raise HTTPException(404, "Данс олдсонгүй")
    enforce_tenant(db, user, w.tenant_id)
    return w


@router.get("/api/admin/wallets")
def admin_wallets(q: str = "", limit: int = 50, db: Session = Depends(get_db),
                  user: User = Depends(require("cashier", "reports"))):
    """Хайлт: дугаар эсвэл утас (§8)."""
    query = db.query(Wallet).filter(tenant_filter(db, user, Wallet.tenant_id)).order_by(Wallet.updated_at.desc())
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
    w = _admin_wallet(db, user, wallet_id)
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
    _admin_wallet(db, user, wallet_id)
    direction = str(body.get("direction") or "").upper()
    _admin_wallet(db, user, wallet_id)
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
    _admin_wallet(db, user, wallet_id)
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
    w = _admin_wallet(db, user, wallet_id)
    if not w:
        raise HTTPException(404, "Данс олдсонгүй")
    w.status = "BLOCKED" if body.get("blocked", True) else "ACTIVE"
    _audit(db, user.username, "WALLET_BLOCK", wallet_id, {"status": w.status})
    db.commit()
    return {"status": w.status}
