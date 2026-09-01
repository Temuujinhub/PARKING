"""Тайлан: dashboard статистик, зогсоолын орлого, Excel экспорт, НӨАТ баримт, лог.
Excel workbook угсрах код: reports_excel.py (энд endpoint-ууд нь нимгэн wrapper)."""
from collections import Counter
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..auth import operator_sites, require
from ..database import get_db
from ..models import (
    AuditLog, Compensation, LprEvent, ParkingSession, ParkingSite, Payment, User, VatReceipt,
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
    keys = ("entered", "exited", "cash_amount", "qpay_amount", "pos_amount",
            "transfer_amount", "paid_amount")
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
        cash, qpay_amt, pos, transfer = (float(prov.get(k, 0))
                                         for k in ("CASH", "QPAY", "POS", "TRANSFER"))
        out.append({"date": ds, "entered": entered, "exited": exited,
                    "cash_amount": cash, "qpay_amount": qpay_amt, "pos_amount": pos,
                    "transfer_amount": transfer,
                    "paid_amount": cash + qpay_amt + pos + transfer})
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
        cash, qpay_amt, pos, transfer = (float(prov.get(k, 0))
                                         for k in ("CASH", "QPAY", "POS", "TRANSFER"))
        paid = cash + qpay_amt + pos + transfer
        unpaid = float(db.query(func.coalesce(func.sum(ParkingSession.total_fee), 0)).filter(
            ParkingSession.site_id == s.id, ParkingSession.status == "AWAITING_PAYMENT",
            ParkingSession.entry_time >= start, ParkingSession.entry_time < end).scalar())
        # ҮҮССЭН ТӨЛБӨР — зогсоолд орж тоолуур явснаар бодогдсон нийт дүн
        # (төлөгдсөн эсэхээс үл хамааран). Цуглуулалтын хувийг эндээс харна.
        # Зогсож БУЙ (OPEN) машины дүн бүрэн бодогдоогүй тул ороогүй.
        accrued = float(db.query(func.coalesce(func.sum(ParkingSession.total_fee), 0)).filter(
            ParkingSession.site_id == s.id,
            ParkingSession.entry_time >= start, ParkingSession.entry_time < end).scalar())
        # ӨР БОЛСОН — төлөлгүй хаагдсан (шөнийн хаалт, авто хаалт, админ хассан,
        # төлбөргүй гарсан) сешнээс үүссэн ТӨЛӨГДӨӨГҮЙ нэхэмжлэл. Үүнгүйгээр
        # «Үүссэн − Нийт» зөрүү тайлагдахгүй байсан: зөрүүний гол хэсэг нь энэ.
        debt = float(db.query(func.coalesce(func.sum(Compensation.amount), 0))
                     .join(ParkingSession, Compensation.session_id == ParkingSession.id)
                     .filter(Compensation.site_id == s.id, Compensation.status == "PENDING",
                             ParkingSession.entry_time >= start,
                             ParkingSession.entry_time < end).scalar())
        # ЦУГЛУУЛАЛТЫН ХУВЬД зориулсан тоо: тухайн мужид ОРСОН сешнүүдээс
        # хураасан дүн. `paid_amount` нь мөнгө ОРСОН цагаар (paid_at) тоологддог
        # тул өмнөх хугацаанд орсон машины төлбөр багтаж, харьцаа 100%-иас
        # давдаг байсан (Кэй Эйч 103%). Хувь тооцоход ижил бүлгийг харьцуулна.
        collected = float(db.query(func.coalesce(func.sum(Payment.amount), 0))
                          .select_from(ParkingSession)
                          .join(Payment, Payment.session_id == ParkingSession.id)
                          .filter(ParkingSession.site_id == s.id, Payment.status == "PAID",
                                  ParkingSession.entry_time >= start,
                                  ParkingSession.entry_time < end).scalar())
        # Нэг төлбөр нь тухайн сешний хураамж + ӨӨР (хуучин) сешнүүдийн ӨР-ийг
        # хамт агуулж болно (_create_payment include_debts). Тэр ӨӨР сешний өр
        # нь энэ сешний хураамж БИШ тул хасна — эс бол хураасан нь үүссэнээс
        # давж, цуглуулалт 100%-иас хэтэрдэг (Кэй Эйч 8 сар −104,000₮).
        # ЧУХАЛ: сешн ӨӨРИЙНХӨӨ өрийг хожим төлсөн бол тэр нь яг тэр сешний
        # хураамжийн хойшлогдсон төлөлт тул ХАСАХГҮЙ.
        debt_in_pay = float(db.query(func.coalesce(func.sum(Compensation.amount), 0))
                            .select_from(ParkingSession)
                            .join(Payment, Payment.session_id == ParkingSession.id)
                            .join(Compensation, Compensation.payment_id == Payment.id)
                            .filter(ParkingSession.site_id == s.id,
                                    Payment.status == "PAID", Compensation.status == "PAID",
                                    Compensation.session_id != ParkingSession.id,
                                    ParkingSession.entry_time >= start,
                                    ParkingSession.entry_time < end).scalar())
        # Эсрэгээр: энэ мужийн сешний ӨӨРИЙНХ нь өр ХОЖИМ (өөр сешний төлбөрт
        # нийлүүлэгдэж) төлөгдсөн бол тэр нь ЯГ энэ сешний хураамжийн
        # хойшлогдсон төлөлт мөн — дээрх хасалтад орсон тул буцааж нэмнэ.
        # Үүнгүйгээр өрөөр цуглуулсан мөнгө хаана ч тоологдохгүй үлддэг.
        own_debt_paid = float(db.query(func.coalesce(func.sum(Compensation.amount), 0))
                              .select_from(ParkingSession)
                              .join(Compensation,
                                    Compensation.session_id == ParkingSession.id)
                              .filter(ParkingSession.site_id == s.id,
                                      Compensation.status == "PAID",
                                      ParkingSession.entry_time >= start,
                                      ParkingSession.entry_time < end).scalar())
        collected = max(0.0, collected - debt_in_pay + own_debt_paid)
        out.append({"site_id": s.id, "site_name": s.name, "entered": entered, "exited": exited,
                    "total_minutes": int(minutes or 0),
                    "cash_amount": cash, "qpay_amount": qpay_amt, "pos_amount": pos,
                    "transfer_amount": transfer, "accrued_amount": accrued,
                    "collected_amount": collected,
                    "paid_amount": paid, "unpaid_amount": unpaid, "debt_amount": debt})
    totals = {
        "entered": sum(r["entered"] for r in out), "exited": sum(r["exited"] for r in out),
        "total_minutes": sum(r["total_minutes"] for r in out),
        "cash_amount": sum(r["cash_amount"] for r in out),
        "qpay_amount": sum(r["qpay_amount"] for r in out),
        "pos_amount": sum(r["pos_amount"] for r in out),
        "transfer_amount": sum(r["transfer_amount"] for r in out),
        "accrued_amount": sum(r["accrued_amount"] for r in out),
        "collected_amount": sum(r["collected_amount"] for r in out),
        "paid_amount": sum(r["paid_amount"] for r in out),
        "unpaid_amount": sum(r["unpaid_amount"] for r in out),
        "debt_amount": sum(r["debt_amount"] for r in out),
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
        m = months.setdefault(int(ym), {"cash": 0.0, "qpay": 0.0, "pos": 0.0,
                                        "transfer": 0.0, "count": 0})
        key = {"CASH": "cash", "QPAY": "qpay", "POS": "pos", "TRANSFER": "transfer"}.get(prov)
        if key:
            m[key] += float(amt)
        m["count"] += int(cnt)
    # ҮҮССЭН ТӨЛБӨР сараар — машин ОРСОН сараар бүлэглэнэ (төлөлт хожим болсон ч
    # боломж үүссэн сард нь тооцогдоно). Цуглуулалтын хувь = төлөгдсөн / үүссэн.
    ym_entry = (cast(func.extract("year", ParkingSession.entry_time + TZ), Integer) * 100
                + cast(func.extract("month", ParkingSession.entry_time + TZ), Integer))
    aq = (db.query(ym_entry.label("ym"), func.coalesce(func.sum(ParkingSession.total_fee), 0))
          .filter(ParkingSession.entry_time >= start, ParkingSession.entry_time < end))
    aq = _flt(aq, ParkingSession.site_id, _scope(user, site_id))
    accrued = {int(ym): float(amt or 0) for ym, amt in aq.group_by("ym").all()}
    # Цуглуулалтын хувьд: тухайн сард ОРСОН сешнээс хураасан дүн (мөнгө ямар
    # сард орсноор биш) — эс бол өмнөх сарын машины төлбөр багтаж 100%+ гардаг
    cq = (db.query(ym_entry.label("ym"), func.coalesce(func.sum(Payment.amount), 0))
          .select_from(ParkingSession)   # FROM-ыг тодорхой заана (хоёр хүснэгтийн багана холилдоно)
          .join(Payment, Payment.session_id == ParkingSession.id)
          .filter(Payment.status == "PAID",
                  ParkingSession.entry_time >= start, ParkingSession.entry_time < end))
    cq = _flt(cq, ParkingSession.site_id, _scope(user, site_id))
    collected = {int(ym): float(amt or 0) for ym, amt in cq.group_by("ym").all()}
    # Төлбөрт багтсан ӨМНӨХ сешнүүдийн өрийг хасна (дээрх revenue-тэй ижил шалтгаан)
    dq = (db.query(ym_entry.label("ym"), func.coalesce(func.sum(Compensation.amount), 0))
          .select_from(ParkingSession)
          .join(Payment, Payment.session_id == ParkingSession.id)
          .join(Compensation, Compensation.payment_id == Payment.id)
          .filter(Payment.status == "PAID", Compensation.status == "PAID",
                  Compensation.session_id != ParkingSession.id,   # өөрийнх бол хойшлогдсон төлөлт
                  ParkingSession.entry_time >= start, ParkingSession.entry_time < end))
    dq = _flt(dq, ParkingSession.site_id, _scope(user, site_id))
    for ym, amt in dq.group_by("ym").all():
        ym = int(ym)
        collected[ym] = max(0.0, collected.get(ym, 0.0) - float(amt or 0))
    # Тухайн сарын сешний ӨӨРИЙНХ нь өр хожим төлөгдсөн бол нэмнэ (revenue-тэй ижил)
    oq = (db.query(ym_entry.label("ym"), func.coalesce(func.sum(Compensation.amount), 0))
          .select_from(ParkingSession)
          .join(Compensation, Compensation.session_id == ParkingSession.id)
          .filter(Compensation.status == "PAID",
                  ParkingSession.entry_time >= start, ParkingSession.entry_time < end))
    oq = _flt(oq, ParkingSession.site_id, _scope(user, site_id))
    for ym, amt in oq.group_by("ym").all():
        ym = int(ym)
        collected[ym] = collected.get(ym, 0.0) + float(amt or 0)
    for ym in accrued:
        months.setdefault(ym, {"cash": 0.0, "qpay": 0.0, "pos": 0.0,
                               "transfer": 0.0, "count": 0})

    out = []
    for ym in sorted(months, reverse=True):
        m = months[ym]
        out.append({"month": f"{ym // 100}-{ym % 100:02d}", **m,
                    "accrued": accrued.get(ym, 0.0),
                    "collected": collected.get(ym, 0.0),
                    "total": m["cash"] + m["qpay"] + m["pos"] + m["transfer"]})
    totals = {k: sum(r[k] for r in out) for k in ("cash", "qpay", "pos", "transfer",
                                                  "accrued", "collected", "total", "count")}
    return {"rows": out, "totals": totals}


PROVIDER_MN = {"CASH": "Бэлэн", "QPAY": "QPay", "POS": "Банкны карт", "TRANSFER": "Дансаар"}
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
              "MANUAL_CLOSED": "Гарах уншилтгүй"}

_CONTRACT_MN = {"MONTHLY": "Сарын", "CONTRACT": "Гэрээт", "VIP": "VIP", "STAFF": "Ажилтан",
                "SPECIAL": "Тусгай", "TRANSIT": "Дамжин", "NIGHT": "Шөнө үнэгүй"}


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
        cash, qpay_amt, pos, transfer = (float(prov.get(k, 0))
                                         for k in ("CASH", "QPAY", "POS", "TRANSFER"))
        out.append({"date": (day + TZ).strftime("%Y-%m-%d"),
                    "window": f"{h:02d}:00–{h:02d}:00",
                    "entered": sq.count(),
                    "exited": sq.filter(ParkingSession.exit_time.isnot(None)).count(),
                    "cash_amount": cash, "qpay_amount": qpay_amt, "pos_amount": pos,
                    "transfer_amount": transfer,
                    "paid_amount": cash + qpay_amt + pos + transfer})
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
              ("entered", "exited", "cash_amount", "qpay_amount", "pos_amount",
               "transfer_amount", "paid_amount")}
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
        card = pos_qpay = qr_qpay = cash = transfer = 0.0
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
            elif prov == "TRANSFER":
                transfer += amt
        st_total = card + pos_qpay + qr_qpay + cash + transfer
        # Тухайн өдөр үүссэн өр (нөхөн төлбөр)
        debt = debt_map.get(ds, 0.0)
        # Ажилласан ажилтнууд (тухайн өдөр ээлж нээсэн)
        workers = sorted(workers_map.get(ds, set()))
        st = setts.get(ds)
        if st_total <= 0 and debt <= 0 and not st:
            continue
        confirmed_cash = float(st.confirmed_cash) if st else 0.0
        confirmed_transfer = float(getattr(st, "confirmed_transfer", 0) or 0) if st else 0.0
        # Карт/QPay электрон баталгаажсан = систем; бэлэн + дансаар (шилжүүлэг)-ийг
        # санхүү дансны хуулгаас баталгаажуулна
        confirmed_total = card + pos_qpay + qr_qpay + confirmed_cash + confirmed_transfer
        out.append({"date": ds, "card": card, "pos_qpay": pos_qpay, "qr_qpay": qr_qpay,
                    "cash": cash, "transfer": transfer,
                    "system_total": st_total, "confirmed_cash": confirmed_cash,
                    "confirmed_transfer": confirmed_transfer,
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
    for k in ("confirmed_card", "confirmed_qpay", "confirmed_cash", "confirmed_transfer"):
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
async def vat_info(db: Session = Depends(get_db), user: User = Depends(require("vat", "reports"))):
    """Ибаримт хуудасны толгойн мэдээлэл — СУВАГ бүрийн бодит байдал.

    2026-08-19-өөс баримтууд msgbill.mn/QPay-ээр (бодит) үүсдэг тул хуучин
    «MOCK горим» badge нь локал PosAPI-ийн .env тохиргоог л заагаад төөрөгдүүлж
    байв. PosAPI-ийн сугалаа/илгээлтийн мэдээллийг зөвхөн PosAPI БОДИТ үед л асууна."""
    from ..config import settings
    from ..services import ebarimt, msgbill
    channels = {
        "qpay": settings.qpay_ebarimt and not settings.qpay_mock,
        "msgbill": msgbill.status_info(db).get("configured", False),
        "posapi": not settings.ebarimt_mock,
        # Хуурамч MOCK баримт үүсэх боломжтой юу (тест/демо серверт л true байх ёстой)
        "mock_receipts": settings.ebarimt_mock and settings.ebarimt_mock_receipts,
    }
    if operator_sites(user) is not None:
        # Хариуцах зогсоолтой (tenant) хэрэглэгч — глобал PosAPI (EasyParking-ийн ТТД)
        # мэдээлэл хамаагүй тул хоосон буцаана; тэдний e-Barimt QPay/msgbill-ээр үүсдэг
        return {"warnings": [], "scoped": True, "channels": channels,
                "tenants": _vat_tenants(db, user)}
    warnings = []
    if channels["mock_receipts"]:
        warnings.append("MOCK баримт асаалттай (PARKING_EBARIMT_MOCK_RECEIPTS=true) — "
                        "msgbill/PosAPI-гүй зогсоолд ХУУРАМЧ баримт үүснэ. Зөвхөн тест серверт байх ёстой!")
    info = {}
    if channels["posapi"]:
        # Локал PosAPI бодит үед л түүний сугалаа/илгээлтийг хянана
        info = await ebarimt.get_information()
        if int(info.get("leftLotteries") or 0) < 500:
            warnings.append(f"Сугалааны дугаар дуусаж байна ({info.get('leftLotteries')} үлдсэн) — "
                            "шинээр авахгүй бол сугалаагүй баримт хэвлэгдэнэ!")
        if int(info.get("unsentCount") or 0) > 0:
            warnings.append(f"Илгээгдээгүй {info.get('unsentCount')} баримт байна — "
                            "3 хоногийн дотор илгээх хуультай.")
    return {**info, "warnings": warnings, "channels": channels,
            "tenants": _vat_tenants(db, user),
            "qpay_ebarimt": channels["qpay"], "local_posapi_mock": settings.ebarimt_mock}


def _vat_tenants(db, user) -> list[dict]:
    """ТЕГ тулгалтад сонгох түрээслэгчид. ТЕГ портал ТТД тус бүрээр экспорт өгдөг
    тул «хэний баримттай тулгах вэ» гэдгийг заах ёстой. Зөвхөн зогсоолтой (тиймээс
    баримттай) түрээслэгчийг, хэрэглэгчийн эрхийн хүрээнд буцаана. /api/admin/tenants
    нь SUPER_ADMIN-only тул санхүүгийн хэрэглэгч тэндээс авч чадахгүй."""
    from ..models import Tenant
    allowed = operator_sites(user)
    q = db.query(Tenant.id, Tenant.name, Tenant.ebarimt_merchant_tin,
                 func.count(ParkingSite.id)).join(
        ParkingSite, ParkingSite.tenant_id == Tenant.id)
    if allowed is not None:
        q = q.filter(ParkingSite.id.in_(allowed))
    rows = q.group_by(Tenant.id, Tenant.name, Tenant.ebarimt_merchant_tin).order_by(Tenant.name).all()
    return [{"id": i, "name": n, "tin": tin or "", "site_count": int(c)} for i, n, tin, c in rows]


@router.post("/vat-reconcile")
async def vat_reconcile(file: UploadFile = File(...), tz_shift: float | None = None, tol: int = 3,
                        excel: bool = False, tenant_id: str | None = None,
                        col_ddtd: str | None = None, col_dt: str | None = None,
                        col_amount: str | None = None, db: Session = Depends(get_db),
                        user: User = Depends(require("vat", "reports"))):
    """ТЕГ-ийн мерчант порталын баримтын экспортыг (xlsx/csv) манай баримттай тулгана.

    ДДТД-ээр тулгаж БОЛОХГҮЙ: суваг бүр (QPay, msgbill/Онлайм) операторын кодтой
    билл буцаадаг бол ТЕГ такспэерийн ТТД + өөрийн counter-оор ӨӨР ДДТД олгодог
    (2026-08-19 нотлогдсон). Тиймээс (цаг ± tol сек, дүн)-ээр тулгана.

    tenant_id: ТЕГ портал нь ТТД тус бүрээр экспорт өгдөг тул тулгалтыг ЗӨВХӨН
    тухайн түрээслэгчийн зогсоолуудын баримттай хийнэ (эс бөгөөс бусад ТТД-ийн
    баримт «ТЕГ-д алга» болж хуурамч зөрүү харагдана).
    tz_shift: ТЕГ файлын цагт нэмэх цаг. Хоосон = 0 ба −TZ хоёрыг туршиж
    ИЛҮҮ ТААРСАНЫГ нь автоматаар сонгоно (файл UTC/локал аль нь ч байж болно).
    col_*: багана автоматаар танигдаагүй үед хэрэглэгчийн гараар заасан үсэг."""
    from ..models import Tenant, VatReceipt
    from . import vat_recon as _vr
    raw = await file.read()
    # nginx нь 10MB-аас том биеийг backend хүртэл НЭВТРҮҮЛДЭГГҮЙ (413, HTML хариу)
    # тул энд мөн 10MB-аар таслаж, ойлгомжтой мессеж өгнө
    if len(raw) > 10 * 1024 * 1024:
        raise HTTPException(400, f"Файл хэт том ({len(raw) / 1048576:.1f}MB) — дээд хязгаар 10MB. "
                                 "Огнооны мужаар хувааж экспортлох, эсвэл Excel дээр нээгээд "
                                 "«Save As → .csv» болгож оруулна уу (хэмжээ олон дахин багасна).")
    import asyncio as _aio
    try:
        # openpyxl parse нь sync — thread дээр ажиллуулж event loop-ыг блоклохгүй
        # (60,000 мөрт файл дээр /api/health 2мс → 5.2с болж хаалт/LPR царцаж байв)
        tax, diag = await _aio.to_thread(
            _vr.parse_tax_export, file.filename or "", raw,
            {"ddtd": col_ddtd, "dt": col_dt, "amount": col_amount})
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        # Гэнэтийн алдаа 500 болж, UI дээр «Алдаа гарлаа» гэсэн бүрхэг мессеж
        # болж хувирдаг байв. Оношилгооны хэрэгсэл тул алдааны ТӨРЛИЙГ хэлнэ
        # (бүтэн traceback нь journalctl-д).
        import traceback
        traceback.print_exc()
        raise HTTPException(400, f"Файл задлахад алдаа гарлаа: {type(e).__name__}: {str(e)[:200]}")
    # ─── Түрээслэгчээр хязгаарлах (сонгосон бол) ───────────────────────────
    tenant, scope_ids = None, None
    if tenant_id:
        tenant = db.get(Tenant, tenant_id)
        if not tenant:
            raise HTTPException(404, "Түрээслэгч олдсонгүй.")
        scope_ids = [r[0] for r in db.query(ParkingSite.id)
                     .filter(ParkingSite.tenant_id == tenant_id).all()]
        if not scope_ids:
            raise HTTPException(400, f"«{tenant.name}»-д зогсоол оноогоогүй байна — "
                                     "Админ → Түрээслэгч хэсэгт зогсоолыг оноож өгнө үү.")
        allowed = operator_sites(user)
        if allowed is not None:
            scope_ids = [s for s in scope_ids if s in set(allowed)]
            if not scope_ids:
                raise HTTPException(403, "Энэ түрээслэгчийн мэдээлэл харах эрхгүй.")
    # Аль ч цагийн шилжилтэд баримт багтахаар цонхыг өргөсгөж татна
    shifts = [0.0, -float(_cfg.tz_offset_hours)] if tz_shift is None else [float(tz_shift)]
    pad = timedelta(hours=max(abs(s) for s in shifts) + 1)
    lo, hi = min(t["dt"] for t in tax) - pad, max(t["dt"] for t in tax) + pad
    q = (db.query(VatReceipt, Payment, ParkingSession.plate_number, ParkingSite.name)
         .join(Payment, VatReceipt.payment_id == Payment.id)
         .outerjoin(ParkingSession, VatReceipt.session_id == ParkingSession.id)
         .outerjoin(ParkingSite, ParkingSession.site_id == ParkingSite.id)
         .filter(Payment.paid_at >= lo, Payment.paid_at < hi))
    q = (q.filter(ParkingSession.site_id.in_(scope_ids)) if scope_ids is not None
         else _flt(q, ParkingSession.site_id, _scope(user)))
    try:
        ours = q.all()
        r = await _aio.to_thread(_vr.best_shift, tax, ours, shifts, tol)
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        raise HTTPException(400, "Манай баримттай тулгах үед алдаа гарлаа: "
                                 f"{type(e).__name__}: {str(e)[:200]}")
    shift, matched, un_ours = r["shift"], r["matched"], r["unmatched_ours"]
    _tail = _vr.pos_tail_len([t["ddtd"] for t in tax])
    left = [t for t in tax if not t["used"]]
    # «ТЕГ-д бий, манайд алга» мөрүүдийг манай ТӨЛБӨРийн бүртгэлээр тайлна:
    # баримтын бүртгэл байхгүй ч төлбөр нь байвал → баримт үүссэн боловч манайд
    # хадгалагдаагүй, эсвэл давхар үүссэн. Энэ ялгааг гараар хөөх нь урт ажил байв.
    tax_explained, verdicts = [], {}
    if left:
        off = timedelta(hours=shift)
        lo_c = min(t["dt"] for t in tax) + off - timedelta(seconds=tol + 5)
        hi_c = max(t["dt"] for t in tax) + off + timedelta(seconds=tol + 5)
        pq = (db.query(Payment, ParkingSession.plate_number, ParkingSite.name,
                       ParkingSession.site_id)
              .outerjoin(ParkingSession, Payment.session_id == ParkingSession.id)
              .outerjoin(ParkingSite, ParkingSession.site_id == ParkingSite.id)
              .filter(Payment.paid_at >= lo_c, Payment.paid_at <= hi_c,
                      Payment.status == "PAID"))
        pq = _flt(pq, ParkingSession.site_id, _scope(user))   # эрхийн хүрээ л барина
        pay_rows = pq.all()
        rec_by_pay: dict[str, list] = {}
        if pay_rows:
            ids = [p.id for p, *_ in pay_rows]
            for pid, eid in db.query(VatReceipt.payment_id, VatReceipt.ebarimt_id).filter(
                    VatReceipt.payment_id.in_(ids)).all():
                rec_by_pay.setdefault(pid, []).append(eid)
        probe = [{"id": p.id, "paid_at": p.paid_at, "amount": float(p.amount),
                  "site_id": site_id, "plate": plate, "site_name": site_name,
                  "provider": p.provider, "method": p.payment_method,
                  "receipts": rec_by_pay.get(p.id, [])}
                 for p, plate, site_name, site_id in pay_rows]
        matched_pay_ids = {t["ours"][1].id for t in tax if t.get("ours")}
        tax_explained = _vr.explain_unmatched_tax(
            left, probe, shift, tol, matched_pay_ids,
            set(scope_ids) if scope_ids is not None else None)
        verdicts = dict(Counter(r["verdict"] for r in tax_explained))
    if excel:
        # Санхүүд илгээх НЭГТГЭСЭН файл: ТЕГ-ийн мөр бүрийн хажууд манай таарсан
        # баримт (машины дугаар, зогсоол, суваг, манай ДДТД) + зөрүүний хуудаснууд
        return await _aio.to_thread(
            _vr.reconcile_excel, tax, ours, un_ours, tol, _cfg.tz_offset_hours,
            tenant.name if tenant else "Бүх зогсоол", shift,
            {r["ddtd"]: r for r in tax_explained})
    return {
        "tax_total": len(tax), "ours_total": r["ours_in_window"], "matched": matched,
        "ddtd_equal": r["ddtd_equal"], "tz_shift": shift, "tol": tol,
        # Тулгалтын хүрээнээс ГАДУУР үлдсэн манай баримтууд — «зөрүү» БИШ:
        # файл тэр хугацааг хамраагүй, эсвэл баримт нь цуцлагдсан
        "outside_window": r["outside_window"], "cancelled": r["cancelled"],
        "ddtd_by_provider": r["by_provider"],
        "tenant": tenant.name if tenant else None,
        "diag": diag,
        "tax_sources": dict(Counter(t["src"] for t in tax)),
        # ДДТД-ийн СҮҮЛИЙН 8 орон = баримт гаргасан КАССЫН (POS) дугаар. Файлд
        # хэдэн өөр касс байна, тэдгээрийн аль нь манайх вэ гэдгийг харуулна —
        # «энэ 82 мөр манай аль gateway-ээр гарсан бэ» гэдгийн шууд хариу.
        "tax_pos": _vr.pos_groups([t["ddtd"] for t in tax], _tail),
        "unmatched_tax_pos": _vr.pos_groups([t["ddtd"] for t in left], _tail),
        "ours_pos": _vr.pos_groups([rec.ebarimt_id for rec, *_ in r["inside"]
                                    if rec.status != "CANCELLED"], _tail),
        # Касс бүрийн ЦАГИЙН ШИЛЖИЛТ — ТЕГ-ийн нэг экспорт дотор цагийн бүс
        # холилдож ирдэг (QPay UTC, msgbill УБ локал). Хэрэглэгчид ХАРАГДАХ ёстой.
        "group_shifts": r.get("group_shifts", {}),
        "unmatched_ours": sorted(un_ours, key=lambda x: x["paid_at"])[:100],
        "unmatched_ours_total": len(un_ours),
        "unmatched_tax": sorted(tax_explained, key=lambda x: x["dt"])[:200],
        "unmatched_tax_total": len(left),
        "tax_verdicts": verdicts, "verdict_labels": _vr.VERDICTS,
        "note": ("Тулгалт (цаг ±%dс, дүн)-ээр%s. ДДТД ижил байх шаардлагагүй — "
                 "суваг ба ТЕГ өөр дугаарладаг."
                 % (tol, "" if shift == 0 else f", файлын цагт {shift:+g}ц нэмсэн")),
    }


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


def _cancel_blocker(rec, payment, site) -> str | None:
    """Энэ баримтыг цуцлах боломжгүй БОЛ шалтгааныг буцаана (урьдчилсан шалгалт)."""
    from ..services import msgbill as _mb
    prov = rec.provider or ("QPAY" if payment.provider == "QPAY" and payment.provider_payment_id
                            else "POSAPI")
    if prov == "MSGBILL":
        if not rec.provider_ref:
            return "msgbill баримтын ID (provider_ref) алга — цуцалж чадахгүй"
        try:
            if not _mb.api_key_for(site).enabled:
                return "msgbill түлхүүр тохируулаагүй"
        except Exception:  # noqa: BLE001
            return "msgbill тохиргоог уншиж чадсангүй"
    elif prov == "QPAY" and not payment.provider_payment_id:
        return "QPay-ийн payment_id алга — цуцалж чадахгүй"
    return None


@router.post("/vat-reconcile/cancel-duplicates")
async def cancel_duplicate_receipts(body: dict, db: Session = Depends(get_db),
                                    user: User = Depends(require("vat", "reports"))):
    """Тулгалтаар илэрсэн ДАВХАР баримтуудыг БӨӨНӨӨР цуцлах.

    body: {payment_ids: [...], note: str, dry_run: bool = true}

    ⚠ ЧУХАЛ — АЛЬ баримт цуцлагдахыг ойлгох: манай систем зөвхөн ӨӨРИЙН
    үүсгэсэн (VatReceipt хүснэгтэд байгаа) баримтыг цуцалж чадна. Тулгалтад
    «ДАВХАР» гэж тэмдэглэгдсэн ТЕГ-ийн мөр нь ӨӨР кассын дугаартай (манайд
    бүртгэлгүй) байвал ТҮҮНИЙГ цуцлах боломжгүй — энэ endpoint нь тухайн
    төлбөрийн МАНАЙ баримтыг цуцална. Тиймээс dry_run=true үед юу цуцлагдахыг
    (ДДТД, суваг) бүтнээр нь буцаадаг ба UI үүнийг заавал харуулна.

    Мөнгө буцаахгүй — зөвхөн татварын баримт."""
    from .payments_router import _lock_payment, _site_of, cancel_ebarimt
    ids = [str(x) for x in (body.get("payment_ids") or []) if x]
    dry = bool(body.get("dry_run", True))
    note = str(body.get("note") or "").strip()[:120]
    if not ids:
        raise HTTPException(400, "Цуцлах төлбөр сонгогдоогүй байна.")
    if len(ids) > 500:
        raise HTTPException(400, f"Нэг удаад 500-аас олон боломжгүй ({len(ids)} ирлээ).")
    if not dry and not note:
        raise HTTPException(400, "Цуцлах шалтгааныг бичнэ үү (ТЕГ-ийн бүртгэлд үлдэнэ).")
    scope = _scope(user)
    allowed = None if scope is None else ({scope} if isinstance(scope, str) else set(scope))

    out, done, failed = [], 0, 0
    for pid in ids:
        payment = db.get(Payment, pid)
        if not payment:
            out.append({"payment_id": pid, "ok": False, "error": "Төлбөр олдсонгүй"})
            failed += 1
            continue
        site = _site_of(payment)
        if allowed is not None and (site is None or site.id not in allowed):
            out.append({"payment_id": pid, "ok": False, "error": "Энэ зогсоолын эрхгүй"})
            failed += 1
            continue
        recs = db.query(VatReceipt).filter(VatReceipt.payment_id == pid).all()
        info = {"payment_id": pid, "site_name": site.name if site else "",
                "amount": float(payment.amount), "paid_at": payment.paid_at.isoformat()
                if payment.paid_at else None,
                "receipts": [{"ddtd": r.ebarimt_id, "provider": r.provider, "status": r.status}
                             for r in recs]}
        target = [r for r in recs if r.status in ("SENT", "CANCEL_PENDING") and r.ebarimt_id]
        if not target:
            out.append({**info, "ok": False, "error": "Цуцлах ИЛГЭЭГДСЭН баримт алга"})
            failed += 1
            continue
        if dry:
            # Цуцлалт БОДИТООР ажиллах уу гэдгийг урьдчилан хэлнэ — 61 баримтыг
            # эхлүүлээд дундуур нь «provider_ref алга» гэж унахаас сэргийлнэ
            blockers = [b for b in (_cancel_blocker(r, payment, site) for r in target) if b]
            out.append({**info, "ok": not blockers,
                        "error": "; ".join(blockers) or None,
                        "will_cancel": [{"ddtd": r.ebarimt_id, "provider": r.provider}
                                        for r in target]})
            done += int(not blockers)
            failed += int(bool(blockers))
            continue
        locked = _lock_payment(db, pid)   # давхар товшилтоос хамгаална
        if locked is None:
            out.append({**info, "ok": False, "error": "Баримтын ажиллагаа явагдаж байна"})
            failed += 1
            continue
        res = await cancel_ebarimt(db, locked, note)
        db.add(AuditLog(username=user.username, action="EBARIMT_CANCEL_BULK", entity="payment",
                        entity_id=pid, detail={**res, "note": note}))
        db.commit()
        out.append({**info, **res})
        done += int(bool(res.get("ok")))
        failed += int(not res.get("ok"))
    if dry:
        # Багцын ЗОНХИЛОХ суваг — давхардал нэг сувгаас үүсдэг (жишээ: касс дээр
        # Ontime POS хэвлэдэг байсан бэлэн/картын төлбөрийн msgbill баримт).
        # Цөөнх сувгийнх нь цаг+дүнгээр санамсаргүй таарсан байх магадлалтай тул
        # СЭЖИГТЭЙ гэж тэмдэглэж, UI-д анхдагчаар СОНГОХГҮЙ.
        provs = Counter(w["provider"] for i in out for w in i.get("will_cancel", []) if w.get("provider"))
        top = provs.most_common(1)[0][0] if provs else None
        for i in out:
            ps = {w.get("provider") for w in i.get("will_cancel", [])}
            i["provider"] = "/".join(sorted(p for p in ps if p)) or ""
            i["suspect"] = bool(top and ps and ps != {top})
    if not dry:
        db.add(AuditLog(username=user.username, action="EBARIMT_CANCEL_BULK_SUMMARY",
                        entity="vat", detail={"requested": len(ids), "ok": done,
                                              "failed": failed, "note": note}))
        db.commit()
    return {"dry_run": dry, "requested": len(ids), "ok": done, "failed": failed, "items": out}


@router.get("/vat-failures")
def vat_failures(days: int = 7, db: Session = Depends(get_db),
                 user: User = Depends(require("vat", "reports"))):
    """Бүтэлгүйтсэн баримтуудыг АЛДААНЫ ШАЛТГААНААР бүлэглэж буцаана.

    Юуны учир (2026-08-28): алдааны текст `receipt_url`-д хадгалагддаг ба мөр
    тус бүрд харагддаг ч, 500+ ижил алдаа хуудаслалттай хүснэгтэд ХЭВ МАЯГ
    болж харагддаггүй. Прод дээр яг ийм хоёр тасалдал 24-48 цаг анзаарагдалгүй
    өнгөрсөн:
      • msgbill 429 «Сарын eBarimt хязгаар (500) дүүрсэн» — 85 баримт
      • QPay «түрээслэгчийн жагсаалтанд ТТД бүртгэлгүй» — 588 баримт
    Хоёулаа НЭГ шалтгаантай байсан тул бүлэглээд харвал шууд илэрнэ.
    """
    from sqlalchemy import func
    days = max(1, min(int(days or 7), 90))
    start = datetime.utcnow() - timedelta(days=days)
    q = (db.query(VatReceipt.provider, VatReceipt.receipt_url,
                  func.count().label("n"), func.min(VatReceipt.created_at).label("first_at"),
                  func.max(VatReceipt.created_at).label("last_at"),
                  func.sum(VatReceipt.amount).label("amount"))
         .outerjoin(ParkingSession, VatReceipt.session_id == ParkingSession.id)
         .filter(VatReceipt.status == "FAILED", VatReceipt.created_at >= start))
    q = _flt(q, ParkingSession.site_id, _scope(user))
    rows = (q.group_by(VatReceipt.provider, VatReceipt.receipt_url)
            .order_by(func.count().desc()).limit(20).all())
    return [{"provider": p or "?", "error": (e or "(алдаа бичигдээгүй)")[:400], "count": n,
             "first_at": f.isoformat(), "last_at": l.isoformat(), "amount": float(a or 0)}
            for p, e, n, f, l, a in rows]


@router.post("/vat-retry-failed")
async def vat_retry_failed(body: dict | None = None, db: Session = Depends(get_db),
                           user: User = Depends(require("vat", "reports"))):
    """Бүтэлгүйтсэн баримтуудыг БӨӨНӨӨР дахин үүсгэнэ. Төлбөрийг ДАХИН АВАХГҮЙ.

    Хэрэглээ: нэг гадны шалтгаанаар (msgbill квот дүүрэх, QPay-ийн ТТД бүртгэл
    унах) олон зуун баримт зэрэг унадаг. Шалтгааныг зассаны дараа тэдгээрийг
    нэг нэгээр нь дарж нөхөх боломжгүй — 2026-08-27-нд ганц тасалдлаас 85,
    QPay-гээс 588 баримт хуримтлагдсан.

    body: {days=7, provider?, limit=100, dry=false}
      dry=true  — ЮУ Ч ҮҮСГЭХГҮЙ, зөвхөн хэдэн баримт оролдохыг тоолно.

    Хамгаалалт:
      • `retry_ebarimt` өөрөө ДДТД-тэй баримтыг алгасдаг (давхардал үүсэхгүй)
        ба msgbill-д өмнө илгээсэн rcp_ дугаарын төлөвийг эхлээд асуудаг.
      • ДАРААЛЖ явна, хооронд нь завсарлагатай — ТЕГ/msgbill-ийг цохихгүй.
      • КВОТ дүүрвэл (429) ТЭР ДОРОО ЗОГСОНО: цаашид оролдох нь утгагүй бөгөөд
        бүтэлгүйтлийн тоог л хийсвэрээр өсгөнө.
      • Оператор зөвхөн ӨӨРИЙН зогсоолын баримтыг нөхнө (_scope).
    """
    import asyncio

    from .payments_router import _lock_payment, retry_ebarimt
    body = body or {}
    days = max(1, min(int(body.get("days") or 7), 90))
    limit = max(1, min(int(body.get("limit") or 100), 500))
    dry = bool(body.get("dry"))
    provider = (body.get("provider") or "").strip().upper() or None
    start = datetime.utcnow() - timedelta(days=days)

    q = (db.query(VatReceipt)
         .outerjoin(ParkingSession, VatReceipt.session_id == ParkingSession.id)
         .filter(VatReceipt.status == "FAILED", VatReceipt.created_at >= start))
    if provider:
        q = q.filter(VatReceipt.provider == provider)
    q = _flt(q, ParkingSession.site_id, _scope(user))
    recs = q.order_by(VatReceipt.created_at).limit(limit).all()
    # Нэг төлбөрт олон бүтэлгүй мөр байж болно — төлбөр бүрд НЭГ л оролдоно
    pay_ids, seen = [], set()
    for r in recs:
        if r.payment_id and r.payment_id not in seen:
            seen.add(r.payment_id)
            pay_ids.append(r.payment_id)
    if dry:
        return {"dry": True, "candidates": len(pay_ids), "rows": len(recs), "days": days,
                "provider": provider}

    out = {"total": len(pay_ids), "ok": 0, "skipped": 0, "failed": 0,
           "stopped": None, "errors": {}}
    for pid in pay_ids:
        payment = _lock_payment(db, pid)
        if payment is None:
            out["skipped"] += 1
            continue
        try:
            res = await retry_ebarimt(db, payment)
        except Exception as e:  # noqa: BLE001 — нэг баримтын алдаа бөөнийг зогсоохгүй
            res = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        db.commit()
        if res.get("ok"):
            out["ok"] += 1
        else:
            err = (res.get("error") or "?")[:200]
            out["failed"] += 1
            out["errors"][err] = out["errors"].get(err, 0) + 1
            # Квот/эрхийн алдаа = БҮХ дараагийнх нь ч унана — үргэлжлүүлэх нь утгагүй
            if "429" in err or "хязгаар" in err or "QUOTA" in err.upper():
                out["stopped"] = "Квот дүүрсэн тул зогслоо — шатлалаа ахиулаад дахин ажиллуулна уу"
                break
        await asyncio.sleep(0.3)      # ТЕГ/msgbill-ийг цохихгүй
    db.add(AuditLog(username=user.username, action="EBARIMT_RETRY_BULK", entity="vat",
                    entity_id=provider or "ALL",
                    detail={k: v for k, v in out.items() if k != "errors"}))
    db.commit()
    return out


def _vat_receipts_query(db, user, date_from, date_to, q=None, plate=None, ddtd=None,
                        lottery=None, site=None, provider=None, status=None,
                        amount=None):
    """Жагсаалт + Excel экспортын НЭГ шүүлтүүр. Багана тус бүрийн параметр
    (plate/ddtd/lottery/site/provider/status/amount) нь `q` глобал хайлттай
    ХАМТ (AND) үйлчилнэ — 2026-09-01: нэг талбарт бүгдийг холиход зогсоолын
    нэр дугаартай, дүн ДДТД-тэй андуурагдаж олддог байсныг салгав."""
    start, end = _range(date_from, date_to)
    query = (db.query(VatReceipt, ParkingSession.plate_number, ParkingSite.name)
             .outerjoin(ParkingSession, VatReceipt.session_id == ParkingSession.id)
             .outerjoin(ParkingSite, ParkingSession.site_id == ParkingSite.id)
             .filter(VatReceipt.created_at >= start, VatReceipt.created_at < end))
    # Tenant хэрэглэгч зөвхөн өөрийн зогсоолын баримт харна (session-гүй баримт орохгүй)
    query = _flt(query, ParkingSession.site_id, _scope(user))
    if q and q.strip():
        from sqlalchemy import or_
        term = q.strip()
        like = f"%{term}%"
        conds = [ParkingSession.plate_number.ilike(like), VatReceipt.ebarimt_id.ilike(like),
                 VatReceipt.lottery_code.ilike(like), ParkingSite.name.ilike(like),
                 VatReceipt.status.ilike(like), VatReceipt.provider.ilike(like)]
        # Цэвэр тоо бичвэл ДҮНГЭЭР ч хайна (ж: 1500 → 1,500₮-ийн баримтууд)
        num = term.replace(",", "").replace(" ", "")
        if num.replace(".", "", 1).isdigit() and len(num) <= 12:
            conds.append(VatReceipt.amount == float(num))
        query = query.filter(or_(*conds))
    if plate and plate.strip():
        query = query.filter(ParkingSession.plate_number.ilike(f"%{plate.strip()}%"))
    if ddtd and ddtd.strip():
        query = query.filter(VatReceipt.ebarimt_id.ilike(f"%{ddtd.strip()}%"))
    if lottery and lottery.strip():
        query = query.filter(VatReceipt.lottery_code.ilike(f"%{lottery.strip()}%"))
    if site and site.strip():
        query = query.filter(ParkingSite.name.ilike(f"%{site.strip()}%"))
    if provider and provider.strip():
        query = query.filter(VatReceipt.provider == provider.strip().upper())
    if status and status.strip():
        query = query.filter(VatReceipt.status == status.strip().upper())
    if amount not in (None, ""):
        try:
            query = query.filter(VatReceipt.amount == float(str(amount).replace(",", "")))
        except ValueError:
            pass
    return query


@router.get("/vat-receipts")
def vat_receipts(date_from: str | None = None, date_to: str | None = None,
                 q: str | None = None, plate: str | None = None, ddtd: str | None = None,
                 lottery: str | None = None, site: str | None = None,
                 provider: str | None = None, status: str | None = None,
                 amount: str | None = None,
                 limit: int = 200, db: Session = Depends(get_db),
                 user: User = Depends(require("vat", "reports"))):
    """НӨАТ баримтын жагсаалт. Багана тус бүрийн шүүлтүүр (plate/ddtd/lottery/
    site/provider/status/amount) + `q` глобал хайлт. Шүүлт `limit`-ээс ӨМНӨ
    ажиллана — тиймээс сүүлийн 200-д багтаагүй хуучин баримт ч олдоно."""
    query = _vat_receipts_query(db, user, date_from, date_to, q, plate, ddtd,
                                lottery, site, provider, status, amount)
    rows = query.order_by(VatReceipt.created_at.desc()).limit(min(limit, 1000)).all()
    return [to_dict(r, extra={"plate_number": plate_, "site_name": site_name})
            for r, plate_, site_name in rows]


@router.get("/vat-receipts/excel")
def vat_receipts_excel(date_from: str | None = None, date_to: str | None = None,
                       q: str | None = None, plate: str | None = None,
                       ddtd: str | None = None, lottery: str | None = None,
                       site: str | None = None, provider: str | None = None,
                       status: str | None = None, amount: str | None = None,
                       db: Session = Depends(get_db),
                       user: User = Depends(require("vat", "reports"))):
    """Ибаримтын жагсаалтыг (одоогийн шүүлтүүрээр, дээд тал нь 20000 мөр)
    Excel болгож татна — ТЕГ тулгалт/нягтлан бодогчид өгөх тайлан."""
    query = _vat_receipts_query(db, user, date_from, date_to, q, plate, ddtd,
                                lottery, site, provider, status, amount)
    rows = query.order_by(VatReceipt.created_at.desc()).limit(20000).all()
    return _excel.vat_receipts_excel(rows)


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
