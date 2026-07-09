"""e-Barimt (НӨАТ баримт) интеграц — ЦАХИМ ТӨЛБӨРИЙН БАРИМТЫН СИСТЕМ POS API 3.0.

Лавлах: https://developer.itc.gov.mn/docs/ebarimt-api/ (POS API 3.0)

Бодит горимд татварын байгууллагаас олгосон PosAPI сервисийг сервер дээр суулгаж
(default: http://localhost:7080/rest), PARKING_EBARIMT_MOCK=false болгоно.

Хариултын формат (3.0):
{
  "status": "SUCCESS",
  "billId": "1234567890123456789012345678901234567890",
  "lottery": "65432101",
  "qrData": "1234567890...543210",   <- QR код болгон хэвлэнэ/харуулна
  "date": "2026-07-06 22:15:35"
}
"""
import random
import time
from datetime import datetime

import httpx

from ..config import settings

# ТЕГ-ын шаардлага №11: "QR код хадгалахгүй" — qrData-г DB-д хадгалахгүй,
# зөвхөн түр санах ойд (1 цаг) байршуулж баримт үзүүлэх/хэвлэхэд ашиглана.
_qr_cache: dict[str, tuple[str, float]] = {}
_QR_TTL = 3600


def cache_qr(payment_id: str, qr_data: str | None):
    if qr_data:
        _qr_cache[payment_id] = (qr_data, time.monotonic() + _QR_TTL)


def get_cached_qr(payment_id: str) -> str | None:
    item = _qr_cache.get(payment_id)
    if not item:
        return None
    qr, exp = item
    if time.monotonic() > exp:
        _qr_cache.pop(payment_id, None)
        return None
    return qr


def _mock_receipt() -> dict:
    bill_id = "".join(random.choices("0123456789", k=40))
    lottery = "".join(random.choices("0123456789", k=8))
    return {
        "status": "SUCCESS",
        "billId": bill_id,
        "lottery": lottery,
        "qrData": bill_id + lottery[:6],
        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "mock": True,
    }


def _build_payload(amount: float, vat_amount: float, payment_type: str,
                   customer_tin: str | None) -> dict:
    """POS API 3.0 баримт үүсгэх payload. НӨАТ үнэд багтсан гэж үзнэ."""
    amount = round(amount, 2)
    vat = round(vat_amount, 2)
    item = {
        "name": "Зогсоолын үйлчилгээ",
        "barCodeType": "UNDEFINED",
        "classificationCode": settings.ebarimt_classification_code,
        "measureUnit": "удаа",
        "qty": 1,
        "unitPrice": amount,
        "totalAmount": amount,
        "totalVAT": vat,
        "totalCityTax": 0,
    }
    return {
        "totalAmount": amount,
        "totalVAT": vat,
        "totalCityTax": 0,
        "districtCode": settings.ebarimt_district_code,
        "merchantTin": settings.ebarimt_merchant_tin,
        "branchNo": settings.ebarimt_branch_no,
        "posNo": settings.ebarimt_pos_no,
        # Байгууллагын ТТД өгвөл B2B, үгүй бол иргэний B2C баримт
        "type": "B2B_RECEIPT" if customer_tin else "B2C_RECEIPT",
        "customerTin": customer_tin or "",
        "receipts": [{
            "totalAmount": amount,
            "totalVAT": vat,
            "totalCityTax": 0,
            "taxType": "VAT_ABLE",
            "merchantTin": settings.ebarimt_merchant_tin,
            "items": [item],
        }],
        "payments": [{
            "code": "CASH" if payment_type == "CASH" else "PAYMENT_CARD",
            "status": "PAID",
            "paidAmount": amount,
        }],
    }


async def create_receipt(amount: float, vat_amount: float, payment_type: str,
                         customer_tin: str | None = None) -> dict:
    """Баримт үүсгэнэ. Буцаах: {status, billId, lottery, qrData, date}."""
    if settings.ebarimt_mock:
        return _mock_receipt()

    payload = _build_payload(amount, vat_amount, payment_type, customer_tin)
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(f"{settings.ebarimt_posapi_url}/receipt", json=payload)
        resp.raise_for_status()
        data = resp.json()
    # 3.0 хувилбараас хамаарч billId эсвэл id гэж ирж болно
    return {
        "status": data.get("status", "SUCCESS"),
        "billId": data.get("billId") or data.get("id"),
        "lottery": data.get("lottery"),
        "qrData": data.get("qrData"),
        "date": data.get("date"),
        "raw": data,
    }


async def get_information() -> dict:
    """PosAPI getInformation — сугалааны үлдэгдэл, илгээгээгүй мэдээний байдал (шаардлага №6)."""
    if settings.ebarimt_mock:
        return {
            "posId": 10000001, "posNo": settings.ebarimt_pos_no,
            "operatorTin": settings.ebarimt_merchant_tin or "0000000",
            "leftLotteries": 9500,          # үлдсэн сугалааны тоо
            "lastSentDate": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "unsentCount": 0,               # илгээгдээгүй баримтын тоо
            "mock": True,
        }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(f"{settings.ebarimt_posapi_url}/info")
        resp.raise_for_status()
        return resp.json()


import os

_LAST_SEND_FILE = os.path.join(os.path.dirname(__file__), "..", "..", ".last_ebarimt_send")


def record_send():
    """ТЕГ-т мэдээ илгээсэн хугацааг файлд тэмдэглэнэ (restart-д тэсвэртэй, health-д харна)."""
    try:
        with open(_LAST_SEND_FILE, "w") as f:
            f.write(str(int(time.time())))
    except Exception:  # noqa: BLE001
        pass


def last_send_at() -> int | None:
    """ТЕГ-т сүүлд мэдээ илгээсэн epoch (health мониторинг). Хэзээ ч илгээгээгүй бол None."""
    try:
        with open(_LAST_SEND_FILE) as f:
            return int(f.read().strip())
    except Exception:  # noqa: BLE001
        return None


async def send_data() -> dict:
    """PosAPI sendData — цугларсан баримтуудыг ТЕГ-ын нэгдсэн системд илгээх
    (шаардлага №4 автомат — өдөр бүр, №5 гараар — Ибаримт хуудасны товч)."""
    if settings.ebarimt_mock:
        record_send()
        return {"success": True, "message": "MOCK: мэдээ илгээгдлээ", "mock": True}
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(f"{settings.ebarimt_posapi_url}/sendData")
        resp.raise_for_status()
    record_send()
    return resp.json()


async def delete_receipt(bill_id: str, date: str) -> bool:
    """Баримт буцаах (DELETE /rest/receipt)."""
    if settings.ebarimt_mock:
        return True
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.request(
            "DELETE", f"{settings.ebarimt_posapi_url}/receipt",
            json={"id": bill_id, "date": date},
        )
    return resp.status_code == 200
