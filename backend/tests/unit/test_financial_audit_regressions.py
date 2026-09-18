"""Acceptance regressions for previously reproduced audit defects.
Only SQLite, mocked services and in-process HTTP/WebSockets are used.
"""
import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock
import pytest
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, update
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from app import auth, models as M
from app.database import Base, get_db
from app.routers import wallet_router as WR, ev_router as ER, billing_router as BR
from app.services import wallet as W, ev_billing as EV, ev_hub, invoicing, app_settings

@pytest.fixture
def db():
    engine = create_engine('sqlite://', poolclass=StaticPool,
                           connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    app_settings.invalidate_cache()
    with Session(engine, autoflush=False) as s:
        yield s
    app_settings.invalidate_cache()
    engine.dispose()


def client(db, user=None):
    app = FastAPI()
    app.include_router(WR.router)
    app.include_router(ER.router)
    app.dependency_overrides[get_db] = lambda: db
    if user is not None:
        app.dependency_overrides[auth.get_current_user] = lambda: user
    return TestClient(app)


def setup_scope(db):
    a, b = M.Tenant(name='Audit tenant A', code='AUD-A'), M.Tenant(name='Audit tenant B', code='AUD-B')
    db.add_all([a,b]); db.flush()
    sa = M.ParkingSite(name='Audit A', site_code='AUD-A', tenant_id=a.id)
    sb = M.ParkingSite(name='Audit B', site_code='AUD-B', tenant_id=b.id)
    db.add_all([sa,sb]); db.flush()
    user = M.User(username='audit-operator', password_hash='unused', role='OPERATOR',
                  tenant_id=a.id, site_id=sa.id, is_active=True)
    w = M.Wallet(tenant_id=b.id, plate_number='1234УБА', phone='99112233', balance=10000)
    plan = M.EvPricePlan(name='B plan', tenant_id=b.id, site_id=sb.id,
                        price_per_wh=1, min_amount=1000, max_amount=200000)
    db.add_all([user,w,plan]); db.flush()
    charger = M.EvCharger(site_id=sb.id, cp_id='AUDIT-CP-B', charger_key='AUDB',
                          price_plan_id=plan.id)
    db.add(charger); db.commit()
    return user,w,charger,sa,sb


def test_scoped_operator_cannot_access_foreign_wallet(db):
    user,w,_,_,_=setup_scope(db)
    with client(db,user) as c:
        response=c.get('/api/admin/wallets')
        assert response.status_code==200
        assert w.id not in [r['id'] for r in response.json()]
        response=c.post(f'/api/admin/wallets/{w.id}/adjust',
                        json={'direction':'DEBIT','amount':1000,'note':'audit synthetic'})
        assert response.status_code==403
        db.refresh(w)
        assert w.balance==10000


def test_scoped_admin_cannot_update_foreign_ev_plan(db):
    user,w,charger,_,_=setup_scope(db)
    user.role='ADMIN'; db.commit()
    with client(db,user) as c:
        response=c.put(f'/api/admin/ev/price-plans/{charger.price_plan_id}',
                       json={'price_per_wh':7})
        assert response.status_code==403


def test_public_lookup_does_not_disclose_token_or_replace_phone(db):
    _,w,charger,_,_=setup_scope(db)
    token=w.public_token
    with client(db) as c:
        response=c.post(f'/api/public/ev/{charger.charger_key}/lookup',
                        json={'plate':w.plate_number,'phone':'88000000'})
        assert response.status_code==403
        assert token not in response.text
        db.refresh(w)
        assert w.phone=='99112233'


def test_public_start_rejects_missing_wallet_token(db,monkeypatch):
    _,w,charger,_,_=setup_scope(db)
    monkeypatch.setattr(ev_hub,'connector_status',AsyncMock(return_value={'online':True,'status':'Preparing'}))
    start=AsyncMock(return_value={'id':'synthetic-command'})
    monkeypatch.setattr(ev_hub,'remote_start',start)
    with client(db) as c:
        response=c.post(f'/api/public/ev/{charger.charger_key}/start',
                        json={'plate':w.plate_number,'phone':'88000000','amount':2000})
        assert response.status_code==403
        db.refresh(w)
        assert w.balance==Decimal('10000')
        start.assert_not_awaited()


def test_wallet_get_webhook_reaches_literal_webhook(db):
    with client(db) as c:
        response=c.get('/api/public/wallet/webhook?payment_id=missing&token=test')
        assert response.status_code==200
        assert response.json()=='SUCCESS'
        # Same unrecognized callback through POST reaches the intended handler.
        assert c.post('/api/public/wallet/webhook?payment_id=missing&token=test').status_code==200


def test_wallet_lock_refreshes_identity_map_balance(db):
    _,w,_,_,_=setup_scope(db)
    wallet_id=w.id
    assert w.balance==Decimal('10000')  # loaded before lock
    with db.bind.begin() as conn:
        conn.execute(update(M.Wallet).where(M.Wallet.id==wallet_id).values(balance=20000))
    locked=W.lock_wallet(db,wallet_id)
    assert locked.balance==Decimal('20000')
    W.apply_ledger(db,locked,'DEBIT',1000,'PARKING')
    db.commit()
    db.refresh(w)
    assert w.balance==Decimal('19000')


def test_underpaid_qpay_topup_does_not_credit_wallet(db,monkeypatch):
    _,w,_,_,_=setup_scope(db)
    p=M.Payment(kind='WALLET_TOPUP',wallet_id=w.id,provider='QPAY',payment_method='QR',
                sender_invoice_no='audit-topup',provider_invoice_id='synthetic',
                amount=5000,vat_amount=0,status='PENDING')
    db.add(p); db.commit()
    monkeypatch.setattr(WR.qpay,'check_payment',AsyncMock(return_value={'paid':True,'paid_amount':1}))
    assert not asyncio.run(WR._verify_and_credit(db,p))
    db.refresh(w)
    assert w.balance==Decimal('10000')
    assert p.status=='REVIEW'


def test_ev_fallback_tariff_stays_in_own_site(db):
    _,_,charger,sa,_=setup_scope(db)
    charger.price_plan_id=None; db.commit(); db.refresh(charger)
    chosen=EV.default_plan(db,charger)
    foreign=M.EvCharger(site_id=sa.id,cp_id='AUD-A',charger_key='AUDA')
    db.add(foreign); db.commit()
    assert EV.default_plan(db,foreign).id!=chosen.id
    assert chosen.tenant_id!=sa.tenant_id


def test_negative_ev_energy_cannot_refund_money(db):
    _,w,charger,_,_=setup_scope(db)
    s=M.ChargeSession(charger_id=charger.id,wallet_id=w.id,plate_number=w.plate_number,
                       id_tag='audit',authorized_amount=1000,price_per_wh=1,wh_limit=1000,
                       status='RUNNING',ocpp_tx_id=777)
    db.add(s); db.flush()
    W.hold_for_charge(db,w.id,1000,s.id); db.commit()
    with pytest.raises(EV.EvError):
        asyncio.run(EV.on_tx_stopped(db,{'ocpp_tx_id':777,'energy_wh':-100}))
    db.refresh(w)
    assert w.balance==Decimal('9000')
    assert s.total_amount is None


def setup_parking_payment(db,monkeypatch):
    from app.routers import payments_router as PR
    user,w,_,_,site=setup_scope(db)
    s=M.ParkingSession(site_id=site.id,plate_number=w.plate_number,
                        entry_time=datetime.utcnow()-timedelta(hours=1),status='AWAITING_PAYMENT')
    db.add(s); db.commit()
    monkeypatch.setattr(PR,'session_fee_info',lambda *args,**kw:{'total_fee':1000,'base_fee':909,'vat_amount':91,'duration_minutes':60,'is_free':False})
    return w,s,PR


def test_external_wallet_timeout_after_charge_stops_for_reconciliation(db,monkeypatch):
    from app.session_logic import _wallet_auto_deduct
    from app.services import wallet_providers
    from types import SimpleNamespace
    w,s,PR=setup_parking_payment(db,monkeypatch)
    w.balance=0; db.commit()
    charged=[]
    async def uncertain_debit(*args,**kw):
        charged.append('first-provider')
        raise TimeoutError('synthetic response lost after charge')
    async def second_debit(*args,**kw):
        charged.append('second-provider')
        return {'ok':True,'tx_id':'synthetic'}
    providers=[SimpleNamespace(name='FIRST',balance=AsyncMock(return_value={'found':True,'balance':5000}),debit=uncertain_debit),
               SimpleNamespace(name='SECOND',balance=AsyncMock(return_value={'found':True,'balance':5000}),debit=second_debit)]
    monkeypatch.setattr(wallet_providers,'external_providers',lambda:providers)
    async def finalize(db,p):
        p.status='PAID'
    monkeypatch.setattr(PR,'_finalize_paid',finalize)
    assert not asyncio.run(_wallet_auto_deduct(db,s,1000))[1]
    assert charged==['first-provider']
    assert db.query(M.Payment).filter_by(session_id=s.id).one().status=='UNKNOWN'
    assert not asyncio.run(_wallet_auto_deduct(db,s,1000))[1]
    assert charged==['first-provider']


def test_cashier_day_filter_matches_report_local_day(db):
    from app.routers import cashier_router as CR, reports_router as RR
    user,_,_,site,_=setup_scope(db)
    shift=M.CashierShift(user_id=user.id,site_id=site.id,
                         opened_at=datetime(2026,9,18,18),status='CLOSED',
                         closed_at=datetime(2026,9,18,19))
    db.add(shift); db.commit()
    # These UTC times are 02:00-03:00 on September 19 in Ulaanbaatar.
    rows=CR.shift_report('2026-09-19','2026-09-19',site.id,db,user)
    assert [r['id'] for r in rows]==[shift.id]
    start,end=RR._range('2026-09-19','2026-09-19')
    assert start <= shift.opened_at < end


def test_same_company_name_is_invoiced_separately_for_each_tenant(db):
    from fastapi import HTTPException
    user,_,_,sa,sb=setup_scope(db)
    now=datetime.utcnow()
    for site,plate,fee in ((sa,'1111УБА',1000),(sb,'2222УБА',9000)):
        db.add(M.RegisteredDriver(plate_number=plate,company='Same company',site_id=site.id,
                                  tenant_id=site.tenant_id,monthly_fee=fee,contract_type='MONTHLY',
                                  valid_from=now-timedelta(days=30),valid_to=now+timedelta(days=30)))
    db.commit()
    mine=invoicing.generate_invoices(db,'2026-08',companies=invoicing.company_scope(db,user),
                                     allowed_site_ids=[sa.id])
    assert len(mine)==1 and mine[0].amount==Decimal('1000')
    assert {r['plate'] for r in mine[0].detail['cars']}=={'1111УБА'}
    theirs=invoicing.generate_invoices(db,'2026-08')
    assert len(theirs)==1 and theirs[0].amount==Decimal('9000')
    assert mine[0].owner_scope!=theirs[0].owner_scope
    with pytest.raises(HTTPException) as err: BR._get_scoped(db,user,theirs[0].id)
    assert err.value.status_code==403
    assert not invoicing.generate_invoices(db,'2026-08')


def test_legacy_invoice_requires_owner_review_before_regeneration(db):
    user,_,_,sa,_=setup_scope(db)
    db.add(M.RegisteredDriver(plate_number='1111УБА',company='Legacy',site_id=sa.id,
                              tenant_id=sa.tenant_id,monthly_fee=1000,contract_type='MONTHLY',
                              valid_to=datetime.utcnow()+timedelta(days=30)))
    db.add(M.CompanyInvoice(invoice_no='old',owner_scope='LEGACY',period='2026-08',company='Legacy',amount=1000))
    db.commit()
    with pytest.raises(ValueError,match='эзэмшлийг'):
        invoicing.generate_invoices(db,'2026-08')
    assert db.query(M.CompanyInvoice).count()==1


def socket_client(db):
    from app.ws import serve_site_socket
    app=FastAPI()
    @app.websocket('/ws/sites/{site_id}')
    async def socket(ws: WebSocket, site_id: str):
        await serve_site_socket(ws,site_id,session_factory=lambda:Session(db.bind))
    return TestClient(app)


def test_anonymous_websocket_never_subscribes(db):
    from app.ws import manager
    with socket_client(db) as c:
        with c.websocket_connect('/ws/sites/all') as ws:
            ws.send_json({})
            with pytest.raises(WebSocketDisconnect): ws.receive_json()
    assert not any(manager.connections.values())


def test_scoped_websocket_all_receives_only_assigned_site(db):
    from app.ws import manager
    user,_,_,sa,sb=setup_scope(db)
    token=auth.create_access_token(user)
    with socket_client(db) as c:
        with c.websocket_connect('/ws/sites/all') as ws:
            ws.send_json({'token':token})
            assert ws.receive_json()['type']=='READY'
            c.portal.call(manager.broadcast,sb.id,'FOREIGN',{'plate':'private'})
            c.portal.call(manager.broadcast,sa.id,'OWN',{'plate':'own'})
            assert ws.receive_json()['type']=='OWN'
        with c.websocket_connect(f'/ws/sites/{sb.id}') as ws:
            ws.send_json({'token':token})
            with pytest.raises(WebSocketDisconnect): ws.receive_json()


def test_existing_wallet_valid_token_preserves_phone_and_can_start(db,monkeypatch):
    _,w,charger,_,_=setup_scope(db)
    monkeypatch.setattr(ev_hub,'connector_status',AsyncMock(return_value={'online':True,'status':'Preparing'}))
    monkeypatch.setattr(ev_hub,'remote_start',AsyncMock(return_value={'id':'test'}))
    with client(db) as c:
        r=c.post(f'/api/public/ev/{charger.charger_key}/lookup',
                 json={'plate':w.plate_number,'phone':'88000000','wallet_token':w.public_token})
        assert r.status_code==200
        db.refresh(w);assert w.phone=='99112233'
        r=c.post(f'/api/public/ev/{charger.charger_key}/start',
                 json={'plate':w.plate_number,'phone':'88000000','wallet_token':w.public_token,'amount':2000})
        assert r.status_code==200
        db.refresh(w);assert w.balance==8000
