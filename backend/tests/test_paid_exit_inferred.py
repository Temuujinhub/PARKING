"""Төлсөн ч гарах уншилт алдагдсан сешн — «deadline дээр гарсан» гэж төлсөн дүнгээр хаах.

    cd backend && venv/bin/python tests/test_paid_exit_inferred.py

Кэй Эйч 2026-09-08 (4924УНУ, 3311УЕУ, 7179УНО): QR-аар төлсөн машиныг гарах камер
уншаагүй → сешн PAID хэвээр 2–3 хоног «дотор» → дараа камерт уншигдахад орсноос
хойшхи бүх цагаар (50,000–75,000₮) дахин бодогдож хуурамч үлдэгдэл/QR/өр үүсдэг байв.

Шалгах зүйл:
  1. ОРОХ камерт дахин уншигдахад: хуучин PAID сешн deadline дээр ТӨЛСӨН дүнгээр
     хаагдаж (CLOSED, exit_confirmed=false), ШИНЭ сешн нээгдэнэ, аудит PAID_EXIT_INFERRED
  2. ГАРАХ камерт уншигдахад: хуучин нь мөн адил хаагдаж, уншилт нь «бүртгэлгүй
     гарц» урсгалаар явна (орсноос хойшхи 2 хоногийн дүн нэхэхгүй)
  3. Босго дотор (deadline-аас 1 цагийн дараа) → хуучин зан: зөрүүг нэхнэ
  4. Дүрэм унтраалттай (paid_exit_hours=0) → хуучин зан
  5. Авто цэвэрлэгээ (auto_close.run_once) PAID гацсаныг босгын дараа хаана
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
from app.session_logic import handle_entry, handle_exit  # noqa: E402

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗ <<< FAIL'} {name}{'' if cond else f'  [{extra}]'}")


P_REENTRY = "1010ХАА"
P_EXIT = "2020ХББ"
P_SOON = "3030ХВВ"
P_OFF = "4040ХГГ"
P_AUTO = "5050ХДД"
PLATES = [P_REENTRY, P_EXIT, P_SOON, P_OFF, P_AUTO]
db = SessionLocal()
made: list = []
_real_rules = A.get_autoclose_rules


def raw(p):
    return {"Picture": {"Plate": {"PlateNumber": p}}}


def mk_cam(site, name, ip, lane_dir):
    d = Device(id=str(uuid.uuid4()), site_id=site.id, name=name, device_type="camera",
               ip_address=ip, lane_no=1, lane_dir=lane_dir, status="active",
               auto_open=True, device_key=f"zz-{uuid.uuid4().hex[:10]}")
    db.add(d)
    db.flush()
    made.append(d)
    return d


def sess(plate, site_id):
    return (db.query(ParkingSession).filter(ParkingSession.plate_number == plate,
                                            ParkingSession.site_id == site_id)
            .order_by(ParkingSession.entry_time).all())


def make_paid_stuck(plate, site, cam, hours_ago: float):
    """Орж ирээд 50 мин зогсоод төлсөн, гарах уншилт ирээгүй сешн; deadline нь
    `hours_ago` цагийн өмнө өнгөрсөн."""
    now = datetime.utcnow()
    paid_at = now - timedelta(hours=hours_ago) - timedelta(minutes=15)
    s = ParkingSession(id=str(uuid.uuid4()), site_id=site.id, plate_number=plate,
                       entry_time=paid_at - timedelta(minutes=50), entry_device_id=cam.id,
                       status="PAID", paid_at=paid_at, exit_deadline=paid_at + timedelta(minutes=15),
                       base_fee=909, vat_amount=91, total_fee=1000, duration_minutes=50,
                       updated_at=paid_at)
    db.add(s)
    db.flush()
    db.add(Payment(id=str(uuid.uuid4()), session_id=s.id, provider="QPAY", payment_method="QR",
                   sender_invoice_no=f"ZZ-{plate}-{uuid.uuid4().hex[:6]}", amount=1000, vat_amount=91,
                   status="PAID", paid_at=paid_at))
    db.commit()
    return s


def rules_with(**over):
    def _f(db_, site_id=None):
        r = dict(_real_rules(db_, site_id))
        r.update(over)
        return r
    return _f


try:
    tpl = TariffTemplate(id=str(uuid.uuid4()), name="ZZ-paid-exit", free_minutes=30,
                         grace_minutes=15, extra_hour_price=1000, daily_cap=25000)
    db.add(tpl)
    db.flush()
    made.append(tpl)
    tier = TariffTier(id=str(uuid.uuid4()), template_id=tpl.id, upto_minutes=60, price=1000)
    db.add(tier)
    db.flush()
    made.append(tier)
    site = ParkingSite(id=str(uuid.uuid4()), name="ZZ-paid-exit",
                       site_code=f"ZP{uuid.uuid4().hex[:7]}", tariff_template_id=tpl.id, capacity=100)
    db.add(site)
    db.flush()
    made.append(site)
    cam_in = mk_cam(site, "ZZ орох", "10.255.9.1", "entry")
    cam_out = mk_cam(site, "ZZ гарах", "10.255.9.2", "exit")
    db.commit()
    ensure_lane_barriers(db)
    db.commit()
    A.get_autoclose_rules = rules_with(paid_exit_hours=2)

    print("\n1. Төлсөн ч гарах уншилтгүй, 3 цагийн дараа ОРОХ камерт дахин уншигдав")
    old = make_paid_stuck(P_REENTRY, site, cam_in, hours_ago=3)
    r = asyncio.run(handle_entry(db, cam_in, P_REENTRY, 0.95, raw(P_REENTRY), burst_merge=False))
    db.expire_all()
    ss = sess(P_REENTRY, site.id)
    check("шинэ сешн нээгдэв, хаалт нээгдэв",
          len(ss) == 2 and r.get("barrier_opened") is True and r.get("session_id") == ss[1].id, r)
    o = db.get(ParkingSession, old.id)
    check("хуучин сешн CLOSED, exit_confirmed=false", o.status == "CLOSED" and o.exit_confirmed is False,
          f"{o.status} {o.exit_confirmed}")
    check("хуучин дүн = төлсөн 1,000₮ (2 хоногийн дүн биш)", float(o.total_fee) == 1000.0
          and o.duration_minutes == 50, f"{o.total_fee} {o.duration_minutes}")
    check("гарах цаг = deadline", o.exit_time == o.exit_deadline, f"{o.exit_time} vs {o.exit_deadline}")
    check("тэмдэглэлд шалтгаан", "гарах уншилт алдагдсан" in (o.note or ""), o.note)
    a = db.query(AuditLog).filter(AuditLog.action == "PAID_EXIT_INFERRED",
                                  AuditLog.entity_id == old.id).first()
    check("аудит PAID_EXIT_INFERRED (reentry)", a is not None and a.detail.get("trigger") == "reentry",
          a.detail if a else None)
    r = asyncio.run(handle_exit(db, cam_out, P_REENTRY, 0.95, raw(P_REENTRY)))
    db.expire_all()
    n = db.get(ParkingSession, ss[1].id)
    check("шинэ сешн 1 минутад үнэгүй гарав", n.status == "FREE" and r.get("barrier_opened") is True,
          f"{n.status} {r}")

    print("\n2. Төлсөн ч гарах уншилтгүй, 3 цагийн дараа ГАРАХ камерт уншигдав")
    old = make_paid_stuck(P_EXIT, site, cam_in, hours_ago=3)
    r = asyncio.run(handle_exit(db, cam_out, P_EXIT, 0.95, raw(P_EXIT)))
    db.expire_all()
    o = db.get(ParkingSession, old.id)
    check("хуучин сешн төлсөн дүнгээр CLOSED", o.status == "CLOSED" and float(o.total_fee) == 1000.0,
          f"{o.status} {o.total_fee}")
    check("уншилт «бүртгэлгүй гарц» урсгалаар явав (2 хоногийн дүн нэхээгүй)",
          r.get("action") in ("no_session_fee", "no_session", "registered_exit")
          and r.get("session_id") != old.id, r)

    print("\n3. Босго ДОТОР — deadline-аас 1 цагийн дараа гарах уншилт → хуучин зан (зөрүү нэхнэ)")
    old = make_paid_stuck(P_SOON, site, cam_in, hours_ago=1)
    r = asyncio.run(handle_exit(db, cam_out, P_SOON, 0.95, raw(P_SOON)))
    db.expire_all()
    o = db.get(ParkingSession, old.id)
    check("сешн AWAITING_PAYMENT, дүн өссөн", o.status == "AWAITING_PAYMENT"
          and float(o.total_fee) > 1000, f"{o.status} {o.total_fee} {r.get('action')}")

    print("\n4. Дүрэм унтраалттай (paid_exit_hours=0) → хуучин зан")
    A.get_autoclose_rules = rules_with(paid_exit_hours=0)
    old = make_paid_stuck(P_OFF, site, cam_in, hours_ago=3)
    r = asyncio.run(handle_entry(db, cam_in, P_OFF, 0.95, raw(P_OFF), burst_merge=False))
    db.expire_all()
    check("шинэ сешн нээгдээгүй (хуучин дээр наалдав)", len(sess(P_OFF, site.id)) == 1
          and r.get("session_id") == old.id, r)
    A.get_autoclose_rules = rules_with(paid_exit_hours=2)

    print("\n5. Авто цэвэрлэгээ — PAID гацсаныг босгын дараа төлсөн дүнгээр хаана")
    from app.services import auto_close
    old = make_paid_stuck(P_AUTO, site, cam_in, hours_ago=3)
    auto_close.run_once()
    db.expire_all()
    o = db.get(ParkingSession, old.id)
    check("CLOSED, дүн 1,000₮, exit=deadline", o.status == "CLOSED" and float(o.total_fee) == 1000.0
          and o.exit_time == o.exit_deadline, f"{o.status} {o.total_fee}")
    a = db.query(AuditLog).filter(AuditLog.action == "PAID_EXIT_INFERRED",
                                  AuditLog.entity_id == old.id).first()
    check("аудит trigger=auto_close", a is not None and "auto_close" in str(a.detail.get("trigger")),
          a.detail if a else None)

finally:
    A.get_autoclose_rules = _real_rules
    db.rollback()
    sess_ids = [r[0] for r in db.query(ParkingSession.id)
                .filter(ParkingSession.plate_number.in_(PLATES)).all()]
    dev_ids = [d.id for d in made if isinstance(d, Device)]
    if sess_ids:
        db.query(BarrierCommand).filter(BarrierCommand.session_id.in_(sess_ids)).delete(
            synchronize_session=False)
        db.query(Payment).filter(Payment.session_id.in_(sess_ids)).delete(synchronize_session=False)
        db.query(AuditLog).filter(AuditLog.entity_id.in_(sess_ids)).delete(synchronize_session=False)
    if dev_ids:
        db.query(BarrierCommand).filter(BarrierCommand.device_id.in_(dev_ids)).delete(
            synchronize_session=False)
        db.query(LprEvent).filter(LprEvent.device_id.in_(dev_ids)).delete(synchronize_session=False)
    db.query(LprEvent).filter(LprEvent.plate_number.in_(PLATES)).delete(synchronize_session=False)
    # Сешн (entry_device_id FK) → дараа нь төхөөрөмж
    db.query(ParkingSession).filter(ParkingSession.plate_number.in_(PLATES)).delete(
        synchronize_session=False)
    db.query(Device).filter(Device.site_id == site.id).delete(synchronize_session=False)
    db.commit()
    for obj in reversed(made):
        if isinstance(obj, Device):
            continue
        try:
            db.delete(obj)
            db.commit()
        except Exception as e:  # noqa: BLE001
            db.rollback()
            print(f"  (цэвэрлэгээ: {e})")
    db.close()

print(f"\n{PASS} ✓  {FAIL} ✗")
sys.exit(1 if FAIL else 0)
