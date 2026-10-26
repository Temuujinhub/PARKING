"""Кассын «Өртэй тест машин» (TEST горим, 2026-10-26): камергүй тест машинд
өмнөх төлөгдөөгүй зогсолт + PENDING өр үүсгэнэ. Production-д хаалттай."""
import asyncio
from datetime import datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import models as M
from app.config import settings
from app.database import Base
from app.services import app_settings as A
from app.routers import payments_router as PR, sessions_router as SR


@pytest.fixture
def db():
    engine = create_engine('sqlite://', poolclass=StaticPool,
                           connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    # The model's PostgreSQL-only predicate otherwise becomes an unconditional
    # SQLite index. Match the real partial index for historical same-plate stays.
    with engine.begin() as conn:
        conn.execute(text('DROP INDEX uq_active_session'))
        conn.execute(text("CREATE UNIQUE INDEX uq_active_session ON parking_sessions "
                          "(site_id, plate_number) WHERE status IN ('OPEN','AWAITING_PAYMENT','PAID')"))
    A.invalidate_cache()
    with Session(engine, autoflush=False) as session:
        yield session
    A.invalidate_cache()
    engine.dispose()


@pytest.fixture
def site_user(db, monkeypatch):
    monkeypatch.setattr(settings, 'allow_simulate', True)
    tariff = M.TariffTemplate(name='Hourly', free_minutes=0, extra_hour_price=1000,
                              daily_cap=50000, grace_minutes=15)
    site = M.ParkingSite(name='Test site', site_code='SITE01', tariff_template=tariff,
                         auto_close_hours=0, entry_only_free_hours=0)
    db.add(site); db.flush()
    user = M.User(username='test_operator', password_hash='unused', role='OPERATOR', site_id=site.id)
    db.add(user); db.commit()
    return site, user


def add(db, user, body):
    return asyncio.run(SR.test_awaiting(body, db, user))


def test_debt_car_has_pending_debts_visible_to_cashier(db, site_user):
    site, user = site_user
    out = add(db, user, {'site_id': site.id, 'debts': 2, 'debt_amount': 3000})
    assert out['status'] == 'AWAITING_PAYMENT' and out['debt_total'] == 6000
    assert len(out['test_debts']) == 2
    comps = db.query(M.Compensation).filter_by(plate_number=out['plate_number']).all()
    assert sorted((c.status, float(c.amount), c.reason) for c in comps) == [
        ('PENDING', 3000.0, 'unpaid_exit'), ('PENDING', 3000.0, 'unpaid_exit')]
    prev = [db.get(M.ParkingSession, c.session_id) for c in comps]
    assert all(p.status == 'MANUAL_CLOSED' and p.exit_time < datetime.utcnow() for p in prev)
    # Касс/QR-ийн өр нийлүүлэх хайлт эдгээрийг олно
    assert {c.id for c in PR._pending_debts(db, out['plate_number'], site)} == {d['id'] for d in out['test_debts']}


def test_plain_test_car_has_no_debt(db, site_user):
    site, user = site_user
    out = add(db, user, {'site_id': site.id})
    assert out['debt_total'] == 0 and out['test_debts'] == []
    assert db.query(M.Compensation).count() == 0


def test_debt_count_is_capped(db, site_user):
    site, user = site_user
    assert len(add(db, user, {'site_id': site.id, 'debts': 9})['test_debts']) == 3


def test_disabled_outside_test_mode(db, site_user, monkeypatch):
    site, user = site_user
    monkeypatch.setattr(settings, 'allow_simulate', False)
    with pytest.raises(HTTPException) as err:
        add(db, user, {'site_id': site.id, 'debts': 2})
    assert err.value.status_code == 403
    assert db.query(M.Compensation).count() == 0
