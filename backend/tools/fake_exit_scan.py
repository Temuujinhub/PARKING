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
            fake, quiet = [], []
            for s in short:
                # ХУУРАМЧ ГАРЦЫН ГАРЫН ҮСЭГ: богино «гарц»-ын ДАРАА тэр машин
                # ГАРАХ камерт ДАХИН уншигдсан. Тэр нь машин дотор байсныг
                # НОТОЛНО — үнэхээр гарсан бол дахин гарч чадахгүй.
                #
                # Өмнөх хувилбар «дараа нь орох уншилт байхгүй бол дотроо
                # үлдсэн» гэж үздэг байсан нь БУРУУ: үнэхээр орж гараад тэр
                # өдөр буцаж ирээгүй машин ч тэр ангилалд ордог байв.
                again = (db.query(LprEvent)
                         .filter(LprEvent.site_id == site.id,
                                 LprEvent.lane_dir == "exit",
                                 LprEvent.plate_number == s.plate_number,
                                 LprEvent.accepted.is_(True),
                                 LprEvent.created_at > s.exit_time + timedelta(minutes=10))
                         .order_by(LprEvent.created_at).first())
                if again is None:
                    quiet.append(s)
                    continue
                # Хооронд нь ДАХИН ОРСОН бол хуурамч биш — гарч, буцаж ирээд,
                # дахин гарсан ердийн урсгал
                back = (db.query(LprEvent)
                        .filter(LprEvent.site_id == site.id,
                                LprEvent.lane_dir == "entry",
                                LprEvent.plate_number == s.plate_number,
                                LprEvent.accepted.is_(True),
                                LprEvent.created_at > s.exit_time,
                                LprEvent.created_at < again.created_at)
                        .first())
                (quiet if back else fake).append((s, again.created_at))
            grand["short"] += len(short)
            grand["fake"] += len(fake)
            print(f"── {site.name} ({site.site_code})")
            print(f"   богино гарц (≤{minutes:.0f}м, төлбөргүй): {len(short)}")
            print(f"   ├ дараа нь дахин гарах уншилт АЛГА (хуурамч биш): {len(quiet)}")
            print(f"   └ ХУУРАМЧ ГАРЦ — дотроо үлдээд дараа нь гарсан: {len(fake)}")
            for s, t2 in sorted(fake, key=lambda x: x[0].entry_time)[:5]:
                mins = (s.exit_time - s.entry_time).total_seconds() / 60
                held = (t2 - s.exit_time).total_seconds() / 3600
                print(f"        {s.plate_number:<9} {s.entry_time:%m-%d %H:%M} "
                      f"→ «гарав» {s.exit_time:%H:%M} ({mins:.0f}м) → "
                      f"ҮНЭНДЭЭ {t2:%H:%M} ({held:.1f}ц дотор) {s.status}")
            print()

        print(f"═══ НИЙТ богино гарц {grand['short']}, "
              f"үүнээс ХУУРАМЧ ГАРЦ {grand['fake']} "
              f"({grand['fake'] * 100 // max(1, grand['short'])}%)")
        print("\nЗөвхөн ХУУРАМЧ ГАРЦ нь бодит алдагдал: машин «гарсан» гэж")
        print("бүртгэгдээд дотроо үлдэж, дараа нь төлбөргүй гарсан.")
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
