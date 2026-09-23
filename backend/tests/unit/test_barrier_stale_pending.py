"""Гацсан PENDING хаалтны команд дараагийн машиныг мөнхөд хорихгүй.

2026-09-23 Андууд: орох хаалтын 09-22 14:32:49-ийн команд PENDING-ээр үлдэж
(гүйцэтгэгч нь дуусаагүй) `_execute` дараагийн БҮХ автомат командыг тэр мөрийг
буцаан алгасав — 21 цагт 482 уншилт, 0 команд, хаалт нээгдээгүй.
"""
import asyncio
import uuid
from datetime import datetime, timedelta

import pytest

from app.config import settings
from app.services import barrier as B


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture()
def env(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from app.database import Base
    from app.models import Device, ParkingSite
    monkeypatch.setattr(settings, "barrier_mock", True)
    monkeypatch.setattr(settings, "barrier_total_budget_sec", 15.0)
    B._open_inflight.clear()
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = Session(engine)
    site = ParkingSite(name="Synthetic", site_code=f"ST{uuid.uuid4().hex[:6]}")
    db.add(site); db.flush()
    dev = Device(site_id=site.id, name="Орох хаалт", device_key=f"st-{uuid.uuid4().hex[:8]}",
                 device_type="barrier", ip_address="203.0.113.77", status="active")
    db.add(dev); db.commit()
    yield db, dev
    db.close()
    engine.dispose()
    B._open_inflight.clear()


def _cmd(db, dev, status, age_sec, session_id=None, source="auto_entry"):
    from app.models import BarrierCommand
    c = BarrierCommand(device_id=dev.id, command="open", command_source=source,
                       status=status, session_id=session_id,
                       created_at=datetime.utcnow() - timedelta(seconds=age_sec))
    db.add(c); db.commit()
    return c


@pytest.mark.anyio
async def test_stale_pending_does_not_block_next_car(env):
    from app.models import AuditLog, BarrierCommand
    db, dev = env
    old = _cmd(db, dev, "PENDING", 21 * 3600, session_id=str(uuid.uuid4()))
    res = await B.open_barrier(db, dev, str(uuid.uuid4()), "auto_entry")
    assert res.id != old.id and res.status == "SUCCESS"
    db.refresh(old)
    assert old.status == "UNKNOWN" and "дуусаагүй" in (old.response_text or "")
    assert db.query(AuditLog).filter(AuditLog.action == "BARRIER_STALE_PENDING").count() == 1
    assert db.query(BarrierCommand).count() == 2


@pytest.mark.anyio
async def test_stale_pending_keeps_same_stay_no_replay(env):
    db, dev = env
    sid = str(uuid.uuid4())
    old = _cmd(db, dev, "PENDING", 600, session_id=sid)
    res = await B.open_barrier(db, dev, sid, "auto_entry")
    assert res.id == old.id and res.status == "UNKNOWN"


@pytest.mark.anyio
async def test_live_pending_still_reserves(env):
    db, dev = env
    old = _cmd(db, dev, "PENDING", 3, session_id=str(uuid.uuid4()))
    res = await B.open_barrier(db, dev, str(uuid.uuid4()), "auto_entry")
    assert res.id == old.id and res.status == "PENDING"


@pytest.mark.anyio
async def test_sessionless_unknown_blocks_only_briefly(env):
    db, dev = env
    fresh = _cmd(db, dev, "UNKNOWN", 5, session_id=None, source="whitelist")
    res = await B.open_barrier(db, dev, None, "whitelist")
    assert res.id == fresh.id                      # богино хугацаанд давтахгүй
    fresh.created_at = datetime.utcnow() - timedelta(minutes=10)
    db.commit()
    res = await B.open_barrier(db, dev, None, "whitelist")
    assert res.id != fresh.id and res.status == "SUCCESS"


@pytest.mark.anyio
async def test_manual_recovery_unchanged(env):
    db, dev = env
    old = _cmd(db, dev, "PENDING", 40, session_id=str(uuid.uuid4()))
    res = await B.open_barrier(db, dev, None, "manual", issued_by="op")
    db.refresh(old)
    assert res.id != old.id and old.status == "REVIEWED"


@pytest.mark.anyio
async def test_expiring_other_stay_keeps_older_unknown_guard(env):
    # Шүүмж: X-ийн 15 мин UNKNOWN + Y-ийн шинэ гацсан PENDING → X-ийн команд Y-ийг
    # хүчингүй болгоод X-д 2 дахь импульс илгээж болохгүй.
    db, dev = env
    x, y = str(uuid.uuid4()), str(uuid.uuid4())
    old_x = _cmd(db, dev, "UNKNOWN", 15 * 60, session_id=x)
    _cmd(db, dev, "PENDING", 5 * 60, session_id=y)
    res = await B.open_barrier(db, dev, x, "auto_entry")
    assert res.id == old_x.id and res.status == "UNKNOWN"


@pytest.mark.anyio
async def test_sessionless_unknown_close_does_not_block_open(env):
    db, dev = env
    _cmd(db, dev, "UNKNOWN", 5, session_id=None, source="sweep")
    from app.models import BarrierCommand
    db.query(BarrierCommand).update({"command": "close"}); db.commit()
    res = await B.open_barrier(db, dev, None, "whitelist")
    assert res.status == "SUCCESS" and res.command == "open"
