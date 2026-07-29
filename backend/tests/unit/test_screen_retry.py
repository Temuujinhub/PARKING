"""Дэлгэцийн хожуу дахин оролдлого — суваг чөлөөлөгдөхөд харуулах (2026-07-29).

Хэмжилтээр манай систем камерын нэвтрэлтийн 80%-ийг эзэлж байсан тул дэлгэц
амжилтгүй болбол ШУУД биш, хожуу (суваг сул үед) дахин оролдоно. Шалгах зүйлс:
  1. Эхний оролдлого амжилттай бол дахин оролдохгүй.
  2. Амжилтгүй бол хожуу дахин оролдож, амжилттай болмогц зогсоно.
  3. Хаалт хүлээж байвал тэр оролдлогыг алгасна (хаалт тэргүүлэх эрхтэй).
  4. Шинэ текст ирвэл хуучин оролдлого өөрөө зогсоно (камерыг дэмий цохихгүй).
"""
import asyncio

import pytest

from app.config import settings
from app.services import barrier as B


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture()
def _fast(monkeypatch):
    monkeypatch.setattr(settings, "screen_enabled", True)
    monkeypatch.setattr(settings, "screen_retry_delays", "0.05,0.05")
    B._display_gen.clear()
    B._cam_sick_until.clear()
    B._barrier_waiting.clear()
    calls = []

    async def _fake(ip, text, voice_text=None, repeat=None, creds=None):
        calls.append((ip, text))
        return "" if getattr(_fake, "ok_from", 0) <= len(calls) - 1 else "алдаа"

    monkeypatch.setattr(B, "display_on_screen", _fake)
    return calls, _fake


@pytest.mark.anyio
async def test_success_first_try_no_retry(_fast):
    calls, fake = _fast
    fake.ok_from = 0            # эхний оролдлогоос амжилттай
    B._display_gen["10.0.0.1"] = 1
    await B._display_with_retry("10.0.0.1", "текст", None, None, 1)
    assert len(calls) == 1


@pytest.mark.anyio
async def test_retries_until_success(_fast):
    calls, fake = _fast
    fake.ok_from = 2            # зөвхөн 3 дахь оролдлого амжилттай
    B._display_gen["10.0.0.2"] = 1
    await B._display_with_retry("10.0.0.2", "текст", None, None, 1)
    assert len(calls) == 3, "амжилттай болтол хожуу дахин оролдоно"


@pytest.mark.anyio
async def test_skips_while_barrier_waiting(_fast):
    calls, fake = _fast
    fake.ok_from = 0
    B._barrier_waiting["10.0.0.3"] = 1      # хаалтны команд хүлээж байна
    B._display_gen["10.0.0.3"] = 1
    await B._display_with_retry("10.0.0.3", "текст", None, None, 1)
    assert calls == [], "хаалт хүлээж байхад камерт хүрэхгүй"


@pytest.mark.anyio
async def test_newer_text_cancels_old_retry(_fast, monkeypatch):
    calls, _fake_unused = _fast
    B._display_gen["10.0.0.4"] = 1

    async def _fail_then_newer(ip, text, voice_text=None, repeat=None, creds=None):
        calls.append((ip, text))
        B._display_gen[ip] = 2       # эхний оролдлогын дараа ШИНЭ текст ирлээ
        return "алдаа"

    monkeypatch.setattr(B, "display_on_screen", _fail_then_newer)
    await B._display_with_retry("10.0.0.4", "хуучин", None, None, 1)
    assert len(calls) == 1, "хоцрогдсон текстийг дахин оролдохгүй"


@pytest.mark.anyio
async def test_schedule_display_sets_generation(_fast, monkeypatch):
    B._display_gen.clear()
    B.schedule_display("10.0.0.5", "нэг")
    B.schedule_display("10.0.0.5", "хоёр")
    assert B._display_gen["10.0.0.5"] == 2
    await asyncio.sleep(0.01)


@pytest.mark.anyio
async def test_permanent_error_not_retried(_fast, monkeypatch):
    """Камер командыг ТАТГАЛЗвал дахин оролдохгүй (дэмий цохилт нэмэхгүй)."""
    calls, _unused = _fast
    B._display_gen["10.0.0.6"] = 1

    async def _reject(ip, text, voice_text=None, repeat=None, creds=None):
        calls.append((ip, text))
        return "DahuaRpcError: setScreenDisplay амжилтгүй: {'result': False}"

    monkeypatch.setattr(B, "display_on_screen", _reject)
    await B._display_with_retry("10.0.0.6", "текст", None, None, 1)
    assert len(calls) == 1


@pytest.mark.anyio
async def test_transient_error_is_retried(_fast, monkeypatch):
    """Нэвтрэлт/сессийн алдаа нь ТҮР зуурын — суваг сулрахад дахин оролдоно."""
    calls, _unused = _fast
    B._display_gen["10.0.0.7"] = 1

    async def _busy(ip, text, voice_text=None, repeat=None, creds=None):
        calls.append((ip, text))
        return "DahuaRpcError: login амжилтгүй: User or password not valid!"

    monkeypatch.setattr(B, "display_on_screen", _busy)
    await B._display_with_retry("10.0.0.7", "текст", None, None, 1)
    assert len(calls) == 3


@pytest.mark.anyio
async def test_rpc_gap_waits_after_barrier(monkeypatch):
    """Хаалтны RPC-ийн дараа дэлгэц ЗАВСАР хүлээнэ — камер сессээ чөлөөлж
    амжаагүй байхад нэвтэрвэл remainLoginTimes буурч түгжээ рүү ойртдог."""
    import time as _t
    monkeypatch.setattr(settings, "camera_rpc_gap_sec", 0.2)
    B.note_rpc_done("10.0.0.9")
    t0 = _t.monotonic()
    await B.wait_rpc_gap("10.0.0.9")
    assert _t.monotonic() - t0 >= 0.15, "завсар хүлээх ёстой"


@pytest.mark.anyio
async def test_rpc_gap_no_wait_when_idle(monkeypatch):
    import time as _t
    monkeypatch.setattr(settings, "camera_rpc_gap_sec", 0.5)
    B._last_rpc_done.pop("10.0.0.10", None)
    t0 = _t.monotonic()
    await B.wait_rpc_gap("10.0.0.10")
    assert _t.monotonic() - t0 < 0.1, "саяхан RPC байхгүй бол хүлээхгүй"


@pytest.mark.anyio
async def test_recently_shown_text_is_skipped(_fast, monkeypatch):
    """Хаалттай ХАМТ бичигдсэн текстийг schedule_display давхардуулахгүй."""
    monkeypatch.setattr(settings, "screen_dedup_sec", 10.0)
    calls, fake = _fast
    fake.ok_from = 0
    B._shown_recent.clear()
    B._note_screen_shown("10.0.0.11", "1234УБА\nТавтай морил")   # хаалттай хамт бичигдэв
    B.schedule_display("10.0.0.11", "1234УБА\nТавтай морил")
    await asyncio.sleep(0.05)
    assert calls == [], "давхардсан бичилт камерт хүрэх ёсгүй"


@pytest.mark.anyio
async def test_different_text_still_sent(_fast, monkeypatch):
    monkeypatch.setattr(settings, "screen_dedup_sec", 10.0)
    calls, fake = _fast
    fake.ok_from = 0
    B._shown_recent.clear()
    B._note_screen_shown("10.0.0.12", "хуучин текст")
    B.schedule_display("10.0.0.12", "ШИНЭ текст")
    await asyncio.sleep(0.05)
    assert len(calls) == 1, "өөр текст бол илгээгдэнэ"
