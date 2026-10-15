"""НӨАТ баримтын (VatReceipt) НЭГ ЭХ ҮНЭН — ДДТД сонголт ба өөрчлөлтийн хяналт.

2026-09-07 (3-р эмнэлэг, 9723УБТ): НЭГ төлбөрт ГУРВАН өөр ДДТД харагдав —
хэвлэсэн баримт …1797…, системд хадгалсан …1796…, ebarimt.mn-д …0851…. Кодын
дөрвөн газар ДДТД-г тус тусдаа сонгож/дарж бичдэг байв:
  • `_print_payload`, public receipt: `VatReceipt.filter(payment_id).first()` —
    нэг төлбөрт ОЛОН мөр (өрийн тусдаа баримт, цуцлагдаад дахин үүссэн, FAILED)
    байхад аль нь ч буцаж болно → хэвлэсэн ≠ Ибаримт хуудас.
  • msgbill `receipt.created` webhook: `rec.ebarimt_id = data.receipt_no` — POST-ын
    хариуд ирсэн (аль хэдийн ХЭВЛЭГДСЭН) дугаарыг ӨӨР дугаараар чимээгүй дардаг.
  • retry / QPay diag / гадны баримт холбох: тус тусдаа шууд бичилт.

Дүрэм (энэ модуль л хэрэгжүүлнэ):
  1. `primary_receipt()` — төлбөрийн «албан ёсны» баримт: SENT > CANCEL_PENDING >
     EXTERNAL > PENDING > REVIEW > FAILED > CANCELLED, тэнцвэл хамгийн ЭРТ үүссэн
     (толгой баримт өрийн баримтаас өмнө үүсдэг). Хэвлэх/харуулах/POS/public БҮГД үүгээр.
     EXTERNAL (2026-10-13) = POS терминал (Ontime) өөрөө гаргасан, систем үүсгээгүй —
     ДДТД-гүй ч ЭЦСИЙН төлөв тул түр зуурын PENDING/алдаанаас өмнө.
  2. `assign_ebarimt_id()` — ДДТД аль хэдийн байгаа мөрийг ӨӨР дугаараар ДАРАХГҮЙ:
     зөрчлийг `ddtd_note` + AuditLog(EBARIMT_ID_CONFLICT)-д бичиж, хадгалсан (хэвлэсэн)
     дугаарыг хэвээр үлдээнэ. Хоосон мөрөнд бичихэд ч AuditLog(EBARIMT_ID_SET).
  3. Сувгийн түүхий хариуг `raw`-д хадгална — «ebarimt.mn-д ямар дугаараар бүртгэгдсэн
     бэ» гэдгийг сувгийн хариунаас (msgbill receipt объект) мөшгөх боломжтой болно.
"""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy.orm import Session

from ..models import AuditLog, VatReceipt

log = logging.getLogger("parking.receipts")

_STATUS_RANK = {"SENT": 0, "CANCEL_PENDING": 1, "EXTERNAL": 2, "PENDING": 3, "REVIEW": 4,
                "FAILED": 5, "CANCELLED": 6}

# Цуцлалтын ШАЛТГААНЫ тэмдэг (2026-10-13): POS терминал (Ontime …10002990) аль
# хэдийн баримт хэвлэсэн төлбөрийн манай msgbill баримтыг ТЕГ тулгалтаар цуцалсан.
# Ийм төлбөрт «Дахин үүсгэх» дарвал ДАХИН давхардал үүснэ — retry 409 өгнө.
DUPLICATE_OF_POS = "DUPLICATE_OF_POS_TERMINAL"


def is_duplicate_cancel(rec) -> bool:
    """Энэ баримт «POS-ийн давхардал» гэж цуцлагдсан (эсвэл цуцлалт хүлээгдэж буй) юу."""
    if DUPLICATE_OF_POS in (getattr(rec, "ddtd_note", None) or ""):
        return True
    raw = getattr(rec, "raw", None)
    cancel = raw.get("cancel") if isinstance(raw, dict) else None
    return isinstance(cancel, dict) and cancel.get("reason") == DUPLICATE_OF_POS


# Дахин гаргалтын тэмдэг (2026-10-14): терминал (Ontime) баримт ХЭВЛЭЭГҮЙ нь ТЕГ
# экспортоор батлагдсан тул (tools/vat_reissue_missing.py) манай msgbill баримтыг
# ДАХИН гаргасан. Ийм баримтыг POS аппын дүрмээр (--pos-app) ДАХИН цуцлахгүй — тэр
# борлуулалтын цорын ганц e-Barimt.
REISSUED_NO_TERMINAL = "REISSUED_NO_TERMINAL_RECEIPT"


def is_reissued_no_terminal(rec) -> bool:
    raw = getattr(rec, "raw", None)
    mark = raw.get("reissue") if isinstance(raw, dict) else None
    return isinstance(mark, dict) and mark.get("reason") == REISSUED_NO_TERMINAL


# Баримтыг ХЭН гаргасан (2026-10-13): манай СИСТЕМИЙН сувгууд (None = хуучин PosAPI/
# QPay мөр; UNCONFIGURED = суваг тохируулаагүй үеийн манай оролдлого). Бусад нь
# (EXTERNAL төлөв, ONTIME/TERMINAL/POS-TERMINAL/EXT-* provider) — POS терминал/гадны
# системийн баримт: давхардлын цуцлалт, ТЕГ тулгалтын «манайх», кассын дугаарын
# таамаг ХӨНДӨХГҮЙ. Нэг л тодорхойлолт — financial_jobs, vat_recon, vat_duplicates бүгд.
SYSTEM_PROVIDERS = frozenset({"MSGBILL", "QPAY", "POSAPI", "UNCONFIGURED"})


def is_system_receipt(rec) -> bool:
    """Манай систем (msgbill/QPay/PosAPI) гаргасан баримт уу — терминал/гадны биш."""
    if getattr(rec, "status", None) == "EXTERNAL":
        return False
    return (getattr(rec, "provider", None) or "POSAPI") in SYSTEM_PROVIDERS


def rank_key(rec) -> tuple:
    """Сонголтын эрэмбэ — тест хийхэд тохиромжтой цэвэр функц."""
    return (_STATUS_RANK.get(rec.status or "", 9),
            0 if getattr(rec, "ebarimt_id", None) else 1,
            rec.created_at or datetime.max)


def choose_primary(receipts: list) -> VatReceipt | None:
    return min(receipts, key=rank_key) if receipts else None


def primary_receipt(db: Session, payment_id: str) -> VatReceipt | None:
    rows = db.query(VatReceipt).filter(VatReceipt.payment_id == payment_id).all()
    return choose_primary(rows)


def print_receipt(db: Session, payment) -> VatReceipt | None:
    """ХЭВЛЭХ баримт — төлбөрийн ҮНДСЭН (зогсоолын) баримтыг өрийн баримтаас ТҮРҮҮЛНЭ.

    Өр нийлүүлсэн төлбөрт өр тус бүрийн баримт `session_id`-аараа (өрийн зогсолт) ялгаатай;
    үндсэн/орлох/терминалын мөр бүгд төлбөрийн зогсолттой ижил session_id-тай. Үндсэн мөр
    алга (сешнгүй өрийн төлбөр г.м) бол бүх мөрөөс primary_receipt-ийн дүрмээр (2026-10-15)."""
    rows = db.query(VatReceipt).filter(VatReceipt.payment_id == payment.id).all()
    sid = getattr(payment, "session_id", None)
    main = [r for r in rows if sid and r.session_id == sid]
    return choose_primary(main or rows)


def receipt_qr(rec) -> str | None:
    """Хэвлэх/харуулах QR — ЗӨВХӨН энэ баримтынх (түр санах ойгоос, DB-д хэзээ ч үгүй).

    Түлхүүр нь баримтын id (2026-10-15): өрийн баримтын QR үндсэн баримтынхыг дарахгүй.
    SENT биш (PENDING/EXTERNAL/CANCELLED…) баримтад QR буцаахгүй — ДДТД-гүй эсвэл
    цуцлагдсан баримтын QR хэвлэгдэх ёсгүй."""
    if rec is None or getattr(rec, "status", None) != "SENT" or not getattr(rec, "ebarimt_id", None):
        return None
    from .ebarimt import get_cached_qr
    return get_cached_qr(rec.id)


def assign_ebarimt_id(db: Session, rec: VatReceipt, new_id: str | None, *,
                      source: str, lottery: str | None = None,
                      username: str = "system", raw: dict | None = None,
                      allow_replace: bool = False) -> bool:
    """ДДТД-г мөрөнд бичнэ. Буцаах: True = бичигдсэн, False = зөрчил (дараагүй).

    allow_replace=True — ЗӨВХӨН цуцлагдсан/FAILED мөрийг шинэ баримтаар нөхөх үед
    (retry). Идэвхтэй (SENT) мөрийн ДДТД-г ямар ч сувгаас ӨӨР дугаараар дарахгүй."""
    new_id = (new_id or "").strip() or None
    old = (getattr(rec, "ebarimt_id", None) or "").strip() or None
    g = lambda k: getattr(rec, k, None)  # noqa: E731 — тестийн хуурамч объект бүх талбаргүй байж болно
    if raw is not None:
        cur = getattr(rec, "raw", None)
        merged = dict(cur) if isinstance(cur, dict) else {}
        merged[source] = raw
        rec.raw = merged
    if not new_id:
        return False
    if old and old != new_id and g("status") == "SENT" and not allow_replace:
        note = f"ДДТД зөрүү ({source}): ирсэн {new_id} ≠ хадгалсан {old} — хадгалсныг хэвээр үлдээв"
        rec.ddtd_note = note[:300]
        db.add(AuditLog(username=username, action="EBARIMT_ID_CONFLICT", entity="vat_receipt",
                        entity_id=getattr(rec, "id", None),
                        detail={"payment_id": g("payment_id"), "stored": old, "incoming": new_id,
                                "source": source, "provider": g("provider"),
                                "provider_ref": g("provider_ref"),
                                "incoming_lottery": lottery, "stored_lottery": g("lottery_code")}))
        log.error("ДДТД ЗӨРЧИЛ payment=%s: %s → ирсэн %s ≠ хадгалсан %s (дараагүй)",
                  g("payment_id"), source, new_id, old)
        return False
    if old != new_id:
        db.add(AuditLog(username=username, action="EBARIMT_ID_SET", entity="vat_receipt",
                        entity_id=getattr(rec, "id", None),
                        detail={"payment_id": g("payment_id"), "from": old, "to": new_id,
                                "source": source, "lottery": lottery, "provider": g("provider")}))
    rec.ebarimt_id = new_id
    if lottery is not None and not g("customer_tin"):
        rec.lottery_code = lottery
    return True
