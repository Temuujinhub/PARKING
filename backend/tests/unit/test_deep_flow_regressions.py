"""Regression checks for the independently reproduced deep-flow audit defects.

No live provider, camera or database connections. Unlike the audit evidence,
these tests assert the CORRECT behavior.
"""
import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request
from app import models as M, session_logic as SL
from app.routers import integration_router as IR, payments_router as PR, lpr_router as LR
from app.services import payment_wait, snapshot, snap_puller
from app.config import settings

def parking(db, code='AUD', plate='1234УБА'):
    tariff=M.TariffTemplate(name='Hourly',free_minutes=0,extra_hour_price=1000,daily_cap=50000)
    site=M.ParkingSite(name='Synthetic '+code,site_code=code,tariff_template=tariff)
    db.add(site);db.flush()
    now=datetime.utcnow()
    s=M.ParkingSession(site_id=site.id,plate_number=plate,entry_time=now-timedelta(minutes=59),status='AWAITING_PAYMENT')
    db.add(s);db.flush();db.refresh(s)
    payment_wait.begin_wait(db,s,SL.session_fee_info(db,s,at=now),now)
    db.commit()
    return s

def partner(name='AUDIT_WALLET', site=None):
    p=IR.PartnerAuth(name);p.site_id=site
    return p

def request():
    return Request({'type':'http','method':'POST','path':'/','headers':[], 'client':('audit',1)})

def user(db):
    u=M.User(username='audit',role='SUPER_ADMIN',password_hash='unused')
    db.add(u);db.commit();return u


import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from app.database import Base
from app.config import settings
from app.services import app_settings

@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    async def blocked_async(*a, **k):
        raise AssertionError('Network forbidden in audit reproduction')
    def blocked_sync(*a, **k):
        raise AssertionError('Network forbidden in audit reproduction')
    monkeypatch.setattr(httpx.AsyncClient, 'send', blocked_async)
    monkeypatch.setattr(httpx.Client, 'send', blocked_sync)
    monkeypatch.setattr(settings, 'ebarimt_mock_receipts', False)
    monkeypatch.setattr(settings, 'qpay_ebarimt', False)
    from app.routers import payments_router as pr
    monkeypatch.setattr(pr.msgbill, 'account_enabled_for', lambda *a: None)

@pytest.fixture
def db():
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    app_settings.invalidate_cache()
    with Session(engine, autoflush=False) as db:
        yield db
    app_settings.invalidate_cache()
    engine.dispose()


def test_registered_push_callback_dispatches_real_event(db, monkeypatch):
    s=parking(db)
    d=M.Device(site_id=s.site_id,name='Cam',device_type='camera',device_key='audit-key',status='active')
    db.add(d);db.commit()
    req=SimpleNamespace(headers={},client=SimpleNamespace(host='127.0.0.1'),
        json=AsyncMock(return_value={'Plate':{'PlateNumber':s.plate_number,'Confidence':99}}))
    handler=AsyncMock(return_value={'action':'entered'});monkeypatch.setattr(LR,'handle_entry',handler)
    asyncio.run(LR.lpr_callback(req,'audit-key',db))
    handler.assert_awaited_once()

@pytest.mark.parametrize('kind',['wallet','pos'])
@pytest.mark.parametrize('amount',['NaN','Infinity','-Infinity',-1,0,True,None,'1000.001'])
def test_nonfinite_or_invalid_money_cannot_confirm(db,kind,amount):
    s=parking(db)
    with pytest.raises(HTTPException) as error:
        if kind=='wallet':
            intent=IR.create_payment_intent({'session_id':s.id},db,partner())
            asyncio.run(IR.confirm_payment(intent['payment_id'],{'amount':amount,'transaction_id':'bank-ref'},db,partner()))
        else:
            asyncio.run(PR.pos_confirm({'session_id':s.id,'amount':amount,'transaction_id':'bank-ref'},request(),db,user(db)))
    assert error.value.status_code==422
    assert db.query(M.Payment).filter_by(status='PAID').count()==0

@pytest.mark.parametrize('kind',['wallet','pos'])
@pytest.mark.parametrize('reference',['',None,' '*3,'x'*121])
def test_confirm_requires_transaction_reference(db,kind,reference):
    s=parking(db)
    with pytest.raises(HTTPException) as error:
        if kind=='wallet':
            intent=IR.create_payment_intent({'session_id':s.id},db,partner())
            asyncio.run(IR.confirm_payment(intent['payment_id'],{'amount':1000,'transaction_id':reference},db,partner()))
        else:
            asyncio.run(PR.pos_confirm({'session_id':s.id,'amount':1000,'transaction_id':reference},request(),db,user(db)))
    assert error.value.status_code==422
    assert db.query(M.Payment).filter_by(status='PAID').count()==0

def test_partner_confirm_and_status_enforce_current_site_scope(db):
    a=parking(db,'A');b=parking(db,'B','5678УБА')
    intent=IR.create_payment_intent({'session_id':b.id},db,partner(site=b.site_id))
    wrong=partner(site=a.site_id)
    with pytest.raises(HTTPException) as error:
        asyncio.run(IR.confirm_payment(intent['payment_id'],{'amount':1000,'transaction_id':'scope-test'},db,wrong))
    assert error.value.status_code==403
    with pytest.raises(HTTPException) as error:
        IR.payment_status(intent['payment_id'],db,wrong)
    assert error.value.status_code==403
    assert db.get(M.Payment,intent['payment_id']).status=='PENDING'

def test_same_pos_transaction_cannot_credit_another_session(db):
    a=parking(db,'A');b=parking(db,'B','5678УБА');u=user(db)
    body={'session_id':a.id,'amount':1000,'transaction_id':'one-bank-transaction'}
    first=asyncio.run(PR.pos_confirm(body,request(),db,u))
    retry=asyncio.run(PR.pos_confirm(body,request(),db,u))
    assert first['payment_id']==retry['payment_id']
    with pytest.raises(HTTPException) as error:
        asyncio.run(PR.pos_confirm({**body,'session_id':b.id},request(),db,u))
    assert error.value.status_code==409
    assert db.query(M.Payment).filter_by(provider='POS',status='PAID').count()==1

def test_pos_does_not_claim_gate_open_without_command(db):
    s=parking(db)
    out=asyncio.run(PR.pos_confirm({'session_id':s.id,'amount':1000,'transaction_id':'no-gate'},request(),db,user(db)))
    assert out['status']=='PAID' and out['barrier_opened'] is False
    assert out['barrier_command_status']=='NOT_REQUESTED'
    assert out['physical_gate_state']=='UNKNOWN'
    assert db.query(M.BarrierCommand).count()==0

@pytest.mark.parametrize('disabled',['camera','site','remapped'])
def test_queued_event_checks_current_device_configuration(db,monkeypatch,disabled):
    from sqlalchemy.orm import Session
    from app.services import cgi_poller as CGI
    s=parking(db)
    d=M.Device(site_id=s.site_id,name='Queued camera',device_type='camera',device_key='queued-key',
               status='deleted' if disabled=='camera' else 'active',lane_dir='entry',ip_address='synthetic-camera')
    if disabled=='site': s.site.is_active=False
    db.add(d);db.commit();did=d.id
    previous=CGI._event_mapping(d)
    if disabled=='remapped': d.lane_no=9;db.commit()
    monkeypatch.setattr(CGI,'SessionLocal',lambda:Session(db.bind,autoflush=False))
    handler=AsyncMock(return_value={'action':'entered'})
    monkeypatch.setattr(CGI,'handle_entry',handler)
    asyncio.run(CGI._process_event(did,{'TrafficCar':{'PlateNumber':s.plate_number,'Confidence':99}},
                                   expected_mapping=previous))
    handler.assert_not_awaited()

def test_barrier_does_not_repeat_strobe_after_lost_ack(db,monkeypatch):
    import httpx
    from app.services import barrier as B
    s=parking(db);sent=[]
    d=M.Device(site_id=s.site_id,name='Synthetic gate',device_type='barrier',device_key='gate-key',
               status='active',lane_dir='exit',ip_address='synthetic-gate')
    db.add(d);db.commit()
    class Rpc:
        def __init__(self,*a): pass
        async def login(self): pass
        async def logout(self): pass
        async def strobe(self,*a):
            sent.append('device executed open')
            if len(sent)==1: raise httpx.ReadTimeout('response lost after execution')
            return {'result':True}
    monkeypatch.setattr(B,'DahuaRpc',Rpc)
    monkeypatch.setattr(B,'camera_client',lambda ip:object())
    monkeypatch.setattr(settings,'barrier_mock',False)
    monkeypatch.setattr(settings,'barrier_retries',1)
    monkeypatch.setattr(settings,'barrier_retry_delay_sec',0)
    monkeypatch.setattr(settings,'barrier_open_path','')
    cmd=asyncio.run(B._execute(db,d,'open',s.id,'audit'))
    assert cmd.status=='UNKNOWN' and len(sent)==1
    # A new request, even after the in-memory flag cleared, cannot repeat it.
    again=asyncio.run(B._execute(db,d,'open',s.id,'audit'))
    assert again.id==cmd.id and len(sent)==1


def test_wallet_intent_cannot_compete_with_pending_qpay(db):
    s=parking(db)
    q=PR._create_payment(db,s,'QPAY','QR');q.provider_invoice_id='live-invoice';db.commit()
    with pytest.raises(HTTPException) as error:
        IR.create_payment_intent({'session_id':s.id},db,partner())
    assert error.value.status_code==409
    assert db.query(M.Payment).count()==1 and q.status=='PENDING'


def test_wallet_reprice_preserves_attempt_and_credits_late_money(db):
    s=parking(db)
    first=IR.create_payment_intent({'session_id':s.id},db,partner())
    s.payment_quote_until=datetime.utcnow()-timedelta(seconds=1)
    s.entry_time=datetime.utcnow()-timedelta(minutes=65)
    # «Хоцорсон мөнгө» = нэхэмжлэлийн үнэ барих хугацаа (invoice_quote_minutes,
    # анхдагч 10) хэтэрсэн intent — одоогийн дүнгээр, үлдэгдлийг нэхнэ.
    db.get(M.Payment,first['payment_id']).created_at=datetime.utcnow()-timedelta(minutes=11)
    db.commit()
    with pytest.raises(HTTPException) as error:
        IR.create_payment_intent({'session_id':s.id},db,partner())
    assert error.value.status_code==409
    assert db.get(M.Payment,first['payment_id']).status=='PENDING'
    paid=asyncio.run(IR.confirm_payment(first['payment_id'],
        {'amount':1000,'transaction_id':'first-authorized'},db,partner()))
    assert paid['status']=='PAID' and paid['amount_due']==1000
    assert s.status=='AWAITING_PAYMENT' and db.query(M.BarrierCommand).count()==0
    second=IR.create_payment_intent({'session_id':s.id},db,partner())
    assert second['amount']==1000  # only the remainder, never the whole 2000 again
    paid=asyncio.run(IR.confirm_payment(second['payment_id'],
        {'amount':1000,'transaction_id':'remainder-authorized'},db,partner()))
    assert paid['amount_due']==0 and s.status=='PAID'
    assert SL.paid_total(db,s)==2000


def test_wallet_intent_paid_within_quote_window_settles_at_quote(db):
    # 2026-09-23 гомдол: төлж байх зуур (17с–6мин) тарифын шатлал ахиад
    # «2,000 төлчихлөө 5,000 болчлоо». Нэхэмжлэлийн дүн invoice_quote_minutes
    # дотор төлөгдвөл хүндэтгэгдэнэ — үлдэгдэлгүй, хаалт нээгдэнэ.
    s=parking(db)
    first=IR.create_payment_intent({'session_id':s.id},db,partner())
    s.payment_quote_until=datetime.utcnow()-timedelta(seconds=1)
    s.entry_time=datetime.utcnow()-timedelta(minutes=65);db.commit()
    paid=asyncio.run(IR.confirm_payment(first['payment_id'],
        {'amount':1000,'transaction_id':'paid-at-quote'},db,partner()))
    assert paid['status']=='PAID' and paid['amount_due']==0
    assert s.status in ('PAID','CLOSED') and SL.paid_total(db,s)==1000
    assert db.query(M.AuditLog).filter_by(action='PAYMENT_QUOTE_HONORED').count()==1
    assert db.query(M.AuditLog).filter_by(action='PAYMENT_PARTIAL').count()==0


def test_late_pos_confirmation_is_recorded_as_partial_payment(db):
    s=parking(db);u=user(db)
    s.payment_quote_until=datetime.utcnow()-timedelta(seconds=1)
    s.entry_time=datetime.utcnow()-timedelta(minutes=65);db.commit()
    body={'session_id':s.id,'amount':1000,'transaction_id':'charged-at-old-quote'}
    paid=asyncio.run(PR.pos_confirm(body,request(),db,u))
    assert paid['status']=='PAID' and paid['amount_due']==1000
    assert paid['needs_additional_payment'] and not paid['barrier_opened']
    retry=asyncio.run(PR.pos_confirm(body,request(),db,u))
    assert retry['payment_id']==paid['payment_id']
    assert db.query(M.Payment).count()==1
    rest=asyncio.run(PR.pos_confirm({**body,'transaction_id':'card-remainder'},request(),db,u))
    assert rest['amount_due']==0 and SL.paid_total(db,s)==2000


def test_pos_cancels_old_qpay_but_never_loses_a_bank_confirmation(db,monkeypatch):
    s=parking(db)
    q=PR._create_payment(db,s,'QPAY','QR');q.provider_invoice_id='old-qr';db.commit()
    cancel=AsyncMock();monkeypatch.setattr(PR.qpay,'cancel_invoice',cancel)
    paid=asyncio.run(PR.pos_confirm({'session_id':s.id,'amount':1000,'transaction_id':'paid-card'},
                                   request(),db,user(db)))
    assert paid['status']=='PAID' and q.status=='CANCELLED'
    cancel.assert_awaited_once()


def test_pos_money_survives_qpay_cancellation_failure(db,monkeypatch):
    import httpx
    s=parking(db)
    q=PR._create_payment(db,s,'QPAY','QR');q.provider_invoice_id='old-qr';db.commit()
    monkeypatch.setattr(PR.qpay,'cancel_invoice',AsyncMock(side_effect=httpx.ReadTimeout('uncertain')))
    out=asyncio.run(PR.pos_confirm({'session_id':s.id,'amount':1000,'transaction_id':'bank-already-paid'},
                                   request(),db,user(db)))
    assert out['status']=='PAID'
    assert db.query(M.AuditLog).filter_by(action='PAYMENT_PROVIDER_CONFLICT').count()==1
    assert q.status=='PENDING'  # no false assertion of cancellation


def test_uncertain_qpay_creation_is_durable_and_blocks_repeated_collection(db,monkeypatch):
    import httpx
    s=parking(db);accepted=[]
    async def provider(sender_no,*args,**kwargs):
        accepted.append(sender_no)
        with Session(db.bind) as other:
            stored=other.query(M.Payment).one()
            assert stored.status=='CREATING' and stored.sender_invoice_no==sender_no
        raise httpx.ReadTimeout('provider accepted; response lost')
    monkeypatch.setattr(PR.qpay,'create_invoice',provider)
    with pytest.raises(HTTPException):
        asyncio.run(PR.qpay_invoice({'session_id':s.id},request(),db))
    p=db.query(M.Payment).one()
    assert p.status=='UNKNOWN' and p.sender_invoice_no==accepted[0]
    with pytest.raises(HTTPException) as error:
        asyncio.run(PR.qpay_invoice({'session_id':s.id},request(),db))
    assert error.value.status_code==409 and len(accepted)==1
    with pytest.raises(HTTPException):
        IR.create_payment_intent({'session_id':s.id},db,partner())
    assert db.query(M.Payment).count()==1


def test_qpay_transport_does_not_retry_non_idempotent_invoice(db,monkeypatch):
    import httpx
    from app.services import qpay
    monkeypatch.setattr(qpay,'_get_token',AsyncMock(return_value='mock-token'))
    send=AsyncMock(side_effect=httpx.ReadTimeout('accepted but response lost'))
    monkeypatch.setattr(httpx.AsyncClient,'request',send)
    with pytest.raises(httpx.ReadTimeout):
        asyncio.run(qpay._api('POST','/invoice',qpay.global_account(),json={'sender_invoice_no':'one'}))
    send.assert_awaited_once()


def test_wallet_cancel_requires_no_charge_attestation(db):
    s=parking(db); p=partner()
    intent=IR.create_payment_intent({'session_id':s.id},db,p)
    with pytest.raises(HTTPException):
        IR.cancel_payment_intent(intent['payment_id'],{},db,p)
    assert db.get(M.Payment,intent['payment_id']).status=='PENDING'
    IR.cancel_payment_intent(intent['payment_id'],{'outcome':'NOT_CHARGED'},db,p)
    assert db.get(M.Payment,intent['payment_id']).status=='CANCELLED'
    fresh=IR.create_payment_intent({'session_id':s.id},db,p)
    assert fresh['payment_id']!=intent['payment_id']


def test_pos_prepare_blocks_competing_wallet_and_confirmation_reuses_intent(db):
    s=parking(db); u=user(db)
    t=M.Device(site_id=s.site_id,name='POS',device_type='pos',device_key='pos-test',status='active')
    db.add(t); db.commit()
    out=asyncio.run(PR.pos_prepare({'session_id':s.id,'terminal_id':t.device_key,'expected_amount':1000},db,u))
    with pytest.raises(HTTPException) as error:
        IR.create_payment_intent({'session_id':s.id},db,partner())
    assert error.value.status_code==409
    paid=asyncio.run(PR.pos_confirm({'session_id':s.id,'payment_id':out['payment_id'],
         'terminal_id':t.device_key,'amount':1000,'transaction_id':'bank-pos-prepare'},request(),db,u))
    assert paid['payment_id']==out['payment_id'] and db.query(M.Payment).count()==1


def test_jpeg_decode_rejects_fake_marker_and_ignores_padding():
    from io import BytesIO
    from PIL import Image
    out=BytesIO(); Image.new('RGB',(128,96),'red').save(out,format='JPEG')
    data=out.getvalue()
    assert not snapshot.valid_jpeg(b'\xff\xd8'+b'x'*2000)
    assert not snapshot.valid_jpeg(data[:-50])
    assert snapshot.valid_jpeg(data)
    assert snapshot.jpeg_score(data)==snapshot.jpeg_score(data+b'padding'*1000)


def test_unidentified_stream_frame_cannot_be_given_to_next_car(monkeypatch):
    from io import BytesIO
    from PIL import Image
    import time
    out=BytesIO(); Image.new('RGB',(64,64)).save(out,format='JPEG')
    snapshot.offer_stream_image('test-camera',out.getvalue())
    monkeypatch.setattr(snapshot,'_snapshot_written',AsyncMock(return_value=False))
    monkeypatch.setattr(settings,'snapshot_wait_event_sec',0)
    monkeypatch.setattr(settings,'snapshot_stream_wait_sec',0)
    assert asyncio.run(snapshot._wait_camera_image('next-car','test-camera','entry',time.monotonic())) is None


def test_snapshot_compare_and_set_preserves_selected_event(db,monkeypatch):
    s=parking(db); discarded=[]
    monkeypatch.setattr(snapshot,'discard_saved',discarded.append)
    assert snapshot.attach_saved(db,s,'exit','live.jpg','snapshot.cgi')
    db.refresh(s)
    assert snapshot.attach_saved(db,s,'exit','event.jpg','comet')
    db.refresh(s)
    assert not snapshot.attach_saved(db,s,'exit','other.jpg','ws')
    assert s.exit_snapshot=='event.jpg' and discarded==['live.jpg']


def test_busy_camera_control_lock_still_sends_barrier_command(db,monkeypatch):
    # 2026-09-23: 317df72 түгжээ завгүй үед хаалтын командыг ОГТ илгээхгүй
    # (FAILED) болгосноор хаалтын алдаа 0.1% → 2–3% (өдөрт 173–284) болсон.
    # Хаалт тэргүүлэх эрхтэй: түгжээг богино хүлээгээд авч чадаагүй ч явуулна.
    from app.services import barrier as B
    s=parking(db)
    d=M.Device(site_id=s.site_id,name='Gate',device_type='barrier',device_key='locked-gate',
               ip_address='test-lock',status='active')
    db.add(d);db.commit()
    monkeypatch.setattr(settings,'barrier_mock',False)
    monkeypatch.setattr(settings,'barrier_lock_wait_sec',0.01)
    monkeypatch.setattr(settings,'screen_enabled',False)
    login=AsyncMock(); monkeypatch.setattr(B.DahuaRpc,'login',login)
    monkeypatch.setattr(B.DahuaRpc,'logout',AsyncMock())
    monkeypatch.setattr(B.DahuaRpc,'strobe',AsyncMock(return_value={'result':True}))
    async def scenario():
        lock=B._rpc_lock(d.ip_address)
        await lock.acquire()
        try: return await B._execute(db,d,'open',s.id,'audit')
        finally: lock.release()
    cmd=asyncio.run(scenario())
    assert cmd.status=='SUCCESS' and not B.barrier_is_waiting(d.ip_address)
    login.assert_awaited()


def test_partial_internal_wallet_uses_shared_receipt_and_ledger(db,monkeypatch):
    s=parking(db)
    w=M.Wallet(plate_number=s.plate_number,balance=500)
    db.add(w);db.commit()
    from app.services import wallet_providers
    monkeypatch.setattr(wallet_providers,'external_providers',lambda:[])
    amount,covered=asyncio.run(SL._wallet_auto_deduct(db,s,1000))
    assert amount==500 and covered is False
    p=db.query(M.Payment).one()
    assert p.status=='PAID' and db.query(M.WalletLedger).count()==1
    assert db.query(M.VatReceipt).filter_by(payment_id=p.id).count()==1
    assert SL.amount_due(db,s,SL.session_fee_info(db,s))==500
    assert db.query(M.BarrierCommand).count()==0


def test_same_named_partner_key_cannot_use_other_key_intent_or_hook(db):
    s=parking(db)
    keys=[M.PartnerKey(name='SAME',key_hash=str(i)*64,key_prefix='test'+str(i),
          site_id=s.site_id,webhook_url='https://example.invalid/'+str(i)) for i in (1,2)]
    db.add_all(keys);db.commit()
    a=partner('SAME',s.site_id);a.key_id=keys[0].id
    b=partner('SAME',s.site_id);b.key_id=keys[1].id
    intent=IR.create_payment_intent({'session_id':s.id},db,a)
    with pytest.raises(HTTPException): IR.payment_status(intent['payment_id'],db,b)
    assert IR._webhook_url_for(db,a)==keys[0].webhook_url


def test_pos_reservation_can_only_cancel_with_terminal_no_charge_proof(db):
    s=parking(db);u=user(db)
    t=M.Device(site_id=s.site_id,name='POS',device_type='pos',device_key='pos-cancel',status='active')
    db.add(t);db.commit()
    intent=asyncio.run(PR.pos_prepare({'session_id':s.id,'terminal_id':t.device_key},db,u))
    with pytest.raises(HTTPException): PR.pos_cancel(intent['payment_id'],{},db,u)
    out=PR.pos_cancel(intent['payment_id'],{'terminal_id':t.device_key,'outcome':'NOT_CHARGED'},db,u)
    assert out['status']=='CANCELLED'


@pytest.mark.parametrize('provider',['QPAY','POS','CASH','TRANSFER','WALLET'])
def test_partner_name_cannot_impersonate_internal_payment_instrument(db,provider):
    s=parking(db);p=partner(provider,s.site_id)
    with pytest.raises(HTTPException) as error:
        IR.create_payment_intent({'session_id':s.id},db,p)
    assert error.value.status_code==403
    existing=M.Payment(session_id=s.id,provider=provider,payment_method='WALLET',
                       sender_invoice_no='reserved-name',amount=1000)
    db.add(existing);db.commit()
    with pytest.raises(HTTPException): IR.payment_status(existing.id,db,p)

