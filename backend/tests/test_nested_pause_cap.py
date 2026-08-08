"""Дамжин (nested) зогсолтын ДЭЭД ХЯЗГААР нэг эх сурвалжтай эсэх.

    cd backend && venv/bin/python tests/test_nested_pause_cap.py

Асуудал (2026-08-08 аудитаар илэрсэн): хязгаарыг гурван газар өөр өөрөөр
тооцдог байв —

  • доторх ГАРАХ уншилт ирсэн үед  → зогсоолын ӨӨРИЙН transit_max_hours
  • жагсаалт/урьдчилсан тооцоонд   → глобал default
  • доторх гарах уншилт АЛДАГДААД гадна гарцад хаагдахад → глобал default

Үр дүнд нь зогсоол дээр 1 цаг гэж тохируулсан ч уншилт алдагдсан машин 4
цагийн хөнгөлөлт авдаг — өөрөөр хэлбэл АЛДАА нь ашигтай болдог байв. Мөн
/check дээр харагдаж байсан дүн гарцан дээр өөрчлөгддөг байлаа.

Энэ тест гурвуулангийнх нь ижил байхыг барина.
"""
import os
import sys
import uuid
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings  # noqa: E402

settings.transit_max_hours = 4          # глобал default = 240 мин

from app.database import SessionLocal  # noqa: E402
from app.models import ParkingSession, ParkingSite  # noqa: E402
from app.services.nested import (  # noqa: E402
    cap_minutes, close_open_pause, effective_paused_minutes, pause_cap_minutes,
    resume_session,
)

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗ <<< FAIL'} {name}{'' if cond else f'  [{extra}]'}")


db = SessionLocal()
made: list = []


def mk_site(name, transit_hours):
    s = ParkingSite(id=str(uuid.uuid4()), name=name,
                    site_code=f"ZC{uuid.uuid4().hex[:7]}", capacity=50,
                    transit_max_hours=transit_hours)
    db.add(s)
    db.flush()
    made.append(s)
    return s


def mk_session(site, plate, paused_since):
    s = ParkingSession(id=str(uuid.uuid4()), site_id=site.id, plate_number=plate,
                       entry_time=paused_since - timedelta(minutes=10), status="OPEN",
                       paused_since=paused_since, paused_minutes=0)
    db.add(s)
    db.flush()
    made.append(s)
    return s


try:
    # Нэг site доторх давхар зогсоол: хүүхэд site БАЙХГҮЙ, зогсоол дээрээ 1 цаг
    strict = mk_site("ZZ-Хязгаар-1ц", 1)
    # Тохируулаагүй зогсоол — глобал default руу унана
    loose = mk_site("ZZ-Хязгаар-default", None)
    db.commit()

    print("\n1. pause_cap_minutes — зогсоолын ӨӨРИЙН тохиргоог хүндэтгэнэ")
    check("1 цаг тохируулсан зогсоол → 60 мин", pause_cap_minutes(db, strict.id) == 60,
          pause_cap_minutes(db, strict.id))
    check("тохируулаагүй зогсоол → глобал 240 мин", pause_cap_minutes(db, loose.id) == 240,
          pause_cap_minutes(db, loose.id))
    check("cap_minutes-тэй нэг утга", pause_cap_minutes(db, strict.id) == cap_minutes(strict))

    print("\n2. Дотор 3 цаг байсан машин — гурван зам ИЖИЛ хариу өгнө")
    now = datetime.utcnow()
    since = now - timedelta(hours=3)

    # (a) жагсаалт/урьдчилсан тооцоо — session-ийг хөндөхгүй уншина
    preview = mk_session(strict, "7001ЦАП", since)
    shown = effective_paused_minutes(db, preview, now)
    check("(a) жагсаалтад 60 мин хасагдана", shown == 60, shown)

    # (b) доторх ГАРАХ уншилт ирсэн — хэвийн зам
    ok_read = mk_session(strict, "7002ЦАП", since)
    resume_session(ok_read, now, cap_minutes(strict))
    check("(b) зөв уншилтаар 60 мин хуримтлагдана", ok_read.paused_minutes == 60,
          ok_read.paused_minutes)
    check("(b) paused_since цэвэрлэгдэнэ", ok_read.paused_since is None)

    # (c) доторх гарах уншилт АЛДАГДСАН — гадна гарцад хаагдана
    lost_read = mk_session(strict, "7003ЦАП", since)
    close_open_pause(db, lost_read, now)
    check("(c) алдагдсан уншилтад ч 60 мин — ИЛҮҮ ХӨНГӨЛӨЛТ АВАХГҮЙ",
          lost_read.paused_minutes == 60, lost_read.paused_minutes)

    check("гурван зам ИЖИЛ", shown == ok_read.paused_minutes == lost_read.paused_minutes,
          f"{shown} / {ok_read.paused_minutes} / {lost_read.paused_minutes}")

    print("\n3. Хязгаарт багтсан зогсолт бүтнээрээ хасагдана")
    short = mk_session(strict, "7004ЦАП", now - timedelta(minutes=25))
    check("25 мин → 25 мин (таслагдахгүй)",
          effective_paused_minutes(db, short, now) == 25,
          effective_paused_minutes(db, short, now))

    print("\n4. Хязгааргүй (0) зогсоол — таслахгүй")
    free = mk_site("ZZ-Хязгаар-0", 0)
    db.flush()
    check("transit_max_hours=0 → хязгааргүй", pause_cap_minutes(db, free.id) == 0,
          pause_cap_minutes(db, free.id))
    endless = mk_session(free, "7005ЦАП", since)
    check("3 цаг бүтнээрээ хасагдана", effective_paused_minutes(db, endless, now) == 180,
          effective_paused_minutes(db, endless, now))

finally:
    db.rollback()
    # rollback-ийн дараа commit хийгээгүй обьектууд DB-д огт үлдээгүй тул алгасна
    from sqlalchemy import inspect as _sa_inspect
    for obj in reversed(made):
        if not _sa_inspect(obj).persistent:
            continue
        try:
            db.delete(obj)
            db.commit()
        except Exception as e:  # noqa: BLE001
            db.rollback()
            print(f"  [cleanup] {type(obj).__name__}: {str(e)[:70]}")
    db.close()

print(f"\n{'=' * 54}\nPASS={PASS} FAIL={FAIL}")
sys.exit(1 if FAIL else 0)
