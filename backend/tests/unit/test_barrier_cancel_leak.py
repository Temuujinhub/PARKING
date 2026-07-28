"""Хаалтны команд таслагдахад _open_inflight леак үүсэхгүй байх (MONNIS 2026-07-28).

Гомдол: картаар төлсөн/удаан шийдэгдсэн гаралтын ДАРАА бүртгэлтэй машин ч
нээгдэхгүй болж restart хийтэл гацдаг байв. Шалтгаан: төлбөрийн HTTP хүсэлт
(POS/QPay/камерын push) таслагдахад CancelledError _execute-ийн цэвэрлэгээг
алгасаж, _open_inflight-д тэмдэглэгээ мөнхөд үлдэж, ensure_entry_barrier/
ensure_exit_barrier_if_cleared «аль хэдийн нээж байна» гэж худал үзээд
команд огт илгээхээ больдог байсан.

Шалгах зүйлс:
  1. Таслагдсан ч in-flight тэмдэглэгээ эцэстээ цэвэрлэгдэнэ.
  2. Таслагдсан ч хаалт нээх RPC ард нь ДУУСТАЛ явна (shield) — машин гарна.
  3. Хуучирсан (леак болсон) тэмдэглэгээг open_in_flight өөрөө хүчингүй болгоно.
"""
import asyncio
import time

import pytest

from app.config import settings
from app.services import barrier as B


@pytest.fixture
def anyio_backend():
    return "asyncio"


class _FakeDb:
    def add(self, obj): pass
    def flush(self): pass
    def commit(self): pass


class _FakeDevice:
    id = "bar-cancel-test"
    name = "Тест хаалт"
    lane_dir = "exit"
    ip_address = "203.0.113.99"   # TEST-NET — бодит холболт үүсэхгүй (mock RPC)
    username = ""
    password = ""
    site = None


@pytest.fixture()
def _real_barrier(monkeypatch):
    """barrier_mock-ыг унтрааж, RPC-г удаан боловч амжилттай mock-оор орлуулна."""
    monkeypatch.setattr(settings, "barrier_mock", False)
    monkeypatch.setattr(settings, "barrier_total_budget_sec", 5.0)
    monkeypatch.setattr(settings, "barrier_attempt_timeout_sec", 3.0)
    finished = asyncio.Event()

    class _SlowRpc:
        def __init__(self, client, host, username, password): pass
        async def login(self):
            await asyncio.sleep(0.5)   # таслагдах цэг — дуудагч 0.1с-д цуцлагдана
        async def logout(self): pass
        async def strobe(self, method, channel, plate=""):
            finished.set()
            return {"result": True}

    monkeypatch.setattr(B, "DahuaRpc", _SlowRpc)
    B._open_inflight.clear()
    yield finished
    B._open_inflight.clear()


@pytest.mark.anyio
async def test_cancelled_open_cleans_inflight_and_still_opens(_real_barrier):
    finished = _real_barrier
    dev = _FakeDevice()
    task = asyncio.ensure_future(
        B.open_barrier(_FakeDb(), dev, None, "payment", plate="1234УБА"))
    await asyncio.sleep(0.1)
    assert B.open_in_flight(dev.id), "команд явж байх үед in-flight гэж үзэх ёстой"
    task.cancel()   # POS/QPay/push хүсэлт таслагдсаныг дуурайна
    with pytest.raises(asyncio.CancelledError):
        await task
    # Хаалт нээх RPC ард нь дуустал явах ёстой (shield) — машин гарна
    await asyncio.wait_for(finished.wait(), timeout=3.0)
    # done-callback цэвэрлэгээ хийх завсар
    for _ in range(50):
        if not B.open_in_flight(dev.id):
            break
        await asyncio.sleep(0.05)
    assert not B.open_in_flight(dev.id), (
        "таслагдсаны дараа in-flight тэмдэглэгээ цэвэрлэгдэх ёстой — "
        "эс бол бүх дараагийн ensure_* нээлт гацна")


@pytest.mark.anyio
async def test_stale_inflight_self_heals():
    B._open_inflight.clear()
    B._open_inflight["bar-x"] = time.monotonic() - (settings.barrier_total_budget_sec + 60)
    assert not B.open_in_flight("bar-x"), "хуучирсан тэмдэглэгээ хүчингүй болох ёстой"
    assert "bar-x" not in B._open_inflight
