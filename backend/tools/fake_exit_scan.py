#!/usr/bin/env python3
"""«Хуурамч гарц» — орж ирээд ШУУД гарах уншуулаад дотроо үлддэг машинуудыг тоолох.

СХЕМ (KH зогсоолын бичлэгээр батлагдсан, 2026-08-14):
  1. жолооч орох камерт уншуулна          → session нээгдэнэ
  2. ухраад гарах камерт уншуулна         → session ҮНЭГҮЙ хаагдана
  3. өдөржин зогсоно
  4. оройдоо гарах гэхэд «бүртгэлгүй»     → оператор гараар гаргана
  → төлбөр 0₮

Энэ хэрэгсэл 2-р алхмын бүртгэлүүдийг олж, тэдгээрийн хэд нь ҮНЭХЭЭР гарсан
(дараа нь орох уншилт бий) ба хэд нь ДОТРОО үлдсэн болохыг ялгана.

`auto_reopen_for_exit` одоо (4dd9279) эдгээрийг гарах уншилт дээр сэргээдэг
болсон. Энэ хэрэгсэл нь ӨМНӨХ алдагдлыг хэмжих, босго (5 мин) зөв эсэхийг
шалгах зорилготой.

Ажиллуулах:
    sudo /root/PARKING/backend/venv/bin/python \\
        /root/PARKING/backend/tools/fake_exit_scan.py --days 3
"""
import os
import sys
from collections import Counter
from datetime import datetime, timedelta

os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.getcwd())

from app.config import settings                               # noqa: E402
from app.database import SessionLocal                         # noqa: E402
from app.models import LprEvent, ParkingSession, ParkingSite   # noqa: E402


def main(days: int, minutes: float):
    db = SessionLocal()
    try:
        since = datetime.utcnow() - timedelta(days=days)
        print(f"=== «Хуурамч гарц» шинжилгээ — сүүлийн {days} хоног, "
              f"босго {minutes:.0f} минут ===\n")
        grand = Counter()
        for site in db.query(ParkingSite).all():
            rows = (db.query(ParkingSession)
                    .filter(ParkingSession.site_id == site.id,
                            ParkingSession.entry_time >= since,
                            ParkingSession.exit_time.isnot(None),
                            ParkingSession.paid_at.is_(None))
                    .all())
            short = [s for s in rows
                     if 0 <= (s.exit_time - s.entry_time).total_seconds() / 60 <= minutes]
            if not short:
                continue
            still_in, left = [], []
            for s in short:
                # Гарсны ДАРАА тэр дугаараар ОРОХ уншилт байсан уу?
                # Байвал үнэхээр гарч, дахин орсон (хуурамч биш).
                back = (db.query(LprEvent)
                        .filter(LprEvent.site_id == site.id,
                                LprEvent.lane_dir == "entry",
                                LprEvent.plate_number == s.plate_number,
                                LprEvent.accepted.is_(True),
                                LprEvent.created_at > s.exit_time)
                        .first())
                (left if back else still_in).append(s)
            grand["short"] += len(short)
            grand["still_in"] += len(still_in)
            print(f"── {site.name} ({site.site_code})")
            print(f"   богино гарц (≤{minutes:.0f}м, төлбөргүй): {len(short)}")
            print(f"   ├ дараа нь ДАХИН орсон (үнэхээр гарсан): {len(left)}")
            print(f"   └ дахин ОРООГҮЙ — дотроо үлдсэн байх магадлалтай: "
                  f"{len(still_in)}")
            for s in sorted(still_in, key=lambda x: x.entry_time)[:5]:
                mins = (s.exit_time - s.entry_time).total_seconds() / 60
                print(f"        {s.plate_number:<9} {s.entry_time:%m-%d %H:%M} "
                      f"→ {s.exit_time:%H:%M} ({mins:.0f}м) {s.status}")
            print()

        print(f"═══ НИЙТ богино гарц {grand['short']}, "
              f"үүнээс дахин ороогүй {grand['still_in']}")
        print("\nДахин ороогүй нь = өдөржин зогсоод төлбөргүй гарсан байж болзошгүй.")
        print("Одооноос эдгээр нь гарах уншилт дээр АВТО сэргэж, орсон цагаас")
        print(f"төлбөр тооцогдоно (PARKING_SUSPICIOUS_EXIT_MINUTES="
              f"{settings.suspicious_exit_minutes}).")
    finally:
        db.close()


if __name__ == "__main__":
    _days, _min = 3, float(settings.suspicious_exit_minutes)
    _a = sys.argv[1:]
    for i, x in enumerate(_a):
        if x == "--days" and i + 1 < len(_a):
            _days = int(_a[i + 1])
        elif x == "--minutes" and i + 1 < len(_a):
            _min = float(_a[i + 1])
    main(_days, _min)
