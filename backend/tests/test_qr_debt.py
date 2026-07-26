"""QR-аар өрийг нийлүүлж төлөх + өр тус бүрд тусдаа e-Barimt (локал DB, mock).

    cd backend && venv/bin/python tests/test_qr_debt.py

Урсгал: session (төлбөртэй) + 2 PENDING өр → QPay invoice (нийлбэр дүн, өр мөр
тус бүр) → төлөгдмөгц: session PAID, өр бүр PAID, VatReceipt 3 ширхэг (session +
өр тус бүр) зөв дүнтэй, НӨАТ тус бүрдээ бодогдсон.
"""
import os
import sys
import uuid
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings

settings.qpay_mock = True
settings.ebarimt_mock = True
settings.barrier_mock = True
settings.snapshot_enabled = False
settings.screen_enabled = False

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (Compensation, ParkingSession, ParkingSite, Payment,  # noqa: E402
                        VatReceipt)

PASS = FAIL = 0


def check(name, cond):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗ <<< FAIL'} {name}")


client = TestClient(app)
db = SessionLocal()
site = db.query(ParkingSite).filter(ParkingSite.is_active.is_(True)).first()
plate = f"8{uuid.uuid4().hex[:3].upper()}ӨРТ"

s = ParkingSession(site_id=site.id, plate_number=plate,
                   entry_time=datetime.utcnow() - timedelta(minutes=95),
                   status="AWAITING_PAYMENT")
db.add(s)
db.flush()
comp1 = Compensation(session_id=None, site_id=site.id, plate_number=plate,
                     amount=5000, reason="unpaid_exit", status="PENDING")
comp2 = Compensation(session_id=None, site_id=site.id, plate_number=plate,
                     amount=2000, reason="unpaid_exit", status="PENDING")
db.add_all([comp1, comp2])
db.commit()
sid, c1id, c2id = s.id, comp1.id, comp2.id
db.close()

print("public /sessions задаргаа:")
r = client.get(f"/api/public/sessions?plate={plate}&site={site.site_code}")
check("200 + debt_amount=7000", r.status_code == 200 and r.json()["debt_amount"] == 7000)
sess_due = r.json()["amount_due"]
check("amount_total = due + 7000", abs(r.json()["amount_total"] - (sess_due + 7000)) < 1)

print("QPay invoice (өр нийлүүлсэн):")
r = client.post("/api/payments/qpay/invoice", json={"session_id": sid})
check("invoice үүссэн", r.status_code == 200)
inv = r.json()
check("нийт дүн = session + өр", abs(inv["amount"] - (sess_due + 7000)) < 1)
check("debt_amount=7000 буцаасан", inv.get("debt_amount") == 7000)
pid = inv["payment_id"]

db = SessionLocal()
check("өрүүд payment-д холбогдсон",
      {c.payment_id for c in db.query(Compensation).filter(
          Compensation.id.in_([c1id, c2id]))} == {pid})
db.close()

print("Төлөгдсөний дараа (mock check):")
r = client.post(f"/api/payments/qpay/check/{pid}")
check("PAID болов", r.status_code == 200 and r.json()["status"] == "PAID")

db = SessionLocal()
p = db.get(Payment, pid)
check("Payment.amount = нийлбэр", abs(float(p.amount) - (sess_due + 7000)) < 1)
c1, c2 = db.get(Compensation, c1id), db.get(Compensation, c2id)
check("өр 2-уул PAID + QR тэмдэглэгээтэй",
      c1.status == "PAID" and c2.status == "PAID" and "QR" in (c1.paid_by or ""))
receipts = db.query(VatReceipt).filter(VatReceipt.payment_id == pid).all()
check("VatReceipt 3 ширхэг (session + өр×2)", len(receipts) == 3)
amounts = sorted(float(v.amount) for v in receipts)
check("баримтын дүнгүүд зөв задарсан (session + 2000 + 5000)",
      amounts == sorted([2000.0, 5000.0, round(sess_due, 2)]))
vat_r = settings.vat_rate
by_amount = {float(v.amount): float(v.vat_amount) for v in receipts}
check("өр тус бүрийн НӨАТ тусдаа бодогдсон",
      by_amount[5000] == round(5000 * vat_r / (1 + vat_r))
      and by_amount[2000] == round(2000 * vat_r / (1 + vat_r)))
check("нийт НӨАТ = хэсгүүдийн нийлбэр",
      abs(sum(float(v.vat_amount) for v in receipts) - float(p.vat_amount)) < 1)
sess = db.get(ParkingSession, sid)
check("session PAID/CLOSED болсон", sess.status in ("PAID", "CLOSED"))

print("Давхар check idempotent:")
r = client.post(f"/api/payments/qpay/check/{pid}")
check("дахин PAID, баримт нэмэгдээгүй",
      r.json()["status"] == "PAID"
      and db.query(VatReceipt).filter(VatReceipt.payment_id == pid).count() == 3)

# ─── Цэвэрлэгээ ─────────────────────────────────────────────────────────────
db.query(VatReceipt).filter(VatReceipt.payment_id == pid).delete()
db.query(Compensation).filter(Compensation.id.in_([c1id, c2id])).delete(synchronize_session=False)
db.query(Payment).filter(Payment.session_id == sid).delete()
db.query(ParkingSession).filter(ParkingSession.id == sid).delete()
db.commit()
db.close()

print(f"\n{PASS} PASS, {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
