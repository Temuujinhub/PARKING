"""Түншийн интеграцийн API (/api/v1) — wallet/апп-уудын B2B холболт.

tokI, Easy Wallet зэрэг гадаад төлбөрийн систем энэ API-аар:
  1. GET  /api/v1/sites                     — зогсоолууд + сул байрны тоо
  2. GET  /api/v1/sessions?plate=           — дугаараар хайж төлөх дүнг авах
  3. POST /api/v1/payments                  — төлбөрийн intent үүсгэх (PENDING)
  4. POST /api/v1/payments/{id}/confirm     — өөрийн талд амжилттай болмогц баталгаажуулах
                                              → систем PAID болгож ХААЛТ НЭЭНЭ
  5. GET  /api/v1/payments/{id}             — төлөв шалгах

Нэвтрэлт: X-API-Key толгой. Түлхүүрүүд .env-ийн PARKING_PARTNER_KEYS-д
("toki:KEY1,easywallet:KEY2") — ШИНЭ WALLET НЭМЭХЭД КОД ӨӨРЧЛӨГДӨХГҮЙ,
түлхүүр нэмээд л болно. Түншийн нэр Payment.provider болж тайланд ялгарна.
"""
from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models import AuditLog, ParkingSession, ParkingSite, Payment
from ..ratelimit import throttle
from ..session_logic import amount_due, normalize_plate, session_fee_info

router = APIRouter(prefix="/api/v1", tags=["integration"])

OPEN_STATUSES = ("OPEN", "AWAITING_PAYMENT", "PAID")


def require_partner(request: Request, x_api_key: str = Header(default="")) -> str:
    """X-API-Key-ээр түншийг таньж нэрийг нь буцаана (Payment.provider болно)."""
    partners = settings.partner_map()
    if not partners:
        raise HTTPException(503, "Интеграцийн API идэвхгүй (PARKING_PARTNER_KEYS тохируулаагүй)")
    name = partners.get(x_api_key or "")
    if not name:
        # Брут-форсоос хамгаалах: буруу түлхүүрийн оролдлогыг IP-ээр хязгаарлана
        ip = request.client.host if request.client else "?"
        if throttle(f"partner-bad:{ip}", limit=20, window=60):
            raise HTTPException(429, "Хэт олон буруу оролдлого")
        raise HTTPException(401, "API түлхүүр буруу")
    return name


def _session_payload(db: Session, s: ParkingSession) -> dict:
    """Түншид өгөх session-ий бүрэн мэдээлэл: хугацаа, тариф, төлөх үлдэгдэл."""
    fee = session_fee_info(db, s)
    due = amount_due(db, s, fee)
    return {
        "session_id": s.id,
        "plate_number": s.plate_number,
        "site_code": s.site.site_code if s.site else None,
        "site_name": s.site.name if s.site else None,
        "entry_time": s.entry_time.isoformat() if s.entry_time else None,
        "duration_minutes": fee["duration_minutes"],
        "base_fee": fee["base_fee"],
        "vat_amount": fee["vat_amount"],
        "discount_amount": fee["discount_amount"],
        "total_fee": fee["total_fee"],
        "amount_due": due,          # ← wallet ЭНЭ дүнг нэхэмжилнэ
        "is_free": fee["is_free"],
        "status": s.status,
        "paid": s.status == "PAID" and due <= 0,
    }


@router.get("/sites")
def list_sites(db: Session = Depends(get_db), partner: str = Depends(require_partner)):
    """Идэвхтэй зогсоолууд + багтаамж, эзэлсэн, сул байрны тоо."""
    occupied_by_site = dict(
        db.query(ParkingSession.site_id, func.count())
        .filter(ParkingSession.status.in_(OPEN_STATUSES))
        .group_by(ParkingSession.site_id).all())
    out = []
    for site in db.query(ParkingSite).filter(ParkingSite.is_active.is_(True)).all():
        occupied = occupied_by_site.get(site.id, 0)
        out.append({
            "site_code": site.site_code, "name": site.name,
            "zone_code": site.zone_code, "address": site.address or "",
            "capacity": site.capacity, "occupied": occupied,
            # capacity=0 → дүүргэлт хянадаггүй зогсоол (сул тоо null)
            "free": max(0, site.capacity - occupied) if site.capacity else None,
        })
    return {"sites": out}


@router.get("/sessions")
def find_sessions(plate: str, site_code: str = "",
                  db: Session = Depends(get_db), partner: str = Depends(require_partner)):
    """Дугаараар нээлттэй session хайна. site_code өгөхгүй бол БҮХ зогсоолоос —
    жолооч аль зогсоолд байгааг wallet апп мэдэхгүй байж болно."""
    plate = normalize_plate(plate)
    if len(plate) < 4:
        raise HTTPException(400, "Дугаар дутуу байна (4+ тэмдэгт)")
    q = (db.query(ParkingSession)
         .filter(ParkingSession.plate_number == plate,
                 ParkingSession.status.in_(OPEN_STATUSES)))
    if site_code:
        site = db.query(ParkingSite).filter(ParkingSite.site_code == site_code).first()
        if not site:
            raise HTTPException(404, "Зогсоол олдсонгүй")
        q = q.filter(ParkingSession.site_id == site.id)
    sessions = q.order_by(ParkingSession.entry_time.desc()).limit(5).all()
    return {"sessions": [_session_payload(db, s) for s in sessions]}


@router.post("/payments")
def create_payment_intent(body: dict, db: Session = Depends(get_db),
                          partner: str = Depends(require_partner)):
    """Төлбөрийн intent үүсгэнэ. body: {session_id}.
    Буцаах amount-ыг wallet өөрийн талд хэрэглэгчээс нэхэмжилж, амжилттай
    болмогц /payments/{id}/confirm-ыг дуудна. Идэвхтэй PENDING intent
    байвал (дүн зөрөөгүй бол) шинээр үүсгэлгүй түүнийгээ буцаана."""
    from .payments_router import _create_payment
    session = db.get(ParkingSession, body.get("session_id", ""))
    if not session:
        raise HTTPException(404, "Session олдсонгүй")
    if session.status not in ("OPEN", "AWAITING_PAYMENT"):
        raise HTTPException(400, f"Session төлөв буруу: {session.status}")

    existing = (db.query(Payment)
                .filter(Payment.session_id == session.id, Payment.provider == partner,
                        Payment.status == "PENDING").first())
    if existing:
        current_due = amount_due(db, session, session_fee_info(db, session))
        if abs(float(existing.amount) - current_due) <= 1:
            return {"payment_id": existing.id, "amount": float(existing.amount),
                    "vat_amount": float(existing.vat_amount), "status": "PENDING"}
        existing.status = "CANCELLED"  # хугацаа өнгөрч дүн өссөн — шинэ intent
        db.flush()

    payment = _create_payment(db, session, partner, "WALLET")
    payment.source = "WALLET"
    db.add(AuditLog(username=f"partner:{partner}", action="WALLET_INTENT", entity="payment",
                    entity_id=payment.id,
                    detail={"plate": session.plate_number, "amount": float(payment.amount)}))
    db.commit()
    return {"payment_id": payment.id, "amount": float(payment.amount),
            "vat_amount": float(payment.vat_amount), "status": "PENDING",
            "plate_number": session.plate_number}


@router.post("/payments/{payment_id}/confirm")
async def confirm_payment(payment_id: str, body: dict, db: Session = Depends(get_db),
                          partner: str = Depends(require_partner)):
    """Wallet өөрийн талд төлбөрийг амжилттай авсныг баталгаажуулна.
    body: {transaction_id, amount}. Дүн зөрвөл татгалзана (буруу дүнгээр хаалт
    нээгдэхгүй). Idempotent — давхар дуудахад алдаа өгөхгүй PAID буцаана."""
    from .payments_router import _finalize_paid
    payment = db.get(Payment, payment_id)
    if not payment:
        raise HTTPException(404, "Payment олдсонгүй")
    if payment.provider != partner:
        raise HTTPException(403, "Энэ төлбөр өөр түншийнх")
    if payment.status == "PAID":
        return {"status": "PAID", "payment_id": payment.id}  # idempotent
    if payment.status != "PENDING":
        raise HTTPException(400, f"Төлбөрийн төлөв буруу: {payment.status}")
    if abs(float(body.get("amount", 0)) - float(payment.amount)) > 1:
        raise HTTPException(400, f"Дүн зөрүүтэй: систем {float(payment.amount)}₮ хүлээж байна")

    payment.provider_payment_id = str(body.get("transaction_id") or "")[:120]
    await _finalize_paid(db, payment, raw={"partner": partner, **{
        k: v for k, v in body.items() if k in ("transaction_id", "amount", "wallet_user")}})
    db.add(AuditLog(username=f"partner:{partner}", action="WALLET_PAID", entity="payment",
                    entity_id=payment.id, detail={"transaction_id": payment.provider_payment_id,
                                                  "amount": float(payment.amount)}))
    db.commit()
    return {"status": "PAID", "payment_id": payment.id, "paid_at":
            payment.paid_at.isoformat() if payment.paid_at else datetime.utcnow().isoformat()}


@router.get("/payments/{payment_id}")
def payment_status(payment_id: str, db: Session = Depends(get_db),
                   partner: str = Depends(require_partner)):
    payment = db.get(Payment, payment_id)
    if not payment or payment.provider != partner:
        raise HTTPException(404, "Payment олдсонгүй")
    return {"payment_id": payment.id, "status": payment.status,
            "amount": float(payment.amount),
            "paid_at": payment.paid_at.isoformat() if payment.paid_at else None}
