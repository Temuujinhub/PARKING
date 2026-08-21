"""Регистрийн дугаараар татвар төлөгчийг тодорхойлох (нэр + НӨАТ төлөгч эсэх).

ЮУНД: POS дээр жолооч байгууллагын регистрээ өгөхөд оператор зөв бичсэн эсэхээ
мэдэх ёстой — дугаар нь ямар БАЙГУУЛЛАГЫНХ болохыг дэлгэц дээр харуулна. Хувь
хүний регистрт НӨАТ төлөгч мөн эсэхийг харуулна (НӨАТ төлөгч иргэнд баримт өөр
хэлбэрээр бүртгэгддэг).

Хоёр суваг, энэ дарааллаар:
  1. ЛОКАЛ PosAPI — сервер дээр ТЕГ-ийн PosAPI суусан бол хамгийн найдвартай
     (сүлжээнээс хамаарахгүй, хязгааргүй).
  2. НИЙТИЙН api.ebarimt.mn — PosAPI суугаагүй үед. ⚠ Монголын IP-ээс л
     хариулдаг (гадаад сервер дээр timeout болдог) тул суваг нь БАЙХГҮЙ байж
     болно — тэр үед `available: false` буцаана.

ХЭЗЭЭ Ч ТААМАГЛАХГҮЙ: суваг ажиллахгүй бол «нэр олдсонгүй» гэж хэлэхгүй,
«шалгах боломжгүй» гэж ялган хэлнэ. Оператор буруу нэр хараад итгэх нь
шалгаагүй байхаас дор.

PosAPI 3.0 / api.ebarimt.mn:
    GET {base}/info/check/getTinInfo?regNo={регистр}   → {"data": "<ТТД>"}
    GET {base}/info/check/getInfo?tin={ТТД}            → {"data": {name, ...}}
"""
import logging
import time

import httpx

from ..config import settings
from .msgbill import classify_reg_no

log = logging.getLogger("parking.tin")

# reg_no → (үр дүн, дуусах хугацаа). Байгууллагын нэр өдөрт хэдэн удаа
# өөрчлөгддөггүй тул урт кэш — POS дээр хариу шуурхай гарна.
_cache: dict[str, tuple[dict, float]] = {}
_TTL = 24 * 3600
_MAX = 2000


def _cached(key: str) -> dict | None:
    hit = _cache.get(key)
    if not hit:
        return None
    val, exp = hit
    if time.monotonic() > exp:
        _cache.pop(key, None)
        return None
    return val


def _remember(key: str, val: dict):
    if len(_cache) >= _MAX:
        _cache.clear()
    _cache[key] = (val, time.monotonic() + _TTL)


def _bases() -> list[tuple[str, str]]:
    """(нэр, base URL) — дарааллаар нь оролдоно."""
    out: list[tuple[str, str]] = []
    if not settings.ebarimt_mock and settings.ebarimt_posapi_url:
        out.append(("posapi", settings.ebarimt_posapi_url.rstrip("/")))
    if settings.ebarimt_tin_lookup_url:
        out.append(("ebarimt.mn", settings.ebarimt_tin_lookup_url.rstrip("/")))
    return out


def _is_vat_payer(data: dict) -> bool | None:
    """Хариунаас НӨАТ төлөгч эсэхийг гаргана. Талбарын нэр хувилбар бүрд өөр тул
    мэдэгдэж буй бүх нэрийг шалгана; аль нь ч байхгүй бол None (мэдэгдэхгүй)."""
    for k in ("vatPayerRegisteredDate", "vatPayerRegisteredDateStr"):
        v = data.get(k)
        if isinstance(v, str) and v.strip():
            return True
    for k in ("isVatPayer", "vatPayer", "isVatpayer"):
        if k in data:
            return bool(data[k])
    return None


async def lookup(reg_no: str) -> dict:
    """Регистр/ТТД-ээр татвар төлөгчийг тодорхойлно.

    Буцаах:
      available     — шалгах суваг ажиллаж байгаа эсэх (false = мэдэхгүй, олдоогүй БИШ)
      found         — татвар төлөгч олдсон эсэх
      name          — байгууллага/хүний нэр
      is_vat_payer  — НӨАТ төлөгч эсэх (None = мэдэгдэхгүй)
      receipt_type  — ORGANIZATION | CITIZEN (форматаас)
      tin           — олдсон ТТД
    """
    reg, rtype = classify_reg_no(reg_no)
    base_out = {"reg_no": reg, "receipt_type": rtype, "available": False,
                "found": False, "name": None, "is_vat_payer": None,
                "tin": None, "source": None, "error": None}
    if not reg:
        return {**base_out, "available": True, "error": "Формат буруу"}

    hit = _cached(reg)
    if hit is not None:
        return hit

    bases = _bases()
    if not bases:
        return {**base_out, "error": "Шалгах суваг тохируулаагүй (PosAPI/eBarimt)"}

    last_err = None
    for source, base in bases:
        try:
            async with httpx.AsyncClient(timeout=settings.ebarimt_tin_lookup_timeout) as c:
                # ТТД өөрөө өгсөн бол getTinInfo алхмыг алгасна
                tin = reg if len(reg) >= 11 else None
                if tin is None:
                    r = await c.get(f"{base}/info/check/getTinInfo", params={"regNo": reg})
                    r.raise_for_status()
                    tin = str((r.json() or {}).get("data") or "").strip()
                if not tin:
                    out = {**base_out, "available": True, "source": source}
                    _remember(reg, out)
                    return out
                r2 = await c.get(f"{base}/info/check/getInfo", params={"tin": tin})
                r2.raise_for_status()
                data = (r2.json() or {}).get("data") or {}
            name = (data.get("name") or data.get("receiverName") or "").strip() or None
            out = {**base_out, "available": True, "found": bool(name), "name": name,
                   "is_vat_payer": _is_vat_payer(data), "tin": tin, "source": source}
            _remember(reg, out)
            return out
        except Exception as e:  # noqa: BLE001 — дараагийн суваг руу шилжинэ
            last_err = f"{type(e).__name__}"
            log.debug("TIN хайлт (%s) амжилтгүй: %s", source, e)
    return {**base_out, "error": f"Шалгах суваг хариу өгсөнгүй ({last_err})"}
