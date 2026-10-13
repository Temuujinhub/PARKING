"""Тусдаа site-тай (parent/child) доторх зогсоол — UI тохиргоо backend-д хүрч байгаа эсэх.

    cd backend && venv/bin/python tests/test_nested_child_config.py

2026-10-13 аудитаар илэрсэн гурван цоорхойг барина:

  1. «Дотор хугацааг хасах хязгаар»-ыг ГАДНА (эцэг) зогсоол дээр тавихад backend
     зөвхөн ХҮҮХДИЙН утгыг уншдаг байсан → 100 айл эмнэлэгт 0 (= хязгааргүй)
     гэж тавьсан ч глобал 4 цагаар таслагдаж 33 машинд 368,000₮ нэхсэн.
  2. Эцэг зогсоолд импортолсон TRANSIT («Дамжин — доторх зогсоолын машин»)
     бүртгэл «site» хүрээтэй тул «Зөвхөн бүртгэлтэй» доторх зогсоолд танигддаггүй
     → Рашбулаг шороо дээр 262 бүртгэлтэй машинд автомат нээлт 0.
  3. «Зөвхөн бүртгэлтэй» доторх зогсоолд хаалт нээгдээгүй (татгалзсан) машины
     гадна тоолуур зогсдог байсан.
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
settings.transit_max_hours = 4          # глобал default = 240 мин

from app.database import SessionLocal  # noqa: E402
from app.models import (  # noqa: E402
    BarrierCommand, Device, LprEvent, ParkingSession, ParkingSite, RegisteredDriver,
)
from app.services.nested import (  # noqa: E402
    child_cap_minutes, on_inner_entry, on_inner_exit, pause_cap_minutes,
)
from app.session_logic import find_registered, handle_entry  # noqa: E402

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗ <<< FAIL'} {name}{'' if cond else f'  [{extra}]'}")


# Ялгаатай дугаар — орох камерын burst нэгтгэл (≤2 тэмдэгт) хооронд нь нийлүүлэхгүй
P_TRANSIT, P_CONTRACT, P_INNER, P_NONE = "5530ТРН", "7782ДЗТ", "3349ӨНМ", "9061ГЗХ"
PLATES = [P_TRANSIT, P_CONTRACT, P_INNER, P_NONE]
db = SessionLocal()
made: list = []


def mk_site(name, transit_hours=None, parent=None, **kw):
    s = ParkingSite(id=str(uuid.uuid4()), name=name, site_code=f"ZC{uuid.uuid4().hex[:7]}",
                    capacity=50, transit_max_hours=transit_hours,
                    parent_site_id=parent.id if parent else None, **kw)
    db.add(s)
    db.flush()
    made.append(s)
    return s


def mk_device(site, name, ip, lane_no, lane_dir, dtype="camera"):
    d = Device(id=str(uuid.uuid4()), site_id=site.id, name=name, device_type=dtype,
               ip_address=ip, lane_no=lane_no, lane_dir=lane_dir, status="active",
               auto_open=True, device_key=f"zz-{uuid.uuid4().hex[:10]}")
    db.add(d)
    db.flush()
    made.append(d)
    return d


def mk_driver(site, plate, contract_type, scope="site"):
    now = datetime.utcnow()
    r = RegisteredDriver(id=str(uuid.uuid4()), plate_number=plate, site_id=site.id,
                         contract_type=contract_type, access_scope=scope, is_active=True,
                         valid_from=now - timedelta(days=1), valid_to=now + timedelta(days=30))
    db.add(r)
    db.flush()
    made.append(r)
    return r


try:
    print("\n1. Хасах хязгаар — хүүхэд хоосон бол ЭЦГИЙН тохиргоо")
    p0 = mk_site("ZZ-Эцэг-0", 0)
    k0 = mk_site("ZZ-Хүүхэд-хоосон", None, p0, no_charge=True)
    p2 = mk_site("ZZ-Эцэг-2", 2)
    k2 = mk_site("ZZ-Хүүхэд-хоосон-2", None, p2, no_charge=True)
    p3 = mk_site("ZZ-Эцэг-3", 3)
    k1 = mk_site("ZZ-Хүүхэд-1", 1, p3, no_charge=True)
    pn = mk_site("ZZ-Эцэг-хоосон", None)
    kn = mk_site("ZZ-Хүүхэд-хоосон-3", None, pn, no_charge=True)
    db.commit()
    check("эцэг 0, хүүхэд хоосон → хязгааргүй (0)", child_cap_minutes(db, k0) == 0,
          child_cap_minutes(db, k0))
    check("гадна гарцын хязгаар ч мөн 0", pause_cap_minutes(db, p0.id) == 0,
          pause_cap_minutes(db, p0.id))
    check("эцэг 2ц, хүүхэд хоосон → 120 мин", child_cap_minutes(db, k2) == 120,
          child_cap_minutes(db, k2))
    check("хүүхэд дээр тавьсан нь давуу (1ц > эцгийн 3ц)", child_cap_minutes(db, k1) == 60,
          child_cap_minutes(db, k1))
    check("хоёулаа хоосон → глобал 240", child_cap_minutes(db, kn) == 240,
          child_cap_minutes(db, kn))

    # Бодит урсгал: 10 цаг дотор — эцэг 0 бол бүтэн 600 мин хасагдана
    t0 = datetime.utcnow() - timedelta(hours=11)
    s = ParkingSession(id=str(uuid.uuid4()), site_id=p0.id, plate_number=P_NONE,
                       entry_time=t0, status="OPEN", paused_minutes=0)
    db.add(s)
    db.flush()
    on_inner_entry(db, k0, P_NONE, t0 + timedelta(minutes=5))
    added = on_inner_exit(db, k0, P_NONE, t0 + timedelta(minutes=605))
    check("10 цаг дотор → 600 мин хасагдсан (өмнө нь 240)", added == 600 and s.paused_minutes == 600,
          (added, s.paused_minutes))
    db.rollback()

    print("\n2. Эцэг зогсоолын TRANSIT бүртгэл доторх зогсоолд хүчинтэй")
    outer = mk_site("ZZ-Гадна")
    inner = mk_site("ZZ-Дотор-хаалттай", None, outer, no_charge=True, registered_only=True)
    mk_driver(outer, P_TRANSIT, "TRANSIT", "site")
    mk_driver(outer, P_CONTRACT, "CONTRACT", "site")
    mk_driver(outer, P_INNER, "CONTRACT", "inner")
    db.commit()
    check("TRANSIT (site) → доторх зогсоолд гэрээт", find_registered(db, P_TRANSIT, inner.id) is not None)
    check("TRANSIT → гадна зогсоолд ч гэрээт (хуучин зан)", find_registered(db, P_TRANSIT, outer.id) is not None)
    check("энгийн CONTRACT (site) → доторх зогсоолд ОРОХГҮЙ (өөрчлөгдөөгүй)",
          find_registered(db, P_CONTRACT, inner.id) is None)
    check("inner хүрээтэй → доторх зогсоолд гэрээт (өөрчлөгдөөгүй)",
          find_registered(db, P_INNER, inner.id) is not None)
    check("inner хүрээтэй → гадна зогсоолд гэрээт БИШ (өөрчлөгдөөгүй)",
          find_registered(db, P_INNER, outer.id) is None)

    print("\n3. Хаалттай доторх зогсоол — татгалзсан машины гадна тоолуур зогсохгүй")
    cam_out = mk_device(outer, "Гадна орох", "10.91.91.10", 1, "entry")
    mk_device(outer, "Гадна орох хаалт", "", 1, "entry", "barrier")
    cam_in = mk_device(inner, "Дотор орох", "10.91.91.12", 1, "entry")
    mk_device(inner, "Дотор орох хаалт", "", 1, "entry", "barrier")
    db.commit()
    for plate in (P_NONE, P_TRANSIT):
        raw = {"Picture": {"Plate": {"PlateNumber": plate}}}
        asyncio.run(handle_entry(db, cam_out, plate, 0.95, raw))
        asyncio.run(handle_entry(db, cam_in, plate, 0.95, raw))
    out_none = (db.query(ParkingSession).filter_by(site_id=outer.id, plate_number=P_NONE)
                .order_by(ParkingSession.entry_time.desc()).first())
    out_tr = (db.query(ParkingSession).filter_by(site_id=outer.id, plate_number=P_TRANSIT)
              .order_by(ParkingSession.entry_time.desc()).first())
    db.refresh(out_none)
    db.refresh(out_tr)
    check("бүртгэлгүй (хаалт нээгдээгүй) → гадна тоолуур ЯВСААР", out_none.paused_since is None,
          out_none.paused_since)
    check("TRANSIT → гадна тоолуур зогссон", out_tr.paused_since is not None, out_tr.paused_since)
    opened = (db.query(BarrierCommand).join(Device, Device.id == BarrierCommand.device_id)
              .filter(Device.site_id == inner.id, BarrierCommand.command == "open").count())
    check("доторх орох хаалт зөвхөн TRANSIT-д нээгдсэн (1 команд)", opened == 1, opened)
finally:
    db.rollback()
    sess_ids = [r[0] for r in db.query(ParkingSession.id)
                .filter(ParkingSession.plate_number.in_(PLATES)).all()]
    if sess_ids:
        db.query(BarrierCommand).filter(BarrierCommand.session_id.in_(sess_ids)).delete(
            synchronize_session=False)
    db.query(LprEvent).filter(LprEvent.plate_number.in_(PLATES)).delete(synchronize_session=False)
    db.query(ParkingSession).filter(ParkingSession.plate_number.in_(PLATES)).delete(
        synchronize_session=False)
    db.commit()
    dev_ids = [d.id for d in made if isinstance(d, Device)]
    if dev_ids:
        db.query(BarrierCommand).filter(BarrierCommand.device_id.in_(dev_ids)).delete(
            synchronize_session=False)
        db.query(LprEvent).filter(LprEvent.device_id.in_(dev_ids)).delete(synchronize_session=False)
        db.commit()
    for obj in reversed(made):
        try:
            db.delete(obj)
            db.commit()
        except Exception as e:  # noqa: BLE001
            db.rollback()
            print(f"  (цэвэрлэгээ алгасав: {e.__class__.__name__})")
    db.close()

print(f"\n{PASS} ✓ / {FAIL} ✗")
sys.exit(1 if FAIL else 0)
