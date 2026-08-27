"""Гадаад wallet-ууд — төлбөрийн хэрэгслийн нэгдсэн интерфэйс.

Гурван эх үүсвэр:
  INTERNAL     — өөрийн данс (services/wallet.py, энэ DB) — үндсэн, бэлэн.
  SITE_WALLET  — site.easy-parking.mn системийн данс (API-аар).
  EP_WALLET    — wallet.easy-parking.mn (хөгжүүлж буй өөрийн wallet).

Нэгдсэн гэрээ (бүх provider):
    balance(plate, phone)              → {"found": bool, "balance": Decimal}
    debit(plate, amount, ref, note)    → {"ok": bool, "tx_id": str}   (идемпотент: ref)
    credit(plate, amount, ref, note)   → {"ok": bool, "tx_id": str}   (буцаалт)

Гадаад системүүдийн API баталгаажаагүй тул SITE_WALLET/EP_WALLET нь энэ
гэрээг ХҮЛЭЭДЭГ REST клиент хэлбэрээр бичигдсэн:
    GET  {base}/balance?plate=..&phone=..
    POST {base}/debit   {"plate","amount","ref","note"}   (Idempotency-Key: ref)
    POST {base}/credit  {"plate","amount","ref","note"}
    Authorization: Bearer <api_key>
Тэдний бодит спек гарахад зөвхөн энэ файлын _http_provider дотор зам/талбарын
нэрийг тааруулна — дуудагч кодууд (session_logic, ev_billing, роутерууд)
ӨӨРЧЛӨГДӨХГҮЙ.

Гарах хаалтны автомат хасалтад (§6.2) provider-уудыг ДАРААЛАН шалгана:
эхлээд INTERNAL, дараа нь тохируулагдсан гадаад wallet-ууд.
"""
import logging
from decimal import Decimal

import httpx

from ..config import settings

log = logging.getLogger("parking.wallet_providers")

D = Decimal


class ProviderError(Exception):
    pass


class _HttpWalletProvider:
    """Гадаад wallet-ийн REST клиент (дээрх гэрээгээр)."""

    def __init__(self, name: str, base_url: str, api_key: str, timeout: float = 6.0):
        self.name = name
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    @property
    def enabled(self) -> bool:
        return bool(self.base_url and self.api_key)

    def _headers(self, idem: str | None = None) -> dict:
        h = {"Authorization": f"Bearer {self.api_key}"}
        if idem:
            h["Idempotency-Key"] = idem
        return h

    async def balance(self, plate: str, phone: str = "") -> dict:
        if not self.enabled:
            return {"found": False, "balance": D(0)}
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as c:
                r = await c.get(f"{self.base_url}/balance",
                                params={"plate": plate, "phone": phone},
                                headers=self._headers())
            if r.status_code == 404:
                return {"found": False, "balance": D(0)}
            r.raise_for_status()
            data = r.json()
            return {"found": bool(data.get("found", True)),
                    "balance": D(str(data.get("balance") or 0))}
        except (httpx.HTTPError, ValueError) as e:
            # Гадаад систем унасан үед гарах урсгал ГАЦАХГҮЙ — олдоогүйд тооцно
            log.warning("%s balance алдаа (%s) — алгасав", self.name, e)
            return {"found": False, "balance": D(0), "error": str(e)}

    async def debit(self, plate: str, amount, ref: str, note: str = "") -> dict:
        """Идемпотент хасалт: ижил ref-ээр давхар дуудахад давхар хасахгүй
        (гэрээний шаардлага — тэдний талд Idempotency-Key-ээр хангагдана)."""
        if not self.enabled:
            raise ProviderError(f"{self.name} тохируулаагүй")
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as c:
                r = await c.post(f"{self.base_url}/debit",
                                 json={"plate": plate, "amount": float(amount),
                                       "ref": ref, "note": note},
                                 headers=self._headers(idem=ref))
            r.raise_for_status()
            data = r.json()
            if not data.get("ok"):
                raise ProviderError(str(data.get("error") or "debit ok=false"))
            return {"ok": True, "tx_id": str(data.get("tx_id") or "")}
        except httpx.HTTPError as e:
            raise ProviderError(f"{self.name} debit алдаа: {e}") from e

    async def credit(self, plate: str, amount, ref: str, note: str = "") -> dict:
        if not self.enabled:
            raise ProviderError(f"{self.name} тохируулаагүй")
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as c:
                r = await c.post(f"{self.base_url}/credit",
                                 json={"plate": plate, "amount": float(amount),
                                       "ref": ref, "note": note},
                                 headers=self._headers(idem=f"credit-{ref}"))
            r.raise_for_status()
            data = r.json()
            return {"ok": bool(data.get("ok")), "tx_id": str(data.get("tx_id") or "")}
        except httpx.HTTPError as e:
            raise ProviderError(f"{self.name} credit алдаа: {e}") from e


def site_wallet() -> _HttpWalletProvider:
    """site.easy-parking.mn — үндсэн системийн данс."""
    return _HttpWalletProvider("SITE_WALLET", settings.site_wallet_url,
                               settings.site_wallet_api_key)


def ep_wallet() -> _HttpWalletProvider:
    """wallet.easy-parking.mn — хөгжүүлж буй wallet систем."""
    return _HttpWalletProvider("EP_WALLET", settings.ep_wallet_url,
                               settings.ep_wallet_api_key)


def external_providers() -> list[_HttpWalletProvider]:
    """Тохируулагдсан гадаад wallet-ууд (шалгах дараалалаараа)."""
    return [p for p in (site_wallet(), ep_wallet()) if p.enabled]
