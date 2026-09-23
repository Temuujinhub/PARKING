"""Төлж байх зуур тарифын шатлал ахисан ч нэхэмжлэлийн дүнгээр хаагдана.

    cd backend && venv/bin/python tests/test_invoice_quote.py

2026-09-23 гомдол (Эрэл-13 0605ГСО, «2,000 төлчихлөө 5,000 болчлоо»):
317df72 (09-19) төлбөр баталгаажих үед дүнг callback-ийн ЦАГААР дахин боддог
болсон тул QR үүсгэснээс хойш 17с–6мин дотор шатлал ахихад (1,500→2,000,
2,000→5,000) PAYMENT_PARTIAL → хаалт нээгдэхгүй, үлдэгдэл дахин нэхдэг.

Шалгах зүйл (тариф: ≤60 1000 · ≤90 1500 · ≤120 2000 · ≤180 5000):
  1. Гарцад 119 мин-д 2,000₮ нэхэмжлэл → 30с дараа (122 мин) төлөгдөв →
     үлдэгдэлгүй хаагдана, хаалт нээгдэнэ, PAYMENT_QUOTE_HONORED аудит
  2. Нэхэмжлэл 20 мин хуучин → одоогийн дүн (5,000) → 3,000₮ үлдэгдэл
     (317df72-ийн «хямд хуучин нэхэмжлэл» хамгаалалт хэвээр)
  3. Утаснаасаа урьдчилж (гарах уншилтгүй) төлсөн → PAID, grace эхэлнэ
  4. invoice_quote_minutes=0 → хуучин (хатуу) зан: 3,000₮ үлдэгдэл
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
settings.qpay_mock = True
settings.ebarimt_mock = True

from app.database import SessionLocal  # noqa: E402
from app.models import (  # noqa: E402
    AuditLog, BarrierCommand, Compensation, Device, ParkingSession, ParkingSite, Payment,
    TariffTemplate, TariffTier,
)
from app.routers import payments_router as pr  # noqa: E402
from app.services import app_settings as A  # noqa: E402
from app.services.device_auto import ensure_lane_barriers  # noqa: E402

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗ <<< FAIL'} {name}{'' if cond else f'  [{extra}]'}")


db = SessionLocal()
made: list = []
site = None
PLATES = ["1201ХТА", "1202ХТА", "1203ХТА", "1204ХТА"]


def audits(action, pid):
    return db.query(AuditLog).filter(AuditLog.action == action, AuditLog.entity_id == pid).all()


def scenario(plate, *, at_exit: bool, invoice_age_sec: int):
    """119 мин-д нэхэмжлэл үүсгээд, цагийг 3 минут урагшлуулж (122 мин) төлнө."""
    now = datetime.utcnow()
    s = ParkingSession(id=str(uuid.uuid4()), site_id=site.id, plate_number=plate,
                       entry_time=now - timedelta(minutes=119), entry_device_id=cam_in.id,
                       status="AWAITING_PAYMENT" if at_exit else "OPEN",
                       exit_device_id=cam_out.id if at_exit else None)
    db.add(s); db.commit()
    pay = pr._create_payment(db, s, "QPAY", "QR")
    db.commit()
    quoted = float(pay.amount)
    # Цаг өнгөрөв: машин 122 мин болж (5,000₮ шатлал), нэхэмжлэл invoice_age_sec настай
    s = db.get(ParkingSession, s.id)
    s.entry_time = s.entry_time - timedelta(minutes=3)
    pay = db.get(Payment, pay.id)
    pay.created_at = datetime.utcnow() - timedelta(seconds=invoice_age_sec)
    db.commit()
    asyncio.run(pr._finalize_paid(db, pay))
    db.expire_all()
    return db.get(ParkingSession, s.id), db.get(Payment, pay.id), quoted


try:
    tpl = TariffTemplate(id=str(uuid.uuid4()), name="ZZ-quote", free_minutes=30,
                         grace_minutes=15, extra_hour_price=3000, daily_cap=25000)
    db.add(tpl); db.flush(); made.append(tpl)
    for upto, price in ((60, 1000), (90, 1500), (120, 2000), (180, 5000)):
        t = TariffTier(id=str(uuid.uuid4()), template_id=tpl.id, upto_minutes=upto, price=price)
        db.add(t); db.flush(); made.append(t)
    site = ParkingSite(id=str(uuid.uuid4()), name="ZZ-quote", site_code=f"ZQ{uuid.uuid4().hex[:6]}",
                       tariff_template_id=tpl.id, capacity=50)
    db.add(site); db.flush(); made.append(site)
    cam_in = Device(id=str(uuid.uuid4()), site_id=site.id, name="ZZ орох", device_type="camera",
                    ip_address="10.255.7.1", lane_no=1, lane_dir="entry", status="active",
                    auto_open=True, device_key=f"zz-{uuid.uuid4().hex[:10]}")
    cam_out = Device(id=str(uuid.uuid4()), site_id=site.id, name="ZZ гарах", device_type="camera",
                     ip_address="10.255.7.2", lane_no=2, lane_dir="exit", status="active",
                     auto_open=True, device_key=f"zz-{uuid.uuid4().hex[:10]}")
    db.add_all([cam_in, cam_out]); db.commit()
    ensure_lane_barriers(db); db.commit()
    print(f"(invoice_quote_minutes = {A.get_exit_rules(db, site.id).get('invoice_quote_minutes')})")

    print("\n1. Гарцад 2,000₮ нэхэмжлэл → 30с дараа төлөгдөхөд шатлал 5,000 болсон")
    s, pay, quoted = scenario(PLATES[0], at_exit=True, invoice_age_sec=30)
    check("нэхэмжлэл 2,000₮", quoted == 2000, quoted)
    check("PAYMENT_PARTIAL алга", not audits("PAYMENT_PARTIAL", pay.id))
    check("PAYMENT_QUOTE_HONORED аудит", len(audits("PAYMENT_QUOTE_HONORED", pay.id)) == 1)
    check("сешн хаагдав (CLOSED), дүн 2,000", s.status == "CLOSED" and float(s.total_fee) == 2000,
          f"{s.status} {s.total_fee}")
    check("хаалт нээгдэв (payment)", db.query(BarrierCommand).filter(
        BarrierCommand.session_id == s.id, BarrierCommand.command_source == "payment",
        BarrierCommand.status == "SUCCESS").count() == 1)

    print("\n2. 20 мин хуучин нэхэмжлэл → одоогийн дүн, 3,000₮ үлдэгдэл")
    s, pay, quoted = scenario(PLATES[1], at_exit=True, invoice_age_sec=20 * 60)
    part = audits("PAYMENT_PARTIAL", pay.id)
    check("PAYMENT_PARTIAL 3,000", len(part) == 1 and float(part[0].detail["remaining_due"]) == 3000,
          [a.detail for a in part])
    check("сешн AWAITING хэвээр", s.status == "AWAITING_PAYMENT", s.status)

    print("\n3. Урьдчилж (гарах уншилтгүй) төлсөн → PAID, grace")
    s, pay, quoted = scenario(PLATES[2], at_exit=False, invoice_age_sec=45)
    check("PAID + exit_deadline", s.status == "PAID" and s.exit_deadline is not None, s.status)
    check("PAYMENT_PARTIAL алга", not audits("PAYMENT_PARTIAL", pay.id))
    check("сешний дүн = барьсан 2,000", float(s.total_fee or 0) == 2000, s.total_fee)
    # Гарах уншилт алдагдаж авто цэвэрлэгээ хаавал өр үүсэхгүй (grace-д шатлал ахисан ч)
    from app.session_logic import close_session_forced
    debt = close_session_forced(db, s, "auto_close", "system", True)
    db.commit(); db.expire_all(); s = db.get(ParkingSession, s.id)
    check("3b. авто хаалт: өр 0, CLOSED 2,000", debt == 0 and s.status == "CLOSED"
          and float(s.total_fee) == 2000, f"{debt} {s.status} {s.total_fee}")

    print("\n4. invoice_quote_minutes=0 → хатуу (хуучин) зан")
    orig = A.get_rules
    A.get_rules = lambda _db, group, site_id=None: (
        {**orig(_db, group, site_id), "invoice_quote_minutes": 0} if group == A.EXITRULES_KEY
        else orig(_db, group, site_id))
    try:
        s, pay, quoted = scenario(PLATES[3], at_exit=True, invoice_age_sec=30)
    finally:
        A.get_rules = orig
    part = audits("PAYMENT_PARTIAL", pay.id)
    check("PAYMENT_PARTIAL 3,000", len(part) == 1 and float(part[0].detail["remaining_due"]) == 3000,
          [a.detail for a in part])

finally:
    db.rollback()
    if site is not None:
        sess_ids = [x[0] for x in db.query(ParkingSession.id).filter(ParkingSession.site_id == site.id).all()]
        pay_ids = [x[0] for x in db.query(Payment.id).filter(Payment.session_id.in_(sess_ids)).all()] if sess_ids else []
        from app.models import FinancialJob, VatReceipt  # noqa: E402
        if pay_ids:
            for M in (FinancialJob, VatReceipt):
                try:
                    db.query(M).filter(M.payment_id.in_(pay_ids)).delete(synchronize_session=False)
                except Exception:  # noqa: BLE001
                    db.rollback()
            db.query(AuditLog).filter(AuditLog.entity_id.in_(pay_ids)).delete(synchronize_session=False)
        if sess_ids:
            db.query(Compensation).filter(Compensation.session_id.in_(sess_ids)).delete(synchronize_session=False)
            db.query(Payment).filter(Payment.session_id.in_(sess_ids)).delete(synchronize_session=False)
            db.query(AuditLog).filter(AuditLog.entity_id.in_(sess_ids)).delete(synchronize_session=False)
        devs = [x[0] for x in db.query(Device.id).filter(Device.site_id == site.id).all()]
        if devs:
            db.query(BarrierCommand).filter(BarrierCommand.device_id.in_(devs)).delete(synchronize_session=False)
        db.query(ParkingSession).filter(ParkingSession.site_id == site.id).delete(synchronize_session=False)
        db.query(Device).filter(Device.site_id == site.id).delete(synchronize_session=False)
        db.commit()
    for obj in reversed(made):
        try:
            db.delete(obj); db.commit()
        except Exception as e:  # noqa: BLE001
            db.rollback(); print(f"  (цэвэрлэгээ: {e})")
    db.close()

print(f"\n{PASS} ✓  {FAIL} ✗")
sys.exit(1 if FAIL else 0)
