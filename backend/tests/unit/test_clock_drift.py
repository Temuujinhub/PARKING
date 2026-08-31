"""clock_drift — камерын цагийн зөрүүг RealUTC-ээс пассив хэмжих цэвэр дүрэм.

2026-08-31: Рашбулагийн 4 камер 46 мин – 2 цаг түрүүлж явсныг хэн ч мэдээгүй
байсан тул эвэнт бүрийн RealUTC-ээс зөрүүг хөтөлж UI-д харуулдаг болов.

    cd backend && venv/bin/python -m pytest tests/unit/test_clock_drift.py -q
"""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.config import settings  # noqa: E402
from app.services import clock_drift  # noqa: E402
from app.services.clock_drift import (describe, device_drift,  # noqa: E402
                                      extract_real_utc, note_event)

NOW = datetime(2026, 8, 31, 10, 0, 0)


def setup_function(_):
    clock_drift._state.clear()


def _ev(drift_sec: float) -> dict:
    return {"Code": "TrafficJunction", "RealUTC": NOW.timestamp() + drift_sec}


def test_extract_only_real_utc():
    """UTC/TrafficCar.UTC нь ЛОКАЛ epoch тул хэрэглэхгүй — зөвхөн RealUTC."""
    assert extract_real_utc({"RealUTC": 1787890678}) == 1787890678
    assert extract_real_utc({"UTC": 1787919478}) is None
    assert extract_real_utc({"TrafficCar": {"UTC": 1787919478}}) is None
    assert extract_real_utc({"RealUTC": 123}) is None      # бодит бус epoch
    assert extract_real_utc({}) is None
    assert extract_real_utc("junk") is None


def test_no_real_utc_is_noop():
    assert note_event("d1", {"Plate": {"PlateNumber": "1234УБА"}}, now=NOW) is None
    assert device_drift("d1") is None


def test_ewma_converges_and_sign():
    """Камер 2 цаг түрүүлсэн (Рашбулагийн бодит кейс) — эерэг тэмдэгтэй тогтоно."""
    for _ in range(10):
        note_event("d1", _ev(7200), now=NOW)
    st = device_drift("d1")
    assert st and 7000 < st["drift_sec"] <= 7200
    assert "түрүүлж" in st["text"]
    assert st["note"] and "гажина" in st["note"]           # босго давсан → улаан


def test_small_drift_no_note():
    for _ in range(5):
        note_event("d1", _ev(30), now=NOW)
    st = device_drift("d1")
    assert st and st["note"] is None                        # 30с < 120с босго
    assert "30с" in st["text"] or "29с" in st["text"]


def test_single_outlier_does_not_alarm():
    """Сүлжээний нэг удаагийн саатал (нэг эвэнт 300с) дохио өгөхгүй —
    n>=3 ба EWMA хоёул хамгаална."""
    note_event("d1", _ev(300), now=NOW)
    st = device_drift("d1")
    assert st and st["note"] is None                        # n=1 — эрт
    note_event("d1", _ev(0), now=NOW)
    note_event("d1", _ev(0), now=NOW)
    note_event("d1", _ev(0), now=NOW)
    st = device_drift("d1")
    assert st and abs(st["drift_sec"]) < settings.clock_drift_warn_sec
    assert st["note"] is None


def test_describe_units():
    assert describe(7380) == "2ц 3м түрүүлж явна"
    assert describe(-95) == "1м 35с хоцорч явна"
    assert describe(12) == "12с түрүүлж явна"
