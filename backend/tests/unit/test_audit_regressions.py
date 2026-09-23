"""Isolated regression tests: no live database, camera, payment or barrier calls."""
import asyncio
import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock
from unittest.mock import Mock
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import auth, models
from app.database import Base
from app.config import settings
from app.routers import admin_router as admin
from app.services import app_settings as A, snapshot as S, snap_puller as SP
from app.services.camera_tasks import ensure_camera_task, stop_camera_task

def jpeg_fixture(size=(64, 64)):
    from io import BytesIO
    from PIL import Image
    out = BytesIO()
    Image.new('RGB', size, 'red').save(out, format='JPEG')
    return out.getvalue()

JPEG = jpeg_fixture()


@pytest.fixture
def db():
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    A.invalidate_cache()
    with Session(engine, autoflush=False) as session:
        yield session
    A.invalidate_cache()
    engine.dispose()


def admin_user(**overrides):
    values = dict(id='admin', username='admin', role='ADMIN', permissions=None,
                  site_id=None, site_ids=None, tenant_id=None)
    values.update(overrides)
    return models.User(**values)


def test_global_capability_matches_write_authorization():
    assert auth.can_manage_global_settings(admin_user())
    scoped = admin_user(site_ids=['site-a'])
    assert not auth.can_manage_global_settings(scoped)
    with pytest.raises(HTTPException) as exc:
        admin.payment_rules_site_save('global', {}, db=None, user=scoped)
    assert exc.value.status_code == 403
    with pytest.raises(HTTPException):
        admin.put_exit_rules({}, db=None, user=scoped)
    with pytest.raises(HTTPException):
        admin.run_camsync_now({}, db=None, user=scoped)


def test_detached_tenant_user_does_not_gain_global_scope():
    assert auth.operator_sites(admin_user(tenant_id='tenant-a')) is not None


def test_settings_persist_across_sessions_and_commit_invalidates_stale_cache(db):
    A.set_rules(db, A.EXITRULES_KEY, {'no_session_fee': 1000}, 'admin')
    db.commit()
    A.set_rules(db, A.EXITRULES_KEY, {'no_session_fee': 5000}, 'admin')
    # A reader can publish a stale value while the write is not committed.
    import time
    A._cache[A.EXITRULES_KEY] = (time.monotonic(), {'no_session_fee': 1000}, {})
    assert A.get_rules(db, A.EXITRULES_KEY)['no_session_fee'] == 5000
    db.commit()
    with Session(db.bind) as fresh:
        assert A.get_rules(fresh, A.EXITRULES_KEY)['no_session_fee'] == 5000
    A.set_rules(db, A.EXITRULES_KEY, {'no_session_fee': 9000}, 'admin')
    assert A.get_rules(db, A.EXITRULES_KEY)['no_session_fee'] == 9000
    db.rollback()
    with Session(db.bind) as fresh:
        assert A.get_rules(fresh, A.EXITRULES_KEY)['no_session_fee'] == 5000


def test_string_false_is_not_saved_as_true(db):
    A.set_rules(db, A.BLACKLIST_KEY, {'auto_enabled': 'false'}, 'admin')
    db.commit()
    assert A.get_rules(db, A.BLACKLIST_KEY)['auto_enabled'] is False


def test_malformed_payload_picture_does_not_prevent_valid_fallback():
    assert S._payload_picture({'Picture': 'bad'}) is None
    assert S._payload_picture({'Picture': {'NormalPic': 'bad'}}) is None
    assert S._payload_picture({'Picture': {'NormalPic': {'Content': base64.b64encode(b'x'*1100).decode()},
                                          'CutoutPic': {'Content': base64.b64encode(JPEG).decode()}}}) == JPEG


def test_cgi_never_bypasses_lock(monkeypatch):
    from app.services import barrier
    async def scenario():
        lock = asyncio.Lock()
        await lock.acquire()
        client = SimpleNamespace(get=AsyncMock())
        monkeypatch.setattr(barrier, '_rpc_lock', lambda ip: lock)
        monkeypatch.setattr(barrier, 'barrier_is_waiting', lambda ip: False)
        monkeypatch.setattr(barrier, 'camera_client', lambda ip: client)
        monkeypatch.setattr(settings, 'snapshot_lock_wait_sec', .01)
        assert await S._fetch_from_camera('camera-lock', ('u','p')) is None
        client.get.assert_not_called()
        assert lock.locked()
        lock.release()
    asyncio.run(scenario())


def test_cgi_budget_and_auth_rejection_bound_requests(monkeypatch):
    from app.services import barrier
    async def scenario():
        lock = asyncio.Lock()
        monkeypatch.setattr(barrier, '_rpc_lock', lambda ip: lock)
        monkeypatch.setattr(barrier, 'barrier_is_waiting', lambda ip: False)
        client = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(status_code=401,content=b'')))
        monkeypatch.setattr(barrier, 'camera_client', lambda ip: client)
        assert await S._fetch_from_camera('camera-auth', ('u','p')) is None
        assert client.get.await_count == 1
        async def slow(*a, **kw):
            await asyncio.sleep(10)
        client.get = AsyncMock(side_effect=slow)
        monkeypatch.setattr(settings, 'snapshot_cgi_budget_sec', .03)
        started=asyncio.get_running_loop().time()
        assert await S._fetch_from_camera('camera-slow', ('u','p')) is None
        assert asyncio.get_running_loop().time()-started < .3
        assert not lock.locked()
    asyncio.run(scenario())


def test_best_picture_burst_has_fixed_deadline_and_one_timer(monkeypatch):
    async def scenario():
        attach=AsyncMock()
        monkeypatch.setattr(SP, '_attach_to_session', attach)
        monkeypatch.setattr(settings, 'snapshot_best_window_sec', .05)
        batch=SP._PictureBatch('cam-a','ip-a','entry','comet')
        await batch.offer('1234УБА',JPEG)
        first=batch.timers['1234УБА']
        await asyncio.sleep(.02)
        await batch.offer('1234УБА',jpeg_fixture((128, 128)))
        assert batch.timers['1234УБА'] is first
        await asyncio.wait_for(first,.08)
        assert attach.await_count == 1
        assert attach.await_args.args[3] == jpeg_fixture((128, 128))
        assert not batch.best and not batch.timers
    asyncio.run(scenario())


def test_duplicate_capture_schedule_creates_one_task(monkeypatch):
    async def scenario():
        release=asyncio.Event()
        capture=AsyncMock(side_effect=lambda *a: None)
        async def slow(*a):
            await capture(*a)
            await release.wait()
        monkeypatch.setattr(S,'_capture_and_store',slow)
        S.schedule_capture('same-session','ip','1234УБА','entry',{})
        S.schedule_capture('same-session','ip','1234УБА','entry',{})
        await asyncio.sleep(0)
        assert capture.await_count == 1
        task=S._capture_tasks[('same-session','entry')]
        release.set()
        await task
        await asyncio.sleep(0)
        assert not S._capture_tasks
    asyncio.run(scenario())


def test_camera_config_change_restarts_only_affected_stream():
    async def scenario():
        tasks, configs, active = {}, {}, set()
        async def stream(name):
            assert name not in active
            active.add(name)
            try:
                await asyncio.Event().wait()
            finally:
                active.remove(name)
        for i in range(4):
            await ensure_camera_task(tasks,configs,str(i),('old',),lambda i=i:stream(str(i)))
        await asyncio.sleep(0)
        originals=dict(tasks)
        assert not await ensure_camera_task(tasks,configs,'0',('old',),lambda:stream('0'))
        assert await ensure_camera_task(tasks,configs,'0',('new-ip','new-creds'),lambda:stream('0'))
        await asyncio.sleep(0)
        assert tasks['0'] is not originals['0'] and originals['0'].cancelled()
        assert all(tasks[str(i)] is originals[str(i)] for i in (1,2,3))
        for name in list(tasks):
            await stop_camera_task(tasks,configs,name)
        assert not tasks and not configs and not active
    asyncio.run(scenario())


def test_four_cameras_have_four_correct_barriers_and_reconcile_is_idempotent(db):
    from app.services.device_auto import ensure_lane_barriers
    from app.services.barrier import _resolve_device
    from app.session_logic import _find_barrier
    site=models.ParkingSite(name='Four cameras',site_code='FOUR',zone_code='A')
    other=models.ParkingSite(name='Other tenant',site_code='OTHER',zone_code='A')
    db.add_all([site,other]); db.flush()
    cameras=[models.Device(site_id=site.id,name=f'Camera {i}',device_type='camera',
               status='active',ip_address=f'10.255.0.{i}',lane_no=i,
               lane_dir='entry' if i<=2 else 'exit',nested_inner=False,device_key=f'key-{i}')
             for i in range(1,5)]
    db.add_all(cameras)
    db.add(models.Device(site_id=other.id,name='Other',device_type='camera',status='active',
                        ip_address='10.255.1.1',lane_no=1,lane_dir='entry',device_key='other'))
    result=ensure_lane_barriers(db,site_ids=[site.id])
    assert result['created']==4
    assert db.query(models.Device).filter_by(site_id=other.id,device_type='barrier').count()==0
    assert ensure_lane_barriers(db,site_ids=[site.id])['created']==0
    for camera in cameras:
        bar=_find_barrier(db,site.id,camera)
        assert bar is not None and bar.lane_no==camera.lane_no
        assert _resolve_device(db,bar)[0]==camera.ip_address
    cameras[0].lane_no=5
    # No commit/flush by caller: reconciliation must use the newly saved lane.
    ensure_lane_barriers(db,site_ids=[site.id])
    assert _find_barrier(db,site.id,cameras[0]).lane_no==5
    assert db.query(models.Device).filter_by(site_id=site.id,device_type='barrier').count()==4


def test_inner_and_outer_lane_identity_and_shared_direction_conflicts(db):
    site=models.ParkingSite(name='Nested',site_code='NEST',zone_code='A')
    db.add(site);db.flush()
    camera=models.Device(site_id=site.id,name='Outer',device_type='camera',status='active',
                         lane_no=1,lane_dir='entry',nested_inner=False,device_key='outer')
    db.add(camera);db.flush()
    assert admin._conflicting_lane(db,site.id,'camera',1,'entry',nested_inner=True) is None
    assert admin._conflicting_lane(db,site.id,'camera',1,'both') is camera


def test_scoped_admin_cannot_manage_unrestricted_or_foreign_tenant_user(db):
    scoped = admin_user(site_ids=['site-a'])
    with pytest.raises(HTTPException):
        admin._enforce_user_scope(scoped, set(), 'edit', db=db)
    tenant_admin = admin_user(site_ids=['site-a'], tenant_id='tenant-a')
    with pytest.raises(HTTPException):
        admin._enforce_user_scope(tenant_admin, set(), 'edit', target_tenant_id='tenant-b', db=db)
    with pytest.raises(HTTPException):
        admin._enforce_user_scope(tenant_admin, {'site-b'}, 'edit', target_tenant_id='tenant-a', db=db)
    admin._enforce_user_scope(tenant_admin, {'site-a'}, 'edit', target_tenant_id='tenant-a', db=db)


def test_four_camera_similar_plates_remain_separate_sessions(db, monkeypatch):
    from app import session_logic as L
    from app.services.device_auto import ensure_lane_barriers
    site = models.ParkingSite(name='Four camera events',site_code='EVENTS',zone_code='A',no_charge=True)
    db.add(site); db.flush()
    cams = [models.Device(site_id=site.id, name=f'Camera {i}', device_type='camera',
                         status='active', ip_address=f'10.255.2.{i}', lane_no=i,
                         lane_dir='entry' if i <= 2 else 'exit', nested_inner=False,
                         auto_open=True, device_key=f'events-{i}') for i in range(1,5)]
    db.add_all(cams); ensure_lane_barriers(db, site_ids=[site.id])
    monkeypatch.setattr(L, 'schedule_capture', Mock())
    monkeypatch.setattr(L, 'schedule_display', Mock())
    monkeypatch.setattr(L, 'notify', Mock())
    # Permit immediate departure and bypass external side effects only.
    A.set_site_rules(db, A.EXITRULES_KEY, site.id, {'min_stay_seconds': 0}, 'admin')
    db.commit()
    opened = []
    async def open_mock(db, barrier, *args, **kwargs):
        opened.append(barrier.lane_no)
        return SimpleNamespace(status='SUCCESS')
    monkeypatch.setattr(L, 'open_barrier', open_mock)
    monkeypatch.setattr(L, 'ensure_entry_barrier', AsyncMock(return_value=True))
    async def scenario():
        p1, p2 = '5155УХК', '5155УКК'
        assert L.is_duplicate_read(p1, p2)  # Old site-wide fuzzy dedup conflated them.
        a = await L.handle_entry(db, cams[0], p1, .99, {})
        b = await L.handle_entry(db, cams[1], p2, .99, {})
        assert a['action'] == b['action'] == 'entry'
        assert a['session_id'] != b['session_id']
        repeat = await L.handle_entry(db, cams[0], p1, .99, {})
        assert repeat['action'] == 'dedup'
        x = await L.handle_exit(db, cams[2], p1, .99, {})
        y = await L.handle_exit(db, cams[3], p2, .99, {})
        assert x['action'] != 'dedup' and y['action'] != 'dedup'
        assert db.get(models.ParkingSession,a['session_id']).exit_device_id == cams[2].id
        assert db.get(models.ParkingSession,b['session_id']).exit_device_id == cams[3].id
        assert db.query(models.ParkingSession).count() == 2
        assert opened == [3,4]
    asyncio.run(scenario())


def test_batch_shutdown_cancels_pending_and_finishes_active_attachment(monkeypatch):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        async def attach(*args, **kwargs):
            entered.set()
            await release.wait()
        monkeypatch.setattr(SP, '_attach_to_session', attach)
        monkeypatch.setattr(settings, 'snapshot_best_window_sec', .01)
        batch = SP._PictureBatch('device','ip','entry','comet')
        await batch.offer('active', JPEG)
        await entered.wait()
        monkeypatch.setattr(settings, 'snapshot_best_window_sec', 10)
        await batch.offer('pending', JPEG)
        closing = asyncio.create_task(batch.close())
        await asyncio.sleep(0)
        assert not closing.done()
        release.set()
        await closing
        assert not batch.best and not batch.timers
    asyncio.run(scenario())


def test_long_stay_exit_picture_attaches_while_awaiting_payment(db, monkeypatch):
    from app import ws
    site = models.ParkingSite(name='Long stay',site_code='LONG',zone_code='A')
    db.add(site); db.flush()
    cam = models.Device(site_id=site.id,name='Exit',device_type='camera',status='active',
                        lane_no=1,lane_dir='exit',device_key='long-exit')
    db.add(cam); db.flush()
    session = models.ParkingSession(site_id=site.id,plate_number='1234УБА',
                    entry_time=datetime.utcnow()-timedelta(days=4), exit_device_id=cam.id,
                    status='AWAITING_PAYMENT', last_exit_seen_at=datetime.utcnow())
    db.add(session); db.commit()
    monkeypatch.setattr(SP, 'SessionLocal', lambda: Session(db.bind))
    monkeypatch.setattr(S, '_save', lambda *a:'test/exit.jpg')
    monkeypatch.setattr(ws, 'notify', Mock())
    asyncio.run(SP._attach_to_session(cam.id,'1234УБА','exit',JPEG))
    db.refresh(session)
    assert session.exit_time is None and session.exit_snapshot == 'test/exit.jpg'


def test_historical_backfill_never_replaces_event_with_live_frame(monkeypatch):
    live = AsyncMock(return_value=JPEG)
    monkeypatch.setattr(S, '_fetch_from_camera', live)
    monkeypatch.setattr(settings, 'snapshot_stored_find', False)
    data, note = asyncio.run(SP.fetch_stored_picture('ip', datetime.utcnow()-timedelta(days=1)))
    assert data is None and note
    live.assert_not_called()


def test_stored_file_selection_requires_matching_plate_metadata(monkeypatch):
    download = AsyncMock(return_value=JPEG)
    monkeypatch.setattr(SP, '_download_file', download)
    now = datetime.utcnow()
    infos = [{'FilePath':'/other.jpg', 'PlateNumber':'9999УБА'},
             {'FilePath':'/unknown.jpg'},
             {'FilePath':'/match.jpg', 'PlateNumber':'1234УБА'}]
    result = asyncio.run(SP._pick_and_download(None, 'ip', 'sid', infos, now, plate='1234УБА'))
    assert result == JPEG
    download.assert_awaited_once_with(None, 'ip', 'sid', '/match.jpg')


def test_stored_search_timeout_releases_camera_lock(monkeypatch):
    from app.services import barrier
    async def scenario():
        lock = asyncio.Lock()
        monkeypatch.setattr(barrier, '_rpc_lock', lambda ip: lock)
        monkeypatch.setattr(barrier, 'barrier_is_waiting', lambda ip: False)
        monkeypatch.setattr(settings, 'snapshot_stored_find', True)
        monkeypatch.setattr(settings, 'snapshot_stored_budget_sec', .03)
        async def slow_login(self):
            await asyncio.sleep(10)
        monkeypatch.setattr(SP.DahuaRpc, 'login', slow_login)
        logout = AsyncMock()
        monkeypatch.setattr(SP.DahuaRpc, 'logout', logout)
        result, note = await SP.fetch_stored_picture('timeout-ip', datetime.utcnow())
        assert result is None and 'TimeoutError' in note
        assert not lock.locked()
        logout.assert_awaited_once()
    asyncio.run(scenario())


def test_backfill_with_missing_camera_does_not_guess_among_four_cameras(db):
    from app.routers.sessions_router import backfill_snapshot
    site = models.ParkingSite(name='Unknown camera',site_code='UNKNOWN',zone_code='A')
    db.add(site); db.flush()
    row = models.ParkingSession(site_id=site.id,plate_number='1234УБА',entry_time=datetime.utcnow())
    db.add(row); db.commit()
    with pytest.raises(HTTPException) as exc:
        asyncio.run(backfill_snapshot(row.id,'entry',db,admin_user()))
    assert exc.value.status_code == 400
