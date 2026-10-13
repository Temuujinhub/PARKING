"""Nested (дамжин) зогсоол — доторх зогсоолд байх хугацаанд ГАДНА зогсоолын
төлбөрийн тоолуурыг зогсоох.

Жишээ урсгал:

    гадна орох  ─► [ГАДНА]  ─► дотор орох ─► [ДОТОР: тоолуур ЗОГСНО]
                      ▲                              │
    гадна гарах ◄─────┴───────────────────  дотор гарах

⚠ Аль IP нь «дотор орох», аль нь «дотор гарах» болохыг ЭНД БҮҮ таамаглаарай —
камер солигдож/дахин байрлуулагдаж болно. Чиглэл СОЛИГДВОЛ систем чимээгүй
буруу тооцно: гаднах ТӨЛБӨРТЭЙ талбарт зогссон машины тоолуур зогсож (дутуу
нэхэмжилнэ), доторх ҮНЭГҮЙ талбарынх ажиллана. Хаалт нээгдсээр байдаг тул
гомдол ирэхгүй. Уншилтын дарааллаас нотлох:
`venv/bin/python tools/nested_lanes.py <SITE>`.

Гадна зогсоолын төлбөр = (гарсан − орсон) − (дотор өнгөрүүлсэн хугацаа).
Доторх зогсоол руу огт ороогүй машин энгийнээр бодогдоно.

Загварын гол шийдвэрүүд:
  • Доторх зогсоол нь ТУСДАА ParkingSite бөгөөд `parent_site_id`-аар гадныхаа
    заана. Session-ий идэвхтэй unique индекс нь (site_id, plate_number) тул нэг
    машин гадна/дотор хоёуланд зэрэг нээлттэй session-тэй байж чадна.
  • Тоолуур зогсолтыг ГАДНА session дээр хадгална: `paused_since` (одоо дотор
    байгаа бол орсон цаг) + `paused_minutes` (хуримтлагдсан). Машин дотогш
    олон удаа орж болох тул хуримтлуулна.
  • Доторх ГАРАХ уншилт алдагдвал тоолуур мөнхөд зогсох тул `transit_max_hours`
    дээд хязгаар тавина — түүнээс хэтэрсэн хугацаа хасагдахгүй, төлбөртэй.

Хязгаарын дүрэм (2026-10-13; «доторх бүх БАТЛАГДСАН хугацааг хасна» — эзэн):
  • БАТЛАГДСАН зогсолт — доторх ГАРАХ уншилтаар (live `on_inner_exit`,
    `handle_inner_pass`-ийн гарах, camera_sync-ийн логийн дотоод/хүүхэд
    сешний гарах уншилт) хаагдсан: тохируулсан хязгаар
    (`child_cap_minutes`: хүүхэд → эцэг → глобал). 0 = ХЯЗГААРГҮЙ.
  • camera_sync/албадан хаалт хүүхэд сешнийг «хуурамч гарц»-ын босгоос
    (`fake_exit_minutes`) БОГИНО зогсолттой хаавал энэ нь батлагдсан гарц БИШ:
    доторх ГАРАХ камер дөнгөж ОРЖ буй машиныг уншдаг (cross-read, 15–104с).
    Эцгийн зогсолт нээлттэй үлдэнэ (`session_logic.close_session_forced`).
  • БАТЛАГДААГҮЙ зогсолт — доторх гарах уншилт ирээгүй, гадна гарц/албадан
    хаалт дээр барагдуулсан (`close_open_pause`), мөн одоо явж буй зогсолтын
    урьдчилсан дүн (`effective_paused_minutes`): `unverified_cap_minutes` —
    тохируулсан хязгаар > 0 бол тэр, 0 бол глобал `settings.transit_max_hours`
    (глобал ч 0 бол хязгааргүй). Шалтгаан: эцгийн 0-ийг хүүхэд өвлөснөөс
    (100 айл) доторх гарах уншилт АЛДАГДСАН машин (доторх зогсоолоос гарч
    гадна талбайд зогссон ч) бүх гадна хугацаагаа үнэгүй авах байв. Үнэ нь:
    1937УАЕ 10-09 үнэхээр 7ц дотор байсан ч гарах уншилт нь зөвхөн татгалзсан
    (итгэл 26) тул 4ц хасагдана — прод 8,000₮-тэй ижил (зөв дүн 0₮).
    Энэ дүрэм ЗӨВХӨН тусдаа site-тай (parent/child) загварт; НЭГ site доторх
    (`nested_inner` камер) загварт 0 = хязгааргүй хэвээр (Рашбулаг ЭТТ —
    2026-09-19 эзэн баталсан `owner_confirmed_unlimited_inner_time`).
  • Машин дотор байх үеийн урьдчилсан дүн нь «одоо гадна гарцад гарах уншилтгүй
    ирвэл» авах дүнтэй ЯГ ижил (гарц ч мөн `unverified_cap_minutes`-ээр
    барагдуулна). Доторх гарах уншилт ирвэл хасалт зөвхөн ӨСНӨ (дүн буурна).

Хэнд хасалт өгөх (2026-10-13):
  • Зөвхөн доторх хаалтаар НЭВТРЭХ эрхтэй машин (`_admitted` — live
    `handle_entry`-ийн `denied`/`black_blocks`-тэй ижил). Татгалзсан машинд ч
    доторх сешн үүсдэг (Рашбулаг шороо 10-12..13: 19 сешн, хаалтны команд 0) —
    сэргээлт/нөхөлтийн зам тэднийг «дотор байсан» гэж тооцохгүй.
  • Өмнөх айлчлалаас үлдсэн (гадна сешн эхлэхээс өмнө нээгдсэн) доторх сешн
    энэ айлчлалд зөвхөн шинэ доторх ОРОХ уншилтаас тоологдоно (8810УКР).
"""
import logging
from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..config import settings
from ..models import LprEvent, ParkingSession, ParkingSite

log = logging.getLogger("parking.nested")

_ACTIVE = ("OPEN", "AWAITING_PAYMENT", "PAID")
# Доторх сешн гадна сешнээс ийм хэмжээгээр ӨМНӨ эхэлсэн бол «өмнөх айлчлалынх»
# (камер/серверийн цагийн бага зөрүүг тэвчинэ)
_STALE_TOLERANCE = timedelta(minutes=2)


def _admitted(db: Session, child_site: ParkingSite, plate: str) -> bool:
    """Энэ машинд доторх хаалт НЭЭГДЭХ үү — live `handle_entry`-ийн `denied`
    («Зөвхөн бүртгэлтэй» зогсоолын бүртгэлгүй машин) ба `black_blocks`
    (хар жагсаалт + орохыг хориглох дүрэм)-тэй ижил шийдвэр.

    Татгалзсан машинд ч доторх сешн үүсдэг (хаалт нээгдэхгүй) — Рашбулаг шороо
    10-12..13-нд 19 доторх сешн, нэг ч хаалтны команд алга. Live зам тэдний
    гадна тоолуурыг зогсоодоггүй; сэргээлт/нөхөлтийн зам ч зогсоох ёсгүй
    (эс бол гадна сешн «хуурамч гарц»-аар сэргэхэд 4ц хүртэл үнэгүй болно).
    Одоогийн бүртгэл/хар жагсаалтаар шалгана (сешнд шийдвэр хадгалагддаггүй)."""
    from ..session_logic import find_registered, is_blacklisted
    if child_site.registered_only and find_registered(db, plate, child_site.id) is None:
        return False
    if is_blacklisted(db, plate) is not None:
        from .app_settings import get_blacklist_rules
        if get_blacklist_rules(db, child_site.id)["block_entry"]:
            return False
    return True


def cap_minutes(site: ParkingSite | None) -> int:
    """Тоолуур зогсох дээд минут. 0 = хязгааргүй."""
    hours = None if site is None else site.transit_max_hours
    if hours is None:
        hours = settings.transit_max_hours
    return max(0, int(hours)) * 60


def child_cap_minutes(db: Session, child: ParkingSite) -> int:
    """ТУСДАА site-тай (parent/child) загварын хүүхэд зогсоолын хязгаар.

    Хүүхэд дээр тохируулаагүй (NULL) бол ЭЦЭГ зогсоолын утгыг авна, тэр ч
    хоосон бол глобал. Өмнө нь зөвхөн хүүхдийнхийг уншдаг байсан тул админ
    «Дотор хугацааг хасах хязгаар»-ыг гадна (эцэг) зогсоол дээр 0 гэж тавихад
    backend үл тоож глобал 4 цагаар тасалдаг байв — 100 айл 10-07..10-12:
    доторх ГАРАХ уншилттай 7–13ц дотор байсан 29 гадна сешнд 4 цагийг л хасаж
    352,000₮ нэхсэн (audit1013 дахин тооцоолол). 0 = хязгааргүй нь зөвхөн
    БАТЛАГДСАН зогсолтод — модулийн docstring-ийн хязгаарын дүрмийг үз."""
    if child is not None and child.transit_max_hours is None and child.parent_site_id:
        parent = db.get(ParkingSite, child.parent_site_id)
        if parent is not None and parent.transit_max_hours is not None:
            return cap_minutes(parent)
    return cap_minutes(child)


def pause_cap_minutes(db: Session, site_id: str) -> int:
    """Гадна session дээрх зогсолтод хэрэглэх дээд хязгаар (минут). 0 = хязгааргүй.

    Гадна session дээр зогсолт үлдсэн (paused_since) байхад ямар хязгаар
    хэрэглэхээ мэдэх хэрэгтэй, гэвч аль доторх зогсоолд орсныг session өөрөө
    хадгалдаггүй.

      • ТУСДАА site-тай (parent/child) загвар: хүүхэд зогсоол(ууд)-ын тохиргоо
        (хүүхэд дээр хоосон бол эцгийнх — `child_cap_minutes`).
        Хэд хэдэн доторх зогсоолтой бол ХАМГИЙН БАГА хязгаарыг авна (аюулгүй тал).
      • НЭГ site доторх (`nested_inner` камер) загвар: хүүхэд зогсоол байхгүй тул
        тухайн зогсоолын ӨӨРИЙН `transit_max_hours`. Өмнө нь энэ тохиолдолд
        глобал default руу шууд унадаг байсан — үр дүнд нь доторх ГАРАХ уншилт
        алдагдсан машин зогсоолын тохиргоог тойрч, ЗӨВ уншсанаас ИЛҮҮ хугацаа
        үнэгүй авдаг байв (жагсаалт дээрх дүн ба гарц дээрх дүн ч зөрдөг).
    """
    return _configured_cap(db, site_id)[0]


def _configured_cap(db: Session, site_id: str) -> tuple[int, bool]:
    """(`pause_cap_minutes`, хүүхэд зогсоолтой эсэх) — нэг асуулгаар."""
    kids = db.query(ParkingSite).filter(ParkingSite.parent_site_id == site_id).all()
    if not kids:
        return cap_minutes(db.get(ParkingSite, site_id)), False
    caps = [child_cap_minutes(db, k) for k in kids]
    return (0 if 0 in caps else min(caps)), True


def unverified_cap_minutes(db: Session, site_id: str) -> int:
    """Доторх ГАРАХ уншилтаар БАТЛАГДААГҮЙ зогсолтын хязгаар (минут). 0 = хязгааргүй.

    Тусдаа site-тай (parent/child) загварт «0 = хязгааргүй» нь зөвхөн жинхэнэ
    доторх гарах уншилтаар хаагдсан зогсолтод үйлчилнэ. Гарах уншилтгүй
    (гадна гарц/албадан хаалт дээр барагдуулсан, эсвэл одоо явж буй) зогсолтод
    тохируулсан хязгаар 0 бол глобал `transit_max_hours`-ыг хэрэглэнэ — эс бол
    доторх гарах уншилт алдагдсан машин (доторх зогсоолоос гараад гадна талбайд
    зогссон ч) бүх гадна хугацаагаа үнэгүй авна. 1937УАЕ (10-09, 7ц дотор,
    гарах уншилт зөвхөн татгалзсан) энэ хязгаараар 4ц = прод 8,000₮.

    НЭГ site доторх загвар (хүүхэд зогсоолгүй) `pause_cap_minutes`-тэй ижил —
    Рашбулаг ЭТТ-ийн 0-ийг эзэн 2026-09-19 «хязгааргүй» гэж баталсан."""
    cap, split = _configured_cap(db, site_id)
    if cap or not split:
        return cap
    return max(0, int(settings.transit_max_hours)) * 60


def _parent_session(db: Session, site: ParkingSite, plate: str) -> ParkingSession | None:
    """Гадна зогсоол дээрх тухайн машины идэвхтэй session."""
    if not site or not site.parent_site_id:
        return None
    return (db.query(ParkingSession)
            .filter(ParkingSession.site_id == site.parent_site_id,
                    ParkingSession.plate_number == plate,
                    ParkingSession.status.in_(_ACTIVE))
            .order_by(ParkingSession.entry_time.desc()).first())


def settle_pause(s: ParkingSession, at: datetime, cap: int) -> int:
    """Явж буй зогсолтыг хааж `paused_minutes` дээр нэмнэ. Нэмсэн минутыг буцаана.

    cap > 0 бол ЭНЭ УДААГИЙН зогсолт тэр хэмжээгээр таслагдана — доторх гарах
    уншилт алдагдсан тохиолдолд бүх хугацаа үнэгүй болохоос сэргийлнэ.
    """
    if not s.paused_since:
        return 0
    mins = max(0, int((at - s.paused_since).total_seconds() // 60))
    if cap:
        mins = min(mins, cap)
    s.paused_minutes = int(s.paused_minutes or 0) + mins
    s.paused_since = None
    return mins


def effective_paused_minutes(db: Session, s: ParkingSession, at: datetime) -> int:
    """Төлбөр тооцоход хасагдах минут — хуримтлагдсан + ЯВЖ БУЙ зогсолт.

    Session-ийг ӨӨРЧЛӨХГҮЙ (зөвхөн уншина) — жагсаалт/урьдчилсан тооцоонд
    аюулгүй дуудагдана. Талбарыг getattr-аар уншина: `session_fee_info` нь
    тестүүдэд хиймэл (ORM бус) session обьекттой ч дуудагддаг.
    """
    total = int(getattr(s, "paused_minutes", 0) or 0)
    since = getattr(s, "paused_since", None)
    if since:
        # Явж буй зогсолт = доторх гарах уншилтаар хараахан батлагдаагүй. Гадна
        # гарц дээр `close_open_pause` ИЖИЛ хязгаараар барагдуулна — урьдчилсан
        # дүн гарц дээр өөрчлөгдөхгүй (модулийн docstring-ийн хязгаарын дүрэм).
        cap = unverified_cap_minutes(db, s.site_id)
        mins = max(0, int((at - since).total_seconds() // 60))
        total += min(mins, cap) if cap else mins
    return total


def on_inner_entry(db: Session, site: ParkingSite, plate: str, at: datetime) -> bool:
    """Доторх зогсоолд ОРЛОО — гадна session-ий тоолуурыг зогсооно.

    Давхар уншилтад найдвартай: аль хэдийн зогссон бол дахин эхлүүлэхгүй.
    Гадна session олдохгүй бол (гадна орох уншилт алдагдсан) юу ч хийхгүй —
    хасах хугацаа байхгүй тул доторх зогсолт энгийнээр явна.
    """
    parent = _parent_session(db, site, plate)
    if parent is None:
        log.info("[nested] %s: «%s»-д орлоо ч гадна зогсоолд идэвхтэй бүртгэл алга — "
                 "тоолуур зогсоохгүй", plate, site.name)
        return False
    if parent.paused_since:
        return False   # аль хэдийн зогссон (давхар уншилт)
    # Гадна сешн эхлэхээс ӨМНӨХ цагаас зогсоохгүй: хуучин (гарах уншилтгүй
    # үлдсэн) доторх сешний entry_time энд ирж болно — хасалт гадна зогсолтоос
    # хэтэрч бүх гадна хугацаа үнэгүй болдог байв (8810УКР 2026-10-13).
    parent.paused_since = max(at, parent.entry_time) if parent.entry_time else at
    log.info("[nested] %s: «%s»-д орлоо — гадна «%s»-ын тоолуур зогслоо",
             plate, site.name, getattr(parent.site, "name", parent.site_id))
    return True


def on_inner_exit(db: Session, site: ParkingSite, plate: str, at: datetime) -> int:
    """Доторх зогсоолоос ГАРЛАА — гадна session-ий тоолуурыг үргэлжлүүлнэ."""
    parent = _parent_session(db, site, plate)
    if parent is None or not parent.paused_since:
        return 0
    if parent.paused_since > at:
        # Энэ гарц нь ӨМНӨХ айлчлалынх (camera_sync/албадан хаалт өнгөрсөн
        # цагаар дууддаг) — түүнээс хойш эхэлсэн шинэ зогсолтыг бүү хаа.
        return 0
    # Жинхэнэ доторх ГАРАХ уншилт — БАТЛАГДСАН зогсолт: тохируулсан хязгаар
    # (0 = хязгааргүй). Модулийн docstring-ийн хязгаарын дүрмийг үз.
    mins = settle_pause(parent, at, child_cap_minutes(db, site))
    log.info("[nested] %s: «%s»-аас гарлаа — гадна тоолуур %d минут зогссон "
             "(нийт %d мин хасагдана)", plate, site.name, mins, parent.paused_minutes)
    return mins


def restore_parent_pause(db: Session, child_site: ParkingSite, child: ParkingSession,
                         since: datetime | None) -> bool:
    """ДОТООД (хүүхэд) сешн «хуурамч гарц»-аас АВТО/гараар СЭРГЭЭГДЛЭЭ — машин
    дотроо байсаар байжээ. Гадна тоолуурыг тэр «гарц»-ын цагаас дахин зогсооно.

    Доторх ГАРАХ камер дөнгөж ОРЖ буй машиныг уншиж (100 айл 8507УБР
    2026-10-12: орсноос 15с-ийн дараа) хүүхэд сешн хаагдахад `on_inner_exit`
    гадна тоолуурыг үргэлжлүүлдэг. Жинхэнэ гарах уншилтаар хүүхэд сешн сэргэхэд
    гадна тоолуур ҮРГЭЛЖИЛСЭЭР байсан тул дотор өнгөрүүлсэн 7 цагийг нэхдэг байв
    (4688УБН, 7969УАА, 8507УБР — 70,000₮; зөв дүн 0₮).

    `since` (хуурамч гарцын цаг) хүртэлх минут `paused_minutes`-д аль хэдийн
    орсон тул давхар тоологдохгүй; дараагийн `on_inner_exit` хязгаараар барагдуулна.
    Зөвхөн тэр үед идэвхтэй байсан, одоо OPEN, зогсоогүй гадна сешнд, доторх
    хаалтаар нэвтрэх эрхтэй машинд (`_admitted`)."""
    if since is None or not _admitted(db, child_site, child.plate_number):
        return False
    parent = _parent_session(db, child_site, child.plate_number)
    if (parent is None or parent.status != "OPEN" or parent.paused_since
            or parent.entry_time > since):
        return False
    parent.paused_since = since
    log.info("[nested] %s: «%s» сешн сэргэв — гадна тоолуурыг %s-аас дахин зогсоов",
             child.plate_number, child_site.name, since)
    return True


def reapply_child_pauses(db: Session, s: ParkingSession, since: datetime | None,
                         now: datetime) -> int:
    """ГАДНА (эцэг) сешн сэргээгдлээ — хаалттай байсан хугацаанд (since..now)
    машин доторх зогсоолд байсан бол тэр хугацааг хасалтад нэмнэ.

    Гадна ГАРАХ камер дөнгөж орж буй машиныг уншиж гадна сешн FREE хаагдана →
    машин доторх зогсоолд ороход идэвхтэй гадна сешн алга тул тоолуур зогсохгүй
    (`on_inner_entry`) → жинхэнэ гарахад гадна сешн «хуурамч гарц» гэж сэргэж
    дотор өнгөрүүлсэн бүх цагийг нэхдэг байв (100 айл 4216УБО 2026-10-07: 11ц,
    25,000₮; зөв дүн 0₮).

      • ДОТООД гарах уншилттай (exit_confirmed) хаагдсан хүүхэд сешн — давхцсан
        хэсгийг (интервалуудын нэгдэл) БАТЛАГДСАН хязгаараар `paused_minutes`-д нэмнэ.
      • Нээлттэй хүүхэд сешн — `paused_since`-ийг эхлүүлнэ; гадна гарц дээр
        `close_open_pause` батлагдаагүй хязгаараар барагдуулна (live замтай ижил).
      • Гарах уншилтгүй албадан хаагдсан хүүхэд сешн — цаг нь таамаг тул тооцохгүй.
      • Доторх хаалтаар нэвтрэх эрхгүй машины (татгалзсан/хориглогдсон) сешн —
        машин дотогш ороогүй (`_admitted`), тооцохгүй.
      • ӨМНӨХ айлчлалаас үлдсэн (гадна сешнээс өмнө нээгдсэн) хүүхэд сешн —
        зөвхөн энэ айлчлалын доторх ОРОХ уншилтаас (`existing` замаар дахин
        ашиглагдсан) тоолно; уншилтгүй бол машин гадаа байсан гэж үзнэ. Эс бол
        гарах уншилтгүй үлдсэн phantom сешн гадаа зогссон хугацааг хасдаг (8810УКР
        хэв маяг; on_inner_entry-ийн «ОДООГООС» дүрэмтэй ижил).
    Цонх нь ямагт [сүүлийн хаалт, одоо] тул дахин сэргээхэд давхар тоологдохгүй.
    Хүүхэд зогсоолгүй (НЭГ site) зогсоолд юу ч хийхгүй."""
    if since is None or since >= now:
        return 0
    kids = {k.id: k for k in db.query(ParkingSite)
            .filter(ParkingSite.parent_site_id == s.site_id).all()}
    if not kids:
        return 0
    rows = (db.query(ParkingSession)
            .filter(ParkingSession.site_id.in_(list(kids)),
                    ParkingSession.plate_number == s.plate_number,
                    ParkingSession.entry_time < now,
                    (ParkingSession.exit_time.is_(None)) | (ParkingSession.exit_time > since))
            .all())
    visit_start = s.entry_time - _STALE_TOLERANCE if s.entry_time else None
    admitted: dict[str, bool] = {}
    spans, open_start = [], None
    for c in rows:
        if c.site_id not in admitted:
            admitted[c.site_id] = _admitted(db, kids[c.site_id], s.plate_number)
        if not admitted[c.site_id]:
            continue
        start = c.entry_time
        if visit_start is not None and c.entry_time < visit_start:
            start = (db.query(func.min(LprEvent.created_at))
                     .filter(LprEvent.site_id == c.site_id,
                             LprEvent.plate_number == s.plate_number,
                             LprEvent.lane_dir == "entry",
                             LprEvent.accepted.is_(True),
                             LprEvent.created_at >= visit_start,
                             LprEvent.created_at <= (c.exit_time or now))
                     .scalar())
            if start is None:
                continue
        start = max(start, since)
        if c.exit_time is None:
            if c.status in _ACTIVE:
                open_start = start if open_start is None else min(open_start, start)
            continue
        if c.exit_confirmed:
            spans.append((start, min(c.exit_time, now)))
    cap = pause_cap_minutes(db, s.site_id)
    added, cur = 0, None
    for a, b in sorted(spans) + [(None, None)]:          # интервалуудын нэгдэл
        if cur and (a is None or a > cur[1]):
            mins = max(0, int((cur[1] - cur[0]).total_seconds() // 60))
            added += min(mins, cap) if cap else mins
            cur = None
        if a is not None and b > a:
            cur = (a, b) if cur is None else (cur[0], max(cur[1], b))
    if added:
        s.paused_minutes = int(s.paused_minutes or 0) + added
    if open_start is not None and not s.paused_since:
        s.paused_since = open_start
    if added or open_start is not None:
        log.info("[nested] %s: гадна сешн сэргэв — доторх %d мин хасалтад нэмэв%s",
                 s.plate_number, added,
                 f", тоолуур {open_start}-аас зогссон" if open_start is not None else "")
    return added


def credit_backfilled_child(db: Session, child_site: ParkingSite, plate: str,
                            start: datetime, end: datetime | None) -> int:
    """camera_sync-аар НӨХӨЖ үүсгэсэн доторх сешн — live `on_inner_entry`
    дуудагдаагүй тул гадна тоолуур огт зогсоогүй (100 айл 10-07..10-12: 8333УАО,
    3016УНЕ, 5508УАТ — дотор 8–9ц, 68,000₮ нэхэмжилсэн, зөв дүн 0₮).

    Гадна сешн тэр үед идэвхтэй (start-аас өмнө орсон) бол:
      • end байхгүй (машин дотроо) → тоолуурыг start-аас зогсооно;
      • end байгаа (логийн ГАРАХ уншилт = батлагдсан) → [start, end] минутыг
        тохируулсан хязгаараар шууд нэмнэ.
    Гадна тоолуур аль хэдийн зогссон бол давхар тооцохгүй. Логт ОРОХ уншилт
    байгаа ч доторх хаалтаар нэвтрэх эрхгүй машинд (`_admitted`) хасалт өгөхгүй
    — камер татгалзсан машиныг ч уншдаг."""
    parent = _parent_session(db, child_site, plate)
    if parent is None or parent.entry_time > start or parent.paused_since:
        return 0
    if not _admitted(db, child_site, plate):
        return 0
    if end is None:
        parent.paused_since = start
        log.info("[nested] %s: «%s»-ийн нөхсөн бүртгэл — гадна тоолуурыг %s-аас зогсоов",
                 plate, child_site.name, start)
        return 0
    mins = max(0, int((end - start).total_seconds() // 60))
    cap = child_cap_minutes(db, child_site)
    mins = min(mins, cap) if cap else mins
    parent.paused_minutes = int(parent.paused_minutes or 0) + mins
    log.info("[nested] %s: «%s»-ийн нөхсөн бүртгэл — гадна сешнд %d мин хасалт нэмэв",
             plate, child_site.name, mins)
    return mins


def close_open_pause(db: Session, s: ParkingSession, at: datetime) -> int:
    """Гадна session хаагдахад үлдсэн зогсолтыг хаана.

    Машин гадна гарцад уншигдсан гэдэг нь доторх зогсоолоос гарсан гэсэн үг —
    доторх ГАРАХ уншилт алдагдсан ч гэсэн. Хязгаараар таслагдана.

    Талбарыг getattr-аар уншина: `close_session_forced` нь тестүүдэд хиймэл
    (ORM бус) session обьекттой ч дуудагддаг — `effective_paused_minutes`-тэй ижил.
    """
    if not getattr(s, "paused_since", None):
        return 0
    # Доторх гарах уншилтгүй → БАТЛАГДААГҮЙ зогсолт (`unverified_cap_minutes`)
    mins = settle_pause(s, at, unverified_cap_minutes(db, s.site_id))
    log.info("[nested] %s: гадна гарцад уншигдлаа — доторх гарах уншилтгүй үлдсэн "
             "зогсолтыг %d минутаар хаав", s.plate_number, mins)
    return mins


def same_site_session(db: Session, site_id: str, plate: str) -> ParkingSession | None:
    """НЭГ зогсоол доторх давхар зогсоолд хэрэглэнэ — тухайн зогсоолын идэвхтэй session."""
    return (db.query(ParkingSession)
            .filter(ParkingSession.site_id == site_id,
                    ParkingSession.plate_number == plate,
                    ParkingSession.status.in_(_ACTIVE))
            .order_by(ParkingSession.entry_time.desc()).first())


def pause_session(s: ParkingSession | None, at: datetime) -> bool:
    """Тоолуурыг зогсооно. Давхар уншилтад найдвартай (зогссоныг дахин эхлүүлэхгүй)."""
    if s is None or s.paused_since:
        return False
    s.paused_since = at
    return True


def resume_session(s: ParkingSession | None, at: datetime, cap: int) -> int:
    """Тоолуурыг үргэлжлүүлж, зогссон минутыг хуримтлуулна."""
    if s is None or not s.paused_since:
        return 0
    return settle_pause(s, at, cap)


def inside_nested_count(db: Session, site_id: str) -> int:
    """Тухайн ГАДНА зогсоолын машинуудаас хэд нь ОДОО доторх зогсоолд байгаа вэ."""
    return (db.query(ParkingSession)
            .filter(ParkingSession.site_id == site_id,
                    ParkingSession.status.in_(_ACTIVE),
                    ParkingSession.paused_since.isnot(None)).count())
