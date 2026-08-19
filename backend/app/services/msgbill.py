"""msgbill.mn Partner API — «Үйлчилгээ 3: eBarimt API» (scope: receipt).

Лавлах: https://msgbill.mn/developers · Postman collection «msgbill.mn Partner API».

Юунд: QPay-ээр төлөөгүй төлбөрт (дансаар/online operator, бэлэн, карт) e-Barimt
үүсгэхийн тулд өмнө нь сервер бүр дээр ТЕГ-ийн PosAPI суулгах шаардлагатай байсан
— production дээр суугаагүй тул PARKING_EBARIMT_MOCK=true буюу ХУУРАМЧ баримт
гарч байв. msgbill.mn нь баримтыг өөрийн бүртгэлээр (ДДТД, сугалаа, QR) үүсгэж
өгдөг тул нэг API дуудлагаар жинхэнэ баримт авна.

    POST {base}/partner/receipts
      X-Api-Key: bsk_...        Idempotency-Key: <payment id>
      {amount, description, receipt_type: CITIZEN|ORGANIZATION,
       payer_reg_no?, payment_method: CASH|CARD|BANK_TRANSFER}
    → 201 {id: "rcp_…", state: CREATED|FAILED|…, receipt_no, lottery, qr_data,
           receipt_type, error}
    GET  {base}/partner/receipts/{id} — төлөв (FAILED бол msgbill өөрөө retry хийнэ)

Idempotency: ижил түлхүүр + ижил body → хадгалсан хариу (давхар баримт үүсэхгүй).
Хязгаар: 429 RECEIPT_QUOTA_EXCEEDED (сарын тоо дүүрсэн — Billing хуудас).

Түлхүүрийн шатлал (api_key_for): ТҮРЭЭСЛЭГЧИЙН түлхүүр → глобал .env түлхүүр.
Глобал түлхүүр нь EasyParking-ийн ӨӨРИЙН бүртгэл тул зөвхөн ерөнхий QPay данс
ашигладаг (өөрийн ТТД-гүй) зогсоолд л уналт хийнэ — өөрийн QPay данстай атлаа
msgbill түлхүүргүй түрээслэгчийн баримтыг EasyParking-ийн ТТД-ээр гаргахгүй
(буруу татвар төлөгчийн нэр дээр баримт гарах эрсдэл).

Буцаах формат нь ebarimt.create_receipt-тэй ИЖИЛ ({status, billId, lottery,
qrData, date, ...}) — payments_router-ийн VatReceipt бүртгэл өөрчлөгдөхгүй.
"""
import logging
from dataclasses import dataclass
from datetime import datetime

import httpx

from ..config import settings

log = logging.getLogger("parking.msgbill")

# msgbill payment_method ↔ манай payment_method
_METHOD_MAP = {
    "TRANSFER": "BANK_TRANSFER",
    "CASH": "CASH",
    "CARD": "CARD",
    "QR": "CARD",
}


class MsgbillError(Exception):
    """msgbill.mn-ээс алдаа (HTTP 4xx/5xx эсвэл state=FAILED). .code = API кодыг барина."""

    def __init__(self, message: str, code: str | None = None, status: int | None = None):
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass
class MsgbillAccount:
    api_key: str
    base_url: str = ""
    scope: str = "global"       # global | tenant — лог/UI-д
    scope_name: str | None = None

    @property
    def enabled(self) -> bool:
        return bool((self.api_key or "").strip())

    @property
    def is_test(self) -> bool:
        return (self.api_key or "").startswith("bsk_test_")


_gcache: dict = {"t": 0.0, "v": None}
_GCACHE_SEC = 30.0


def global_config(db=None) -> dict:
    """Глобал тохиргоо: DB (Тохиргоо → Холболт, app_settings) → .env fallback.
    {api_key, methods, source: db|env}. db=None бол .env л (кэштэй DB утга байвал түүнийг)."""
    import time as _t
    env = {"api_key": (settings.msgbill_api_key or "").strip(),
           "methods": (settings.msgbill_methods or "").strip(), "source": "env"}
    if db is None:
        v = _gcache["v"]
        if v is not None and _t.monotonic() - _gcache["t"] < _GCACHE_SEC:
            return v
        return env
    try:
        from ..secretbox import decrypt_secret
        from .app_settings import MSGBILL_STATE, get_state
        st = get_state(db, MSGBILL_STATE)
    except Exception:  # noqa: BLE001
        st = {}
    key = decrypt_secret((st.get("api_key") or "").strip()) if st.get("api_key") else ""
    out = {
        "api_key": key or env["api_key"],
        # DB-д арга заасан бол түүнийг, үгүй бол .env (хоосон = ямар ч аргад msgbill-гүй)
        "methods": st["methods"] if st.get("methods") else env["methods"],
        "source": "db" if key else "env",
    }
    _gcache["t"], _gcache["v"] = _t.monotonic(), out
    return out


def invalidate_cache():
    _gcache["v"] = None


def _db_of(obj):
    if obj is None:
        return None
    try:
        from sqlalchemy.orm import object_session
        return object_session(obj)
    except Exception:  # noqa: BLE001
        return None


def enabled_methods(db=None) -> set[str]:
    """msgbill_methods → {"TRANSFER", ...}; ALL → бүх арга."""
    raw = (global_config(db).get("methods") or "").strip().upper()
    if not raw:
        return set()
    if raw == "ALL":
        return set(_METHOD_MAP.keys())
    return {m.strip() for m in raw.split(",") if m.strip()}


def method_enabled(payment_method: str | None, db=None) -> bool:
    return (payment_method or "").upper() in enabled_methods(db)


def _tenant_of(site):
    if site is None:
        return None
    try:
        from ..services import qpay as _qpay
        return _qpay._tenant_of(site)
    except Exception:  # noqa: BLE001
        return getattr(site, "tenant", None)


def _own_qpay(obj) -> bool:
    return bool((getattr(obj, "qpay_username", None) or "").strip()
                and (getattr(obj, "qpay_password", None) or "").strip())


def api_key_for(site) -> MsgbillAccount:
    """Зогсоолд ашиглах msgbill түлхүүр: түрээслэгч → глобал (дээрх нөхцөлтэй)."""
    from ..secretbox import decrypt_secret
    base = settings.msgbill_base_url.rstrip("/")
    ten = _tenant_of(site)
    if ten is not None:
        key = decrypt_secret((getattr(ten, "msgbill_api_key", None) or "").strip())
        if key:
            return MsgbillAccount(api_key=key, base_url=base, scope="tenant",
                                  scope_name=getattr(ten, "name", None))
    g = (global_config(_db_of(site)).get("api_key") or "").strip()
    if not g:
        return MsgbillAccount(api_key="", base_url=base)
    # Өөрийн QPay данстай (өөрийн ТТД-тэй) зогсоол/түрээслэгчид глобал түлхүүр ХЭРЭГЛЭХГҮЙ
    if site is not None and (_own_qpay(site) or (ten is not None and _own_qpay(ten))):
        return MsgbillAccount(api_key="", base_url=base)
    return MsgbillAccount(api_key=g, base_url=base, scope="global")


def account_enabled_for(site, payment_method: str | None) -> MsgbillAccount | None:
    """Энэ зогсоол + төлбөрийн аргад msgbill ашиглах уу? Тийм бол данс, үгүй бол None."""
    if not method_enabled(payment_method, _db_of(site)):
        return None
    acc = api_key_for(site)
    return acc if acc.enabled else None


def _headers(acc: MsgbillAccount, idem: str | None = None) -> dict:
    h = {"X-Api-Key": acc.api_key, "Content-Type": "application/json",
         "Accept": "application/json"}
    if idem:
        h["Idempotency-Key"] = idem[:120]
    return h


def _err_from_response(resp: httpx.Response) -> MsgbillError:
    # msgbill алдааны формат: {code, message_mn, message_en, retryable, request_id}
    code, msg = None, None
    try:
        j = resp.json()
        if isinstance(j, dict):
            code = j.get("code") or j.get("error_code") or (j.get("error") if isinstance(j.get("error"), str) else None)
            msg = (j.get("message_mn") or j.get("message") or j.get("detail")
                   or j.get("message_en") or (j.get("error") if isinstance(j.get("error"), str) else None))
            if isinstance(j.get("error"), dict):
                code = code or j["error"].get("code")
                msg = msg or j["error"].get("message_mn") or j["error"].get("message")
    except Exception:  # noqa: BLE001
        pass
    if resp.status_code == 429:
        code = code or "RECEIPT_QUOTA_EXCEEDED"
        msg = msg or "msgbill сарын баримтын хязгаар дүүрсэн — Billing хуудаснаас шатлалаа ахиулна уу"
    elif resp.status_code in (401, 403):
        code = code or "UNAUTHORIZED"
        msg = msg or "msgbill API түлхүүр буруу/эрхгүй (receipt scope шаардлагатай)"
    text = msg or resp.text[:200] or f"HTTP {resp.status_code}"
    return MsgbillError(f"msgbill {resp.status_code}: {text}", code=code, status=resp.status_code)


def normalize(data: dict) -> dict:
    """msgbill хариуг ebarimt.create_receipt форматад хөрвүүлнэ.

    state=CREATED → billId=receipt_no (ДДТД). Бусад төлөвт billId=None —
    дуудагч FAILED/PENDING гэж бүртгэнэ, msgbillId-ээр дараа нь GET-ээр шалгана."""
    state = (data.get("state") or data.get("status") or "").upper()
    err = data.get("error")
    if isinstance(err, dict):
        err = err.get("message") or err.get("code")
    return {
        "status": "SUCCESS" if state == "CREATED" else (state or "UNKNOWN"),
        "billId": data.get("receipt_no") if state == "CREATED" else None,
        "lottery": data.get("lottery"),
        "qrData": data.get("qr_data"),
        "date": data.get("created_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "msgbillId": data.get("id"),
        "state": state,
        "error": err,
        "test": bool(data.get("test")),
        "raw": data,
    }


async def create_receipt(acc: MsgbillAccount, amount: float, *, description: str,
                         payment_method: str, idempotency_key: str,
                         customer_tin: str | None = None) -> dict:
    """Баримт үүсгэнэ. Амжилтгүй HTTP → MsgbillError (дуудагч барьж VatReceipt FAILED болгоно)."""
    if not acc.enabled:
        raise MsgbillError("msgbill API түлхүүр тохируулаагүй", code="NOT_CONFIGURED")
    body = {
        # msgbill бүхэл төгрөгөөр (НӨАТ багтсан) — таслалын зөрүү үүсэхээс сэргийлж round
        "amount": int(round(float(amount))),
        "description": (description or "Зогсоолын үйлчилгээ")[:200],
        "receipt_type": "ORGANIZATION" if customer_tin else "CITIZEN",
        "payment_method": _METHOD_MAP.get((payment_method or "").upper(), "CASH"),
    }
    if customer_tin:
        body["payer_reg_no"] = str(customer_tin).strip()[:20]
    async with httpx.AsyncClient(timeout=settings.msgbill_timeout) as client:
        resp = await client.post(f"{acc.base_url}/partner/receipts", json=body,
                                 headers=_headers(acc, idempotency_key))
    if resp.status_code >= 400:
        raise _err_from_response(resp)
    data = resp.json() if resp.content else {}
    out = normalize(data if isinstance(data, dict) else {})
    if out["state"] == "FAILED":
        # msgbill өөрөө автомат retry хийдэг — гэхдээ бидний талд FAILED гэж
        # бүртгээд «Баримт дахин үүсгэх»-ээр GET/дахин POST хийж болно
        log.warning("msgbill баримт FAILED (id=%s): %s", out["msgbillId"], out["error"])
    if out.get("test"):
        log.warning("msgbill ТЕСТ түлхүүр — баримт симуляц (бодит ДДТД биш): %s", out["billId"])
    return out


async def get_receipt(acc: MsgbillAccount, msgbill_id: str) -> dict:
    """Баримтын төлөв (state, receipt_no, lottery, qr_data) — PENDING/FAILED-ийг дараа нөхөхөд."""
    async with httpx.AsyncClient(timeout=settings.msgbill_timeout) as client:
        resp = await client.get(f"{acc.base_url}/partner/receipts/{msgbill_id}",
                                headers=_headers(acc))
    if resp.status_code >= 400:
        raise _err_from_response(resp)
    data = resp.json() if resp.content else {}
    return normalize(data if isinstance(data, dict) else {})


async def cancel_receipt(acc: MsgbillAccount, msgbill_id: str, note: str = "") -> dict:
    """Баримт цуцлах (буцаалт) — `DELETE {base}/partner/receipts/{id}`.

    2026-08-19 байдлаар msgbill Partner API-д цуцлах endpoint БАЙХГҮЙ (DELETE /
    cancel / void / refund бүгд 404 «Cannot DELETE …»). msgbill талд нэмэгдмэгц
    энэ функц шууд ажиллана; тэр хүртэл MsgbillError(code=NOT_SUPPORTED) өгнө —
    дуудагч операторт «msgbill-ээс цуцлах боломж хараахан алга» гэж хэлнэ.
    msgbill талын хэрэгжилт: PosAPI 3.0 `DELETE /rest/receipt {id: billId, date}`."""
    async with httpx.AsyncClient(timeout=settings.msgbill_timeout) as client:
        resp = await client.request("DELETE", f"{acc.base_url}/partner/receipts/{msgbill_id}",
                                    json={"note": note} if note else None, headers=_headers(acc))
    if resp.status_code == 404:
        err = _err_from_response(resp)
        if "Cannot DELETE" in str(err) or err.code == "NOT_FOUND":
            raise MsgbillError("msgbill.mn Partner API-д баримт цуцлах endpoint хараахан байхгүй "
                               "(DELETE /partner/receipts/{id} → 404). msgbill талд нэмэгдсэний "
                               "дараа энэ товч ажиллана.", code="NOT_SUPPORTED", status=404)
        raise err
    if resp.status_code >= 400:
        raise _err_from_response(resp)
    data = resp.json() if resp.content else {}
    return normalize(data if isinstance(data, dict) else {"state": "CANCELLED"})


def status_info(db=None) -> dict:
    """Тохиргоо → Холболт → e-Barimt хэсэгт харуулах глобал төлөв (нууц задлахгүй)."""
    cfg = global_config(db)
    key = cfg["api_key"]
    return {
        "configured": bool(key),
        "source": cfg["source"],            # db = UI-аас, env = .env
        "env_configured": bool((settings.msgbill_api_key or "").strip()),
        "test_key": key.startswith("bsk_test_"),
        "key_hint": (key[:9] + "…" + key[-4:]) if len(key) > 16 else ("тохируулсан" if key else None),
        "methods": sorted(enabled_methods(db)),
        "base_url": settings.msgbill_base_url,
    }
