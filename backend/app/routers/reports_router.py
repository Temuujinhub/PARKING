"""Тайлан: dashboard статистик, зогсоолын орлого, Excel экспорт, НӨАТ баримт, лог."""
import io
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..auth import require
from ..database import get_db
from ..models import (
    AuditLog, LprEvent, ParkingSession, ParkingSite, Payment, User, VatReceipt,
)
from ..serializers import to_dict

router = APIRouter(prefix="/api/reports", tags=["reports"])


def _range(date_from: str | None, date_to: str | None):
    start = datetime.fromisoformat(date_from) if date_from else datetime.utcnow().replace(
        hour=0, minute=0, second=0, microsecond=0)
    end = (datetime.fromisoformat(date_to) + timedelta(days=1)) if date_to else datetime.utcnow() + timedelta(days=1)
    return start, end


@router.get("/dashboard")
def dashboard_stats(db: Session = Depends(get_db), user: User = Depends(require("dashboard"))):
    """Нүүр хуудасны статистик."""
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    open_count = db.query(ParkingSession).filter(
        ParkingSession.status.in_(["OPEN", "AWAITING_PAYMENT", "PAID"])).count()
    awaiting = db.query(ParkingSession).filter(ParkingSession.status == "AWAITING_PAYMENT").count()
    today_entries = db.query(ParkingSession).filter(ParkingSession.entry_time >= today).count()
    today_exits = db.query(ParkingSession).filter(ParkingSession.exit_time >= today).count()
    today_revenue = float(db.query(func.coalesce(func.sum(Payment.amount), 0)).filter(
        Payment.status == "PAID", Payment.paid_at >= today).scalar())
    total_capacity = db.query(func.coalesce(func.sum(ParkingSite.capacity), 0)).filter(
        ParkingSite.is_active.is_(True)).scalar()

    sites = []
    for s in db.query(ParkingSite).filter(ParkingSite.is_active.is_(True)).all():
        occupied = db.query(ParkingSession).filter(
            ParkingSession.site_id == s.id,
            ParkingSession.status.in_(["OPEN", "AWAITING_PAYMENT", "PAID"])).count()
        revenue = float(db.query(func.coalesce(func.sum(Payment.amount), 0))
                        .join(ParkingSession, Payment.session_id == ParkingSession.id)
                        .filter(ParkingSession.site_id == s.id, Payment.status == "PAID",
                                Payment.paid_at >= today).scalar())
        sites.append({"id": s.id, "name": s.name, "capacity": s.capacity,
                      "occupied": occupied, "free": max(0, (s.capacity or 0) - occupied),
                      "today_revenue": revenue})

    # Сүүлийн 7 хоногийн орлого (график)
    week = []
    for i in range(6, -1, -1):
        day = today - timedelta(days=i)
        rev = float(db.query(func.coalesce(func.sum(Payment.amount), 0)).filter(
            Payment.status == "PAID", Payment.paid_at >= day,
            Payment.paid_at < day + timedelta(days=1)).scalar())
        week.append({"date": day.strftime("%m-%d"), "revenue": rev})

    return {"open_sessions": open_count, "awaiting_payment": awaiting,
            "today_entries": today_entries, "today_exits": today_exits,
            "today_revenue": today_revenue, "total_capacity": int(total_capacity or 0),
            "sites": sites, "week_revenue": week}


@router.get("/revenue")
def revenue_report(date_from: str | None = None, date_to: str | None = None,
                   site_id: str | None = None,
                   db: Session = Depends(get_db), user: User = Depends(require("reports"))):
    """Зогсоол тус бүрийн орлогын тайлан (easy-park 'Зогсоолын төлбөрийн тайлан')."""
    start, end = _range(date_from, date_to)
    out = []
    sites = db.query(ParkingSite).all()
    if site_id:
        sites = [s for s in sites if s.id == site_id]
    for s in sites:
        base = db.query(ParkingSession).filter(ParkingSession.site_id == s.id,
                                               ParkingSession.entry_time >= start,
                                               ParkingSession.entry_time < end)
        entered = base.count()
        exited = base.filter(ParkingSession.exit_time.isnot(None)).count()
        minutes = db.query(func.coalesce(func.sum(ParkingSession.duration_minutes), 0)).filter(
            ParkingSession.site_id == s.id, ParkingSession.entry_time >= start,
            ParkingSession.entry_time < end).scalar()
        paid = float(db.query(func.coalesce(func.sum(Payment.amount), 0))
                     .join(ParkingSession, Payment.session_id == ParkingSession.id)
                     .filter(ParkingSession.site_id == s.id, Payment.status == "PAID",
                             Payment.paid_at >= start, Payment.paid_at < end).scalar())
        unpaid = float(db.query(func.coalesce(func.sum(ParkingSession.total_fee), 0)).filter(
            ParkingSession.site_id == s.id, ParkingSession.status == "AWAITING_PAYMENT",
            ParkingSession.entry_time >= start, ParkingSession.entry_time < end).scalar())
        out.append({"site_id": s.id, "site_name": s.name, "entered": entered, "exited": exited,
                    "total_minutes": int(minutes or 0), "paid_amount": paid, "unpaid_amount": unpaid})
    totals = {
        "entered": sum(r["entered"] for r in out), "exited": sum(r["exited"] for r in out),
        "total_minutes": sum(r["total_minutes"] for r in out),
        "paid_amount": sum(r["paid_amount"] for r in out),
        "unpaid_amount": sum(r["unpaid_amount"] for r in out),
    }
    return {"rows": out, "totals": totals,
            "date_from": start.isoformat(), "date_to": end.isoformat()}


@router.get("/revenue/excel")
def revenue_excel(date_from: str | None = None, date_to: str | None = None,
                  db: Session = Depends(get_db), user: User = Depends(require("reports"))):
    from openpyxl import Workbook
    from openpyxl.styles import Font
    data = revenue_report(date_from, date_to, None, db, user)
    wb = Workbook()
    ws = wb.active
    ws.title = "Орлогын тайлан"
    headers = ["Зогсоол", "Орсон", "Гарсан", "Нийт минут", "Төлөгдсөн (₮)", "Төлөгдөөгүй (₮)"]
    ws.append(headers)
    for c in ws[1]:
        c.font = Font(bold=True)
    for r in data["rows"]:
        ws.append([r["site_name"], r["entered"], r["exited"], r["total_minutes"],
                   r["paid_amount"], r["unpaid_amount"]])
    t = data["totals"]
    ws.append(["НИЙТ", t["entered"], t["exited"], t["total_minutes"],
               t["paid_amount"], t["unpaid_amount"]])
    ws[f"A{ws.max_row}"].font = Font(bold=True)
    for col, w in zip("ABCDEF", (30, 12, 12, 14, 18, 18)):
        ws.column_dimensions[col].width = w
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    fname = f"revenue_{datetime.utcnow():%Y%m%d_%H%M}.xlsx"
    return StreamingResponse(
        buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={fname}"})


@router.get("/vat-receipts")
def vat_receipts(date_from: str | None = None, date_to: str | None = None,
                 limit: int = 200, db: Session = Depends(get_db),
                 user: User = Depends(require("vat", "reports"))):
    start, end = _range(date_from, date_to)
    rows = (db.query(VatReceipt).filter(VatReceipt.created_at >= start,
                                        VatReceipt.created_at < end)
            .order_by(VatReceipt.created_at.desc()).limit(min(limit, 1000)).all())
    return [to_dict(r) for r in rows]


@router.get("/audit-logs")
def audit_logs(username: str | None = None, action: str | None = None, limit: int = 200,
               db: Session = Depends(get_db), user: User = Depends(require("logs"))):
    q = db.query(AuditLog)
    if username:
        q = q.filter(AuditLog.username == username)
    if action:
        q = q.filter(AuditLog.action == action)
    return [to_dict(a) for a in q.order_by(AuditLog.created_at.desc()).limit(min(limit, 1000)).all()]


@router.get("/lpr-events")
def lpr_events(site_id: str | None = None, limit: int = 200,
               db: Session = Depends(get_db), user: User = Depends(require("logs", "dashboard"))):
    q = db.query(LprEvent)
    if site_id:
        q = q.filter(LprEvent.site_id == site_id)
    return [to_dict(e) for e in q.order_by(LprEvent.created_at.desc()).limit(min(limit, 1000)).all()]
