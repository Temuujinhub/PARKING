#!/usr/bin/env python3
"""Авто хаалтын бичсэн ХУУРАМЧ зогсолтын хугацааг арилгах.

Машин гарсан ч гарах камерт уншигдаагүй бол сешн нээлттэй үлдэж, авто хаалт
хаах үедээ `duration = хаасан цаг − орсон цаг` гэж бичдэг байв. Үүнээс болж:

    Моннис билдинг: 356 сешн × 72ц = 25,712ц буюу нийт цагийн 67%

Тэдгээр машин үнэндээ 72 цаг зогссонгүй — хэзээ гарсныг нь МЭДЭХГҮЙ. Тиймээс
хугацааг нь «тодорхойгүй» (хоосон) болгоно. Мөнгө нь аль хэдийн 0 (үнэгүй
хаагдсан) тул орлогод НӨЛӨӨЛӨХГҮЙ — зөвхөн Тайлангийн «Хугацаа» багана
бодит болно.

Кодын эх үүсвэр нь мөн зассан (auto_close цаашид хуурамч хугацаа бичихгүй) —
энэ хэрэгсэл нь ӨМНӨ хуримтлагдсаныг нөхөж цэвэрлэнэ.

    sudo /root/PARKING/backend/venv/bin/python /root/PARKING/tools/clean_phantom_duration.py
    sudo ... clean_phantom_duration.py --site "Моннис" --site "Кэй Эйч" --apply
"""
import argparse
import os
import sys
from collections import defaultdict

os.chdir("/root/PARKING/backend")
sys.path.insert(0, "/root/PARKING/backend")

from sqlalchemy import or_  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import AuditLog, ParkingSession, ParkingSite  # noqa: E402

# Авто хаалтын үлдээсэн тэмдэглэгээ — эдгээр л hugацаа нь хуурамч
PHANTOM_NOTES = ("зөвхөн орох уншилттай", "формат буруу")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--site", action="append", default=[], help="Зогсоолын нэрийн хэсэг")
    ap.add_argument("--min-hours", type=float, default=0,
                    help="Зөвхөн энэ цагаас урт бичлэгийг (0 = бүгд)")
    ap.add_argument("--apply", action="store_true", help="Бодитоор цэвэрлэх")
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

        q = (db.query(ParkingSession)
             .filter(ParkingSession.site_id.in_(wanted),
                     ParkingSession.status == "FREE",
                     ParkingSession.duration_minutes.isnot(None),
                     ParkingSession.duration_minutes > 0,
                     or_(*[ParkingSession.note.ilike(f"%{n}%") for n in PHANTOM_NOTES])))
        if args.min_hours:
            q = q.filter(ParkingSession.duration_minutes >= args.min_hours * 60)
        rows = q.all()
        if not rows:
            print("Цэвэрлэх бичлэг олдсонгүй.")
            return

        by_site = defaultdict(lambda: [0, 0])
        for s in rows:
            b = by_site[sites.get(s.site_id, "?")]
            b[0] += 1
            b[1] += int(s.duration_minutes or 0)
        print("── Хуурамч хугацаатай бичлэгүүд ──")
        for name in sorted(by_site):
            cnt, mins = by_site[name]
            print(f"  {name:22} {cnt:>5} сешн · {mins / 60:>9,.0f}ц арилна")
        total_min = sum(int(s.duration_minutes or 0) for s in rows)
        print(f"\nНИЙТ: {len(rows)} сешн · {total_min / 60:,.0f} цаг")
        print("Мөнгөнд нөлөөлөхгүй (эдгээр аль хэдийн 0₮-ийн үнэгүй бүртгэл).")

        if not args.apply:
            print("\nЭнэ бол DRY-RUN — юу ч өөрчлөгдөөгүй. Бодитоор хийхдээ --apply нэмнэ.")
            return

        for s in rows:
            s.duration_minutes = None
        db.add(AuditLog(username="system", action="PHANTOM_DURATION_CLEANUP",
                        entity="session", entity_id=None,
                        detail={"sessions": len(rows), "hours_removed": round(total_min / 60),
                                "sites": args.site or "бүгд"}))
        db.commit()
        print(f"\n✅ {len(rows)} бичлэгийн хугацаа «тодорхойгүй» боллоо "
              f"({total_min / 60:,.0f}ц арилав).")
        print("Тайлан → Зогсоолоор дээрх «Хугацаа» багана бодит утга руугаа орно.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
