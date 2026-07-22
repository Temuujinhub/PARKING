"""session_fee_info — AWAITING_PAYMENT үед төлбөр царцахгүй үргэлжлэн бодогдохыг шалгана.

    cd backend && venv/bin/python tests/test_awaiting_fee.py

Шалгах зүйл:
  - OPEN: exit_time байхгүй — одоог хүртэл бодно
  - AWAITING_PAYMENT: exit_time (гарах оролдлого) байсан ч одоог хүртэл бодно (царцахгүй)
  - PAID/CLOSED: exit_time дээр царцана (хуучин семантик хэвээр)
  - at= өгвөл яг тэр цагаар бодно (handle_exit/night_close урсгал)
"""
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.session_logic import session_fee_info

PASS = FAIL = 0


def check(name, cond):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗ <<< FAIL'} {name}")


class Tier:
    def __init__(self, upto_minutes, price):
        self.upto_minutes = upto_minutes
        self.price = price


class FakeTemplate:
    free_minutes = 0
    extra_hour_price = 1000
    daily_cap = None
    tiers = [Tier(60, 1000), Tier(120, 2000)]


class FakeSite:
    tariff_template = FakeTemplate()


class FakeSession:
    def __init__(self, status, entry_hours_ago, exit_hours_ago=None):
        now = datetime.utcnow()
        self.status = status
        self.entry_time = now - timedelta(hours=entry_hours_ago)
        self.exit_time = now - timedelta(hours=exit_hours_ago) if exit_hours_ago else None
        self.site = FakeSite()
        self.discount = None
        self.is_registered = False


print("OPEN — exit_time байхгүй, одоог хүртэл:")
s = FakeSession("OPEN", entry_hours_ago=2)
fee = session_fee_info(None, s)
check("2 цаг зогссон OPEN ≈ 120 мин", 118 <= fee["duration_minutes"] <= 121)

print("AWAITING_PAYMENT — гарах оролдлогоос хойш ч бодолт үргэлжилнэ:")
s = FakeSession("AWAITING_PAYMENT", entry_hours_ago=5, exit_hours_ago=3)
fee = session_fee_info(None, s)
check("exit_time (2ц зогсоод) дээр царцаагүй — 5 цаг ≈ 300 мин",
      298 <= fee["duration_minutes"] <= 301)
check("төлбөр 5 цагаар өссөн (2000+3×1000=5000)", fee["total_fee"] == 5000)

print("PAID/CLOSED — exit_time дээр хэвээр царцана:")
for st in ("PAID", "CLOSED", "MANUAL_CLOSED"):
    s = FakeSession(st, entry_hours_ago=5, exit_hours_ago=3)
    fee = session_fee_info(None, s)
    check(f"{st}: 2 цаг ≈ 120 мин (exit_time-аар)", 118 <= fee["duration_minutes"] <= 121)

print("at= явно өгвөл түүгээр бодно:")
s = FakeSession("AWAITING_PAYMENT", entry_hours_ago=5, exit_hours_ago=3)
fee = session_fee_info(None, s, at=s.exit_time)
check("at=exit_time → 120 мин (нөхөн төлбөрийн хуучин дүн)",
      118 <= fee["duration_minutes"] <= 121)

print(f"\n{PASS} PASS, {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
