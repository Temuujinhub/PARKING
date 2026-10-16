"""QPay e-Barimt-ын REVIEW (тодорхойгүй) баримтыг нөхөх — ebarimt-diag-тай НЭГ код зам (2026-10-15).

Яагаад: 10-р сард EasyParking-ийн ~40 QR төлбөр (≈119,500₮) QPAY баримт нь REVIEW төлөвт
гацсан — outbox-ын анхны ebarimt_v3/create HTTPStatusError буцаасан тул ажил UNKNOWN болж
(financial_jobs.deliver: QPay create-ийн дахин илгээлт идемпотент гэж БАТЛАГДААГҮЙ тул
хорионд авна), «Дахин үүсгэх» review_required өгдөг. Ганц зам нь SUPER_ADMIN-ий
`POST /api/admin/qpay/ebarimt-diag {payment_id, create:true}` байв. Энэ модуль тэр
endpoint-ийн create хэсгийг (`create_and_assign`) НЭГ функц болгож гаргасан — endpoint
болон `tools/qpay_review_resolve.py` хоёулаа ЯГ ЭНЭ функцийг дуудна (endpoint-ийн амжилтын ба
HTTP алдааны хариуны түлхүүрүүд өөрчлөгдөөгүй; review-ийн дараа: QPay-ийн billId ДДТД биш бол
SENT болгохгүй, хадгалалт унавал billId/сугалааг алдааны хариунд нэмж буцаана).

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
    retry-ebarimt, баримт холбох нэг төлбөр дээр ЗЭРЭГ ажиллахгүй; (2) түгжээний дотор дахин
    шалгана (REVIEW, ДДТД-гүй, өөр идэвхтэй/ДДТД-гүй баримтын мөргүй, ажил SENDING биш,
    өмнөх тодорхойгүй оролдлогогүй); (3) QPay-г дуудахаас ӨМНӨ оролдлогыг
    (`raw.qpay_review_attempts` + AuditLog EBARIMT_QPAY_REVIEW_ATTEMPT) ТУСДАА транзакцаар
    commit хийнэ, дараа нь үр дүнг (SENT/REJECTED/NETWORK/…, billId) нэмж бичнэ — timeout/5xx/
    billId-гүй/хадгалалт унасан оролдлоготой төлбөрийг дахин ажиллуулахад АЛГАСНА (QPay
    4xx-ээр тодорхой татгалзсанаас бусад); дахин оролдох нь зөвхөн --retry-attempted + сүүлийн
    оролдлогоос ≥24ц хойш хамарсан ТЕГ экспортоор; (4) амжилтын дараа SENT + ажил DONE →
    дахин ажиллуулахад/outbox/retry алгасна; (5) ТЕГ-т байгаа нь (IN_TEG) хэзээ ч create
    хийхгүй; (6) QPay-ийн billId нь ДДТД (33 оронтой тоо — audit1014-ийн 67,838 SENT QPAY
    мөр бүгд 030101065006…10014863) биш бол SENT болгохгүй (REVIEW + raw хадгална).
  • ⚠ `POST /api/admin/qpay/ebarimt-diag {create:true}` нь түгждэггүй, оролдлогын бүртгэлийг
    ШАЛГАДАГГҮЙ, бичдэггүй (зөвхөн QPAY_EBARIMT_DIAG аудит) — энэ хэрэгсэлтэй ЗЭРЭГ ч, ДАРАА нь
    ч (ялангуяа timeout/5xx-ээр унасан төлбөр дээр) БҮҮ ашигла: QPay талд баримт үүссэн байж
    болзошгүй, давхар баримт гарна. Тодорхойгүй төлбөрийг ТЕГ экспортоор шалгаж «баримт
    холбох»-оор эсвэл --retry-attempted-аар л.

customer_tin-ийг ХЭЗЭЭ Ч өөрчлөхгүй: ААН-д `ebarimt_receiver` = төлбөрийн customer_tin
(QPay 7 оронтой регистр л хүлээн авдаг — MAX 7, 1c114f3); QPAY-ээс бусад суваг, msgbill
баримт ХӨНДӨХГҮЙ.
"""
from __future__ import annotations

import logging
import uuid
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
ATTEMPT_ACTION = "EBARIMT_QPAY_REVIEW_ATTEMPT"   # QPay create-ийн ӨМНӨ commit хийгдэх оролдлого
DIAG_ACTION = "QPAY_EBARIMT_DIAG"                # ebarimt-diag endpoint-ийн аудит (create:true = оролдлого)
ATTEMPTS_KEY = "qpay_review_attempts"            # VatReceipt.raw дахь оролдлогын жагсаалт
ATTEMPT_COVER = timedelta(hours=24)              # --retry-attempted: ТЕГ файл сүүлийн оролдлогоос ≥24ц хойш
ATTEMPT_NEAR_BEFORE = timedelta(minutes=2)       # оролдлогын үеийн эзэнгүй ТЕГ мөр: −2 мин …
ATTEMPT_NEAR_AFTER = timedelta(hours=1)          # … +1 цаг (QPay ТЕГ-т хожуу бүртгүүлж болно)
DEFAULT_TOL = 60                    # vat_recon.DEFAULT_TOL — манай баримт ↔ ТЕГ мөр (сек)
TEG_LAG = timedelta(hours=24)       # NOT_IN_TEG: файл төлбөрөөс ≥24ц хойш хамарсан байх (QPay ТЕГ-т хожуу бүртгүүлж болно)
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
    "MULTI_RECEIPT": "нэг төлбөрт өөр цуцлагдаагүй баримтын мөр (PENDING/REVIEW/FAILED, msgbill г.м) бий — санхүү тулгана",
    "ATTEMPTED": "өмнө QPay create оролдсон, үр дүн ТОДОРХОЙГҮЙ (timeout/5xx/billId-гүй/хадгалагдаагүй) — "
                 "QPay талд баримт үүссэн байж болзошгүй; ДАХИН create хийхгүй",
    "NO_QPAY_ID": "QPay payment_id (provider_payment_id) алга — create хийх боломжгүй",
    "AMOUNT_MISMATCH": "баримтын дүн ≠ төлбөрийн дүн (QPay бүтэн дүнгээр гаргана)",
    "JOB_SENDING": "баримтын ажил одоо илгээгдэж байна",
    "JOB_BLOCKED": "тохиргоогоор хаагдсан FAILED (BLOCKED ажил) — эхлээд тохиргоо засна",
    "JOB_STATE": "баримтын ажлын төлөв хачин (UNKNOWN/ажилгүй биш)",
    "MISSING": "баримт олдсонгүй",
    "QPAY_ID_SHARED": "өөр төлбөр ижил QPay payment_id-тэй — нэг QPay төлбөрт нэг баримт, гараар тулгана",
    "LOCKED": "төлбөр өөр хүсэлтэд түгжигдсэн (QPay дуудагдаагүй)",
    "ATTEMPT_NOT_SAVED": "оролдлогын бүртгэл хадгалагдсангүй — QPay дуудагдаагүй",
}
# Оролдлогын үр дүн. ТОДОРХОЙ (QPay баримт ҮҮСГЭЭГҮЙ нь мэдэгдэж буй) = DEFINITE — дахин
# ажиллуулахад зөвшөөрнө; бусад нь бүгд АЛГАСНА (--retry-attempted + ТЕГ нотолгоогүйгээр).
ATTEMPT_MN = {
    "PENDING": "үр дүн бичигдээгүй (процесс тасарсан/хадгалалт унасан)",
    "SENT": "ДДТД авч SENT болсон",
    "REJECTED": "QPay 4xx-ээр тодорхой татгалзсан",
    "HTTP_ERROR": "QPay 5xx/408/409 эсвэл «аль хэдийн үүссэн» — тодорхойгүй",
    "NETWORK": "timeout/холболтын алдаа — тодорхойгүй",
    "NO_BILL": "QPay billId буцаасангүй — тодорхойгүй",
    "BAD_BILL": "QPay-ийн billId ДДТД биш — тодорхойгүй",
    "CONFLICT": "ДДТД зөрчил (өөр дугаар хадгалсан) — гараар",
    "NOT_SAVED": "QPay ДДТД буцаасан ч хадгалагдсангүй — ДДТД аудит/логт",
    "NOT_CALLED": "QPay дуудагдаагүй (дахин шалгалтад алгассан)",
    "DIAG": "ebarimt-diag create (үр дүн тодорхойгүй)",
    "UNKNOWN": "тодорхойгүй",
}
DEFINITE = frozenset({"REJECTED", "NOT_CALLED"})
VERDICT_MN = {
    "NO_FILE": "ТЕГ файл өгөөгүй",
    "NOT_IN_TEG": "ТЕГ-т алга — QPay-ээр create",
    "IN_TEG": "ТЕГ-т БИЙ — create ХИЙХГҮЙ, ТЕГ-ийн ДДТД-г холбоно",
    "IN_TEG_AMBIG": "ТЕГ-т бий бололтой, гэхдээ аль мөр нь вэ тодорхойгүй — гараар",
    "AMBIGUOUS": "ТОДОРХОЙГҮЙ (ойролцоо эзэнгүй мөр эсвэл өдрийн тооны зөрүү) — "
                 "--include-ambiguous-гүйгээр create хийхгүй",
    "OUTSIDE": "ТЕГ файлын хүрээнээс гадуур — дүгнэхгүй",
    "NOT_COVERED": "ТЕГ файл энэ түрээслэгчийнх биш — дүгнэхгүй",
    "ATTEMPT_UNCOVERED": "өмнөх QPay оролдлого тодорхойгүй, ТЕГ файл түүнээс хойш 24ц-ийг хамрахгүй — create хийхгүй",
    "ATTEMPT_HAS_BILL": "өмнөх QPay оролдлого ДДТД/billId буцаасан — баримт ҮҮССЭН, create ХИЙХГҮЙ, тэр ДДТД-г гараар холбоно",
    "TEG_LAG": "ТЕГ файл төлбөрөөс хойш 24ц хамрахгүй (QPay хожуу бүртгүүлсэн байж болзошгүй) — шинэ экспортоор",
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


def is_ddtd(v) -> bool:
    """QPay-ийн billId ДДТД мөн эсэх: 33 оронтой тоо (vat_recon.is_teg_ddtd). audit1014-ийн
    67,838 SENT QPAY мөр бүгд энэ хэлбэртэй (030101065006…10014863). UUID/ebarimt id г.м
    бусдыг ДДТД гэж бичихгүй."""
    from ..routers import vat_recon as VR
    return VR.is_teg_ddtd(v)


async def create_and_assign(db, payment, rec, acc, *, username: str, source: str = DIAG_SOURCE,
                            commit: bool = True) -> dict:
    """`POST /api/admin/qpay/ebarimt-diag` (create=true)-ийн create хэсэг — 2026-10-15-нд
    endpoint-оос гаргасан (амжилтын болон HTTP алдааны хариуны түлхүүрүүд ӨӨРЧЛӨГДӨӨГҮЙ).

    QPay ebarimt_v3/create-г НЭГ удаа дуудаж түүхий хариуг буцаана. ДДТД ирвэл `rec`-д
    assign_ebarimt_id-ээр бичиж (SENT мөрийг өөр дугаараар дарахгүй) SENT болгоно.
    HTTP алдаа → {"ok": False, "http_status", "raw_body"} — баримтад ХҮРЭХГҮЙ (REVIEW хэвээр).
    commit=False — дуудагч (CLI) аудитын мөртэй НЭГ транзакцаар commit хийнэ.

    QPay хариу ирсний ДАРАА (billId-тэй) юу ч болсон billId/сугалаа/raw алдагдахгүй: QPay-ийн
    дуудлага ба хадгалалт ТУСДАА try — хадгалалт унавал rollback хийж {"ok": True,
    "receipt_updated": False, "bill_id", "lottery", "save_error"} буцаана (лог-д ч billId).
    billId нь ДДТД биш (is_ddtd) бол SENT болгохгүй: {"ok": False, "bill_id", "error"},
    түүхий хариуг rec.raw[source]-д хадгална. Нэмэлт түлхүүрүүд ЗӨВХӨН алдааны замд."""
    import httpx as _httpx

    from . import qpay as qpay_svc
    t0 = datetime.utcnow()

    def _ms():
        return int((datetime.utcnow() - t0).total_seconds() * 1000)
    receiver_type, receiver = receiver_args(payment)
    try:
        eb = await qpay_svc.create_ebarimt(
            payment.provider_payment_id, receiver_type,
            receiver=receiver,
            acc=acc)
    except _httpx.HTTPStatusError as e:
        body_raw: object
        try:
            body_raw = e.response.json()
        except Exception:  # noqa: BLE001
            body_raw = e.response.text[:1000]
        return {"ok": False, "http_status": e.response.status_code,
                "raw_body": body_raw, "elapsed_ms": _ms()}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:300]}"}
    # ── QPay хариу ирсэн: эндээс хойш billId/сугалаа/raw-г ЮУ Ч болсон алдахгүй ──
    eb = eb if isinstance(eb, dict) else {}
    bill = str(eb.get("billId") or "").strip() or None
    lottery = None if receiver_type == "COMPANY" else eb.get("lottery")
    res = {"ok": bool(bill), "raw": eb.get("raw"), "elapsed_ms": _ms()}
    if bill:
        log.warning("QPay ebarimt_v3/create payment=%s qpay_payment=%s → billId=%s сугалаа=%s",
                    getattr(payment, "id", None), payment.provider_payment_id, bill, lottery)
    if bill and not is_ddtd(bill):
        res.update(ok=False, bill_id=bill, lottery=lottery,
                   error=f"QPay-ийн billId ДДТД биш (33 оронтой тоо биш): {bill[:80]}")
        if rec is not None:
            try:
                from .receipts import assign_ebarimt_id
                assign_ebarimt_id(db, rec, None, source=source, username=username,
                                  raw={"invalid_bill_id": bill, "lottery": lottery,
                                       "response": eb.get("raw")})
                if commit:
                    db.commit()
            except Exception as e:  # noqa: BLE001
                db.rollback()
                log.exception("QPay billId (ДДТД биш) %s raw хадгалагдсангүй payment=%s", bill,
                              getattr(payment, "id", None))
                res["save_error"] = f"{type(e).__name__}: {str(e)[:300]}"
        return res
    if bill and rec:
        try:
            from .receipts import assign_ebarimt_id
            rec.lottery_code = None
            ok = assign_ebarimt_id(db, rec, bill, source=source, lottery=lottery,
                                   username=username, raw=eb.get("raw"),
                                   allow_replace=rec.status != "SENT")
            if ok:
                rec.status = "SENT"
                rec.receipt_url = None
            if commit:
                db.commit()
            res["receipt_updated"] = ok
            if not ok:
                res.update(conflict=rec.ddtd_note, bill_id=bill, lottery=lottery)
        except Exception as e:  # noqa: BLE001 — QPay баримт ҮҮССЭН: billId-г алдахгүй
            db.rollback()
            log.exception("QPay ДДТД %s (сугалаа %s) ирсэн ч хадгалагдсангүй payment=%s",
                          bill, lottery, getattr(payment, "id", None))
            res.update(receipt_updated=False, bill_id=bill, lottery=lottery,
                       save_error=f"{type(e).__name__}: {str(e)[:300]}")
    return res


def error_text(res: dict) -> str | None:
    """create_and_assign-ийн алдааг нэг мөр болгоно (аудит/хэвлэлтэд, ≤300)."""
    if res.get("save_error"):
        return f"QPay ДДТД {res.get('bill_id')} ирсэн ч хадгалагдсангүй: {res['save_error']}"[:300]
    if res.get("ok"):
        return None if res.get("receipt_updated", True) else f"ДДТД зөрчил: {res.get('conflict')}"[:300]
    if res.get("http_status"):
        body = res.get("raw_body")
        if isinstance(body, dict):
            body = body.get("message") or body.get("error") or body
        return f"HTTP {res['http_status']}: {body}"[:300]
    return (res.get("error") or "QPay ДДТД буцаасангүй")[:300]


def classify_attempt(res: dict, done: bool) -> str:
    """create_and_assign-ийн үр дүн → оролдлогын код (ATTEMPT_MN). Зөвхөн QPay 4xx (408/409-өөс
    бусад, «аль хэдийн/давхар/үүссэн/in progress» текстгүй) нь «баримт үүсээгүй» гэж ТОДОРХОЙ;
    бусад бүх алдаа тодорхойгүй. 409 — financial_jobs.provider_error-тэй ижил (2026-10-16 хяналт:
    «EBARIMT_ALREADY_CREATED» г.м create-ийн 409 баримт үүссэнийг илэрхийлж болно)."""
    if done:
        return "SENT"
    if res.get("save_error"):
        return "NOT_SAVED"
    if res.get("bill_id") and not is_ddtd(res["bill_id"]):
        return "BAD_BILL"
    if res.get("ok"):
        return "CONFLICT"
    st = res.get("http_status")
    if st:
        return "REJECTED" if _definite_4xx(st, error_text(res)) else "HTTP_ERROR"
    if res.get("error"):
        return "NETWORK"
    return "NO_BILL"


def _definite_4xx(status, text) -> bool:
    """QPay create-ийн HTTP алдаа баримт ҮҮСЭЭГҮЙг батлах уу: 4xx, 408/409 биш, «аль хэдийн/
    давхар/үүссэн/in progress» текстгүй (financial_jobs._MAYBE_CREATED_RE — нэг тодорхойлолт)."""
    from .financial_jobs import _MAYBE_CREATED_RE
    try:
        st = int(status)
    except (TypeError, ValueError):
        return False
    return 400 <= st < 500 and st not in (408, 409) and not _MAYBE_CREATED_RE.search(str(text or ""))


# ───────────────────────────── оролдлогын бүртгэл ─────────────────────────────

def _dt(v) -> datetime | None:
    if isinstance(v, datetime):
        return v
    try:
        return datetime.fromisoformat(str(v)) if v else None
    except ValueError:
        return None


def attempt_audits(db, payment_ids) -> dict:
    """Төлбөр бүрийн оролдлоготой холбоотой аудит (ATTEMPT/RESOLVE/ebarimt-diag) — нэг query
    (ix_audit_action)."""
    ids = [str(x) for x in payment_ids or []]
    out: dict = {}
    if not ids:
        return out
    for i in range(0, len(ids), 500):
        for a in (db.query(AuditLog).filter(AuditLog.action.in_((ATTEMPT_ACTION, AUDIT_ACTION, DIAG_ACTION)),
                                            AuditLog.entity == "payment",
                                            AuditLog.entity_id.in_(ids[i:i + 500]))
                  .order_by(AuditLog.created_at).all()):
            out.setdefault(a.entity_id, []).append(a)
    return out


def prior_attempts(recs: list, audits: list, *, exclude: str | None = None) -> list[dict]:
    """Төлбөрийн QPay create оролдлогууд (баримтын мөр бүрийн raw.qpay_review_attempts +
    аудит). Нэг оролдлого (id) хэд хэдэн эх сурвалжид байвал PENDING-ээс бусад үр дүн давамгайлна
    — нэг нь алдагдсан ч нөгөөгөөр нь тогтооно. ebarimt-diag create = оролдлого (үр дүн
    тодорхойгүй, амжилттай бол SENT). exclude — одоогийн (өөрийн) оролдлогын id."""
    seen: dict = {}

    def put(key, at, outcome, **kw):
        cur = seen.get(key)
        if cur is None:
            seen[key] = {"id": key, "at": _dt(at), "outcome": outcome or "PENDING", **kw}
            return
        if cur.get("at") is None:
            cur["at"] = _dt(at)
        if outcome and outcome != "PENDING":
            cur["outcome"] = outcome
        for k, v in kw.items():
            if v is not None and cur.get(k) is None:
                cur[k] = v
    for r in recs or []:
        raw = r.raw if isinstance(getattr(r, "raw", None), dict) else {}
        for a in raw.get(ATTEMPTS_KEY) or []:
            if isinstance(a, dict):
                put(a.get("id") or f"raw:{r.id}:{a.get('at')}", a.get("at"), a.get("outcome"),
                    http_status=a.get("http_status"), bill_id=a.get("bill_id"), by=a.get("by"))
    for a in audits or []:
        d = a.detail if isinstance(a.detail, dict) else {}
        if a.action == ATTEMPT_ACTION:
            put(d.get("attempt_id") or f"audit:{a.id}", d.get("at") or a.created_at, "PENDING", by=a.username)
        elif a.action == AUDIT_ACTION:
            if d.get("attempt_id"):
                put(d["attempt_id"], d.get("at") or a.created_at, d.get("attempt_outcome"),
                    http_status=d.get("http_status"), bill_id=d.get("bill_id"))
            elif d.get("action") == "CREATE":          # оролдлогын id-гүй хуучин мөр (01bc5dd)
                st = d.get("http_status")
                oc = ("SENT" if d.get("ok") else "REJECTED" if st and _definite_4xx(st, d.get("error"))
                      else "UNKNOWN")
                put(f"audit:{a.id}", a.created_at, oc, http_status=st, bill_id=d.get("ebarimt_id"))
        elif a.action == DIAG_ACTION and d.get("create"):
            put(f"diag:{a.id}", a.created_at, "SENT" if d.get("result") is True else "DIAG", by=a.username)
    seen.pop(exclude, None)
    return sorted(seen.values(), key=lambda x: (x["at"] or datetime.min, x["id"]))


def attempt_info(attempts: list[dict]) -> dict:
    """Сонголтын мөрөнд: оролдлогын тоо, тодорхойгүй (блоклох) эсэх, сүүлийн цаг (UTC)."""
    block = [a for a in attempts if a["outcome"] not in DEFINITE]
    ats = [a["at"] for a in attempts if a["at"] is not None]
    last = max(ats) if ats else None
    return {"attempts": len(attempts), "attempt_block": bool(block),
            "attempt_last": last, "attempt_last_ub": ub(last).strftime("%Y-%m-%d %H:%M") if last else "",
            "attempt_ats": [a["at"] for a in attempts if a["at"] is not None],
            "attempt_outcome": ",".join(a["outcome"] for a in attempts),
            "attempt_bills": ",".join(a["bill_id"] for a in attempts if a.get("bill_id"))}


# ───────────────────────────── сонголт (READ ONLY) ─────────────────────────────

def skip_reason(payment, rec, recs: list, jobs: list, *, include_failed: bool = False,
                attempts: list | None = None, retry_attempted: bool = False) -> str | None:
    """Энэ баримтыг QPay-ээр нөхөж БОЛОХ уу — сонголт болон түгжээний доторх дахин шалгалт
    ХОЁУЛАА энэ нэг дүрмээр. None = болно, эс бөгөөс шалтгааны код (SKIP_MN).
    attempts — prior_attempts(): QPay 4xx-ээр тодорхой татгалзсанаас (DEFINITE) бусад оролдлого
    байвал ATTEMPTED; retry_attempted=True үед л зөвшөөрнө (ТЕГ нотолгоог дуудагч шалгана)."""
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
    if others:                     # цуцлагдаагүй өөр мөр (PENDING/REVIEW/FAILED msgbill г.м)
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
    if not retry_attempted and any(a["outcome"] not in DEFINITE for a in attempts or []):
        return "ATTEMPTED"
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


def candidate_row(db, payment, rec, job, attempts: list | None = None) -> dict:
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
        **attempt_info(attempts or []),
    }


def select_candidates(db, *, site_ids: list | None, lo: datetime | None = None,
                      hi: datetime | None = None, include_failed: bool = False,
                      payment_ids: list | None = None, retry_attempted: bool = False) -> dict:
    """READ ONLY. QPAY баримт REVIEW (+ include_failed бол FAILED) PAID төлбөрт, paid_at
    [lo, hi) (UTC), зогсоолоор. Тодорхойгүй оролдлоготой нь ATTEMPTED-ээр хасагдана
    (retry_attempted=True үед мөрөнд attempt_block=True-тэйгээр үлдэнэ). Буцаах
    {"candidates": [dict], "skipped": Counter, "skipped_rows": [dict]} — paid_at-аар эрэмбэлсэн."""
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
    audits = attempt_audits(db, {pay.id for _rec, pay in rows})
    out, skipped, skipped_rows, seen = [], Counter(), [], set()
    for rec, pay in rows:
        recs = db.query(VatReceipt).filter(VatReceipt.payment_id == pay.id).all()
        jobs = (db.query(FinancialJob).filter(FinancialJob.payment_id == pay.id,
                                              FinancialJob.kind == "RECEIPT").all())
        job = next((j for j in jobs if j.receipt_id == rec.id), None)
        attempts = prior_attempts(recs, audits.get(str(pay.id)))
        row = candidate_row(db, pay, rec, job, attempts)
        why = skip_reason(pay, rec, recs, jobs, include_failed=include_failed, attempts=attempts,
                          retry_attempted=retry_attempted)
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


def db_last_paid(db) -> datetime | None:
    """DB-ийн хамгийн сүүлийн PAID төлбөр (UTC) — хуулбар DB (audit1014 г.м) хэзээ хүртэлхийг
    хамардаг вэ. Түүнээс хойших ТЕГ мөр манай баримттай тулгагдах боломжгүй (ix_payments_status_paid_at)."""
    return db.query(func.max(Payment.paid_at)).filter(Payment.status == "PAID").scalar()


def teg_evidence(db, taxes: list, candidates: list[dict], *, tol: int = DEFAULT_TOL,
                 before: timedelta = TEG_BEFORE, after: timedelta = TEG_AFTER) -> dict:
    """ТЕГ экспорт(ууд)-оор «энэ REVIEW төлбөрийн QPay баримт ТЕГ-т аль хэдийн бий юу».

    taxes: [(файлын нэр, vat_recon.parse_tax_export мөрүүд)]. Алхам:
      1. QPay-ийн кассын мөрүүд (…10014863, UTC). Файлын ТҮРЭЭСЛЭГЧ = файлын мөртэй
         (±tol, нэг-нэгээр) таарсан манай ДДТД-тэй QPAY баримтын ОЛОНХ (>50%) нь тэр
         түрээслэгчийнх БӨГӨӨД тааралт файлын QPay мөрийн ≥50% — эс бөгөөс нотолгоо биш.
         Файлын хүрээ DB-ийн сүүлийн PAID төлбөрөөр (db_last_paid − tol − 60с) тасдагдана —
         хуулбар DB-ийн дараах мөр «эзэнгүй» (хуурамч илүүдэл) мэт харагдахаас сэргийлнэ
         (ажиллаж буй DB-д сүүлийн төлбөр ≈ одоо тул нөлөөгүй).
      2. ЭЗЭНТЭЙ мөр: тэр түрээслэгчийн ДДТД-тэй баримттай нэг-нэгээр (±tol) таарсан мөр.
         ТЕГ-ийн ДДТД (0152…) нь QPay-ийн буцаасан дугаараас (0301…) өөр тул ЗӨВХӨН цаг+дүнгээр.
      3. Төлбөр бүрд: paid_at −before…+after цонхонд ижил дүнтэй ЭЗЭНГҮЙ мөр, мөн (УБ өдөр ×
         дүн)-гийн ТЭНЦЭЛ = ТЕГ мөрийн тоо − манай ДДТД-тэй баримтын тоо (файлын хүрээнд; хосолсон
         нь хасагдаж = эзэнгүй мөр − ТЕГ мөргүй манай баримт).
         • эзэнгүй мөр бий + тэнцэл ≥1 → IN_TEG (цонхонд ганц мөр, өөр төлбөрийн цонхонд
           ороогүй, өдрийн IN_TEG ≤ тэнцэл бол тэр мөрийн ДДТД-г холбоно; эс бөгөөс IN_TEG_AMBIG)
         • эзэнгүй мөр бий ч тэнцэл ≤0 (цагийн зөрүүтэй эзэнтэй мөр) эсвэл мөр алга ч
           тэнцэл ≥1 (цонхноос гадуур эзэнгүй мөр) → AMBIGUOUS
         • цонхонд мөр алга, тэнцэл ≤0, ГЭХДЭЭ тэр (өдөр × дүн)-д эзэнгүй мөр БИЙ (мөргүй манай
           баримт тэнцлийг нөхсөн) эсвэл ИЖИЛ дүнгийн тэнцэл тэр/ХОЖУУ өдөр ≥1 (QPay хожуу
           үүсгэсэн байж болзошгүй) → AMBIGUOUS
         • аль нь ч үгүй → NOT_IN_TEG.
      4. Өмнө тодорхойгүй QPay оролдлоготой (attempt_block) төлбөр: оролдлогын үед
         (−2 мин…+1ц) ижил дүнтэй эзэнгүй мөр → IN_TEG_AMBIG (гараар холбоно); файл (DB-ээр
         тасдсан) сүүлийн оролдлогоос ≥24ц хойш хүртэл хамрахгүй → ATTEMPT_UNCOVERED.
    Буцаах {"files": [статистик], "verdicts": {receipt_id: {...}}, "tenants": {tenant_id: {...}},
    "db_last_paid": datetime|None}."""
    files, by_tenant = [], {}
    pad = timedelta(seconds=tol + 60)
    db_hi = db_last_paid(db)
    for name, tax in taxes:
        rows = teg_qpay_rows(tax, name)
        st = {"file": name, "rows": len(tax), "qpay_rows": len(rows), "tenant_id": None,
              "window": None, "matched": 0, "share": 0.0, "usable": False, "note": "", "clipped": 0}
        files.append(st)
        if db_hi is not None and rows:
            keep = [r for r in rows if r["dt"] <= db_hi - pad]
            st["clipped"] = len(rows) - len(keep)
            rows = keep
        if not rows:
            st["note"] = (f"QPay-ийн кассын (…{QPAY_REGISTER}) мөр алга" if not st["clipped"] else
                          "бүх QPay мөр DB-ийн сүүлийн төлбөрөөс хойш — тулгах боломжгүй")
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
                        "surplus": surplus_b, "windows": wins, "cover_to": hi}
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
            at = c["paid_at"]
            key = (c["day"], round(c["amount"]))
            surplus = surplus_b.get(key, 0)
            later = sorted((d, v) for (d, a), v in surplus_b.items()
                           if a == key[1] and d > key[0] and v >= 1)
            note = None
            if near and surplus >= 1:
                v = "IN_TEG"
                in_teg_per_bucket[key] += 1
            elif near:
                v, note = "AMBIGUOUS", "ойролцоо эзэнгүй мөр бий ч өдрийн тоо тэнцүү (цаг зөрсөн эзэнтэй мөр байж магадгүй)"
            elif surplus >= 1:
                v, note = "AMBIGUOUS", f"энэ өдөр энэ дүнгийн эзэнгүй мөр {surplus} бий (цонхноос гадуур)"
            elif orphan_b.get(key):
                v, note = "AMBIGUOUS", (f"энэ (өдөр × дүн)-д эзэнгүй ТЕГ мөр {orphan_b[key]} бий (цонхноос гадуур; "
                                        f"тэнцэл {surplus:+d} — ТЕГ мөргүй манай баримт нөхсөн)")
            elif later:
                v, note = "AMBIGUOUS", ("ижил дүнгийн илүүдэл ТЕГ мөр хожуу өдөр: "
                                        + ", ".join(f"{d} {n:+d}" for d, n in later[:5])
                                        + (f" (+{len(later) - 5})" if len(later) > 5 else "")
                                        + " — QPay хожуу үүсгэсэн байж болзошгүй")
            elif not any(a <= at - before and at + TEG_LAG <= b for a, b in wins):
                # Анхны outbox create өөрөө тодорхойгүй (HTTPStatusError) байсан — QPay баримтаа ТЕГ-т
                # хожуу бүртгүүлсэн бол файлын төгсгөлд ойр төлбөрт «алга» гэх нотолгоо сул (10-15 хяналт)
                v, note = "TEG_LAG", f"ТЕГ файл {ub(max(b for _a, b in wins)):%m-%d %H:%M} УБ хүртэл — төлбөрөөс ≥24ц хойш хамрах экспорт хэрэгтэй"
            else:
                v = "NOT_IN_TEG"
            prelim[c["receipt_id"]] = (v, key, surplus, near, note, c)
        for rid, (v, key, surplus, near, note, c) in prelim.items():
            info = {"teg": v, "near": len(near), "surplus": surplus,
                    "bucket_orphans": orphan_b.get(key, 0),
                    "near_rows": [{"ddtd": r["ddtd"], "dt": r["dt"].isoformat(sep=" "),
                                   "file": r["file"]} for r in near[:5]]}
            if note:
                info["note"] = note
            if v == "IN_TEG":
                if (len(near) == 1 and len(users.get(near[0]["ddtd"], [])) == 1
                        and in_teg_per_bucket[key] <= surplus):
                    info.update(teg_ddtd=near[0]["ddtd"], teg_dt=near[0]["dt"],
                                teg_file=near[0]["file"])
                else:
                    info["teg"] = "IN_TEG_AMBIG"
            if c.get("attempt_block"):
                last = c.get("attempt_last")
                ats = [a for a in (c.get("attempt_ats") or [last]) if a is not None]
                near_a = [r for r in orphans if abs(r["amount"] - c["amount"]) < 1
                          and any(a - ATTEMPT_NEAR_BEFORE <= r["dt"] <= a + ATTEMPT_NEAR_AFTER for a in ats)]
                # Оролдлого БҮР нэг файлын цонхонд (−2 мин … +24ц) бүрэн багтах ёстой — хоёр экспортын
                # завсарт орсон оролдлогыг «хамарсан» гэж үзэхгүй (10-15 хяналт)
                info["attempt_cover"] = bool(ats) and all(
                    any(a0 <= at_ - ATTEMPT_NEAR_BEFORE and at_ + ATTEMPT_COVER <= b0 for a0, b0 in wins)
                    for at_ in ats)
                info["attempt_near"] = len(near_a)
                if c.get("attempt_bills"):
                    # QPay billId буцаасан оролдлого = баримт ҮҮССЭН (ТЕГ-т хожуу орж болно) → хэзээ ч create биш
                    info["teg"] = "ATTEMPT_HAS_BILL"
                    info["note"] = f"өмнөх оролдлогын billId: {c['attempt_bills']} — «баримт холбох»-оор гараар"
                    for k in ("teg_ddtd", "teg_dt", "teg_file"):
                        info.pop(k, None)
                elif near_a:
                    info["teg"] = "IN_TEG_AMBIG"
                    info["note"] = (f"өмнөх QPay оролдлогын үед ижил дүнтэй эзэнгүй ТЕГ мөр {len(near_a)} — "
                                    "тэр оролдлого баримт үүсгэсэн байж магадгүй, гараар холбоно")
                    info["near_rows"] = [{"ddtd": r["ddtd"], "dt": r["dt"].isoformat(sep=" "),
                                          "file": r["file"]} for r in near_a[:5]]
                    for k in ("teg_ddtd", "teg_dt", "teg_file"):
                        info.pop(k, None)
                elif not info["attempt_cover"]:
                    info["teg"] = "ATTEMPT_UNCOVERED"
                    info["note"] = (f"сүүлийн оролдлого {ub(last):%Y-%m-%d %H:%M} УБ, ТЕГ файл "
                                    f"{ub(hi):%Y-%m-%d %H:%M} УБ хүртэл — ≥24ц хойш хамрах экспорт хэрэгтэй"
                                    if last else "оролдлогын цаг тодорхойгүй")
            verdicts[rid] = info
    for c in candidates:
        if c["receipt_id"] not in verdicts:
            verdicts[c["receipt_id"]] = {"teg": "NOT_COVERED"}
    return {"files": files, "verdicts": verdicts, "tenants": tenants, "db_last_paid": db_hi}


def plan_action(verdict: str, *, include_ambiguous: bool = False,
                include_uncovered: bool = False, attach_teg: bool = False,
                attempted: bool = False) -> str:
    """Нотолгооны дүгнэлт → үйлдэл: CREATE | ATTACH_TEG | SKIP.
    attach_teg — IN_TEG-ийн ТЕГ ДДТД-г холбох нь ил зөвшөөрлөөр (--attach-teg) л.
    attempted — өмнө тодорхойгүй QPay оролдлоготой (--retry-attempted): ЗӨВХӨН NOT_IN_TEG
    (сүүлийн оролдлогоос ≥24ц хойш хамарсан файлаар) үед CREATE; бусад нь SKIP."""
    if verdict == "IN_TEG":
        return "ATTACH_TEG" if attach_teg else "SKIP"
    if attempted:
        return "CREATE" if verdict == "NOT_IN_TEG" else "SKIP"
    if verdict in ("NO_FILE", "NOT_IN_TEG"):
        return "CREATE"
    if verdict == "AMBIGUOUS":
        return "CREATE" if include_ambiguous else "SKIP"
    if verdict in ("OUTSIDE", "NOT_COVERED"):
        return "CREATE" if include_uncovered else "SKIP"
    return "SKIP"                          # IN_TEG_AMBIG, ATTEMPT_UNCOVERED — гараар


# Файлын нотолгоогүй CREATE (--apply-д --no-teg-evidence шаардана)
NO_EVIDENCE = ("NO_FILE", "OUTSIDE", "NOT_COVERED")


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


def _now() -> datetime:
    """Оролдлогын цаг (UTC) — тестэд солигдоно."""
    return datetime.utcnow()


def _js(v):
    """AuditLog/raw-д JSON болгох (datetime → ISO)."""
    if isinstance(v, datetime):
        return v.isoformat(sep=" ", timespec="seconds")
    if isinstance(v, dict):
        return {k: _js(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_js(x) for x in v]
    return v


def _set_attempt(rec, attempt: dict) -> None:
    """rec.raw[ATTEMPTS_KEY]-д оролдлогыг нэмнэ/шинэчилнэ (id-аар). JSON баганыг шинэ dict-ээр."""
    raw = dict(rec.raw) if isinstance(rec.raw, dict) else {}
    lst = [a for a in (raw.get(ATTEMPTS_KEY) or []) if isinstance(a, dict)]
    if any(a.get("id") == attempt["id"] for a in lst):
        lst = [({**a, **attempt} if a.get("id") == attempt["id"] else a) for a in lst]
    else:
        lst.append(dict(attempt))
    raw[ATTEMPTS_KEY] = lst
    rec.raw = raw


def _lock_check(db, cand: dict, *, include_failed: bool, retry_attempted: bool,
                own: str | None = None):
    """Төлбөр (NOWAIT) + ажил + баримтын мөрийг түгжиж бүх дүрмийг ДАХИН шалгана.
    Буцаах (state, None) эсвэл (None, {"reason", "error"?}) — алгасвал rollback хийсэн.
    Сонголтоос хойш оролдлогын тоо өөрчлөгдсөн (зэрэг ажиллагаа) → ATTEMPTED. own — өөрийн
    (дөнгөж commit хийсэн) оролдлогыг тооцохгүй."""
    from fastapi import HTTPException

    from ..routers.payments_router import _lock_payment
    from .financial_jobs import _account, payment_site
    pid, rid = cand["payment_id"], cand["receipt_id"]
    try:
        payment = _lock_payment(db, pid)
    except HTTPException:
        return None, {"reason": "LOCKED",
                      "error": "Төлбөр өөр хүсэлтэд түгжигдсэн — QPay дуудагдаагүй"}
    if payment is None:
        db.rollback()
        return None, {"reason": "MISSING"}
    jobs = (db.query(FinancialJob).filter(FinancialJob.payment_id == pid, FinancialJob.kind == "RECEIPT")
            .order_by(FinancialJob.id).populate_existing().with_for_update().all())
    recs = (db.query(VatReceipt).filter(VatReceipt.payment_id == pid)
            .order_by(VatReceipt.created_at, VatReceipt.id).populate_existing().with_for_update().all())
    rec = next((r for r in recs if r.id == rid), None)
    attempts = prior_attempts(recs, attempt_audits(db, [pid]).get(str(pid)), exclude=own)
    why = skip_reason(payment, rec, recs, jobs, include_failed=include_failed, attempts=attempts,
                      retry_attempted=retry_attempted)
    if why is None and len(attempts) != int(cand.get("attempts") or 0):
        why = "ATTEMPTED"           # сонголтоос хойш өөр оролдлого бичигдсэн — QPay-г дуудахгүй
    if why is None and shared_qpay_payment(db, payment):
        why = "QPAY_ID_SHARED"
    if why:
        db.rollback()
        return None, {"reason": why}
    job = next((j for j in jobs if j.receipt_id == rec.id), None)
    try:
        acc, fingerprint = _account("QPAY", payment_site(payment))
    except Exception as e:  # noqa: BLE001
        db.rollback()
        return None, {"reason": "ACCOUNT_ERROR", "error": type(e).__name__}
    want = (job.payload or {}).get("account_fingerprint") if job is not None else None
    if want and want != fingerprint:
        # outbox-ын deliver-тэй ижил: төлбөр авсан данснаас өөр дансаар баримт гаргахгүй
        db.rollback()
        return None, {"reason": "ACCOUNT_CHANGED",
                      "error": "Баримтын дансны тохиргоо өөрчлөгдсөн; санхүү тулгана уу"}
    if acc.mock:
        db.rollback()
        return None, {"reason": "QPAY_MOCK"}
    return {"payment": payment, "rec": rec, "job": job, "acc": acc, "attempts": attempts}, None


def _record_outcome(db, username: str, pid: str, rid: str, attempt: dict, detail: dict) -> bool:
    """Үндсэн хадгалалт унасны дараа оролдлогын үр дүнг (billId-тэй) ТУСДАА транзакцаар бичнэ:
    эхлээд баримтын raw + аудит, болохгүй бол зөвхөн аудит. Аль нь ч болохгүй бол оролдлого
    PENDING хэвээр — дахин ажиллуулахад алгасна (болгоомжтой тал)."""
    for with_rec in (True, False):
        try:
            if with_rec:
                rec = db.get(VatReceipt, rid)
                if rec is not None:
                    _set_attempt(rec, attempt)
            _audit(db, username, pid, detail)
            db.commit()
            return True
        except Exception:  # noqa: BLE001
            db.rollback()
            log.exception("QPay REVIEW оролдлогын үр дүн хадгалагдсангүй payment=%s attempt=%s billId=%s "
                          "(raw=%s)", pid, attempt.get("id"), attempt.get("bill_id"), with_rec)
    return False


async def resolve_one(db, cand: dict, *, action: str, username: str, include_failed: bool = False,
                      evidence: dict | None = None, source: str = "cli",
                      retry_attempted: bool = False) -> dict:
    """НЭГ төлбөрийн QPAY REVIEW баримтыг нөхнө. Төлбөр (NOWAIT) + ажил + баримтын мөрийг
    түгжиж skip_reason-оор ДАХИН шалгана; action:
      CREATE     — QPay-г дуудахаас ӨМНӨ оролдлогыг (raw.qpay_review_attempts + AuditLog
                   ATTEMPT_ACTION) commit хийж, ДАХИН түгжиж шалгаад create_and_assign
                   (ebarimt-diag-ийн зам; QPay-г НЭГ удаа). Үр дүн (SENT/REJECTED/NETWORK/…,
                   billId, сугалаа) оролдлого болон AuditLog-д; хадгалалт унавал _record_outcome.
      ATTACH_TEG — QPay-г ДУУДАХГҮЙ: ТЕГ-ийн ДДТД-г холбоно (баримт аль хэдийн бий)
    Амжилтад: SENT + ДДТД, ажил DONE (outbox/«баримт холбох»-той ижил), partner-д receipt.ready.
    Алдаанд: баримт REVIEW хэвээр. Үр дүн бүрд AuditLog (AUDIT_ACTION). Commit хийнэ. Хариунд
    attempt_outcome (CREATE), bill_id (QPay буцаасан бол)."""
    from .financial_jobs import enqueue_receipt_ready
    from .receipts import assign_ebarimt_id
    pid, rid = cand["payment_id"], cand["receipt_id"]
    base = {"payment_id": pid, "receipt_id": rid, "action": action}
    if action not in ("CREATE", "ATTACH_TEG"):
        return {**base, "outcome": "SKIPPED", "reason": "NO_ACTION"}
    st, skip = _lock_check(db, cand, include_failed=include_failed, retry_attempted=retry_attempted)
    if skip:
        return {**base, "outcome": "SKIPPED", **skip}
    payment, rec, job = st["payment"], st["rec"], st["job"]
    detail = {"tool": "qpay_review_resolve", "source": source, "receipt_id": rid, "action": action,
              "prev_status": rec.status, "job_status": job.status if job is not None else None,
              "qpay_payment_id": payment.provider_payment_id, "amount": float(payment.amount),
              "receiver_type": receiver_args(payment)[0], "customer_tin": payment.customer_tin,
              "teg": _js({k: v for k, v in (evidence or {}).items() if k != "near_rows"})}
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
        if ok:
            rec.status, rec.receipt_url = "SENT", None
            if job is not None:
                job.status, job.last_error = "DONE", None
                job.lease_token, job.lease_until = None, None
            enqueue_receipt_ready(db, payment, rec)
        err = None if ok else error_text({"ok": True, "receipt_updated": False, "conflict": rec.ddtd_note})
        detail.update(ok=ok, ebarimt_id=rec.ebarimt_id if ok else None, error=err)
        try:
            _audit(db, username, pid, detail)
            db.commit()
        except Exception:
            db.rollback()
            log.exception("ТЕГ ДДТД холболт хадгалагдсангүй payment=%s ДДТД=%s", pid, ddtd)
            raise
        return {**base, "outcome": "SENT" if ok else "FAILED", "ebarimt_id": rec.ebarimt_id if ok else None,
                "lottery": None, "error": err, "http_status": None}

    # ── CREATE: 1) оролдлогыг QPay-г дуудахаас ӨМНӨ тусдаа транзакцаар commit ──
    attempt = {"id": uuid.uuid4().hex, "at": _js(_now()), "by": username, "source": source,
               "outcome": "PENDING", "receipt_id": rid, "qpay_payment_id": payment.provider_payment_id}
    prior = [_js({k: a.get(k) for k in ("id", "at", "outcome", "http_status", "bill_id")})
             for a in st["attempts"]]
    detail.update(attempt_id=attempt["id"], attempt_at=attempt["at"], retry_attempted=bool(retry_attempted),
                  prior_attempts=prior)
    try:
        _set_attempt(rec, attempt)
        db.add(AuditLog(username=username, action=ATTEMPT_ACTION, entity="payment", entity_id=pid,
                        detail={"attempt_id": attempt["id"], "at": attempt["at"], "receipt_id": rid,
                                "qpay_payment_id": payment.provider_payment_id,
                                "amount": float(payment.amount), "source": source,
                                "retry_attempted": bool(retry_attempted), "prior_attempts": prior,
                                "teg": detail["teg"]}))
        db.commit()
    except Exception as e:  # noqa: BLE001
        db.rollback()
        log.exception("QPay оролдлогын бүртгэл хадгалагдсангүй payment=%s — QPay дуудагдаагүй", pid)
        return {**base, "outcome": "SKIPPED", "reason": "ATTEMPT_NOT_SAVED",
                "error": f"{type(e).__name__}: {str(e)[:200]}"}
    # ── 2) commit түгжээг суллав — ДАХИН түгжиж (өөрийн оролдлогыг тооцохгүй) бүгдийг шалгана ──
    st, skip = _lock_check(db, cand, include_failed=include_failed, retry_attempted=retry_attempted,
                           own=attempt["id"])
    if skip:
        attempt.update(outcome="NOT_CALLED", reason=skip["reason"], done_at=_js(_now()))
        _record_outcome(db, username, pid, rid, attempt,
                        {**detail, "ok": False, "attempt_outcome": "NOT_CALLED",
                         "error": f"QPay дуудагдаагүй: {skip['reason']}"})
        return {**base, "outcome": "SKIPPED", **skip, "attempt_outcome": "NOT_CALLED"}
    payment, rec, job = st["payment"], st["rec"], st["job"]
    # ── 3) QPay create (НЭГ удаа, түгжээтэй) ──
    res = await create_and_assign(db, payment, rec, acc=st["acc"], username=username,
                                  source=TOOL_SOURCE, commit=False)
    # ── 4) үр дүн: billId-г эхэлж барина — дараа нь юу ч болсон алдахгүй ──
    updated = bool(res.get("ok") and res.get("receipt_updated") and not res.get("save_error"))
    bill = res.get("bill_id") or ((rec.ebarimt_id or None) if updated else None)
    lottery = res.get("lottery") if res.get("bill_id") else (rec.lottery_code if updated else None)
    done = bool(updated and rec.status == "SENT" and is_ddtd(rec.ebarimt_id))
    outcome = classify_attempt(res, done)
    err = None if done else error_text(res)
    attempt.update(outcome=outcome, http_status=res.get("http_status"), bill_id=bill, lottery=lottery,
                   error=err, elapsed_ms=res.get("elapsed_ms"), done_at=_js(_now()))
    detail.update(ok=done, ebarimt_id=bill if done else None, bill_id=bill, lottery=lottery,
                  attempt_outcome=outcome, http_status=res.get("http_status"), error=err,
                  elapsed_ms=res.get("elapsed_ms"))
    if bill and not done:
        detail["qpay_raw"] = _js(res.get("raw")) if isinstance(res.get("raw"), dict) else None
    try:
        if res.get("save_error"):
            raise RuntimeError(res["save_error"])      # create_and_assign rollback хийсэн
        if done:
            if job is not None:
                job.status, job.last_error = "DONE", None
                job.lease_token, job.lease_until = None, None
            enqueue_receipt_ready(db, payment, rec)
        _set_attempt(rec, attempt)
        _audit(db, username, pid, detail)
        db.commit()
    except Exception as e:  # noqa: BLE001 — QPay дуудагдсан: үр дүнг (billId) ямар ч байсан бичнэ
        db.rollback()
        log.exception("QPay REVIEW нөхөлт хадгалагдсангүй payment=%s attempt=%s billId=%s сугалаа=%s",
                      pid, attempt["id"], bill, lottery)
        outcome = "NOT_SAVED" if bill else outcome
        err = (f"QPay ДДТД {bill} ирсэн ч хадгалагдсангүй: " if bill else "хадгалагдсангүй: ") + \
              f"{type(e).__name__}: {str(e)[:200]}"
        attempt.update(outcome=outcome, error=err[:300])
        _record_outcome(db, username, pid, rid, attempt,
                        {**detail, "ok": False, "ebarimt_id": None, "attempt_outcome": outcome,
                         "error": err[:300]})
        return {**base, "outcome": "FAILED", "ebarimt_id": None, "bill_id": bill, "lottery": lottery,
                "error": err[:300], "http_status": res.get("http_status"), "attempt_outcome": outcome}
    return {**base, "outcome": "SENT" if done else "FAILED", "ebarimt_id": bill if done else None,
            "bill_id": bill, "lottery": lottery if done else None, "error": err,
            "http_status": res.get("http_status"), "attempt_outcome": outcome}
