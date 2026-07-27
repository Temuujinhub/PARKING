"""Зогсоолын QPay дансыг машингүйгээр турших endpoint.

    cd backend && venv/bin/python tests/test_qpay_site_test_endpoint.py

Яагаад чухал вэ: түрээслэгч бүрийн данс зөв холбогдсон эсэхийг ГО-LIVE-ийн ӨМНӨ
шалгах цорын ганц арга. Буруу данс руу төлбөр орвол мөнгө өөр байгууллагад очно.

Шалгах зүйл:
  - mock горимд байхад туршилт зөвшөөрөхгүй (хуурамч амжилтаар төөрөгдүүлэхгүй)
  - Дүнгийн хязгаар (1–10000₮) мөрдөгдөнө
  - Нэхэмжлэл нь ЗОГСООЛЫН дансаар үүснэ (глобалаар биш)
  - using_own_account нь өөрийн данстай эсэхийг үнэн зөв хэлнэ
  - Төлөгдөөгүй үед paid=False
  - Төлөгдсөн үед e-Barimt-ын ДДТД + баримт олгосон ТТД буцаана
  - e-Barimt унасан ч төлбөрийн үр дүнг нуухгүй (ebarimt_error)
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings

settings.qpay_mock = True
settings.qpay_username = "GLOBAL_USER"
settings.qpay_password = "GLOBAL_PASS"
settings.qpay_invoice_code = "GLOBAL_INVOICE"
settings.qpay_district_code = "2318"

from fastapi import HTTPException  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models import ParkingSite, User  # noqa: E402
from app.routers import admin_router  # noqa: E402
from app.services import qpay  # noqa: E402

PASS = FAIL = 0


def check(name, cond):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗ <<< FAIL'} {name}")


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def expect_400(coro, needle=""):
    try:
        run(coro)
        return False
    except HTTPException as e:
        return e.status_code == 400 and (needle in str(e.detail) if needle else True)


CODE = "ZZQPAYTEST"
db = SessionLocal()
db.query(ParkingSite).filter(ParkingSite.site_code == CODE).delete()
db.commit()
site = ParkingSite(name="QPay тест зогсоол", site_code=CODE, zone_code="A", capacity=0)
db.add(site)
db.commit()
user = User(username="tester", password_hash="x", role="ADMIN")

# ── QPay-г бүрэн орлуулна (бодит сүлжээ рүү огт хандахгүй) ──
calls = {}


async def fake_invoice(inv_no, desc, receiver, callback, lines, receiver_data=None, acc=None):
    calls["invoice_acc"] = acc
    calls["invoice_no"] = inv_no
    return {"invoice_id": "INV-1", "qr_text": "qr://x", "qr_image": "BASE64",
            "deep_link": "qpay://x", "urls": []}


async def fake_check(invoice_id, acc=None):
    calls["check_acc"] = acc
    return dict(calls.get("check_result") or {"paid": False})


async def fake_ebarimt(payment_id, receiver_type="CITIZEN", receiver=None,
                       district_code=None, acc=None):
    calls["eb_acc"] = acc
    if calls.get("eb_raise"):
        raise RuntimeError("ebarimt татгалзав")
    return {"billId": "DDTD-123", "lottery": "AA 111", "qrData": "QRDATA",
            "raw": {"merchant_register": "5395305", "ebarimt_status": "REGISTERED"}}


qpay.create_invoice, qpay.check_payment, qpay.create_ebarimt = (
    fake_invoice, fake_check, fake_ebarimt)

try:
    print("mock горимд туршилт хориглоно:")
    check("mock=true үед 400",
          expect_400(admin_router.qpay_test_invoice(site.id, {}, db, user), "mock"))

    print("\nБодит данс тохируулсан үед:")
    site.qpay_username = "MONNIS_PROPERTIES"
    site.qpay_password = "SECRET"
    site.qpay_invoice_code = "MONNIS_PROPERTIES_INVOICE"
    site.qpay_district_code = "2606"
    db.commit()

    check("дүн 0 бол 400", expect_400(admin_router.qpay_test_invoice(site.id, {"amount": 0}, db, user)))
    check("дүн 99999 бол 400",
          expect_400(admin_router.qpay_test_invoice(site.id, {"amount": 99999}, db, user)))

    r = run(admin_router.qpay_test_invoice(site.id, {"amount": 10}, db, user))
    check("нэхэмжлэл үүснэ", r["invoice_id"] == "INV-1")
    check("ЗОГСООЛЫН мерчантаар", r["merchant"] == "MONNIS_PROPERTIES")
    check("зогсоолын invoice_code", r["invoice_code"] == "MONNIS_PROPERTIES_INVOICE")
    check("зогсоолын дүүрэг", r["district_code"] == "2606")
    check("using_own_account=True", r["using_own_account"] is True)
    check("дамжуулсан acc нь зогсоолынх", calls["invoice_acc"].username == "MONNIS_PROPERTIES")
    check("acc бодит горимд (mock=False)", calls["invoice_acc"].mock is False)
    check("гүйлгээний дугаар TEST-ээр эхэлнэ", calls["invoice_no"].startswith(f"TEST-{CODE}"))

    print("\nТөлбөр шалгах:")
    r = run(admin_router.qpay_test_check(site.id, {"invoice_id": "INV-1"}, db, user))
    check("төлөгдөөгүй бол paid=False", r["paid"] is False)
    check("check мөн зогсоолын дансаар", calls["check_acc"].username == "MONNIS_PROPERTIES")
    check("invoice_id хоосон бол 400",
          expect_400(admin_router.qpay_test_check(site.id, {}, db, user)))

    calls["check_result"] = {"paid": True, "paid_amount": 10.0, "payment_id": "GPAY-9"}
    r = run(admin_router.qpay_test_check(site.id, {"invoice_id": "INV-1"}, db, user))
    check("төлөгдсөн бол paid=True", r["paid"] is True)
    check("ДДТД буцаана", r["ebarimt_id"] == "DDTD-123")
    check("сугалаа буцаана", r["lottery"] == "AA 111")
    check("баримт олгосон ТТД буцаана", r["merchant_register"] == "5395305")
    check("e-Barimt мөн зогсоолын дансаар", calls["eb_acc"].username == "MONNIS_PROPERTIES")

    print("\ne-Barimt унасан ч төлбөрийн үр дүн нуугдахгүй:")
    calls["eb_raise"] = True
    r = run(admin_router.qpay_test_check(site.id, {"invoice_id": "INV-1"}, db, user))
    check("paid=True хэвээр", r["paid"] is True)
    check("ebarimt_ok=False", r["ebarimt_ok"] is False)
    check("алдааны шалтгаан харагдана", "татгалзав" in (r.get("ebarimt_error") or ""))
    calls["eb_raise"] = False

    print("\nӨөрийн дансгүй зогсоол (глобал руу уналт):")
    site.qpay_username = site.qpay_password = None
    db.commit()
    settings.qpay_mock = False  # глобал данс бодит гэж үзье
    r = run(admin_router.qpay_test_invoice(site.id, {"amount": 10}, db, user))
    check("using_own_account=False гэж АНХААРУУЛНА", r["using_own_account"] is False)
    check("глобал мерчант ашиглана", r["merchant"] == "GLOBAL_USER")
finally:
    db.query(ParkingSite).filter(ParkingSite.site_code == CODE).delete()
    db.commit()
    db.close()

print(f"\n{PASS} PASS, {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
