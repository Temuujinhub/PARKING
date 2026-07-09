"""Төлбөр: QPay invoice/webhook, e-Barimt 3.0, кассын бэлэн мөнгө, PAX POS баталгаажуулалт."""
import hmac
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse


def secrets_compare(a: str, b: str) -> bool:
    """Цагийн зөрүүнд суурилсан халдлагаас хамгаалсан харьцуулалт."""
    return hmac.compare_digest((a or "").encode(), (b or "").encode())
from sqlalchemy.orm import Session

from ..auth import require
from ..config import settings
from ..database import get_db
from ..models import AuditLog, CashierShift, ParkingSession, Payment, User, VatReceipt
from ..serializers import to_dict
from ..services import ebarimt, qpay
from ..session_logic import amount_due, mark_paid_and_open, session_fee_info

router = APIRouter(prefix="/api/payments", tags=["payments"])


def _invoice_no(session: ParkingSession) -> str:
    site_code = session.site.site_code if session.site else "SITE"
    return f"{site_code}-{datetime.utcnow():%Y%m%d}-{uuid.uuid4().hex[:8].upper()}"


async def _finalize_paid(db: Session, payment: Payment, raw: dict | None = None):
    """Төлбөр PAID болмогц: session PAID + barrier + e-Barimt."""
    if payment.status == "PAID":
        return  # idempotent — давхар webhook хамгаалалт
    payment.status = "PAID"
    payment.paid_at = datetime.utcnow()
    if raw:
        payment.raw_payload = raw

    receiver_type = payment.ebarimt_receiver_type or ("COMPANY" if payment.customer_tin else "CITIZEN")
    # QR-аар (QPay) төлсөн бол QPay-ийн ebarimt_v3-аар; бэлэн/картаар бол локал PosAPI-аар
    use_qpay_eb = (settings.qpay_ebarimt and payment.provider == "QPAY"
                   and bool(payment.provider_payment_id))
    if use_qpay_eb:
        receipt_raw = await qpay.create_ebarimt(
            payment.provider_payment_id, receiver_type,
            # COMPANY үед ААН регистр (ТТД)-ийг ebarimt_receiver болгон дамжуулна
            receiver=payment.customer_tin if receiver_type == "COMPANY" else None,
        )
    else:
        receipt_raw = await ebarimt.create_receipt(
            float(payment.amount), float(payment.vat_amount),
            "CASH" if payment.payment_method == "CASH" else "CARD",
            customer_tin=payment.customer_tin,  # байгууллагаар авах бол B2B баримт
        )
    # ТЕГ шаардлага №11: qrData-г DB-д ХАДГАЛАХГҮЙ — түр санах ойд (баримт үзүүлэх/хэвлэх хугацаанд)
    ebarimt.cache_qr(payment.id, receipt_raw.get("qrData"))
    db.add(VatReceipt(
        payment_id=payment.id, session_id=payment.session_id,
        ebarimt_id=receipt_raw.get("billId"),
        # ААН-ны баримтад сугалаа олгогдохгүй (шаардлага №1, №16)
        lottery_code=None if receiver_type == "COMPANY" else receipt_raw.get("lottery"),
        amount=payment.amount, vat_amount=payment.vat_amount,
        customer_tin=payment.customer_tin,
        status="SENT" if receipt_raw.get("billId") else "FAILED",
    ))
    session = db.get(ParkingSession, payment.session_id)
    await mark_paid_and_open(db, session)


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
    res = await qpay.check_payment(payment.provider_invoice_id)
    if not res.get("paid"):
        return False
    if abs(float(res.get("paid_amount") or 0) - float(payment.amount)) > 1:
        payment.status = "REVIEW"
        payment.raw_payload = res.get("raw") or {}
        db.commit()
        return False
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


def _create_payment(db: Session, session: ParkingSession, provider: str, method: str,
                    cashier: User | None = None) -> Payment:
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

    shift = None
    if cashier:
        shift = db.query(CashierShift).filter(CashierShift.user_id == cashier.id,
                                              CashierShift.status == "OPEN").first()
    payment = Payment(
        session_id=session.id, provider=provider, payment_method=method,
        sender_invoice_no=_invoice_no(session),
        amount=due, vat_amount=vat_due,
        cashier_id=cashier.id if cashier else None,
        shift_id=shift.id if shift else None,
    )
    db.add(payment)
    db.flush()
    return payment


# ─────────────────────────── QPay ───────────────────────────
@router.post("/qpay/invoice")
async def qpay_invoice(body: dict, db: Session = Depends(get_db)):
    """QPay invoice үүсгэх. body: {session_id}. Public pay page + касс хоёулаа ашиглана."""
    session = db.get(ParkingSession, body.get("session_id", ""))
    if not session:
        raise HTTPException(404, "Session олдсонгүй")
    if session.status not in ("OPEN", "AWAITING_PAYMENT"):
        raise HTTPException(400, f"Session төлөв буруу: {session.status}")

    # Өмнө үүсгэсэн PENDING invoice байвал дүн нь одоогийн үлдэгдэлтэй таарч
    # байгаа тохиолдолд л дахин ашиглана (хугацаа өнгөрч тариф өссөн эсвэл
    # grace хэтэрч зөрүү нэхэж буй үед хуучин QR буруу дүнтэй болно)
    existing = db.query(Payment).filter(Payment.session_id == session.id,
                                        Payment.provider == "QPAY",
                                        Payment.status == "PENDING").first()
    if existing and existing.provider_invoice_id:
        current_due = amount_due(db, session, session_fee_info(db, session))
        if abs(float(existing.amount) - current_due) <= 1:
            return {"payment_id": existing.id, "invoice_id": existing.provider_invoice_id,
                    "qr_text": existing.qr_text, "deep_link": existing.deep_link, "urls": [],
                    "amount": float(existing.amount), "mock": settings.qpay_mock}
        existing.status = "CANCELLED"  # дүн зөрсөн — шинэ invoice үүсгэнэ
        db.flush()

    payment = _create_payment(db, session, "QPAY", "QR")
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
    # e-Barimt нэхэмжлэхийн мөрүүд — бүтээгдэхүүн бүрээр (зогсоолын үйлчилгээ = нэг мөр)
    lines = qpay.build_lines([{
        "description": f"Зогсоолын үйлчилгээ — {session.plate_number}",
        "unit_price": float(payment.amount), "quantity": 1,
    }])
    inv = await qpay.create_invoice(
        payment.sender_invoice_no,
        f"Зогсоолын төлбөр — {session.plate_number}",
        f"terminal_{session.site.site_code if session.site else 'X'}", callback,
        lines, receiver_data=receiver_data,
    )
    payment.provider_invoice_id = inv["invoice_id"]
    payment.qr_text = inv["qr_text"]
    payment.deep_link = inv["deep_link"]
    db.commit()
    return {"payment_id": payment.id, "invoice_id": inv["invoice_id"],
            "qr_text": inv["qr_text"], "qr_image": inv.get("qr_image", ""),
            "deep_link": inv["deep_link"], "urls": inv.get("urls", []),
            "amount": float(payment.amount), "mock": inv.get("mock", False)}


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
async def qpay_check(payment_id: str, db: Session = Depends(get_db)):
    """Webhook ирээгүй үед polling шалгалт (pay page/POS 5 сек тутам дуудна).
    PAID болмогц e-Barimt-ийн хэвлэх мэдээллийг (POS терминалд) хамт буцаана."""
    payment = db.get(Payment, payment_id)
    if not payment:
        raise HTTPException(404, "Payment олдсонгүй")
    if payment.status != "PAID" and payment.provider == "QPAY" and payment.provider_invoice_id:
        await _confirm_qpay(db, payment)
        db.commit()
    if payment.status == "PAID":
        return {"status": "PAID", **_print_payload(db, payment)}
    return {"status": payment.status}


# ─────────────────────────── Касс (бэлэн мөнгө) ───────────────────────────
@router.post("/cash")
async def cash_payment(body: dict, db: Session = Depends(get_db),
                       user: User = Depends(require("cashier"))):
    """Кассын бэлэн мөнгөний төлбөр. body: {session_id}"""
    session = db.get(ParkingSession, body.get("session_id", ""))
    if not session:
        raise HTTPException(404, "Session олдсонгүй")
    if session.status not in ("OPEN", "AWAITING_PAYMENT"):
        raise HTTPException(400, f"Session төлөв буруу: {session.status}")
    payment = _create_payment(db, session, "CASH", "CASH", cashier=user)
    await _finalize_paid(db, payment)
    db.add(AuditLog(username=user.username, action="CASH_PAYMENT", entity="payment",
                    entity_id=payment.id, detail={"amount": float(payment.amount)}))
    db.commit()
    return {"ok": True, "payment_id": payment.id, "amount": float(payment.amount)}


# ─────────────────────────── PAX A9000 POS ───────────────────────────
@router.post("/pos/confirm")
async def pos_confirm(body: dict, db: Session = Depends(get_db),
                      user: User = Depends(require("cashier"))):
    """PAX A9000 апп картын төлбөр авсныг баталгаажуулна.
    body: {session_id, amount, auth_code, card_last4, card_brand, terminal_id, transaction_id}"""
    session = db.get(ParkingSession, body.get("session_id", ""))
    if not session:
        raise HTTPException(404, "Session олдсонгүй")
    if session.status not in ("OPEN", "AWAITING_PAYMENT"):
        raise HTTPException(400, f"Session төлөв буруу: {session.status}")

    payment = _create_payment(db, session, "POS", "CARD", cashier=user)
    if abs(float(body.get("amount", 0)) - float(payment.amount)) > 1:
        db.rollback()
        raise HTTPException(400, f"Дүн зөрүүтэй: систем {float(payment.amount)}₮")
    payment.card_last4 = body.get("card_last4")
    payment.card_brand = body.get("card_brand")
    payment.terminal_id = body.get("terminal_id")
    if body.get("customer_tin"):
        payment.customer_tin = str(body["customer_tin"]).strip()[:20]
        payment.ebarimt_receiver_type = "COMPANY"
    else:
        payment.ebarimt_receiver_type = "CITIZEN"
    await _finalize_paid(db, payment, raw=body)
    db.commit()
    return {"status": "PAID", "payment_id": payment.id, "barrier_opened": True,
            **_print_payload(db, payment)}


# ─────────────────────────── Жагсаалт ───────────────────────────
@router.get("")
def list_payments(
    site_id: str | None = None, status: str | None = None, provider: str | None = None,
    date_from: str | None = None, date_to: str | None = None, limit: int = 100, offset: int = 0,
    db: Session = Depends(get_db), user: User = Depends(require("payments", "reports", "cashier")),
):
    from datetime import timedelta
    q = db.query(Payment).join(ParkingSession, Payment.session_id == ParkingSession.id)
    if site_id:
        q = q.filter(ParkingSession.site_id == site_id)
    if status:
        q = q.filter(Payment.status == status)
    if provider:
        q = q.filter(Payment.provider == provider)
    if date_from:
        q = q.filter(Payment.created_at >= datetime.fromisoformat(date_from))
    if date_to:
        q = q.filter(Payment.created_at < datetime.fromisoformat(date_to) + timedelta(days=1))
    total = q.count()
    rows = q.order_by(Payment.created_at.desc()).offset(offset).limit(min(limit, 500)).all()
    return {"total": total, "rows": [
        to_dict(p, extra={"plate_number": p.session.plate_number if p.session else None,
                          "site_name": p.session.site.name if p.session and p.session.site else None})
        for p in rows]}
