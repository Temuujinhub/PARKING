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
# public_router-ийг fixture session_fee_info-г patch хийхээс ӨМНӨ import хийнэ — эс бөгөөс
# түүний `from ..session_logic import session_fee_info` нэр хуурамч функцийг барьж үлддэг.
from app.routers import public_router as PUB
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


# ── F5 (2026-10-24): орох буруу/тайрсан, гарах зөв → review → төлбөр тэр эгнээг нээнэ ──

def no_flat_fee(c):
    A.set_site_rules(c.db,A.EXITRULES_KEY,c.site.id,{'wallet_auto_deduct':False,'no_session_fee':0},'test');c.db.commit()


def review(c, plate, cam=None):
    return asyncio.run(SL.handle_exit(c.db,cam or c.cams[1],plate,95,{}))


@pytest.mark.parametrize('read,stay_plate', [('0539УАТ','0539УАП'),   # ялалт: Т↔П, 1 тэмдэгт
                                             ('3163УЕУ','3163УУ')])   # Айр нэтворк: орох тайрсан
def test_payment_opens_lane_of_near_review_read(ctx, read, stay_plate):
    c=ctx;no_flat_fee(c);s=stay(c,plate=stay_plate)
    r=review(c,read)
    assert r['action']=='review_required' and s.status=='OPEN' and not c.commands
    asyncio.run(SL.mark_paid_and_open(c.db,s,fee_snapshot=dict(FEE)))
    assert s.status=='CLOSED' and c.commands==[(c.bars[1].id,s.id,'payment')]
    assert s.exit_device_id==c.cams[1].id and s.exit_confirmed is False
    audit=c.db.query(M.AuditLog).filter_by(action='PAYMENT_REVIEW_READ_OPEN').one()
    assert audit.detail['read_plate']==read and audit.detail['lane_no']==2


def test_paid_first_then_near_exit_read_still_reviews(ctx):
    # Гарах уншилт ойролцоох ТӨЛСӨН сешний эрхийг ашиглахгүй (test_exit_review, 10-10) —
    # F5 нь зөвхөн ТӨЛБӨРИЙН мөчид (жолооч гарцад байх үед) үйлчилнэ.
    c=ctx;no_flat_fee(c);s=stay(c,plate='0539УАП')
    asyncio.run(SL.mark_paid_and_open(c.db,s,fee_snapshot=dict(FEE)))
    r=review(c,'0539УАТ')
    assert r['action']=='review_required' and s.status=='PAID' and not c.commands


def test_unpaid_stay_near_exit_read_still_reviews(ctx):
    c=ctx;no_flat_fee(c);s=stay(c,plate='0539УАП')
    assert review(c,'0539УАТ')['action']=='review_required' and s.status=='OPEN'


@pytest.mark.parametrize('fault', ['two_cameras','other_car_after','unknown_after','old','two_stays','far_plate'])
def test_near_review_read_is_not_enough(ctx, fault):
    c=ctx;no_flat_fee(c);s=stay(c,plate='0539УАП')
    if fault=='far_plate':
        review(c,'0538УАТ')                       # 2 тэмдэгт (8, Т) — чанга дүрэм хүлээн авахгүй
    else:
        review(c,'0539УАТ')
    if fault=='two_cameras': review(c,'0539УАТ',c.cams[0])
    if fault=='other_car_after': review(c,'9999УБА')
    if fault=='unknown_after':
        c.db.add(M.LprEvent(site_id=c.site.id,device_id=c.cams[1].id,plate_number='?',lane_dir='exit',
                 confidence=0,accepted=False,reject_reason='CGI: plate not parsed',raw={}));c.db.commit()
    if fault=='old':
        for e in c.db.query(M.LprEvent).all(): e.created_at-=timedelta(minutes=6)
        c.db.commit()
    if fault=='two_stays': stay(c,plate='0539УАК')   # уншилт хоёр сешнтэй ойролцоо
    asyncio.run(SL.mark_paid_and_open(c.db,s,fee_snapshot=dict(FEE)))
    assert s.status=='PAID' and not c.commands
    assert not c.db.query(M.AuditLog).filter_by(action='PAYMENT_REVIEW_READ_OPEN').count()


@pytest.mark.parametrize('read,stay_plate,ok', [
    ('0539УАТ','0539УАП',True),    # 1 тэмдэгт
    ('3163УЕУ','3163УУ',True),     # урвуу тайралт
    ('1041УНС','1041УКС',True),    # хэмжигдсэн хос
    ('4845УАР','4825УАТ',False),   # 2 дурын тэмдэгт
    ('0538УАТ','0539УАП',False),
])
def test_paid_near_rule(read, stay_plate, ok):
    assert SL.plates_paid_near(read, stay_plate) is ok


# ── 2026-10-24 аудит, 1-р шат: F5 хамгаалалт, хаалт дахин нээх цонх, данс ──

def flat_fee(c, **extra):
    A.set_site_rules(c.db,A.EXITRULES_KEY,c.site.id,{'wallet_auto_deduct':False,'no_session_fee':2000,**extra},'test');c.db.commit()


def test_f5_corrects_malformed_entry_plate_so_passing_reread_is_no_phantom(ctx):
    # 1a: «3163УУ» F5-аар нээгдэв → хаалтаар гарахдаа «3163УЕУ» дахин уншигдав → 2000₮ хий сешн ҮҮСЭХГҮЙ
    c=ctx;flat_fee(c);s=stay(c,plate='3163УУ')
    assert review(c,'3163УЕУ')['reason']=='plate_mismatch_review'   # суурь хураамж биш
    asyncio.run(SL.mark_paid_and_open(c.db,s,fee_snapshot=dict(FEE)))
    assert s.status=='CLOSED' and s.plate_number=='3163УЕУ'
    assert c.db.query(M.AuditLog).filter_by(action='PAYMENT_REVIEW_READ_OPEN').one().detail['corrected'] is True
    r=review(c,'3163УЕУ')
    assert r['action']=='reread' and c.db.query(M.ParkingSession).count()==1


def _review_event(c, plate, age=30, cam=None):
    e=M.LprEvent(site_id=c.site.id,device_id=(cam or c.cams[1]).id,plate_number=plate,lane_dir='exit',
                 confidence=90,accepted=False,reject_reason='plate_mismatch_review',raw={},
                 created_at=datetime.utcnow()-timedelta(seconds=age))
    c.db.add(e);c.db.commit();return e


def test_f5_skips_reread_of_a_car_that_just_left(ctx):
    # 1b: зангуу «0539УАТ» нь 60с өмнө гарсан 0539УАК-ийн давтан уншилт байж болно
    c=ctx;no_flat_fee(c);s=stay(c,plate='0539УАП')
    gone=stay(c,plate='0539УАК',status='CLOSED',camera=c.cams[1])
    gone.exit_time=datetime.utcnow()-timedelta(seconds=60);c.db.commit()
    _review_event(c,'0539УАТ',age=30)
    asyncio.run(SL.mark_paid_and_open(c.db,s,fee_snapshot=dict(FEE)))
    assert s.status=='PAID' and not c.commands


def test_f5_skips_lane_opened_by_hand_after_the_read(ctx):
    # 1c: оператор зангуунаас хойш тэр эгнээг гараар нээсэн → машин аль хэдийн гарсан байж болно
    c=ctx;no_flat_fee(c);s=stay(c,plate='0539УАП')
    _review_event(c,'0539УАТ',age=60)
    c.db.add(M.BarrierCommand(device_id=c.bars[1].id,command='open',command_source='manual',status='SUCCESS',
                              created_at=datetime.utcnow()-timedelta(seconds=20)));c.db.commit()
    asyncio.run(SL.mark_paid_and_open(c.db,s,fee_snapshot=dict(FEE)))
    assert s.status=='PAID' and not c.commands


def test_reread_window_also_reopens_the_barrier(ctx):
    # 1d: exit_reread_seconds=900 — төлсөн машин 7 мин дараа ижил эгнээнд дахин уншигдвал хаалт нээнэ
    c=ctx;flat_fee(c,exit_reread_seconds=900);s=stay(c,plate='5979УНТ');read(c,s,c.cams[1])
    asyncio.run(SL.mark_paid_and_open(c.db,s,fee_snapshot=dict(FEE)))
    assert s.status=='CLOSED' and len(c.commands)==1
    s.exit_time=datetime.utcnow()-timedelta(minutes=7);c.db.commit()
    for e in c.db.query(M.LprEvent).all()+c.db.query(M.BarrierCommand).all(): e.created_at-=timedelta(minutes=7)
    c.db.commit()
    r=review(c,'5979УНТ')
    assert r['action']=='reread' and r['barrier_opened'] and len(c.commands)==2
    assert c.db.query(M.ParkingSession).count()==1   # 2000₮ сешн үүсээгүй


def test_wallet_is_never_debited_for_a_guessed_plate(ctx):
    # 1e: OCR андуурлаар (К↔Н) тохоосон сешний эзний данснаас хасахгүй
    c=ctx;no_flat_fee(c)
    A.set_site_rules(c.db,A.EXITRULES_KEY,c.site.id,{'wallet_auto_deduct':True},'test');c.db.commit()
    s=stay(c,plate='1041УКС')
    w=M.Wallet(tenant_id=None,plate_number='1041УКС',phone='',name='',balance=50000,status='ACTIVE')
    c.db.add(w);c.db.commit()
    r=review(c,'1041УНС')
    assert r['action']=='awaiting_payment' and float(w.balance)==50000 and not c.commands


# ── 2-р шат: дөнгөж гарсан машины давтан уншилт өөр сешнийг булаахгүй; орох dedup ──

def _leave(c, plate, cam):
    s=stay(c,plate=plate);read(c,s,cam)
    asyncio.run(SL.mark_paid_and_open(c.db,s,fee_snapshot=dict(FEE)))
    assert s.status=='CLOSED';return s


def test_departed_car_reread_does_not_grab_neighbour_stay(ctx):
    c=ctx;no_flat_fee(c);gone=_leave(c,'1234УКА',c.cams[1])
    other=stay(c,plate='1234УНА')            # К↔Н хэмжигдсэн хос — өмнө нь энэ сешн «булаагддаг» байв
    r=review(c,'1234УКА')
    assert r['action'] in ('dedup','reread') and other.status=='OPEN' and other.exit_device_id is None


def test_departed_car_truncated_reread_does_not_grab_neighbour_stay(ctx):
    c=ctx;no_flat_fee(c);_leave(c,'1234УКА',c.cams[1])
    other=stay(c,plate='1234УКБ')            # «1234УК» нь хоёуланд тайралт
    review(c,'1234УК')
    assert other.status=='OPEN' and other.exit_device_id is None


@pytest.mark.parametrize('new,old,dup', [
    ('2943УКН','2942УКН',False),   # өөр машин (2↔3)
    ('2942УНН','2942УКН',True),    # К↔Н хэмжигдсэн
    ('2942УКН','2942УКН',True),
    ('2942УК','2942УКН',True),     # тайрсан хог
    ('5551ХК','ХК',False),         # цифргүй хуучин хог — өмнө нь ҮРГЭЛЖ давхар гэдэг байв
    ('4627КД','4627УКА',True),     # 08-14 хэмжилтийн жишээ
])
def test_entry_duplicate_read_rule(new, old, dup):
    assert SL.is_duplicate_read(new, old) is dup


# ── 3-р шат: хий өр — гараар/онцгой гаргалт, нөхөн төлбөр, AWAITING, давтан уншилт ──

def _manual(c, s, **body):
    SR.open_barrier = SL.open_barrier     # fixture-ийн хуурамч нээгч (sessions_router өөрийн нэрээр дууддаг)
    return asyncio.run(SR.manual_exit(s.id, {'reason_code': '', 'reason': 'тест гаргалт', **body}, c.db, c.user))


@pytest.fixture(autouse=False)
def sr_open(monkeypatch):
    from app.services import barrier as B
    monkeypatch.setattr(SR, 'open_barrier', B.open_barrier)   # тест бүрийн дараа сэргээнэ
    yield


def test_manual_exit_on_multi_lane_site_requires_a_lane(ctx, sr_open):
    from fastapi import HTTPException
    c=ctx;s=stay(c)
    with pytest.raises(HTTPException) as err:
        _manual(c,s,open_barrier=True)
    assert err.value.status_code==409 and s.status=='OPEN' and not c.commands
    _manual(c,s,open_barrier=True,device_id=c.bars[1].id)
    assert s.status=='MANUAL_CLOSED' and s.exit_device_id==c.cams[1].id
    assert c.commands==[(c.bars[1].id,s.id,'manual')]


def test_manual_exit_on_single_lane_site_needs_no_lane(ctx, sr_open):
    c=ctx;c.cams[1].status='inactive';c.bars[1].status='inactive';c.db.commit();s=stay(c)
    _manual(c,s,open_barrier=True)
    assert c.commands==[(c.bars[0].id,s.id,'manual')]


def test_manual_exit_refuses_already_closed_stay(ctx, sr_open):
    from fastapi import HTTPException
    c=ctx;s=stay(c,status='FREE')
    with pytest.raises(HTTPException) as err:
        _manual(c,s,open_barrier=True,device_id=c.bars[0].id,create_compensation=True)
    assert err.value.status_code==409 and s.status=='FREE' and not c.commands
    assert c.db.query(M.Compensation).count()==0


def test_special_exit_without_read_on_multi_lane_requires_lane(ctx, sr_open):
    from fastapi import HTTPException
    c=ctx;s=stay(c)
    with pytest.raises(HTTPException) as err:
        asyncio.run(SR.special_exit(s.id,{'kind':'emergency'},c.db,c.user))
    assert err.value.status_code==409 and s.status=='OPEN' and not c.commands


def test_compensation_is_the_unpaid_remainder(ctx):
    from app.routers.compensations_router import create_compensation
    c=ctx;s=stay(c)
    c.db.add(M.Payment(session_id=s.id,provider='WALLET',payment_method='WALLET',sender_invoice_no='part-1',
                       status='PAID',amount=400,paid_at=datetime.utcnow()));c.db.commit()
    comp=create_compensation(c.db,s,'manual','тест')
    assert float(comp.amount)==600              # 1000 − 400 (өмнө нь 1000 бичдэг байв)
    c.db.add(M.Payment(session_id=s.id,provider='CASH',payment_method='CASH',sender_invoice_no='part-2',
                       status='PAID',amount=600,paid_at=datetime.utcnow()));c.db.commit()
    assert create_compensation(c.db,s,'manual','тест') is None


def _run_autoclose(c, monkeypatch):
    from app.services import auto_close as AC
    monkeypatch.setattr(AC,'SessionLocal',lambda:c.db)
    monkeypatch.setattr(c.db,'close',lambda:None)
    return AC.run_once()


def test_awaiting_timeout_counts_from_latest_exit_read(ctx, monkeypatch):
    c=ctx;s=stay(c,status='AWAITING_PAYMENT',age=300)
    s.payment_wait_started_at=datetime.utcnow()-timedelta(hours=3)
    s.last_exit_seen_at=datetime.utcnow()-timedelta(minutes=5)     # гарцад саяхан дахин уншигдсан
    s.updated_at=datetime.utcnow()-timedelta(hours=2);c.db.commit()
    _run_autoclose(c,monkeypatch)
    assert s.status=='AWAITING_PAYMENT' and c.db.query(M.Compensation).count()==0
    s.last_exit_seen_at=datetime.utcnow()-timedelta(hours=3);c.db.commit()
    _run_autoclose(c,monkeypatch)
    assert s.status!='AWAITING_PAYMENT'


def test_reread_after_10_minutes_only_without_reentry(ctx):
    # D4: 15 мин цонх; 5 минутаас хойш бол тэр хооронд орох уншилт байх ёсгүй
    c=ctx;flat_fee(c);s=stay(c,plate='5979УНТ');read(c,s,c.cams[1])
    asyncio.run(SL.mark_paid_and_open(c.db,s,fee_snapshot=dict(FEE)))
    s.exit_time=datetime.utcnow()-timedelta(minutes=10);c.db.commit()
    for e in c.db.query(M.LprEvent).all()+c.db.query(M.BarrierCommand).all(): e.created_at-=timedelta(minutes=10)
    c.db.commit()
    assert review(c,'5979УНТ')['action']=='reread'
    # орох камерт уншигдсан (дахин орсон) бол давтан уншилт БИШ
    assert SL.recent_exit_reread(c.db,'5979УНТ',c.site.id,datetime.utcnow(),900) is not None
    c.db.add(M.LprEvent(site_id=c.site.id,device_id=c.cams[0].id,plate_number='5979УНТ',lane_dir='entry',
                        confidence=90,accepted=True,raw={},created_at=datetime.utcnow()-timedelta(minutes=3)))
    c.db.commit()
    assert SL.recent_exit_reread(c.db,'5979УНТ',c.site.id,datetime.utcnow(),900) is None


def test_reentry_after_paid_grace_starts_a_new_stay(ctx):
    # 2i: 11:00 төлсөн (deadline 11:15), уншилтгүй гарсан, 11:40 дахин орсон
    from sqlalchemy import text
    c=ctx
    # SQLite-д uq_active_session нөхцөлгүй (postgresql_where л) — продын адил partial болгоно
    c.db.execute(text("DROP INDEX IF EXISTS uq_active_session"))
    c.db.execute(text("CREATE UNIQUE INDEX uq_active_session ON parking_sessions(site_id, plate_number) "
                      "WHERE status IN ('OPEN','AWAITING_PAYMENT','PAID')"));c.db.commit()
    s=stay(c,plate='7777УБА',age=120)
    asyncio.run(SL.mark_paid_and_open(c.db,s,fee_snapshot=dict(FEE)))
    s.paid_at-=timedelta(minutes=40);s.exit_deadline=s.paid_at+timedelta(minutes=15);c.db.commit()
    entry_cam=M.Device(site_id=c.site.id,name='Entry',device_type='camera',lane_dir='entry',lane_no=1,
                       status='active',nested_inner=False,ip_address='192.0.2.9')
    c.db.add(entry_cam);c.db.commit()
    asyncio.run(SL.handle_entry(c.db,entry_cam,'7777УБА',95,{}))
    stays=c.db.query(M.ParkingSession).filter_by(plate_number='7777УБА').all()
    assert len(stays)==2 and s.status=='CLOSED' and any(x.status=='OPEN' for x in stays)


# ── 4-р шат: өрийн хориг = тухайн түрээслэгч; camsync давхар сешн ──

def test_other_tenant_debts_do_not_block_payment_opening(ctx):
    c=ctx;s=stay(c);read(c,s,c.cams[1])
    tenant=M.Tenant(name='Other',code='OTH');c.db.add(tenant);c.db.flush()
    other=M.ParkingSite(name='Other site',site_code='OTH',tenant_id=tenant.id);c.db.add(other);c.db.flush()
    A.set_site_rules(c.db,A.BLACKLIST_KEY,c.site.id,{'block_exit_debt_count':1},'test')
    c.db.add(M.Compensation(site_id=other.id,plate_number=s.plate_number,amount=5000,status='PENDING',reason='t'))
    c.db.commit()
    asyncio.run(SL.mark_paid_and_open(c.db,s,fee_snapshot=dict(FEE)))
    assert s.status=='CLOSED' and c.commands==[(c.bars[1].id,s.id,'payment')]


# ── 5-р шат: гараар оруулах дугаар, тохиргооны шахалт/хуучин давхарга, доторх босго ──

@pytest.mark.parametrize('raw,out', [('1234 уба','1234УБА'),('1234-УБА','1234УБА'),('12.34УБА','1234УБА')])
def test_admin_plate_cleans_separators(raw, out):
    assert SL.admin_plate(raw)==out


@pytest.mark.parametrize('raw,strict', [('1234УБA',True),('1234YBA',False),('12УБА',True),('',False)])
def test_admin_plate_rejects_latin_and_bad_format(raw, strict):
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        SL.admin_plate(raw, strict_format=strict)


def test_old_rule_endpoints_are_clamped_and_legacy_overlay_resets(ctx):
    c=ctx
    A.set_rules(c.db,A.EXITRULES_KEY,{'exit_read_valid_minutes':1440,'reopen_max_hours':0},'t');c.db.commit()
    r=A.get_exit_rules(c.db,None)
    assert r['exit_read_valid_minutes']==60 and r['reopen_max_hours']==1
    A.set_rules(c.db,A.BARRIER_KEY,{'dedup_seconds':0},'t');c.db.commit()
    assert A.get_barrier_rules(c.db,None)['dedup_seconds']==3
    # хуучин site_overrides: «глобал руу буцаах» нь түүнийг ч устгана
    row=c.db.query(M.AppSetting).filter_by(key=A.EXITRULES_KEY).one()
    row.value={**row.value,'site_overrides':{c.site.id:'0'},'no_session_fee':2000};c.db.commit();A.invalidate_cache()
    A.set_site_rules(c.db,A.EXITRULES_KEY,c.site.id,{'no_session_fee':3000},'t');c.db.commit();A.invalidate_cache()
    assert A.no_session_exit_fee(c.db,c.site.id)==3000
    A.set_site_rules(c.db,A.EXITRULES_KEY,c.site.id,{'no_session_fee':None},'t');c.db.commit();A.invalidate_cache()
    assert A.no_session_exit_fee(c.db,c.site.id)==2000          # өмнө нь хуучин 0 нууцаар буцдаг байв


def test_inner_cross_read_threshold_is_separate_from_fake_exit(ctx):
    c=ctx
    A.set_site_rules(c.db,A.EXITRULES_KEY,c.site.id,{'fake_exit_minutes':30},'t');c.db.commit()
    assert SL.inner_cross_read_minutes(c.db,c.site.id)==30     # 0 = хуучнаар fake_exit-ийг дагана
    A.set_site_rules(c.db,A.EXITRULES_KEY,c.site.id,{'inner_cross_read_minutes':2},'t');c.db.commit()
    assert SL.inner_cross_read_minutes(c.db,c.site.id)==2
