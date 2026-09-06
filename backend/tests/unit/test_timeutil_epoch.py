"""timeutil.utc_epoch + camera_records.to_camera_epoch — OS цагийн бүсээс хамаарахгүй.

2026-09-06: прод сервер Asia/Ulaanbaatar болоход naive.timestamp() 8ц хазайж
камерын лог хайлтын муж (to_camera_epoch) 8ц ухарсан — log_tail юу ч олдоггүй болов.

    cd backend && venv/bin/python -m pytest tests/unit/test_timeutil_epoch.py -q
"""
import calendar
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.config import settings  # noqa: E402
from app.services.camera_records import from_camera_epoch, to_camera_epoch  # noqa: E402
from app.timeutil import utc_epoch  # noqa: E402

DT = datetime(2026, 9, 6, 8, 2, 30)
EPOCH = calendar.timegm(DT.timetuple())  # 1788681750


def _with_tz(tz):
    os.environ["TZ"] = tz
    time.tzset()


def teardown_function(_):
    os.environ.pop("TZ", None)
    time.tzset()


def test_utc_epoch_same_in_every_timezone():
    for tz in ("Etc/UTC", "Asia/Ulaanbaatar", "America/New_York"):
        _with_tz(tz)
        assert utc_epoch(DT) == EPOCH, tz
    assert utc_epoch(DT.replace(tzinfo=timezone.utc)) == EPOCH
    assert utc_epoch(DT.replace(microsecond=500000)) == EPOCH + 0.5


def test_camera_epoch_roundtrip_under_ulaanbaatar_tz():
    _with_tz("Asia/Ulaanbaatar")
    cam = to_camera_epoch(DT)
    assert cam == EPOCH + settings.camera_tz_offset_hours * 3600
    assert from_camera_epoch(cam) == DT
