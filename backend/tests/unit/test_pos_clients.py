"""«Холбогдсон POS» (2026-10-15): POS аппын хүсэлтийг хариуны ДАРАА санах ойд нэгтгэж ≤1/60с
upsert хийх, heartbeat, админы тайлангийн tenant хүрээ.

Прод (audit1014) нотолгоо: pax_terminal бүртгэлтэй терминал 0; 'TDB-PAX-SITE01-01' нь 10-р сард 8
хэрэглэгч/7 зогсоолоос ирсэн (зааврын жишээ) → түлхүүр (user_id, terminal_id); POS аппын
бэлэн төлбөр 1.5.1/1.5.3 X-App-Version-тэй. Сүлжээ/Postgres-гүй — SQLite санах ой + TestClient.
"""
from datetime import datetime, timedelta

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import auth, models as M
from app.database import Base, get_db
from app.routers import admin_router, payments_router
from app.services import app_settings as A
from app.services import pos_clients as PC

TDB = 'TDB-PAX-SITE01-01'


class Clock:
    """time.monotonic-ийг удирдана — 60с throttle-ыг жинхэнэ хүлээлтгүй шалгана."""
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(PC.time, 'monotonic', c)
    return c


@pytest.fixture
def env(clock):
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    PC.reset_state()
    A.invalidate_cache()
    db = factory()
    t1 = M.Tenant(name='EasyParking', code='EP')
    t2 = M.Tenant(name='Моннис', code='MON')
    db.add_all([t1, t2]); db.flush()
    s1 = M.ParkingSite(name='3-р эмнэлэг', site_code='3EM', tenant_id=t1.id)
    s2 = M.ParkingSite(name='Спортын төв ордон', site_code='SPORT', tenant_id=t1.id)
    s3 = M.ParkingSite(name='Моннис билдинг', site_code='MON', tenant_id=t2.id)
    db.add_all([s1, s2, s3]); db.flush()
    u = {
        'zuun': M.User(username='pos.3zuun', role='OPERATOR', password_hash='x', site_ids=[s1.id],
                       tenant_id=t1.id),
        'sport': M.User(username='pos.sport', role='POS', password_hash='x', site_ids=[s2.id],
                        tenant_id=t1.id),
        'building': M.User(username='Building', role='OPERATOR', password_hash='x', site_ids=[s3.id],
                           tenant_id=t2.id),
        'multi': M.User(username='pos', role='OPERATOR', password_hash='x', site_ids=[s1.id, s2.id],
                        tenant_id=t1.id),
        'web': M.User(username='sogii', role='OPERATOR', password_hash='x', site_ids=[s2.id],
                      tenant_id=t1.id),
        'root': M.User(username='temuujin', role='SUPER_ADMIN', password_hash='x'),
        'ep_admin': M.User(username='ep.admin', role='ADMIN', password_hash='x', tenant_id=t1.id),
        'mon_admin': M.User(username='mon.admin', role='ADMIN', password_hash='x', tenant_id=t2.id),
    }
    db.add_all(u.values()); db.commit()
    yield {'engine': engine, 'factory': factory, 'db': db, 'u': u, 'sites': (s1, s2, s3)}
    db.close()
    PC.reset_state()
    A.invalidate_cache()
    engine.dispose()


def make_app(env, *extra_routers):
    app = FastAPI()
    app.add_middleware(PC.PosCaptureMiddleware)
    app.include_router(payments_router.router)
    app.include_router(admin_router.router)
    for r in extra_routers:
        app.include_router(r)

    def _db():
        s = env['factory']()
        try:
            yield s
        finally:
            s.close()
    app.dependency_overrides[get_db] = _db
    return app


def hdr(user, version='1.5.3', ua='Dart/3.4 (dart:io)', **extra):
    h = {'Authorization': f'Bearer {auth.create_access_token(user)}', 'User-Agent': ua}
    if version:
        h['X-App-Version'] = version
    h.update(extra)
    return h


def rows(env, **kw):
    with env['factory']() as s:
        return s.query(M.PosClient).filter_by(**kw).order_by(M.PosClient.terminal_id).all()


def flush(env, **kw):
    return PC.flush_due(session_factory=env['factory'], **kw)


# ── 1. Барих: толгой / query / зам / бие ──────────────────────────────────────
def test_capture_from_header_query_path_and_body(env, clock):
    from fastapi import APIRouter
    fake = APIRouter()

    @fake.post('/api/payments/pos/confirm')
    async def confirm(request: Request):     # төлбөрийн маршрутын ЗАГВАР (биеийг өөрөө уншина)
        body = await request.json()
        return {'got': body.get('terminal_id'), 'size': len(body.get('pad', ''))}

    u = env['u']
    app = FastAPI()
    app.add_middleware(PC.PosCaptureMiddleware)
    app.include_router(fake)

    @app.get('/api/payments/pos/terminal/{terminal_id}')
    def term(terminal_id: str, user=Depends(auth.get_current_user)):
        return {'t': terminal_id}

    @app.get('/api/sessions/recent-exits')
    def recent(site_id: str, user=Depends(auth.get_current_user)):
        return []

    app.dependency_overrides[get_db] = lambda: env['factory']()
    # confirm нь auth шаардахгүй загвар — auth_user-ийг гараар тавина
    @app.middleware('http')
    async def fake_auth(request, call_next):
        if request.url.path.endswith('/confirm'):
            request.state.auth_user = (u['zuun'].id, 'pos.3zuun', 'OPERATOR')
        return await call_next(request)

    with TestClient(app) as c:
        r = c.post('/api/payments/pos/confirm', json={'terminal_id': TDB, 'amount': 1000},
                   headers={'User-Agent': 'Dart/3.4', 'X-App-Version': '1.5.3'})
        assert r.status_code == 200
        # 8KB-аас том бие: апп БҮТЭН биеийг авна (middleware зөвхөн хуулна), бидний талд задлахгүй
        # → terminal_id-гүй → ижил хэрэглэгч+IP-ийн сүүлийн терминалд (санах ой) онооно
        big = 'x' * 20000
        r = c.post('/api/payments/pos/confirm', json={'terminal_id': '91186908', 'pad': big},
                   headers={'User-Agent': 'Dart/3.4', 'X-App-Version': '1.5.3'})
        assert r.json() == {'got': '91186908', 'size': 20000}
        c.get(f'/api/payments/pos/terminal/{TDB}', headers=hdr(u['sport']))
        c.get('/api/sessions/recent-exits', params={'site_id': env['sites'][1].id},
              headers=hdr(u['multi'], **{'X-Terminal-Id': '91186916'}))
    assert flush(env) == 3
    zuun = rows(env, user_id=u['zuun'].id)
    assert [(r.terminal_id, r.terminal_source) for r in zuun] == [(TDB, 'body')]
    assert zuun[0].payment_count == 2 and zuun[0].last_payment_at is not None
    assert zuun[0].request_count == 2 and zuun[0].last_endpoint == 'POST /api/payments/pos/confirm'
    sport = rows(env, user_id=u['sport'].id)
    assert [(r.terminal_id, r.terminal_source, r.site_id) for r in sport] == [
        (TDB, 'path', env['sites'][1].id)]
    multi = rows(env, user_id=u['multi'].id)
    # олон зогсоолтой хэрэглэгч — хүсэлтийн site_id (хүрээн дотор) зогсоолыг тогтооно
    assert [(r.terminal_id, r.terminal_source, r.site_id) for r in multi] == [
        ('91186916', 'header', env['sites'][1].id)]


def test_web_cashier_browser_not_captured_but_pos_app_is(env):
    u = env['u']
    app = make_app(env)
    with TestClient(app) as c:
        # веб касс: хөтөч, толгойгүй → бүртгэхгүй (POS замаар ч)
        c.get('/api/payments?limit=10', headers=hdr(u['web'], version=None, ua='Mozilla/5.0 Chrome'))
        c.get('/api/payments/pos/bootstrap', headers=hdr(u['web'], version=None, ua='Mozilla/5.0'))
        # role POS, хөтөч биш, толгойгүй (хуучин build) → бүртгэнэ
        c.get('/api/payments?limit=10', headers=hdr(u['sport'], version=None))
        # буруу токен (401) → бүртгэхгүй
        c.get('/api/payments?limit=10', headers={'Authorization': 'Bearer junk',
                                                 'X-App-Version': '1.5.3'})
        # хөтчийн хэрэглэгч X-App-Version илгээвэл (api.js илгээдэггүй) — POS апп гэж үзнэ
    flush(env)
    assert {r.username for r in rows(env)} == {'pos.sport'}
    sport = rows(env, user_id=u['sport'].id)[0]
    assert sport.pos_version is None and sport.terminal_id == '' and sport.site_id == env['sites'][1].id


def test_known_user_without_header_captured_only_outside_browser(env):
    u = env['u']
    PC.set_known_users({'pos.3zuun'})
    app = make_app(env)
    with TestClient(app) as c:
        c.get('/api/payments?limit=10', headers=hdr(u['zuun'], version=None))
        c.get('/api/payments?limit=10', headers=hdr(u['web'], version=None))          # мэдэгдэхгүй
        c.get('/api/payments?limit=10', headers=hdr(u['zuun'], version=None, ua='Mozilla/5.0'))
    flush(env)
    assert {r.username for r in rows(env)} == {'pos.3zuun'}
    assert rows(env)[0].request_count == 1


# ── 2. Throttle: ≤1 бичилт/60с, хувилбар/төлөв солигдвол ≤10с ─────────────────
def test_throttle_counts_accumulate_and_urgent_changes(env, clock):
    u = env['u']
    app = make_app(env)
    with TestClient(app) as c:
        for _ in range(5):
            c.get('/api/payments?limit=10', headers=hdr(u['sport'], **{'X-Terminal-Id': TDB}))
        assert flush(env) == 1                   # шинэ мөр → шууд
        for _ in range(7):
            c.get('/api/payments?limit=10', headers=hdr(u['sport'], **{'X-Terminal-Id': TDB}))
        clock.t += 30
        assert flush(env) == 0                   # 60с болоогүй — DB-д хүрэхгүй
        assert rows(env)[0].request_count == 5
        clock.t += 31
        assert flush(env) == 1
        assert rows(env)[0].request_count == 12
        # хувилбар солигдвол 10с-ийн дараа (60с биш)
        c.get('/api/payments?limit=10', headers=hdr(u['sport'], version='1.6.0',
                                                    **{'X-Terminal-Id': TDB}))
        clock.t += 5
        assert flush(env) == 0
        clock.t += 6
        assert flush(env) == 1
        # төлбөр яаралтай БИШ (бичилт олшрохгүй) — 60с-ийн хэвийн цонхоор
        c.get('/api/payments?limit=10', headers=hdr(u['sport'], version='1.6.0',
                                                    **{'X-Terminal-Id': TDB}))
        clock.t += 11
        assert flush(env) == 0
    r = rows(env)[0]
    assert (r.pos_version, r.prev_pos_version) == ('1.6.0', '1.5.3')
    assert r.version_changed_at is not None and r.request_count == 13
    clock.t += 60
    assert flush(env) == 1 and rows(env)[0].request_count == 14


def test_write_failure_keeps_counts_for_retry(env, clock, monkeypatch):
    u = env['u']
    app = make_app(env)
    with TestClient(app) as c:
        for _ in range(3):
            c.get('/api/payments?limit=10', headers=hdr(u['sport'], **{'X-Terminal-Id': TDB}))
    real = PC._write
    monkeypatch.setattr(PC, '_write', lambda db, snap: (_ for _ in ()).throw(RuntimeError('db down')))
    assert flush(env) == 0
    monkeypatch.setattr(PC, '_write', real)
    clock.t += 61
    assert flush(env) == 1
    assert rows(env)[0].request_count == 3


# ── 3. Heartbeat ─────────────────────────────────────────────────────────────
def test_heartbeat_validation():
    ok = PC.parse_heartbeat({'terminal_id': '91186239', 'pos_version': '1.6.0', 'status': 'PRINTER_ERROR',
                             'printer': 'PAPER_OUT', 'battery': '87', 'network': 'wifi',
                             'extra': {'sn': 'A9000-1'}})
    assert ok['battery'] == 87 and ok['status'] == 'PRINTER_ERROR' and ok['extra'] == {'sn': 'A9000-1'}
    assert PC.parse_heartbeat({}, header_terminal='T-1', header_version='1.5.3')['pos_version'] == '1.5.3'
    bad = [
        {},                                               # terminal_id алга
        {'terminal_id': 'bad id with spaces'},
        {'terminal_id': 'x' * 61},
        {'terminal_id': 'T', 'status': 'S' * 41},
        {'terminal_id': 'T', 'status': {'a': 1}},
        {'terminal_id': 'T', 'battery': 101},
        {'terminal_id': 'T', 'battery': True},
        {'terminal_id': 'T', 'battery': 'full'},
        {'terminal_id': 'T', 'extra': 'text'},
        {'terminal_id': 'T', 'extra': {'blob': 'я' * 600}},   # >1KB UTF-8
        {'terminal_id': 'T', 'site_id': "x' OR 1=1"},
    ]
    for b in bad:
        with pytest.raises(ValueError):
            PC.parse_heartbeat(b)
    with pytest.raises(ValueError):
        PC.parse_heartbeat(['not', 'dict'])
    # удирдлагын тэмдэгт цэвэрлэгдэнэ
    assert PC.parse_heartbeat({'terminal_id': 'T', 'status': 'OK\r\n\tX'})['status'] == 'OK X'


def test_heartbeat_endpoint_stores_status_and_reports_min_version(env, clock):
    u = env['u']
    db = env['db']
    A.set_rules(db, A.POSAPP_KEY, {'min_app_version': '1.6.0'}, 'test'); db.commit()
    A.invalidate_cache()
    app = make_app(env)
    with TestClient(app) as c:
        r = c.post('/api/payments/pos/heartbeat', headers=hdr(u['sport']),
                   json={'terminal_id': '91186920', 'status': 'ONLINE', 'battery': 55,
                         'site_id': env['sites'][2].id})   # хүрээнээс гадуурх зогсоол
        assert r.status_code == 200, r.text
        j = r.json()
        assert j['update_required'] is True and j['min_app_version'] == '1.6.0'
        assert j['terminal_id'] == '91186920'
        r = c.post('/api/payments/pos/heartbeat', headers=hdr(u['sport']), json={'battery': 500,
                                                                                'terminal_id': 'T'})
        assert r.status_code == 422
        c.post('/api/payments/pos/heartbeat', headers=hdr(u['sport'], version='1.6.1'),
               json={'terminal_id': '91186920', 'status': 'PRINTER_ERROR', 'printer': 'PAPER_OUT'})
    flush(env)
    clock.t += 11
    flush(env)
    rs = rows(env, user_id=u['sport'].id)
    hb = [r for r in rs if r.terminal_id == '91186920']
    assert len(hb) == 1
    h = hb[0]
    assert (h.status, h.printer, h.pos_version, h.heartbeat_count) == ('PRINTER_ERROR', 'PAPER_OUT', '1.6.1', 2)
    assert h.battery is None                  # сүүлийн heartbeat battery илгээгээгүй
    assert h.site_id == env['sites'][1].id    # хүрээнээс гадуурх hint үл тоогдож ганц зогсоол
    # 422 хүсэлт heartbeat-ийн төлөв бичихгүй (терминал 'T' нь X-хүсэлтийн алдаа болж л тоологдоно)
    assert all(r.status != 'ONLINE' or r.terminal_id == '91186920' for r in rs)


def test_x_pos_status_header_on_any_request(env):
    u = env['u']
    app = make_app(env)
    with TestClient(app) as c:
        c.get('/api/payments?limit=10', headers=hdr(u['zuun'], **{'X-Terminal-Id': 'P1',
                                                                   'X-POS-Status': 'OFFLINE_QUEUE\x01'}))
    flush(env)
    r = rows(env)[0]
    assert r.status == 'OFFLINE_QUEUE' and r.status_at is not None and r.terminal_id == 'P1'


# ── 4. terminal_id-гүй хүсэлтийг онох ────────────────────────────────────────
def test_terminal_less_requests_inferred_from_memory_and_db(env, clock):
    u = env['u']
    app = make_app(env)
    with TestClient(app) as c:
        c.get('/api/payments/pos/bootstrap', params={'terminal_id': TDB}, headers=hdr(u['zuun']))
        c.get('/api/payments?limit=10', headers=hdr(u['zuun']))      # terminal-гүй
    flush(env)
    rs = rows(env, user_id=u['zuun'].id)
    assert [(r.terminal_id, r.request_count) for r in rs] == [(TDB, 2)]
    # процесс дахин асав (санах ой хоосон) — DB-ээс ижил IP-ийн сүүлийн терминал
    PC.reset_state()
    with TestClient(app) as c:
        c.get('/api/payments?limit=10', headers=hdr(u['zuun']))
    flush(env)
    rs = rows(env, user_id=u['zuun'].id)
    assert [(r.terminal_id, r.request_count, r.terminal_source) for r in rs] == [(TDB, 3, 'inferred')]


def test_per_user_terminal_cap(env, monkeypatch):
    monkeypatch.setattr(PC, 'MAX_TERMINALS_PER_USER', 2)
    u = env['u']
    app = make_app(env)
    with TestClient(app) as c:
        for i in range(5):
            c.get('/api/payments?limit=10', headers=hdr(u['zuun'], **{'X-Terminal-Id': f'R{i}'}))
    flush(env)
    # шинэ мөр үүсэхгүй: илүүдэл нь тодорхойгүй мөрөнд → тэр нь DB-ээс ижил IP-ийн терминалд
    rs = rows(env)
    assert {r.terminal_id for r in rs} <= {'', 'R0', 'R1'} and len(rs) <= 3
    assert sum(r.request_count for r in rs) == 5


# ── 5. Админы тайлан: tenant хүрээ, тооцоолсон талбарууд ──────────────────────
def seed_clients(env, clock):
    u, (s1, s2, s3) = env['u'], env['sites']
    app = make_app(env)
    with TestClient(app) as c:
        c.get('/api/payments?limit=10', headers=hdr(u['zuun'], **{'X-Terminal-Id': TDB}))
        c.get('/api/payments?limit=10', headers=hdr(u['sport'], version='1.5.1', **{'X-Terminal-Id': TDB}))
        c.get('/api/payments?limit=10', headers=hdr(u['building'], version=None, ua='okhttp/4.9',
                                                    **{'X-Terminal-Id': '91160973'}))
        c.get('/api/payments?limit=10', headers=hdr(u['multi'], **{'X-Terminal-Id': 'M1'}))  # зогсоолгүй
    flush(env)
    return app


def test_admin_report_tenant_scope_and_flags(env, clock):
    u, (s1, s2, s3) = env['u'], env['sites']
    db = env['db']
    seed_clients(env, clock)
    # Building-ийн сүүлийн POS аппын бэлэн төлбөр — «терминал гаргасан» (EXTERNAL) баримттай
    sess = M.ParkingSession(site_id=s3.id, plate_number='1234УБА', entry_time=datetime.utcnow(),
                            status='CLOSED')
    db.add(sess); db.flush()
    pay = M.Payment(session_id=sess.id, provider='CASH', payment_method='CASH', source='POSAPP',
                    sender_invoice_no='inv-1', amount=1000, status='PAID', paid_at=datetime.utcnow(),
                    cashier_id=u['building'].id)
    db.add(pay); db.flush()
    db.add(M.VatReceipt(payment_id=pay.id, session_id=sess.id, amount=1000, vat_amount=90,
                        status='EXTERNAL', provider='TERMINAL'))
    A.set_site_rules(db, A.EBARIMT_KEY, s3.id, {'pos_terminal_issues': True}, 'test')
    db.commit(); A.invalidate_cache()

    root = PC.clients_report(db, u['root'])
    by = {(c['username'], c['terminal_id']): c for c in root['clients']}
    assert set(by) == {('pos.3zuun', TDB), ('pos.sport', TDB), ('Building', '91160973'), ('pos', 'M1')}
    assert by[('pos.3zuun', TDB)]['terminal_shared'] and by[('pos.3zuun', TDB)]['terminal_is_example']
    assert by[('pos.sport', TDB)]['version_state'] == 'behind'       # 1.5.1 < хамгийн сүүлийн 1.5.3
    assert by[('Building', '91160973')]['version_state'] == 'unknown'
    b = by[('Building', '91160973')]
    assert (b['payments_24h'], b['payments_7d'], b['external_7d']) == (1, 1, 1)
    assert b['receipt_mode'] == 'terminal' and b['receipt_methods'] == ['CARD', 'CASH']
    assert b['last_receipt']['status'] == 'EXTERNAL' and b['presence'] == 'online'
    assert root['latest_version'] == '1.5.3'

    # EasyParking-ийн админ: Моннисын мөр (IP/хэрэглэгч) ХАРАГДАХГҮЙ; олон зогсоолтой 'pos' нь
    # зогсоол тодорхойгүй ч хүрээ нь бүхэлдээ EP-ийнх → харагдана
    ep = PC.clients_report(db, u['ep_admin'])
    assert {c['username'] for c in ep['clients']} == {'pos.3zuun', 'pos.sport', 'pos'}
    assert {s['name'] for s in ep['sites']} == {'3-р эмнэлэг', 'Спортын төв ордон'}
    mon = PC.clients_report(db, u['mon_admin'])
    assert [c['username'] for c in mon['clients']] == ['Building']
    assert not mon['clients'][0]['terminal_shared']
    # зогсоолын шүүлт
    only = PC.clients_report(db, u['root'], site_id=s2.id)
    assert {c['username'] for c in only['clients']} == {'pos.sport', 'pos'}

    # доод хувилбар тавивал «behind» → «outdated»
    A.set_rules(db, A.POSAPP_KEY, {'min_app_version': '1.5.3'}, 'test'); db.commit(); A.invalidate_cache()
    root = PC.clients_report(db, u['root'])
    st = {c['username']: c['version_state'] for c in root['clients']}
    assert st['pos.sport'] == 'outdated' and st['pos.3zuun'] == 'ok'

    # хуучирсан (24ц+) / онлайн биш
    later = datetime.utcnow() + timedelta(hours=25)
    assert {c['presence'] for c in PC.clients_report(db, u['root'], now=later)['clients']} == {'stale'}


def test_admin_endpoints_permissions(env, clock):
    u = env['u']
    app = seed_clients(env, clock)
    s3 = env['sites'][2]
    env['db'].add_all([
        M.Device(site_id=s3.id, name='Гарах камер', device_type='camera', lane_dir='exit', lane_no=2,
                 device_key='cam-x', status='active'),
        M.Device(site_id=s3.id, name='Гарах хаалт', device_type='barrier', lane_dir='exit', lane_no=2,
                 status='active'),
        M.Device(site_id=s3.id, name='Орох камер', device_type='camera', lane_dir='entry', lane_no=1,
                 device_key='cam-y', status='active'),
        M.Device(site_id=s3.id, name='PAX', device_type='pax_terminal', device_key='91160973',
                 status='active', extra={'app_version': '1.5.3'}),
        M.Device(site_id=env['sites'][0].id, name='PAX EP', device_type='pax_terminal',
                 device_key='91186908', status='active'),
    ])
    env['db'].commit()
    with TestClient(app) as c:
        assert c.get('/api/admin/pos-clients', headers=hdr(u['zuun'])).status_code == 403
        r = c.get('/api/admin/pos-clients', headers=hdr(u['mon_admin'], version=None, ua='Mozilla/5.0'))
        assert r.status_code == 200 and [x['username'] for x in r.json()['clients']] == ['Building']
        # өөр түрээслэгчийн зогсоолоор шүүх → 403
        r = c.get('/api/admin/pos-clients', params={'site_id': env['sites'][0].id},
                  headers=hdr(u['mon_admin'], version=None, ua='Mozilla/5.0'))
        assert r.status_code == 403
        r = c.get('/api/admin/pos-settings', headers=hdr(u['mon_admin'], version=None, ua='Mozilla/5.0'))
        assert r.status_code == 200
        s = r.json()
        assert [x['site_name'] for x in s['ebarimt']['sites']] == ['Моннис билдинг']
        assert s['ebarimt']['sites'][0]['lanes'] == [
            {'lane_dir': 'entry', 'lane_no': 1, 'nested_inner': False, 'barrier': False, 'camera': True},
            {'lane_dir': 'exit', 'lane_no': 2, 'nested_inner': False, 'barrier': True, 'camera': True}]
        # өөр түрээслэгчийн бүртгэлтэй терминал харагдахгүй
        assert [(t['terminal_id'], t['app_version']) for t in s['terminals']] == [('91160973', '1.5.3')]
        assert {x['username'] for x in s['pos_users']} == {'Building'}
        assert s['can_edit_global'] is False
        # түрээслэгчийн админ ерөнхий доод хувилбарыг өөрчилж чадахгүй
        r = c.put('/api/admin/pos-settings', json={'min_app_version': '1.6.0'},
                  headers=hdr(u['mon_admin'], version=None, ua='Mozilla/5.0'))
        assert r.status_code == 403
        root = hdr(u['root'], version=None, ua='Mozilla/5.0')
        assert c.put('/api/admin/pos-settings', json={'min_app_version': '1.6 beta'},
                     headers=root).status_code == 400
        r = c.put('/api/admin/pos-settings', json={'min_app_version': '1.6.0'}, headers=root)
        assert r.status_code == 200 and r.json()['pos_app_rules']['min_app_version'] == '1.6.0'
        s = c.get('/api/admin/pos-settings', headers=root).json()
        assert {x['username'] for x in s['pos_users']} >= {'pos.sport', 'pos.3zuun', 'Building', 'pos'}
        assert 'sogii' not in {x['username'] for x in s['pos_users']}
        assert s['receipt_inline_wait_sec'] >= 0 and s['open_reasons']
        # админы өөрийн (хөтөч) хүсэлтүүд «холбогдсон POS» болохгүй
    flush(env)
    assert 'temuujin' not in {r.username for r in rows(env)}


def test_version_helpers():
    assert PC.version_key('v1.5.3+12') == (1, 5, 3, 0)
    assert PC.version_lt('1.5.1', '1.5.3') and not PC.version_lt('1.10.0', '1.9.9')
    assert not PC.version_lt('abc', '1.0') and not PC.version_lt(None, '1.0')
    assert PC.clean_terminal_id(' 91186239 ') == '91186239'
    assert PC.clean_terminal_id('a b') is None and PC.clean_terminal_id(True) is None


def test_get_current_user_still_works_without_connection():
    """ws.py нь get_current_user(token=, db=)-г шууд дууддаг — conn-гүй ажиллана."""
    import inspect
    assert inspect.signature(auth.get_current_user).parameters['conn'].default is None
