"""Four-camera recovery and truthful gate outcomes; synthetic data, no network."""
import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import models as M, session_logic as SL
from app.database import Base
from app.services import app_settings, camera_records as CR, camera_sync as CS


@pytest.fixture
def db(monkeypatch):
    def blocked(*a, **kw):
        raise AssertionError('No network in inner recovery tests')
    monkeypatch.setattr(httpx.Client, 'send', blocked)
    monkeypatch.setattr(httpx.AsyncClient, 'send', blocked)
    app_settings.invalidate_cache()
    CR._audit_cache.clear()
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with Session(engine, autoflush=False) as db:
        yield db
    engine.dispose()
    app_settings.invalidate_cache()
    CR._audit_cache.clear()


def scenario(db):
    now = datetime.utcnow() - timedelta(hours=12)
    site = M.ParkingSite(name='Synthetic', site_code='INNER', transit_max_hours=12)
    db.add(site); db.flush()
    cams = []
    for lane, direction, inner in ((1,'entry',False), (2,'exit',False),
                                    (3,'exit',True), (4,'entry',True)):
        cam = M.Device(site_id=site.id, name=f'Camera {lane}', device_key=f'key-{lane}',
                       device_type='camera', lane_no=lane, lane_dir=direction,
                       nested_inner=inner, ip_address=f'camera-{lane}', status='active')
        db.add(cam); cams.append(cam)
    stay = M.ParkingSession(site_id=site.id, plate_number='1234УБА',
                            entry_time=now, status='OPEN')
    db.add(stay); db.commit()
    return site, stay, cams, now


def record(camera, when, plate='1234УБА', **kw):
    return dict(device_id=camera.id, lane_dir=camera.lane_dir,
                time=when, plate=plate, event='gate_pass', **kw)


def replay(db, site, records, now, dry_run=False):
    return CS._sync_inner(db, site, {'inner_events':records},
                          now-timedelta(seconds=1), now+timedelta(hours=12),
                          {'skip_invalid_plate':True}, dry_run)


def live(db, camera, when, accepted=True, plate='1234УБА'):
    db.add(M.LprEvent(site_id=camera.site_id, device_id=camera.id,
                     plate_number=plate, lane_dir=camera.lane_dir,
                     created_at=when, accepted=accepted,
                     reject_reason=None if accepted else 'INNER_REGISTRATION_DENIED'))
    db.commit()


def test_outer_read_does_not_swallow_inner_entry_and_ten_hour_credit_is_once(db):
    site, stay, cams, now = scenario(db)
    live(db, cams[0], now)
    entry = record(cams[3], now+timedelta(seconds=30))
    exit_ = record(cams[2], now+timedelta(hours=10, seconds=30))
    assert replay(db, site, [entry, exit_], now) == (1,1)
    db.refresh(stay)
    assert stay.paused_since is None and stay.paused_minutes == 600
    assert replay(db, site, [entry, exit_], now) == (0,0)
    db.refresh(stay)
    assert stay.paused_minutes == 600
    assert db.query(M.AuditLog).filter_by(action='CAMERA_SYNC_INNER').count() == 2
    assert db.query(M.BarrierCommand).count() == db.query(M.Payment).count() == 0


@pytest.mark.parametrize('accepted', [True, False])
def test_same_camera_decision_is_not_replayed_even_if_rejected(db, accepted):
    site, stay, cams, now = scenario(db)
    when = now+timedelta(seconds=30)
    live(db, cams[3], when, accepted)
    assert replay(db, site, [record(cams[3],when)], now) == (0,0)
    assert stay.paused_since is None


@pytest.mark.parametrize('bad', ['missing_id','deleted','outer','direction','other_site','manual'])
def test_requires_current_inner_camera_identity(db, bad):
    site, stay, cams, now = scenario(db)
    ev = record(cams[3], now+timedelta(minutes=2))
    if bad == 'missing_id': ev.pop('device_id')
    if bad == 'deleted': cams[3].status = 'deleted'
    if bad == 'outer': cams[3].nested_inner = False
    if bad == 'direction': cams[3].lane_dir = 'exit'
    if bad == 'other_site':
        other = M.ParkingSite(name='Other', site_code='OTHER')
        db.add(other); db.flush(); cams[3].site_id = other.id
    if bad == 'manual': ev['event'] = 'manual_snap'
    db.commit()
    assert replay(db, site, [ev], now) == (0,0)
    assert stay.paused_since is None


@pytest.mark.parametrize('case', ['later_visit','awaiting','paid','closed','quoted','fee_locked'])
def test_historical_read_does_not_change_later_or_financially_settled_stay(db, case):
    site, stay, cams, now = scenario(db)
    if case == 'later_visit': stay.entry_time = now+timedelta(hours=2)
    if case == 'awaiting': stay.status = 'AWAITING_PAYMENT'
    if case == 'paid': stay.status = 'PAID'
    if case == 'closed': stay.status = 'CLOSED'
    if case == 'quoted': stay.payment_wait_started_at = now
    if case == 'fee_locked': stay.fee_locked = True
    db.commit()
    assert replay(db, site, [record(cams[3],now+timedelta(minutes=2))], now) == (0,0)
    assert stay.paused_since is None


def test_old_entry_does_not_reopen_pause_after_later_live_exit(db):
    site, stay, cams, now = scenario(db)
    live(db, cams[2], now+timedelta(hours=1))
    assert replay(db, site, [record(cams[3],now+timedelta(minutes=2))], now) == (0,0)
    assert stay.paused_since is None


def test_old_exit_does_not_clear_newer_live_pause(db):
    site, stay, cams, now = scenario(db)
    stay.paused_since = now+timedelta(hours=2); db.commit()
    assert replay(db, site, [record(cams[2],now+timedelta(hours=1))], now) == (0,0)
    assert stay.paused_since == now+timedelta(hours=2)


@pytest.mark.parametrize('scope', [None,'site','inner','both'])
def test_recovery_respects_inner_registration_policy(db, scope):
    site, stay, cams, now = scenario(db)
    site.inner_registered_only = True
    if scope:
        db.add(M.RegisteredDriver(plate_number=stay.plate_number, site_id=site.id,
                                  access_scope=scope, is_active=True,
                                  valid_from=now-timedelta(days=1), valid_to=now+timedelta(days=2)))
    db.commit()
    expected = int(scope in ('inner','both'))
    assert replay(db, site, [record(cams[3],now+timedelta(minutes=2))], now) == (expected,0)


def test_inner_dry_run_simulates_pair_but_writes_nothing(db):
    site, stay, cams, now = scenario(db)
    records = [record(cams[3],now+timedelta(seconds=30)),
               record(cams[2],now+timedelta(hours=10,seconds=30))]
    assert replay(db, site, records+records, now, dry_run=True) == (1,1)
    db.refresh(stay)
    assert stay.paused_since is None and stay.paused_minutes == 0
    assert db.query(M.LprEvent).count() == db.query(M.AuditLog).count() == 0


def test_camera_records_keep_stable_identity_and_separate_zones(db, monkeypatch):
    site, stay, cams, now = scenario(db)
    monkeypatch.setattr('app.services.device_auth.camera_credentials', lambda _: ('fake','fake'))
    monkeypatch.setattr(CR, 'fetch_snap_events', AsyncMock(return_value=[{
        'Time':CR.to_camera_epoch(now), 'PlateNumber':stay.plate_number,
        'event_name':'gate_pass', 'SnapSource':'Video'}]))
    result = CR.site_camera_events(db, site.id)
    assert {e['device_id'] for e in result['events']} == {cams[0].id,cams[1].id}
    assert {e['device_id'] for e in result['inner_events']} == {cams[2].id,cams[3].id}


def test_outer_log_dry_run_cannot_close_stay_or_create_debt(db, monkeypatch):
    site, stay, cams, now = scenario(db)
    monkeypatch.setattr(CS, 'site_camera_events', lambda *a, **kw: {
        'cameras':[], 'events':[record(cams[1],now+timedelta(hours=10))], 'inner_events':[]})
    result = CS.sync_site(db, site, {'min_age_minutes':30, 'lookback_hours':24,
                                   'skip_invalid_plate':True,'create_debt_log_exit':True}, dry_run=True)
    db.refresh(stay)
    assert stay.status == 'OPEN' and stay.exit_time is None
    assert db.query(M.Compensation).count() == db.query(M.AuditLog).count() == 0


@pytest.mark.parametrize('mode,expected', [('pending',False),('other_car',False),
                                         ('same_car',True),('no_session',False),
                                         ('success',True),('failed',False),('unknown',False)])
def test_inner_gate_only_reports_ack_for_this_stay(db, monkeypatch, mode, expected):
    site, stay, cams, now = scenario(db)
    gate = M.Device(site_id=site.id, name='Inner gate', device_type='barrier',
                    device_key='gate', nested_inner=True, lane_no=4, lane_dir='entry')
    db.add(gate); db.commit()
    monkeypatch.setattr(SL, '_find_barrier', lambda *a: gate)
    monkeypatch.setattr('app.services.barrier.open_in_flight', lambda _: mode == 'pending')
    monkeypatch.setattr(SL, '_barrier_rules', lambda *a: {'reopen_cooldown_sec':60})
    sid = None if mode == 'no_session' else stay.id
    if mode in ('other_car','same_car','no_session'):
        db.add(M.BarrierCommand(device_id=gate.id, session_id=stay.id if mode=='same_car' else None,
                                command='open', command_source='inner_entry', status='SUCCESS'))
        db.commit()
    sent = AsyncMock(return_value=SimpleNamespace(status=mode.upper(),response_text='synthetic'))
    monkeypatch.setattr(SL,'open_barrier',sent)
    result = asyncio.run(SL.ensure_inner_barrier(db,cams[3],sid,stay.plate_number,'inner_entry'))
    assert result is expected
    assert sent.await_count == int(mode in ('success','failed','unknown'))
