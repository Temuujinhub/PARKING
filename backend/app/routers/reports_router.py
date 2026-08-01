"""Тайлан: dashboard статистик, зогсоолын орлого, Excel экспорт, НӨАТ баримт, лог.
Excel workbook угсрах код: reports_excel.py (энд endpoint-ууд нь нимгэн wrapper)."""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..auth import operator_sites, require
from ..database import get_db
from ..models import (
    AuditLog, LprEvent, ParkingSession, ParkingSite, Payment, User, VatReceipt,
)
from ..serializers import to_dict
from . import reports_excel as _excel

router = APIRouter(prefix="/api/reports", tags=["reports"])

# DB бүх цагийг UTC-ээр хадгалдаг; хэрэглэгч локал (УБ, UTC+8) өдрөөр сэтгэдэг тул
# өдрийн зааг, цагийн бүлэглэлтийг TZ-ээр хөрвүүлнэ.
from ..config import settings as _cfg  # noqa: E402
TZ = timedelta(hours=_cfg.tz_offset_hours)


def _local_midnight_utc(dt_utc: datetime) -> datetime:
    """Тухайн UTC моментын ЛОКАЛ өдрийн 00:00-ийг UTC-ээр буцаана."""
    return (dt_utc + TZ).replace(hour=0, minute=0, second=0, microsecond=0) - TZ


def _range(date_from: str | None, date_to: str | None):
    """UI-ийн огноог ЛОКАЛ өдөр гэж ойлгож UTC зааг руу хөрвүүлнэ."""
    start = (datetime.fromisoformat(date_from) - TZ) if date_from else _local_midnight_utc(datetime.utcnow())
    end = (datetime.fromisoformat(date_to) + timedelta(days=1) - TZ) if date_to         else datetime.utcnow() + timedelta(days=1)
    return start, end


def _day_list(start, end):
    """[start-ийн локал өдрийн 00:00 UTC; end) хүртэлх өдрийн заагуудын жагсаалт.
    Хуучин давталтын day/nxt цонхнуудтай ЯГ ижил (эхний цонх start-аас өмнө эхэлж,
    сүүлийн цонх end-ээс хэтэрч болно)."""
    days = []
    day = _local_midnight_utc(start)
    while day < end:
        days.append(day)
        day += timedelta(days=1)
    return days


def _scope(user, site_id=None):
    """Tenant салгалт: хүссэн site_id-г хэрэглэгчийн хариуцах зогсоолуудаар хязгаарлана.
    Буцаана: None (бүх зогсоол) | str (нэг зогсоол) | list[str] (хэд хэдэн зогсоол).
    Эрхгүй зогсоол хүсвэл 403 — тайлан/лог бүр энэ шүүлтээр дамжина."""
    allowed = operator_sites(user)
    if allowed is None:
        return site_id or None
    if site_id:
        if site_id not in allowed:
            raise HTTPException(403, "Энэ зогсоолын мэдээлэл харах эрхгүй.")
        return site_id
    return allowed[0] if len(allowed) == 1 else allowed


def _flt(q, col, scope):
    """_scope-ийн утгаар query шүүнэ (None=шүүхгүй, str=нэг, list=олон зогсоол)."""
    if scope is None:
        return q
    if isinstance(scope, list):
        return q.filter(col.in_(scope))
    return q.filter(col == scope)


def _daily_rows(db, start, end, site_id):
    """Өдөр өдрөөр орц/гарц + төлбөрийн хэрэгслээр (бэлэн/QPay/карт) орлого.
    daily_report ба daily_excel хоёр ижил логик ашигладаг тул нэг эх сурвалж болгов.
    Өдөр бүр 2-3 query биш — бүх хугацааг ЛОКАЛ өдрөөр бүлэглэсэн 2 query."""
    keys = ("entered", "exited", "cash_amount", "qpay_amount", "pos_amount", "paid_amount")
    days = _day_list(start, end)
    if not days:
        return [], {k: 0 for k in keys}
    lo, hi = days[0], days[-1] + timedelta(days=1)
    sess_day = func.date(ParkingSession.entry_time + TZ)
    sq = (db.query(sess_day, func.count(), func.count(ParkingSession.exit_time))
          .filter(ParkingSession.entry_time >= lo, ParkingSession.entry_time < hi))
    pay_day = func.date(Payment.paid_at + TZ)
    pq = (db.query(pay_day, Payment.provider, func.coalesce(func.sum(Payment.amount), 0))
          .join(ParkingSession, Payment.session_id == ParkingSession.id)
          .filter(Payment.status == "PAID", Payment.paid_at >= lo, Payment.paid_at < hi))
    sq = _flt(sq, ParkingSession.site_id, site_id)
    pq = _flt(pq, ParkingSession.site_id, site_id)
    counts = {str(d): (int(n), int(x)) for d, n, x in sq.group_by(sess_day).all()}
    pays = {}
    for d, provider, amt in pq.group_by(pay_day, Payment.provider).all():
        pays.setdefault(str(d), {})[provider] = amt
    out = []
    for day in days:
        ds = (day + TZ).strftime("%Y-%m-%d")
        entered, exited = counts.get(ds, (0, 0))
        prov = pays.get(ds, {})
        cash, qpay_amt, pos = (float(prov.get(k, 0)) for k in ("CASH", "QPAY", "POS"))
        out.append({"date": ds, "entered": entered, "exited": exited,
                    "cash_amount": cash, "qpay_amount": qpay_amt, "pos_amount": pos,
                    "paid_amount": cash + qpay_amt + pos})
    totals = {k: sum(r[k] for r in out) for k in keys}
    return out, totals


@router.get("/dashboard")
def dashboard_stats(rev_days: int = 7,
                    db: Session = Depends(get_db), user: User = Depends(require("dashboard"))):
    """Нүүр хуудасны статистик. Хариуцах зогсоолтой хэрэглэгчид зөвхөн өөрийн
    зогсоолуудын тоо баримт харагдана (tenant салгалт).
    rev_days — орлогын графикийн хоногийн тоо (7/14/30, UI-ийн сонголт)."""
    rev_days = max(1, min(31, rev_days))
    scope = _scope(user)
    today = _local_midnight_utc(datetime.utcnow())
    open_count = _flt(db.query(ParkingSession).filter(
        ParkingSession.status.in_(["OPEN", "AWAITING_PAYMENT", "PAID"])),
        ParkingSession.site_id, scope).count()
    awaiting = _flt(db.query(ParkingSession).filter(ParkingSession.status == "AWAITING_PAYMENT"),
                    ParkingSession.site_id, scope).count()
    today_entries = _flt(db.query(ParkingSession).filter(ParkingSession.entry_time >= today),
                         ParkingSession.site_id, scope).count()
    today_exits = _flt(db.query(ParkingSession).filter(ParkingSession.exit_time >= today),
                       ParkingSession.site_id, scope).count()
    rev_q = db.query(func.coalesce(func.sum(Payment.amount), 0)).filter(
        Payment.status == "PAID", Payment.paid_at >= today)
    if scope is not None:
        rev_q = _flt(rev_q.join(ParkingSession, Payment.session_id == ParkingSession.id),
                     ParkingSession.site_id, scope)
    today_revenue = float(rev_q.scalar())
    total_capacity = _flt(db.query(func.coalesce(func.sum(ParkingSite.capacity), 0)).filter(
        ParkingSite.is_active.is_(True)), ParkingSite.id, scope).scalar()

    # Зогсоол тус бүрийн дүүргэлт/орлого — сайт бүрд 2 query биш, 2 бүлэглэсэн query
    occ_by_site = dict(db.query(ParkingSession.site_id, func.count())
                       .filter(ParkingSession.status.in_(["OPEN", "AWAITING_PAYMENT", "PAID"]))
                       .group_by(ParkingSession.site_id).all())
    rev_by_site = {sid: float(a) for sid, a in
                   db.query(ParkingSession.site_id, func.coalesce(func.sum(Payment.amount), 0))
                   .join(Payment, Payment.session_id == ParkingSession.id)
                   .filter(Payment.status == "PAID", Payment.paid_at >= today)
                   .group_by(ParkingSession.site_id).all()}
    sites = []
    for s in _flt(db.query(ParkingSite).filter(ParkingSite.is_active.is_(True)),
                  ParkingSite.id, scope).all():
        occupied = int(occ_by_site.get(s.id, 0))
        sites.append({"id": s.id, "name": s.name, "capacity": s.capacity,
                      # capacity=0 → дүүргэлтгүй: сул тоо null
                      "occupied": occupied,
                      "free": max(0, s.capacity - occupied) if s.capacity else None,
                      "today_revenue": rev_by_site.get(s.id, 0.0)})

    # Сүүлийн rev_days хоногийн орлого (график) — өдөр бүр query биш, 1 бүлэглэсэн query
    wk_day = func.date(Payment.paid_at + TZ)
    wk_q = (db.query(wk_day, func.coalesce(func.sum(Payment.amount), 0))
            .filter(Payment.status == "PAID", Payment.paid_at >= today - timedelta(days=rev_days - 1),
                    Payment.paid_at < today + timedelta(days=1)))
    if scope is not None:
        wk_q = _flt(wk_q.join(ParkingSession, Payment.session_id == ParkingSession.id),
                    ParkingSession.site_id, scope)
    wk = {str(d): float(a) for d, a in wk_q.group_by(wk_day).all()}
    week = []
    for i in range(rev_days - 1, -1, -1):
        day = today - timedelta(days=i)
        week.append({"date": (day + TZ).strftime("%m-%d"),
                     "revenue": wk.get((day + TZ).strftime("%Y-%m-%d"), 0.0)})

    # Өнөөдрийн цагийн ачаалал — цаг тус бүрийн орц/гарц (0–23)
    from sqlalchemy import Integer, cast
    hourly = {h: {"hour": h, "entries": 0, "exits": 0} for h in range(24)}
    for hr, cnt in (_flt(db.query(cast(func.extract("hour", ParkingSession.entry_time + TZ), Integer),
                                  func.count()).filter(ParkingSession.entry_time >= today),
                         ParkingSession.site_id, scope)
                    .group_by(func.extract("hour", ParkingSession.entry_time + TZ)).all()):
        if hr is not None:
            hourly[int(hr)]["entries"] = int(cnt)
    for hr, cnt in (_flt(db.query(cast(func.extract("hour", ParkingSession.exit_time + TZ), Integer),
                                  func.count()).filter(ParkingSession.exit_time >= today),
                         ParkingSession.site_id, scope)
                    .group_by(func.extract("hour", ParkingSession.exit_time + TZ)).all()):
        if hr is not None:
            hourly[int(hr)]["exits"] = int(cnt)
    hourly_load = [hourly[h] for h in range(24)]

    # Төхөөрөмжийн холболтын статус (сүүлийн 3 минутад холбогдсон = онлайн)
    from ..models import Device
    online_cutoff = datetime.utcnow() - timedelta(minutes=3)
    devices = _flt(db.query(Device).filter(Device.status == "active"),
                   Device.site_id, scope).all()
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

    # Ажиллаж буй ээлж — хэн аль зогсоолд POS/системд нэвтэрч ажиллаж байгаа
    from ..models import CashierShift
    active_shifts = []
    for sh in (_flt(db.query(CashierShift).filter(CashierShift.status == "OPEN"),
                    CashierShift.site_id, scope)
               .order_by(CashierShift.opened_at.desc()).all()):
        rev = float(db.query(func.coalesce(func.sum(Payment.amount), 0)).filter(
            Payment.status == "PAID", Payment.cashier_id == sh.user_id,
            Payment.paid_at >= sh.opened_at).scalar() or 0)
        active_shifts.append({
            "cashier": (sh.user.full_name or sh.user.username) if sh.user else "?",
            "site_name": sh.site.name if sh.site else "Бүх зогсоол",
            "opened_at": sh.opened_at.isoformat(), "revenue": rev})

    # Төхөөрөмжийн төрлөөр (карт дээр том тоогоор харуулна)
    cameras_total = sum(1 for d in device_status if d["device_type"] == "camera")
    barriers_total = sum(1 for d in device_status if d["device_type"] == "barrier")
    return {"open_sessions": open_count, "awaiting_payment": awaiting,
            "today_entries": today_entries, "today_exits": today_exits,
            "today_revenue": today_revenue, "total_capacity": int(total_capacity or 0),
            "sites": sites, "week_revenue": week, "hourly_load": hourly_load,
            "sites_total": len(sites), "active_shifts": active_shifts,
            "cameras_total": cameras_total, "barriers_total": barriers_total,
            "devices_online": online_n, "devices_total": len(devices),
            "device_status": device_status}


@router.get("/revenue")
def revenue_report(date_from: str | None = None, date_to: str | None = None,
                   site_id: str | None = None,
                   db: Session = Depends(get_db), user: User = Depends(require("reports"))):
    """Зогсоол тус бүрийн орлогын тайлан (easy-park 'Зогсоолын төлбөрийн тайлан')."""
    start, end = _range(date_from, date_to)
    out = []
    sites = _flt(db.query(ParkingSite), ParkingSite.id, _scope(user, site_id)).all()
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
    data = revenue_report(date_from, date_to, None, db, user)
    return _excel.revenue_excel(data)


@router.get("/daily")
def daily_report(date_from: str | None = None, date_to: str | None = None,
                 site_id: str | None = None,
                 db: Session = Depends(get_db), user: User = Depends(require("reports"))):
    """Өдөр өдрөөр задарсан тайлан (easy-park UAT item 3)."""
    start, end = _range(date_from, date_to)
    out, totals = _daily_rows(db, start, end, _scope(user, site_id))
    return {"rows": out, "totals": totals}


@router.get("/monthly")
def monthly_report(date_from: str | None = None, date_to: str | None = None,
                   site_id: str | None = None,
                   db: Session = Depends(get_db), user: User = Depends(require("reports"))):
    """Сар сараар — төлбөрийн хэрэгслээр (бэлэн/QPay/карт) задарсан тайлан."""
    from sqlalchemy import Integer, cast
    start, end = _range(date_from, date_to)
    ymexpr = (cast(func.extract("year", Payment.paid_at + TZ), Integer) * 100
              + cast(func.extract("month", Payment.paid_at + TZ), Integer))
    q = (db.query(ymexpr.label("ym"), Payment.provider,
                  func.coalesce(func.sum(Payment.amount), 0), func.count())
         .join(ParkingSession, Payment.session_id == ParkingSession.id)
         .filter(Payment.status == "PAID", Payment.paid_at >= start, Payment.paid_at < end))
    q = _flt(q, ParkingSession.site_id, _scope(user, site_id))
    months = {}
    for ym, prov, amt, cnt in q.group_by("ym", Payment.provider).all():
        m = months.setdefault(int(ym), {"cash": 0.0, "qpay": 0.0, "pos": 0.0, "count": 0})
        key = {"CASH": "cash", "QPAY": "qpay", "POS": "pos"}.get(prov)
        if key:
            m[key] += float(amt)
        m["count"] += int(cnt)
    out = []
    for ym in sorted(months, reverse=True):
        m = months[ym]
        out.append({"month": f"{ym // 100}-{ym % 100:02d}", **m,
                    "total": m["cash"] + m["qpay"] + m["pos"]})
    totals = {k: sum(r[k] for r in out) for k in ("cash", "qpay", "pos", "total", "count")}
    return {"rows": out, "totals": totals}


PROVIDER_MN = {"CASH": "Бэлэн", "QPAY": "QPay", "POS": "Банкны карт"}
STATUS_MN2 = {"PAID": "Төлсөн", "FREE": "Үнэгүй", "AWAITING_PAYMENT": "Төлбөр хүлээж буй",
              "OPEN": "Нээлттэй", "CLOSED": "Хаагдсан"}


def _car_type(s) -> str:
    if s.is_registered:
        return "Гэрээт"
    if s.discount_id:
        return "Хөнгөлөлттэй"
    return "Энгийн"


def _txn_query(db, start, end, site_id, provider, car_type, status, date_field="entry"):
    """Бичилтийн шүүлттэй session query. date_field='entry'=орсон цагаар,
    'paid'=төлбөрийн огноогоор (тухайн өдрийн ГҮЙЛГЭЭ — орлоготой таарна)."""
    if date_field == "paid":
        # Тухайн хугацаанд ТӨЛӨГДСӨН гүйлгээтэй session-ууд
        paid_sub = (db.query(Payment.session_id).filter(
            Payment.status == "PAID", Payment.paid_at >= start, Payment.paid_at < end).subquery())
        q = db.query(ParkingSession).filter(ParkingSession.id.in_(db.query(paid_sub.c.session_id)))
    else:
        q = db.query(ParkingSession).filter(ParkingSession.entry_time >= start,
                                            ParkingSession.entry_time < end)
    q = _flt(q, ParkingSession.site_id, site_id)
    if status:
        q = q.filter(ParkingSession.status == status)
    if car_type == "contract":
        q = q.filter(ParkingSession.is_registered.is_(True))
    elif car_type == "discount":
        q = q.filter(ParkingSession.discount_id.isnot(None))
    elif car_type == "normal":
        q = q.filter(ParkingSession.is_registered.is_(False), ParkingSession.discount_id.is_(None))
    if provider:
        sub = (db.query(Payment.session_id).filter(Payment.status == "PAID",
                                                   Payment.provider == provider).subquery())
        q = q.filter(ParkingSession.id.in_(db.query(sub.c.session_id)))
    return q  # order-гүй — caller шаардлагатай бол .order_by нэмнэ


def _txn_rows(db, sessions):
    """Session жагсаалтыг бүрэн бичилт болгон дэлгэнэ (payment/receipt/cashier багцаар)."""
    from ..models import CashierShift, User, VatReceipt
    ids = [s.id for s in sessions]
    pays_by_sess = {}
    if ids:
        for p in db.query(Payment).filter(Payment.session_id.in_(ids)).all():
            pays_by_sess.setdefault(p.session_id, []).append(p)
    rec_by_sess = {r.session_id: r for r in
                   db.query(VatReceipt).filter(VatReceipt.session_id.in_(ids)).all()} if ids else {}
    cashier_ids = {p.cashier_id for ps in pays_by_sess.values() for p in ps if p.cashier_id}
    cashiers = {u.id: u.full_name or u.username for u in
                db.query(User).filter(User.id.in_(cashier_ids)).all()} if cashier_ids else {}
    out = []
    for s in sessions:
        pays = pays_by_sess.get(s.id, [])
        paid = [p for p in pays if p.status == "PAID"]
        primary = (paid[0] if paid else (pays[0] if pays else None))
        paid_amount = sum(float(p.amount) for p in paid)
        rec = rec_by_sess.get(s.id)
        out.append({
            "session_id": s.id,
            "plate_number": s.plate_number,
            "site_name": s.site.name if s.site else None,
            "entry_time": s.entry_time.isoformat() if s.entry_time else None,
            "exit_time": s.exit_time.isoformat() if s.exit_time else None,
            "duration_minutes": s.duration_minutes,
            "car_type": _car_type(s),
            "discount_name": s.discount.name if s.discount else None,
            "base_fee": float(s.base_fee or 0),
            "discount_amount": float(s.discount_amount or 0),
            "vat_amount": float(s.vat_amount or 0),
            "total_fee": float(s.total_fee or 0),
            "paid_amount": paid_amount,
            "provider": PROVIDER_MN.get(primary.provider, primary.provider) if primary else None,
            "payment_method": primary.payment_method if primary else None,
            "status": STATUS_MN2.get(s.status, s.status),
            "cashier": cashiers.get(primary.cashier_id) if primary and primary.cashier_id else None,
            # QPay портал/банкны хуулгатай тулгах гүйлгээний утга (машины дугаартай)
            "invoice_no": primary.sender_invoice_no if primary else None,
            "ebarimt_id": rec.ebarimt_id if rec else None,
            "lottery_code": rec.lottery_code if rec else None,
            "customer_tin": rec.customer_tin if rec else (primary.customer_tin if primary else None),
            "paid_at": primary.paid_at.isoformat() if primary and primary.paid_at else None,
        })
    return out


@router.get("/transactions")
def transactions(date_from: str | None = None, date_to: str | None = None,
                 site_id: str | None = None, provider: str | None = None,
                 car_type: str | None = None, status: str | None = None,
                 date_field: str = "entry", limit: int = 500, offset: int = 0,
                 db: Session = Depends(get_db), user: User = Depends(require("reports"))):
    """Дэлгэрэнгүй бичилтийн тайлан — машин бүрийн бүрэн мөчлөг, олон талбараар шүүнэ.
    Шүүлт: огноо (date_field=entry орсон / paid төлсөн), зогсоол, төлбөрийн хэрэгсэл,
    машины төрөл (contract/discount/normal), төлөв. Багцалж татахад /transactions/excel."""
    start, end = _range(date_from, date_to)
    q = _txn_query(db, start, end, _scope(user, site_id), provider, car_type, status, date_field)
    total = q.count()
    paid_sum = float(q.with_entities(func.coalesce(func.sum(ParkingSession.total_fee), 0)).scalar() or 0)
    sessions = q.order_by(ParkingSession.entry_time.desc()).offset(offset).limit(min(limit, 2000)).all()
    rows = _txn_rows(db, sessions)
    return {"total": total, "rows": rows,
            "totals": {"count": total, "total_fee": paid_sum}}


@router.get("/transactions/excel")
def transactions_excel(date_from: str | None = None, date_to: str | None = None,
                       site_id: str | None = None, provider: str | None = None,
                       car_type: str | None = None, status: str | None = None,
                       date_field: str = "entry",
                       db: Session = Depends(get_db), user: User = Depends(require("reports"))):
    """Шүүсэн бичилтүүдийг Excel болгон багцалж татна (одоогийн шүүлтээр)."""
    start, end = _range(date_from, date_to)
    sessions = (_txn_query(db, start, end, _scope(user, site_id), provider, car_type, status, date_field)
                .order_by(ParkingSession.entry_time.desc()).limit(20000).all())
    rows = _txn_rows(db, sessions)
    return _excel.transactions_excel(rows)


@router.get("/by-payment")
def by_payment(date_from: str | None = None, date_to: str | None = None, site_id: str | None = None,
               db: Session = Depends(get_db), user: User = Depends(require("reports"))):
    """Төлбөрийн төрлөөр — бүгд ТӨЛӨГДСӨН гүйлгээгээр (paid_at), тул хэрэгслээр ба
    машины төрлөөр 2 задаргаа ИЖИЛ нийлбэрт нийлнэ (тэнцвэржинэ).
    Үнэгүй гарсан нь орлогогүй тул тусад нь тоогоор (info) харуулна."""
    start, end = _range(date_from, date_to)
    sid = _scope(user, site_id)
    # Хэрэгслээр — төлсөн гүйлгээ
    pq = (db.query(Payment.provider, func.coalesce(func.sum(Payment.amount), 0), func.count())
          .join(ParkingSession, Payment.session_id == ParkingSession.id)
          .filter(Payment.status == "PAID", Payment.paid_at >= start, Payment.paid_at < end))
    pq = _flt(pq, ParkingSession.site_id, sid)
    by_method = [{"key": PROVIDER_MN.get(p, p), "amount": float(a), "count": int(c)}
                 for p, a, c in pq.group_by(Payment.provider).all()]
    # Машины төрлөөр — ИЖИЛ төлсөн гүйлгээг session-ий төрлөөр бүлэглэнэ (тэнцвэржинэ)
    payq = (db.query(Payment).join(ParkingSession, Payment.session_id == ParkingSession.id)
            .filter(Payment.status == "PAID", Payment.paid_at >= start, Payment.paid_at < end))
    payq = _flt(payq, ParkingSession.site_id, sid)
    buckets = {"Гэрээт": [0, 0.0], "Хөнгөлөлттэй": [0, 0.0], "Энгийн": [0, 0.0]}
    for p in payq.all():
        buckets[_car_type(p.session)][0] += 1
        buckets[_car_type(p.session)][1] += float(p.amount)
    by_car = [{"key": k, "count": v[0], "amount": v[1]} for k, v in buckets.items()]
    # Үнэгүй гарсан машин (орлогогүй — тусад нь тоо) — гарсан огноогоор
    free_q = db.query(ParkingSession).filter(ParkingSession.status == "FREE",
                                             ParkingSession.exit_time >= start,
                                             ParkingSession.exit_time < end)
    free_q = _flt(free_q, ParkingSession.site_id, sid)
    total = sum(m["amount"] for m in by_method)
    return {"by_method": by_method, "by_car": by_car,
            "total": total, "free_count": free_q.count()}


@router.get("/by-company")
def by_company(date_from: str | None = None, date_to: str | None = None, site_id: str | None = None,
               db: Session = Depends(get_db), user: User = Depends(require("reports"))):
    """Байгууллагаар (гэрээт машин) — тухайн хугацаанд байгууллага бүрийн хэдэн машин
    хэдэн удаа орж, нийт хэдэн цаг зогссоныг нэгтгэнэ. Session-ийг бүртгэлтэй
    машины жагсаалттай ДУГААРААР нь тулгана (тухайн зогсоолд эсвэл бүх зогсоолд
    эрхтэй бүртгэл тоологдоно); байгууллагагүй бүртгэлийг «(байгууллагагүй)» гэж бүлэглэнэ."""
    from ..models import RegisteredDriver
    start, end = _range(date_from, date_to)
    sid = _scope(user, site_id)
    now = datetime.utcnow()

    # plate → [(site_id|None, tenant_id, company)] — нэг дугаар олон бүртгэлтэй байж болно.
    # «Бүх зогсоол» (site NULL) бүртгэл нь зөвхөн ӨӨРИЙН түрээслэгчийн зогсоолд тоологдоно.
    site_tenant = {st.id: st.tenant_id for st in db.query(ParkingSite).all()}
    drv: dict[str, list] = {}
    reg_count: dict[str, set] = {}   # company → бүртгэлтэй машины олонлог (одоогийн жагсаалтаар)
    for d in db.query(RegisteredDriver).filter(RegisteredDriver.is_active.is_(True)).all():
        comp = (d.company or "").strip() or "(байгууллагагүй)"
        drv.setdefault(d.plate_number, []).append((d.site_id, d.tenant_id, comp))
        if sid is None or d.site_id == sid or (
                d.site_id is None and d.tenant_id and d.tenant_id == site_tenant.get(sid)):
            reg_count.setdefault(comp, set()).add(d.plate_number)

    sq = db.query(ParkingSession).filter(ParkingSession.entry_time >= start,
                                         ParkingSession.entry_time < end)
    sq = _flt(sq, ParkingSession.site_id, sid)

    agg: dict[str, dict] = {}
    for s in sq.all():
        matches = drv.get(s.plate_number)
        if not matches:
            continue
        # Тухайн зогсоолд яг таарсан бүртгэл тэргүүлнэ; «бүх зогсоол» (None) бүртгэл
        # зөвхөн session-ий зогсоолын ТҮРЭЭСЛЭГЧТЭЙ таарвал (дамнахгүй)
        _stn = site_tenant.get(s.site_id)
        comp = next((c for st, tn, c in matches if st == s.site_id),
                    next((c for st, tn, c in matches
                          if st is None and (tn == _stn if tn or _stn else True)), None))
        if comp is None:
            continue   # өөр зогсоолд л эрхтэй машин — энд гэрээтэд тооцохгүй
        mins = s.duration_minutes
        if mins is None:
            mins = ((s.exit_time or now) - s.entry_time).total_seconds() / 60
        a = agg.setdefault(comp, {"company": comp, "plates": set(), "sessions": 0, "minutes": 0.0})
        a["plates"].add(s.plate_number)
        a["sessions"] += 1
        a["minutes"] += float(mins)

    rows = [{"company": c,
             "registered_cars": len(reg_count.get(c, set())),
             "visited_cars": len(a["plates"]),
             "sessions": a["sessions"],
             "total_minutes": round(a["minutes"]),
             "avg_minutes": round(a["minutes"] / a["sessions"]) if a["sessions"] else 0}
            for c, a in agg.items()]
    # Тухайн хугацаанд огт ирээгүй ч бүртгэлтэй байгууллагуудыг 0-тэйгээр үзүүлнэ
    for c, plates in reg_count.items():
        if c not in agg:
            rows.append({"company": c, "registered_cars": len(plates), "visited_cars": 0,
                         "sessions": 0, "total_minutes": 0, "avg_minutes": 0})
    rows.sort(key=lambda r: -r["total_minutes"])
    return {"rows": rows,
            "total_sessions": sum(r["sessions"] for r in rows),
            "total_minutes": sum(r["total_minutes"] for r in rows)}


_STATUS_MN = {"OPEN": "Зогсож буй", "AWAITING_PAYMENT": "Төлбөр хүлээж буй",
              "PAID": "Төлсөн (дотор)", "CLOSED": "Гарсан", "FREE": "Үнэгүй гарсан",
              "MANUAL_CLOSED": "Гараар хаасан"}

_CONTRACT_MN = {"MONTHLY": "Сарын", "CONTRACT": "Гэрээт", "VIP": "VIP", "STAFF": "Ажилтан"}


def _company_sessions(db, user, company: str, start, end, sid):
    """Нэг байгууллагын гэрээт машинуудын session-үүд (тооцоо нийлэх дэлгэрэнгүй).
    by_company-тэй ИЖИЛ тулгалтын дүрэм: дугаар таарч, бүртгэл нь тухайн зогсоолд
    эсвэл ӨӨРИЙН ТҮРЭЭСЛЭГЧИЙН бүх-зогсоолд хамаарсан байх (дамнахгүй).
    Мөр бүрд санхүүд хэрэгтэй бүрэн мэдээлэл: эзэмшигч, гэрээний төрөл, сарын
    хураамж, тухайн орц дээр төлсөн дүн, төлөв."""
    from ..models import RegisteredDriver
    q = db.query(RegisteredDriver).filter(RegisteredDriver.is_active.is_(True))
    if company == "(байгууллагагүй)":
        q = q.filter((RegisteredDriver.company.is_(None)) | (RegisteredDriver.company == ""))
    else:
        q = q.filter(RegisteredDriver.company == company)
    site_tenant = {st.id: st.tenant_id for st in db.query(ParkingSite).all()}
    drv: dict[str, list] = {}
    dinfo: dict[str, RegisteredDriver] = {}
    for d in q.all():
        drv.setdefault(d.plate_number, []).append((d.site_id, d.tenant_id))
        dinfo.setdefault(d.plate_number, d)
    if not drv:
        return []
    now = datetime.utcnow()
    sq = (db.query(ParkingSession)
          .filter(ParkingSession.entry_time >= start, ParkingSession.entry_time < end,
                  ParkingSession.plate_number.in_(list(drv)))
          .order_by(ParkingSession.plate_number, ParkingSession.entry_time))
    sq = _flt(sq, ParkingSession.site_id, sid)
    sessions = sq.all()
    # Тухайн session-ууд дээр төлөгдсөн дүн (гэрээт ихэвчлэн 0, гэхдээ нотолгоонд чухал)
    paid_by_session = dict(
        db.query(Payment.session_id, func.coalesce(func.sum(Payment.amount), 0))
        .filter(Payment.status == "PAID",
                Payment.session_id.in_([x.id for x in sessions] or ["-"]))
        .group_by(Payment.session_id).all())
    site_names = {sid_: st for sid_, st in db.query(ParkingSite.id, ParkingSite.name).all()}
    rows = []
    for s in sessions:
        matches = drv[s.plate_number]
        _stn = site_tenant.get(s.site_id)
        ok = any(st == s.site_id for st, tn in matches) or any(
            st is None and (tn == _stn if tn or _stn else True) for st, tn in matches)
        if not ok:
            continue
        mins = s.duration_minutes
        if mins is None:
            mins = ((s.exit_time or now) - s.entry_time).total_seconds() / 60
        d = dinfo.get(s.plate_number)
        rows.append({"plate": s.plate_number,
                     "owner": (d.full_name if d else "") or "",
                     "contract": _CONTRACT_MN.get(d.contract_type if d else "", (d.contract_type if d else "")),
                     "monthly_fee": float(d.monthly_fee or 0) if d else 0.0,
                     "site": site_names.get(s.site_id, "?"),
                     "entry": (s.entry_time + TZ).strftime("%Y-%m-%d %H:%M"),
                     "exit": (s.exit_time + TZ).strftime("%Y-%m-%d %H:%M") if s.exit_time else "",
                     "minutes": round(float(mins)),
                     "paid": float(paid_by_session.get(s.id, 0)),
                     "status": _STATUS_MN.get(s.status, s.status)})
    return rows


@router.get("/by-company/sessions")
def by_company_sessions(company: str, date_from: str | None = None, date_to: str | None = None,
                        site_id: str | None = None,
                        db: Session = Depends(get_db), user: User = Depends(require("reports"))):
    """Нэг байгууллагын дэлгэрэнгүй — тухайн байгууллагад илгээж тооцоо нийлэх жагсаалт."""
    start, end = _range(date_from, date_to)
    sid = _scope(user, site_id)
    rows = _company_sessions(db, user, company, start, end, sid)
    return {"company": company, "rows": rows,
            "total_sessions": len(rows), "total_minutes": sum(r["minutes"] for r in rows),
            "cars": len({r["plate"] for r in rows})}


@router.get("/by-company/sessions/excel")
def by_company_sessions_excel(company: str, date_from: str | None = None, date_to: str | None = None,
                              site_id: str | None = None,
                              db: Session = Depends(get_db), user: User = Depends(require("reports"))):
    """Байгууллагад илгээх тооцооны Excel — мөр бүр нэг орц/гарц."""
    start, end = _range(date_from, date_to)
    sid = _scope(user, site_id)
    rows = _company_sessions(db, user, company, start, end, sid)

    def _h(m):
        return f"{m // 60}ц {m % 60:02d}м"

    data = [[i + 1, r["plate"], r["site"], r["entry"], r["exit"] or "—", _h(r["minutes"])]
            for i, r in enumerate(rows)]
    total_min = sum(r["minutes"] for r in rows)
    import re as _re
    # Content-Disposition header latin-1 шаарддаг тул файлын нэрэнд зөвхөн ASCII
    safe = _re.sub(r"[^A-Za-z0-9_-]+", "_", company).strip("_")[:24] or "company"
    return _excel._xlsx(
        f"tootsoo_{safe}",
        f"{company} — гэрээт машины тооцоо {(start + TZ):%Y-%m-%d} — {(end + TZ - timedelta(days=1)):%Y-%m-%d}",
        ["№", "Улсын дугаар", "Зогсоол", "Орсон", "Гарсан", "Зогссон хугацаа"],
        data, widths=[6, 14, 22, 18, 18, 16],
        total_row=["НИЙТ", f"{len({r['plate'] for r in rows})} машин", "", f"{len(rows)} удаа", "", _h(total_min)])


@router.get("/by-company/excel")
def by_company_excel(date_from: str | None = None, date_to: str | None = None,
                     site_id: str | None = None,
                     db: Session = Depends(get_db), user: User = Depends(require("reports"))):
    """Байгууллагаар тайлангийн Excel."""
    data = by_company(date_from, date_to, site_id, db, user)
    start, end = _range(date_from, date_to)

    def _h(m):
        return f"{m // 60}ц {m % 60:02.0f}м" if m else "0м"

    rows = [[r["company"], r["registered_cars"], r["visited_cars"], r["sessions"],
             _h(r["total_minutes"]), _h(r["avg_minutes"])] for r in data["rows"]]
    return _excel._xlsx(
        "companies",
        f"Байгууллагаар (гэрээт) {(start + TZ):%Y-%m-%d} — {(end + TZ - timedelta(days=1)):%Y-%m-%d}",
        ["Байгууллага", "Бүртгэлтэй машин", "Ирсэн машин", "Орсон удаа", "Нийт зогссон", "Дундаж"],
        rows, widths=[32, 16, 14, 12, 16, 12],
        total_row=["НИЙТ", "", "", data["total_sessions"], _h(data["total_minutes"]), ""])


def _shift_rows(db, start, end, site_id):
    """Ээлжийн өдөрөөр (өдрийг shift_change_hour-аар тасалж) төлбөрийг задлана.
    Ээлжийн өдөр D = [D + Hц, D+1 + Hц). Өдөрөөртэй ижил бүтэц, зөвхөн зааг цаг өөр."""
    from ..config import settings
    h = settings.shift_change_hour
    out = []
    # Ээлж солигдох цаг H нь ЛОКАЛ цаг — локал өдрийн эхлэл дээр H нэмж UTC зааг гаргана
    day = _local_midnight_utc(start) + timedelta(hours=h)
    if start < day:
        day -= timedelta(days=1)
    while day < end:
        nxt = day + timedelta(days=1)
        sq = db.query(ParkingSession).filter(ParkingSession.entry_time >= day,
                                             ParkingSession.entry_time < nxt)
        pq = (db.query(Payment.provider, func.coalesce(func.sum(Payment.amount), 0))
              .join(ParkingSession, Payment.session_id == ParkingSession.id)
              .filter(Payment.status == "PAID", Payment.paid_at >= day, Payment.paid_at < nxt))
        sq = _flt(sq, ParkingSession.site_id, site_id)
        pq = _flt(pq, ParkingSession.site_id, site_id)
        prov = dict(pq.group_by(Payment.provider).all())
        cash, qpay_amt, pos = (float(prov.get(k, 0)) for k in ("CASH", "QPAY", "POS"))
        out.append({"date": (day + TZ).strftime("%Y-%m-%d"),
                    "window": f"{h:02d}:00–{h:02d}:00",
                    "entered": sq.count(),
                    "exited": sq.filter(ParkingSession.exit_time.isnot(None)).count(),
                    "cash_amount": cash, "qpay_amount": qpay_amt, "pos_amount": pos,
                    "paid_amount": cash + qpay_amt + pos})
        day = nxt
    return out


@router.get("/by-shift")
def by_shift_report(date_from: str | None = None, date_to: str | None = None,
                    site_id: str | None = None,
                    db: Session = Depends(get_db), user: User = Depends(require("reports"))):
    """Ээлжээр — өдрийг ээлж солигдох цагаар тасалж бүлэглэсэн орлого (Өдрөөртэй адил
    боловч зааг нь шөнө дунд биш, ээлж солигдох цаг)."""
    from ..config import settings
    start, end = _range(date_from, date_to)
    out = _shift_rows(db, start, end, _scope(user, site_id))
    totals = {k: sum(r[k] for r in out) for k in
              ("entered", "exited", "cash_amount", "qpay_amount", "pos_amount", "paid_amount")}
    return {"rows": out, "shift_hour": settings.shift_change_hour, "totals": totals}


@router.get("/settlement")
def settlement(site_id: str, date_from: str | None = None, date_to: str | None = None,
               db: Session = Depends(get_db), user: User = Depends(require("reports"))):
    """Санхүүгийн өдрийн тооцоо — pos-Карт / pos-QPay / QR-QPay / Бэлэн задаргаатай.
    Карт ба QPay нь электрон баталгаажсан тул систем=баталгаа (засахгүй); зөвхөн бэлэнг
    санхүү дансны хуулгаас баталгаажуулна. Мөн ажилтан + тухайн өдөр үүссэн өрийн дүн."""
    from ..models import CashierShift, Compensation, DailySettlement
    _scope(user, site_id)  # хариуцаагүй зогсоолын тооцоо руу хандахыг хориглоно
    start, end = _range(date_from, date_to)
    setts = {s.date: s for s in db.query(DailySettlement).filter(
        DailySettlement.site_id == site_id).all()}
    days = _day_list(start, end)
    # Өдөр бүр 3-4 query биш — бүх хугацааг ЛОКАЛ өдрөөр бүлэглэсэн 3 query
    pay_map, debt_map, workers_map = {}, {}, {}
    if days:
        lo, hi = days[0], days[-1] + timedelta(days=1)
        pay_day = func.date(Payment.paid_at + TZ)
        for d, provider, src, amt in (
                db.query(pay_day, Payment.provider, Payment.source,
                         func.coalesce(func.sum(Payment.amount), 0))
                .join(ParkingSession, Payment.session_id == ParkingSession.id)
                .filter(Payment.status == "PAID", Payment.paid_at >= lo, Payment.paid_at < hi,
                        ParkingSession.site_id == site_id)
                .group_by(pay_day, Payment.provider, Payment.source).all()):
            pay_map.setdefault(str(d), []).append((provider, src, amt))
        comp_day = func.date(Compensation.created_at + TZ)
        debt_map = {str(d): float(a) for d, a in
                    db.query(comp_day, func.coalesce(func.sum(Compensation.amount), 0))
                    .filter(Compensation.site_id == site_id, Compensation.created_at >= lo,
                            Compensation.created_at < hi).group_by(comp_day).all()}
        for sh in db.query(CashierShift).filter(CashierShift.site_id == site_id,
                                                CashierShift.opened_at >= lo,
                                                CashierShift.opened_at < hi).all():
            if sh.user:
                workers_map.setdefault((sh.opened_at + TZ).strftime("%Y-%m-%d"), set()).add(
                    sh.user.full_name or sh.user.username)
    out = []
    for day in days:
        ds = (day + TZ).strftime("%Y-%m-%d")
        # Төлбөрийг provider+source-оор
        card = pos_qpay = qr_qpay = cash = 0.0
        for prov, src, amt in pay_map.get(ds, ()):
            amt = float(amt)
            if prov == "POS":
                card += amt
            elif prov == "QPAY":
                if src == "POS":
                    pos_qpay += amt
                else:
                    qr_qpay += amt
            elif prov == "CASH":
                cash += amt
        st_total = card + pos_qpay + qr_qpay + cash
        # Тухайн өдөр үүссэн өр (нөхөн төлбөр)
        debt = debt_map.get(ds, 0.0)
        # Ажилласан ажилтнууд (тухайн өдөр ээлж нээсэн)
        workers = sorted(workers_map.get(ds, set()))
        st = setts.get(ds)
        if st_total <= 0 and debt <= 0 and not st:
            continue
        confirmed_cash = float(st.confirmed_cash) if st else 0.0
        # Карт/QPay электрон баталгаажсан = систем; зөвхөн бэлэн санхүү баталгаажуулна
        confirmed_total = card + pos_qpay + qr_qpay + confirmed_cash
        out.append({"date": ds, "card": card, "pos_qpay": pos_qpay, "qr_qpay": qr_qpay,
                    "cash": cash, "system_total": st_total, "confirmed_cash": confirmed_cash,
                    "confirmed_total": confirmed_total, "difference": st_total - confirmed_total,
                    "debt": debt, "workers": workers,
                    "status": st.status if st else "OPEN", "note": (st.note if st else "") or "",
                    "closed_by": st.closed_by if st else None,
                    "closed_at": st.closed_at.isoformat() if st and st.closed_at else None})
    out.reverse()
    return {"rows": out}


@router.put("/settlement")
def settlement_upsert(body: dict, db: Session = Depends(get_db), user: User = Depends(require("reports"))):
    """Санхүү тухайн өдрийн баталгаажсан дүнг оруулж/тооцоо хаана.
    body: {site_id, date, confirmed_card?, confirmed_qpay?, confirmed_cash?, note?, status?}."""
    from ..models import DailySettlement
    if not body.get("site_id") or not body.get("date"):
        raise HTTPException(400, "site_id болон date шаардлагатай")
    _scope(user, body["site_id"])  # хариуцаагүй зогсоолын тооцоог хаах/засахыг хориглоно
    st = (db.query(DailySettlement)
          .filter(DailySettlement.site_id == body["site_id"], DailySettlement.date == body["date"]).first())
    if not st:
        st = DailySettlement(site_id=body["site_id"], date=body["date"])
        db.add(st)
    for k in ("confirmed_card", "confirmed_qpay", "confirmed_cash"):
        if k in body:
            setattr(st, k, body[k] or 0)
    if "note" in body:
        st.note = body["note"]
    if body.get("status") == "CLOSED" and st.status != "CLOSED":
        st.status, st.closed_by, st.closed_at = "CLOSED", user.username, datetime.utcnow()
    elif body.get("status") == "OPEN":
        st.status, st.closed_by, st.closed_at = "OPEN", None, None
    db.add(AuditLog(username=user.username, action="SETTLEMENT", entity="settlement",
                    entity_id=f"{body['site_id']}:{body['date']}",
                    detail={"status": st.status}))
    db.commit()
    return to_dict(st)


@router.get("/settlement/excel")
def settlement_excel(site_id: str, date_from: str | None = None, date_to: str | None = None,
                     db: Session = Depends(get_db), user: User = Depends(require("reports"))):
    """Санхүүгийн тооцооны Excel."""
    data = settlement(site_id, date_from, date_to, db, user)
    return _excel.settlement_excel(data["rows"])


@router.get("/daily/excel")
def daily_excel(date_from: str | None = None, date_to: str | None = None,
                site_id: str | None = None,
                db: Session = Depends(get_db), user: User = Depends(require("reports"))):
    """Өдөр өдрөөр задарсан тайлангийн Excel."""
    start, end = _range(date_from, date_to)
    out, tot = _daily_rows(db, start, end, _scope(user, site_id))
    return _excel.daily_excel(out, tot)


@router.get("/by-shift/excel")
def by_shift_excel(date_from: str | None = None, date_to: str | None = None, site_id: str | None = None,
                   db: Session = Depends(get_db), user: User = Depends(require("reports"))):
    """Ээлжээр тайлангийн Excel."""
    start, end = _range(date_from, date_to)
    rows = _shift_rows(db, start, end, _scope(user, site_id))
    return _excel.by_shift_excel(rows)


@router.get("/monthly/excel")
def monthly_excel(date_from: str | None = None, date_to: str | None = None, site_id: str | None = None,
                  db: Session = Depends(get_db), user: User = Depends(require("reports"))):
    """Сараар тайлангийн Excel (төлбөрийн хэрэгслээр)."""
    start, end = _range(date_from, date_to)
    data = monthly_report(date_from, date_to, site_id, db, user)
    daily, _ = _daily_rows(db, start, end, _scope(user, site_id))
    return _excel.monthly_excel(data, daily)


@router.get("/by-payment/excel")
def by_payment_excel(date_from: str | None = None, date_to: str | None = None, site_id: str | None = None,
                     db: Session = Depends(get_db), user: User = Depends(require("reports"))):
    """Төлбөрийн төрлөөр тайлангийн Excel."""
    data = by_payment(date_from, date_to, site_id, db, user)
    start, end = _range(date_from, date_to)
    return _excel.by_payment_excel(data, start, end)


@router.get("/site-sessions/excel")
def site_sessions_excel(site_id: str, date_from: str | None = None, date_to: str | None = None,
                        db: Session = Depends(get_db), user: User = Depends(require("reports"))):
    """Нэг зогсоолын session-уудын дэлгэрэнгүй Excel (тайлангийн мөрийн 'Татах' үйлдэл)."""
    _scope(user, site_id)  # хариуцаагүй зогсоолын дэлгэрэнгүйг татахыг хориглоно
    start, end = _range(date_from, date_to)
    site = db.get(ParkingSite, site_id)
    if not site:
        raise HTTPException(404, "Зогсоол олдсонгүй")
    rows = (db.query(ParkingSession)
            .filter(ParkingSession.site_id == site_id,
                    ParkingSession.entry_time >= start, ParkingSession.entry_time < end)
            .order_by(ParkingSession.entry_time.desc()).limit(20000).all())
    return _excel.site_sessions_excel(site, rows)


@router.get("/shifts/excel")
def shifts_excel(date_from: str | None = None, date_to: str | None = None,
                 db: Session = Depends(get_db), user: User = Depends(require("reports"))):
    """Касс хаалтын тайлангийн Excel."""
    from ..models import CashierShift
    start, end = _range(date_from, date_to)
    shifts = (_flt(db.query(CashierShift).filter(CashierShift.opened_at >= start,
                                                 CashierShift.opened_at < end),
                   CashierShift.site_id, _scope(user))
              .order_by(CashierShift.opened_at.desc()).limit(2000).all())
    return _excel.shifts_excel(db, shifts)


@router.get("/vat-info")
async def vat_info(user: User = Depends(require("vat", "reports"))):
    """PosAPI getInformation — сугалааны үлдэгдэл, илгээгдээгүй мэдээ (ТЕГ шаардлага №6).
    Frontend үүнийг ашиглан анхааруулга харуулна."""
    from ..config import settings
    from ..services import ebarimt
    if operator_sites(user) is not None:
        # Хариуцах зогсоолтой (tenant) хэрэглэгч — глобал PosAPI (EasyParking-ийн ТТД)
        # мэдээлэл хамаагүй тул хоосон буцаана; тэдний e-Barimt QPay ebarimt_v3-ээр үүсдэг
        return {"warnings": [], "scoped": True,
                "qpay_ebarimt": settings.qpay_ebarimt and not settings.qpay_mock,
                "local_posapi_mock": settings.ebarimt_mock}
    info = await ebarimt.get_information()
    warnings = []
    if int(info.get("leftLotteries") or 0) < 500:
        warnings.append(f"Сугалааны дугаар дуусаж байна ({info.get('leftLotteries')} үлдсэн) — "
                        "шинээр авахгүй бол сугалаагүй баримт хэвлэгдэнэ!")
    if int(info.get("unsentCount") or 0) > 0:
        warnings.append(f"Илгээгдээгүй {info.get('unsentCount')} баримт байна — "
                        "3 хоногийн дотор илгээх хуультай.")
    # e-Barimt-ийн 2 суваг: (1) QR/QPay — ebarimt_v3 (бодит, QPay ТЕГ рүү өөрөө илгээнэ),
    # (2) локал PosAPI — картын/бэлэн баримтад (энэ хуудасны сугалаа/мэдээ илгээх хэсэг).
    return {**info, "warnings": warnings,
            "qpay_ebarimt": settings.qpay_ebarimt and not settings.qpay_mock,
            "local_posapi_mock": settings.ebarimt_mock}


@router.post("/vat-send")
async def vat_send(db: Session = Depends(get_db), user: User = Depends(require("vat", "reports"))):
    """Борлуулалтын мэдээг ТЕГ рүү ГАРААР илгээх (ТЕГ шаардлага №5 — гэмтэл саатлын үед)."""
    from ..models import AuditLog
    from ..services import ebarimt
    if operator_sites(user) is not None:
        raise HTTPException(403, "Глобал PosAPI мэдээ илгээх нь EasyParking-ийн санхүүгийн үйлдэл.")
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
    # Дугаар/зогсоолыг хамт өгнө — «энэ баримт аль машин, аль зогсоолынх вэ»
    # гэдэг UI дээр харагдахгүй байсан (нэг query, session→site join)
    q = (db.query(VatReceipt, ParkingSession.plate_number, ParkingSite.name)
         .outerjoin(ParkingSession, VatReceipt.session_id == ParkingSession.id)
         .outerjoin(ParkingSite, ParkingSession.site_id == ParkingSite.id)
         .filter(VatReceipt.created_at >= start, VatReceipt.created_at < end))
    # Tenant хэрэглэгч зөвхөн өөрийн зогсоолын баримт харна (session-гүй баримт орохгүй)
    q = _flt(q, ParkingSession.site_id, _scope(user))
    rows = q.order_by(VatReceipt.created_at.desc()).limit(min(limit, 1000)).all()
    return [to_dict(r, extra={"plate_number": plate, "site_name": site_name})
            for r, plate, site_name in rows]


def _scoped_usernames(db, user) -> list[str] | None:
    """Tenant хэрэглэгчид үйлдлийн логоос зөвхөн ӨӨРИЙН зогсоолуудтай огтлолцсон
    хэрэглэгчдийн (болон өөрийн) үйлдлийг харуулна. AuditLog-д site_id байхгүй тул
    хэрэглэгчээр нь ойролцоолж шүүнэ. None = хязгааргүй (EasyParking түвшин)."""
    allowed = operator_sites(user)
    if allowed is None:
        return None
    aset = set(allowed)
    names = {user.username}
    for u in db.query(User).all():
        sites = {s for s in (u.site_ids or []) if s} or ({u.site_id} if u.site_id else set())
        if sites & aset:
            names.add(u.username)
    return sorted(names)


@router.get("/audit-logs")
def audit_logs(username: str | None = None, action: str | None = None, limit: int = 200,
               db: Session = Depends(get_db), user: User = Depends(require("logs"))):
    q = db.query(AuditLog)
    scoped = _scoped_usernames(db, user)
    if scoped is not None:
        q = q.filter(AuditLog.username.in_(scoped))
    if username:
        q = q.filter(AuditLog.username == username)
    if action:
        q = q.filter(AuditLog.action == action)
    return [to_dict(a) for a in q.order_by(AuditLog.created_at.desc()).limit(min(limit, 1000)).all()]


@router.get("/audit-logs/excel")
def audit_logs_excel(username: str | None = None, action: str | None = None,
                     db: Session = Depends(get_db), user: User = Depends(require("logs"))):
    """Үйлдлийн логийг Excel болгон татна (ADMIN/FINANCE)."""
    q = db.query(AuditLog)
    scoped = _scoped_usernames(db, user)
    if scoped is not None:
        q = q.filter(AuditLog.username.in_(scoped))
    if username:
        q = q.filter(AuditLog.username == username)
    if action:
        q = q.filter(AuditLog.action == action)
    rows = q.order_by(AuditLog.created_at.desc()).limit(10000).all()
    return _excel.audit_logs_excel(rows)


@router.get("/lpr-events")
def lpr_events(site_id: str | None = None, plate: str | None = None,
               lane: str | None = None, limit: int = 200,
               db: Session = Depends(get_db), user: User = Depends(require("logs", "dashboard"))):
    """Камерын уншилтын лог. plate=эхний тэмдэгтээр шүүнэ (гарах OCR зөрүүг илрүүлэхэд —
    орох vs гарах уншилтыг харьцуулна), lane=entry|exit. Гарах уншилт бүрд тухайн
    агшинд нээлттэй session ЯГ таарч байсан эсэхийг (matched) тэмдэглэнэ."""
    from ..models import Device
    from ..session_logic import normalize_plate
    q = _flt(db.query(LprEvent), LprEvent.site_id, _scope(user, site_id))
    if plate:
        q = q.filter(LprEvent.plate_number.ilike(f"{normalize_plate(plate)}%"))
    if lane in ("entry", "exit"):
        q = q.filter(LprEvent.lane_dir == lane)
    events = q.order_by(LprEvent.created_at.desc()).limit(min(limit, 1000)).all()
    dev_ids = {e.device_id for e in events if e.device_id}
    names = ({d.id: d.name for d in db.query(Device).filter(Device.id.in_(dev_ids)).all()}
             if dev_ids else {})
    # Гарах уншилт нээлттэй session-тэй ЯГ таарч байсан эсэх (odoo байдлаар)
    exit_plates = {e.plate_number for e in events if e.lane_dir == "exit"}
    open_plates = set()
    if exit_plates:
        open_plates = {p for (p,) in db.query(ParkingSession.plate_number)
                       .filter(ParkingSession.plate_number.in_(exit_plates),
                               ParkingSession.status.in_(["OPEN", "AWAITING_PAYMENT", "PAID"])).all()}
    out = []
    for e in events:
        d = to_dict(e)
        d["device_name"] = names.get(e.device_id)
        d["matched"] = (e.plate_number in open_plates) if e.lane_dir == "exit" else None
        out.append(d)
    return out


@router.get("/lpr-events/excel")
def lpr_events_excel(site_id: str | None = None, plate: str | None = None,
                     lane: str | None = None, limit: int = 5000,
                     db: Session = Depends(get_db), user: User = Depends(require("logs", "dashboard"))):
    """Камерын уншилтын логыг Excel болгож татна (гарах камерын уншсан дугаарууд).
    plate/lane шүүлт lpr-events-тэй ижил."""
    from ..models import Device
    from ..session_logic import normalize_plate
    q = _flt(db.query(LprEvent), LprEvent.site_id, _scope(user, site_id))
    if plate:
        q = q.filter(LprEvent.plate_number.ilike(f"{normalize_plate(plate)}%"))
    if lane in ("entry", "exit"):
        q = q.filter(LprEvent.lane_dir == lane)
    events = q.order_by(LprEvent.created_at.desc()).limit(min(limit, 20000)).all()
    dev_ids = {e.device_id for e in events if e.device_id}
    names = ({d.id: d.name for d in db.query(Device).filter(Device.id.in_(dev_ids)).all()}
             if dev_ids else {})
    return _excel.lpr_events_excel(events, names)


# Хаалт нээх командын эх сурвалжийн монгол нэр — "хэн/юугаар нээгдсэн"-ийг харуулна
_CMD_SRC_MN = {"auto_entry": "Авто орох", "auto_exit": "Авто гарах (үнэгүй/төлсөн)",
               "payment": "Төлбөрийн дараа", "manual": "Гараар (оператор)",
               "whitelist": "Цагаан жагсаалт", "forced": "Албадан"}


def _barrier_command_rows(db, site_id, plate, source, limit):
    from ..models import BarrierCommand, Device
    q = db.query(BarrierCommand).join(Device, BarrierCommand.device_id == Device.id)
    q = _flt(q, Device.site_id, site_id)
    if source:
        q = q.filter(BarrierCommand.command_source == source)
    cmds = q.order_by(BarrierCommand.created_at.desc()).limit(min(limit, 2000)).all()
    # Дугаарыг session-оос холбоно (аль машинд хаалт нээснийг харуулна)
    sids = {c.session_id for c in cmds if c.session_id}
    plates = ({s.id: s.plate_number for s in
               db.query(ParkingSession).filter(ParkingSession.id.in_(sids)).all()}
              if sids else {})
    dev = {d.id: d.name for d in db.query(Device).filter(
        Device.id.in_({c.device_id for c in cmds if c.device_id})).all()}
    if plate:
        pl = plate.upper()
        cmds = [c for c in cmds if plates.get(c.session_id, "").startswith(pl)]
    return cmds, plates, dev


@router.get("/barrier-commands")
def barrier_commands(site_id: str | None = None, plate: str | None = None,
                     source: str | None = None, limit: int = 200,
                     db: Session = Depends(get_db), user: User = Depends(require("logs", "dashboard"))):
    """Хаалт нээх командын лог — аль машинд, ямар эх сурвалжаар (авто гарах / төлбөр /
    гараар), амжилттай эсэхийг харуулна. Төлбөргүй машин гарсан бол ХААЛТ ХЭРХЭН
    нээгдсэнийг эндээс шалгана (эсвэл команд огт байхгүй = tailgating)."""
    cmds, plates, dev = _barrier_command_rows(db, _scope(user, site_id), plate, source, limit)
    return [{"id": c.id, "created_at": c.created_at.isoformat() if c.created_at else None,
             "command": c.command, "source": c.command_source,
             "source_mn": _CMD_SRC_MN.get(c.command_source, c.command_source),
             "status": c.status, "plate_number": plates.get(c.session_id),
             "device_name": dev.get(c.device_id), "issued_by": c.issued_by,
             "response_text": (c.response_text or "")[:200]} for c in cmds]


@router.get("/barrier-commands/excel")
def barrier_commands_excel(site_id: str | None = None, plate: str | None = None,
                           source: str | None = None, limit: int = 5000,
                           db: Session = Depends(get_db), user: User = Depends(require("logs", "dashboard"))):
    cmds, plates, dev = _barrier_command_rows(db, _scope(user, site_id), plate, source, limit)
    return _excel.barrier_commands_excel(cmds, plates, dev, _CMD_SRC_MN)
