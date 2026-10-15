"""QPay e-Barimt-ын REVIEW (тодорхойгүй) баримтыг нөхөх — ebarimt-diag-тай НЭГ код зам (2026-10-15).

Яагаад: 10-р сард EasyParking-ийн ~40 QR төлбөр (≈119,500₮) QPAY баримт нь REVIEW төлөвт
гацсан — outbox-ын анхны ebarimt_v3/create HTTPStatusError буцаасан тул ажил UNKNOWN болж
(financial_jobs.deliver: QPay create-ийн дахин илгээлт идемпотент гэж БАТЛАГДААГҮЙ тул
хорионд авна), «Дахин үүсгэх» review_required өгдөг. Ганц зам нь SUPER_ADMIN-ий
`POST /api/admin/qpay/ebarimt-diag {payment_id, create:true}` байв. Энэ модуль тэр
endpoint-ийн create хэсгийг (`create_and_assign`) НЭГ функц болгож гаргасан — endpoint
болон `tools/qpay_review_resolve.py` хоёулаа ЯГ ЭНЭ функцийг дуудна (endpoint-ийн хариу,
зан төлөв өөрчлөгдөөгүй).

ИДЕМПОТЕНТ ЭСЭХ — КОДООС БАТЛАХ БОЛОМЖГҮЙ (тиймээс --apply нь
--i-understand-not-idempotent шаардана):
  • Дуудлага: `qpay.create_ebarimt` → POST /v2/ebarimt_v3/create, биет {payment_id,
    ebarimt_receiver_type, ebarimt_receiver?, district_code}. Цорын ганц «түлхүүр» нь
    QPay-ийн g_payment_id (`payments.provider_payment_id`); Idempotency-Key толгой, хүсэлтийн
    ID огт илгээдэггүй. QPay-ийн спек (2026.3.17 V2 + Ebarimt 3.0) идемпотентийн гэрээ
    заагаагүй. `qpay._api` нь POST create-г тодорхойгүй алдаан дээр ДАВТДАГГҮЙ, outbox нь
    UNKNOWN болгодог — код өөрөө «идемпотент биш» гэж үздэг.
  • Урьдчилан ШАЛГАХ (GET) зам алга: манай QPay клиентэд ebarimt-ийг унших endpoint
    хэрэгжээгүй; `payment/check`-ийн мөрүүд (audit1014-д хадгалсан 60,885 мөр) зөвхөн
    `ebarimt_customer_no`-той, баримтын дугаар/ID агуулдаггүй — «өмнө үүссэн баримтыг
    татах» боломжгүй. Үүний оронд ТЕГ экспорт (--file) нь «баримт аль хэдийн бий» эсэхийн
    нотолгоо (`teg_evidence`): ТЕГ-т бий бол create ХИЙХГҮЙ, ТЕГ-ийн ДДТД-г холбоно.
  • Ажиглалт (prod-ын audit1014 хуулбар): ДАРААЛСАН дуудлага — 09-01 diag
    (QPay payment 781636913529316) 05:00:25 ба 05:03:45-д хоёулаа амжилттай, хадгалсан
    (2-р дуудлагын) ДДТД …340020910014863 = 1-рийнх (санамжийн тэмдэглэл); ЗЭРЭГЦЭЭ (webhook+polling-ийн хуучин уралдаан) 4 төлбөрт хоёр
    дуудлага ИЖИЛ ДДТД (2-т нь ижил QPay ebarimt id f08cf968…, bee567ad…) буцаасан.
    ГЭХДЭЭ 2026-08-19 15:46:04 (payment f7bb0ae7…, QPay payment 419498722217504) зэрэгцээ
    2 дуудлага 2 ӨӨР баримт (…0757/…0758 10014863, хоёулаа SENT) үүсгэсэн — QPay талд
    идемпотент нь баталгаатай биш (дор хаяж зэрэгцээ үед).
  • Тиймээс энэ зам давхар баримтаас ингэж сэргийлнэ: (1) төлбөрийн мөрийг FOR UPDATE
    NOWAIT (+ ажил, баримтын мөр FOR UPDATE)-ээр QPay дуудлагын турш түгжинэ — энэ хэрэгсэл,
    retry-ebarimt, баримт холбох нэг төлбөр дээр ЗЭРЭГ ажиллахгүй (diag endpoint түгждэггүй —
    түүнийг зэрэг бүү ажиллуул); (2) түгжээний дотор дахин шалгана (REVIEW, ДДТД-гүй, өөр
    идэвхтэй баримтгүй, ажил SENDING биш); (3) нэг ажиллагаанд төлбөр бүрд НЭГ л create,
    дараалан; (4) амжилтын дараа SENT + ажил DONE → дахин ажиллуулахад/outbox/retry
    алгасна; (5) ТЕГ-т байгаа нь (IN_TEG) хэзээ ч create хийхгүй.

customer_tin-ийг ХЭЗЭЭ Ч өөрчлөхгүй: ААН-д `ebarimt_receiver` = төлбөрийн customer_tin
(QPay 7 оронтой регистр л хүлээн авдаг — MAX 7, 1c114f3); QPAY-ээс бусад суваг, msgbill
баримт ХӨНДӨХГҮЙ.
"""
from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timedelta

from sqlalchemy import func

from ..config import settings
from ..models import (AuditLog, FinancialJob, ParkingSession, ParkingSite, Payment, Tenant,
                      VatReceipt)

log = logging.getLogger("parking.qpay_review")

QPAY_REGISTER = "10014863"          # ТЕГ-ийн ДДТД-ийн сүүлийн 8 орон = QPay-ийн касс
DIAG_SOURCE = "qpay-diag"           # ebarimt-diag endpoint-ийн raw/аудитын шошго (хуучин)
TOOL_SOURCE = "qpay-review-resolve"
TEG_SOURCE = "teg:qpay-review-resolve"
AUDIT_ACTION = "EBARIMT_QPAY_REVIEW_RESOLVE"
SUMMARY_ACTION = "EBARIMT_QPAY_REVIEW_SUMMARY"
DEFAULT_TOL = 60                    # vat_recon.DEFAULT_TOL — манай баримт ↔ ТЕГ мөр (сек)
TEG_BEFORE = timedelta(minutes=20)  # ТЕГ мөр paid_at-аас −20 мин …
TEG_AFTER = timedelta(minutes=5)    # … +5 мин (QPay касс UTC)
TEG_TENANT_SHARE = 0.5              # файлын QPay мөрийн ≥50% нь нэг түрээслэгчийн баримттай таарах
JOB_OK = (None, "UNKNOWN")          # ажилгүй (хуучин мөр) эсвэл хорионд (UNKNOWN)

PAY_SITE = func.coalesce(Payment.site_id, ParkingSession.site_id)

SKIP_MN = {
    "NOT_PAID": "төлбөр PAID биш",
    "NOT_QPAY": "баримт QPAY биш — хөндөхгүй",
    "STATUS": "баримтын төлөв өөрчлөгдсөн (REVIEW/FAILED биш)",
    "HAS_DDTD": "баримт аль хэдийн ДДТД-тэй",
    "HAS_RECEIPT": "төлбөрт өөр идэвхтэй баримт (ДДТД-тэй/терминалын) бий",
    "MULTI_RECEIPT": "нэг төлбөрт ДДТД-гүй QPAY мөр олон — санхүү тулгана",
    "NO_QPAY_ID": "QPay payment_id (provider_payment_id) алга — create хийх боломжгүй",
    "AMOUNT_MISMATCH": "баримтын дүн ≠ төлбөрийн дүн (QPay бүтэн дүнгээр гаргана)",
    "JOB_SENDING": "баримтын ажил одоо илгээгдэж байна",
    "JOB_BLOCKED": "тохиргоогоор хаагдсан FAILED (BLOCKED ажил) — эхлээд тохиргоо засна",
    "JOB_STATE": "баримтын ажлын төлөв хачин (UNKNOWN/ажилгүй биш)",
    "MISSING": "баримт олдсонгүй",
    "QPAY_ID_SHARED": "өөр төлбөр ижил QPay payment_id-тэй — нэг QPay төлбөрт нэг баримт, гараар тулгана",
}
VERDICT_MN = {
    "NO_FILE": "ТЕГ файл өгөөгүй",
    "NOT_IN_TEG": "ТЕГ-т алга — QPay-ээр create",
    "IN_TEG": "ТЕГ-т БИЙ — create ХИЙХГҮЙ, ТЕГ-ийн ДДТД-г холбоно",
    "IN_TEG_AMBIG": "ТЕГ-т бий бололтой, гэхдээ аль мөр нь вэ тодорхойгүй — гараар",
    "AMBIGUOUS": "ТОДОРХОЙГҮЙ (ойролцоо эзэнгүй мөр эсвэл өдрийн тооны зөрүү) — "
                 "--include-ambiguous-гүйгээр create хийхгүй",
    "OUTSIDE": "ТЕГ файлын хүрээнээс гадуур — дүгнэхгүй",
    "NOT_COVERED": "ТЕГ файл энэ түрээслэгчийнх биш — дүгнэхгүй",
}


def ub(at: datetime | None) -> datetime | None:
    return at + timedelta(hours=settings.tz_offset_hours) if at else None


def ub_day(at: datetime) -> str:
    return ub(at).date().isoformat()


def ub_window(date_from: str | None, date_to: str | None) -> tuple[datetime | None, datetime | None]:
    """УБ-ын огноо (YYYY-MM-DD, хоёулаа хамааруулна) → UTC [lo, hi). Өгөөгүй бол хязгааргүй."""
    off = timedelta(hours=settings.tz_offset_hours)
    lo = datetime.strptime(date_from, "%Y-%m-%d") - off if date_from else None
    hi = datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1) - off if date_to else None
    return lo, hi


# ───────────────────── ebarimt-diag-ийн create (НЭГ код зам) ─────────────────────

def receiver_args(payment) -> tuple[str, str | None]:
    """ebarimt-diag-ийн хүлээн авагч: төлбөрийн ebarimt_receiver_type, эс бол ТТД-тэй бол
    COMPANY. ААН-д receiver = customer_tin ЯГ байгаагаар (өөрчлөхгүй, хөрвүүлэхгүй)."""
    receiver_type = payment.ebarimt_receiver_type or (
        "COMPANY" if payment.customer_tin else "CITIZEN")
    return receiver_type, (payment.customer_tin if receiver_type == "COMPANY" else None)


async def create_and_assign(db, payment, rec, acc, *, username: str, source: str = DIAG_SOURCE,
                            commit: bool = True) -> dict:
    """`POST /api/admin/qpay/ebarimt-diag` (create=true)-ийн create хэсэг — 2026-10-15-нд
    endpoint-оос үг үсгээр нь гаргасан (endpoint-ийн хариу/зан төлөв өөрчлөгдөөгүй).

    QPay ebarimt_v3/create-г НЭГ удаа дуудаж түүхий хариуг буцаана. ДДТД ирвэл `rec`-д
    assign_ebarimt_id-ээр бичиж (SENT мөрийг өөр дугаараар дарахгүй) SENT болгоно.
    HTTP алдаа → {"ok": False, "http_status", "raw_body"} — баримтад ХҮРЭХГҮЙ (REVIEW хэвээр).
    commit=False — дуудагч (CLI) аудитын мөртэй НЭГ транзакцаар commit хийнэ."""
    import httpx as _httpx

    from . import qpay as qpay_svc
    t0 = datetime.utcnow()
    try:
        receiver_type, receiver = receiver_args(payment)
        eb = await qpay_svc.create_ebarimt(
            payment.provider_payment_id, receiver_type,
            receiver=receiver,
            acc=acc)
        res = {"ok": bool(eb.get("billId")), "raw": eb.get("raw"),
               "elapsed_ms": int((datetime.utcnow() - t0).total_seconds() * 1000)}
        if eb.get("billId") and rec:
            from .receipts import assign_ebarimt_id
            rec.lottery_code = None
            ok = assign_ebarimt_id(db, rec, eb["billId"], source=source,
                                   lottery=(None if receiver_type == "COMPANY" else eb.get("lottery")),
                                   username=username, raw=eb.get("raw"),
                                   allow_replace=rec.status != "SENT")
            if ok:
                rec.status = "SENT"
                rec.receipt_url = None
            if commit:
                db.commit()
            res["receipt_updated"] = ok
            if not ok:
                res["conflict"] = rec.ddtd_note
    except _httpx.HTTPStatusError as e:
        body_raw: object
        try:
            body_raw = e.response.json()
        except Exception:  # noqa: BLE001
            body_raw = e.response.text[:1000]
        res = {"ok": False, "http_status": e.response.status_code,
               "raw_body": body_raw,
               "elapsed_ms": int((datetime.utcnow() - t0).total_seconds() * 1000)}
    except Exception as e:  # noqa: BLE001
        res = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:300]}"}
    return res


def error_text(res: dict) -> str | None:
    """create_and_assign-ийн алдааг нэг мөр болгоно (аудит/хэвлэлтэд, ≤300)."""
    if res.get("ok"):
        return None if res.get("receipt_updated", True) else f"ДДТД зөрчил: {res.get('conflict')}"[:300]
    if res.get("http_status"):
        body = res.get("raw_body")
        if isinstance(body, dict):
            body = body.get("message") or body.get("error") or body
        return f"HTTP {res['http_status']}: {body}"[:300]
    return (res.get("error") or "QPay ДДТД буцаасангүй")[:300]


# ───────────────────────────── сонголт (READ ONLY) ─────────────────────────────

def skip_reason(payment, rec, recs: list, jobs: list, *, include_failed: bool = False) -> str | None:
    """Энэ баримтыг QPay-ээр нөхөж БОЛОХ уу — сонголт болон түгжээний доторх дахин шалгалт
    ХОЁУЛАА энэ нэг дүрмээр. None = болно, эс бөгөөс шалтгааны код (SKIP_MN)."""
    if rec is None:
        return "MISSING"
    if payment is None or payment.status != "PAID":
        return "NOT_PAID"
    if (rec.provider or "") != "QPAY":
        return "NOT_QPAY"
    if (rec.ebarimt_id or "").strip():
        return "HAS_DDTD"
    if rec.status not in (("REVIEW", "FAILED") if include_failed else ("REVIEW",)):
        return "STATUS"
    others = [r for r in recs if r.id != rec.id and r.status != "CANCELLED"]
    if any((r.ebarimt_id or "").strip() or r.status == "EXTERNAL" for r in others):
        return "HAS_RECEIPT"
    if any((r.provider or "") == "QPAY" for r in others):
        return "MULTI_RECEIPT"
    if not (payment.provider_payment_id or "").strip():
        return "NO_QPAY_ID"
    if abs(float(rec.amount or 0) - float(payment.amount or 0)) >= 0.005:
        return "AMOUNT_MISMATCH"
    job = next((j for j in jobs if j.receipt_id == rec.id), None)
    if any(j.status == "SENDING" for j in jobs):
        return "JOB_SENDING"
    status = job.status if job is not None else None
    if status == "BLOCKED":
        return "JOB_BLOCKED"
    if status not in JOB_OK:
        return "JOB_STATE"
    return None


def shared_qpay_payment(db, payment) -> bool:
    """Өөр төлбөрийн мөр ИЖИЛ QPay payment_id-тэй юу (давхар бүртгэгдсэн нэг QPay гүйлгээ) — тэгвэл
    QPay create нөгөөгийнх нь баримтыг буцаах/хоёр дахь баримт үүсгэх эрсдэлтэй тул хөндөхгүй."""
    pid = (payment.provider_payment_id or "").strip()
    return bool(pid) and db.query(Payment.id).filter(Payment.provider_payment_id == pid,
                                                     Payment.id != payment.id).first() is not None


def _site_info(db, payment):
    site = payment.site or (payment.session.site if payment.session else None)
    tenant = db.get(Tenant, site.tenant_id) if site is not None and site.tenant_id else None
    return site, tenant


def candidate_row(db, payment, rec, job) -> dict:
    site, tenant = _site_info(db, payment)
    plate = (payment.session.plate_number if payment.session else None) or \
        (payment.raw_payload or {}).get("plate_number")
    rtype, receiver = receiver_args(payment)
    return {
        "payment_id": payment.id, "receipt_id": rec.id,
        "paid_at": payment.paid_at, "paid_at_ub": ub(payment.paid_at).strftime("%Y-%m-%d %H:%M:%S"),
        "day": ub_day(payment.paid_at),
        "site_id": getattr(site, "id", None), "site_code": getattr(site, "site_code", None) or "",
        "site_name": getattr(site, "name", None) or "",
        "tenant_id": getattr(tenant, "id", None), "tenant": getattr(tenant, "code", None) or "",
        "plate": plate or "", "amount": float(payment.amount),
        "customer_tin": payment.customer_tin or "", "receiver_type": rtype,
        "receiver": receiver or "",
        "receipt_status": rec.status, "job_status": job.status if job is not None else "",
        "job_attempts": job.attempts if job is not None else 0,
        "last_error": ((job.last_error if job is not None else None) or rec.receipt_url or "")[:200],
        "qpay_payment_id": payment.provider_payment_id or "",
        "fingerprint": ((job.payload or {}).get("account_fingerprint") if job is not None else None),
    }


def select_candidates(db, *, site_ids: list | None, lo: datetime | None = None,
                      hi: datetime | None = None, include_failed: bool = False,
                      payment_ids: list | None = None) -> dict:
    """READ ONLY. QPAY баримт REVIEW (+ include_failed бол FAILED) PAID төлбөрт, paid_at
    [lo, hi) (UTC), зогсоолоор. Буцаах {"candidates": [dict], "skipped": Counter,
    "skipped_rows": [dict]} — paid_at-аар эрэмбэлсэн."""
    statuses = ("REVIEW", "FAILED") if include_failed else ("REVIEW",)
    q = (db.query(VatReceipt, Payment).enable_eagerloads(False)
         .join(Payment, VatReceipt.payment_id == Payment.id)
         .outerjoin(ParkingSession, Payment.session_id == ParkingSession.id)
         .filter(VatReceipt.provider == "QPAY", VatReceipt.status.in_(statuses),
                 Payment.status == "PAID"))
    if site_ids is not None:
        q = q.filter(PAY_SITE.in_(list(site_ids)))
    if lo is not None:
        q = q.filter(Payment.paid_at >= lo)
    if hi is not None:
        q = q.filter(Payment.paid_at < hi)
    if payment_ids:
        q = q.filter(Payment.id.in_(list(payment_ids)))
    rows = q.order_by(Payment.paid_at, Payment.id, VatReceipt.created_at).all()
    out, skipped, skipped_rows, seen = [], Counter(), [], set()
    for rec, pay in rows:
        recs = db.query(VatReceipt).filter(VatReceipt.payment_id == pay.id).all()
        jobs = (db.query(FinancialJob).filter(FinancialJob.payment_id == pay.id,
                                              FinancialJob.kind == "RECEIPT").all())
        job = next((j for j in jobs if j.receipt_id == rec.id), None)
        row = candidate_row(db, pay, rec, job)
        why = skip_reason(pay, rec, recs, jobs, include_failed=include_failed)
        if why is None and pay.id in seen:
            why = "MULTI_RECEIPT"
        if why is None and shared_qpay_payment(db, pay):
            why = "QPAY_ID_SHARED"
        seen.add(pay.id)
        if why:
            skipped[why] += 1
            skipped_rows.append({**row, "skip": why})
            continue
        out.append(row)
    return {"candidates": out, "skipped": skipped, "skipped_rows": skipped_rows}


# ───────────────────────────── ТЕГ нотолгоо (READ ONLY) ─────────────────────────────

def teg_qpay_rows(tax: list, name: str = "") -> list[dict]:
    """parse_tax_export-ийн мөрүүдээс QPay-ийн кассын (…10014863, 33 орон) мөрүүд. Цаг нь UTC."""
    from ..routers import vat_recon as VR
    out, seen = [], set()
    for t in tax:
        d = str(t.get("ddtd") or "").strip()
        if not (VR.is_teg_ddtd(d) and d.endswith(QPAY_REGISTER)) or d in seen:
            continue
        seen.add(d)
        out.append({"ddtd": d, "dt": t["dt"], "amount": float(t["amount"]), "file": name})
    return out


def _load_ours(db, lo: datetime, hi: datetime) -> list[tuple]:
    """Манай ДДТД-тэй QPAY баримтууд [(дүн, paid_at, tenant_id)] — ТЕГ мөрийн «эзэд».
    Цуцлагдсаныг оруулахгүй (файлд үлдсэн бол эзэнгүй → болгоомжтой тал руу: create хийхгүй)."""
    q = (db.query(VatReceipt.amount, Payment.paid_at, ParkingSite.tenant_id)
         .join(Payment, VatReceipt.payment_id == Payment.id)
         .outerjoin(ParkingSession, Payment.session_id == ParkingSession.id)
         .outerjoin(ParkingSite, PAY_SITE == ParkingSite.id)
         .filter(VatReceipt.provider == "QPAY", VatReceipt.ebarimt_id.isnot(None),
                 VatReceipt.status.in_(("SENT", "CANCEL_PENDING")),
                 Payment.paid_at >= lo, Payment.paid_at <= hi))
    return [(float(a), p, t) for a, p, t in q.all() if p is not None]


def _match(rows: list[dict], ours: list[tuple], tol: int) -> list[tuple[int, int, float]]:
    """ТЕГ мөр ↔ манай баримт: дүн <1₮, цаг ±tol, ХАТУУ НЭГ-НЭГЭЭР хамгийн ойроос (vat_recon)."""
    from ..routers import vat_recon as VR
    pairs = VR._pairs([r["dt"] for r in rows], [r["amount"] for r in rows],
                      [o[1] for o in ours], [o[0] for o in ours], tol)
    return VR._assign(pairs)


def teg_evidence(db, taxes: list, candidates: list[dict], *, tol: int = DEFAULT_TOL,
                 before: timedelta = TEG_BEFORE, after: timedelta = TEG_AFTER) -> dict:
    """ТЕГ экспорт(ууд)-оор «энэ REVIEW төлбөрийн QPay баримт ТЕГ-т аль хэдийн бий юу».

    taxes: [(файлын нэр, vat_recon.parse_tax_export мөрүүд)]. Алхам:
      1. QPay-ийн кассын мөрүүд (…10014863, UTC). Файлын ТҮРЭЭСЛЭГЧ = файлын мөртэй
         (±tol, нэг-нэгээр) таарсан манай ДДТД-тэй QPAY баримтын ОЛОНХ (>50%) нь тэр
         түрээслэгчийнх БӨГӨӨД тааралт файлын QPay мөрийн ≥50% — эс бөгөөс нотолгоо биш.
      2. ЭЗЭНТЭЙ мөр: тэр түрээслэгчийн ДДТД-тэй баримттай нэг-нэгээр (±tol) таарсан мөр.
         ТЕГ-ийн ДДТД (0152…) нь QPay-ийн буцаасан дугаараас (0301…) өөр тул ЗӨВХӨН цаг+дүнгээр.
      3. Төлбөр бүрд: paid_at −before…+after цонхонд ижил дүнтэй ЭЗЭНГҮЙ мөр, мөн (УБ өдөр ×
         дүн)-гийн ТЭНЦЭЛ = ТЕГ мөрийн тоо − манай ДДТД-тэй баримтын тоо (файлын хүрээнд; хосолсон
         нь хасагдаж = эзэнгүй мөр − ТЕГ мөргүй манай баримт).
         • эзэнгүй мөр бий + тэнцэл ≥1 → IN_TEG (цонхонд ганц мөр, өөр төлбөрийн цонхонд
           ороогүй, өдрийн IN_TEG ≤ тэнцэл бол тэр мөрийн ДДТД-г холбоно; эс бөгөөс IN_TEG_AMBIG)
         • эзэнгүй мөр бий ч тэнцэл ≤0 (цагийн зөрүүтэй эзэнтэй мөр) эсвэл мөр алга ч
           тэнцэл ≥1 (цонхноос гадуур эзэнгүй мөр) → AMBIGUOUS
         • аль нь ч үгүй → NOT_IN_TEG.
    Буцаах {"files": [статистик], "verdicts": {receipt_id: {...}}, "tenants": {tenant_id: {...}}}."""
    files, by_tenant = [], {}
    pad = timedelta(seconds=tol + 60)
    for name, tax in taxes:
        rows = teg_qpay_rows(tax, name)
        st = {"file": name, "rows": len(tax), "qpay_rows": len(rows), "tenant_id": None,
              "window": None, "matched": 0, "share": 0.0, "usable": False, "note": ""}
        files.append(st)
        if not rows:
            st["note"] = f"QPay-ийн кассын (…{QPAY_REGISTER}) мөр алга"
            continue
        lo, hi = min(r["dt"] for r in rows), max(r["dt"] for r in rows)
        st["window"] = (lo, hi)
        ours = _load_ours(db, lo - pad, hi + pad)
        per = Counter(ours[j][2] for _i, j, _d in _match(rows, ours, tol))
        top, n = per.most_common(1)[0] if per else (None, 0)
        st["matched"], st["share"] = n, n / max(1, len(rows))
        if top is None or n * 2 <= sum(per.values()) or st["share"] < TEG_TENANT_SHARE:
            st["note"] = (f"файлын QPay {len(rows)} мөрийн {n} ({st['share']:.0%}) л манай нэг "
                          "түрээслэгчийн баримттай таарсан — нотолгоо болохгүй")
            continue
        st["tenant_id"], st["usable"] = top, True
        t = by_tenant.setdefault(top, {"rows": {}, "windows": []})
        for r in rows:
            t["rows"].setdefault(r["ddtd"], r)
        t["windows"].append((lo, hi))
    verdicts, tenants = {}, {}
    for tid, t in by_tenant.items():
        rows = sorted(t["rows"].values(), key=lambda r: (r["dt"], r["ddtd"]))
        wins = t["windows"]
        lo, hi = min(w[0] for w in wins), max(w[1] for w in wins)
        ours = [o for o in _load_ours(db, lo - pad, hi + pad) if o[2] == tid]
        pairs = _match(rows, ours, tol)
        owned, used = {i for i, _j, _d in pairs}, {j for _i, j, _d in pairs}
        orphans = [r for i, r in enumerate(rows) if i not in owned]

        def inside(at, wins=wins):
            return any(a <= at <= b for a, b in wins)
        # (УБ өдөр × дүн)-гийн ТЭНЦЭЛ = ТЕГ мөрийн тоо − манай ДДТД-тэй баримтын тоо. Хосолсон
        # (мөр ↔ баримт) нь хоёр талд ИЖИЛ тоологдож хасагдана — үлдэх нь эзэнгүй мөр − мөргүй
        # баримт (файлын хүрээнд). Шөнө дундын/файлын хилийн хос хуурамч ±1 үүсгэхгүй.
        orphan_b = Counter((ub_day(r["dt"]), round(r["amount"])) for r in orphans)
        lonely_b = Counter((ub_day(o[1]), round(o[0])) for j, o in enumerate(ours)
                           if j not in used and inside(o[1]))
        surplus_b = {k: orphan_b[k] - lonely_b[k] for k in set(orphan_b) | set(lonely_b)
                     if orphan_b[k] != lonely_b[k]}
        tenants[tid] = {"rows": len(rows), "ours": len(used) + sum(lonely_b.values()),
                        "orphans": len(orphans), "lonely": sum(lonely_b.values()),
                        "surplus": surplus_b, "windows": wins}
        mine = [c for c in candidates if c.get("tenant_id") == tid]
        near_of, users = {}, {}
        for c in mine:
            at = c["paid_at"]
            if not any(a <= at - before and at + after <= b for a, b in wins):
                verdicts[c["receipt_id"]] = {"teg": "OUTSIDE"}
                continue
            near = [r for r in orphans if abs(r["amount"] - c["amount"]) < 1
                    and at - before <= r["dt"] <= at + after]
            near_of[c["receipt_id"]] = near
            for r in near:
                users.setdefault(r["ddtd"], []).append(c["receipt_id"])
        in_teg_per_bucket = Counter()
        prelim = {}
        for c in mine:
            near = near_of.get(c["receipt_id"])
            if near is None:
                continue
            key = (c["day"], round(c["amount"]))
            surplus = surplus_b.get(key, 0)
            if near and surplus >= 1:
                v = "IN_TEG"
                in_teg_per_bucket[key] += 1
            elif near or surplus >= 1:
                v = "AMBIGUOUS"
            else:
                v = "NOT_IN_TEG"
            prelim[c["receipt_id"]] = (v, key, surplus, near)
        for rid, (v, key, surplus, near) in prelim.items():
            info = {"teg": v, "near": len(near), "surplus": surplus,
                    "near_rows": [{"ddtd": r["ddtd"], "dt": r["dt"].isoformat(sep=" "),
                                   "file": r["file"]} for r in near[:5]]}
            if v == "IN_TEG":
                if (len(near) == 1 and len(users.get(near[0]["ddtd"], [])) == 1
                        and in_teg_per_bucket[key] <= surplus):
                    info.update(teg_ddtd=near[0]["ddtd"], teg_dt=near[0]["dt"],
                                teg_file=near[0]["file"])
                else:
                    info["teg"] = "IN_TEG_AMBIG"
            if v == "AMBIGUOUS":
                info["note"] = ("ойролцоо эзэнгүй мөр бий ч өдрийн тоо тэнцүү (цаг зөрсөн эзэнтэй мөр байж магадгүй)"
                                if near else f"энэ өдөр энэ дүнгийн эзэнгүй мөр {surplus} бий (цонхноос гадуур)")
            verdicts[rid] = info
    for c in candidates:
        if c["receipt_id"] not in verdicts:
            verdicts[c["receipt_id"]] = {"teg": "NOT_COVERED"}
    return {"files": files, "verdicts": verdicts, "tenants": tenants}


def plan_action(verdict: str, *, include_ambiguous: bool = False,
                include_uncovered: bool = False) -> str:
    """Нотолгооны дүгнэлт → үйлдэл: CREATE | ATTACH_TEG | SKIP."""
    if verdict in ("NO_FILE", "NOT_IN_TEG"):
        return "CREATE"
    if verdict == "IN_TEG":
        return "ATTACH_TEG"
    if verdict == "AMBIGUOUS":
        return "CREATE" if include_ambiguous else "SKIP"
    if verdict in ("OUTSIDE", "NOT_COVERED"):
        return "CREATE" if include_uncovered else "SKIP"
    return "SKIP"                          # IN_TEG_AMBIG — гараар


# ───────────────────────────── бодит нөхөлт (--apply) ─────────────────────────────

def account_problems(sites: list) -> list[str]:
    """Зогсоол бүрийн QPay данс БОДИТ эсэх (MOCK/sandbox/тохируулаагүй бол нөхөхгүй)."""
    from . import qpay
    groups: dict[str, list] = {}
    for site in {getattr(s, "id", None): s for s in sites or []}.values():
        name = (getattr(site, "name", None) or getattr(site, "id", "?")).strip()
        try:
            acc = qpay.account_for(site)
        except Exception as e:  # noqa: BLE001 — нууц утга тайлагдахгүй г.м
            groups.setdefault(f"QPay данс уншигдсангүй ({type(e).__name__})", []).append(name)
            continue
        if acc.mock:
            why = "QPay MOCK горимд (PARKING_QPAY_MOCK) — бодит баримт гарахгүй"
        elif not (acc.username and acc.password):
            why = "QPay данс тохируулаагүй (нэр/нууц үг алга)"
        elif "sandbox" in (acc.base_url or "") or not (acc.base_url or "").startswith("https://"):
            why = f"QPay sandbox/бодит биш хаяг ({acc.base_url})"
        else:
            continue
        groups.setdefault(why, []).append(name)
    return [f"{why}: {len(names)} зогсоол (" + ", ".join(f"«{n}»" for n in names[:4])
            + (f" +{len(names) - 4}" if len(names) > 4 else "") + ")" for why, names in groups.items()]


def _audit(db, username, payment_id, detail):
    db.add(AuditLog(username=username, action=AUDIT_ACTION, entity="payment",
                    entity_id=payment_id, detail=detail))


async def resolve_one(db, cand: dict, *, action: str, username: str, include_failed: bool = False,
                      evidence: dict | None = None, source: str = "cli") -> dict:
    """НЭГ төлбөрийн QPAY REVIEW баримтыг нөхнө. Төлбөр (NOWAIT) + ажил + баримтын мөрийг
    түгжиж skip_reason-оор ДАХИН шалгана; action:
      CREATE     — create_and_assign (ebarimt-diag-ийн зам; QPay-г НЭГ удаа дуудна)
      ATTACH_TEG — QPay-г ДУУДАХГҮЙ: ТЕГ-ийн ДДТД-г холбоно (баримт аль хэдийн бий)
    Амжилтад: SENT + ДДТД, ажил DONE (outbox/«баримт холбох»-той ижил), partner-д receipt.ready.
    Алдаанд: баримт REVIEW хэвээр. Үр дүн бүрд AuditLog (AUDIT_ACTION). Commit хийнэ."""
    from fastapi import HTTPException

    from ..routers.payments_router import _lock_payment
    from .financial_jobs import _account, enqueue_receipt_ready, payment_site
    from .receipts import assign_ebarimt_id
    pid, rid = cand["payment_id"], cand["receipt_id"]
    base = {"payment_id": pid, "receipt_id": rid, "action": action}
    try:
        payment = _lock_payment(db, pid)
    except HTTPException:
        return {**base, "outcome": "SKIPPED", "reason": "LOCKED",
                "error": "Төлбөр өөр хүсэлтэд түгжигдсэн — дахин ажиллуулахад нөхөгдөнө"}
    if payment is None:
        db.rollback()
        return {**base, "outcome": "SKIPPED", "reason": "MISSING"}
    jobs = (db.query(FinancialJob).filter(FinancialJob.payment_id == pid, FinancialJob.kind == "RECEIPT")
            .order_by(FinancialJob.id).populate_existing().with_for_update().all())
    recs = (db.query(VatReceipt).filter(VatReceipt.payment_id == pid)
            .order_by(VatReceipt.created_at, VatReceipt.id).populate_existing().with_for_update().all())
    rec = next((r for r in recs if r.id == rid), None)
    why = skip_reason(payment, rec, recs, jobs, include_failed=include_failed)
    if why is None and shared_qpay_payment(db, payment):
        why = "QPAY_ID_SHARED"
    if why:
        db.rollback()
        return {**base, "outcome": "SKIPPED", "reason": why}
    job = next((j for j in jobs if j.receipt_id == rec.id), None)
    try:
        acc, fingerprint = _account("QPAY", payment_site(payment))
    except Exception as e:  # noqa: BLE001
        db.rollback()
        return {**base, "outcome": "SKIPPED", "reason": "ACCOUNT_ERROR", "error": type(e).__name__}
    want = (job.payload or {}).get("account_fingerprint") if job is not None else None
    if want and want != fingerprint:
        # outbox-ын deliver-тэй ижил: төлбөр авсан данснаас өөр дансаар баримт гаргахгүй
        db.rollback()
        return {**base, "outcome": "SKIPPED", "reason": "ACCOUNT_CHANGED",
                "error": "Баримтын дансны тохиргоо өөрчлөгдсөн; санхүү тулгана уу"}
    if acc.mock:
        db.rollback()
        return {**base, "outcome": "SKIPPED", "reason": "QPAY_MOCK"}
    detail = {"tool": "qpay_review_resolve", "source": source, "receipt_id": rid, "action": action,
              "prev_status": rec.status, "job_status": job.status if job is not None else None,
              "qpay_payment_id": payment.provider_payment_id, "amount": float(payment.amount),
              "receiver_type": receiver_args(payment)[0], "customer_tin": payment.customer_tin,
              "teg": {k: (v.isoformat(sep=" ") if isinstance(v, datetime) else v)
                      for k, v in (evidence or {}).items() if k != "near_rows"}}
    if action == "ATTACH_TEG":
        ddtd = (evidence or {}).get("teg_ddtd")
        taken = ddtd and db.query(VatReceipt.id).filter(VatReceipt.ebarimt_id == ddtd).first()
        if not ddtd or taken:
            db.rollback()
            return {**base, "outcome": "SKIPPED", "reason": "DDTD_TAKEN" if taken else "NO_TEG_DDTD"}
        rec.lottery_code = None
        ok = assign_ebarimt_id(db, rec, ddtd, source=TEG_SOURCE, username=username,
                               raw={"teg_ddtd": ddtd, "teg_dt": detail["teg"].get("teg_dt"),
                                    "file": (evidence or {}).get("teg_file"), "by": username},
                               allow_replace=True)
        res = {"ok": ok, "receipt_updated": ok}
        if ok:
            rec.status, rec.receipt_url = "SENT", None
    elif action == "CREATE":
        res = await create_and_assign(db, payment, rec, acc, username=username,
                                      source=TOOL_SOURCE, commit=False)
    else:
        db.rollback()
        return {**base, "outcome": "SKIPPED", "reason": "NO_ACTION"}
    done = bool(res.get("ok") and res.get("receipt_updated") and rec.status == "SENT" and rec.ebarimt_id)
    if done:
        if job is not None:
            job.status, job.last_error = "DONE", None
            job.lease_token, job.lease_until = None, None
        enqueue_receipt_ready(db, payment, rec)
    err = None if done else error_text(res)
    detail.update(ok=done, ebarimt_id=rec.ebarimt_id if done else None,
                  http_status=res.get("http_status"), error=err,
                  elapsed_ms=res.get("elapsed_ms"))
    try:
        _audit(db, username, pid, detail)
        db.commit()
    except Exception:
        db.rollback()
        log.exception("QPay REVIEW нөхөлт хадгалагдсангүй payment=%s (QPay хариу: %s)", pid,
                      (res.get("raw") or {}).get("ebarimt_receipt_id") if isinstance(res.get("raw"), dict) else None)
        raise
    return {**base, "outcome": "SENT" if done else "FAILED", "ebarimt_id": rec.ebarimt_id if done else None,
            "lottery": rec.lottery_code if done else None, "error": err,
            "http_status": res.get("http_status")}
