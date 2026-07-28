"""QPay v2 merchant интеграц + Ebarimt 3.0 (merchant.qpay.mn).

Урсгал (QR төлбөр + e-Barimt):
  1. POST /v2/invoice — НӨАТ-ийн мэдээлэлтэй нэхэмжлэхийн кодоор (EB_..._INVOICE),
     lines (бүтээгдэхүүн бүрээр задлан), tax_type, district_code-той нэхэмжлэл үүсгэнэ
     → invoice_id, qr_text, qr_image, urls (банкны deeplink-ууд).
  2. Жолооч QR-ийг банкны/QPay апп-аар уншиж төлнө.
  3. QPay callback (GET, "SUCCESS" буцаана) ЭСВЭЛ POST /v2/payment/check-ээр төлөгдсөнийг
     баталгаажуулж g_payment_id (QPay-ийн payment_id)-г авна.
  4. POST /v2/ebarimt_v3/create — payment_id + ebarimt_receiver_type-аар e-Barimt үүсгэнэ
     → ebarimt_qr_data (QR болгон хэвлэнэ), ebarimt_lottery (сугалаа), ebarimt_receipt_id (ДДТД).

Endpoint-ууд (docs — 2026.3.17 V2 API with Ebarimt 3.0):
  POST /v2/auth/token          — Basic auth → access_token, refresh_token, expires_in
  POST /v2/auth/refresh        — Bearer refresh_token → шинэ access_token
  POST /v2/invoice             — нэхэмжлэл үүсгэх → invoice_id, qr_text, qr_image, urls
  POST /v2/payment/check       — төлбөр шалгах → count, paid_amount, rows[].payment_id
  GET  /v2/payment/{id}        — төлбөрийн дэлгэрэнгүй
  POST /v2/ebarimt_v3/create   — e-Barimt үүсгэх → id, ebarimt_qr_data, ebarimt_lottery, ...
  DELETE /v2/ebarimt_v3/{id}   — e-Barimt цуцлах

qpay_mock=True үед бодит QPay руу хандахгүй — туршилтын QR/invoice/ebarimt буцаана.
Бодит: PARKING_QPAY_MOCK=false, PARKING_QPAY_SANDBOX (true/false),
       PARKING_QPAY_USERNAME/PASSWORD/INVOICE_CODE.
"""
import base64
import math
import random
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx

from ..config import settings


@dataclass(frozen=True)
class QpayAccount:
    """Нэг QPay мерчант данс. Зогсоол бүр өөрийн гэрээтэй байж болно — төлбөр нь
    тухайн түрээслэгчийн данс руу орж, e-Barimt нь тэдний ТТД-ээр үүснэ."""
    username: str
    password: str
    invoice_code: str
    branch_code: str
    district_code: str
    tax_type: str
    classification_code: str
    base_url: str
    mock: bool

    @property
    def cache_key(self) -> tuple[str, str]:
        return (self.base_url, self.username)


def global_account() -> QpayAccount:
    """.env-ийн глобал QPay данс (өөрийн данс тохируулаагүй зогсоолуудад)."""
    return QpayAccount(
        username=settings.qpay_username,
        password=settings.qpay_password,
        invoice_code=settings.qpay_invoice_code,
        branch_code=settings.qpay_branch_code,
        district_code=settings.qpay_district_code,
        tax_type=settings.qpay_tax_type,
        classification_code=settings.qpay_classification_code,
        base_url=settings.qpay_base_url,
        mock=settings.qpay_mock,
    )


def account_for(site) -> QpayAccount:
    """Зогсоолын QPay данс. Зогсоолд өөрийн username/password тохируулсан бол
    ТҮҮГЭЭР, эс бол глобал .env-ийн данснаас. Талбар тус бүр тусад нь уналт
    хийдэг тул зөвхөн дүүргийн кодоо өөрчлөх гэх мэт хэсэгчилсэн тохиргоо бас болно.

    ЧУХАЛ: өөрийн данстай зогсоолд mock=False — глобал mock=true байсан ч
    бодит гэрээтэй түрээслэгчийн төлбөрийг хуурамчаар боловсруулахгүй."""
    g = global_account()
    if site is None:
        return g
    from ..secretbox import decrypt_secret
    user = (getattr(site, "qpay_username", None) or "").strip()
    # DB-д шифрлэгдсэн байж болно ("enc:" угтвар); тайлж чадахгүй бол ЧАНГА алдаа
    # шидэгдэнэ — түрээслэгчийн төлбөр глобал данс руу чимээгүй унахгүй
    pwd = decrypt_secret((getattr(site, "qpay_password", None) or "").strip())
    if not (user and pwd):
        # Хэсэгчилсэн тохиргоо (зөвхөн дүүрэг/салбар) — данс нь глобал хэвээр
        return QpayAccount(
            username=g.username, password=g.password,
            invoice_code=(getattr(site, "qpay_invoice_code", None) or g.invoice_code),
            branch_code=(getattr(site, "qpay_branch_code", None) or g.branch_code),
            district_code=(getattr(site, "qpay_district_code", None) or g.district_code),
            tax_type=g.tax_type, classification_code=g.classification_code,
            base_url=g.base_url, mock=g.mock,
        )
    return QpayAccount(
        username=user, password=pwd,
        invoice_code=(getattr(site, "qpay_invoice_code", None) or g.invoice_code),
        branch_code=(getattr(site, "qpay_branch_code", None) or g.branch_code),
        district_code=(getattr(site, "qpay_district_code", None) or g.district_code),
        tax_type=g.tax_type, classification_code=g.classification_code,
        base_url=g.base_url, mock=False,
    )


# Токены cache — мерчант данс бүрд ТУСДАА (нэг процесст олон гэрээ зэрэг ажиллана).
# QPay-ийн заавар: токеныг хугацаанд нь нэг л удаа авч, дуусах хүртэл дахин ашиглана.
_tokens: dict[tuple[str, str], dict] = {}


def _cache(acc: QpayAccount) -> dict:
    return _tokens.setdefault(acc.cache_key,
                              {"access": None, "refresh": None, "access_exp": datetime.min})


async def _auth_basic(acc: QpayAccount) -> dict:
    """POST /v2/auth/token — client_id:client_secret Basic auth."""
    basic = base64.b64encode(f"{acc.username}:{acc.password}".encode()).decode()
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(f"{acc.base_url}/auth/token",
                                 headers={"Authorization": f"Basic {basic}"})
        resp.raise_for_status()
        return resp.json()


async def _auth_refresh(acc: QpayAccount) -> dict:
    """POST /v2/auth/refresh — Bearer refresh_token."""
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(f"{acc.base_url}/auth/refresh",
                                 headers={"Authorization": f"Bearer {_cache(acc)['refresh']}"})
        resp.raise_for_status()
        return resp.json()


async def _get_token(acc: QpayAccount | None = None) -> str:
    """Хүчинтэй access_token буцаана. Дуусах дөхсөн бол refresh, боломжгүй бол дахин auth."""
    acc = acc or global_account()
    tok = _cache(acc)
    now = datetime.utcnow()
    if tok["access"] and tok["access_exp"] > now:
        return tok["access"]
    try:
        data = await _auth_refresh(acc) if tok["refresh"] else await _auth_basic(acc)
    except Exception:
        data = await _auth_basic(acc)  # refresh амжилтгүй бол шинээр
    tok["access"] = data["access_token"]
    tok["refresh"] = data.get("refresh_token", tok["refresh"])
    tok["access_exp"] = now + timedelta(seconds=int(data.get("expires_in", 3600)) - 60)
    return tok["access"]


def _vat_of(price: float) -> float:
    """НӨАТ багтсан үнээс НӨАТ-ыг гаргана (4 орны нарийвчлалтай ТАСЛАЖ — docs жишээ).
    QPay жишээ: 50₮ → 50*0.1/1.1 = 4.54545… → 4.5454 (round биш truncate)."""
    r = settings.vat_rate
    return math.floor(price * r / (1 + r) * 10000) / 10000


def build_lines(items: list[dict], acc: QpayAccount | None = None) -> list[dict]:
    """e-Barimt нэхэмжлэхийн lines-ийг бүтээгдэхүүн бүрээр байгуулна.

    items: [{"description", "unit_price", "quantity"(=1), "classification_code"(optional),
             "barcode"(optional), "note"(optional)}]
    tax_type=1 (НӨАТ тооцогдох) үед мөр бүрт VAT taxes нэмнэ. 2/3 үед VAT тооцохгүй.
    """
    acc = acc or global_account()
    lines = []
    vat_able = acc.tax_type == "1"
    for it in items:
        price = round(float(it["unit_price"]), 2)
        qty = float(it.get("quantity", 1) or 1)
        line = {
            "tax_product_code": it.get("tax_product_code", ""),
            "line_description": it["description"][:255],
            "line_quantity": f"{qty:.2f}",
            "line_unit_price": f"{price:.2f}",
            "note": it.get("note", ""),
            "classification_code": it.get("classification_code") or acc.classification_code,
        }
        if it.get("barcode"):
            line["barcode"] = str(it["barcode"])
        if vat_able:
            line["taxes"] = [{
                "tax_code": "VAT",
                "description": "НӨАТ",
                "amount": _vat_of(price * qty),
                "note": "НӨАТ",
            }]
        lines.append(line)
    return lines


def pick_qpay_deeplink(urls: list[dict]) -> str:
    """ЗӨВХӨН qPay хэтэвчний өөрийнх нь deeplink-ийг сонгоно. АНХААР: банк бүрийн
    линк "...://q?qPay_QRcode=..." хэлбэртэй тул "qpay" substring-ээр хайвал
    ЭХНИЙ ДУРЫН апп (ж: eBarimt) таарч утсан дээр буруу апп руу үсэргэдэг байсан —
    scheme (://-ийн өмнөх хэсэг) болон нэрээр нь шүүнэ. Тохирох нь олдоогүй бол
    хоосон буцаана — frontend автоматаар үсэргэхгүй, QR + жагсаалтаас сонгуулна."""
    for u in urls:
        link = u.get("link") or ""
        scheme = link.split("://", 1)[0].lower() if "://" in link else ""
        name = (u.get("name") or "").lower().replace(" ", "")
        if "qpay" in scheme or "qpay" in name:
            return link
    return ""


async def create_invoice(sender_invoice_no: str, description: str, receiver_code: str,
                         callback_url: str, lines: list[dict],
                         receiver_data: dict | None = None,
                         acc: QpayAccount | None = None) -> dict:
    """POST /v2/invoice — e-Barimt-тэй нэхэмжлэл үүсгэнэ (lines бүтээгдэхүүн бүрээр задлагдсан).

    Буцаах: invoice_id, qr_text, qr_image (base64 PNG), deep_link, urls (банкны жагсаалт)."""
    acc = acc or global_account()
    if acc.mock:
        mock_id = f"MOCK-INV-{uuid.uuid4().hex[:10].upper()}"
        return {"invoice_id": mock_id, "qr_text": f"https://qpay.mn/q/MOCK/{mock_id}",
                "qr_image": "", "deep_link": f"qpay://q?invoice={mock_id}", "urls": [], "mock": True}

    token = await _get_token(acc)
    payload = {
        "invoice_code": acc.invoice_code,
        "sender_invoice_no": sender_invoice_no,
        "invoice_receiver_code": receiver_code or "terminal",
        "sender_branch_code": acc.branch_code,
        "invoice_description": description,
        "tax_type": acc.tax_type,
        "district_code": acc.district_code,
        "callback_url": callback_url,
        "lines": lines,
    }
    if receiver_data:
        payload["invoice_receiver_data"] = receiver_data
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(f"{acc.base_url}/invoice",
                                 json=payload, headers={"Authorization": f"Bearer {token}"})
        resp.raise_for_status()
        data = resp.json()

    urls = data.get("urls") or []
    deep_link = pick_qpay_deeplink(urls)
    return {
        "invoice_id": data.get("invoice_id"),
        "qr_text": data.get("qr_text", ""),
        "qr_image": data.get("qr_image", ""),  # base64 PNG
        "deep_link": deep_link,
        "urls": urls,  # бүх банкны deeplink (нэр, лого, линк) — апп/веб сонголт харуулна
        "mock": False,
    }


async def check_payment(invoice_id: str, acc: QpayAccount | None = None) -> dict:
    """POST /v2/payment/check — invoice-ийн төлбөр төлөгдсөн эсэх (webhook ирээгүй үед polling).
    Хариу: paid, paid_amount, count, rows, payment_id (эхний төлбөрийн g_payment_id — ebarimt-д)."""
    acc = acc or global_account()
    if acc.mock:
        return {"paid": False, "mock": True}
    token = await _get_token(acc)
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{acc.base_url}/payment/check",
            json={"object_type": "INVOICE", "object_id": invoice_id,
                  "offset": {"page_number": 1, "page_limit": 100}},
            headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()
        data = resp.json()
    paid_amount = float(data.get("paid_amount") or 0)
    rows = data.get("rows", []) or []
    # QPay-ийн payment_id — e-Barimt үүсгэхэд шаардлагатай (эхний амжилттай гүйлгээнээс)
    payment_id = None
    for row in rows:
        pid = row.get("payment_id") or row.get("id")
        if pid and (row.get("payment_status") or "PAID") == "PAID":
            payment_id = str(pid)
            break
    if not payment_id and rows:
        payment_id = str(rows[0].get("payment_id") or rows[0].get("id") or "")
    return {"paid": paid_amount > 0, "paid_amount": paid_amount,
            "count": int(data.get("count") or 0), "rows": rows,
            "payment_id": payment_id, "raw": data}


async def create_ebarimt(payment_id: str, receiver_type: str = "CITIZEN",
                         receiver: str | None = None,
                         district_code: str | None = None,
                         acc: QpayAccount | None = None) -> dict:
    """POST /v2/ebarimt_v3/create — төлөгдсөн төлбөр дээр e-Barimt баримт үүсгэнэ.

    payment_id: QPay-ийн g_payment_id (payment/check-ээс ирсэн).
    receiver_type: CITIZEN (иргэн) | COMPANY (ААН).
    receiver: CITIZEN үед ebarimt апп-д бүртгэлтэй утас (сонголт); COMPANY үед ААН регистр.

    Буцаах (стандартчилсан): {status, billId(=ebarimt_receipt_id), id, lottery, qrData, date, raw}."""
    acc = acc or global_account()
    if acc.mock:
        return _mock_ebarimt()

    token = await _get_token(acc)
    payload = {"payment_id": payment_id, "ebarimt_receiver_type": receiver_type}
    if receiver:
        payload["ebarimt_receiver"] = receiver
    payload["district_code"] = district_code or acc.district_code
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(f"{acc.base_url}/ebarimt_v3/create",
                                 json=payload, headers={"Authorization": f"Bearer {token}"})
        resp.raise_for_status()
        data = resp.json()
    return _normalize_ebarimt(data)


async def cancel_ebarimt(ebarimt_id: str, acc: QpayAccount | None = None) -> bool:
    """DELETE /v2/ebarimt_v3/{id} — e-Barimt цуцлах."""
    acc = acc or global_account()
    if acc.mock:
        return True
    token = await _get_token(acc)
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.delete(f"{acc.base_url}/ebarimt_v3/{ebarimt_id}",
                                   headers={"Authorization": f"Bearer {token}"})
    return resp.status_code in (200, 204)


async def cancel_payment(payment_id: str, acc: QpayAccount | None = None) -> bool:
    """DELETE /v2/payment/cancel/{id} — картын гүйлгээ цуцлах."""
    acc = acc or global_account()
    if acc.mock:
        return True
    token = await _get_token(acc)
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.delete(f"{acc.base_url}/payment/cancel/{payment_id}",
                                   headers={"Authorization": f"Bearer {token}"})
    return resp.status_code in (200, 204)


def _normalize_ebarimt(data: dict) -> dict:
    """QPay ebarimt_v3 хариуг локал ebarimt-тэй нэгэн ижил бүтэц рүү хөрвүүлнэ."""
    return {
        "status": "SUCCESS" if data.get("status", True) else "FAILED",
        # ДДТД (баримтын дугаар) — receipt_id, эс бол ebarimt ID
        "billId": data.get("ebarimt_receipt_id") or data.get("id"),
        "id": data.get("id"),
        "lottery": data.get("ebarimt_lottery") or data.get("lottery"),
        "qrData": data.get("ebarimt_qr_data") or data.get("qr_data"),
        "date": data.get("barimt_status_date") or data.get("ebarimt_status_date") or data.get("created_date"),
        "raw": data,
    }


def _mock_ebarimt() -> dict:
    """Туршилтын e-Barimt хариу (QPay холбогдоогүй үед)."""
    receipt_id = "".join(random.choices("0123456789", k=33))
    lottery = "".join(random.choices("АБВГДЕЁЖЗ", k=2)) + " " + "".join(random.choices("0123456789", k=8))
    qr = "".join(random.choices("0123456789", k=160))
    return {"status": "SUCCESS", "billId": receipt_id, "id": str(uuid.uuid4()),
            "lottery": lottery, "qrData": qr,
            "date": datetime.now().strftime("%Y-%m-%dT%H:%M:%S.000Z"), "mock": True, "raw": {}}
