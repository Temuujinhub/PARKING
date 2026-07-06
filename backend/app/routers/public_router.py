"""Public API — нэвтрэлт шаардахгүй, /pay QR хуудсанд зориулагдсан.

Урсгал: QR (https://domain/pay?site=SITE_CODE) уншина →
  1. GET /api/public/site/{site_code}         — зогсоолын нэр
  2. GET /api/public/recent-exits/{site_code} — гарах камерт сүүлд уншигдсан дугаарууд (сонгох)
  3. GET /api/public/sessions?plate=&site=    — session + төлбөр
  4. POST /api/payments/qpay/invoice          — QPay invoice
  5. POST /api/payments/qpay/check/{id}       — төлөгдсөн эсэх polling
"""
import io
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models import ParkingSession, ParkingSite
from ..session_logic import normalize_plate, session_fee_info

router = APIRouter(prefix="/api/public", tags=["public"])


def _mask(plate: str) -> str:
    """Нууцлал: 1234АБВ → 12**АБВ"""
    if len(plate) <= 4:
        return plate
    return plate[:2] + "*" * (len(plate) - 5) + plate[-3:]


@router.get("/qr/{site_code}.png")
def site_qr(site_code: str, size: int = 1200, db: Session = Depends(get_db)):
    """Зогсоолын төлбөрийн QR код (хэвлэхэд бэлэн PNG).
    Зогсоол бүр өөрийн /pay?site={code} линктэй тул QR нь автоматаар өвөрмөц байна."""
    import qrcode
    from qrcode.constants import ERROR_CORRECT_H

    site = db.query(ParkingSite).filter(ParkingSite.site_code == site_code,
                                        ParkingSite.is_active.is_(True)).first()
    if not site:
        raise HTTPException(404, "Зогсоол олдсонгүй")
    url = f"{settings.public_base_url}/pay?site={site.site_code}"
    qr = qrcode.QRCode(error_correction=ERROR_CORRECT_H, box_size=max(4, min(40, size // 33)), border=3)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#231F20", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/png", headers={
        "Content-Disposition": f'inline; filename="{site.site_code}-pay-qr.png"',
        "Cache-Control": "public, max-age=3600",
    })


@router.get("/receipt/{payment_id}")
def payment_receipt(payment_id: str, db: Session = Depends(get_db)):
    """Төлбөрийн дараах НӨАТ (e-Barimt) баримтын мэдээлэл — /pay хуудасны амжилтын дэлгэцэд."""
    from ..models import Payment, VatReceipt
    payment = db.get(Payment, payment_id)
    if not payment or payment.status != "PAID":
        raise HTTPException(404, "Төлөгдсөн баримт олдсонгүй")
    receipt = db.query(VatReceipt).filter(VatReceipt.payment_id == payment_id).first()
    return {
        "plate_number": payment.session.plate_number if payment.session else None,
        "amount": float(payment.amount),
        "vat_amount": float(payment.vat_amount),
        "paid_at": payment.paid_at.isoformat() if payment.paid_at else None,
        "ebarimt_id": receipt.ebarimt_id if receipt else None,
        "lottery_code": receipt.lottery_code if receipt else None,
        "qr_data": receipt.receipt_url if receipt else None,
    }


@router.get("/receipt/{payment_id}/qr.png")
def receipt_qr(payment_id: str, db: Session = Depends(get_db)):
    """e-Barimt баримтын qrData-г QR зураг болгон буцаана (ebarimt апп-аар уншуулна)."""
    import qrcode
    from qrcode.constants import ERROR_CORRECT_M

    from ..models import Payment, VatReceipt
    payment = db.get(Payment, payment_id)
    if not payment or payment.status != "PAID":
        raise HTTPException(404, "Төлөгдсөн баримт олдсонгүй")
    receipt = db.query(VatReceipt).filter(VatReceipt.payment_id == payment_id).first()
    if not receipt or not receipt.receipt_url:
        raise HTTPException(404, "Баримтын QR өгөгдөл олдсонгүй")
    qr = qrcode.QRCode(error_correction=ERROR_CORRECT_M, box_size=8, border=2)
    qr.add_data(receipt.receipt_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#231F20", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/png",
                             headers={"Cache-Control": "public, max-age=86400"})


@router.get("/site/{site_code}")
def get_site(site_code: str, db: Session = Depends(get_db)):
    site = db.query(ParkingSite).filter(ParkingSite.site_code == site_code,
                                        ParkingSite.is_active.is_(True)).first()
    if not site:
        raise HTTPException(404, "Зогсоол олдсонгүй")
    t = site.tariff_template
    return {"name": site.name, "site_code": site.site_code, "zone_code": site.zone_code,
            "free_minutes": t.free_minutes if t else 0,
            "grace_minutes": t.grace_minutes if t else 15}


@router.get("/recent-exits/{site_code}")
def recent_exits(site_code: str, db: Session = Depends(get_db)):
    """Гарах камерт сүүлийн 15 минутад уншигдсан, төлбөр хүлээж буй дугаарууд (масктай)."""
    site = db.query(ParkingSite).filter(ParkingSite.site_code == site_code).first()
    if not site:
        raise HTTPException(404, "Зогсоол олдсонгүй")
    since = datetime.utcnow() - timedelta(minutes=15)
    sessions = (
        db.query(ParkingSession)
        .filter(ParkingSession.site_id == site.id,
                ParkingSession.status == "AWAITING_PAYMENT",
                ParkingSession.updated_at >= since)
        .order_by(ParkingSession.updated_at.desc()).limit(8).all()
    )
    return [{"plate_number": s.plate_number, "masked_plate": _mask(s.plate_number),
             "total_fee": float(s.total_fee or 0)} for s in sessions]


@router.get("/search")
def search_plates(site: str, q: str, db: Session = Depends(get_db)):
    """Хялбар хайлт: дугаарын эхний тоогоор (үсэг оруулахгүйгээр) нээлттэй
    session-уудаас таарах машинуудын жагсаалт буцаана. Жолооч жагсаалтаас сонгоно."""
    q = normalize_plate(q)
    if len(q) < 2:
        return []
    site_obj = db.query(ParkingSite).filter(ParkingSite.site_code == site).first()
    if not site_obj:
        raise HTTPException(404, "Зогсоол олдсонгүй")
    sessions = (
        db.query(ParkingSession)
        .filter(ParkingSession.site_id == site_obj.id,
                ParkingSession.status.in_(["OPEN", "AWAITING_PAYMENT", "PAID"]),
                ParkingSession.plate_number.ilike(f"{q}%"))
        .order_by(ParkingSession.updated_at.desc()).limit(8).all()
    )
    out = []
    for s in sessions:
        fee = session_fee_info(db, s)
        out.append({
            "plate_number": s.plate_number,
            "total_fee": fee["total_fee"],
            "duration_minutes": fee["duration_minutes"],
            "is_free": fee["is_free"],
            "status": s.status,
        })
    return out


@router.get("/sessions")
def find_session(plate: str, site: str, db: Session = Depends(get_db)):
    """Дугаараар нээлттэй session хайна (төлбөрийн задаргаатай)."""
    plate = normalize_plate(plate)
    if not plate:
        raise HTTPException(400, "Дугаараа оруулна уу")
    site_obj = db.query(ParkingSite).filter(ParkingSite.site_code == site).first()
    if not site_obj:
        raise HTTPException(404, "Зогсоол олдсонгүй")
    s = (
        db.query(ParkingSession)
        .filter(ParkingSession.plate_number == plate,
                ParkingSession.site_id == site_obj.id,
                ParkingSession.status.in_(["OPEN", "AWAITING_PAYMENT", "PAID"]))
        .order_by(ParkingSession.entry_time.desc()).first()
    )
    if not s:
        raise HTTPException(404, "Энэ дугаартай нээлттэй бүртгэл олдсонгүй. Дугаараа шалгана уу.")
    fee = session_fee_info(db, s)
    return {
        "session_id": s.id, "plate_number": s.plate_number,
        "entry_time": s.entry_time.isoformat(),
        "duration_minutes": fee["duration_minutes"],
        "base_fee": fee["base_fee"], "vat_amount": fee["vat_amount"],
        "discount_amount": fee["discount_amount"], "total_fee": fee["total_fee"],
        "is_free": fee["is_free"], "free_reason": fee["reason"],
        "status": s.status,
        "paid": s.status == "PAID",
        "exit_deadline": s.exit_deadline.isoformat() if s.exit_deadline else None,
    }
