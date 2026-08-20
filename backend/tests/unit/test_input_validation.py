"""Гараар оруулах утгуудын сервер талын хамгаалалт.

ЯАГААД: frontend-ийн шалгалтыг API-г шууд дуудаж (эсвэл хуучин таб дээрээс)
тойрч болдог. Мөнгө/төлбөрт нөлөөлдөг талбарууд заавал энд ч зогсоох ёстой:

  • manual-entry-ийн `entry_time` — ирээдүйн цаг (сөрөг хугацаа) эсвэл олон
    жилийн өмнөх цаг (тэнгэр баганадсан төлбөр) хоёулаа кассын дүнг эвднэ.
  • Хөнгөлөлтийн `value` — СӨРӨГ хувь нь `fee - (-x)` болж төлбөрийг
    НЭМЭГДҮҮЛНЭ, 100-аас дээш хувь нь тайланд бодит төлбөрөөс их
    «хөнгөлөлт» бичнэ.
  • Тарифын шатлал буурах дараалалтай бол урт зогссон машин бага төлдөг.
"""
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app import schemas
from app.routers.sessions_router import (MANUAL_ENTRY_MAX_BACKDATE_DAYS,
                                         _parse_manual_entry_time)


# ── manual-entry-ийн орсон цаг ──
def test_entry_time_defaults_to_now():
    assert (datetime.utcnow() - _parse_manual_entry_time(None)).total_seconds() < 5


def test_entry_time_in_the_past_is_accepted():
    t = datetime.utcnow() - timedelta(hours=3)
    assert _parse_manual_entry_time(t.isoformat()) == t


def test_future_entry_time_rejected():
    future = (datetime.utcnow() + timedelta(hours=2)).isoformat()
    with pytest.raises(HTTPException) as e:
        _parse_manual_entry_time(future)
    assert e.value.status_code == 400


def test_small_clock_skew_tolerated():
    """Кассын станцын цаг 1-2 минут түрүүлж болно — үүнийг алдаа гэж үзэхгүй."""
    skewed = (datetime.utcnow() + timedelta(minutes=2)).isoformat()
    assert _parse_manual_entry_time(skewed)


def test_too_old_entry_time_rejected():
    old = (datetime.utcnow() - timedelta(days=MANUAL_ENTRY_MAX_BACKDATE_DAYS + 1)).isoformat()
    with pytest.raises(HTTPException):
        _parse_manual_entry_time(old)


def test_garbage_entry_time_gives_400_not_500():
    with pytest.raises(HTTPException) as e:
        _parse_manual_entry_time("өчигдөр")
    assert e.value.status_code == 400


# ── Хөнгөлөлт ──
@pytest.mark.parametrize("dtype,value", [("PERCENT", 500), ("PERCENT", -5),
                                         ("FIXED", -1), ("FREE_MINUTES", 99999)])
def test_invalid_discount_rejected(dtype, value):
    with pytest.raises(ValidationError):
        schemas.DiscountCreate(name="тест", discount_type=dtype, value=value)


def test_valid_discount_accepted():
    assert schemas.DiscountCreate(name="VIP", discount_type="PERCENT", value=100).value == 100


# ── Тарифын шатлал ──
def _tiers(*pairs):
    return [{"upto_minutes": m, "price": p} for m, p in pairs]


def test_descending_tiers_rejected_on_create_and_update():
    bad = _tiers((120, 2000), (60, 1000))
    with pytest.raises(ValidationError):
        schemas.TariffTemplateCreate(name="t", tiers=bad)
    with pytest.raises(ValidationError):
        schemas.TariffTemplateUpdate(tiers=bad)


def test_duplicate_tier_minutes_rejected():
    with pytest.raises(ValidationError):
        schemas.TariffTemplateCreate(name="t", tiers=_tiers((60, 1000), (60, 2000)))


def test_ascending_tiers_accepted():
    assert len(schemas.TariffTemplateCreate(name="t", tiers=_tiers((60, 1000), (120, 2000))).tiers) == 2


# ── Бусад тоон хязгаар ──
@pytest.mark.parametrize("model,kw", [
    (schemas.SiteCreate, dict(name="a", site_code="A", capacity=-1)),
    (schemas.SiteCreate, dict(name="a", site_code="A", auto_close_hours=-3)),
    (schemas.DeviceCreate, dict(site_id="s", device_type="camera", lane_no=0)),
    (schemas.DriverCreate, dict(plate_number="1234УБА", valid_to="2027-01-01", monthly_fee=-1)),
])
def test_negative_numbers_rejected(model, kw):
    with pytest.raises(ValidationError):
        model(**kw)
