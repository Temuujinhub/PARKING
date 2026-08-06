"""Дансаар (TRANSFER) төлбөрийн урсгал — e-Barimt зам ба эрхийн шалгалт.

    cd backend && venv/bin/python tests/test_transfer_payment.py

Шалгах зүйл:
  - TRANSFER төлбөр локал PosAPI-аар баримт үүсгэнэ (QPay ebarimt_v3 биш)
  - eBarimt 3.0-д шилжүүлгийн код байхгүй тул payment_type=CASH-аар илгээгдэнэ
  - CARD/QR хуучин зам өөрчлөгдөөгүй (CARD хэвээр)
  - pay_transfer permission ALL_MODULES-д бүртгэлтэй (эрхийн матрицад гарна)
  - ebarimt._build_payload: CASH→CASH код, бусад→PAYMENT_CARD
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings
from app.auth import ALL_MODULES, ROLE_PERMISSIONS
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
        self.provider = kw.get("provider", "TRANSFER")
        self.payment_method = kw.get("payment_method", "TRANSFER")
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
    entry_time = __import__("datetime").datetime(2026, 8, 6, 11, 44)
    duration_minutes = 60


class FakeQuery:
    def __init__(self, store): self.store = store
    def filter(self, *a, **k): return self
    def first(self): return self.store.get("receipt")
    def all(self): return []


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
    settings.qpay_mock = True
    settings.qpay_ebarimt = True
    settings.ebarimt_mock = True

    async def _fake_mark(db, session, grace_minutes=None): pass
    pr.mark_paid_and_open = _fake_mark

    # create_receipt-ийн payment_type аргументыг барина
    seen = {"via": None, "ptype": None}
    orig_qpay_eb, orig_local = qpay.create_ebarimt, ebarimt.create_receipt
    async def _spy_qpay(*a, **k):
        seen["via"] = "qpay"; return await orig_qpay_eb(*a, **k)
    async def _spy_local(amount, vat, ptype, **k):
        seen["via"], seen["ptype"] = "local", ptype
        return await orig_local(amount, vat, ptype, **k)
    qpay.create_ebarimt, ebarimt.create_receipt = _spy_qpay, _spy_local

    print("TRANSFER → локал PosAPI, CASH кодоор:")
    db = FakeDB()
    p = FakePayment()
    await pr._finalize_paid(db, p)
    check("status PAID болов", p.status == "PAID")
    check("локал PosAPI-аар явсан", seen["via"] == "local")
    check("payment_type=CASH (шилжүүлэгт тусдаа код байхгүй)", seen["ptype"] == "CASH")
    check("VatReceipt SENT", db.store["receipt"].status == "SENT")

    print("\nCARD хуучин зам өөрчлөгдөөгүй:")
    db = FakeDB()
    p = FakePayment(id="PAY2", provider="POS", payment_method="CARD")
    await pr._finalize_paid(db, p)
    check("CARD → payment_type=CARD хэвээр", seen["ptype"] == "CARD")

    print("\nCASH хуучин зам:")
    db = FakeDB()
    p = FakePayment(id="PAY3", provider="CASH", payment_method="CASH")
    await pr._finalize_paid(db, p)
    check("CASH → payment_type=CASH хэвээр", seen["ptype"] == "CASH")

    qpay.create_ebarimt, ebarimt.create_receipt = orig_qpay_eb, orig_local

    print("\nЭрхийн бүртгэл:")
    check("pay_transfer ALL_MODULES-д бий", "pay_transfer" in ALL_MODULES)
    check("OPERATOR default-д pay_transfer БАЙХГҮЙ (гараар олгоно)",
          "pay_transfer" not in ROLE_PERMISSIONS["OPERATOR"])

    print("\nebarimt._build_payload код mapping:")
    pl = ebarimt._build_payload(1000, 91, "CASH", None)
    check("CASH → код CASH", pl["payments"][0]["code"] == "CASH")
    pl = ebarimt._build_payload(1000, 91, "CARD", None)
    check("CARD → код PAYMENT_CARD", pl["payments"][0]["code"] == "PAYMENT_CARD")

    print(f"\n{'='*40}\nҮР ДҮН: {PASS} passed, {FAIL} failed")
    return FAIL == 0


if __name__ == "__main__":
    sys.exit(0 if asyncio.run(run()) else 1)
