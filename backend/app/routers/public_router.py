"""Public API — нэвтрэлт шаардахгүй, /pay QR хуудсанд зориулагдсан.

Урсгал: QR (https://domain/pay?site=SITE_CODE) уншина →
  1. GET /api/public/site/{site_code}         — зогсоолын нэр
  2. GET /api/public/recent-exits/{site_code} — гарах камерт сүүлд уншигдсан дугаарууд (сонгох)
  3. GET /api/public/sessions?plate=&site=    — session + төлбөр
  4. POST /api/payments/qpay/invoice          — QPay invoice
  5. POST /api/payments/qpay/check/{id}       — төлөгдсөн эсэх polling
"""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import ParkingSession, ParkingSite
from ..session_logic import normalize_plate, session_fee_info

router = APIRouter(prefix="/api/public", tags=["public"])


def _mask(plate: str) -> str:
    """Нууцлал: 1234АБВ → 12**АБВ"""
    if len(plate) <= 4:
        return plate
    return plate[:2] + "*" * (len(plate) - 5) + plate[-3:]


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
