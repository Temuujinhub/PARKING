"""log_tail: камерын цаг гулссан үед ХУУЧИН уншилтаар хаалт нээхгүй байх.

    cd backend && venv/bin/python tests/test_log_tail_clock_skew.py

2026-08-22 Рашбулаг: дотоод гарах камерын цаг УБ локалаар (сервер UTC) явж
байсан тул логийн бичлэг СЕРВЕРИЙН цагаас ИРЭЭДҮЙД харагдаж, «хэт хуучин»
шалгуурыг («бичлэгийн цаг < fresh_cut») хэзээ ч зөрчихгүй байв. Үр дүнд нь
19:5x-д гарсан машины уншилт шөнийн 01:5x-д «шинэхэн» гэж орж ирээд эзэнгүй
хаалт нээж байсан (5611УАЕ, 9485УБР, 6201УБЦ — camera_time нь 6 цагийн өмнөх).
"""
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings  # noqa: E402

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗ <<< FAIL'} {name}{f' ({extra})' if extra and not cond else ''}")


def verdict(record_time: datetime, now: datetime) -> str:
    """log_tail-ийн шийдвэрийг давтана (services/log_tail.py-ийн шалгуур)."""
    age = (now - record_time).total_seconds()
    if age > settings.log_tail_fresh_sec:
        return "old"          # camera_sync хариуцна
    if age < -settings.log_tail_clock_skew_max_sec:
        return "skewed"       # камерын цаг гулссан — хаалт НЭЭХГҮЙ
    return "open"             # шинэхэн — хаалт нээнэ


now = datetime(2026, 8, 21, 17, 55, 17)   # серверийн UTC

print("Тохиргоо байгаа эсэх:")
check("log_tail_clock_skew_max_sec бий",
      hasattr(settings, "log_tail_clock_skew_max_sec"))
check("анхдагч нь эерэг", getattr(settings, "log_tail_clock_skew_max_sec", 0) > 0)

print("\nХЭВИЙН камер (цаг зөв):")
check("саяхны уншилт → хаалт нээнэ",
      verdict(now - timedelta(seconds=5), now) == "open")
check("2 минутын өмнөх → нээнэ (fresh_sec=240)",
      verdict(now - timedelta(seconds=120), now) == "open")
check("10 минутын өмнөх → хуучин, нээхгүй",
      verdict(now - timedelta(minutes=10), now) == "old")
check("6 цагийн өмнөх → хуучин, нээхгүй",
      verdict(now - timedelta(hours=6), now) == "old")

print("\nЖИЖИГ drift (NTP-гүй камер, 1 минут түрүүлсэн):")
check("1 мин ирээдүйд → шинэхэн гэж үзнэ (тэвчээрт багтана)",
      verdict(now + timedelta(minutes=1), now) == "open")

print("\nГУЛССАН камер — 2026-08-22 Рашбулагийн бодит кейс:")
# camera_time=19:55:09, сервер UTC=17:55:17 → 2 цаг ИРЭЭДҮЙД харагдана
rb = datetime(2026, 8, 21, 19, 55, 9)
check("бодит кейс: 2ц ирээдүйн огноо → SKEWED (хаалт НЭЭХГҮЙ)",
      verdict(rb, now) == "skewed", verdict(rb, now))
check("8 цаг түрүүлсэн (УБ локал цаг) → SKEWED",
      verdict(now + timedelta(hours=8), now) == "skewed")

print("\nЗАСВАРААС ӨМНӨХ зан төлөв (регресс хамгаалалт):")
old_open = rb >= (now - timedelta(seconds=settings.log_tail_fresh_sec))
check("хуучин логикоор энэ бичлэг «шинэхэн» гэж тооцогдож байсан", old_open)
check("шинэ логикоор нээгдэхээ болив", verdict(rb, now) != "open")

print(f"\n{'='*46}\n  PASS {PASS} / FAIL {FAIL}\n{'='*46}")
sys.exit(1 if FAIL else 0)
