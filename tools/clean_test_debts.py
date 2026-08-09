#!/usr/bin/env python3
"""Тестийн үед хуримтлагдсан ЛОГИКГҮЙ өрийг (нөхөн төлбөр) цэвэрлэх.

Туршилтын хугацаанд камер/сервер тогтворгүй байснаас гарсан хиймэл өрүүд:
машин гарсан ч session хаагдаагүй → 12/72 цагийн авто хаалт → «өр». Эдгээр нь
Тайлангийн «Өр болсон» баганыг сая-саяар хөөрөгдөж, эздийг нь ХАР ЖАГСААЛТАД
оруулж байгаа тул нэг удаа цэвэрлэнэ.

Сонгох ШАЛГУУР (аль нэг нь ч бай хангагдвал «логикгүй» гэж үзнэ):
  • sys        — систем өөрөө үүсгэсэн (auto_close / admin_remove / night_close);
                 оператор гараар үүсгэсэн өрийг ХЭЗЭЭ Ч хөндөхгүй
  • --hours N  — зогсолт N цагаас урт (default 24) — бодит хүн ийм удаан
                 зогсоод төлбөргүй гардаггүй, энэ нь хаагдаагүй session
  • --min-amount N — дүн N-ээс их (тарифын дээд хязгаарт хүрсэн хиймэл өр)

Цуцлах (CANCELLED) болгоно — УСТГАХГҮЙ. Ингэснээр түүх, аудит хэвээр үлдэж,
«Өр болсон» дүн болон хар жагсаалтын тооллогоос хасагдана. Мөн зөвхөн эдгээр
өрийн улмаас автоматаар хар жагсаалтад орсон дугаарын хоригийг тайлна.

Хэрэглээ (default DRY-RUN — юу ч өөрчлөхгүй, зөвхөн харуулна):
    sudo /root/PARKING/backend/venv/bin/python /root/PARKING/tools/clean_test_debts.py
    # тодорхой зогсоол + огнооны хязгаар:
    sudo ... clean_test_debts.py --site "Моннис" --site "Кэй Эйч" --before 2026-08-09
    # бодитоор гүйцэтгэх:
    sudo ... clean_test_debts.py --site "Моннис" --site "Кэй Эйч" --apply
"""
import argparse
import os
import sys
from datetime import datetime

os.chdir("/root/PARKING/backend")
sys.path.insert(0, "/root/PARKING/backend")

from app.database import SessionLocal  # noqa: E402
from app.models import (AuditLog, BlacklistEntry, Compensation,  # noqa: E402
                        ParkingSession, ParkingSite)

SYSTEM_REASONS = ("auto_close", "admin_remove", "night_close")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--site", action="append", default=[],
                    help="Зогсоолын нэрийн хэсэг (олон удаа өгч болно). Өгөхгүй бол бүгд.")
    ap.add_argument("--before", default=None,
                    help="Зөвхөн энэ огнооноос ӨМНӨ үүссэн өр (YYYY-MM-DD)")
    ap.add_argument("--hours", type=float, default=24.0,
                    help="Зогсолт энэ цагаас урт бол логикгүй (default 24)")
    ap.add_argument("--min-amount", type=float, default=0,
                    help="Дүн үүнээс их бол логикгүй (0 = хэрэглэхгүй)")
    ap.add_argument("--include-unpaid-exit", action="store_true",
                    help="Төлбөргүй гарсан (unpaid_exit) өрийг ч оруулах — БОДИТ өр байж "
                         "болзошгүй тул анхаарна уу")
    ap.add_argument("--keep-blacklist", action="store_true",
                    help="Хар жагсаалтын автомат хоригийг хэвээр үлдээх")
    ap.add_argument("--apply", action="store_true", help="Бодитоор цуцлах")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        sites = {s.id: s.name for s in db.query(ParkingSite).all()}
        wanted = None
        if args.site:
            wanted = {sid for sid, name in sites.items()
                      if any(p.strip().lower() in (name or "").lower() for p in args.site)}
            if not wanted:
                print("Өгсөн нэрэнд тохирох зогсоол олдсонгүй. Байгаа зогсоолууд:")
                for name in sorted(sites.values()):
                    print(f"  • {name}")
                sys.exit(1)

        q = (db.query(Compensation, ParkingSession)
             .outerjoin(ParkingSession, Compensation.session_id == ParkingSession.id)
             .filter(Compensation.status == "PENDING"))
        if wanted:
            q = q.filter(Compensation.site_id.in_(wanted))
        if args.before:
            q = q.filter(Compensation.created_at < datetime.fromisoformat(args.before))

        reasons = set(SYSTEM_REASONS) | ({"unpaid_exit"} if args.include_unpaid_exit else set())

        picked, kept = [], 0
        for comp, sess in q.all():
            hours = None
            if sess is not None and sess.entry_time:
                end_t = sess.exit_time or sess.updated_at or comp.created_at
                hours = (end_t - sess.entry_time).total_seconds() / 3600
            why = []
            if (comp.reason or "") in reasons:
                why.append(comp.reason)
            if hours is not None and hours >= args.hours:
                why.append(f"{hours:.0f}ц зогссон")
            if args.min_amount and float(comp.amount) >= args.min_amount:
                why.append(f"{float(comp.amount):,.0f}₮")
            if why:
                picked.append((comp, sites.get(comp.site_id, "?"), ", ".join(why)))
            else:
                kept += 1

        if not picked:
            print(f"Цэвэрлэх өр олдсонгүй (шалгуурт нийцээгүй {kept} өр хэвээр).")
            return

        by_site: dict[str, list] = {}
        for comp, site_name, why in picked:
            by_site.setdefault(site_name, []).append((comp, why))
        for site_name in sorted(by_site):
            items = by_site[site_name]
            total = sum(float(c.amount) for c, _ in items)
            print(f"\n── {site_name}: {len(items)} өр · {total:,.0f}₮")
            for comp, why in sorted(items, key=lambda x: -float(x[0].amount))[:8]:
                print(f"   {comp.plate_number:10} {float(comp.amount):>10,.0f}₮  "
                      f"{comp.created_at:%Y-%m-%d}  ({why})")
            if len(items) > 8:
                print(f"   … ба бусад {len(items) - 8}")

        plates = {c.plate_number for c, _, _ in picked}
        grand = sum(float(c.amount) for c, _, _ in picked)
        print(f"\nНИЙТ: {len(picked)} өр · {grand:,.0f}₮ · {len(plates)} дугаар "
              f"(хөндөхгүй үлдэх: {kept} өр)")

        # Автомат хориг — зөвхөн эдгээр дугаарынх, гараар нэмсэнийг хөндөхгүй
        bl = []
        if not args.keep_blacklist and plates:
            bl = (db.query(BlacklistEntry)
                  .filter(BlacklistEntry.plate_number.in_(plates),
                          BlacklistEntry.is_active.is_(True),
                          BlacklistEntry.reason.ilike("%автомат хориг%")).all())
            print(f"Тайлагдах автомат хориг: {len(bl)}")

        if not args.apply:
            print("\nЭнэ бол DRY-RUN — юу ч өөрчлөгдөөгүй. Бодитоор хийхдээ --apply нэмнэ.")
            return

        for comp, _, why in picked:
            comp.status = "CANCELLED"
        for b in bl:
            b.is_active = False
        db.add(AuditLog(username="system", action="TEST_DEBT_CLEANUP", entity="compensation",
                        entity_id=None,
                        detail={"canceled": len(picked), "amount": round(grand, 2),
                                "plates": len(plates), "blacklist_cleared": len(bl),
                                "sites": args.site or "бүгд", "hours": args.hours}))
        db.commit()
        print(f"\n✅ {len(picked)} өр цуцлагдлаа ({grand:,.0f}₮), "
              f"{len(bl)} автомат хориг тайлагдлаа.")
        print("Тайлан → Зогсоолоор дээр «Өр болсон» багана буурсан байх ёстой.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
