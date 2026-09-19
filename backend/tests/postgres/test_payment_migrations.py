"""Never uses the application DSN: requires an explicitly disposable test DSN."""
import os
import threading
import uuid
from datetime import datetime

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session

from app import migrations, models as M
from app.services import wallet as W
from app.routers import wallet_router as WR
from app.database import Base

URL=os.environ.get('PARKING_TEST_DATABASE_URL')
pytestmark=pytest.mark.skipif(not URL,reason='Disposable PostgreSQL DSN required')


def test_concurrent_inner_recovery_credits_once_and_live_lock_handles_joins(engine, monkeypatch):
    import asyncio
    from datetime import timedelta
    from app import session_logic as SL
    from app.services import camera_sync as CS
    migrations.run_migrations()
    when = datetime.utcnow() - timedelta(hours=12)
    with Session(engine) as db:
        site = M.ParkingSite(name='Synthetic inner',site_code='INNER',transit_max_hours=12)
        db.add(site);db.flush()
        cams = [M.Device(site_id=site.id,name=direction,device_type='camera',
                         device_key='inner-'+direction,nested_inner=True,
                         lane_dir=direction,lane_no=lane,status='active')
                for direction,lane in [('entry',4),('exit',3)]]
        stay = M.ParkingSession(site_id=site.id,plate_number='1234УБА',
                                entry_time=when,status='OPEN')
        db.add_all([stay,*cams]);db.commit()
        sid,site_id = stay.id,site.id
        records = [{'device_id':c.id,'plate':stay.plate_number,'lane_dir':c.lane_dir,
                    'event':'gate_pass','time':when+timedelta(minutes=1,hours=i*10)}
                   for i,c in enumerate(cams)]
        entry_camera_id = cams[0].id
    ready = threading.Barrier(2);errors=[];results=[]
    def recover():
        try:
            with Session(engine,autoflush=False) as db:
                site=db.get(M.ParkingSite,site_id)
                original=db.get(M.ParkingSession,sid)
                ready.wait(timeout=10)
                results.append(CS._sync_inner(db,site,{'inner_events':records},when,
                    when+timedelta(hours=12),{'skip_invalid_plate':True},False))
        except Exception as exc: errors.append(exc)
    workers=[threading.Thread(target=recover,daemon=True) for _ in range(2)]
    for worker in workers: worker.start()
    for worker in workers: worker.join(timeout=20)
    assert not any(worker.is_alive() for worker in workers)
    assert not errors,errors
    assert tuple(map(sum,zip(*results))) == (1,1)
    with Session(engine) as db:
        stay=db.get(M.ParkingSession,sid)
        assert stay.paused_since is None and stay.paused_minutes==600
        assert db.query(M.LprEvent).count()==2
        assert db.query(M.AuditLog).filter_by(action='CAMERA_SYNC_INNER').count()==2
        monkeypatch.setattr(SL,'notify',lambda *a: None)
        out=asyncio.run(SL.handle_inner_pass(db,db.get(M.Device,entry_camera_id),
                                            stay.plate_number,99,{},allow_open=False))
        assert out['counter_changed'] is True and out['barrier_opened'] is False
        assert stay.paused_since is not None


@pytest.fixture
def engine(monkeypatch):
    schema='audit_'+uuid.uuid4().hex
    admin=create_engine(URL)
    with admin.begin() as c: c.execute(text(f'CREATE SCHEMA {schema}'))
    engine=create_engine(URL,connect_args={'options':f'-csearch_path={schema}'})
    monkeypatch.setattr(migrations,'engine',engine)
    try:
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as c: c.execute(text(f'DROP SCHEMA {schema} CASCADE'))
        admin.dispose()


def test_versioned_migration_replay_and_readiness(engine):
    migrations.run_migrations()
    assert migrations.check_ready()['database']=='ok'
    with engine.connect() as c:
        count=c.execute(text('SELECT count(*) FROM parking_schema_migrations')).scalar()
    migrations.run_migrations()
    with engine.connect() as c:
        assert count==c.execute(text('SELECT count(*) FROM parking_schema_migrations')).scalar()
    with engine.begin() as c:
        c.execute(text('ALTER TABLE parking_sessions DROP COLUMN payment_quote'))
    with pytest.raises(Exception): migrations.check_ready()
    with pytest.raises(RuntimeError,match='Schema incomplete'): migrations.run_migrations()


def test_topup_outer_join_lock_and_concurrent_idempotency(engine):
    migrations.run_migrations()
    with Session(engine) as db:
        wallet=M.Wallet(plate_number='1234УБА',balance=0)
        db.add(wallet);db.flush()
        payment=M.Payment(kind='WALLET_TOPUP',wallet_id=wallet.id,provider='QPAY',
                          payment_method='QR',sender_invoice_no='test-topup',amount=5000)
        db.add(payment);db.commit();pid=payment.id;wid=wallet.id
    ready=threading.Barrier(2);errors=[]
    def credit():
        try:
            with Session(engine) as db:
                p=db.get(M.Payment,pid)
                ready.wait(timeout=10)
                assert WR._credit_if_paid(db,p)
        except Exception as e: errors.append(e)
    workers=[threading.Thread(target=credit,daemon=True) for _ in range(2)]
    for worker in workers: worker.start()
    for worker in workers: worker.join(timeout=20)
    assert not any(t.is_alive() for t in workers)
    assert not errors,errors
    with Session(engine) as db:
        assert db.get(M.Wallet,wid).balance==5000
        assert db.query(M.WalletLedger).count()==1


def test_preloaded_wallet_locks_see_fresh_balance(engine):
    migrations.run_migrations()
    with Session(engine) as db:
        w=M.Wallet(plate_number='1234УБА',balance=10000);db.add(w);db.commit();wid=w.id
    ready=threading.Barrier(2);errors=[]
    def debit():
        try:
            with Session(engine,autoflush=False) as db:
                original=db.get(M.Wallet,wid)
                ready.wait(timeout=10)
                locked=W.lock_wallet(db,wid)
                W.apply_ledger(db,locked,'DEBIT',1000,'PARKING')
                db.commit()
        except Exception as e: errors.append(e)
    workers=[threading.Thread(target=debit,daemon=True) for _ in range(2)]
    for worker in workers: worker.start()
    for worker in workers: worker.join(timeout=20)
    assert not any(t.is_alive() for t in workers)
    assert not errors,errors
    with Session(engine) as db:
        assert db.get(M.Wallet,wid).balance==8000
        assert db.query(M.WalletLedger).count()==2


def test_upgrade_legacy_rows_backfills_once_without_guessing_company_owner(engine):
    from datetime import datetime, timedelta
    Base.metadata.create_all(engine)
    start=datetime(2026,9,18,10,0)
    with Session(engine) as db:
        site=M.ParkingSite(name='Legacy',site_code='LEGACY')
        db.add(site);db.flush()
        session=M.ParkingSession(site_id=site.id,plate_number='1234УБА',
            entry_time=start-timedelta(hours=1),updated_at=start,status='AWAITING_PAYMENT')
        contact=M.CompanyContact(company='Legacy company')
        db.add_all([session,contact]);db.commit();sid=session.id;cid=contact.id
    # Simulate the schema from before this release, not merely an empty database.
    with engine.begin() as c:
        for col in ['payment_wait_started_at','last_exit_seen_at','payment_quote_until','payment_quote',
                    'entry_snapshot_source','exit_snapshot_source']:
            c.execute(text(f'ALTER TABLE parking_sessions DROP COLUMN {col}'))
        for col in ['fee_snapshot','provider_tx_key','partner_key_id']:
            c.execute(text(f'ALTER TABLE payments DROP COLUMN {col}'))
        c.execute(text('ALTER TABLE company_contacts DROP COLUMN owner_scope CASCADE'))
        c.execute(text('ALTER TABLE company_invoices DROP COLUMN owner_scope CASCADE'))
        c.execute(text('ALTER TABLE company_contacts ADD CONSTRAINT company_contacts_company_key UNIQUE (company)'))
        c.execute(text('ALTER TABLE company_invoices ADD CONSTRAINT uq_invoice_period_company UNIQUE (period,company)'))
    migrations.run_migrations()
    with Session(engine) as db:
        session=db.get(M.ParkingSession,sid)
        assert session.payment_wait_started_at==start
        assert session.last_exit_seen_at==start
        assert session.payment_quote is None
        assert db.get(M.CompanyContact,cid).owner_scope=='LEGACY'
        session.note='new metadata';db.commit()
    migrations.run_migrations()
    with Session(engine) as db:
        assert db.get(M.ParkingSession,sid).payment_wait_started_at==start
        assert db.get(M.CompanyContact,cid).owner_scope=='LEGACY'


def test_late_payment_locks_debt_without_outer_join_error(engine,monkeypatch):
    import asyncio
    from datetime import datetime
    from unittest.mock import AsyncMock
    from app.routers import payments_router as PR
    from app.config import settings
    migrations.run_migrations()
    monkeypatch.setattr(settings,'ebarimt_mock',True)
    monkeypatch.setattr(settings,'ebarimt_mock_receipts',False)
    monkeypatch.setattr(settings,'qpay_ebarimt',False)
    monkeypatch.setattr(PR.msgbill,'account_enabled_for',lambda *args:None)
    gate=AsyncMock();monkeypatch.setattr(PR,'mark_paid_and_open',gate)
    with Session(engine,autoflush=False) as db:
        site=M.ParkingSite(name='Late payment',site_code='LATE')
        db.add(site);db.flush()
        session=M.ParkingSession(site_id=site.id,plate_number='1234УБА',
            entry_time=datetime.utcnow(),status='MANUAL_CLOSED',total_fee=1000)
        db.add(session);db.flush()
        debt=M.Compensation(site_id=site.id,session_id=session.id,
            plate_number=session.plate_number,amount=1000,reason='unpaid_exit')
        payment=M.Payment(session_id=session.id,provider='QPAY',payment_method='QR',
            sender_invoice_no='late-test',amount=1000,vat_amount=91,
            fee_snapshot={'parking_amount':1000,'debts':[]})
        db.add_all([debt,payment]);db.commit()
        asyncio.run(PR._finalize_paid(db,payment));db.commit()
        assert debt.status=='PAID'
        assert payment.status=='PAID'
        assert session.status=='MANUAL_CLOSED'
        gate.assert_not_awaited()

def test_provider_transaction_unique_across_concurrent_payments(engine):
    from fastapi import HTTPException
    from app.services.payment_validation import claim_reference
    migrations.run_migrations()
    with Session(engine) as db:
        rows=[M.Payment(provider='POS',payment_method='CARD',sender_invoice_no='tx-'+str(i),amount=1000) for i in range(2)]
        db.add_all(rows);db.commit();ids=[p.id for p in rows]
    ready=threading.Barrier(2);outcomes=[]
    def claim(pid):
        with Session(engine) as db:
            p=db.get(M.Payment,pid);ready.wait(timeout=10)
            try:
                claim_reference(db,p,'terminal:test','same-bank-transaction')
                p.status='PAID';db.commit();outcomes.append('PAID')
            except HTTPException as exc:
                outcomes.append(exc.status_code)
    workers=[threading.Thread(target=claim,args=(pid,),daemon=True) for pid in ids]
    for t in workers:t.start()
    for t in workers:t.join(timeout=20)
    assert sorted(map(str,outcomes))==['409','PAID']
    with Session(engine) as db:
        assert db.query(M.Payment).filter_by(status='PAID').count()==1


def test_checkout_session_reservation_is_nowait(engine):
    from fastapi import HTTPException
    from app.services.checkout import lock_session
    migrations.run_migrations()
    with Session(engine) as db:
        site=M.ParkingSite(name='Concurrent',site_code='CONCURRENT');db.add(site);db.flush()
        s=M.ParkingSession(site_id=site.id,plate_number='1234УБА',entry_time=datetime.utcnow());db.add(s);db.commit();sid=s.id
    with Session(engine) as first, Session(engine) as second:
        lock_session(first,sid)
        with pytest.raises(HTTPException) as error:lock_session(second,sid)
        assert error.value.status_code==409
        first.rollback()
        assert lock_session(second,sid).id==sid


def test_snapshot_cas_rejects_stale_writer(engine,monkeypatch):
    from app.services.snapshot import attach_saved
    migrations.run_migrations()
    with Session(engine) as db:
        site=M.ParkingSite(name='Pictures',site_code='PICTURES');db.add(site);db.flush()
        s=M.ParkingSession(site_id=site.id,plate_number='1234УБА',entry_time=datetime.utcnow());db.add(s);db.commit();sid=s.id
    with Session(engine) as first, Session(engine) as second:
        a=first.get(M.ParkingSession,sid);b=second.get(M.ParkingSession,sid)
        assert attach_saved(first,a,'entry','one.jpg','comet')
        assert not attach_saved(second,b,'entry','two.jpg','ws')
        second.rollback();second.refresh(b)
        assert b.entry_snapshot=='one.jpg'


def test_preboot_gate_recovery_serializes_with_executor_and_keeps_audit_atomic(engine):
    from datetime import timedelta
    from sqlalchemy.exc import OperationalError
    from app.services.gate_recovery import recover_preboot_command
    migrations.run_migrations()
    now=datetime.utcnow();boot=now-timedelta(minutes=1)
    with Session(engine) as db:
        site=M.ParkingSite(name='Recovery',site_code='RECOVERY');db.add(site);db.flush()
        gate=M.Device(site_id=site.id,name='Gate',device_type='barrier',status='active')
        stay=M.ParkingSession(site_id=site.id,plate_number='1234УБА',entry_time=boot,status='OPEN')
        db.add_all([gate,stay]);db.flush()
        cmd=M.BarrierCommand(device_id=gate.id,session_id=stay.id,command='open',
            command_source='auto_entry',status='PENDING',created_at=boot-timedelta(seconds=10))
        db.add(cmd);db.commit();did,cid=gate.id,cmd.id
    with Session(engine) as executor, Session(engine) as recovery:
        # Same lock held by the actual gate executor; recovery must not steal it.
        executor.query(M.Device.id).filter_by(id=did).with_for_update().one()
        saved=[]
        with pytest.raises(OperationalError):
            recover_preboot_command(recovery,cid,boot,saved.append,now=now)
        recovery.rollback();assert not saved
        executor.rollback()
        assert recover_preboot_command(recovery,cid,boot,saved.append,now=now)['changed']
        # PostgreSQL must lock only the base rows, not nullable eager joins.
        recovery.rollback()
        assert recovery.get(M.BarrierCommand,cid).status=='PENDING'
        assert recovery.query(M.AuditLog).count()==0
        assert recover_preboot_command(recovery,cid,boot,saved.append,now=now)['changed']
        recovery.commit()
    with Session(engine) as db:
        cmd=db.get(M.BarrierCommand,cid)
        assert cmd.status=='UNKNOWN' and cmd.executed_at is None
        assert db.query(M.AuditLog).filter_by(action='BARRIER_PREBOOT_RECOVERY').count()==1
        assert not recover_preboot_command(db,cid,boot,saved.append,now=now)['changed']
