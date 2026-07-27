"""Давхар уншилт хаалтыг ХЭЗЭЭ Ч хаалттай үлдээхгүй.

    cd backend && venv/bin/python tests/test_entry_barrier_always.py

Бодит алдаа (production, 2026-07-27, Моннис билдинг, 1779УНП): бүртгэлтэй
машин орох камерт уншуулсан боловч хаалт нээгдээгүй. Шалтгаан: давхар
уншилтын шүүлтүүрүүд (dedup — 20 секунд, burst — 6 секунд) нь ДАВХАР SESSION
үүсэхээс сэргийлэх зорилготой атлаа хаалт нээх кодыг ч алгасаад буцдаг байв.
Жолооч дугаараа дахин уншуулах бүрд «дахин уншсан» гэж тооцогдож, 20 секундын
турш хаалт огт нээгддэггүй байсан.

Дүрэм: session үүсгэх шийдэл ба хаалт нээх шийдэл нь ТУСДАА. Машин хаалганы
өмнө байгаа тул уншилт давтагдсан ч хаалт нээгдэх ёстой.

Шалгах зүйл:
  - Эхний уншилт: хаалт нээгдэнэ
  - Тэр даруй дахин уншихад: cooldown дотор тул ДАВХАР команд илгээхгүй
    (хаалт нээлттэй хэвээр гэж үзнэ)
  - Cooldown өнгөрсний дараа дахин уншихад: хаалт ДАХИН нээгдэнэ
  - auto_open=false камерт хаалт нээхгүй
  - Тухайн эгнээнд хаалт байхгүй бол эвдрэхгүй
"""
import asyncio
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings

settings.barrier_mock = True          # бодит камер руу команд явуулахгүй
settings.barrier_reopen_cooldown_sec = 5

from app.database import SessionLocal  # noqa: E402
from app.models import BarrierCommand, Device, ParkingSite  # noqa: E402
from app.session_logic import ensure_entry_barrier  # noqa: E402

PASS = FAIL = 0


def check(name, cond):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗ <<< FAIL'} {name}")


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


CODE = "ZZBARRIER"
db = SessionLocal()

old = db.query(ParkingSite).filter(ParkingSite.site_code == CODE).first()
if old:
    ids = [d.id for d in db.query(Device).filter(Device.site_id == old.id).all()]
    if ids:
        db.query(BarrierCommand).filter(BarrierCommand.device_id.in_(ids)).delete(
            synchronize_session=False)
    db.query(Device).filter(Device.site_id == old.id).delete()
    db.delete(old)
    db.commit()

site = ParkingSite(name="ZZ хаалт тест", site_code=CODE, zone_code="A", capacity=0)
db.add(site)
db.flush()
cam = Device(site_id=site.id, name="Орох камер", device_type="camera",
             ip_address="127.0.0.1", lane_no=1, lane_dir="entry", auto_open=True,
             status="active", device_key=f"cam-{CODE}")
bar = Device(site_id=site.id, name="Орох хаалт", device_type="barrier",
             ip_address="", lane_no=1, lane_dir="entry", auto_open=False,
             status="active", device_key=f"bar-{CODE}")
db.add_all([cam, bar])
db.commit()


def open_cmds():
    return (db.query(BarrierCommand)
            .filter(BarrierCommand.device_id == bar.id,
                    BarrierCommand.command == "open").count())


try:
    print("Эхний уншилт:")
    ok = run(ensure_entry_barrier(db, cam, "1779УНП"))
    db.commit()
    check("хаалт нээгдсэн", ok is True)
    check("1 команд илгээгдсэн", open_cmds() == 1)

    print("\nТэр даруй ДАХИН уншихад (cooldown дотор):")
    ok = run(ensure_entry_barrier(db, cam, "1779УНП"))
    db.commit()
    check("хаалт нээлттэй гэж хариулна", ok is True)
    check("ДАВХАР команд илгээгээгүй", open_cmds() == 1)

    print("\nCooldown өнгөрсний дараа:")
    for c in db.query(BarrierCommand).filter(BarrierCommand.device_id == bar.id).all():
        c.created_at = datetime.utcnow() - timedelta(seconds=60)
    db.commit()
    ok = run(ensure_entry_barrier(db, cam, "1779УНП"))
    db.commit()
    check("хаалт ДАХИН нээгдсэн", ok is True)
    check("шинэ команд илгээгдсэн", open_cmds() == 2)

    print("\nauto_open унтраасан камер:")
    cam.auto_open = False
    db.commit()
    before = open_cmds()
    ok = run(ensure_entry_barrier(db, cam, "1779УНП"))
    db.commit()
    check("хаалт нээгдэхгүй", ok is False)
    check("команд илгээгээгүй", open_cmds() == before)
    cam.auto_open = True
    db.commit()

    print("\nЭнэ эгнээнд хаалт байхгүй бол:")
    bar.status = "deleted"
    db.commit()
    ok = run(ensure_entry_barrier(db, cam, "1779УНП"))
    db.commit()
    check("алдаа шидэхгүй, False буцаана", ok is False)
finally:
    ids = [d.id for d in db.query(Device).filter(Device.site_id == site.id).all()]
    if ids:
        db.query(BarrierCommand).filter(BarrierCommand.device_id.in_(ids)).delete(
            synchronize_session=False)
    db.query(Device).filter(Device.site_id == site.id).delete()
    db.delete(site)
    db.commit()
    db.close()

print(f"\n{PASS} PASS, {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
