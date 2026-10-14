"""Read-only history projections. Never recalculate fees or settle a debt."""
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, or_

from ..auth import operator_sites
from ..models import Compensation, ParkingSession, Payment, User


def payment_allocation(amount, snapshot):
    """Only present a reconciled invoice snapshot; legacy/malformed data stays unknown."""
    unknown = {"known": False, "parking_amount": None, "previous_debt_amount": None}
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("debts"), list):
        return unknown
    try:
        parking = Decimal(str(snapshot["parking_amount"]))
        total = Decimal(str(amount))
        debts = snapshot["debts"]
        ids = [str(d["id"]) for d in debts]
        parts = [Decimal(str(d["amount"])) for d in debts]
        values = [parking, total, *parts]
        if (len(ids) != len(set(ids)) or any(not v.is_finite() or v < 0 for v in values)
                or any(v != v.quantize(Decimal("0.01")) for v in values)
                or parking + sum(parts, Decimal(0)) != total):
            return unknown
        return {"known": True, "parking_amount": float(parking),
                "previous_debt_amount": float(sum(parts, Decimal(0)))}
    except (KeyError, TypeError, ValueError, InvalidOperation):
        return unknown


def attach_history_financials(db, rows, user):
    """Batch payments and this stay's debts without exposing another site's payments.

    A debt may be PAID even when its old stay is still MANUAL_CLOSED. A payment_id
    on a PENDING debt is only an invoice link, never evidence of settlement.
    """
    ids = [r["id"] for r in rows]
    if not ids:
        return rows
    allowed = operator_sites(user)
    q = db.query(Compensation.id, Compensation.session_id, Compensation.site_id,
                 Compensation.amount, Compensation.status, Compensation.reason,
                 Compensation.created_at, Compensation.paid_at, Compensation.payment_id)
    q = q.filter(Compensation.session_id.in_(ids))
    if allowed is not None:
        q = q.filter(Compensation.site_id.in_(allowed))
    debts = q.order_by(Compensation.created_at, Compensation.id).all()
    linked = {c.payment_id for c in debts if c.status == "PAID" and c.payment_id}

    q = (db.query(Payment.id, Payment.session_id, Payment.provider, Payment.payment_method,
                  Payment.source, Payment.amount, Payment.paid_at, Payment.fee_snapshot,
                  User.username.label("cashier"))
         .outerjoin(ParkingSession, ParkingSession.id == Payment.session_id)
         .outerjoin(User, User.id == Payment.cashier_id)
         .filter(Payment.status == "PAID",
                 or_(Payment.session_id.in_(ids), Payment.id.in_(linked))))
    if allowed is not None:
        # Parking payments belong to their stay; standalone collections use site_id.
        q = q.filter(func.coalesce(ParkingSession.site_id, Payment.site_id).in_(allowed))
    pays, by_stay = {}, {}
    for p in q.order_by(Payment.paid_at, Payment.id).all():
        item = {"id": p.id, "session_id": p.session_id, "provider": p.provider,
                "method": p.payment_method, "source": p.source, "amount": float(p.amount),
                "paid_at": p.paid_at.isoformat() if p.paid_at else None,
                "cashier": p.cashier, "allocation": payment_allocation(p.amount, p.fee_snapshot)}
        pays[p.id] = item
        by_stay.setdefault(p.session_id, []).append(item)
    by_debt = {}
    for c in debts:
        # No bank payload, invoice URLs, card data or other-site identifiers in this projection.
        by_debt.setdefault(c.session_id, []).append({
            "id": c.id, "amount": float(c.amount), "status": c.status, "reason": c.reason,
            "created_at": c.created_at.isoformat() if c.created_at else None,
            "paid_at": c.paid_at.isoformat() if c.paid_at else None,
            "payment": pays.get(c.payment_id) if c.status == "PAID" else None,
        })
    for row in rows:
        row["payments"] = by_stay.get(row["id"], [])
        row["session_debts"] = by_debt.get(row["id"], [])
    return rows
