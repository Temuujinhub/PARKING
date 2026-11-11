"""Түншийн түлхүүр тутмын лавлах хүсэлтийн хязгаар (2026-11-11).

PROD: Easy Wallet (`easywallet`) ~190 дугаарыг 24с тутам GET /api/v1/sessions-оор асуудаг байв
(секундэд ~8). Эзэмшигчийн хүсэлтээр:
  1. ratelimit.allow — ЗӨВХӨН зөвшөөрсөн хүсэлтийг тоолно (тасралтгүй асуусан ч минутад N өнгөрнө)
  2. Хязгаар түлхүүр тутам, ЗАМ ТУС БҮРД (sessions / recent-exits / sites тусдаа) — 429 + Retry-After
  3. Төлбөрийн замууд хэзээ ч хязгаарлагдахгүй; хязгааргүй түлхүүрт толгой ч нэмэгдэхгүй
  4. Admin: зөвхөн системийн админ, 10–100000 эсвэл хоосон, аудит; жагсаалтад 429-ийн тоо
  5. Deploy preset: easywallet → 120/мин НЭГ удаа, админы өөрчлөлтийг хөндөхгүй
"""
import hashlib
import socket
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import auth, models as M, ratelimit
from app.config import settings
from app.database import Base, get_db
from app.routers import admin_router as A, integration_router as I
from app.services import app_settings as AS, financial_jobs as FJ, partner_presets as PP

KEY = "pk_easywallet_synthetic"
KEY2 = "pk_otherwallet_synthetic"


def _clear_limits():
    ratelimit._hits.clear()
    ratelimit._allowed.clear()
    I._limited.clear()
    I._limited_logged.clear()


@pytest.fixture
def c(monkeypatch):
    def forbidden(*a, **k):
        raise AssertionError("Network forbidden")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(settings, "partner_keys", "")
    monkeypatch.setattr(settings, "partner_lookup_rate_per_min", 0)
    monkeypatch.setattr(settings, "ebarimt_mock_receipts", False)
    monkeypatch.setattr(settings, "qpay_ebarimt", False)
    monkeypatch.setattr(FJ.msgbill, "account_enabled_for", lambda *a: None)
    _clear_limits()
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    AS.invalidate_cache()
    with Session(engine, autoflush=False) as db:
        tariff = M.TariffTemplate(name="Hourly", free_minutes=0, extra_hour_price=1000, grace_minutes=15)
        tariff.tiers = [M.TariffTier(upto_minutes=60, price=1000), M.TariffTier(upto_minutes=120, price=2000)]
        site = M.ParkingSite(name="Номадс", site_code="NOMADS", tariff_template=tariff)
        db.add(site)
        db.flush()
        key = M.PartnerKey(name="easywallet", key_hash=hashlib.sha256(KEY.encode()).hexdigest(),
                           key_prefix=KEY[:10], scopes="read,pay", rate_limit_per_min=2)
        other = M.PartnerKey(name="otherwallet", key_hash=hashlib.sha256(KEY2.encode()).hexdigest(),
                             key_prefix=KEY2[:10], scopes="read,pay")
        user = M.User(username="admin", password_hash="unused", role="SUPER_ADMIN")
        db.add_all([key, other, user])
        db.commit()
        app = FastAPI()
        app.include_router(I.router)
        app.include_router(A.router)
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[auth.get_current_user] = lambda: user
        with TestClient(app) as client:
            yield SimpleNamespace(db=db, client=client, site=site, key=key, other=other)
    _clear_limits()
    AS.invalidate_cache()
    engine.dispose()


def get(c, path, key=KEY):
    return c.client.get(f"/api/v1/{path}", headers={"X-API-Key": key})


def _session(c, plate="1234УБА", minutes=90):
    s = M.ParkingSession(site_id=c.site.id, plate_number=plate, status="OPEN",
                         entry_time=datetime.utcnow() - timedelta(minutes=minutes))
    c.db.add(s)
    c.db.commit()
    return s


# ── 1. Хязгаарлагч ──
def test_allow_counts_only_accepted(monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(ratelimit.time, "monotonic", lambda: clock["t"])
    _clear_limits()
    assert [ratelimit.allow("k", 3, 60)[0] for _ in range(3)] == [True, True, True]
    ok, remaining, wait = ratelimit.allow("k", 3, 60)
    assert not ok and remaining == 0 and 59 < wait <= 60
    for _ in range(100):                     # тасралтгүй асуусан ч цонхыг сунгахгүй
        clock["t"] += 0.1
        assert ratelimit.allow("k", 3, 60)[0] is False
    clock["t"] = 1060.01                     # эхний 3 хугацаа нь дуусав → дахин өнгөрнө
    assert ratelimit.allow("k", 3, 60)[:2] == (True, 2)
    assert ratelimit.allow("other", 3, 60)[0] is True   # өөр түлхүүр — тусдаа сав
    _clear_limits()


# ── 2–3. Лавлах зам: түлхүүр тутам, зам тус бүрд; төлбөр хязгаарлагдахгүй ──
def test_sessions_limited_per_key_and_route(c):
    _session(c)
    r1, r2 = get(c, "sessions?plate=1234УБА"), get(c, "sessions?plate=1234УБА")
    assert r1.status_code == r2.status_code == 200
    assert r1.headers["X-RateLimit-Limit"] == "2"
    assert (r1.headers["X-RateLimit-Remaining"], r2.headers["X-RateLimit-Remaining"]) == ("1", "0")
    r3 = get(c, "sessions?plate=1234УБА")
    assert r3.status_code == 429
    assert 1 <= int(r3.headers["Retry-After"]) <= 60
    assert r3.headers["X-RateLimit-Remaining"] == "0"
    assert "/sessions" in r3.json()["detail"] and "минутад 2" in r3.json()["detail"]
    # Өөр зам — тусдаа сав (гарах урсгал, газрын зураг тасрахгүй)
    assert get(c, "recent-exits").status_code == 200
    assert get(c, "sites").status_code == 200
    # Өөр түлхүүр (хязгааргүй) — нөлөөгүй, толгой нэмэгдэхгүй
    for _ in range(10):
        r = get(c, "sessions?plate=1234УБА", key=KEY2)
        assert r.status_code == 200 and "X-RateLimit-Limit" not in r.headers
    assert I.limited_counts(c.key.id) == {"sessions": 1}


def test_payments_never_limited(c):
    s = _session(c)
    for _ in range(2):
        get(c, "sessions?plate=1234УБА")
    assert get(c, "sessions?plate=1234УБА").status_code == 429
    r = c.client.post("/api/v1/payments", json={"session_id": s.id}, headers={"X-API-Key": KEY})
    assert r.status_code == 200, r.text
    pid = r.json()["payment_id"]
    for _ in range(5):                       # төлбөрийн төлөв шалгах ч хязгааргүй
        assert c.client.get(f"/api/v1/payments/{pid}", headers={"X-API-Key": KEY}).status_code == 200


def test_global_default_applies_when_key_has_no_limit(c, monkeypatch):
    monkeypatch.setattr(settings, "partner_lookup_rate_per_min", 3)
    codes = [get(c, "sites", key=KEY2).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    # Түлхүүрийн өөрийн утга анхдагчаас давуу
    c.other.rate_limit_per_min = 10
    c.db.commit()
    assert get(c, "sites", key=KEY2).status_code == 200


# ── 4. Admin ──
def test_admin_sets_and_clears_limit_with_audit(c):
    url = f"/api/admin/partner-keys/{c.key.id}/rate-limit"
    r = c.client.post(url, json={"per_min": 120})
    assert r.status_code == 200 and r.json()["rate_limit_per_min"] == 120
    c.db.refresh(c.key)
    assert c.key.rate_limit_per_min == 120
    log = c.db.query(M.AuditLog).filter_by(action="PARTNER_RATE_LIMIT_SET").one()
    assert log.detail == {"name": "easywallet", "old": 2, "new": 120}
    for bad in (5, 100001, "abc", True, 1.5):
        assert c.client.post(url, json={"per_min": bad}).status_code == 422, bad
    assert c.client.post(url, json={"per_min": "300"}).json()["rate_limit_per_min"] == 300
    assert c.client.post(url, json={"per_min": None}).json()["rate_limit_per_min"] is None
    c.db.refresh(c.key)
    assert c.key.rate_limit_per_min is None


def test_admin_list_shows_limit_and_429_counts(c):
    for _ in range(3):
        get(c, "sessions?plate=0000ТЕС")
    d = c.client.get("/api/admin/partner-keys").json()
    row = next(k for k in d["keys"] if k["name"] == "easywallet")
    assert row["rate_limit_per_min"] == 2 and row["rate_limited"] == {"sessions": 1}
    other = next(k for k in d["keys"] if k["name"] == "otherwallet")
    assert other["rate_limit_per_min"] is None and other["rate_limited"] == {}
    assert d["lookup_rate_default"] == 0


def test_admin_rate_limit_only_system_admin(c, monkeypatch):
    monkeypatch.setattr(A, "_key_scope", lambda user: ("tenant", "tenant-x"))
    r = c.client.post(f"/api/admin/partner-keys/{c.key.id}/rate-limit", json={"per_min": 600})
    assert r.status_code == 403
    c.db.refresh(c.key)
    assert c.key.rate_limit_per_min == 2


# ── 5. Deploy preset ──
@pytest.fixture
def db():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    AS.invalidate_cache()
    with Session(engine) as s:
        yield s
    AS.invalidate_cache()
    engine.dispose()


def _key(db, name, **kw):
    raw = f"pk_{name}_preset"
    k = M.PartnerKey(name=name, key_hash=hashlib.sha256(raw.encode()).hexdigest(), key_prefix=raw[:10],
                     scopes="read,pay", **kw)
    db.add(k)
    db.commit()
    return k


def test_easywallet_preset_once_and_respects_admin(db):
    ew = _key(db, "easywallet")
    sp = _key(db, "socialpay")
    changes = PP.apply_easywallet_rate_limit(db)
    assert [ch["new"] for ch in changes] == [120]
    db.refresh(ew)
    db.refresh(sp)
    assert ew.rate_limit_per_min == 120 and sp.rate_limit_per_min is None
    audit = db.query(M.AuditLog).filter_by(action="PARTNER_RATE_LIMIT_SET").one()
    assert audit.username == "system:deploy" and audit.detail["new"] == 120
    # Админ хассан → дараагийн restart-д дахин тавихгүй
    ew.rate_limit_per_min = None
    db.commit()
    assert PP.apply_easywallet_rate_limit(db) == []
    db.refresh(ew)
    assert ew.rate_limit_per_min is None


def test_easywallet_preset_waits_for_key_and_keeps_existing_value(db):
    assert PP.apply_easywallet_rate_limit(db) == []           # түлхүүр алга — тэмдэглэхгүй
    ew = _key(db, "easywallet", rate_limit_per_min=300)        # админ өмнө нь тавьсан
    assert PP.apply_easywallet_rate_limit(db) == []
    db.refresh(ew)
    assert ew.rate_limit_per_min == 300
    assert AS.get_state(db, PP.RATE_STATE_KEY)["done"] is True


def test_startup_presets_are_independent(monkeypatch):
    calls = []

    def boom(db):
        raise RuntimeError("monpay failed")
    monkeypatch.setattr(PP, "apply_monpay_webhook", boom)
    monkeypatch.setattr(PP, "apply_easywallet_rate_limit", lambda db: calls.append("easywallet"))
    fake = SimpleNamespace(rollback=lambda: calls.append("rollback"), close=lambda: calls.append("close"))
    PP.run_startup_presets(lambda: fake)
    assert calls == ["rollback", "easywallet", "close"]
