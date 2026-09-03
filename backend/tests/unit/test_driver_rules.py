"""Бүртгэлтэй машины логик шалгалт (2026-09-03 аудит).

Олдсон алдаанууд:
  • valid_from/valid_to зөвхөн огноогоор ирэхэд 00:00 UTC (=08:00 УБ) гэж
    хадгалдаг байсан → гэрээ сүүлийн өдрийн 08:00-оос хойш ХҮЧИНГҮЙ болдог байв
  • contract_type-ийг шалгадаггүй, үнэгүй цонхны ганц талыг зөвшөөрдөг байв
  • ижил дугаарыг ижил хамрах хүрээнд олон удаа бүртгэж болдог байв (прод: 0023УБЭ ×3)
  • тоон дүрмийн дээд хязгаарыг зөвхөн UI шахдаг байв
"""
from datetime import timedelta

import pytest
from fastapi import HTTPException

from app.config import settings
from app.routers.admin_router import CONTRACT_TYPES, _driver_validate, _parse_dt
from app.services import app_settings as A
from app.services import payment_rules as PR

TZ = timedelta(hours=settings.tz_offset_hours)


def test_date_only_valid_from_is_local_midnight():
    dt = _parse_dt("2026-09-03", "valid_from")
    assert (dt + TZ).strftime("%Y-%m-%d %H:%M:%S") == "2026-09-03 00:00:00"


def test_date_only_valid_to_is_local_end_of_day():
    dt = _parse_dt("2026-09-03", "valid_to", end_of_day=True)
    assert (dt + TZ).strftime("%Y-%m-%d %H:%M:%S") == "2026-09-03 23:59:59"


def test_datetime_with_time_is_kept_as_is():
    """Цагтай ISO мөр (хуучин клиент) хөндөгдөхгүй."""
    dt = _parse_dt("2026-09-03T10:30:00", "valid_to", end_of_day=True)
    assert dt.strftime("%Y-%m-%d %H:%M:%S") == "2026-09-03 10:30:00"


def test_bad_date_is_400_not_500():
    with pytest.raises(HTTPException) as e:
        _parse_dt("2026/09/03", "valid_to")
    assert e.value.status_code == 400


@pytest.mark.parametrize("ct", CONTRACT_TYPES)
def test_known_contract_types_pass(ct):
    _driver_validate({"contract_type": ct})


def test_unknown_contract_type_rejected():
    with pytest.raises(HTTPException) as e:
        _driver_validate({"contract_type": "GOLD"})
    assert e.value.status_code == 400


def test_half_free_window_rejected():
    with pytest.raises(HTTPException):
        _driver_validate({"free_from": "08:00"})
    with pytest.raises(HTTPException):
        _driver_validate({"free_until": "18:00"})


def test_equal_window_rejected_and_night_crossing_allowed():
    with pytest.raises(HTTPException):
        _driver_validate({"free_from": "08:00", "free_until": "08:00"})
    _driver_validate({"contract_type": "NIGHT", "free_from": "21:00", "free_until": "08:00"})


def test_clamp_respects_max_and_min():
    out = PR.clamp_values(A.BARRIER_KEY, {"dedup_seconds": 10_000, "entry_burst_seconds": 0})
    assert out["dedup_seconds"] == PR.MAX[(A.BARRIER_KEY, "dedup_seconds")]
    assert out["entry_burst_seconds"] == PR.MIN[(A.BARRIER_KEY, "entry_burst_seconds")]


def test_clamp_leaves_reset_and_bools_alone():
    out = PR.clamp_values(A.EXITRULES_KEY, {"no_session_fee": None, "wallet_auto_deduct": False,
                                             "min_stay_seconds": ""})
    assert out == {"no_session_fee": None, "wallet_auto_deduct": False, "min_stay_seconds": ""}


def test_night_window_is_not_per_site():
    """«Шөнө үнэгүй» цонх тодорхой МАШИНУУДЫН нөхцөл — зогсоолын давхаргад
    байх ёсгүй (2026-09-03 шийдвэр)."""
    assert A.DRIVERTYPE_KEY not in A.PER_SITE
    assert not any(r["group"] == A.DRIVERTYPE_KEY for r in PR.CATALOG)
