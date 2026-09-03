"""UI-аас тохируулдаг систем дүрмүүд (app_settings хүснэгт).

.env-ийн тохиргоо deploy шаарддаг тул өдөр тутам өөрчлөгддөг дүрмийг (хар
жагсаалтад ямар нөхцөлд орох, зогсоолыг хэзээ авто цэвэрлэх) DB-д хадгалж
UI-аас удирдана. Уншилт нь халуун зам (event/30 мин тутам) тул богино TTL кэштэй.

Шинэ бүлэг дүрэм нэмэхдээ DEFAULTS-д нэг мөр нэмнэ — үлдсэн нь автоматаар
(get/set/валидаци/кэш) ажиллана.
"""
import re
import time

BLACKLIST_KEY = "blacklist_rules"
OPEN_REASONS_KEY = "open_reasons"
AUTOCLOSE_KEY = "autoclose_rules"
ENTRYPLATE_KEY = "entry_plate_rules"
EXITRULES_KEY = "exit_rules"
BARRIER_KEY = "barrier_rules"
DRIVERTYPE_KEY = "driver_type_rules"
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
        # ── ӨР ҮҮСГЭХ БУСАД ЗАМУУД (2026-08-21) ────────────────────────────
        # Өмнө нь эдгээр нь кодод ХАТУУ бичигдсэн байсан тул «өр дахин
        # хуримтлагдаж байна» гэдэгт зөвхөн deploy-оор л нөлөөлж болдог байв.
        # Одоо тус бүрийг нь энэ хуудсаас унтраана.
        "create_debt_unpaid_exit": True,   # гарцад уншигдсан ч төлөөгүй (баримттай авлага)
        "create_debt_reentry": True,       # төлөлгүй үлдсэн машин ДАХИН орж ирэхэд
        "create_debt_shift_close": True,   # ээлж хаахад «бүх машиныг гаргах» сонгосон үед
        "create_debt_night_close": True,   # шөнийн бөөнөөр хаалтад
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
    ENTRYPLATE_KEY: {
        # Орох хаалт: ФОРМАТ БУРУУ уншигдсан дугаарыг яах вэ (2026-08-21).
        # Хангарьд дээр 5-7 оронтой хог уншилт session болж, гарахдаа
        # «бүртгэлгүй гарах оролдлого» үүсгэдэг байсны хариу арга.
        #   open   — одоогийн зан төлөв: шууд нээнэ
        #   hold   — hold_seconds хүлээж камерын ДАХИН уншилтыг (burst autocorrect)
        #            хүлээнэ; зөв уншилт ирэхгүй бол НЭЭГЭЭД тэмдэглэнэ (fail-open)
        #   strict — хүлээгээд ирэхгүй бол НЭЭХГҮЙ, операторт мэдэгдэнэ
        # Хатуу хаалтыг default болгодоггүй шалтгаан: гадаад/түр дугаар, бохир
        # дугаар regex-д хэзээ ч таарахгүй байж болно — машин эгнээ хааж гацна.
        "policy": "hold",
        "hold_seconds": 4,       # burst цонх (6с)-оос богино байх нь зүйтэй
        # Зогсоол бүрийн давхарга: {site_id: policy}. Хоосон = глобал policy.
        "site_overrides": {},
    },
    EXITRULES_KEY: {
        # Гарах хаалтны дүрэм. no_session_fee: орох уншилтгүй, гэрээт биш машин
        # гарцад ирвэл нэхэмжлэх СУУРЬ ХУРААМЖ (₮). 0 = унтраах (хуучин зан:
        # операторт мэдэгдээд хүлээнэ). Registered-only болон төлбөргүй
        # (no_charge) зогсоолд үйлчлэхгүй. Формат буруу (junk) уншилтад мөн
        # үйлчлэхгүй — хог дугаарт нэхэмжлэл үүсгэхгүй.
        "no_session_fee": 2000,
        # Зогсоол бүрийн давхарга: {site_id: дүн}. Хоосон = глобал дүн.
        # ХУУЧИН механизм — шинэ UI нь нийтлэг `_sites` давхаргад бичнэ, энэ нь
        # нийцтэй байдлын үүднээс уншигдсаар байна (доор no_session_exit_fee).
        "site_overrides": {},
        # ── 2026-09-03: өмнө нь КОДОД / .env-д хатуу байсан дүрмүүд ──────────
        # Орж ирээд N СЕКУНДЫН ДОТОР гарах камерт уншуулбал хаалт НЭЭХГҮЙ.
        # «Хуурамч гарц» схемийн эсрэг (орох-гарах уншуулаад дотроо үлдэх —
        # session үнэгүй хаагдаж өдөржин үнэгүй зогсдог). 0 = унтраах.
        # Гэрээт машинд үйлчлэхгүй (тэд ямар ч байсан үнэгүй).
        "min_stay_seconds": 0,
        # Гарах уншилттай ч ийм богино зогсолт нь «хуурамч гарц» гэж сэжиглэгдэж,
        # машин ДАХИН гарцад ирэхэд хаагдсан бүртгэл нь сэргээгдэнэ
        # (.env: suspicious_exit_minutes байсан).
        "fake_exit_minutes": 2,
        # Гарах уншилтаар авто сэргээх дээд хугацаа (кодод REOPEN_MAX_HOURS=48).
        "reopen_max_hours": 48,
        # Гарах хаалтан дээр данснаас автомат хасалт хийх эсэх.
        "wallet_auto_deduct": True,
    },
    BARRIER_KEY: {
        # Хаалт/уншилтын ЦАГИЙН цонхнууд — өмнө нь зөвхөн .env-д байсан тул
        # зогсоол бүрийн онцлогт (эгнээний тоо, урсгалын хурд) тохируулах
        # боломжгүй, өөрчлөхөд deploy шаарддаг байв (2026-09-03).
        # Давхар уншилтыг нэг машин гэж үзэх цонх (сек).
        "dedup_seconds": 30,
        # Нэг эгнээнд энэ хугацаанд ирсэн уншилтууд = НЭГ машин (сек).
        "entry_burst_seconds": 6,
        # Амжилттай нээснээс хойш дахин команд илгээхгүй завсар (сек).
        "reopen_cooldown_sec": 5,
        # Давхар уншилт дээр ГАРАХ хаалтыг дахин нээх эсэх (эрхтэй машинд).
        # Унтраавал: хаалт нээгдээгүй гацсан машин dedup цонх дуустал хүлээнэ.
        "exit_dedup_reopen": True,
    },
    DRIVERTYPE_KEY: {
        # «Шөнө үнэгүй» (NIGHT) гэрээний төрлийн ГЛОБАЛ цагийн цонх (УБ цагаар,
        # "HH:MM"). from > until = шөнө дамнасан цонх (billing.free_window_minutes
        # дэмждэг). Жолооч бүр дээр free_from/free_until тавьсан бол тэр нь
        # энэ глобал цонхыг ДАРНА. Excel импортоор олон машиныг NIGHT төрлөөр
        # оруулахад ажилтан цаг бөглөх шаардлагагүй болгох зорилготой (2026-09-01).
        "night_from": "21:00",
        "night_until": "08:00",
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

# .env-ийн ОДООГИЙН утга нь эдгээр түлхүүрийн анхдагч болно — DB-д ГАРААР
# тохируулаагүй л бол зогсоол өнөөдрийнхтэй ЯГ ИЖИЛ зан төлөвтэй үлдэнэ.
# Уншилт бүрд ШИНЭЭР авна (import-д царцаахгүй): .env-ийн утга шинэчлэгдэхэд
# эсвэл тестээс `settings.*` өөрчлөхөд шууд үйлчилнэ.
ENV_FALLBACK: dict[tuple[str, str], str] = {
    (BARRIER_KEY, "dedup_seconds"): "lpr_dedup_seconds",
    (BARRIER_KEY, "entry_burst_seconds"): "entry_burst_seconds",
    (BARRIER_KEY, "reopen_cooldown_sec"): "barrier_reopen_cooldown_sec",
    (EXITRULES_KEY, "fake_exit_minutes"): "suspicious_exit_minutes",
}


def _base(key: str) -> dict:
    """DEFAULTS дээр .env-ийн одоогийн утгыг давхарласан анхдагч багц."""
    out = dict(DEFAULTS[key])
    if not any(g == key for g, _ in ENV_FALLBACK):
        return out
    try:
        from ..config import settings as _env
    except Exception:  # noqa: BLE001 — config уншигдахгүй бол тогтмолууд хэвээр
        return out
    for (g, k), attr in ENV_FALLBACK.items():
        if g != key:
            continue
        try:
            out[k] = max(0, int(getattr(_env, attr)))
        except (AttributeError, TypeError, ValueError):
            pass
    return out

# ── ЗОГСООЛ БҮРИЙН ДАВХАРГА ────────────────────────────────────────────────
# Бүлэг бүрийн DB мөрөнд `_sites` гэсэн НӨӨЦЛӨГДСӨН түлхүүр байна:
#     {"_sites": {"<site_id>": {"stale_hours": 24, ...}}}
# Энэ нь DEFAULTS-д БАЙХГҮЙ тул дүрэм хэрэглэгч код руу хэзээ ч задардаггүй —
# зөвхөн `get_rules(db, key, site_id=...)` дуудахад тухайн зогсоолын утгууд
# глобалыг дарж буцна. Ингэснээр «энэ тохиргоо зарим зогсоолд хэрэгжих
# боломжгүй» гэсэн асуудал нэг механизмаар шийдэгдэнэ (2026-09-03).
SITE_OVERLAY = "_sites"

# Зогсоол бүрээр ДАРЖ болох түлхүүрүүд. Энд БАЙХГҮЙ түлхүүр зөвхөн глобал —
# UI мөн үүгээр «глобал» гэж тэмдэглэнэ (ж: e-Barimt/НӨАТ нь ТТД-тэй уялдаатай
# тул зогсоол бүрээр салгаж болохгүй).
PER_SITE: dict[str, set[str]] = {
    BLACKLIST_KEY: {"auto_enabled", "debt_count", "debt_amount",
                    "block_entry", "block_exit_debt_count"},
    AUTOCLOSE_KEY: {"enabled", "stale_hours", "awaiting_hours", "entry_only_free_hours",
                    "invalid_plate_hours", "create_debt", "create_debt_unpaid_exit",
                    "create_debt_reentry", "create_debt_shift_close",
                    "create_debt_night_close"},
    ENTRYPLATE_KEY: {"policy", "hold_seconds"},
    EXITRULES_KEY: {"no_session_fee", "min_stay_seconds", "fake_exit_minutes",
                    "reopen_max_hours", "wallet_auto_deduct"},
    BARRIER_KEY: {"dedup_seconds", "entry_burst_seconds", "reopen_cooldown_sec",
                  "exit_dedup_reopen"},
    DRIVERTYPE_KEY: {"night_from", "night_until"},
}

# Сонголттой (enum) утгын зөвшөөрөгдөх багц — (бүлэг, түлхүүр) бүрээр.
# dict төрлийн түлхүүрт БҮХ утга нь энэ багцад багтах ёстой (site_overrides г.м).
_POLICY_CHOICES = {"open", "hold", "strict"}
CHOICES: dict[tuple[str, str], set] = {
    (ENTRYPLATE_KEY, "policy"): _POLICY_CHOICES,
    (ENTRYPLATE_KEY, "site_overrides"): _POLICY_CHOICES,
}

_cache: dict[str, tuple[float, dict]] = {}
_CACHE_SEC = 30.0


def _load(db, key: str) -> tuple[dict, dict]:
    """(DB-д ЯВЦТАЙ бичигдсэн глобал утгууд, зогсоол бүрийн давхарга) — кэштэй.

    Анхдагчийг ЭНД холихгүй: `_base()` нь .env-ээс амьдаар уншдаг тул кэшлэвэл
    хуучирна. Давхарга нь түүхий (валидацилагдаагүй) байж болно."""
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < _CACHE_SEC:
        return hit[1], hit[2]
    from ..models import AppSetting
    stored, overlay = {}, {}
    try:
        row = db.get(AppSetting, key)
        if row and isinstance(row.value, dict):
            stored = {k: v for k, v in row.value.items() if k in DEFAULTS[key]}
            raw = row.value.get(SITE_OVERLAY)
            if isinstance(raw, dict):
                overlay = {str(sid): dict(v) for sid, v in raw.items() if isinstance(v, dict)}
    except Exception:  # noqa: BLE001 — тохиргоо уншиж чадахгүй бол default-аар үргэлжилнэ
        pass
    _cache[key] = (time.monotonic(), stored, overlay)
    return stored, overlay


def _coerce(default, v):
    """DB-д мөр хэлбэрээр хадгалагдсан утгыг DEFAULTS-ийн ТӨРӨЛД буулгана.
    Хөрвүүлж чадахгүй бол None → дуудагч глобал утгыг хэвээр үлдээнэ."""
    try:
        if isinstance(default, bool):
            return v if isinstance(v, bool) else str(v).strip().lower() in ("1", "true", "on", "yes")
        if isinstance(default, str):
            return str(v).strip()[:30] or None
        if isinstance(default, dict):
            return None                      # dict төрлийн түлхүүр давхаргад ордоггүй
        return max(0, int(float(v)))
    except (TypeError, ValueError):
        return None


def get_rules(db, key: str, site_id: str | None = None) -> dict:
    """Дүрмийн бүлэг: DEFAULTS → глобал (DB) → ЗОГСООЛЫН давхарга.

    site_id өгвөл тухайн зогсоолын дарсан утгууд нэмэгдэнэ. `PER_SITE`-д
    зөвшөөрөгдөөгүй эсвэл төрөл нь таарахгүй түлхүүр АВТОМАТААР алгасагдана —
    буруу тохиргоо биллингийг унагаахгүй."""
    stored, overlay = _load(db, key)
    rules = {**_base(key), **stored}
    site = overlay.get(site_id or "") if site_id else None
    if not site:
        return rules
    allowed_keys = PER_SITE.get(key, set())
    out = dict(rules)
    for k, v in site.items():
        if k not in allowed_keys or k not in DEFAULTS[key]:
            continue
        cv = _coerce(DEFAULTS[key][k], v)
        if cv is None:
            continue
        allowed = CHOICES.get((key, k))
        if allowed and cv not in allowed:
            continue
        out[k] = cv
    return out


def get_site_overrides(db, key: str) -> dict:
    """Бүлгийн зогсоол бүрийн түүхий давхарга ({site_id: {түлхүүр: утга}})."""
    return _load(db, key)[1]


def set_site_rules(db, key: str, site_id: str, values: dict, username: str) -> dict:
    """Нэг зогсоолын давхаргыг шинэчилнэ. Утга нь None/"" бол ТУХАЙН түлхүүрийг
    устгаж глобал руу буцаана; бүх түлхүүр уствал зогсоолын мөр өөрөө арилна."""
    from ..models import AppSetting
    if key not in DEFAULTS:
        raise ValueError(f"мэдэгдэхгүй бүлэг: {key}")
    allowed_keys = PER_SITE.get(key, set())
    row = db.get(AppSetting, key)
    if row is None:
        row = AppSetting(key=key, value={})
        db.add(row)
    stored = dict(row.value or {})
    overlay = {str(k): dict(v) for k, v in (stored.get(SITE_OVERLAY) or {}).items()
               if isinstance(v, dict)}
    site = dict(overlay.get(site_id) or {})
    for k, v in (values or {}).items():
        if k not in allowed_keys:
            continue                      # энэ түлхүүр зогсоол бүрээр тохируулагддаггүй
        if v is None or (isinstance(v, str) and not v.strip()):
            site.pop(k, None)             # глобал руу буцаана
            continue
        cv = _coerce(DEFAULTS[key][k], v)
        if cv is None:
            continue
        allowed = CHOICES.get((key, k))
        if allowed and cv not in allowed:
            continue
        site[k] = cv
    if site:
        overlay[site_id] = site
    else:
        overlay.pop(site_id, None)
    stored[SITE_OVERLAY] = overlay
    row.value = stored
    row.updated_by = username
    _cache.pop(key, None)
    return site


def set_rules(db, key: str, values: dict, username: str) -> dict:
    """Дүрмийг хадгална (зөвхөн мэдэгдэж буй түлхүүр, төрлөө шалгана)."""
    from ..models import AppSetting
    clean: dict = {}
    for k, default in DEFAULTS[key].items():
        if k not in values:
            continue
        v = values[k]
        allowed = CHOICES.get((key, k))
        if isinstance(default, bool):
            clean[k] = bool(v)
        elif isinstance(default, str):
            v = str(v).strip()[:30]
            if allowed and v not in allowed:
                continue                      # мэдэхгүй утга — хуучин нь хэвээр
            clean[k] = v
        elif isinstance(default, dict):
            # {гадаад түлхүүр: утга} хэлбэрийн давхарга (site_overrides г.м) —
            # бүхэлд нь дарж бичигдэнэ, буруу утгатай мөрүүд нь хаягдана
            if isinstance(v, dict):
                clean[k] = {str(kk)[:64]: str(vv).strip()[:30]
                            for kk, vv in v.items()
                            if not allowed or str(vv).strip() in allowed}
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
    return {**_base(key), **{k: v for k, v in merged.items() if k in DEFAULTS[key]}}


# ── Тохиромжтой нэрийн богиносголууд ────────────────────────────────────────
def get_blacklist_rules(db, site_id: str | None = None) -> dict:
    return get_rules(db, BLACKLIST_KEY, site_id)


def set_blacklist_rules(db, values: dict, username: str) -> dict:
    return set_rules(db, BLACKLIST_KEY, values, username)


def get_autoclose_rules(db, site_id: str | None = None) -> dict:
    return get_rules(db, AUTOCLOSE_KEY, site_id)


def set_autoclose_rules(db, values: dict, username: str) -> dict:
    return set_rules(db, AUTOCLOSE_KEY, values, username)


def get_entry_plate_rules(db, site_id: str | None = None) -> dict:
    return get_rules(db, ENTRYPLATE_KEY, site_id)


def set_entry_plate_rules(db, values: dict, username: str) -> dict:
    return set_rules(db, ENTRYPLATE_KEY, values, username)


def entry_plate_policy(db, site_id: str | None) -> tuple[str, int]:
    """Тухайн зогсоолд үйлчлэх (policy, hold_seconds).
    Дараалал: ЗОГСООЛЫН давхарга (`_sites`) → хуучин `site_overrides` → глобал."""
    r = get_rules(db, ENTRYPLATE_KEY, site_id)
    pol = r["policy"]
    if site_id and site_id not in (get_site_overrides(db, ENTRYPLATE_KEY) or {}):
        pol = (r.get("site_overrides") or {}).get(site_id) or pol
    return (pol if pol in _POLICY_CHOICES else "hold"), max(1, int(r["hold_seconds"]))


def get_exit_rules(db, site_id: str | None = None) -> dict:
    return get_rules(db, EXITRULES_KEY, site_id)


def get_barrier_rules(db, site_id: str | None = None) -> dict:
    """Хаалт/уншилтын цагийн цонхнууд (dedup/burst/cooldown) — зогсоолоор."""
    return get_rules(db, BARRIER_KEY, site_id)


def set_barrier_rules(db, values: dict, username: str) -> dict:
    return set_rules(db, BARRIER_KEY, values, username)


def set_exit_rules(db, values: dict, username: str) -> dict:
    return set_rules(db, EXITRULES_KEY, values, username)


def no_session_exit_fee(db, site_id: str | None) -> int:
    """Тухайн зогсоолд үйлчлэх «орох уншилтгүй машины суурь хураамж» (₮).
    site_overrides нь глобал дүнг дарна; утгууд DB-д мөр (string) хэлбэрээр
    хадгалагдаж болох тул int руу хамгаалалттай хөрвүүлнэ. 0 = унтраалттай."""
    r = get_rules(db, EXITRULES_KEY, site_id)
    raw = r.get("no_session_fee", 0)
    # Хуучин `site_overrides` механизм — зөвхөн шинэ давхаргад энэ зогсоолын
    # мөр БАЙХГҮЙ үед л үйлчилнэ (шинэ UI нь `_sites`-д бичдэг).
    if site_id and "no_session_fee" not in (get_site_overrides(db, EXITRULES_KEY)
                                            .get(site_id) or {}):
        legacy = (r.get("site_overrides") or {}).get(site_id)
        if legacy not in (None, ""):
            raw = legacy
    try:
        return max(0, int(float(raw)))
    except (TypeError, ValueError):
        return 0


_HHMM = re.compile(r"^([01]?\d|2[0-3]):[0-5]\d$")


def get_driver_type_rules(db, site_id: str | None = None) -> dict:
    return get_rules(db, DRIVERTYPE_KEY, site_id)


def set_driver_type_rules(db, values: dict, username: str) -> dict:
    return set_rules(db, DRIVERTYPE_KEY, values, username)


def night_window(db, site_id: str | None = None) -> tuple[str, str]:
    """NIGHT төрлийн хүчинтэй цонх — буруу/дутуу тохиргоонд default (21:00–08:00)
    руу унана: биллинг хэзээ ч «цонхгүй = бүх цагт үнэгүй» болж алдахгүй."""
    r = get_rules(db, DRIVERTYPE_KEY, site_id)
    f = str(r.get("night_from") or "").strip()
    u = str(r.get("night_until") or "").strip()
    if _HHMM.match(f) and _HHMM.match(u) and f != u:
        return f, u
    return "21:00", "08:00"


def get_camsync_rules(db) -> dict:
    return get_rules(db, CAMSYNC_KEY)


def set_camsync_rules(db, values: dict, username: str) -> dict:
    return set_rules(db, CAMSYNC_KEY, values, username)


# ── НЭЭХ ШАЛТГААН — удирдлагатай жагсаалт ───────────────────────────────────
# Хаалтыг гараар нээх / машиныг төлбөргүй гаргах бүрд оператор ЭНЭ ЖАГСААЛТААС
# сонгоно. Өмнө нь чөлөөт текст байсан тул «хэн, ямар шалтгаанаар хэдэн удаа
# үнэгүй гаргасан» гэдгийг тоолох боломжгүй байв — бүх мөр өөр өөрөөр бичигдэнэ.
OPEN_REASON_DEFAULTS = [
    {"code": "vip", "label": "VIP / гэрээт зочин", "is_active": True},
    {"code": "staff", "label": "Ажилтны машин", "is_active": True},
    {"code": "wrong_plate", "label": "Дугаар буруу уншсан", "is_active": True},
    {"code": "no_session", "label": "Бүртгэл олдоогүй", "is_active": True},
    {"code": "system_error", "label": "Системийн алдаа", "is_active": True},
    {"code": "device_fault", "label": "Хаалт/камер эвдэрсэн", "is_active": True},
    {"code": "emergency", "label": "Онцгой байдал (түргэн, гал)", "is_active": True},
    {"code": "test", "label": "Туршилт", "is_active": True},
    {"code": "other", "label": "Бусад", "is_active": True},
]


def get_open_reasons(db, active_only: bool = False) -> list[dict]:
    """Нээх шалтгааны жагсаалт (тохируулаагүй бол анхдагч)."""
    rows = get_state(db, OPEN_REASONS_KEY).get("items")
    if not isinstance(rows, list) or not rows:
        rows = OPEN_REASON_DEFAULTS
    out = [r for r in rows if isinstance(r, dict) and r.get("code") and r.get("label")]
    return [r for r in out if r.get("is_active", True)] if active_only else out


def set_open_reasons(db, items: list, username: str) -> list[dict]:
    """Жагсаалтыг бүхэлд нь дарж бичнэ. `code` нь давхардахгүй, тогтмол байх ёстой
    (тайлан хуучин мөрүүдийг кодоор нь бүлэглэдэг)."""
    clean, seen = [], set()
    for r in items or []:
        if not isinstance(r, dict):
            continue
        code = re.sub(r"[^a-z0-9_]", "", str(r.get("code") or "").strip().lower())[:30]
        label = str(r.get("label") or "").strip()[:80]
        if not code or not label or code in seen:
            continue
        seen.add(code)
        clean.append({"code": code, "label": label, "is_active": bool(r.get("is_active", True))})
    if not clean:
        raise ValueError("Дор хаяж нэг шалтгаан үлдээх ёстой")
    set_state(db, OPEN_REASONS_KEY, {"items": clean}, username)
    return clean


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
