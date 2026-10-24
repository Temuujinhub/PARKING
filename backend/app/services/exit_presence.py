"""Exit presence is separate from payment entitlement; no network side effects."""
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy.orm import defer

from ..config import settings
from ..models import Device, LprEvent, ParkingSite
from .device_auto import barrier_matches_camera


def presence_seconds():
    # Reuse the existing POS queue window; never borrow the 15/30 minute paid grace.
    return max(1, min(10, int(settings.exit_queue_show_min))) * 60


def lane_cameras(db, site_id, device_id=None, lane_no=None):
    """Explicit invalid/ambiguous lane selections must not widen to all lanes."""
    cameras = db.query(Device).filter(Device.site_id == site_id, Device.device_type == 'camera',
        Device.status == 'active', Device.lane_dir.in_(('exit', 'both')), Device.nested_inner.is_(False)).all()
    if device_id:
        dev = db.get(Device, device_id)
        if (not dev or dev.site_id != site_id or dev.status != 'active' or dev.nested_inner
                or dev.lane_dir not in ('exit', 'both') or dev.device_type not in ('camera', 'barrier')):
            raise HTTPException(400, 'Энэ зогсоолын гадна гарах хаалт/камерыг сонгоно уу')
        if lane_no is not None and lane_no != dev.lane_no:
            raise HTTPException(400, 'Сонгосон төхөөрөмж ба эгнээ зөрж байна')
        cameras = [c for c in cameras if c.id == dev.id] if dev.device_type == 'camera' else [
            c for c in cameras if barrier_matches_camera(c, dev)]
        if len(cameras) != 1:
            raise HTTPException(409, 'Гарах эгнээний камерын холбоос тодорхойгүй')
    elif lane_no is not None:
        cameras = [c for c in cameras if c.lane_no == lane_no]
        if len(cameras) != 1:
            raise HTTPException(409, 'Гарах эгнээг төхөөрөмжөөр нь сонгоно уу')
    return cameras


def payment_presence_seconds(db, site_id):
    """How long an exit read still proves the car is at that lane for a payment.

    10-11 release used the 3-minute POS queue window: payments 3–15 minutes after
    the exit read stopped opening (before: 585/585 opened, after: 2/46). The
    window is now the site's `exit_read_valid_minutes` (default 15); the real
    protection is that no OTHER plate was read on that lane since (below)."""
    from .app_settings import get_exit_rules
    minutes = int(get_exit_rules(db, site_id).get('exit_read_valid_minutes') or 0)
    return minutes * 60 if minutes > 0 else presence_seconds()


def same_vehicle_read(read_plate, stay_plate):
    """A read of this stay's car: exact text, a measured OCR confusion, or a
    truncated (invalid-format) read of it (the read matched by
    `ocr_confusion_match` carries the camera text). 2026-10-24: the paying
    car's own truncated re-read («4380НК» after 4380УНК) used to count as
    another car and block the payment opening; '?' still does."""
    from ..session_logic import plates_ocr_confusable, plates_truncated_read
    return bool(read_plate) and (read_plate == stay_plate
                                 or plates_ocr_confusable(read_plate, stay_plate)
                                 or plates_truncated_read(read_plate, stay_plate))


def fresh_presence(db, stay, now=None):
    """This car's latest accepted read on a uniquely bound live outer exit camera,
    within `payment_presence_seconds`, with NO other plate read on that camera
    since. A newer different/unknown read ('?') invalidates presence even if it
    was rejected; a newer rejected read of the same plate does not.
    Never infer a camera from payment, vehicle colour, or a nearby plate.
    """
    now = now or datetime.utcnow()
    cam = db.get(Device, stay.exit_device_id) if stay.exit_device_id else None
    if cam is None:
        return None, None, 'waiting_for_exit_read'
    site = db.get(ParkingSite, stay.site_id)
    if (not site or not site.is_active or cam.site_id != stay.site_id or cam.status != 'active'
            or cam.device_type != 'camera' or cam.lane_dir not in ('exit', 'both')
            or cam.nested_inner or not cam.ip_address):
        return None, None, 'exit_mapping_invalid'
    since = max(stay.entry_time, now - timedelta(seconds=payment_presence_seconds(db, stay.site_id)))
    events = (db.query(LprEvent).options(defer(LprEvent.raw)).filter(
        LprEvent.site_id == stay.site_id, LprEvent.device_id == cam.id,
        LprEvent.lane_dir == 'exit', LprEvent.created_at >= since, LprEvent.created_at <= now)
        .order_by(LprEvent.created_at.desc(), LprEvent.id.desc()).limit(500).all())
    anchor, newer = None, []
    for event in events:
        if event.accepted and same_vehicle_read(event.plate_number, stay.plate_number):
            anchor = event
            break
        newer.append(event)
    others = [e for e in newer if not same_vehicle_read(e.plate_number, stay.plate_number)]
    if anchor is None:
        return None, (newer[0] if newer else None), ('exit_presence_unconfirmed' if newer else 'exit_read_stale')
    if others:
        return None, others[0], 'exit_presence_unconfirmed'
    if isinstance(anchor.raw, dict) and anchor.raw.get('_server_open_eligible') is False:
        return None, anchor, 'exit_read_stale'
    return cam, anchor, None


REVIEW_READ_SECONDS = 300
REVIEW_READ_REASONS = ('plate_mismatch_review', 'no_matching_stay_review', 'invalid_plate_review')


def review_read_presence(db, stay, now=None):
    """F5 (2026-10-24): the stay has no exit lane, but a REVIEW exit read of a
    near plate (`plates_paid_near`: measured OCR pair, truncation either way,
    or one character) happened in the last 5 minutes. Opens only if those reads
    are on ONE live outer exit camera, the read is near no other active stay,
    and no other car ('?' included) was read on that camera since.
    ялалт 0539УАП/0539УАТ, Айр нэтворк 3163УУ/3163УЕУ: paid, then the operator
    had to open by hand. Returns (camera, event) or (None, None)."""
    from ..models import ParkingSession
    from ..session_logic import plates_paid_near
    now = now or datetime.utcnow()
    since = max(stay.entry_time, now - timedelta(seconds=REVIEW_READ_SECONDS))
    events = (db.query(LprEvent).options(defer(LprEvent.raw)).filter(
        LprEvent.site_id == stay.site_id, LprEvent.lane_dir == 'exit',
        LprEvent.accepted.is_(False), LprEvent.reject_reason.in_(REVIEW_READ_REASONS),
        LprEvent.created_at >= since, LprEvent.created_at <= now)
        .order_by(LprEvent.created_at.desc(), LprEvent.id.desc()).limit(200).all())
    hits = [e for e in events if plates_paid_near(e.plate_number, stay.plate_number)]
    if not hits or len({e.device_id for e in hits}) != 1:
        return None, None
    anchor = hits[0]
    cam = db.get(Device, anchor.device_id)
    site = db.get(ParkingSite, stay.site_id)
    if (not cam or not site or not site.is_active or cam.site_id != stay.site_id
            or cam.status != 'active' or cam.device_type != 'camera'
            or cam.lane_dir not in ('exit', 'both') or cam.nested_inner or not cam.ip_address):
        return None, None
    if isinstance(anchor.raw, dict) and anchor.raw.get('_server_open_eligible') is False:
        return None, None
    others = db.query(ParkingSession.plate_number).filter(
        ParkingSession.site_id == stay.site_id, ParkingSession.id != stay.id,
        ParkingSession.status.in_(['OPEN', 'AWAITING_PAYMENT', 'PAID'])).all()
    if any(p == anchor.plate_number or plates_paid_near(anchor.plate_number, p) for (p,) in others):
        return None, None   # уншилт өөр сешнтэй ч ойролцоо — хоёрдмол
    # Дөнгөж ГАРСАН машины давтан уншилт байж болно (replay: 233-аас 6) — зангуунаас
    # 5 мин өмнөөс хойш гарцаар хаагдсан ижил/ойролцоо дугаартай сешн байвал алгасна.
    departed = db.query(ParkingSession.plate_number).filter(
        ParkingSession.site_id == stay.site_id, ParkingSession.id != stay.id,
        ParkingSession.status.in_(['CLOSED', 'FREE', 'MANUAL_CLOSED']),
        ParkingSession.exit_time >= anchor.created_at - timedelta(seconds=REVIEW_READ_SECONDS)).all()
    if any(p == anchor.plate_number or plates_paid_near(anchor.plate_number, p) for (p,) in departed):
        return None, None
    # Зангуунаас хойш тэр эгнээний хаалтыг оператор ГАРААР нээсэн бол тэр машин
    # аль хэдийн гарсан байж болно — хаалтыг хоосон эгнээнд дахин нээхгүй.
    from ..models import BarrierCommand
    from ..session_logic import _find_barrier
    gate = _find_barrier(db, stay.site_id, cam)
    if gate is not None and db.query(BarrierCommand.id).filter(
            BarrierCommand.device_id == gate.id, BarrierCommand.command_source == 'manual',
            BarrierCommand.created_at > anchor.created_at,
            BarrierCommand.created_at <= now).first():
        return None, None
    newer = (db.query(LprEvent.plate_number).filter(
        LprEvent.site_id == stay.site_id, LprEvent.device_id == cam.id, LprEvent.lane_dir == 'exit',
        LprEvent.created_at > anchor.created_at, LprEvent.created_at <= now).all())
    if any(not (same_vehicle_read(p, stay.plate_number) or plates_paid_near(p, stay.plate_number))
           for (p,) in newer):
        return None, None
    return cam, anchor


def infer_pos_context(db, stay):
    """Per-transaction observation, never a permanent terminal assignment."""
    cam, event, reason = fresh_presence(db, stay)
    from ..session_logic import _find_barrier
    gate = _find_barrier(db, stay.site_id, cam) if cam else None
    if cam and not gate:
        reason = 'exit_mapping_invalid'
    return {'status': 'inferred_from_exit_read' if not reason else 'waiting_for_exit_evidence',
            'reason': reason, 'site_id': stay.site_id, 'session_id': stay.id,
            'camera_id': cam.id if cam else None, 'lane_no': cam.lane_no if cam else None,
            'gate_id': gate.id if gate else None, 'event_id': event.id if event else None,
            'read_at': event.created_at.isoformat() if event else None,
            'inferred_at': datetime.utcnow().isoformat(), 'permanent_assignment': False}


def record_pos_context(db, payment, stay, terminal_id, username, phase, context=None):
    from ..models import AuditLog
    context = context if context is not None else infer_pos_context(db, stay)
    db.add(AuditLog(username=username, action='POS_EXIT_CONTEXT', entity='payment',
        entity_id=payment.id, detail={**context, 'phase': phase, 'terminal_id': terminal_id}))
    return context


def _requested_lane(body):
    if body.get('device_id') and body.get('exit_device_id') and body['device_id'] != body['exit_device_id']:
        raise HTTPException(400, 'Гарах төхөөрөмжийн хоёр утга зөрж байна')
    lane_no = body.get('lane_no')
    if lane_no is not None and (isinstance(lane_no, bool) or not isinstance(lane_no, int)):
        raise HTTPException(400, 'Эгнээний дугаар бүхэл тоо байна')
    return body.get('device_id') or body.get('exit_device_id'), lane_no


def validate_pos_context(db, stay, body):
    """Additive protocol: old POS requests remain valid; new context is enforced.

    An explicit lane without a fresh read is accepted (`pos_lane_selected`): the
    cashier at that lane is the exit evidence (2026-10-12). A conflicting fresh
    read on another lane, or a stale `exit_event_id`, still asks for a refresh."""
    device_id, lane_no = _requested_lane(body)
    event_id = body.get('exit_event_id')
    if device_id is None and lane_no is None and event_id is None:
        return infer_pos_context(db, stay)
    cams = lane_cameras(db, stay.site_id, device_id, lane_no)
    cam, event, reason = fresh_presence(db, stay)
    if event_id and (reason or not event or event.id != event_id):
        raise HTTPException(409, 'Гарах уншилт шинэчлэгдсэн; жагсаалтаа шинэчлээд машинаа дахин сонгоно уу')
    if cam and cam.id not in {c.id for c in cams}:
        raise HTTPException(409, 'Гарах уншилт шинэчлэгдсэн; жагсаалтаа шинэчлээд машинаа дахин сонгоно уу')
    if cam is None:
        lane = pos_lane(db, stay, body)
        if lane is None:
            raise HTTPException(409, 'Гарах хаалтын холбоос тодорхойгүй; оператор шалгана уу')
        return {**infer_pos_context(db, stay), 'status': 'pos_lane_selected',
                'camera_id': lane['camera'].id, 'lane_no': lane['camera'].lane_no,
                'gate_id': lane['gate_id'], 'pos_lane_source': lane['source']}
    context = infer_pos_context(db, stay)
    if context['reason']:
        raise HTTPException(409, 'Гарах хаалтын холбоос тодорхойгүй; оператор шалгана уу')
    return {**context, 'status': 'lane_verified'}


def outer_exit_cameras(db, site_id):
    return db.query(Device).filter(Device.site_id == site_id, Device.device_type == 'camera',
        Device.status == 'active', Device.lane_dir.in_(('exit', 'both')),
        Device.nested_inner.is_(False)).all()


def pos_lane(db, stay, body):
    """POS card payment: the exit lane where the cashier physically stands.

    Sources, in order: the lane the POS request names (`device_id` /
    `exit_device_id` / `lane_no`, bootstrap `lanes[]`); otherwise the site's only
    outer exit lane. Several lanes and no lane in the request -> None (wait for
    the exit read as before). Never guesses from the cashier account or terminal
    id: the legacy app sends the same terminal id at five sites (2026-10-12).
    Returns {'camera', 'gate_id', 'source'} or None; never raises."""
    from ..session_logic import _find_barrier
    try:
        device_id, lane_no = _requested_lane(body or {})
        if device_id is not None or lane_no is not None:
            cams, source = lane_cameras(db, stay.site_id, device_id, lane_no), 'pos_request'
        else:
            cams, source = outer_exit_cameras(db, stay.site_id), 'single_exit_lane'
    except HTTPException:
        return None
    if len(cams) != 1:
        return None
    site = db.get(ParkingSite, stay.site_id)
    if not site or not site.is_active or not cams[0].ip_address:
        return None
    gate = _find_barrier(db, stay.site_id, cams[0])
    if gate is None:
        return None
    return {'camera': cams[0], 'gate_id': gate.id, 'source': source}
