"""Камерын зураг (2026-11-11): «Manual Snapshot» анхдагч асаалттай, хаалтанд бууж өгөх.

10-30-нд snapshot.cgi анхдагчаар унтарсан тул зургийн суваггүй камерууд орох/гарах зураг огт
хадгалахгүй болсон. Одоо: (1) анхдагч асаалттай, (2) snapshot.cgi-ийн хариуг хүлээх зуур хаалтны
команд ирвэл ≤50мс-д тасалж түгжээг өгнө (камерын алдаанд тооцохгүй), (3) CGI стримийн
танигдаагүй кадр (session-д хэзээ ч наагддаггүй) хүлээлт өдөөхгүй.
"""
import asyncio
import time
from types import SimpleNamespace

from app.config import settings
from app.services import app_settings as AS, barrier, snapshot as S, snap_puller


def test_manual_snapshot_default_on():
    assert AS.DEFAULTS[AS.BARRIER_KEY]["manual_snapshot"] is True


def test_stream_frames_do_not_trigger_wait(monkeypatch):
    monkeypatch.setattr(settings, "snap_comet", False)
    monkeypatch.setattr(settings, "snap_pull", False)
    S._stream_seen["10.9.9.9"] = time.monotonic()
    try:
        assert S.stream_delivers("10.9.9.9") is True
        assert S.image_channel_alive("10.9.9.9") is False
    finally:
        S._stream_seen.pop("10.9.9.9", None)
    # comet холбогдсон камер хүлээлт өдөөсөөр
    monkeypatch.setattr(settings, "snap_comet", True)
    snap_puller._comet_state["10.9.9.8"] = {"attached": time.monotonic(), "pics": 0}
    try:
        assert S.image_channel_alive("10.9.9.8") is True
    finally:
        snap_puller._comet_state.pop("10.9.9.8", None)


def test_cgi_yields_to_barrier_midflight(monkeypatch):
    async def scenario():
        loop = asyncio.get_running_loop()
        lock = asyncio.Lock()
        waiting = {"v": False}
        started = asyncio.Event()
        cancelled = {"v": False}

        async def slow_get(*a, **kw):
            started.set()
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                cancelled["v"] = True
                raise

        monkeypatch.setattr(barrier, "_rpc_lock", lambda ip: lock)
        monkeypatch.setattr(barrier, "barrier_is_waiting", lambda ip: waiting["v"])
        monkeypatch.setattr(barrier, "camera_client", lambda ip: SimpleNamespace(get=slow_get))
        monkeypatch.setattr(settings, "snapshot_cgi_budget_sec", 5)
        S._CGI_STATE.pop("cam-yield", None)
        task = asyncio.create_task(S._fetch_from_camera("cam-yield", ("u", "p")))
        await asyncio.wait_for(started.wait(), 1)
        assert lock.locked()                       # зураг авалт түгжээг барьж байна
        t0 = loop.time()
        waiting["v"] = True                        # хаалтны команд ирлээ
        assert await task is None
        assert loop.time() - t0 < 0.3              # ≤50мс тутам шалгадаг
        assert not lock.locked()                   # түгжээ хаалтанд шилжинэ
        await asyncio.sleep(0)
        assert cancelled["v"]                      # камерын хүсэлт тасарсан
        assert S._CGI_STATE["cam-yield"]["fails"] == 0   # камерын алдаа биш
        assert S.yielded_counts().get("cam-yield", 0) >= 1
    asyncio.run(scenario())


def test_cgi_success_unchanged(monkeypatch):
    jpeg = None
    import io
    import random
    from PIL import Image
    im = Image.frombytes("L", (64, 48), bytes(random.getrandbits(8) for _ in range(64 * 48)))
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "JPEG")
    jpeg = buf.getvalue()

    async def scenario():
        lock = asyncio.Lock()

        async def get(*a, **kw):
            await asyncio.sleep(0.12)              # хэд хэдэн шалгалтын мөчлөг
            return SimpleNamespace(status_code=200, content=jpeg)

        monkeypatch.setattr(barrier, "_rpc_lock", lambda ip: lock)
        monkeypatch.setattr(barrier, "barrier_is_waiting", lambda ip: False)
        monkeypatch.setattr(barrier, "camera_client", lambda ip: SimpleNamespace(get=get))
        S._CGI_STATE.pop("cam-ok", None)
        assert await S._fetch_from_camera("cam-ok", ("u", "p")) == jpeg
        assert not lock.locked()
    asyncio.run(scenario())


# ── 2026-11-11 PROD: зураг авалт ӨӨРИЙН хаалтны командад тасарч байв (26 тасалсан / 9 OK) ──
def _fake_fetch(monkeypatch, results, yield_on):
    """results — дуудлага бүрийн хариу; yield_on — аль дуудлагад хаалтанд зам тавьсан мэт болох."""
    calls = []

    async def fake(ip, creds=None):
        n = len(calls)
        calls.append(asyncio.get_running_loop().time())
        if n in yield_on:
            S._yielded[ip] = S._yielded.get(ip, 0) + 1
        return results[n] if n < len(results) else None
    monkeypatch.setattr(S, "_fetch_from_camera", fake)

    async def not_written(*a):
        return False
    monkeypatch.setattr(S, "_snapshot_written", not_written)
    return calls


def test_retry_once_after_yield(monkeypatch):
    monkeypatch.setattr(settings, "snapshot_settle_sec", 0)
    monkeypatch.setattr(barrier, "barrier_is_waiting", lambda ip: False)
    calls = _fake_fetch(monkeypatch, [None, b"JPEG"], yield_on={0})
    assert asyncio.run(S._fetch_after_own_barrier("sid", "ip-r1", "entry", None)) == b"JPEG"
    assert len(calls) == 2


def test_no_retry_on_camera_failure_and_at_most_two(monkeypatch):
    monkeypatch.setattr(settings, "snapshot_settle_sec", 0)
    monkeypatch.setattr(barrier, "barrier_is_waiting", lambda ip: False)
    calls = _fake_fetch(monkeypatch, [None], yield_on=set())      # камерын алдаа — давтахгүй
    assert asyncio.run(S._fetch_after_own_barrier("sid", "ip-r2", "entry", None)) is None
    assert len(calls) == 1
    calls = _fake_fetch(monkeypatch, [None, None, b"x"], yield_on={0, 1})   # 2 удаа тасарвал болино
    assert asyncio.run(S._fetch_after_own_barrier("sid", "ip-r3", "entry", None)) is None
    assert len(calls) == 2


def test_waits_for_own_barrier_to_start(monkeypatch):
    """Хаалтны команд 0.15с-ийн дараа эхэлдэг → зураг авалт түүнийг хүлээж эхэлсний ДАРАА дуудагдана."""
    monkeypatch.setattr(settings, "snapshot_settle_sec", 1.0)
    state = {"t0": None}

    def waiting(ip):
        now = asyncio.get_event_loop().time()
        return state["t0"] is not None and now - state["t0"] >= 0.15

    monkeypatch.setattr(barrier, "barrier_is_waiting", waiting)
    calls = _fake_fetch(monkeypatch, [b"JPEG"], yield_on=set())

    async def scenario():
        state["t0"] = asyncio.get_running_loop().time()
        out = await S._fetch_after_own_barrier("sid", "ip-r4", "entry", None)
        return out, calls[0] - state["t0"]
    out, started = asyncio.run(scenario())
    assert out == b"JPEG" and 0.14 <= started < 0.6     # settle-ийг бүтэн 1с хүлээхгүй
