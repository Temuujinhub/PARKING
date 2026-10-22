"""Гараар бүртгэх үеийн «Байгууллага» (харилцагчийн №52, 2026-10-22).

  • гэрээт (CONTRACT) идэвхтэй бүртгэлд байгууллага заавал — 422;
  • бусад төрөл, идэвхгүй болгох, company/contract_type хөндөөгүй засвар унахгүй;
  • зай нэгтгэнэ; импортын хог нэрийг («Улсын дугаар») шинээр оруулахыг
    хориглоно, хуучнаараа үлдээхийг зөвшөөрнө;
  • company-search хог нэрийг санал болгохгүй (include_junk=true үед харуулна).
Synthetic SQLite — прод/тест DB хөндөхгүй.
"""
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import auth, models as M
from app.database import Base, get_db
from app.routers import admin_router as A
from app.routers.admin_router import _driver_company
from app.services.company_names import is_junk_company, normalize_company


@pytest.fixture
def ctx():
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    with Session(engine, autoflush=False) as db:
        user = M.User(username='synthetic', password_hash='unused', role='SUPER_ADMIN')
        db.add(user); db.commit()
        app = FastAPI(); app.include_router(A.router)
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[auth.get_current_user] = lambda: user
        with TestClient(app) as client:
            yield db, client
    engine.dispose()


def _body(**kw):
    b = {'plate_number': '1234УБА', 'contract_type': 'CONTRACT', 'valid_to': '2027-01-01'}
    b.update(kw)
    return b


def _row(db, **kw):
    v = dict(plate_number='5678УНЕ', contract_type='CONTRACT', company='',
             valid_from=datetime.utcnow() - timedelta(days=1),
             valid_to=datetime.utcnow() + timedelta(days=30))
    v.update(kw)
    r = M.RegisteredDriver(**v); db.add(r); db.commit(); return r


@pytest.mark.parametrize('name,junk', [
    ('Улсын дугаар', True), ('дугаар', True), ('Байгууллага 1', True), ('Байгууллага 2', True),
    ('data', True), ('эзэмшигч', True), ('Sheet1', True), ('Лист 3', True), ('байгууллага', True),
    ('  Улсын   дугаар: ', True), ('№', True), ('12', True),
    ('Голомт банк', False), ('SGS', False), ('nissan', False), ('3-р эмнэлэг', False),
    ('100 айл эмнэлэг', False), ('', False),
])
def test_junk_detection(name, junk):
    assert is_junk_company(name) is junk


def test_normalize_collapses_spaces():
    assert normalize_company('  Голомт   банк ') == 'Голомт банк'
    assert normalize_company(None) == ''


def test_contract_requires_company_on_create(ctx):
    _, c = ctx
    r = c.post('/api/admin/drivers', json=_body())
    assert r.status_code == 422 and 'байгууллаг' in r.json()['detail'].lower()
    r = c.post('/api/admin/drivers', json=_body(company='   '))
    assert r.status_code == 422


def test_contract_company_saved_normalized_with_position(ctx):
    _, c = ctx
    r = c.post('/api/admin/drivers', json=_body(company='  Голомт   банк ', note=' Захирал '))
    assert r.status_code == 200, r.text
    assert r.json()['company'] == 'Голомт банк' and r.json()['note'] == 'Захирал'


@pytest.mark.parametrize('ct', ['MONTHLY', 'VIP', 'STAFF', 'SPECIAL', 'TRANSIT', 'NIGHT'])
def test_other_types_do_not_require_company(ctx, ct):
    _, c = ctx
    assert c.post('/api/admin/drivers', json=_body(contract_type=ct)).status_code == 200


def test_new_junk_company_rejected_for_contract(ctx):
    _, c = ctx
    r = c.post('/api/admin/drivers', json=_body(company='Улсын дугаар'))
    assert r.status_code == 422 and 'Excel' in r.json()['detail']
    # гэрээт биш бол хориглохгүй (ХБИ жагсаалт г.м)
    assert c.post('/api/admin/drivers', json=_body(company='Sheet1', contract_type='SPECIAL')).status_code == 200


def test_update_legacy_without_company_untouched_fields_ok(ctx):
    """company/contract_type ирээгүй засвар (хугацаа сунгах, идэвхгүй болгох) — 566
    хуучин гэрээт бүртгэл байгууллагагүй тул тэдгээрийг гацаахгүй."""
    db, c = ctx
    row = _row(db)
    assert c.put(f'/api/admin/drivers/{row.id}', json={'valid_to': '2027-06-01'}).status_code == 200
    assert c.put(f'/api/admin/drivers/{row.id}',
                 json={'company': '', 'contract_type': 'CONTRACT', 'is_active': False}).status_code == 200


def test_update_contract_requires_company_when_sent(ctx):
    db, c = ctx
    row = _row(db)
    r = c.put(f'/api/admin/drivers/{row.id}', json={'company': '', 'contract_type': 'CONTRACT'})
    assert r.status_code == 422
    # MONTHLY → CONTRACT болгоход байгууллага хоосон бол 422
    row2 = _row(db, plate_number='1111УБА', contract_type='MONTHLY')
    assert c.put(f'/api/admin/drivers/{row2.id}', json={'contract_type': 'CONTRACT'}).status_code == 422
    assert c.put(f'/api/admin/drivers/{row2.id}',
                 json={'contract_type': 'CONTRACT', 'company': 'Хаан банк'}).status_code == 200


def test_update_keeps_existing_junk_but_rejects_new_junk(ctx):
    db, c = ctx
    row = _row(db, company='Улсын дугаар')
    ok = c.put(f'/api/admin/drivers/{row.id}', json={'company': 'Улсын дугаар', 'contract_type': 'CONTRACT',
                                                     'valid_to': '2027-02-01'})
    assert ok.status_code == 200, ok.text
    assert c.put(f'/api/admin/drivers/{row.id}', json={'company': 'Sheet1'}).status_code == 422
    assert c.put(f'/api/admin/drivers/{row.id}', json={'company': 'BSB мебел'}).status_code == 200


def test_validate_helper_does_not_touch_non_admin_paths():
    """_driver_company нь зөвхөн admin create/update-д — тусдаа функц тул импорт/түнш
    (driver_import, socialpay) замд дуудагддаггүй; _driver_validate-д нэмээгүй."""
    with pytest.raises(HTTPException):
        _driver_company({'contract_type': 'CONTRACT'})
    _driver_company({'contract_type': 'MONTHLY'})
    from app.routers.admin_router import _driver_validate
    _driver_validate({'contract_type': 'CONTRACT'})   # байгууллагагүй ч энд унахгүй


def test_company_search_hides_junk_by_default(ctx):
    db, c = ctx
    for i, comp in enumerate(['Улсын дугаар', 'Байгууллага 1', 'Голомт банк', 'Голомт банк', 'дугаар']):
        _row(db, plate_number=f'{i:04}УБА', company=comp)
    got = c.get('/api/admin/drivers/company-search', params={'q': 'а'}).json()
    assert [g['company'] for g in got] == ['Голомт банк'] and got[0]['count'] == 2
    got = c.get('/api/admin/drivers/company-search', params={'q': 'дугаар'}).json()
    assert got == []
    got = c.get('/api/admin/drivers/company-search', params={'q': 'дугаар', 'include_junk': 'true'}).json()
    assert {g['company'] for g in got} == {'Улсын дугаар', 'дугаар'}
