"""Гэрээт машины ҮНЭГҮЙ ЦАГИЙН ЦОНХ (№13): цонхтой давхцсан минут үнэгүй,
гаднах хугацаа энгийнээр бодогдоно.

    cd backend && venv/bin/python tests/test_free_window.py
"""
import os
import sys
import uuid
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.billing import free_window_minutes  # noqa: E402

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {extra}")


def ub(y, mo, d, h, m=0):
    """УБ-ын цагийг UTC болгож буцаана (сервер UTC хадгалдаг)."""
    return datetime(y, mo, d, h, m) - timedelta(hours=8)


print("1. free_window_minutes — цэвэр тооцоолол")
# 07:00-19:00 зогссон, цонх 08:00-18:00 → 600 мин үнэгүй
mins = free_window_minutes(ub(2026, 8, 11, 7), ub(2026, 8, 11, 19), "08:00", "18:00")
check("07:00-19:00 · цонх 08-18 → 600 мин", mins == 600, mins)
# Бүхэлдээ цонхон дотор
mins = free_window_minutes(ub(2026, 8, 11, 9), ub(2026, 8, 11, 12), "08:00", "18:00")
check("09:00-12:00 → бүгд үнэгүй (180)", mins == 180, mins)
# Бүхэлдээ цонхны гадна (шөнө)
mins = free_window_minutes(ub(2026, 8, 11, 19), ub(2026, 8, 11, 23), "08:00", "18:00")
check("19:00-23:00 → 0", mins == 0, mins)
# Хоёр өдөр дамнасан: 8/11 17:00 → 8/12 09:00; цонх 08-18
# 8/11-нд 17:00-18:00 (60) + 8/12-нд 08:00-09:00 (60) = 120
mins = free_window_minutes(ub(2026, 8, 11, 17), ub(2026, 8, 12, 9), "08:00", "18:00")
check("өдөр дамнасан → 120", mins == 120, mins)
# Буруу цонх → 0 (унахгүй)
check("буруу формат → 0", free_window_minutes(ub(2026, 8, 11, 7), ub(2026, 8, 11, 19), "xx", "18:00") == 0)
check("урвуу цонх → 0", free_window_minutes(ub(2026, 8, 11, 7), ub(2026, 8, 11, 19), "18:00", "08:00") == 0)

print("\n2. session_fee_info — цонхтой гэрээт машины бодит тооцоо (DB)")
from app.database import SessionLocal  # noqa: E402
from app.models import ParkingSession, ParkingSite, RegisteredDriver, TariffTemplate, TariffTier  # noqa: E402
from app.session_logic import session_fee_info  # noqa: E402

db = SessionLocal()
made = []
try:
    tpl = TariffTemplate(id=str(uuid.uuid4()), name="fw-test", free_minutes=0,
                         grace_minutes=15, prepaid_price=1000, extra_hour_price=1000,
                         daily_cap=0, is_active=True)
    db.add(tpl); made.append(tpl)
    db.flush()
    tier = TariffTier(id=str(uuid.uuid4()), template_id=tpl.id, upto_minutes=60, price=1000)
    db.add(tier); made.append(tier)
    site = ParkingSite(id=str(uuid.uuid4()), name="FW тест", site_code=f"FW{uuid.uuid4().hex[:5].upper()}",
                       zone_code="A", tariff_template_id=tpl.id, is_active=True)
    db.add(site); made.append(site)
    db.flush()

    now = datetime.utcnow()
    drv = RegisteredDriver(id=str(uuid.uuid4()), plate_number="5959ФЦЦ", site_id=site.id,
                           contract_type="CONTRACT", free_from="00:00", free_until="23:59",
                           valid_from=now - timedelta(days=1), valid_to=now + timedelta(days=30),
                           is_active=True)
    db.add(drv); made.append(drv)
    s = ParkingSession(id=str(uuid.uuid4()), site_id=site.id, plate_number="5959ФЦЦ",
                       entry_time=now - timedelta(minutes=120), status="OPEN")
    db.add(s); made.append(s)
    db.commit()

    # Бараг бүх өдрийг хамарсан цонх → одоо хүртэлх бүх минут цонхонд → үнэгүй
    fee = session_fee_info(db, s)
    check("өргөн цонх → үнэгүй", fee["is_free"], fee)

    # Цонхыг аль хэдийн ӨНГӨРСӨН цаг руу шилжүүлье → 2 цаг бүгд төлбөртэй болно
    two_ago_local = (now + timedelta(hours=8)) - timedelta(minutes=125)
    drv.free_from = "00:00"
    drv.free_until = two_ago_local.strftime("%H:%M")
    db.commit()
    # Цонх энэ session-ий хугацаанаас гадна үлдэхээр бол төлбөртэй байх ёстой
    fee2 = session_fee_info(db, s)
    check("цонхны гаднах хугацаа төлбөртэй", not fee2["is_free"] and fee2["total_fee"] > 0, fee2)
    check("тайланд бодит хугацаа хэвээр", fee2["duration_minutes"] >= 119, fee2)

    # Цонхгүй болговол хуучин зан төлөв: бүх цагт үнэгүй
    drv.free_from = None
    drv.free_until = None
    db.commit()
    fee3 = session_fee_info(db, s)
    check("цонхгүй гэрээт → бүх цагт үнэгүй", fee3["is_free"], fee3)
finally:
    db.rollback()
    for obj in reversed(made):
        try:
            db.delete(obj)
            db.commit()
        except Exception:
            db.rollback()
    db.close()

print(f"\n{'='*40}\n{PASS} PASS, {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
