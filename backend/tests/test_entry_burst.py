"""Орох цуврал уншилтын нэгтгэл — 2026-07-25-ны бодит кейс (локал DB ашиглана).

    cd backend && venv/bin/python tests/test_entry_burst.py

Нэг машин 3 секундын дотор 3 ӨӨР дугаараар уншигдахад (1101ЭН → 1310ХЭН → 7370ХЭН)
3 тусдаа session үүсдэг байсныг: НЭГ session + сүүлийн зөв уншилтын дугаартай
болохыг шалгана. Мөн тайрагдсан уншилт (7524УБТ → 524УБТ) давхардахгүйг шалгана.
"""
import asyncio
import os
import sys
import uuid
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings

settings.barrier_mock = True
settings.snapshot_enabled = False
settings.screen_enabled = False

from app.database import SessionLocal  # noqa: E402
from app.models import AuditLog, Device, LprEvent, ParkingSession, ParkingSite  # noqa: E402
from app.session_logic import handle_entry  # noqa: E402

PASS = FAIL = 0


def check(name, cond):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗ <<< FAIL'} {name}")


db = SessionLocal()
site = db.query(ParkingSite).filter(ParkingSite.is_active.is_(True)).first()
cam = Device(site_id=site.id, name="Тест орох камер", device_type="camera",
             lane_dir="entry", device_key=f"BURSTTEST{uuid.uuid4().hex[:6]}",
             status="active", auto_open=False)
db.add(cam)
db.commit()


def open_sessions(plates):
    return (db.query(ParkingSession)
            .filter(ParkingSession.site_id == site.id,
                    ParkingSession.plate_number.in_(plates),
                    ParkingSession.status.in_(["OPEN", "AWAITING_PAYMENT", "PAID"])).all())


def cleanup(plates):
    ids = [s.id for s in db.query(ParkingSession)
           .filter(ParkingSession.plate_number.in_(plates)).all()]
    db.query(LprEvent).filter(LprEvent.plate_number.in_(plates)).delete(synchronize_session=False)
    if ids:
        db.query(AuditLog).filter(AuditLog.entity_id.in_(ids)).delete(synchronize_session=False)
        db.query(ParkingSession).filter(ParkingSession.id.in_(ids)).delete(synchronize_session=False)
    db.commit()


async def run():
    # ─── Кейс 1: 3 өөр уншилт = 1 машин, эцсийн зөв дугаар ──────────────────
    print("Цуврал уншилт (1101ЭН → 1310ХЭН → 7370ХЭН):")
    plates = ["1101ЭН", "1310ХЭН", "7370ХЭН"]
    cleanup(plates)
    r1 = await handle_entry(db, cam, "1101ЭН", 100.0, {})
    check("1-р уншилт session үүсгэв", r1["action"] == "entry")
    r2 = await handle_entry(db, cam, "1310ХЭН", 100.0, {})
    check("2-р уншилт: шинэ session ҮҮСГЭЛГҮЙ дугаар засав",
          r2["action"] == "plate_autocorrect" and r2["new"] == "1310ХЭН")
    r3 = await handle_entry(db, cam, "7370ХЭН", 100.0, {})
    check("3-р уншилт: мөн засаж эцсийн дугаарт нийлэв",
          r3["action"] == "plate_autocorrect" and r3["new"] == "7370ХЭН")
    db.expire_all()
    live = open_sessions(plates)
    check("нээлттэй session ЯГ 1", len(live) == 1)
    check("дугаар нь эцсийн зөв уншилт (7370ХЭН)",
          live and live[0].plate_number == "7370ХЭН")
    audits = db.query(AuditLog).filter(AuditLog.action == "PLATE_AUTOCORRECT",
                                       AuditLog.entity_id == r1["session_id"]).count()
    check("залруулга бүр аудитад бичигдсэн (2)", audits == 2)
    cleanup(plates)

    # ─── Кейс 2: тайрагдсан уншилт давхардахгүй ────────────────────────────
    print("Тайрагдсан уншилт (7524УБТ → 524УБТ):")
    plates2 = ["7524УБТ", "524УБТ"]
    cleanup(plates2)
    r1 = await handle_entry(db, cam, "7524УБТ", 100.0, {})
    check("бүтэн уншилт session үүсгэв", r1["action"] == "entry")
    r2 = await handle_entry(db, cam, "524УБТ", 100.0, {})
    check("тайрагдсан уншилт dedup болов", r2["action"] in ("dedup", "burst_dedup"))
    db.expire_all()
    live = open_sessions(plates2)
    check("нээлттэй session ЯГ 1, дугаар 7524УБТ",
          len(live) == 1 and live[0].plate_number == "7524УБТ")
    cleanup(plates2)

    # ─── Кейс 3: burst цонхоос ГАДУУР өөр машин хэвийн орно ────────────────
    print("Цонхоос гадуурх өөр машин:")
    import time
    plates3 = ["5555АБВ", "6666ГДЕ"]
    cleanup(plates3)
    settings.entry_burst_seconds = 1  # тестийг хурдлуулах
    r1 = await handle_entry(db, cam, "5555АБВ", 100.0, {})
    time.sleep(1.2)
    r2 = await handle_entry(db, cam, "6666ГДЕ", 100.0, {})
    check("цонх өнгөрсний дараа тусдаа session",
          r1["action"] == "entry" and r2["action"] == "entry")
    db.expire_all()
    check("2 тусдаа session", len(open_sessions(plates3)) == 2)
    cleanup(plates3)


async def run_multilane():
    # ─── Кейс 4: ХОЁР ЭГНЭЭГЭЭР ЗЭРЭГ орсон 2 ӨӨР машин нэгтгэгдЭХГҮЙ ──────
    # (2026-09-01, Маршилын 2 орох эгнээний туршилтаар илэрсэн: burst цонх
    # зогсоол даяар хайдаг байсан тул хажуу эгнээний машиныг «нэг машин» гэж
    # үзэж эхний session-ий дугаарыг дарж бичдэг байв. Одоо burst НЭГ КАМЕРЫН
    # хүрээнд л нэгтгэнэ.)
    print("Хоёр эгнээний зэрэг орох 2 өөр машин:")
    settings.entry_burst_seconds = 6
    cam2 = Device(site_id=site.id, name="Тест орох камер 2", device_type="camera",
                  lane_dir="entry", lane_no=2, device_key=f"BURSTTEST{uuid.uuid4().hex[:6]}",
                  status="active", auto_open=False)
    db.add(cam2)
    db.commit()
    plates4 = ["7771АБГ", "7772ВЖЗ"]
    cleanup(plates4)
    r1 = await handle_entry(db, cam, "7771АБГ", 100.0, {})
    r2 = await handle_entry(db, cam2, "7772ВЖЗ", 100.0, {})
    check("хоёулаа тусдаа session үүсгэв (autocorrect БИШ)",
          r1["action"] == "entry" and r2["action"] == "entry")
    db.expire_all()
    live = open_sessions(plates4)
    check("2 тусдаа session, дугаарууд хэвээр",
          len(live) == 2 and {s.plate_number for s in live} == set(plates4))
    cleanup(plates4)
    db.query(Device).filter(Device.id == cam2.id).delete()
    db.commit()


asyncio.run(run())
asyncio.run(run_multilane())
db.query(Device).filter(Device.id == cam.id).delete()
db.commit()
db.close()

print(f"\n{PASS} PASS, {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
