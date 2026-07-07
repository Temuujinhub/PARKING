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

    # Төхөөрөмжийн холболтын статус (сүүлийн 3 минутад холбогдсон = онлайн)
    from ..models import Device
    online_cutoff = datetime.utcnow() - timedelta(minutes=3)
    devices = db.query(Device).filter(Device.status == "active").all()
    device_status = []
    online_n = 0
    for d in devices:
        online = bool(d.last_seen and d.last_seen >= online_cutoff)
        if online:
            online_n += 1
        device_status.append({
            "id": d.id, "name": d.name, "device_type": d.device_type,
            "lane_dir": d.lane_dir, "site_name": d.site.name if d.site else None,
            "online": online, "last_seen": d.last_seen.isoformat() if d.last_seen else None,
        })

    return {"open_sessions": open_count, "awaiting_payment": awaiting,
            "today_entries": today_entries, "today_exits": today_exits,
            "today_revenue": today_revenue, "total_capacity": int(total_capacity or 0),
            "sites": sites, "week_revenue": week,
            "devices_online": online_n, "devices_total": len(devices),
            "device_status": device_status}


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
        # Төлбөрийн төрлөөр задаргаа (easy-park UAT items 1, 4, 6, 7)
        prov = dict(db.query(Payment.provider, func.coalesce(func.sum(Payment.amount), 0))
                    .join(ParkingSession, Payment.session_id == ParkingSession.id)
                    .filter(ParkingSession.site_id == s.id, Payment.status == "PAID",
                            Payment.paid_at >= start, Payment.paid_at < end)
                    .group_by(Payment.provider).all())
        cash, qpay_amt, pos = (float(prov.get(k, 0)) for k in ("CASH", "QPAY", "POS"))
        paid = cash + qpay_amt + pos
        unpaid = float(db.query(func.coalesce(func.sum(ParkingSession.total_fee), 0)).filter(
            ParkingSession.site_id == s.id, ParkingSession.status == "AWAITING_PAYMENT",
            ParkingSession.entry_time >= start, ParkingSession.entry_time < end).scalar())
        out.append({"site_id": s.id, "site_name": s.name, "entered": entered, "exited": exited,
                    "total_minutes": int(minutes or 0),
                    "cash_amount": cash, "qpay_amount": qpay_amt, "pos_amount": pos,
                    "paid_amount": paid, "unpaid_amount": unpaid})
    totals = {
        "entered": sum(r["entered"] for r in out), "exited": sum(r["exited"] for r in out),
        "total_minutes": sum(r["total_minutes"] for r in out),
        "cash_amount": sum(r["cash_amount"] for r in out),
        "qpay_amount": sum(r["qpay_amount"] for r in out),
        "pos_amount": sum(r["pos_amount"] for r in out),
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
    headers = ["Зогсоол", "Орсон", "Гарсан", "Нийт минут",
               "Бэлэн (₮)", "QPay (₮)", "Карт (₮)", "Нийт төлөгдсөн (₮)", "Төлөгдөөгүй (₮)"]
    ws.append(headers)
    for c in ws[1]:
        c.font = Font(bold=True)
    for r in data["rows"]:
        ws.append([r["site_name"], r["entered"], r["exited"], r["total_minutes"],
                   r["cash_amount"], r["qpay_amount"], r["pos_amount"],
                   r["paid_amount"], r["unpaid_amount"]])
    t = data["totals"]
    ws.append(["НИЙТ", t["entered"], t["exited"], t["total_minutes"],
               t["cash_amount"], t["qpay_amount"], t["pos_amount"],
               t["paid_amount"], t["unpaid_amount"]])
    ws[f"A{ws.max_row}"].font = Font(bold=True)
    for col, w in zip("ABCDEFGHI", (30, 10, 10, 12, 14, 14, 14, 18, 16)):
        ws.column_dimensions[col].width = w
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    fname = f"revenue_{datetime.utcnow():%Y%m%d_%H%M}.xlsx"
    return StreamingResponse(
        buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={fname}"})


@router.get("/daily")
def daily_report(date_from: str | None = None, date_to: str | None = None,
                 site_id: str | None = None,
                 db: Session = Depends(get_db), user: User = Depends(require("reports"))):
    """Өдөр өдрөөр задарсан тайлан (easy-park UAT item 3)."""
    start, end = _range(date_from, date_to)
    out = []
    day = start.replace(hour=0, minute=0, second=0, microsecond=0)
    while day < end:
        nxt = day + timedelta(days=1)
        sq = db.query(ParkingSession).filter(ParkingSession.entry_time >= day,
                                             ParkingSession.entry_time < nxt)
        pq = (db.query(Payment.provider, func.coalesce(func.sum(Payment.amount), 0))
              .join(ParkingSession, Payment.session_id == ParkingSession.id)
              .filter(Payment.status == "PAID", Payment.paid_at >= day, Payment.paid_at < nxt))
        if site_id:
            sq = sq.filter(ParkingSession.site_id == site_id)
            pq = pq.filter(ParkingSession.site_id == site_id)
        prov = dict(pq.group_by(Payment.provider).all())
        cash, qpay_amt, pos = (float(prov.get(k, 0)) for k in ("CASH", "QPAY", "POS"))
        out.append({"date": day.strftime("%Y-%m-%d"),
                    "entered": sq.count(),
                    "exited": sq.filter(ParkingSession.exit_time.isnot(None)).count(),
                    "cash_amount": cash, "qpay_amount": qpay_amt, "pos_amount": pos,
                    "paid_amount": cash + qpay_amt + pos})
        day = nxt
    totals = {k: sum(r[k] for r in out) for k in
              ("entered", "exited", "cash_amount", "qpay_amount", "pos_amount", "paid_amount")}
    return {"rows": out, "totals": totals}


def _excel_response(wb, prefix: str):
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    fname = f"{prefix}_{datetime.utcnow():%Y%m%d_%H%M}.xlsx"
    return StreamingResponse(
        buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={fname}"})


@router.get("/site-sessions/excel")
def site_sessions_excel(site_id: str, date_from: str | None = None, date_to: str | None = None,
                        db: Session = Depends(get_db), user: User = Depends(require("reports"))):
    """Нэг зогсоолын session-уудын дэлгэрэнгүй Excel (тайлангийн мөрийн 'Татах' үйлдэл)."""
    from openpyxl import Workbook
    from openpyxl.styles import Font
    start, end = _range(date_from, date_to)
    site = db.get(ParkingSite, site_id)
    if not site:
        from fastapi import HTTPException
        raise HTTPException(404, "Зогсоол олдсонгүй")
    rows = (db.query(ParkingSession)
            .filter(ParkingSession.site_id == site_id,
                    ParkingSession.entry_time >= start, ParkingSession.entry_time < end)
            .order_by(ParkingSession.entry_time.desc()).limit(20000).all())
    STATUS_MN = {"OPEN": "Зогсож байна", "AWAITING_PAYMENT": "Төлбөр хүлээж буй",
                 "PAID": "Төлсөн", "CLOSED": "Гарсан", "FREE": "Үнэгүй гарсан",
                 "MANUAL_CLOSED": "Гараар хаасан"}
    wb = Workbook()
    ws = wb.active
    ws.title = site.name[:30]
    ws.append(["Дугаар", "Орсон", "Гарсан", "Хугацаа (мин)", "Дүн (₮)", "НӨАТ (₮)",
               "Хөнгөлөлт (₮)", "Гэрээт", "Төлөв"])
    for c in ws[1]:
        c.font = Font(bold=True)
    for s in rows:
        ws.append([
            s.plate_number,
            s.entry_time.strftime("%Y-%m-%d %H:%M"),
            s.exit_time.strftime("%Y-%m-%d %H:%M") if s.exit_time else "",
            s.duration_minutes or "",
            float(s.total_fee or 0), float(s.vat_amount or 0), float(s.discount_amount or 0),
            "Тийм" if s.is_registered else "",
            STATUS_MN.get(s.status, s.status),
        ])
    for col, w in zip("ABCDEFGHI", (12, 18, 18, 14, 12, 10, 14, 8, 18)):
        ws.column_dimensions[col].width = w
    return _excel_response(wb, f"sessions_{site.site_code}")


@router.get("/shifts/excel")
def shifts_excel(date_from: str | None = None, date_to: str | None = None,
                 db: Session = Depends(get_db), user: User = Depends(require("reports"))):
    """Касс хаалтын тайлангийн Excel."""
    from openpyxl import Workbook
    from openpyxl.styles import Font

    from ..models import CashierShift, Payment
    from sqlalchemy import func as f
    start, end = _range(date_from, date_to)
    shifts = (db.query(CashierShift).filter(CashierShift.opened_at >= start,
                                            CashierShift.opened_at < end)
              .order_by(CashierShift.opened_at.desc()).limit(2000).all())
    wb = Workbook()
    ws = wb.active
    ws.title = "Касс хаалтын тайлан"
    ws.append(["Кассчин", "Төлөв", "Нээсэн цаг", "Хаасан цаг", "Эхэлсэн дүн (₮)",
               "Гүйлгээний тоо", "Бэлэн (₮)", "QPay (₮)", "Карт (₮)", "Нийт орлого (₮)"])
    for c in ws[1]:
        c.font = Font(bold=True)
    grand = 0.0
    for s in shifts:
        totals = dict(db.query(Payment.provider, f.coalesce(f.sum(Payment.amount), 0))
                      .filter(Payment.shift_id == s.id, Payment.status == "PAID")
                      .group_by(Payment.provider).all())
        count = db.query(Payment).filter(Payment.shift_id == s.id, Payment.status == "PAID").count()
        cash, qpay_amt, pos = (float(totals.get(k, 0)) for k in ("CASH", "QPAY", "POS"))
        total = cash + qpay_amt + pos
        grand += total
        ws.append([s.user.username if s.user else "", "Нээлттэй" if s.status == "OPEN" else "Хаагдсан",
                   s.opened_at.strftime("%Y-%m-%d %H:%M"),
                   s.closed_at.strftime("%Y-%m-%d %H:%M") if s.closed_at else "",
                   float(s.opening_amount or 0), count, cash, qpay_amt, pos, total])
    ws.append(["НИЙТ", "", "", "", "", "", "", "", "", grand])
    ws[f"A{ws.max_row}"].font = Font(bold=True)
    ws[f"J{ws.max_row}"].font = Font(bold=True)
    for col, w in zip("ABCDEFGHIJ", (14, 10, 18, 18, 14, 14, 12, 12, 12, 16)):
        ws.column_dimensions[col].width = w
    return _excel_response(wb, "cashier_shifts")


@router.get("/vat-info")
async def vat_info(user: User = Depends(require("vat", "reports"))):
    """PosAPI getInformation — сугалааны үлдэгдэл, илгээгдээгүй мэдээ (ТЕГ шаардлага №6).
    Frontend үүнийг ашиглан анхааруулга харуулна."""
    from ..services import ebarimt
    info = await ebarimt.get_information()
    warnings = []
    if int(info.get("leftLotteries") or 0) < 500:
        warnings.append(f"Сугалааны дугаар дуусаж байна ({info.get('leftLotteries')} үлдсэн) — "
                        "шинээр авахгүй бол сугалаагүй баримт хэвлэгдэнэ!")
    if int(info.get("unsentCount") or 0) > 0:
        warnings.append(f"Илгээгдээгүй {info.get('unsentCount')} баримт байна — "
                        "3 хоногийн дотор илгээх хуультай.")
    return {**info, "warnings": warnings}


@router.post("/vat-send")
async def vat_send(db: Session = Depends(get_db), user: User = Depends(require("vat", "reports"))):
    """Борлуулалтын мэдээг ТЕГ рүү ГАРААР илгээх (ТЕГ шаардлага №5 — гэмтэл саатлын үед)."""
    from ..models import AuditLog
    from ..services import ebarimt
    result = await ebarimt.send_data()
    db.add(AuditLog(username=user.username, action="VAT_SEND_DATA", entity="ebarimt",
                    detail={"result": result.get("message", str(result.get("success")))}))
    db.commit()
    return result


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
