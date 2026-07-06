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
from datetime import datetime

import httpx

from ..config import settings


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
