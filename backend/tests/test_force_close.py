"""close_session_forced — админ/авто хасалтын өрийн дүнгийн дүрэм (DB шаардлагагүй, fake db).

    cd backend && venv/bin/python tests/test_force_close.py

Дүрэм:
  - AWAITING_PAYMENT + exit_time: өр = ГАРАХ ОРОЛДЛОГЫН үеийн дүн, exit_time хэвээр
  - OPEN (гарах оролдлогогүй): өр = одоог хүртэлх дүн (daily_cap хамгаална)
  - paid_at-тай (төлсөн) session → CLOSED, өр 0
  - create_comp=False → өр бүртгэхгүй (0), гэхдээ session хаагдана
"""
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.session_logic import close_session_forced

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
    daily_cap = 10000
    tiers = [Tier(60, 1000), Tier(120, 2000)]


class FakeSite:
    tariff_template = FakeTemplate()


class FakeQ:
    def filter(self, *a, **k): return self
    def count(self): return 0
    def first(self): return None
    def all(self): return []


class FakeDB:
    def __init__(self): self.added = []
    def query(self, *a, **k): return FakeQ()
    def add(self, obj): self.added.append(obj)
    def flush(self): pass


class FakeSession:
    def __init__(self, status, entry_hours_ago, exit_hours_ago=None, paid_at=None):
        now = datetime.utcnow()
        self.id = "SESS1"
        self.site_id = "SITE1"
        self.plate_number = "1234УБА"
        self.status = status
        self.entry_time = now - timedelta(hours=entry_hours_ago)
        self.exit_time = now - timedelta(hours=exit_hours_ago) if exit_hours_ago else None
        self.paid_at = paid_at
        self.site = FakeSite()
        self.discount = None
        self.is_registered = False
        self.duration_minutes = None
        self.base_fee = self.vat_amount = self.total_fee = None
        self.note = None


print("AWAITING + гарах оролдлоготой — тэр үеийн дүнгээр:")
db = FakeDB()
s = FakeSession("AWAITING_PAYMENT", entry_hours_ago=80, exit_hours_ago=78)
old_exit = s.exit_time
due = close_session_forced(db, s, "auto_close", "system")
check("өр = 2 цагийн дүн 2000₮ (80ц-ийн биш)", due == 2000)
check("exit_time хэвээр (гарах оролдлогын цаг)", s.exit_time == old_exit)
check("status = MANUAL_CLOSED", s.status == "MANUAL_CLOSED")
check("нөхөн төлбөр үүссэн (comp.amount=due)",
      any(getattr(a, "amount", None) == 2000 for a in db.added))

print("OPEN, гарах оролдлогогүй — одоог хүртэл, daily_cap хамгаална:")
db = FakeDB()
s = FakeSession("OPEN", entry_hours_ago=80)
due = close_session_forced(db, s, "auto_close", "system")
# 80ц = 3 хоног 8ц: 3×10000 (cap) + 8ц (2000+6×1000=8000) = 38000
check("өр 38000₮ (cap-тай олон хоног)", due == 38000)
check("exit_time одоо болсон", s.exit_time is not None and
      abs((datetime.utcnow() - s.exit_time).total_seconds()) < 5)

print("Төлсөн (paid_at) session:")
db = FakeDB()
s = FakeSession("AWAITING_PAYMENT", entry_hours_ago=3, exit_hours_ago=1, paid_at=datetime.utcnow())
close_session_forced(db, s, "admin_remove", "admin")
check("status = CLOSED (төлсөн)", s.status == "CLOSED")

print("create_comp=False:")
db = FakeDB()
s = FakeSession("OPEN", entry_hours_ago=5)
due = close_session_forced(db, s, "admin_remove", "admin", create_comp=False)
check("өр бүртгэгдээгүй (0 буцаана)", due == 0.0)
check("session хаагдсан", s.status == "MANUAL_CLOSED")
check("Compensation нэмэгдээгүй", not any(type(a).__name__ == "Compensation" for a in db.added))

print(f"\n{PASS} PASS, {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
