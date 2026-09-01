"""«Шөнө үнэгүй» (NIGHT) төрөл — шөнө дамнасан цонхны тооцоо.

    cd backend && venv/bin/python tests/test_night_window.py

Шалгах зүйл:
  - free_window_minutes: шөнө дамнасан цонх (21:00→08:00) зөв тоологдоно
  - өдрийн цонх (08:00→18:00) хуучин зан төлөвөөрөө (регресс)
  - буруу утга / ижил цаг → 0 (цонх үйлчлэхгүй, тооцоо унахгүй)
  - calculate_fee-тэй хослол: шөнө бүрэн багтсан зогсолт 0₮, хэтэрсэн нь
    зөвхөн цонхны гаднах хугацаагаар тарифладаг
  - night_window() буруу тохиргоонд default (21:00–08:00) буцаана
"""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.billing import calculate_fee, free_window_minutes
from app.services.app_settings import night_window

PASS = FAIL = 0


def check(name, cond):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗ <<< FAIL'} {name}")


# УБ (UTC+8): локал цагийг UTC болгож өгнө
def ub(y, mo, d, h, mi=0):
    from datetime import timedelta
    return datetime(y, mo, d, h, mi) - timedelta(hours=8)


print("Шөнө дамнасан цонх 21:00→08:00:")
# 22:00 → 07:00 (9ц) — бүхэлдээ цонхонд
m = free_window_minutes(ub(2026, 9, 1, 22), ub(2026, 9, 2, 7), "21:00", "08:00")
check("22:00–07:00 → 540 мин (бүгд цонхонд)", m == 540)
# 20:00 → 09:00 (13ц) — цонхтой давхцах нь 21:00→08:00 = 660
m = free_window_minutes(ub(2026, 9, 1, 20), ub(2026, 9, 2, 9), "21:00", "08:00")
check("20:00–09:00 → 660 мин (цонх бүтэн)", m == 660)
# Өдрийн зогсолт 10:00–15:00 — цонхтой огт давхцахгүй
m = free_window_minutes(ub(2026, 9, 1, 10), ub(2026, 9, 1, 15), "21:00", "08:00")
check("10:00–15:00 → 0 мин", m == 0)
# Өглөөний хэсэгт л таарна: 06:00–10:00 → 06:00–08:00 = 120
m = free_window_minutes(ub(2026, 9, 1, 6), ub(2026, 9, 1, 10), "21:00", "08:00")
check("06:00–10:00 → 120 мин (өглөөний тал)", m == 120)
# 2 хоног дамнасан: 20:00 → нөгөөдрийн 09:00 (37ц) → 2 бүтэн шөнө = 1320
m = free_window_minutes(ub(2026, 9, 1, 20), ub(2026, 9, 3, 9), "21:00", "08:00")
check("20:00–(+2өдөр)09:00 → 1320 мин (2 шөнө)", m == 1320)

print("Өдрийн цонх (регресс) 08:00→18:00:")
m = free_window_minutes(ub(2026, 9, 1, 7), ub(2026, 9, 1, 19), "08:00", "18:00")
check("07:00–19:00 → 600 мин", m == 600)
m = free_window_minutes(ub(2026, 9, 1, 9), ub(2026, 9, 1, 12), "08:00", "18:00")
check("09:00–12:00 → 180 мин", m == 180)

print("Буруу утга:")
check("ижил цаг → 0", free_window_minutes(ub(2026, 9, 1, 6), ub(2026, 9, 1, 10), "10:00", "10:00") == 0)
check("хог текст → 0", free_window_minutes(ub(2026, 9, 1, 6), ub(2026, 9, 1, 10), "abc", "08:00") == 0)
check("хоосон → 0", free_window_minutes(ub(2026, 9, 1, 6), ub(2026, 9, 1, 10), "", None) == 0)

print("Тарифтай хослол (60→1000, 120→2000, 180→5000, чөлөөт 0):")


class Tier:
    def __init__(self, u, p):
        self.upto_minutes, self.price = u, p


class T:
    free_minutes = 0
    extra_hour_price = 2000
    daily_cap = None
    tiers = [Tier(60, 1000), Tier(120, 2000), Tier(180, 5000)]


# Шөнө 22:00–07:00 бүрэн цонхонд → paused=540, billable=0 → үнэгүй
paused = free_window_minutes(ub(2026, 9, 1, 22), ub(2026, 9, 2, 7), "21:00", "08:00")
fee = calculate_fee(T(), ub(2026, 9, 1, 22), ub(2026, 9, 2, 7), paused_minutes=paused)
check("шөнөжин зогссон NIGHT машин 0₮", fee["total_fee"] == 0 and fee["is_free"])
# 19:00–07:00 (12ц): цонхны гадна 19:00–21:00 = 120 мин → 2000₮
paused = free_window_minutes(ub(2026, 9, 1, 19), ub(2026, 9, 2, 7), "21:00", "08:00")
fee = calculate_fee(T(), ub(2026, 9, 1, 19), ub(2026, 9, 2, 7), paused_minutes=paused)
check("19:00 ирсэн бол гаднах 120 мин = 2000₮", fee["total_fee"] == 2000)

print("night_window default:")
f, u = night_window(None)   # DB-гүй/буруу тохиргоо → default
check("default 21:00–08:00", (f, u) == ("21:00", "08:00"))

print(f"\n{PASS} PASS, {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
