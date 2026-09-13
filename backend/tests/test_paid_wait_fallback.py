"""«Төлсөн-хүлээж буй» fallback — гарах камер уншаагүй/буруу уншсан төлсөн машин.

    cd backend && venv/bin/python tests/test_paid_wait_fallback.py

14 хоногт «төлсөн ч хаалт нээгдээгүй» 216-ийн 184 = утаснаасаа төлсөн машиныг
гарах камер junk/OCR зөрүүтэй уншсан (Кэй Эйч 40/122). Дүрэм: зогсоолд сүүлийн N
минутад төлсөн, гарцад уншигдаагүй PAID сешн ЯГ НЭГ + уншсан дугаар junk эсвэл
≤2 тэмдэгт зөрүүтэй → тэр машин гэж үзэж нээнэ.

Шалгах зүйл:
  1. junk уншилт («492ХН») → төлсөн сешн хаагдаж хаалт нээгдэнэ, аудит PAID_WAIT_FALLBACK
  2. OCR зөрүүтэй зөв дугаар (2 тэмдэгт) → мөн адил
  3. Огт өөр зөв дугаар → fallback ҮГҮЙ (бүртгэлгүй гарцын урсгал), төлсөн сешн хэвээр
  4. Нэр дэвшигч 2 → fallback ҮГҮЙ
  5. Дүрэм 0 → fallback ҮГҮЙ
  6. Burst max_diff тохиргоо: 7 → огт өөр дугаарыг ч нэгтгэнэ (хуучин зан)
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
    AuditLog, BarrierCommand, Device, LprEvent, ParkingSession, ParkingSite, Payment,
    TariffTemplate, TariffTier,
)
from app.services import app_settings as A  # noqa: E402
from app.services.device_auto import ensure_lane_barriers  # noqa: E402
from app.session_logic import handle_entry, handle_exit, plates_burst_similar  # noqa: E402

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗ <<< FAIL'} {name}{'' if cond else f'  [{extra}]'}")


P1, P2, P3, P4 = "4924ХНУ", "3311ХЕУ", "7179ХНО", "5555ХАА"
# JUNK: тайрагдаагүй хог (тайрагдсаныг plates_ocr_similar аль хэдийн тохоодог)
# OCR1: 2 тэмдэгтийн зөрүү (1 зөрүүг мөн ердийн fuzzy тохоолт барина)
JUNK, OCR1, OTHER = "492ХН", "4924ХБО", "8888ХББ"
PLATES = [P1, P2, P3, P4, JUNK, OCR1, OTHER]
db = SessionLocal()
made: list = []
_real_exit = A.get_exit_rules
_real_barrier = A.get_barrier_rules


def raw(p):
    return {"Picture": {"Plate": {"PlateNumber": p}}}


def rules_with(real, **over):
    def _f(db_, site_id=None):
        r = dict(real(db_, site_id)); r.update(over); return r
    return _f


def make_paid(plate, site, cam, minutes_ago=3):
    now = datetime.utcnow()
    paid_at = now - timedelta(minutes=minutes_ago)
    s = ParkingSession(id=str(uuid.uuid4()), site_id=site.id, plate_number=plate,
                       entry_time=paid_at - timedelta(minutes=50), entry_device_id=cam.id,
                       status="PAID", paid_at=paid_at, exit_deadline=paid_at + timedelta(minutes=15),
                       base_fee=909, vat_amount=91, total_fee=1000, duration_minutes=50)
    db.add(s); db.flush()
    db.add(Payment(id=str(uuid.uuid4()), session_id=s.id, provider="QPAY", payment_method="QR",
                   sender_invoice_no=f"ZZ-{plate}-{uuid.uuid4().hex[:6]}", amount=1000, vat_amount=91,
                   status="PAID", paid_at=paid_at))
    db.commit()
    return s


def clear_plates(*plates):
    ids = [r[0] for r in db.query(ParkingSession.id).filter(ParkingSession.plate_number.in_(plates)).all()]
    if ids:
        db.query(BarrierCommand).filter(BarrierCommand.session_id.in_(ids)).delete(synchronize_session=False)
        db.query(Payment).filter(Payment.session_id.in_(ids)).delete(synchronize_session=False)
        db.query(AuditLog).filter(AuditLog.entity_id.in_(ids)).delete(synchronize_session=False)
        db.query(ParkingSession).filter(ParkingSession.id.in_(ids)).delete(synchronize_session=False)
    db.query(LprEvent).filter(LprEvent.plate_number.in_(plates)).delete(synchronize_session=False)
    db.commit()


try:
    tpl = TariffTemplate(id=str(uuid.uuid4()), name="ZZ-fallback", free_minutes=30,
                         grace_minutes=15, extra_hour_price=1000, daily_cap=25000)
    db.add(tpl); db.flush(); made.append(tpl)
    tier = TariffTier(id=str(uuid.uuid4()), template_id=tpl.id, upto_minutes=60, price=1000)
    db.add(tier); db.flush(); made.append(tier)
    site = ParkingSite(id=str(uuid.uuid4()), name="ZZ-fallback", site_code=f"ZF{uuid.uuid4().hex[:7]}",
                       tariff_template_id=tpl.id, capacity=100)
    db.add(site); db.flush(); made.append(site)
    cam_in = Device(id=str(uuid.uuid4()), site_id=site.id, name="ZZ орох", device_type="camera",
                    ip_address="10.255.7.1", lane_no=1, lane_dir="entry", status="active",
                    auto_open=True, device_key=f"zz-{uuid.uuid4().hex[:10]}")
    cam_out = Device(id=str(uuid.uuid4()), site_id=site.id, name="ZZ гарах", device_type="camera",
                     ip_address="10.255.7.2", lane_no=1, lane_dir="exit", status="active",
                     auto_open=True, device_key=f"zz-{uuid.uuid4().hex[:10]}")
    db.add_all([cam_in, cam_out]); db.flush(); made += [cam_in, cam_out]
    db.commit()
    ensure_lane_barriers(db); db.commit()
    A.get_exit_rules = rules_with(_real_exit, paid_wait_fallback_minutes=10, no_session_fee=2000)

    print("\n1. junk уншилт → төлсөн-хүлээж буй машин гарна")
    s = make_paid(P1, site, cam_in)
    r = asyncio.run(handle_exit(db, cam_out, JUNK, 0.6, raw(JUNK)))
    db.expire_all(); s = db.get(ParkingSession, s.id)
    check("хаалт нээгдэж сешн CLOSED", r.get("barrier_opened") is True and s.status == "CLOSED"
          and r.get("session_id") == s.id, f"{r} {s.status}")
    check("дүн 1,000 хэвээр (нэмэлт нэхээгүй)", float(s.total_fee) == 1000.0, s.total_fee)
    a = db.query(AuditLog).filter(AuditLog.action == "PAID_WAIT_FALLBACK", AuditLog.entity_id == s.id).first()
    check("аудит PAID_WAIT_FALLBACK", a is not None and a.detail.get("read_plate") == JUNK, a)
    clear_plates(P1, JUNK)

    print("\n2. OCR зөрүүтэй (2 тэмдэгт) зөв дугаар → мөн адил")
    s = make_paid(P1, site, cam_in)
    r = asyncio.run(handle_exit(db, cam_out, OCR1, 0.8, raw(OCR1)))
    db.expire_all(); s = db.get(ParkingSession, s.id)
    check("нээгдэв", r.get("barrier_opened") is True and s.status == "CLOSED", f"{r} {s.status}")
    clear_plates(P1, OCR1)

    print("\n3. Огт өөр зөв дугаар → fallback ҮГҮЙ (өөр машин байж болно)")
    s = make_paid(P1, site, cam_in)
    r = asyncio.run(handle_exit(db, cam_out, OTHER, 0.9, raw(OTHER)))
    db.expire_all(); s = db.get(ParkingSession, s.id)
    check("төлсөн сешн PAID хэвээр, уншилт бүртгэлгүй гарцын урсгалаар", s.status == "PAID"
          and r.get("action") in ("no_session_fee", "no_session") and r.get("session_id") != s.id,
          f"{r} {s.status}")
    clear_plates(P1, OTHER)

    print("\n4. Нэр дэвшигч 2 → fallback ҮГҮЙ")
    s1 = make_paid(P1, site, cam_in); s2 = make_paid(P2, site, cam_in)
    r = asyncio.run(handle_exit(db, cam_out, JUNK, 0.6, raw(JUNK)))
    db.expire_all()
    check("хоёулаа PAID хэвээр", db.get(ParkingSession, s1.id).status == "PAID"
          and db.get(ParkingSession, s2.id).status == "PAID" and r.get("action") in ("no_session", "no_session_fee"), r)
    clear_plates(P1, P2, JUNK)

    print("\n5. Дүрэм 0 → fallback ҮГҮЙ")
    A.get_exit_rules = rules_with(_real_exit, paid_wait_fallback_minutes=0, no_session_fee=2000)
    s = make_paid(P1, site, cam_in)
    r = asyncio.run(handle_exit(db, cam_out, JUNK, 0.6, raw(JUNK)))
    db.expire_all()
    check("PAID хэвээр", db.get(ParkingSession, s.id).status == "PAID", r)
    clear_plates(P1, JUNK)
    A.get_exit_rules = _real_exit

    print("\n6. entry_burst_max_diff = 7 → огт өөр дугаарыг ч нэгтгэнэ (хуучин зан)")
    check("plates_burst_similar max_diff=7", plates_burst_similar(P3, P4, 7) and not plates_burst_similar(P3, P4, 2))
    A.get_barrier_rules = rules_with(_real_barrier, entry_burst_max_diff=7)
    r1 = asyncio.run(handle_entry(db, cam_in, P3, 0.9, raw(P3)))
    r2 = asyncio.run(handle_entry(db, cam_in, P4, 0.9, raw(P4)))
    check("2-р уншилт autocorrect (тохиргоогоор)", r2.get("action") == "plate_autocorrect", r2)
    A.get_barrier_rules = _real_barrier
    clear_plates(P3, P4)

finally:
    A.get_exit_rules = _real_exit
    A.get_barrier_rules = _real_barrier
    db.rollback()
    clear_plates(*PLATES)
    site_devs = [d.id for d in db.query(Device.id).filter(Device.site_id == site.id).all()]
    if site_devs:
        db.query(BarrierCommand).filter(BarrierCommand.device_id.in_(site_devs)).delete(synchronize_session=False)
        db.query(LprEvent).filter(LprEvent.device_id.in_(site_devs)).delete(synchronize_session=False)
    db.query(ParkingSession).filter(ParkingSession.site_id == site.id).delete(synchronize_session=False)
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
