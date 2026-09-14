"""snapshot.cgi-ийн өмнө камерын ӨӨРИЙН event зургийг хүлээх логик — standalone тест.

    cd backend && venv/bin/python tests/test_snapshot_wait.py

2026-09-14 (зогсоолуудын «event бүрд давхар Manual Snapshot» гомдол): snapshot.cgi
ЗӨВХӨН камер зургаа өөрөө өгч ЧАДААГҮЙ үед л дуудагдана. Гол баталгаа:
  (1) зургийн суваггүй камерт зан төлөв ОГТ өөрчлөгдөхгүй (шууд snapshot.cgi),
  (2) comet суваг ХОЛБООТОЙ (зураг өгсөн түүхгүй ч) бол хүлээнэ,
  (3) хүлээх үед зураг ирвэл snapshot.cgi ОГТ дуудагдахгүй,
  (4) ирэхгүй бол fallback ажиллана,
  (5) session-д зураг АЛЬ ХЭДИЙН байвал (дахин уншилт) юу ч хийхгүй,
  (6) логоос нөхсөн (log_tail) event-д амьд кадр авахгүй.
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


# ── puller_delivers / comet_attached ──
print("puller_delivers / comet_attached:")
snap_puller._last_pic.clear()
snap_puller._comet_state.clear()
orig_snap_pull, orig_comet = settings.snap_pull, settings.snap_comet

settings.snap_pull = False
settings.snap_comet = False
snap_puller._last_pic["1.2.3.4"] = time.monotonic()
check("snap_pull=snap_comet=false үед ямагт False", snap_puller.puller_delivers("1.2.3.4") is False)
settings.snap_comet = True
check("comet асаалттай бол зураг өгсөн камерт True (өмнө нь зөвхөн snap_pull-аар хаагддаг байв)",
      snap_puller.puller_delivers("1.2.3.4") is True)
settings.snap_comet = False
settings.snap_pull = True
check("зураг ирээгүй камерт False", snap_puller.puller_delivers("9.9.9.9") is False)
check("саяхан зураг ирсэн камерт True", snap_puller.puller_delivers("1.2.3.4") is True)
snap_puller._last_pic["5.5.5.5"] = time.monotonic() - 7200
check("2 цагийн өмнөх зураг → False (30 мин цонх)", snap_puller.puller_delivers("5.5.5.5") is False)
check("ip хоосон → False", snap_puller.puller_delivers("") is False)
settings.snap_pull = False
settings.snap_comet = True
snap_puller._comet_state["7.7.7.7"] = {"attached": time.monotonic(), "pics": 0}
check("comet attach-тай камер (зураг өгөөгүй ч) → comet_attached True",
      snap_puller.comet_attached("7.7.7.7") is True)
snap_puller._comet_state["8.8.8.8"] = {"attached": None, "pics": 3}
check("тасарсан comet → False", snap_puller.comet_attached("8.8.8.8") is False)
settings.snap_comet = False
check("snap_comet унтраалттай → False", snap_puller.comet_attached("7.7.7.7") is False)
check("image_channel_alive: суваггүй камер → False", snapshot.image_channel_alive("10.0.113.10") is False)

# ── _capture_and_store урсгал (fake-уудтай) ──
print("_capture_and_store:")
calls = {"fetch": 0, "written": 0}
written_after = {"n": None}   # хэд дэх _snapshot_written дуудлагаас True болох вэ (None = хэзээ ч)


async def fake_fetch(ip, creds=None):
    calls["fetch"] += 1
    return None  # зураг татагдсангүй гэж үзнэ — _save/DB зам ажиллахгүй


async def fake_written(session_id, lane_dir):
    calls["written"] += 1
    return written_after["n"] is not None and calls["written"] >= written_after["n"]


_orig_fetch, _orig_written = snapshot._fetch_from_camera, snapshot._snapshot_written
snapshot._fetch_from_camera = fake_fetch
snapshot._snapshot_written = fake_written
_orig_wait, _orig_stream = settings.snapshot_wait_event_sec, settings.snapshot_stream_wait_sec
settings.snapshot_wait_event_sec = 1.5
settings.snapshot_stream_wait_sec = 0.5


def run(raw, ip, written_n=None):
    calls["fetch"] = calls["written"] = 0
    written_after["n"] = written_n
    t0 = time.monotonic()
    asyncio.run(snapshot._capture_and_store("sid1", ip, "1234УБА", "entry", raw))
    return time.monotonic() - t0


# 1) Зургийн суваггүй камер → хүлээлгүй шууд snapshot.cgi (хуучин зан төлөв)
snap_puller._last_pic.clear(); snap_puller._comet_state.clear()
settings.snap_comet = False
dt = run({}, "10.0.113.10")
check("суваггүй камер: хүлээлгүй шууд snapshot.cgi", calls["fetch"] == 1 and dt < 0.5)

# 2) comet ХОЛБООТОЙ, зураг өгсөн түүхгүй (шөнийн эхний машин) → хүлээнэ; зураг ирвэл fetch ҮГҮЙ
settings.snap_comet = True
snap_puller._comet_state["10.0.113.10"] = {"attached": time.monotonic(), "pics": 0}
dt = run({}, "10.0.113.10", written_n=2)
check("comet attach-тай: хүлээгээд зураг ирэв → snapshot.cgi ОГТ дуудагдахгүй",
      calls["fetch"] == 0 and calls["written"] >= 2)

# 3) comet холбоотой ч зураг ирсэнгүй → цонх дуусаад fallback snapshot.cgi
dt = run({}, "10.0.113.10")
check("comet attach-тай ч зураг ирсэнгүй: цонхны дараа fallback snapshot.cgi",
      calls["fetch"] == 1 and dt >= 1.4)

# 4) Стримийн санамжид зураг байвал хүлээлгүй шууд түүнийг авна (fetch үгүй)
_orig_save = snapshot._save
snapshot._save = lambda *a: None
try:
    snapshot.offer_stream_image("10.0.113.10", b"\xff\xd8" + b"x" * 2000, src="comet")
    dt = run({}, "10.0.113.10")
finally:
    snapshot._save = _orig_save
check("стримийн зураг бэлэн: шууд авна, snapshot.cgi үгүй", calls["fetch"] == 0 and dt < 0.5)
snapshot._stream_images.clear()

# 5) session-д зураг АЛЬ ХЭДИЙН бий (гарах хаалтан дээр дахин уншигдав) → юу ч хийхгүй
dt = run({}, "10.0.113.10", written_n=1)
check("зураг аль хэдийн бий: хүлээхгүй, snapshot.cgi үгүй", calls["fetch"] == 0 and dt < 0.5)

# 6) log_tail (логоос нөхсөн) event → амьд кадр авахгүй
dt = run({"log_tail": True}, "10.0.113.10")
check("log_tail event: snapshot.cgi үгүй (машин аль хэдийн өнгөрсөн)",
      calls["fetch"] == 0 and calls["written"] == 0)

# 7) payload-д base64 зурагтай (ITSAPI push) → хүлээлт ч, fetch ч хэрэггүй
import base64
big = base64.b64encode(b"\xff\xd8" + b"x" * 2000).decode()
snapshot._save = lambda *a: None
try:
    run({"Picture": {"NormalPic": {"Content": big}}}, "10.0.113.10")
finally:
    snapshot._save = _orig_save
check("payload зурагтай: хүлээхгүй, snapshot.cgi дуудагдахгүй",
      calls["fetch"] == 0 and calls["written"] <= 1)   # written=1: бичихийн өмнөх давхардлын шалгалт

# 8) snapshot.cgi fallback унтраалттай → зураггүй үлдэнэ (fetch үгүй)
_orig_fb = settings.snapshot_cgi_fallback
settings.snapshot_cgi_fallback = False
run({}, "10.0.113.10")
settings.snapshot_cgi_fallback = _orig_fb
check("cgi_fallback=false: snapshot.cgi дуудагдахгүй", calls["fetch"] == 0)

snapshot._fetch_from_camera = _orig_fetch
snapshot._snapshot_written = _orig_written
settings.snapshot_wait_event_sec, settings.snapshot_stream_wait_sec = _orig_wait, _orig_stream
settings.snap_pull, settings.snap_comet = orig_snap_pull, orig_comet
snap_puller._comet_state.clear()

print("=" * 40)
print(f"ҮР ДҮН: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
