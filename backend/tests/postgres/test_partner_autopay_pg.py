"""Disposable PostgreSQL only (PARKING_TEST_DATABASE_URL): wallet-ийн автомат төлбөр
(2026-11-03) — хуучин схемээс шинэчлэх migration, зэрэгцээ гарах уншилт, FOR UPDATE
(joined relationship-тэй Payment) ба SKIP LOCKED жинхэнэ PG дээр.

    PARKING_TEST_DATABASE_URL=postgresql+psycopg2://…/t_recon \
        venv/bin/python -m pytest tests/postgres/test_partner_autopay_pg.py -q
"""
import hashlib
import threading
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from test_payment_migrations import engine, URL  # noqa: F401 — fixture
from app import migrations, models as M
from app.config import settings
from app.services import partner_autopay as PA
from app.services import partner_vehicles as PV

pytestmark = pytest.mark.skipif(not URL, reason="Disposable PostgreSQL DSN required")
PLATE = "7886УКР"
NEW = [s for s in migrations.MIGRATIONS if "auto_pay" in s or "partner_autopay" in s]


@pytest.fixture(autouse=True)
def flags(monkeypatch):
    monkeypatch.setattr(settings, "partner_autopay_enabled", True)
    monkeypatch.setattr(settings, "partner_vehicle_notify", True)


def seed(engine):
    migrations.run_migrations()
    assert migrations.check_ready()["database"] == "ok"
    with Session(engine) as db:
        tariff = M.TariffTemplate(name="T", free_minutes=0, extra_hour_price=1000)
        tariff.tiers = [M.TariffTier(upto_minutes=60, price=1000)]
        site = M.ParkingSite(name="AP synthetic", site_code="APSYN", tariff_template=tariff)
        key = M.PartnerKey(name="monpay", key_hash=hashlib.sha256(b"k").hexdigest(), key_prefix="k",
                           scopes="read,pay,vehicles", webhook_url="http://127.0.0.1:9/hook")
        db.add_all([site, key]); db.commit()
        assert PV.register_vehicle(db, key.id, "monpay", PLATE, "u1", auto_pay=True) == "OK"
        s = M.ParkingSession(site_id=site.id, plate_number=PLATE, status="AWAITING_PAYMENT",
                             entry_time=datetime.utcnow() - timedelta(minutes=30))
        db.add(s); db.commit()
        return site.id, key.id, s.id


def test_upgrade_from_previous_schema_keeps_registrations_off(engine):
    migrations.run_migrations()
    with engine.begin() as c:
        c.execute(text("DROP TABLE partner_autopay_requests"))
        c.execute(text("ALTER TABLE partner_vehicles DROP COLUMN auto_pay, DROP COLUMN auto_pay_updated_at"))
        for stmt in NEW:
            c.execute(text("DELETE FROM parking_schema_migrations WHERE version = :v"),
                      {"v": hashlib.sha256(stmt.encode()).hexdigest()})
        kid = c.execute(text("INSERT INTO partner_keys (id, name, key_hash, key_prefix, scopes, is_active, "
                             "created_by, created_at) VALUES (gen_random_uuid(), 'old', 'h', 'p', "
                             "'read,vehicles', true, '', now()) RETURNING id")).scalar()
        c.execute(text("INSERT INTO partner_vehicles (id, partner_key_id, plate_number, external_user_id, "
                       "created_via, created_at) VALUES (gen_random_uuid(), :k, '1234УБА', 'old-user', "
                       "'api', now())"), {"k": kid})
    assert len(NEW) >= 3
    migrations.run_migrations()
    assert migrations.check_ready()["database"] == "ok"
    with Session(engine) as db:
        row = db.query(M.PartnerVehicle).filter_by(external_user_id="old-user").one()
        assert row.auto_pay is False and row.auto_pay_updated_at is None


def test_concurrent_exit_reads_create_one_request(engine):
    site_id, kid, sid = seed(engine)
    start = threading.Barrier(4)
    results, errors = [], []

    def work():
        try:
            with Session(engine, autoflush=False) as db:
                s = db.get(M.ParkingSession, sid)
                start.wait(timeout=10)
                try:
                    results.append(PA.request_on_exit(db, s, PLATE))
                except HTTPException as exc:     # NOWAIT түгжээ — зэрэгцээ нэг нь л
                    db.rollback()
                    results.append(exc.status_code)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
    workers = [threading.Thread(target=work, daemon=True) for _ in range(4)]
    for w in workers: w.start()
    for w in workers: w.join(timeout=30)
    assert not errors, errors
    assert sum(1 for r in results if isinstance(r, dict)) == 1, results
    with Session(engine) as db:
        assert db.query(M.PartnerAutopayRequest).count() == 1
        assert db.query(M.Payment).filter_by(session_id=sid).count() == 1


def test_claim_skip_locked_and_expire_locks_payment_on_postgres(engine):
    site_id, kid, sid = seed(engine)
    with Session(engine, autoflush=False) as db:
        out = PA.request_on_exit(db, db.get(M.ParkingSession, sid), PLATE)
        assert out and out["amount"] == 1000.0

    got = []

    def claim():
        with Session(engine, autoflush=False) as db:
            got.append(PA.claim_due(db))
    ts = [threading.Thread(target=claim) for _ in range(2)]
    for t in ts: t.start()
    for t in ts: t.join(timeout=20)
    assert sorted(len(g) for g in got) == [0, 1]      # нэг л worker авна
    with Session(engine, autoflush=False) as db:
        req = db.query(M.PartnerAutopayRequest).one()
        assert req.status == "SENDING"
        # lease дуусаагүй SENDING мөрийг хугацаа дуусгалт ХӨНДӨХГҮЙ (HTTP явж байна)
        req.expires_at = req.lease_until - timedelta(seconds=5)
        db.commit()
        assert PA.expire_due(db, now=req.lease_until - timedelta(seconds=1)) == 0
        assert PA.expire_due(db, now=req.lease_until + timedelta(seconds=1)) == 1
        db.expire_all()
        assert db.get(M.Payment, out["payment_id"]).status == "CANCELLED"
        assert db.query(M.PartnerAutopayRequest).one().status == "FAILED"
