"""Камерын цагийн зөрүүг ПАССИВ хэмжих — камер руу нэг ч хүсэлт илгээхгүй.

Яагаад: цагийн зөрүү бол системийн хамгийн хор хөнөөлтэй чимээгүй гэмтэл —
нөхөлтийн (camera_sync) цаг, төлбөрийн тооцоо, ТЕГ тулгалт, log_tail-ийн
«шинэхэн» шалгалт бүгд гажина. 2026-08-31-ний хэмжилтээр Рашбулаг ЭТТ-ийн
4 камер 46 мин – 2 цаг ТҮРҮҮЛЖ явж байсныг хэн ч мэдээгүй байв: камерууд NTP
сервертэй (172.16.100.23) тохируулагдсан ч хүрч чадахгүй болсныг харуулах
ямар ч дохио байгаагүй.

Хэрхэн: Dahua-гийн TrafficJunction эвэнт бүр `RealUTC` (камерын өөрийн UTC
цаг, epoch сек) талбартай ирдэг. Серверийн хүлээн авсан цагтай зөрүүг эвэнт
бүр дээр хөвөгч дунджаар (EWMA) хөтөлнө — сүлжээний саатал ганц эвэнтэд
нөлөөлж болох ч дундажид арилна. Тиймээс:
  • нэмэлт RPC байхгүй — камерын ховор нөөцөд огт нөлөөгүй
  • камер ажиллаж л байвал 5-10 эвэнтийн дотор зөрүү нь харагдана
  • Тохиргоо → Төхөөрөмж дээр камер бүрд badge, босго давбал УЛААН + WARNING лог

Тэмдэг: drift_sec > 0 = камерын цаг ТҮРҮҮЛЖ (ирээдүйд), < 0 = ХОЦОРЧ явна.
"""
import logging
import time
from datetime import datetime

from ..config import settings

log = logging.getLogger("parking.clock_drift")

# device_id → {"drift": EWMA сек, "n": эвэнтийн тоо, "at": monotonic,
#              "checked_at": UTC iso, "warned": threshold давсан үеийн drift}
_state: dict[str, dict] = {}
_ALPHA = 0.3          # EWMA жин — 5 эвэнтэд ~83% шинэ утга руу дөхнө
_REWARN_FACTOR = 1.5  # зөрүү өмнөх анхааруулгаас 1.5 дахин муудвал дахин логлоно


def extract_real_utc(raw: dict) -> float | None:
    """Эвэнтээс камерын жинхэнэ UTC цагийг олно.

    Зөвхөн `RealUTC`-д итгэнэ: дээд түвшний `UTC` болон `TrafficCar.UTC` нь
    нэрнээсээ үл хамааран ЛОКАЛ цагийн epoch байдаг нь батлагдсан (2026-08-22,
    camera_records-ийн ижил олдвор) тул тэдгээрээр хэмжвэл бүх камер «8 цаг
    түрүүлсэн» мэт худал дохио гарна."""
    if not isinstance(raw, dict):
        return None
    v = raw.get("RealUTC")
    if isinstance(v, (int, float)) and v > 1_500_000_000:   # 2017 оноос хойшхи бодит epoch
        return float(v)
    return None


def note_event(device_id: str, raw: dict, now: datetime | None = None) -> float | None:
    """Эвэнт бүр дээр дуудагдана. RealUTC байхгүй бол юу ч хийхгүй (None).

    Буцаах: шинэчлэгдсэн EWMA зөрүү (сек) — тестэд хэрэгтэй."""
    cam_utc = extract_real_utc(raw)
    if cam_utc is None:
        return None
    now = now or datetime.utcnow()
    drift = cam_utc - now.timestamp()   # >0 = камер түрүүлж
    st = _state.get(device_id)
    if st is None or time.monotonic() - st["at"] > 6 * 3600:
        # Шинэ эсвэл 6+ цаг чимээгүй байсан камер — дундажийг шинээр эхэлнэ
        st = _state[device_id] = {"drift": drift, "n": 0, "at": 0.0,
                                  "checked_at": "", "warned": 0.0}
    st["drift"] = _ALPHA * drift + (1 - _ALPHA) * st["drift"]
    st["n"] += 1
    st["at"] = time.monotonic()
    st["checked_at"] = now.isoformat()
    _maybe_warn(device_id, st)
    return st["drift"]


def _maybe_warn(device_id: str, st: dict) -> None:
    """Босго давсан үед НЭГ удаа WARNING (муудвал дахин) — лог бөглөхгүй."""
    d = abs(st["drift"])
    if st["n"] < 3 or d < settings.clock_drift_warn_sec:
        if d < settings.clock_drift_warn_sec:
            st["warned"] = 0.0   # эргэж хэвийн болбол дараагийн давалтад дахин дохионо
        return
    if st["warned"] and d < st["warned"] * _REWARN_FACTOR:
        return
    st["warned"] = d
    log.warning("КАМЕРЫН ЦАГ ЗӨРСӨН [device %s]: %s — NTP-гээ алдсан байх "
                "магадлалтай (камерын тохиргоонд заасан NTP сервер хүрэгдэхгүй "
                "байгаа эсэхийг шалга). Нөхөлт/тайлангийн цаг гажина.",
                device_id, describe(st["drift"]))


def describe(drift_sec: float) -> str:
    """Хүнд ойлгомжтой хэлбэр: «1ц 43м түрүүлж» / «34с хоцорч»."""
    d = abs(drift_sec)
    if d >= 3600:
        txt = f"{int(d // 3600)}ц {int(d % 3600 // 60)}м"
    elif d >= 60:
        txt = f"{int(d // 60)}м {int(d % 60)}с"
    else:
        txt = f"{int(d)}с"
    return f"{txt} {'түрүүлж' if drift_sec > 0 else 'хоцорч'} явна"


def device_drift(device_id: str) -> dict | None:
    """UI-д зориулсан төлөв: {drift_sec, n, checked_at, note}. Хэмжээгүй бол None.

    note нь зөвхөн босго давсан үед бөглөгдөнө — UI үүгээр УЛААН болгоно."""
    st = _state.get(device_id)
    if not st or st["n"] < 1:
        return None
    over = st["n"] >= 3 and abs(st["drift"]) >= settings.clock_drift_warn_sec
    return {
        "drift_sec": round(st["drift"], 1),
        "n": st["n"],
        "checked_at": st["checked_at"],
        "text": describe(st["drift"]),
        "note": (f"Камерын цаг серверээс {describe(st['drift'])} — NTP-гээ "
                 "алдсан байх магадлалтай. Камерын Тохиргоо → Цаг хэсэгт NTP "
                 "сервер хүрэгдэж буй эсэхийг шалгаад, шаардлагатай бол "
                 "tools/camera_clock_check.py --fix ажиллуул. Энэ зөрүүтэй үед "
                 "нөхөлтийн цаг, төлбөр, ТЕГ тулгалт бүгд гажина.") if over else None,
    }
