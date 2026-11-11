"""Гарах LED-ийн төлөх дүнг барих (2026-11-11): «дүн маш богино харагдаад алга болдог» гомдол.

Машин гарцад төлбөр хүлээж байх хооронд текстийг дахин илгээнэ; ШИНЭ текст (дараагийн машин,
«Баяртай»), сешн төлөгдсөн/хаагдсан, хугацаа дууссан үед зогсоно; хаалтны команд хүлээж байвал
тэр мөчлөгийг алгасна; давталт нь НЭГ богино RPC (repeat=1) ба LED-ийн статистикт орохгүй.
"""
import asyncio

import pytest

from app.config import settings
from app.services import barrier as B


@pytest.fixture
def fast(monkeypatch):
    calls = []

    async def fake_display(ip, text, voice_text=None, repeat=None, creds=None, keepalive=False,
                           keep_since=None):
        calls.append({"ip": ip, "text": text, "repeat": repeat, "keepalive": keepalive})
        return ""

    monkeypatch.setattr(B, "display_on_screen", fake_display)
    monkeypatch.setattr(B, "_MIN_KEEP_INTERVAL", 0.005)
    monkeypatch.setattr(settings, "screen_keep_sec", 5)
    monkeypatch.setattr(settings, "screen_keep_interval_sec", 0.01)
    monkeypatch.setattr(B, "barrier_is_waiting", lambda ip: False)
    monkeypatch.setattr(B, "camera_sick_remaining", lambda ip: 0)
    monkeypatch.setattr(B, "auth_block_remaining", lambda ip: 0)
    return calls


def test_resends_until_session_no_longer_waiting(fast):
    state = {"n": 0}

    def alive():
        state["n"] += 1
        return state["n"] <= 3

    B._display_gen["10.0.0.1"] = 7
    sent = asyncio.run(B._keep_on_screen("10.0.0.1", "1234УБА\n5000T", None, 7, alive))
    assert sent == 3
    assert all(c["repeat"] == 1 and c["keepalive"] and c["text"] == "1234УБА\n5000T" for c in fast)


def test_new_text_for_camera_stops_keeper(fast):
    def alive():
        if len(fast) >= 2:
            B._display_gen["10.0.0.2"] = 99    # дараагийн машин уншигдаж шинэ текст хүсэгдэв
        return True

    B._display_gen["10.0.0.2"] = 1
    sent = asyncio.run(B._keep_on_screen("10.0.0.2", "T", None, 1, alive))
    assert sent == 2


def test_barrier_waiting_skips_cycles(fast, monkeypatch):
    monkeypatch.setattr(B, "barrier_is_waiting", lambda ip: True)
    state = {"n": 0}

    def alive():
        state["n"] += 1
        return state["n"] <= 4

    B._display_gen["10.0.0.3"] = 1
    assert asyncio.run(B._keep_on_screen("10.0.0.3", "T", None, 1, alive)) == 0
    assert fast == []


def test_deadline_bounds_keeper(fast, monkeypatch):
    monkeypatch.setattr(settings, "screen_keep_sec", 0.05)
    B._display_gen["10.0.0.4"] = 1
    sent = asyncio.run(B._keep_on_screen("10.0.0.4", "T", None, 1, lambda: True))
    assert 1 <= sent <= 6


def test_disabled_or_without_condition(fast, monkeypatch):
    B._display_gen["10.0.0.5"] = 1
    assert asyncio.run(B._keep_on_screen("10.0.0.5", "T", None, 1, None)) == 0
    monkeypatch.setattr(settings, "screen_keep_sec", 0)
    assert asyncio.run(B._keep_on_screen("10.0.0.5", "T", None, 1, lambda: True)) == 0
    assert fast == []


def test_permanent_error_stops(fast, monkeypatch):
    async def rejecting(ip, text, voice_text=None, repeat=None, creds=None, keepalive=False,
                        keep_since=None):
        fast.append(1)
        return "DahuaRpcError: setScreenDisplay амжилтгүй: {}"
    monkeypatch.setattr(B, "display_on_screen", rejecting)
    B._display_gen["10.0.0.6"] = 1
    assert asyncio.run(B._keep_on_screen("10.0.0.6", "T", None, 1, lambda: True)) == 0
    assert len(fast) == 1


def test_schedule_display_starts_keeper_after_first_display(fast, monkeypatch):
    monkeypatch.setattr(settings, "screen_enabled", True)
    state = {"n": 0}

    def alive():
        state["n"] += 1
        return state["n"] <= 2

    async def scenario():
        B._shown_recent.pop("10.0.0.7", None)
        B.schedule_display("10.0.0.7", "FEE", None, None, keep_while=alive)
        for _ in range(200):
            await asyncio.sleep(0.005)
            if not B._display_tasks:
                break
    asyncio.run(scenario())
    first, *kept = fast
    assert first["keepalive"] is False and first["repeat"] is None   # анхны бичилт хуучнаараа
    assert len(kept) == 2 and all(k["keepalive"] for k in kept)


def test_barrier_command_after_display_stops_keeper(fast):
    async def scenario():
        since = __import__("time").monotonic()
        B._display_gen["10.0.0.8"] = 1

        def alive():
            if len(fast) >= 1:
                B._last_control_at["10.0.0.8"] = __import__("time").monotonic()   # хаалт нээгдэв
            return True
        return await B._keep_on_screen("10.0.0.8", "T", None, 1, alive, since)
    assert asyncio.run(scenario()) == 1


def test_in_lock_check_never_overwrites_bye_text(monkeypatch):
    """Түгжээ хүлээх зуур хаалт нээгдвэл (Баяртай бичигдсэн) хуучин дүнг бичихгүй."""
    import time as _t
    monkeypatch.setattr(settings, "barrier_mock", False)
    monkeypatch.setattr(B, "camera_sick_remaining", lambda ip: 0)
    monkeypatch.setattr(B, "auth_block_remaining", lambda ip: 0)

    async def no_gap(ip):
        return None
    monkeypatch.setattr(B, "wait_rpc_gap", no_gap)

    class NoRpc:
        def __init__(self, *a, **k):
            raise AssertionError("камерт бичих ёсгүй")
    monkeypatch.setattr(B, "DahuaRpc", NoRpc)
    since = _t.monotonic()
    B._last_control_at["10.0.0.9"] = _t.monotonic() + 1
    err = asyncio.run(B.display_on_screen("10.0.0.9", "T", repeat=1, creds=("u", "p"),
                                          keepalive=True, keep_since=since))
    assert err == B.KEEP_STOPPED
