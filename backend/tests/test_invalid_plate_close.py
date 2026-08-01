"""Формат буруу (дутуу уншсан) phantom-ыг хурдан цэвэрлэх auto_close дүрэм.

    cd backend && venv/bin/python tests/test_invalid_plate_close.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.session_logic import is_valid_plate

PASS = FAIL = 0


def check(name, cond):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗ <<< FAIL'} {name}")


# Дүрмийн цөм: аль дугаар «формат буруу phantom» гэж тооцогдож хурдан хаагдах вэ
print("is_valid_plate (junk close-д ашиглагдана):")
check("«4132» (үсэггүй, дутуу) — БУРУУ формат → хурдан хаагдана",
      not is_valid_plate("4132"))
check("«132УБИ» (3 цифр) — БУРУУ формат",
      not is_valid_plate("132УБИ"))
check("«4132УАР» (4цифр+3үсэг) — ЗӨВ формат → энэ дүрэмд хамаарахгүй",
      is_valid_plate("4132УАР"))
check("«9999ГГГ» — ЗӨВ формат (junk-аар хаагдахгүй, 72ц entry-only-оор)",
      is_valid_plate("9999ГГГ"))
check("«8066УАО» — ЗӨВ формат", is_valid_plate("8066УАО"))
check("хоосон — буруу", not is_valid_plate(""))
check("«ABCDEFG» латин — буруу", not is_valid_plate("ABCDEFG"))

# Логик: config босго ба сонголт
from app.config import settings  # noqa: E402
check("invalid_plate_close_hours тохиргоо бий (default 2)",
      hasattr(settings, "invalid_plate_close_hours") and settings.invalid_plate_close_hours == 2)

print("=" * 40)
print(f"ҮР ДҮН: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
