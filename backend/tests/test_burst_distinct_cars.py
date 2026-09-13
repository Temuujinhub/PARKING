"""Burst цонхонд дараалан орсон ӨӨР машинууд нэгтгэгдэхгүй + буруу авто сэргээлт.

    cd backend && venv/bin/python tests/test_burst_distinct_cars.py

Маршил 2026-09-13: хаалт онгорхой байхад машин 3–5 секунд тутам орж, burst
нэгтгэл дараагийн машины дугаарыг өмнөх машины сешн дээр дарж бичсэн
(1588УБВ→9361УКА→1353УЕС→…). Сешнгүй үлдсэн машин гарцад «бүртгэлгүй» болж,
2 хоногийн өмнөх (гарах уншилттай, unpaid_exit-ээр хаагдсан) сешн рүү авто
сэргээгдэж 50,000₮ нэхэгдэв.

Шалгах зүйл:
  1. Нэг камерт 4с зайтай 3 ОГТ ӨӨР дугаар → 3 тусдаа сешн (autocorrect биш)
  2. ≤2 тэмдэгтийн зөрүүтэй уншилт → нэг машин, autocorrect хэвээр
  3. junk → зөв дугаар → autocorrect хэвээр (hold policy)
  4. Гарах камерт уншигдаад AWAITING-аас авто хаагдсан сешн (exit_device_id
     байгаа) → auto_reopen_for_exit СЭРГЭЭХГҮЙ
  5. Гарах уншилтгүй (OPEN→stale) хаагдсан сешн → сэргээнэ (хуучин зан)
  6. close_session_forced AWAITING+exit_device_id → exit_confirmed=True
"""
import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings  # noqa: E402

settings.barrier_mock = True
settings.snapshot_enabled = False
settings.screen_enabled = False

from app.database import SessionLocal  # noqa: E402
from app.models import (  # noqa: E402
    AuditLog, BarrierCommand, Compensation, Device, LprEvent, ParkingSession, ParkingSite,
    TariffTemplate, TariffTier,
)
from app.services.device_auto import ensure_lane_barriers  # noqa: E402
from app.session_logic import (  # noqa: E402
    auto_reopen_for_exit, close_session_forced, handle_entry, plates_burst_similar,
)

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗ <<< FAIL'} {name}{'' if cond else f'  [{extra}]'}")


A, B, C = "9361ХКА", "1353ХЕС", "3186ХНИ"          # огт өөр 3 машин
V1, V2 = "1310ХЭН", "7370ХЭН"                       # 2 тэмдэгтийн зөрүү = нэг машин
J, JV = "1101ЭН", "2244ХАБ"                         # junk → зөв
R1, R2 = "5555ХРА", "6666ХРБ"                       # сэргээлтийн тест
PLATES = [A, B, C, V1, V2, J, JV, R1, R2]
db = SessionLocal()
made: list = []


def raw(p):
    return {"Picture": {"Plate": {"PlateNumber": p}}}


def opens(site_id, plates):
    return (db.query(ParkingSession)
            .filter(ParkingSession.site_id == site_id, ParkingSession.plate_number.in_(plates),
                    ParkingSession.status.in_(["OPEN", "AWAITING_PAYMENT", "PAID"])).all())


try:
    tpl = TariffTemplate(id=str(uuid.uuid4()), name="ZZ-burst", free_minutes=30,
                         grace_minutes=15, extra_hour_price=1000, daily_cap=25000)
    db.add(tpl); db.flush(); made.append(tpl)
    tier = TariffTier(id=str(uuid.uuid4()), template_id=tpl.id, upto_minutes=60, price=1000)
    db.add(tier); db.flush(); made.append(tier)
    site = ParkingSite(id=str(uuid.uuid4()), name="ZZ-burst", site_code=f"ZB{uuid.uuid4().hex[:7]}",
                       tariff_template_id=tpl.id, capacity=100)
    db.add(site); db.flush(); made.append(site)
    cam_in = Device(id=str(uuid.uuid4()), site_id=site.id, name="ZZ орох", device_type="camera",
                    ip_address="10.255.8.1", lane_no=1, lane_dir="entry", status="active",
                    auto_open=True, device_key=f"zz-{uuid.uuid4().hex[:10]}")
    cam_out = Device(id=str(uuid.uuid4()), site_id=site.id, name="ZZ гарах", device_type="camera",
                     ip_address="10.255.8.2", lane_no=1, lane_dir="exit", status="active",
                     auto_open=True, device_key=f"zz-{uuid.uuid4().hex[:10]}")
    db.add_all([cam_in, cam_out]); db.flush(); made += [cam_in, cam_out]
    db.commit()
    ensure_lane_barriers(db)
    db.commit()

    print("\n0. plates_burst_similar")
    check("огт өөр → False", not plates_burst_similar(A, B))
    check("2 зөрүү → True", plates_burst_similar(V1, V2))
    check("junk → зөв → True", plates_burst_similar(J, JV))
    check("тайрагдсан → True", plates_burst_similar("7524УБТ", "524УБТ"))

    print("\n3. junk → зөв → autocorrect хэвээр (эхэнд: 30с dedup-ийн нөлөөгүй)")
    r1 = asyncio.run(handle_entry(db, cam_in, J, 0.9, raw(J)))
    r2 = asyncio.run(handle_entry(db, cam_in, JV, 0.9, raw(JV)))
    db.expire_all()
    check("junk-ийн дараах зөв уншилт autocorrect", r2.get("action") == "plate_autocorrect", r2)

    print("\n1. Нэг камерт 4с зайтай 3 огт өөр дугаар → 3 тусдаа сешн")
    r = [asyncio.run(handle_entry(db, cam_in, p, 0.9, raw(p))) for p in (A, B, C)]
    db.expire_all()
    check("3 уншилт бүгд «entry» (autocorrect биш)", all(x.get("action") == "entry" for x in r),
          [x.get("action") for x in r])
    live = opens(site.id, [A, B, C])
    check("3 нээлттэй сешн, дугаарууд хэвээр", len(live) == 3 and {s.plate_number for s in live} == {A, B, C},
          [s.plate_number for s in live])

    print("\n2. ≤2 тэмдэгтийн зөрүү → нэг машин (autocorrect хэвээр)")
    r1 = asyncio.run(handle_entry(db, cam_in, V1, 0.9, raw(V1)))
    r2 = asyncio.run(handle_entry(db, cam_in, V2, 0.9, raw(V2)))
    db.expire_all()
    check("2-р уншилт autocorrect", r2.get("action") == "plate_autocorrect" and r2.get("new") == V2, r2)
    check("нээлттэй сешн 1", len(opens(site.id, [V1, V2])) == 1)

    print("\n4. Гарцад уншигдаад төлөлгүй хаагдсан сешн → сэргээхгүй")
    now = datetime.utcnow()
    s1 = ParkingSession(id=str(uuid.uuid4()), site_id=site.id, plate_number=R1,
                        entry_time=now - timedelta(hours=30), entry_device_id=cam_in.id,
                        status="AWAITING_PAYMENT", exit_device_id=cam_out.id,
                        updated_at=now - timedelta(hours=29))
    db.add(s1); db.flush()
    debt = close_session_forced(db, s1, "unpaid_exit", "system", True)
    db.commit(); db.expire_all()
    s1 = db.get(ParkingSession, s1.id)
    check("6. хаагдахад exit_confirmed=True, өр үүсэв", s1.exit_confirmed is True and debt > 0
          and s1.status == "MANUAL_CLOSED", f"{s1.exit_confirmed} {debt} {s1.status}")
    s1.exit_time = now - timedelta(hours=3)          # 12ц цонхонд багтаана
    s1.exit_confirmed = False                        # хуучин өгөгдөл шиг (засвараас өмнөх)
    db.commit()
    got = auto_reopen_for_exit(db, R1, site.id)
    db.rollback()
    check("exit_device_id-тай хаагдсан сешн сэргээгдээгүй", got is None, got.id if got else None)

    print("\n5. Гарах уншилтгүй (OPEN→stale) хаагдсан сешн → сэргээнэ (хуучин зан)")
    s2 = ParkingSession(id=str(uuid.uuid4()), site_id=site.id, plate_number=R2,
                        entry_time=now - timedelta(hours=14), entry_device_id=cam_in.id,
                        status="MANUAL_CLOSED", exit_time=now - timedelta(hours=2),
                        exit_confirmed=False, total_fee=0, base_fee=0, vat_amount=0)
    db.add(s2); db.commit()
    got = auto_reopen_for_exit(db, R2, site.id)
    check("сэргээв (OPEN)", got is not None and got.status == "OPEN", got)
    db.commit()
    s3 = db.get(ParkingSession, s2.id)
    s3.status = "MANUAL_CLOSED"; s3.exit_time = now - timedelta(hours=13)   # 12ц цонхноос гадуур
    db.commit()
    check("12ц-аас хуучин хаалт сэргээгдэхгүй", auto_reopen_for_exit(db, R2, site.id) is None)
    db.rollback()

finally:
    db.rollback()
    sess_ids = [r[0] for r in db.query(ParkingSession.id)
                .filter(ParkingSession.plate_number.in_(PLATES)).all()]
    dev_ids = [d.id for d in made if isinstance(d, Device)]
    if sess_ids:
        db.query(BarrierCommand).filter(BarrierCommand.session_id.in_(sess_ids)).delete(synchronize_session=False)
        db.query(Compensation).filter(Compensation.session_id.in_(sess_ids)).delete(synchronize_session=False)
        db.query(AuditLog).filter(AuditLog.entity_id.in_(sess_ids)).delete(synchronize_session=False)
    site_devs = [d.id for d in db.query(Device.id).filter(Device.site_id == site.id).all()]
    if site_devs:
        db.query(BarrierCommand).filter(BarrierCommand.device_id.in_(site_devs)).delete(synchronize_session=False)
        db.query(LprEvent).filter(LprEvent.device_id.in_(site_devs)).delete(synchronize_session=False)
    db.query(LprEvent).filter(LprEvent.plate_number.in_(PLATES)).delete(synchronize_session=False)
    db.query(ParkingSession).filter(ParkingSession.plate_number.in_(PLATES)).delete(synchronize_session=False)
    db.query(Device).filter(Device.site_id == site.id).delete(synchronize_session=False)
    db.commit()
    for obj in reversed(made):
        if isinstance(obj, Device):
            continue
        try:
            db.delete(obj); db.commit()
        except Exception as e:  # noqa: BLE001
            db.rollback(); print(f"  (цэвэрлэгээ: {e})")
    db.close()

print(f"\n{PASS} ✓  {FAIL} ✗")
sys.exit(1 if FAIL else 0)
