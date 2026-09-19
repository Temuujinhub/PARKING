"""Money commits and durable follow-ups; every provider is replaced by a stub."""
import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app import models as M
from app.routers import compensations_router as C, payments_router as P, reports_router as R
from app.services import financial_jobs as J
from test_payment_reliability import db
from test_payment_wait_regressions import parking


def setup_debt(db):
    site=M.ParkingSite(name='Debt site',site_code='DEBT')
    user=M.User(username='cashier',password_hash='test',role='SUPER_ADMIN')
    db.add_all([site,user]);db.flush()
    debt=M.Compensation(site_id=site.id,plate_number='1234УБА',amount=Decimal('1500.25'))
    db.add(debt);db.commit()
    return debt,user,site


def test_sessionless_debt_has_payment_shift_receipt_and_scoped_reports(db,monkeypatch):
    comp,user,site=setup_debt(db)
    shift=M.CashierShift(user_id=user.id,site_id=site.id,status='OPEN')
    db.add(shift);db.commit()
    http=AsyncMock(side_effect=AssertionError('HTTP during money transaction'))
    monkeypatch.setattr(J.ebarimt,'create_receipt',http)
    result=asyncio.run(C.pay_compensation(comp.id,{},db,user))
    p=db.get(M.Payment,result['payment_id'])
    assert p.session_id is None and p.kind=='DEBT' and p.site_id==site.id
    assert p.amount==Decimal('1500.25') and p.shift_id==shift.id
    assert db.query(M.VatReceipt).count()==db.query(M.FinancialJob).count()==1
    assert http.await_count==0
    repeat=asyncio.run(C.pay_compensation(comp.id,{},db,user))
    assert repeat['already_paid'] and db.query(M.Payment).count()==1
    report=R.by_payment('2020-01-01','2030-01-01',site.id,db,user)
    assert report['total']==1500.25
    assert sum(x['amount'] for x in report['by_car'])==1500.25
    rows=R._vat_receipts_query(db,user,'2020-01-01','2030-01-01').all()
    assert len(rows)==1 and rows[0][1]=='1234УБА' and rows[0][2]=='Debt site'
    user.role='OPERATOR';user.site_id='abcdef00-0000-0000-0000-000000000999';db.commit()
    with pytest.raises(HTTPException) as exc:
        asyncio.run(C.pay_compensation(comp.id,{},db,user))
    assert exc.value.status_code==403
    assert R.by_payment('2020-01-01','2030-01-01',None,db,user)['total']==0


@pytest.mark.parametrize('body',[{'method':'CHEQUE'},{'method':'CARD'},
    {'method':'CARD','terminal_id':'not-registered','transaction_id':'bank-1'}])
def test_debt_rejects_missing_or_untrusted_card_evidence(db,body):
    c,u,_=setup_debt(db)
    with pytest.raises(HTTPException): asyncio.run(C.pay_compensation(c.id,body,db,u))
    db.rollback()
    assert db.get(M.Compensation,c.id).status=='PENDING' and db.query(M.Payment).count()==0


def test_card_reference_cannot_be_reused_for_another_debt_or_parking(db):
    c,u,site=setup_debt(db)
    device=M.Device(site_id=site.id,name='POS',device_type='pax_terminal',device_key='T-1',status='active')
    db.add(device);db.commit()
    body={'method':'CARD','terminal_id':'T-1','transaction_id':'bank-123'}
    asyncio.run(C.pay_compensation(c.id,body,db,u))
    c2=M.Compensation(site_id=site.id,plate_number='5678УБА',amount=1500)
    db.add(c2);db.commit()
    with pytest.raises(HTTPException) as exc: asyncio.run(C.pay_compensation(c2.id,body,db,u))
    assert exc.value.status_code==409
    db.rollback()
    assert db.query(M.Payment).count()==1 and c2.status=='PENDING'


def queued(db,monkeypatch,provider='QPAY'):
    s,_,_=parking(db)
    monkeypatch.setattr(J.settings,'qpay_ebarimt',True)
    if provider=='MSGBILL':
        from app.services.msgbill import MsgbillAccount
        account=MsgbillAccount(api_key='synthetic-key',base_url='https://test.invalid')
        monkeypatch.setattr(J.msgbill,'account_enabled_for',lambda *a:account)
        monkeypatch.setattr(J.msgbill,'api_key_for',lambda *a:account)
    p=M.Payment(session_id=s.id,provider=provider,payment_method='QR',amount=1000,vat_amount=91,
        status='PAID',paid_at=datetime.utcnow(),sender_invoice_no='receipt-test',provider_payment_id='bank-id')
    db.add(p);db.flush()
    rec=J.enqueue_receipt(db,p)
    db.commit()
    return p,rec,db.query(M.FinancialJob).one()


def test_receipt_is_durable_before_http_and_worker_commits_result(db,monkeypatch):
    p,rec,job=queued(db,monkeypatch)
    sessions=[]
    def factory():
        session=Session(db.bind,autoflush=False);sessions.append(session);return session
    async def send(*a,**k):
        assert all(not s.in_transaction() for s in sessions)
        with factory() as observer:
            assert observer.get(M.Payment,p.id).status=='PAID'
            assert observer.get(M.FinancialJob,job.id).status=='SENDING'
        return {'billId':'tax-123','lottery':'123'}
    monkeypatch.setattr(J.qpay,'create_ebarimt',AsyncMock(side_effect=send))
    db.rollback();asyncio.run(J.run_once(factory=factory));db.expire_all()
    assert job.status=='DONE' and rec.status=='SENT' and rec.ebarimt_id=='tax-123'
    asyncio.run(J.run_once(factory=factory))
    assert J.qpay.create_ebarimt.await_count==1


def test_unsafe_receipt_timeout_is_retained_without_repeating_post(db,monkeypatch):
    p,rec,job=queued(db,monkeypatch)
    create=AsyncMock(side_effect=TimeoutError())
    monkeypatch.setattr(J.qpay,'create_ebarimt',create)
    factory=lambda:Session(db.bind,autoflush=False)
    asyncio.run(J.run_once(factory=factory));db.expire_all()
    assert job.status=='UNKNOWN' and rec.status=='REVIEW'
    response=asyncio.run(P.retry_ebarimt(db,p))
    assert response['review_required']
    asyncio.run(J.run_once(factory=factory));assert create.await_count==1


def test_dead_unsafe_worker_quarantines_and_safe_worker_reuses_same_key(db,monkeypatch):
    p,rec,job=queued(db,monkeypatch)
    claim=J.claim_one(db)
    now=datetime.utcnow()+timedelta(minutes=4)
    assert J.claim_one(db,now)=='quarantined'
    db.refresh(job);assert job.status=='UNKNOWN'


def test_msgbill_retry_reuses_key_then_polls_known_reference(db,monkeypatch):
    p,rec,job=queued(db,monkeypatch,'MSGBILL')
    create=AsyncMock(side_effect=[TimeoutError(),{'msgbillId':'rcp-1','state':'PENDING'}])
    get=AsyncMock(return_value={'msgbillId':'rcp-1','billId':'tax-1'})
    monkeypatch.setattr(J.msgbill,'create_receipt',create)
    monkeypatch.setattr(J.msgbill,'get_receipt',get)
    factory=lambda:Session(db.bind,autoflush=False)
    for _ in range(3):
        db.expire_all();job.next_attempt_at=datetime.utcnow()-timedelta(seconds=1);db.commit()
        asyncio.run(J.run_once(factory=factory,limit=1))
    db.expire_all()
    assert job.status=='DONE' and rec.ebarimt_id=='tax-1'
    assert create.await_count==2 and get.await_count==1
    assert create.await_args_list[0].kwargs==create.await_args_list[1].kwargs


def test_account_change_never_sends_to_a_different_issuer(db,monkeypatch):
    _,_,job=queued(db,monkeypatch)
    monkeypatch.setattr(J.settings,'qpay_username','different-issuer')
    create=AsyncMock();monkeypatch.setattr(J.qpay,'create_ebarimt',create)
    asyncio.run(J.run_once(factory=lambda:Session(db.bind)))
    db.expire_all();assert job.status=='BLOCKED' and create.await_count==0


def test_finalize_creates_receipt_job_with_money_without_provider_http(db,monkeypatch):
    s,_,_=parking(db)
    p=M.Payment(session_id=s.id,provider='QPAY',payment_method='QR',sender_invoice_no='finalize',
        amount=1000,vat_amount=91,provider_payment_id='bank',status='PENDING')
    db.add(p);db.commit()
    monkeypatch.setattr(J.settings,'qpay_ebarimt',True)
    create=AsyncMock(side_effect=AssertionError('receipt must be queued'))
    monkeypatch.setattr(J.qpay,'create_ebarimt',create)
    async def close(db,stay,**kw):
        stay.status='PAID';db.commit()
    monkeypatch.setattr(P,'mark_paid_and_open',close)
    asyncio.run(P._finalize_paid(db,p))
    assert p.status=='PAID' and db.query(M.FinancialJob).count()==1
    assert db.query(M.VatReceipt).one().status=='PENDING' and create.await_count==0


def test_money_rollback_discards_job_and_receipt_together(db,monkeypatch):
    s,_,_=parking(db)
    p=M.Payment(session_id=s.id,provider='QPAY',payment_method='QR',sender_invoice_no='rollback',
        amount=1000,vat_amount=91,status='PAID',provider_payment_id='bank')
    db.add(p);db.flush();J.enqueue_receipt(db,p);db.rollback()
    assert db.query(M.Payment).count()==db.query(M.FinancialJob).count()==db.query(M.VatReceipt).count()==0


def test_legacy_receipt_never_creates_new_key_after_ambiguous_failure(db,monkeypatch):
    p,rec,job=queued(db,monkeypatch,'MSGBILL')
    db.delete(job);rec.provider_ref='existing-ref';rec.status='FAILED';db.commit()
    create=AsyncMock(side_effect=AssertionError('no second POST'))
    get=AsyncMock(return_value={'billId':'existing-tax-id'})
    monkeypatch.setattr(J.msgbill,'create_receipt',create)
    monkeypatch.setattr(J.msgbill,'get_receipt',get)
    assert asyncio.run(P.retry_ebarimt(db,p))['pending']
    asyncio.run(J.run_once(factory=lambda:Session(db.bind)))
    db.expire_all();assert rec.ebarimt_id=='existing-tax-id'
    create.assert_not_awaited();assert get.await_count==1
    assert asyncio.run(P.retry_ebarimt(db,p))['ok']


def test_legacy_receipt_without_provider_reference_requires_review(db,monkeypatch):
    p,rec,job=queued(db,monkeypatch)
    db.delete(job);db.commit()
    assert asyncio.run(P.retry_ebarimt(db,p))['review_required']
    assert db.query(M.FinancialJob).count()==0


def test_partner_delivery_survives_failure_with_same_event_id(db,monkeypatch):
    import httpx
    p,_,receipt_job=queued(db,monkeypatch)
    db.delete(receipt_job)
    J.enqueue_partner(db,p,'https://partner.invalid/callback','test-partner');db.commit()
    job=db.query(M.FinancialJob).one();event_id=job.id
    requests=[]
    class Client:
        def __init__(self,**kw):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*a):pass
        async def post(self,url,**kw):
            requests.append(kw)
            if len(requests)==1:raise TimeoutError()
            return httpx.Response(200,request=httpx.Request('POST',url))
    monkeypatch.setattr(httpx,'AsyncClient',Client)
    factory=lambda:Session(db.bind,autoflush=False)
    asyncio.run(J.run_once(factory=factory));db.expire_all();assert job.status=='RETRY'
    job.next_attempt_at=datetime.utcnow()-timedelta(seconds=1);db.commit()
    asyncio.run(J.run_once(factory=factory));db.expire_all();assert job.status=='DONE'
    assert requests[0]==requests[1]
    assert requests[1]['headers']['Idempotency-Key']==event_id
    assert requests[1]['json']['event_id']==event_id
    assert 'qr_data' not in (requests[1]['json'].get('ebarimt') or {})


def test_webhook_winning_timeout_keeps_sent_receipt_and_queues_notice(db,monkeypatch):
    p,rec,job=queued(db,monkeypatch,'MSGBILL')
    J.enqueue_partner(db,p,'https://partner.invalid/callback','test-partner');db.commit()
    async def webhook_wins(*a,**kw):
        with Session(db.bind) as callback:
            r=callback.get(M.VatReceipt,rec.id);r.ebarimt_id='tax-webhook';r.status='SENT';callback.commit()
        raise TimeoutError()
    monkeypatch.setattr(J.msgbill,'create_receipt',AsyncMock(side_effect=webhook_wins))
    asyncio.run(J.run_once(factory=lambda:Session(db.bind),limit=1));db.expire_all()
    assert rec.status=='SENT' and job.status=='DONE'
    ready=db.query(M.FinancialJob).filter_by(job_key=f'partner-receipt:{rec.id}').one()
    assert ready.payload['event']['ddtd']=='tax-webhook'


def test_ev_settlement_and_receipt_intent_commit_together_once(db,monkeypatch):
    from app.services import ev_billing as EV, wallet as W
    from test_financial_audit_regressions import setup_scope
    _,w,charger,_,_=setup_scope(db)
    stay=M.ChargeSession(charger_id=charger.id,wallet_id=w.id,plate_number=w.plate_number,
        id_tag='audit',authorized_amount=1000,price_per_wh=1,wh_limit=1000,status='RUNNING',ocpp_tx_id=777)
    db.add(stay);db.flush();W.hold_for_charge(db,w.id,1000,stay.id);db.commit()
    create=AsyncMock(side_effect=AssertionError('HTTP in settlement'))
    monkeypatch.setattr(J.msgbill,'create_receipt',create)
    asyncio.run(EV.on_tx_stopped(db,{'ocpp_tx_id':777,'energy_wh':750}))
    asyncio.run(EV.on_tx_stopped(db,{'ocpp_tx_id':777,'energy_wh':750}))
    db.refresh(w)
    assert w.balance==9250 and stay.status=='SETTLED'
    assert db.query(M.Payment).count()==db.query(M.FinancialJob).count()==1
    assert db.query(M.Payment).one().site_id==charger.site_id
    assert stay.vat_receipt_id==db.query(M.VatReceipt).one().id
    create.assert_not_awaited()


def test_financial_inventory_is_read_only_and_scoped(db,monkeypatch):
    p,rec,job=queued(db,monkeypatch)
    user=M.User(username='observer',password_hash='x',role='OPERATOR',site_id=p.session.site_id)
    db.add(user);db.commit()
    report=R.financial_work(None,100,db,user)
    assert report['outstanding_job_count']==1 and report['jobs'][0]['id']==job.id
    assert 'payload' not in report['jobs'][0]
    user.site_id='abcdef00-0000-0000-0000-000000000999';db.commit()
    assert R.financial_work(None,100,db,user)['outstanding_job_count']==0


def test_bulk_receipt_preview_includes_standalone_debt_without_duplicate_join(db):
    comp,user,site=setup_debt(db)
    asyncio.run(C.pay_compensation(comp.id,{},db,user))
    rec=db.query(M.VatReceipt).one();rec.status='REVIEW';db.commit()
    result=asyncio.run(R.vat_retry_failed({'dry':True},db,user))
    assert result['candidates']==1
