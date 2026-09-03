"""Төлбөр/хаалтны БҮХ дүрмийн нэгдсэн бүртгэл ба зогсоол бүрийн шийдэл.

Яагаад хэрэгтэй вэ (2026-09-03):
    Төлбөр тооцох, хаалт нээх шийдвэрт нөлөөлдөг дүрмүүд гурван өөр газар
    тархсан байсан — .env (deploy шаардана), `app_settings` (UI-тай ч глобал),
    зогсоолын багана (`parking_sites`), тарифын загвар. Үүнээс болж:
      • нэг зогсоолд тохирсон утга нөгөө зогсоолыг гацаадаг,
      • «төлбөрөө төлсөн атлаа хаалт нээгдэхгүй» тохиолдлыг ямар тохиргооны
        хослол үүсгэснийг хэн ч хэлж чаддаггүй байв.

Энэ модуль хоёр зүйл хийнэ:
  1. `catalog()` — дүрэм бүрийн ТОДОРХОЙЛОЛТ (аль бүлэг, юунд нөлөөлдөг,
     зогсоолоор тохируулж болох эсэх, ямар код замд уншигддаг).
  2. `site_report(db, site)` — тухайн зогсоолд ҮЙЛЧИЛЖ буй бодит утгууд +
     ЗӨРЧЛИЙН шалгалт (аль хослол машиныг гацаах вэ).

Дүрмийг ЭНД хэрэгжүүлдэггүй — зөвхөн уншиж, тайлбарлаж, шалгана. Хэрэгжилт нь
`session_logic`, `billing`, `auto_close` дотор хэвээр.
"""
from . import app_settings as A

# ── Дүрмийн бүртгэл ────────────────────────────────────────────────────────
# (бүлэг, түлхүүр, нэр, тайлбар, нэгж, ямар үед үйлчилдэг)
# unit: "hour" | "min" | "sec" | "mnt" | "count" | "bool" | "choice" | "time"
CATALOG: list[dict] = [
    # ── Гарах хаалт / төлбөрийн шийдвэр ─────────────────────────────────────
    {"group": A.EXITRULES_KEY, "key": "no_session_fee", "unit": "mnt",
     "name": "Орох цаг олдоогүй машины суурь хураамж",
     "desc": "Орох уншилтгүй, гэрээт БИШ машин гарцад ирвэл тогтмол дүн нэхэмжилнэ. "
             "Хэзээ орсныг мэдэхгүй тул цагаар бодох боломжгүй. 0 = унтраах "
             "(операторт мэдэгдээд хүлээнэ).",
     "applies": "Гарах уншилт · session олдоогүй үед",
     "not_applied": "Хаалттай (registered_only) ба төлбөргүй (no_charge) зогсоол; "
                    "формат буруу (junk) уншилт"},
    {"group": A.EXITRULES_KEY, "key": "min_stay_seconds", "unit": "sec",
     "name": "Хамгийн бага зогсолт — эрт гарахад хаалт нээхгүй",
     "desc": "Орж ирээд энэ хугацаанаас ЭРТ гарах камерт уншуулбал хаалт "
             "НЭЭГДЭХГҮЙ, бүртгэл ч хаагдахгүй. «Хуурамч гарц» схемийн эсрэг: "
             "жолооч орох-гарах хоёуланд уншуулаад дотроо үлдэж, бүртгэл нь "
             "үнэгүй хаагдсанаар өдөржин үнэгүй зогсдог. 0 = унтраах.",
     "applies": "Гарах уншилт · нээлттэй бүртгэлтэй, гэрээт БИШ машин",
     "not_applied": "Гэрээт машин (ямар ч байсан үнэгүй)"},
    {"group": A.EXITRULES_KEY, "key": "fake_exit_minutes", "unit": "min",
     "name": "«Хуурамч гарц» гэж сэжиглэх босго",
     "desc": "Гарах уншилттай ч зогсолт нь үүнээс богино байсан бүртгэлийг "
             "машин ДАХИН гарцад ирэхэд сэргээж, төлбөрийг орсон цагаас нь "
             "дахин бодно.",
     "applies": "Гарах уншилт · идэвхтэй бүртгэл олдоогүй үеийн авто сэргээлт",
     "not_applied": "Төлбөр төлөгдсөн бүртгэл"},
    {"group": A.EXITRULES_KEY, "key": "reopen_max_hours", "unit": "hour",
     "name": "Хаагдсан бүртгэлийг авто сэргээх дээд хугацаа",
     "desc": "Гарах камерт уншигдсан ч идэвхтэй бүртгэл алга бол энэ хугацаанд "
             "хаагдсан бүртгэлээс ЯГ НЭГ нэр дэвшигч олдвол сэргээнэ.",
     "applies": "Гарах уншилт · «бүртгэлгүй гарах» болохоос өмнө",
     "not_applied": "Төлөгдсөн бүртгэл; олон нэр дэвшигч; junk дугаар"},
    {"group": A.EXITRULES_KEY, "key": "wallet_auto_deduct", "unit": "bool",
     "name": "Данснаас автомат хасалт",
     "desc": "Гарах хаалтан дээр жолоочийн үлдэгдлээс төлбөрийг автоматаар "
             "хасна. Хүрэлцвэл хаалт шууд нээгдэнэ, хүрэлцэхгүй бол байгааг нь "
             "хасаад үлдсэнд нь QR гарна.",
     "applies": "Гарах уншилт · төлбөртэй машин",
     "not_applied": "Үнэгүй/гэрээт машин"},

    # ── Хаалт/уншилтын цагийн цонхнууд ──────────────────────────────────────
    {"group": A.BARRIER_KEY, "key": "dedup_seconds", "unit": "sec",
     "name": "Давхар уншилтын цонх",
     "desc": "Энэ хугацаанд дахин уншигдсан дугаарыг НЭГ машин гэж үзнэ — "
             "шинэ бүртгэл үүсгэхгүй. Хэт урт бол нэг эгнээгээр дараалсан "
             "хоёр машин нэг болж нийлнэ; хэт богино бол нэг машинд хоёр "
             "бүртгэл үүснэ.",
     "applies": "Орох ба гарах уншилт бүр",
     "not_applied": "Доторх (nested) хаалтны уншилт"},
    {"group": A.BARRIER_KEY, "key": "entry_burst_seconds", "unit": "sec",
     "name": "Цуврал уншилтын цонх (нэг эгнээ)",
     "desc": "НЭГ эгнээнд энэ хугацаанд ирсэн уншилтууд физикийн хувьд нэг "
             "машин гэж тооцогдож, дугаар нь хамгийн сүүлийн ЗӨВ уншилтаар "
             "залруулагдана.",
     "applies": "Орох уншилт · зөвхөн НЭГ камерын хүрээнд",
     "not_applied": "Логоос нөхөн тоглуулсан уншилт (log_tail/camsync)"},
    {"group": A.BARRIER_KEY, "key": "reopen_cooldown_sec", "unit": "sec",
     "name": "Хаалт дахин нээх завсар",
     "desc": "Амжилттай нээснээс хойш энэ хугацаанд шинэ «нээ» команд "
             "илгээхгүй. Хэт урт бол араас нь дагаж ирсэн машинд хаалт "
             "нээгдэхгүй.",
     "applies": "Бүх хаалт (орох/гарах/дотоод)",
     "not_applied": "Амжилтгүй болсон команд — тэр cooldown барихгүй"},
    {"group": A.BARRIER_KEY, "key": "exit_dedup_reopen", "unit": "bool",
     "name": "Давхар уншилт дээр гарах хаалтыг дахин нээх",
     "desc": "Эрхтэй (төлсөн/үнэгүй/гэрээт) машин хаалтны өмнө дахин уншигдвал "
             "хаалтыг ДАХИН нээнэ. УНТРААВАЛ: эхний команд амжилтгүй болсон "
             "машин давхар уншилтын цонх дуустал ГАЦНА.",
     "applies": "Гарах уншилт · давхар уншилтын зам",
     "not_applied": "—"},

    # ── Орох хаалт ──────────────────────────────────────────────────────────
    {"group": A.ENTRYPLATE_KEY, "key": "policy", "unit": "choice",
     "name": "Формат буруу дугаарт орох хаалтыг яах",
     "desc": "open = шууд нээнэ · hold = хэдэн секунд хүлээж дахин уншилт авна, "
             "ирэхгүй бол НЭЭНЭ · strict = ирэхгүй бол НЭЭХГҮЙ, оператор шийднэ.",
     "applies": "Орох уншилт · дугаар PLATE_RE-д таарахгүй үед",
     "not_applied": "Зөв форматтай уншилт"},
    {"group": A.ENTRYPLATE_KEY, "key": "hold_seconds", "unit": "sec",
     "name": "Дахин уншилт хүлээх хугацаа",
     "desc": "Цуврал уншилтын цонхноос БОГИНО байх нь зүйтэй — эс бол "
             "залруулга ирэхээс өмнө шийдвэр гарна.",
     "applies": "Орох уншилт · hold/strict горим",
     "not_applied": "open горим"},

    # ── Өр үүсгэх / гарахыг хорих ───────────────────────────────────────────
    {"group": A.BLACKLIST_KEY, "key": "block_exit_debt_count", "unit": "count",
     "name": "Хэдэн өртэй машиныг гарцад саатуулах",
     "desc": "Энэ тооноос дээш ТӨЛӨГДӨӨГҮЙ өртэй машин гарах хаалтад "
             "автоматаар нээгдэхгүй — оператор өрийг цуглуулна. 0 = саатуулахгүй.",
     "applies": "Гарах уншилт · гэрээт БИШ машин",
     "not_applied": "Гэрээт машин (өр нь ихэвчлэн уншилт алдагдсаны артефакт)"},
    {"group": A.BLACKLIST_KEY, "key": "block_entry", "unit": "bool",
     "name": "Хар жагсаалтын машиныг ОРУУЛАХГҮЙ",
     "desc": "Унтраалттай үед машиныг оруулаад операторт анхааруулга өгнө "
             "(2026-08-09-ний шийдвэр): гадаа орхивол өрөө хэзээ ч төлөхгүй.",
     "applies": "Орох уншилт",
     "not_applied": "—"},
    {"group": A.BLACKLIST_KEY, "key": "auto_enabled", "unit": "bool",
     "name": "Автоматаар хар жагсаалтад оруулах",
     "desc": "Доорх босгод хүрсэн машиныг автоматаар хар жагсаалтад оруулна.",
     "applies": "Өр үүсэх бүрд", "not_applied": "—"},
    {"group": A.BLACKLIST_KEY, "key": "debt_count", "unit": "count",
     "name": "Хар жагсаалтын босго — өрийн ТОО",
     "desc": "Энэ тооны төлөгдөөгүй өр хуримтлагдвал. 0 = унтраах.",
     "applies": "Автомат хар жагсаалт", "not_applied": "—"},
    {"group": A.BLACKLIST_KEY, "key": "debt_amount", "unit": "mnt",
     "name": "Хар жагсаалтын босго — өрийн ДҮН",
     "desc": "Нийт өр энэ дүнд хүрвэл. 0 = унтраах.",
     "applies": "Автомат хар жагсаалт", "not_applied": "—"},

    # ── Авто цэвэрлэгээ (гацсан бүртгэл) ────────────────────────────────────
    {"group": A.AUTOCLOSE_KEY, "key": "enabled", "unit": "bool",
     "name": "Авто цэвэрлэгээ асаах",
     "desc": "Унтраавал гацсан бүртгэл өөрөө хаагдахаа болино — зөвхөн ээлж "
             "хаах/гараар цэвэрлэнэ.",
     "applies": "30 мин тутмын цэвэрлэгээ", "not_applied": "—"},
    {"group": A.AUTOCLOSE_KEY, "key": "invalid_plate_hours", "unit": "hour",
     "name": "Формат буруу (junk) дугаар — хаах хугацаа",
     "desc": "Ийм машин гарахдаа хэзээ ч таарахгүй тул хурдан цэвэрлэнэ. "
             "ӨР ҮҮСГЭХГҮЙ, үнэгүй хаана.",
     "applies": "OPEN/AWAITING бүртгэл · дугаар PLATE_RE-д таарахгүй",
     "not_applied": "Зөв форматтай дугаар"},
    {"group": A.AUTOCLOSE_KEY, "key": "awaiting_hours", "unit": "hour",
     "name": "Гарцад уншигдсан ч төлөөгүй — хаах хугацаа",
     "desc": "Гарах камерт уншигдсан хэрнээ N цаг хөдөлгөөнгүй бол дагаж "
             "гарсан гэж үзнэ. Төлбөр нь сүүлд харагдсан үеийн дүнгээр царцана.",
     "applies": "AWAITING_PAYMENT бүртгэл", "not_applied": "—"},
    {"group": A.AUTOCLOSE_KEY, "key": "entry_only_free_hours", "unit": "hour",
     "name": "Зөвхөн орох уншилттай — үнэгүй хаах хугацаа",
     "desc": "Гарах камерт огт уншигдаагүй бүртгэл — гарах уншилт алдагдсан "
             "байх магадлалтай тул ӨРГҮЙГЭЭР үнэгүй хаана.",
     "applies": "OPEN бүртгэл · exit_device_id хоосон",
     "not_applied": "—", "site_column": "entry_only_free_hours"},
    {"group": A.AUTOCLOSE_KEY, "key": "stale_hours", "unit": "hour",
     "name": "Ерөнхий хугацаа хэтэрсэн — хаах хугацаа",
     "desc": "Дээрхэд хамаарахгүй бүх гацсан бүртгэл.",
     "applies": "OPEN/AWAITING/PAID бүртгэл",
     "not_applied": "Сүүлийн 1 цагт хөдөлгөөнтэй бүртгэл",
     "site_column": "auto_close_hours"},
    {"group": A.AUTOCLOSE_KEY, "key": "create_debt", "unit": "bool",
     "name": "ӨР — авто хаалт, гарах уншилтгүй машинд",
     "desc": "Машин хэзээ гарсныг МЭДЭХГҮЙ тул хугацаа нь таамаг. 2026-08-12-ны "
             "аудитаар ийм дүрэм 1,786 хуурамч өр (3.3 сая₮) үүсгэсэн.",
     "applies": "Авто хаалт", "not_applied": "—"},
    {"group": A.AUTOCLOSE_KEY, "key": "create_debt_unpaid_exit", "unit": "bool",
     "name": "ӨР — гарцад уншигдсан ч төлөөгүй машинд",
     "desc": "Гарсан нь камерын уншилтаар БАРИМТТАЙ — жинхэнэ авлага.",
     "applies": "Авто хаалт · AWAITING_PAYMENT", "not_applied": "—"},
    {"group": A.AUTOCLOSE_KEY, "key": "create_debt_reentry", "unit": "bool",
     "name": "ӨР — төлөлгүй үлдсэн машин ДАХИН орж ирэхэд",
     "desc": "Өмнөх бүртгэлийг хааж, үлдэгдлээр нь нэхэмжлэл үүсгэнэ.",
     "applies": "Орох уншилт", "not_applied": "—"},
    {"group": A.AUTOCLOSE_KEY, "key": "create_debt_shift_close", "unit": "bool",
     "name": "ӨР — ээлж хаахад «бүх машиныг гаргах»",
     "desc": "Тэдгээр машин ихэнхдээ хэдийнэ явсан байдаг.",
     "applies": "Ээлж хаах", "not_applied": "—"},
    {"group": A.AUTOCLOSE_KEY, "key": "create_debt_night_close", "unit": "bool",
     "name": "ӨР — шөнийн бөөнөөр хаалтад",
     "desc": "Нэхэмжлэл хуудасны «Шөнийн хаалт» үйлдэлд.",
     "applies": "Шөнийн хаалт", "not_applied": "—"},

]

# Зогсоолын БАГАНААР тохируулагддаг (app_settings-д биш) дүрмүүд — UI-д
# «Зогсоол» табын засах цонх руу заана.
SITE_COLUMN_RULES = [
    {"key": "no_charge", "unit": "bool", "name": "Төлбөр АВАХГҮЙ зогсоол",
     "desc": "Цаг тоолохгүй, бүх машин 0₮. Ажилчдын/дотоод зогсоолд."},
    {"key": "registered_only", "unit": "bool", "name": "Зөвхөн гэрээт машин",
     "desc": "Бүртгэлгүй машинд орох хаалт нээгдэхгүй."},
    {"key": "auto_close_hours", "unit": "hour", "name": "Гацсан машины авто хаалт",
     "desc": "Хоосон = системийн анхдагч, 0 = энэ зогсоолд унтраах."},
    {"key": "entry_only_free_hours", "unit": "hour", "name": "Зөвхөн орох уншилттай",
     "desc": "Хоосон = системийн анхдагч, 0 = унтраах."},
    {"key": "transit_max_hours", "unit": "hour", "name": "Доторх зогсоолын тоолуур зогсох дээд хугацаа",
     "desc": "Зөвхөн nested (доторх) зогсоолд."},
    {"key": "barrier_close_sweep_min", "unit": "min", "name": "Онгорхой гацсан хаалтыг хаах давтамж",
     "desc": "0/хоосон = унтраалттай."},
]

# Тарифын загвараар (Тохиргоо → Тариф) тодорхойлогддог дүрмүүд
TARIFF_RULES = [
    {"key": "free_minutes", "unit": "min", "name": "Үнэгүй эхний хугацаа",
     "desc": "Энэ хугацаанд багтаж гарвал 0₮."},
    {"key": "grace_minutes", "unit": "min", "name": "Төлсний дараа гарах хугацаа",
     "desc": "Төлбөр төлсний дараа энэ хугацаанд гарах ёстой. ХЭТЭРВЭЛ зөрүү "
             "дахин нэхэгдэж, ХААЛТ НЭЭГДЭХГҮЙ болно. 0 = маш эрсдэлтэй."},
    {"key": "extra_hour_price", "unit": "mnt", "name": "Шатлалаас хэтэрсэн цаг тутмын үнэ",
     "desc": "Сүүлийн шатлалаас хойш эхэлсэн цаг тутамд нэмэгдэнэ."},
    {"key": "daily_cap", "unit": "mnt", "name": "Хоногийн дээд хязгаар",
     "desc": "Хоног тутмын дүн үүнээс хэтрэхгүй. Хоосон = хязгааргүй."},
]

_GROUP_NAMES = {
    A.EXITRULES_KEY: "Гарах хаалт ба төлбөрийн шийдвэр",
    A.BARRIER_KEY: "Хаалт/уншилтын цагийн цонх",
    A.ENTRYPLATE_KEY: "Орох дугаарын шалгалт",
    A.BLACKLIST_KEY: "Өр ба хар жагсаалт",
    A.AUTOCLOSE_KEY: "Авто цэвэрлэгээ ба өр үүсгэх",
}

# Тоон дүрмийн ЗӨВШӨӨРӨГДӨХ ДЭЭД хязгаар — өмнө нь зөвхөн UI (AutoCloseSection
# BOUNDS) шахдаг байсан тул API-аар шууд бичвэл 10⁹ секундын цонх орж болох байв.
MAX: dict[tuple[str, str], int] = {
    (A.EXITRULES_KEY, "no_session_fee"): 1_000_000,
    (A.EXITRULES_KEY, "min_stay_seconds"): 3600,
    (A.EXITRULES_KEY, "fake_exit_minutes"): 1440,
    (A.EXITRULES_KEY, "reopen_max_hours"): 720,
    (A.BARRIER_KEY, "dedup_seconds"): 300,
    (A.BARRIER_KEY, "entry_burst_seconds"): 60,
    (A.BARRIER_KEY, "reopen_cooldown_sec"): 120,
    (A.ENTRYPLATE_KEY, "hold_seconds"): 30,
    (A.BLACKLIST_KEY, "block_exit_debt_count"): 100,
    (A.BLACKLIST_KEY, "debt_count"): 100,
    (A.BLACKLIST_KEY, "debt_amount"): 100_000_000,
    (A.AUTOCLOSE_KEY, "invalid_plate_hours"): 720,
    (A.AUTOCLOSE_KEY, "awaiting_hours"): 720,
    (A.AUTOCLOSE_KEY, "entry_only_free_hours"): 720,
    (A.AUTOCLOSE_KEY, "stale_hours"): 720,
}
MIN: dict[tuple[str, str], int] = {
    (A.BARRIER_KEY, "dedup_seconds"): 3,
    (A.BARRIER_KEY, "entry_burst_seconds"): 1,
    (A.BARRIER_KEY, "reopen_cooldown_sec"): 1,
    (A.ENTRYPLATE_KEY, "hold_seconds"): 1,
}


def clamp_values(group: str, values: dict) -> dict:
    """PUT-аар ирсэн тоон утгыг [MIN, MAX] мужид шахна; None/"" (=ерөнхий рүү
    буцаах) болон bool/мөрийг хөндөхгүй."""
    out = {}
    for k, v in (values or {}).items():
        default = A.DEFAULTS.get(group, {}).get(k)
        if (v is None or (isinstance(v, str) and not v.strip())
                or isinstance(default, (bool, str, dict)) or default is None):
            out[k] = v
            continue
        try:
            n = int(float(v))
        except (TypeError, ValueError):
            out[k] = v
            continue
        lo = MIN.get((group, k), 0)
        hi = MAX.get((group, k))
        out[k] = max(lo, min(n, hi) if hi is not None else n)
    return out


def group_names() -> dict:
    return dict(_GROUP_NAMES)


def catalog() -> list[dict]:
    """Дүрэм бүрийн тодорхойлолт + зогсоолоор тохируулж болох эсэх."""
    out = []
    for row in CATALOG:
        g, k = row["group"], row["key"]
        out.append({**row,
                    "group_name": _GROUP_NAMES.get(g, g),
                    "default": A._base(g)[k],
                    "min": MIN.get((g, k), 0), "max": MAX.get((g, k)),
                    "per_site": k in A.PER_SITE.get(g, set())})
    return out


def tariff_dict(site) -> dict | None:
    """Зогсоолд холбогдсон тарифын хүн уншихуйц зураг (холбоогүй бол None)."""
    tmpl = site.tariff_template
    if not tmpl:
        return None
    return {"name": tmpl.name,
            "free_minutes": tmpl.free_minutes,
            "grace_minutes": tmpl.grace_minutes,
            "extra_hour_price": float(tmpl.extra_hour_price or 0),
            "daily_cap": float(tmpl.daily_cap) if tmpl.daily_cap is not None else None,
            "tiers": [{"upto_minutes": t.upto_minutes, "price": float(t.price)}
                      for t in sorted(tmpl.tiers, key=lambda t: t.upto_minutes)]}


def _globals(db) -> dict:
    """Бүлэг бүрийн ГЛОБАЛ (зогсоолын давхаргагүй) утгууд."""
    return {g: A.get_rules(db, g) for g in _GROUP_NAMES}


def effective(db, site_id: str | None) -> dict:
    """Бүлэг бүрийн тухайн зогсоолд ҮЙЛЧИЛЖ буй утгууд."""
    return {g: A.get_rules(db, g, site_id) for g in _GROUP_NAMES}


def global_report(db) -> dict:
    """«Ерөнхий» горим — бүх зогсоолын АНХДАГЧ утгууд (зогсоолын давхаргагүй).
    Зөрчлийн шалгалт энд ХИЙГДЭХГҮЙ: тариф/төхөөрөмж зогсоол тус бүрийнх."""
    glob = _globals(db)
    rules = [{**row, "value": glob[row["group"]][row["key"]],
              "global_value": glob[row["group"]][row["key"]], "source": "global"}
             for row in catalog()]
    return {"site_id": None, "site_name": "Ерөнхий — бүх зогсоолын анхдагч",
            "site_flags": {}, "rules": rules, "tariff": None, "conflicts": []}


def site_report(db, site) -> dict:
    """Нэг зогсоолын бүрэн зураг: үйлчилж буй утга, эх сурвалж, зөрчил.

    `source`: "site" = энэ зогсоолд тусгайлан тохируулсан, "global" = системийн
    утга. Зогсоолын БАГАНА (auto_close_hours г.м) нь app_settings-ийг ДАРДАГ тул
    түүнийг ч "site_column" гэж тусад нь тэмдэглэнэ — эс бол админ UI-д нэг утга
    харагдаад код өөр утгаар ажиллана.
    """
    site_id = site.id
    glob, eff = _globals(db), effective(db, site_id)
    overrides = {g: (A.get_site_overrides(db, g).get(site_id) or {}) for g in _GROUP_NAMES}

    rules = []
    for row in catalog():
        g, k = row["group"], row["key"]
        item = {**row, "value": eff[g][k], "global_value": glob[g][k],
                "source": "site" if k in overrides[g] else "global"}
        col = row.get("site_column")
        if col and getattr(site, col, None) is not None:
            item.update(value=getattr(site, col), source="site_column",
                        site_column_note=f"«Зогсоол» табын засах цонхны утга "
                                         f"({getattr(site, col)}) app_settings-ийг ДАРНА")
        rules.append(item)

    tariff = tariff_dict(site)

    return {"site_id": site_id, "site_name": site.name,
            "site_flags": {f["key"]: getattr(site, f["key"], None) for f in SITE_COLUMN_RULES},
            "rules": rules, "tariff": tariff,
            "conflicts": check_conflicts(db, site, eff, tariff)}


# ── ЗӨРЧЛИЙН ШАЛГАЛТ ───────────────────────────────────────────────────────
# «Төлбөрөө төлсөн атлаа хаалт нээгдэхгүй» гэсэн гомдол бүрийн ард тохиргооны
# ХОСЛОЛ байдаг. Тэдгээрийг нэрлээд, аль тохиргоог өөрчлөхийг шууд хэлнэ.
def check_conflicts(db, site, eff: dict | None = None, tariff: dict | None = None) -> list[dict]:
    from ..models import Device
    eff = eff if eff is not None else effective(db, site.id)
    # Тарифыг дуудагч өгөөгүй бол ӨӨРӨӨ уншина — эс бол grace_minutes-тэй
    # холбоотой ХАМГИЙН чухал зөрчлүүд чимээгүй алгасагдана.
    tariff = tariff if tariff is not None else tariff_dict(site)
    ex, bar = eff[A.EXITRULES_KEY], eff[A.BARRIER_KEY]
    bl, ac = eff[A.BLACKLIST_KEY], eff[A.AUTOCLOSE_KEY]
    ep = eff[A.ENTRYPLATE_KEY]
    out: list[dict] = []

    def add(level, title, detail, fix):
        out.append({"level": level, "title": title, "detail": detail, "fix": fix})

    # 1. Тарифгүй зогсоол — БҮХ машин үнэгүй гарна (чимээгүй орлогын алдагдал)
    if not site.tariff_template and not site.no_charge:
        add("high", "Тариф тохируулаагүй",
            "Тарифын загвар холбогдоогүй тул төлбөр тооцох дүрэм алга — бүх машин "
            "0₮-өөр гарна («Тариф тохируулаагүй» гэсэн шалтгаантай).",
            "«Зогсоол» табын засах цонхноос тарифын загвар холбоно уу.")

    # 2. grace = 0 — төлсөн машин ГАРААД АМЖИХГҮЙ
    if tariff and tariff["grace_minutes"] == 0:
        add("high", "Төлсний дараа гарах хугацаа 0 минут",
            "Төлбөр төлмөгц эцсийн хугацаа нь ТЭР АГШИНД дуусна. Гарах камерт "
            "уншигдах хүртэл хэдхэн секунд өнгөрөхөд зөрүү дахин нэхэгдэж, "
            "ХААЛТ НЭЭГДЭХГҮЙ.",
            "Тарифын загвар дээр «Төлбөрийн дараах үнэгүй гарах хугацаа»-г "
            "хамгийн багадаа 5-15 минут болгоно уу.")
    elif tariff and 0 < tariff["grace_minutes"] < 3:
        add("warn", "Төлсний дараа гарах хугацаа маш богино",
            f"{tariff['grace_minutes']} минут — хаалт удаан нээгдвэл эсвэл машин "
            "эгнээнд хүлээвэл төлбөр дахин нэхэгдэж болно.",
            "5-15 минут болгохыг зөвлөнө.")

    # 3. Хуурамч өр гарахыг хориод байх хослол
    if bl["block_exit_debt_count"] and ac["create_debt"]:
        add("high", "Хуурамч өр гарах хаалтыг хаана",
            "«Гарах уншилтгүй машинд өр үүсгэх» асаалттай байхад «өртэй машиныг "
            "гарцад саатуулах» ч асаалттай. Гарах уншилт алдагдсанаас үүссэн "
            "ХУУРАМЧ өр машиныг гарахад нь хорино — жолооч төлөх зүйлгүй атлаа "
            "хаалт нээгдэхгүй.",
            "«ӨР — авто хаалт, гарах уншилтгүй машинд»-ыг унтраана уу "
            "(2026-08-12-ны аудитын зөвлөмж).")
    if bl["auto_enabled"] and ac["create_debt"]:
        add("warn", "Хуурамч өрөөс автомат хар жагсаалт",
            "Гарах уншилтгүй машинд өр үүсгэх нь асаалттай байхад автомат хар "
            "жагсаалт ч асаалттай — уншилт алдагдсан машин хар жагсаалтад орж "
            "болно.",
            "Аль нэгийг нь унтраана уу.")

    # 4. Давхар уншилтын цонх ба гарах хаалтын дахин нээлт
    if not bar["exit_dedup_reopen"]:
        add("high", "Давхар уншилт дээр гарах хаалт дахин нээгдэхгүй",
            f"Эхний «нээ» команд амжилтгүй болвол машин {bar['dedup_seconds']} "
            "секунд хүлээнэ — жолооч ухраад дахин ойртсон ч хаалт хөдлөхгүй "
            "(2026-08 хүртэлх гол гомдол).",
            "Энэ дүрмийг асаана уу.")
    elif bar["dedup_seconds"] >= 60:
        add("warn", "Давхар уншилтын цонх хэт урт",
            f"{bar['dedup_seconds']}с — нэг эгнээгээр дараалан орсон хоёр ӨӨР "
            "машин нэг бүртгэл болж нийлэх эрсдэлтэй.",
            "20-40 секундын хооронд байлгахыг зөвлөнө.")
    if bar["reopen_cooldown_sec"] >= 15:
        add("warn", "Хаалт дахин нээх завсар урт",
            f"{bar['reopen_cooldown_sec']}с — араас нь дагаж ирсэн машинд "
            "хаалт нээгдэхгүй байж болно.",
            "5-10 секунд хангалттай.")
    if ep["hold_seconds"] >= bar["entry_burst_seconds"]:
        add("warn", "Дахин уншилт хүлээх хугацаа цуврал цонхноос урт",
            f"hold={ep['hold_seconds']}с ≥ burst={bar['entry_burst_seconds']}с — "
            "залруулга ирэхээс өмнө шийдвэр гарч, буруу дугаартай бүртгэл үлдэнэ.",
            "hold-ыг burst-аас БОГИНО болгоно уу.")

    # 5. Хатуу орох хаалт — эгнээ гацах эрсдэл
    if ep["policy"] == "strict":
        add("warn", "Орох хаалт «нээхгүй» горимд",
            "Гадаад/түр/бохир дугаартай машин орох хаалтад гацаж, ард нь эгнээ "
            "үүснэ. Оператор 24/7 байхгүй бол эрсдэлтэй.",
            "«Түр барина, дараа нь нээнэ» (hold) горимыг зөвлөнө.")

    # 6. Тохиргоо энэ зогсоолд ХЭРЭГЖИХГҮЙ хослолууд
    if ex["no_session_fee"] and (site.registered_only or site.no_charge):
        add("info", "Суурь хураамж энэ зогсоолд үйлчлэхгүй",
            f"{ex['no_session_fee']}₮ тохируулсан ч зогсоол "
            f"{'«зөвхөн гэрээт»' if site.registered_only else '«төлбөр авахгүй»'} "
            "тул код түүнийг алгасана.",
            "Тохиргоо нь эндүүрэл төрүүлж байвал 0 болгоно уу.")
    if site.no_charge and (tariff or ex["min_stay_seconds"]):
        add("info", "Төлбөргүй зогсоолд төлбөрийн дүрэм үйлчлэхгүй",
            "«Төлбөр АВАХГҮЙ зогсоол» тэмдэглэгээ бусад бүх төлбөрийн дүрмийг дардаг.",
            "Төлбөр авах бол «Зогсоол» табаас тэмдэглэгээг авна уу.")

    # 7. min_stay — үнэгүй хугацаатай зөрчилдөх эсэх
    if ex["min_stay_seconds"] and tariff and tariff["free_minutes"]:
        add("info", "Эрт гарахад хаалт нээхгүй дүрэм асаалттай",
            f"Машин {ex['min_stay_seconds']}с дотор буцаж гарвал хаалт нээгдэхгүй "
            f"(тарифын үнэгүй хугацаа {tariff['free_minutes']} мин байсан ч). "
            "Буруу орсон/эргэж гарах жолоочийг оператор гараар гаргана.",
            "Хэт өндөр утга (>120с) тавихаас болгоомжил.")

    # 8. Төхөөрөмжийн талын шалтгаанууд — тохиргоо зөв ч хаалт нээгдэхгүй
    devs = db.query(Device).filter(Device.site_id == site.id,
                                   Device.status == "active").all()
    cams = [d for d in devs if d.device_type == "camera"]
    bars = [d for d in devs if d.device_type == "barrier"]
    if not cams:
        # Камер огт бүртгэгдээгүй — зориудын (QR-only, түр) байж болно; хаалт
        # камерын ард АВТОМАТААР үүсдэг тул энд «хаалт алга» гэж хэлэх нь буруу.
        add("info", "Камер бүртгэгдээгүй зогсоол",
            "Энэ зогсоолд идэвхтэй LPR камер алга — уншилт ирэхгүй тул хаалтны "
            "дүрмүүд ажиллах зүйлгүй. Камер бүртгэмэгц хаалт нь автоматаар үүснэ.",
            "Төхөөрөмж хэрэглэдэг зогсоол бол «Төхөөрөмж» табаас камер нэмнэ үү.")
    else:
        from .device_auto import barrier_matches_camera
        unpaired = [c for c in cams if not any(barrier_matches_camera(c, b) for b in bars)]
        if unpaired:
            add("high", "Хаалтгүй камер байна",
                "Дараах камерын эгнээнд идэвхтэй хаалт алга: "
                + ", ".join(f"{c.name or c.ip_address} (эгнээ {c.lane_no}/{c.lane_dir})"
                            for c in unpaired)
                + " — дугаар уншсан ч нээх зүйл байхгүй.",
                "«Төхөөрөмж» таб → «Хаалтыг баталгаажуулах» — дутуу хаалтыг "
                "давхардалгүй нөхөж үүсгэнэ (устгасныг сэргээнэ).")
        # Реле олдохгүй хаалт — команд үүссэн ч хөдлөхгүй
        from .barrier import relay_note
        dead = [b for b in bars if relay_note(db, b)]
        if dead:
            add("high", "Релегүй хаалт",
                "Дараах хаалт ижил эгнээнд камергүй тул реле олдохгүй: "
                + ", ".join(f"{b.name} (эгнээ {b.lane_no}/{b.lane_dir})" for b in dead)
                + " — машин ирэхэд команд үүссэн ч хөдлөхгүй.",
                "Тэр эгнээнд камер бүртгэх эсвэл хаалтын эгнээг камерынхтай тааруулна уу.")
    no_auto = [d.name or d.ip_address for d in devs
               if d.device_type == "camera" and d.lane_dir in ("entry", "both")
               and not d.auto_open]
    if no_auto:
        add("warn", "Орох камер «автомат нээхгүй» тохиргоотой",
            "Дараах камерт auto_open унтраалттай: " + ", ".join(no_auto)
            + " — дугаар уншсан ч хаалт нээгдэхгүй.",
            "«Төхөөрөмж» табаас автомат нээлтийг асаана уу.")

    # 9. Nested зогсоолын тоолуур мөнхөд зогсох эрсдэл
    if site.parent_site_id is not None and site.transit_max_hours == 0:
        add("warn", "Доторх зогсоолын тоолуур хязгааргүй зогсоно",
            "transit_max_hours=0 — доторх гарах уншилт алдагдвал гадна талын "
            "төлбөрийн тоолуур мөнхөд зогсож машин 0₮-өөр гарна.",
            "4-8 цагийн хязгаар тавина уу.")
    return out
