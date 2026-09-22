import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.billing import calculate_fee
from app.config import settings
from app.models import TariffTemplate, TariffTier, HospitalIntegration
from app.routers.hospital_router import VisitInput, IntegrationInput, check_scope, public_config
from app.services.hospital_benefits import apply_hospital_benefit, finish_hospital_usage, local_date
from app.services.hospital_signing import sign, verify
from app.serializers import to_dict

START = datetime(2026, 9, 22, 1)
ID = "00000000-0000-0000-0000-000000000001"


def template():
    t = TariffTemplate(name="Hospital test", free_minutes=0, extra_hour_price=3000)
    t.tiers = [TariffTier(upto_minutes=n, price=p) for n, p in [(60, 1000), (120, 2000), (180, 5000)]]
    return t


def stay(minutes=120):
    return SimpleNamespace(hospital_grant_id=ID, hospital_allowance_minutes=minutes, fee_locked=False)


@pytest.mark.parametrize("duration,allowance,expected,used", [
    (180, 120, 3000, 120), (60, 120, 0, 60), (120, 60, 1000, 60),
    (180, 0, 5000, 0), (0, 120, 0, 0), (61, 120, 0, 61),
])
def test_money_difference_not_remaining_time(duration, allowance, expected, used, vat_inclusive):
    t = template()
    fee = calculate_fee(t, START, START + timedelta(minutes=duration))
    result = apply_hospital_benefit(stay(allowance), fee, t)
    assert result["total_fee"] == expected
    assert result["hospital_used_minutes"] == used
    assert fee.get("hospital_grant_id") is None  # no mutation of original quote
    assert result["base_fee"] + result["vat_amount"] == result["total_fee"]


def test_exclusive_vat_is_discounted_consistently(monkeypatch):
    monkeypatch.setattr(settings, "vat_inclusive", False)
    monkeypatch.setattr(settings, "vat_rate", 0.1)
    t = template()
    result = apply_hospital_benefit(stay(), calculate_fee(t, START, START + timedelta(hours=3)), t)
    assert (result["total_fee"], result["base_fee"], result["vat_amount"]) == (3300, 3000, 300)


def test_nested_minutes_are_not_credited_twice(vat_inclusive):
    t = template()
    result = apply_hospital_benefit(stay(), calculate_fee(t, START, START + timedelta(hours=10), paused_minutes=540), t)
    assert result["hospital_used_minutes"] == 60 and result["total_fee"] == 0


def test_replay_held_quote_never_deducts_twice_or_changes_expiry(vat_inclusive):
    t = template()
    fee = calculate_fee(t, START, START + timedelta(hours=3))
    fee["price_held_until"] = "2026-09-22T04:03:00"
    once = apply_hospital_benefit(stay(), fee, t)
    assert apply_hospital_benefit(stay(), once, t) == once
    assert once["price_held_until"] == fee["price_held_until"]


def test_close_releases_only_unused_minutes_and_reopen_cannot_reclaim_them(vat_inclusive):
    t, s = template(), stay()
    fee = apply_hospital_benefit(s, calculate_fee(t, START, START + timedelta(hours=1)), t)
    finish_hospital_usage(s, fee)
    assert s.hospital_used_minutes == s.hospital_allowance_minutes == 60
    later = apply_hospital_benefit(s, calculate_fee(t, START, START + timedelta(hours=3)), t)
    assert later["hospital_used_minutes"] == 60 and later["total_fee"] == 4000


def test_unknown_consumption_does_not_release_reservation():
    s = stay()
    finish_hospital_usage(s, {"total_fee": 0})
    assert s.hospital_allowance_minutes == 120


def test_day_boundary_uses_ub_time():
    assert str(local_date(datetime(2026, 9, 21, 15, 59))) == "2026-09-21"
    assert str(local_date(datetime(2026, 9, 21, 16))) == "2026-09-22"
    assert local_date(datetime(2026, 9, 22, tzinfo=timezone(timedelta(hours=8)))) == local_date(START)


def test_signatures_bind_body_timestamp_and_integration():
    raw = b'{"visit_id":"one"}'
    stamp = "1790030000"
    sig = sign("test-secret", ID, stamp, raw)
    assert verify("test-secret", ID, stamp, raw, sig, int(stamp))
    assert not verify("test-secret", ID, stamp, raw + b" ", sig, int(stamp))
    assert not verify("other-secret", ID, stamp, raw, sig, int(stamp))
    assert not verify("test-secret", ID[:-1] + "2", stamp, raw, sig, int(stamp))
    assert not verify("test-secret", ID, stamp, raw, sig, int(stamp) + 301)
    assert not verify("test-secret", ID, "bad", raw, sig, int(stamp))


@pytest.mark.parametrize("plate", ["1234UBA", "1234 УБА", "1234уба", "１２３４УБА", "1234УБ", "1234УБА\n"])
def test_plate_rejects_ocr_fuzz_and_noncanonical(plate):
    with pytest.raises(ValidationError):
        VisitInput(visit_id="one", site_code="HOSPITAL", plate_number=plate, served_at="2026-09-22T10:00:00+08:00")


def test_request_rejects_patient_data_and_naive_time():
    base = dict(visit_id="one", site_code="HOSPITAL", plate_number="1234УБА", served_at="2026-09-22T10:00:00+08:00")
    with pytest.raises(ValidationError):
        VisitInput(**base, patient_name="must never be stored")
    with pytest.raises(ValidationError):
        VisitInput(**{**base, "served_at": "2026-09-22T10:00:00"})


@pytest.mark.parametrize("minutes", [0, -1, 1441, True, 120.5, "120"])
def test_configuration_minutes_are_bounded_integers(minutes):
    with pytest.raises(ValidationError):
        IntegrationInput(name="Hospital", daily_minutes=minutes)


def test_new_integration_is_disabled_and_unassigned():
    body = IntegrationInput(name="Hospital")
    assert body.site_id is None and body.is_active is False and body.daily_minutes == 120


def test_secret_never_leaks_through_generic_or_config_serializer():
    row = HospitalIntegration(id=ID, name="Hospital", signing_secret="private", daily_minutes=120, key_version=1)
    assert "private" not in json.dumps(to_dict(row))
    assert "private" not in json.dumps(public_config(row))
