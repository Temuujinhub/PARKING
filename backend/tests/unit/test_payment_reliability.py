"""Failure/retry tests on a disposable DB. Never calls a bank or a device."""
import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base
from app import models as M
from app.services import wallet_topup as W, qpay_recheck as Q


@pytest.fixture
def db():
    engine=create_engine('sqlite://',poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine,autoflush=False) as session:
        yield session
    engine.dispose()


def wallet(db):
    row=M.Wallet(plate_number='TEST123',balance=Decimal('0'))
    db.add(row);db.commit()
    return row


def invoice(db,w,*,status='PENDING',days=0,n=0):
    p=M.Payment(kind='WALLET_TOPUP',wallet_id=w.id,provider='QPAY',payment_method='QR',
                amount=10000,sender_invoice_no=f'test-{n}',provider_invoice_id=f'invoice-{n}',
                status=status,created_at=datetime.utcnow()-timedelta(days=days,minutes=5),
                qpay_check_requested_at=datetime.utcnow()-timedelta(minutes=5))
    db.add(p);db.commit()
    return p


def test_topup_timeout_preserves_committed_intent_and_blocks_retry(db,monkeypatch):
    w=wallet(db);wid=w.id
    async def uncertain(*args,**kwargs):
        with Session(db.bind) as observer:
            p=observer.query(M.Payment).one()
            assert p.wallet_id==wid and p.status=='CREATING'
        raise TimeoutError('provider may have accepted')
    create=AsyncMock(side_effect=uncertain);monkeypatch.setattr(W.qpay,'create_invoice',create)
    with pytest.raises(HTTPException) as error:
        asyncio.run(W.create_topup(db,wid,10000,'attempt-1'))
    assert error.value.status_code==502
    db.rollback();p=db.query(M.Payment).one()
    assert p.status=='UNKNOWN' and p.raw_payload['request_key']=='attempt-1'
    with pytest.raises(HTTPException) as retry:
        asyncio.run(W.create_topup(db,wid,10000,'attempt-1'))
    assert retry.value.status_code==409 and create.await_count==1
    assert db.query(M.WalletLedger).count()==0


def test_successful_topup_reuses_invoice_and_paid_idempotency_key(db,monkeypatch):
    w=wallet(db)
    create=AsyncMock(return_value={'invoice_id':'invoice','qr_image':'image','qr_text':'qr','urls':[]})
    monkeypatch.setattr(W.qpay,'create_invoice',create)
    first=asyncio.run(W.create_topup(db,w.id,10000,'attempt-1'))
    repeat=asyncio.run(W.create_topup(db,w.id,10000,'attempt-1'))
    assert first==repeat and create.await_count==1
    p=db.get(M.Payment,first['payment_id']);p.status='PAID';db.commit()
    paid=asyncio.run(W.create_topup(db,w.id,10000,'attempt-1'))
    assert paid['status']=='PAID' and create.await_count==1


@pytest.mark.parametrize('amount',['NaN','Infinity',True,1000.99,-1000,1000001])
def test_bad_topup_amount_never_reaches_provider(db,monkeypatch,amount):
    w=wallet(db);create=AsyncMock();monkeypatch.setattr(W.qpay,'create_invoice',create)
    with pytest.raises(HTTPException):asyncio.run(W.create_topup(db,w.id,amount))
    create.assert_not_awaited();assert db.query(M.Payment).count()==0


def test_missing_invoice_identity_is_durable_unknown(db,monkeypatch):
    w=wallet(db);monkeypatch.setattr(W.qpay,'create_invoice',AsyncMock(return_value={}))
    with pytest.raises(HTTPException):asyncio.run(W.create_topup(db,w.id,10000))
    assert db.query(M.Payment).one().status=='UNKNOWN'


def test_repeated_topup_verification_credits_once(db,monkeypatch):
    w=wallet(db);p=invoice(db,w)
    monkeypatch.setattr(W.qpay,'check_payment',AsyncMock(return_value={
        'paid':True,'paid_amount':10000,'payment_id':'verified-topup'}))
    assert asyncio.run(W.verify_and_credit(db,p))
    assert asyncio.run(W.verify_and_credit(db,p))
    db.refresh(w)
    assert w.balance==Decimal('10000') and db.query(M.WalletLedger).count()==1


def test_one_provider_transaction_cannot_credit_two_topups(db,monkeypatch):
    w=wallet(db);one=invoice(db,w,n=1);two=invoice(db,w,n=2)
    monkeypatch.setattr(W.qpay,'check_payment',AsyncMock(return_value={
        'paid':True,'paid_amount':10000,'payment_id':'same-bank-reference'}))
    assert asyncio.run(W.verify_and_credit(db,one))
    with pytest.raises(HTTPException):asyncio.run(W.verify_and_credit(db,two))
    db.rollback();db.refresh(w)
    assert w.balance==10000 and db.query(M.WalletLedger).count()==1


@pytest.mark.parametrize('received,reference',[('NaN','tx'),(9999,'tx'),(10000,None)])
def test_invalid_bank_proof_stays_review(db,monkeypatch,received,reference):
    w=wallet(db);p=invoice(db,w)
    monkeypatch.setattr(W.qpay,'check_payment',AsyncMock(return_value={
        'paid':True,'paid_amount':received,'payment_id':reference}))
    assert not asyncio.run(W.verify_and_credit(db,p))
    assert p.status=='REVIEW' and db.query(M.WalletLedger).count()==0


def test_recheck_durable_fairness_old_invoices_and_crash_lease(db):
    w=wallet(db)
    for n in range(45):invoice(db,w,days=3 if n<25 else 0,n=n)
    now=datetime.utcnow()
    first=Q.claim_batch(db,now);second=Q.claim_batch(db,now)
    assert len(first)==len(second)==20 and not set(first)&set(second)
    assert any(db.get(M.Payment,pid).created_at < now-timedelta(days=1) for pid in first)
    assert len(Q.claim_batch(db,now))==5
    assert Q.claim_batch(db,now)==[]
    assert len(Q.claim_batch(db,now+timedelta(minutes=6)))==20


def test_background_dispatches_wallet_credit_without_parking_finalize(db,monkeypatch):
    from app.routers import payments_router as PR
    w=wallet(db);p=invoice(db,w)
    monkeypatch.setattr(Q.settings,'qpay_mock',False)
    monkeypatch.setattr(Q,'SessionLocal',lambda:Session(db.bind,autoflush=False))
    monkeypatch.setattr(Q.asyncio,'sleep',AsyncMock())
    monkeypatch.setattr(W.qpay,'check_payment',AsyncMock(return_value={
        'paid':True,'paid_amount':10000,'payment_id':'background-bank-reference'}))
    finalize=AsyncMock();monkeypatch.setattr(PR,'_confirm_qpay',finalize)
    assert asyncio.run(Q.run_once())==1
    finalize.assert_not_awaited();db.refresh(w);db.refresh(p)
    assert p.status=='PAID' and w.balance==10000


def test_background_never_polls_invoice_without_callback_or_after_retry_limit(db):
    w=wallet(db);p=invoice(db,w)
    p.qpay_check_requested_at=None;db.commit()
    assert Q.claim_batch(db,datetime.utcnow())==[]
    p.qpay_check_requested_at=datetime.utcnow();p.qpay_check_attempts=Q.settings.qpay_callback_max_attempts
    db.commit();assert Q.claim_batch(db,datetime.utcnow())==[]


def test_failed_callback_is_durable_and_newer_signal_not_cleared(db):
    w=wallet(db);p=invoice(db,w)
    first=Q.request_verification(db,p)
    db.rollback();db.refresh(p)
    assert p.qpay_check_requested_at==first
    newer=Q.request_verification(db,p)
    Q.finish_verification(db,p.id,first,completed=True)
    db.refresh(p);assert p.qpay_check_requested_at==newer
    Q.finish_verification(db,p.id,newer,completed=True)
    db.refresh(p);assert p.qpay_check_requested_at is None


def test_fractional_wallet_debit_matches_payment_and_ledger(db,monkeypatch):
    from test_payment_wait_regressions import parking
    from app import session_logic as SL
    from app.routers import payments_router as PR
    stay,_,_=parking(db)
    w=M.Wallet(plate_number=stay.plate_number,balance=Decimal('499.75'))
    db.add(w);db.commit()
    async def finalize(db,payment):
        payment.status='PAID';db.commit()
    monkeypatch.setattr(PR,'_finalize_paid',finalize)
    deducted,covered=asyncio.run(SL._wallet_auto_deduct(db,stay,1000))
    assert deducted==499.75 and not covered
    assert db.query(M.Payment).one().amount==Decimal('499.75')
    assert db.query(M.WalletLedger).one().amount==Decimal('499.75')
    db.refresh(w);assert w.balance==0


def test_interrupted_creator_becomes_unknown_without_any_provider_request(db,monkeypatch):
    w=wallet(db);p=invoice(db,w,status='CREATING',days=1)
    p.provider_invoice_id=None;db.commit()
    create=AsyncMock();monkeypatch.setattr(W.qpay,'create_invoice',create)
    assert W.expire_creating(db)==1
    db.refresh(p);assert p.status=='UNKNOWN'
    with pytest.raises(HTTPException):asyncio.run(W.create_topup(db,w.id,10000))
    create.assert_not_awaited()


def test_wallet_callback_failure_keeps_work_and_foreground_check_never_calls_bank(db,monkeypatch):
    from app.routers import wallet_router as WR
    from starlette.requests import Request
    w=wallet(db);p=invoice(db,w)
    p.raw_payload={'webhook_token':'synthetic-callback'};db.commit()
    check=AsyncMock(side_effect=TimeoutError())
    monkeypatch.setattr(W.qpay,'check_payment',check)
    with pytest.raises(TimeoutError):
        asyncio.run(WR.wallet_topup_webhook(p.id,'synthetic-callback',db))
    db.rollback();db.refresh(p)
    assert p.qpay_check_requested_at is not None and p.status=='PENDING'
    request=Request({'type':'http','client':('test',123),'headers':[]})
    result=asyncio.run(WR.wallet_topup_check(w.public_token,p.id,request,db))
    assert result['status']=='PENDING' and check.await_count==1
