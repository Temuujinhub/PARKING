"""Гэрээний нөхцөл (эхний N цаг үнэгүй) + царцсан дүнтэй session (суурь хураамж).

    cd backend && venv/bin/python tests/test_contract_conditions.py

Шалгах зүйл:
  - free_first_minutes семантик: N минутыг хугацаанаас ХАСЧ илүүг тарифаар бодно
    (calculate_fee-ийн paused_minutes механизмаар — session_fee_info ингэж дууддаг)
  - free_first дотор багтсан бол 0₮ (template.free_minutes-тэй нийлж ажиллана)
  - fee_locked session: тарифаас үл хамааран хадгалсан дүн, is_free=False
  - fee_locked биш 0 минутын session үнэгүй хэвээр (хуучин зан эвдрэхгүй)
"""
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.billing import calculate_fee
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
    extra_hour_price = 2000
    daily_cap = None
    tiers = [Tier(60, 1000), Tier(120, 2000), Tier(180, 5000)]


now = datetime.utcnow()

print("Гэрээний нөхцөл — эхний 2 цаг үнэгүй (paused_minutes=120):")
# 3 цаг зогссон, эхний 120 мин үнэгүй → 60 мин тарифаар = 1000₮
fee = calculate_fee(FakeTemplate(), now - timedelta(hours=3), now,
                    is_registered=False, paused_minutes=120)
check("3ц зогсоод 2ц үнэгүй → 60 минутын тариф 1000₮", fee["total_fee"] == 1000)
check("duration нь бодит 180 мин хэвээр", 178 <= fee["duration_minutes"] <= 181)

# 1.5 цаг зогссон, 2ц үнэгүй → бүрэн үнэгүй
fee = calculate_fee(FakeTemplate(), now - timedelta(minutes=90), now,
                    is_registered=False, paused_minutes=120)
check("90 мин зогсоод 2ц үнэгүй → 0₮", fee["total_fee"] == 0 and fee["is_free"])

# 1 цаг үнэгүй нөхцөлтэй, 2ц40м зогссон → 100 мин → 120-ийн шатлал 2000₮
fee = calculate_fee(FakeTemplate(), now - timedelta(minutes=160), now,
                    is_registered=False, paused_minutes=60)
check("160 мин зогсоод 1ц үнэгүй → 100 мин = 2000₮", fee["total_fee"] == 2000)


print("Царцсан дүн (fee_locked) — суурь хураамж:")


class FakeSite:
    tariff_template = FakeTemplate()
    no_charge = False


class LockedSession:
    status = "AWAITING_PAYMENT"
    entry_time = now
    exit_time = None
    site = FakeSite()
    discount = None
    is_registered = False
    fee_locked = True
    duration_minutes = 0
    base_fee = 1818
    vat_amount = 182
    total_fee = 2000
    paused_minutes = 0


fee = session_fee_info(None, LockedSession())
check("царцсан 2000₮ хэвээр (тарифаар дахин бодохгүй)", fee["total_fee"] == 2000.0)
check("is_free=False — хаалт төлбөргүй нээгдэхгүй", not fee["is_free"])
check("НӨАТ хадгалсан утгаараа", fee["vat_amount"] == 182.0)


class UnlockedZero:
    status = "OPEN"
    entry_time = now
    exit_time = None
    site = FakeSite()
    discount = None
    is_registered = False
    fee_locked = False
    total_fee = None
    paused_minutes = 0


fee = session_fee_info(None, UnlockedZero())
check("энгийн 0 минутын session үнэгүй хэвээр (хуучин зан)", fee["is_free"])

print(f"\n{PASS} PASS, {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
