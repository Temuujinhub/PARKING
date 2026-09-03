"""Зогсоол бүрийн төлбөрийн дүрэм — амьд урсгал дээрх E2E (локал DB).

    cd backend && venv/bin/python tests/test_site_payment_rules.py

2026-09-03: төлбөр/хаалтны дүрэм .env + app_settings + зогсоолын багана гэж
тархсанаас «нэг зогсоолд тохирсон утга нөгөөг нь гацаадаг» асуудал үүсдэг байв.
Шалгах зүйлс:
  • `_sites` давхарга ЗӨВХӨН тухайн зогсоолд үйлчилнэ, бусад нь глобалаараа
  • ⭑ `min_stay_seconds`: орж ирээд эрт гарах уншилтад хаалт НЭЭГДЭХГҮЙ,
    бүртгэл нь OPEN хэвээр үлдэнэ («хуурамч гарц»-ын эсрэг)
  • босго өнгөрсний дараа ердийн урсгал үргэлжилнэ
  • гэрээт машинд min_stay үйлчлэхгүй
  • `exit_dedup_reopen=false` үед давхар уншилт хаалтыг дахин нээхгүй
  • `no_session_fee` зогсоолын давхаргаар өөрчлөгдөнө
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
from app.models import (AppSetting, AuditLog, BarrierCommand, Device,  # noqa: E402
                        LprEvent, ParkingSession, ParkingSite, RegisteredDriver,
                        TariffTemplate, TariffTier)
from app.services.app_settings import (BARRIER_KEY, EXITRULES_KEY,  # noqa: E402
                                       get_rules, invalidate_cache,
                                       no_session_exit_fee, set_site_rules)
from app.session_logic import handle_entry, handle_exit  # noqa: E402

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗ <<< FAIL'} {name}{f' — {extra}' if extra and not cond else ''}")


db = SessionLocal()
tag = uuid.uuid4().hex[:6]

tmpl = TariffTemplate(name=f"ZZ-Rules-{tag}", free_minutes=0, grace_minutes=15,
                      extra_hour_price=1000)
db.add(tmpl)
db.flush()
db.add(TariffTier(template_id=tmpl.id, upto_minutes=60, price=1000))
db.flush()

# ХОЁР зогсоол — давхарга зөвхөн НЭГД нь үйлчлэхийг батлах
site = ParkingSite(name=f"ZZ-Rules-{tag}", site_code=f"ZZR{tag}", is_active=True,
                   tariff_template_id=tmpl.id)
other = ParkingSite(name=f"ZZ-Other-{tag}", site_code=f"ZZO{tag}", is_active=True,
                    tariff_template_id=tmpl.id)
db.add_all([site, other])
db.flush()
cam_in = Device(site_id=site.id, name="орох камер", device_type="camera",
                lane_dir="entry", device_key=f"RIN{tag}", status="active", auto_open=True)
bar_in = Device(site_id=site.id, name="орох хаалт", device_type="barrier",
                lane_dir="entry", device_key=f"RBI{tag}", status="active")
cam_out = Device(site_id=site.id, name="гарах камер", device_type="camera",
                 lane_dir="exit", device_key=f"ROU{tag}", status="active", auto_open=True)
bar_out = Device(site_id=site.id, name="гарах хаалт", device_type="barrier",
                 lane_dir="exit", device_key=f"RBO{tag}", status="active")
db.add_all([cam_in, bar_in, cam_out, bar_out])
db.commit()
DEVS = [cam_in, bar_in, cam_out, bar_out]

_old = {k: (db.get(AppSetting, k).value if db.get(AppSetting, k) else None)
        for k in (EXITRULES_KEY, BARRIER_KEY)}


def sess(plate):
    return (db.query(ParkingSession)
            .filter(ParkingSession.site_id == site.id,
                    ParkingSession.plate_number == plate)
            .order_by(ParkingSession.entry_time.desc()).first())


def wipe(plate):
    for s in db.query(ParkingSession).filter(ParkingSession.plate_number == plate).all():
        db.query(AuditLog).filter(AuditLog.entity_id == s.id).delete(synchronize_session=False)
        db.delete(s)
    db.query(LprEvent).filter(LprEvent.plate_number == plate).delete(synchronize_session=False)
    db.commit()


def clear_events():
    """Уншилтын түүхийг цэвэрлэнэ — эс бол дараагийн тестийн орох уншилт
    өмнөхтэйгээ burst/dedup цонхонд нийлж шинэ бүртгэл үүсэхгүй."""
    db.query(LprEvent).filter(LprEvent.site_id == site.id).delete(synchronize_session=False)
    db.commit()


def clear_cmds():
    db.query(BarrierCommand).filter(
        BarrierCommand.device_id.in_([d.id for d in DEVS])).delete(synchronize_session=False)
    db.commit()


async def run():
    # ─── 1. Давхарга ЗӨВХӨН тухайн зогсоолд ────────────────────────────────
    print("Зогсоолын давхаргын хамрах хүрээ:")
    set_site_rules(db, EXITRULES_KEY, site.id, {"no_session_fee": 7500}, "test")
    db.commit()
    invalidate_cache()
    check("энэ зогсоолд 7500₮", no_session_exit_fee(db, site.id) == 7500,
          str(no_session_exit_fee(db, site.id)))
    check("нөгөө зогсоолд глобал хэвээр",
          no_session_exit_fee(db, other.id) == get_rules(db, EXITRULES_KEY)["no_session_fee"])
    set_site_rules(db, EXITRULES_KEY, site.id, {"no_session_fee": None}, "test")
    db.commit()
    invalidate_cache()
    check("буцаасны дараа глобал руу орлоо",
          no_session_exit_fee(db, site.id) == get_rules(db, EXITRULES_KEY)["no_session_fee"])

    # ─── 2. min_stay_seconds — эрт гарахад хаалт нээхгүй ───────────────────
    print("\nЭрт гарах хамгаалалт (min_stay_seconds=300):")
    set_site_rules(db, EXITRULES_KEY, site.id, {"min_stay_seconds": 300}, "test")
    db.commit()
    invalidate_cache()
    plate = "1111УБА"
    wipe(plate)
    clear_events()
    await handle_entry(db, cam_in, plate, 95.0, {})
    clear_cmds()
    r = await handle_exit(db, cam_out, plate, 95.0, {})
    check("гарах уншилт too_soon болов", r["action"] == "too_soon", str(r))
    opened = db.query(BarrierCommand).filter(BarrierCommand.device_id == bar_out.id,
                                             BarrierCommand.status == "SUCCESS").count()
    check("гарах хаалт НЭЭГДСЭНГҮЙ", opened == 0, f"{opened} команд")
    db.expire_all()
    s = sess(plate)
    check("бүртгэл OPEN хэвээр (үнэгүй хаагдаагүй)", s is not None and s.status == "OPEN",
          s.status if s else "алга")
    check("гарах төхөөрөмж тэмдэглэгдээгүй", s is not None and s.exit_device_id is None)

    # Босго өнгөрсний дараа ердийн урсгал
    print("\nБосго өнгөрсний дараа:")
    s.entry_time = datetime.utcnow() - timedelta(minutes=30)
    db.commit()
    invalidate_cache()
    r2 = await handle_exit(db, cam_out, plate, 95.0, {})
    check("ердийн урсгал үргэлжлэв (too_soon БИШ)", r2["action"] != "too_soon", str(r2))

    # ─── 3. Гэрээт машинд min_stay үйлчлэхгүй ──────────────────────────────
    print("\nГэрээт машин (min_stay үйлчлэхгүй):")
    reg_plate = "2222УБА"
    wipe(reg_plate)
    clear_events()
    drv = RegisteredDriver(site_id=site.id, plate_number=reg_plate, full_name="Тест",
                           valid_from=datetime.utcnow() - timedelta(days=1),
                           valid_to=datetime.utcnow() + timedelta(days=30), is_active=True)
    db.add(drv)
    db.commit()
    await handle_entry(db, cam_in, reg_plate, 95.0, {})
    clear_cmds()
    r3 = await handle_exit(db, cam_out, reg_plate, 95.0, {})
    check("гэрээт машин эрт ч гарлаа", r3["action"] != "too_soon", str(r3))
    db.query(RegisteredDriver).filter(RegisteredDriver.id == drv.id).delete(
        synchronize_session=False)
    db.commit()

    set_site_rules(db, EXITRULES_KEY, site.id, {"min_stay_seconds": None}, "test")
    db.commit()
    invalidate_cache()

    # ─── 4. exit_dedup_reopen ──────────────────────────────────────────────
    print("\nДавхар уншилт дээр гарах хаалтын дахин нээлт:")
    plate2 = "3333УБА"
    wipe(plate2)
    clear_events()
    await handle_entry(db, cam_in, plate2, 95.0, {})
    s2 = sess(plate2)
    s2.entry_time = datetime.utcnow() - timedelta(minutes=5)
    s2.is_registered = False
    db.commit()
    await handle_exit(db, cam_out, plate2, 95.0, {})     # → AWAITING/FREE
    # Дүрмийг унтраагаад давхар уншилт явуулна
    set_site_rules(db, BARRIER_KEY, site.id, {"exit_dedup_reopen": False}, "test")
    db.commit()
    invalidate_cache()
    clear_cmds()
    r4 = await handle_exit(db, cam_out, plate2, 95.0, {})
    check("давхар уншилт гэж танигдав", r4["action"] == "dedup", str(r4))
    check("унтраалттай үед хаалт нээгдээгүй", r4["barrier_opened"] is False)
    skipped = db.query(BarrierCommand).filter(
        BarrierCommand.device_id == bar_out.id,
        BarrierCommand.status == "SKIPPED").count()
    check("SKIPPED мөр үлдэж оношлогдоно", skipped >= 1, f"{skipped} мөр")

    # ─── 5. Зөрчлийн шалгалт ажиллаж байгаа эсэх ───────────────────────────
    print("\nЗөрчлийн шалгалт:")
    from app.services import payment_rules as PR
    titles = {c["title"] for c in PR.check_conflicts(db, site)}
    check("«exit_dedup_reopen унтраалттай» зөрчил илэрлээ",
          any("дахин нээгдэхгүй" in t for t in titles), str(titles))
    set_site_rules(db, BARRIER_KEY, site.id, {"exit_dedup_reopen": None}, "test")
    db.commit()
    invalidate_cache()

    tmpl.grace_minutes = 0
    db.commit()
    titles = {c["title"] for c in PR.check_conflicts(db, site)}
    check("grace=0 нь HIGH зөрчил болж илэрлээ",
          any("0 минут" in t for t in titles), str(titles))
    tmpl.grace_minutes = 15
    db.commit()

    wipe(plate)
    wipe(plate2)


try:
    asyncio.run(run())
finally:
    for key, val in _old.items():
        row = db.get(AppSetting, key)
        if row is not None:
            if val is None:
                db.delete(row)
            else:
                row.value = val
    db.commit()
    invalidate_cache()
    sids = [s.id for s in db.query(ParkingSession)
            .filter(ParkingSession.site_id.in_([site.id, other.id])).all()]
    db.query(LprEvent).filter(LprEvent.site_id.in_([site.id, other.id])).delete(
        synchronize_session=False)
    db.query(BarrierCommand).filter(
        BarrierCommand.device_id.in_([d.id for d in DEVS])).delete(synchronize_session=False)
    if sids:
        db.query(AuditLog).filter(AuditLog.entity_id.in_(sids)).delete(synchronize_session=False)
        db.query(ParkingSession).filter(ParkingSession.id.in_(sids)).delete(
            synchronize_session=False)
    db.query(RegisteredDriver).filter(RegisteredDriver.site_id == site.id).delete(
        synchronize_session=False)
    db.query(Device).filter(Device.id.in_([d.id for d in DEVS])).delete(synchronize_session=False)
    db.query(ParkingSite).filter(ParkingSite.id.in_([site.id, other.id])).delete(
        synchronize_session=False)
    db.query(TariffTier).filter(TariffTier.template_id == tmpl.id).delete(
        synchronize_session=False)
    db.query(TariffTemplate).filter(TariffTemplate.id == tmpl.id).delete(
        synchronize_session=False)
    db.commit()
    db.close()

print(f"\n{'='*40}\n  PASS {PASS} / FAIL {FAIL}\n{'='*40}")
sys.exit(1 if FAIL else 0)
