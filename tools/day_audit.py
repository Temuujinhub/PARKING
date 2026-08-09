#!/usr/bin/env python3
"""Нэг өдрийн бүх машиныг задалж «яагаад төлбөр аваагүй вэ» гэдгийг тайлбарлах.

Жишээ асуулт (2026-08-10): «Эрэл-13 дээр 08-08-нд 100 орж 100 гарсан гэж
байхад ердөө 32 гүйлгээ, 45,000₮ орлоготой. Үлдсэн 68 машин яагаад төлөөгүй
вэ? Манайх бүртгэсэн үү, эсвэл алдагдсан уу?»

Хэрэгсэл нь тухайн өдөр ОРСОН сешн бүрийг үр дүнгээр нь бүлэглэнэ:
  • Төлсөн                — Payment бий
  • Гэрээт/тусгай         — is_registered (үнэгүй нэвтрэх эрхтэй)
  • Үнэгүй (тариф)        — үнэгүй хугацаанд багтсан
  • Өр болсон             — төлөлгүй гарсан, нэхэмжлэл бий
  • Авто хаагдсан         — гарах уншилтгүй/junk тул систем үнэгүй хаасан
  • ЦЭВЭРЛЭГЭЭГЭЭР тэгссэн — бидний хэрэгслүүд дүнг тэглэсэн (note-оор танина)
  • Зогсоолд байгаа       — одоо ч гараагүй
  • ТАЙЛБАРГҮЙ            — хаагдсан ч төлбөргүй, өр ч биш (шалгах ёстой)

    sudo /root/PARKING/backend/venv/bin/python /root/PARKING/tools/day_audit.py \
        --site "Эрэл" --date 2026-08-08
    # дэлгэрэнгүй жагсаалттай:
        ... --site "Эрэл" --date 2026-08-08 --list
"""
import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta

os.chdir("/root/PARKING/backend")
sys.path.insert(0, "/root/PARKING/backend")

from sqlalchemy import func  # noqa: E402
from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import (Compensation, ParkingSession, ParkingSite,  # noqa: E402
                        Payment)

CLEAN_MARKS = ("цэвэрлэгээ", "өр цуцлав", "үнэгүй болгов", "нэвтрүүлэлтийн")
AUTO_MARKS = ("авто:",)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--site", required=True, help="Зогсоолын нэрийн хэсэг")
    ap.add_argument("--date", required=True, help="YYYY-MM-DD (ЛОКАЛ огноо)")
    ap.add_argument("--list", action="store_true", help="Бүлэг бүрийн бүх мөрийг харуулах")
    args = ap.parse_args()

    tz = timedelta(hours=settings.tz_offset_hours)
    start = datetime.fromisoformat(args.date) - tz          # локал 00:00 → UTC
    end = start + timedelta(days=1)

    db = SessionLocal()
    try:
        site = next((s for s in db.query(ParkingSite).all()
                     if args.site.strip().lower() in (s.name or "").lower()), None)
        if site is None:
            print("Зогсоол олдсонгүй. Байгаа:",
                  ", ".join(s.name for s in db.query(ParkingSite).all()))
            sys.exit(1)

        rows = (db.query(ParkingSession)
                .filter(ParkingSession.site_id == site.id,
                        ParkingSession.entry_time >= start,
                        ParkingSession.entry_time < end)
                .order_by(ParkingSession.entry_time).all())
        print(f"=== {site.name} · {args.date} (локал) ===")
        if not rows:
            print("Тухайн өдөр бүртгэл алга.")
            return

        ids = [s.id for s in rows]
        paid = defaultdict(float)
        for chunk in [ids[i:i + 900] for i in range(0, len(ids), 900)]:
            for sid, amt in (db.query(Payment.session_id, func.sum(Payment.amount))
                             .filter(Payment.session_id.in_(chunk),
                                     Payment.status == "PAID")
                             .group_by(Payment.session_id).all()):
                paid[sid] += float(amt or 0)
        debts = defaultdict(lambda: [0.0, ""])
        for chunk in [ids[i:i + 900] for i in range(0, len(ids), 900)]:
            for sid, amt, st in (db.query(Compensation.session_id,
                                          func.sum(Compensation.amount),
                                          func.min(Compensation.status))
                                 .filter(Compensation.session_id.in_(chunk))
                                 .group_by(Compensation.session_id).all()):
                debts[sid] = [float(amt or 0), st or ""]

        groups = defaultdict(list)
        for s in rows:
            note = (s.note or "").lower()
            fee = float(s.total_fee or 0)
            got = paid.get(s.id, 0.0)
            debt_amt, debt_st = debts.get(s.id, [0.0, ""])
            if got > 0:
                g = "Төлсөн"
            elif s.status in ("OPEN", "AWAITING_PAYMENT", "PAID"):
                g = "Зогсоолд байгаа / гараагүй"
            elif s.is_registered:
                g = "Гэрээт/тусгай (үнэгүй эрх)"
            elif debt_st == "PENDING":
                g = "Өр болсон (нэхэгдэнэ)"
            elif debt_st == "CANCELLED":
                g = "Өр нь ЦУЦЛАГДСАН (цэвэрлэгээ)"
            elif any(m in note for m in CLEAN_MARKS):
                g = "Цэвэрлэгээгээр тэгсгэсэн"
            elif any(m in note for m in AUTO_MARKS):
                g = "Авто хаагдсан (гарах уншилтгүй/junk)"
            elif fee <= 0:
                g = "Үнэгүй (тарифын хөнгөлөлтөд багтсан)"
            else:
                g = "⚠ ТАЙЛБАРГҮЙ (хаагдсан ч төлбөргүй)"
            groups[g].append((s, fee, got, debt_amt))

        total_paid = sum(paid.values())
        exited = sum(1 for s in rows if s.exit_time)
        print(f"Орсон {len(rows)} · Гарсан {exited} · "
              f"Төлбөрийн гүйлгээ {sum(1 for v in paid.values() if v > 0)} · "
              f"Орлого {total_paid:,.0f}₮\n")

        print(f"{'Бүлэг':40} {'Тоо':>5} {'Үүссэн':>11} {'Хураасан':>11}")
        print("─" * 70)
        for g in sorted(groups, key=lambda x: -len(groups[x])):
            items = groups[g]
            fee_sum = sum(f for _s, f, _p, _d in items)
            got_sum = sum(p for _s, _f, p, _d in items)
            print(f"{g:40} {len(items):>5} {fee_sum:>11,.0f} {got_sum:>11,.0f}")
        print("─" * 70)
        print(f"{'НИЙТ':40} {len(rows):>5} "
              f"{sum(float(s.total_fee or 0) for s in rows):>11,.0f} {total_paid:>11,.0f}")

        show = groups.get("⚠ ТАЙЛБАРГҮЙ (хаагдсан ч төлбөргүй)", [])
        if show:
            print(f"\n⚠ ТАЙЛБАРГҮЙ {len(show)} бүртгэл — эдгээрийг шалгах ёстой:")
            for s, fee, _p, _d in show[:15]:
                ex = (s.exit_time + tz).strftime("%H:%M") if s.exit_time else "—"
                print(f"   {s.plate_number:10} {(s.entry_time + tz):%H:%M}→{ex:5} "
                      f"{s.status:16} {fee:>8,.0f}₮")

        if args.list:
            for g in sorted(groups, key=lambda x: -len(groups[x])):
                print(f"\n── {g} ({len(groups[g])}) ──")
                for s, fee, got, debt in groups[g]:
                    ex = (s.exit_time + tz).strftime("%H:%M") if s.exit_time else "—"
                    print(f"   {s.plate_number:10} {(s.entry_time + tz):%H:%M}→{ex:5} "
                          f"{s.status:16} үүссэн {fee:>7,.0f} хураасан {got:>7,.0f}"
                          + (f" өр {debt:,.0f}" if debt else ""))

        print("\nТайлбар: «Гэрээт/тусгай» ба «Үнэгүй» нь ЗӨВ ажиллагаа (төлбөргүй).")
        print("«Цэвэрлэгээгээр тэгсгэсэн» нь бидний хэрэгслээр дүн нь арилсан.")
        print("«ТАЙЛБАРГҮЙ» гарвал л жинхэнэ асуудал — дээрх жагсаалтыг шалгана уу.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
