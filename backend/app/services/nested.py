"""Nested (дамжин) зогсоол — доторх зогсоолд байх хугацаанд ГАДНА зогсоолын
төлбөрийн тоолуурыг зогсоох.

Жишээ урсгал (Рашбулаг ЭТТ):

    .10 гадна орох  ─► [ГАДНА]  ─► .12 дотор орох ─► [ДОТОР: тоолуур ЗОГСНО]
                          ▲                                    │
    .11 гадна гарах ◄─────┴──────────────────────────  .13 дотор гарах

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
"""
import logging
from datetime import datetime

from sqlalchemy.orm import Session

from ..config import settings
from ..models import ParkingSession, ParkingSite

log = logging.getLogger("parking.nested")

_ACTIVE = ("OPEN", "AWAITING_PAYMENT", "PAID")


def cap_minutes(site: ParkingSite | None) -> int:
    """Тоолуур зогсох дээд минут. 0 = хязгааргүй."""
    hours = None if site is None else site.transit_max_hours
    if hours is None:
        hours = settings.transit_max_hours
    return max(0, int(hours)) * 60


def parent_cap_minutes(db: Session, parent_site_id: str) -> int:
    """Гадна зогсоолын талаас нь хязгаар олох — доторх зогсоол(ууд)-ын тохиргоо.

    Гадна session дээр зогсолт үлдсэн (paused_since) байхад ямар хязгаар
    хэрэглэхээ мэдэх хэрэгтэй, гэвч аль доторх зогсоолд орсныг session өөрөө
    хадгалдаггүй. Хэд хэдэн доторх зогсоолтой бол ХАМГИЙН БАГА хязгаарыг авна
    (аюулгүй тал руу).
    """
    kids = db.query(ParkingSite).filter(ParkingSite.parent_site_id == parent_site_id).all()
    if not kids:
        return max(0, int(settings.transit_max_hours)) * 60
    caps = [cap_minutes(k) for k in kids]
    return 0 if 0 in caps else min(caps)


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
        cap = parent_cap_minutes(db, s.site_id)
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
    parent.paused_since = at
    log.info("[nested] %s: «%s»-д орлоо — гадна «%s»-ын тоолуур зогслоо",
             plate, site.name, getattr(parent.site, "name", parent.site_id))
    return True


def on_inner_exit(db: Session, site: ParkingSite, plate: str, at: datetime) -> int:
    """Доторх зогсоолоос ГАРЛАА — гадна session-ий тоолуурыг үргэлжлүүлнэ."""
    parent = _parent_session(db, site, plate)
    if parent is None or not parent.paused_since:
        return 0
    mins = settle_pause(parent, at, cap_minutes(site))
    log.info("[nested] %s: «%s»-аас гарлаа — гадна тоолуур %d минут зогссон "
             "(нийт %d мин хасагдана)", plate, site.name, mins, parent.paused_minutes)
    return mins


def close_open_pause(db: Session, s: ParkingSession, at: datetime) -> int:
    """Гадна session хаагдахад үлдсэн зогсолтыг хаана.

    Машин гадна гарцад уншигдсан гэдэг нь доторх зогсоолоос гарсан гэсэн үг —
    доторх ГАРАХ уншилт алдагдсан ч гэсэн. Хязгаараар таслагдана.
    """
    if not s.paused_since:
        return 0
    mins = settle_pause(s, at, parent_cap_minutes(db, s.site_id))
    log.info("[nested] %s: гадна гарцад уншигдлаа — доторх гарах уншилтгүй үлдсэн "
             "зогсолтыг %d минутаар хаав", s.plate_number, mins)
    return mins


def inside_nested_count(db: Session, site_id: str) -> int:
    """Тухайн ГАДНА зогсоолын машинуудаас хэд нь ОДОО доторх зогсоолд байгаа вэ."""
    return (db.query(ParkingSession)
            .filter(ParkingSession.site_id == site_id,
                    ParkingSession.status.in_(_ACTIVE),
                    ParkingSession.paused_since.isnot(None)).count())
