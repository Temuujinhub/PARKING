"""Durable wallet invoice attempts. Provider uncertainty never means unpaid."""
import logging
import secrets
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..config import settings
from ..models import AuditLog, Payment, Wallet
from . import qpay, wallet as wallets
from .payment_validation import claim_reference, money

log = logging.getLogger("parking.wallet_topup")
ACTIVE = ("CREATING", "PENDING", "UNKNOWN", "REVIEW")


def expire_creating(db, now=None):
    """A crashed invoice creator leaves uncertainty, never permission to repeat."""
    cutoff = (now or datetime.utcnow()) - timedelta(minutes=10)
    rows = (db.query(Payment).enable_eagerloads(False).filter(Payment.kind == "WALLET_TOPUP",
        Payment.provider == "QPAY", Payment.status == "CREATING", Payment.created_at < cutoff)
        .order_by(Payment.created_at).with_for_update(skip_locked=True).limit(100).all())
    for p in rows:
        p.status = "UNKNOWN"
        db.add(AuditLog(username="system", action="WALLET_TOPUP_INTERRUPTED", entity="payment",
            entity_id=p.id, detail={"replayed": False}))
    db.commit()
    return len(rows)


def invoice_response(payment):
    payload = payment.raw_payload or {}
    return {"payment_id": payment.id, "status": payment.status,
            "amount": float(payment.amount), "qr_text": payment.qr_text,
            "qr_image": payload.get("invoice_qr_image"),
            "deep_link": payment.deep_link, "urls": payload.get("invoice_urls", [])}


def _existing(payment, amount):
    if money(payment.amount) != amount:
        raise HTTPException(409, "Өмнөх цэнэглэлтийн дүн өөр байна. Тэр оролдлогыг эхлээд шалгана уу")
    if payment.status == "PAID" or (payment.status == "PENDING" and payment.provider_invoice_id):
        return invoice_response(payment)
    raise HTTPException(409, "Өмнөх цэнэглэлтийн үр дүн тодорхойгүй байна. "
                        "Дахин төлөхгүй; санхүүгээр нэхэмжлэлийг тулгуулна уу")


async def create_topup(db: Session, wallet_id: str, amount_value, request_key=None):
    amount = money(amount_value)
    if amount != amount.to_integral_value():
        raise HTTPException(422, "Цэнэглэх дүн бүхэл төгрөг байна")
    if not Decimal(str(settings.ev_min_topup)) <= amount <= Decimal("1000000"):
        raise HTTPException(422, f"Цэнэглэх дүн {settings.ev_min_topup}–1,000,000₮ байна")
    if request_key is not None and (not isinstance(request_key, str) or
            not 1 <= len(request_key) <= 100 or not request_key.isascii() or
            any(not (c.isalnum() or c in "-_") for c in request_key)):
        raise HTTPException(422, "Цэнэглэлтийн оролдлогын түлхүүр буруу")
    # Serialize invoice creation on this wallet, but release before any HTTP.
    w = wallets.lock_wallet(db, wallet_id)
    if w.status != "ACTIVE":
        raise HTTPException(409, "Данс идэвхгүй байна")
    query = db.query(Payment).filter(Payment.wallet_id == w.id,
                                      Payment.kind == "WALLET_TOPUP", Payment.provider == "QPAY")
    if request_key:
        previous = query.filter(Payment.raw_payload["request_key"].as_string() == request_key).first()
        if previous:
            result = _existing(previous, amount)
            db.commit()
            return result
    previous = query.filter(Payment.status.in_(ACTIVE)).order_by(Payment.created_at, Payment.id).first()
    if previous:
        if request_key and (previous.raw_payload or {}).get("request_key") != request_key:
            raise HTTPException(409, "Өмнөх цэнэглэлтийг хуудаснаас үргэлжлүүлнэ үү; шинэ оролдлого үүсгээгүй")
        result = _existing(previous, amount)
        db.commit()
        return result
    hook = secrets.token_urlsafe(24)
    plate, receiver = w.plate_number, w.phone or "terminal"
    payment = Payment(kind="WALLET_TOPUP", wallet_id=w.id, provider="QPAY",
        payment_method="QR", source="QR", amount=amount, vat_amount=0, status="CREATING",
        sender_invoice_no=qpay.fit_bytes(f"WT-{plate}", qpay.SENDER_INVOICE_NO_MAX-17)
                          + "-" + secrets.token_hex(8).upper(),
        raw_payload={"webhook_token": hook, "request_key": request_key})
    db.add(payment)
    db.flush()
    pid, sender = payment.id, payment.sender_invoice_no
    db.commit()
    callback = f"{settings.public_base_url}/api/public/wallet/webhook?payment_id={pid}&token={hook}"
    lines = [{"line_description": f"Данс цэнэглэх — {plate}", "line_quantity": "1.00",
              "line_unit_price": f"{amount:.2f}", "amount": float(amount), "taxes": []}]
    try:
        inv = await qpay.create_invoice(sender, f"EasyParking данс цэнэглэх {plate}",
                                        receiver, callback, lines)
        if not isinstance(inv.get("invoice_id"), str) or not inv["invoice_id"].strip():
            raise ValueError("Missing invoice identity")
    except Exception as exc:
        db.rollback()
        p = db.query(Payment).enable_eagerloads(False).filter_by(id=pid).with_for_update().one()
        if p.status == "CREATING":
            p.status = "UNKNOWN"
        db.add(AuditLog(username="system", action="WALLET_TOPUP_UNKNOWN", entity="payment",
                        entity_id=pid, detail={"error_type": type(exc).__name__}))
        db.commit()
        log.warning("wallet invoice result unknown: payment=%s type=%s", pid, type(exc).__name__)
        raise HTTPException(502, "QPay цэнэглэлтийн хариу тодорхойгүй. Дахин төлөхгүй; "
                            "санхүүгээр өмнөх нэхэмжлэлийг тулгуулна уу") from exc
    p = (db.query(Payment).enable_eagerloads(False).filter_by(id=pid)
         .populate_existing().with_for_update().one())
    if p.status in ("CREATING", "UNKNOWN"):
        p.status = "PENDING"
    p.provider_invoice_id = inv["invoice_id"]
    p.qr_text, p.deep_link = inv.get("qr_text"), inv.get("deep_link")
    p.raw_payload = {**(p.raw_payload or {}), "invoice_urls": inv.get("urls", [])[:40],
                     "invoice_qr_image": inv.get("qr_image")}
    result = invoice_response(p)
    db.commit()
    return result


def credit_if_paid(db: Session, payment: Payment) -> bool:
    db.flush()
    p = (db.query(Payment).enable_eagerloads(False).filter_by(id=payment.id)
         .populate_existing().with_for_update().first())
    if not p or p.kind != "WALLET_TOPUP" or not p.wallet_id:
        return False
    if p.status == "PAID":
        return True
    wallets.credit_topup(db, p.wallet_id, p.amount, p.id, note="QPay цэнэглэлт")
    p.status, p.paid_at = "PAID", datetime.utcnow()
    db.commit()
    return True


async def verify_and_credit(db: Session, payment: Payment) -> bool:
    if payment.kind != "WALLET_TOPUP" or payment.provider != "QPAY":
        return False
    if payment.status == "PAID":
        return True
    pid, invoice = payment.id, payment.provider_invoice_id
    if not invoice:
        return False
    # No wallet/payment row locks survive the provider round trip.
    db.commit()
    chk = await qpay.check_payment(invoice)
    if not chk.get("paid"):
        return False
    p = (db.query(Payment).enable_eagerloads(False).filter_by(id=pid)
         .populate_existing().with_for_update().one())
    if p.status == "PAID":
        return True
    try:
        received = Decimal(str(chk.get("paid_amount", 0)))
    except (InvalidOperation, ValueError):
        received = Decimal("NaN")
    reference = chk.get("payment_id")
    if (not received.is_finite() or received < Decimal(str(p.amount)) or
            not isinstance(reference, str) or not reference.strip() or len(reference.strip()) > 120
            or any(ord(c) < 32 for c in reference)):
        p.status = "REVIEW"
        db.commit()
        return False
    claim_reference(db, p, "qpay:" + qpay.global_account().username, reference.strip())
    if received > Decimal(str(p.amount)):
        p.raw_payload = {**(p.raw_payload or {}), "overpaid_amount": str(received - p.amount)}
    try:
        return credit_if_paid(db, p)
    except wallets.WalletError:
        # Money exists at the provider; a blocked wallet must retain the evidence.
        p.status = "REVIEW"
        db.commit()
        return False
