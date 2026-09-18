"""Payment queue rules against a disposable database, with no devices/network."""
import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import models as M, session_logic as SL
from app.database import Base
from app.services import app_settings as A, auto_close, payment_wait
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


def parking(db, now=None):
    now = now or datetime.utcnow()
    tariff = M.TariffTemplate(name='Hourly', free_minutes=0, extra_hour_price=1000,
                              daily_cap=50000, grace_minutes=15)
    site = M.ParkingSite(name='Test site', site_code='TEST', tariff_template=tariff,
                         auto_close_hours=0, entry_only_free_hours=0)
    db.add(site); db.flush()
    s = M.ParkingSession(site_id=site.id, plate_number='1234УБА',
                         entry_time=now-timedelta(minutes=59), status='AWAITING_PAYMENT')
    db.add(s); db.flush(); db.refresh(s)
    fee = SL.session_fee_info(db, s, at=now)
    payment_wait.begin_wait(db, s, fee, now)
    db.commit()
    return s, fee, now


def test_price_held_three_minutes_then_total_elapsed_time(db):
    s, fee, start = parking(db)
    assert fee['total_fee'] == 1000
    assert SL.session_fee_info(db, s, start+timedelta(seconds=179))['total_fee'] == 1000
    assert SL.session_fee_info(db, s, start+timedelta(minutes=3))['total_fee'] == 2000
    assert SL.session_fee_info(db, s, start+timedelta(minutes=3))['duration_minutes'] == 62


def test_retry_and_restart_preserve_quote_and_deadline(db):
    s, fee, start = parking(db)
    payment_wait.begin_wait(db, s, {'total_fee': 9000}, start+timedelta(minutes=2))
    s.note='new metadata'; db.commit(); db.expire_all()
    assert s.payment_wait_started_at == start
    assert s.payment_quote_until == start+timedelta(minutes=3)
    assert s.payment_quote['total_fee'] == 1000
    assert s.last_exit_seen_at == start+timedelta(minutes=2)


@pytest.mark.parametrize('minutes',[0,5])
def test_hold_setting_applies_to_new_waits(db, minutes):
    s, _, start = parking(db)
    A.set_site_rules(db,A.EXITRULES_KEY,s.site_id,{'payment_hold_minutes':minutes},'admin')
    db.commit()
    payment_wait.begin_wait(db,s,{'total_fee':1000},start,restart=True)
    assert s.payment_quote_until == start+timedelta(minutes=minutes)


def test_two_hours_closes_despite_stale_cleanup_disabled_and_metadata_updates(db, monkeypatch):
    start=datetime.utcnow()-timedelta(hours=2,seconds=1)
    s,fee,_=parking(db,start)
    s.note='fresh metadata'; db.commit()
    monkeypatch.setattr(auto_close,'SessionLocal',lambda:Session(db.bind,autoflush=False))
    assert auto_close.run_once()==1
    db.refresh(s)
    assert s.status=='MANUAL_CLOSED'
    debt=db.query(M.Compensation).filter_by(session_id=s.id).one()
    assert debt.amount==1000  # no invented two-hour charge after last exit sighting
    assert debt.reason=='unpaid_exit'
    assert auto_close.run_once()==0
    assert db.query(M.Compensation).filter_by(session_id=s.id).count()==1


def test_before_two_hours_stays_in_queue(db, monkeypatch):
    s,_,_=parking(db,datetime.utcnow()-timedelta(hours=1,minutes=59))
    monkeypatch.setattr(auto_close,'SessionLocal',lambda:Session(db.bind,autoflush=False))
    assert auto_close.run_once()==0
    assert s.status=='AWAITING_PAYMENT'


def test_site_can_enable_cleanup_when_global_disabled(db, monkeypatch):
    s,_,_=parking(db,datetime.utcnow()-timedelta(hours=3))
    A.set_rules(db,A.AUTOCLOSE_KEY,{'enabled':False},'admin')
    A.set_site_rules(db,A.AUTOCLOSE_KEY,s.site_id,{'enabled':True},'admin'); db.commit()
    monkeypatch.setattr(auto_close,'SessionLocal',lambda:Session(db.bind,autoflush=False))
    assert auto_close.run_once()==1


def test_partial_payment_becomes_only_residual_debt(db, monkeypatch):
    s,_,_=parking(db,datetime.utcnow()-timedelta(hours=3))
    db.add(M.Payment(session_id=s.id,provider='WALLET',payment_method='WALLET',
                     sender_invoice_no='partial',amount=400,status='PAID'))
    db.commit()
    monkeypatch.setattr(auto_close,'SessionLocal',lambda:Session(db.bind,autoflush=False))
    assert auto_close.run_once()==1
    assert db.query(M.Compensation).one().amount==600


def test_daily_cap_never_increases_hourly_charge(db):
    s,_,_=parking(db)
    for hours,expected in [(24,24000),(48,48000),(72,72000)]:
        assert SL.session_fee_info(db,s,s.entry_time+timedelta(hours=hours))['total_fee']==expected


def test_wallet_finalize_failure_rolls_back_debit(db, monkeypatch):
    s,_,_=parking(db)
    w=M.Wallet(plate_number=s.plate_number,balance=10000)
    db.add(w);db.commit()
    monkeypatch.setattr(PR,'_finalize_paid',AsyncMock(side_effect=RuntimeError('failure')))
    with pytest.raises(RuntimeError):
        asyncio.run(SL._wallet_auto_deduct(db,s,1000))
    db.rollback();db.refresh(w)
    assert w.balance==Decimal('10000')
    assert db.query(M.WalletLedger).count()==0
    assert db.query(M.Payment).count()==0


def invoice(db, s):
    from starlette.requests import Request
    request = Request({'type':'http','method':'POST','path':'/',
                       'client':('quote-test',1),'headers':[]})
    return asyncio.run(PR.qpay_invoice({'session_id':s.id},request,db))


def test_repeated_invoice_keeps_same_id_and_price_during_hold(db, monkeypatch):
    s,_,_=parking(db)
    create=AsyncMock(return_value={'invoice_id':'inv-one','qr_text':'qr','deep_link':'','urls':[]})
    monkeypatch.setattr(PR.qpay,'create_invoice',create)
    first=invoice(db,s)
    # Tariff changes do not alter a quote already offered to a driver.
    s.site.tariff_template.extra_hour_price=5000; db.commit()
    second=invoice(db,s)
    assert first['payment_id']==second['payment_id']
    assert first['amount']==second['amount']==1000
    create.assert_awaited_once()


def test_expired_hold_replaces_invoice_only_after_provider_cancellation(db, monkeypatch):
    s,_,_=parking(db)
    events=[]
    async def create(*args,**kwargs):
        events.append('create')
        return {'invoice_id':f'inv-{len(events)}','qr_text':'qr','deep_link':'','urls':[]}
    async def cancel(*args,**kwargs): events.append('cancel')
    monkeypatch.setattr(PR.qpay,'create_invoice',create)
    monkeypatch.setattr(PR.qpay,'cancel_invoice',cancel)
    first=invoice(db,s)
    s.payment_quote_until=datetime.utcnow()-timedelta(seconds=1)
    s.entry_time=datetime.utcnow()-timedelta(minutes=63);db.commit()
    second=invoice(db,s)
    assert events==['create','cancel','create']
    assert first['amount']==1000 and second['amount']==2000
    assert db.get(M.Payment,first['payment_id']).status=='CANCELLED'


def test_cancel_timeout_does_not_create_a_second_payable_invoice(db, monkeypatch):
    import httpx
    from fastapi import HTTPException
    s,_,_=parking(db)
    first=invoice(db,s)
    s.payment_quote_until=datetime.utcnow()-timedelta(seconds=1)
    s.entry_time=datetime.utcnow()-timedelta(minutes=63);db.commit()
    monkeypatch.setattr(PR.qpay,'cancel_invoice',AsyncMock(side_effect=httpx.ReadTimeout('lost response')))
    with pytest.raises(HTTPException) as err: invoice(db,s)
    assert err.value.status_code==409
    assert db.query(M.Payment).count()==1
    assert db.get(M.Payment,first['payment_id']).status=='PENDING'


def test_late_bank_payment_settles_debt_without_reopening_barrier(db, monkeypatch):
    s,_,_=parking(db,datetime.utcnow()-timedelta(hours=3))
    payment=PR._create_payment(db,s,'QPAY','QR')
    # Auto-close uses last physical sighting; this invoice may have been opened later.
    payment.amount=1000;payment.fee_snapshot['parking_amount']=1000
    db.commit()
    sid=s.id
    monkeypatch.setattr(auto_close,'SessionLocal',lambda:Session(db.bind,autoflush=False))
    assert auto_close.run_once()==1
    db.expire_all()
    open_gate=AsyncMock();monkeypatch.setattr(PR,'mark_paid_and_open',open_gate)
    asyncio.run(PR._finalize_paid(db,payment));db.commit()
    assert db.get(M.ParkingSession,sid).status=='MANUAL_CLOSED'
    assert db.query(M.Compensation).one().status=='PAID'
    assert payment.status=='PAID'
    open_gate.assert_not_awaited()
    asyncio.run(PR._finalize_paid(db,payment))
    assert db.query(M.VatReceipt).count()==1


def test_wallet_does_not_debit_while_driver_is_paying_qr(db, monkeypatch):
    s,_,_=parking(db)
    w=M.Wallet(plate_number=s.plate_number,balance=10000);db.add(w);db.commit()
    invoice(db,s)
    assert asyncio.run(SL._wallet_auto_deduct(db,s,1000))==(0.0,False)
    assert w.balance==10000 and db.query(M.WalletLedger).count()==0
