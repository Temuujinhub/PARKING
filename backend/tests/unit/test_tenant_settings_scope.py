"""Түрээслэгчийн хүрээ (2026-10-26, Тэмүүжин): түрээслэгч бүрийн админ бүх тохиргоо,
үйлдлээ өөрийн түрээслэгчийн хүрээнд саадгүй хийнэ; «Бүх зогсоол» = түрээслэгчийн бүх
зогсоол; SUPER_ADMIN бүх түрээслэгчид хандана. Сүлжээ/төхөөрөмж дуудахгүй."""
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import auth, models as M, schemas
from app.database import Base
from app.routers import admin_router as AR, integration_router as IR
from app.services import app_settings as A, partner_scope as PS


@pytest.fixture
def db():
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    A.invalidate_cache()
    with Session(engine, autoflush=False) as session:
        yield session
    A.invalidate_cache()
    engine.dispose()


@pytest.fixture
def world(db):
    """Хоёр түрээслэгч (EASYPARKING 2 зогсоол, MONNIS 1) + хэрэглэгчид."""
    ep = M.Tenant(name='ИйзиПаркинг ХХК', code='EASYPARKING')
    mn = M.Tenant(name='Моннис', code='MONNIS')
    db.add_all([ep, mn]); db.flush()
    s1 = M.ParkingSite(name='A', site_code='A', tenant_id=ep.id)
    s2 = M.ParkingSite(name='B', site_code='B', tenant_id=ep.id)
    s3 = M.ParkingSite(name='M', site_code='M', tenant_id=mn.id)
    db.add_all([s1, s2, s3]); db.flush()
    users = {
        'super': M.User(username='super', password_hash='x', role='SUPER_ADMIN'),
        'company': M.User(username='company', password_hash='x', role='ADMIN'),
        'ep_admin': M.User(username='ep_admin', password_hash='x', role='ADMIN', tenant_id=ep.id),
        'mn_admin': M.User(username='mn_admin', password_hash='x', role='ADMIN', tenant_id=mn.id),
        'site_admin': M.User(username='site_admin', password_hash='x', role='ADMIN',
                             tenant_id=ep.id, site_id=s1.id),
        'operator': M.User(username='op', password_hash='x', role='OPERATOR', tenant_id=ep.id),
    }
    db.add_all(users.values()); db.commit()
    return {'ep': ep, 'mn': mn, 's1': s1, 's2': s2, 's3': s3, **users}


# ── Эрхийн хүрээ ─────────────────────────────────────────────────────────────
def test_settings_scope_by_role(world):
    w = world
    assert auth.settings_tenant(w['super']) is None
    assert auth.settings_tenant(w['company']) is None          # түрээслэгчгүй компанийн админ
    assert auth.settings_tenant(w['ep_admin']) == w['ep'].id
    assert auth.settings_tenant(w['mn_admin']) == w['mn'].id
    for u in ('site_admin', 'operator'):
        with pytest.raises(HTTPException) as err:
            auth.settings_tenant(w[u])
        assert err.value.status_code == 403


# ── Тохиргооны давхарга ──────────────────────────────────────────────────────
def test_rule_layers_system_tenant_site(db, world):
    w = world
    K = A.EXITRULES_KEY
    A.set_rules(db, K, {'no_session_fee': 2000}, 'super')
    A.set_tenant_rules(db, K, w['mn'].id, {'no_session_fee': 5000}, 'mn_admin')
    A.set_site_rules(db, K, w['s2'].id, {'no_session_fee': 3000}, 'ep_admin')
    db.commit(); A.invalidate_cache()
    assert A.get_rules(db, K, w['s1'].id)['no_session_fee'] == 2000      # систем
    assert A.get_rules(db, K, w['s2'].id)['no_session_fee'] == 3000      # зогсоол
    assert A.get_rules(db, K, w['s3'].id)['no_session_fee'] == 5000      # түрээслэгч
    assert A.get_rules(db, K)['no_session_fee'] == 2000                   # системийн утга хөндөгдөөгүй
    assert A.get_rules(db, K, tenant_id=w['mn'].id)['no_session_fee'] == 5000
    # null → системийн утга руу буцна
    A.set_tenant_rules(db, K, w['mn'].id, {'no_session_fee': None}, 'mn_admin')
    db.commit(); A.invalidate_cache()
    assert A.get_rules(db, K, w['s3'].id)['no_session_fee'] == 2000


def test_tenant_layer_ignores_system_only_keys(db, world):
    w = world
    A.set_tenant_rules(db, A.CAMSYNC_KEY, w['ep'].id, {'times_per_day': 24}, 'ep_admin')
    db.commit(); A.invalidate_cache()
    assert A.get_rules(db, A.CAMSYNC_KEY, w['s1'].id)['times_per_day'] == 4


def test_tenant_admin_saves_general_rules_only_for_own_tenant(db, world):
    w = world
    out = AR.put_exit_rules({'no_session_fee': 7000}, db, w['ep_admin'])
    assert out['no_session_fee'] == 7000
    A.invalidate_cache()
    assert A.get_rules(db, A.EXITRULES_KEY, w['s1'].id)['no_session_fee'] == 7000
    assert A.get_rules(db, A.EXITRULES_KEY, w['s3'].id)['no_session_fee'] != 7000   # Моннис хөндөгдөөгүй
    assert A.get_rules(db, A.EXITRULES_KEY)['no_session_fee'] != 7000               # систем хөндөгдөөгүй
    assert AR.get_exit_rules_api(db, w['ep_admin'])['no_session_fee'] == 7000
    assert AR.get_exit_rules_api(db, w['mn_admin'])['no_session_fee'] != 7000
    with pytest.raises(HTTPException):
        AR.put_exit_rules({'no_session_fee': 1}, db, w['site_admin'])


def test_payment_rules_global_save_is_tenant_scoped(db, world):
    w = world
    rep = AR.payment_rules_site_save('global', {'blacklist_rules': {'debt_count': 9}}, db, w['mn_admin'])
    assert rep['tenant_id'] == w['mn'].id and 'Моннис' in rep['site_name']
    row = next(r for r in rep['rules'] if r['group'] == 'blacklist_rules' and r['key'] == 'debt_count')
    assert row['value'] == 9 and row['source'] == 'tenant' and row['system_value'] == 3
    site_rep = AR.payment_rules_site('' + w['s3'].id, db, w['mn_admin'])
    srow = next(r for r in site_rep['rules'] if r['group'] == 'blacklist_rules' and r['key'] == 'debt_count')
    assert srow['value'] == 9 and srow['source'] == 'tenant'
    assert A.get_blacklist_rules(db, w['s1'].id)['debt_count'] == 3


def test_open_reasons_per_tenant(db, world):
    w = world
    AR.put_open_reasons({'items': [{'code': 'mn_only', 'label': 'Моннисын шалтгаан'}]}, db, w['mn_admin'])
    mn = AR.list_open_reasons(False, db, w['mn_admin'])
    ep = AR.list_open_reasons(False, db, w['ep_admin'])
    assert [r['code'] for r in mn] == ['mn_only']
    assert 'mn_only' not in [r['code'] for r in ep]
    assert A.get_open_reasons(db, site_id=w['s3'].id)[0]['code'] == 'mn_only'
    assert A.open_reason_labels(db)['mn_only'] == 'Моннисын шалтгаан'


# ── API түлхүүр ──────────────────────────────────────────────────────────────
def test_tenant_admin_all_sites_key_is_tenant_scoped(db, world):
    w = world
    out = AR.create_partner_key({'name': 'chargex'}, db, w['ep_admin'])
    assert out['tenant_id'] == w['ep'].id and out['site_id'] is None
    key = db.get(M.PartnerKey, out['id'])
    assert sorted(PS.key_site_ids(db, key)) == sorted([w['s1'].id, w['s2'].id])
    listed = AR.list_partner_keys(db, w['ep_admin'])
    assert [k['name'] for k in listed['keys']] == ['chargex'] and listed['scope'] == 'tenant'
    assert AR.list_partner_keys(db, w['mn_admin'])['keys'] == []


def test_system_admin_must_choose_tenant(db, world):
    w = world
    with pytest.raises(HTTPException) as err:
        AR.create_partner_key({'name': 'nokey'}, db, w['super'])
    assert err.value.status_code == 400
    out = AR.create_partner_key({'name': 'socialpay2', 'tenant_id': w['mn'].id}, db, w['super'])
    assert out['tenant_id'] == w['mn'].id
    with pytest.raises(HTTPException):   # зогсоол өөр түрээслэгчийнх
        AR.create_partner_key({'name': 'bad', 'tenant_id': w['mn'].id, 'site_id': w['s1'].id}, db, w['super'])


def test_site_admin_creates_only_own_site_key(db, world):
    w = world
    with pytest.raises(HTTPException):
        AR.create_partner_key({'name': 'k1'}, db, w['site_admin'])
    out = AR.create_partner_key({'name': 'k2', 'site_id': w['s1'].id}, db, w['site_admin'])
    assert out['site_id'] == w['s1'].id and out['tenant_id'] == w['ep'].id


def test_other_tenant_key_cannot_be_managed(db, world):
    w = world
    out = AR.create_partner_key({'name': 'mnkey', 'tenant_id': w['mn'].id}, db, w['super'])
    for call in (lambda: AR.revoke_partner_key(out['id'], db, w['ep_admin']),
                 lambda: AR.set_partner_webhook(out['id'], {'url': 'https://x'}, db, w['ep_admin'])):
        with pytest.raises(HTTPException) as err:
            call()
        assert err.value.status_code == 403
    assert AR.revoke_partner_key(out['id'], db, w['mn_admin']) == {'ok': True}


def test_partner_api_scope_follows_tenant(db, world):
    w = world
    out = AR.create_partner_key({'name': 'tk', 'tenant_id': w['ep'].id}, db, w['super'])
    p = IR.PartnerAuth('tk'); p.site_ids = PS.key_site_ids(db, db.get(M.PartnerKey, out['id']))
    assert p.allows(w['s1'].id) and p.allows(w['s2'].id) and not p.allows(w['s3'].id)
    with pytest.raises(HTTPException):
        IR._check_site_scope(p, w['s3'].id)
    sites, _, _ = IR.site_catalog(db, p)
    assert {s.site_code for s in sites} == {'A', 'B'}
    legacy = IR.PartnerAuth('env')   # .env түлхүүр — хязгааргүй хэвээр
    assert legacy.allows(w['s3'].id)


# ── Түрээслэгчийн өөрийн мэдээлэл ───────────────────────────────────────────
def test_tenant_admin_edits_own_tenant_only(db, world):
    w = world
    upd = AR.update_tenant(w['ep'].id, schemas.TenantUpdate(register='1234567', code='EASYPARKING',
                                                            site_ids=[w['s1'].id, w['s2'].id]),
                           db, w['ep_admin'])
    assert upd['register'] == '1234567'
    with pytest.raises(HTTPException) as err:
        AR.update_tenant(w['mn'].id, schemas.TenantUpdate(register='1'), db, w['ep_admin'])
    assert err.value.status_code == 403
    for body in ({'code': 'HACK'}, {'site_ids': [w['s3'].id]}):
        with pytest.raises(HTTPException) as err:
            AR.update_tenant(w['ep'].id, schemas.TenantUpdate(**body), db, w['ep_admin'])
        assert err.value.status_code == 403
    assert [t['id'] for t in AR.list_tenants(db, w['ep_admin'])] == [w['ep'].id]
    assert len(AR.list_tenants(db, w['super'])) == 2


def test_platform_tenant_key_covers_tenantless_sites(db, world):
    w = world
    orphan = M.ParkingSite(name='O', site_code='O'); db.add(orphan); db.commit()
    ep_key = AR.create_partner_key({'name': 'epk', 'tenant_id': w['ep'].id}, db, w['super'])
    mn_key = AR.create_partner_key({'name': 'mnk', 'tenant_id': w['mn'].id}, db, w['super'])
    assert orphan.id in PS.key_site_ids(db, db.get(M.PartnerKey, ep_key['id']))
    assert orphan.id not in PS.key_site_ids(db, db.get(M.PartnerKey, mn_key['id']))
    # Түрээслэгчгүй зогсоолын тохиргоо системийн утгаар (ТТД/данс хөндөгдөхгүй)
    A.set_tenant_rules(db, A.EXITRULES_KEY, w['ep'].id, {'no_session_fee': 9000}, 'ep_admin')
    db.commit(); A.invalidate_cache()
    assert A.get_rules(db, A.EXITRULES_KEY, orphan.id)['no_session_fee'] != 9000


def test_site_admin_cannot_mint_vehicles_key(db, world):
    w = world
    with pytest.raises(HTTPException) as err:
        AR.create_partner_key({'name': 'veh', 'site_id': w['s1'].id, 'can_vehicles': True}, db, w['site_admin'])
    assert err.value.status_code == 403


def test_site_admin_cannot_enable_vehicles_later(db, world):
    w = world
    out = AR.create_partner_key({'name': 'sk', 'site_id': w['s1'].id}, db, w['site_admin'])
    with pytest.raises(HTTPException) as err:
        AR.set_partner_vehicles(out['id'], {'enabled': True}, db, w['site_admin'])
    assert err.value.status_code == 403
    assert 'vehicles' in AR.set_partner_vehicles(out['id'], {'enabled': True}, db, w['ep_admin'])['scopes']
