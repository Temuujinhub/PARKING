"""QPay v2 merchant интеграц — developer.qpay.mn/mn/docs/merchant (v2.0.0).

Урсгал: жолооч утасны камераар QR уншина → банкны апп → төлнө → QPay webhook →
систем PAID болгож e-Barimt баримт үүсгэнэ.

Endpoint-ууд (docs):
  POST /v2/auth/token       — Basic auth → access_token, refresh_token, expires_in
  POST /v2/auth/refresh     — Bearer refresh_token → шинэ access_token
  POST /v2/invoice          — нэхэмжлэл үүсгэх → invoice_id, qr_text, qr_image, urls
  POST /v2/payment/check    — төлбөр шалгах → count, paid_amount, rows
  GET  /v2/payment/{id}     — төлбөрийн дэлгэрэнгүй

qpay_mock=True үед бодит QPay руу хандахгүй — туршилтын QR/invoice буцаана.
Бодит: PARKING_QPAY_MOCK=false, PARKING_QPAY_SANDBOX (true/false),
       PARKING_QPAY_USERNAME/PASSWORD/INVOICE_CODE.
"""
import base64
import uuid
from datetime import datetime, timedelta

import httpx

from ..config import settings

# Токены cache (access + refresh)
_token = {"access": None, "refresh": None, "access_exp": datetime.min}


async def _auth_basic() -> dict:
    """POST /v2/auth/token — client_id:client_secret Basic auth."""
    basic = base64.b64encode(f"{settings.qpay_username}:{settings.qpay_password}".encode()).decode()
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(f"{settings.qpay_base_url}/auth/token",
                                 headers={"Authorization": f"Basic {basic}"})
        resp.raise_for_status()
        return resp.json()


async def _auth_refresh() -> dict:
    """POST /v2/auth/refresh — Bearer refresh_token."""
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(f"{settings.qpay_base_url}/auth/refresh",
                                 headers={"Authorization": f"Bearer {_token['refresh']}"})
        resp.raise_for_status()
        return resp.json()


async def _get_token() -> str:
    """Хүчинтэй access_token буцаана. Дуусах дөхсөн бол refresh, боломжгүй бол дахин auth."""
    now = datetime.utcnow()
    if _token["access"] and _token["access_exp"] > now:
        return _token["access"]
    try:
        data = await _auth_refresh() if _token["refresh"] else await _auth_basic()
    except Exception:
        data = await _auth_basic()  # refresh амжилтгүй бол шинээр
    _token["access"] = data["access_token"]
    _token["refresh"] = data.get("refresh_token", _token["refresh"])
    _token["access_exp"] = now + timedelta(seconds=int(data.get("expires_in", 3600)) - 60)
    return _token["access"]


async def create_invoice(sender_invoice_no: str, amount: float, description: str,
                         receiver_code: str, callback_url: str) -> dict:
    """POST /v2/invoice — нэхэмжлэл үүсгэнэ.
    Буцаах: invoice_id, qr_text, qr_image (base64), deep_link, urls (банкны жагсаалт)."""
    if settings.qpay_mock:
        mock_id = f"MOCK-INV-{uuid.uuid4().hex[:10].upper()}"
        return {"invoice_id": mock_id, "qr_text": f"https://qpay.mn/q/MOCK/{mock_id}",
                "qr_image": "", "deep_link": f"qpay://q?invoice={mock_id}", "urls": [], "mock": True}

    token = await _get_token()
    payload = {
        "invoice_code": settings.qpay_invoice_code,
        "sender_invoice_no": sender_invoice_no,
        "invoice_receiver_code": receiver_code or "terminal",
        "invoice_description": description,
        "amount": round(float(amount), 2),
        "callback_url": callback_url,
    }
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(f"{settings.qpay_base_url}/invoice",
                                 json=payload, headers={"Authorization": f"Bearer {token}"})
        resp.raise_for_status()
        data = resp.json()

    urls = data.get("urls") or []
    deep_link = ""
    for u in urls:
        if "qpay" in (u.get("link") or "").lower():
            deep_link = u["link"]
            break
    if not deep_link and urls:
        deep_link = urls[0].get("link", "")
    return {
        "invoice_id": data.get("invoice_id"),
        "qr_text": data.get("qr_text", ""),
        "qr_image": data.get("qr_image", ""),  # base64 PNG
        "deep_link": deep_link,
        "urls": urls,  # бүх банкны deeplink (нэр, лого, линк) — апп/веб сонголт харуулна
        "mock": False,
    }


async def check_payment(invoice_id: str) -> dict:
    """POST /v2/payment/check — invoice-ийн төлбөр төлөгдсөн эсэх (webhook ирээгүй үед polling).
    Хариу: count, paid_amount, rows."""
    if settings.qpay_mock:
        return {"paid": False, "mock": True}
    token = await _get_token()
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{settings.qpay_base_url}/payment/check",
            json={"object_type": "INVOICE", "object_id": invoice_id,
                  "offset": {"page_number": 1, "page_limit": 100}},
            headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()
        data = resp.json()
    paid_amount = float(data.get("paid_amount") or 0)
    return {"paid": paid_amount > 0, "paid_amount": paid_amount,
            "count": int(data.get("count") or 0), "rows": data.get("rows", []), "raw": data}


async def cancel_payment(payment_id: str) -> bool:
    """DELETE /v2/payment/cancel/{id} — картын гүйлгээ цуцлах."""
    if settings.qpay_mock:
        return True
    token = await _get_token()
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.delete(f"{settings.qpay_base_url}/payment/cancel/{payment_id}",
                                   headers={"Authorization": f"Bearer {token}"})
    return resp.status_code in (200, 204)
