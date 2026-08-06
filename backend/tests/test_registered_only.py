"""Хаалттай зогсоол (registered_only) — зөвхөн бүртгэлтэй машинд хаалт нээнэ.

    cd backend && venv/bin/python tests/test_registered_only.py

Monnis ажилчдын зогсоол: registered_only=true үед бүртгэлгүй машинд орох хаалт
НЭЭГДЭХГҮЙ (session бүртгэл хэвийн үүснэ — хамгаалагч гараар оруулж болно),
бүртгэлтэй машинд хэвийн нээгдэнэ. Давтан уншилт (dedup) ч мөн нээхгүй.
"""
import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings

settings.barrier_mock = True
settings.snapshot_enabled = False
settings.screen_enabled = False

from app.database import SessionLocal  # noqa: E402
from app.models import (  # noqa: E402
    AuditLog, BarrierCommand, Device, LprEvent, ParkingSession, ParkingSite,
    RegisteredDriver,
)
from app.session_logic import handle_entry  # noqa: E402

PASS = FAIL = 0


def check(name, cond):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗ <<< FAIL'} {name}")


CODE = "ZZREGONLY"
REG_PLATE = "9001ААА"
UNREG_PLATE = "9002БББ"
UNREG_PLATE2 = "9003ВВВ"
PLATES = [REG_PLATE, UNREG_PLATE, UNREG_PLATE2]

db = SessionLocal()

old = db.query(ParkingSite).filter(ParkingSite.site_code == CODE).first()
if old:
    ids = [d.id for d in db.query(Device).filter(Device.site_id == old.id).all()]
    sess_ids = [s.id for s in db.query(ParkingSession)
                .filter(ParkingSession.site_id == old.id).all()]
    if ids:
        db.query(BarrierCommand).filter(BarrierCommand.device_id.in_(ids)).delete(
            synchronize_session=False)
    if sess_ids:
        db.query(BarrierCommand).filter(BarrierCommand.session_id.in_(sess_ids)).delete(
            synchronize_session=False)
    # FK дараалал: session (entry_device_id) → device → site
    db.query(ParkingSession).filter(ParkingSession.site_id == old.id).delete(
        synchronize_session=False)
    db.query(LprEvent).filter(LprEvent.site_id == old.id).delete(synchronize_session=False)
    db.query(Device).filter(Device.site_id == old.id).delete()
    db.query(RegisteredDriver).filter(RegisteredDriver.site_id == old.id).delete(
        synchronize_session=False)
    db.delete(old)
    db.commit()

site = ParkingSite(name="ZZ хаалттай зогсоол тест", site_code=CODE, zone_code="A",
                   capacity=0, registered_only=True)
db.add(site)
db.flush()
cam = Device(site_id=site.id, name="Орох камер", device_type="camera",
             ip_address="127.0.0.1", lane_no=1, lane_dir="entry", auto_open=True,
             status="active", device_key=f"cam-{CODE}")
bar = Device(site_id=site.id, name="Орох хаалт", device_type="barrier",
             ip_address="", lane_no=1, lane_dir="entry", auto_open=False,
             status="active", device_key=f"bar-{CODE}")
db.add_all([cam, bar])
driver = RegisteredDriver(plate_number=REG_PLATE, full_name="Тест ажилтан",
                          contract_type="STAFF", site_id=site.id, is_active=True,
                          valid_from=datetime.utcnow() - timedelta(days=1),
                          valid_to=datetime.utcnow() + timedelta(days=30))
db.add(driver)
db.commit()


def open_cmds():
    return (db.query(BarrierCommand)
            .filter(BarrierCommand.device_id == bar.id,
                    BarrierCommand.command == "open").count())


def cleanup_plates():
    ids = [s.id for s in db.query(ParkingSession)
           .filter(ParkingSession.plate_number.in_(PLATES)).all()]
    db.query(LprEvent).filter(LprEvent.plate_number.in_(PLATES)).delete(
        synchronize_session=False)
    if ids:
        # barrier_commands.session_id FK — session-оос ӨМНӨ устгана
        db.query(BarrierCommand).filter(BarrierCommand.session_id.in_(ids)).delete(
            synchronize_session=False)
        db.query(AuditLog).filter(AuditLog.entity_id.in_(ids)).delete(synchronize_session=False)
        db.query(ParkingSession).filter(ParkingSession.id.in_(ids)).delete(
            synchronize_session=False)
    db.commit()


async def run():
    cleanup_plates()

    print("Бүртгэлгүй машин — хаалттай зогсоолд:")
    r = await handle_entry(db, cam, UNREG_PLATE, 100.0, {})
    check("session хэвийн үүснэ", r["action"] == "entry" and r.get("session_id"))
    check("хаалт НЭЭГДЭХГҮЙ", r["barrier_opened"] is False)
    check("нээх команд илгээгээгүй", open_cmds() == 0)

    print("\nДавтан уншилт (dedup) — мөн нээхгүй:")
    r = await handle_entry(db, cam, UNREG_PLATE, 100.0, {})
    check("dedup гэж таньсан", r["action"] in ("dedup", "burst_dedup"))
    check("хаалт мөн нээгдээгүй", r["barrier_opened"] is False)
    check("команд мөн илгээгээгүй", open_cmds() == 0)

    print("\nБүртгэлтэй машин — хэвийн нэвтэрнэ:")
    # dedup/burst цонхноос гарахын тулд өмнөх event-үүдийг хуучруулна
    for ev in db.query(LprEvent).filter(LprEvent.site_id == site.id).all():
        ev.created_at = datetime.utcnow() - timedelta(minutes=5)
    db.commit()
    r = await handle_entry(db, cam, REG_PLATE, 100.0, {})
    check("session үүснэ", r["action"] == "entry")
    check("хаалт НЭЭГДЭНЭ", r["barrier_opened"] is True)
    check("нээх команд илгээгдсэн", open_cmds() == 1)

    print("\nregistered_only=false буцаахад бүх машин ордог:")
    site.registered_only = False
    for ev in db.query(LprEvent).filter(LprEvent.site_id == site.id).all():
        ev.created_at = datetime.utcnow() - timedelta(minutes=5)
    # reopen cooldown-оос гарахын тулд өмнөх командыг хуучруулна
    for c in db.query(BarrierCommand).filter(BarrierCommand.device_id == bar.id).all():
        c.created_at = datetime.utcnow() - timedelta(minutes=5)
    db.commit()
    r = await handle_entry(db, cam, UNREG_PLATE2, 100.0, {})
    check("бүртгэлгүй машинд ч нээгдэнэ", r["barrier_opened"] is True)
    check("шинэ команд илгээгдсэн", open_cmds() == 2)


try:
    asyncio.get_event_loop().run_until_complete(run())
finally:
    cleanup_plates()
    db.query(RegisteredDriver).filter(RegisteredDriver.id == driver.id).delete(
        synchronize_session=False)
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
