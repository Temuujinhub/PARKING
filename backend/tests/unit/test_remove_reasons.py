"""«Зогсоолоос хасах»-ын шалтгааныг дахин шивэхгүй (2026-11-11): хадгалсан (★) + сүүлд хэрэглэсэн."""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models as M
from app.database import Base
from app.routers import sessions_router as R

ADMIN = SimpleNamespace(username="admin1", role="ADMIN")


@pytest.fixture
def db():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _removed(db, reason, days_ago=0):
    db.add(M.AuditLog(username="admin1", action="ADMIN_REMOVE", entity="session", entity_id="x",
                      detail={"count": 1, "reason": reason},
                      created_at=datetime.utcnow() - timedelta(days=days_ago)))
    db.commit()


def test_recent_by_frequency_then_recency(db):
    _removed(db, "Аудит — гарсан ч хаагдаагүй", 3)
    _removed(db, "Аудит — гарсан ч хаагдаагүй", 2)
    _removed(db, "Junk дугаар", 1)
    _removed(db, "", 0)                                   # хоосон — тоологдохгүй
    _removed(db, "Хуучин шалтгаан", 120)                 # 90 хоногоос хуучин
    out = R.remove_reasons(db=db, user=ADMIN)
    assert out == {"presets": [], "recent": ["Аудит — гарсан ч хаагдаагүй", "Junk дугаар"]}


def test_presets_saved_cleaned_and_excluded_from_recent(db):
    _removed(db, "Junk дугаар")
    res = R.save_remove_reasons({"items": ["  Junk   дугаар ", "Junk дугаар", "", "Тест машин"]},
                                db=db, user=ADMIN)
    assert res["presets"] == ["Junk дугаар", "Тест машин"]       # зай цэвэрлэгдэж, давхардал арилна
    out = R.remove_reasons(db=db, user=ADMIN)
    assert out["presets"] == ["Junk дугаар", "Тест машин"] and out["recent"] == []
    assert db.query(M.AuditLog).filter_by(action="REMOVE_REASONS_SET").count() == 1


def test_presets_validation(db):
    with pytest.raises(HTTPException) as e:
        R.save_remove_reasons({"items": "nope"}, db=db, user=ADMIN)
    assert e.value.status_code == 422
    with pytest.raises(HTTPException):
        R.save_remove_reasons({"items": [f"r{i}" for i in range(R.REMOVE_REASONS_MAX + 1)]},
                              db=db, user=ADMIN)
    res = R.save_remove_reasons({"items": ["x" * 500]}, db=db, user=ADMIN)
    assert len(res["presets"][0]) == R.REMOVE_REASON_LEN
