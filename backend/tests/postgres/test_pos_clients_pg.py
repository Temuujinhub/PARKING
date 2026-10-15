"""Disposable PostgreSQL only (PARKING_TEST_DATABASE_URL): «Холбогдсон POS»-ын migration,
ON CONFLICT upsert (CASE/өмнөх хувилбар), зэрэг бичилтэд тоолуур алдагдахгүй — жинхэнэ PG дээр.

    PARKING_TEST_DATABASE_URL=postgresql+psycopg2://…/t_integ \
        venv/bin/python -m pytest tests/postgres/test_pos_clients_pg.py -q
"""
import threading
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from test_payment_migrations import engine, URL  # noqa: F401 — fixture
from app import migrations, models as M
from app.services import pos_clients as PC

pytestmark = pytest.mark.skipif(not URL, reason="Disposable PostgreSQL DSN required")


def snap(uid, name, tid='TDB-PAX-SITE01-01', **kw):
    now = datetime.utcnow()
    base = {"user_id": uid, "username": name, "terminal_id": tid, "terminal_source": "header",
            "requests": 1, "errors": 0, "payments": 0, "heartbeats": 0, "first_seen": now,
            "last_seen": now, "version": "1.5.3", "status": None, "status_at": None, "hb": None,
            "ip": "10.0.0.5", "ua": "Dart/3.4", "endpoint": "GET /api/payments", "status_code": 200,
            "last_payment": None, "site_hint": None}
    base.update(kw)
    return base


def test_migration_and_upsert_semantics(engine):
    migrations.run_migrations()
    assert migrations.check_ready()["database"] == "ok"
    with engine.connect() as c:
        n = c.execute(text("SELECT count(*) FROM parking_schema_migrations")).scalar()
        idx = {r[0] for r in c.execute(text(
            "SELECT indexname FROM pg_indexes WHERE tablename='pos_clients'"))}
    assert "uq_pos_clients_user_terminal" in idx
    migrations.run_migrations()           # дахин ажиллуулахад ledger өсөхгүй
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM parking_schema_migrations")).scalar() == n

    factory = sessionmaker(bind=engine, autoflush=False)
    with Session(engine) as db:
        site = M.ParkingSite(name="PG POS", site_code="PGPOS")
        db.add(site); db.flush()
        u = M.User(username="pos.pg", role="POS", password_hash="x", site_ids=[site.id])
        dev = M.Device(site_id=site.id, name="PAX 1", device_type="pax_terminal",
                       device_key="91186239", status="active")
        db.add_all([u, dev]); db.commit()
        uid, sid = u.id, site.id

    with factory() as db:
        assert PC._write(db, snap(uid, "pos.pg", tid="91186239", version="1.5.1"))
        db.commit()
        assert PC._write(db, snap(uid, "pos.pg", tid="91186239", version="1.5.3", requests=4,
                                  status="PRINTER_ERROR", status_at=datetime.utcnow(),
                                  hb={"printer": "PAPER_OUT", "battery": 40, "network": "4g",
                                      "extra": {"sn": "A9"}},
                                  last_payment=datetime.utcnow(), payments=1))
        db.commit()
        # хуучин last_seen-тэй (дараалал алдагдсан) бичилт сүүлийн цагийг ухраахгүй
        assert PC._write(db, snap(uid, "pos.pg", tid="91186239", version=None,
                                  last_seen=datetime.utcnow() - timedelta(hours=1)))
        db.commit()
        r = db.query(M.PosClient).one()
        assert (r.pos_version, r.prev_pos_version, r.request_count, r.payment_count) == \
            ("1.5.3", "1.5.1", 6, 1)
        assert r.version_changed_at is not None and r.status == "PRINTER_ERROR"
        assert (r.printer, r.battery, r.extra) == ("PAPER_OUT", 40, {"sn": "A9"})
        assert r.site_id == sid           # бүртгэлтэй терминалын зогсоол
        assert r.last_seen_at > datetime.utcnow() - timedelta(minutes=5)

    # Зэрэг бичилт (2 процесс/thread) — нэг мөр, тоолуур нийлбэрээрээ
    start = threading.Barrier(6)
    errors = []

    def work():
        try:
            with factory() as db:
                start.wait(timeout=10)
                PC._write(db, snap(uid, "pos.pg", tid="NEW-1", requests=3))
                db.commit()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
    ts = [threading.Thread(target=work, daemon=True) for _ in range(6)]
    for t in ts: t.start()
    for t in ts: t.join(timeout=30)
    assert not errors, errors
    with factory() as db:
        r = db.query(M.PosClient).filter_by(terminal_id="NEW-1").one()
        assert r.request_count == 18


def test_flush_due_end_to_end_on_pg(engine, monkeypatch):
    migrations.run_migrations()
    factory = sessionmaker(bind=engine, autoflush=False)
    with Session(engine) as db:
        site = M.ParkingSite(name="PG POS2", site_code="PGPOS2")
        db.add(site); db.flush()
        u = M.User(username="pos.pg2", role="OPERATOR", password_hash="x", site_ids=[site.id])
        db.add(u); db.commit()
        uid = u.id
    PC.reset_state()
    try:
        for _ in range(3):
            PC.record(user_id=uid, username="pos.pg2", terminal_id="T-9", terminal_source="header",
                      version="1.5.3", status=None, ip="10.1.1.1", ua="okhttp", endpoint="GET x",
                      status_code=200)
        PC.record(user_id=uid, username="pos.pg2", terminal_id=None, terminal_source=None,
                  version="1.5.3", status=None, ip="10.1.1.1", ua="okhttp", endpoint="GET y",
                  status_code=404)
        assert PC.flush_due(session_factory=factory) == 1
        with factory() as db:
            r = db.query(M.PosClient).one()
            assert (r.terminal_id, r.request_count, r.error_count) == ("T-9", 4, 1)
    finally:
        PC.reset_state()
