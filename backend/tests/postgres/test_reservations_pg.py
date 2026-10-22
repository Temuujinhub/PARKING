"""Disposable PostgreSQL only (PARKING_TEST_DATABASE_URL): захиалгын байрны уралдаан
(advisory lock), confirm/cancel-ийн мөрийн түгжээ жинхэнэ PG дээр.

    PARKING_TEST_DATABASE_URL=postgresql+psycopg2://…/t_x \\
        venv/bin/python -m pytest tests/postgres/test_reservations_pg.py -q
"""
import asyncio
import hashlib
import threading
from datetime import datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from test_payment_migrations import engine, URL  # noqa: F401 — fixture
from app import migrations, models as M
from app.config import settings
from app.routers import integration_router as IR
from app.services import app_settings as A, financial_jobs as FJ, reservations as R

pytestmark = pytest.mark.skipif(not URL, reason="Disposable PostgreSQL DSN required")


def seed(engine, spots=1):
    migrations.run_migrations()
    A.invalidate_cache()
    with Session(engine) as db:
        tariff = M.TariffTemplate(name="Hourly", free_minutes=0, extra_hour_price=1000)
        site = M.ParkingSite(name="PG rsv", site_code="PGRSV", tariff_template=tariff)
        key = M.PartnerKey(name="mobifinance", key_hash=hashlib.sha256(b"k").hexdigest(),
                           key_prefix="k", scopes="read,pay")
        db.add_all([site, key]); db.commit()
        A.set_site_rules(db, A.RESERVATION_KEY, site.id, {"enabled": True, "spots": spots}, "t")
        db.commit()
        return site.id, key.id


def partner(kid):
    p = IR.PartnerAuth("mobifinance"); p.scopes = "read,pay"; p.key_id = kid
    return p


def test_last_spot_is_sold_once_under_concurrency(engine, monkeypatch):
    monkeypatch.setattr(settings, "ebarimt_mock_receipts", False)
    _, kid = seed(engine, spots=1)
    start = (datetime.utcnow() + timedelta(hours=2)).replace(microsecond=0)
    n = 6
    gate = threading.Barrier(n)
    results, errors = [], []

    def book(i):
        try:
            with Session(engine, autoflush=False) as db:
                gate.wait(timeout=10)
                try:
                    R.create(db, partner(kid), {"site_code": "PGRSV", "plate_number": f"10{i:02d}УБА",
                                                "start_time": start.isoformat() + "Z",
                                                "duration_minutes": 60})
                    db.commit()
                    results.append("OK")
                except R.ReservationError as exc:
                    db.rollback()
                    results.append(exc.code)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
    threads = [threading.Thread(target=book, args=(i,), daemon=True) for i in range(n)]
    for t in threads: t.start()
    for t in threads: t.join(timeout=30)
    assert not errors, errors
    assert results.count("OK") == 1 and results.count("NO_AVAILABILITY") == n - 1, results


def test_confirm_cancel_on_postgres(engine, monkeypatch):
    monkeypatch.setattr(settings, "ebarimt_mock_receipts", False)
    monkeypatch.setattr(FJ.msgbill, "account_enabled_for", lambda *a: None)
    _, kid = seed(engine, spots=2)
    with Session(engine, autoflush=False) as db:
        start = datetime.utcnow() + timedelta(hours=3)
        r, _ = R.create(db, partner(kid), {"site_code": "PGRSV", "plate_number": "7886УКР",
                                           "start_time": start.isoformat() + "Z", "duration_minutes": 90})
        db.commit()
        r = asyncio.run(R.confirm(db, partner(kid), r, {"transaction_id": 12345, "amount": 2000}))
        assert r.status == "CONFIRMED"
        r = asyncio.run(R.cancel(db, partner(kid), r, {}))
        assert r.status == "CANCELLED" and float(r.refund_amount) == 2000.0
        assert db.get(M.Payment, r.payment_id).status == "REFUNDED"
        assert R.sweep(db) == {"expired": 0, "no_show": 0, "completed": 0}
