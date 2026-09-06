"""Цагийн нэгдсэн дүрэм — «системийн цаг» ба «хананы цаг»-ийг андуурахгүй.

Дүрэм (2026-08-31-ний цагийн аудитаас):
  • DB болон дотоод бүх тооцоо — UTC naive (`utc_now`). Серверүүд NTP-тэй,
    TZ=Etc/UTC тул `datetime.now()` санамсаргүй зөв ажиллаж байсан ч серверийн
    цагийн бүс өөрчлөгдмөгц чимээгүй эвдэрнэ — тиймээс `datetime.now()`-г
    аппын кодод ХЭРЭГЛЭХГҮЙ, эндхийн хоёр функцийн аль нэгийг дуудна.
  • Хүнд харуулах / гадаад локал систем (msgbill, PosAPI) рүү явах огноо —
    Улаанбаатарын хананы цаг (`local_now`, `settings.tz_offset_hours`).
    Жишээ алдаа: msgbill-ийн баримтын огнооны fallback `datetime.now()` байсан
    нь UTC сервер дээр УБ-аас 8 цаг хоцорсон огноо бичих латент алдаа байв.
"""
import calendar
from datetime import datetime, timedelta

from .config import settings


def utc_now() -> datetime:
    """Системийн цаг — DB/тооцооны бүх цэгт (naive UTC, DB-ийн конвенц)."""
    return datetime.utcnow()


def local_now() -> datetime:
    """Улаанбаатарын хананы цаг — дэлгэц/баримт/гадаад локал системд."""
    return datetime.utcnow() + timedelta(hours=settings.tz_offset_hours)


def local_str(fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    return local_now().strftime(fmt)


def utc_epoch(dt: datetime) -> float:
    """Naive UTC datetime → Unix epoch (сек). tz-тэй бол шууд.

    `dt.timestamp()`-ийг naive UTC дээр ХЭЗЭЭ Ч БҮҮ дууд: Python naive datetime-ийг
    ЛОКАЛ цаг гэж үзэж OS-ийн бүсээр хөрвүүлдэг тул сервер Etc/UTC биш болмогц
    (2026-09-06: прод 202.21.117.179 Asia/Ulaanbaatar болсон) epoch яг бүсийн
    зөрүүгээр (8ц) хазайж — камерын цагийн зөрүү «7ц 59м түрүүлж» гэж 38 камерт
    хуурамчаар дохиолж, camsync/log_tail-ийн хайлтын муж 8ц ухарч байв."""
    if dt.tzinfo is not None:
        return dt.timestamp()
    return calendar.timegm(dt.timetuple()) + dt.microsecond / 1e6
