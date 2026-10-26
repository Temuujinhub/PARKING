"""/qpay/check polling-оос хязгаартай банкны шалгалт (2026-10-26).

10-24 22:00 – 10-25 11:30 УБ QPay callback 70–150с хоцорсон; систем зөвхөн
callback-аар таньдаг байсан тул жолооч хаалтан дээр 2 мин хүлээсэн. Нэхэмжлэл
30с-ээс удаан PENDING бол 5 мин хүртэл 15с тутам payment/check дуудна.
Сүлжээ, төхөөрөмж огт дуудахгүй — QPay клиент stub."""
import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

from app import models as M, session_logic as SL
from app.database import Base
from app.services import app_settings as A, payment_wait
from app.routers import payments_router as PR


@pytest.fixture
def db():
    engine = create_engine('sqlite://', poolclass=StaticPool,
                           connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    A.invalidate_cache()
    with Session(engine, autoflush=False) as session:
        yield session
    A.invalidate_cache()
    engine.dispose()


@pytest.fixture
def bank(monkeypatch):
    """Бодит QPay горим (mock биш) + stub payment/check."""
    monkeypatch.setattr(PR.settings, 'qpay_mock', False)
    monkeypatch.setattr(PR.settings, 'qpay_poll_check_after_sec', 30)
    monkeypatch.setattr(PR.settings, 'qpay_poll_check_every_sec', 15)
    monkeypatch.setattr(PR.settings, 'qpay_poll_check_window_sec', 300)
    monkeypatch.setattr(PR.qpay, 'account_for', lambda site: SimpleNamespace(username='TEST_ACC', mock=False))
    check = AsyncMock(return_value={'paid': False, 'paid_amount': 0, 'rows': [], 'payment_id': None, 'raw': {}})
    monkeypatch.setattr(PR.qpay, 'check_payment', check)
    return check


def pending_invoice(db, age_sec):
    now = datetime.utcnow()
    tariff = M.TariffTemplate(name='Hourly', free_minutes=0, extra_hour_price=1000,
                              daily_cap=50000, grace_minutes=15)
    site = M.ParkingSite(name='Test site', site_code='TEST', tariff_template=tariff,
                         auto_close_hours=0, entry_only_free_hours=0)
    db.add(site); db.flush()
    s = M.ParkingSession(site_id=site.id, plate_number='1234УБА',
                         entry_time=now - timedelta(minutes=59), status='AWAITING_PAYMENT')
    db.add(s); db.flush(); db.refresh(s)
    payment_wait.begin_wait(db, s, SL.session_fee_info(db, s, at=now), now)
    p = PR._create_payment(db, s, 'QPAY', 'QR', allow_qpay=True)
    p.provider_invoice_id = 'inv-1'
    p.created_at = now - timedelta(seconds=age_sec)
    db.commit()
    return p


def poll(db, pid):
    request = Request({'type': 'http', 'client': ('poll-test', 1), 'headers': []})
    return asyncio.run(PR.qpay_check(pid, request, db))


def paid_result(amount):
    return {'paid': True, 'paid_amount': float(amount), 'rows': [], 'payment_id': 'bank-tx-1',
            'raw': {'count': 1, 'paid_amount': float(amount)}}


def test_fresh_invoice_reads_stored_status_only(db, bank):
    p = pending_invoice(db, age_sec=10)
    assert poll(db, p.id) == {'status': 'PENDING'}
    bank.assert_not_awaited()


def test_slow_callback_poll_detects_payment_and_reaches_barrier_flow(db, bank):
    p = pending_invoice(db, age_sec=40)
    bank.return_value = paid_result(p.amount)
    out = poll(db, p.id)
    assert out['status'] == 'PAID' and bank.await_count == 1
    db.expire_all(); p = db.get(M.Payment, p.id)
    assert p.status == 'PAID' and p.session.status == 'PAID'
    # Энэ сешнд гарах камер байхгүй — хаалтын шийдвэр ердийн урсгалаар бичигдэнэ
    assert db.query(M.AuditLog).filter_by(action='PAYMENT_EXIT_WAIT', entity_id=p.session_id).count() == 1
    audit = db.query(M.AuditLog).filter_by(action='QPAY_POLL_PAID', entity_id=p.id).one()
    assert audit.detail['invoice_age_sec'] >= 40


def test_bank_checked_at_most_every_15_seconds(db, bank):
    p = pending_invoice(db, age_sec=40)
    for _ in range(3):
        assert poll(db, p.id) == {'status': 'PENDING'}
    assert bank.await_count == 1
    db.query(M.Payment).filter_by(id=p.id).update(
        {M.Payment.qpay_last_check_at: datetime.utcnow() - timedelta(seconds=16)})
    db.commit()
    poll(db, p.id)
    assert bank.await_count == 2


def test_no_bank_check_after_five_minutes(db, bank):
    p = pending_invoice(db, age_sec=301)
    assert poll(db, p.id) == {'status': 'PENDING'}
    bank.assert_not_awaited()


def test_bank_error_keeps_polling_page_alive(db, bank):
    p = pending_invoice(db, age_sec=40)
    bank.side_effect = TimeoutError('qpay slow')
    assert poll(db, p.id) == {'status': 'PENDING'}
    db.expire_all()
    assert db.get(M.Payment, p.id).qpay_last_check_at is not None  # 15с дараа дахин оролдоно


@pytest.mark.parametrize('setting,value', [('qpay_mock', True), ('qpay_poll_check_every_sec', 0)])
def test_mock_or_disabled_never_calls_bank(db, bank, monkeypatch, setting, value):
    p = pending_invoice(db, age_sec=40)
    monkeypatch.setattr(PR.settings, setting, value)
    assert poll(db, p.id)['status'] == 'PENDING'
    bank.assert_not_awaited()


def test_only_parking_qpay_invoices_are_checked(db, bank):
    p = pending_invoice(db, age_sec=40)
    p.kind = 'WALLET_TOPUP'; db.commit()
    poll(db, p.id)
    p.kind = 'PARKING'; p.provider_invoice_id = None; db.commit()
    poll(db, p.id)
    bank.assert_not_awaited()


def test_payment_after_callback_is_not_attributed_to_poll(db, bank):
    p = pending_invoice(db, age_sec=40)
    p.qpay_next_check_at = datetime.utcnow() + timedelta(minutes=2)  # callback ирсэн тэмдэг
    db.commit()
    bank.return_value = paid_result(p.amount)
    assert poll(db, p.id)['status'] == 'PAID'
    assert db.query(M.AuditLog).filter_by(action='QPAY_POLL_PAID').count() == 0
