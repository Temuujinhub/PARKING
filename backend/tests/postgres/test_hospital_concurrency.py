"""Disposable PostgreSQL only: real row locks, quota uniqueness, migration replay."""
import hashlib
import threading
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError, IntegrityError
from sqlalchemy.orm import Session

from test_payment_migrations import engine, URL
from app import migrations, models as M, session_logic as SL
from app.config import settings
from app.routers.hospital_router import accept_visit, VisitInput
from app.services.checkout import lock_session
from app.services.hospital_benefits import attach_daily_grant, finish_hospital_usage

pytestmark = pytest.mark.skipif(not URL, reason="Disposable PostgreSQL DSN required")
NOW = datetime(2026, 9, 22, 10, tzinfo=timezone.utc)


def seed(engine):
    migrations.run_migrations()
    with Session(engine) as db:
        tariff = M.TariffTemplate(name="Hospital synthetic", free_minutes=0, extra_hour_price=1000)
        site = M.ParkingSite(name="Hospital synthetic", site_code="HOSP", tariff_template=tariff)
        db.add(site); db.flush()
        integration = M.HospitalIntegration(name="Hospital synthetic", site_id=site.id, daily_minutes=120)
        stay = M.ParkingSession(site_id=site.id, plate_number="1234УБА",
            entry_time=NOW.replace(tzinfo=None) - timedelta(minutes=60), status="OPEN")
        db.add_all([integration, stay]); db.commit()
        return integration.id, stay.id, site.id


def visit(visit_id="one"):
    return VisitInput(visit_id=visit_id, site_code="HOSP", plate_number="1234УБА", served_at=NOW)


def test_concurrent_verified_requests_grant_once(engine):
    iid, sid, _ = seed(engine)
    start = threading.Barrier(2); results = []; errors = []
    def grant():
        try:
            with Session(engine, autoflush=False) as db:
                start.wait(timeout=10)
                try:
                    integration = db.query(M.HospitalIntegration).filter_by(id=iid).with_for_update(nowait=True).one()
                    results.append(accept_visit(db, integration, visit(), "a"*64, NOW))
                    db.commit()
                except (OperationalError, IntegrityError):
                    db.rollback()
                    results.append({"busy": True})
        except Exception as exc:
            errors.append(exc)
    workers = [threading.Thread(target=grant, daemon=True) for _ in range(2)]
    for worker in workers: worker.start()
    for worker in workers: worker.join(timeout=20)
    assert not any(worker.is_alive() for worker in workers)
    assert not errors, errors
    with Session(engine, autoflush=False) as db:
        retried = accept_visit(db, db.get(M.HospitalIntegration, iid), visit(), "a"*64, NOW)
        db.commit()
        assert retried["replayed"]
        assert db.query(M.HospitalGrantRequest).count() == db.query(M.HospitalDailyGrant).count() == 1
        assert db.get(M.ParkingSession, sid).hospital_allowance_minutes == 120
        assert db.query(M.Payment).count() == db.query(M.BarrierCommand).count() == 0


def test_checkout_lock_prevents_invoice_grant_race(engine):
    iid, sid, _ = seed(engine)
    with Session(engine) as checkout, Session(engine) as callback:
        lock_session(checkout, sid)
        with pytest.raises(OperationalError):
            accept_visit(callback, callback.get(M.HospitalIntegration, iid), visit(), "b"*64, NOW)
        callback.rollback()
        assert callback.query(M.HospitalDailyGrant).count() == 0
        checkout.rollback()
        assert accept_visit(callback, callback.get(M.HospitalIntegration, iid), visit(), "b"*64, NOW)["status"] == "GRANTED"
        callback.commit()


def test_manual_operator_mutations_cannot_race_a_hospital_grant(engine):
    import asyncio
    from fastapi import HTTPException
    from app.routers import sessions_router as S
    iid, sid, _ = seed(engine)
    with Session(engine) as grant, Session(engine) as operator:
        lock_session(grant, sid)
        user = M.User(username="synthetic", role="SUPER_ADMIN", password_hash="unused")
        for action in (
            lambda: S.apply_discount(sid, {}, operator, user),
            lambda: asyncio.run(S.edit_plate(sid, {"plate_number":"5678УБА"}, operator, user)),
            lambda: asyncio.run(S.manual_exit(sid, {"reason":"Synthetic", "open_barrier":False}, operator, user)),
            lambda: asyncio.run(S.special_exit(sid, {"kind":"emergency"}, operator, user)),
        ):
            with pytest.raises(HTTPException) as error:
                action()
            assert error.value.status_code == 409
            operator.rollback()
        assert operator.query(M.BarrierCommand).count() == 0
        grant.rollback()


def test_real_partial_index_and_daily_reservation_across_reentries(engine, monkeypatch):
    monkeypatch.setattr(settings, "vat_inclusive", True)
    iid, sid, site_id = seed(engine)
    with Session(engine, autoflush=False) as db:
        accept_visit(db, db.get(M.HospitalIntegration, iid), visit(), "c"*64, NOW)
        db.commit()
        stay = db.get(M.ParkingSession, sid)
        fee = SL.session_fee_info(db, stay, at=NOW.replace(tzinfo=None))
        assert fee["total_fee"] == 0 and fee["hospital_used_minutes"] == 60
        finish_hospital_usage(stay, fee)
        stay.status = "FREE"; stay.exit_time = NOW.replace(tzinfo=None)
        db.commit()
        next_stay = M.ParkingSession(site_id=site_id, plate_number=stay.plate_number,
            entry_time=NOW.replace(tzinfo=None) + timedelta(minutes=10), status="OPEN")
        db.add(next_stay); db.flush()
        attach_daily_grant(db, next_stay); db.commit()
        assert next_stay.hospital_allowance_minutes == 60
        fee = SL.session_fee_info(db, next_stay, at=next_stay.entry_time + timedelta(minutes=180))
        assert fee["total_fee"] == 2000
        # Reopening the old stay cannot reclaim the 60 minutes released to this one.
        assert stay.hospital_allowance_minutes == 60
        assert db.query(M.HospitalDailyGrant).one().daily_minutes == 120


def test_grant_reservation_row_lock_is_nowait(engine):
    iid, sid, site_id = seed(engine)
    with Session(engine) as db:
        grant = M.HospitalDailyGrant(integration_id=iid, site_id=site_id, plate_number="5678УБА",
            benefit_date=NOW.date(), daily_minutes=120, created_at=NOW.replace(tzinfo=None))
        db.add(grant); db.commit(); gid = grant.id
    with Session(engine) as owner, Session(engine) as entrant:
        owner.query(M.HospitalDailyGrant).filter_by(id=gid).with_for_update().one()
        stay = M.ParkingSession(site_id=site_id, plate_number="5678УБА",
            entry_time=NOW.replace(tzinfo=None) + timedelta(minutes=1), status="OPEN")
        entrant.add(stay); entrant.flush()
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as error:
            attach_daily_grant(entrant, stay)
        assert error.value.status_code == 409
        entrant.rollback()
        owner.rollback()


def test_additive_hospital_upgrade_preserves_financial_rows_and_replays(engine):
    iid, sid, _ = seed(engine)
    with Session(engine) as db:
        payment = M.Payment(session_id=sid, provider="CASH", payment_method="CASH",
            sender_invoice_no="historical-synthetic", amount=1234.25, status="PAID")
        db.add(payment); db.commit()
    with engine.begin() as c:
        before = c.execute(text("SELECT md5(string_agg(to_jsonb(p)::text,'' ORDER BY id)) FROM payments p")).scalar()
        for name in ("hospital_grant_id", "hospital_allowance_minutes", "hospital_used_minutes", "hospital_fee_snapshot"):
            c.execute(text("ALTER TABLE parking_sessions DROP COLUMN " + name))
        c.execute(text("DROP TABLE hospital_grant_requests"))
        c.execute(text("DROP TABLE hospital_daily_grants"))
        c.execute(text("DROP TABLE hospital_integrations"))
        # Simulate the pre-feature ledger; old financial migration hashes remain.
        for statement in migrations.MIGRATIONS:
            if "hospital_" in statement:
                c.execute(text("DELETE FROM parking_schema_migrations WHERE version=:version"),
                          {"version": hashlib.sha256(statement.encode()).hexdigest()})
    migrations.run_migrations()
    migrations.run_migrations()
    assert migrations.check_ready()["database"] == "ok"
    with engine.connect() as c:
        assert before == c.execute(text("SELECT md5(string_agg(to_jsonb(p)::text,'' ORDER BY id)) FROM payments p")).scalar()
        assert c.execute(text("SELECT count(*) FROM hospital_daily_grants")).scalar() == 0
        assert c.execute(text("SELECT hospital_grant_id FROM parking_sessions WHERE id=:sid"), {"sid": sid}).scalar() is None
