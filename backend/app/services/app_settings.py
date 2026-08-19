"""UI-аас тохируулдаг систем дүрмүүд (app_settings хүснэгт).

.env-ийн тохиргоо deploy шаарддаг тул өдөр тутам өөрчлөгддөг дүрмийг (хар
жагсаалтад ямар нөхцөлд орох, зогсоолыг хэзээ авто цэвэрлэх) DB-д хадгалж
UI-аас удирдана. Уншилт нь халуун зам (event/30 мин тутам) тул богино TTL кэштэй.

Шинэ бүлэг дүрэм нэмэхдээ DEFAULTS-д нэг мөр нэмнэ — үлдсэн нь автоматаар
(get/set/валидаци/кэш) ажиллана.
"""
import time

BLACKLIST_KEY = "blacklist_rules"
AUTOCLOSE_KEY = "autoclose_rules"
CAMSYNC_KEY = "camsync_rules"
CAMHEALTH_KEY = "camhealth_rules"
# Дүрэм БИШ, ТӨЛӨВ (watermark г.м) — валидацигүй, чөлөөт JSON
CAMSYNC_STATE = "camsync_state"
CAMHEALTH_STATE = "camhealth_state"
# msgbill.mn глобал тохиргоо (UI-аас): {api_key: шифрлэгдсэн, methods: "TRANSFER,..."}
# — прод серверийн .env-д SSH-гүй хүрэхэд зориулав; .env нь fallback.
MSGBILL_STATE = "msgbill_global"

# Түлхүүр бүрийн default. Утгын ТӨРӨЛ нь валидацийн дүрэм болно (bool/int).
DEFAULTS: dict[str, dict] = {
    BLACKLIST_KEY: {
        # Автоматаар хар жагсаалтад оруулах эсэх ба босго
        "auto_enabled": True,
        "debt_count": 3,          # энэ тооны төлөгдөөгүй өр хуримтлагдвал (0=унтраах)
        "debt_amount": 0,         # эсвэл нийт өрийн дүн энэ хэмжээнд хүрвэл (0=унтраах)
        # Орох хаалт: хар жагсаалтын машиныг ХОРИГЛОХ уу, эсвэл нэвтрүүлээд
        # операторт анхааруулга өгөх үү (2026-08-09-ний шийдвэр: анхааруулга)
        "block_entry": False,
        # Гарах хаалт: энэ тооноос дээш өртэй машиныг саатуулж өрийг нь авна
        "block_exit_debt_count": 3,
    },
    AUTOCLOSE_KEY: {
        # Зогсоолд гацсан бүртгэлийг автоматаар хаах дүрмүүд (цагаар; 0=унтраах).
        # Зогсоол бүрийн онцлогийг site.auto_close_hours / entry_only_free_hours
        # дарж тохируулна — эдгээр нь СИСТЕМИЙН анхдагч.
        "enabled": True,
        "stale_hours": 12,          # ямар ч хөдөлгөөнгүй N цагийн дараа хаана
        # 2026-08-12: True → False. Гарах уншилтгүй хаагдсан машин ҮНЭНДЭЭ
        # хэдийнэ гарсан байдаг — тэдэнд өр нэхэх нотолгоо байхгүй. 7 хоногийн
        # аудитаар бүртгэсэн 39.6 сая₮ өрийн 99.5% нь ийм системийн гаралтай,
        # цуглуулалт 0.7%. Түүнээс гадна өр нь жолоочийг БЛОКЛОДОГ (QR нэхэмжлэл
        # 2+ мөр болох, автомат хар жагсаалт). Жинхэнэ авлага = unpaid_exit.
        "create_debt": False,       # хаахдаа төлөгдөөгүй дүнгээр өр үүсгэх эсэх
        "awaiting_hours": 2,        # гарах хаалтад уншигдсан ч төлөөгүй N цаг
        "entry_only_free_hours": 72,  # зөвхөн орох уншилттай (гарц уншаагүй) → үнэгүй
        "invalid_plate_hours": 2,   # формат буруу (junk) дугаар → үнэгүй хаана
    },
    CAMSYNC_KEY: {
        # Камерын дотоод логоос алдагдсан event-ийг нөхөж бүртгэх автомат sync.
        # ЧУХАЛ: watermark-аар ажиллана — нэг event ХОЁР УДАА боловсруулагдахгүй
        # (2026-08-10: 48ц-ийн лог бүхлээр нь дахин уншсанаас аль хэдийн
        # шийдэгдсэн машинууд дахин өр болсон).
        "enabled": False,          # анхдагчаар УНТРААЛТТАЙ — гараар асаана
        "times_per_day": 4,        # өдөрт хэдэн удаа (6 цаг тутам)
        "lookback_hours": 12,      # watermark байхгүй үед хамгийн ихдээ ухрах
        "min_age_minutes": 30,     # сүүлийн N минутын event-д хүрэхгүй (яг явж буй)
        # ӨРИЙН БОДЛОГО — ХОЁР ТЭС ӨӨР ТОХИОЛДЛЫГ САЛГАВ (2026-08-17).
        #
        # 2026-08-12-нд `create_debt` НЭГ шилжүүлэгчээр хоёуланг нь унтраасан
        # (тэр дүрэм 1,786 хуурамч өр = 3.3 сая₮ үүсгэсэн). Гэвч дараах хоёр
        # тохиолдол НОТОЛГООНЫ хувьд огт өөр:
        #
        #   • Гарах уншилт ОГТ БАЙХГҮЙ → машин хэзээ гарсныг МЭДЭХГҮЙ. Хугацаа
        #     нь таамаг тул өр нэхэх үндэслэлгүй. `create_debt` = False хэвээр.
        #   • Камерын логоор ГАРСАН нь ТОГТООГДСОН → камерын өөрийн бичлэг
        #     гарсан цагийг гэрчилнэ. Төлбөр нь ТЭР ЦАГААР зөв бодогдоно —
        #     энэ бол таамаг биш ЖИНХЭНЭ АВЛАГА.
        #
        # 2026-08-17 хэмжилт: Эрэл-13 дээр ГАНЦ ӨДӨРТ логоор гарсан нь
        # тогтоогдсон 112 машинд 334,000₮ бодогдоод бүртгэгдэлгүй өнгөрсөн.
        "create_debt": False,      # гарах уншилтгүй машинд өр үүсгэх (таамаг)
        "create_debt_log_exit": True,   # логоор ГАРСАН нь тогтоогдсонд (баримттай)
        "skip_invalid_plate": True,  # формат буруу (junk) дугаарыг алгасах
    },
    CAMHEALTH_KEY: {
        # Гацсан камерыг илрүүлж, шаардвал reboot хийх (snapshot эрүүл мэнд).
        # Гацсан = event стрим АМЬД (200) атлаа snapshot.cgi ШУУД 400 буцаана
        # (2026-08-10 Рашбулаг дээр батлагдсан — reboot л засдаг).
        "enabled": True,           # өдөрт хэдэн удаа шалгах эсэх
        "times_per_day": 4,        # 4 = 6 цаг тутам
        "auto_reboot": True,       # ГАЦСАН илэрвэл автоматаар reboot хийх эсэх
        "cooldown_min": 120,       # нэг камерыг дахин reboot хийхээс өмнөх завсар
        "samples": 3,              # snapshot.cgi-г хэдэн удаа шалгаж баталгаажуулах
    },
}

_cache: dict[str, tuple[float, dict]] = {}
_CACHE_SEC = 30.0


def get_rules(db, key: str) -> dict:
    """Дүрмийн бүлэг (default дээр DB-ийн утгыг давхарлана)."""
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < _CACHE_SEC:
        return hit[1]
    from ..models import AppSetting
    rules = dict(DEFAULTS[key])
    try:
        row = db.get(AppSetting, key)
        if row and isinstance(row.value, dict):
            rules.update({k: v for k, v in row.value.items() if k in DEFAULTS[key]})
    except Exception:  # noqa: BLE001 — тохиргоо уншиж чадахгүй бол default-аар үргэлжилнэ
        pass
    _cache[key] = (time.monotonic(), rules)
    return rules


def set_rules(db, key: str, values: dict, username: str) -> dict:
    """Дүрмийг хадгална (зөвхөн мэдэгдэж буй түлхүүр, төрлөө шалгана)."""
    from ..models import AppSetting
    clean: dict = {}
    for k, default in DEFAULTS[key].items():
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
    row = db.get(AppSetting, key)
    if row is None:
        row = AppSetting(key=key, value={})
        db.add(row)
    merged = dict(row.value or {})
    merged.update(clean)
    row.value = merged
    row.updated_by = username
    _cache.pop(key, None)  # дараагийн уншилт шинэ утгыг авна
    return {**DEFAULTS[key], **merged}


# ── Тохиромжтой нэрийн богиносголууд ────────────────────────────────────────
def get_blacklist_rules(db) -> dict:
    return get_rules(db, BLACKLIST_KEY)


def set_blacklist_rules(db, values: dict, username: str) -> dict:
    return set_rules(db, BLACKLIST_KEY, values, username)


def get_autoclose_rules(db) -> dict:
    return get_rules(db, AUTOCLOSE_KEY)


def set_autoclose_rules(db, values: dict, username: str) -> dict:
    return set_rules(db, AUTOCLOSE_KEY, values, username)


def get_camsync_rules(db) -> dict:
    return get_rules(db, CAMSYNC_KEY)


def set_camsync_rules(db, values: dict, username: str) -> dict:
    return set_rules(db, CAMSYNC_KEY, values, username)


# ── ТӨЛӨВ (watermark) — дүрэм биш тул валидацигүй, кэшлэхгүй ────────────────
def get_state(db, key: str) -> dict:
    """Чөлөөт JSON төлөв (ж: зогсоол бүрийн сүүлд боловсруулсан event цаг)."""
    from ..models import AppSetting
    try:
        row = db.get(AppSetting, key)
        return dict(row.value) if row and isinstance(row.value, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def set_state(db, key: str, value: dict, username: str = "system"):
    """Төлөвийг бүхэлд нь дарж бичнэ (caller commit хийнэ)."""
    from ..models import AppSetting
    row = db.get(AppSetting, key)
    if row is None:
        row = AppSetting(key=key, value={})
        db.add(row)
    row.value = dict(value)
    row.updated_by = username


def invalidate_cache():
    _cache.clear()


# Хуучин кодтой нийцтэй байх (тестүүд BLACKLIST_DEFAULTS-ыг ашигладаг)
BLACKLIST_DEFAULTS = DEFAULTS[BLACKLIST_KEY]
