"""Synthetic registration data only; HTTP authorization and atomic status writes."""
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import auth, models as M
from app.database import Base, get_db
from app.routers import admin_router as R
from app.services.driver_import import import_rows, replacement_plan


@pytest.fixture
def ctx():
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    with Session(engine, autoflush=False) as db:
        user = M.User(username='synthetic', password_hash='unused', role='SUPER_ADMIN')
        db.add(user); db.commit()
        app = FastAPI(); app.include_router(R.router)
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[auth.get_current_user] = lambda: user
        with TestClient(app) as client:
            yield db, user, client
    engine.dispose()


def driver(db, **kw):
    values = dict(plate_number='1234УБА', full_name='Synthetic', company='Synthetic',
                  contract_type='SPECIAL', is_active=False,
                  valid_from=datetime.utcnow()-timedelta(days=2),
                  valid_to=datetime.utcnow()+timedelta(days=90))
    values.update(kw)
    row = M.RegisteredDriver(**values); db.add(row); db.commit()
    return row


def preview(client, rows, active=True):
    return client.post('/api/admin/drivers/bulk-status', json={
        'ids': [r.id for r in rows], 'is_active': active})


def apply(client, rows, proof, active=True):
    return client.post('/api/admin/drivers/bulk-status', json={
        'ids': [r.id for r in rows], 'is_active': active, 'dry_run': False,
        'preview_token': proof['preview_token']})


def test_preview_apply_and_repeated_request_preserve_entitlements(ctx):
    db, user, client = ctx
    row = driver(db, note='existing', free_first_minutes=120)
    dates = (row.valid_from, row.valid_to)
    proof = preview(client, [row]).json()
    assert proof['change_count'] == 1 and not row.is_active
    assert db.query(M.AuditLog).count() == 0
    response = apply(client, [row], proof)
    assert response.status_code == 200, response.text
    assert response.json()['changed'] == 1 and row.is_active
    assert (row.valid_from, row.valid_to) == dates
    assert row.free_first_minutes == 120 and row.note == 'existing'
    assert db.query(M.AuditLog).filter_by(entity_id=row.id).one().detail['is_active_before'] is False
    assert apply(client, [row], proof).status_code == 409
    assert db.query(M.Payment).count() == db.query(M.BarrierCommand).count() == 0


def test_expired_future_active_duplicate_and_selected_duplicates_are_skipped(ctx):
    db, _, client = ctx
    good = driver(db)
    expired = driver(db, plate_number='2222УБА', valid_to=datetime.utcnow()-timedelta(days=1))
    future = driver(db, plate_number='3333УБА', valid_from=datetime.utcnow()+timedelta(days=1))
    dup = driver(db, plate_number='4444УБА')
    driver(db, plate_number=dup.plate_number, is_active=True)
    pair = [driver(db, plate_number='5555УБА'), driver(db, plate_number='5555УБА')]
    rows = [good, expired, future, dup, *pair]
    proof = preview(client, rows).json()
    assert proof['change_count'] == 1 and proof['blocked_count'] == 5
    assert {r['reason'] for r in proof['items']} == {'change','expired','not_started','active_duplicate','selected_duplicate'}
    assert apply(client, rows, proof).json()['changed'] == 1
    assert good.is_active and not any(r.is_active for r in rows[1:])


def test_stale_preview_rejected_if_entitlement_changes(ctx):
    db, _, client = ctx
    row = driver(db); proof = preview(client, [row]).json()
    row.access_scope = 'inner'; db.commit()
    assert apply(client, [row], proof).status_code == 409
    assert not row.is_active


def test_new_duplicate_between_preview_and_apply_rejected(ctx):
    db, _, client = ctx
    row = driver(db); proof = preview(client, [row]).json()
    driver(db, is_active=True)
    assert apply(client, [row], proof).status_code == 409
    assert not row.is_active


@pytest.mark.parametrize('role,permissions', [('OPERATOR',['drivers']), ('ADMIN',[])])
def test_bulk_requires_admin_and_module_permission(ctx, role, permissions):
    db, user, client = ctx
    row = driver(db); user.role = role; user.permissions = permissions; db.commit()
    assert preview(client, [row]).status_code == 403
    assert not row.is_active


def test_foreign_tenant_in_selection_rejects_entire_batch(ctx):
    db, user, client = ctx
    own = M.Tenant(name='Own', code='OWN'); foreign = M.Tenant(name='Foreign', code='FOREIGN')
    db.add_all([own,foreign]); db.commit()
    user.role='ADMIN'; user.tenant_id=own.id; db.commit()
    first = driver(db, tenant_id=own.id); second = driver(db, tenant_id=foreign.id)
    assert preview(client,[first]).status_code == 200
    assert preview(client,[first,second]).status_code == 403
    assert not first.is_active and not second.is_active


def test_same_plate_other_tenant_not_a_duplicate(ctx):
    db, _, client = ctx
    row = driver(db, tenant_id=str(uuid4()))
    driver(db, tenant_id=str(uuid4()), is_active=True)
    assert preview(client,[row]).json()['change_count'] == 1


def test_deactivation_does_not_require_current_dates(ctx):
    db, _, client = ctx
    row = driver(db, is_active=True, valid_to=datetime.utcnow()-timedelta(days=1))
    proof = preview(client,[row],False).json()
    assert apply(client,[row],proof,False).json()['changed'] == 1
    assert not row.is_active


def test_bounded_payload_and_missing_confirmation(ctx):
    db, _, client = ctx
    row = driver(db)
    for body in ({'ids':[],'is_active':True}, {'ids':['invalid'],'is_active':True},
                 {'ids':[row.id]*2001,'is_active':True}, {'ids':[row.id],'is_active':'false'}):
        assert client.post('/api/admin/drivers/bulk-status',json=body).status_code == 422
    assert client.post('/api/admin/drivers/bulk-status',json={
        'ids':[row.id],'is_active':True,'dry_run':False}).status_code == 409
    assert not row.is_active


def test_list_status_filter(ctx):
    db, _, client = ctx
    off = driver(db); driver(db,plate_number='2222УБА',is_active=True)
    rows = client.get('/api/admin/drivers?is_active=false').json()
    assert [r['id'] for r in rows] == [off.id]


def test_import_replace_preserves_other_types_scopes_and_tenants(ctx):
    db, _, _ = ctx
    tenant, foreign = str(uuid4()), str(uuid4())
    hbi = driver(db,tenant_id=tenant,is_active=True)
    other = driver(db,tenant_id=foreign,is_active=True,contract_type='CONTRACT')
    inner = driver(db,tenant_id=tenant,is_active=True,contract_type='CONTRACT',access_scope='inner')
    missing = driver(db,tenant_id=tenant,is_active=True,contract_type='CONTRACT',plate_number='8888УБА')
    rows=[{'plate':'9999УБА','full_name':'Synthetic','company':'Company','note':''}]
    proof=replacement_plan(db,rows,None,tenant,'CONTRACT','site')
    assert proof['deactivate_count'] == 1
    result=import_rows(db,rows,None,default_tenant_id=tenant,deactivate_missing=True)
    assert result['deactivated']==1 and not missing.is_active
    assert hbi.is_active and other.is_active and inner.is_active
    # Import and audit remain one transaction; service cannot commit early.
    db.rollback()
    assert missing.is_active and db.query(M.RegisteredDriver).filter_by(plate_number='9999УБА').count()==0


def test_import_upsert_cannot_rewrite_other_tenant_same_plate(ctx):
    db, _, _ = ctx
    tenant, foreign = str(uuid4()), str(uuid4())
    other=driver(db,tenant_id=foreign,is_active=True)
    result=import_rows(db,[{'plate':other.plate_number,'full_name':'New','company':'New','note':''}],
                       None,default_tenant_id=tenant)
    assert result['created']==1 and result['updated']==0
    assert other.full_name=='Synthetic' and other.company=='Synthetic'


def test_import_does_not_dedupe_unrelated_plate(ctx):
    db, _, _ = ctx
    twins=[driver(db,is_active=True),driver(db,is_active=True)]
    result=import_rows(db,[{'plate':'9999УБА','full_name':'New','company':'New','note':''}],None)
    assert result['deduped']==0 and all(r.is_active for r in twins)


def test_import_cannot_silently_convert_special_entitlement_to_contract(ctx):
    from fastapi import HTTPException
    db, _, _ = ctx
    row=driver(db,is_active=True)
    with pytest.raises(HTTPException) as error:
        import_rows(db,[{'plate':row.plate_number,'full_name':'New','company':'New','note':''}],None)
    assert error.value.status_code==409
    assert row.contract_type=='SPECIAL' and row.is_active and row.full_name=='Synthetic'


def test_http_import_replacement_requires_fresh_preview_and_audits_together(ctx):
    from io import BytesIO
    from openpyxl import Workbook
    db, _, client = ctx
    row = driver(db, is_active=True, contract_type='CONTRACT')
    book=Workbook(); sheet=book.active; sheet.title='Synthetic'
    sheet.append(['Улсын дугаар','Эзэмшигч']); sheet.append(['9999УБА','Synthetic'])
    file=BytesIO(); book.save(file)
    def post(**kw):
        return client.post('/api/admin/drivers/import',
            files={'file':('synthetic.xlsx',file.getvalue())},
            data={'replace':'true','contract_type':'CONTRACT',**kw})
    assert post().status_code==409 and row.is_active
    proof=post(dry_run='true').json()
    assert proof['deactivate_count']==1
    response=post(replacement_token=proof['replacement_token'])
    assert response.status_code==200,response.text
    assert response.json()['deactivated']==1 and not row.is_active
    assert db.query(M.AuditLog).filter_by(action='IMPORT').one().detail['contract_type']=='CONTRACT'
    assert post(replacement_token=proof['replacement_token']).status_code==409
