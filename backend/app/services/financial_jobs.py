"""Transactional financial outbox. No provider HTTP while holding money locks.

Delivery is at least once only when the remote operation has a stable idempotency
key. Ambiguous QPay/PosAPI receipt creation is quarantined, never blindly replayed.
"""
import asyncio
import hashlib
import json
import logging
import re
import uuid
from datetime import datetime, timedelta
from decimal import Decimal

import httpx
from sqlalchemy import or_

from ..config import settings
from ..database import SessionLocal
from ..models import AuditLog, FinancialJob, Payment, VatReceipt
from . import ebarimt, msgbill, qpay
from .pos_app import is_pos_app_payment  # noqa: F401 — нэг тодорхойлолт (2026-10-14)
from .receipts import (REISSUED_NO_TERMINAL, assign_ebarimt_id, is_duplicate_cancel,  # noqa: F401
                       is_reissued_no_terminal, is_system_receipt)

log = logging.getLogger("parking.financial_jobs")


# ── СУВГИЙН АЛДААНЫ ТЕКСТ БА АНГИЛАЛ (2026-10-16) ─────────────────────────────
# Гомдол «байгууллагаар НӨАТ гаргахад алдаа» (2026-10-16). Outbox (de4ce4e, 09-19) алдааны
# зөвхөн АНГИЙН НЭРИЙГ («HTTPStatusError», «MsgbillError») job.last_error / receipt_url-д
# хадгалдаг байв — QPay-ийн «receipt.customerTin … [0-9]{11,14}» (регистр ТЕГ-т алга),
# «ebarimt_receiver MAX_LENGTH(7)» (ТТД өгсөн) зэрэг ТОДОРХОЙ татгалзал UNKNOWN/REVIEW-д орж,
# Ибаримт хуудас «Дугаар засах»/«Иргэнээр»-ийг хэзээ ч санал болгодоггүй, «Дахин үүсгэх» 400
# өгдөг байв (audit1014: 10-р сарын байгууллагын 6 баримт гацсан — QPay 5 + msgbill 1;
# HTTPStatusError-ийн 6-гийнх нь шалтгааныг текст хаягдсан тул тогтоох боломжгүй).
# Одоо: HTTP статус + цэвэрлэсэн (нууцгүй) хариуны бие ≤300 тэмдэгт хадгална; ҮҮСГЭХ
# хүсэлтийн HTTP 400/404/409/422 = суваг хүсэлтийг ТАТГАЛЗСАН (баримт үүсээгүй) →
# FAILED + ангилал (REJECTED / REJECTED_RECEIVER). Timeout/холболт/5xx/billId-гүй 200 бол
# ТОДОРХОЙГҮЙ хэвээр (UNKNOWN/REVIEW, msgbill-д ижил түлхүүрээр RETRY) — QPay create
# идемпотент БИШ (2026-08-19 f7bb0ae7: зэрэгцээ 2 дуудлага 2 өөр ДДТД) тул тодорхойгүй
# үр дүнгийн дараа хэзээ ч автоматаар дахин create хийхгүй.
REJECT_HTTP = frozenset({400, 404, 409, 422})
ERR_REJECTED = "REJECTED"                    # суваг татгалзсан — баримт үүсээгүй
ERR_REJECTED_RECEIVER = "REJECTED_RECEIVER"  # … худалдан авагчийн дугаараас болж
ERR_UNCERTAIN = "UNCERTAIN"                  # үр дүн тодорхойгүй — сувгаар тулгана
REJECTED_JOB = "REJECTED"                    # FinancialJob.status — эцсийн, автоматаар давтахгүй
ERROR_TEXT_MAX = 300
# Худалдан авагчийн дугаартай холбоотой татгалзал. «ТТД» дангаараа БИШ: QPay-ийн мерчант
# талын «… түрээслэгчийн жагсаалтанд '…' ТТД бүртгэлгүй», msgbill «merchantTin … is not
# registered» нь худалдан авагчийн алдаа биш (2026-09-06: «Дугаар засах» буруу гарч байв).
# (`_` нь \b-ийн хувьд үсэг тул INVALID_PAYER_REG_NO / INVALID_TIN кодыг ч барина)
_RECEIVER_RE = re.compile(r"customer_?tin|receiver|payer|buyer|худалдан\s*авагч|регистр|"
                          r"(?<![a-z])regist(?:er|ration)(?![a-z])|(?<![a-z])tin(?![a-z])|MAX_LENGTH", re.I)
_MERCHANT_RE = re.compile(r"merchant|түрээслэгч|POS API instance", re.I)
# 4xx ч «баримт аль хэдийн бий / түлхүүр давхардсан» гэсэн утгатай бол тодорхой биш
_MAYBE_CREATED_RE = re.compile(r"already\s+(?:exist|created|issued)|duplicate|idempoten|"
                               r"давхар|аль\s*хэдийн", re.I)
_SECRET_RES = (
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer ***"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9._-]+"), "***"),           # JWT
    (re.compile(r"\b(?:bsk|whsec|sk|pk)_[A-Za-z0-9_-]{6,}"), "***"),           # msgbill/webhook түлхүүр
    (re.compile(r'(?i)("?(?:access_token|refresh_token|token|api_?key|x-api-key|password|'
                r'secret|authorization)"?\s*[:=]\s*)"?[^",\s}]+'), r"\1***"),
)


def safe_text(value, limit: int = ERROR_TEXT_MAX) -> str:
    """Сувгийн хариу/алдааг НЭГ мөр текст болгоно: dict → JSON, токен/түлхүүр/нууц үг
    ***-ээр, хоосон зай шахаж ≤limit. QPay 400-ийн `message` dict байж болно (1c114f3)."""
    if value is None:
        return ""
    if not isinstance(value, str):
        try:
            value = json.dumps(value, ensure_ascii=False, default=str)
        except Exception:  # noqa: BLE001
            value = str(value)
    for pattern, repl in _SECRET_RES:
        value = pattern.sub(repl, value)
    return " ".join(value.split())[:limit]


def _response_body(response) -> object:
    try:
        data = response.json()
    except Exception:  # noqa: BLE001
        return response.text or ""
    if isinstance(data, dict) and data.get("message"):
        code = data.get("error") or data.get("code")
        msg = data["message"]
        return f"{code}: {safe_text(msg)}" if isinstance(code, str) and code else msg
    return data


def provider_error(exc: Exception, provider: str | None, *, creating: bool) -> tuple[str, str, int | None]:
    """Сувгийн алдаа → (текст ≤300, ангилал, HTTP статус).

    ТОДОРХОЙ татгалзал (REJECTED/REJECTED_RECEIVER) нь ЗӨВХӨН баримт ҮҮСГЭХ хүсэлтийн
    (QPay ebarimt_v3/create, msgbill POST /partner/receipts) HTTP 400/404/409/422, хариунд
    «аль хэдийн/давхар/idempotency» гэсэн утга агуулаагүй үед. GET (provider_ref мэдэгдэж
    буй msgbill) — 404 ч баримт үүсээгүйг батлахгүй. PosAPI-ийн create идемпотент биш,
    гэрээ нь тодорхойгүй тул ангилахгүй. Сүлжээ/timeout-ийн текст хариуны биегүй тул зөвхөн
    ангийн нэр (хуучнаараа — нууц агуулж болзошгүй)."""
    status = None
    if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None:
        status = exc.response.status_code
        label = {"QPAY": "QPay", "POSAPI": "PosAPI", "MSGBILL": "msgbill"}.get(provider or "", provider or "?")
        text = safe_text(f"{label} HTTP {status}: {safe_text(_response_body(exc.response))}")
    elif isinstance(exc, msgbill.MsgbillError):
        status = exc.status
        text = str(exc)
        if exc.code and exc.code not in text:
            text = f"{text} [{exc.code}]"
        text = safe_text(text)
    else:
        return type(exc).__name__, ERR_UNCERTAIN, None
    cls = ERR_UNCERTAIN
    if (creating and provider in ("QPAY", "MSGBILL") and status in REJECT_HTTP
            and not _MAYBE_CREATED_RE.search(text)):
        cls = (ERR_REJECTED_RECEIVER if _RECEIVER_RE.search(text) and not _MERCHANT_RE.search(text)
               else ERR_REJECTED)
    return text, cls, status


def error_class(rec) -> str | None:
    """Баримтын сүүлийн сувгийн алдааны ангилал (raw.error.class) — UI/retry-д."""
    raw = getattr(rec, "raw", None)
    err = raw.get("error") if isinstance(raw, dict) else None
    return err.get("class") if isinstance(err, dict) else None


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


# ── POS ТЕРМИНАЛЫН ӨӨРИЙН БАРИМТ (2026-10-13; 10-14 бүх зогсоолд; 10-15 анхдагч OFF) ──
# PAX терминал POS аппаар авсан карт/бэлэн төлбөрт Ontime-ийн e-Barimt-ыг өөрөө
# хэвлэдэг байсан — систем msgbill-ээр ДАХИН баримт гаргаж ТЕГ-т давхардал үүсгэж байв
# (2026-08-24 Хангарьд 63: 39 бэлэн + 24 карт; 9-р сард ~13 сая₮ илүү борлуулалт).
# Апп тэр ДДТД-г илгээдэггүй тул бид дугаарыг нь мэдэхгүй — зөвхөн «терминал
# гаргасан» гэсэн EXTERNAL мөр үлдээж, FinancialJob ҮҮСГЭХГҮЙ. Тохиргоо:
# app_settings.EBARIMT_KEY — 2026-10-15-аас ерөнхий анхдагч УНТРААЛТТАЙ (аппын шинэ
# хувилбар Ontime-гүй, баримтыг манай API-аас хэвлэдэг); ерөнхий/зогсоолоор асааж болно.
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
    тэмдэглэдэг), (2) арга CARD/CASH, (3) тохиргоо асаалттай — ерөнхий анхдагч 2026-10-15-аас
    УНТРААЛТТАЙ (10-14-нд асаалттай байсан; аппын шинэ хувилбар Ontime-гүй), ерөнхий эсвэл
    зогсоолд асааж энэ арга сонгогдсон, (4) ААН-ийн (ТТД-тэй) баримт БИШ — терминал (Ontime) ИРГЭНИЙ баримт
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
        ebarimt.cache_qr(receipt.id, external.get("qrData"))   # түлхүүр = баримт
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


# ── ХҮЛЭЭН АВАГЧ СОЛИХ (2026-10-16) ───────────────────────────────────────────
# retry-ebarimt {customer_tin|receiver_type} аль нэг ажил attempts>0 бол ҮРГЭЛЖ 409 өгдөг
# байв — баримт ≤5с-д (outbox) эсвэл шууд (inline) илгээгддэг тул «Цуцлах»-ын дараа ч,
# тодорхой татгалзлын дараа ч засах зам огт байгаагүй (09-19-өөс). Хуучин ажлын payload-ыг
# ӨӨРЧЛӨХГҮЙ (msgbill ижил Idempotency-Key-г өөр биетэй хүлээн авахгүй; QPay-д илгээсэн
# хүсэлтийн нотолгоо), харин ШИНЭ VatReceipt + FinancialJob-ийг ШИНЭ түлхүүрээр
# (pay-{payment_id}-rcv-{n}) үүсгэнэ. Зөвшөөрөх нөхцөл — төлбөрийн БҮХ мөр:
#   • CANCELLED (сувгаар цуцлалт батлагдсан), эсвэл
#   • сувгийн ТОДОРХОЙ татгалзал (ажил REJECTED, ДДТД/billId/provider_ref хэзээ ч ирээгүй), эсвэл
#   • сувагт ОГТ илгээгээгүй (ажил attempts 0).
# UNKNOWN/REVIEW/SENT/PENDING(RETRY)/SENDING/CANCEL_PENDING/EXTERNAL → 409 хэвээр.
RCV_KEY = "pay-{pid}-rcv-{n}"
RECEIVER_LOCKED = ("Хүлээн авагчийг солих боломжгүй: баримт илгээгдсэн/үр дүн тодорхойгүй мөр байна. "
                   "ИЛГЭЭГДСЭН баримтыг эхлээд «Цуцлах»-аар сувгаар цуцална уу; «Тулгах шаардлагатай» "
                   "(REVIEW) баримтыг сувгаар тулгаж ДДТД-г батална уу — давхар баримт гаргахгүй.")


def _jobs_by_receipt(jobs) -> dict:
    out: dict = {}
    for j in jobs:
        out.setdefault(j.receipt_id, []).append(j)
    return out


def superseded_ids(recs, jobs) -> set:
    """Өөр мөрөөр ОРЛОГДСОН мөрүүд: цуцалсны дараах «Дахин үүсгэх» (түлхүүр replace-{id}),
    хүлээн авагч солилт (raw.receiver_change.replaces / raw.superseded_by)."""
    out = set()
    for r in recs:
        raw = r.raw if isinstance(r.raw, dict) else {}
        if raw.get("superseded_by"):
            out.add(r.id)
        rc = raw.get("receiver_change")
        if isinstance(rc, dict) and rc.get("replaces"):
            out.add(rc["replaces"])
    for j in jobs:
        key = str((j.payload or {}).get("idempotency_key") or "")
        if key.startswith("replace-"):
            out.add(key[len("replace-"):])
    return out


def leaf_receipts(recs, jobs) -> list:
    """Орлогдоогүй (сүүлийн үеийн) мөрүүд — төлбөрийн одоогийн баримт(ууд)."""
    gone = superseded_ids(recs, jobs)
    return [r for r in recs if r.id not in gone]


def never_sent(rec, jobs) -> bool:
    """Энэ мөрийн хүсэлт сувагт ОГТ хүрээгүй (claim attempts-ийг өсгөдөг тул attempts 0 =
    илгээгээгүй) — хүлээн авагчийг солих нь аюулгүй."""
    return (bool(jobs) and all(j.attempts == 0 and j.status in ("PENDING", "BLOCKED") for j in jobs)
            and not (rec.ebarimt_id or "").strip() and not (rec.provider_ref or "").strip()
            and rec.status in ("PENDING", "FAILED"))


def definitely_rejected(rec, jobs) -> bool:
    """Сувгийн ТОДОРХОЙ татгалзал: ажил REJECTED (HTTP 400/404/409/422 create-д), мөр FAILED,
    ДДТД/billId/provider_ref хэзээ ч ирээгүй, qpay_review-ийн тодорхойгүй оролдлогогүй."""
    if not jobs or rec.status != "FAILED" or any(j.status != REJECTED_JOB for j in jobs):
        return False
    if (rec.ebarimt_id or "").strip() or (rec.provider_ref or "").strip():
        return False
    raw = rec.raw if isinstance(rec.raw, dict) else {}
    if any(isinstance(v, dict) and (v.get("invalid_bill_id") or v.get("bill_id")) for v in raw.values()):
        return False
    attempts = raw.get("qpay_review_attempts") or []
    return all(isinstance(a, dict) and a.get("outcome") in ("REJECTED", "NOT_CALLED") for a in attempts)


def _next_rcv_index(payment_id, jobs) -> int:
    pat = re.compile(rf"^pay-{re.escape(str(payment_id))}-rcv-(\d+)")
    seen = [int(m.group(1)) for j in jobs
            for m in [pat.match(str((j.payload or {}).get("idempotency_key") or ""))] if m]
    return max(seen, default=0) + 1


def change_receiver(db, payment, *, customer_tin, receiver_type, username="system",
                    allow_duplicate=False, mark=None) -> dict:
    """Хүлээн авагчийг (ААН регистр/ТТД ↔ иргэн) солиж ШИНЭ баримт гаргана. Commit хийхгүй.

    Буцаах: {"ok": False, "pending": True, "receiver_changed": True, receipts, replaced, keys,
    from, to} эсвэл {"ok": False, "conflict": True, "error"} (дуудагч 409, rollback).
    Хуучин мөр: CANCELLED хэвээр (raw.superseded_by), ТАТГАЛЗСАН/илгээгээгүй мөр нь CANCELLED
    («баримт үүсээгүй» тэмдэгтэй) болно — нэг төлбөрт НЭГ л идэвхтэй баримт (мөр бүрд)."""
    db.query(Payment).enable_eagerloads(False).filter_by(id=payment.id).with_for_update().one()
    jobs = (db.query(FinancialJob).filter(FinancialJob.payment_id == payment.id,
            FinancialJob.kind == "RECEIPT").order_by(FinancialJob.id)
            .populate_existing().with_for_update().all())
    recs = (db.query(VatReceipt).filter_by(payment_id=payment.id)
            .order_by(VatReceipt.created_at, VatReceipt.id).populate_existing().with_for_update().all())
    if not recs:
        return {"ok": False, "conflict": True,
                "error": "Энэ төлбөрт НӨАТ баримтын мөр алга — санхүү тулгана уу"}
    if not allow_duplicate and duplicate_guarded(recs):
        return {**DUPLICATE_REFUSAL, "conflict": True}
    by_rec = _jobs_by_receipt(jobs)
    reasons, blockers = {}, []
    for r in recs:
        js = by_rec.get(r.id, [])
        if r.status == "CANCELLED":
            reasons[r.id] = "CANCELLED"
        elif definitely_rejected(r, js):
            reasons[r.id] = error_class(r) or ERR_REJECTED
        elif never_sent(r, js):
            reasons[r.id] = "NOT_SENT"
        else:
            blockers.append({"receipt_id": r.id, "status": r.status,
                             "jobs": [f"{j.status}/{j.attempts}" for j in js]})
    if blockers:
        return {"ok": False, "conflict": True, "error": RECEIVER_LOCKED, "blockers": blockers}
    leaves = leaf_receipts(recs, jobs)
    if not leaves:
        return {"ok": False, "conflict": True,
                "error": "Орлох баримтын мөр тодорхойгүй — санхүү тулгана уу"}
    old = {"customer_tin": payment.customer_tin, "receiver_type": payment.ebarimt_receiver_type}
    new = {"customer_tin": customer_tin, "receiver_type": receiver_type}
    payment.customer_tin, payment.ebarimt_receiver_type = customer_tin, receiver_type
    now = datetime.utcnow().isoformat(timespec="seconds")
    n = _next_rcv_index(payment.id, jobs)
    from ..routers.payments_router import _receipt_desc
    created, keys = [], []
    for i, leaf in enumerate(leaves):
        key = RCV_KEY.format(pid=payment.id, n=n) + (f"-{i + 1}" if i else "")
        prev_job = (by_rec.get(leaf.id) or [None])[0]
        desc = ((prev_job.payload or {}).get("description") if prev_job is not None else None) \
            or _receipt_desc(payment)
        # terminal=False: өмнө нь СИСТЕМ гаргасан баримтыг орлож байгаа — «POS терминал
        # гаргадаг» дүрмээр EXTERNAL (баримтгүй) болгохгүй
        rec = enqueue_receipt(db, payment, amount=leaf.amount, vat=leaf.vat_amount,
                              session_id=leaf.session_id, description=desc, key=key, terminal=False)
        rec.raw = {**(rec.raw if isinstance(rec.raw, dict) else {}),
                   "receiver_change": {"replaces": leaf.id, "from": old, "to": new,
                                       "reason": reasons.get(leaf.id), "by": username, "at": now},
                   # allow_duplicate/no_terminal — дараагийн --pos-app цуцлалт дахин хөндөхгүй
                   **({"reissue": dict(mark)} if mark else {})}
        created.append(rec)
        keys.append(key)
        leaf.raw = {**(leaf.raw if isinstance(leaf.raw, dict) else {}), "superseded_by": rec.id}
    for r in recs:
        if r.status == "CANCELLED":
            continue
        # ТАТГАЛЗСАН/илгээгээгүй мөр — баримт ҮҮСЭЭГҮЙ; идэвхгүй болгоно (ТЕГ тулгалт,
        # бөөн нөхөлт, «Дахин үүсгэх» дахин хөндөхгүй)
        repl = next((c.id for c in created
                     if (c.raw or {}).get("receiver_change", {}).get("replaces") == r.id), None)
        r.status = "CANCELLED"
        r.receipt_url = (f"Хүлээн авагч солигдсон — энэ хүсэлтээр баримт ҮҮСЭЭГҮЙ "
                         f"({reasons.get(r.id)}); шинэ мөр {(repl or created[0].id)[:8]}")[:200]
        r.raw = {**(r.raw if isinstance(r.raw, dict) else {}),
                 "superseded": {"reason": "RECEIVER_CHANGE", "never_issued": True,
                                "class": reasons.get(r.id), "by": username, "at": now}}
        for j in by_rec.get(r.id, []):
            # Илгээгээгүй ажил хэзээ ч илгээгдэхгүй; татгалзсан ажил шийдэгдсэн (тулгалтын
            # жагсаалтад «дуусаагүй» гэж үлдэхгүй) — сувгийн алдааны текст хэвээр
            if j.status in ("PENDING", "BLOCKED"):
                j.status, j.last_error = "DONE", "Хүлээн авагч солигдсон — илгээгээгүй"
            elif j.status == REJECTED_JOB:
                j.status = "DONE"
                j.last_error = f"Хүлээн авагч солигдсон — {j.last_error or REJECTED_JOB}"[:ERROR_TEXT_MAX + 40]
            j.lease_token, j.lease_until = None, None
    return {"ok": False, "pending": True, "receiver_changed": True,
            "error": "Шинэ хүлээн авагчаар баримтын ажил хадгалагдлаа — дахин төлөхгүй",
            "receipts": [r.id for r in created], "replaced": [r.id for r in leaves],
            "keys": keys, "reasons": reasons, "from": old, "to": new}


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
        # Орлогдсон мөрийг (replace-/хүлээн авагч солилт, 2026-10-16) тооцохгүй — сүүлийн
        # үеийн НЭГ мөр л байвал түүнийг орлоно
        leaves = leaf_receipts(recs, jobs)
        if len(leaves) != 1:
            return {"ok": False, "review_required": True,
                    "error": "Олон цуцалсан баримтын дүнг эхлээд санхүү тулгана уу"}
        # Cancellation is already confirmed. Explicit operator request creates
        # replacement rows with new stable keys, preserving every old receipt.
        new = [enqueue_receipt(db, payment, amount=rec.amount, vat=rec.vat_amount,
                               session_id=rec.session_id, key=f"replace-{rec.id}",
                               terminal=False if no_terminal_receipt else None)
               for rec in leaves]
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
    pending_jobs = []
    for job in jobs:
        rec = db.get(VatReceipt, job.receipt_id)
        if rec and (rec.ebarimt_id or rec.status in (TERMINAL_STATUS, "CANCELLED")):
            # CANCELLED: цуцалсан эсвэл хүлээн авагч солигдож орлогдсон (баримтгүй) мөр —
            # түүний ажлыг дахин илгээхгүй
            continue
        if job.status == "UNKNOWN":
            # Аль нэг мөрийн үр дүн тодорхойгүй бол юуг ч (татгалзсан мөрийг ч) хөдөлгөхгүй
            return {"ok": False, "review_required": True,
                    "error": "Баримт өмнө үүссэн байж болно. Сувгаар тулгаж ДДТД-г батална уу; дахин илгээгээгүй"}
        pending_jobs.append((job, rec))
    for job, rec in pending_jobs:
        if job.status == REJECTED_JOB:
            # Суваг хүсэлтийг ТОДОРХОЙ татгалзсан (HTTP 400/404/409/422) — баримт үүсээгүй
            # тул ИЖИЛ ажлыг (ижил түлхүүр/хүлээн авагч) дахин илгээх нь аюулгүй. Оператор
            # «Дахин үүсгэх»/бөөн нөхөлтөөр л (ж: QPay-ийн мерчант талын түр татгалзал);
            # дугаар буруу бол «Дугаар засах»/«Иргэнээр» (change_receiver) — шинэ мөр.
            job.status, job.next_attempt_at = "PENDING", datetime.utcnow()
            job.lease_token, job.lease_until = None, None
            if rec is not None:
                rec.status, rec.receipt_url = "PENDING", None
            continue
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
    # creating — баримт ҮҮСГЭХ хүсэлт илгээсэн эсэх (GET-ийн алдаа татгалзал биш)
    err_class, err_status, creating = None, None, False
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
                creating = True
                result = await msgbill.create_receipt(acc, float(Decimal(data["amount"])),
                    description=data["description"], payment_method=data["method"],
                    idempotency_key=data["idempotency_key"], payer_reg_no=data["customer_tin"])
        elif data["provider"] == "QPAY":
            creating = True
            result = await qpay.create_ebarimt(data["provider_payment_id"], data["receiver_type"],
                receiver=data["customer_tin"] if data["receiver_type"] == "COMPANY" else None, acc=acc)
        else:
            result = await ebarimt.create_receipt(float(Decimal(data["amount"])),
                float(Decimal(data["vat"])),
                "CASH" if data["method"] in ("CASH", "TRANSFER") else "CARD",
                customer_tin=data["customer_tin"], merchant=acc)
        if kind == "RECEIPT" and not result.get("billId"):
            # 200 ч ДДТД-гүй (msgbill state FAILED/PENDING, QPay billId-гүй) — ТОДОРХОЙГҮЙ хэвээр,
            # гэхдээ сувгийн өөрийн шалтгааныг (msgbill `error`) нууж хаяхгүй
            error = "ДДТД хараахан баталгаажаагүй; сувгийн төлөвийг тулгана уу"
            if result.get("error") or result.get("state"):
                error = safe_text(f"{error} (суваг: {result.get('state') or '?'}"
                                  f"{': ' + safe_text(result.get('error')) if result.get('error') else ''})")
    except Exception as exc:
        if kind == "RECEIPT":
            # HTTP статус + цэвэрлэсэн хариу (нууцгүй) — сүлжээний алдаанд зөвхөн ангийн нэр
            error, err_class, err_status = provider_error(exc, data.get("provider"), creating=creating)
        else:
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
            assigned = assign_ebarimt_id(db, rec, result["billId"], source="outbox",
                lottery=None if data.get("receiver_type") == "COMPANY" else result.get("lottery"),
                raw={**{k: (result.get("raw") or {}).get(k) for k in
                        ("id", "receipt_no", "state", "barimt_status", "ebarimt_receipt_id")},
                     # QR ирсэн эсэх (2026-10-15) — QR-ыг ӨӨРИЙГ нь хэзээ ч DB-д бичихгүй
                     # (ТЕГ №11); «QR хэвлэгдэхгүй» гомдлыг сувгийн хариунаас ялгах нотолгоо
                     "has_qr": bool(result.get("qrData"))})
            rec.status, rec.receipt_url = "SENT", None
            if assigned and result.get("qrData"):
                # Түлхүүр = баримт (өрийн баримт үндсэн баримтын QR-ыг дарахгүй)
                ebarimt.cache_qr(rec.id, result.get("qrData"))
            elif not result.get("qrData"):
                log.warning("баримт %s: суваг (%s) ДДТД өгсөн ч QR өгсөнгүй — /print msgbill-ээс "
                            "нөхнө", rec.id, data.get("provider"))
        # A webhook may have completed the receipt while the request timed out.
        if rec and rec.ebarimt_id:
            error = None
            enqueue_receipt_ready(db, db.get(Payment, job.payment_id), rec)
        rejected = bool(error) and err_class in (ERR_REJECTED, ERR_REJECTED_RECEIVER)
        if error:
            if rejected:
                # Суваг хүсэлтийг ТАТГАЛЗСАН — баримт үүсээгүй нь мэдэгдэж буй. Автоматаар
                # давтахгүй (ижил дугаар дахин унана): оператор «Дугаар засах»/«Иргэнээр»/
                # «Дахин үүсгэх»-ээр шийднэ (retry_payment_receipts, change_receiver).
                job.status = REJECTED_JOB
            else:
                job.status = "RETRY" if safe_retry and job.attempts < 24 else "UNKNOWN"
                job.next_attempt_at = datetime.utcnow() + timedelta(seconds=min(3600, 30 * 2**min(job.attempts, 7)))
            job.last_error = error
            if rec and not rec.ebarimt_id:
                rec.status = "FAILED" if rejected else ("PENDING" if job.status == "RETRY" else "REVIEW")
                rec.receipt_url = error
                if kind == "RECEIPT":
                    # Ангилал UI-д (Vat.jsx «Дугаар засах» — текстийн regex биш) ба retry-д
                    rec.raw = {**(rec.raw if isinstance(rec.raw, dict) else {}),
                               "error": {"class": err_class or ERR_UNCERTAIN,
                                         "provider": data.get("provider"), "http_status": err_status,
                                         "text": error, "job_status": job.status,
                                         "attempt": job.attempts,
                                         "at": datetime.utcnow().isoformat(timespec="seconds")}}
        else:
            job.status, job.last_error = "DONE", None
        job.lease_until, job.lease_token = None, None
        db.add(AuditLog(username="financial-worker", action="FINANCIAL_JOB_RESULT",
            entity="payment", entity_id=job.payment_id,
            detail={"job_id": job.id, "kind": job.kind, "status": job.status,
                    **({"error": (error or "")[:200], "error_class": err_class or ERR_UNCERTAIN,
                        "http_status": err_status} if error and kind == "RECEIPT" else {})}))
        db.commit()


async def run_once(factory=SessionLocal, limit=20):
    for _ in range(limit):
        with factory() as db:
            claim = claim_one(db)
        if claim is None:
            break
        if claim != "quarantined":
            # Бүртгэлтэй task — POS-ийн confirm/cash ижил ажлыг давхар илгээхгүй, харин
            # дуусахыг нь хүлээж чадна (deliver_now)
            await _track(claim[0], deliver(*claim, factory=factory))


# ── POS АППЫН БАРИМТЫГ ХАРИУНД НЬ (2026-10-15) ────────────────────────────────
# PAX аппын шинэ хувилбар e-Barimt-ыг ЗӨВХӨН манай API-аас хэвлэдэг (Ontime-гүй). Outbox
# 5с тутам ажилладаг тул /pos/confirm, /cash-ийн хариу ДДТД/сугалаа/QR-гүй (PENDING)
# буцдаг байв — QR нь зөвхөн outbox-ын POST хариунаас түр санах ойд орно. Одоо мөнгө
# commit болсны ДАРАА ЭНЭ төлбөрийн msgbill ажлыг ижил lease-ээр авч (claim) шууд
# илгээж, ≤6с хүлээнэ. Хүлээлт дуусвал task-ийг ЗОГСООХГҮЙ (HTTP-г тасалбал баримт
# үүссэн эсэх тодорхойгүй болно) — task өөрөө дуусгаж бичнэ, апп /print-ээр дахин татна.
# Давхар илгээлтээс: ажлыг FOR UPDATE SKIP LOCKED + SENDING lease-ээр авна (worker-тэй
# нэг механизм); lease-тэй ажлыг worker (claim_one) алгасна; процесс унавал lease 3 мин
# дуусч worker ИЖИЛ Idempotency-Key-ээр нөхнө (msgbill — давхар баримт үүсэхгүй).
# Зөвхөн MSGBILL: QPay/PosAPI-ийн дахин илгээлт идемпотент биш (claim_one тусгаарлана).
INLINE_PROVIDERS = frozenset({"MSGBILL"})
INLINE_MAX_SEC = 6.0
_INFLIGHT: dict = {}   # job_id → asyncio.Task (энэ процесс дахь илгээж буй ажил)


def _track(job_id, coro):
    """deliver-ийг task болгож бүртгэнэ — өөр хүсэлт дуусахыг нь хүлээж чадна. Хүчтэй
    лавлагаа (dict) нь task-ийг хог цуглуулагчаас хамгаална."""
    task = asyncio.ensure_future(coro)
    _INFLIGHT[job_id] = task

    def _done(t, key=job_id):
        if _INFLIGHT.get(key) is t:
            _INFLIGHT.pop(key, None)
        if not t.cancelled() and t.exception() is not None:
            log.error("баримтын ажил %s алдаатай дуусав: %r", key, t.exception())
    task.add_done_callback(_done)
    return task


def session_factory(db):
    """Хүсэлтийн session-тэй ИЖИЛ DB (engine) — тестийн SQLite/прод Postgres аль аль нь."""
    from sqlalchemy.orm import Session
    bind = db.get_bind()
    return lambda: Session(bind=bind, autoflush=False)


def claim_payment_jobs(db, payment_id, now=None) -> tuple[list, list]:
    """ЭНЭ төлбөрийн илгээх боломжтой msgbill баримтын ажлуудыг SENDING lease-ээр авна.

    Буцаах: (claims [(job_id, lease_token)], илгээгдэж буй [job_id]). claim_one-той ижил
    дүрэм: PENDING эсвэл хугацаа нь болсон RETRY; өөр процесс/worker түгжсэнийг алгасна
    (SKIP LOCKED); BLOCKED/UNKNOWN/DONE-д хүрэхгүй (санхүүгийн тулгалт шаардлагатай)."""
    now = now or datetime.utcnow()
    jobs = (db.query(FinancialJob)
            .filter(FinancialJob.payment_id == payment_id, FinancialJob.kind == "RECEIPT",
                    or_(FinancialJob.status == "PENDING",
                        (FinancialJob.status == "RETRY") & (FinancialJob.next_attempt_at <= now),
                        FinancialJob.status == "SENDING"))
            .order_by(FinancialJob.created_at, FinancialJob.id)
            .with_for_update(skip_locked=True).all())
    claims, sending = [], []
    for job in jobs:
        if (job.payload or {}).get("provider") not in INLINE_PROVIDERS:
            continue
        if job.status == "SENDING":
            sending.append(job.id)
            continue
        job.status = "SENDING"
        job.attempts += 1
        job.lease_token = str(uuid.uuid4())
        job.lease_until = now + timedelta(minutes=3)
        claims.append((job.id, job.lease_token))
    db.commit()
    return claims, sending


def inline_budget(value=None) -> float:
    """Шууд илгээлтийн хүлээлтийн төсөв (сек): тохиргоо → [0, INLINE_MAX_SEC]."""
    try:
        v = float(settings.receipt_inline_wait_sec if value is None else value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(v, INLINE_MAX_SEC)) if v == v else 0.0   # NaN → 0


async def deliver_now(db, payment_id, *, budget=None, factory=None) -> dict:
    """Мөнгө commit болсны ДАРАА энэ төлбөрийн баримтыг шууд илгээж ≤budget сек хүлээнэ.

    Хэзээ ч алдаа шидэхгүй (төлбөр, хаалт аль хэдийн болсон) — үр дүнг request-ийн session
    дахин уншина. budget анхдагч settings.receipt_inline_wait_sec (≤6с; 0 = унтраах).
    Буцаах: {claimed, awaited, pending, ms} — логт/тестэд."""
    import time as _t
    t0 = _t.monotonic()
    budget = inline_budget(budget)
    out = {"claimed": 0, "awaited": 0, "pending": 0, "ms": 0}
    if budget <= 0 or not payment_id:
        return out
    factory = factory or session_factory(db)
    try:
        with factory() as s:
            claims, sending = claim_payment_jobs(s, payment_id)
    except Exception:  # noqa: BLE001 — баримт worker-ээр нөхөгдөнө
        log.exception("шууд илгээх ажил авч чадсангүй (payment=%s)", payment_id)
        return out
    tasks = [_track(job_id, deliver(job_id, token, factory=factory)) for job_id, token in claims]
    # Worker эсвэл давтан confirm аль хэдийн илгээж байгаа бол дуусахыг нь хүлээнэ
    loop = asyncio.get_running_loop()
    tasks += [_INFLIGHT[j] for j in sending
              if j in _INFLIGHT and _INFLIGHT[j].get_loop() is loop and not _INFLIGHT[j].done()]
    out["claimed"], out["awaited"] = len(claims), len(tasks)
    if tasks:
        remaining = max(0.0, budget - (_t.monotonic() - t0))
        # asyncio.wait хугацаа дуусахад task-ийг ЦУЦЛАХГҮЙ — тэд lease-ээ барьсаар дуусна
        _done, pending = await asyncio.wait(tasks, timeout=remaining)
        out["pending"] = len(pending)
    out["ms"] = int((_t.monotonic() - t0) * 1000)
    if out["pending"]:
        log.warning("баримт %dмс-д бэлэн болсонгүй (payment=%s) — апп /print-ээр дахин татна",
                    out["ms"], payment_id)
    return out


# QR нөхөлт: msgbill-ийн SENT баримтад QR санах ойд алга (POST хариунд QR ирээгүй, webhook-оор
# дууссан, процесс restart, 1 цаг өнгөрсөн) бол msgbill GET /partner/receipts/{id}-ээс татна.
# Хяналтын засвар (2026-10-15): татах ажил нь БИЕ ДААСАН task — дуудагчийн төсөв (confirm-ийн
# үлдсэн хугацаа) дуусах/цуцлагдах нь татлагыг тасалдуулахгүй, QR ирмэгц кэшлэгдэнэ. ЗЭРЭГ
# хүсэлтүүд нэг task-ийг хүлээнэ (давхар GET үгүй). Бүтэлгүй/QR-гүй хариуны дараа ЗӨВХӨН
# QR_RETRY_SEC (2с) дотор дахин татахгүй — өмнө нь 60с түгжиж, аппын /print-ийн давталт QR-гүй
# хэвлэдэг байв. Цуцлагдсан (процесс зогссон) оролдлого бүртгэгдэхгүй.
QR_FETCH_TIMEOUT = 3.0
QR_RETRY_SEC = 2.0
QR_LIST_MAX_FETCH = 10      # /pos/receipts нэг хүсэлтэд QR татах дээд тоо
_qr_fetching: dict = {}     # receipt_id → asyncio.Task (одоо татаж буй)
_qr_failed: dict = {}       # receipt_id → сүүлийн бүтэлгүй/QR-гүй хариуны monotonic


def qr_plan(db, payment, rec=None) -> dict | None:
    """SYNC (DB): хэвлэх баримтын QR-ыг татах шаардлагатай юу. None = татах зүйлгүй (QR кэштэй,
    SENT биш, msgbill биш, provider_ref/түлхүүргүй). Түлхүүрүүдийг ЭНД (request-ийн session-д)
    бодно — арын task DB-д хүрэхгүй."""
    from .receipts import print_receipt, receipt_qr
    rec = rec if rec is not None else print_receipt(db, payment)
    if rec is None or rec.status != "SENT" or not rec.ebarimt_id or receipt_qr(rec):
        return None
    if (rec.provider or "") != "MSGBILL" or not rec.provider_ref:
        return None
    site = payment_site(payment)
    accounts = msgbill.cancel_accounts(site)
    if not accounts:
        return None
    return {"receipt_id": rec.id, "ref": rec.provider_ref, "ddtd": rec.ebarimt_id,
            "payment_id": payment.id, "site": site, "accounts": accounts}


async def _fetch_qr(plan) -> str | None:
    import time as _t
    rid, ref, ddtd = plan["receipt_id"], plan["ref"], plan["ddtd"]
    try:
        res = await msgbill.get_receipt_any(plan["site"], ref, timeout=QR_FETCH_TIMEOUT,
                                            accounts=plan["accounts"])
    except Exception as exc:  # noqa: BLE001 — msgbill унасан/удаан: QR-гүй хэвлэнэ
        _qr_failed[rid] = _t.monotonic()
        log.warning("QR татаж чадсангүй (payment=%s): %s", plan["payment_id"], type(exc).__name__)
        return None
    qr, no = res.get("qrData"), res.get("billId")
    if not qr or (res.get("state") or "").upper() != "CREATED":
        _qr_failed[rid] = _t.monotonic()
        log.info("msgbill %s QR өгсөнгүй (state=%s)", ref, res.get("state"))
        return None
    if no and no != ddtd:
        _qr_failed[rid] = _t.monotonic()
        log.error("QR татахад ДДТД зөрөв payment=%s: msgbill %s ≠ хадгалсан %s — QR ашиглахгүй",
                  plan["payment_id"], no, ddtd)
        return None
    _qr_failed.pop(rid, None)
    ebarimt.cache_qr(rid, qr)
    return qr


async def fetch_qr(plan, *, timeout: float = QR_FETCH_TIMEOUT) -> str | None:
    """qr_plan-ийн дагуу QR татна (эсвэл татаж буйг хүлээнэ) ≤timeout сек. timeout 0 = арын
    горимд эхлүүлээд шууд буцна (дараагийн /print кэшээс авна). Хэзээ ч алдаа шидэхгүй."""
    import time as _t
    if not plan:
        return None
    rid = plan["receipt_id"]
    loop = asyncio.get_running_loop()
    task = _qr_fetching.get(rid)
    if task is None or task.done() or task.get_loop() is not loop:
        last = _qr_failed.get(rid)
        if last is not None and _t.monotonic() - last < QR_RETRY_SEC:
            return None
        if len(_qr_failed) > 5000:
            _qr_failed.clear()
        task = asyncio.ensure_future(_fetch_qr(plan))
        _qr_fetching[rid] = task
        task.add_done_callback(lambda t, k=rid: _qr_fetching.pop(k, None)
                               if _qr_fetching.get(k) is t else None)
    if timeout and timeout > 0:
        # wait нь task-ийг ЦУЦЛАХГҮЙ — төсөв дууссан ч татлага үргэлжилж кэшилнэ
        await asyncio.wait([task], timeout=timeout)
    return ebarimt.get_cached_qr(rid)


async def ensure_qr(db, payment, rec=None, *, timeout: float = QR_FETCH_TIMEOUT) -> str | None:
    """Хэвлэх баримтын QR (санах ойд байхгүй бол msgbill-ээс татаж кэшилнэ — DB-д ХЭЗЭЭ Ч үгүй,
    ТЕГ №11). ЗӨВХӨН SENT, MSGBILL, provider_ref-тэй баримтад; msgbill-ийн дугаар хадгалсан
    ДДТД-ээс өөр бол QR-ыг ашиглахгүй. Алдаа/удаашрал хэвлэлтийг унагаахгүй (QR-гүй хэвлэнэ)."""
    from .receipts import print_receipt, receipt_qr
    try:
        rec = rec if rec is not None else print_receipt(db, payment)
        cached = receipt_qr(rec)
        if cached:
            return cached
        plan = qr_plan(db, payment, rec)
    except Exception:  # noqa: BLE001
        log.exception("QR төлөвлөж чадсангүй (payment=%s)", getattr(payment, "id", None))
        return None
    return await fetch_qr(plan, timeout=timeout)


async def supervisor():
    await asyncio.sleep(5)
    while True:
        try:
            await run_once()
        except Exception:
            log.exception("financial outbox pass failed")
        await asyncio.sleep(5)
