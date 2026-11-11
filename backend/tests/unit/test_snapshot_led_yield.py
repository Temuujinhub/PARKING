"""Гарах зураг vs LED-ийн төлөх дүн (2026-11-11 PROD, PR #30-ийн дараа).

21:35-аас хойш: орох 86%, гарах ЗӨВХӨН 56% зурагтай. Шалтгаан: гарах LED-ийн төлөх дүн 4×1.5с
давталтаар камерын түгжээг ~5с барьдаг, snapshot.cgi түгжээг 3с л хүлээгээд ЧИМЭЭГҮЙ бууж өгдөг
байв (төлбөр хүлээж буй машин). Мөн хүлээлтийн хугацаа камерын хариуны 5с-ийн төсөвт орж,
түгжээ хүлээсэн зураг авалт «TimeoutError» → камерын алдаа → 5 дараалвал 30 мин зураггүй.

  1. LED-ийн давталт хүлээж буй зураг авалтад ≤50мс-д зам тавина (эхний текст аль хэдийн бичигдсэн)
  2. keepalive мөчлөг зураг авалт хүлээж байвал алгасна
  3. snapshot түгжээ хүлээх зуур «хүлээж байна» гэж тэмдэглэнэ; LED-тэй зэрэг ажиллахад зураг авна
  4. Хүлээлт камерын хариуны төсөвт орохгүй (камерын алдаа биш); шалтгаан «ОЛДСОНГҮЙ» логт
"""
import asyncio
import io
import random
from types import SimpleNamespace

import pytest
from PIL import Image

from app.config import settings
from app.services import barrier as B, snapshot as S


def _jpeg() -> bytes:
    im = Image.frombytes("L", (64, 48), bytes(random.getrandbits(8) for _ in range(64 * 48)))
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "JPEG")
    return buf.getvalue()


@pytest.fixture
def led(monkeypatch):
    """display_on_screen-ийг жинхэнэ кодоор нь, камерыг хуурамчаар ажиллуулна."""
    sent = []

    class FakeRpc:
        def __init__(self, *a, **k):
            pass

        async def login(self):
            await asyncio.sleep(0.01)

        async def set_screen(self, text):
            sent.append((asyncio.get_running_loop().time(), text))
            await asyncio.sleep(0.01)

        async def set_voice(self, text):
            pass

        async def logout(self):
            await asyncio.sleep(0.01)

    async def no_gap(ip):
        return None

    monkeypatch.setattr(settings, "barrier_mock", False)
    monkeypatch.setattr(settings, "screen_hold_sec", 0)
    monkeypatch.setattr(settings, "screen_repeat", 4)
    monkeypatch.setattr(settings, "screen_repeat_interval", 1.5)
    monkeypatch.setattr(B, "DahuaRpc", FakeRpc)
    monkeypatch.setattr(B, "wait_rpc_gap", no_gap)
    monkeypatch.setattr(B, "camera_client", lambda ip: SimpleNamespace())
    monkeypatch.setattr(B, "camera_sick_remaining", lambda ip: 0)
    monkeypatch.setattr(B, "auth_block_remaining", lambda ip: 0)
    monkeypatch.setattr(B, "barrier_credentials", lambda *a: ("u", "p"))
    B._snapshot_waiting.clear()
    yield sent
    B._snapshot_waiting.clear()


def test_led_repeats_yield_to_waiting_snapshot(led):
    ip = "10.9.1.1"

    async def scenario():
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        task = asyncio.create_task(B.display_on_screen(ip, "1234УБА 5000₮"))
        while not led:                      # эхний текст бичигдтэл
            await asyncio.sleep(0.005)
        B.note_snapshot_waiting(ip, +1)     # гарах зураг түгжээ хүлээж эхэллээ
        try:
            assert await task == ""
        finally:
            B.note_snapshot_waiting(ip, -1)
        return loop.time() - t0
    took = asyncio.run(scenario())
    assert len(led) == 1                    # 4 давталтаас эхнийх л — дараа нь зам тавив
    assert took < 0.5                       # өмнө нь ~4.5с түгжээ барьдаг байв
    assert not B._rpc_lock(ip).locked()


def test_led_repeats_unchanged_without_waiters(led, monkeypatch):
    monkeypatch.setattr(settings, "screen_repeat_interval", 0.05)
    assert asyncio.run(B.display_on_screen("10.9.1.2", "T")) == ""
    assert len(led) == 4


def test_keepalive_skips_cycle_while_snapshot_waits(monkeypatch):
    calls = []

    async def fake_display(*a, **k):
        calls.append(1)
        return ""
    monkeypatch.setattr(B, "display_on_screen", fake_display)
    monkeypatch.setattr(B, "_MIN_KEEP_INTERVAL", 0.005)
    monkeypatch.setattr(settings, "screen_keep_sec", 5)
    monkeypatch.setattr(settings, "screen_keep_interval_sec", 0.01)
    monkeypatch.setattr(B, "barrier_is_waiting", lambda ip: False)
    monkeypatch.setattr(B, "camera_sick_remaining", lambda ip: 0)
    monkeypatch.setattr(B, "auth_block_remaining", lambda ip: 0)
    monkeypatch.setattr(B, "snapshot_is_waiting", lambda ip: True)
    state = {"n": 0}

    def alive():
        state["n"] += 1
        return state["n"] <= 4
    B._display_gen["10.9.1.3"] = 1
    assert asyncio.run(B._keep_on_screen("10.9.1.3", "T", None, 1, alive)) == 0
    assert calls == []


def test_exit_snapshot_succeeds_while_fee_is_on_led(led, monkeypatch):
    """Гарах камер: LED дүнгээ 4×1.5с давтаж эхэлсэн, 0.1с-ийн дараа зураг авалт эхэлнэ → зураг авна."""
    ip = "10.9.1.4"
    jpeg = _jpeg()

    async def get(*a, **kw):
        await asyncio.sleep(0.05)
        return SimpleNamespace(status_code=200, content=jpeg)
    monkeypatch.setattr(B, "camera_client", lambda ip: SimpleNamespace(get=get))
    monkeypatch.setattr(B, "barrier_is_waiting", lambda ip: False)
    monkeypatch.setattr(settings, "snapshot_lock_wait_sec", 3.0)
    S._CGI_STATE.pop(ip, None)

    async def scenario():
        loop = asyncio.get_running_loop()
        shown = asyncio.create_task(B.display_on_screen(ip, "1234УБА 5000₮"))
        await asyncio.sleep(0.1)
        t0 = loop.time()
        data = await S._fetch_from_camera(ip, ("u", "p"))
        took = loop.time() - t0
        await shown
        return data, took
    data, took = asyncio.run(scenario())
    assert data == jpeg and took < 1.0     # өмнө нь 3с хүлээгээд None буцаадаг байв
    assert S.skip_reason(ip) is None
    assert not B.snapshot_is_waiting(ip)


def test_lock_wait_is_not_a_camera_failure_and_reason_is_kept(monkeypatch):
    """Түгжээ завгүй (өөр ажил барьсан) → None + шалтгаан; камерын алдаанд тоологдохгүй."""
    ip = "10.9.1.5"
    monkeypatch.setattr(settings, "snapshot_lock_wait_sec", 0.05)
    monkeypatch.setattr(B, "barrier_is_waiting", lambda ip: False)
    S._CGI_STATE.pop(ip, None)

    async def scenario():
        lock = B._rpc_lock(ip)
        await lock.acquire()               # өөр урт ажил түгжээг барьж байна
        try:
            return await S._fetch_from_camera(ip, ("u", "p"))
        finally:
            lock.release()
    assert asyncio.run(scenario()) is None
    assert "түгжээ" in S.skip_reason(ip)
    assert S._CGI_STATE[ip]["fails"] == 0 and not B.snapshot_is_waiting(ip)


def test_budget_counts_only_camera_time(monkeypatch):
    """Түгжээг 0.3с хүлээсэн ч камерын хариуны төсөв (0.2с) бүтэн үлдэнэ."""
    ip = "10.9.1.6"
    jpeg = _jpeg()

    async def get(*a, **kw):
        await asyncio.sleep(0.12)
        return SimpleNamespace(status_code=200, content=jpeg)
    monkeypatch.setattr(B, "camera_client", lambda ip: SimpleNamespace(get=get))
    monkeypatch.setattr(B, "barrier_is_waiting", lambda ip: False)
    monkeypatch.setattr(settings, "snapshot_cgi_budget_sec", 0.2)
    monkeypatch.setattr(settings, "snapshot_lock_wait_sec", 2.0)
    S._CGI_STATE.pop(ip, None)

    async def scenario():
        lock = B._rpc_lock(ip)
        await lock.acquire()

        async def release_later():
            await asyncio.sleep(0.3)
            lock.release()
        asyncio.create_task(release_later())
        return await S._fetch_from_camera(ip, ("u", "p"))
    assert asyncio.run(scenario()) == jpeg
    assert S._CGI_STATE[ip]["fails"] == 0


def test_quiet_camera_reason(monkeypatch):
    import time
    ip = "10.9.1.7"
    S._CGI_STATE[ip] = {"url": None, "fails": 0, "quiet_until": time.monotonic() + 600}
    try:
        assert asyncio.run(S._fetch_from_camera(ip, ("u", "p"))) is None
        assert "түр зогссон" in S.skip_reason(ip)
    finally:
        S._CGI_STATE.pop(ip, None)
