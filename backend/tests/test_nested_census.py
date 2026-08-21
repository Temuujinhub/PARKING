"""Талбайн тооллого: «дотор» тоолуурыг буцаан тооцох + байхгүй машиныг өргүй хаах.

Амьд Postgres шаардана. Бүх өөрчлөлт rollback хийгддэг — DB-д юу ч үлдэхгүй.

    cd backend && venv/bin/python tests/test_nested_census.py
"""
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

from app.database import SessionLocal  # noqa: E402
from app.models import (Compensation, Device, LprEvent, ParkingSession,  # noqa: E402
                        ParkingSite)
from nested_lanes import backdate_inside, close_absent  # noqa: E402

PASS = FAIL = 0


def check(name, cond):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗ <<< FAIL'} {name}")


def mkfile(path, plates):
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(plates) + "\n")
    return path


db = SessionLocal()
now = datetime.utcnow()
IN_F, OUT_F = "/tmp/_t_in.txt", "/tmp/_t_out.txt"
try:
    site = ParkingSite(name="ZZ-тооллого", site_code=f"ZZC{int(now.timestamp()) % 100000}",
                       capacity=50, is_active=True)
    db.add(site)
    db.flush()
    cam = Device(site_id=site.id, name="Дотор орох камер", device_type="camera",
                 ip_address="10.255.255.13", lane_dir="entry", nested_inner=True,
                 status="active")
    db.add(cam)
    db.flush()

    def sess(plate, entered_h, paused=None):
        # `updated_at`-ыг мөн хойш нь тавина: тооллогын хамгаалалт нь СҮҮЛИЙН
        # хөдөлгөөнөөр шалгадаг тул шинэ мөр бүр «саяхан хөдөлсөн» болж харагдана
        at = now - timedelta(hours=entered_h)
        s = ParkingSession(site_id=site.id, plate_number=plate, status="OPEN",
                           entry_time=at, updated_at=at, paused_since=paused)
        db.add(s)
        return s

    a = sess("1111ААА", 8, paused=now)      # дотор, гараар тэмдэглэсэн
    b = sess("2222БББ", 6, paused=now)      # дотор, дотоод уншилтгүй
    c = sess("3333ВВВ", 20)                 # талбайд АЛГА — хаагдана
    d = sess("4444ГГГ", 2)                  # гадаа, тооллогод бий
    f = sess("6666ЕЕЕ", 0)                  # ДӨНГӨЖ орсон, тооллогод амжаагүй
    # Өглөө орсон ч ЯГ ОДОО гарцад төлбөр хүлээж байгаа — хаах нь машиныг
    # үнэгүй гаргана. Орсон цаг нь хуучин ч сүүлийн хөдөлгөөн нь САЯ.
    g = sess("7777ЖЖЖ", 9)
    g.status, g.exit_time, g.total_fee = "AWAITING_PAYMENT", now, 20000
    db.flush()
    # «1111ААА» 3 цагийн өмнө дотогшоо орсон гэсэн уншилт
    db.add(LprEvent(site_id=site.id, device_id=cam.id, plate_number="1111ААА",
                    lane_dir="entry", confidence=99, accepted=True, raw={},
                    created_at=now - timedelta(hours=3)))
    # Session эхлэхээс ӨМНӨХ уншилт — хэрэглэгдэх ЁСГҮЙ
    db.add(LprEvent(site_id=site.id, device_id=cam.id, plate_number="1111ААА",
                    lane_dir="entry", confidence=99, accepted=True, raw={},
                    created_at=now - timedelta(hours=30)))
    db.flush()

    print("backdate_inside — дотогш орсон цагаас нь буцаан тооцох:")
    backdate_inside(db, site, apply=True)
    db.flush()
    check("уншилттай машины paused_since 3 цагийн өмнө болов",
          a.paused_since is not None
          and abs((a.paused_since - (now - timedelta(hours=3))).total_seconds()) < 2)
    check("session эхлэхээс өмнөх уншилтыг сонгоогүй",
          a.paused_since > a.entry_time)
    check("уншилтгүй машиныг хөндөөгүй", b.paused_since == now)

    print("\nclose_absent — тооллогод байхгүй бүртгэлийг өргүй хаах:")
    mkfile(IN_F, ["1111ААА", "2222БББ"])
    mkfile(OUT_F, ["4444ГГГ"])
    before_comps = db.query(Compensation).filter(Compensation.site_id == site.id).count()
    close_absent(db, site, IN_F, OUT_F, apply=True)
    db.flush()
    check("талбайд байхгүй машин хаагдав", c.status == "MANUAL_CLOSED")
    check("дүн бичээгүй (хуурамч төлбөр үүсээгүй)", float(c.total_fee or 0) == 0)
    check("өр ҮҮСЭЭГҮЙ",
          db.query(Compensation).filter(Compensation.site_id == site.id).count()
          == before_comps)
    check("тооллогод байгаа машинуудыг хөндөөгүй",
          a.status == "OPEN" and b.status == "OPEN" and d.status == "OPEN")
    check("дөнгөж орсон машиныг хаахгүй (тооллого хийх зуур ирсэн)",
          f.status == "OPEN")
    check("гарцад төлбөр хүлээж буй машиныг хаахгүй", g.status == "AWAITING_PAYMENT")

    print("\nОйролцоо (тайрагдсан) уншилттай машиныг АЛДААТАЙ хаахгүй:")
    e = sess("555ДДД", 3)                    # камер эхний цифрийг алгассан бүртгэл
    db.flush()
    mkfile(OUT_F, ["4444ГГГ", "5555ДДД"])    # тооллогод БҮТЭН дугаараар бичсэн
    close_absent(db, site, IN_F, OUT_F, apply=True)
    db.flush()
    check("ойролцоо тохирол байвал хаахгүй", e.status == "OPEN")
finally:
    db.rollback()
    db.close()
    for p in (IN_F, OUT_F):
        if os.path.exists(p):
            os.remove(p)

print(f"\n{PASS} PASS, {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
