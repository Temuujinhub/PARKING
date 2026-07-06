"""e-Barimt (НӨАТ баримт) интеграц.

ebarimt_mock=True үед бодит НӨАТ систем рүү илгээхгүй — туршилтын баримт үүсгэнэ.
Бодит горимд PosAPI суулгаж PARKING_EBARIMT_* тохиргоог хийнэ.
"""
import uuid
from datetime import datetime

import httpx

from ..config import settings


async def create_receipt(amount: float, vat_amount: float, payment_type: str,
                         customer_tin: str | None = None) -> dict:
    if settings.ebarimt_mock:
        return {
            "id": f"EB-MOCK-{uuid.uuid4().hex[:12].upper()}",
            "lottery": f"MK{uuid.uuid4().hex[:6].upper()}",
            "qrData": "",
            "date": datetime.utcnow().isoformat(),
            "mock": True,
        }
    payload = {
        "amount": round(amount, 2),
        "vat": round(vat_amount, 2),
        "cashAmount": round(amount, 2) if payment_type == "CASH" else 0,
        "nonCashAmount": 0 if payment_type == "CASH" else round(amount, 2),
        "paymentType": payment_type,
        "merchantTin": settings.ebarimt_merchant_tin,
        "posNo": settings.ebarimt_pos_no,
        "branchNo": settings.ebarimt_branch_no,
        "districtCode": settings.ebarimt_district_code,
        "customerNo": customer_tin or "",
        "items": [{
            "name": "Зогсоолын үйлчилгээ",
            "qty": 1,
            "unitPrice": round(amount, 2),
            "totalAmount": round(amount, 2),
            "vat": round(vat_amount, 2),
            "cityTax": 0,
        }],
    }
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(f"{settings.ebarimt_base_url}/put", json=payload)
        resp.raise_for_status()
        return resp.json()
