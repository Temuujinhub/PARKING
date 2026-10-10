"""Operator review of exit evidence. Reading this API never opens a gate."""
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field, StrictBool
from sqlalchemy import func
from sqlalchemy.orm import Session, defer, aliased

from ..auth import enforce_site, require
from ..config import settings
from ..database import get_db
from ..models import AuditLog, BarrierCommand, Compensation, Device, LprEvent, ParkingSession, ParkingSite, User
from ..session_logic import amount_due, session_fee_info
from ..services.device_auto import barrier_matches_camera
from ..services import snapshot

router = APIRouter(prefix='/api/exit-review', tags=['exit-review'])
ACTIVE = ('OPEN', 'AWAITING_PAYMENT', 'PAID')
PROOF_SECONDS = 90


def get_stay(db, user, sid, *, lock=False):
    q = db.query(ParkingSession).enable_eagerloads(False).filter(ParkingSession.id == sid)
    from ..services.checkout import lock_session
    s = lock_session(db, sid) if lock else q.first()
    if not s:
        raise HTTPException(404, 'Зогсолт олдсонгүй')
    enforce_site(user, s.site_id)
    return s


def get_camera(db, site_id, camera_id):
    cam = db.get(Device, camera_id)
    site = db.get(ParkingSite, site_id)
    if (not cam or cam.site_id != site_id or cam.device_type != 'camera'
            or cam.status != 'active' or cam.lane_dir not in ('exit', 'both')
            or cam.nested_inner or not cam.ip_address or not site or not site.is_active):
        raise HTTPException(400, 'Энэ зогсоолын идэвхтэй гарах камерыг сонгоно уу')
    return cam


def mapping(cam):
    return [cam.site_id, cam.ip_address, cam.lane_no, cam.lane_dir, bool(cam.nested_inner)]


def saved_image(relative):
    root = Path(settings.snapshot_dir).resolve()
    path = (root / (relative or '')).resolve()
    return bool(relative and path.is_relative_to(root) and path.is_file())


def event_query(db):
    # Project just the OCR text. Do not load base64 pictures for a queue page.
    texts = []
    for prefix in (('Plate',), ('TrafficCar',), ('Picture','Plate'), ()):
        for key in ('PlateNumber','PlateNo','plateNumber'):
            value = LprEvent.raw
            for part in (*prefix, key):
                value = value[part]
            texts.append(func.nullif(value.as_string(), ''))
    return db.query(LprEvent, func.coalesce(*texts, '').label('raw_plate')).options(defer(LprEvent.raw))


def event_out(e, camera=None, raw_plate=''):
    # Never expose arbitrary camera payloads (they can contain credentials/images).
    return {'id': e.id, 'plate': e.plate_number, 'raw_plate': raw_plate[:32],
            'camera_id': e.device_id, 'camera': camera.name if camera else None,
            'lane': camera.lane_no if camera else None, 'created_at': e.created_at,
            'confidence': e.confidence, 'accepted': e.accepted, 'reason': e.reject_reason,
            'image_url': f'/api/exit-review/events/{e.id}/image'}


def exit_commands(db, s):
    return (db.query(BarrierCommand).join(Device, BarrierCommand.device_id == Device.id)
            .filter(BarrierCommand.session_id == s.id, BarrierCommand.command == 'open',
                    Device.site_id == s.site_id, Device.lane_dir.in_(('exit', 'both')),
                    Device.nested_inner.is_(False))
            .order_by(BarrierCommand.created_at.desc(), BarrierCommand.id.desc()))


@router.get('')
def queue(site_id: str, limit: int = Query(50, ge=1, le=100),
          db: Session = Depends(get_db), user: User = Depends(require('cashier'))):
    enforce_site(user, site_id)
    since = datetime.utcnow() - timedelta(hours=24)
    rejected = (event_query(db).filter(LprEvent.site_id == site_id, LprEvent.lane_dir == 'exit',
                LprEvent.accepted.is_(False), LprEvent.created_at >= since)
                .order_by(LprEvent.created_at.desc(), LprEvent.id.desc()).limit(limit+1).all())
    cams = {c.id: c for c in db.query(Device).filter(Device.site_id == site_id).all()}
    # Exact text candidates only. An empty/wrong OCR is not assigned to a nearby plate.
    plates = {e.plate_number for e, _ in rejected[:limit] if e.plate_number != '?'}
    candidates = (db.query(ParkingSession).enable_eagerloads(False)
                  .filter(ParkingSession.site_id == site_id, ParkingSession.status.in_(ACTIVE),
                          ParkingSession.plate_number.in_(plates)).all()) if plates else []
    events = []
    for e, raw_plate in rejected[:limit]:
        out = event_out(e, cams.get(e.device_id), raw_plate)
        out['candidates'] = [{'id': s.id, 'plate': s.plate_number, 'entry_time': s.entry_time}
                             for s in candidates if s.plate_number == e.plate_number and s.entry_time <= e.created_at]
        events.append(out)
    paid = (db.query(ParkingSession).enable_eagerloads(False)
            .filter(ParkingSession.site_id == site_id, ParkingSession.status == 'PAID',
                    ParkingSession.paid_at.isnot(None))
            .order_by(ParkingSession.paid_at.desc(), ParkingSession.id.desc()).limit(limit+1).all())
    def paid_attention(s):
        if not s.exit_device_id:
            return None  # Advance QR payment before reaching any gate is normal.
        cam = cams.get(s.exit_device_id)
        if (not cam or cam.device_type != 'camera' or cam.status != 'active' or not cam.ip_address
                or cam.nested_inner or cam.lane_dir not in ('exit','both')):
            return 'exit_mapping_invalid'
        bars = [b for b in cams.values() if b.device_type == 'barrier' and b.status == 'active'
                and barrier_matches_camera(cam,b)]
        exact = [b for b in bars if b.lane_dir == cam.lane_dir]
        return None if len(exact or bars) == 1 else 'exit_mapping_invalid'
    later = aliased(BarrierCommand)
    later_device = aliased(Device)
    resolved = db.query(later.id).join(later_device, later_device.id == later.device_id).filter(
        later_device.site_id == site_id, later_device.lane_dir.in_(('exit','both')),
        later_device.nested_inner.is_(False), later.session_id == BarrierCommand.session_id,
        later.status == 'SUCCESS', later.command == 'open', later.created_at > BarrierCommand.created_at).exists()
    problems = (db.query(BarrierCommand, ParkingSession, Device).join(
        ParkingSession, ParkingSession.id == BarrierCommand.session_id).join(Device, Device.id == BarrierCommand.device_id)
        .filter(ParkingSession.site_id == site_id, Device.lane_dir.in_(('exit','both')),
                Device.nested_inner.is_(False), BarrierCommand.created_at >= since,
                BarrierCommand.command == 'open', BarrierCommand.status.in_(('FAILED','UNKNOWN','PENDING')),
                ~resolved).order_by(BarrierCommand.created_at.desc()).limit(limit+1).all())
    return {'events': events, 'events_more': len(rejected)>limit, 'window_hours': 24,
            'command_attention': [{'id': cmd.id, 'session_id': s.id, 'plate': s.plate_number,
                'status': cmd.status, 'camera': d.name, 'lane': d.lane_no, 'created_at': cmd.created_at}
                for cmd,s,d in problems[:limit]], 'commands_more': len(problems)>limit,
            'paid_waiting': [{'id': s.id, 'plate': s.plate_number, 'paid_at': s.paid_at,
                              'entry_time': s.entry_time, 'exit_deadline': s.exit_deadline,
                              'grace_expired': bool(s.exit_deadline and s.exit_deadline < datetime.utcnow()),
                              'camera_id': s.exit_device_id, 'attention_reason': paid_attention(s)} for s in paid[:limit]],
            'paid_more': len(paid)>limit,
            'cameras': [{'id': c.id, 'name': c.name, 'lane': c.lane_no} for c in cams.values()
                        if c.device_type == 'camera' and c.status == 'active' and c.ip_address
                        and c.lane_dir in ('exit', 'both') and not c.nested_inner]}


@router.get('/sessions/{session_id}')
def facts(session_id: str, db: Session = Depends(get_db), user: User = Depends(require('cashier'))):
    s = get_stay(db, user, session_id)
    row = (event_query(db).filter(LprEvent.site_id == s.site_id, LprEvent.lane_dir == 'exit',
          LprEvent.plate_number == s.plate_number, LprEvent.created_at >= s.entry_time,
          LprEvent.created_at <= (datetime.utcnow() if s.status in ACTIVE else (s.exit_time or datetime.utcnow())))
          .order_by(LprEvent.created_at.desc(), LprEvent.id.desc()).first())
    e, raw_plate = row if row else (None, '')
    cmd = exit_commands(db, s).first()
    fee = session_fee_info(db, s)
    in_grace = s.status == 'PAID' and s.paid_at and (not s.exit_deadline or datetime.utcnow() <= s.exit_deadline)
    return {'session_id': s.id, 'paid': bool(s.paid_at), 'paid_at': s.paid_at,
            'closed': s.status not in ACTIVE, 'status': s.status,
            'read_state': ('accepted' if e.accepted else 'rejected') if e else 'not_linked',
            'read': event_out(e, db.get(Device, e.device_id), raw_plate) if e else None,
            'exit_camera_id': s.exit_device_id,
            'command': {'id': cmd.id, 'status': cmd.status, 'created_at': cmd.created_at,
                        'executed_at': cmd.executed_at} if cmd else None,
            'acknowledged': cmd.status == 'SUCCESS' if cmd else False,
            # exit_confirmed historically also means inferred/manual closure.
            # Neither that field nor a camera ACK is a gate-position sensor.
            'physical_exit': 'unverified', 'amount_due': 0 if in_grace else amount_due(db, s, fee),
            'exit_deadline': s.exit_deadline, 'vehicle_color': s.vehicle_color, 'vehicle_type': s.vehicle_type,
            'is_free': bool(fee['is_free'])}


@router.get('/events/{event_id}/evidence')
def evidence(event_id: str, db: Session = Depends(get_db), user: User = Depends(require('cashier'))):
    from ..services.camera_evidence import camera_evidence
    e = db.get(LprEvent, event_id)
    if not e or e.lane_dir != 'exit':
        raise HTTPException(404, 'Гарах уншилт олдсонгүй')
    enforce_site(user, e.site_id)
    info = camera_evidence(e.raw)
    # Project scalar identifiers only, never twenty base64 image payloads.
    # Distinct IDs help an operator; they do not prove independent frames.
    fields = [LprEvent.raw[k].as_string() for k in ('RealUTC','UTC','FrameSequence','Sequence')]
    rows = db.query(*fields).filter(LprEvent.site_id == e.site_id, LprEvent.device_id == e.device_id,
        LprEvent.lane_dir == 'exit', LprEvent.plate_number == e.plate_number,
        LprEvent.created_at >= e.created_at - timedelta(seconds=30),
        LprEvent.created_at <= e.created_at).order_by(LprEvent.created_at.desc()).limit(20).all()
    observations = set()
    for row in rows:
        item = camera_evidence(dict(zip(('RealUTC','UTC','FrameSequence','Sequence'), row)))
        if item['camera_time'] or item['frame_id']:
            observations.add((item['camera_time'], item['frame_id']))
    return {**info, 'distinct_observation_ids': len(observations), 'observation_limit': 20,
            'event_id': e.id, 'server_received_at': e.created_at}


@router.get('/events/{event_id}/image')
def event_image(event_id: str, db: Session = Depends(get_db), user: User = Depends(require('cashier'))):
    e = db.get(LprEvent, event_id)
    if not e or e.lane_dir != 'exit':
        raise HTTPException(404, 'Гарах уншилт олдсонгүй')
    enforce_site(user, e.site_id)
    data = snapshot._payload_picture(e.raw if isinstance(e.raw, dict) else {})
    if not data:
        raise HTTPException(404, 'Энэ event-ийн эх зураг хадгалагдаагүй; одоогийн кадр тусдаа баримт болно')
    return Response(data, media_type='image/jpeg', headers={'Cache-Control': 'private, no-store'})


class Capture(BaseModel):
    camera_id: str
    event_id: str | None = None


@router.post('/events/{event_id}/ocr')
async def retry_ocr(event_id: str, db: Session = Depends(get_db), user: User = Depends(require('cashier'))):
    e = db.get(LprEvent, event_id)
    if not e or e.lane_dir != 'exit':
        raise HTTPException(404, 'Гарах уншилт олдсонгүй')
    enforce_site(user, e.site_id)
    from ..services.exit_review_ocr import recognize
    import asyncio
    result = await asyncio.to_thread(recognize, e.raw)
    for suggestion in result.get('suggestions', []):
        plate = suggestion.get('normalized_plate')
        matches = (db.query(ParkingSession).enable_eagerloads(False).filter(
            ParkingSession.site_id == e.site_id, ParkingSession.plate_number == plate,
            ParkingSession.status.in_(ACTIVE), ParkingSession.entry_time <= e.created_at).limit(2).all()
            if suggestion.get('valid_format') else [])
        suggestion['exact_candidates'] = [{'id': s.id, 'plate': s.plate_number,
            'vehicle_color': s.vehicle_color, 'vehicle_type': s.vehicle_type,
            'has_entry_image': bool(s.entry_snapshot)} for s in matches]
        suggestion['unique_match'] = len(matches) == 1
    db.add(AuditLog(username=user.username, action='EXIT_REVIEW_OCR', entity='lpr_event', entity_id=e.id,
                    detail=result))
    db.commit()
    return result


@router.post('/sessions/{session_id}/capture')
async def capture(session_id: str, body: Capture, db: Session = Depends(get_db),
                  user: User = Depends(require('cashier'))):
    s = get_stay(db, user, session_id)
    cam = get_camera(db, s.site_id, body.camera_id)
    if s.status not in ACTIVE:
        raise HTTPException(409, 'Зогсолт хаагдсан байна')
    expected_mapping = mapping(cam)
    event = db.get(LprEvent, body.event_id) if body.event_id else None
    if body.event_id and (not event or event.site_id != s.site_id or event.device_id != cam.id
                          or event.lane_dir != 'exit'):
        raise HTTPException(400, 'Уншилт сонгосон гарах камерынх биш байна')
    from ..services.device_auth import camera_credentials
    data = await snapshot._fetch_from_camera(cam.ip_address, camera_credentials(cam))
    if not data:
        raise HTTPException(502, 'Гарах камераас шинэ зураг авч чадсангүй')
    captured_at = datetime.utcnow()
    import asyncio
    rel = await asyncio.to_thread(snapshot._save, data, s.plate_number, 'verify')
    if not rel:
        raise HTTPException(500, 'Баталгаажуулах зураг хадгалагдсангүй')
    # Fetch first, then lock briefly; never hold a stay lock during camera I/O.
    s = get_stay(db, user, session_id, lock=True)
    db.refresh(cam)
    if mapping(get_camera(db, s.site_id, cam.id)) != expected_mapping or s.status not in ACTIVE:
        raise HTTPException(409, 'Зогсолт эсвэл камерын тохиргоо өөрчлөгдсөн; дахин шалгана уу')
    s.verify_snapshot = rel
    proof = AuditLog(username=user.username, action='EXIT_REVIEW_CAPTURE', entity='session', entity_id=s.id, created_at=captured_at,
                     detail={'path': rel, 'camera_id': cam.id, 'event_id': body.event_id,
                             'mapping': expected_mapping, 'entry_path': s.entry_snapshot,
                             'source': 'current_camera_frame', 'plate': s.plate_number})
    db.add(proof); db.commit()
    return {'proof_id': proof.id, 'camera_id': cam.id, 'taken_at': proof.created_at,
            'expires_seconds': PROOF_SECONDS, 'source': 'current_camera_frame'}


class Resolve(BaseModel):
    proof_id: str
    confirmed_same_vehicle: StrictBool
    reason: str = Field(min_length=3, max_length=200)


@router.post('/sessions/{session_id}/resolve')
async def resolve(session_id: str, body: Resolve, db: Session = Depends(get_db),
                  user: User = Depends(require('cashier'))):
    s = get_stay(db, user, session_id, lock=True)
    now = datetime.utcnow()
    if len(body.reason.strip()) < 3:
        raise HTTPException(400, 'Шалтгаанаа 3-аас дээш тэмдэгтээр бичнэ үү')
    proof = (db.query(AuditLog).filter(AuditLog.id == body.proof_id,
             AuditLog.action == 'EXIT_REVIEW_CAPTURE', AuditLog.entity == 'session', AuditLog.entity_id == s.id)
             .populate_existing().with_for_update().first())
    detail = proof.detail if proof and isinstance(proof.detail, dict) else {}
    if (not body.confirmed_same_vehicle or not proof or proof.username != user.username
            or detail.get('consumed_at') or not 0 <= (now-proof.created_at).total_seconds() <= PROOF_SECONDS
            or detail.get('plate') != s.plate_number or detail.get('path') != s.verify_snapshot):
        raise HTTPException(409, 'Орох ба гарах шинэ зургийг дахин тулгаж баталгаажуулна уу')
    if s.status not in ACTIVE:
        raise HTTPException(409, 'Зогсолт аль хэдийн хаагдсан; командын үр дүнг шалгана уу')
    cam = get_camera(db, s.site_id, detail.get('camera_id'))
    if detail.get('mapping') != mapping(cam):
        raise HTTPException(409, 'Камерын эгнээ өөрчлөгдсөн; шинэ кадр авна уу')
    if detail.get('entry_path') != s.entry_snapshot or not saved_image(s.entry_snapshot):
        raise HTTPException(409, 'Орох зураг байхгүй эсвэл өөрчлөгдсөн; тусдаа операторын шалгалт шаардлагатай')
    bars = [b for b in db.query(Device).filter(Device.site_id == s.site_id, Device.device_type == 'barrier',
             Device.status == 'active').all() if barrier_matches_camera(cam, b)]
    if len(bars) != 1:
        raise HTTPException(409, 'Энэ эгнээний хаалтын холбоос тодорхойгүй')
    if not saved_image(detail['path']):
        raise HTTPException(409, 'Баталгаажуулах зураг олдсонгүй; дахин авна уу')
    newer = (db.query(LprEvent.id).filter(LprEvent.device_id == cam.id, LprEvent.lane_dir == 'exit',
             LprEvent.created_at > proof.created_at,
             ((LprEvent.plate_number != s.plate_number) | LprEvent.accepted.is_(False))).first())
    if newer:
        raise HTTPException(409, 'Энэ эгнээнд өөр дугаар уншигдсан; шинэ кадр авна уу')
    if exit_commands(db, s).filter(BarrierCommand.status.in_(('PENDING', 'UNKNOWN'))).first():
        raise HTTPException(409, 'Өмнөх командын үр дүн тодорхойгүй; давтаж нээхгүй')
    fee = session_fee_info(db, s, at=now)
    in_grace = s.status == 'PAID' and s.paid_at and (not s.exit_deadline or now <= s.exit_deadline)
    due = 0 if in_grace else amount_due(db, s, fee)
    if due > 0 or (not s.paid_at and not fee['is_free']):
        raise HTTPException(409, 'Үлдэгдэл төлбөртэй; эхлээд төлбөрийн хэсгээс шийдвэрлэнэ үү')
    from ..session_logic import exit_policy_block
    if exit_policy_block(db, s, now):
        raise HTTPException(409, 'Гарах доод хугацаа эсвэл өрийн хоригийн дүрэм үйлчилж байна')
    proof.detail = {**detail, 'consumed_at': now.isoformat()}
    s.exit_device_id = cam.id
    db.add(AuditLog(username=user.username, action='EXIT_REVIEW_RESOLVE', entity='session', entity_id=s.id,
        detail={'proof_id': proof.id, 'event_id': detail.get('event_id'), 'camera_id': cam.id,
                'plate': s.plate_number, 'reason': body.reason.strip(), 'paid': bool(s.paid_at),
                'is_free': bool(fee['is_free']), 'physical_exit': 'unverified'}))
    # Financial calculation and closure use the existing path. Raw rejected OCR
    # is never rewritten or promoted to an accepted camera read.
    from ..session_logic import _close_and_open
    result = await _close_and_open(db, cam, s, now, fee, source='manual', exit_confirmed=False)
    return {'result': result, 'facts': facts(s.id, db, user)}
