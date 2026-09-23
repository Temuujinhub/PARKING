"""Касс «Өнөөдөр гарсан машинууд»: дутуу төлсөн машиныг «Төлсөн» гэж харуулахгүй.

    cd backend && venv/bin/python tests/test_today_exits_paid.py

2026-09-23 Эрэл-13 0605ГСО: 1,500₮ төлсөн ч шатлал 2,000₮ болж 500₮ үлдэгдэлтэй,
хаалт нээгдээгүй машиныг хүснэгт «Төлсөн» гэж харуулсан (`paid = status=='PAID'
or bool(payment)`). 0875УАВ: 72ц авто хаалтыг «гарсан» гэж харуулсан.

Шалгах зүйл:
  1. AWAITING + 1,500 төлсөн, дүн 2,000 → partial, үлдэгдэл 500, paid=False
  2. CLOSED бүрэн төлсөн → paid=True, partial=False, үлдэгдэл 0
  3. Гарах уншилтгүй авто хаасан FREE → exit_inferred=True, paid=False
  4. Нэг QR-т ӨӨР сешний өр багтсан (snapshot-гүй) → paid_amount зөвхөн өөрийн хэсэг
"""
import os
import sys
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal  # noqa: E402
from app.models import (  # noqa: E402
    Compensation, ParkingSession, ParkingSite, Payment, TariffTemplate, TariffTier,
)
from app.routers.sessions_router import today_exits  # noqa: E402

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗ <<< FAIL'} {name}{'' if cond else f'  [{extra}]'}")


db = SessionLocal()
made: list = []
site = None
try:
    tpl = TariffTemplate(id=str(uuid.uuid4()), name="ZZ-today", free_minutes=30,
                         grace_minutes=15, extra_hour_price=3000, daily_cap=25000)
    db.add(tpl); db.flush(); made.append(tpl)
    for upto, price in ((60, 1000), (90, 1500), (120, 2000), (180, 5000)):
        t = TariffTier(id=str(uuid.uuid4()), template_id=tpl.id, upto_minutes=upto, price=price)
        db.add(t); db.flush(); made.append(t)
    site = ParkingSite(id=str(uuid.uuid4()), name="ZZ-today", site_code=f"ZT{uuid.uuid4().hex[:6]}",
                       tariff_template_id=tpl.id, capacity=50)
    db.add(site); db.flush(); made.append(site)
    now = datetime.utcnow()

    def sess(plate, **kw):
        s = ParkingSession(id=str(uuid.uuid4()), site_id=site.id, plate_number=plate, **kw)
        db.add(s); db.flush()
        return s

    def pay(s, amount, snapshot=True):
        p = Payment(id=str(uuid.uuid4()), session_id=s.id, provider="QPAY", payment_method="QR",
                    sender_invoice_no=f"ZZT-{uuid.uuid4().hex[:10]}",
                    amount=amount, vat_amount=round(amount / 11), status="PAID", paid_at=now,
                    fee_snapshot={"total_fee": amount, "parking_amount": amount} if snapshot else None)
        db.add(p); db.flush()
        return p

    a = sess("0605ХГО", entry_time=now - timedelta(minutes=93), status="AWAITING_PAYMENT",
             total_fee=2000, payment_wait_started_at=now - timedelta(minutes=2))
    pay(a, 1500)
    b = sess("3992ХНА", entry_time=now - timedelta(minutes=54), exit_time=now - timedelta(minutes=1),
             status="CLOSED", total_fee=1000, paid_at=now, exit_confirmed=True)
    pay(b, 1000)
    c = sess("0875ХАВ", entry_time=now - timedelta(hours=72), exit_time=now,
             status="FREE", total_fee=0, exit_confirmed=False)
    d = sess("1111ХББ", entry_time=now - timedelta(minutes=50), exit_time=now - timedelta(minutes=2),
             status="CLOSED", total_fee=1000, paid_at=now, exit_confirmed=True)
    other = sess("2222ХББ", entry_time=now - timedelta(days=3), exit_time=now - timedelta(days=3),
                 status="MANUAL_CLOSED", total_fee=2000)
    pd = pay(d, 3000, snapshot=False)                       # 1,000 өөрийн + 2,000 өөр сешний өр
    db.add(Compensation(id=str(uuid.uuid4()), session_id=other.id, site_id=site.id,
                        plate_number="2222ХББ", amount=2000, status="PAID", payment_id=pd.id))
    db.commit()

    user = SimpleNamespace(role="SUPER_ADMIN", site_ids=None, site_id=None, tenant_id=None)
    rows = {r["plate_number"]: r for r in today_exits(site.id, db, user)["rows"]}

    print("\n1. Дутуу төлсөн (1,500 / 2,000)")
    r = rows["0605ХГО"]
    check("partial=True, paid=False", r["partial"] and not r["paid"], r)
    check("үлдэгдэл 500, төлсөн 1,500", r["amount_due"] == 500 and r["paid_amount"] == 1500, r)

    print("\n2. Бүрэн төлсөн")
    r = rows["3992ХНА"]
    check("paid=True, partial=False, үлдэгдэл 0", r["paid"] and not r["partial"] and r["amount_due"] == 0, r)
    check("exit_inferred=False", r["exit_inferred"] is False, r)

    print("\n3. 72ц авто хаалт (гарах уншилтгүй)")
    r = rows["0875ХАВ"]
    check("exit_inferred=True, paid=False", r["exit_inferred"] and not r["paid"], r)

    print("\n4. Нэг QR-т өөр сешний өр багтсан")
    r = rows["1111ХББ"]
    check("paid_amount = 1,000 (өрийг хассан), paid=True", r["paid_amount"] == 1000 and r["paid"], r)

finally:
    db.rollback()
    if site is not None:
        ids = [x[0] for x in db.query(ParkingSession.id).filter(ParkingSession.site_id == site.id).all()]
        if ids:
            db.query(Compensation).filter(Compensation.session_id.in_(ids)).delete(synchronize_session=False)
            db.query(Payment).filter(Payment.session_id.in_(ids)).delete(synchronize_session=False)
            db.query(ParkingSession).filter(ParkingSession.id.in_(ids)).delete(synchronize_session=False)
        db.commit()
    for obj in reversed(made):
        try:
            db.delete(obj); db.commit()
        except Exception as e:  # noqa: BLE001
            db.rollback(); print(f"  (цэвэрлэгээ: {e})")
    db.close()

print(f"\n{PASS} ✓  {FAIL} ✗")
sys.exit(1 if FAIL else 0)
