"""Transactional financial outbox. No provider HTTP while holding money locks.

Delivery is at least once only when the remote operation has a stable idempotency
key. Ambiguous QPay/PosAPI receipt creation is quarantined, never blindly replayed.
"""
import asyncio
import hashlib
import logging
import re
import uuid
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import or_

from ..config import settings
from ..database import SessionLocal
from ..models import AuditLog, FinancialJob, Payment, VatReceipt
from . import ebarimt, msgbill, qpay
from .pos_app import is_pos_app_payment  # noqa: F401 — нэг тодорхойлолт (2026-10-14)
from .receipts import (REISSUED_NO_TERMINAL, assign_ebarimt_id, is_duplicate_cancel,  # noqa: F401
                       is_reissued_no_terminal, is_system_receipt)

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


# ── POS ТЕРМИНАЛЫН ӨӨРИЙН БАРИМТ (2026-10-13; 10-14 бүх зогсоолд) ───────────────
# PAX терминал POS аппаар авсан карт/бэлэн төлбөрт Ontime-ийн e-Barimt-ыг өөрөө
# хэвлэдэг — систем msgbill-ээр ДАХИН баримт гаргаж ТЕГ-т давхардал үүсгэж байв
# (2026-08-24 Хангарьд 63: 39 бэлэн + 24 карт; 9-р сард ~13 сая₮ илүү борлуулалт).
# Апп тэр ДДТД-г илгээдэггүй тул бид дугаарыг нь мэдэхгүй — зөвхөн «терминал
# гаргасан» гэсэн EXTERNAL мөр үлдээж, FinancialJob ҮҮСГЭХГҮЙ. Тохиргоо:
# app_settings.EBARIMT_KEY — ерөнхий анхдагч АСААЛТТАЙ, зогсоолоор унтрааж болно.
TERMINAL_PROVIDER = "POS-TERMINAL"
TERMINAL_STATUS = "EXTERNAL"
TERMINAL_NOTE = "Баримтыг POS терминал (Ontime) гаргасан — систем давхар үүсгээгүй"
# Хэрэглэгчид харагдах тайлбар — retry/цуцлалт EXTERNAL дээр яагаад татгалздаг вэ
EXTERNAL_REFUSAL = ("Энэ төлбөрийн НӨАТ баримтыг POS терминал (Ontime) гаргасан тул систем "
                    "давхар баримт үүсгэхгүй. Терминалын ДДТД-г «баримт холбох»-оор бүртгэнэ үү. "
                    "Терминал баримт ГАРГААГҮЙ нь батлагдвал «Систем баримт гаргах»-ыг дарж "
                    "баталгаажуулна уу.")
DUPLICATE_REFUSAL = {
    "ok": False, "duplicate_of_pos": True,
    "error": "Энэ төлбөрийн баримтыг POS терминалын (касс дээр хэвлэсэн) баримттай "
             "ДАВХАРДСАН гэж ТЕГ тулгалтаар цуцалсан — дахин үүсгэвэл ТЕГ-т нэг "
             "борлуулалт 2 баримттай болно. Үнэхээр шаардлагатай бол давхардлыг "
             "зөвшөөрч (allow_duplicate) дахин дарна уу."}
EXTERNAL_SETTING_ON = ("«POS терминал (PAX апп) өөрөө e-Barimt гаргадаг» дүрэм энэ төлбөрт "
                       "үйлчилж байна — систем баримт гаргахгүй. Терминал баримт ГАРГААГҮЙ нь "
                       "батлагдсан бол «терминал баримт гаргаагүй» гэж баталгаажуулж "
                       "(no_terminal_receipt) дахин илгээнэ үү.")


def terminal_issues_receipt(db, payment) -> bool:
    """Энэ төлбөрийн НӨАТ баримтыг POS терминал (PAX апп / Ontime) ӨӨРӨӨ гаргасан гэж үзэх үү.

    Дөрвөн нөхцөл ЗЭРЭГ: (1) POS аппын төлбөр (services/pos_app — source POSAPP эсвэл
    /pos/confirm-ын карт; толгойгүй хуучин аппын хэрэглэгчийг _mark_pos_app POSAPP болгож
    тэмдэглэдэг), (2) арга CARD/CASH, (3) тохиргоо — ерөнхий анхдагч АСААЛТТАЙ (2026-10-14
    эзний шийдвэр: бүх зогсоол, бүх түрээслэгч), зогсоолд тодорхой унтраагаагүй ба энэ
    арга сонгогдсон, (4) ААН-ийн (ТТД-тэй) баримт БИШ — терминал (Ontime) ИРГЭНИЙ баримт
    хэвлэдэг тул байгууллагын НӨАТ-ын баримтыг систем гаргасаар байна (түүхэн цуцлалт
    `vat_cancel_duplicates --pos-app` ч ААН-ийг хөндөхгүй — нэг бодлого). Зогсоол
    тодорхойгүй бол ерөнхий утга. Тохиргоо уншигдахгүй бол False — систем баримт
    гаргана: баримтгүй үлдэхээс давхардал дээр."""
    method = (payment.payment_method or "").upper()
    if method not in ("CARD", "CASH") or not is_pos_app_payment(payment):
        return False
    if (getattr(payment, "ebarimt_receiver_type", None) == "COMPANY"
            or (getattr(payment, "customer_tin", None) or "").strip()):
        return False
    site = payment_site(payment)
    try:
        from .app_settings import pos_terminal_receipt_methods
        return method in pos_terminal_receipt_methods(db, getattr(site, "id", None))
    except Exception:  # noqa: BLE001
        log.exception("POS терминалын баримтын тохиргоо уншигдсангүй (payment=%s)", payment.id)
        return False


def _mark_terminal(receipt, payment, site):
    receipt.provider, receipt.status = TERMINAL_PROVIDER, TERMINAL_STATUS
    receipt.receipt_url = TERMINAL_NOTE
    receipt.raw = {"terminal": {"rule": "ebarimt_rules.pos_terminal_issues",
                                "site_id": getattr(site, "id", None),
                                "method": payment.payment_method,
                                "source": getattr(payment, "source", None),
                                "provider": payment.provider}}


# ── ДАВХАРДЛЫН ЦУЦЛАЛТЫН ДАРААХ ТЕРМИНАЛЫН БАРИМТ (2026-10-13 хяналт #2) ──────
# Манай/гадны баримтын ялгаа: receipts.is_system_receipt (нэг тодорхойлолт).
TEG_TERMINAL_PROVIDER = "ONTIME"   # апп ebarimt_provider=ONTIME илгээсэн үетэй ижил
_TEG_DDTD = re.compile(r"^\d{33}$")


def duplicate_guarded(recs) -> bool:
    """POS-ийн давхардал гэж цуцалсан (тэмдэгтэй) баримттай, ТЭМДЭГГҮЙ ИЛГЭЭГДСЭН баримтгүй
    төлбөр — систем баримтыг ДАХИН гаргахгүй (retry 409, бөөн нөхөлт алгасна).
    Нэг л дүрэм: financial_jobs.retry_payment_receipts ба vat-retry-failed хоёулаа үүгээр.
    Терминал хэвлээгүй нь батлагдаж ДАХИН гаргасан (REISSUED_NO_TERMINAL) идэвхтэй баримттай
    бол хамгаалахгүй — тэр баримт FAILED болбол бөөн нөхөлт/retry дахин илгээнэ (хяналт 2, I)."""
    if any(is_reissued_no_terminal(r) and r.status != "CANCELLED" for r in recs):
        return False
    return (any(is_duplicate_cancel(r) for r in recs)
            and not any(r.status == "SENT" and r.ebarimt_id and not is_duplicate_cancel(r)
                        for r in recs))


def record_terminal_receipt(db, payment, teg_ddtd, *, replaced=(), username="system",
                            source="vat-reconcile.cancel-duplicates"):
    """POS-ийн давхардал гэж манай баримтыг цуцалсны ДАРАА терминалын баримтыг бүртгэнэ.

    Яагаад (2026-10-13 хяналт #2): цуцлалтын дараа төлбөрт зөвхөн CANCELLED мөр үлдэж,
    «Дахин үүсгэх» msgbill-ээр ДАХИН баримт гаргаж давхардлыг сэргээдэг байв (Postgres-д
    давтагдсан). ТЕГ-ийн экспортод терминалын ДДТД (DUPLICATE мөр) бий — апп ebarimt_id
    илгээсэн үетэй ИЖИЛ хэлбэрээр (provider ONTIME, SENT) бүртгэнэ: хэвлэлт/public баримт
    тэр ДДТД-г харуулна, ТЕГ тулгалт таарна, retry «ДДТД-тэй» гэж юу ч үүсгэхгүй.

    ДДТД-д итгэхгүй бол (33 оронтой биш · Excel-ийн тоон эвдрэл · цуцалсан манай баримтын
    КАССтай ижил = тулгалт хосыг сольж msgbill-ийн өөрийнх нь мөр үлдсэн · өөр мөрөнд
    аль хэдийн бүртгэлтэй) ДДТД-гүй EXTERNAL мөр (терминал гаргасан) бүртгэнэ.
    Идемпотент: терминалын мөр аль хэдийн байвал түүнийг буцаана — ДДТД-гүй EXTERNAL
    мөр байхад итгэх ТЕГ ДДТД ирвэл (ж: POS аппын дүрмээр цуцалсны дараа ТЕГ файлаар
    дахин ажиллуулсан) тэр мөрийг ONTIME/SENT болгож ДДТД-г нь бичнэ (шинэ мөр нэмэхгүй —
    нэг төлбөрт хоёр терминалын баримт харагдахгүй). Commit хийхгүй."""
    recs = db.query(VatReceipt).filter_by(payment_id=payment.id).all()
    ddtd = str(teg_ddtd or "").strip()
    ours_pos = {(r.ebarimt_id or "")[-8:] for r in recs if r.ebarimt_id and is_system_receipt(r)}
    trusted = (bool(_TEG_DDTD.match(ddtd)) and ddtd[-8:] not in ours_pos
               and not ddtd.endswith("00000000")
               and db.query(VatReceipt.id).filter(VatReceipt.ebarimt_id == ddtd).first() is None)
    info = {"teg_ddtd": ddtd or None, "replaces": [r.id for r in replaced],
            "by": username, "source": source}
    for r in recs:
        if r.status == TERMINAL_STATUS or (r.status in ("SENT", "CANCEL_PENDING")
                                           and not is_system_receipt(r)):
            if r.status == TERMINAL_STATUS and not r.ebarimt_id and trusted:
                r.provider, r.status, r.receipt_url = TEG_TERMINAL_PROVIDER, "SENT", None
                assign_ebarimt_id(db, r, ddtd, source=f"teg:{source}", username=username, raw=info)
                db.add(AuditLog(username=username, action="EBARIMT_TERMINAL_RECORDED",
                                entity="payment", entity_id=payment.id,
                                detail={"receipt_id": r.id, "status": r.status,
                                        "ebarimt_id": r.ebarimt_id, "upgraded_external": True,
                                        **info}))
            return r
    rec = VatReceipt(id=str(uuid.uuid4()), payment_id=payment.id, session_id=payment.session_id,
                     amount=payment.amount, vat_amount=payment.vat_amount,
                     customer_tin=payment.customer_tin)
    if trusted:
        rec.provider, rec.status = TEG_TERMINAL_PROVIDER, "SENT"
        db.add(rec)
        assign_ebarimt_id(db, rec, ddtd, source=f"teg:{source}", username=username, raw=info)
    else:
        _mark_terminal(rec, payment, payment_site(payment))
        rec.raw = {**rec.raw, "teg": {**info, "untrusted": bool(ddtd)}}
        db.add(rec)
    db.add(AuditLog(username=username, action="EBARIMT_TERMINAL_RECORDED", entity="payment",
                    entity_id=payment.id, detail={"receipt_id": rec.id, "status": rec.status,
                                                  "ebarimt_id": rec.ebarimt_id, **info}))
    return rec


def receipt_provider(payment):
    if settings.qpay_ebarimt and payment.provider == "QPAY" and payment.provider_payment_id:
        return "QPAY"
    return "MSGBILL" if msgbill.account_enabled_for(payment_site(payment), payment.payment_method) else "POSAPI"


def enqueue_receipt(db, payment, *, amount=None, vat=None, session_id=None,
                    description="Зогсоолын төлбөр", key=None, external=None, terminal=None):
    """Insert receipt and job in the caller's money transaction; never commit.

    terminal: POS терминал баримтаа өөрөө гаргасан эсэх — None бол энд шийднэ
    (`terminal_issues_receipt`). Тийм бол EXTERNAL мөр, ажилгүй (msgbill-гүй)."""
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
    if terminal is None:
        terminal = terminal_issues_receipt(db, payment)
    if terminal:
        _mark_terminal(receipt, payment, site)
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
    # The outbox references this receipt. Explicitly persist its parent row
    # before adding the job; FK columns alone do not order ORM unit-of-work
    # inserts without a mapped relationship. Both remain in one transaction.
    db.flush()
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


def _reissue_external(db, payment, rec, mark=None):
    """EXTERNAL мөрийг СИСТЕМИЙН баримт болгоно (админ тодорхой хүссэн/терминал хэвлээгүй
    нь батлагдсан үед л). Мөр нь хэвээр (id тогтвортой) — шинэ PENDING ажил."""
    site = payment_site(payment)
    provider = receipt_provider(payment)
    _, fingerprint = _account(provider, site)
    if provider == "POSAPI" and settings.ebarimt_mock and not settings.ebarimt_mock_receipts:
        raise ValueError("Баримтын суваг тохируулаагүй")
    rec.provider, rec.status, rec.receipt_url = provider, "PENDING", None
    rec.customer_tin = payment.customer_tin
    raw = dict(rec.raw) if isinstance(rec.raw, dict) else {}
    raw["reissued_from_external"] = {"at": datetime.utcnow().isoformat(timespec="seconds")}
    if mark:
        raw["reissue"] = dict(mark)
    rec.raw = raw
    from ..routers.payments_router import _receipt_desc
    if (payment.kind or "") == "DEBT":
        # Нөхөн төлбөрийн (өрийн) төлөлт — анхны урсгалтай ижил тайлбар (compensations pay)
        plate = (payment.raw_payload or {}).get("plate_number") or ""
        desc = f"Зогсоолын өр · {plate}"
    else:
        desc = _receipt_desc(payment)
    payload = {"provider": provider, "amount": str(rec.amount), "vat": str(rec.vat_amount),
               "method": payment.payment_method, "customer_tin": payment.customer_tin,
               "receiver_type": payment.ebarimt_receiver_type or
                    ("COMPANY" if payment.customer_tin else "CITIZEN"),
               "provider_payment_id": payment.provider_payment_id,
               "description": desc, "idempotency_key": f"external-{rec.id}",
               "account_fingerprint": fingerprint}
    # EXTERNAL мөрийн ажил ердийн урсгалд ҮҮСДЭГГҮЙ; гараар нэмэгдсэн байсан ч deliver
    # түүнийг сувагт илгээлгүй DONE болгосон — тэр ажлыг дахин ашиглана (job_key unique)
    job = db.query(FinancialJob).filter_by(job_key=f"receipt:{rec.id}").first()
    if job is None:
        db.add(FinancialJob(job_key=f"receipt:{rec.id}", kind="RECEIPT", payment_id=payment.id,
                            receipt_id=rec.id, payload=payload))
    else:
        job.payload, job.status, job.last_error = payload, "PENDING", None
        job.next_attempt_at, job.lease_token, job.lease_until = datetime.utcnow(), None, None


def retry_payment_receipts(db, payment, *, reissue_external=False, allow_duplicate=False,
                           no_terminal_receipt=False, username="system", mark=None):
    """reissue_external=True — ЗӨВХӨН нэг төлбөрийн «Систем баримт гаргах» товч эсвэл
    tools/vat_reissue_missing. Бөөн нөхөлт EXTERNAL-ийг ХЭЗЭЭ Ч хөндөхгүй.
    allow_duplicate=True — POS-ийн давхардал гэж цуцалсан баримтыг ил тод зөвшөөрлөөр
    дахин гаргах (эс бөгөөс татгалзана). username — аудитын мөрөнд (товч дарсан админ).
    no_terminal_receipt=True — терминал (Ontime) баримт ХЭВЛЭЭГҮЙ нь батлагдсан (админ
    баталгаажуулсан эсвэл ТЕГ экспортод терминалын мөр алга): «POS терминал баримт
    гаргадаг» дүрэм үйлчилж байсан ч систем msgbill баримт гаргана — EXTERNAL-ийг системийн
    баримт болгох, цуцлагдсаныг ОРЛОХ мөрийг EXTERNAL биш системийнхээр, BLOCKED ажлыг
    EXTERNAL болгохгүй. 2026-10-14: дүрэм ерөнхийдөө асаалттай тул нэг борлуулалтын
    төлөө зогсоолын тохиргоог унтраах шаардлагагүй болгов (бусад төлбөрт нөлөөлөхгүй).
    mark: шинэ/хөрвүүлсэн баримтын raw["reissue"] (ж: REISSUED_NO_TERMINAL тэмдэг)."""
    # Also serializes adoption of historical receipts by concurrent operators.
    db.query(Payment).enable_eagerloads(False).filter_by(id=payment.id).with_for_update().one()
    jobs = (db.query(FinancialJob).filter(FinancialJob.payment_id == payment.id,
        FinancialJob.kind == "RECEIPT").order_by(FinancialJob.id)
        .populate_existing().with_for_update().all())
    recs = (db.query(VatReceipt).filter_by(payment_id=payment.id)
            .order_by(VatReceipt.created_at, VatReceipt.id).with_for_update().all())
    active = [r for r in recs if r.status != "CANCELLED"]
    # Давхардлын тэмдгийг БУСАД мөрөөс үл хамааран, EXTERNAL-ээс ӨМНӨ шалгана (2026-10-13
    # хяналт + нэгтгэл): өмнө нь БҮХ баримт CANCELLED үед л хамгаалдаг байсан тул цуцалсан
    # msgbill + ДДТД-гүй FAILED/REVIEW/PENDING мөртэй төлбөрийг бөөн нөхөлт дахин илгээнэ.
    # Давхардлын цуцлалт одоо терминалын мөр (ONTIME/SENT эсвэл EXTERNAL) бүртгэдэг —
    # ONTIME/SENT нь «тэмдэггүй илгээгдсэн» тул хэвийн урсгал (ДДТД-тэй, юу ч үүсгэхгүй);
    # EXTERNAL бол энд давхардал гэж татгалзана (бөөн нөхөлт skipped_duplicate_of_pos).
    if not allow_duplicate and duplicate_guarded(recs):
        # 2026-10-13: POS терминал (Ontime) баримт хэвлэсэн тул манай msgbill
        # баримтыг ТЕГ тулгалтаар цуцалсан. Дахин үүсгэвэл давхардал буцаж үүснэ.
        return dict(DUPLICATE_REFUSAL)
    external = [r for r in active if r.status == TERMINAL_STATUS]
    if external:
        # Терминал баримтаа гаргасан — msgbill-ээр дахин гаргавал ТЕГ-т давхар.
        if not reissue_external:
            return {"ok": False, "external": True, "error": EXTERNAL_REFUSAL}
        if not no_terminal_receipt and terminal_issues_receipt(db, payment):
            return {"ok": False, "external": True, "error": EXTERNAL_SETTING_ON}
        try:
            for rec in external:
                _reissue_external(db, payment, rec, mark)
        except Exception as exc:  # noqa: BLE001 — суваггүй бол EXTERNAL хэвээр үлдэнэ
            db.rollback()
            return {"ok": False, "error": str(exc)[:200]}
        db.add(AuditLog(username=username, action="EBARIMT_EXTERNAL_REISSUE", entity="payment",
                        entity_id=payment.id,
                        detail={"receipts": [r.id for r in external],
                                "no_terminal_receipt": bool(no_terminal_receipt),
                                **({"reason": mark.get("reason")} if mark else {})}))
        db.commit()
        return {"ok": False, "pending": True,
                "error": "Терминалын тэмдэглэгээг системийн баримт болгож ажил хадгаллаа"}
    if recs and not active:
        if len(recs) != 1:
            return {"ok": False, "review_required": True,
                    "error": "Олон цуцалсан баримтын дүнг эхлээд санхүү тулгана уу"}
        # Cancellation is already confirmed. Explicit operator request creates
        # replacement rows with new stable keys, preserving every old receipt.
        new = [enqueue_receipt(db, payment, amount=rec.amount, vat=rec.vat_amount,
                               session_id=rec.session_id, key=f"replace-{rec.id}",
                               terminal=False if no_terminal_receipt else None)
               for rec in recs]
        if mark:
            # Зөвхөн СИСТЕМИЙН баримтад (EXTERNAL бүртгэл — «дахин гаргасан баримт» биш)
            for r in new:
                if r.status != TERMINAL_STATUS:
                    r.raw = {**(r.raw if isinstance(r.raw, dict) else {}), "reissue": dict(mark)}
        db.commit()
        if all(r.status == TERMINAL_STATUS for r in new):
            # Тохиргоо асаалттай — орлох баримтыг терминалынх гэж БҮРТГЭВ (commit
            # хийгдсэн), msgbill-гүй. created=True: endpoint 409 биш 200 буцаана.
            return {"ok": False, "external": True, "created": True,
                    "error": "Терминалын (Ontime) баримт гэж бүртгэлээ — систем баримт үүсгээгүй "
                             "(«POS терминал (PAX апп) өөрөө e-Barimt гаргадаг» дүрэм)"}
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
    terminal_now = None
    converted = 0
    for job in jobs:
        rec = db.get(VatReceipt, job.receipt_id)
        if rec and (rec.ebarimt_id or rec.status == TERMINAL_STATUS):
            continue
        if job.status == "UNKNOWN":
            return {"ok": False, "review_required": True,
                    "error": "Баримт өмнө үүссэн байж болно. Сувгаар тулгаж ДДТД-г батална уу; дахин илгээгээгүй"}
        if job.status == "BLOCKED" and job.attempts == 0:
            if terminal_now is None:
                terminal_now = not no_terminal_receipt and terminal_issues_receipt(db, payment)
            if terminal_now and rec is not None and not is_reissued_no_terminal(rec):
                # Сувагт ОГТ илгээгээгүй (attempts 0) ба «POS терминал өөрөө НӨАТ баримт
                # гаргадаг» дүрэм үйлчилж байна — шинэ төлбөртэй ижил EXTERNAL болгоно
                # (терминал хэвлээгүй нь батлагдаж ДАХИН гаргасан баримтаас бусдыг);
                # msgbill-ээр гаргавал терминалын баримттай давхардана (нэгтгэлийн хяналт)
                _mark_terminal(rec, payment, payment_site(payment))
                job.status, job.last_error = "DONE", "EXTERNAL: " + TERMINAL_NOTE[:150]
                job.lease_until, job.lease_token = None, None
                converted += 1
                continue
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
    if converted and not any(j.status in ("PENDING", "RETRY") for j in jobs):
        return {"ok": False, "external": True, "created": True,
                "error": "Терминалын (Ontime) баримт гэж бүртгэлээ — систем баримт үүсгээгүй "
                         "(«POS терминал (PAX апп) өөрөө e-Barimt гаргадаг» дүрэм)"}
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
        if rec and rec.status == TERMINAL_STATUS:
            # Терминал гаргасан баримт — сувагт ХЭЗЭЭ Ч илгээхгүй (давхардал)
            job.status, job.last_error = "DONE", "EXTERNAL: " + TERMINAL_NOTE[:150]
            job.lease_until, job.lease_token = None, None
            db.commit()
            return
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
