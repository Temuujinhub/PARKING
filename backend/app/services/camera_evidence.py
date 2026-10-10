"""Allowlisted camera evidence. Vehicle confidence is never plate confidence."""
import math


def node(value):
    return value if isinstance(value, dict) else {}


def text(value, limit=64):
    return str(value)[:limit] if isinstance(value, (str, int, float)) and not isinstance(value, bool) else None


def confidence(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) and 0 <= number <= 100 else None
    except (TypeError, ValueError):
        return None


def camera_evidence(raw):
    raw = node(raw)
    picture, obj, vehicle, traffic = (node(raw.get(k)) for k in ('Picture', 'Object', 'Vehicle', 'TrafficCar'))
    sources = [('Plate', node(raw.get('Plate'))), ('TrafficCar', traffic),
               ('Picture.Plate', node(picture.get('Plate'))), ('', raw)]
    if obj.get('ObjectType') == 'Plate':
        sources.append(('Object', obj))
    plates = []
    score, score_source, chosen = None, None, {}
    for prefix, source in sources:
        value = next((source[k] for k in ('PlateNumber', 'PlateNo', 'plateNumber')
                      if isinstance(source.get(k), str) and source[k].strip()), None)
        if value:
            plates.append({'source': prefix or 'root', 'text': value[:32]})
            if not chosen:
                chosen = source
    # Keep 0 as an explicitly low score. Invalid values are unknown, never 100.
    for prefix, source in [('plate_text', chosen), ('Object', obj if obj.get('ObjectType') == 'Plate' else {}), ('root', raw)]:
        for key in ('Confidence', 'Accuracy'):
            if key in source:
                score, score_source = confidence(source[key]), f'{prefix}.{key}'
                break
        if score_source:
            break
    from ..session_logic import normalize_plate
    identities = {normalize_plate(p['text']) for p in plates} - {''}
    return {'plate_texts': plates, 'plate_confidence': score, 'confidence_source': score_source,
            'text_conflict': len(identities) > 1,
            'vehicle_confidence': confidence(vehicle.get('Confidence')),
            'vehicle_color': text(vehicle.get('Color') or traffic.get('VehicleColor')),
            'vehicle_type': text(vehicle.get('ObjectType') or traffic.get('VehicleType')),
            'event_code': text(raw.get('Code')), 'camera_time': text(raw.get('RealUTC') or raw.get('UTC')),
            'frame_id': text(raw.get('FrameSequence') or raw.get('Sequence')),
            'object_id': text(obj.get('ObjectID')), 'reported_lane': text(traffic.get('Lane')),
            'reported_direction': text(traffic.get('Direction')),
            'has_plate_crop': bool(node(picture.get('CutoutPic')).get('Content') or node(raw.get('CutoutPic')).get('Content')),
            'automatic_decision': False}


def exit_evidence_rejection(raw, minimum):
    evidence = camera_evidence(raw)
    if evidence['text_conflict']:
        return 'plate_metadata_conflict', evidence['plate_confidence'] or 0
    score = evidence['plate_confidence']
    if score is None:
        return 'plate_confidence_unknown', 0
    if score < minimum:
        return f'confidence<{minimum}', score
    return None, score


def legacy_plate_read(raw):
    """Keep entry/inner confidence fallback rules; outer exits validate strictly."""
    evidence = camera_evidence(raw)
    if not evidence['plate_texts']:
        return '', 0.0
    first = evidence['plate_texts'][0]
    source = raw
    if first['source'] != 'root':
        for key in first['source'].split('.'):
            source = node(source).get(key)
    from ..session_logic import extract_confidence
    return first['text'], extract_confidence(raw, node(source))
