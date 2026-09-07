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
     PENDING > FAILED > CANCELLED, тэнцвэл хамгийн ЭРТ үүссэн (толгой баримт өрийн
     баримтаас өмнө үүсдэг). Хэвлэх/харуулах/POS/public БҮГД үүгээр.
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

_STATUS_RANK = {"SENT": 0, "CANCEL_PENDING": 1, "PENDING": 2, "FAILED": 3, "CANCELLED": 4}


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
