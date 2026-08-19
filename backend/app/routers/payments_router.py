"""Төлбөр: QPay invoice/webhook, e-Barimt 3.0, кассын бэлэн мөнгө, PAX POS баталгаажуулалт."""
import logging
import hmac
import re
import uuid
from datetime import datetime

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from ..auth import enforce_site, require, scoped_site
from ..config import settings
from ..database import get_db
from ..models import (AuditLog, CashierShift, Compensation, ParkingSession, Payment,
                      User, VatReceipt)
from ..ratelimit import throttle
from ..serializers import to_dict
from ..services import ebarimt, msgbill, qpay
from ..session_logic import amount_due, mark_paid_and_open, session_fee_info

log = logging.getLogger("parking.payments")

router = APIRouter(prefix="/api/payments", tags=["payments"])


def secrets_compare(a: str, b: str) -> bool:
    """Цагийн зөрүүнд суурилсан халдлагаас хамгаалсан харьцуулалт (webhook токен)."""
    return hmac.compare_digest((a or "").encode(), (b or "").encode())


def _lock_payment(db: Session, payment_id: str) -> Payment | None:
    """Payment мөрийг SELECT FOR UPDATE NOWAIT-аар түгжинэ.

    QPay webhook болон 5 сек polling ЗЭРЭГ орж ирэхэд хоёул finalize хийж давхар
    e-Barimt/өр төлөлт үүсгэдэг race-ээс сэргийлнэ: түгжээг авсан нь боловсруулж,
    аваагүй нь None авч одоогийн төлөвөө л буцаана. populate_existing нь нөгөө
    транзакцын commit хийсэн PAID төлөвийг шинээр уншуулна."""
    from sqlalchemy.exc import OperationalError
    try:
        # enable_eagerloads(False) — ЗААВАЛ: Payment-ийн lazy="joined" харилцаанууд
        # (session→site→tariff, discount) OUTER JOIN үүсгэдэг ба Postgres нь
        # "FOR UPDATE cannot be applied to the nullable side of an outer join"
        # гэж унадаг. Холбоосууд дараа нь хэрэгцээгээр (lazy) ачаалагдана.
        return (db.query(Payment).filter(Payment.id == payment_id)
                .enable_eagerloads(False)
                .populate_existing().with_for_update(nowait=True).first())
    except OperationalError:
        db.rollback()  # түгжээ авч чадсангүй — өөр request боловсруулж байна
        return None


def _site_of(payment: Payment):
    """Төлбөрийн зогсоол — QPay данс/e-Barimt-ыг тухайн түрээслэгчээр сонгоход.
    Session-гүй төлбөр практикт үүсдэггүй; тэр тохиолдолд глобал данс үйлчилнэ."""
    sess = getattr(payment, "session", None)
    return getattr(sess, "site", None) if sess is not None else None


def _receipt_desc(payment: Payment, extra: str | None = None) -> str:
    """msgbill баримтын тайлбар — дугаар + зогсоол (жолооч/санхүү танихад)."""
    sess = getattr(payment, "session", None)
    plate = getattr(sess, "plate_number", None) or ""
    site = _site_of(payment)
    site_name = getattr(site, "name", None) or ""
    parts = ["Зогсоолын үйлчилгээ", plate, site_name, extra or ""]
    return " · ".join(p for p in parts if p)[:200]


def _ebarimt_err(e: Exception) -> str:
    """e-Barimt/msgbill/QPay алдааг богино текст болгоно (VatReceipt.receipt_url-д)."""
    import httpx as _httpx
    if isinstance(e, _httpx.HTTPStatusError):
        try:
            return (e.response.json().get("message") or e.response.text[:200])
        except Exception:  # noqa: BLE001
            return e.response.text[:200]
    return str(e)[:200]


def _invoice_no(session: ParkingSession) -> str:
    """QPay/санхүүд харагдах гүйлгээний дугаар — машины дугаарыг шингээнэ
    (банкны хуулга, QPay порталтай тулгахад дугаараар олоход амар)."""
    site_code = session.site.site_code if session.site else "SITE"
    plate = re.sub(r"[^0-9A-ZА-ЯЁӨҮ]", "", (session.plate_number or "").upper())[:12]
    return f"{site_code}-{plate}-{datetime.utcnow():%Y%m%d}-{uuid.uuid4().hex[:6].upper()}"


async def _finalize_paid(db: Session, payment: Payment, raw: dict | None = None):
    """Төлбөр PAID болмогц: session PAID + barrier + e-Barimt.

    Нэгдсэн (өр багтсан) төлбөрт: session-ий хэсэгт нэг баримт, холбогдсон ӨР ТУС
    БҮРД ТУСДАА баримт үүсгэж, нөхөн төлбөрүүдийг PAID болгоно.

    ХУГАЦААНЫ ХЭМЖИЛТ: «төлбөр төлсний дараа хаалт удаан нээгдэж байна» гомдлыг
    үе шат тус бүрээр нь ялгаж хэмжинэ (e-Barimt vs хаалт vs DB) — эс бол аль нь
    удааширч байгааг таамаглах болно."""
    import time as _t
    _p0 = _t.monotonic()
    _marks: list[str] = []

    def _mark(label: str, since: float) -> float:
        _marks.append(f"{label}={int((_t.monotonic() - since) * 1000)}мс")
        return _t.monotonic()

    if payment.status == "PAID":
        return  # idempotent — давхар webhook хамгаалалт
    payment.status = "PAID"
    payment.paid_at = datetime.utcnow()
    if raw:
        payment.raw_payload = raw

    # Энэ төлбөрт багтсан (QR-аар нийлүүлж төлсөн) өрүүд — тус бүрийн НӨАТ,
    # session-ий хэсгийн дүнг ялгаж тооцно
    comps = (db.query(Compensation)
             .filter(Compensation.payment_id == payment.id,
                     Compensation.status == "PENDING").all())
    vat_r = settings.vat_rate
    comp_vats = {c.id: round(float(c.amount) * vat_r / (1 + vat_r)) for c in comps}
    sess_amount = float(payment.amount) - sum(float(c.amount) for c in comps)
    sess_vat = float(payment.vat_amount) - sum(comp_vats.values())

    receiver_type = payment.ebarimt_receiver_type or ("COMPANY" if payment.customer_tin else "CITIZEN")
    # QR-аар (QPay) төлсөн бол QPay-ийн ebarimt_v3-аар; бэлэн/картаар бол локал PosAPI-аар.
    #
    # 2026-08-12: өмнө нь `and not comps` нөхцөлтэй байсан — ӨР БАГТСАН QPay
    # төлбөр локал PosAPI руу уначихдаг байв. Тэр зам нь (а) ebarimt_mock=true
    # үед ХУУРАМЧ баримт буцаадаг (production дээр яг тийм байсан), (б) ГЛОБАЛ
    # merchant TIN хэрэглэдэг — зогсоолын түрээслэгчийнхээр БИШ. Өөрөөр хэлбэл
    # өргүй жолооч жинхэнэ баримт авч, өртэй жолооч хуурамч баримт авдаг байв.
    #
    # Одоо: өр нь QPay-ийн нэхэмжлэлд АЛЬ ХЭДИЙН тусдаа мөрөөр орсон байдаг тул
    # ebarimt_v3 нь бүтэн дүнг (өнөөдрийн төлбөр + өр бүр) хамарсан НЭГ ЖИНХЭНЭ
    # баримт үүсгэнэ — түрээслэгчийн ТТД-ээр. Өрүүд ижил баримтад холбогдоно.
    use_qpay_eb = (settings.qpay_ebarimt and payment.provider == "QPAY"
                   and bool(payment.provider_payment_id))
    # msgbill.mn (Үйлчилгээ 3 — eBarimt API): QPay-ээр төлөөгүй төлбөрт (анхдагчаар
    # ДАНСААР = online operator) локал PosAPI-ийн оронд msgbill-ээр ЖИНХЭНЭ баримт.
    # Түлхүүр: түрээслэгч → глобал (services/msgbill.api_key_for-ийн дүрмээр).
    mb_acc = None if use_qpay_eb else msgbill.account_enabled_for(
        _site_of(payment), payment.payment_method)
    use_msgbill = mb_acc is not None
    # PosAPI суугаагүй (MOCK) + msgbill түлхүүргүй → хуурамч баримт ҮҮСГЭХГҮЙ,
    # FAILED гэж бүртгээд шалтгааныг бичнэ (дараа msgbill тохируулаад «Дахин үүсгэх»)
    no_channel = (settings.ebarimt_mock and not settings.ebarimt_mock_receipts
                  and not use_qpay_eb and not use_msgbill)
    if no_channel:
        log.warning("e-Barimt: payment=%s %s₮ — бодит баримтын суваг байхгүй (PosAPI MOCK, "
                    "msgbill түлхүүргүй) → FAILED гэж бүртгэв", payment.id, float(payment.amount))
    elif settings.ebarimt_mock and not use_qpay_eb and not use_msgbill:
        log.warning("e-Barimt MOCK: payment=%s %s₮ — ХУУРАМЧ баримт үүслээ. "
                    "Production дээр PARKING_EBARIMT_MOCK=false байх ёстой.",
                    payment.id, float(payment.amount))
    # ВАЖНО: e-Barimt амжилтгүй болсон ч төлбөрийг PAID болгож ХААЛТЫГ НЭЭНЭ —
    # жолооч төлсөн атлаа гацахгүй. Баримтыг FAILED болгож дараа дахин үүсгэж болно.
    receipt_raw, ebarimt_error = {}, None
    _NO_CHANNEL_MSG = ("Баримтын суваг байхгүй — PosAPI суугаагүй (MOCK), msgbill түлхүүр "
                       "тохируулаагүй. Тохиргоо → Холболт → e-Barimt API-д түлхүүр тавиад "
                       "«Дахин үүсгэх» дарна уу")
    try:
        if no_channel:
            ebarimt_error = _NO_CHANNEL_MSG
        elif use_qpay_eb:
            receipt_raw = await qpay.create_ebarimt(
                payment.provider_payment_id, receiver_type,
                # COMPANY үед ААН регистр (ТТД)-ийг ebarimt_receiver болгон дамжуулна
                receiver=payment.customer_tin if receiver_type == "COMPANY" else None,
                # Зогсоолын өөрийн QPay данс — баримт нь тухайн түрээслэгчийн ТТД-ээр үүснэ
                acc=qpay.account_for(_site_of(payment)),
            )
        elif use_msgbill:
            receipt_raw = await msgbill.create_receipt(
                mb_acc, sess_amount,
                description=_receipt_desc(payment),
                payment_method=payment.payment_method,   # TRANSFER→BANK_TRANSFER, CASH, CARD
                # Idempotency-Key = payment id: давхар finalize/retry-д давхар баримт үүсэхгүй
                idempotency_key=f"pay-{payment.id}",
                customer_tin=payment.customer_tin,       # ААН → ORGANIZATION + payer_reg_no
            )
            if not receipt_raw.get("billId"):
                ebarimt_error = (receipt_raw.get("error")
                                 or f"msgbill төлөв {receipt_raw.get('state') or '?'} — ДДТД ирээгүй")
        else:
            receipt_raw = await ebarimt.create_receipt(
                sess_amount, sess_vat,
                # eBarimt 3.0-д зөвхөн CASH/PAYMENT_CARD код бий — дансаар (TRANSFER)
                # шилжүүлсэн төлбөрийг CASH кодоор бүртгэнэ (API мөнгөн урсгал шалгадаггүй)
                "CASH" if payment.payment_method in ("CASH", "TRANSFER") else "CARD",
                customer_tin=payment.customer_tin,  # байгууллагаар авах бол B2B баримт
            )
    except Exception as e:  # noqa: BLE001 — баримтын алдаа хаалтыг зогсоохгүй
        ebarimt_error = _ebarimt_err(e)
        log.error(f"e-Barimt амжилтгүй: payment={payment.id}: {ebarimt_error}")
    rcpt_provider = "QPAY" if use_qpay_eb else ("MSGBILL" if use_msgbill else "POSAPI")

    # ТЕГ шаардлага №11: qrData-г DB-д ХАДГАЛАХГҮЙ — түр санах ойд (баримт үзүүлэх/хэвлэх хугацаанд)
    ebarimt.cache_qr(payment.id, receipt_raw.get("qrData"))
    # QPay-ийн ebarimt_v3 нь ТӨЛБӨРИЙН БҮТЭН дүнгээр (нэхэмжлэлийн мөр бүрийг
    # багтаасан) НЭГ баримт үүсгэдэг — тиймээс өр багтсан үед ч дүнг задалж
    # бичихгүй, бүтнээр нь бүртгэнэ. Локал PosAPI үед л хэсэг тус бүрд тусдаа.
    head_amount = float(payment.amount) if use_qpay_eb else sess_amount
    head_vat = float(payment.vat_amount) if use_qpay_eb else sess_vat
    db.add(VatReceipt(
        payment_id=payment.id, session_id=payment.session_id,
        ebarimt_id=receipt_raw.get("billId"),
        # ААН-ны баримтад сугалаа олгогдохгүй (шаардлага №1, №16)
        lottery_code=None if receiver_type == "COMPANY" else receipt_raw.get("lottery"),
        amount=head_amount, vat_amount=head_vat,
        customer_tin=payment.customer_tin,
        # Баримт үүссэн бол SENT, алдаатай бол FAILED (дараа дахин оролдоно), тэмдэглэлд алдаа.
        # msgbill FAILED/PENDING үед provider_ref (rcp_…) хадгалж дараа GET-ээр нөхнө.
        status="SENT" if receipt_raw.get("billId") else "FAILED",
        receipt_url=ebarimt_error,
        provider=rcpt_provider, provider_ref=receipt_raw.get("msgbillId"),
    ))

    for comp in comps:
        comp_amount, comp_vat = float(comp.amount), comp_vats[comp.id]
        if use_qpay_eb:
            # Өр нь дээрх НЭГ ЖИНХЭНЭ баримтад аль хэдийн багтсан (QPay
            # нэхэмжлэлд тусдаа мөрөөр орсон) — давхар баримт үүсгэхгүй.
            ebarimt.cache_qr(comp.id, receipt_raw.get("qrData"))
        else:
            # Локал PosAPI / msgbill (бэлэн/карт/дансаар): хэсэг тус бүрд тусдаа баримт
            comp_receipt, comp_error = {}, None
            try:
                if no_channel:
                    comp_error = _NO_CHANNEL_MSG
                elif use_msgbill:
                    comp_receipt = await msgbill.create_receipt(
                        mb_acc, comp_amount,
                        description=_receipt_desc(payment, f"өр {comp.plate_number or ''}".strip()),
                        payment_method=payment.payment_method,
                        idempotency_key=f"pay-{payment.id}-comp-{comp.id}",
                        customer_tin=payment.customer_tin)
                    if not comp_receipt.get("billId"):
                        comp_error = (comp_receipt.get("error")
                                      or f"msgbill төлөв {comp_receipt.get('state') or '?'}")
                else:
                    comp_receipt = await ebarimt.create_receipt(
                        comp_amount, comp_vat, "CARD", customer_tin=payment.customer_tin)
            except Exception as e:  # noqa: BLE001
                comp_error = _ebarimt_err(e)
                log.error(f"e-Barimt амжилтгүй: compensation={comp.id}: {comp_error}")
            ebarimt.cache_qr(comp.id, comp_receipt.get("qrData"))
            db.add(VatReceipt(
                # Өрийн анхны session (байхгүй бол одоо төлж буй session-д) холбоно
                payment_id=payment.id, session_id=comp.session_id or payment.session_id,
                ebarimt_id=comp_receipt.get("billId"),
                lottery_code=None if receiver_type == "COMPANY" else comp_receipt.get("lottery"),
                amount=comp_amount, vat_amount=comp_vat, customer_tin=payment.customer_tin,
                status="SENT" if comp_receipt.get("billId") else "FAILED",
                receipt_url=comp_error,
                provider=rcpt_provider, provider_ref=comp_receipt.get("msgbillId"),
            ))
        comp.status = "PAID"
        comp.paid_at = datetime.utcnow()
        comp.paid_by = f"{payment.provider}:QR"
        log.info(f"өр төлөгдөв: {comp.plate_number} {comp_amount:.0f}₮ "
              f"(comp {comp.id}, payment {payment.id}, "
              f"баримт={rcpt_provider})")

    _t1 = _mark("ebarimt", _p0)
    session = db.get(ParkingSession, payment.session_id)
    await mark_paid_and_open(db, session)
    _mark("хаалт+session", _t1)
    total = int((_t.monotonic() - _p0) * 1000)
    (log.warning if total >= settings.payment_slow_warn_ms else log.info)(
        "төлбөр finalize: %dмс (%s) provider=%s", total, " ".join(_marks), payment.provider)


async def _confirm_qpay(db: Session, payment: Payment) -> bool:
    """QPay төлбөрийг баталгаажуулж (mock үед шууд), g_payment_id аваад finalize хийнэ.
    Буцаах: PAID болсон эсэх. Дүн зөрвөл REVIEW болгож False буцаана."""
    if payment.status == "PAID":
        return True
    if settings.qpay_mock:
        # Туршилтын горим — QPay ebarimt_v3-ийн урсгалыг бүрэн дуурайхын тулд mock payment_id тавина
        if not payment.provider_payment_id:
            payment.provider_payment_id = f"MOCK-PAY-{uuid.uuid4().hex[:12]}"
        await _finalize_paid(db, payment)
        return True
    res = await qpay.check_payment(payment.provider_invoice_id,
                                   acc=qpay.account_for(_site_of(payment)))
    if not res.get("paid"):
        return False
    paid_amount = float(res.get("paid_amount") or 0)
    expected = float(payment.amount)
    # ДУТУУ төлсөн бол л REVIEW — жолоочийг гаргахгүй, оператор шалгана.
    # ИЛҮҮ төлсөн тохиолдолд машиныг зогсоолд ХОРИХ нь буруу: мөнгө нь бүрэн
    # ирсэн байтал хаалт нээгдэхгүй үлддэг байв (мерчантын НӨАТ-ын тохиргооноос
    # болж QPay нэхэмжлэлийн дүн дээр татвар нэмж тооцох тохиолдол бий).
    if paid_amount < expected - 1:
        payment.status = "REVIEW"
        payment.raw_payload = res.get("raw") or {}
        db.commit()
        log.warning(f"ДУТУУ төлөгдсөн: {payment.id} нэхэмжлэл={expected:.2f} "
              f"төлсөн={paid_amount:.2f} → REVIEW")
        return False
    if paid_amount > expected + 1:
        # Илүү төлсөнийг бүртгэлд үлдээнэ (санхүү тулгахад хэрэгтэй) ч гаргана
        log.warning(f"ИЛҮҮ төлөгдсөн: {payment.id} нэхэмжлэл={expected:.2f} "
              f"төлсөн={paid_amount:.2f} зөрүү={paid_amount - expected:.2f} "
              f"— хаалтыг нээж байна. QPay мерчантын НӨАТ тохиргоог шалгана уу.")
    if res.get("payment_id"):
        payment.provider_payment_id = res["payment_id"]
    await _finalize_paid(db, payment, raw=res.get("raw"))
    return True


def _print_payload(db: Session, payment: Payment) -> dict:
    """POS терминал/веб-д хэвлэх e-Barimt баримтын мэдээлэл (мөрүүд + QR + сугалаа)."""
    session = db.get(ParkingSession, payment.session_id)
    receipt = db.query(VatReceipt).filter(VatReceipt.payment_id == payment.id).first()
    lines = [
        "ЗОГСООЛЫН ТӨЛБӨРИЙН БАРИМТ",
        f"Дугаар: {session.plate_number if session else '-'}",
        f"Орсон: {session.entry_time:%Y-%m-%d %H:%M}" if session and session.entry_time else "",
        f"Хугацаа: {session.duration_minutes} мин" if session and session.duration_minutes else "",
        f"Дүн: {float(payment.amount):,.0f}₮",
        f"НӨАТ: {float(payment.vat_amount):,.0f}₮",
        f"ДДТД: {receipt.ebarimt_id if receipt else '-'}",
    ]
    if payment.customer_tin:
        lines.append(f"Худалдан авагч ТТД: {payment.customer_tin}")  # ААН — сугалаа хэвлэгдэхгүй
    elif receipt and receipt.lottery_code:
        lines.append(f"Сугалаа: {receipt.lottery_code}")
    return {
        "ebarimt_id": receipt.ebarimt_id if receipt else None,
        "lottery_code": receipt.lottery_code if receipt else None,
        # thermal printer энэ qrData-г QR болгон хэвлэнэ (түр санах ойгоос — DB-д хадгалагдахгүй)
        "qr_data": ebarimt.get_cached_qr(payment.id),
        "print_data": {"lines": [ln for ln in lines if ln]},
    }


def _pending_debts(db: Session, plate: str) -> list[Compensation]:
    """Дугаарын төлөгдөөгүй нөхөн төлбөрүүд (бүх зогсоолын — өр дугаарыг дагадаг)."""
    return (db.query(Compensation)
            .filter(Compensation.plate_number == plate,
                    Compensation.status == "PENDING").all())


def _create_payment(db: Session, session: ParkingSession, provider: str, method: str,
                    cashier: User | None = None, include_debts: bool = False) -> Payment:
    fee = session_fee_info(db, session)
    if fee["total_fee"] <= 0:
        raise HTTPException(400, "Төлбөр шаардлагагүй (үнэгүй) session байна")
    # Grace хэтэрч дахин ирсэн session: өмнө төлснийг хасаад зөвхөн үлдэгдлийг нэхнэ
    due = amount_due(db, session, fee)
    if due <= 0:
        raise HTTPException(400, "Төлбөр бүрэн төлөгдсөн байна — нэмэлт төлбөр шаардлагагүй")
    if due >= fee["total_fee"]:
        vat_due = fee["vat_amount"]  # анхны төлбөр — тооцоолсон НӨАТ-ыг шууд ашиглана
    else:
        # Үлдэгдэлд ногдох НӨАТ (total_fee ямагт НӨАТ багтсан эцсийн дүн)
        r = settings.vat_rate
        vat_due = round(due * r / (1 + r))
    session.base_fee, session.vat_amount, session.total_fee = (
        fee["base_fee"], fee["vat_amount"], fee["total_fee"])
    session.duration_minutes = fee["duration_minutes"]

    # Өмнөх өрийг нийлүүлж нэхэмжлэх: дүн + өр тус бүрийн НӨАТ (үнэд багтсан)
    comps: list[Compensation] = _pending_debts(db, session.plate_number) if include_debts else []
    debt_total = sum(float(c.amount) for c in comps)
    debt_vat = sum(round(float(c.amount) * settings.vat_rate / (1 + settings.vat_rate))
                   for c in comps)

    shift = None
    if cashier:
        shift = db.query(CashierShift).filter(CashierShift.user_id == cashier.id,
                                              CashierShift.status == "OPEN").first()
    payment = Payment(
        session_id=session.id, provider=provider, payment_method=method,
        sender_invoice_no=_invoice_no(session),
        amount=due + debt_total, vat_amount=vat_due + debt_vat,
        cashier_id=cashier.id if cashier else None,
        shift_id=shift.id if shift else None,
    )
    db.add(payment)
    db.flush()
    # Өрүүдийг энэ төлбөрт холбоно (PENDING статус л эрх мэдэлтэй — өмнөх орхигдсон
    # invoice-ийн холбоосыг дарж бичихэд аюулгүй)
    for comp in comps:
        comp.payment_id = payment.id
    # Дуудагч талд шууд хэрэглэх (autoflush=False тул дахин query найдваргүй)
    payment._debt_comps = comps  # type: ignore[attr-defined]
    return payment


def _throttle_qpay(request: Request, name: str, limit: int = 30):
    """Нэвтрэлтгүй QPay endpoint-үүдийн IP хязгаар — public pay page-д хангалттай,
    спамд (дурын session-д invoice үүсгэх, QPay руу давтан дуудлага) хаалт болно."""
    ip = request.client.host if request.client else "?"
    if throttle(f"qpay:{name}:{ip}", limit=limit, window=60):
        raise HTTPException(429, "Хэт олон хүсэлт — түр хүлээгээд дахин оролдоно уу")


# ─────────────────────────── QPay ───────────────────────────
@router.post("/qpay/invoice")
async def qpay_invoice(body: dict, request: Request, db: Session = Depends(get_db)):
    """QPay invoice үүсгэх. body: {session_id}. Public pay page + касс хоёулаа ашиглана."""
    # 2026-08-19: 20 → 60/мин. Утасны операторын CGNAT-аар олон жолооч НЭГ IP-ээр
    # ирдэг тул ачаалалтай зогсоолд 20 нь хүрэлцэхгүй, «товч анивчаад юу ч
    # болохгүй» (429 чимээгүй) гомдол гарч байв.
    _throttle_qpay(request, "invoice", limit=60)
    session = db.get(ParkingSession, body.get("session_id", ""))
    if not session:
        raise HTTPException(404, "Session олдсонгүй")
    if session.status not in ("OPEN", "AWAITING_PAYMENT"):
        raise HTTPException(400, f"Session төлөв буруу: {session.status}")

    # Өмнөх өрийг QR-д нийлүүлж нэхэмжилнэ (body-д include_debts=false өгвөл зөвхөн
    # одоогийн төлбөр — кассын тусгай хэрэглээнд)
    include_debts = bool(body.get("include_debts", True))

    # Өмнө үүсгэсэн PENDING invoice байвал дүн нь одоогийн үлдэгдэл (+өр)-тэй таарч
    # байгаа тохиолдолд л дахин ашиглана (хугацаа өнгөрч тариф өссөн эсвэл
    # grace хэтэрч зөрүү нэхэж буй үед хуучин QR буруу дүнтэй болно)
    existing = db.query(Payment).filter(Payment.session_id == session.id,
                                        Payment.provider == "QPAY",
                                        Payment.status == "PENDING").first()
    if existing and existing.provider_invoice_id:
        current_due = amount_due(db, session, session_fee_info(db, session))
        if include_debts:
            current_due += sum(float(c.amount) for c in _pending_debts(db, session.plate_number))
        if abs(float(existing.amount) - current_due) <= 1:
            # qr_image-ыг заавал буцаана: өмнө нь энэ (ДАВТАН хүсэлтийн) зам
            # түүнийг огт өгдөггүй байсан тул хуудсаа сэргээсэн жолоочид QR-ийн
            # оронд түүхий текст гарч, төлж чаддаггүй байв (2026-08-14 гомдол)
            # Банкны deeplink жагсаалтыг ч буцаана — үүсгэх үед raw_payload-д
            # хадгалсан (өмнө нь [] буцаадаг байсан тул буцаж орсон жолоочид
            # банк сонгох товчнууд алга болдог байв)
            _raw = existing.raw_payload if isinstance(existing.raw_payload, dict) else {}
            return {"payment_id": existing.id, "invoice_id": existing.provider_invoice_id,
                    "qr_text": existing.qr_text,
                    "qr_image": qpay.qr_png_b64(existing.qr_text or ""),
                    "deep_link": existing.deep_link, "urls": _raw.get("invoice_urls") or [],
                    "amount": float(existing.amount), "mock": settings.qpay_mock}
        existing.status = "CANCELLED"  # дүн зөрсөн — шинэ invoice үүсгэнэ
        db.flush()

    payment = _create_payment(db, session, "QPAY", "QR", include_debts=include_debts)
    payment.source = "POS" if body.get("source") == "POS" else "QR"  # кассын пос эсвэл утас
    # НӨАТ-ийн баримтын хүлээн авагчийн төрөл: ТТД өгвөл ААН (COMPANY), үгүй бол иргэн (CITIZEN)
    receiver_data = None
    if body.get("customer_tin"):
        payment.customer_tin = str(body["customer_tin"]).strip()[:20]
        payment.ebarimt_receiver_type = "COMPANY"
        receiver_data = {"register": payment.customer_tin}
    else:
        payment.ebarimt_receiver_type = "CITIZEN"
    callback = f"{settings.public_base_url}/api/payments/qpay/webhook?payment_id={payment.id}"
    if settings.qpay_webhook_secret:
        callback += f"&token={settings.qpay_webhook_secret}"
    # e-Barimt нэхэмжлэхийн мөрүүд — session-ий төлбөр нэг мөр, өр тус бүр тусдаа мөр
    linked_comps = getattr(payment, "_debt_comps", [])
    debt_total = sum(float(c.amount) for c in linked_comps)
    line_items = [{
        "description": f"Зогсоолын үйлчилгээ — {session.plate_number}",
        "unit_price": float(payment.amount) - debt_total, "quantity": 1,
    }]
    for comp in linked_comps:
        line_items.append({
            "description": f"Өмнөх өр ({comp.created_at:%Y-%m-%d}) — {comp.plate_number}",
            "unit_price": float(comp.amount), "quantity": 1,
        })
    acc = qpay.account_for(session.site)
    lines = qpay.build_lines(line_items, acc)
    # QPay унах нь ЖОЛООЧИД харагддаг алдаа — «QR гарахгүй байна» гомдол. Өмнө нь
    # raise_for_status()/timeout нь endpoint-оос шууд гарч 500 болдог байсан:
    # (а) жолоочид ойлгомжгүй, (б) rollback болохоор DB-д ЯМАР Ч ул мөр үлддэггүй
    # тул «хэдэн удаа тохиолдов» гэдгийг хойшоо хэмжих боломжгүй байв. Одоо
    # шалтгааныг логлож, хүнд ойлгомжтой 502 буцаана (frontend дахин оролдоно).
    try:
        inv = await qpay.create_invoice(
            payment.sender_invoice_no,
            f"Зогсоолын төлбөр — {session.plate_number}",
            f"terminal_{session.site.site_code if session.site else 'X'}", callback,
            lines, receiver_data=receiver_data, acc=acc,
        )
    except httpx.HTTPStatusError as e:
        body_txt = (e.response.text or "")[:400]
        log.error("QPay invoice БҮТЭЛГҮЙ (%s, %s): HTTP %s — %s",
                  session.plate_number, acc.username, e.response.status_code, body_txt)
        db.rollback()
        raise HTTPException(502, "QPay-тэй холбогдож чадсангүй — QR үүсгэж чадаагүй. "
                                 "Дахин оролдоно уу, эсвэл кассаар төлнө үү.")
    except httpx.HTTPError as e:  # timeout, DNS, холболт тасрах
        log.error("QPay invoice БҮТЭЛГҮЙ (%s, %s): %r", session.plate_number, acc.username, e)
        db.rollback()
        raise HTTPException(502, "QPay хариу өгсөнгүй (сүлжээ) — QR үүсгэж чадаагүй. "
                                 "Дахин оролдоно уу, эсвэл кассаар төлнө үү.")
    except KeyError as e:  # QPay 200 буцаасан ч хүлээгдсэн талбар алга
        log.error("QPay invoice хариу дутуу (%s): талбар %s алга", session.plate_number, e)
        db.rollback()
        raise HTTPException(502, "QPay-ээс ирсэн хариу дутуу байна — QR үүсгэж чадаагүй. "
                                 "Дахин оролдоно уу, эсвэл кассаар төлнө үү.")
    payment.provider_invoice_id = inv["invoice_id"]
    payment.qr_text = inv["qr_text"]
    payment.deep_link = inv["deep_link"]
    # Банкны deeplink-үүдийг хадгална — давтан хүсэлт (хуудас сэргээх/буцаж орох)
    # ижил нэхэмжлэлийг бүрэн (QR + банкны товчнууд) буцаахад хэрэгтэй.
    # finalize үед raw_payload QPay-ийн төлбөрийн хариугаар дарагдана — зүгээр.
    payment.raw_payload = {"invoice_urls": inv.get("urls", [])[:40]}
    db.commit()
    return {"payment_id": payment.id, "invoice_id": inv["invoice_id"],
            "qr_text": inv["qr_text"], "qr_image": inv.get("qr_image", ""),
            "deep_link": inv["deep_link"], "urls": inv.get("urls", []),
            "amount": float(payment.amount), "debt_amount": debt_total,
            "mock": inv.get("mock", False)}


async def _webhook_handler(payment_id: str, token: str, qpay_payment_id: str,
                           body: dict, db: Session):
    """QPay callback-ийн нийтлэг логик (GET/POST хоёуланд).

    QPay стандарт: callback нь GET, HTTP 200 + body 'SUCCESS' буцаах ёстой. Тиймээс
    төлбөрийг QPay-ийн payment/check-ээр эргэж баталгаажуулна (query дахь дүнд итгэхгүй)."""
    if settings.qpay_webhook_secret:
        if not secrets_compare(token, settings.qpay_webhook_secret):
            raise HTTPException(403, "Webhook токен буруу")
    payment = None
    if payment_id:
        payment = db.get(Payment, payment_id)
    if not payment and body.get("sender_invoice_no"):
        payment = db.query(Payment).filter(
            Payment.sender_invoice_no == body["sender_invoice_no"]).first()
    if not payment:
        raise HTTPException(404, "Payment олдсонгүй")
    # Мөрийг түгжинэ — polling/давхар webhook зэрэг орж ирвэл нэг нь л боловсруулна
    payment = _lock_payment(db, payment.id)
    if payment is None:
        return  # өөр request яг одоо баталгаажуулж байна — QPay-д SUCCESS буцаахад хангалттай
    # QPay-ийн дамжуулсан payment_id-г эхлээд авна (баталгаажуулалтын check амжилтгүй бол ч)
    if qpay_payment_id:
        payment.provider_payment_id = str(qpay_payment_id)
    # Бодит баталгаажуулалт: payment/check → paid + дүн + g_payment_id
    await _confirm_qpay(db, payment)
    db.commit()


@router.get("/qpay/webhook")
async def qpay_webhook_get(payment_id: str = "", token: str = "",
                           qpay_payment_id: str = "", db: Session = Depends(get_db)):
    """QPay callback (GET) — стандарт форматаар 'SUCCESS' plain text буцаана."""
    await _webhook_handler(payment_id, token, qpay_payment_id, {}, db)
    return PlainTextResponse("SUCCESS")


@router.post("/qpay/webhook")
async def qpay_webhook_post(request: Request, payment_id: str = "", token: str = "",
                            qpay_payment_id: str = "", db: Session = Depends(get_db)):
    """QPay callback (POST) — зарим тохиргоонд body-той POST-оор ирдэг. 'SUCCESS' буцаана.
    Мөн pay page-ийн туршилтын горим (mock) энэ endpoint-ийг дууддаг."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not qpay_payment_id:
        qpay_payment_id = body.get("qpay_payment_id") or body.get("payment_id") or ""
    await _webhook_handler(payment_id, token, qpay_payment_id, body, db)
    return PlainTextResponse("SUCCESS")


@router.post("/qpay/check/{payment_id}")
async def qpay_check(payment_id: str, request: Request, db: Session = Depends(get_db)):
    """Webhook ирээгүй үед polling шалгалт (pay page/POS 5 сек тутам дуудна).
    PAID болмогц e-Barimt-ийн хэвлэх мэдээллийг (POS терминалд) хамт буцаана."""
    # 5 сек тутам poll = 12/мин/утас; CGNAT-аар нэг IP-д олон жолооч → 300
    _throttle_qpay(request, "check", limit=300)
    payment = db.get(Payment, payment_id)
    if not payment:
        raise HTTPException(404, "Payment олдсонгүй")
    if payment.status != "PAID" and payment.provider == "QPAY" and payment.provider_invoice_id:
        locked = _lock_payment(db, payment.id)
        if locked is not None:
            payment = locked
            await _confirm_qpay(db, payment)
            db.commit()
        # түгжээг webhook авчихсан бол энэ poll юу ч хийхгүй — дараагийн poll-д PAID харагдана
    if payment.status == "PAID":
        return {"status": "PAID", **_print_payload(db, payment)}
    return {"status": payment.status}


# ─────────────────────────── Касс (бэлэн мөнгө) ───────────────────────────
# ЭРХ (бүх кассын endpoint): role биш «cashier» МОДУЛИАР. Өмнө нь
# require_role("OPERATOR", "SUPER_ADMIN") байсан тул:
#   • ADMIN — кассын хуудас нээгддэг ч төлбөр авахад 403 («операторын эрх
#     шаардаж байна» гэсэн гомдол яг үүнээс)
#   • ONLINE_OPERATOR — pay_transfer эрхтэй атлаа /transfer рүү огт ороогүй
#     (энэ ролийн БҮХ утга учир нь дансаар төлбөр батлах)
# free_exit шиг санхүүгийн эрсдэлтэй тусгай үйлдлүүд ХЭВЭЭР тусдаа эрхтэй.
@router.post("/cash")
async def cash_payment(body: dict, db: Session = Depends(get_db),
                       user: User = Depends(require("cashier"))):
    """Кассын бэлэн мөнгөний төлбөр. body: {session_id}"""
    session = db.get(ParkingSession, body.get("session_id", ""))
    if not session:
        raise HTTPException(404, "Session олдсонгүй")
    enforce_site(user, session.site_id)  # оператор зөвхөн өөрийн зогсоолын төлбөр
    if session.status not in ("OPEN", "AWAITING_PAYMENT"):
        raise HTTPException(400, f"Session төлөв буруу: {session.status}")
    payment = _create_payment(db, session, "CASH", "CASH", cashier=user)
    await _finalize_paid(db, payment)
    db.add(AuditLog(username=user.username, action="CASH_PAYMENT", entity="payment",
                    entity_id=payment.id, detail={"amount": float(payment.amount)}))
    db.commit()
    return {"ok": True, "payment_id": payment.id, "amount": float(payment.amount)}


# ─────────────────────────── Касс (дансаар / шилжүүлэг) ───────────────────────────
@router.post("/transfer")
async def transfer_payment(body: dict, db: Session = Depends(get_db),
                           user: User = Depends(require("cashier"))):
    """Дансаар (банкны шилжүүлгээр) төлбөр авах — online operator-ын урсгал.

    Оператор жолоочид зогсоолын дансыг хэлж, шилжүүлэг ОРЖ ИРСНИЙГ хуулгаас
    хараад энэ endpoint-оор баталгаажуулна → хаалт нээгдэж, e-Barimt үүснэ
    (eBarimt 3.0-д шилжүүлгийн тусдаа код байхгүй тул CASH кодоор бүртгэгдэнэ).
    body: {session_id, customer_tin?} — ТТД өгвөл байгууллагын B2B баримт."""
    from ..auth import has_permission
    if not has_permission(user, "pay_transfer"):
        raise HTTPException(403, "Танд «Дансаар» төлбөр авах эрх байхгүй — "
                                 "админ эрхийн матрицаас pay_transfer олгоно")
    session = db.get(ParkingSession, body.get("session_id", ""))
    if not session:
        raise HTTPException(404, "Session олдсонгүй")
    enforce_site(user, session.site_id)  # оператор зөвхөн өөрийн зогсоолын төлбөр
    if session.status not in ("OPEN", "AWAITING_PAYMENT"):
        raise HTTPException(400, f"Session төлөв буруу: {session.status}")
    payment = _create_payment(db, session, "TRANSFER", "TRANSFER", cashier=user)
    if body.get("customer_tin"):
        payment.customer_tin = str(body["customer_tin"]).strip()[:20]
        payment.ebarimt_receiver_type = "COMPANY"
    else:
        payment.ebarimt_receiver_type = "CITIZEN"
    await _finalize_paid(db, payment)
    site = session.site
    db.add(AuditLog(username=user.username, action="TRANSFER_PAYMENT", entity="payment",
                    entity_id=payment.id,
                    detail={"amount": float(payment.amount),
                            # аль данс руу орсны бүртгэл (сайтын тухайн үеийн данс)
                            "bank": getattr(site, "bank_name", None),
                            "account": getattr(site, "bank_account", None)}))
    db.commit()
    return {"ok": True, "payment_id": payment.id, "amount": float(payment.amount)}


# ─────────────────────────── PAX A9000 POS ───────────────────────────

def _find_terminal(db: Session, terminal_id: str):
    """POS терминалыг бүртгэлээс олно (Device: device_type=pax_terminal, device_key=terminal_id).
    Терминал бүр аль зогсоолд харьяалагдахаа device_key-ээрээ өөрөө танина."""
    from ..models import Device
    if not terminal_id:
        return None
    return (db.query(Device)
            .filter(Device.device_key == terminal_id,
                    Device.device_type.in_(["pax_terminal", "pos"]),
                    Device.status == "active").first())


@router.get("/pos/terminal/{terminal_id}")
def pos_terminal_info(terminal_id: str, db: Session = Depends(get_db),
                      user: User = Depends(require("cashier"))):
    """POS апп асахдаа өөрийн terminal_id-ээр дуудаж АЛЬ ЗОГСООЛД харьяалагдахаа
    танина + тухайн зогсоолын төлбөр хүлээж буй машинуудыг авна.
    Терминалыг Тохиргоо→Төхөөрөмжид device_type=pax_terminal, device_key=terminal_id-ээр бүртгэнэ."""
    terminal = _find_terminal(db, terminal_id)
    if not terminal:
        raise HTTPException(404, "Терминал бүртгэлгүй — Тохиргоо→Төхөөрөмжид "
                                 "pax_terminal төрлөөр device_key-тэй бүртгэнэ үү")
    terminal.last_seen = datetime.utcnow()
    awaiting = (db.query(ParkingSession)
                .filter(ParkingSession.site_id == terminal.site_id,
                        ParkingSession.status == "AWAITING_PAYMENT")
                .order_by(ParkingSession.updated_at.desc()).limit(20).all())
    from ..session_logic import session_fee_info as _fee
    db.commit()
    return {
        "terminal_id": terminal_id, "site_id": terminal.site_id,
        "site_code": terminal.site.site_code if terminal.site else None,
        "site_name": terminal.site.name if terminal.site else None,
        "awaiting": [{"session_id": s.id, "plate_number": s.plate_number,
                      "amount_due": amount_due(db, s, _fee(db, s))} for s in awaiting],
    }


@router.post("/pos/confirm")
async def pos_confirm(body: dict, db: Session = Depends(get_db),
                      user: User = Depends(require("cashier"))):
    """PAX A9000 апп картын төлбөр авсныг баталгаажуулна.
    body: {session_id, amount, auth_code, card_last4, card_brand, terminal_id, transaction_id}"""
    session = db.get(ParkingSession, body.get("session_id", ""))
    if not session:
        raise HTTPException(404, "Session олдсонгүй")
    enforce_site(user, session.site_id)  # оператор зөвхөн өөрийн зогсоолын төлбөр
    # POS апп-ийн offline queue 30 сек тутам retry хийдэг — эхний хүсэлт удааширч
    # давхар ирвэл ижил transaction_id-тай ТӨЛӨГДСӨН payment-ийг idempotent буцаана
    # (картаас мөнгө нэг л удаа гарсан тул алдаа биш, амжилт гэж хариулна)
    txn_id = str(body.get("transaction_id") or "")[:120]
    if txn_id:
        dup = (db.query(Payment)
               .filter(Payment.session_id == session.id, Payment.provider == "POS",
                       Payment.provider_payment_id == txn_id, Payment.status == "PAID")
               .first())
        if dup:
            return {"status": "PAID", "payment_id": dup.id, "barrier_opened": True,
                    **_print_payload(db, dup)}
    if session.status not in ("OPEN", "AWAITING_PAYMENT"):
        raise HTTPException(400, f"Session төлөв буруу: {session.status}")
    # Session мөрийг түгжинэ — нэг session дээр хоёр confirm ЗЭРЭГ орж ирвэл
    # (retry + удаан анхны хүсэлт) давхар Payment/баримт үүсэхээс сэргийлнэ
    from sqlalchemy.exc import OperationalError
    try:
        session = (db.query(ParkingSession).filter(ParkingSession.id == session.id)
                   .enable_eagerloads(False)  # OUTER JOIN + FOR UPDATE зөрчлөөс сэргийлнэ
                   .populate_existing().with_for_update(nowait=True).first())
    except OperationalError:
        db.rollback()
        raise HTTPException(409, "Энэ session-ий төлбөр боловсруулагдаж байна — "
                                 "түр хүлээгээд дахин оролдоно уу")
    if session.status not in ("OPEN", "AWAITING_PAYMENT"):
        raise HTTPException(400, f"Session төлөв буруу: {session.status}")
    # Терминал бүртгэлтэй бол зогсоолын харьяаллыг тулгана — А зогсоолын POS
    # Б зогсоолын машинд төлбөр авахаас (буруу тооцоо) сэргийлнэ
    terminal = _find_terminal(db, body.get("terminal_id", ""))
    if terminal and terminal.site_id != session.site_id:
        raise HTTPException(400, f"Энэ терминал «{terminal.site.name if terminal.site else '?'}» "
                                 "зогсоолд харьяалагддаг — өөр зогсоолын машинд төлбөр авах боломжгүй")

    payment = _create_payment(db, session, "POS", "CARD", cashier=user)
    if abs(float(body.get("amount", 0)) - float(payment.amount)) > 1:
        db.rollback()
        raise HTTPException(400, f"Дүн зөрүүтэй: систем {float(payment.amount)}₮")
    payment.card_last4 = body.get("card_last4")
    payment.card_brand = body.get("card_brand")
    payment.terminal_id = body.get("terminal_id")
    payment.provider_payment_id = txn_id or None  # retry-ийн idempotency түлхүүр
    if body.get("customer_tin"):
        payment.customer_tin = str(body["customer_tin"]).strip()[:20]
        payment.ebarimt_receiver_type = "COMPANY"
    else:
        payment.ebarimt_receiver_type = "CITIZEN"
    import time as _t
    _c0 = _t.monotonic()
    await _finalize_paid(db, payment, raw=body)
    db.commit()
    out = {"status": "PAID", "payment_id": payment.id, "barrier_opened": True,
           **_print_payload(db, payment)}
    _ms = int((_t.monotonic() - _c0) * 1000)
    (log.warning if _ms >= settings.payment_slow_warn_ms else log.info)(
        "POS confirm: %dмс (session=%s)", _ms, session.plate_number)
    return out


# ─────────────────────────── Жагсаалт ───────────────────────────
@router.get("")
def list_payments(
    site_id: str | None = None, status: str | None = None, provider: str | None = None,
    date_from: str | None = None, date_to: str | None = None, limit: int = 100, offset: int = 0,
    db: Session = Depends(get_db), user: User = Depends(require("payments", "reports", "cashier")),
):
    from datetime import timedelta
    q = db.query(Payment).join(ParkingSession, Payment.session_id == ParkingSession.id)
    site_id, site_ids = scoped_site(user, site_id)  # tenant хэрэглэгч зөвхөн өөрийн зогсоолууд
    if site_id:
        q = q.filter(ParkingSession.site_id == site_id)
    elif site_ids:
        q = q.filter(ParkingSession.site_id.in_(site_ids))
    if status:
        q = q.filter(Payment.status == status)
    if provider:
        q = q.filter(Payment.provider == provider)
    try:
        if date_from:
            q = q.filter(Payment.created_at >= datetime.fromisoformat(date_from))
        if date_to:
            q = q.filter(Payment.created_at < datetime.fromisoformat(date_to) + timedelta(days=1))
    except ValueError:
        raise HTTPException(400, "Огнооны формат буруу (YYYY-MM-DD)")
    total = q.count()
    rows = q.order_by(Payment.created_at.desc()).offset(offset).limit(min(limit, 500)).all()
    return {"total": total, "rows": [
        to_dict(p, extra={"plate_number": p.session.plate_number if p.session else None,
                          "site_name": p.session.site.name if p.session and p.session.site else None})
        for p in rows]}


async def retry_ebarimt(db: Session, payment: Payment) -> dict:
    """Бүтэлгүйтсэн e-Barimt баримтыг ДАХИН үүсгэнэ (төлбөрийг дахин авахгүй).

    Хэрэглээ: QPay талд «И баримт» тохиргоо идэвхжээгүй байх үед баримт
    EBARIMT_NOT_ENABLED алдаатай унадаг. Тохиргоо асаагдмагц энэ функцээр
    хуучин төлбөрүүдийн баримтыг нөхөж үүсгэнэ — жолоочоос дахин мөнгө авахгүй.

    Буцаах: {ok, ebarimt_id, lottery, error}
    """
    if payment.status != "PAID":
        return {"ok": False, "error": "Төлбөр PAID биш — эхлээд төлбөрөө баталгаажуулна уу"}

    # ЦУЦЛАГДСАН баримтыг тооцохгүй — цуцалсны дараа «Дахин үүсгэх» ШИНЭ баримт
    # үүсгэж тусдаа мөрөөр бүртгэнэ (түүх хадгалагдана)
    rec = (db.query(VatReceipt).filter(VatReceipt.payment_id == payment.id,
                                       VatReceipt.status != "CANCELLED")
           .order_by(VatReceipt.created_at.desc()).first())
    if rec and rec.ebarimt_id:
        return {"ok": True, "ebarimt_id": rec.ebarimt_id, "lottery": rec.lottery_code,
                "error": "Баримт аль хэдийн үүссэн байна"}

    receiver_type = payment.ebarimt_receiver_type or (
        "COMPANY" if payment.customer_tin else "CITIZEN")
    is_qpay = payment.provider == "QPAY" and bool(payment.provider_payment_id)
    mb_acc = None if is_qpay else msgbill.account_enabled_for(
        _site_of(payment), payment.payment_method)
    rcpt_provider = "QPAY" if is_qpay else ("MSGBILL" if mb_acc else "POSAPI")
    try:
        if is_qpay:
            raw = await qpay.create_ebarimt(
                payment.provider_payment_id, receiver_type,
                receiver=payment.customer_tin if receiver_type == "COMPANY" else None,
                acc=qpay.account_for(_site_of(payment)))
        elif mb_acc:
            raw = {}
            # Өмнө msgbill-д илгээгдсэн (rcp_…) бол эхлээд төлөвийг нь асууна —
            # msgbill FAILED-ийг өөрөө retry хийдэг тул CREATED болсон байж магадгүй
            prev_ref = getattr(rec, "provider_ref", None) if rec else None
            if prev_ref:
                try:
                    raw = await msgbill.get_receipt(mb_acc, prev_ref)
                except msgbill.MsgbillError as e:
                    if e.status != 404:
                        raise
                    raw = {}
            if not raw.get("billId"):
                if raw.get("state") and raw["state"] not in ("FAILED", "UNKNOWN"):
                    # PENDING/QUEUED — msgbill боловсруулж байна, дахин POST хийхгүй
                    err = f"msgbill боловсруулж байна (төлөв {raw['state']}) — түр хүлээгээд дахин шалгана уу"
                    if rec:
                        rec.receipt_url = err
                        db.commit()
                    return {"ok": False, "error": err, "pending": True}
                # ШИНЭ Idempotency-Key — өмнөх FAILED хариуг msgbill буцааж өгөхөөс сэргийлнэ
                raw = await msgbill.create_receipt(
                    mb_acc, float(payment.amount),
                    description=_receipt_desc(payment),
                    payment_method=payment.payment_method,
                    idempotency_key=f"pay-{payment.id}-retry-{int(datetime.utcnow().timestamp())}",
                    customer_tin=payment.customer_tin)
                if rec is not None:
                    rec.provider_ref = raw.get("msgbillId") or rec.provider_ref
                    rec.provider = "MSGBILL"
                if not raw.get("billId"):
                    err = raw.get("error") or f"msgbill төлөв {raw.get('state') or '?'} — ДДТД ирээгүй"
                    if rec:
                        rec.receipt_url = err
                    db.commit()
                    return {"ok": False, "error": err}
        else:
            if settings.ebarimt_mock and not settings.ebarimt_mock_receipts:
                err = ("Баримтын суваг байхгүй — PosAPI суугаагүй (MOCK), энэ зогсоолд msgbill "
                       "түлхүүр тохируулаагүй (Тохиргоо → Холболт → e-Barimt API)")
                if rec:
                    rec.receipt_url = err
                    db.commit()
                return {"ok": False, "error": err}
            raw = await ebarimt.create_receipt(
                float(payment.amount), float(payment.vat_amount),
                "CASH" if payment.payment_method in ("CASH", "TRANSFER") else "CARD",
                customer_tin=payment.customer_tin)
    except Exception as e:  # noqa: BLE001
        err = _ebarimt_err(e)
        if rec:
            rec.receipt_url = err
            db.commit()
        return {"ok": False, "error": err}

    if not raw.get("billId"):
        return {"ok": False, "error": "Баримтын дугаар (ДДТД) буцаасангүй"}

    ebarimt.cache_qr(payment.id, raw.get("qrData"))
    if rec:
        rec.ebarimt_id = raw.get("billId")
        rec.lottery_code = None if receiver_type == "COMPANY" else raw.get("lottery")
        rec.status = "SENT"
        rec.receipt_url = None
        rec.provider = rcpt_provider
        rec.provider_ref = raw.get("msgbillId") or rec.provider_ref
    else:
        db.add(VatReceipt(
            payment_id=payment.id, session_id=payment.session_id,
            ebarimt_id=raw.get("billId"),
            lottery_code=None if receiver_type == "COMPANY" else raw.get("lottery"),
            amount=payment.amount, vat_amount=payment.vat_amount,
            customer_tin=payment.customer_tin, status="SENT",
            provider=rcpt_provider, provider_ref=raw.get("msgbillId")))
    db.commit()
    return {"ok": True, "ebarimt_id": raw.get("billId"), "lottery": raw.get("lottery")}


async def cancel_ebarimt(db: Session, payment: Payment, note: str) -> dict:
    """ИЛГЭЭГДСЭН (SENT) e-Barimt баримтыг сувгаар нь цуцална (буцаалт).

    Суваг бүрийн цуцлах зам:
      • QPAY    — `DELETE /v2/ebarimt_v3/{payment_id}` (qpay.cancel_ebarimt), note заавал
      • POSAPI  — локал PosAPI `DELETE /rest/receipt {id: billId, date}` (ebarimt.delete_receipt)
      • MSGBILL — `DELETE /partner/receipts/{id}` (msgbill.cancel_receipt) — msgbill
                  талд одоогоор БАЙХГҮЙ → NOT_SUPPORTED алдаа буцна
    Амжилттай бол VatReceipt.status=CANCELLED, receipt_url=тэмдэглэл. Төлбөр
    (мөнгө) хөндөгдөхгүй — зөвхөн татварын баримт. Дараа нь «Дахин үүсгэх»
    шинэ баримт гаргана (retry_ebarimt CANCELLED-ийг алгасдаг)."""
    recs = (db.query(VatReceipt)
            .filter(VatReceipt.payment_id == payment.id, VatReceipt.status == "SENT",
                    VatReceipt.ebarimt_id.isnot(None)).all())
    if not recs:
        return {"ok": False, "error": "Цуцлах ИЛГЭЭГДСЭН баримт олдсонгүй"}
    site = _site_of(payment)
    cancelled, errors = [], []
    for rec in recs:
        prov = rec.provider or ("QPAY" if payment.provider == "QPAY" and payment.provider_payment_id
                                else "POSAPI")
        try:
            if prov == "QPAY":
                ok = await qpay.cancel_ebarimt(payment.provider_payment_id, note or "Гүйлгээ буцаав",
                                               acc=qpay.account_for(site))
                if not ok:
                    raise RuntimeError("QPay баримт цуцлахыг зөвшөөрсөнгүй")
            elif prov == "MSGBILL":
                acc = msgbill.api_key_for(site)
                if not acc.enabled:
                    raise RuntimeError("msgbill түлхүүр тохируулаагүй")
                if not rec.provider_ref:
                    raise RuntimeError("msgbill баримтын ID (provider_ref) алга")
                await msgbill.cancel_receipt(acc, rec.provider_ref, note)
            else:
                date = (rec.created_at or datetime.utcnow()).strftime("%Y-%m-%d %H:%M:%S")
                if not await ebarimt.delete_receipt(rec.ebarimt_id, date):
                    raise RuntimeError("PosAPI баримт буцаахыг зөвшөөрсөнгүй")
        except Exception as e:  # noqa: BLE001
            errors.append(f"{prov}: {_ebarimt_err(e)}")
            continue
        rec.status = "CANCELLED"
        rec.receipt_url = (note or "Цуцлав")[:200]
        ebarimt.cache_qr(payment.id, None)
        cancelled.append(rec.ebarimt_id)
    db.commit()
    return {"ok": bool(cancelled) and not errors, "cancelled": cancelled,
            "error": "; ".join(errors) or None}


@router.post("/{payment_id}/cancel-ebarimt")
async def cancel_ebarimt_endpoint(payment_id: str, body: dict | None = None,
                                  db: Session = Depends(get_db),
                                  user: User = Depends(require("vat", "reports"))):
    """Ибаримт хуудасны «Цуцлах» — баримтыг сувгаар нь буцааж CANCELLED болгоно.
    body: {note?}. Мөнгө буцаахгүй. Дараа нь «Дахин үүсгэх»-ээр шинэ баримт."""
    payment = db.get(Payment, payment_id)
    if not payment:
        raise HTTPException(404, "Төлбөр олдсонгүй")
    site = _site_of(payment)
    enforce_site(user, site.id if site else None)
    note = str((body or {}).get("note") or "").strip()[:120] or f"Цуцлав ({user.username})"
    payment = _lock_payment(db, payment_id)
    if payment is None:
        raise HTTPException(409, "Баримтын ажиллагаа явагдаж байна — түр хүлээнэ үү")
    res = await cancel_ebarimt(db, payment, note)
    db.add(AuditLog(username=user.username, action="EBARIMT_CANCEL", entity="payment",
                    entity_id=payment_id, detail={**res, "note": note}))
    db.commit()
    if not res.get("ok"):
        raise HTTPException(400, f"Баримт цуцалж чадсангүй: {res.get('error')}")
    return res


@router.post("/{payment_id}/retry-ebarimt")
async def retry_ebarimt_endpoint(payment_id: str, db: Session = Depends(get_db),
                                 user: User = Depends(require("vat", "reports"))):
    """Бүтэлгүйтсэн НӨАТ баримтыг дахин үүсгэх (UI-ийн «Баримт дахин үүсгэх» товч)."""
    payment = db.get(Payment, payment_id)
    if not payment:
        raise HTTPException(404, "Төлбөр олдсонгүй")
    site = _site_of(payment)
    enforce_site(user, site.id if site else None)  # өөр зогсоолын баримтыг дахин үүсгэхгүй
    payment = _lock_payment(db, payment_id)  # давхар товшилтоос давхар баримт гаргахгүй
    if payment is None:
        raise HTTPException(409, "Баримт үүсгэх ажиллагаа явагдаж байна — түр хүлээнэ үү")
    res = await retry_ebarimt(db, payment)
    db.add(AuditLog(username=user.username, action="EBARIMT_RETRY", entity="payment",
                    entity_id=payment_id, detail=res))
    db.commit()
    if not res.get("ok"):
        raise HTTPException(400, f"Баримт үүсгэж чадсангүй: {res.get('error')}")
    return res
