"""Кассын ээлж: нээх, хаах, тайлан."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..auth import require, require_role
from ..database import get_db
from ..models import AuditLog, CashierShift, Payment, User
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
    return {"open": True, "shift": to_dict(shift), **_shift_totals(db, shift)}


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
def close_shift(db: Session = Depends(get_db),
                user: User = Depends(require_role("OPERATOR", "SUPER_ADMIN"))):
    shift = db.query(CashierShift).filter(CashierShift.user_id == user.id,
                                          CashierShift.status == "OPEN").first()
    if not shift:
        raise HTTPException(400, "Нээлттэй ээлж байхгүй байна.")
    shift.closed_at = datetime.utcnow()
    shift.status = "CLOSED"
    totals = _shift_totals(db, shift)
    db.add(AuditLog(username=user.username, action="SHIFT_CLOSE", entity="shift",
                    entity_id=shift.id, detail=totals))
    db.commit()
    return {"shift": to_dict(shift), **totals}


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
