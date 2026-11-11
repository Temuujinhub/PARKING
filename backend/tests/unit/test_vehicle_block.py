"""Машин блоклох (2026-11-11): машин тус бүрээр «нэвтрүүлэх / нэвтрүүлэхгүй» + шалтгаан, оператор
ч блоклоно, шалтгаан Касс/Шалгах/POS-ын мөрөнд (blacklist) харагдана."""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models as M, session_logic as SL
from app.database import Base
from app.routers import admin_router as A, sessions_router as R

OP = SimpleNamespace(username="op1", role="OPERATOR", id="u1", site_id=None, site_ids=None,
                     permissions=None, tenant_id=None)


@pytest.fixture
def db():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


@pytest.mark.parametrize("policy,rule,expected", [
    ("DENY", False, True), ("DENY", True, True),
    ("ALLOW", True, False), ("ALLOW", False, False),
    (None, True, True), (None, False, False),
])
def test_policy_overrides_rule(policy, rule, expected):
    b = M.BlacklistEntry(plate_number="1234УБА", reason="x", entry_policy=policy)
    assert SL.blacklist_blocks_entry(b, {"block_entry": rule}) is expected
    assert SL.blacklist_blocks_entry(None, {"block_entry": True}) is False


def test_lookup_prefers_explicit_policy(db):
    now = datetime.utcnow()
    db.add_all([M.BlacklistEntry(plate_number="1234УБА", reason="автомат хориг: 3 өр",
                                 created_at=now),
                M.BlacklistEntry(plate_number="1234УБА", reason="Хулгайн машин", entry_policy="DENY",
                                 created_at=now - timedelta(days=2)),
                M.BlacklistEntry(plate_number="1234УБА", reason="хуучин", entry_policy="DENY",
                                 is_active=False, created_at=now)])
    db.commit()
    b = SL.is_blacklisted(db, "1234УБА")
    assert b.reason == "Хулгайн машин" and b.entry_policy == "DENY"


def test_operator_can_block_with_reason_and_upsert(db, monkeypatch):
    monkeypatch.setattr(A, "admin_plate", lambda p: p.strip().upper())
    with pytest.raises(HTTPException) as e:
        A.add_blacklist({"plate_number": "1234УБА", "reason": " "}, db=db, user=OP)
    assert e.value.status_code == 400                       # шалтгаангүй блок хориотой
    with pytest.raises(HTTPException) as e:
        A.add_blacklist({"plate_number": "1234УБА", "reason": "Төлөөгүй гарсан", "entry_policy": "MAYBE"},
                        db=db, user=OP)
    assert e.value.status_code == 422
    first = A.add_blacklist({"plate_number": "1234УБА", "reason": "Төлөөгүй гарсан",
                             "entry_policy": "deny"}, db=db, user=OP)
    assert first["entry_policy"] == "DENY" and first["created_by"] == "op1"
    again = A.add_blacklist({"plate_number": "1234УБА", "reason": "Менежертэй уулзуулна",
                             "entry_policy": "ALLOW"}, db=db, user=OP)
    assert again["id"] == first["id"]                       # давхар мөр үүсгэхгүй — шинэчилнэ
    assert again["entry_policy"] == "ALLOW" and again["updated_by"] == "op1"
    assert db.query(M.BlacklistEntry).count() == 1
    actions = [a for (a,) in db.query(M.AuditLog.action).all()]
    assert sorted(actions) == ["BLACKLIST_UPDATE", "CREATE"]


def test_update_and_unblock_audited(db):
    b = M.BlacklistEntry(plate_number="5555УНА", reason="Хаалт эвдэлсэн", created_by="admin")
    db.add(b)
    db.commit()
    out = A.update_blacklist(b.id, {"is_active": False, "note": "Хохирол барагдуулсан"}, db=db, user=OP)
    assert out["is_active"] is False and out["updated_by"] == "op1"
    log = db.query(M.AuditLog).filter_by(entity="blacklist").one()
    assert log.detail["note"] == "Хохирол барагдуулсан" and log.detail["old"]["is_active"] is True


def test_operator_routes_allow_cashier_and_check():
    for fn in (A.add_blacklist, A.update_blacklist, A.list_blacklist):
        dep = [d for d in fn.__defaults__ if hasattr(getattr(d, "dependency", None), "required_modules")]
        mods = set(dep[0].dependency.required_modules)
        assert {"blacklist", "cashier", "check"} <= mods, fn.__name__
    clear = [d for d in A.clear_blacklist.__defaults__
             if hasattr(getattr(d, "dependency", None), "required_modules")][0]
    assert set(clear.dependency.required_modules) == {"blacklist"}   # бүгдийг цэвэрлэх — зөвхөн эрхтэй


def test_rows_carry_block_reason(db):
    db.add(M.BlacklistEntry(plate_number="7777УБА", reason="Хаалт мөргөсөн", entry_policy="DENY",
                            created_by="op1"))
    db.commit()
    rows = R._attach_blacklist(db, [{"plate_number": "7777УБА"}, {"plate_number": "1111УБА"}])
    assert rows[0]["blacklist"]["reason"] == "Хаалт мөргөсөн"
    assert rows[0]["blacklist"]["entry_policy"] == "DENY" and rows[0]["blacklist"]["created_by"] == "op1"
    assert rows[1]["blacklist"] is None
