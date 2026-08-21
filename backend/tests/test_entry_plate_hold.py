"""Орох дугаарын шалгалт — формат буруу уншилтад хаалтыг түр барих (локал DB).

    cd backend && venv/bin/python tests/test_entry_plate_hold.py

Хангарьд: 5-7 оронтой хог уншилт session болж хаалт нээгдээд, гарахдаа
«бүртгэлгүй гарах оролдлого» үүсгэдэг байв (2026-08-21). Шалгах зүйлс:
  • PLATE_RE: дипломат шинэ формат (1302ДК/9914АК) нэмэгдсэн, тайрагдсан
    (1234УБ) хэвээр invalid
  • policy=hold: буруу форматтай орох уншилт хаалт нээхгүй (held)
  • burst цонхонд зөв уншилт ирвэл autocorrect + хаалт нээгдэнэ
  • hold дуусахад: policy=hold → нээнэ (fail-open), strict → нээхгүй;
    хоёулаа ENTRY_HOLD audit үлдээнэ
  • dedup: барьж буй машины давтан junk нээхгүй, харин зөв уншилтын
    ард ирсэн junk (давхар уншилт) нээнэ
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
from app.models import (AppSetting, AuditLog, BarrierCommand, Device, LprEvent,  # noqa: E402
                        ParkingSession, ParkingSite)
from app.services.app_settings import (ENTRYPLATE_KEY, invalidate_cache,  # noqa: E402
                                       set_entry_plate_rules)
from app.session_logic import (entry_hold_expire, handle_entry,  # noqa: E402
                               is_valid_plate)

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗ <<< FAIL'} {name}{f' ({extra})' if extra and not cond else ''}")


db = SessionLocal()
tag = uuid.uuid4().hex[:6]
site = ParkingSite(name=f"ZZ-Hold-{tag}", site_code=f"ZZH{tag}", is_active=True)
db.add(site)
db.flush()
cam = Device(site_id=site.id, name="Тест орох камер", device_type="camera",
             lane_dir="entry", device_key=f"HOLDCAM{tag}", status="active",
             auto_open=True)
gate = Device(site_id=site.id, name="Тест орох хаалт", device_type="barrier",
              lane_dir="entry", device_key=f"HOLDBAR{tag}", status="active")
db.add_all([cam, gate])
db.commit()

# Хуучин глобал дүрмийг хадгалаад тест дуустал hold болгоно
_old_row = db.get(AppSetting, ENTRYPLATE_KEY)
_old_val = dict(_old_row.value) if _old_row and isinstance(_old_row.value, dict) else None


def set_policy(policy):
    set_entry_plate_rules(db, {"policy": policy, "hold_seconds": 4,
                               "site_overrides": {}}, "test")
    db.commit()
    invalidate_cache()


def age_events():
    """Өмнөх кейсийн event-үүдийг burst/dedup цонхноос гаргана."""
    db.query(LprEvent).filter(LprEvent.site_id == site.id).update(
        {LprEvent.created_at: datetime.utcnow() - timedelta(seconds=120)})
    db.commit()


def commands():
    return (db.query(BarrierCommand)
            .filter(BarrierCommand.device_id == gate.id,
                    BarrierCommand.command == "open",
                    BarrierCommand.status == "SUCCESS").count())


async def run():
    print("PLATE_RE — дугаарын стандарт (docs/дугаарын стандарт):")
    check("энгийн 1234УБА зөв", is_valid_plate("1234УБА"))
    check("дипломат хуучин ДК0188 зөв", is_valid_plate("ДК0188"))
    check("дипломат шинэ 1302ДК зөв", is_valid_plate("1302ДК"))
    check("дипломат шинэ 9914АК зөв", is_valid_plate("9914АК"))
    check("тайрагдсан 1234УБ invalid хэвээр", not is_valid_plate("1234УБ"))
    check("цифр дутуу 132УБИ invalid", not is_valid_plate("132УБИ"))
    check("хог уншилт АБВГДЕ invalid", not is_valid_plate("АБВГДЕ"))

    # ─── policy=hold: junk уншилт хаалт нээхгүй, зөв уншилт нээнэ ───────────
    set_policy("hold")
    print("\npolicy=hold — буруу формат хаалт нээхгүй:")
    r = await handle_entry(db, cam, "132УБИ", 95.0, {})
    check("session үүссэн (action=entry)", r["action"] == "entry", r["action"])
    check("held=True", r.get("held") is True)
    check("хаалт нээгдээгүй", r["barrier_opened"] is False and commands() == 0,
          f"commands={commands()}")

    print("\nburst цонхонд ЗӨВ уншилт ирэхэд — autocorrect + хаалт нээгдэнэ:")
    r2 = await handle_entry(db, cam, "7132УБИ", 98.0, {})
    check("plate_autocorrect болов", r2["action"] == "plate_autocorrect", r2["action"])
    check("хаалт нээгдэв", r2["barrier_opened"] is True and commands() == 1,
          f"commands={commands()}")
    sid_fixed = r2["session_id"]

    print("\nautocorrect хийгдсэн session дээр hold дуусахад — юу ч хийхгүй:")
    await entry_hold_expire(sid_fixed, cam.id, 1, "hold")
    n_audit = (db.query(AuditLog).filter(AuditLog.action == "ENTRY_HOLD",
                                         AuditLog.entity_id == sid_fixed).count())
    check("ENTRY_HOLD audit ҮГҮЙ (зөв дугаар болсон)", n_audit == 0, str(n_audit))

    # ─── дедуп: барьж буй машины давтан junk нээхгүй ────────────────────────
    age_events()
    print("\ndedup — барьж буй машины ДАВТАН junk уншилт нээхгүй:")
    r3 = await handle_entry(db, cam, "555ХОВ", 95.0, {})
    check("эхний junk баригдав", r3.get("held") is True and r3["barrier_opened"] is False)
    base = commands()
    r4 = await handle_entry(db, cam, "555ХОВ", 95.0, {})
    check("давтан junk = dedup, мөн баригдсан хэвээр",
          r4["action"] == "dedup" and r4.get("held") is True, r4["action"])
    check("хаалт нээгдээгүй хэвээр", commands() == base, f"commands={commands()}")

    print("\nhold дуусахад (policy=hold) — fail-open нээнэ + ENTRY_HOLD audit:")
    await entry_hold_expire(r3["session_id"], cam.id, 1, "hold")
    a = (db.query(AuditLog).filter(AuditLog.action == "ENTRY_HOLD",
                                   AuditLog.entity_id == r3["session_id"]).first())
    check("ENTRY_HOLD audit үлдсэн", a is not None)
    check("opened=True (fail-open)", a and a.detail.get("opened") is True)

    # ─── strict: hold дуусахад ч нээхгүй ────────────────────────────────────
    age_events()
    set_policy("strict")
    print("\npolicy=strict — hold дуусахад ч нээхгүй:")
    r5 = await handle_entry(db, cam, "77АБВ", 95.0, {})
    check("junk баригдав", r5.get("held") is True and r5["barrier_opened"] is False)
    base = commands()
    await entry_hold_expire(r5["session_id"], cam.id, 1, "strict")
    a5 = (db.query(AuditLog).filter(AuditLog.action == "ENTRY_HOLD",
                                    AuditLog.entity_id == r5["session_id"]).first())
    check("шинэ open команд ЯВААГҮЙ", commands() == base, f"commands={commands()}")
    check("ENTRY_HOLD audit opened=False", a5 is not None and a5.detail.get("opened") is False)

    # ─── зөв уншилтын АРД ирсэн junk (давхар уншилт) — нээнэ ────────────────
    age_events()
    set_policy("hold")
    print("\nзөв уншилтын ард ирсэн junk (4627УКА→4627КД маягийн) — нээнэ:")
    r6 = await handle_entry(db, cam, "5678УВС", 99.0, {})
    check("зөв дугаар шууд нээв", r6["barrier_opened"] is True and r6.get("held") is False)
    r7 = await handle_entry(db, cam, "5678УВ", 90.0, {})
    check("junk = давхар уншилт гэж танив", r7["action"] == "dedup", r7["action"])
    check("энэ davхар уншилт held БИШ (зөв event нээчихсэн)", r7.get("held") is False)

    # ─── open: хуучин зан төлөв ─────────────────────────────────────────────
    age_events()
    set_policy("open")
    print("\npolicy=open — өмнөх зан төлөв (junk ч шууд нээнэ):")
    r8 = await handle_entry(db, cam, "999ГЭР", 95.0, {})
    check("held байхгүй, хаалт нээгдэв",
          r8.get("held") is False and r8["barrier_opened"] is True)


try:
    asyncio.run(run())
finally:
    # Глобал дүрмийг буцаана
    row = db.get(AppSetting, ENTRYPLATE_KEY)
    if row is not None:
        if _old_val is None:
            db.delete(row)
        else:
            row.value = _old_val
    db.commit()
    invalidate_cache()
    # Тестийн мөрүүдийг цэвэрлэнэ
    sids = [s.id for s in db.query(ParkingSession)
            .filter(ParkingSession.site_id == site.id).all()]
    db.query(LprEvent).filter(LprEvent.site_id == site.id).delete(synchronize_session=False)
    db.query(BarrierCommand).filter(
        BarrierCommand.device_id.in_([cam.id, gate.id])).delete(synchronize_session=False)
    if sids:
        db.query(AuditLog).filter(AuditLog.entity_id.in_(sids)).delete(synchronize_session=False)
        db.query(ParkingSession).filter(
            ParkingSession.id.in_(sids)).delete(synchronize_session=False)
    db.query(Device).filter(Device.id.in_([cam.id, gate.id])).delete(synchronize_session=False)
    db.query(ParkingSite).filter(ParkingSite.id == site.id).delete(synchronize_session=False)
    db.commit()
    db.close()

print(f"\n{'='*40}\n  PASS {PASS} / FAIL {FAIL}\n{'='*40}")
sys.exit(1 if FAIL else 0)
