"""Disposable PostgreSQL only (PARKING_TEST_DATABASE_URL): SocialPay бүртгэлийн race,
partial unique индекс, outbox-ийн SKIP LOCKED, scanner-ийн SQL жинхэнэ PG дээр.

    PARKING_TEST_DATABASE_URL=postgresql+psycopg2://…/t_recon \
        venv/bin/python -m pytest tests/postgres/test_partner_vehicles_pg.py -q
"""
import hashlib
import threading
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from test_payment_migrations import engine, URL  # noqa: F401 — fixture
from app import migrations, models as M
from app.config import settings
from app.services import partner_vehicles as PV

pytestmark = pytest.mark.skipif(not URL, reason="Disposable PostgreSQL DSN required")


def seed(engine):
    migrations.run_migrations()
    assert migrations.check_ready()["database"] == "ok"
    with Session(engine) as db:
        site = M.ParkingSite(name="SP synthetic", site_code="SPSYN")
        key = M.PartnerKey(name="socialpay", key_hash=hashlib.sha256(b"k").hexdigest(),
                           key_prefix="k", scopes="read,pay,vehicles",
                           vehicle_webhook_url="http://127.0.0.1:9/n")
        db.add_all([site, key]); db.commit()
        return site.id, key.id


def run_threads(n, fn):
    start = threading.Barrier(n); results = []; errors = []

    def work(i):
        try:
            start.wait(timeout=10)
            results.append(fn(i))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
    workers = [threading.Thread(target=work, args=(i,), daemon=True) for i in range(n)]
    for w in workers: w.start()
    for w in workers: w.join(timeout=30)
    assert not any(w.is_alive() for w in workers)
    assert not errors, errors
    return results


def test_concurrent_register_same_vehicle_once(engine):
    _, kid = seed(engine)

    def reg(_):
        with Session(engine, autoflush=False) as db:
            return PV.register_vehicle(db, kid, "socialpay", "7886УКР", "100264", via="test")
    results = run_threads(8, reg)
    assert sorted(results).count("OK") == 1 and results.count("ALREADY_REGISTERED") == 7
    with Session(engine) as db:
        assert db.query(M.PartnerVehicle).filter_by(deleted_at=None).count() == 1
        # partial unique: идэвхтэй давхардал DB түвшинд хориотой, устгасных зөвшөөрөгдөнө
        db.add(M.PartnerVehicle(partner_key_id=kid, plate_number="7886УКР", external_user_id="100264"))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        assert PV.delete_vehicle(db, kid, "socialpay", "7886УКР", "100264") == "OK"
        assert PV.register_vehicle(db, kid, "socialpay", "7886УКР", "100264") == "OK"


def test_concurrent_registers_respect_per_user_limit(engine, monkeypatch):
    _, kid = seed(engine)
    monkeypatch.setattr(settings, "partner_vehicle_max_per_user", 3)

    def reg(i):
        with Session(engine, autoflush=False) as db:
            try:
                return PV.register_vehicle(db, kid, "socialpay", f"10{i:02d}УБА", "u1")
            except PV.VehicleInputError as exc:
                return exc.code
    results = run_threads(8, reg)
    assert results.count("OK") == 3 and results.count("LIMIT_EXCEEDED") == 5
    with Session(engine) as db:
        assert db.query(M.PartnerVehicle).filter_by(external_user_id="u1", deleted_at=None).count() == 3


def test_scan_sql_and_skip_locked_claims_on_postgres(engine, monkeypatch):
    site_id, kid = seed(engine)
    monkeypatch.setattr(settings, "partner_vehicle_settle_sec", 0)
    with Session(engine) as db:
        for i in range(6):
            PV.register_vehicle(db, kid, "socialpay", f"20{i:02d}УБА", f"user{i}")
        now = datetime.utcnow()
        for i in range(6):
            # бүртгэлийн ДАРАА бүртгэгдсэн айлчлал (орсон цаг нь 30 мин өмнө ч)
            db.add(M.ParkingSession(site_id=site_id, plate_number=f"20{i:02d}УБА", status="OPEN",
                                    entry_time=now - timedelta(minutes=30), created_at=now))
        db.commit()
        assert PV.scan_once(db, now) == {"entry": 6, "exit": 0}
        s = db.query(M.ParkingSession).filter_by(plate_number="2000УБА").one()
        s.status, s.exit_time, s.exit_confirmed = "FREE", now - timedelta(minutes=1), True
        db.commit()
        assert PV.scan_once(db, now)["exit"] == 1
        assert PV.scan_once(db, now) == {"entry": 0, "exit": 0}

    def claim(_):
        with Session(engine, autoflush=False) as db:
            return [c["id"] for c in PV.claim_batch(db, limit=4)]
    got = run_threads(2, claim)
    flat = [x for batch in got for x in batch]
    # 2000УБА-ийн EXIT нь ENTRY-гээ хүлээнэ → эхний ээлжинд 6 ENTRY л
    assert len(flat) == len(set(flat)) == 6
    with Session(engine) as db:
        assert db.query(M.PartnerVehicleEvent).filter_by(status="SENDING").count() == 6


def test_new_migration_statements_apply_on_existing_schema(engine):
    """Шинэ мэдэгдлүүд ledger-гүй (хуучин) схем дээр давтагдахад алдаагүй."""
    migrations.run_migrations()
    with engine.begin() as c:
        c.execute(text("DROP TABLE partner_vehicle_events"))
        c.execute(text("DROP TABLE partner_vehicles"))
        c.execute(text("ALTER TABLE partner_keys DROP COLUMN vehicle_webhook_url, "
                       "DROP COLUMN vehicle_webhook_format"))
        c.execute(text("DELETE FROM parking_schema_migrations"))
    migrations.run_migrations()
    assert migrations.check_ready()["database"] == "ok"
