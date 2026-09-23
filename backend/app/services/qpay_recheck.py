"""Durably retry failed verification of an authenticated QPay callback.

QPay's published contract prohibits periodically polling arbitrary invoices.
Only persisted callback work is eligible. Old invoice age is not an exclusion;
bounded, oldest-due scheduling prevents starvation and survives restarts.
An unresolved PENDING invoice is never evidence that the bank was unpaid.
"""
import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy import or_

from ..config import settings
from ..database import SessionLocal
from ..models import Payment

log = logging.getLogger("parking.qpay_recheck")


def request_verification(db, payment):
    payment = (db.query(Payment).enable_eagerloads(False).filter(Payment.id == payment.id)
               .populate_existing().with_for_update().one())
    if payment.provider != "QPAY" or payment.kind not in ("PARKING", "WALLET_TOPUP"):
        raise ValueError("Callback is not for a QPay payment")
    stamp = datetime.utcnow()
    payment.qpay_check_requested_at = stamp
    payment.qpay_check_attempts = 0
    payment.qpay_next_check_at = stamp + timedelta(minutes=2)
    db.commit()  # The callback survives even if verification/process fails next.
    return stamp


def finish_verification(db, payment_id, stamp, *, completed):
    payment = (db.query(Payment).enable_eagerloads(False).filter(Payment.id == payment_id,
        Payment.qpay_check_requested_at == stamp).populate_existing().with_for_update().first())
    if payment:
        if completed or payment.status == "PAID":
            payment.qpay_check_requested_at = None
        else:
            payment.qpay_next_check_at = datetime.utcnow() + timedelta(
                seconds=min(3600, max(30, settings.qpay_recheck_sec) * 2**min(payment.qpay_check_attempts, 6)))
        db.commit()


def claim_batch(db, now, limit=20):
    """Reserve a bounded batch before HTTP. Crashes release reservations in 5m.

    Half the batch is reserved for recent invoices; the rest walks the oldest
    due work, including invoices older than 24h. Persisted schedules survive
    restarts and SKIP LOCKED prevents multiple workers claiming the same row.
    """
    query = db.query(Payment).enable_eagerloads(False).filter(
        Payment.provider == "QPAY", Payment.kind.in_(("PARKING", "WALLET_TOPUP")),
        Payment.status.in_(("PENDING", "UNKNOWN", "REVIEW")),
        Payment.qpay_check_requested_at.isnot(None),
        Payment.qpay_check_attempts < settings.qpay_callback_max_attempts,
        Payment.provider_invoice_id.isnot(None), Payment.provider_invoice_id != "",
        Payment.created_at <= now - timedelta(minutes=2),
        or_(Payment.qpay_next_check_at.is_(None), Payment.qpay_next_check_at <= now))
    ordered = query.order_by(Payment.qpay_next_check_at.asc().nullsfirst(),
                             Payment.created_at, Payment.id)
    recent = (ordered.filter(Payment.created_at >= now-timedelta(hours=24))
              .with_for_update(skip_locked=True).limit(limit//2).all())
    ids = [p.id for p in recent]
    older = (ordered.filter(Payment.id.notin_(ids)).with_for_update(skip_locked=True)
             .limit(limit-len(ids)).all())
    rows = recent + older
    for payment in rows:
        payment.qpay_check_attempts += 1
        payment.qpay_last_check_at = now
        payment.qpay_next_check_at = now + timedelta(minutes=5)
    result = [p.id for p in rows]
    db.commit()
    return result



async def run_once() -> int:
    """Нэг давталт — PAID болгож сэргээсэн төлбөрийн тоог буцаана."""
    if settings.qpay_mock or not settings.qpay_recheck_sec:
        return 0
    db = SessionLocal()
    fixed = 0
    try:
        # Circular import-оос зайлсхийж энд импортолно (router нь service-үүдийг татдаг)
        from ..routers.payments_router import _confirm_qpay
        from .wallet_topup import verify_and_credit, expire_creating
        now = datetime.utcnow()
        expire_creating(db, now)
        ids = claim_batch(db, now, max(1, min(20, settings.qpay_recheck_batch_size)))
        for pid in ids:
            stamp, completed = None, False
            try:
                p = db.get(Payment, pid)
                if p is None or p.status not in ("PENDING", "UNKNOWN", "REVIEW"):
                    continue  # webhook/polling түрүүлж авчихсан
                stamp = p.qpay_check_requested_at
                verify = verify_and_credit if p.kind == "WALLET_TOPUP" else _confirm_qpay
                if await verify(db, p):
                    fixed += 1
                    log.info("PENDING → PAID сэргээв: %s %.0f₮ (payment %s)",
                             p.sender_invoice_no, float(p.amount), p.id)
                completed = True
                db.rollback()
            except Exception as e:  # noqa: BLE001 — нэг төлбөрийн алдаа бусдыг зогсоохгүй
                log.warning("recheck алдаа (payment %s): %r", pid, e)
                db.rollback()
            finally:
                db.rollback()  # Never commit a cancelled/partly finalized transaction.
                if stamp is not None:
                    finish_verification(db, pid, stamp, completed=completed)
            await asyncio.sleep(max(0.5, settings.qpay_recheck_spacing_sec))  # QPay API-д завсарлага
    finally:
        db.close()
    return fixed


async def supervisor():
    """Startup-аас create_task-аар ажиллана: эхний удаа 2 минутын дараа,
    дараа нь qpay_recheck_sec (default 60с) тутам."""
    await asyncio.sleep(120)
    while True:
        try:
            n = await run_once()
            if n:
                log.info("нийт %d гацсан QPay төлбөр сэргээгдлээ", n)
        except Exception as e:  # noqa: BLE001
            log.error("qpay_recheck давталт унав: %r", e)
        await asyncio.sleep(max(30, int(settings.qpay_recheck_sec or 60)))
