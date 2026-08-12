"""Тарифын урьдчилан харах + EasyParking-ийн «30 мин тутам 500 / цаг 3000» загвар.

    cd backend && venv/bin/python tests/test_tariff_preview.py

Загварын шаардлага (2026-08-12):
  • эхний 120 минут хүртэл — 30 минут тутамд 500₮ (кумулятив: 30→500 … 120→2000)
  • 120 минутаас дээш — эхэлсэн цаг тутамд 3000₮
  • эхний 30 минут үнэгүй (free_minutes)
"""
import os
import sys
from types import SimpleNamespace as N

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.billing import tier_price  # noqa: E402

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {extra}")


# EasyParking-ийн production тохиргоо (Үндсэн загвар)
TPL = N(free_minutes=30, extra_hour_price=3000, daily_cap=25000,
        tiers=[N(upto_minutes=60, price=1000), N(upto_minutes=90, price=1500),
               N(upto_minutes=120, price=2000), N(upto_minutes=180, price=5000),
               N(upto_minutes=240, price=8000)])


def fee(minutes, tpl=TPL):
    if tpl.free_minutes and minutes <= tpl.free_minutes:
        return 0
    v = float(tier_price(tpl, minutes))
    return min(v, tpl.daily_cap) if tpl.daily_cap else v


print("1. Эхний 120 мин — 30 минут тутам 500₮ алхам")
check("30 мин → үнэгүй (free_minutes)", fee(30) == 0, fee(30))
check("31 мин → 1000₮ (2 дахь 30-минутын блок)", fee(31) == 1000, fee(31))
check("60 мин → 1000₮", fee(60) == 1000, fee(60))
check("61 мин → 1500₮", fee(61) == 1500, fee(61))
check("90 мин → 1500₮", fee(90) == 1500, fee(90))
check("120 мин → 2000₮", fee(120) == 2000, fee(120))

print("\n2. 120 минутаас дээш — эхэлсэн цаг тутамд 3000₮")
check("121 мин → 5000₮ (2000 + 1 цаг)", fee(121) == 5000, fee(121))
check("180 мин → 5000₮", fee(180) == 5000, fee(180))
check("181 мин → 8000₮ (2000 + 2 цаг)", fee(181) == 8000, fee(181))
check("240 мин → 8000₮", fee(240) == 8000, fee(240))
check("300 мин → 11000₮ (шатлалаас хэтэрсэн 1 цаг)", fee(300) == 11000, fee(300))
check("360 мин → 14000₮", fee(360) == 14000, fee(360))
check("480 мин → 20000₮", fee(480) == 20000, fee(480))

print("\n3. Хоногийн дээд хязгаар")
check("600 мин → 25000₮ (cap)", fee(600) == 25000, fee(600))
check("1440 мин → 25000₮ (cap)", fee(1440) == 25000, fee(1440))

print("\n4. extra_hour_price буруу (2000) үлдвэл 240+ дээр зөрнө")
BAD = N(free_minutes=30, extra_hour_price=2000, daily_cap=25000, tiers=TPL.tiers)
check("300 мин → 10000₮ (3000 биш 2000-аар бодогдоно)", fee(300, BAD) == 10000, fee(300, BAD))
check("240 хүртэл ижил хэвээр", fee(180, BAD) == 5000 and fee(240, BAD) == 8000)

print("\n5. 30-минутын шатлал НЭМСЭН хувилбар (үнэгүй хугацаагүй)")
NOFREE = N(free_minutes=0, extra_hour_price=3000, daily_cap=25000,
           tiers=[N(upto_minutes=30, price=500)] + list(TPL.tiers))
check("15 мин → 500₮", fee(15, NOFREE) == 500, fee(15, NOFREE))
check("30 мин → 500₮", fee(30, NOFREE) == 500, fee(30, NOFREE))
check("31 мин → 1000₮", fee(31, NOFREE) == 1000, fee(31, NOFREE))
check("120 мин → 2000₮ (өөрчлөгдөөгүй)", fee(120, NOFREE) == 2000, fee(120, NOFREE))

print(f"\n{'=' * 44}\n{PASS} PASS, {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
