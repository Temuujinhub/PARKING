"""Nested (дамжин) зогсоол — доторх зогсоолд байх хугацаагаар гадна тоолуур зогсоно.

    cd backend && venv/bin/python tests/test_nested_transit.py

Урсгал (Рашбулаг ЭТТ):
    .10 гадна орох → .12 дотор орох → [дотор: тоолуур зогсоно] → .13 дотор гарах
    → гадна талдаа 30 мин үнэгүй → .11 гадна гарах → 30-аас хэтэрсэн бол төлбөр
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
settings.transit_max_hours = 4

from app.billing import calculate_fee  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import (  # noqa: E402
    BarrierCommand, Device, LprEvent, ParkingSession, ParkingSite, TariffTemplate, TariffTier,
)
from app.services.nested import (  # noqa: E402
    effective_paused_minutes, inside_nested_count, on_inner_entry, on_inner_exit,
)
from app.session_logic import handle_entry, handle_exit, session_fee_info  # noqa: E402

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗ <<< FAIL'} {name}{'' if cond else f'  [{extra}]'}")


PLATE = "7777ДАМ"
PLATE2 = "7778ДАМ"
db = SessionLocal()
made: list = []


def mk_device(site, name, ip, lane_no, lane_dir, dtype="camera"):
    d = Device(id=str(uuid.uuid4()), site_id=site.id, name=name, device_type=dtype,
               ip_address=ip, lane_no=lane_no, lane_dir=lane_dir, status="active",
               auto_open=True, device_key=f"zz-{uuid.uuid4().hex[:10]}")
    db.add(d)
    db.flush()
    made.append(d)
    return d


try:
    # ─── Тариф: эхний 30 мин үнэгүй, 60 мин → 1000₮, дараа нь цаг тутам 1000₮ ───
    tpl = TariffTemplate(id=str(uuid.uuid4()), name="ZZ-дамжин", free_minutes=30,
                         grace_minutes=15, extra_hour_price=1000)
    db.add(tpl)
    db.flush()
    made.append(tpl)
    tier = TariffTier(id=str(uuid.uuid4()), template_id=tpl.id, upto_minutes=60, price=1000)
    db.add(tier)
    db.flush()
    made.append(tier)   # устгах нь `made`-ийн УРВУУ дараалал: tier → tpl (FK)

    outer = ParkingSite(id=str(uuid.uuid4()), name="ZZ-Гадна", site_code=f"ZZO{uuid.uuid4().hex[:6]}",
                        tariff_template_id=tpl.id, capacity=100)
    db.add(outer)
    db.flush()
    inner = ParkingSite(id=str(uuid.uuid4()), name="ZZ-Дотор", site_code=f"ZZI{uuid.uuid4().hex[:6]}",
                        parent_site_id=outer.id, no_charge=True, capacity=20)
    db.add(inner)
    db.flush()
    made += [outer, inner]   # урвуугаар: inner → outer (parent_site_id FK)

    cam_out_in = mk_device(outer, "Гадна орох", "10.90.90.10", 1, "entry")
    mk_device(outer, "Гадна орох хаалт", "", 1, "entry", "barrier")
    cam_out_ex = mk_device(outer, "Гадна гарах", "10.90.90.11", 2, "exit")
    mk_device(outer, "Гадна гарах хаалт", "", 2, "exit", "barrier")
    cam_in_in = mk_device(inner, "Дотор орох", "10.90.90.12", 1, "entry")
    mk_device(inner, "Дотор орох хаалт", "", 1, "entry", "barrier")
    cam_in_ex = mk_device(inner, "Дотор гарах", "10.90.90.13", 2, "exit")
    mk_device(inner, "Дотор гарах хаалт", "", 2, "exit", "barrier")
    db.commit()

    RAW = {"Picture": {"Plate": {"PlateNumber": PLATE}}}

    print("\n1. Тооцооллын цөм (billing) — дамжин минут хасагдана")
    t0 = datetime(2026, 8, 7, 10, 0)
    f = calculate_fee(tpl, t0, t0 + timedelta(minutes=200), paused_minutes=180)
    check("200 мин − 180 дамжин = 20 мин → үнэгүй (30-д багтав)",
          f["is_free"] and f["total_fee"] == 0, f)
    check("бодит хугацаа ХЭВЭЭР харагдана (тайланд)", f["duration_minutes"] == 200, f)
    check("хасагдсан минут буцаана", f["paused_minutes"] == 180, f)
    f2 = calculate_fee(tpl, t0, t0 + timedelta(minutes=200), paused_minutes=0)
    check("дамжингүй бол 200 мин → төлбөртэй", not f2["is_free"] and f2["total_fee"] > 0, f2)
    f3 = calculate_fee(tpl, t0, t0 + timedelta(minutes=200), paused_minutes=100)
    check("хэсэгчилсэн хасалт: 100 мин үлдэнэ → төлбөртэй",
          f3["chargeable_minutes"] == 100 and f3["total_fee"] > 0, f3)
    f4 = calculate_fee(tpl, t0, t0 + timedelta(minutes=50), paused_minutes=999)
    check("хасалт нийт хугацаанаас хэтрэхгүй", f4["paused_minutes"] == 50, f4)
    f5 = calculate_fee(tpl, t0, t0 + timedelta(minutes=500), no_charge=True)
    check("төлбөргүй зогсоол → 0₮", f5["is_free"] and f5["total_fee"] == 0, f5)

    print("\n2. Бодит урсгал: гадна орох → дотор орох/гарах → гадна гарах")
    r = asyncio.run(handle_entry(db, cam_out_in, PLATE, 0.95, RAW))
    s_out = db.get(ParkingSession, r["session_id"])
    check("гадна session нээгдэв", s_out is not None and s_out.status == "OPEN")
    # Гадна орсноос хойш 20 минут гадаа явсан гэж үзье
    s_out.entry_time = datetime.utcnow() - timedelta(minutes=20)
    db.commit()

    r2 = asyncio.run(handle_entry(db, cam_in_in, PLATE, 0.95, RAW))
    s_in = db.get(ParkingSession, r2["session_id"])
    db.refresh(s_out)
    check("дотор session ТУСДАА нээгдэв", s_in is not None and s_in.id != s_out.id)
    check("гадна тоолуур ЗОГССОН (paused_since тавигдав)", s_out.paused_since is not None)
    check("«дотор байгаа» тоо 1", inside_nested_count(db, outer.id) == 1)

    # Дотор 90 минут зогслоо
    s_out.paused_since = datetime.utcnow() - timedelta(minutes=90)
    s_in.entry_time = datetime.utcnow() - timedelta(minutes=90)
    s_out.entry_time = datetime.utcnow() - timedelta(minutes=110)
    db.commit()

    fee_in = session_fee_info(db, s_in)
    check("доторх зогсоол өөрөө төлбөр авахгүй",
          fee_in["is_free"] and fee_in["reason"] == "Төлбөргүй зогсоол", fee_in)

    asyncio.run(handle_exit(db, cam_in_ex, PLATE, 0.95, RAW))
    db.refresh(s_out)
    check("дотроос гармагц тоолуур үргэлжлэв (paused_since цэвэрлэгдэв)",
          s_out.paused_since is None)
    check("дотор өнгөрүүлсэн ~90 мин хуримтлагдав",
          88 <= s_out.paused_minutes <= 92, s_out.paused_minutes)
    check("«дотор байгаа» тоо 0 боллоо", inside_nested_count(db, outer.id) == 0)

    fee_out = session_fee_info(db, s_out)
    check("гадна төлбөр: 110 − 90 = 20 мин → үнэгүй (30-д багтав)",
          fee_out["is_free"], fee_out)
    check("гадна бодит хугацаа 110 мин хэвээр",
          108 <= fee_out["duration_minutes"] <= 112, fee_out)

    asyncio.run(handle_exit(db, cam_out_ex, PLATE, 0.95, RAW))
    db.refresh(s_out)
    check("гадна session үнэгүй хаагдав", s_out.status == "FREE" and not s_out.total_fee)

    print("\n3. Дотогш ОРООГҮЙ машин — энгийнээр төлбөртэй")
    # Session-ийг шууд үүсгэнэ: ижил камер дээр дараалан уншуулбал entry_burst
    # dedup залгидаг (нэг машины давтан уншилт гэж үзнэ) — энэ хэсэг нь төлбөрийн
    # логикийг шалгах тул тэр замыг тойрно.
    s2 = ParkingSession(id=str(uuid.uuid4()), site_id=outer.id, plate_number=PLATE2,
                        entry_time=datetime.utcnow() - timedelta(minutes=110),
                        entry_device_id=cam_out_in.id, status="OPEN")
    db.add(s2)
    db.commit()
    fee2 = session_fee_info(db, s2)
    check("110 мин, дамжингүй → төлбөртэй", not fee2["is_free"] and fee2["total_fee"] > 0, fee2)
    check("хасагдсан минут 0", fee2["paused_minutes"] == 0, fee2)

    print("\n4. Доторх ГАРАХ уншилт алдагдсан — дээд хязгаараар таслана")
    s2.paused_since = datetime.utcnow() - timedelta(hours=10)   # 10 цаг «дотор»
    s2.entry_time = datetime.utcnow() - timedelta(hours=11)
    db.commit()
    eff = effective_paused_minutes(db, s2, datetime.utcnow())
    check("10 цагийн зогсолт 4 цагаар (transit_max_hours) таслагдав",
          eff == 4 * 60, eff)
    fee3 = session_fee_info(db, s2)
    check("үлдсэн 7 цаг төлбөртэй хэвээр — 0₮ болоогүй",
          not fee3["is_free"] and fee3["total_fee"] > 0, fee3)

    print("\n5. Давхар уншилт / эцэг session байхгүй үе")
    s2.paused_since = None
    s2.paused_minutes = 0
    db.commit()
    check("нэг удаа зогсооно", on_inner_entry(db, inner, PLATE2, datetime.utcnow()) is True)
    check("давхар уншилт дахин эхлүүлэхгүй",
          on_inner_entry(db, inner, PLATE2, datetime.utcnow()) is False)
    db.refresh(s2)
    on_inner_exit(db, inner, PLATE2, datetime.utcnow())
    check("гадна бүртгэлгүй машинд юу ч хийхгүй",
          on_inner_entry(db, inner, "0000ХОО", datetime.utcnow()) is False)

finally:
    # Цэвэрлэгээ: FK дарааллаар (barrier_commands → sessions → devices → sites)
    db.rollback()
    sess_ids = [r[0] for r in db.query(ParkingSession.id)
                .filter(ParkingSession.plate_number.in_([PLATE, PLATE2])).all()]
    if sess_ids:
        db.query(BarrierCommand).filter(BarrierCommand.session_id.in_(sess_ids)).delete(
            synchronize_session=False)
    db.query(LprEvent).filter(LprEvent.plate_number.in_([PLATE, PLATE2])).delete(
        synchronize_session=False)
    db.query(ParkingSession).filter(ParkingSession.plate_number.in_([PLATE, PLATE2])).delete(
        synchronize_session=False)
    db.commit()
    dev_ids = [d.id for d in made if isinstance(d, Device)]
    if dev_ids:
        db.query(BarrierCommand).filter(BarrierCommand.device_id.in_(dev_ids)).delete(
            synchronize_session=False)
        db.query(LprEvent).filter(LprEvent.device_id.in_(dev_ids)).delete(
            synchronize_session=False)
        db.commit()
    for obj in reversed(made):
        try:
            db.delete(obj)
            db.commit()
        except Exception as e:  # noqa: BLE001
            db.rollback()
            print(f"  [cleanup] {type(obj).__name__} устгаж чадсангүй: {str(e)[:80]}")
    db.close()

print(f"\n{'='*54}\nPASS={PASS} FAIL={FAIL}")
sys.exit(1 if FAIL else 0)
