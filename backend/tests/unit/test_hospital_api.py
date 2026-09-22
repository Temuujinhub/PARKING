"""HTTP and persistence checks; only an in-memory DB, no bank or gate connections."""
import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import auth, models as M, ratelimit, session_logic as SL
from app.config import settings
from app.database import Base, get_db
from app.routers import hospital_router as H
from app.secretbox import encrypt_secret
from app.services import app_settings
from app.services.hospital_benefits import attach_daily_grant, finish_hospital_usage
from app.services.hospital_signing import sign, PATH
from app.services.payment_wait import begin_wait
from test_hospital_benefits import template

NOW = datetime(2026, 9, 22, 10, tzinfo=timezone.utc)
SECRET = "synthetic-hospital-secret-not-a-real-key"


@pytest.fixture
def context(monkeypatch):
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    # SQLAlchemy ignores postgresql_where on SQLite. Reproduce the production
    # partial unique index here; real locking is tested separately on PostgreSQL.
    with engine.begin() as c:
        c.execute(text("DROP INDEX uq_active_session"))
        c.execute(text("CREATE UNIQUE INDEX uq_active_session ON parking_sessions(site_id,plate_number) "
                       "WHERE status IN ('OPEN','AWAITING_PAYMENT','PAID')"))
    monkeypatch.setattr(settings, "secret_enc_key", Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "vat_inclusive", True)
    monkeypatch.setattr(settings, "vat_rate", 0.1)
    monkeypatch.setattr(H.time, "time", lambda: NOW.timestamp())
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)
        @classmethod
        def utcnow(cls):
            return NOW.replace(tzinfo=None)
    monkeypatch.setattr(H, "datetime", Frozen)
    ratelimit._hits.clear()
    app_settings.invalidate_cache()
    with Session(engine, autoflush=False) as db:
        site = M.ParkingSite(name="Synthetic", site_code="HOSP", tariff_template=template())
        other = M.ParkingSite(name="Other", site_code="OTHER", tariff_template=template())
        db.add_all([site, other]); db.flush()
        integration = M.HospitalIntegration(name="Hospital", site_id=site.id,
            daily_minutes=120, is_active=True, key_version=1, signing_secret=encrypt_secret(SECRET))
        user = M.User(username="admin", password_hash="unused", role="SUPER_ADMIN")
        stay = M.ParkingSession(site_id=site.id, plate_number="1234УБА",
            entry_time=NOW.replace(tzinfo=None) - timedelta(hours=3), status="OPEN")
        db.add_all([integration, user, stay]); db.commit()
        app = FastAPI()
        app.include_router(H.router)
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[auth.get_current_user] = lambda: user
        with TestClient(app, base_url="https://parking.test") as client:
            yield db, site, other, integration, user, stay, client
    app_settings.invalidate_cache()
    engine.dispose()


def payload(**changes):
    return {"visit_id": "visit-1", "site_code": "HOSP", "plate_number": "1234УБА",
            "served_at": NOW.isoformat(), **changes}


def send(client, integration, body=None, *, secret=SECRET, stamp=None, signature=None):
    raw = json.dumps(body or payload(), ensure_ascii=False, separators=(",", ":")).encode()
    stamp = str(int(NOW.timestamp())) if stamp is None else stamp
    return client.post(PATH, content=raw, headers={"Content-Type": "application/json",
        "X-Hospital-ID": integration.id, "X-Hospital-Timestamp": stamp,
        "X-Hospital-Signature": signature or sign(secret, integration.id, stamp, raw)})


def test_grant_retry_and_new_visit_cannot_increase_daily_quota(context):
    db, site, other, integration, user, stay, client = context
    first = send(client, integration)
    assert first.status_code == 200, first.text
    assert send(client, integration).json()["replayed"]
    assert send(client, integration, payload(visit_id="visit-2")).status_code == 200
    assert db.query(M.HospitalDailyGrant).count() == 1
    assert db.query(M.HospitalGrantRequest).count() == 2
    db.refresh(stay)
    assert stay.hospital_allowance_minutes == 120
    fee = SL.session_fee_info(db, stay, at=NOW.replace(tzinfo=None))
    assert fee["total_fee"] == 3000
    assert db.query(M.Payment).count() == db.query(M.BarrierCommand).count() == 0


def test_body_conflict_cross_site_and_signature_tamper(context):
    db, _, _, integration, _, _, client = context
    assert send(client, integration).status_code == 200
    assert send(client, integration, payload(plate_number="5678УБА")).status_code == 409
    assert send(client, integration, payload(visit_id="new", site_code="OTHER")).status_code == 403
    assert send(client, integration, signature="0"*64).status_code == 401
    assert send(client, integration, stamp=str(int(NOW.timestamp()) - 301)).status_code == 401
    assert db.query(M.HospitalDailyGrant).count() == 1


@pytest.mark.parametrize("status", ["CREATING", "PENDING", "UNKNOWN", "REVIEW", "PAID"])
def test_new_grant_never_changes_existing_invoice(context, status):
    db, _, _, integration, _, stay, client = context
    payment = M.Payment(session_id=stay.id, provider="QPAY", payment_method="QR",
        sender_invoice_no="synthetic", amount=5000, status=status)
    db.add(payment); db.commit()
    response = send(client, integration)
    assert response.status_code == 409
    assert response.json()["detail"] == "PAYMENT_IN_PROGRESS_OR_PAID"
    assert db.query(M.HospitalDailyGrant).count() == 0
    assert payment.amount == 5000 and payment.status == status


def test_held_exit_quote_discount_keeps_first_exit_and_expiry(context):
    db, _, _, integration, _, stay, client = context
    at = NOW.replace(tzinfo=None)
    stay.status = "AWAITING_PAYMENT"
    begin_wait(db, stay, SL.session_fee_info(db, stay, at=at), at)
    db.commit()
    first, until = stay.payment_wait_started_at, stay.payment_quote_until
    assert send(client, integration).status_code == 200
    db.refresh(stay)
    held = SL.session_fee_info(db, stay, at=at + timedelta(minutes=2))
    assert held["total_fee"] == 3000
    assert stay.total_fee == 3000
    assert (stay.payment_wait_started_at, stay.payment_quote_until) == (first, until)


def test_expired_quote_uses_current_full_duration_without_restarting_hold(context):
    db, _, _, integration, _, stay, client = context
    at = NOW.replace(tzinfo=None)
    stay.entry_time = at - timedelta(minutes=240)
    first = at - timedelta(minutes=61)
    stay.status = "AWAITING_PAYMENT"
    begin_wait(db, stay, SL.session_fee_info(db, stay, at=first), first)
    db.commit()
    expiry = stay.payment_quote_until
    assert send(client, integration).status_code == 200
    db.refresh(stay)
    current = SL.session_fee_info(db, stay, at=at)
    assert current["total_fee"] == stay.total_fee == 6000
    assert stay.payment_wait_started_at == first and stay.payment_quote_until == expiry


def test_manual_exit_uses_saved_discount_and_releases_unused_minutes(context, monkeypatch):
    import asyncio
    from app.routers import sessions_router as S
    db, _, _, integration, user, stay, client = context
    stay.entry_time = NOW.replace(tzinfo=None) - timedelta(minutes=60)
    stay.status = "AWAITING_PAYMENT"; stay.total_fee = 1000
    db.commit()
    assert send(client, integration).status_code == 200
    monkeypatch.setattr(S, "datetime", H.datetime)
    async def no_network(*args, **kwargs): pass
    monkeypatch.setattr(S.manager, "broadcast", no_network)
    asyncio.run(S.manual_exit(stay.id, {"reason":"Synthetic hospital check", "open_barrier":False}, db, user))
    db.refresh(stay)
    assert stay.total_fee == 0
    assert stay.hospital_used_minutes == stay.hospital_allowance_minutes == 60
    assert db.query(M.BarrierCommand).count() == db.query(M.Payment).count() == 0


def test_external_client_signature_matches_server(context):
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("hospital_example", Path(__file__).parents[3]/"examples/hospital_client.py")
    client_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(client_module)
    _, _, _, integration, _, _, client = context
    raw = json.dumps(payload(), ensure_ascii=False).encode()
    response = client.post(PATH, content=raw, headers=client_module.headers(integration.id, SECRET, raw, int(NOW.timestamp())))
    assert response.status_code == 200
    with pytest.raises(ValueError):
        client_module.submit("http://parking.test" + PATH, integration.id, SECRET, raw)


def test_terminal_hospital_snapshot_replaces_expired_cached_unpaid_amount(context):
    db, _, _, integration, _, stay, client = context
    assert send(client, integration).status_code == 200
    db.refresh(stay)
    stay.total_fee = 3000
    final = SL.session_fee_info(db, stay, at=NOW.replace(tzinfo=None) + timedelta(hours=1))
    assert final["total_fee"] == 6000
    finish_hospital_usage(stay, final)
    assert stay.total_fee == stay.hospital_fee_snapshot["total_fee"] == 6000
    stay.paid_at = NOW.replace(tzinfo=None)
    stay.total_fee = 3000
    finish_hospital_usage(stay, final)
    assert stay.total_fee == 3000  # settled row is never repriced by quota cleanup


def test_reentry_spends_remaining_minutes_then_full_tariff(context):
    db, site, _, integration, _, stay, client = context
    stay.entry_time = NOW.replace(tzinfo=None) - timedelta(minutes=60)
    db.commit()
    assert send(client, integration).status_code == 200
    db.refresh(stay)
    first_fee = SL.session_fee_info(db, stay, at=NOW.replace(tzinfo=None))
    assert first_fee["total_fee"] == 0
    finish_hospital_usage(stay, first_fee)
    stay.exit_time = NOW.replace(tzinfo=None)
    stay.status = "FREE"
    db.commit()
    second = M.ParkingSession(site_id=site.id, plate_number=stay.plate_number,
        entry_time=NOW.replace(tzinfo=None) + timedelta(minutes=5), status="OPEN")
    db.add(second); db.flush()
    attach_daily_grant(db, second); db.commit()
    assert second.hospital_allowance_minutes == 60
    second_fee = SL.session_fee_info(db, second, at=second.entry_time + timedelta(minutes=90))
    assert second_fee["total_fee"] == 1000 and second_fee["hospital_used_minutes"] == 60
    finish_hospital_usage(second, second_fee)
    second.status = "CLOSED"; second.exit_time = second.entry_time + timedelta(minutes=90)
    db.commit()
    third = M.ParkingSession(site_id=site.id, plate_number=stay.plate_number,
        entry_time=second.exit_time + timedelta(minutes=5), status="OPEN")
    db.add(third); db.flush()
    attach_daily_grant(db, third)
    assert third.hospital_allowance_minutes == 0
    assert SL.session_fee_info(db, third, at=third.entry_time + timedelta(minutes=180))["total_fee"] == 5000


def test_no_grant_next_day_and_no_retroactive_backfill(context):
    db, site, _, integration, _, stay, client = context
    assert send(client, integration).status_code == 200
    stay.status = "CLOSED"; db.commit()
    next_day = M.ParkingSession(site_id=site.id, plate_number=stay.plate_number,
        entry_time=NOW.replace(tzinfo=None) + timedelta(days=1), status="OPEN")
    db.add(next_day); db.flush()
    attach_daily_grant(db, next_day)
    assert next_day.hospital_grant_id is None
    next_day.status = "CLOSED"; db.commit()
    backfill = M.ParkingSession(site_id=site.id, plate_number=stay.plate_number,
        entry_time=NOW.replace(tzinfo=None) - timedelta(hours=6), status="OPEN")
    db.add(backfill); db.flush()
    attach_daily_grant(db, backfill)
    assert backfill.hospital_grant_id is None


def test_scoped_admin_cannot_read_edit_rotate_other_site_or_unassigned(context):
    db, site, other, integration, user, _, client = context
    user.role = "ADMIN"; user.site_id = other.id
    db.commit()
    assert client.get(H.ADMIN_PATH).json()["integrations"] == []
    body = {"name": "Changed", "site_id": other.id, "daily_minutes": 60, "is_active": False}
    assert client.put(H.ADMIN_PATH + "/" + integration.id, json=body).status_code == 403
    assert client.post(H.ADMIN_PATH + "/" + integration.id + "/rotate-key").status_code == 403
    assert client.post(H.ADMIN_PATH, json={"name": "Unassigned"}).status_code == 403
    user.role = "OPERATOR"; user.permissions = ["settings"]; db.commit()
    assert client.get(H.ADMIN_PATH).status_code == 403


def test_admin_disabled_unassigned_config_and_rotation_revokes_old_key(context):
    db, _, _, integration, _, _, client = context
    created = client.post(H.ADMIN_PATH, json={"name": "Unassigned"})
    assert created.status_code == 200
    assert not created.json()["is_active"] and created.json()["site_id"] is None
    rotated = client.post(H.ADMIN_PATH + "/" + integration.id + "/rotate-key")
    assert rotated.status_code == 200
    assert rotated.headers["cache-control"] == "no-store"
    key = rotated.json()["signing_secret"]
    db.refresh(integration)
    assert integration.signing_secret.startswith("enc:") and key not in integration.signing_secret
    assert send(client, integration).status_code == 401
    assert send(client, integration, secret=key).status_code == 200
    assert key not in client.get(H.ADMIN_PATH).text
    assert all(key not in json.dumps(a.detail) for a in db.query(M.AuditLog))


def test_encryption_required_no_plaintext_fallback(context, monkeypatch):
    _, _, _, integration, _, _, client = context
    monkeypatch.setattr(settings, "secret_enc_key", "")
    assert client.post(H.ADMIN_PATH + "/" + integration.id + "/rotate-key").status_code == 503


def test_https_and_bounded_body_required(context):
    _, _, _, integration, _, _, client = context
    assert client.post("http://parking.test" + PATH, json=payload()).status_code == 400
    assert client.post(PATH, content=b"x"*4097, headers={"content-type": "application/json"}).status_code == 413
    assert client.post(PATH, content="bad", headers={"content-type": "text/plain"}).status_code == 415
