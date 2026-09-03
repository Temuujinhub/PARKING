"""Кассын ээлж: нээх, хаах, тайлан."""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..auth import enforce_site, operator_site, require
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


def _z_report(db: Session, shift: CashierShift, totals: dict, closed_cars: int = 0) -> dict:
    """Ээлжийн Z-тайлан — POS терминал дээр хэвлэхэд бэлэн мөрүүд.

    Өмнө нь ээлжийн тооцоог оператор ГАРААР бичдэг байсан (алдаа, маргаан).
    Кассын бүх сувгийн задаргаа + баталгаажуулсан бэлэн мөнгө нэг хуудсанд."""
    tz = timedelta(hours=8)   # Улаанбаатар — принтерт орон нутгийн цаг
    label = {"CASH": "Бэлэн", "POS": "Карт", "QPAY": "QPay", "TRANSFER": "Дансаар"}
    site = shift.site if shift.site_id else None
    lines = [
        "ЭЭЛЖИЙН ТАЙЛАН (Z)",
        site.name if site else "",
        f"Оператор: {shift.user.username if shift.user else '-'}",
        f"Нээсэн: {(shift.opened_at + tz):%Y-%m-%d %H:%M}" if shift.opened_at else "",
        f"Хаасан: {(shift.closed_at + tz):%Y-%m-%d %H:%M}" if shift.closed_at
        else f"Хэвлэсэн: {(datetime.utcnow() + tz):%Y-%m-%d %H:%M}",
        "-" * 24,
    ]
    for prov, v in sorted(totals["by_provider"].items(), key=lambda kv: -kv[1]["amount"]):
        lines.append(f"{label.get(prov, prov)}: {v['amount']:,.0f}₮ ({v['count']})")
    lines += [
        "-" * 24,
        f"НИЙТ: {totals['total']:,.0f}₮ ({totals['count']} гүйлгээ)",
    ]
    if shift.cash_confirmed is not None:
        cash = float(totals["by_provider"].get("CASH", {}).get("amount", 0))
        diff = float(shift.cash_confirmed) - cash
        lines.append(f"Тушаасан бэлэн: {float(shift.cash_confirmed):,.0f}₮")
        if abs(diff) >= 1:
            lines.append(f"ЗӨРҮҮ: {diff:+,.0f}₮")
    if closed_cars:
        lines.append(f"Хаасан машин: {closed_cars}")
    return {"lines": [ln for ln in lines if ln]}


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
    _t = _shift_totals(db, shift)
    return {"open": True, "shift": shift_out, "remaining_cars": remaining, **_t,
            # Ээлж хаахаас өмнө ч завсрын тайлан хэвлэж болно (X-тайлан)
            "print_data": _z_report(db, shift, _t)}


@router.post("/shift/open")
def open_shift(body: dict, db: Session = Depends(get_db),
               user: User = Depends(require("cashier"))):
    # ЭРХ: role биш «cashier» МОДУЛИАР шалгана. Өмнө require_role("OPERATOR",
    # "SUPER_ADMIN") байсан тул кассын хуудас НЭЭГДЭЖ байж (GET /shift/current
    # нь require("cashier")) ээлж нээхэд ADMIN/ONLINE_OPERATOR 403 иддэг байв —
    # «админаар орсон ч операторын эрх шаардаж байна» гомдол яг үүнээс.
    if db.query(CashierShift).filter(CashierShift.user_id == user.id,
                                     CashierShift.status == "OPEN").first():
        raise HTTPException(400, "Танд нээлттэй ээлж байна. Эхлээд хаана уу.")
    # ADMIN-д үндсэн site_id байхгүй байж болно — хамрах хүрээнийхээ эхнийг авна
    site_id = body.get("site_id") or user.site_id or operator_site(user)
    enforce_site(user, site_id)
    shift = CashierShift(user_id=user.id, site_id=site_id,
                         opening_amount=body.get("opening_amount", 0))
    db.add(shift)
    db.add(AuditLog(username=user.username, action="SHIFT_OPEN", entity="shift", entity_id=""))
    db.commit()
    return to_dict(shift)


@router.post("/shift/close")
def close_shift(body: dict | None = None, db: Session = Depends(get_db),
                user: User = Depends(require("cashier"))):
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
            s.status = "FREE" if fee["is_free"] else "MANUAL_CLOSED"
            # Өр үүсгэх эсэх — Тохиргоо → Авто цэвэрлэгээ (2026-08-21-ээс өмнө
            # хатуу бичигдсэн байсан тул ээлж хаах бүрд өр хуримтлагддаг байв)
            from ..services.app_settings import get_autoclose_rules
            if not fee["is_free"] and get_autoclose_rules(
                    db, s.site_id)["create_debt_shift_close"]:
                create_compensation(db, s, "shift_close", user.username)
            # Session тутамд бичнэ — эс бол Түүх дээр «хэн хаасан» нь хоосон
            # үлдэж, оператор гараар хаасан мэт харагддаг (site-ийн түвшний
            # SHIFT_CLOSE бүртгэл нь тухайн МӨРийг тайлбарлаж чадахгүй).
            db.add(AuditLog(username=user.username, action="SHIFT_CLOSE_CAR",
                            entity="session", entity_id=s.id,
                            detail={"plate": s.plate_number, "shift": shift.id}))
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
    # POS/веб дээр шууд хэвлэх Z-тайлан
    return {"shift": to_dict(shift), **totals, "closed_cars": closed_cars,
            "print_data": _z_report(db, shift, totals, closed_cars)}


@router.get("/hr/worked-days")
def hr_worked_days(month: str, db: Session = Depends(get_db), user: User = Depends(require("users", "reports"))):
    """Хүний нөөц: тухайн сард (YYYY-MM) OPERATOR бүрийн ажилласан өдрүүд.
    Ажилласан өдөр = тухайн өдөр ээлж нээгдсэн (login-д суурилсан). Календарт харуулна."""
    from datetime import datetime as _dt
    y, m = (int(x) for x in month.split("-"))
    start = _dt(y, m, 1)
    end = _dt(y + 1, 1, 1) if m == 12 else _dt(y, m + 1, 1)
    ops = (db.query(User).filter(User.role.in_(("OPERATOR", "ONLINE_OPERATOR")),
                                 User.is_active.is_(True)).order_by(User.full_name).all())
    from ..auth import operator_sites
    allowed = operator_sites(user)
    if allowed is not None:
        # Tenant хэрэглэгч (ж: Моннис) зөвхөн өөрийн зогсоолуудын операторуудыг харна
        aset = set(allowed)
        ops = [o for o in ops
               if ({s for s in (o.site_ids or []) if s} or ({o.site_id} if o.site_id else set())) & aset]
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
    from ..auth import scoped_site
    site_id, site_ids = scoped_site(user, site_id)  # оператор зөвхөн өөрийн зогсоолууд
    q = db.query(CashierShift)
    if site_id:
        q = q.filter(CashierShift.site_id == site_id)
    elif site_ids:
        q = q.filter(CashierShift.site_id.in_(site_ids))
    if date_from:
        q = q.filter(CashierShift.opened_at >= datetime.fromisoformat(date_from))
    if date_to:
        q = q.filter(CashierShift.opened_at < datetime.fromisoformat(date_to) + timedelta(days=1))
    shifts = q.order_by(CashierShift.opened_at.desc()).limit(200).all()
    # Бүх ээлжийн provider-аар бүлэглэсэн дүнг НЭГ query-ээр (ээлж тус бүрт query хийхгүй)
    shift_ids = [s.id for s in shifts]
    totals_rows = (
        db.query(Payment.shift_id, Payment.provider,
                 func.coalesce(func.sum(Payment.amount), 0), func.count())
        .filter(Payment.shift_id.in_(shift_ids), Payment.status == "PAID")
        .group_by(Payment.shift_id, Payment.provider).all()) if shift_ids else []
    by_shift: dict[str, dict] = {}
    for sid, prov, amt, cnt in totals_rows:
        by_shift.setdefault(sid, {})[prov] = {"amount": float(amt), "count": cnt}
    out = []
    for s in shifts:
        end = s.closed_at or datetime.utcnow()
        dur_min = int((end - s.opened_at).total_seconds() // 60)
        by_provider = by_shift.get(s.id, {})
        out.append(to_dict(s, extra={
            "cashier": (s.user.full_name or s.user.username) if s.user else None,
            "site_name": s.site.name if s.site else "Бүх зогсоол",
            "duration_minutes": dur_min,
            "by_provider": by_provider,
            "total": sum(v["amount"] for v in by_provider.values()),
            "count": sum(v["count"] for v in by_provider.values())}))
    return out
