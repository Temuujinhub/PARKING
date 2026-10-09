"""Validated map metadata and the same occupancy semantics used by admin UI."""
import math
from urllib.parse import urlsplit

from fastapi import HTTPException
from sqlalchemy import func

from ..models import ParkingSession, ParkingSite


def maps_url(value):
    value = (value or '').strip()
    if not value:
        return None
    try:
        url = urlsplit(value)
        host = url.hostname
        path_ok = (host in ('maps.app.goo.gl', 'maps.google.com')
                   or host in ('google.com', 'www.google.com') and (url.path == '/maps' or url.path.startswith('/maps/'))
                   or host == 'goo.gl' and url.path.startswith('/maps/'))
        valid = url.scheme == 'https' and path_ok and not url.username and not url.password and url.port in (None, 443)
        valid = valid and not any(ch.isspace() or ord(ch) < 32 for ch in value) and '\\' not in value
    except ValueError:
        valid = False
    if not valid or len(value) > 2048:
        raise ValueError('Зөв HTTPS Google Maps холбоос оруулна уу.')
    return value


def validate_coordinates(body, site=None):
    lat = body.get('latitude', getattr(site, 'latitude', None))
    lng = body.get('longitude', getattr(site, 'longitude', None))
    if (lat is None) != (lng is None):
        raise HTTPException(422, 'Өргөрөг, уртрагыг хоёуланг нь оруулна уу.')
    if lat is not None and not (math.isfinite(float(lat)) and math.isfinite(float(lng))
                                and -90 <= float(lat) <= 90 and -180 <= float(lng) <= 180):
        raise HTTPException(422, 'Координатын утга буруу байна.')


def occupancy_counts(db, site_ids=None):
    query = db.query(ParkingSession.site_id, func.count(),
                     func.count().filter(ParkingSession.paused_since.isnot(None))).filter(
        ParkingSession.status.in_(('OPEN', 'AWAITING_PAYMENT', 'PAID')))
    if site_ids is not None:
        query = query.filter(ParkingSession.site_id.in_(site_ids))
    counts = query.group_by(ParkingSession.site_id).all()
    occupied = {sid: total for sid, total, paused in counts}
    inside = {sid: paused for sid, total, paused in counts}
    child_query = db.query(ParkingSite.parent_site_id, func.count()).filter(ParkingSite.parent_site_id.isnot(None))
    if site_ids is not None:
        child_query = child_query.filter(ParkingSite.parent_site_id.in_(site_ids))
    children = dict(child_query.group_by(ParkingSite.parent_site_id).all())
    return occupied, inside, children


def occupied_count(site_id, occupied, inside, children):
    # Parent/child sites have two sessions; a single site with inner cameras has one.
    return max(0, occupied.get(site_id, 0) - (inside.get(site_id, 0) if children.get(site_id) else 0))
