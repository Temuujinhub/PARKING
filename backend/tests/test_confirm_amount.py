"""QPay төлбөрийн дүн зөрсөн үеийн шийдэл — илүү төлсөн машиныг хорихгүй.

    cd backend && venv/bin/python tests/test_confirm_amount.py

Бодит алдаа (production, 2026-07-27, Моннис билдинг): 2000₮-ийн нэхэмжлэлд
жолооч 2181.82₮ төлсөн (QPay мерчантын НӨАТ тохиргооноос болж дүн дээр татвар
нэмэгдсэн). Систем «дүн зөрсөн» гэж REVIEW болгож ХААЛТЫГ НЭЭГЭЭГҮЙ — мөнгө нь
бүрэн ирсэн атлаа жолооч зогсоолд хоригдсон.

Дүрэм: ДУТУУ төлсөн бол REVIEW (оператор шалгана), ИЛҮҮ төлсөн бол гаргана.

Шалгах зүйл:
  - Яг таарсан дүн → PAID
  - 1₮ дотор зөрүү → PAID (бөөрөнхийлөлт)
  - Илүү төлсөн → PAID, хаалт нээгдэнэ (REVIEW БОЛОХГҮЙ)
  - Дутуу төлсөн → REVIEW, хаалт нээгдэхгүй
  - QPay төлөгдөөгүй гэвэл юу ч болохгүй
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings

settings.qpay_mock = False   # бодит урсгалын салаа шалгана
settings.qpay_ebarimt = False

from app.routers import payments_router  # noqa: E402
from app.services import qpay  # noqa: E402

PASS = FAIL = 0


def check(name, cond):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗ <<< FAIL'} {name}")


class FakePayment:
    def __init__(self, amount):
        self.id = "PAY-TEST"
        self.status = "PENDING"
        self.amount = amount
        self.provider = "QPAY"
        self.provider_invoice_id = "INV-1"
        self.provider_payment_id = None
        self.raw_payload = {}
        self.session = None
        self.session_id = None
        self.paid_at = None


class FakeDb:
    def __init__(self):
        self.commits = 0

    def commit(self):
        self.commits += 1


finalized: list = []


async def fake_finalize(db, payment, raw=None):
    payment.status = "PAID"
    finalized.append(payment.id)


payments_router._finalize_paid = fake_finalize


def run_case(invoice_amount, paid_amount, paid=True):
    finalized.clear()

    async def fake_check(invoice_id, acc=None):
        return {"paid": paid, "paid_amount": paid_amount, "rows": [],
                "payment_id": "GPAY-1", "raw": {}}

    qpay.check_payment = fake_check
    p = FakePayment(invoice_amount)
    db = FakeDb()
    ok = asyncio.get_event_loop().run_until_complete(
        payments_router._confirm_qpay(db, p))
    return ok, p, bool(finalized)


print("Яг таарсан дүн:")
ok, p, fin = run_case(2000, 2000)
check("PAID болов", ok is True and p.status == "PAID")
check("хаалт нээх урсгал ажилласан", fin is True)

print("\n1₮ дотор зөрүү (бөөрөнхийлөлт):")
ok, p, fin = run_case(2000, 2000.5)
check("PAID болов", ok is True and p.status == "PAID")

print("\nИЛҮҮ төлсөн (2000 → 2181.82, НӨАТ дээр нь нэмэгдсэн):")
ok, p, fin = run_case(2000, 2181.82)
check("REVIEW БОЛООГҮЙ", p.status != "REVIEW")
check("PAID болов", ok is True and p.status == "PAID")
check("хаалт нээх урсгал ажилласан", fin is True)

print("\nДУТУУ төлсөн (2000 → 1500):")
ok, p, fin = run_case(2000, 1500)
check("REVIEW болов", p.status == "REVIEW")
check("PAID болоогүй", ok is False)
check("хаалт нээгдээгүй", fin is False)

print("\nQPay төлөгдөөгүй гэвэл:")
ok, p, fin = run_case(2000, 0, paid=False)
check("юу ч болоогүй", ok is False and p.status == "PENDING" and fin is False)

print(f"\n{PASS} PASS, {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
