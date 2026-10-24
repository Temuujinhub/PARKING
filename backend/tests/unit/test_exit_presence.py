"""Synthetic regression tests: camera, bank and gate networking is forbidden."""
import asyncio
import socket
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app import models as M, session_logic as SL
from app.database import Base
from app.routers import payments_router as PR, sessions_router as SR
from app.services import app_settings as A
from app.services.payment_validation import gate_result

FEE = dict(is_free=False, total_fee=1000, base_fee=909, vat_amount=91,
           discount_amount=0, duration_minutes=60, reason='audit')


@pytest.fixture
def ctx(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Network forbidden in synthetic audit')
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    import httpx
    monkeypatch.setattr(httpx.Client, 'send', forbidden)
    monkeypatch.setattr(httpx.AsyncClient, 'send', forbidden)
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    A.invalidate_cache()
    with Session(engine, autoflush=False) as db:
        tariff = M.TariffTemplate(name='Audit', free_minutes=0,
                                 extra_hour_price=1000, grace_minutes=15)
        site = M.ParkingSite(name='Synthetic', site_code='SYNTH', tariff_template=tariff)
        user = M.User(username='auditor', password_hash='unused', role='SUPER_ADMIN')
        db.add_all([site, user]); db.flush()
        cams=[]; bars=[]
        for lane in (1,2):
            cams.append(M.Device(site_id=site.id, name=f'Exit {lane}',
                        device_type='camera', lane_dir='exit', lane_no=lane,
                        status='active', nested_inner=False, ip_address=f'192.0.2.{lane}'))
            bars.append(M.Device(site_id=site.id, name=f'Barrier {lane}',
                        device_type='barrier', lane_dir='exit', lane_no=lane,
                        status='active', nested_inner=False))
        terminal=M.Device(site_id=site.id, name='Synthetic POS', device_type='pax_terminal',
                          lane_no=1, lane_dir='exit', device_key='SYNTHETIC-ONLY', status='active')
        db.add_all(cams+bars+[terminal]); db.commit()
        messages=[]; commands=[]
        monkeypatch.setattr(SL, 'notify', lambda sid, kind, data: messages.append((kind,data)))
        monkeypatch.setattr(SL, 'schedule_capture', lambda *a, **kw: None)
        monkeypatch.setattr(SL, 'schedule_display', lambda *a, **kw: None)
        monkeypatch.setattr(SL, '_inner_lane_devices', lambda *a: [])
        monkeypatch.setattr(SL, 'session_fee_info', lambda *a, **kw: dict(FEE))
        monkeypatch.setattr(PR, 'session_fee_info', lambda *a, **kw: dict(FEE))
        monkeypatch.setattr(SR, 'session_fee_info', lambda *a, **kw: dict(FEE))
        monkeypatch.setattr(PR, '_retire_qpay', AsyncMock())
        A.set_site_rules(db,A.EXITRULES_KEY,site.id,{'wallet_auto_deduct':False},'audit')
        db.commit()
        async def open_fake(db, barrier, session_id, source, **kw):
            commands.append((barrier.id, session_id, source))
            cmd=M.BarrierCommand(device_id=barrier.id,session_id=session_id,
                                 command='open',command_source=source,status='SUCCESS')
            db.add(cmd); db.commit()
            return cmd
        monkeypatch.setattr(SL, 'open_barrier', open_fake)
        yield SimpleNamespace(db=db,site=site,user=user,cams=cams,bars=bars,
                              terminal=terminal,tariff=tariff,messages=messages,commands=commands)
    A.invalidate_cache(); engine.dispose()


def stay(c, plate='1234УБА', status='OPEN', camera=None, age=60):
    s=M.ParkingSession(site_id=c.site.id,plate_number=plate,status=status,
            entry_time=datetime.utcnow()-timedelta(minutes=age),
            exit_device_id=camera.id if camera else None)
    c.db.add(s); c.db.commit(); return s


def queue(c, **kw):
    return SR.recent_exits(site_id=c.site.id, db=c.db, user=c.user, **kw)


def read(c, s, camera=None, *, age=0, plate=None, accepted=True, raw=None):
    cam = camera or c.cams[0]
    e=M.LprEvent(site_id=c.site.id,device_id=cam.id,plate_number=plate or s.plate_number,
        lane_dir='exit',confidence=90,accepted=accepted,created_at=datetime.utcnow()-timedelta(seconds=age),raw=raw or {})
    s.exit_device_id=cam.id; s.last_exit_seen_at=e.created_at
    c.db.add(e);c.db.commit();return e


@pytest.mark.parametrize('grace',[15,30])
def test_prepayment_waits_then_fresh_arrival_opens_correct_gate(ctx,grace):
    c=ctx;c.tariff.grace_minutes=grace;c.db.commit();s=stay(c)
    asyncio.run(SL.mark_paid_and_open(c.db,s,fee_snapshot=dict(FEE)))
    assert s.status=='PAID' and not c.commands
    assert s.exit_deadline-s.paid_at==timedelta(minutes=grace)
    result=asyncio.run(SL.handle_exit(c.db,c.cams[1],s.plate_number,95,{}))
    assert result['barrier_opened'] and s.status=='CLOSED'
    assert c.commands[0][0]==c.bars[1].id


@pytest.mark.parametrize('fault',['stale','rejected','different_car','unknown_car','no_read','inner','disabled','no_gate','queued_stale'])
def test_payment_is_retained_without_unsafe_open(ctx,fault):
    c=ctx;s=stay(c,camera=c.cams[0]);e=read(c,s)
    if fault=='stale':e.created_at-=timedelta(minutes=20)
    if fault=='rejected':e.accepted=False
    if fault=='different_car':read(c,s,plate='9999УБА')
    if fault=='unknown_car':read(c,s,plate='?',accepted=False)
    if fault=='no_read':c.db.delete(e)
    if fault=='inner':c.cams[0].nested_inner=True
    if fault=='disabled':c.cams[0].status='inactive'
    if fault=='no_gate':c.bars[0].status='inactive'
    if fault=='queued_stale':e.raw={'_server_open_eligible':False}
    c.db.commit()
    asyncio.run(SL.mark_paid_and_open(c.db,s,fee_snapshot=dict(FEE)))
    assert s.status=='PAID' and s.paid_at and not c.commands
    assert c.db.query(M.AuditLog).filter_by(action='PAYMENT_EXIT_WAIT').count()==1


def test_fresh_read_then_payment_opens_once_on_its_gate(ctx):
    c=ctx;s=stay(c);read(c,s,c.cams[1])
    asyncio.run(SL.mark_paid_and_open(c.db,s,fee_snapshot=dict(FEE)))
    assert s.status=='CLOSED' and c.commands==[(c.bars[1].id,s.id,'payment')]


def test_recent_queue_is_lane_scoped_and_uses_read_time_not_row_update(ctx):
    c=ctx;s=stay(c,status='AWAITING_PAYMENT');read(c,s,c.cams[1])
    assert queue(c,device_id=c.bars[0].id)==[]
    assert queue(c,device_id=c.bars[1].id)[0]['exit_camera_id']==c.cams[1].id
    s.last_exit_seen_at-=timedelta(minutes=20);s.updated_at=datetime.utcnow();c.db.commit()
    assert queue(c)==[]


@pytest.mark.parametrize('params',[{'device_id':'invalid'},{'lane_no':9},{'device_id':'gate1','lane_no':2}])
def test_bad_lane_never_broadens_queue(ctx,params):
    from fastapi import HTTPException
    c=ctx;s=stay(c,status='AWAITING_PAYMENT');read(c,s,c.cams[1])
    if params.get('device_id')=='gate1':params={**params,'device_id':c.bars[0].id}
    with pytest.raises(HTTPException):queue(c,**params)


def test_legacy_pos_prepare_still_works_and_new_context_is_validated(ctx):
    from fastapi import HTTPException
    c=ctx;s=stay(c);e=read(c,s,c.cams[1])
    body={'session_id':s.id,'terminal_id':c.terminal.device_key,'expected_amount':1000}
    with pytest.raises(HTTPException) as err:
        asyncio.run(PR.pos_prepare({**body,'device_id':c.bars[0].id},c.db,c.user))
    assert err.value.status_code==409 and c.db.query(M.Payment).count()==0
    result=asyncio.run(PR.pos_prepare(body,c.db,c.user))
    assert result['exit_context']=='inferred_from_exit_read' and result['amount']==1000 and not c.commands
    assert result['exit_lane_context']['gate_id']==c.bars[1].id
    assert result['exit_lane_context']['event_id']==e.id
    assert c.terminal.lane_no==1  # Transaction inference must not reconfigure the POS.
    audit=c.db.query(M.AuditLog).filter_by(action='POS_EXIT_CONTEXT').one()
    assert audit.detail['terminal_id']==c.terminal.device_key and audit.detail['phase']=='prepare'


def test_terminal_legacy_shape_and_explicit_lane_selection(ctx):
    c=ctx;s=stay(c,status='AWAITING_PAYMENT');read(c,s,c.cams[1])
    out=PR.pos_terminal_info(c.terminal.device_key,c.db,c.user)
    assert out['awaiting'][0]['session_id']==s.id and out['awaiting'][0]['amount_due']==1000
    assert PR.pos_terminal_info(c.terminal.device_key,c.db,c.user,device_id=c.bars[0].id)['awaiting']==[]


def test_gate_result_includes_later_auto_exit_but_excludes_entry(ctx):
    c=ctx;s=stay(c);p=M.Payment(session_id=s.id,provider='QPAY',sender_invoice_no='synthetic',payment_method='QR',status='PAID',amount=1000,paid_at=datetime.utcnow()-timedelta(minutes=1))
    c.db.add(p);c.db.commit()
    c.db.add(M.BarrierCommand(session_id=s.id,device_id=c.bars[1].id,command='open',command_source='auto_exit',status='SUCCESS'));c.db.commit()
    assert gate_result(c.db,p)['gate_acknowledged']
    c.bars[1].lane_dir='entry';c.db.commit()
    assert gate_result(c.db,p)['barrier_command_status']=='NOT_REQUESTED'


def test_minimum_stay_rule_survives_payment_callback(ctx):
    c=ctx;s=stay(c,age=0);read(c,s)
    A.set_site_rules(c.db,A.EXITRULES_KEY,c.site.id,{'min_stay_seconds':60},'test');c.db.commit()
    asyncio.run(SL.mark_paid_and_open(c.db,s,fee_snapshot=dict(FEE)))
    assert s.status=='PAID' and not c.commands


def test_paid_lane_change_inside_dedup_closes_on_current_lane(ctx):
    c=ctx;s=stay(c);read(c,s,c.cams[0]);s.status='PAID';s.paid_at=datetime.utcnow();s.exit_deadline=s.paid_at+timedelta(minutes=15);c.db.commit()
    asyncio.run(SL.handle_exit(c.db,c.cams[1],s.plate_number,95,{}))
    assert s.status=='CLOSED' and s.exit_device_id==c.cams[1].id and c.commands[0][0]==c.bars[1].id


def test_outer_and_inner_lanes_do_not_overwrite_each_other():
    bars=[{'id':'outer','lane_dir':'exit','lane_no':2},{'id':'inner','lane_dir':'exit','lane_no':2,'nested_inner':True}]
    assert len(PR.pos_lanes(bars,[]))==2


@pytest.mark.parametrize('stale',[False,True])
def test_old_pos_confirm_infers_transaction_lane_and_preserves_bank_fact(ctx,monkeypatch,stale):
    from starlette.requests import Request
    c=ctx;s=stay(c);e=read(c,s,c.cams[1],age=1200 if stale else 0)
    monkeypatch.setattr(PR,'_print_payload',lambda *a:{})
    body={'session_id':s.id,'amount':1000,'terminal_id':c.terminal.device_key,
          'transaction_id':'synthetic-bank-reference','device_id':c.bars[0].id}
    req=Request({'type':'http','headers':[]})
    out=asyncio.run(PR.pos_confirm(body,req,c.db,c.user))
    assert out['status']=='PAID' and c.db.query(M.Payment).one().amount==1000
    assert c.terminal.lane_no==1
    audit=c.db.query(M.AuditLog).filter_by(action='POS_EXIT_CONTEXT').one()
    assert audit.detail['phase']=='confirm'
    if stale:
        # 2026-10-12 rule 1: the read is 20 min old, but the POS request names
        # lane 1 — the cashier standing there is the evidence; open that lane.
        assert c.commands==[(c.bars[0].id,s.id,'payment')] and s.status=='CLOSED'
        assert out['exit_lane_context']['reason']=='exit_read_stale'
        assert out['exit_lane_context']['pos_lane_source']=='pos_request'
        assert s.exit_confirmed is False and s.exit_device_id==c.cams[0].id
    else:
        assert c.commands==[(c.bars[1].id,s.id,'payment')]
        assert audit.detail['event_id']==e.id and audit.detail['gate_id']==c.bars[1].id
    again=asyncio.run(PR.pos_confirm(body,req,c.db,c.user))
    assert again['payment_id']==out['payment_id'] and c.db.query(M.Payment).count()==1
    assert len(c.commands)==1


def test_legacy_pos_prepare_without_read_keeps_prepayment_possible(ctx):
    c=ctx;s=stay(c)
    out=asyncio.run(PR.pos_prepare({'session_id':s.id,'terminal_id':c.terminal.device_key,
                                    'expected_amount':1000},c.db,c.user))
    assert out['exit_lane_context']['reason']=='waiting_for_exit_read' and not c.commands


def test_another_cars_gate_ack_is_not_this_cars_ack(ctx):
    c=ctx;s=stay(c,plate='9999УБА')
    c.db.add(M.BarrierCommand(session_id=s.id,device_id=c.bars[0].id,command='open',
                             command_source='auto_exit',status='SUCCESS'));c.db.commit()
    assert not asyncio.run(SL.ensure_exit_barrier_if_cleared(c.db,c.cams[0],'1234УБА'))
    assert not c.commands


def test_cgi_low_confidence_retains_metadata_but_heartbeat_is_not_a_rejection(ctx,monkeypatch):
    from app.services import cgi_poller as CP
    c=ctx;s=stay(c)
    monkeypatch.setattr(CP,'SessionLocal',lambda:c.db)
    monkeypatch.setattr(c.db,'close',lambda:None)
    monkeypatch.setattr(CP,'_dump_event',lambda *a:None)
    # 2026-10-12: a low-confidence read EXACTLY matching an active stay is now
    # accepted (test_exit_regressions_1012); keep this test on an unknown plate.
    raw={'Code':'TrafficJunction','TrafficCar':{'PlateNumber':'9999УБА','Confidence':14},
         'Vehicle':{'Confidence':92,'Color':'White'},'RealUTC':1234567890}
    asyncio.run(CP._process_event(c.cams[0].id,raw))
    asyncio.run(CP._process_event(c.cams[0].id,{'Code':'KeepAlive'}))
    e=c.db.query(M.LprEvent).one()
    assert not e.accepted and e.confidence==14 and e.raw['Vehicle']['Confidence']==92
    assert s.status=='OPEN' and not c.commands and c.messages[-1][0]=='EXIT_REVIEW_REQUIRED'


# ── 2026-10-24 Барилгачдын талбай: тайрагдсан гарах уншилт / QR OCR хайлт ──

@pytest.mark.parametrize('read,stay_plate,ok', [
    ('7237УН', '7237УНП', True),     # сүүлийн үсэг тайрагдсан
    ('4380НК', '4380УНК', True),     # дундаас нэг үсэг унасан
    ('237УНП', '7237УНП', True),     # эхний цифр тайрагдсан
    ('9942УУ', '9942УНИ', False),    # тайралт + орлуулалт — таамаглахгүй
    ('?', '1234УБА', False),
    ('1234УБ', '1234УБ', False),
    ('1234УБА', '1234УБА', False),   # зөв формат = өөр машин байж болно
    ('УНП72', '7237УНП', False),     # дараалал алдагдсан
    ('7237', '7237УНП', False),      # хэт богино
])
def test_truncated_read_rule(read, stay_plate, ok):
    assert SL.plates_truncated_read(read, stay_plate) is ok


def test_truncated_exit_read_then_qr_payment_opens_that_lane(ctx):
    # 7237УНП 10-21 18:13: камер «7237УН» уншиж review болсон → QR төлбөр нээгдээгүй
    c=ctx;s=stay(c,plate='7237УНП')
    asyncio.run(SL.handle_exit(c.db,c.cams[1],'7237УН',90,{}))
    assert s.status=='AWAITING_PAYMENT' and s.exit_device_id==c.cams[1].id and not c.commands
    audit=c.db.query(M.AuditLog).filter_by(action='EXIT_OCR_MATCH').one()
    assert audit.detail['rule']=='truncated_read' and s.plate_number=='7237УНП'
    asyncio.run(SL.mark_paid_and_open(c.db,s,fee_snapshot=dict(FEE)))
    assert s.status=='CLOSED' and c.commands==[(c.bars[1].id,s.id,'payment')]


def test_paid_car_truncated_read_opens_its_gate(ctx):
    c=ctx;s=stay(c,plate='1589УКО');asyncio.run(SL.mark_paid_and_open(c.db,s,fee_snapshot=dict(FEE)))
    assert s.status=='PAID' and not c.commands
    asyncio.run(SL.handle_exit(c.db,c.cams[0],'1589УК',94,{}))
    assert s.status=='CLOSED' and c.commands[0][0]==c.bars[0].id


def test_truncated_read_with_two_candidates_stays_in_review(ctx):
    c=ctx;a=stay(c,plate='7237УНП');b=stay(c,plate='7237УНК')
    r=asyncio.run(SL.handle_exit(c.db,c.cams[0],'7237УН',90,{}))
    assert r['reason']=='invalid_plate_review' and a.status==b.status=='OPEN' and not c.commands


def test_own_truncated_reread_does_not_block_payment_opening(ctx):
    # 4380УНК 10-21 05:25 зөв уншилт, 05:30 «4380НК» (review) → шилжүүлэг 05:36 нээгдээгүй
    c=ctx;s=stay(c,plate='4380УНК');read(c,s,c.cams[0],age=600)
    c.db.add(M.LprEvent(site_id=c.site.id,device_id=c.cams[0].id,plate_number='4380НК',lane_dir='exit',
             confidence=86,accepted=False,reject_reason='invalid_plate_review',raw={},
             created_at=datetime.utcnow()-timedelta(seconds=300)));c.db.commit()
    asyncio.run(SL.mark_paid_and_open(c.db,s,fee_snapshot=dict(FEE)))
    assert s.status=='CLOSED' and c.commands==[(c.bars[0].id,s.id,'payment')]


def _public_lookup(c, monkeypatch, typed):
    from starlette.requests import Request
    from app.routers import public_router as PUB
    monkeypatch.setattr(PUB,'session_fee_info',lambda *a,**kw:dict(FEE))
    monkeypatch.setattr(PUB,'amount_due',lambda *a,**kw:1000)
    req=Request({'type':'http','headers':[],'client':('198.51.100.7',1)})
    return PUB.find_session(typed,'SYNTH',req,c.db)


@pytest.mark.parametrize('typed,stored', [
    ('6537УБН', '6537УБК'),   # орох камер Н-ийг К гэж уншсан (хэмжигдсэн хос)
    ('9035УНУ', '9035УНҮ'),   # Ү/У
    ('6537YBH', '6537УБК'),   # латин үсгээр бичсэн
    ('7237УНП', '7237УН'),    # орох камер тайрсан
])
def test_qr_lookup_finds_single_ocr_variant(ctx, monkeypatch, typed, stored):
    c=ctx;s=stay(c,plate=stored)
    out=_public_lookup(c,monkeypatch,typed)
    assert out['session_id']==s.id and out['matched_by']=='ocr' and out['plate_number']==stored


def test_qr_lookup_canon_match_beats_confusable_neighbours(ctx, monkeypatch):
    # TEST-ийн дараалсан дугаарууд: «1002ДМ0» → 1002ДМО (О/0), 1007ДМО/1012ДМО нь 2↔7, 0↔1 хосоор ойролцоо
    c=ctx;s=stay(c,plate='1002ДМО');stay(c,plate='1007ДМО');stay(c,plate='1012ДМО')
    out=_public_lookup(c,monkeypatch,'1002ДМ0')
    assert out['session_id']==s.id and out['matched_by']=='ocr'


def test_qr_lookup_exact_and_ambiguous(ctx, monkeypatch):
    from fastapi import HTTPException
    c=ctx;s=stay(c,plate='6537УБН')
    out=_public_lookup(c,monkeypatch,'6537 убн')
    assert out['session_id']==s.id and out['matched_by'] is None
    s.status='CLOSED';stay(c,plate='5979УКТ');stay(c,plate='5979УАТ');c.db.commit()
    with pytest.raises(HTTPException) as err:
        _public_lookup(c,monkeypatch,'5979УНТ')   # 2 нэр дэвшигч — таамаглахгүй
    assert err.value.status_code==404
