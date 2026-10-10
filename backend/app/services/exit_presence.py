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


def fresh_presence(db, stay, now=None):
    """Latest exact accepted read on a uniquely bound live outer exit camera.

    Every newer different/unknown read invalidates presence, even if rejected.
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
    since = max(stay.entry_time, now - timedelta(seconds=presence_seconds()))
    event = (db.query(LprEvent).options(defer(LprEvent.raw)).filter(
        LprEvent.site_id == stay.site_id, LprEvent.device_id == cam.id,
        LprEvent.lane_dir == 'exit', LprEvent.created_at >= since)
        .order_by(LprEvent.created_at.desc(), LprEvent.id.desc()).first())
    if not event:
        return None, None, 'exit_read_stale'
    if event.created_at > now or not event.accepted or event.plate_number != stay.plate_number:
        return None, event, 'exit_presence_unconfirmed'
    if isinstance(event.raw, dict) and event.raw.get('_server_open_eligible') is False:
        return None, event, 'exit_read_stale'
    return cam, event, None


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


def validate_pos_context(db, stay, body):
    """Additive protocol: old POS requests remain valid; new context is enforced."""
    if body.get('device_id') and body.get('exit_device_id') and body['device_id'] != body['exit_device_id']:
        raise HTTPException(400, 'Гарах төхөөрөмжийн хоёр утга зөрж байна')
    device_id = body.get('device_id') or body.get('exit_device_id')
    lane_no = body.get('lane_no')
    event_id = body.get('exit_event_id')
    if device_id is None and lane_no is None and event_id is None:
        return infer_pos_context(db, stay)
    if lane_no is not None and (isinstance(lane_no, bool) or not isinstance(lane_no, int)):
        raise HTTPException(400, 'Эгнээний дугаар бүхэл тоо байна')
    cams = lane_cameras(db, stay.site_id, device_id, lane_no)
    cam, event, reason = fresh_presence(db, stay)
    if reason or not cam or cam.id not in {c.id for c in cams} or (event_id and event.id != event_id):
        raise HTTPException(409, 'Гарах уншилт шинэчлэгдсэн; жагсаалтаа шинэчлээд машинаа дахин сонгоно уу')
    context = infer_pos_context(db, stay)
    if context['reason']:
        raise HTTPException(409, 'Гарах хаалтын холбоос тодорхойгүй; оператор шалгана уу')
    return {**context, 'status': 'lane_verified'}
