"""Dahua ITSAPI LPR callback.

Камерын тохиргоо: Setting > Network > Platform Access > ITSAPI
  URL: http://{server}/api/lpr/callback?device_key={device_key}
device_key нь тухайн камерыг СИСТЕМД бүртгэсэн Device.device_key-тэй таарна.
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models import Device, LprEvent
from ..session_logic import handle_entry, handle_exit, normalize_plate

router = APIRouter(prefix="/api/lpr", tags=["lpr"])


def _extract_events(payload: dict) -> list[dict]:
    """Dahua ITSAPI-ийн хэд хэдэн хувилбарын бүтцийг дэмжинэ."""
    if "Events" in payload:
        return payload["Events"] or []
    if "Picture" in payload or "TrafficCar" in payload:
        return [payload]
    return []


@router.post("/callback")
async def lpr_callback(request: Request, device_key: str = "", db: Session = Depends(get_db)):
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(400, "JSON body шаардлагатай")

    device = None
    if device_key:
        device = db.query(Device).filter(Device.device_key == device_key,
                                         Device.device_type == "camera").first()
    if not device:
        # device_key байхгүй бол IP-ээр таних оролдлого
        client_ip = request.client.host if request.client else ""
        device = db.query(Device).filter(Device.ip_address == client_ip,
                                         Device.device_type == "camera").first()
    if not device:
        raise HTTPException(404, "Камер бүртгэлгүй байна. Device.device_key тохируулна уу.")

    device.last_seen = datetime.utcnow()
    results = []
    for event in _extract_events(payload):
        car = event.get("TrafficCar") or event.get("Picture", {}).get("Plate") or {}
        plate = normalize_plate(car.get("PlateNumber") or car.get("PlateNo") or "")
        conf = float(car.get("Confidence") or event.get("Confidence") or 100)
        if not plate:
            continue
        if conf < settings.lpr_min_confidence:
            db.add(LprEvent(site_id=device.site_id, device_id=device.id, plate_number=plate,
                            lane_dir=device.lane_dir, confidence=conf, accepted=False,
                            reject_reason=f"confidence<{settings.lpr_min_confidence}", raw=event))
            db.commit()
            continue
        if device.lane_dir == "exit":
            results.append(await handle_exit(db, device, plate, conf, event))
        else:
            results.append(await handle_entry(db, device, plate, conf, event))
    return {"ok": True, "results": results}


@router.post("/simulate")
async def simulate_lpr(body: dict, db: Session = Depends(get_db)):
    """Туршилтын event (хөгжүүлэлт/демонд). body: {device_key, plate, confidence?}"""
    device = db.query(Device).filter(Device.device_key == body.get("device_key")).first()
    if not device:
        raise HTTPException(404, "Төхөөрөмж олдсонгүй")
    plate = normalize_plate(body.get("plate", ""))
    if not plate:
        raise HTTPException(400, "plate шаардлагатай")
    conf = float(body.get("confidence", 99))
    raw = {"simulated": True}
    device.last_seen = datetime.utcnow()
    if device.lane_dir == "exit":
        return await handle_exit(db, device, plate, conf, raw)
    return await handle_entry(db, device, plate, conf, raw)
