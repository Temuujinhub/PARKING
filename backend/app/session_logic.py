"""Орох/гарах урсгалын гол логик — LPR event-ээс session үүсгэх, хаах, barrier нээх."""
import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .billing import calculate_fee
from .config import settings
from .services.device_auth import camera_credentials
from .models import (
    AuditLog, BarrierCommand, BlacklistEntry, Device, LprEvent, ParkingSession,
    ParkingSite, Payment, RegisteredDriver,
)
from .services.barrier import format_duration, open_barrier, render_screen_text, schedule_display
from .services.snapshot import schedule_capture
from .ws import manager, notify


import re

log = logging.getLogger("parking.session_logic")

# Монгол улсын дугаарын формат (docs/дугаарын стандарт-ын зургууд, MNS 4410:2002):
#   • энгийн/эко/тээвэр/технологи: 4 цифр + 3 кирилл үсэг (1234УБА, Ө/Ү орно)
#   • дипломат хуучин: 2 үсэг ЭХЭНДЭЭ + 4 цифр (ДК0188 — Nissan Patrol-ын зураг)
#   • дипломат шинэ: 4 цифр + ДК/АК АРДАА (1302ДК, 9914АК — улаан дэвсгэртэй) —
#     зөвхөн ЭДГЭЭР хос үсгийг зөвшөөрнө!
# АНХААР: 4 цифр + ДУРЫН 2 үсэг (1234УБ) хэлбэрийг ЗОРИУД оруулаагүй — энэ нь
# энгийн дугаарын ТАЙРАГДСАН уншилт бөгөөд plates_ocr_similar-ийн тайралт-тохирол
# «богино нь буруу форматтай» гэдэгт тулгуурладаг. ДК/АК-г нэмснээр «1234ДКХ →
# 1234ДК» тайралт нийлэхээ болих жижиг эрсдэл бий — дипломат сери ховор тул
# хог уншилтаар хаалт гацахаас (Хангарьд, 2026-08) хамаагүй бага хохиролтой.
PLATE_RE = re.compile(r"^(?:\d{4}[А-ЯЁӨҮ]{3}|[А-ЯЁӨҮ]{2}\d{4}|\d{4}(?:ДК|АК))$")


def normalize_plate(plate: str) -> str:
    return (plate or "").upper().replace(" ", "").replace("-", "").strip()


def strip_images(raw):
    """LprEvent.raw-д хадгалахын өмнө том base64 зургийг хасна — push-аар ирсэн зураг
    (~1MB) event бүрд DB-д хуримтлагдвал сан хэт томордог. Capture нь ТУСДАА бүрэн
    raw-г ашигладаг тул энэ нь зөвхөн ЛОГД хадгалах хувилбар."""
    import copy
    if not isinstance(raw, (dict, list)):
        return raw
    out = copy.deepcopy(raw)

    def scrub(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(v, str) and len(v) > 2000:
                    node[k] = f"<{len(v)}b image stripped>"
                else:
                    scrub(v)
        elif isinstance(node, list):
            for v in node:
                scrub(v)
    scrub(out)
    return out


def is_valid_plate(plate: str) -> bool:
    return bool(PLATE_RE.match(normalize_plate(plate)))


# Dahua-ийн өнгө/төрлийг монголоор (оператор танихад ойлгомжтой)
_COLOR_MN = {"white": "цагаан", "black": "хар", "gray": "саарал", "grey": "саарал",
             "silver": "мөнгөлөг", "red": "улаан", "blue": "цэнхэр", "green": "ногоон",
             "yellow": "шар", "brown": "бор", "gold": "алтлаг", "orange": "улбар"}
_TYPE_MN = {"sedan": "суудлын", "suv": "жийп", "bus": "автобус", "truck": "ачааны",
            "van": "фургон", "minivan": "микро", "motorcycle": "мотоцикл",
            "car": "суудлын", "saloon": "суудлын", "pickup": "пикап"}


def extract_vehicle_info(raw: dict) -> tuple[str | None, str | None]:
    """Камерын event-ээс машины ӨНГӨ ба ТӨРЛИЙГ гаргана (Dahua-ийн олон түлхүүр).
    Дугаар буруу уншигдсан үед машиныг таних нэмэлт шинж — оператор snapshot-той
    тулгах, систем тохирлыг батлахад ашиглана."""
    if not isinstance(raw, dict):
        return None, None
    color = _type = None
    # Боломжит байрлалууд: дээд түвшин, Vehicle.*, Object.*, TrafficCar.*, Plate.*
    for src in (raw, raw.get("Vehicle") or {}, raw.get("Object") or {},
                raw.get("TrafficCar") or {}, raw.get("Plate") or {}):
        if not isinstance(src, dict):
            continue
        color = color or (src.get("VehicleColor") or src.get("Color")
                          or src.get("PlateColor"))
        _type = _type or (src.get("VehicleType") or src.get("Category")
                          or src.get("CarType") or src.get("ObjectType"))
    cn = _COLOR_MN.get(str(color).strip().lower(), str(color).strip()) if color else None
    tn = _TYPE_MN.get(str(_type).strip().lower(), str(_type).strip()) if _type else None
    return (cn or None), (tn or None)


def find_registered(db: Session, plate: str, site_id: str) -> RegisteredDriver | None:
    """Гэрээт машин мөн эсэх. site_id NULL («бүх зогсоол») бүртгэл нь зөвхөн
    ӨӨРИЙН ТҮРЭЭСЛЭГЧИЙН зогсоолуудад үйлчилнэ — түрээслэгч ДАМНАН үнэгүй
    нэвтрэхийг хориглоно (NULL/NULL тохирол нь tenant-гүй хуучин суулгацад
    хуучин зан төлөвөө хадгална)."""
    now = datetime.utcnow()
    q = (
        db.query(RegisteredDriver)
        .filter(
            RegisteredDriver.plate_number == plate,
            RegisteredDriver.is_active.is_(True),
            RegisteredDriver.valid_from <= now,
            RegisteredDriver.valid_to >= now,
        )
    )
    site_tenant = (db.query(ParkingSite.tenant_id)
                   .filter(ParkingSite.id == site_id).scalar()) if site_id else None
    all_sites_cond = (RegisteredDriver.site_id.is_(None)) & (
        (RegisteredDriver.tenant_id == site_tenant) if site_tenant
        else RegisteredDriver.tenant_id.is_(None))
    # Тусгай хэрэгцээт (ХБИ г.м) жагсаалт ч түрээслэгчийн хил ДОТРОО л үйлчилнэ:
    # site_id NULL + тухайн түрээслэгчийн tenant_id = түрээслэгчийн бүх зогсоол.
    # Түрээслэгч дамнасан систем-даяарх whitelist байхгүй (2026-08-09 шийдвэр).
    return q.filter((RegisteredDriver.site_id == site_id) | all_sites_cond).first()


def is_blacklisted(db: Session, plate: str) -> BlacklistEntry | None:
    return (
        db.query(BlacklistEntry)
        .filter(BlacklistEntry.plate_number == plate, BlacklistEntry.is_active.is_(True))
        .first()
    )


def get_open_session(db: Session, plate: str, site_id: str) -> ParkingSession | None:
    return (
        db.query(ParkingSession)
        .filter(
            ParkingSession.plate_number == plate,
            ParkingSession.site_id == site_id,
            ParkingSession.status.in_(["OPEN", "AWAITING_PAYMENT", "PAID"]),
        )
        .order_by(ParkingSession.entry_time.desc())
        .first()
    )


# OCR-т амархан андуурагддаг Кирилл/цифр хосууд — жигдэлмэгц ижил болгож харьцуулна
_OCR_CANON = str.maketrans({
    "О": "0", "O": "0", "В": "Б", "Ь": "Б", "Ё": "Е", "Э": "З",
    "Ү": "У", "Ұ": "У", "Й": "И", "П": "Н", "Ц": "Ч", "І": "1", "l": "1",
})


def _ocr_canon(p: str) -> str:
    return (p or "").translate(_OCR_CANON)


def plates_ocr_similar(a: str, b: str) -> bool:
    """Хоёр дугаар OCR-ийн зөрүүтэй ижил машинйх байж болох эсэх:
    - яг ижил
    - ижил урттай бөгөөд НЭГ л байрлалд зөрүүтэй (substitution)
    - андуурагддаг тэмдэгтүүдийг (О/0, Б/В г.м.) жигдэлмэгц ижил
    - нэг нь нөгөөгийнхөө ТАЙРАГДСАН уншилт: камер эхний/сүүлийн цифрийг
      алгасч уншдаг (ж: 7524УБТ → 524УБТ) — богино нь стандарт формат биш
      бол л (жинхэнэ өөр машин байх боломжгүй) ижил гэж үзнэ."""
    if a == b:
        return True
    if len(a) != len(b):
        long_p, short_p = (a, b) if len(a) > len(b) else (b, a)
        return (len(long_p) - len(short_p) <= 2 and len(short_p) >= 5
                and not is_valid_plate(short_p)
                and (long_p.endswith(short_p) or long_p.startswith(short_p)))
    if _ocr_canon(a) == _ocr_canon(b):
        return True
    return sum(1 for x, y in zip(a, b) if x != y) == 1


def is_duplicate_read(plate: str, recent: str) -> bool:
    """Нэг машиныг ХОЁР ДАХЬ УДАА уншсан эсэх (зөвхөн dedup-д, session тохооход БИШ).

    ЯАГААД ТУСДАА ДҮРЭМ: `plates_ocr_similar` нь session тохооход ч ашиглагддаг
    тул зориуд ХАТУУ — сулруулбал буруу машинд төлбөр тохоно. Гэвч давхар
    уншилтыг таних нь өөр асуудал: тухайн ХАЖУУГААР нь (20 секундын дотор, ижид
    зурвас дээр) зөв уншилт аль хэдийн бүртгэгдсэн байхад дараагийн эвдэрсэн
    уншилт нь бараг үргэлж ТЭР МАШИН байна.

    Хэмжилт (2026-08-14, 2 хоног, 5,357 гарах уншилт): «хог уншилт» 135 удаа
    гарсны ихэнх нь яг тэр секундэд бүртгэгдсэн зөв уншилтын хажууд байв:
        4627УКА 13:37  ✓ гарсан
        4627КД  13:37  ✗ «бүртгэлгүй» → LED дээрх төлбөрийн текстийг дарна
    Одоогийн дүрэм зөвхөн эхний/сүүлийн тэмдэгт тасарсныг барьдаг тул
    `4627УКА`.startswith(`4627КД`) худал болж давхар уншилт мэдрэгдээгүй.

    ШИНЭ ШАЛГУУР: уншсан дугаар ФОРМАТ БУРУУ бөгөөд цифрийн хэсэг нь саяхны
    зөв уншилтын цифрүүдэд багтаж байвал давхар уншилт гэж үзнэ. Цифр шалгах нь
    санамсаргүй тохиолдлыг хаана — ард нь дараалсан өөр машины дугаар цифрээрээ
    давхцах магадлал бага."""
    if plates_ocr_similar(plate, recent):
        return True
    if is_valid_plate(plate):
        return False        # зөв форматтай дугаар — жинхэнэ өөр машин байж болно
    d_new = "".join(c for c in plate if c.isdigit())
    d_old = "".join(c for c in recent if c.isdigit())
    return len(d_new) >= 3 and (d_new in d_old or d_old in d_new)


def match_open_session(db: Session, plate: str, site_id: str) -> tuple[ParkingSession | None, bool]:
    """Гарах талд session хайх: эхлээд ЯГ таарах, олдохгүй бол OCR-ийн зөрүүтэй
    (үсэг андуурч уншсан) session-ийг олно. Буцаах: (session|None, fuzzy_эсэх).

    Аюулгүй байдал: OCR-ойролцоо нэр дэвшигч ЯГ НЭГ байвал л зөвшөөрнө — 2+ бол
    сэжигтэй тул None буцааж, оператор гараар шийднэ (буруу машинд төлбөр тохохгүй)."""
    exact = get_open_session(db, plate, site_id)
    if exact:
        return exact, False
    opens = (db.query(ParkingSession)
             .filter(ParkingSession.site_id == site_id,
                     ParkingSession.status.in_(["OPEN", "AWAITING_PAYMENT", "PAID"]))
             .all())
    close = [s for s in opens if plates_ocr_similar(plate, s.plate_number)]
    if len(close) == 1:
        return close[0], True
    # 3-Р ШАТ — ОРОХ ДУТУУ УНШИГДСАН, ГАРАХ ЗӨВ: гарах камер бүтэн зөв дугаар
    # уншсан ч орох камер дутуу (үсэггүй «4132», нэг цифр дутуу «132УБИ» г.м.)
    # уншсанаас 1-2-р шат тохироогүй. Бодит машиныг алдахгүй, phantom-ыг цэвэрлэж,
    # ОРОХ ЦАГААР нь төлбөр авахын тулд: гарах дугаар ЗӨВ форматтай үед орох
    # ФОРМАТ БУРУУ session-ий дугаар нь гарахынхаа дэд мөр (эхлэл/төгсгөл/дотор)
    # байвал тохоно. Аюулгүй байдал: нэр дэвшигч ЯГ НЭГ байх ёстой (эс бол алгасна).
    if is_valid_plate(plate):
        partial = [s for s in opens
                   if not is_valid_plate(s.plate_number) and len(s.plate_number) >= 3
                   and (plate.startswith(s.plate_number)
                        or plate.endswith(s.plate_number)
                        or s.plate_number in plate)]
        if len(partial) == 1:
            return partial[0], True
    return None, False


# Гарах уншилтад бүртгэл олдоогүй үед хэр хуучин хаалтыг сэргээхийг зөвшөөрөх
REOPEN_MAX_HOURS = 48


def auto_reopen_for_exit(db: Session, plate: str, site_id: str) -> ParkingSession | None:
    """Гарах камерт уншигдсан ч ИДЭВХТЭЙ бүртгэл алга — САЯХАН АЛБАДАН хаагдсаныг
    сэргээнэ («Бүртгэлгүй гарах оролдлого»-ын 23%-ийн шалтгаан).

    Юу болдог вэ: машин орж ирээд гарах уншилт алдагдсанаас session нь OPEN
    хэвээр үлдэнэ → 12 цаг болоод авто хаалт хаана → машин үнэндээ ДОТОР
    БАЙСААР байгаа → гарахад нь «бүртгэлгүй» болж хаалт нээгдэхгүй, оператор
    гараар шийддэг. Гараар «Сэргээх» товч байсан ч камерын урсгалд ажилладаггүй
    байв — үүнийг автоматжуулна.

    ХАМГААЛАЛТ (гараар сэргээхтэй ижил дүрэм + нэмэлт):
      • ТӨЛБӨР ТӨЛӨГДСӨН бүртгэлд ХҮРЭХГҮЙ (давхар нэхэхгүй)
      • exit_confirmed=True (жинхэнэ гарах уншилттай) бол ХҮРЭХГҮЙ — тэр машин
        үнэхээр гарсан, одоогийнх нь ШИНЭ зогсолт
      • зөвхөн REOPEN_MAX_HOURS дотор хаагдсан
      • нэр дэвшигч ЯГ НЭГ байх ёстой (эс бол буруу машинд төлбөр тохоно)
      • тухайн дугаараар өөр идэвхтэй бүртгэл байвал хүрэхгүй (uq_active_session)
      • хаалтаас үүссэн PENDING өрийг цуцална — сэргээсэн бүртгэл дээр төлбөр
        дахин бодогдох тул үлдээвэл ДАВХАР нэхэгдэнэ
    """
    if not is_valid_plate(plate):
        return None                      # junk уншилт — сэргээх үндэслэлгүй
    if get_open_session(db, plate, site_id):
        return None                      # идэвхтэй бүртгэл бий — энэ функц хэрэггүй
    since = datetime.utcnow() - timedelta(hours=REOPEN_MAX_HOURS)
    closed = (db.query(ParkingSession)
              .filter(ParkingSession.site_id == site_id,
                      ParkingSession.status.in_(["MANUAL_CLOSED", "CLOSED", "FREE"]),
                      ParkingSession.paid_at.is_(None),
                      ParkingSession.exit_time >= since)
              .all())

    def _short_fake_exit(s: ParkingSession) -> bool:
        """Гарах уншилт БАЙСАН ч зогсолт хэт богино — «хуурамч гарц» уу.

        KH зогсоолын бичлэгээр батлагдсан арга (2026-08-14): жолооч орох
        камерт уншуулаад ухраад гарах камерт уншуулна → session үнэгүй
        хаагдана → өдөржин зогсоод оройдоо «бүртгэлгүй» болж гарна.

        Хуурамч гарцыг тэр агшинд нь ялгах аргагүй (орж ирээд эргэж гарах нь
        бодитой). ГЭХДЭЭ машин ОРОЙ гарах камерт ДАХИН уншигдсан нь өөрөө
        нотолгоо: үнэхээр гарсан бол дахин ирэхгүй байсан."""
        if not (s.entry_time and s.exit_time):
            return False
        mins = (s.exit_time - s.entry_time).total_seconds() / 60
        return 0 <= mins <= settings.suspicious_exit_minutes

    # exit_confirmed=True (жинхэнэ гарах уншилттай) бол ердийн үед ХҮРЭХГҮЙ —
    # тэр машин үнэхээр гарсан, одоогийнх нь ШИНЭ зогсолт. Цорын ганц үл
    # хамаарах нь дээрх «хуурамч гарц».
    closed = [s for s in closed if not s.exit_confirmed or _short_fake_exit(s)]
    cands = [s for s in closed if s.plate_number == plate]
    if not cands:   # OCR зөрүүтэй уншсан байж болно — ЯГ НЭГ таарвал зөвшөөрнө
        cands = [s for s in closed if plates_ocr_similar(plate, s.plate_number)]
    if len(cands) != 1:
        return None
    s = cands[0]
    if paid_total(db, s) > 0:
        return None
    fake = bool(s.exit_confirmed) and _short_fake_exit(s)
    fake_min = ((s.exit_time - s.entry_time).total_seconds() / 60
                if fake and s.entry_time and s.exit_time else 0.0)
    from .models import AuditLog, Compensation
    canceled = (db.query(Compensation)
                .filter(Compensation.session_id == s.id, Compensation.status == "PENDING")
                .update({"status": "CANCELLED"}, synchronize_session=False))
    s.status = "OPEN"
    s.exit_time = None
    s.exit_device_id = None
    s.duration_minutes = None
    s.total_fee = s.base_fee = s.vat_amount = None
    s.exit_deadline = None
    # Тэмдэглэл нь АЛЬ тохиолдол болохыг ялгана — оператор маргаан гарвал
    # юунд үндэслэн төлбөр нэмэгдсэнийг тайлбарлах ёстой
    if fake:
        _why = (f"Гарах уншилтаар АВТО сэргээв: {fake_min:.0f} минутын дараа "
                f"«гарсан» гэж бүртгэгдсэн ч машин ДОТРОО үлдсэн байна "
                f"(төлбөр орсон цагаас тооцогдоно)")
    else:
        _why = "Гарах уншилтаар АВТО сэргээв (албадан хаалт эрт байсан)"
    s.note = f"{s.note + ' | ' if s.note else ''}{_why}"[:1000]
    db.add(AuditLog(username="system", action="AUTO_REOPEN", entity="session",
                    entity_id=s.id,
                    detail={"plate": s.plate_number, "read_plate": plate,
                            "canceled_debt": canceled,
                            "short_fake_exit": fake,
                            "prev_minutes": round(fake_min, 1) if fake else None}))
    db.flush()
    log.info("[exit] АВТО сэргээв: %s (session %s, цуцалсан өр %d)%s",
             s.plate_number, s.id, canceled,
             f" — ХУУРАМЧ ГАРЦ {fake_min:.0f}м" if fake else "")
    return s


def session_fee_info(db: Session, s: ParkingSession, at: datetime | None = None) -> dict:
    site: ParkingSite = s.site
    template = site.tariff_template if site else None
    if at is None:
        # AWAITING_PAYMENT машин зогсоолд байгаа хэвээр (гарч чадаагүй) тул төлбөр
        # exit_time дээр царцахгүй — одоог хүртэл үргэлжлэн бодогдоно.
        if s.status in ("OPEN", "AWAITING_PAYMENT"):
            at = datetime.utcnow()
        else:
            at = s.exit_time or datetime.utcnow()
    # Гэрээт эсэхийг ОРОХ үед л тогтоож session дээр хөлдөөдөг байсан тул, машин
    # орсны ДАРАА жагсаалтад нэмэгдвэл (ж: Excel импорт, гараар бүртгэх) төлбөртэй
    # хэвээр үлдэж, оператор гараар чөлөөлөх шаардлагатай болдог байв. Зогсоолд
    # байгаа машины төлбөрийг тооцох бүрд ДАХИН шалгаснаар шинээр бүртгэсэн машин
    # ямар ч гар ажиллагаагүйгээр шууд гарна (fee.is_free → хаалт авто нээгдэнэ).
    registered = s.is_registered
    drv = find_registered(db, s.plate_number, s.site_id) if db is not None else None
    if not registered and drv is not None and s.status in ("OPEN", "AWAITING_PAYMENT"):
        registered = True
        # Session дээр нь тэмдэглэнэ — жагсаалтад "Гэрээт" гэж зөв харагдана
        # (дараагийн commit-той хамт хадгалагдана; read-only хүсэлтэд хадгалагдахгүй
        # ч тооцоолол зөв хэвээр).
        s.is_registered = True

    # Доторх (nested) зогсоолд өнгөрүүлсэн хугацааг хасна. Session-ийг өөрчлөхгүй
    # уншина — энэ функц жагсаалт/урьдчилсан тооцоонд ч дуудагддаг.
    from .services.nested import effective_paused_minutes
    paused = (effective_paused_minutes(db, s, at) if db is not None
              else int(getattr(s, "paused_minutes", 0) or 0))

    # Үнэгүй ЦАГИЙН ЦОНХТОЙ гэрээт (ж: сургуулийн машин 08:00-18:00): бүрэн
    # үнэгүй биш — цонхтой давхцсан минут тоолуураас хасагдаж, гаднах хугацаа
    # энгийнээр бодогдоно. Цонхгүй гэрээт хуучин шигээ бүх цагт үнэгүй.
    registered_free = registered
    if drv is not None and drv.free_from and drv.free_until:
        from .billing import free_window_minutes
        registered_free = False
        paused += free_window_minutes(s.entry_time, at, drv.free_from, drv.free_until)

    return calculate_fee(
        template, s.entry_time, at,
        discount=s.discount, is_registered=registered_free,
        paused_minutes=paused, no_charge=bool(site and getattr(site, "no_charge", False)),
    )


def paid_total(db: Session, s: ParkingSession) -> float:
    """Session-д аль хэдийн төлөгдсөн нийт дүн (PAID төлбөрүүдийн нийлбэр)."""
    rows = db.query(Payment.amount).filter(Payment.session_id == s.id,
                                           Payment.status == "PAID").all()
    return float(sum(float(r[0]) for r in rows))


def amount_due(db: Session, s: ParkingSession, fee: dict) -> float:
    """Одоо төлөх ёстой үлдэгдэл: нийт тооцоолсон дүнгээс төлснийг хассан.
    Grace хугацаа хэтэрч дахин тооцоход өмнөх төлбөрийг ДАВХАРДУУЛЖ нэхэхгүй."""
    return max(0.0, round(fee["total_fee"] - paid_total(db, s), 2))


def close_session_forced(db: Session, s: ParkingSession, reason: str, username: str,
                         create_comp: bool = True) -> float:
    """Админ/авто цэвэрлэгээ: гацсан session-ийг хааж, төлөгдөөгүй дүнгээр өр үүсгэнэ.

    Өрийн дүнгийн дүрэм: гарах оролдлоготой (AWAITING_PAYMENT + exit_time) машин
    тэр үедээ л төлөлгүй явсан гэж үзэж ТЭР ҮЕИЙН дүнгээр (шударга дүн, exit_time хэвээр);
    гарах оролдлогогүй (OPEN) бол одоог хүртэлх дүнгээр (daily_cap хамгаална).
    Буцаах: үүсгэсэн өрийн дүн (0 бол өр үүсээгүй). commit хийхгүй — caller хийнэ."""
    now = datetime.utcnow()
    # Гарах цаг нь БАРИМТТАЙ юу (гарах камерт уншигдсан) эсвэл ТААМАГ уу.
    # Зөвхөн AWAITING_PAYMENT нь гарах уншилттай — бусад нь «одоо» гэсэн таамаг.
    confirmed = s.status == "AWAITING_PAYMENT" and bool(s.exit_time)
    if s.status == "PAID" and s.paid_at:
        # Төлчихсөн машин — grace дотор гарсан гэж үзэж төлбөрийг ТӨЛСӨН/deadline
        # үедээ царцаана. Эс бол одоог хүртэлх хугацаагаар хэт нэхэж, худал өр үүснэ.
        at = s.exit_deadline or s.paid_at
    elif s.status == "AWAITING_PAYMENT":
        # Гарах оролдлоготой машин: exit_time (байвал), үгүй бол сүүлд гарах хаалтанд
        # харагдсан үе (updated_at) дээр төлбөрийг царцаана — дагаж гарсан машинд
        # алга болсноос хойшхи цагийг нэхэхгүй (шударга дүн).
        at = s.exit_time or s.updated_at or now
    else:
        at = now
    # ӨР ҮҮСГЭХГҮЙ + ГАРАХ УНШИЛТ ОГТ БАЙХГҮЙ бол ХУУРАМЧ ДҮН ч бичихгүй.
    # Ийм машин ҮНЭНДЭЭ хэдийнэ гарсан ч гарцын камер уншаагүй; «одоо − орсон»
    # гэж бодвол 12 цагийн төлбөр бичигдэж, тайлангийн «Үүссэн» багана хуурамчаар
    # хөөрөгдөж «Цуглуулалт %»-ийг доогуур харуулдаг (Соёлын төв 22%, Номадс 23%).
    # Формат буруу phantom-д хэдийнэ хэрэглэдэг зарчмыг (auto_close.py) энд ч мөрдөнө.
    if not create_comp and s.status == "OPEN" and not s.exit_time and not s.paid_at:
        from .services.nested import close_open_pause
        close_open_pause(db, s, now)
        s.exit_time = now
        s.duration_minutes = None          # хэзээ гарсныг МЭДЭХГҮЙ — таамаглахгүй
        s.base_fee, s.vat_amount, s.total_fee = 0, 0, 0
        s.status = "MANUAL_CLOSED"
        s.note = f"{s.note + ' | ' if s.note else ''}{reason}: гарах уншилтгүй — өргүй, дүнгүй хаав"[:1000]
        return 0.0

    fee = session_fee_info(db, s, at=at)
    due = amount_due(db, s, fee)
    # Доторх (nested) зогсоолд байхад нь хаагдаж байгаа бол явж буй зогсолтыг
    # ЭНД барагдуулна. Төлбөр нь `fee` дотор аль хэдийн зөв хасагдсан (ижил
    # хязгаараар) — гэхдээ барагдуулахгүй бол хаагдсан мөрөнд paused_minutes=0
    # хэвээр үлдэж, тайлан дээр «хасалт хийгээгүй мөртөө хямд» гэж харагдана.
    from .services.nested import close_open_pause
    close_open_pause(db, s, at)
    s.exit_time = at
    # Аль хэдийн баримтжсаныг бүү бууруул (camsync логоос цагийг нь оруулсан байж болно)
    s.exit_confirmed = bool(s.exit_confirmed) or confirmed
    s.duration_minutes = fee["duration_minutes"]
    s.base_fee, s.vat_amount, s.total_fee = fee["base_fee"], fee["vat_amount"], fee["total_fee"]
    # ТӨЛӨВ нь ЮУ БОЛСНЫГ хэлнэ, ХЭН хаасныг биш (түүнийг `closed_by` хэлдэг):
    #   CLOSED        — төлбөр барагдсан
    #   FREE          — төлбөр 0₮ (үнэгүй хугацаа/гэрээт/хөнгөлөлт)
    #   MANUAL_CLOSED — «гарах уншилтгүй», төлбөр үлдсэн
    # Өмнө нь 0₮ зогсолт ч `MANUAL_CLOSED` болж «Гараар хаасан» гэж харагддаг
    # байв — жинхэнэ гарц (`_close_and_open`) аль хэдийн энэ дүрэмтэй байсныг
    # албадан хаалтын зам мөрдөөгүйгээс (2026-08-16 Рашбулаг: 286-аас 138 мөр).
    s.status = ("CLOSED" if s.paid_at
                else "FREE" if fee["is_free"] else "MANUAL_CLOSED")
    if create_comp and due > 0 and not fee["is_free"]:
        from .routers.compensations_router import create_compensation
        comp = create_compensation(db, s, reason, username)
        comp.amount = due
        return due
    return 0.0


async def ensure_entry_barrier(db: Session, device: Device, plate: str,
                               session_id: str | None = None,
                               registered=None, screen_text: str = "") -> bool:
    """Орох хаалтыг нээх — ХАРИН саяхан нээсэн бол давтахгүй.

    Яагаад хэрэгтэй вэ: давхар уншилтын (dedup/burst) шүүлтүүрүүд нь ДАВХАР
    SESSION үүсэхээс сэргийлэх зорилготой боловч, өмнө нь хаалт нээх кодыг ч
    алгасаад буцдаг байв. Үр дүнд нь машин хаалганы өмнө зогсоод, жолооч
    дугаараа дахин уншуулах бүрд «дахин уншсан» гэж тооцогдож хаалт хэзээ ч
    нээгддэггүй байсан (lpr_dedup_seconds=20с тул 20 секундын турш гацна).

    Session үүсгэх эсэх нь тусдаа шийдэл — энэ функц ЗӨВХӨН хаалтыг хариуцна.
    """
    if not device.auto_open:
        return False
    barrier = _find_barrier(db, device.site_id, device)
    if not barrier:
        return False
    # ЯГ ОДОО явж буй команд байвал давтахгүй: DB-ийн cooldown нь SUCCESS болсныг
    # л хайдаг тул хараахан дуусаагүй командыг олж хардаггүй. Үр дүнд нэг камерт
    # хоёр RPC зэрэг очиж хоёулаа удаашрана (production: ганц команд 87-410мс,
    # давхацсан үед 749-985мс, нэг тохиолдолд хоёул 15 СЕКУНДЭД timeout болсон).
    from .services.barrier import open_in_flight
    if open_in_flight(barrier.id):
        return True
    # Саяхан амжилттай нээсэн бол дахин команд илгээхгүй (командын үер үүсгэхгүй)
    cooldown = datetime.utcnow() - timedelta(seconds=settings.barrier_reopen_cooldown_sec)
    recent = (db.query(BarrierCommand)
              .filter(BarrierCommand.device_id == barrier.id,
                      BarrierCommand.command == "open",
                      BarrierCommand.status == "SUCCESS",
                      BarrierCommand.created_at >= cooldown)
              .first())
    if recent:
        return True   # хаалт нээлттэй байгаа — дахин нээх шаардлагагүй
    source = "whitelist" if registered else "auto_entry"
    # Дэлгэцийг хаалттай ХАМТ (нэг сессээр) бичнэ — тусад нь нэвтрэхгүй
    cmd = await open_barrier(db, barrier, session_id, source, plate=plate,
                             screen_text=screen_text)
    return cmd.status == "SUCCESS"


async def ensure_exit_barrier_if_cleared(db: Session, device: Device, plate: str) -> bool:
    """Давхар уншилт дээр ГАРАХ хаалтыг дахин нээх — зөвхөн эрхтэй машинд.

    Орох талын ensure_entry_barrier-ийн гарах хувилбар. Ялгаа: орох талд болзолгүй
    нээж болдог бол энд ЗААВАЛ эрхийг шалгана — төлбөрөө төлөөгүй машиныг
    гаргах ёсгүй. Эрхтэй гэж үзэх тохиолдол:
      - өргүй гэрээт машин
      - PAID (grace дотор) / үнэгүй / үлдэгдэлгүй нээлттэй session
      - саяхан (5 мин дотор) гаргахаар хаагдсан session: хаалт нь амжилтгүй
        болсон ч session нь CLOSED болчихсон тохиолдол
    """
    barrier = _find_barrier(db, device.site_id, device)
    if not barrier:
        return False
    now = datetime.utcnow()
    # Саяхан АМЖИЛТТАЙ нээсэн бол хаалт нээлттэй хэвээр - команд давтахгүй
    cooldown = now - timedelta(seconds=settings.barrier_reopen_cooldown_sec)
    recent_ok = (db.query(BarrierCommand)
                 .filter(BarrierCommand.device_id == barrier.id,
                         BarrierCommand.command == "open",
                         BarrierCommand.status == "SUCCESS",
                         BarrierCommand.created_at >= cooldown)
                 .first())
    if recent_ok:
        return True

    # ГЭРЭЭТ машин ЯМАГТ гарна — төлбөр авдаггүй тул өр (ихэвчлэн орох уншилт
    # алдагдсанаас үүссэн хуучин артефакт) хаах шалтгаан болохгүй (2026-07-28
    # Monnis: өглөө гараар оруулсан гэрээт машин гарахдаа гацсан).
    registered = find_registered(db, plate, device.site_id)
    from .models import Compensation
    if not registered and db.query(Compensation).filter(
            Compensation.plate_number == plate,
            Compensation.status == "PENDING").count():
        return False  # өртэй (гэрээт биш) - оператор шийднэ

    session, _fuzzy = match_open_session(db, plate, device.site_id)
    entitled = False
    if registered:
        entitled = True
    elif session:
        if session.status == "PAID" and (not session.exit_deadline or now <= session.exit_deadline):
            entitled = True
        else:
            fee = session_fee_info(db, session, at=now)
            entitled = fee["is_free"] or amount_due(db, session, fee) <= 0
    else:
        # Саяхан гарахаар хаагдсан (хаалт нь амжилтгүй байж болзошгүй) машин
        entitled = bool(
            db.query(ParkingSession)
            .filter(ParkingSession.site_id == device.site_id,
                    ParkingSession.plate_number == plate,
                    ParkingSession.status.in_(["CLOSED", "FREE"]),
                    ParkingSession.exit_time >= now - timedelta(minutes=5))
            .first())
    if not entitled:
        return False
    # ЭРХ баталгаажсаны ДАРАА: яг одоо явж буй команд байвал давтахгүй. DB-ийн
    # cooldown нь SUCCESS болсныг л хайдаг тул хараахан дуусаагүй командыг олж
    # хардаггүй — нэг камерт хоёр RPC зэрэг очиж хоёулаа удаашрана (production:
    # ганц команд 87-410мс, давхацсан үед 749-985мс, нэг удаа хоёул 15с timeout).
    from .services.barrier import open_in_flight
    if open_in_flight(barrier.id):
        return True
    cmd = await open_barrier(db, barrier, session.id if session else None,
                             "exit_retry", plate=plate)
    if cmd.status != "SUCCESS":
        log.warning("[exit] давхар уншилт дээр хаалт дахин нээх оролдлого амжилтгүй: %s", plate)
    return cmd.status == "SUCCESS"


async def ensure_inner_barrier(db: Session, device: Device, session_id: str | None,
                               plate: str, source: str) -> bool:
    """ДОТООД (давхар зогсоолын) хаалтыг нээх — гадна талтай ижил хамгаалалттай.

    Уншилт бүрд ДАХИН нээхийг зөвшөөрнө: жолооч ухраад дахин ойртоход хаалт
    гарцаагүй хөдлөх ёстой (гадна талд энэ dedup-ийн улмаас машин гацдаг байсныг
    аль хэдийн зассан). Гэхдээ хамгаалалтгүй бол нэг машины 2-3 дараалсан уншилт
    камерын ховор RPC сесс рүү тэр бүрд шинэ команд илгээж, өөрсдөө хоорондоо
    өрсөлдөж удаашруулна (production дээр яг ийм хэв маяг бүртгэгдсэн):
      • open_in_flight — ЯГ ОДОО явж буй командыг давхардуулахгүй
      • cooldown       — саяхан амжилттай нээсэн бол дахин ачаалахгүй
    Амжилтгүй болсон командыг cooldown БАРИХГҮЙ тул дараагийн уншилт дахин оролдоно.
    """
    barrier = _find_barrier(db, device.site_id, device)
    if not barrier:
        return False
    from .services.barrier import open_in_flight
    if open_in_flight(barrier.id):
        return True
    cooldown = datetime.utcnow() - timedelta(seconds=settings.barrier_reopen_cooldown_sec)
    recent_ok = (db.query(BarrierCommand)
                 .filter(BarrierCommand.device_id == barrier.id,
                         BarrierCommand.command == "open",
                         BarrierCommand.status == "SUCCESS",
                         BarrierCommand.created_at >= cooldown)
                 .first())
    if recent_ok:
        return True
    cmd = await open_barrier(db, barrier, session_id, source, plate=plate)
    if cmd.status != "SUCCESS":
        log.warning("[nested] %s: дотоод %s хаалт НЭЭГДСЭНГҮЙ — %s", plate,
                    "орох" if source == "inner_entry" else "гарах",
                    (cmd.response_text or "")[:160])
    return cmd.status == "SUCCESS"


def _inner_lane_devices(site_id: str):
    """Зогсоолын ДОТООД (давхар зогсоолын) хаалтны төхөөрөмжүүдийн id-ийн subquery.

    Давхар уншилтын (dedup) шалгалтад хэрэглэнэ: доторх хаалтны уншилт нь ГАДНА
    орох/гарах уншилтыг «давхар» болгож залгих ёсгүй. Эс бол машин доторх
    зогсоолоос гараад шууд гадна гарцад ирэхэд гарах уншилт нь доторхтойгоо
    давхцаж, session хаагдалгүй машин гацна (2026-08-07).
    """
    return select(Device.id).where(Device.site_id == site_id,
                                   Device.nested_inner.is_(True))


def schedule_entry_hold(session_id: str, device_id: str, hold_seconds: int, policy: str):
    """Формат буруу дугаартай орох event-ийн хаалтын шийдвэрийг АРД НЬ хойшлуулна
    (snapshot/дэлгэцтэй ижил хэв маяг — event боловсруулалтыг хэзээ ч хүлээлгэхгүй)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return  # event loop-гүй орчин (тест г.м) — тест coroutine-ийг шууд дуудна
    asyncio.create_task(entry_hold_expire(session_id, device_id, hold_seconds, policy))


async def entry_hold_expire(session_id: str, device_id: str, hold_seconds: int, policy: str):
    """Hold цонх дуусахад: зөв уншилт ирээгүй л бол policy-гийн дагуу шийднэ.

    Зөв уншилт ИРСЭН бол юу ч хийхгүй — burst autocorrect session-ий дугаарыг
    зөв болгоод хаалтаа өөрөө нээчихсэн (тэр зам hold-д баригддаггүй).
    """
    await asyncio.sleep(max(1, hold_seconds))
    from .database import SessionLocal
    db = SessionLocal()
    try:
        s = db.get(ParkingSession, session_id)
        if not s or s.status != "OPEN":
            return          # хаагдсан/устгагдсан — шийдэх зүйл алга
        if is_valid_plate(s.plate_number):
            return          # autocorrect зөв дугаар авчирсан — хаалт нээгдсэн
        device = db.get(Device, device_id)
        opened = False
        if policy == "hold" and device:
            # fail-open: зөв уншилт ирсэнгүй ч машиныг гацаахгүй — нээгээд
            # тэмдэглэнэ. Оператор сүүлд нь snapshot-оос дугаарыг засна.
            opened = await ensure_entry_barrier(db, device, s.plate_number, s.id)
        db.add(AuditLog(username="system", action="ENTRY_HOLD", entity="session",
                        entity_id=s.id,
                        detail={"plate": s.plate_number, "policy": policy,
                                "hold_seconds": hold_seconds, "opened": opened}))
        db.commit()
        notify(s.site_id, "PLATE_UNREADABLE", {
            "session_id": s.id, "plate": s.plate_number, "lane": "entry",
            "policy": policy, "holding": False, "opened": opened})
        log.info("[entry-hold] %s: хүлээлт дууслаа — зөв уншилт ирсэнгүй "
                 "(policy=%s, opened=%s, session %s)",
                 s.plate_number, policy, opened, session_id)
    except Exception:
        log.exception("[entry-hold] шийдвэрийн алдаа (session %s)", session_id)
        db.rollback()
    finally:
        db.close()


async def handle_entry(db: Session, device: Device, plate: str, confidence: float, raw: dict,
                       allow_open: bool = True) -> dict:
    """Орох камерын event: session нээж, barrier нээнэ (blacklist биш бол).

    allow_open=False: дараалалд ХОЦОРСОН event — бүртгэлийг хэвийн хийнэ, харин
    хаалт НЭЭХГҮЙ (машиныг гараар оруулчихсан байхад хоосон зам руу онгойхгүй)."""
    site_id = device.site_id
    now = datetime.utcnow()

    # Хаалттай зогсоол: зөвхөн бүртгэлтэй машинд хаалт нээнэ (ажилчдын зогсоол
    # г.м). Dedup/burst замууд ч мөн адил шалгана — эс бол эхний татгалзсан
    # уншилтын дараах давтан уншилт хаалтыг нээчихнэ.
    _site = db.get(ParkingSite, site_id)
    restricted = bool(_site and _site.registered_only)

    # Орох дугаарын шалгалт (Тохиргоо → Дүрэм): формат буруу уншилтад хаалтыг
    # ШУУД нээхгүй, burst цонхны дахин уншилтыг хүлээнэ (hold), эсвэл огт
    # нээхгүй (strict). Хангарьд: 5-7 оронтой хог уншилт session болж, гарахдаа
    # «бүртгэлгүй гарах оролдлого» болдог байв (2026-08-21).
    from .services.app_settings import entry_plate_policy
    _pol, _hold_sec = entry_plate_policy(db, site_id)
    hold_active = _pol in ("hold", "strict") and not is_valid_plate(plate)

    # Давхар event хамгаалалт — OCR зөрүүтэй уншилтыг ч барина. Орох камер нэг
    # машиныг хэдэн секундын зайтай 2 удаа өөр дугаараар (Х/К, О/0 г.м. андуурч)
    # уншихад 2 тусдаа session үүсдэг байсныг (ж: 5155УХК + 5155УКК) зогсооно.
    recent_plates = [
        rp for (rp,) in db.query(LprEvent.plate_number).filter(
            LprEvent.site_id == site_id, LprEvent.lane_dir == "entry",
            LprEvent.device_id.notin_(_inner_lane_devices(site_id)),
            LprEvent.accepted.is_(True),
            LprEvent.created_at >= now - timedelta(seconds=settings.lpr_dedup_seconds),
        ).all()
    ]
    dup_of = [rp for rp in recent_plates if is_duplicate_read(plate, rp)]
    # ЗӨВ форматтай уншилт зөвхөн БУРУУ форматтай саяхны уншилтуудтай давхацвал
    # энэ нь junk-аар бүртгэгдсэн (hold-д баригдсан) машины ЖИНХЭНЭ дугаар:
    # dedup-аар хаявал session нь junk дугаартайгаа үлдэж гарахдаа таарахгүй.
    # Оронд нь доош — burst autocorrect руу үргэлжлүүлж дугаарыг засуулна.
    if dup_of and is_valid_plate(plate) and not any(is_valid_plate(rp) for rp in dup_of):
        dup_of = []
    if dup_of:
        # Давхар уншилт — шинэ session үүсгэхгүй, ГЭХДЭЭ хаалтыг заавал нээнэ:
        # машин хаалганы өмнө зогсож байгаа тул уншилт давтагдсан байж болно.
        # Hold горимд нэг л ялгаа: барьж буй машины ДАВТАН junk уншилт нээх
        # ёсгүй (эс бол хүлээлт утгагүй) — харин саяхны ЗӨВ уншилттай давхацсан
        # junk бол тэр зөв event хэдийнэ нээсэн тул энд нээх нь зөв хэвээр.
        _held_dup = hold_active and not any(is_valid_plate(rp) for rp in dup_of)
        _can_open = (allow_open and not _held_dup
                     and (not restricted or find_registered(db, plate, site_id)))
        opened = await ensure_entry_barrier(db, device, plate) if _can_open else False
        db.commit()
        return {"action": "dedup", "plate": plate, "barrier_opened": opened,
                "held": _held_dup}

    # ЦУВРАЛ уншилт: burst цонхонд (default 6с) энэ зогсоолын орох камерт өөр event
    # аль хэдийн ирсэн бол физикийн хувьд НЭГ машин (хаалтаар 6 секундэд 2 машин
    # орохгүй) — огт өөр уншигдсан ч шинэ session ҮҮСГЭХГҮЙ. Шинэ уншилт зөв
    # форматтай бол өмнөх session-ий дугаарыг сүүлийн (хамгийн ойрын, ихэвчлэн
    # хамгийн зөв) уншилтаар засна: 1101ЭН → 1310ХЭН → 7370ХЭН гэж нийлдэг.
    burst_prev = (db.query(LprEvent)
                  .filter(LprEvent.site_id == site_id, LprEvent.lane_dir == "entry",
                          LprEvent.device_id.notin_(_inner_lane_devices(site_id)),
                          LprEvent.accepted.is_(True),
                          LprEvent.created_at >= now - timedelta(seconds=settings.entry_burst_seconds))
                  .order_by(LprEvent.created_at.desc()).first())
    if burst_prev:
        if is_valid_plate(plate) and not get_open_session(db, plate, site_id):
            prev_session = get_open_session(db, burst_prev.plate_number, site_id)
            # Зөвхөн саяхан (энэ burst-д) үүссэн session-ийг л засна
            if prev_session and prev_session.entry_time >= now - timedelta(seconds=60):
                old_plate = prev_session.plate_number
                prev_session.plate_number = plate
                db.add(LprEvent(site_id=site_id, device_id=device.id, plate_number=plate,
                                lane_dir="entry", confidence=confidence, accepted=True,
                                raw=strip_images(raw)))
                db.add(AuditLog(username="system", action="PLATE_AUTOCORRECT", entity="session",
                                entity_id=prev_session.id,
                                detail={"old": old_plate, "new": plate,
                                        "reason": "цуврал уншилт — сүүлийн зөв уншилтаар"}))
                db.commit()
                log.info(f"[entry] цуврал уншилт: {old_plate} → {plate} (session {prev_session.id})")
                notify(site_id, "PLATE_EDITED", {
                    "session_id": prev_session.id, "old_plate": old_plate, "plate": plate,
                    "by": "system:autocorrect"})
                _reg = find_registered(db, plate, site_id)
                opened = (await ensure_entry_barrier(db, device, plate, prev_session.id, _reg)
                          if allow_open and (not restricted or _reg) else False)
                db.commit()
                return {"action": "plate_autocorrect", "session_id": prev_session.id,
                        "old": old_plate, "new": plate, "barrier_opened": opened}
        _reg = find_registered(db, plate, site_id)
        # Hold: өмнөх burst уншилт нь ч буруу форматтай бол машин баригдсан
        # хэвээр — junk-ийн junk давталт хаалт нээхгүй. Өмнөх нь зөв бол хаалт
        # аль хэдийн нээгдсэн (cooldown давтахгүй) тул нээх нь аюулгүй.
        _held_burst = hold_active and not is_valid_plate(burst_prev.plate_number)
        opened = (await ensure_entry_barrier(db, device, plate, registered=_reg)
                  if allow_open and not _held_burst and (not restricted or _reg) else False)
        db.commit()
        return {"action": "burst_dedup", "plate": plate, "barrier_opened": opened,
                "held": _held_burst}

    black = is_blacklisted(db, plate)
    registered = find_registered(db, plate, site_id)

    existing = get_open_session(db, plate, site_id)
    if existing and existing.exit_device_id and existing.status == "AWAITING_PAYMENT":
        # Машин өмнө нь гарах камерт уншигдаад ТӨЛБӨРГҮЙ гарсан байж — одоо дахин орж ирэв.
        # Хуучин session дээр наалдвал шинэ зогсолт огт бүртгэгдэхгүй (7/12, 7/20-ны гацаа).
        # Тиймээс: хуучныг өр (нөхөн төлбөр) үүсгэн хааж, шинэ session нээнэ.
        from .routers.compensations_router import create_compensation
        existing.exit_time = existing.updated_at or now
        existing.exit_confirmed = True   # гарах эгнээнд уншигдсан — бодит
        old_fee = session_fee_info(db, existing, at=existing.exit_time)
        existing.duration_minutes = old_fee["duration_minutes"]
        if existing.total_fee is None:
            existing.base_fee = old_fee["base_fee"]
            existing.vat_amount = old_fee["vat_amount"]
            existing.total_fee = old_fee["total_fee"]
        existing.status = "FREE" if old_fee["is_free"] else "MANUAL_CLOSED"
        due = amount_due(db, existing, old_fee)
        # Өр үүсгэх эсэх нь Тохиргоо → Авто цэвэрлэгээ хуудаснаас удирдагдана
        # (өмнө нь кодод хатуу бичигдсэн байв — 2026-08-21).
        from .services.app_settings import get_autoclose_rules
        if due > 0 and get_autoclose_rules(db)["create_debt_reentry"]:
            comp = create_compensation(db, existing, "unpaid_exit", "system")
            comp.amount = due
        # Энэ зам ямар ч AuditLog үлдээдэггүй байсан тул Түүх дээр «хэн хаасан»
        # нь хоосон харагддаг байв — хамгийн будлиантай тохиолдол (машин гарах
        # хаалтанд уншигдаад төлөлгүй үлдчихээд дахин орж ирсэн).
        from .models import AuditLog as _AuditLog
        db.add(_AuditLog(username="system", action="REENTRY_CLOSE", entity="session",
                         entity_id=existing.id,
                         detail={"plate": plate, "due": float(due)}))
        # uq_active_session: шинэ OPEN session оруулахын ӨМНӨ хаалтыг DB-д тулгана
        db.flush()
        if due > 0:
            notify(site_id, "DEBT_ALERT", {
                "plate": plate, "debt_count": 1, "debt_amount": float(due),
                "note": "Төлбөргүй гарсан машин дахин орж ирлээ — өр үүсгэв",
            })
        existing = None
    if existing:
        session = existing  # давхар орох event — session хэвээр
    else:
        _vcolor, _vtype = extract_vehicle_info(raw)
        session = ParkingSession(
            site_id=site_id, plate_number=plate, entry_time=now,
            entry_device_id=device.id, confidence_entry=confidence,
            is_registered=registered is not None, status="OPEN",
            vehicle_color=_vcolor, vehicle_type=_vtype,
        )
        db.add(session)
        db.flush()

    # Nested: энэ зогсоол өөр зогсоолын ДОТОР бол гадна session-ий төлбөрийн
    # тоолуурыг зогсооно. Давхар уншилтад найдвартай (аль хэдийн зогссоныг
    # дахин эхлүүлэхгүй) тул `existing` замд ч дуудаж болно.
    if _site and _site.parent_site_id:
        from .services.nested import on_inner_entry
        on_inner_entry(db, _site, plate, session.entry_time)

    db.add(LprEvent(site_id=site_id, device_id=device.id, plate_number=plate,
                    lane_dir="entry", confidence=confidence, accepted=True, raw=strip_images(raw)))
    db.commit()
    # Зургийг ард нь татаж хадгална (хаалт нээхийг хүлээлгэхгүй)
    schedule_capture(session.id, device.ip_address, plate, "entry", raw,
                             camera_credentials(device))

    # Орох дэлгэцийн текстийг УРЬДЧИЛЖ бэлдэнэ — хаалт нээх командтай ХАМТ
    # (нэг RPC сессээр) илгээгдэнэ. Ингэснээр нэвтрэлт хоёр дахин цөөрч,
    # камерын сессийн давхцлаас (худал «нууц үг буруу») сэргийлнэ.
    # Хар жагсаалт: ХОРИГЛОХ уу, эсвэл нэвтрүүлээд операторт анхааруулах уу
    # (Хар жагсаалт → Дүрэм). Default нь анхааруулга — машиныг гадаа орхивол
    # өрөө хэзээ ч төлөхгүй, харин оруулаад гарахад нь оператор өрийг авна.
    from .services.app_settings import get_blacklist_rules
    _bl_rules = get_blacklist_rules(db)
    black_blocks = bool(black) and _bl_rules["block_entry"]

    _local_hm = (session.entry_time + timedelta(hours=settings.tz_offset_hours)).strftime("%H:%M")
    denied = restricted and registered is None and not black_blocks
    # Формат буруу дугаар + hold/strict policy → хаалтыг түр барина (доор шийднэ)
    held = hold_active and not black_blocks and not denied
    _entry_lines = None if (black_blocks or denied or held) else _site_screen_lines(db, site_id, "entry")
    if black_blocks:
        _welcome = ""
    elif held:
        # 2 мөрт анхааруулга: жолооч дугаараа камерт дахин зөв уншуулбал burst
        # цонхонд autocorrect ажиллаж хаалт нээгдэнэ
        _welcome = render_screen_text(settings.screen_plate_unreadable_text,
                                      plate=plate, time_str=_local_hm)
    elif denied:  # хаалттай зогсоол — татгалзсан шалтгааныг дэлгэцэнд харуулна
        _welcome = render_screen_text("{plate}\nBurtgelgui mashin", plate=plate,
                                      time_str=_local_hm)
    elif _entry_lines:  # Тохиргоо → LED дэлгэц: зогсоолын өөрийн мөрүүд
        _welcome = _screen_text_from_lines(_entry_lines, plate=plate, time_str=_local_hm)
    else:
        _welcome = render_screen_text(settings.screen_welcome_text,
                                      plate=plate, time_str=_local_hm)

    barrier_opened = False
    if black:
        # Өрийн хэмжээг хамт илгээнэ — оператор шууд «хэдэн төгрөг нэхэхээ» мэдэж,
        # Кассын анхааруулгаас нэг товчоор өрийг барагдуулна.
        from .models import Compensation as _Comp
        _bl_debts = (db.query(_Comp).filter(_Comp.plate_number == plate,
                                            _Comp.status == "PENDING").all())
        notify(site_id, "BLACKLIST_ALERT", {
            "plate": plate, "reason": black.reason, "lane": "entry",
            "session_id": session.id,
            "blocked": black_blocks,
            "debt_count": len(_bl_debts),
            "debt_amount": float(sum(d.amount for d in _bl_debts)),
        })
    if black_blocks:
        pass  # хаалт нээхгүй — дүрмээр хориглосон
    elif denied:
        # Зөвхөн бүртгэлтэй машины зогсоол — хаалт нээхгүй, операторт мэдэгдэнэ
        notify(site_id, "UNREGISTERED_DENIED", {"plate": plate, "lane": "entry"})
    elif held:
        # Хаалтыг ОДООХОНДОО нээхгүй — _hold_sec хүлээгээд дахин шалгана:
        # burst autocorrect зөв дугаар авчирвал тэр зам өөрөө нээнэ; ирэхгүй
        # бол policy=hold үед нээгээд тэмдэглэнэ, strict үед оператор шийднэ.
        log.info("[entry-hold] %s: формат буруу — хаалтыг %sс барьж дахин уншилт хүлээнэ "
                 "(policy=%s, session %s)", plate, _hold_sec, _pol, session.id)
        if allow_open and device.auto_open:
            schedule_entry_hold(session.id, device.id, _hold_sec, _pol)
        notify(site_id, "PLATE_UNREADABLE", {
            "session_id": session.id, "plate": plate, "lane": "entry",
            "policy": _pol, "holding": True})
    elif device.auto_open and allow_open:
        barrier_opened = await ensure_entry_barrier(db, device, plate, session.id, registered,
                                                    screen_text=_welcome)

    notify(site_id, "ENTRY_EVENT", {
        "session_id": session.id, "plate": plate, "entry_time": session.entry_time.isoformat(),
        "registered": registered is not None, "blacklisted": black is not None,
        "denied": denied, "held": held, "barrier_opened": barrier_opened,
    })
    # Орох LED дэлгэцэнд орсон цаг + дугаар + мэндчилгээ (Managed горимд камер өөрөө
    # харуулахгүй тул сервер илгээнэ; blacklist бол харуулахгүй). Хаалт нээхийг
    # хүлээлгэхгүй, ард нь. {time} = УБ-ын локал цаг (DB нь UTC хадгалдаг).
    # Хаалттай хамт бичигдсэн бол энэ дуудлага өөрөө алгасагдана (screen_dedup_sec);
    # хаалтгүй/mock/амжилтгүй үед л камерт тусад нь очно.
    if _welcome:
        schedule_display(device.ip_address, _welcome, camera_credentials(device))
    return {"action": "entry", "session_id": session.id, "barrier_opened": barrier_opened,
            "held": held}


async def handle_inner_pass(db: Session, device: Device, plate: str, confidence: float,
                            raw: dict, allow_open: bool = True) -> dict:
    """ДОТООД (давхар) хаалтны event — НЭГ зогсоол доторх жижиг зогсоол.

    Ийм камер (device.nested_inner=True) session НЭЭХГҮЙ, ХААХГҮЙ. Зөвхөн:
      • lane_dir=entry → доторх зогсоолд орлоо   → төлбөрийн тоолуур ЗОГСОНО
      • lane_dir=exit  → доторх зогсоолоос гарлаа → тоолуур ҮРГЭЛЖИЛНЭ
    Дараа нь өөрийнхөө эгнээний хаалтыг нээнэ.

    Гадна орох уншилт алдагдсан (идэвхтэй session алга) машиныг ЗААВАЛ
    нэвтрүүлнэ — хасах хугацаа байхгүй болохоос доторх хаалт нь машиныг
    гацаах ёсгүй. Тэр машин гарцдаа энгийнээр төлбөр төлнө.
    """
    from .services.nested import cap_minutes, pause_session, resume_session

    now = datetime.utcnow()
    site = db.get(ParkingSite, device.site_id)
    # ЯГ таарахгүй бол OCR-ойролцоо тохирол (гарах хаалттай ижил дүрэм) —
    # шороон зогсоолд бохир дугаарыг дотоод камер өөр уншихад тоолуур
    # зогсдоггүй, машин доторх (үнэгүй) хугацаагаа бүрэн төлдөг байв
    # (2026-08-11 Рашбулаг ЭТТ: 165 машинаас «2 дотор» гэж харагдсан шалтгаан).
    session, fuzzy = match_open_session(db, plate, device.site_id)
    if fuzzy:
        log.info("[nested] %s: дотоод камерын уншилтыг OCR-ойролцоо «%s» session-д тохов",
                 plate, session.plate_number)
    entering = device.lane_dir != "exit"

    if session is None and is_valid_plate(plate):
        # САЯХАН ГАРСАН машиныг нөхөж болохгүй: шороон зогсоолд эгнээ байхгүй тул
        # гарч яваа машиныг дотоод ОРОХ камер дахин уншдаг (2026-08-11 Рашбулаг,
        # 18:17-18:42 event-үүд) — session нь хаагдчихсан байхад нөхвөл гарсан
        # машин «дотор зогсож байгаа» хий бүртгэл болно.
        just_left = (db.query(ParkingSession.id)
                     .filter(ParkingSession.site_id == device.site_id,
                             ParkingSession.plate_number == plate,
                             ParkingSession.exit_time.isnot(None),
                             ParkingSession.exit_time >= now - timedelta(minutes=15))
                     .first())
        if just_left:
            log.info("[nested] %s: 15 минутын дотор гарсан машин — нөхөж үүсгэхгүй", plate)
        else:
            # Дотоод камерт уншигдсан машин гадна хаалтаар орсон нь гарцаагүй —
            # гадна орох камер (шороо/тоос) уншиж чадаагүй бол машин session-гүй
            # «үл үзэгдэгч» болж, дотор тоолол ч, төлбөр ч алдагддаг байв
            # (2026-08-11 Рашбулаг: дотор байсан 46 машины 15 нь session-гүй).
            # Нөхөж үүсгэнэ: тоолуур одооноос, дотогшоо бол шууд зогсоно.
            session = ParkingSession(
                site_id=device.site_id, plate_number=plate, entry_time=now,
                entry_device_id=device.id, status="OPEN", confidence_entry=confidence,
                is_registered=find_registered(db, plate, device.site_id) is not None,
                note="Дотоод камераас нөхөж үүсгэв (гадна орох уншилт алдагдсан)")
            db.add(session)
            db.flush()
            log.info("[nested] %s: гадна орох уншилт алдагдсан — session нөхөж үүсгэв (%s)",
                     plate, session.id[:8])

    if entering:
        changed = pause_session(session, now)
        action = "inner_entry"
    else:
        changed = bool(resume_session(session, now, cap_minutes(site)))
        action = "inner_exit"

    db.add(LprEvent(site_id=device.site_id, device_id=device.id, plate_number=plate,
                    lane_dir=device.lane_dir, confidence=confidence, accepted=True,
                    raw=strip_images(raw)))
    db.commit()

    if session is None:
        log.info("[nested] %s: дотоод %s хаалт — зогсоолд идэвхтэй бүртгэл алга, "
                 "тоолуур хөндөхгүй нэвтрүүлэв", plate, "орох" if entering else "гарах")

    # `auto_open` нь ЗӨВХӨН ОРОХ чиглэлд утгатай. Гарах хаалт нь эрхээр
    # (төлсөн/үнэгүй/гэрээт) шийдэгддэг болохоос энэ чагтаар биш — тийм учраас
    # UI-д ч зөвхөн орох камерт харагддаг. Дотоод ГАРАХ камерыг үүгээр хаавал
    # админ асаах ч аргагүй чагтын улмаас машин доторх зогсоолд гацна
    # (2026-08-08 Рашбулаг ЭТТ: «Гарах 2» дээр яг ийм зүйл болсон).
    barrier_opened = False
    if allow_open and (device.auto_open or not entering):
        barrier_opened = await ensure_inner_barrier(
            db, device, session.id if session else None, plate, action)
    elif allow_open:
        # Чимээгүй бүү өнгөр: «дотоод хаалт нээгдэхгүй байна» гэдгийн хамгийн
        # түгээмэл шалтгаан нь энэ чагт унтарсан байх явдал бөгөөд лог дээр ямар
        # ч ул мөр үлдэхгүй тул оношлоход хэцүү байв.
        log.warning("[nested] %s: «%s» камерын «Автомат нээх» унтраалттай тул дотоод "
                    "орох хаалт нээгээгүй. Тохиргоо → Төхөөрөмж дээр асаана уу.",
                    plate, device.name)

    notify(device.site_id, "INNER_PASS", {
        "session_id": session.id if session else None, "plate": plate,
        "lane_dir": device.lane_dir, "paused": bool(session and session.paused_since),
        "paused_minutes": int(session.paused_minutes or 0) if session else 0,
        "barrier_opened": barrier_opened,
    })
    return {"action": action, "session_id": session.id if session else None,
            "counter_changed": changed, "barrier_opened": barrier_opened}


async def handle_exit(db: Session, device: Device, plate: str, confidence: float, raw: dict,
                      allow_open: bool = True) -> dict:
    """Гарах камерын event:
    - Төлсөн (grace хугацаанд) эсвэл үнэгүй/гэрээт бол barrier нээж session хаана.
    - Үгүй бол AWAITING_PAYMENT болгож касс/PAX/QR руу мэдэгдэнэ.

    allow_open=False: дараалалд ХОЦОРСОН event — бүртгэл/төлбөрийн урсгал хэвийн,
    харин хаалт НЭЭХГҮЙ (машин аль хэдийн гарчихсан байж болно).
    """
    site_id = device.site_id
    now = datetime.utcnow()

    # Давхар event хамгаалалт — OCR зөрүүтэй уншилтыг ч барина. Камер гарах дугаарыг
    # хэдэн секундын зайтай 2 дахь удаа арай ӨӨР уншихад (жишээ 2 дахь уншилт session-
    # тэй таарахгүй) LED-д "бүртгэлгүй" текст илгээж, төлбөрийн текстийг дардаг байсныг
    # зогсооно (орох талтай ижил OCR-тэсвэртэй дүрэм).
    recent_plates = [
        rp for (rp,) in db.query(LprEvent.plate_number).filter(
            LprEvent.site_id == site_id, LprEvent.lane_dir == "exit",
            LprEvent.device_id.notin_(_inner_lane_devices(site_id)),
            LprEvent.accepted.is_(True),
            LprEvent.created_at >= now - timedelta(seconds=settings.lpr_dedup_seconds),
        ).all()
    ]
    if any(is_duplicate_read(plate, rp) for rp in recent_plates):
        # Давхар уншилт — session/төлбөрийг ДАХИН боловсруулахгүй. ГЭХДЭЭ өмнөх
        # уншилтын хаалтны команд амжилтгүй болсон бол машин хаалганы өмнө
        # lpr_dedup_seconds (20с) турш ГАЦНА: жолооч ухраад дахин ойртох бүрд
        # «дахин уншсан» гэж тооцогдоод хаалт огт нээгддэггүй байв (орох талд
        # энэ алдаа аль хэдийн зассан, гарах талд үлдсэн байсан).
        # Тиймээс ГАРАХ ЭРХТЭЙ (гэрээт/төлсөн/үнэгүй) машинд хаалтыг дахин нээнэ.
        opened = await ensure_exit_barrier_if_cleared(db, device, plate) if allow_open else False
        db.commit()
        return {"action": "dedup", "plate": plate, "barrier_opened": opened}

    session, fuzzy = match_open_session(db, plate, site_id)
    if session is None:
        # Идэвхтэй бүртгэл алга — саяхан АЛБАДАН хаагдсаныг сэргээж үзнэ.
        # Машин дотор байсаар байтал авто хаалт хаачихсан тохиолдол (7 хоногт
        # «бүртгэлгүй гарах»-ын 23%). Олдвол ердийн гарах урсгал үргэлжилнэ.
        session = auto_reopen_for_exit(db, plate, site_id)
    # Гарах камерын өнгө/төрлөөр session-ий мэдээллийг нөхнө (орох дээр алга байсан
    # эсвэл орох дутуу уншсан бол) — оператор дугаар зөрүүтэй үед машиныг таних тусламж
    if session:
        _xc, _xt = extract_vehicle_info(raw)
        if _xc and not session.vehicle_color:
            session.vehicle_color = _xc
        if _xt and not session.vehicle_type:
            session.vehicle_type = _xt
    db.add(LprEvent(site_id=site_id, device_id=device.id, plate_number=plate,
                    lane_dir="exit", confidence=confidence, accepted=True, raw=strip_images(raw)))
    if session and fuzzy:
        # Гарах камер орох дугаараас өөр уншсан (OCR зөрүү) — ойролцоо session-д
        # тохоов. Ил тод байдлын үүднээс тэмдэглэж, аудитад бичнэ.
        from .models import AuditLog
        old_plate = session.plate_number
        # ОРОХ ДУТУУ уншсан байсан бол гарах ЗӨВ дугаараар ЗАСНА — цэвэр дата
        # үлдээж, дараагийн тайлан/хайлт зөв дугаараар ажиллана. Төлбөр нь
        # session-ий entry_time-аар бодогдох тул орсон цаг зөв хэвээр.
        corrected = not is_valid_plate(old_plate) and is_valid_plate(plate)
        if corrected:
            session.plate_number = plate
            note = f"Орох дутуу уншилт «{old_plate}» → гарах зөв «{plate}» (засав; төлбөр орсон цагаар)"
        else:
            note = f"Гарах OCR зөрүү: уншсан «{plate}» → «{old_plate}»"
        session.note = f"{session.note + ' | ' if session.note else ''}{note}"[:1000]
        db.add(AuditLog(username="system",
                        action="EXIT_PLATE_CORRECT" if corrected else "EXIT_OCR_MATCH",
                        entity="session", entity_id=session.id,
                        detail={"read_plate": plate, "entry_plate": old_plate,
                                "corrected": corrected}))
        log.info(f"[exit] {'дугаар засав' if corrected else 'OCR зөрүү тохов'}: "
                 f"{old_plate} → {plate} (session {session.id})")

    # #6 Өртэй машин — гарах камерт уншигдмагц касст шууд сануулах
    from .models import Compensation
    debts = db.query(Compensation).filter(Compensation.plate_number == plate,
                                          Compensation.status == "PENDING").all()
    debt_amount = float(sum(c.amount for c in debts))
    if debts:
        notify(site_id, "DEBT_ALERT", {
            "plate": plate, "debt_count": len(debts), "debt_amount": debt_amount})

    if not session:
        # ГЭРЭЭТ машин: session олдоогүй ч (орох уншилт алдагдсан, жагсаалтад
        # дараа нэмэгдсэн г.м.) төлөх зүйлгүй тул ХОРИХ ёсгүй — шууд гаргана.
        # Өмнө нь «Бүртгэл олдсонгүй» гэж LED-д гаргаад хаалт нээгддэггүй байв.
        # ӨРТЭЙ байсан ч гаргана (2026-07-28 Monnis): гэрээт машинаас төлбөр
        # авдаггүй тул өр нь ихэвчлэн орох/гарах уншилт алдагдсанаас үүссэн
        # артефакт — DEBT_ALERT дээр аль хэдийн мэдэгдсэн, оператор хянана.
        reg = find_registered(db, plate, site_id)
        if reg:
            db.commit()
            barrier = _find_barrier(db, site_id, device)
            opened = False
            _reg_lines = _site_screen_lines(db, site_id, "exit")
            if _reg_lines:
                _reg_hm = (datetime.utcnow()
                           + timedelta(hours=settings.tz_offset_hours)).strftime("%H:%M")
                _bye_reg = _screen_text_from_lines(_reg_lines, plate=plate,
                                                   time_str=_reg_hm, reason="Гэрээт")
            else:
                _bye_reg = render_screen_text(settings.screen_bye_registered_text, plate=plate)
            if barrier and allow_open:
                cmd = await open_barrier(db, barrier, None, "whitelist", plate=plate,
                                         screen_text=_bye_reg)
                opened = cmd.status == "SUCCESS"
            notify(site_id, "EXIT_NO_SESSION", {
                "plate": plate, "has_debt": bool(debts), "debt_amount": debt_amount,
                "registered": True, "barrier_opened": opened})
            schedule_display(device.ip_address, _bye_reg, camera_credentials(device))
            return {"action": "registered_exit", "plate": plate, "barrier_opened": opened}

        # Session олдсонгүй — оператор шийднэ (гараар нээх боломжтой)
        db.commit()
        notify(site_id, "EXIT_NO_SESSION",
                                {"plate": plate, "has_debt": bool(debts), "debt_amount": debt_amount})
        schedule_display(device.ip_address,
                         render_screen_text(settings.screen_nosession_text, plate=plate),
                         camera_credentials(device))
        return {"action": "no_session", "plate": plate}

    session.exit_device_id = device.id
    session.confidence_exit = confidence
    # Гарах зургийг ард нь татаж хадгална (маргаан/нотолгоонд — ялангуяа төлбөргүй гарсан үед)
    schedule_capture(session.id, device.ip_address, plate, "exit", raw,
                             camera_credentials(device))

    fee = session_fee_info(db, session, at=now)

    # Өртэй машин — гарах хаалтыг автоматаар нээхгүй, оператор өрийг цуглуулна.
    # Босгыг Хар жагсаалт → Дүрэм хэсгээс тохируулна (0 = саатуулахгүй).
    # ГЭРЭЭТ машинд үйлчлэхгүй (төлбөр авдаггүй тул ямагт гарна; session_fee_info
    # дээр is_registered шинэчлэгдсэн байгаа).
    from .services.app_settings import get_blacklist_rules
    _exit_block_at = get_blacklist_rules(db)["block_exit_debt_count"]
    if _exit_block_at and len(debts) >= _exit_block_at and not session.is_registered:
        session.status = "AWAITING_PAYMENT"
        session.duration_minutes = fee["duration_minutes"]
        session.base_fee, session.vat_amount, session.total_fee = (
            fee["base_fee"], fee["vat_amount"], fee["total_fee"])
        db.commit()
        due_now = amount_due(db, session, fee)
        notify(site_id, "EXIT_LPR_EVENT", {
            "session_id": session.id, "plate": plate,
            "entry_time": session.entry_time.isoformat(),
            "duration_minutes": fee["duration_minutes"], "total_fee": fee["total_fee"],
            "amount_due": due_now,
            "has_debt": True, "debt_amount": debt_amount, "blocked": True})
        # Дэлгэцэнд өнөөдрийн төлбөр + өмнөх өрийн нийлбэрийг харуулна
        _txt = render_screen_text(settings.screen_fee_text,
                                  amount=due_now + debt_amount, plate=plate,
                                  duration_minutes=fee["duration_minutes"])
        schedule_display(device.ip_address, _txt,
                         _txt if settings.screen_voice else None,
                         camera_credentials(device))
        return {"action": "debt_blocked", "plate": plate, "debt_amount": debt_amount}

    # Төлчихсөн — grace хугацаа дотор гарч байна
    if session.status == "PAID":
        if not session.exit_deadline or now <= session.exit_deadline:
            return await _close_and_open(db, device, session, now, fee, source="auto_exit",
                                         allow_open=allow_open)
        # Grace хэтэрсэн — нэмэлт төлбөр шаардана (доор үлдэгдлээр шалгана)
        session.status = "AWAITING_PAYMENT"

    if fee["is_free"]:
        session.status = "PAID"  # үнэгүй тул шууд гаргана
        return await _close_and_open(db, device, session, now, fee, source="auto_exit",
                                     allow_open=allow_open)

    # Үлдэгдэл тооцох: өмнө нь төлсөн бол (grace хэтэрсэн тохиолдол) зөвхөн зөрүүг нэхнэ.
    # Тарифын шатлал ахиагүй бол зөрүү 0 — нэмэлт төлбөргүйгээр гаргана.
    due = amount_due(db, session, fee)
    if due <= 0 and session.paid_at:
        session.status = "PAID"
        return await _close_and_open(db, device, session, now, fee, source="auto_exit",
                                     allow_open=allow_open)

    # ── Данснаас автомат хасалт (EV_CHARGING_PLAN.md §6.2) ────────────────
    # Үлдэгдэл ХҮРЭЛЦВЭЛ: хасаад шууд нээнэ — жолооч юу ч хийхгүй.
    # ХҮРЭЛЦЭХГҮЙ бол: байгааг нь хасаад үлдсэн дүнд ердийн QR урсгал.
    # Данс нь EV-ээс хамааралгүй бие даасан боломж — бүх жолоочид ажиллана.
    if due > 0:
        try:
            deducted, covered = await _wallet_auto_deduct(db, session, due)
        except Exception:  # noqa: BLE001 — данс унасан ч гарах урсгал зогсохгүй
            log.exception("wallet auto-deduct алдаа: session=%s", session.id)
            deducted, covered = 0.0, False
        if covered:
            # _finalize_paid дотор session PAID + хаалт + e-Barimt бүгд хийгдсэн
            return {"action": "paid_from_wallet", "session_id": session.id,
                    "plate": plate, "amount": deducted}
        if deducted:
            due = amount_due(db, session, fee)

    # Төлбөртэй — төлбөр хүлээнэ
    session.status = "AWAITING_PAYMENT"
    session.duration_minutes = fee["duration_minutes"]
    session.base_fee = fee["base_fee"]
    session.vat_amount = fee["vat_amount"]
    session.total_fee = fee["total_fee"]
    db.commit()

    notify(site_id, "EXIT_LPR_EVENT", {
        "session_id": session.id, "plate": plate,
        "entry_time": session.entry_time.isoformat(),
        "duration_minutes": fee["duration_minutes"], "total_fee": fee["total_fee"],
        "amount_due": due,
        "has_debt": bool(debts), "debt_amount": debt_amount,
    })
    # Гарах хаалтны LED дэлгэцэнд төлөх дүнг харуулна (ард нь, урсгалыг хүлээлгэхгүй).
    # Өртэй машинд ӨМНӨХ ӨРИЙГ НИЙЛҮҮЛЖ нэхэмжилнэ (жолооч нийт дүнгээ шууд харна).
    fee_text = render_screen_text(settings.screen_fee_text,
                                  amount=due + debt_amount, plate=plate,
                                  duration_minutes=fee["duration_minutes"])
    schedule_display(device.ip_address, fee_text,
                     fee_text if settings.screen_voice else None,
                         camera_credentials(device))
    return {"action": "awaiting_payment", "session_id": session.id,
            "total_fee": fee["total_fee"], "amount_due": due,
            "debt_amount": debt_amount}


def _site_screen_lines(db: Session, site_id: str, lane: str) -> list | None:
    """Зогсоолын LED мөрийн тохиргоо (Тохиргоо → LED дэлгэц). None = тохиргоогүй
    → глобал .env template-ууд хэвээр үйлчилнэ."""
    site = db.get(ParkingSite, site_id) if site_id else None
    cfg = getattr(site, "screen_config", None) or {}
    lines = cfg.get(lane)
    return lines if isinstance(lines, list) and lines else None


def _screen_text_from_lines(lines: list, *, plate: str = "", time_str: str = "",
                            duration_minutes=None, amount=None,
                            payment: str = "", reason: str = "") -> str:
    """Тохируулсан мөрүүдээс LED-ийн эцсийн текстийг угсарна. Хоосон утгатай
    мөр (ж: төлбөртэй гарахад {reason}) өөрөө хасагдана — LED-д цоорхой үлдэхгүй."""
    out = []
    for ln in lines[:4]:
        t = (ln or {}).get("type") if isinstance(ln, dict) else None
        if t == "time":
            v = time_str
        elif t == "plate":
            v = plate
        elif t == "duration":
            v = format_duration(duration_minutes)
        elif t == "amount":
            v = "" if amount is None else f"{int(round(float(amount)))}T"
        elif t == "payment":
            v = payment
        elif t == "reason":
            v = reason
        elif t == "text":
            v = str(ln.get("text", ""))
        else:
            v = ""
        v = (v or "").strip()
        if v:
            out.append(v)
    return "\n".join(out)


# Payment.payment_method → LED дээр харуулах нэр
_PAYMENT_LABELS = {"QR": "QPay", "CARD": "Карт", "CASH": "Бэлэн", "TRANSFER": "Данс"}


def _payment_label(db: Session, session: ParkingSession) -> str:
    """Session-ий төлбөр ЯМАР хэрэгслээр төлөгдсөнийг буцаана (төлөөгүй бол "")."""
    if not session.paid_at:
        return ""
    p = (db.query(Payment)
         .filter(Payment.session_id == session.id, Payment.status == "PAID")
         .order_by(Payment.created_at.desc()).first())
    m = ((p.payment_method if p else "") or "").upper()
    return _PAYMENT_LABELS.get(m, m.capitalize())


def _bye_screen_text(db: Session, session: ParkingSession, fee: dict) -> str:
    """Гарах дэлгэцийн текст — ЯАГААД гарч байгааг нь жолоочид хэлнэ:
      • Гэрээт        — бүртгэлтэй машины жагсаалтад байгаа (төлбөр авдаггүй)
      • Түр зогссон   — үнэгүй хугацаанд (ж: эхний 15 мин) багтсан
      • Баяртай       — төлбөрөө төлж гарч байна
    fee["reason"] нь billing.calculate_fee-ээс ирнэ («Бүртгэлтэй жолооч»,
    «Эхний N минут үнэгүй», «Хөнгөлөлт: ...»).
    Зогсоолд LED мөрийн тохиргоо (screen_config.exit) байвал түүгээр угсарна:
    {payment} = төлсөн хэрэгсэл, {reason} = үнэгүй гарсан шалтгаан."""
    reason = (fee or {}).get("reason") or ""
    registered = session.is_registered or reason == "Бүртгэлтэй жолооч"
    free = bool(fee.get("is_free")) and not session.paid_at
    lines = _site_screen_lines(db, session.site_id, "exit")
    if lines:
        _local_hm = ((session.exit_time or datetime.utcnow())
                     + timedelta(hours=settings.tz_offset_hours)).strftime("%H:%M")
        return _screen_text_from_lines(
            lines, plate=session.plate_number, time_str=_local_hm,
            duration_minutes=fee.get("duration_minutes"),
            amount=None if free else fee.get("total_fee"),
            payment=_payment_label(db, session),
            reason=("Гэрээт" if registered else (reason or "Үнэгүй") if free else ""))
    if registered:
        tmpl = settings.screen_bye_registered_text
    elif free:
        tmpl = settings.screen_bye_free_text
    else:
        tmpl = settings.screen_bye_text
    return render_screen_text(tmpl, plate=session.plate_number,
                              duration_minutes=fee.get("duration_minutes"),
                              amount=fee.get("total_fee"))


async def _close_and_open(db: Session, exit_device: Device, session: ParkingSession,
                          now: datetime, fee: dict, source: str,
                          allow_open: bool = True) -> dict:
    # ─── Nested (дамжин) зогсоол ───────────────────────────────────────────
    from .services.nested import close_open_pause, on_inner_exit
    _site = session.site
    if _site and _site.parent_site_id:
        # ДОТОРХ зогсоолоос гарлаа — ГАДНА зогсоолын тоолуур үргэлжилнэ
        on_inner_exit(db, _site, session.plate_number, now)
    else:
        # ГАДНА зогсоолоос гарлаа — доторх гарах уншилт алдагдсан ч зогсолт
        # энд хаагдана (эс бол тоолуур мөнхөд зогсож 0₮ болно). ЧУХАЛ: fee нь
        # дуудагч талд аль хэдийн тооцоологдсон бөгөөд ижил хязгаарыг хэрэглэдэг
        # тул дүн өөрчлөгдөхгүй — энд зөвхөн session дээр БАРИМТЖУУЛНА.
        close_open_pause(db, session, now)
    session.exit_time = now
    session.exit_confirmed = True   # камерын гарах уншилт — бодит
    session.duration_minutes = fee["duration_minutes"]
    if session.total_fee is None:
        session.base_fee = fee["base_fee"]
        session.vat_amount = fee["vat_amount"]
        session.total_fee = fee["total_fee"]
    session.status = "FREE" if (fee["is_free"] and not session.paid_at) else "CLOSED"

    # ЧУХАЛ: session-ий өөрчлөлтийг хаалт нээхийн ӨМНӨ commit хийнэ.
    # Өмнө нь хаалтны RPC (15с хүртэл) хугацаанд транзакц нээлттэй байж
    # parking_sessions мөрийг ТҮГЖДЭГ байв — тэр үед зургийн background task-ийн
    # UPDATE lock_timeout(10с)-д унаж, зургийн зам бичигдэхгүй үлддэг байлаа.
    # Зан төлөв өөрчлөгдөхгүй: session нь хаалт амжилттай эсэхээс үл хамааран
    # хаагддаг байсан (доорх barrier_opened зөвхөн мэдэгдэлд ашиглагдана).
    db.commit()

    barrier = _find_barrier(db, session.site_id, exit_device)
    barrier_opened = False
    _bye = _bye_screen_text(db, session, fee)
    if barrier and allow_open:
        cmd = await open_barrier(db, barrier, session.id, source, plate=session.plate_number,
                                 screen_text=_bye)
        barrier_opened = cmd.status == "SUCCESS"

    notify(session.site_id, "EXIT_COMPLETED", {
        "session_id": session.id, "plate": session.plate_number,
        "status": session.status, "barrier_opened": barrier_opened,
        "total_fee": float(session.total_fee or 0),
    })
    # Дэлгэцэнд мэндчилгээ (төлбөр төлөгдсөн/үнэгүй — хаалт нээгдэж байна)
    # Хаалттай хамт бичигдсэн бол алгасагдана (screen_dedup_sec)
    schedule_display(exit_device.ip_address, _bye, camera_credentials(exit_device))
    return {"action": "exit_completed", "session_id": session.id, "barrier_opened": barrier_opened}


def _find_barrier(db: Session, site_id: str, near_device: Device) -> Device | None:
    """Тухайн lane-ийн barrier төхөөрөмжийг олно (ижил lane_no, эсвэл эхний barrier)."""
    # Хосолгох дүрэм НЭГ газар: `barrier_matches_camera` — хаалт ҮҮСГЭХ
    # (`ensure_lane_barriers`) болон хаалт ОЛОХ (энд) хоёр ижил дүрмээр явна.
    # Хоёр нь зөрвөл «үүссэн хаалтаа өөрөө олохгүй» гэсэн чимээгүй анги үүсдэг.
    # Дүрэм: ижил ЭГНЭЭ + чиглэл (эсвэл "both") + ижил дотоод/гадна. Дотоод
    # (давхар зогсоолын) хаалт нь гаднахаас БИЕ ДААСАН — доторх камерын команд
    # гадна хаалтыг нээвэл машин төлбөр төлөхгүй зогсоолоос шууд гарна.
    from .services.device_auto import barrier_matches_camera
    bars = db.query(Device).filter(
        Device.site_id == site_id, Device.device_type == "barrier",
        Device.status == "active",
    ).order_by(Device.created_at, Device.id).all()
    barrier = next((b for b in bars if barrier_matches_camera(near_device, b)), None)
    if barrier:
        return barrier
    # ӨӨР ЭГНЭЭНИЙ хаалт руу ХЭЗЭЭ Ч үсрэхгүй. Өмнө нь чиглэл таарсан ЭХНИЙ
    # хаалтыг буцаадаг байсан нь машин ирээгүй газар хаалт нээх ХОЁР УДААГИЙН
    # ослын шууд шалтгаан болсон:
    #   • 2026-08-26 Рашбулаг ЭТТ — `nested_inner` санамсаргүй унтарснаар доторх
    #     камерын уншилт ГУДАМЖНЫ хаалтыг 197 удаа нээсэн (жолоочдын гомдол).
    #   • 2026-08-28 Маршил — эгнээ 3,4-т хаалт огт үүсээгүй тул эгнээ 3-ын
    #     уншилт эгнээ 1-ийн хаалтыг нээх байсан (`6254d23`-аар хаалт нь үүсдэг
    #     болсон ч энэ үсрэлт өөрөө хэвээр байв).
    # Одоо `ensure_lane_barriers` идэвхтэй камер бүрд ижил эгнээний хаалтыг
    # баталгаажуулдаг тул энэ салаа нь ЗӨВХӨН тохиргоо эвдэрсэн үед л хүрнэ —
    # тэр үед БУРУУ хаалт нээхээс ЮУ Ч ХИЙХГҮЙ нь дээр (буруу нээлт = төлбөргүй
    # гарах / гудамжинд хий нээлт, харин нээхгүй нь = лог + улаан анхааруулга).
    log.error("%s (эгнээ %s/%s, дотоод=%s): ЭНЭ ЭГНЭЭНД хаалт бүртгэгдээгүй — "
              "команд илгээхгүй (өөр эгнээний хаалт нээхээс сэргийлэв). Тохиргоо → "
              "Төхөөрөмж дээр эгнээ %s-д %s хаалт нэмнэ үү.",
              near_device.name, near_device.lane_no, near_device.lane_dir,
              bool(near_device.nested_inner), near_device.lane_no,
              "орох" if near_device.lane_dir == "entry" else "гарах")
    return None


async def mark_paid_and_open(db: Session, session: ParkingSession, grace_minutes: int | None = None) -> None:
    """Төлбөр амжилттай болмогц дуудагдана: session-ийг PAID болгож, exit lane-ийн barrier нээнэ."""
    now = datetime.utcnow()
    session.paid_at = now
    site: ParkingSite = session.site
    template = site.tariff_template if site else None
    g = grace_minutes if grace_minutes is not None else (template.grace_minutes if template else 15)
    session.exit_deadline = now + timedelta(minutes=g)
    session.status = "PAID"

    # Гарах гэж зогсож байгаа бол (exit камерт аль хэдийн уншигдсан) шууд нээнэ
    if session.exit_device_id:
        exit_device = db.get(Device, session.exit_device_id)
        if exit_device:
            fee = session_fee_info(db, session, at=now)
            await _close_and_open(db, exit_device, session, now, fee, source="payment")
            return
    db.commit()
    notify(session.site_id, "PAYMENT_COMPLETED", {
        "session_id": session.id, "plate": session.plate_number,
        "exit_deadline": session.exit_deadline.isoformat(),
    })


async def _wallet_auto_deduct(db: Session, session: ParkingSession,
                              due: float) -> tuple[float, bool]:
    """Гарах хаалтан дээрх дансны автомат хасалт (EV_CHARGING_PLAN.md §6.2).

    Дараалал: (1) дотоод данс — хэсэгчилсэн хасалт зөвшөөрнө;
              (2) гадаад wallet-ууд (site/wallet.easy-parking.mn) — зөвхөн
                  БҮТЭН дүн хүрэлцэх үед (хоёр системийн хооронд хэсэгчилсэн
                  тооцоо нийлүүлэх эрсдэлээс зайлсхийнэ).

    → (хассан дүн, бүтэн төлөгдсөн эсэх). Бүтэн төлөгдсөн үед _finalize_paid
    дотор session PAID + хаалт + e-Barimt бүгд хийгдсэн байна.
    Физик нотолгоо (§1.2): энэ функц зөвхөн ГАРАХ КАМЕРТ дугаар уншигдсаны
    дараа дуудагддаг тул хангагдсан."""
    from decimal import Decimal
    from .models import Wallet
    from .services import wallet as wallet_svc
    from .services.wallet_providers import external_providers
    from .routers import payments_router as pr

    site = db.get(ParkingSite, session.site_id)
    tenant_id = site.tenant_id if site else None
    plate = normalize_plate(session.plate_number)

    # ── 1. Дотоод данс ──
    w = wallet_svc.find_wallet(db, tenant_id, plate)
    if w and w.status == "ACTIVE" and float(w.balance or 0) > 0:
        balance = float(w.balance)
        take = min(balance, due)
        covered = take >= due - 0.01
        # Payment-ийг эхлээд PENDING-ээр үүсгэж (дүн нь _create_payment-ийн
        # дотоод дүрмээр), дараа нь данснаас хасна — нэг транзакцид.
        payment = pr._create_payment(db, session, provider="WALLET",
                                     method="WALLET", include_debts=False)
        if not covered:
            payment.amount = Decimal(str(round(take)))
            r = settings.vat_rate
            payment.vat_amount = Decimal(str(round(take * r / (1 + r))))
        payment.kind = "PARKING"
        payment.wallet_id = w.id
        payment.source = "WALLET"
        wallet_svc.debit_parking(db, w.id, float(payment.amount), session.id,
                                 note=f"гарах хаалт {plate}")
        session.paid_from_wallet = True
        db.commit()
        if covered:
            await pr._finalize_paid(db, payment)
            db.commit()
            log.info("данснаас БҮТЭН төлөгдөв: %s %s₮ (үлдэгдэл %s₮)",
                     plate, float(payment.amount), float(w.balance))
            return float(payment.amount), True
        # Хэсэгчилсэн: төлбөрийг PAID болгоод (finalize ХИЙХГҮЙ — хаалт нээхгүй)
        payment.status = "PAID"
        payment.paid_at = datetime.utcnow()
        db.commit()
        log.info("данснаас ХЭСЭГЧЛЭН: %s %s₮/%s₮", plate, take, due)
        return take, False

    # ── 2. Гадаад wallet-ууд (бүтэн дүн л) ──
    for provider in external_providers():
        try:
            info = await provider.balance(plate)
            if not info.get("found") or float(info.get("balance") or 0) < due:
                continue
            payment = pr._create_payment(db, session, provider=provider.name,
                                         method="WALLET", include_debts=False)
            payment.kind = "PARKING"
            payment.source = "WALLET"
            db.commit()
            res = await provider.debit(plate, float(payment.amount),
                                       ref=f"parking-{payment.id}",
                                       note=f"Зогсоол {site.name if site else ''}")
            payment.provider_payment_id = res.get("tx_id") or None
            session.paid_from_wallet = True
            db.commit()
            await pr._finalize_paid(db, payment)
            db.commit()
            log.info("%s-ээс БҮТЭН төлөгдөв: %s %s₮", provider.name, plate, due)
            return float(payment.amount), True
        except Exception as e:  # noqa: BLE001 — нэг provider унавал дараагийнх
            log.warning("%s auto-deduct алдаа (%s) — алгасав", provider.name, e)
            db.rollback()
    return 0.0, False
