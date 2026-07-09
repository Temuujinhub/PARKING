"""QPay v2 + Ebarimt 3.0 интеграцийн тест (pytest шаардлагагүй, шууд ажиллана).

    cd backend && venv/bin/python tests/test_qpay_ebarimt.py

httpx.MockTransport-аар бодит QPay-г дуурайж (qpay_mock=False), нэхэмжлэл→төлбөр
шалгах→e-Barimt үүсгэх бүтэн урсгалыг шалгана. Мөн НӨАТ тооцоолол, mock горимыг шалгана.
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx

from app.config import settings
from app.services import qpay

PASS, FAIL = 0, 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  <<< FAIL")


# ─── Fake QPay сервер (MockTransport) ───
_captured = {}


def _handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    body = json.loads(request.content) if request.content else {}
    if url.endswith("/auth/token"):
        return httpx.Response(200, json={"access_token": "ACCESS_X", "refresh_token": "REFRESH_X",
                                         "expires_in": 3600})
    if url.endswith("/v2/invoice"):
        _captured["invoice"] = body
        return httpx.Response(200, json={
            "invoice_id": "d50f49f2-9032-4a74-8929-530531f28f63",
            "qr_text": "0002010102121531...C66D",
            "qr_image": "iVBORw0KGgo=",
            "urls": [{"name": "qPay wallet", "description": "qPay хэтэвч",
                      "logo": "https://x/logo.png", "link": "qpaywallet://q?qPay_QRcode=000201"}],
        })
    if url.endswith("/payment/check"):
        _captured["check"] = body
        return httpx.Response(200, json={
            "count": 1, "paid_amount": 5000,
            "rows": [{"payment_id": "019276866891878", "payment_status": "PAID", "amount": 5000}],
        })
    if url.endswith("/ebarimt_v3/create"):
        _captured["ebarimt"] = body
        return httpx.Response(200, json={
            "id": "ca48461c-0b85-438d-b8f4-8b46582a668c",
            "ebarimt_receiver_type": body.get("ebarimt_receiver_type"),
            "ebarimt_qr_data": "138431709437501143529757963639945461820281573286",
            "ebarimt_lottery": "HV 83198235",
            "ebarimt_receipt_id": "030101065006000090690000210005595",
            "barimt_status": "REGISTERED",
            "barimt_status_date": "2026-07-09T05:45:42.945Z",
            "status": True,
        })
    return httpx.Response(404, json={"error": "not found", "url": url})


class _MockClient(httpx.AsyncClient):
    def __init__(self, *a, **kw):
        kw["transport"] = httpx.MockTransport(_handler)
        super().__init__(*a, **kw)


async def run():
    # ── 1. НӨАТ тооцоолол (docs: 50₮ → 4.5454; screenshot: 5000₮ → 454.5454) ──
    print("1. НӨАТ тооцоолол (_vat_of, build_lines)")
    check("50₮-ийн НӨАТ = 4.5454", qpay._vat_of(50) == 4.5454)
    check("100₮-ийн НӨАТ = 9.0909", qpay._vat_of(100) == 9.0909)
    check("5000₮-ийн НӨАТ = 454.5454", qpay._vat_of(5000) == 454.5454)

    lines = qpay.build_lines([{"description": "Зогсоолын үйлчилгээ — 1234УБА", "unit_price": 5000}])
    check("build_lines нэг мөр үүсгэв", len(lines) == 1)
    check("line_unit_price = 5000.00", lines[0]["line_unit_price"] == "5000.00")
    check("мөрд VAT татвар багтсан", lines[0]["taxes"][0]["amount"] == 454.5454)
    check("classification_code тавигдсан", lines[0]["classification_code"] == settings.qpay_classification_code)

    # tax_type=2 (чөлөөлөгдөх) үед VAT тооцохгүй
    settings.qpay_tax_type = "2"
    lines2 = qpay.build_lines([{"description": "x", "unit_price": 5000}])
    check("tax_type=2 үед VAT мөр байхгүй", "taxes" not in lines2[0])
    settings.qpay_tax_type = "1"

    # ── 2. Бодит QPay урсгал (MockTransport) ──
    print("2. Бодит QPay урсгал — invoice → check → ebarimt")
    settings.qpay_mock = False
    settings.qpay_sandbox = False
    settings.qpay_username = "EASY_2PARKING"
    settings.qpay_password = "test-secret"  # MockTransport тул жинхэнэ нууц үг шаардлагагүй
    settings.qpay_invoice_code = "EB_EASY_2PARKING_INVOICE"
    qpay.httpx.AsyncClient = _MockClient
    qpay._token.update({"access": None, "refresh": None})

    inv = await qpay.create_invoice(
        "SITE01-20260709-ABCD1234", "Зогсоолын төлбөр — 1234УБА", "terminal_SITE01",
        "https://test.easy-parking.mn/api/payments/qpay/webhook?payment_id=P1", lines,
    )
    check("invoice_id ирсэн", inv["invoice_id"] == "d50f49f2-9032-4a74-8929-530531f28f63")
    check("qr_image ирсэн", inv["qr_image"] == "iVBORw0KGgo=")
    check("deep_link (qpay) сонгосон", inv["deep_link"].startswith("qpaywallet://"))
    check("invoice payload-д invoice_code зөв", _captured["invoice"]["invoice_code"] == "EB_EASY_2PARKING_INVOICE")
    check("invoice payload-д tax_type=1", _captured["invoice"]["tax_type"] == "1")
    check("invoice payload-д district_code байгаа", "district_code" in _captured["invoice"])
    check("invoice payload-д lines байгаа", len(_captured["invoice"]["lines"]) == 1)

    chk = await qpay.check_payment(inv["invoice_id"])
    check("check paid=True", chk["paid"] is True)
    check("check g_payment_id гарч ирсэн", chk["payment_id"] == "019276866891878")
    check("check object_id=invoice_id", _captured["check"]["object_id"] == inv["invoice_id"])

    eb = await qpay.create_ebarimt(chk["payment_id"], "CITIZEN")
    check("ebarimt payload payment_id зөв", _captured["ebarimt"]["payment_id"] == "019276866891878")
    check("ebarimt receiver_type=CITIZEN", _captured["ebarimt"]["ebarimt_receiver_type"] == "CITIZEN")
    check("ebarimt billId=ДДТД(receipt_id)", eb["billId"] == "030101065006000090690000210005595")
    check("ebarimt lottery mapping", eb["lottery"] == "HV 83198235")
    check("ebarimt qrData mapping", eb["qrData"].startswith("1384317"))
    check("ebarimt status=SUCCESS", eb["status"] == "SUCCESS")

    # COMPANY (ААН) — регистрийг ebarimt_receiver болгон дамжуулна
    eb2 = await qpay.create_ebarimt(chk["payment_id"], "COMPANY", receiver="1234567")
    check("COMPANY үед ebarimt_receiver=регистр", _captured["ebarimt"]["ebarimt_receiver"] == "1234567")

    # ── 3. Mock горим (QPay холбогдоогүй) ──
    print("3. Mock горим")
    settings.qpay_mock = True
    inv_m = await qpay.create_invoice("X", "d", "t", "cb", lines)
    check("mock invoice_id MOCK-INV угтвартай", inv_m["invoice_id"].startswith("MOCK-INV-"))
    check("mock=True тэмдэглэгдсэн", inv_m["mock"] is True)
    eb_m = await qpay.create_ebarimt("MOCK-PAY-1", "CITIZEN")
    check("mock ebarimt billId (ДДТД) үүссэн", bool(eb_m["billId"]))
    check("mock ebarimt lottery үүссэн", bool(eb_m["lottery"]))
    check("mock ebarimt qrData үүссэн", bool(eb_m["qrData"]))

    print(f"\n{'='*40}\nҮР ДҮН: {PASS} passed, {FAIL} failed")
    return FAIL == 0


if __name__ == "__main__":
    ok = asyncio.run(run())
    sys.exit(0 if ok else 1)
