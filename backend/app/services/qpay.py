"""QPay v2 интеграц.

qpay_mock=True үед бодит QPay руу хандахгүй — туршилтын QR/invoice буцаана.
Бодит горимд: PARKING_QPAY_MOCK=false, PARKING_QPAY_USERNAME/PASSWORD/INVOICE_CODE
тохируулна (QPay merchant гэрээнээс).
"""
import base64
import uuid
from datetime import datetime, timedelta

import httpx

from ..config import settings

_token_cache = {"access_token": None, "expires_at": datetime.min}


async def _get_token() -> str:
    if _token_cache["access_token"] and _token_cache["expires_at"] > datetime.utcnow():
        return _token_cache["access_token"]
    basic = base64.b64encode(f"{settings.qpay_username}:{settings.qpay_password}".encode()).decode()
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{settings.qpay_base_url}/auth/token",
            headers={"Authorization": f"Basic {basic}"},
        )
        resp.raise_for_status()
        data = resp.json()
    _token_cache["access_token"] = data["access_token"]
    _token_cache["expires_at"] = datetime.utcnow() + timedelta(seconds=int(data.get("expires_in", 3600)) - 60)
    return _token_cache["access_token"]


async def create_invoice(sender_invoice_no: str, amount: float, description: str,
                         receiver_code: str, callback_url: str) -> dict:
    """QPay invoice үүсгэнэ. Буцаах: invoice_id, qr_text, qr_image, deep_link."""
    if settings.qpay_mock:
        mock_id = f"MOCK-INV-{uuid.uuid4().hex[:10].upper()}"
        return {
            "invoice_id": mock_id,
            "qr_text": f"https://qpay.mn/q/MOCK/{mock_id}",
            "qr_image": "",
            "deep_link": f"qpay://q?invoice={mock_id}",
            "mock": True,
        }
    token = await _get_token()
    payload = {
        "invoice_code": settings.qpay_invoice_code,
        "sender_invoice_no": sender_invoice_no,
        "invoice_receiver_code": receiver_code,
        "invoice_description": description,
        "amount": round(amount, 2),
        "callback_url": callback_url,
    }
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            f"{settings.qpay_base_url}/invoice",
            json=payload, headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()
        data = resp.json()
    deep_link = ""
    for u in data.get("urls", []):
        if "qpay" in (u.get("link") or "").lower():
            deep_link = u["link"]
            break
    return {
        "invoice_id": data.get("invoice_id"),
        "qr_text": data.get("qr_text", ""),
        "qr_image": data.get("qr_image", ""),
        "deep_link": deep_link or (data.get("urls") or [{}])[0].get("link", ""),
        "mock": False,
    }


async def check_payment(invoice_id: str) -> dict:
    """Webhook ирээгүй үед төлбөрийг шалгах (polling)."""
    if settings.qpay_mock:
        return {"paid": False, "mock": True}
    token = await _get_token()
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{settings.qpay_base_url}/payment/check",
            json={"object_type": "INVOICE", "object_id": invoice_id,
                  "offset": {"page_number": 1, "page_limit": 10}},
            headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()
        data = resp.json()
    paid_amount = float(data.get("paid_amount") or 0)
    return {"paid": paid_amount > 0, "paid_amount": paid_amount, "raw": data}
