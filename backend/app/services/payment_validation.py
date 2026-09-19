"""Shared validation for money already confirmed by a payment instrument."""
import hashlib
import json
from decimal import Decimal, InvalidOperation

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from ..models import Payment


def money(value, *, positive=True) -> Decimal:
    try:
        if isinstance(value, bool):
            raise ValueError("boolean is not money")
        amount = Decimal(str(value))
        if (not amount.is_finite() or amount < 0 or (positive and amount == 0)
                or amount >= Decimal("10000000000")
                or amount != amount.quantize(Decimal("0.01"))):
            raise ValueError("invalid money")
        return amount
    except (InvalidOperation, TypeError, ValueError):
        raise HTTPException(422, "Төлбөрийн дүн зөв, эерэг, хоёр хүртэл орны нарийвчлалтай тоо байна")


def transaction_reference(value) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 120:
        raise HTTPException(422, "Гүйлгээний баталгааны дугаар шаардлагатай (1–120 тэмдэгт)")
    reference = value.strip()
    if any(ord(c) < 32 for c in reference):
        raise HTTPException(422, "Гүйлгээний дугаар буруу")
    return reference


def reference_key(provider: str, account: str, reference: str) -> str:
    # The namespace must be a server-validated merchant/terminal/partner identity.
    return hashlib.sha256(json.dumps([provider, account, reference],
                                    ensure_ascii=False).encode()).hexdigest()


def claim_reference(db, payment, account: str, reference: str):
    key = reference_key(payment.provider, account, reference)
    duplicate = db.query(Payment).filter(Payment.provider_tx_key == key,
                                         Payment.id != payment.id).first()
    if duplicate:
        raise HTTPException(409, "Энэ гүйлгээ өөр төлбөрт бүртгэгдсэн байна")
    payment.provider_tx_key = key
    payment.provider_payment_id = reference
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Гүйлгээ давхар баталгаажиж байна. Эхний төлөлтийг шалгана уу") from exc


def gate_result(db, payment) -> dict:
    from ..models import BarrierCommand
    q = db.query(BarrierCommand).filter(
        BarrierCommand.session_id == payment.session_id,
        BarrierCommand.command_source == "payment",
        BarrierCommand.command.in_(["open", "force_open"]))
    if payment.paid_at:
        q = q.filter(BarrierCommand.created_at >= payment.paid_at)
    command = q.order_by(BarrierCommand.created_at.desc()).first()
    status = command.status if command else "NOT_REQUESTED"
    return {"barrier_opened": status == "SUCCESS", "barrier_command_status": status,
            "barrier_command_id": command.id if command else None,
            "physical_gate_state": "UNKNOWN"}  # An ACK is not a physical sensor reading.
