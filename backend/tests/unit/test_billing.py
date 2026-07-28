"""app/billing.py — мөнгөний тооцооллын тест.

Яагаад чухал вэ: энэ файл жолооч бүрээс авах дүнг тодорхойлдог мөртлөө өмнө нь
НЭГ Ч тестгүй байсан. Шатлалын хил, хоногийн таазны хуваалт, хөнгөлөлт, НӨАТ-ын
бөөрөнхийлөл дээр алдвал өдөр бүр бүх зогсоол дээр буруу мөнгө нэхэгдэнэ.
"""
from datetime import datetime, timedelta

import pytest

from app.billing import calculate_fee, tier_price
from app.models import Discount, TariffTemplate, TariffTier

ENTRY = datetime(2026, 7, 1, 8, 0, 0)


def tmpl(tiers=((60, 1000), (120, 2000), (180, 5000)), free_minutes=15,
         extra_hour_price=1000, daily_cap=None):
    t = TariffTemplate(name="Тест", free_minutes=free_minutes, grace_minutes=15,
                       prepaid_price=0, extra_hour_price=extra_hour_price, daily_cap=daily_cap)
    t.tiers = [TariffTier(upto_minutes=u, price=p) for u, p in tiers]
    return t


def fee(minutes, template=None, discount=None, is_registered=False):
    template = tmpl() if template is None else template
    return calculate_fee(template, ENTRY, ENTRY + timedelta(minutes=minutes),
                         discount=discount, is_registered=is_registered)


# ── Үнэгүй тохиолдлууд ────────────────────────────────────────────────────────
def test_registered_driver_is_free(vat_inclusive):
    r = fee(600, is_registered=True)
    assert r["is_free"] and r["total_fee"] == 0
    assert r["reason"] == "Бүртгэлтэй жолооч"


def test_no_template_is_free(vat_inclusive):
    r = calculate_fee(None, ENTRY, ENTRY + timedelta(hours=5))
    assert r["is_free"] and r["total_fee"] == 0


def test_free_minutes_boundary(vat_inclusive):
    assert fee(15)["is_free"] is True       # яг хил дээр — үнэгүй
    assert fee(16)["total_fee"] == 1000     # хилээс хэтэрмэгц төлбөртэй


def test_exit_before_entry_is_zero(vat_inclusive):
    r = calculate_fee(tmpl(), ENTRY, ENTRY - timedelta(hours=1))
    assert r["duration_minutes"] == 0 and r["is_free"]


# ── Шатлалын хил ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize("minutes,expected", [
    (60, 1000), (61, 2000), (120, 2000), (121, 5000), (180, 5000),
])
def test_tier_boundaries(vat_inclusive, minutes, expected):
    assert fee(minutes)["total_fee"] == expected


@pytest.mark.parametrize("minutes,expected", [
    (181, 6000),    # сүүлийн шатлал + эхэлсэн 1 цаг
    (240, 6000),    # 180 + яг 60 мин
    (241, 7000),    # 180 + 61 мин → 2 цаг
])
def test_beyond_last_tier(vat_inclusive, minutes, expected):
    assert fee(minutes)["total_fee"] == expected


def test_no_tiers_uses_hourly(vat_inclusive):
    t = tmpl(tiers=(), extra_hour_price=500, free_minutes=0)
    assert fee(90, template=t)["total_fee"] == 1000   # эхэлсэн 2 цаг


def test_tier_price_zero_minutes():
    assert tier_price(tmpl(), 0) == 0


# ── Хоногийн тааз ────────────────────────────────────────────────────────────
def test_daily_cap_single_day(vat_inclusive):
    t = tmpl(daily_cap=5000)
    assert fee(600, template=t)["total_fee"] == 5000  # 10 цаг ч таазаар хязгаарлана


def test_daily_cap_multi_day(vat_inclusive):
    t = tmpl(daily_cap=5000)
    # 2 бүтэн хоног (2×5000) + үлдсэн 60 мин (1000, тааз хүрэхгүй)
    assert fee(2 * 24 * 60 + 60, template=t)["total_fee"] == 11000


def test_daily_cap_multi_day_remainder_capped(vat_inclusive):
    t = tmpl(daily_cap=5000)
    # 1 хоног + 600 мин: үлдэгдэл нь таазаас хэтрэхгүй
    assert fee(24 * 60 + 600, template=t)["total_fee"] == 10000


def test_multi_day_without_cap(vat_inclusive):
    # 1500 мин = 180-аас хэтэрсэн 1320 мин → 22 цаг × 1000 + 5000
    assert fee(1500)["total_fee"] == 27000


# ── Хөнгөлөлт ────────────────────────────────────────────────────────────────
def test_percent_discount(vat_inclusive):
    d = Discount(name="50%", discount_type="PERCENT", value=50)
    r = fee(120, discount=d)
    assert r["total_fee"] == 1000 and r["discount_amount"] == 1000


def test_fixed_discount_clamped_to_fee(vat_inclusive):
    d = Discount(name="5000₮", discount_type="FIXED", value=5000)
    r = fee(120, discount=d)
    assert r["total_fee"] == 0 and r["discount_amount"] == 2000  # төлбөрөөс их хасахгүй
    assert r["is_free"] is True


def test_free_minutes_discount(vat_inclusive):
    d = Discount(name="30 мин", discount_type="FREE_MINUTES", value=30)
    r = fee(90, discount=d)
    assert r["chargeable_minutes"] == 60 and r["total_fee"] == 1000


def test_free_minutes_discount_covers_whole_stay(vat_inclusive):
    d = Discount(name="120 мин", discount_type="FREE_MINUTES", value=120)
    r = fee(90, discount=d)
    assert r["is_free"] and r["total_fee"] == 0


# ── НӨАТ ─────────────────────────────────────────────────────────────────────
def test_vat_inclusive_split(vat_inclusive):
    r = fee(60)   # 1000₮ дотор НӨАТ багтсан
    assert r["total_fee"] == 1000
    assert r["vat_amount"] == 91 and r["base_fee"] == 909


def test_vat_exclusive_adds_on_top(vat_inclusive):
    from app.config import settings
    settings.vat_inclusive = False
    r = fee(60)
    assert r["base_fee"] == 1000 and r["vat_amount"] == 100 and r["total_fee"] == 1100
