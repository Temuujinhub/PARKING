"""Cancellation leaves a durable unknown command, without leaking in-flight state."""
import asyncio
import time

import pytest

from app.config import settings
from app.services import barrier as B


@pytest.fixture
def anyio_backend():
    return "asyncio"


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
async def test_cancelled_open_is_durable_and_does_not_repeat(_real_barrier):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from app.database import Base
    from app.models import Device, ParkingSite, BarrierCommand
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        site = ParkingSite(name="Synthetic", site_code="CANCEL")
        db.add(site); db.flush()
        dev = Device(site_id=site.id, name="Gate", device_key="cancel-test",
                     device_type="barrier", ip_address="203.0.113.99", status="active")
        db.add(dev); db.commit()
        task = asyncio.create_task(B.open_barrier(db, dev, None, "payment"))
        await asyncio.sleep(0.1)
        assert B.open_in_flight(dev.id)
        with Session(engine) as observer:
            assert observer.query(BarrierCommand).one().status == "PENDING"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not B.open_in_flight(dev.id)
        assert not _real_barrier.is_set()
        command = db.query(BarrierCommand).one()
        assert command.status == "UNKNOWN"
        again = await B.open_barrier(db, dev, None, "payment")
        assert again.id == command.id
    engine.dispose()


@pytest.mark.anyio
async def test_stale_inflight_self_heals():
    B._open_inflight.clear()
    B._open_inflight["bar-x"] = time.monotonic() - (settings.barrier_total_budget_sec + 60)
    assert not B.open_in_flight("bar-x"), "хуучирсан тэмдэглэгээ хүчингүй болох ёстой"
    assert "bar-x" not in B._open_inflight
