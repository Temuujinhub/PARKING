"""Transactional financial outbox. No provider HTTP while holding money locks.

Delivery is at least once only when the remote operation has a stable idempotency
key. Ambiguous QPay/PosAPI receipt creation is quarantined, never blindly replayed.
"""
import asyncio
import hashlib
import logging
import uuid
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import or_

from ..config import settings
from ..database import SessionLocal
from ..models import AuditLog, FinancialJob, Payment, VatReceipt
from . import ebarimt, msgbill, qpay
from .receipts import assign_ebarimt_id

log = logging.getLogger("parking.financial_jobs")


def payment_site(payment):
    return payment.site or (payment.session.site if payment.session else None)


def _account(provider, site):
    if provider == "QPAY":
        acc = qpay.account_for(site)
        identity = f"{acc.username}|{acc.invoice_code}"
    elif provider == "MSGBILL":
        acc = msgbill.api_key_for(site)
        identity = f"{acc.base_url}|{acc.api_key}"
    else:
        acc = ebarimt.merchant_for(site)
        identity = repr(acc)
    return acc, hashlib.sha256(identity.encode()).hexdigest()


def receipt_provider(payment):
    if settings.qpay_ebarimt and payment.provider == "QPAY" and payment.provider_payment_id:
        return "QPAY"
    return "MSGBILL" if msgbill.account_enabled_for(payment_site(payment), payment.payment_method) else "POSAPI"


def enqueue_receipt(db, payment, *, amount=None, vat=None, session_id=None,
                    description="Зогсоолын төлбөр", key=None, external=None):
    """Insert receipt and job in the caller's money transaction; never commit."""
    site = payment_site(payment)
    provider, blocked = "UNCONFIGURED", None
    try:
        provider = receipt_provider(payment)
    except Exception as exc:
        blocked = type(exc).__name__  # Never let issuer configuration lose the money fact.
    receipt = VatReceipt(id=str(uuid.uuid4()), payment_id=payment.id,
        session_id=session_id or payment.session_id,
        amount=payment.amount if amount is None else amount,
        vat_amount=payment.vat_amount if vat is None else vat,
        customer_tin=payment.customer_tin, provider=provider, status="PENDING")
    db.add(receipt)
    if external and external.get("billId"):
        receipt.provider = external.get("provider") or "TERMINAL"
        assign_ebarimt_id(db, receipt, external["billId"], source="terminal",
            lottery=None if payment.customer_tin else external.get("lottery"))
        receipt.status = "SENT"
        ebarimt.cache_qr(payment.id, external.get("qrData"))
        return receipt
    payload = {"provider": provider, "amount": str(receipt.amount),
               "vat": str(receipt.vat_amount), "method": payment.payment_method,
               "customer_tin": payment.customer_tin,
               "receiver_type": payment.ebarimt_receiver_type or
                    ("COMPANY" if payment.customer_tin else "CITIZEN"),
               "provider_payment_id": payment.provider_payment_id,
               "description": description, "idempotency_key": key or f"pay-{payment.id}"}
    try:
        if blocked:
            raise ValueError(blocked)
        if provider == "POSAPI" and settings.ebarimt_mock and not settings.ebarimt_mock_receipts:
            raise ValueError("Баримтын суваг тохируулаагүй")
        _, payload["account_fingerprint"] = _account(provider, site)
    except Exception as exc:
        blocked = type(exc).__name__ + ": Баримтын сувгийн тохиргоог шалгана уу"
    job = FinancialJob(job_key=f"receipt:{receipt.id}", kind="RECEIPT",
        payment_id=payment.id, receipt_id=receipt.id, payload=payload,
        status="BLOCKED" if blocked else "PENDING", last_error=blocked)
    db.add(job)
    if blocked:
        receipt.status, receipt.receipt_url = "FAILED", blocked
    return receipt


def enqueue_partner(db, payment, hook, partner):
    from ..routers.integration_router import _payment_event
    key = f"partner-paid:{payment.id}"
    if db.query(FinancialJob.id).filter(FinancialJob.job_key == key).first():
        return
    event = _payment_event(db, payment, "payment.paid")
    event_id = str(uuid.uuid4())
    event["event_id"] = event_id
    if event.get("ebarimt"):
        event["ebarimt"].pop("qr_data", None)  # QR is deliberately memory-only.
    # Receipt delivery is independent: do not claim it has been issued yet.
    db.add(FinancialJob(id=event_id, job_key=key, kind="PARTNER",
        payment_id=payment.id, payload={"url": hook, "partner": str(partner), "event": event}))


def enqueue_receipt_ready(db, payment, receipt):
    original = db.query(FinancialJob).filter_by(job_key=f"partner-paid:{payment.id}").first()
    key = f"partner-receipt:{receipt.id}"
    if not original or db.query(FinancialJob.id).filter_by(job_key=key).first():
        return
    event_id = str(uuid.uuid4())
    event = {"event": "receipt.ready", "event_id": event_id, "payment_id": payment.id,
             "receipt_id": receipt.id, "ddtd": receipt.ebarimt_id,
             "lottery": receipt.lottery_code, "amount": str(receipt.amount), "status": "SENT"}
    db.add(FinancialJob(id=event_id, job_key=key, kind="PARTNER", payment_id=payment.id,
        payload={"url": original.payload["url"], "partner": original.payload["partner"], "event": event}))


def retry_payment_receipts(db, payment):
    # Also serializes adoption of historical receipts by concurrent operators.
    db.query(Payment).enable_eagerloads(False).filter_by(id=payment.id).with_for_update().one()
    jobs = (db.query(FinancialJob).filter(FinancialJob.payment_id == payment.id,
        FinancialJob.kind == "RECEIPT").order_by(FinancialJob.id)
        .populate_existing().with_for_update().all())
    recs = (db.query(VatReceipt).filter_by(payment_id=payment.id)
            .order_by(VatReceipt.created_at, VatReceipt.id).with_for_update().all())
    active = [r for r in recs if r.status != "CANCELLED"]
    if recs and not active:
        if len(recs) != 1:
            return {"ok": False, "review_required": True,
                    "error": "Олон цуцалсан баримтын дүнг эхлээд санхүү тулгана уу"}
        # Cancellation is already confirmed. Explicit operator request creates
        # replacement rows with new stable keys, preserving every old receipt.
        for rec in recs:
            enqueue_receipt(db, payment, amount=rec.amount, vat=rec.vat_amount,
                session_id=rec.session_id, key=f"replace-{rec.id}")
        db.commit()
        return {"ok": False, "pending": True, "error": "Шинэ баримтын ажил хадгалагдлаа"}
    if active and all(r.ebarimt_id for r in active):
        return {"ok": True, "ebarimt_id": active[0].ebarimt_id, "lottery": active[0].lottery_code}
    if not jobs:
        # A historical request may already have reached the tax provider.
        # Only a known msgbill reference supports non-creating reconciliation.
        if not active or any(not r.ebarimt_id and not (r.provider == "MSGBILL" and r.provider_ref) for r in active):
            return {"ok": False, "review_required": True,
                    "error": "Хуучин баримтын үр дүнг сувгаар тулгана уу; давхар баримт илгээгээгүй"}
        for rec in active:
            if rec.ebarimt_id:
                continue
            _, fingerprint = _account("MSGBILL", payment_site(payment))
            db.add(FinancialJob(job_key=f"receipt:{rec.id}", kind="RECEIPT", payment_id=payment.id,
                receipt_id=rec.id, payload={"provider": "MSGBILL", "account_fingerprint": fingerprint}))
        db.commit()
        return {"ok": False, "pending": True, "error": "Хуучин баримтын төлөвийг нөхөн шалгана"}
    for job in jobs:
        rec = db.get(VatReceipt, job.receipt_id)
        if rec and rec.ebarimt_id:
            continue
        if job.status == "UNKNOWN":
            return {"ok": False, "review_required": True,
                    "error": "Баримт өмнө үүссэн байж болно. Сувгаар тулгаж ДДТД-г батална уу; дахин илгээгээгүй"}
        if job.status == "BLOCKED" and job.attempts == 0:
            # No HTTP was attempted. It is safe to adopt repaired configuration.
            try:
                provider = receipt_provider(payment)
                _, fingerprint = _account(provider, payment_site(payment))
                if provider == "POSAPI" and settings.ebarimt_mock and not settings.ebarimt_mock_receipts:
                    raise ValueError("Баримтын суваг тохируулаагүй")
            except Exception as exc:
                return {"ok": False, "error": str(exc)[:200]}
            job.payload = {**job.payload, "provider": provider, "account_fingerprint": fingerprint,
                "customer_tin": payment.customer_tin, "receiver_type": payment.ebarimt_receiver_type or "CITIZEN"}
            rec.provider, rec.customer_tin = provider, payment.customer_tin
            job.status, job.next_attempt_at = "PENDING", datetime.utcnow()
            rec.status, rec.receipt_url = "PENDING", None
    db.commit()
    return {"ok": False, "pending": True, "error": "Баримтын хадгалсан ажлыг боловсруулж байна; дахин төлөхгүй"}


def claim_one(db, now=None):
    now = now or datetime.utcnow()
    job = (db.query(FinancialJob).filter(
        or_((FinancialJob.status.in_(("PENDING", "RETRY"))) &
                (FinancialJob.next_attempt_at <= now),
            (FinancialJob.status == "SENDING") & (FinancialJob.lease_until <= now)))
        .order_by(FinancialJob.next_attempt_at, FinancialJob.created_at, FinancialJob.id)
        .with_for_update(skip_locked=True).first())
    if job is None:
        db.rollback()
        return None
    # A dead worker may have issued the receipt already. These providers have
    # no verified create-idempotency contract; retain the unresolved intent.
    if job.status == "SENDING" and job.kind == "RECEIPT" and job.payload["provider"] != "MSGBILL":
        job.status, job.last_error = "UNKNOWN", "Баримт илгээх үеэр процесс тасарсан; сувгаар тулгана уу"
        rec = db.get(VatReceipt, job.receipt_id)
        if rec and not rec.ebarimt_id:
            rec.status, rec.receipt_url = "REVIEW", job.last_error
        db.commit()
        return "quarantined"
    job.status = "SENDING"
    job.attempts += 1
    job.lease_token = str(uuid.uuid4())
    job.lease_until = now + timedelta(minutes=3)
    result = (job.id, job.lease_token)
    db.commit()
    return result


async def deliver(job_id, lease_token, factory=SessionLocal):
    """Snapshot arguments, end read transaction, then contact the provider."""
    with factory() as db:
        job = db.get(FinancialJob, job_id)
        if not job or job.lease_token != lease_token or job.status != "SENDING":
            return
        data, kind, receipt_id = dict(job.payload), job.kind, job.receipt_id
        payment = db.get(Payment, job.payment_id)
        if payment is None or payment.status != "PAID":
            job.status, job.last_error = "BLOCKED", "Payment is not PAID"
            db.commit()
            return
        rec = db.get(VatReceipt, receipt_id) if receipt_id else None
        if rec and (rec.ebarimt_id or rec.status == "CANCELLED"):
            if rec.status == "SENT" and rec.ebarimt_id:
                enqueue_receipt_ready(db, payment, rec)
            job.status = "DONE"
            db.commit()
            return
        provider_ref = rec.provider_ref if rec else None
        try:
            if kind == "RECEIPT":
                acc, fingerprint = _account(data["provider"], payment_site(payment))
                if fingerprint != data.get("account_fingerprint"):
                    raise ValueError("Баримтын дансны тохиргоо өөрчлөгдсөн; санхүү тулгана уу")
        except Exception as exc:
            job.status, job.last_error = "BLOCKED", str(exc)[:200]
            if rec:
                rec.status, rec.receipt_url = "FAILED", job.last_error
            db.commit()
            return
        # Closing this session is intentional: neither lazy loads nor row locks
        # may keep a transaction open across the following HTTP call.
    result, error, safe_retry = {}, None, kind == "PARTNER" or data.get("provider") == "MSGBILL"
    try:
        if kind == "PARTNER":
            import httpx
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(data["url"], json=data["event"], headers={
                    "X-Parking-Partner": data["partner"], "X-Parking-Event": data["event"]["event"],
                    "X-Parking-Event-Id": job_id, "Idempotency-Key": job_id})
            response.raise_for_status()
            result = {"delivered": True}
        elif data["provider"] == "MSGBILL":
            if provider_ref:
                # Once an ID is known, only GET. FAILED/404 never authorizes a
                # new key or a second tax receipt behind the operator's back.
                result = await msgbill.get_receipt(acc, provider_ref)
            else:
                # This adapter sends whole MNT. Never round a fractional ledger
                # amount into a different tax receipt without reconciliation.
                if Decimal(data["amount"]) != Decimal(data["amount"]).to_integral_value():
                    safe_retry = False
                    raise ValueError("Fractional receipt requires a decimal-capable channel")
                result = await msgbill.create_receipt(acc, float(Decimal(data["amount"])),
                    description=data["description"], payment_method=data["method"],
                    idempotency_key=data["idempotency_key"], payer_reg_no=data["customer_tin"])
        elif data["provider"] == "QPAY":
            result = await qpay.create_ebarimt(data["provider_payment_id"], data["receiver_type"],
                receiver=data["customer_tin"] if data["receiver_type"] == "COMPANY" else None, acc=acc)
        else:
            result = await ebarimt.create_receipt(float(Decimal(data["amount"])),
                float(Decimal(data["vat"])),
                "CASH" if data["method"] in ("CASH", "TRANSFER") else "CARD",
                customer_tin=data["customer_tin"], merchant=acc)
        if kind == "RECEIPT" and not result.get("billId"):
            error = "ДДТД хараахан баталгаажаагүй; сувгийн төлөвийг тулгана уу"
    except Exception as exc:
        # Exception text/HTTP bodies may contain credentials. Keep only type.
        error = type(exc).__name__
    with factory() as db:
        job = (db.query(FinancialJob).filter(FinancialJob.id == job_id)
               .populate_existing().with_for_update().one())
        if job.lease_token != lease_token or job.status != "SENDING":
            return
        rec = (db.query(VatReceipt).filter_by(id=receipt_id).populate_existing()
               .with_for_update().one()) if receipt_id else None
        if rec and rec.status == "CANCELLED":
            job.status, job.lease_token, job.lease_until = "DONE", None, None
            db.commit()
            return
        if rec and result.get("msgbillId"):
            rec.provider_ref = result["msgbillId"]
        if rec and result.get("billId"):
            assign_ebarimt_id(db, rec, result["billId"], source="outbox",
                lottery=None if data.get("receiver_type") == "COMPANY" else result.get("lottery"),
                raw={k: (result.get("raw") or {}).get(k) for k in
                     ("id", "receipt_no", "state", "barimt_status", "ebarimt_receipt_id")})
            rec.status, rec.receipt_url = "SENT", None
            ebarimt.cache_qr(job.payment_id, result.get("qrData"))
        # A webhook may have completed the receipt while the request timed out.
        if rec and rec.ebarimt_id:
            error = None
            enqueue_receipt_ready(db, db.get(Payment, job.payment_id), rec)
        if error:
            job.status = "RETRY" if safe_retry and job.attempts < 24 else "UNKNOWN"
            job.next_attempt_at = datetime.utcnow() + timedelta(seconds=min(3600, 30 * 2**min(job.attempts, 7)))
            job.last_error = error
            if rec and not rec.ebarimt_id:
                rec.status = "PENDING" if job.status == "RETRY" else "REVIEW"
                rec.receipt_url = error
        else:
            job.status, job.last_error = "DONE", None
        job.lease_until, job.lease_token = None, None
        db.add(AuditLog(username="financial-worker", action="FINANCIAL_JOB_RESULT",
            entity="payment", entity_id=job.payment_id,
            detail={"job_id": job.id, "kind": job.kind, "status": job.status}))
        db.commit()


async def run_once(factory=SessionLocal, limit=20):
    for _ in range(limit):
        with factory() as db:
            claim = claim_one(db)
        if claim is None:
            break
        if claim != "quarantined":
            await deliver(*claim, factory=factory)


async def supervisor():
    await asyncio.sleep(5)
    while True:
        try:
            await run_once()
        except Exception:
            log.exception("financial outbox pass failed")
        await asyncio.sleep(5)
