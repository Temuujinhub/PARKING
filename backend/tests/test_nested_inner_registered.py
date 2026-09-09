"""Давхар зогсоолын ДОТООД хаалт ХААЛТТАЙ — зөвхөн «дотоод» хүрээтэй бүртгэлд нээгдэнэ.

    cd backend && venv/bin/python tests/test_nested_inner_registered.py

Рашбулаг ЭТТ (2026-09-09): нэг зогсоол, дотроо жижиг (ажилчдын) зогсоол.
`site.inner_registered_only=true` үед:

    доторх ОРОХ камер + access_scope inner/both бүртгэл → тоолуур зогсоно, хаалт нээгдэнэ
    доторх ОРОХ камер + бүртгэлгүй / зөвхөн «site» гэрээт  → ТАТГАЛЗАНА (хаалт, тоолуур хөндөхгүй)
    доторх ГАРАХ камер                                     → ямагт нээгдэнэ (дотор гацаахгүй)
    гадна хаалтууд                                          → өөрчлөлтгүй

Мөн «inner» хүрээтэй бүртгэл гадна талбайд ГЭРЭЭТ гэж тоологдохгүй (төлбөртэй),
«both» бол тоологдоно; parent/child загварт хүүхэд зогсоол дээр эцгийн inner
бүртгэл гэрээт гэж тоологдоно.
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
    BarrierCommand, Device, LprEvent, ParkingSession, ParkingSite, RegisteredDriver,
    TariffTemplate, TariffTier,
)
from app.services.device_auto import ensure_lane_barriers  # noqa: E402
from app.session_logic import (  # noqa: E402
    find_inner_registered, find_registered, handle_entry, handle_inner_pass,
)

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗ <<< FAIL'} {name}{'' if cond else f'  [{extra}]'}")


INNER = "5161ДАВ"     # access_scope=inner (дугаарууд OCR-ойролцоо БИШ байх ёстой)
BOTH = "7272БОХ"      # access_scope=both
SITEONLY = "8383ХӨӨ"  # access_scope=site (энгийн гэрээт)
NOBODY = "9494ЕНГ"    # бүртгэлгүй
PLATES = [INNER, BOTH, SITEONLY, NOBODY]
db = SessionLocal()
made: list = []


def raw(p):
    return {"Picture": {"Plate": {"PlateNumber": p}}}


def mk_cam(site, name, ip, lane_no, lane_dir, inner=False):
    d = Device(id=str(uuid.uuid4()), site_id=site.id, name=name, device_type="camera",
               ip_address=ip, lane_no=lane_no, lane_dir=lane_dir, status="active",
               auto_open=True, nested_inner=inner, device_key=f"zz-{uuid.uuid4().hex[:10]}")
    db.add(d)
    db.flush()
    made.append(d)
    return d


def mk_drv(plate, site_id, scope):
    d = RegisteredDriver(id=str(uuid.uuid4()), plate_number=plate, site_id=site_id,
                         contract_type="CONTRACT", access_scope=scope,
                         valid_from=datetime.utcnow() - timedelta(days=1),
                         valid_to=datetime.utcnow() + timedelta(days=30), is_active=True)
    db.add(d)
    db.flush()
    made.append(d)
    return d


try:
    tpl = TariffTemplate(id=str(uuid.uuid4()), name="ZZ-дотоод-хаалттай", free_minutes=30,
                         grace_minutes=15, extra_hour_price=1000)
    db.add(tpl)
    db.flush()
    made.append(tpl)
    tier = TariffTier(id=str(uuid.uuid4()), template_id=tpl.id, upto_minutes=60, price=1000)
    db.add(tier)
    db.flush()
    made.append(tier)
    site = ParkingSite(id=str(uuid.uuid4()), name="ZZ-Рашбулаг-хаалттай",
                       site_code=f"ZI{uuid.uuid4().hex[:7]}", tariff_template_id=tpl.id,
                       capacity=100, inner_registered_only=True)
    db.add(site)
    db.flush()
    made.append(site)

    out_in = mk_cam(site, "Орох камер", "10.93.93.10", 1, "entry")
    mk_cam(site, "Гарах камер", "10.93.93.11", 2, "exit")
    in_ex = mk_cam(site, "Дотор гарах камер", "10.93.93.12", 3, "exit", inner=True)
    in_in = mk_cam(site, "Дотор орох камер", "10.93.93.13", 4, "entry", inner=True)
    mk_drv(INNER, site.id, "inner")
    mk_drv(BOTH, site.id, "both")
    mk_drv(SITEONLY, site.id, "site")
    db.commit()
    ensure_lane_barriers(db)
    bars = db.query(Device).filter(Device.site_id == site.id, Device.device_type == "barrier",
                                   Device.status == "active").all()
    made += bars
    check("4 хаалт үүсэв (гадна 2 + дотоод 2)", len(bars) == 4, [b.name for b in bars])

    print("\n1. Хамрах хүрээний хайлт")
    check("inner → find_registered ҮГҮЙ (гадна гэрээт биш)",
          find_registered(db, INNER, site.id) is None)
    check("inner → find_inner_registered ТИЙМ",
          find_inner_registered(db, INNER, site.id) is not None)
    check("both → find_registered ТИЙМ", find_registered(db, BOTH, site.id) is not None)
    check("both → find_inner_registered ТИЙМ",
          find_inner_registered(db, BOTH, site.id) is not None)
    check("site → find_registered ТИЙМ", find_registered(db, SITEONLY, site.id) is not None)
    check("site → find_inner_registered ҮГҮЙ",
          find_inner_registered(db, SITEONLY, site.id) is None)
    check("бүртгэлгүй → хоёулаа ҮГҮЙ",
          find_registered(db, NOBODY, site.id) is None
          and find_inner_registered(db, NOBODY, site.id) is None)

    print("\n2. Гадна орох — бүгд хэвийн орно (гадна хаалтад нөлөөгүй)")
    for p in PLATES:
        # burst_merge=False: 6с дотор нэг камерт 4 өөр дугаар уншуулж байгаа нь
        # физикийн хувьд боломжгүй тул цуврал нэгтгэлт ажиллана — тестэд унтраана
        r = asyncio.run(handle_entry(db, out_in, p, 0.95, raw(p), burst_merge=False))
        check(f"{p} гадна орох → хаалт нээгдэв, session үүсэв",
              r.get("barrier_opened") is True and r.get("session_id"), r)
    s_inner = db.query(ParkingSession).filter(ParkingSession.plate_number == INNER,
                                              ParkingSession.site_id == site.id).first()
    s_none = db.query(ParkingSession).filter(ParkingSession.plate_number == NOBODY,
                                             ParkingSession.site_id == site.id).first()
    check("inner хүрээтэй машин гадна session-д ГЭРЭЭТ БИШ (төлбөртэй)",
          s_inner is not None and s_inner.is_registered is False, getattr(s_inner, "is_registered", None))
    s_both = db.query(ParkingSession).filter(ParkingSession.plate_number == BOTH,
                                             ParkingSession.site_id == site.id).first()
    check("both хүрээтэй машин гадна session-д ГЭРЭЭТ", s_both is not None and s_both.is_registered is True)

    print("\n3. Дотоод ОРОХ хаалт — хаалттай (inner_registered_only=true)")
    r = asyncio.run(handle_inner_pass(db, in_in, INNER, 0.95, raw(INNER)))
    db.refresh(s_inner)
    check("inner → нээгдэв, тоолуур зогсов",
          r["barrier_opened"] is True and r["action"] == "inner_entry" and s_inner.paused_since is not None, r)
    r = asyncio.run(handle_inner_pass(db, in_in, BOTH, 0.95, raw(BOTH)))
    check("both → нээгдэв", r["barrier_opened"] is True and r["action"] == "inner_entry", r)
    r = asyncio.run(handle_inner_pass(db, in_in, SITEONLY, 0.95, raw(SITEONLY)))
    check("зөвхөн «site» гэрээт → ТАТГАЛЗАВ (дотоод жагсаалтад байхгүй)",
          r["barrier_opened"] is False and r["action"] == "inner_denied" and r["denied"] is True, r)
    n_before = db.query(ParkingSession).filter(ParkingSession.site_id == site.id).count()
    r = asyncio.run(handle_inner_pass(db, in_in, NOBODY, 0.95, raw(NOBODY)))
    db.refresh(s_none)
    n_after = db.query(ParkingSession).filter(ParkingSession.site_id == site.id).count()
    check("бүртгэлгүй → ТАТГАЛЗАВ", r["barrier_opened"] is False and r["denied"] is True, r)
    check("татгалзсан машины тоолуур ЗОГСООГҮЙ (гадна төлбөр хэвийн гүйнэ)",
          s_none.paused_since is None and r["counter_changed"] is False)
    check("татгалзалт шинэ session үүсгээгүй", n_after == n_before, (n_before, n_after))
    ev = (db.query(LprEvent).filter(LprEvent.device_id == in_in.id, LprEvent.plate_number == NOBODY)
          .order_by(LprEvent.created_at.desc()).first())
    check("татгалзсан уншилт LprEvent-д accepted=false, шалтгаантай",
          ev is not None and ev.accepted is False and "дотоод" in (ev.reject_reason or ""),
          getattr(ev, "reject_reason", None))
    inner_bar_ids = [b.id for b in bars if b.nested_inner and b.lane_dir == "entry"]
    cmds = db.query(BarrierCommand).filter(BarrierCommand.device_id.in_(inner_bar_ids),
                                           BarrierCommand.session_id == s_none.id).count()
    check("татгалзсан машинд дотоод хаалтны команд ҮҮСЭЭГҮЙ", cmds == 0, cmds)

    print("\n4. Дотоод ГАРАХ хаалт — ямагт нээгдэнэ (дотор гацаахгүй)")
    for p in (INNER, SITEONLY, NOBODY):
        r = asyncio.run(handle_inner_pass(db, in_ex, p, 0.95, raw(p)))
        check(f"{p} дотоод гарах → нээгдэв", r["barrier_opened"] is True and r["denied"] is False, r)

    print("\n5. Унтраалга унтарсан — хуучин зан: бүгд нэвтэрнэ")
    site.inner_registered_only = False
    db.commit()
    r = asyncio.run(handle_inner_pass(db, in_in, NOBODY, 0.95, raw(NOBODY)))
    check("inner_registered_only=false → бүртгэлгүй ч нээгдэнэ",
          r["barrier_opened"] is True and r["action"] == "inner_entry", r)
    site.inner_registered_only = True
    db.commit()

    print("\n6. «Бүх зогсоол» (site_id NULL, tenant NULL) inner бүртгэл ч үйлчилнэ")
    g = mk_drv("1515УНЖ", None, "inner")
    db.commit()
    PLATES.append("1515УНЖ")
    check("tenant-гүй глобал inner бүртгэл → дотоод эрхтэй",
          find_inner_registered(db, "1515УНЖ", site.id) is not None)
    check("… гэхдээ гадна гэрээт биш", find_registered(db, "1515УНЖ", site.id) is None)
    g.is_active = False
    db.commit()

    print("\n7. Parent/child загвар: хүүхэд зогсоол дээр эцгийн inner бүртгэл гэрээт")
    child = ParkingSite(id=str(uuid.uuid4()), name="ZZ-дотоод-хүүхэд",
                        site_code=f"ZC{uuid.uuid4().hex[:7]}", parent_site_id=site.id,
                        capacity=10, no_charge=True, registered_only=True)
    db.add(child)
    db.flush()
    made.append(child)
    db.commit()
    check("эцэгт inner бүртгэлтэй → хүүхэд дээр гэрээт",
          find_registered(db, INNER, child.id) is not None)
    check("эцэгт both бүртгэлтэй → хүүхэд дээр гэрээт",
          find_registered(db, BOTH, child.id) is not None)
    check("эцэгт зөвхөн site гэрээт → хүүхэд дээр гэрээт БИШ",
          find_registered(db, SITEONLY, child.id) is None)

finally:
    db.rollback()
    sess_ids = [r[0] for r in db.query(ParkingSession.id)
                .filter(ParkingSession.plate_number.in_(PLATES)).all()]
    dev_ids = [d.id for d in made if isinstance(d, Device)]
    if sess_ids:
        db.query(BarrierCommand).filter(BarrierCommand.session_id.in_(sess_ids)).delete(
            synchronize_session=False)
    if dev_ids:
        db.query(BarrierCommand).filter(BarrierCommand.device_id.in_(dev_ids)).delete(
            synchronize_session=False)
        db.query(LprEvent).filter(LprEvent.device_id.in_(dev_ids)).delete(
            synchronize_session=False)
    db.query(LprEvent).filter(LprEvent.plate_number.in_(PLATES)).delete(synchronize_session=False)
    db.query(ParkingSession).filter(ParkingSession.plate_number.in_(PLATES)).delete(
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
