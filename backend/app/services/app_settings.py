"""UI-аас тохируулдаг систем дүрмүүд (app_settings хүснэгт).

.env-ийн тохиргоо deploy шаарддаг тул өдөр тутам өөрчлөгддөг дүрмийг (хар
жагсаалтад ямар нөхцөлд орох, өртэй машиныг саатуулах эсэх) DB-д хадгалж
UI-аас удирдана. Уншилт нь халуун зам (event бүрд) тул богино TTL кэштэй.
"""
import time

BLACKLIST_KEY = "blacklist_rules"

# Дүрмийн default — DB-д мөр байхгүй/талбар дутуу бол эдгээр үйлчилнэ.
BLACKLIST_DEFAULTS = {
    # Автоматаар хар жагсаалтад оруулах эсэх ба босго
    "auto_enabled": True,
    "debt_count": 3,          # энэ тооны төлөгдөөгүй өр хурамагц хориглоно (0=унтраах)
    "debt_amount": 0,         # эсвэл нийт өрийн дүн энэ хэмжээнд хүрвэл (0=унтраах)
    # Орох хаалт: хар жагсаалтын машиныг ХОРИГЛОХ уу, эсвэл нэвтрүүлээд
    # операторт анхааруулга өгөх үү (2026-08-09-ний шийдвэр: анхааруулга)
    "block_entry": False,
    # Гарах хаалт: энэ тооноос дээш өртэй машиныг саатуулж өрийг нь авна
    # (0 = саатуулахгүй). Өмнөх хатуу кодлогдсон 3-тай ижил default.
    "block_exit_debt_count": 3,
}


_cache: tuple[float, dict] | None = None
_CACHE_SEC = 30.0


def get_blacklist_rules(db) -> dict:
    """Хар жагсаалтын дүрэм (default дээр DB-ийн утгыг давхарлана)."""
    global _cache
    if _cache and time.monotonic() - _cache[0] < _CACHE_SEC:
        return _cache[1]
    from ..models import AppSetting
    rules = dict(BLACKLIST_DEFAULTS)
    try:
        row = db.get(AppSetting, BLACKLIST_KEY)
        if row and isinstance(row.value, dict):
            rules.update({k: v for k, v in row.value.items() if k in BLACKLIST_DEFAULTS})
    except Exception:  # noqa: BLE001 — тохиргоо уншиж чадахгүй бол default-аар үргэлжилнэ
        pass
    _cache = (time.monotonic(), rules)
    return rules


def set_blacklist_rules(db, values: dict, username: str) -> dict:
    """Дүрмийг хадгална (зөвхөн мэдэгдэж буй түлхүүр, төрлөө шалгана)."""
    global _cache
    from ..models import AppSetting
    clean: dict = {}
    for k, default in BLACKLIST_DEFAULTS.items():
        if k not in values:
            continue
        v = values[k]
        if isinstance(default, bool):
            clean[k] = bool(v)
        else:
            try:
                clean[k] = max(0, int(v))
            except (TypeError, ValueError):
                continue
    row = db.get(AppSetting, BLACKLIST_KEY)
    if row is None:
        row = AppSetting(key=BLACKLIST_KEY, value={})
        db.add(row)
    merged = dict(row.value or {})
    merged.update(clean)
    row.value = merged
    row.updated_by = username
    _cache = None  # дараагийн уншилт шинэ утгыг авна
    return {**BLACKLIST_DEFAULTS, **merged}


def invalidate_cache():
    global _cache
    _cache = None
