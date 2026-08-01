"""snapshot.cgi-ийн өмнө WS event зургийг хүлээх логик — standalone тест.

    cd backend && venv/bin/python tests/test_snapshot_wait.py

Гол баталгаа: (1) WS зураг өгдөггүй камерт зан төлөв ОГТ өөрчлөгдөхгүй (шууд
snapshot.cgi), (2) өгдөг камерт зураг ирвэл snapshot.cgi ОГТ дуудагдахгүй,
(3) хүлээгээд ирэхгүй бол fallback ажиллана.
"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings  # noqa: E402
from app.services import snapshot, snap_puller  # noqa: E402

PASS, FAIL = 0, 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  <<< FAIL")


# ── puller_delivers ──
print("puller_delivers:")
snap_puller._last_pic.clear()
orig_snap_pull = settings.snap_pull

settings.snap_pull = False
snap_puller._last_pic["1.2.3.4"] = time.monotonic()
check("snap_pull=false үед ямагт False", snap_puller.puller_delivers("1.2.3.4") is False)

settings.snap_pull = True
check("зураг ирээгүй камерт False", snap_puller.puller_delivers("9.9.9.9") is False)
check("саяхан зураг ирсэн камерт True", snap_puller.puller_delivers("1.2.3.4") is True)
snap_puller._last_pic["5.5.5.5"] = time.monotonic() - 7200
check("2 цагийн өмнөх зураг → False (30 мин цонх)", snap_puller.puller_delivers("5.5.5.5") is False)
check("ip хоосон → False", snap_puller.puller_delivers("") is False)

# ── _capture_and_store урсгал (fake-уудтай) ──
print("_capture_and_store:")
calls = {"fetch": 0, "wait": 0}


async def fake_fetch(ip, creds=None):
    calls["fetch"] += 1
    return None  # зураг татагдсангүй гэж үзнэ — _save/DB зам ажиллахгүй


_orig_fetch = snapshot._fetch_from_camera
_orig_wait = snapshot._wait_event_snapshot
snapshot._fetch_from_camera = fake_fetch


async def run(raw, ip, wait_result=None):
    calls["fetch"] = calls["wait"] = 0

    async def fake_wait(session_id, lane_dir):
        calls["wait"] += 1
        return wait_result

    snapshot._wait_event_snapshot = fake_wait
    await snapshot._capture_and_store("sid1", ip, "1234УБА", "entry", raw)


# 1) WS зураг өгдөггүй камер (puller_delivers=False) → хүлээхгүй шууд fetch
snap_puller._last_pic.clear()
asyncio.run(run({}, "10.0.113.10"))
check("WS зураггүй камер: хүлээлгүй шууд snapshot.cgi", calls["fetch"] == 1 and calls["wait"] == 0)

# 2) WS зураг өгдөг камер + зураг ирэв → snapshot.cgi ОГТ дуудагдахгүй
snap_puller._last_pic["10.0.113.10"] = time.monotonic()
asyncio.run(run({}, "10.0.113.10", wait_result=True))
check("WS зураг ирэв: snapshot.cgi алгасагдана (Manual Snap үүсэхгүй)",
      calls["fetch"] == 0 and calls["wait"] == 1)

# 3) WS зураг өгдөг ч энэ удаад ирсэнгүй → fallback snapshot.cgi
asyncio.run(run({}, "10.0.113.10", wait_result=False))
check("WS зураг ирээгүй: fallback snapshot.cgi ажиллана", calls["fetch"] == 1 and calls["wait"] == 1)

# 4) payload-д base64 зурагтай (ITSAPI push) → хүлээлт ч, fetch ч хэрэггүй
import base64
big = base64.b64encode(b"\xff\xd8" + b"x" * 2000).decode()
_orig_save = snapshot._save
snapshot._save = lambda *a: None  # диск/DB-д хүрэхгүй (зам бичих үе шат энд сонирхолгүй)
try:
    asyncio.run(run({"Picture": {"NormalPic": {"Content": big}}}, "10.0.113.10", wait_result=True))
finally:
    snapshot._save = _orig_save
check("payload зурагтай: wait/fetch аль нь ч дуудагдахгүй",
      calls["fetch"] == 0 and calls["wait"] == 0)

# 5) snapshot_wait_event_sec=0 → хуучин зан төлөв (шууд fetch)
_orig_cfg = settings.snapshot_wait_event_sec
settings.snapshot_wait_event_sec = 0
asyncio.run(run({}, "10.0.113.10", wait_result=True))
check("wait=0 тохиргоо: хуучин зан төлөв", calls["fetch"] == 1 and calls["wait"] == 0)
settings.snapshot_wait_event_sec = _orig_cfg

snapshot._fetch_from_camera = _orig_fetch
snapshot._wait_event_snapshot = _orig_wait
settings.snap_pull = orig_snap_pull

print("=" * 40)
print(f"ҮР ДҮН: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
