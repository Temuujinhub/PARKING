#!/usr/bin/env python3
"""Тайлангийн тоонуудыг сешн бүрээр ТУЛГАЖ шалгах (цэвэрлэгээний дараа).

«Үүссэн − Нийт = Төлөгдөөгүй» гэсэн тэнцэл яагаад таарахгүй байгааг, мөн
цэвэрлэгээ бүрэн болсон эсэхийг зогсоол × сараар задалж харуулна:

    Орсон / Гарсан / Зогсож буй  — сешний ТОО
    Хугацаа                      — нийт зогссон цаг (гацсан сешн энд харагдана)
    Үүссэн                       — total_fee нийлбэр
    Хураасан                     — тэдгээр сешнээс төлөгдсөн (Payment)
    Хүлээгдэж буй / Өр болсон    — үлдэгдэл хаана байгаа
    ЗӨРҮҮ                        — тайлагдаагүй үлдсэн дүн (0 байх ёстой)

Мөн цэвэрлэгээ ДУТУУ болсон шинжийг тусад нь тоолно:
    • төлбөртэй ч төлөгдөөгүй, ямар ч нэхэмжлэлгүй сешн  → цэвэрлэгдээгүй
    • 24ц-аас урт зогсолт                                → гацсан бүртгэл
    • одоо ч OPEN байгаа хуучин сешн                     → авто хаалт хүрээгүй

Хэрэглээ:
    sudo /root/PARKING/backend/venv/bin/python /root/PARKING/tools/verify_reports.py
    sudo ... verify_reports.py --site "NIC" --site "Моннис" --from 2026-07-01
"""
import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime

os.chdir("/root/PARKING/backend")
sys.path.insert(0, "/root/PARKING/backend")

from sqlalchemy import func  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import Compensation, ParkingSession, ParkingSite, Payment  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--site", action="append", default=[], help="Зогсоолын нэрийн хэсэг")
    ap.add_argument("--from", dest="date_from", default=None, help="YYYY-MM-DD")
    ap.add_argument("--until", default=None, help="YYYY-MM-DD")
    ap.add_argument("--stale-hours", type=float, default=24.0,
                    help="Энэ цагаас урт зогсолтыг гацсан гэж тоолно (default 24)")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        sites = {s.id: s.name for s in db.query(ParkingSite).all()}
        wanted = set(sites)
        if args.site:
            wanted = {sid for sid, name in sites.items()
                      if any(p.strip().lower() in (name or "").lower() for p in args.site)}
            if not wanted:
                print("Зогсоол олдсонгүй. Байгаа:", ", ".join(sorted(sites.values())))
                sys.exit(1)

        q = db.query(ParkingSession).filter(ParkingSession.site_id.in_(wanted))
        if args.date_from:
            q = q.filter(ParkingSession.entry_time >= datetime.fromisoformat(args.date_from))
        if args.until:
            q = q.filter(ParkingSession.entry_time < datetime.fromisoformat(args.until))
        rows = q.all()
        if not rows:
            print("Сешн олдсонгүй.")
            return

        ids = [s.id for s in rows]
        # Төлбөр ба өрийг НЭГ query-ээр (мөр бүрд query хийвэл мянган сешнд удаана)
        paid_by_sess = defaultdict(float)
        for chunk in [ids[i:i + 900] for i in range(0, len(ids), 900)]:
            for sid, amt in (db.query(Payment.session_id, func.sum(Payment.amount))
                             .filter(Payment.session_id.in_(chunk),
                                     Payment.status == "PAID")
                             .group_by(Payment.session_id).all()):
                paid_by_sess[sid] += float(amt or 0)
        debt_by_sess = defaultdict(float)
        for chunk in [ids[i:i + 900] for i in range(0, len(ids), 900)]:
            for sid, amt in (db.query(Compensation.session_id, func.sum(Compensation.amount))
                             .filter(Compensation.session_id.in_(chunk),
                                     Compensation.status == "PENDING")
                             .group_by(Compensation.session_id).all()):
                debt_by_sess[sid] += float(amt or 0)
        # Нэг төлбөрт ӨМНӨХ сешнүүдийн өр багтдаг (include_debts) — тэр хэсгийг
        # энэ сешний "хураасан"-аас хасахгүй бол хураасан нь үүссэнээс давна
        bundled = defaultdict(float)
        for chunk in [ids[i:i + 900] for i in range(0, len(ids), 900)]:
            for sid, amt in (db.query(Payment.session_id, func.sum(Compensation.amount))
                             .select_from(Payment)
                             .join(Compensation, Compensation.payment_id == Payment.id)
                             .filter(Payment.session_id.in_(chunk),
                                     Payment.status == "PAID",
                                     Compensation.status == "PAID")
                             .group_by(Payment.session_id).all()):
                bundled[sid] += float(amt or 0)

        # Сешн ӨӨРИЙНХӨӨ өрийг хожим төлсөн — энэ нь тэр сешний хураамжийн
        # хойшлогдсон төлөлт тул «хураасан»-д тооцогдоно
        own_paid_debt = defaultdict(float)
        for chunk in [ids[i:i + 900] for i in range(0, len(ids), 900)]:
            for sid, amt in (db.query(Compensation.session_id, func.sum(Compensation.amount))
                             .filter(Compensation.session_id.in_(chunk),
                                     Compensation.status == "PAID")
                             .group_by(Compensation.session_id).all()):
                own_paid_debt[sid] += float(amt or 0)

        agg = defaultdict(lambda: dict(entered=0, exited=0, open=0, minutes=0, accrued=0.0,
                                       collected=0.0, awaiting=0.0, debt=0.0,
                                       uncleaned=0, stale=0, still_open=0))
        for s in rows:
            k = (sites.get(s.site_id, "?"), s.entry_time.strftime("%Y-%m"))
            a = agg[k]
            a["entered"] += 1
            if s.exit_time:
                a["exited"] += 1
            if s.status in ("OPEN", "AWAITING_PAYMENT", "PAID"):
                a["open"] += 1
            a["minutes"] += int(s.duration_minutes or 0)
            fee = float(s.total_fee or 0)
            # Энэ сешний хураамжид ногдох хэсэг: өөрийн төлбөр − бусдын өрийн
            # нийлүүлсэн хэсэг + ӨӨРИЙНХӨӨ өр хожим төлөгдсөн дүн
            paid = max(0.0, paid_by_sess.get(s.id, 0.0) - bundled.get(s.id, 0.0)
                       + own_paid_debt.get(s.id, 0.0))
            debt = debt_by_sess.get(s.id, 0.0)
            a["accrued"] += fee
            a["collected"] += paid
            a["debt"] += debt
            if s.status == "AWAITING_PAYMENT":
                a["awaiting"] += fee
            # Цэвэрлэгдээгүй шинж: төлбөртэй мөртлөө төлөгдөөгүй, өр ч болоогүй
            if fee > 0 and paid == 0 and debt == 0 and s.status not in ("AWAITING_PAYMENT",):
                a["uncleaned"] += 1
            if (s.duration_minutes or 0) >= args.stale_hours * 60:
                a["stale"] += 1
            if s.status == "OPEN":
                a["still_open"] += 1

        hdr = (f"{'Зогсоол · сар':28} {'Орсон':>6} {'Гарсан':>6} {'Дотор':>6} "
               f"{'Хугацаа(ц)':>10} {'Үүссэн':>11} {'Хураасан':>11} {'Хүлээгд':>9} "
               f"{'Өр':>9} {'ЗӨРҮҮ':>10}")
        print(hdr)
        print("─" * len(hdr))
        tot = defaultdict(float)
        problems = []
        for k in sorted(agg):
            a = agg[k]
            gap = a["accrued"] - a["collected"] - a["awaiting"] - a["debt"]
            print(f"{(k[0] + ' · ' + k[1]):28} {a['entered']:>6} {a['exited']:>6} "
                  f"{a['open']:>6} {a['minutes'] // 60:>10,} {a['accrued']:>11,.0f} "
                  f"{a['collected']:>11,.0f} {a['awaiting']:>9,.0f} {a['debt']:>9,.0f} "
                  f"{gap:>10,.0f}")
            for f in ("entered", "exited", "open", "accrued", "collected", "awaiting", "debt"):
                tot[f] += a[f]
            tot["minutes"] += a["minutes"]
            tot["gap"] += gap
            if a["uncleaned"] or a["stale"] or a["still_open"]:
                problems.append((k, a))

        print("─" * len(hdr))
        print(f"{'НИЙТ':28} {int(tot['entered']):>6} {int(tot['exited']):>6} "
              f"{int(tot['open']):>6} {int(tot['minutes']) // 60:>10,} {tot['accrued']:>11,.0f} "
              f"{tot['collected']:>11,.0f} {tot['awaiting']:>9,.0f} {tot['debt']:>9,.0f} "
              f"{tot['gap']:>10,.0f}")
        rate = tot["collected"] / tot["accrued"] * 100 if tot["accrued"] else 0
        print(f"\nЦуглуулалт: {rate:.0f}%  (хураасан {tot['collected']:,.0f}₮ / "
              f"үүссэн {tot['accrued']:,.0f}₮)")
        if abs(tot["gap"]) > 1:
            print(f"ЗӨРҮҮ {tot['gap']:,.0f}₮ — өр ч болоогүй, хүлээгдээгүй, "
                  f"хураагдаагүй дүн (цэвэрлэгдэх ёстой бичилтүүд).")
        else:
            print("Тэнцэл ЗӨВ: үүссэн = хураасан + хүлээгдэж буй + өр.")

        if problems:
            print("\n── Анхаарах бичилтүүд (зогсоол · сар) ──")
            for k, a in problems:
                bits = []
                if a["uncleaned"]:
                    bits.append(f"цэвэрлэгдээгүй {a['uncleaned']}")
                if a["stale"]:
                    bits.append(f"{args.stale_hours:g}ц+ зогсолт {a['stale']}")
                if a["still_open"]:
                    bits.append(f"одоо ч нээлттэй {a['still_open']}")
                print(f"  {k[0]} · {k[1]}: " + ", ".join(bits))
            print("\nЦэвэрлэх: tools/clean_test_period.py --site ... --apply")
            print("Гацсаныг хаах: Тохиргоо → Авто цэвэрлэгээ → «Яг одоо ажиллуулах»")
    finally:
        db.close()


if __name__ == "__main__":
    main()
