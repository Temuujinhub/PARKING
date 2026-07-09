"""payments_router-ийн e-Barimt салаалалтын логикийг fake DB-ээр шалгана (DB шаардлагагүй).

    cd backend && venv/bin/python tests/test_finalize_paid.py

Шалгах зүйл:
  - QR (QPAY) төлбөр → QPay ebarimt_v3-аар баримт үүснэ, CITIZEN-д сугалаа, COMPANY-д сугалаагүй
  - Бэлэн (CASH) төлбөр → локал PosAPI-аар баримт үүснэ
  - _print_payload зөв мөрүүд + qr_data буцаана
"""
import asyncio
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings
from app.routers import payments_router as pr
from app.services import ebarimt, qpay

PASS = FAIL = 0


def check(name, cond):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗ <<< FAIL'} {name}")


class FakePayment:
    def __init__(self, **kw):
        self.id = kw.get("id", "PAY1")
        self.session_id = "SESS1"
        self.provider = kw.get("provider", "QPAY")
        self.payment_method = kw.get("payment_method", "QR")
        self.provider_payment_id = kw.get("provider_payment_id")
        self.ebarimt_receiver_type = kw.get("ebarimt_receiver_type")
        self.customer_tin = kw.get("customer_tin")
        self.amount = 5000
        self.vat_amount = 454
        self.status = "PENDING"
        self.paid_at = None
        self.raw_payload = {}
        self.duration_minutes = None


class FakeSession:
    plate_number = "1234УБА"
    entry_time = __import__("datetime").datetime(2026, 7, 9, 11, 44)
    duration_minutes = 175


class FakeQuery:
    def __init__(self, store): self.store = store
    def filter(self, *a, **k): return self
    def first(self): return self.store.get("receipt")


class FakeDB:
    def __init__(self): self.added = []; self.store = {}
    def get(self, model, _id): return FakeSession()
    def add(self, obj):
        self.added.append(obj)
        if obj.__class__.__name__ == "VatReceipt":
            self.store["receipt"] = obj
    def query(self, *a, **k): return FakeQuery(self.store)
    def commit(self): pass


async def run():
    settings.qpay_mock = True   # qpay.create_ebarimt / ebarimt.create_receipt mock буцаана
    settings.qpay_ebarimt = True

    # mark_paid_and_open-ыг хаалт руу хүрэхээс сэргийлж fake болгоно
    called = {"barrier": 0}
    async def _fake_mark(db, session, grace_minutes=None): called["barrier"] += 1
    pr.mark_paid_and_open = _fake_mark

    # ── 1. QR (QPAY) + CITIZEN → QPay ebarimt_v3 ──
    print("1. QPAY / CITIZEN")
    db = FakeDB()
    p = FakePayment(provider="QPAY", payment_method="QR",
                    provider_payment_id="019276866891878", ebarimt_receiver_type="CITIZEN")
    await pr._finalize_paid(db, p)
    r = db.store["receipt"]
    check("status PAID болов", p.status == "PAID")
    check("VatReceipt-д ДДТД(ebarimt_id) орсон", bool(r.ebarimt_id))
    check("CITIZEN-д сугалаа орсон", bool(r.lottery_code))
    check("receipt статус SENT", r.status == "SENT")
    check("хаалт нээх дуудагдсан", called["barrier"] == 1)
    check("qrData кэшлэгдсэн", bool(ebarimt.get_cached_qr(p.id)))

    # ── 2. QR (QPAY) + COMPANY → сугалаагүй ──
    print("2. QPAY / COMPANY (ААН)")
    db = FakeDB()
    p = FakePayment(id="PAY2", provider="QPAY", provider_payment_id="019276866891878",
                    ebarimt_receiver_type="COMPANY", customer_tin="1234567")
    await pr._finalize_paid(db, p)
    check("COMPANY-д сугалаа ОЛГОГДООГҮЙ", db.store["receipt"].lottery_code is None)
    check("customer_tin хадгалагдсан", db.store["receipt"].customer_tin == "1234567")

    # ── 3. Бэлэн (CASH) → локал PosAPI ебаримт (QPay биш) ──
    print("3. CASH → локал PosAPI")
    routed = {"via": None}
    orig_qpay_eb, orig_local = qpay.create_ebarimt, ebarimt.create_receipt
    async def _spy_qpay(*a, **k): routed["via"] = "qpay"; return await orig_qpay_eb(*a, **k)
    async def _spy_local(*a, **k): routed["via"] = "local"; return await orig_local(*a, **k)
    qpay.create_ebarimt, ebarimt.create_receipt = _spy_qpay, _spy_local
    db = FakeDB()
    p = FakePayment(id="PAY3", provider="CASH", payment_method="CASH")
    await pr._finalize_paid(db, p)
    check("CASH нь локал PosAPI-аар явсан", routed["via"] == "local")

    # QPAY дахин → qpay branch
    db = FakeDB()
    p = FakePayment(id="PAY4", provider="QPAY", provider_payment_id="X", ebarimt_receiver_type="CITIZEN")
    await pr._finalize_paid(db, p)
    check("QPAY нь qpay ebarimt_v3-аар явсан", routed["via"] == "qpay")
    qpay.create_ebarimt, ebarimt.create_receipt = orig_qpay_eb, orig_local

    # ── 4. _print_payload ──
    print("4. _print_payload (POS хэвлэх)")
    db = FakeDB()
    p = FakePayment(id="PAY5", provider="QPAY", provider_payment_id="X", ebarimt_receiver_type="CITIZEN")
    await pr._finalize_paid(db, p)
    pl = pr._print_payload(db, p)
    check("print_data.lines-д гарчиг байна", pl["print_data"]["lines"][0] == "ЗОГСООЛЫН ТӨЛБӨРИЙН БАРИМТ")
    check("print_data-д дугаар багтсан", any("1234УБА" in ln for ln in pl["print_data"]["lines"]))
    check("print_data-д ДДТД багтсан", any("ДДТД" in ln for ln in pl["print_data"]["lines"]))
    check("qr_data буцсан", bool(pl["qr_data"]))
    check("lottery_code буцсан", bool(pl["lottery_code"]))

    print(f"\n{'='*40}\nҮР ДҮН: {PASS} passed, {FAIL} failed")
    return FAIL == 0


if __name__ == "__main__":
    sys.exit(0 if asyncio.run(run()) else 1)
