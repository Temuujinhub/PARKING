"""Real PostgreSQL contention and additive upgrade tests; disposable DSN only."""
import asyncio
import threading
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from test_payment_migrations import engine, URL
from app import migrations, models as M
from app.routers import compensations_router as C
from app.services import qpay_recheck as Q, financial_jobs as J, wallet_topup as W

pytestmark=pytest.mark.skipif(not URL, reason='Disposable PostgreSQL DSN required')


def race(function, count=2):
    ready=threading.Barrier(count);results=[];errors=[]
    def run():
        try:
            ready.wait(timeout=10);results.append(function())
        except Exception as exc: errors.append(exc)
    threads=[threading.Thread(target=run,daemon=True) for _ in range(count)]
    for t in threads:t.start()
    for t in threads:t.join(timeout=20)
    assert not any(t.is_alive() for t in threads)
    assert not errors,errors
    return results


def test_two_cashiers_collect_sessionless_debt_once(engine):
    migrations.run_migrations()
    with Session(engine) as db:
        site=M.ParkingSite(name='Synthetic',site_code='DEBT')
        user=M.User(username='synthetic',password_hash='x',role='SUPER_ADMIN')
        db.add_all([site,user]);db.flush()
        c=M.Compensation(site_id=site.id,plate_number='1234УБА',amount=Decimal('1500.25'))
        db.add(c);db.commit();cid,uid=c.id,user.id
    def collect():
        with Session(engine,autoflush=False) as db:
            return asyncio.run(C.pay_compensation(cid,{},db,db.get(M.User,uid)))
    result=race(collect)
    assert sum(bool(r.get('already_paid')) for r in result)==1
    with Session(engine) as db:
        assert db.query(M.Payment).count()==db.query(M.FinancialJob).count()==db.query(M.VatReceipt).count()==1
        assert db.query(M.Payment).one().amount==Decimal('1500.25')


def test_collect_and_writeoff_cannot_overwrite_each_other(engine):
    migrations.run_migrations()
    with Session(engine) as db:
        site=M.ParkingSite(name='Synthetic',site_code='DEBT')
        user=M.User(username='synthetic',password_hash='x',role='SUPER_ADMIN')
        db.add_all([site,user]);db.flush()
        c=M.Compensation(site_id=site.id,plate_number='1234УБА',amount=1000)
        db.add(c);db.commit();cid,uid=c.id,user.id
    lock=threading.Lock();assigned=[]
    def mutate():
        with lock:
            which=len(assigned);assigned.append(which)
        with Session(engine,autoflush=False) as db:
            try:
                if which==0:return asyncio.run(C.pay_compensation(cid,{},db,db.get(M.User,uid)))
                return C.cancel_compensation(cid,{'reason':'synthetic test'},db,db.get(M.User,uid))
            except HTTPException as exc:
                db.rollback();return {'blocked':exc.status_code}
    race(mutate)
    with Session(engine) as db:
        c=db.get(M.Compensation,cid)
        assert (c.status,db.query(M.Payment).count()) in [('PAID',1),('CANCELLED',0)]


def test_wallet_creation_concurrency_keeps_one_committed_intent(engine,monkeypatch):
    migrations.run_migrations();calls=[]
    with Session(engine) as db:
        w=M.Wallet(plate_number='1234УБА',balance=0);db.add(w);db.commit();wid=w.id
    async def create(*args,**kwargs):
        calls.append(args)
        with Session(engine) as observer:
            assert observer.query(M.Payment).one().status=='CREATING'
        return {'invoice_id':'synthetic-invoice'}
    monkeypatch.setattr(W.qpay,'create_invoice',create)
    def create_one():
        with Session(engine,autoflush=False) as db:
            try:return asyncio.run(W.create_topup(db,wid,10000,'same-attempt'))
            except HTTPException as exc:
                db.rollback();assert exc.status_code==409;return None
    race(create_one)
    assert len(calls)==1
    with Session(engine) as db:assert db.query(M.Payment).count()==1


def test_callback_claims_and_outbox_leases_are_disjoint(engine):
    migrations.run_migrations();now=datetime.utcnow()
    with Session(engine) as db:
        for n in range(45):
            p=M.Payment(provider='QPAY',payment_method='QR',sender_invoice_no=f'cb-{n}',
                amount=1000,status='PENDING',provider_invoice_id=f'bank-{n}',
                qpay_check_requested_at=now-timedelta(days=1),created_at=now-timedelta(days=3))
            db.add(p);db.flush()
            db.add(M.FinancialJob(job_key=f'job-{n}',kind='PARTNER',payment_id=p.id,payload={}))
        db.commit()
    def claim():
        with Session(engine) as db:return Q.claim_batch(db,now)
    a,b=race(claim)
    assert len(a)==len(b)==20 and not set(a)&set(b)
    def claim_job():
        with Session(engine) as db:return J.claim_one(db)
    a,b=race(claim_job)
    assert a[0]!=b[0]


def test_upgrade_from_previous_schema_preserves_money_and_replays(engine):
    migrations.run_migrations()
    with Session(engine) as db:
        p=M.Payment(provider='CASH',payment_method='CASH',sender_invoice_no='before',
            amount=Decimal('1000.25'),vat_amount=91,status='PAID')
        db.add(p);db.commit()
    with engine.begin() as conn:
        conn.execute(text('DROP TABLE financial_jobs'))
        for col in ('site_id','qpay_check_requested_at','qpay_check_attempts','qpay_last_check_at','qpay_next_check_at'):
            conn.execute(text(f'ALTER TABLE payments DROP COLUMN {col} CASCADE'))
        conn.execute(text('DELETE FROM parking_schema_migrations'))
    migrations.run_migrations();migrations.run_migrations()
    assert migrations.check_ready()['database']=='ok'
    with Session(engine) as db:
        assert db.query(M.Payment).one().amount==Decimal('1000.25')
        assert db.query(M.FinancialJob).count()==0
