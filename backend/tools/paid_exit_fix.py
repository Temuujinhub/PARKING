"""Төлсөн ч гарах уншилт алдагдсан сешний ХИЙ дүнг цэвэрлэх (paid-exit fix).

Асуудал (Кэй Эйч 2026-09-08, 4924УНУ/3311УЕУ/7179УНО): жолооч QR-аар төлсөн,
гарах камер машиныг уншаагүй → сешн PAID хэвээр «дотор» гэж тоологдож, 2–3
хоногийн дараа камерт дахин уншигдахад орсноос хойшхи БҮХ цагаар (2,326 мин =
50,000₮, 4,255 мин = 75,000₮) дахин бодогдож, 49,000–70,000₮-ийн хуурамч
үлдэгдэл/өр/QR үүсдэг байв. Баримт нь төлсөн 1,000–5,000₮-өөр ЗӨВ үүссэн тул
тайлангийн «бодогдсон дүн» ба борлуулалтын бүртгэл зөрдөг.

Код талын засвар (session_logic.paid_exit_expired / close_paid_as_inferred_exit)
ИРЭЭДҮЙН тохиолдлыг хаана. Энэ хэрэгсэл ӨНГӨРСӨН өгөгдлийг засна:

  • сешн: exit_time = exit_deadline, exit_confirmed = false, дүн = ТӨЛСӨН ҮЕИЙН
    дүн (баримттай яг таарна), status = CLOSED, тэмдэглэлд шалтгаан
  • тэр сешний PENDING (төлөгдөөгүй) QPay нэхэмжлэл → CANCELLED (жолооч
    санамсаргүй төлөх эрсдэлийг хаана)
  • тэр сешнээс үүссэн PENDING өр → CANCELLED (шалтгаантай)
  • AuditLog PAID_EXIT_FIX

Шалгуур: paid_at байгаа, exit_deadline-аас хойш --hours (анхдагч 2) цагаас
илүү хугацаанд гарах уншилт ирээгүй (одоо ч PAID гацсан, ЭСВЭЛ хожуу уншигдаад
дүн нь төлснөөс ИХ болж дахин бодогдсон). Зөрүүгээ ТӨЛСӨН (total_fee <= төлсөн)
сешнд хүрэхгүй — тэр бодит хугацааны төлбөр.

Анхдагчаар ЗӨВХӨН ХАРУУЛНА (dry-run). Бичихийн тулд --apply.

    cd /root/PARKING/backend
    venv/bin/python tools/paid_exit_fix.py --since 2026-08-01            # харах
    venv/bin/python tools/paid_exit_fix.py --since 2026-08-01 --apply    # засах
    venv/bin/python tools/paid_exit_fix.py --site "Кэй Эйч" --apply

Өөр сан дээр (ж: тест сервер дээр сэргээсэн продын backup):
    PARKING_DATABASE_URL=postgresql+psycopg2://parking:...@localhost/parking_prod_probe \\
        venv/bin/python tools/paid_exit_fix.py --since 2026-08-01
"""
import argparse
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import func, or_  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import AuditLog, Compensation, ParkingSession, ParkingSite, Payment  # noqa: E402
from app.session_logic import paid_total, session_fee_info  # noqa: E402

TZ = timedelta(hours=settings.tz_offset_hours)
TOOL = "paid_exit_fix"


def _ub(dt):
    return (dt + TZ).strftime("%m-%d %H:%M") if dt else "—"


def find_candidates(db, since: datetime, hours: int, site_like: str | None,
                    stuck_hours: int = 24):
    """Одоо ч PAID гацсаныг зөвхөн `stuck_hours` (24ц)-аас хуучин бол — саяхных нь
    гарах уншилт одоо ирж болзошгүй; тэдгээрийг авто цэвэрлэгээ (paid_exit_hours)
    хариуцна. Хаагдсан/дахин бодогдсон сешнд `hours` (2ц) босго."""
    now = datetime.utcnow()
    lag = timedelta(hours=hours)
    stuck_lag = timedelta(hours=max(hours, stuck_hours))
    q = (db.query(ParkingSession)
         .filter(ParkingSession.paid_at.isnot(None),
                 ParkingSession.entry_time >= since,
                 or_(
                     # одоо ч PAID гацсан (гарах уншилт огт ирээгүй)
                     (ParkingSession.status == "PAID")
                     & (func.coalesce(ParkingSession.exit_deadline, ParkingSession.paid_at)
                        < now - stuck_lag),
                     # хожуу уншигдаад/хаагдаад орсноос хойшхи бүх цагаар дахин бодогдсон
                     ParkingSession.exit_time.isnot(None)
                     & (ParkingSession.exit_time
                        > func.coalesce(ParkingSession.exit_deadline, ParkingSession.paid_at) + lag),
                 )))
    if site_like:
        q = q.join(ParkingSite, ParkingSite.id == ParkingSession.site_id) \
             .filter(ParkingSite.name.ilike(f"%{site_like}%"))
    out = []
    for s in q.order_by(ParkingSession.entry_time).all():
        paid = paid_total(db, s)
        # Гацсан PAID-ийг ямагт; хаагдсаныг зөвхөн дүн нь төлснөөс ИХ бол (зөрүүгээ
        # төлсөн = бодит хугацааны төлбөр, хүрэхгүй)
        if s.status != "PAID" and float(s.total_fee or 0) <= paid:
            continue
        out.append((s, paid))
    return out


def fix_one(db, s: ParkingSession, paid: float, dry: bool) -> dict:
    deadline = s.exit_deadline or s.paid_at
    fee = session_fee_info(db, s, at=s.paid_at)
    pend_pay = (db.query(Payment).filter(Payment.session_id == s.id,
                                         Payment.status == "PENDING").all())
    pend_comp = (db.query(Compensation).filter(Compensation.session_id == s.id,
                                               Compensation.status == "PENDING").all())
    info = {"plate": s.plate_number, "site": s.site.name if s.site else "?",
            "entry": _ub(s.entry_time), "paid_at": _ub(s.paid_at), "exit_was": _ub(s.exit_time),
            "status_was": s.status, "fee_was": float(s.total_fee or 0), "paid": paid,
            "fee_new": fee["total_fee"], "exit_new": _ub(deadline),
            "cancel_invoices": [f"{float(p.amount):.0f}₮ {p.provider}" for p in pend_pay],
            "cancel_debts": [f"{float(c.amount):.0f}₮ {c.reason}" for c in pend_comp]}
    if dry:
        return info
    s.exit_time = deadline
    s.exit_confirmed = False
    s.duration_minutes = fee["duration_minutes"]
    s.base_fee, s.vat_amount, s.total_fee = fee["base_fee"], fee["vat_amount"], fee["total_fee"]
    s.status = "CLOSED"
    s.note = (f"{s.note + ' | ' if s.note else ''}{TOOL}: төлсөн ч гарах уншилт алдагдсан — "
              f"deadline ({_ub(deadline)}) дээр гарсан гэж үзэж төлсөн дүнгээр засав "
              f"(өмнө {info['fee_was']:.0f}₮, {info['status_was']})")[:1000]
    now = datetime.utcnow()
    for p in pend_pay:
        p.status = "CANCELLED"
    for c in pend_comp:
        c.status = "CANCELLED"
        c.cancelled_at = now
        c.cancelled_by = TOOL
        c.cancel_reason = "Системийн алдаа — гарах уншилт алдагдсанаас хий дүнгээр үүссэн өр"
    db.add(AuditLog(username=TOOL, action="PAID_EXIT_FIX", entity="session", entity_id=s.id,
                    detail={k: v for k, v in info.items() if k not in ("site",)}))
    return info


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", default=(datetime.utcnow() - timedelta(days=60)).strftime("%Y-%m-%d"),
                    help="энэ огнооноос хойш орсон сешнүүд (анхдагч: 60 хоног)")
    ap.add_argument("--hours", type=int, default=2, help="deadline-аас хойшхи босго (анхдагч 2)")
    ap.add_argument("--stuck-hours", type=int, default=24,
                    help="одоо ч PAID гацсан сешнийг энэ цагаас хуучин бол (анхдагч 24)")
    ap.add_argument("--site", default=None, help="зогсоолын нэрийн хэсэг")
    ap.add_argument("--apply", action="store_true", help="бодитоор бичих (анхдагч: зөвхөн харуулна)")
    a = ap.parse_args()
    since = datetime.fromisoformat(a.since) - TZ
    db = SessionLocal()
    try:
        cands = find_candidates(db, since, a.hours, a.site, a.stuck_hours)
        print(f"{'ЗАСНА' if a.apply else 'DRY-RUN'} — {a.since}-аас хойш, босго {a.hours}ц: "
              f"{len(cands)} сешн")
        tot_was = tot_new = 0.0
        for s, paid in cands:
            info = fix_one(db, s, paid, dry=not a.apply)
            tot_was += info["fee_was"]
            tot_new += info["fee_new"]
            extra = ""
            if info["cancel_invoices"]:
                extra += f"  ✂ нэхэмжлэл: {', '.join(info['cancel_invoices'])}"
            if info["cancel_debts"]:
                extra += f"  ✂ өр: {', '.join(info['cancel_debts'])}"
            print(f"  {info['plate']:<9} {info['site'][:16]:<16} орсон {info['entry']} · төлсөн "
                  f"{info['paid_at']} {info['paid']:>7.0f}₮ · гарц {info['exit_was']} → {info['exit_new']} · "
                  f"дүн {info['fee_was']:>8.0f} → {info['fee_new']:>6.0f}₮ [{info['status_was']}]{extra}")
        print(f"\nБодогдсон дүн: {tot_was:,.0f}₮ → {tot_new:,.0f}₮ (хий дүн {tot_was - tot_new:,.0f}₮)")
        if a.apply:
            db.commit()
            print("✓ Хадгалав (AuditLog: PAID_EXIT_FIX).")
        elif cands:
            print("Бичихийн тулд --apply нэмнэ.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
