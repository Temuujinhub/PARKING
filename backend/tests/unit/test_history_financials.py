"""History must distinguish exit evidence, closure, and independently settled debt."""
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import models as M
from app.database import Base
from app.routers import sessions_router as R
from app.services import auto_close, app_settings as A
from app.services.history_financials import attach_history_financials, payment_allocation
from test_payment_wait_regressions import parking


@pytest.fixture
def db():
    engine = create_engine('sqlite://', poolclass=StaticPool)
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


def history(db, user, **kwargs):
    return R.list_sessions(db=db, user=user, **kwargs)


@pytest.fixture
def incident(db):
    old, _, _ = parking(db, datetime(2026, 9, 24, 15, 55))
    old.status = 'MANUAL_CLOSED'
    old.total_fee = 25000
    old.exit_confirmed = True
    camera = M.Device(site_id=old.site_id, name='Exit', device_type='camera', lane_dir='exit')
    db.add(camera); db.flush()
    old.exit_device_id = camera.id
    old.confidence_exit = 90
    new = M.ParkingSession(site_id=old.site_id, plate_number=old.plate_number,
                           entry_time=datetime(2026, 9, 29, 8, 13), status='CLOSED', total_fee=5000)
    db.add(new); db.flush()
    debt = M.Compensation(session_id=old.id, site_id=old.site_id, plate_number=old.plate_number,
                          amount=25000, status='PAID', reason='unpaid_exit',
                          paid_at=datetime(2026, 9, 29, 10, 43))
    db.add(debt); db.flush()
    pay = M.Payment(session_id=new.id, provider='QPAY', payment_method='QR',
                     sender_invoice_no='history-test', amount=30000, status='PAID',
                     paid_at=debt.paid_at, raw_payload={'secret': 'never-return'},
                     fee_snapshot={'parking_amount': 5000, 'debts': [{'id': debt.id, 'amount': 25000}]})
    db.add(pay); db.flush(); debt.payment_id = pay.id
    db.add(M.AuditLog(username='system', action='AUTO_CLOSE', entity='session', entity_id=old.id,
                     created_at=datetime(2026, 9, 24, 17, 56),
                     detail={'reason': 'unpaid_exit', 'hours': 72, 'debt': 25000}))
    db.commit()
    return old, new, debt, pay


def test_old_stay_debt_settled_by_later_qr_without_reopening_or_repricing(db, incident):
    old, new, debt, pay = incident
    before = (old.status, old.total_fee, old.paid_at, debt.status, pay.amount)
    result = history(db, M.User(role='SUPER_ADMIN'))
    rows = {r['id']: r for r in result['rows']}
    assert rows[old.id]['exit_read_status'] == 'CAMERA_READ'
    assert rows[old.id]['status'] == 'MANUAL_CLOSED'
    assert rows[old.id]['payments'] == []
    assert rows[old.id]['session_debts'][0]['payment']['session_id'] == new.id
    assert rows[old.id]['session_debts'][0]['payment']['id'] == pay.id
    assert rows[new.id]['payments'][0]['allocation'] == {
        'known': True, 'parking_amount': 5000, 'previous_debt_amount': 25000}
    assert rows[old.id]['closed_by']['reason_label'] == 'Төлбөр хүлээх хугацаа дууссан'
    # The old incorrect hours field is not rewritten or advertised as a 72h timeout.
    assert rows[old.id]['closed_by']['threshold_hours'] is None
    assert db.query(M.AuditLog).one().detail['hours'] == 72
    assert (old.status, old.total_fee, old.paid_at, debt.status, pay.amount) == before
    assert not db.new and not db.dirty and not db.deleted
    assert 'never-return' not in str(result)


@pytest.mark.parametrize('status', ['PENDING', 'CANCELLED'])
def test_invoice_link_does_not_claim_debt_settled(db, incident, status):
    old, _, debt, _ = incident
    debt.status = status; debt.paid_at = None; db.commit()
    row = history(db, M.User(role='SUPER_ADMIN'), session_id=UUID(old.id))['rows'][0]
    assert row['session_debts'][0]['status'] == status
    assert row['session_debts'][0]['payment'] is None


def test_cross_site_paid_debt_does_not_expose_other_sites_payment(db, incident):
    old, new, _, pay = incident
    other = M.ParkingSite(name='Private', site_code='PRIVATE')
    db.add(other); db.flush(); new.site_id = other.id
    # Even a wrong legacy payment site cannot override its real stay's site.
    pay.site_id = old.site_id; db.commit()
    user = M.User(role='ADMIN', site_ids=[old.site_id])
    row = history(db, user, session_id=UUID(old.id))['rows'][0]
    assert row['session_debts'][0]['status'] == 'PAID'
    assert row['session_debts'][0]['payment'] is None
    assert history(db, user, session_id=UUID(new.id)) == {'total': 0, 'rows': []}


def test_standalone_debt_payment_is_shown_without_invented_session(db, incident):
    old, _, _, pay = incident
    pay.session_id = None; pay.site_id = old.site_id; db.commit()
    row = history(db, M.User(role='ADMIN', site_ids=[old.site_id]), session_id=UUID(old.id))['rows'][0]
    assert row['session_debts'][0]['payment']['session_id'] is None


@pytest.mark.parametrize('snapshot', [None, {}, {'parking_amount': 30000},
    {'parking_amount': 5000, 'debts': [{'id': 'a', 'amount': 24000}]},
    {'parking_amount': 0, 'debts': [{'id': 'a', 'amount': 15000}, {'id': 'a', 'amount': 15000}]},
    {'parking_amount': 'NaN', 'debts': []}, {'parking_amount': -1, 'debts': []},
    {'parking_amount': 0, 'debts': [None]}, {'parking_amount': 0, 'debts': [{'id': 'a'}]}])
def test_missing_or_inconsistent_snapshot_never_guesses_a_split(snapshot):
    assert payment_allocation(30000, snapshot) == {
        'known': False, 'parking_amount': None, 'previous_debt_amount': None}


def test_decimal_amounts_reconcile_exactly():
    assert payment_allocation(Decimal('1.23'), {'parking_amount': '0.23',
        'debts': [{'id': 'one', 'amount': '1.00'}]})['known'] is True
    assert payment_allocation('0.001', {'parking_amount': '0.001', 'debts': []})['known'] is False
    assert payment_allocation(0, {'parking_amount': 0, 'debts': []})['known'] is True


def test_projection_uses_two_queries_and_no_writes_for_many_rows(db, incident):
    old, new, _, _ = incident
    rows = [{'id': old.id}, {'id': new.id}] * 50
    statements = []
    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    event.listen(db.bind, 'before_cursor_execute', record)
    try:
        attach_history_financials(db, rows, M.User(role='SUPER_ADMIN'))
    finally:
        event.remove(db.bind, 'before_cursor_execute', record)
    assert len(statements) == 2
    assert all(s.lstrip().upper().startswith('SELECT') for s in statements)


@pytest.mark.parametrize('device,confirmed,expected', [
    (None, False, 'NO_CAMERA_READ'), (None, True, 'RECORDED_EXIT'),
    ('removed-device', False, 'CAMERA_LINKED')])
def test_closure_timestamp_is_not_exit_camera_evidence(db, device, confirmed, expected):
    stay, _, _ = parking(db)
    stay.exit_time = datetime.utcnow(); stay.exit_device_id = device
    stay.exit_confirmed = confirmed; stay.status = 'MANUAL_CLOSED'
    assert R._session_out(db, stay)['exit_read_status'] == expected


@pytest.mark.parametrize('hours', [2, 4])
def test_awaiting_audit_records_actual_site_timeout_not_72(db, monkeypatch, hours):
    start = datetime.utcnow() - timedelta(hours=hours, seconds=1)
    stay, _, _ = parking(db, start)
    stay.site.auto_close_hours = 72
    A.set_site_rules(db, A.AUTOCLOSE_KEY, stay.site_id, {'awaiting_hours': hours}, 'admin')
    db.commit()
    monkeypatch.setattr(auto_close, 'SessionLocal', lambda: Session(db.bind, autoflush=False))
    assert auto_close.run_once() == 1
    log = db.query(M.AuditLog).filter_by(action='AUTO_CLOSE').one()
    assert log.detail['trigger'] == 'awaiting_timeout'
    assert log.detail['threshold_hours'] == log.detail['hours'] == hours
    assert log.detail['wait_started_at'] == start.isoformat() + 'Z'
    assert datetime.fromisoformat(log.detail['closed_at']).replace(tzinfo=None) >= start + timedelta(hours=hours)


def test_entry_only_72h_is_free_cleanup_with_its_own_trigger(db, monkeypatch):
    stay, _, _ = parking(db, datetime.utcnow() - timedelta(hours=74))
    stay.status = 'OPEN'; stay.site.entry_only_free_hours = 72
    stay.updated_at = datetime.utcnow() - timedelta(hours=2); db.commit()
    monkeypatch.setattr(auto_close, 'SessionLocal', lambda: Session(db.bind, autoflush=False))
    assert auto_close.run_once() == 1
    log = db.query(M.AuditLog).filter_by(action='AUTO_FREE_CLOSE').one()
    assert log.detail['trigger'] == 'entry_only_timeout'
    assert log.detail['threshold_hours'] == 72
    assert log.detail['wait_started_at'] is None
    db.refresh(stay)
    assert stay.status == 'FREE' and stay.total_fee == 0
    assert db.query(M.Compensation).count() == 0


def test_session_link_excel_retains_scope_and_exact_session(db, incident):
    from io import BytesIO
    from openpyxl import load_workbook
    import asyncio
    old, new, _, _ = incident
    response = R.sessions_excel(session_id=UUID(new.id), db=db, user=M.User(role='SUPER_ADMIN'))
    async def read():
        return b''.join([part async for part in response.body_iterator])
    book = load_workbook(BytesIO(asyncio.run(read())), read_only=True)
    values = list(book.active.values)
    matching = [r for r in values if r[0] == new.plate_number]
    assert len(matching) == 1
    assert matching[0][7] == 5000
