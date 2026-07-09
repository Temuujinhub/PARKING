"""Кассын ээлж: нээх, хаах, тайлан."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..auth import operator_site, require, require_role
from ..database import get_db
from ..models import AuditLog, CashierShift, ParkingSession, Payment, User
from ..serializers import to_dict

router = APIRouter(prefix="/api/cashier", tags=["cashier"])


def _shift_totals(db: Session, shift: CashierShift) -> dict:
    totals = (
        db.query(Payment.provider, func.coalesce(func.sum(Payment.amount), 0), func.count())
        .filter(Payment.shift_id == shift.id, Payment.status == "PAID")
        .group_by(Payment.provider).all()
    )
    by_provider = {p: {"amount": float(a), "count": c} for p, a, c in totals}
    total = sum(v["amount"] for v in by_provider.values())
    return {"by_provider": by_provider, "total": total,
            "count": sum(v["count"] for v in by_provider.values())}


@router.get("/shift/current")
def current_shift(db: Session = Depends(get_db), user: User = Depends(require("cashier"))):
    shift = db.query(CashierShift).filter(CashierShift.user_id == user.id,
                                          CashierShift.status == "OPEN").first()
    if not shift:
        return {"open": False}
    # Зогсоолд одоо байгаа машины тоо (ээлж хаахад бүгдийг гаргах сонголтод)
    remaining = db.query(ParkingSession).filter(
        ParkingSession.site_id == shift.site_id,
        ParkingSession.status.in_(["OPEN", "AWAITING_PAYMENT"])).count() if shift.site_id else 0
    shift_out = to_dict(shift, extra={"site_name": shift.site.name if shift.site else None})
    return {"open": True, "shift": shift_out, "remaining_cars": remaining, **_shift_totals(db, shift)}


@router.post("/shift/open")
def open_shift(body: dict, db: Session = Depends(get_db),
               user: User = Depends(require_role("OPERATOR", "SUPER_ADMIN"))):
    if db.query(CashierShift).filter(CashierShift.user_id == user.id,
                                     CashierShift.status == "OPEN").first():
        raise HTTPException(400, "Танд нээлттэй ээлж байна. Эхлээд хаана уу.")
    shift = CashierShift(user_id=user.id, site_id=body.get("site_id") or user.site_id,
                         opening_amount=body.get("opening_amount", 0))
    db.add(shift)
    db.add(AuditLog(username=user.username, action="SHIFT_OPEN", entity="shift", entity_id=""))
    db.commit()
    return to_dict(shift)


@router.post("/shift/close")
def close_shift(body: dict | None = None, db: Session = Depends(get_db),
                user: User = Depends(require_role("OPERATOR", "SUPER_ADMIN"))):
    """Ээлж хаах + тооцоо. body: {confirmed_cash?, close_cars?, note?}.
    close_cars=True үед зогсоолд үлдсэн бүх машиныг гаргаж, төлбөртэйд нь нөхөн төлбөр
    үүсгэнэ. confirmed_cash = операторын данс руу шилжүүлэхээр баталгаажуулсан бэлэн."""
    body = body or {}
    shift = db.query(CashierShift).filter(CashierShift.user_id == user.id,
                                          CashierShift.status == "OPEN").first()
    if not shift:
        raise HTTPException(400, "Нээлттэй ээлж байхгүй байна.")
    totals = _shift_totals(db, shift)
    closed_cars = 0
    if body.get("close_cars") and shift.site_id:
        from ..session_logic import session_fee_info
        from .compensations_router import create_compensation
        now = datetime.utcnow()
        for s in db.query(ParkingSession).filter(
                ParkingSession.site_id == shift.site_id,
                ParkingSession.status.in_(["OPEN", "AWAITING_PAYMENT"])).all():
            fee = session_fee_info(db, s, at=now)
            s.exit_time, s.duration_minutes = now, fee["duration_minutes"]
            s.base_fee, s.vat_amount, s.total_fee = fee["base_fee"], fee["vat_amount"], fee["total_fee"]
            s.status = "MANUAL_CLOSED"
            if not fee["is_free"]:
                create_compensation(db, s, "shift_close", user.username)
            closed_cars += 1
    shift.closed_at = datetime.utcnow()
    shift.status = "CLOSED"
    shift.cash_confirmed = body.get("confirmed_cash")
    shift.closed_cars = closed_cars
    shift.note = (body.get("note") or "")[:500] or None
    db.add(AuditLog(username=user.username, action="SHIFT_CLOSE", entity="shift",
                    entity_id=shift.id,
                    detail={**totals, "closed_cars": closed_cars,
                            "confirmed_cash": body.get("confirmed_cash")}))
    db.commit()
    return {"shift": to_dict(shift), **totals, "closed_cars": closed_cars}


@router.get("/hr/worked-days")
def hr_worked_days(month: str, db: Session = Depends(get_db), user: User = Depends(require("users", "reports"))):
    """Хүний нөөц: тухайн сард (YYYY-MM) OPERATOR бүрийн ажилласан өдрүүд.
    Ажилласан өдөр = тухайн өдөр ээлж нээгдсэн (login-д суурилсан). Календарт харуулна."""
    from datetime import datetime as _dt
    y, m = (int(x) for x in month.split("-"))
    start = _dt(y, m, 1)
    end = _dt(y + 1, 1, 1) if m == 12 else _dt(y, m + 1, 1)
    ops = db.query(User).filter(User.role == "OPERATOR", User.is_active.is_(True)).order_by(User.full_name).all()
    out = []
    for op in ops:
        shifts = db.query(CashierShift).filter(
            CashierShift.user_id == op.id, CashierShift.opened_at >= start,
            CashierShift.opened_at < end).all()
        days = sorted({s.opened_at.strftime("%Y-%m-%d") for s in shifts})
        out.append({"user_id": op.id, "name": op.full_name or op.username,
                    "username": op.username, "days_count": len(days), "days": days})
    return {"month": month, "operators": out}


@router.get("/shifts")
def shift_report(date_from: str | None = None, date_to: str | None = None, site_id: str | None = None,
                 db: Session = Depends(get_db), user: User = Depends(require("reports", "cashier"))):
    from datetime import timedelta
    from ..auth import operator_site
    site_id = operator_site(user) or site_id
    q = db.query(CashierShift)
    if site_id:
        q = q.filter(CashierShift.site_id == site_id)
    if date_from:
        q = q.filter(CashierShift.opened_at >= datetime.fromisoformat(date_from))
    if date_to:
        q = q.filter(CashierShift.opened_at < datetime.fromisoformat(date_to) + timedelta(days=1))
    shifts = q.order_by(CashierShift.opened_at.desc()).limit(200).all()
    out = []
    for s in shifts:
        end = s.closed_at or datetime.utcnow()
        dur_min = int((end - s.opened_at).total_seconds() // 60)
        out.append(to_dict(s, extra={
            "cashier": (s.user.full_name or s.user.username) if s.user else None,
            "site_name": s.site.name if s.site else "Бүх зогсоол",
            "duration_minutes": dur_min,
            **_shift_totals(db, s)}))
    return out
