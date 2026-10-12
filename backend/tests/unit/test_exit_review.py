"""Synthetic evidence only. No camera, bank or gate network calls."""
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import auth, models as M, session_logic
from app.database import Base, get_db
from app.routers import exit_review_router as R


@pytest.fixture
def ctx(monkeypatch, tmp_path):
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread':False})
    Base.metadata.create_all(engine)
    with Session(engine, autoflush=False) as db:
        user = M.User(username='reviewer', password_hash='unused', role='SUPER_ADMIN')
        site = M.ParkingSite(name='Synthetic site', site_code='SYNTH')
        other = M.ParkingSite(name='Other site', site_code='OTHER')
        db.add_all([user,site,other]); db.commit()
        cam = M.Device(site_id=site.id, name='Exit camera', device_type='camera', lane_dir='exit', lane_no=2,
                       ip_address='192.0.2.1', status='active', nested_inner=False)
        bar = M.Device(site_id=site.id, name='Exit barrier', device_type='barrier', lane_dir='exit', lane_no=2,
                       status='active', nested_inner=False)
        stay = M.ParkingSession(site_id=site.id, plate_number='1234УБА', status='PAID',
                entry_time=datetime.utcnow()-timedelta(hours=1), paid_at=datetime.utcnow(), total_fee=1000)
        db.add_all([cam,bar,stay]); db.commit()
        event = M.LprEvent(site_id=site.id, device_id=cam.id, plate_number=stay.plate_number, lane_dir='exit',
                confidence=14, accepted=False, reject_reason='confidence<30.0',
                raw={'TrafficCar':{'PlateNumber':stay.plate_number}, 'password':'must-not-leak'})
        db.add(event); db.commit()
        monkeypatch.setattr(R.settings, 'snapshot_dir', str(tmp_path))
        (tmp_path/'entry.jpg').write_bytes(b'synthetic-entry')
        stay.entry_snapshot='entry.jpg'; db.commit()
        async def image(*args): return b'synthetic-image'
        def save(*args):
            name = str(uuid4())+'.jpg'; (tmp_path/name).write_bytes(b'synthetic-image'); return name
        monkeypatch.setattr(R.snapshot, '_fetch_from_camera', image)
        monkeypatch.setattr(R.snapshot, '_save', save)
        monkeypatch.setattr(R, 'session_fee_info', lambda *args,**kw: {'is_free':False,'total_fee':1000})
        monkeypatch.setattr(R, 'amount_due', lambda *args: 0)
        calls=[]
        async def close(db, camera, s, now, fee, **kwargs):
            calls.append((camera.id,s.id,kwargs)); s.status='CLOSED'; s.exit_time=now
            s.exit_confirmed=kwargs.get('exit_confirmed',True)
            db.add(M.BarrierCommand(device_id=bar.id,session_id=s.id,command='open',command_source='manual',status='SUCCESS'))
            db.commit()
            return {'barrier_opened':True}
        monkeypatch.setattr(session_logic, '_close_and_open', close)
        app=FastAPI(); app.include_router(R.router)
        app.dependency_overrides[get_db]=lambda:db
        app.dependency_overrides[auth.get_current_user]=lambda:user
        with TestClient(app) as client:
            yield db,user,site,other,cam,bar,stay,event,client,calls
    engine.dispose()


def proof(ctx):
    db,user,site,other,cam,bar,s,e,c,calls=ctx
    r=c.post(f'/api/exit-review/sessions/{s.id}/capture',json={'camera_id':cam.id,'event_id':e.id})
    assert r.status_code==200,r.text
    return r.json()['proof_id']


def resolve(ctx,pid,**extra):
    return ctx[8].post(f'/api/exit-review/sessions/{ctx[6].id}/resolve',json={
        'proof_id':pid,'confirmed_same_vehicle':True,'reason':'Matched both images',**extra})


def test_queue_is_scoped_does_not_fuzzy_match_or_expose_raw_secrets(ctx):
    db,user,site,other,cam,bar,s,e,c,calls=ctx
    similar=M.ParkingSession(site_id=site.id,plate_number='1235УБА',status='OPEN',entry_time=s.entry_time)
    db.add(similar);db.commit()
    r=c.get('/api/exit-review',params={'site_id':site.id}).json()
    assert r['events'][0]['candidates']==[{'id':s.id,'plate':s.plate_number,'entry_time':s.entry_time.isoformat()}]
    assert r['paid_waiting'][0]['id']==s.id
    assert 'must-not-leak' not in str(r) and not calls
    assert db.query(M.BarrierCommand).count()==0
    user.role='OPERATOR';user.permissions=['cashier'];user.site_id=other.id;db.commit()
    assert c.get('/api/exit-review',params={'site_id':site.id}).status_code==403
    assert c.get(f'/api/exit-review/events/{e.id}/image').status_code==403


def test_missing_module_permission_rejected(ctx):
    db,user,site,other,cam,bar,s,e,c,calls=ctx
    user.role='OPERATOR';user.permissions=[];db.commit()
    assert c.get('/api/exit-review',params={'site_id':site.id}).status_code==403
    assert c.post(f'/api/exit-review/sessions/{s.id}/capture',json={'camera_id':cam.id}).status_code==403


def test_paid_unbound_and_rejected_evidence_are_separate_facts(ctx):
    db,user,site,other,cam,bar,s,e,c,calls=ctx
    f=c.get(f'/api/exit-review/sessions/{s.id}').json()
    assert f['paid'] and not f['closed'] and f['read_state']=='rejected'
    assert f['exit_camera_id'] is None and f['command'] is None and f['physical_exit']=='unverified'


@pytest.mark.parametrize('kind',['foreign','entry','inactive','inner'])
def test_capture_never_falls_back_from_invalid_camera(ctx,kind):
    db,user,site,other,cam,bar,s,e,c,calls=ctx
    if kind=='foreign':cam.site_id=other.id
    if kind=='entry':cam.lane_dir='entry'
    if kind=='inactive':cam.status='inactive'
    if kind=='inner':cam.nested_inner=True
    db.commit()
    assert c.post(f'/api/exit-review/sessions/{s.id}/capture',json={'camera_id':cam.id}).status_code==400
    assert db.query(M.AuditLog).count()==0


@pytest.mark.parametrize('fault',['expired','wrong_actor','consumed','changed_plate','changed_path','not_confirmed','blank_reason','other_lane_car','changed_lane','missing_entry','inactive_site'])
def test_stale_or_unconfirmed_proof_cannot_open(ctx,fault):
    db,user,site,other,cam,bar,s,e,c,calls=ctx
    pid=proof(ctx);p=db.get(M.AuditLog,pid);extra={}
    if fault=='expired':p.created_at=datetime.utcnow()-timedelta(seconds=91)
    if fault=='wrong_actor':p.username='another_operator'
    if fault=='consumed':p.detail={**p.detail,'consumed_at':'previous'}
    if fault=='changed_plate':s.plate_number='9999УБА'
    if fault=='changed_path':s.verify_snapshot='missing.jpg'
    if fault=='changed_lane':cam.lane_no=3
    if fault=='missing_entry':s.entry_snapshot=None
    if fault=='inactive_site':site.is_active=False
    if fault=='not_confirmed':extra['confirmed_same_vehicle']=False
    if fault=='blank_reason':extra['reason']='   '
    if fault=='other_lane_car':db.add(M.LprEvent(site_id=site.id,device_id=cam.id,plate_number='9999УБА',lane_dir='exit',accepted=True,created_at=datetime.utcnow()+timedelta(seconds=1)))
    db.commit()
    assert resolve(ctx,pid,**extra).status_code in (400,409)
    assert not calls and s.exit_device_id is None


def test_outstanding_amount_and_debt_do_not_open(ctx,monkeypatch):
    db,user,site,other,cam,bar,s,e,c,calls=ctx
    s.exit_deadline=datetime.utcnow()-timedelta(minutes=1);db.commit()
    pid=proof(ctx)
    monkeypatch.setattr(R,'amount_due',lambda *args:1000)
    assert resolve(ctx,pid).status_code==409 and not calls
    monkeypatch.setattr(R,'amount_due',lambda *args:0)
    from app.services import app_settings as A
    A.set_site_rules(db,A.BLACKLIST_KEY,site.id,{'block_exit_debt_count':1},'test');db.commit()
    db.add(M.Compensation(site_id=site.id,session_id=s.id,plate_number=s.plate_number,amount=1000,reason='unpaid_exit',status='PENDING'));db.commit()
    assert resolve(ctx,pid).status_code==409 and not calls


@pytest.mark.parametrize('status',['PENDING','UNKNOWN'])
def test_uncertain_command_is_not_replayed(ctx,status):
    db,user,site,other,cam,bar,s,e,c,calls=ctx
    pid=proof(ctx)
    db.add(M.BarrierCommand(device_id=bar.id,session_id=s.id,command='open',command_source='payment',status=status));db.commit()
    assert resolve(ctx,pid).status_code==409 and not calls


def test_paid_recovery_preserves_ocr_and_payment_and_consumes_proof(ctx):
    db,user,site,other,cam,bar,s,e,c,calls=ctx
    pid=proof(ctx);paid_at=s.paid_at;result=resolve(ctx,pid)
    assert result.status_code==200,result.text
    assert calls==[(cam.id,s.id,{'source':'manual','exit_confirmed':False})]
    assert s.exit_device_id==cam.id and s.paid_at==paid_at and not s.exit_confirmed
    assert not e.accepted and e.confidence==14 and e.reject_reason=='confidence<30.0'
    f=result.json()['facts'];assert f['closed'] and f['acknowledged'] and f['physical_exit']=='unverified'
    assert db.query(M.Payment).count()==0
    assert db.query(M.AuditLog).filter_by(action='EXIT_REVIEW_RESOLVE').count()==1
    assert resolve(ctx,pid).status_code==409 and len(calls)==1


def test_free_recovery_checks_real_entitlement_even_if_amount_zero(ctx,monkeypatch):
    db,user,site,other,cam,bar,s,e,c,calls=ctx
    s.status='OPEN';s.paid_at=None;db.commit();pid=proof(ctx)
    assert resolve(ctx,pid).status_code==409 and not calls
    monkeypatch.setattr(R,'session_fee_info',lambda *args,**kw:{'is_free':True,'total_fee':0})
    assert resolve(ctx,pid).status_code==200 and len(calls)==1


def test_ambiguous_lane_refuses_to_pick_first_gate(ctx):
    db,user,site,other,cam,bar,s,e,c,calls=ctx
    pid=proof(ctx)
    db.add(M.Device(site_id=site.id,name='Duplicate gate',device_type='barrier',lane_dir='exit',lane_no=2,status='active',nested_inner=False));db.commit()
    assert resolve(ctx,pid).status_code==409 and not calls


@pytest.mark.parametrize('plate', ['1235УБА', '1234', '?', ''])
def test_live_exit_does_not_spend_nearby_paid_entitlement(ctx, monkeypatch, plate):
    import asyncio
    db,user,site,other,cam,bar,s,e,c,calls=ctx
    monkeypatch.setattr(session_logic, 'notify', lambda *a,**k:None)
    result=asyncio.run(session_logic.handle_exit(db,cam,plate,90,{'PlateNumber':plate}))
    assert result['action']=='review_required' and not result['barrier_opened']
    assert s.status=='PAID' and s.exit_device_id is None and not calls
    assert db.query(M.Payment).count()==0 and db.query(M.BarrierCommand).count()==0


def test_unbound_payment_is_saved_without_gate_command(ctx,monkeypatch):
    import asyncio
    db,user,site,other,cam,bar,s,e,c,calls=ctx
    events=[];monkeypatch.setattr(session_logic,'notify',lambda *args:events.append(args))
    asyncio.run(session_logic.mark_paid_and_open(db,s))
    assert s.status=='PAID' and s.paid_at and not calls
    assert events[-1][2]['gate_attention'] is None
    assert events[-1][2]['exit_wait_reason']=='waiting_for_exit_read'


def test_legacy_paid_shortcut_cannot_pick_first_exit_for_unbound_payment(ctx):
    import asyncio
    from fastapi import HTTPException
    from app.routers import sessions_router
    db,user,site,other,cam,bar,s,e,c,calls=ctx
    # 2026-10-12: a single outer exit lane is unambiguous and may be used; with
    # two exit lanes and no operator lane choice the shortcut must still refuse.
    db.add_all([M.Device(site_id=site.id, name='Exit camera 4', device_type='camera', lane_dir='exit', lane_no=4,
                         ip_address='192.0.2.4', status='active', nested_inner=False),
                M.Device(site_id=site.id, name='Exit barrier 4', device_type='barrier', lane_dir='exit', lane_no=4,
                         status='active', nested_inner=False)]); db.commit()
    with pytest.raises(HTTPException) as error:
        asyncio.run(sessions_router.special_exit(s.id,{'kind':'paid_no_open'},db,user))
    assert error.value.status_code==409 and not calls


def test_secondary_ocr_is_suggestion_only_and_site_scoped(ctx,monkeypatch):
    from app.services import exit_review_ocr
    db,user,site,other,cam,bar,s,e,c,calls=ctx
    monkeypatch.setattr(exit_review_ocr,'recognize',lambda raw:{'source':'event_frame','suggestions':[{'text':'9999УБА','confidence':92}], 'automatic_decision':False})
    r=c.post(f'/api/exit-review/events/{e.id}/ocr')
    assert r.status_code==200 and not r.json()['automatic_decision']
    assert s.plate_number=='1234УБА' and not e.accepted and not calls
    user.role='OPERATOR';user.permissions=['cashier'];user.site_id=other.id;db.commit()
    assert c.post(f'/api/exit-review/events/{e.id}/ocr').status_code==403


def test_unknown_valid_plate_does_not_open_on_zero_no_session_fee(ctx,monkeypatch):
    import asyncio
    db,user,site,other,cam,bar,s,e,c,calls=ctx
    monkeypatch.setattr(session_logic,'notify',lambda *args:None)
    monkeypatch.setattr(session_logic,'_exit_rules',lambda *args:{'fake_exit_minutes':5,'reopen_max_hours':12,'exit_reread_seconds':300,'no_session_fee':0})
    before=db.query(M.ParkingSession).count()
    r=asyncio.run(session_logic.handle_exit(db,cam,'9999УБА',90,{'PlateNumber':'9999УБА'}))
    assert r['action']=='review_required' and r['reason']=='no_matching_stay_review'
    assert not calls and db.query(M.ParkingSession).count()==before


def test_event_summary_query_does_not_select_picture_payload(ctx):
    from sqlalchemy.dialects import postgresql
    statement=str(R.event_query(ctx[0]).statement.compile(dialect=postgresql.dialect()))
    columns=statement.split('FROM')[0]
    assert 'lpr_events.raw,' not in columns
    assert 'raw_plate' in columns and 'coalesce' in columns


def test_active_stay_includes_rejections_after_provisional_exit_time(ctx):
    db,user,site,other,cam,bar,s,e,c,calls=ctx
    s.exit_time=e.created_at-timedelta(minutes=1);db.commit()
    f=c.get(f'/api/exit-review/sessions/{s.id}').json()
    assert f['read_state']=='rejected' and f['read']['raw_plate']==s.plate_number


def test_paid_grace_is_honored_in_facts_and_review(ctx,monkeypatch):
    db,user,site,other,cam,bar,s,e,c,calls=ctx
    s.exit_deadline=datetime.utcnow()+timedelta(minutes=15);db.commit()
    monkeypatch.setattr(R,'amount_due',lambda *a:1000)
    assert c.get(f'/api/exit-review/sessions/{s.id}').json()['amount_due']==0
    assert resolve(ctx,proof(ctx)).status_code==200 and len(calls)==1


def test_new_rejected_unknown_frame_invalidates_operator_proof(ctx):
    db,user,site,other,cam,bar,s,e,c,calls=ctx
    pid=proof(ctx)
    db.add(M.LprEvent(site_id=site.id,device_id=cam.id,plate_number='?',lane_dir='exit',
                     accepted=False,created_at=datetime.utcnow()+timedelta(seconds=1)));db.commit()
    assert resolve(ctx,pid).status_code==409 and not calls


def test_metadata_distinguishes_confidences_and_duplicate_ids_without_leaking_raw(ctx):
    db,user,site,other,cam,bar,s,e,c,calls=ctx
    e.raw={'TrafficCar':{'PlateNumber':s.plate_number,'Confidence':14},'Vehicle':{'Confidence':92},
           'RealUTC':1234,'FrameSequence':7,'password':'hidden'}
    db.add(M.LprEvent(site_id=site.id,device_id=cam.id,plate_number=s.plate_number,lane_dir='exit',
        accepted=False,created_at=e.created_at-timedelta(seconds=1),raw=e.raw));db.commit()
    r=c.get(f'/api/exit-review/events/{e.id}/evidence').json()
    assert r['plate_confidence']==14 and r['vehicle_confidence']==92
    assert r['distinct_observation_ids']==1 and 'hidden' not in str(r) and not calls


def test_attention_is_cleared_by_exit_ack_not_entry_ack(ctx):
    db,user,site,other,cam,bar,s,e,c,calls=ctx
    failed=M.BarrierCommand(device_id=bar.id,session_id=s.id,command='open',command_source='payment',
                            status='UNKNOWN',created_at=datetime.utcnow()-timedelta(minutes=1))
    entry=M.Device(site_id=site.id,name='Entry',device_type='barrier',lane_dir='entry',nested_inner=False)
    db.add_all([failed,entry]);db.flush()
    db.add(M.BarrierCommand(device_id=entry.id,session_id=s.id,command='open',command_source='auto_entry',status='SUCCESS'));db.commit()
    assert len(c.get('/api/exit-review',params={'site_id':site.id}).json()['command_attention'])==1
    db.add(M.BarrierCommand(device_id=bar.id,session_id=s.id,command='open',command_source='manual',status='SUCCESS'));db.commit()
    assert c.get('/api/exit-review',params={'site_id':site.id}).json()['command_attention']==[]


def test_advance_payment_and_broken_bound_gate_have_different_attention(ctx):
    db,user,site,other,cam,bar,s,e,c,calls=ctx
    assert c.get('/api/exit-review',params={'site_id':site.id}).json()['paid_waiting'][0]['attention_reason'] is None
    s.exit_device_id=cam.id;bar.status='inactive';db.commit()
    assert c.get('/api/exit-review',params={'site_id':site.id}).json()['paid_waiting'][0]['attention_reason']=='exit_mapping_invalid'
