"""Never uses the application DSN: requires an explicitly disposable test DSN."""
import os
import threading
import uuid

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session

from app import migrations, models as M
from app.services import wallet as W
from app.routers import wallet_router as WR

URL=os.environ.get('PARKING_TEST_DATABASE_URL')
pytestmark=pytest.mark.skipif(not URL,reason='Disposable PostgreSQL DSN required')


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
