"""Давхар зогсоол НЭГ зогсоол дотор — `Device.nested_inner` камерууд.

    cd backend && venv/bin/python tests/test_nested_inner_lane.py

Рашбулаг ЭТТ: нэг талбай, дотроо жижиг зогсоол. Зогсоолыг хоёр болгож
салгахгүйгээр доторх орох/гарах камерыг «дотоод» гэж тэмдэглэнэ:

    .10 гадна орох  → зогсолт ЭХЭЛНЭ
    .12 дотор орох  → тоолуур ЗОГСОНО (зогсолт эхлэхгүй!)
    .13 дотор гарах → тоолуур ҮРГЭЛЖИЛНЭ (зогсолт дуусахгүй!)
    .11 гадна гарах → зогсолт ДУУСНА, (нийт − дотор) дээр төлбөр
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

from app.database import SessionLocal  # noqa: E402
from app.models import (  # noqa: E402
    BarrierCommand, Device, LprEvent, ParkingSession, ParkingSite, TariffTemplate, TariffTier,
)
from app.services.device_auto import ensure_lane_barriers  # noqa: E402
from app.session_logic import (  # noqa: E402
    _find_barrier, handle_entry, handle_exit, handle_inner_pass, session_fee_info,
)

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗ <<< FAIL'} {name}{'' if cond else f'  [{extra}]'}")


PLATE = "5151ДАВ"
db = SessionLocal()
made: list = []


def mk_cam(site, name, ip, lane_no, lane_dir, inner=False):
    d = Device(id=str(uuid.uuid4()), site_id=site.id, name=name, device_type="camera",
               ip_address=ip, lane_no=lane_no, lane_dir=lane_dir, status="active",
               auto_open=True, nested_inner=inner, device_key=f"zz-{uuid.uuid4().hex[:10]}")
    db.add(d)
    db.flush()
    made.append(d)
    return d


try:
    tpl = TariffTemplate(id=str(uuid.uuid4()), name="ZZ-давхар", free_minutes=30,
                         grace_minutes=15, extra_hour_price=1000)
    db.add(tpl)
    db.flush()
    made.append(tpl)
    tier = TariffTier(id=str(uuid.uuid4()), template_id=tpl.id, upto_minutes=60, price=1000)
    db.add(tier)
    db.flush()
    made.append(tier)

    site = ParkingSite(id=str(uuid.uuid4()), name="ZZ-Рашбулаг", site_code=f"ZR{uuid.uuid4().hex[:7]}",
                       tariff_template_id=tpl.id, capacity=100)
    db.add(site)
    db.flush()
    made.append(site)

    out_in = mk_cam(site, "Орох камер", "10.92.92.10", 1, "entry")
    out_ex = mk_cam(site, "Гарах камер", "10.92.92.11", 2, "exit")
    in_in = mk_cam(site, "Орох 2", "10.92.92.12", 3, "entry", inner=True)
    in_ex = mk_cam(site, "Гарах 2", "10.92.92.13", 4, "exit", inner=True)
    db.commit()

    print("\n1. Хаалт автоматаар үүсэх — дотоод нь БИЕ ДААСАН")
    ensure_lane_barriers(db)
    bars = db.query(Device).filter(Device.site_id == site.id, Device.device_type == "barrier",
                                   Device.status == "active").all()
    made += bars
    outer_b = [b for b in bars if not b.nested_inner]
    inner_b = [b for b in bars if b.nested_inner]
    check("гадна 2 хаалт үүсэв", len(outer_b) == 2, [b.name for b in outer_b])
    check("дотоод 2 хаалт ТУСДАА үүсэв", len(inner_b) == 2, [b.name for b in inner_b])
    check("дотоод хаалт нэрээрээ ялгарна",
          all("Дотор" in b.name for b in inner_b), [b.name for b in inner_b])
    check("доторх камер ДОТООД хаалтаа олно",
          (_find_barrier(db, site.id, in_in) or Device()).nested_inner is True)
    check("гаднах камер ГАДНА хаалтаа олно",
          (_find_barrier(db, site.id, out_in) or Device(nested_inner=True)).nested_inner is False)

    print("\n2. Урсгал: .10 орох → .12 дотор → .13 гарах → .11 гарах")
    RAW = {"Picture": {"Plate": {"PlateNumber": PLATE}}}
    r = asyncio.run(handle_entry(db, out_in, PLATE, 0.95, RAW))
    s = db.get(ParkingSession, r["session_id"])
    check("гадна орох → зогсолт эхлэв", s is not None and s.status == "OPEN")
    n_before = db.query(ParkingSession).filter(ParkingSession.site_id == site.id).count()

    r2 = asyncio.run(handle_inner_pass(db, in_in, PLATE, 0.95, RAW))
    db.refresh(s)
    n_after = db.query(ParkingSession).filter(ParkingSession.site_id == site.id).count()
    check("дотор орох → ШИНЭ зогсолт үүсээгүй", n_after == n_before, (n_before, n_after))
    check("дотор орох → тоолуур зогслоо", s.paused_since is not None)
    check("дотор орох → хаалт нээгдэв", r2["barrier_opened"] is True, r2)
    check("буцаах action = inner_entry", r2["action"] == "inner_entry", r2)

    # 90 минут дотор, нийт 110 минут
    s.paused_since = datetime.utcnow() - timedelta(minutes=90)
    s.entry_time = datetime.utcnow() - timedelta(minutes=110)
    db.commit()

    r3 = asyncio.run(handle_inner_pass(db, in_ex, PLATE, 0.95, RAW))
    db.refresh(s)
    check("дотор гарах → зогсолт ХААГДААГҮЙ", s.status == "OPEN", s.status)
    check("дотор гарах → тоолуур үргэлжлэв", s.paused_since is None)
    check("дотор өнгөрүүлсэн ~90 мин хуримтлагдав",
          88 <= s.paused_minutes <= 92, s.paused_minutes)
    check("дотор гарах → хаалт нээгдэв", r3["barrier_opened"] is True, r3)

    fee = session_fee_info(db, s)
    check("төлбөр: 110 − 90 = 20 мин → үнэгүй (30-д багтав)", fee["is_free"], fee)
    check("бодит хугацаа 110 мин хэвээр", 108 <= fee["duration_minutes"] <= 112, fee)

    asyncio.run(handle_exit(db, out_ex, PLATE, 0.95, RAW))
    db.refresh(s)
    check("гадна гарах → зогсолт үнэгүй хаагдав",
          s.status == "FREE" and not s.total_fee, (s.status, s.total_fee))

    print("\n3. Дотогш ОРООГҮЙ машин — бүтэн хугацаагаар төлбөртэй")
    s2 = ParkingSession(id=str(uuid.uuid4()), site_id=site.id, plate_number="5152ДАВ",
                        entry_time=datetime.utcnow() - timedelta(minutes=110),
                        entry_device_id=out_in.id, status="OPEN")
    db.add(s2)
    db.commit()
    fee2 = session_fee_info(db, s2)
    check("110 мин, дотогш ороогүй → төлбөртэй",
          not fee2["is_free"] and fee2["total_fee"] > 0, fee2)

    print("\n4. Гадна орох уншилтгүй машин — дотоод хаалт гацаахгүй, session нөхөгдөнө")
    r4 = asyncio.run(handle_inner_pass(db, in_in, "9999ХОО", 0.9,
                                       {"Picture": {"Plate": {"PlateNumber": "9999ХОО"}}}))
    check("зогсоолд бүртгэлгүй ч хаалт нээгдэнэ", r4["barrier_opened"] is True, r4)
    # Гадна орох камер уншиж чадаагүй машин дотоод камерт харагдвал session
    # НӨХӨЖ үүснэ (2026-08-11 Рашбулаг: дотор байсан 46 машины 15 нь session-гүй
    # «үл үзэгдэгч» байсныг засав) — тоолуур одооноос, дотогшоо бол шууд зогсоно.
    check("session нөхөж үүсэв", r4["session_id"] is not None, r4)
    s4 = db.get(ParkingSession, r4["session_id"])
    check("нөхсөн session дотор гэж тэмдэглэгдсэн", s4.paused_since is not None, r4)
    check("тоолуур зогссон гэж тоологдов", r4["counter_changed"] is True, r4)

    print("\n4б. «Автомат нээх» унтраалттай — ГАРАХ талыг гацаахгүй")
    # `auto_open` нь зөвхөн ОРОХ чиглэлд утгатай бөгөөд UI-д ч зөвхөн орох
    # камерт харагддаг. Дотоод ГАРАХ камерыг үүгээр хаавал админ асаах ч
    # аргагүй чагтын улмаас машин доторх зогсоолд гацна (production дээр
    # яг ийм зүйл болсон: Рашбулаг ЭТТ «Гарах 2», 2026-08-08).
    in_ex.auto_open = False
    in_in.auto_open = False
    db.commit()
    r4b = asyncio.run(handle_inner_pass(db, in_ex, "9999ХОО", 0.9,
                                        {"Picture": {"Plate": {"PlateNumber": "9999ХОО"}}}))
    check("дотоод ГАРАХ хаалт auto_open-оос үл хамааран нээгдэнэ",
          r4b["barrier_opened"] is True, r4b)
    r4c = asyncio.run(handle_inner_pass(db, in_in, "9999ХОО", 0.9,
                                        {"Picture": {"Plate": {"PlateNumber": "9999ХОО"}}}))
    check("дотоод ОРОХ хаалт auto_open унтарсан үед нээгдэхгүй",
          r4c["barrier_opened"] is False, r4c)
    in_ex.auto_open = True
    in_in.auto_open = True
    db.commit()

    print("\n5. Давхар уншилт — тоолуурыг дахин эхлүүлэхгүй")
    s2.paused_since = None
    db.commit()
    a = asyncio.run(handle_inner_pass(db, in_in, "5152ДАВ", 0.95, RAW))
    b = asyncio.run(handle_inner_pass(db, in_in, "5152ДАВ", 0.95, RAW))
    check("эхний уншилт тоолуурыг зогсоов", a["counter_changed"] is True, a)
    check("давхар уншилт дахин эхлүүлэхгүй", b["counter_changed"] is False, b)

finally:
    db.rollback()
    plates = [PLATE, "5152ДАВ", "9999ХОО"]
    sess_ids = [r[0] for r in db.query(ParkingSession.id)
                .filter(ParkingSession.plate_number.in_(plates)).all()]
    dev_ids = [d.id for d in made if isinstance(d, Device)]
    if sess_ids:
        db.query(BarrierCommand).filter(BarrierCommand.session_id.in_(sess_ids)).delete(
            synchronize_session=False)
    if dev_ids:
        db.query(BarrierCommand).filter(BarrierCommand.device_id.in_(dev_ids)).delete(
            synchronize_session=False)
        db.query(LprEvent).filter(LprEvent.device_id.in_(dev_ids)).delete(
            synchronize_session=False)
    db.query(LprEvent).filter(LprEvent.plate_number.in_(plates)).delete(synchronize_session=False)
    db.query(ParkingSession).filter(ParkingSession.plate_number.in_(plates)).delete(
        synchronize_session=False)
    db.commit()
    for obj in reversed(made):
        try:
            db.delete(obj)
            db.commit()
        except Exception as e:  # noqa: BLE001
            db.rollback()
            print(f"  [cleanup] {type(obj).__name__}: {str(e)[:70]}")
    db.close()

print(f"\n{'='*54}\nPASS={PASS} FAIL={FAIL}")
sys.exit(1 if FAIL else 0)
