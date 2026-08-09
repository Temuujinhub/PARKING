"""Гарах хаалтыг тогтмол хаах цэвэрлэгээ — ХАМГААЛАЛТУУД нь ажиллаж байгаа эсэх.

    cd backend && venv/bin/python tests/test_barrier_sweep.py

Энэ функц ФИЗИК хаалт руу «хаах» команд илгээдэг тул хамгаалалт нь эвдэрвэл
хаалганы доор явж буй машин дээр хаалт буух эрсдэлтэй. Тиймээс `due_barriers`
нь дараах бүх тохиолдолд ХООСОН буцаахыг барина:

  • зогсоол дээр тохиргоо унтраалттай
  • сүүлийн минутуудад ГАРАХ уншилт болсон (машин хөдөлж байна)
  • хаалтад саяхан команд илгээгдсэн (дөнгөж нээгдсэн)
  • зогсоолд төлбөр хүлээж буй машин байна (гарцад зогсож байж болзошгүй)
  • өмнөх цэвэрлэгээнээс хойш давтамжийн хугацаа болоогүй
"""
import os
import sys
import uuid
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings  # noqa: E402

settings.barrier_close_sweep_min = 0     # глобал default — унтраалттай
settings.barrier_sweep_quiet_min = 5

from app.database import SessionLocal  # noqa: E402
from app.models import (  # noqa: E402
    BarrierCommand, Device, LprEvent, ParkingSession, ParkingSite,
)
from app.services.barrier_sweep import due_barriers  # noqa: E402

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗ <<< FAIL'} {name}{'' if cond else f'  [{extra}]'}")


db = SessionLocal()
made: list = []
NOW = datetime.utcnow()


def add(obj):
    db.add(obj)
    db.flush()
    made.append(obj)
    return obj


def names(pairs):
    return sorted(d.name for _s, d in pairs)


try:
    site = add(ParkingSite(id=str(uuid.uuid4()), name="ZZ-Sweep",
                           site_code=f"ZS{uuid.uuid4().hex[:7]}", capacity=10,
                           barrier_close_sweep_min=30))
    cam_ex = add(Device(id=str(uuid.uuid4()), site_id=site.id, name="Гарах камер",
                        device_type="camera", ip_address="10.93.93.11", lane_no=2,
                        lane_dir="exit", status="active",
                        device_key=f"zz-{uuid.uuid4().hex[:10]}"))
    bar_ex = add(Device(id=str(uuid.uuid4()), site_id=site.id, name="Гарах хаалт",
                        device_type="barrier", lane_no=2, lane_dir="exit", status="active",
                        device_key=f"zz-{uuid.uuid4().hex[:10]}"))
    bar_in = add(Device(id=str(uuid.uuid4()), site_id=site.id, name="Орох хаалт",
                        device_type="barrier", lane_no=1, lane_dir="entry", status="active",
                        device_key=f"zz-{uuid.uuid4().hex[:10]}"))
    db.commit()

    print("\n1. Тайван зогсоол — гарах хаалт хаагдана, ОРОХ хаалтад хүрэхгүй")
    due = due_barriers(db, NOW)
    mine = [(s, d) for s, d in due if s.id == site.id]
    check("гарах хаалт сонгогдов", names(mine) == ["Гарах хаалт"], names(mine))

    print("\n2. Тохиргоо унтраалттай (0) — юу ч хийхгүй")
    site.barrier_close_sweep_min = 0
    db.commit()
    check("хоосон", not [d for s, d in due_barriers(db, NOW) if s.id == site.id])
    site.barrier_close_sweep_min = 30
    db.commit()

    print("\n3. Саяхан ГАРАХ уншилт болсон — машин хөдөлж байна, хаахгүй")
    ev = add(LprEvent(id=str(uuid.uuid4()), site_id=site.id, device_id=cam_ex.id,
                      plate_number="1111ЗЗЗ", lane_dir="exit", confidence=99,
                      accepted=True, raw={}, created_at=NOW - timedelta(minutes=2)))
    db.commit()
    check("хоосон (2 мин өмнө уншилт)",
          not [d for s, d in due_barriers(db, NOW) if s.id == site.id])
    ev.created_at = NOW - timedelta(minutes=40)   # чимээгүй цонхноос гарлаа
    db.commit()
    check("40 мин өмнөх уншилт саад болохгүй",
          names([(s, d) for s, d in due_barriers(db, NOW) if s.id == site.id]) == ["Гарах хаалт"])

    print("\n4. Хаалтад саяхан команд илгээгдсэн — дөнгөж нээгдсэн, хаахгүй")
    cmd = add(BarrierCommand(id=str(uuid.uuid4()), device_id=bar_ex.id, command="open",
                             command_source="auto_exit", status="SUCCESS",
                             created_at=NOW - timedelta(minutes=1)))
    db.commit()
    check("хоосон (1 мин өмнө нээсэн)",
          not [d for s, d in due_barriers(db, NOW) if s.id == site.id])

    print("\n5. Өмнөх ЦЭВЭРЛЭГЭЭ саяхан болсон — давтамж хүлээнэ")
    cmd.command = "close"
    cmd.command_source = "sweep"
    cmd.created_at = NOW - timedelta(minutes=10)   # чимээгүй цонхноос гарсан ч 30 мин болоогүй
    db.commit()
    check("хоосон (10 мин өмнө цэвэрлэсэн, давтамж 30)",
          not [d for s, d in due_barriers(db, NOW) if s.id == site.id])
    cmd.created_at = NOW - timedelta(minutes=31)
    db.commit()
    check("31 мин өнгөрсөн — дахин хаана",
          names([(s, d) for s, d in due_barriers(db, NOW) if s.id == site.id]) == ["Гарах хаалт"])

    print("\n6. Гарцад төлбөр хүлээж буй машин — хаахгүй")
    sess = add(ParkingSession(id=str(uuid.uuid4()), site_id=site.id, plate_number="2222ЗЗЗ",
                              entry_time=NOW - timedelta(hours=2), status="AWAITING_PAYMENT"))
    db.commit()
    check("хоосон (AWAITING_PAYMENT)",
          not [d for s, d in due_barriers(db, NOW) if s.id == site.id])
    sess.status = "CLOSED"
    db.commit()

    print("\n7. Хаалт идэвхгүй (устгагдсан) — сонгогдохгүй")
    bar_ex.status = "deleted"
    db.commit()
    check("хоосон", not [d for s, d in due_barriers(db, NOW) if s.id == site.id])
    bar_ex.status = "active"
    db.commit()

    print("\n8. «Хоёулаа» (both) чиглэлтэй хаалт ч хамрагдана")
    bar_in.lane_dir = "both"
    db.commit()
    check("both хаалт нэмэгдэв",
          names([(s, d) for s, d in due_barriers(db, NOW) if s.id == site.id])
          == ["Гарах хаалт", "Орох хаалт"],
          names([(s, d) for s, d in due_barriers(db, NOW) if s.id == site.id]))

finally:
    db.rollback()
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
