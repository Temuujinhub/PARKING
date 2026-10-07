"""Printed-board contract, including the SPORT duplicated-URL incident."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.auth import get_current_user
from app.config import settings
from app.database import Base, get_db
from app.models import AuditLog, ParkingSite, User
from app.routers import admin_router, public_router
from app.services.site_qr import validate_printed_qr_url

PRINTED = 'https://app.easy-parking.mn/checkout/565a3ddb-1c01-4201-ad1c-81fc449cbdd7'


@pytest.fixture
def api(monkeypatch):
    engine = create_engine('sqlite://', poolclass=StaticPool,
                           connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    monkeypatch.setattr(settings, 'public_base_url', 'https://app.easy-parking.mn')
    with Session(engine) as db:
        site = ParkingSite(site_code='SPORT', name='Sport', qr_url=PRINTED,
                           capacity=77, zone_code='A')
        db.add(site)
        db.commit()
        app = FastAPI()
        app.include_router(admin_router.router)
        app.include_router(public_router.router)
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: User(
            id='admin', username='test-admin', role='SUPER_ADMIN')
        with TestClient(app) as client:
            yield client, db, site
    engine.dispose()


@pytest.mark.parametrize('bad', [
    PRINTED + PRINTED, PRINTED + '\n' + PRINTED, PRINTED + 'https://other.test',
    'javascript:alert(1)', '/pay?site=SPORT', 'https://other.test/pay?site=SPORT',
    'https://app.easy-parking.mn@other.test/pay?site=SPORT',
    'https://app.easy-parking.mn/pay?site=SPORT&site=KH',
    'https://app.easy-parking.mn/pay?site=',
    'https://app.easy-parking.mn/checkout/SPORT%2Fbad',
    PRINTED + '#elsewhere', PRINTED + '?redirect=https%3A%2F%2Fother.test',
    'https://app.easy-parking.mn/checkout/' + 'x' * 2050,
])
def test_bad_url_cannot_be_saved_by_create_or_update(api, bad):
    client, db, site = api
    for method, path, body in [
        ('POST', '/api/admin/sites', {'name':'New', 'site_code':'NEW', 'qr_url':bad}),
        ('PUT', f'/api/admin/sites/{site.id}', {'qr_url':bad}),
    ]:
        response = client.request(method, path, json=body)
        assert response.status_code == 422
        assert response.json()['detail'][0]['loc'][-1] == 'qr_url'
    db.refresh(site)
    assert site.qr_url == PRINTED
    assert db.query(ParkingSite).count() == 1
    assert db.query(AuditLog).count() == 0


def test_fix_preserves_old_printed_uuid_and_records_both_values(api):
    client, db, site = api
    site.qr_url = PRINTED + PRINTED  # pre-existing bad persisted configuration
    db.commit()
    current_id = site.id
    assert current_id not in PRINTED
    response = client.put(f'/api/admin/sites/{site.id}', json={'qr_url':'  ' + PRINTED + '  '})
    assert response.status_code == 200
    assert response.json()['pay_url'] == PRINTED
    db.refresh(site)
    assert (site.id, site.capacity, site.zone_code) == (current_id, 77, 'A')
    audit = db.query(AuditLog).one()
    assert audit.detail['qr_url_change'] == {'before':PRINTED+PRINTED, 'after':PRINTED}
    for ref in ['SPORT', 'sport', current_id, PRINTED.rsplit('/',1)[1]]:
        response = client.get('/api/public/site/' + ref)
        assert response.status_code == 200
        assert response.json()['site_code'] == 'SPORT'


def test_unchanged_qr_does_not_claim_link_changed(api):
    client, db, site = api
    assert client.put(f'/api/admin/sites/{site.id}', json={'qr_url':PRINTED}).status_code == 200
    assert 'qr_url_change' not in db.query(AuditLog).one().detail


def test_qr_encodes_exact_board_link_and_is_not_cached(api, monkeypatch):
    import qrcode
    client, _, _ = api
    captured = []
    original = qrcode.QRCode.add_data
    def record(self, data, *args, **kwargs):
        captured.append(data)
        return original(self, data, *args, **kwargs)
    monkeypatch.setattr(qrcode.QRCode, 'add_data', record)
    response = client.get('/api/public/qr/SPORT.png')
    assert response.status_code == 200
    assert response.content.startswith(b'\x89PNG')
    assert captured == [PRINTED]
    assert response.headers['cache-control'] == 'no-store'


def test_legacy_bad_config_is_editable_but_cannot_generate_png(api):
    from app.serializers import site_pay_url
    client, db, site = api
    site.qr_url = PRINTED + PRINTED
    db.commit()
    assert site_pay_url(site) == PRINTED + PRINTED
    response = client.get('/api/public/qr/SPORT.png')
    assert response.status_code == 422
    assert 'Давхар' in response.json()['detail']


@pytest.mark.parametrize('value', [None, '', '   '])
def test_empty_custom_link_uses_existing_default(api, value):
    client, _, site = api
    response = client.put(f'/api/admin/sites/{site.id}', json={'qr_url':value})
    assert response.status_code == 200
    assert response.json()['qr_url'] is None
    assert response.json()['pay_url'] == 'https://app.easy-parking.mn/pay?site=SPORT'
    assert client.get('/api/public/qr/SPORT.png').status_code == 200


@pytest.mark.parametrize('url', [PRINTED, PRINTED.replace('checkout','check-cost'),
    'https://site.easy-parking.mn/pay?site=SPORT', 'https://stage.example/pay?site=SPORT'])
def test_known_routes_and_configured_deployment_origin_are_preserved(monkeypatch, url):
    monkeypatch.setattr(settings,'public_base_url','https://stage.example')
    assert validate_printed_qr_url(url) == url
