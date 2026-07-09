"""Нөхөн төлбөр — төлбөргүй гарсан машины нэхэмжлэл (Google Sheets: JGA Admin sp / JGA Cash таб).

Урсгал:
  1. Үүсэх: (а) оператор төлбөргүй гаргахдаа "нөхөн төлбөр үүсгэх" сонгох,
            (б) шөнийн хаалт — бүх зогсож буй машиныг гаргаж нэхэмжлэл үүсгэх
  2. Төлөгдөх: касс дээр бэлнээр (дараагийн ирэлтэд)
  3. Хориг: нэг дугаар 3+ ТӨЛӨГДӨӨГҮЙ нэхэмжлэлтэй бол автоматаар хар жагсаалтад орно
  4. Касс/шалгах дэлгэцэд нөхөн төлбөртэй машин улаанаар тэмдэглэгдэнэ
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..auth import operator_site, require
from ..database import get_db
from ..models import AuditLog, BlacklistEntry, Compensation, ParkingSession, User
from ..serializers import to_dict
from ..session_logic import session_fee_info
from ..ws import manager

router = APIRouter(prefix="/api/compensations", tags=["compensations"])


def pending_count(db: Session, plate: str) -> int:
    return db.query(Compensation).filter(Compensation.plate_number == plate,
                                         Compensation.status == "PENDING").count()


def _auto_blacklist(db: Session, plate: str, username: str):
    """3+ төлөгдөөгүй нөхөн төлбөртэй бол хар жагсаалтад автоматаар нэмнэ."""
    if pending_count(db, plate) < 3:
        return
    exists = db.query(BlacklistEntry).filter(BlacklistEntry.plate_number == plate,
                                             BlacklistEntry.is_active.is_(True)).first()
    if not exists:
        db.add(BlacklistEntry(plate_number=plate,
                              reason="Нөхөн төлбөр 3+ удаа төлөгдөөгүй (автомат хориг)",
                              created_by=f"систем ({username})"))


def create_compensation(db: Session, session: ParkingSession, reason: str, username: str) -> Compensation:
    fee = session_fee_info(db, session)
    comp = Compensation(
        session_id=session.id, site_id=session.site_id, plate_number=session.plate_number,
        amount=fee["total_fee"] or session.total_fee or 0, reason=reason, created_by=username,
    )
    db.add(comp)
    db.flush()
    _auto_blacklist(db, session.plate_number, username)
    return comp


@router.get("")
def list_compensations(status: str | None = None, plate: str | None = None,
                       limit: int = 200, db: Session = Depends(get_db),
                       user: User = Depends(require("compensations", "reports"))):
    osid = operator_site(user)  # оператор зөвхөн өөрийн зогсоолын өр
    q = db.query(Compensation)
    if osid:
        q = q.filter(Compensation.site_id == osid)
    if status:
        q = q.filter(Compensation.status == status)
    if plate:
        q = q.filter(Compensation.plate_number.ilike(f"%{plate.upper().strip()}%"))
    rows = q.order_by(Compensation.created_at.desc()).limit(min(limit, 1000)).all()
    now = datetime.utcnow()

    def _age(c):
        return (now - c.created_at).days

    def _bucket(days):
        return "0-7" if days <= 7 else "8-30" if days <= 30 else "31-90" if days <= 90 else "90+"

    out_rows = []
    for c in rows:
        d = _age(c)
        out_rows.append(to_dict(c, extra={"site_name": c.site.name if c.site else None,
                                          "days_old": d, "age_bucket": _bucket(d),
                                          "pending_count": pending_count(db, c.plate_number)}))
    # Нийлбэрүүд: төлөгдөөгүй нийт + настжуулалт (aging) + цугларсан
    pq = db.query(Compensation).filter(Compensation.status == "PENDING")
    paidq = db.query(Compensation).filter(Compensation.status == "PAID")
    if osid:
        pq = pq.filter(Compensation.site_id == osid)
        paidq = paidq.filter(Compensation.site_id == osid)
    pending = pq.all()
    aging = {"0-7": 0.0, "8-30": 0.0, "31-90": 0.0, "90+": 0.0}
    for c in pending:
        aging[_bucket(_age(c))] += float(c.amount)
    return {"rows": out_rows,
            "total_pending": float(sum(c.amount for c in pending)),
            "pending_count": len(pending),
            "total_collected": float(sum(c.amount for c in paidq.all())),
            "aging": aging}


@router.post("/{comp_id}/pay")
async def pay_compensation(comp_id: str, body: dict | None = None, db: Session = Depends(get_db),
                           user: User = Depends(require("compensations"))):
    """Нөхөн төлбөрийг бэлэн/картаар төлүүлж хаах + e-Barimt үүсгэнэ.
    body: {method: CASH|CARD, customer_tin?}."""
    from ..config import settings
    from ..services import ebarimt
    body = body or {}
    method = body.get("method", "CASH")
    comp = db.get(Compensation, comp_id)
    if not comp or comp.status != "PENDING":
        raise HTTPException(404, "Төлөгдөөгүй нэхэмжлэл олдсонгүй")
    osid = operator_site(user)
    if osid and comp.site_id != osid:
        raise HTTPException(403, "Энэ нэхэмжлэл таны хариуцах зогсоолынх биш")
    comp.status = "PAID"
    comp.paid_at = datetime.utcnow()
    comp.paid_by = user.username
    # e-Barimt (амжилтгүй байсан ч төлбөрийг хаана) — локал PosAPI, НӨАТ үнэд багтсан
    amount = float(comp.amount)
    vat = round(amount * settings.vat_rate / (1 + settings.vat_rate))
    tin = str(body.get("customer_tin") or "").strip()[:20] or None
    receipt = {}
    try:
        receipt = await ebarimt.create_receipt(amount, vat, "CASH" if method == "CASH" else "CARD",
                                                customer_tin=tin)
        ebarimt.cache_qr(comp.id, receipt.get("qrData"))
    except Exception as e:  # noqa: BLE001
        print(f"[compensation ebarimt FAILED] {comp_id}: {e}")
    db.add(AuditLog(username=user.username, action="COMPENSATION_PAID", entity="compensation",
                    entity_id=comp_id,
                    detail={"plate": comp.plate_number, "amount": amount, "method": method}))
    db.commit()
    return {**to_dict(comp), "method": method, "ebarimt_id": receipt.get("billId"),
            "lottery_code": receipt.get("lottery"),
            "qr_data": ebarimt.get_cached_qr(comp.id)}


@router.post("/{comp_id}/cancel")
def cancel_compensation(comp_id: str, body: dict, db: Session = Depends(get_db),
                        user: User = Depends(require("discounts", "settings"))):
    """Нэхэмжлэл цуцлах (зөвхөн админ) — шалтгаан заавал."""
    comp = db.get(Compensation, comp_id)
    if not comp or comp.status != "PENDING":
        raise HTTPException(404, "Төлөгдөөгүй нэхэмжлэл олдсонгүй")
    comp.status = "CANCELLED"
    db.add(AuditLog(username=user.username, action="COMPENSATION_CANCELLED", entity="compensation",
                    entity_id=comp_id, detail={"reason": body.get("reason", ""), "plate": comp.plate_number}))
    db.commit()
    return to_dict(comp)


@router.post("/night-close")
async def night_close(body: dict, db: Session = Depends(get_db),
                      user: User = Depends(require("settings"))):
    """Шөнийн хаалт (JGA спек): зогсож буй БҮХ машиныг гаргаж нөхөн төлбөр үүсгэнэ.
    body: {site_id?} — заавал биш, өгөхгүй бол бүх зогсоол. Болгоомжтой — буцаахгүй үйлдэл!"""
    q = db.query(ParkingSession).filter(ParkingSession.status.in_(["OPEN", "AWAITING_PAYMENT"]))
    if body.get("site_id"):
        q = q.filter(ParkingSession.site_id == body["site_id"])
    sessions = q.all()
    now = datetime.utcnow()
    created = 0
    for s in sessions:
        fee = session_fee_info(db, s, at=now)
        s.exit_time = now
        s.duration_minutes = fee["duration_minutes"]
        s.base_fee, s.vat_amount, s.total_fee = fee["base_fee"], fee["vat_amount"], fee["total_fee"]
        s.status = "MANUAL_CLOSED"
        if not fee["is_free"]:
            create_compensation(db, s, "night_close", user.username)
            created += 1
    db.add(AuditLog(username=user.username, action="NIGHT_CLOSE", entity="site",
                    entity_id=body.get("site_id") or "all",
                    detail={"closed_sessions": len(sessions), "compensations": created}))
    db.commit()
    await manager.broadcast(body.get("site_id") or "all", "NIGHT_CLOSE", {
        "closed": len(sessions), "compensations": created, "by": user.username,
    })
    return {"closed_sessions": len(sessions), "compensations_created": created}
