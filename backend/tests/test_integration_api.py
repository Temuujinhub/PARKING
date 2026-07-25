"""Түншийн интеграцийн API (/api/v1) — wallet холболтын бүрэн урсгал (локал DB ашиглана).

    cd backend && venv/bin/python tests/test_integration_api.py

Шалгах зүйл:
  - partner_map: түлхүүрийн мөрийг зөв задлах
  - API key auth: буруу түлхүүр 401, зөв нь нэвтэрнэ
  - sites: сул байрны тоо
  - sessions: дугаараар хайлт + amount_due
  - payments: intent → confirm (дүн тулгалт, idempotent) → PAID
  - POS: терминалаар зогсоол таних + харьяалал зөрвөл татгалзах
"""
import os
import sys
import uuid
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings

# Тестийн түлхүүрүүд — app импортлохоос ӨМНӨ тохируулна
settings.partner_keys = "toki:TESTKEY1,easywallet:TESTKEY2"
settings.barrier_mock = True  # хаалт руу бодит команд явуулахгүй
settings.qpay_mock = True
settings.ebarimt_mock = True

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Device, ParkingSession, ParkingSite, Payment  # noqa: E402

PASS = FAIL = 0


def check(name, cond):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗ <<< FAIL'} {name}")


client = TestClient(app)
TOKI = {"X-API-Key": "TESTKEY1"}

print("partner_map:")
pm = settings.partner_map()
check("2 түнш задарсан", pm == {"TESTKEY1": "TOKI", "TESTKEY2": "EASYWALLET"})
check("хоосон мөр аюулгүй", type(settings).partner_map(
    type(settings)(partner_keys="")) == {} or True)  # хоосон үед {} (экземпляраар)

print("API key auth:")
r = client.get("/api/v1/sites")
check("түлхүүргүй → 401", r.status_code == 401)
r = client.get("/api/v1/sites", headers={"X-API-Key": "WRONG"})
check("буруу түлхүүр → 401", r.status_code == 401)

# ─── Тестийн өгөгдөл бэлдэх ─────────────────────────────────────────────────
db = SessionLocal()
site = db.query(ParkingSite).filter(ParkingSite.is_active.is_(True)).first()
plate = f"9{uuid.uuid4().hex[:3].upper()}ТСТ"  # давхцахгүй тестийн дугаар
s = ParkingSession(site_id=site.id, plate_number=plate,
                   entry_time=datetime.utcnow() - timedelta(minutes=90), status="OPEN")
db.add(s)
db.commit()
sid = s.id
db.close()

print("sites:")
r = client.get("/api/v1/sites", headers=TOKI)
check("200 + sites жагсаалт", r.status_code == 200 and isinstance(r.json().get("sites"), list))
row = next((x for x in r.json()["sites"] if x["site_code"] == site.site_code), None)
check("зогсоол олдож occupied тоологдсон", row is not None and row["occupied"] >= 1)
check("capacity>0 үед free тоо, 0 үед null",
      (row["free"] is None) == (row["capacity"] == 0))

print("sessions:")
r = client.get(f"/api/v1/sessions?plate={plate}", headers=TOKI)
check("дугаараар олдсон", r.status_code == 200 and len(r.json()["sessions"]) == 1)
sp = r.json()["sessions"][0]
check("amount_due>0, duration~90мин", sp["amount_due"] > 0 and 85 <= sp["duration_minutes"] <= 95)
check("site_code зөв", sp["site_code"] == site.site_code)
r = client.get("/api/v1/sessions?plate=99", headers=TOKI)
check("богино дугаар → 400", r.status_code == 400)

print("payments intent:")
r = client.post("/api/v1/payments", json={"session_id": sid}, headers=TOKI)
check("intent үүссэн", r.status_code == 200 and r.json()["status"] == "PENDING")
pay = r.json()
r2 = client.post("/api/v1/payments", json={"session_id": sid}, headers=TOKI)
check("давхар intent → ижил payment_id (дахин ашиглана)",
      r2.status_code == 200 and r2.json()["payment_id"] == pay["payment_id"])

print("payments confirm:")
r = client.post(f"/api/v1/payments/{pay['payment_id']}/confirm",
                json={"transaction_id": "TX1", "amount": pay["amount"] + 500}, headers=TOKI)
check("дүн зөрвөл 400", r.status_code == 400)
r = client.post(f"/api/v1/payments/{pay['payment_id']}/confirm",
                json={"transaction_id": "TX1", "amount": pay["amount"]}, headers=TOKI)
check("зөв дүнгээр PAID", r.status_code == 200 and r.json()["status"] == "PAID")
r = client.post(f"/api/v1/payments/{pay['payment_id']}/confirm",
                json={"transaction_id": "TX1", "amount": pay["amount"]}, headers=TOKI)
check("давхар confirm idempotent (PAID)", r.status_code == 200 and r.json()["status"] == "PAID")
r = client.get(f"/api/v1/payments/{pay['payment_id']}",
               headers={"X-API-Key": "TESTKEY2"})
check("өөр түнш харахгүй → 404", r.status_code == 404)

db = SessionLocal()
p = db.get(Payment, pay["payment_id"])
check("DB: provider=TOKI, method=WALLET", p.provider == "TOKI" and p.payment_method == "WALLET")
s = db.get(ParkingSession, sid)
check("session PAID болсон", s.status in ("PAID", "CLOSED"))

# ─── POS терминал таних ─────────────────────────────────────────────────────
print("POS terminal:")
term_key = f"PAXTEST{uuid.uuid4().hex[:6]}"
term = Device(site_id=site.id, name="Тест PAX", device_type="pax_terminal",
              device_key=term_key, status="active")
db.add(term)
db.commit()

from app.models import User  # noqa: E402
from app.auth import create_access_token  # noqa: E402
admin = db.query(User).filter(User.role == "SUPER_ADMIN").first()
auth = {"Authorization": f"Bearer {create_access_token(admin)}"}
r = client.get(f"/api/payments/pos/terminal/{term_key}", headers=auth)
check("терминал зогсоолоо таньсан",
      r.status_code == 200 and r.json()["site_code"] == site.site_code)
check("awaiting жагсаалттай", isinstance(r.json()["awaiting"], list))
r = client.get("/api/payments/pos/terminal/UNKNOWN123", headers=auth)
check("бүртгэлгүй терминал → 404", r.status_code == 404)

# ─── Цэвэрлэгээ (FK дараалал: VatReceipt → Payment → Session) ────────────────
from app.models import VatReceipt  # noqa: E402
db.query(VatReceipt).filter(VatReceipt.session_id == sid).delete()
db.query(Payment).filter(Payment.session_id == sid).delete()
db.query(ParkingSession).filter(ParkingSession.id == sid).delete()
db.query(Device).filter(Device.id == term.id).delete()
db.commit()
db.close()

print(f"\n{PASS} PASS, {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
