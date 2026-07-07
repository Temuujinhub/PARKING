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
    """Dahua ITSAPI-ийн хэд хэдэн хувилбарын бүтцийг дэмжинэ:
    - eventManager CGI:  {"Events": [{"TrafficCar": {...}}]}
    - ITSAPI TollgateInfo: {"Plate": {...}, "VehicleType": ...}  ← гарах/орох камерын үндсэн формат
    - Picture wrapper:     {"Picture": {"Plate": {...}}}
    """
    if isinstance(payload.get("Events"), list):
        return payload["Events"]
    # TollgateInfo болон бусад дан event-ийг нэг элементтэй жагсаалт болгоно
    return [payload]


def _extract_plate(event: dict) -> tuple[str, float]:
    """Event-ийн олон боломжит байршлаас дугаар + итгэлцүүрийг гаргана."""
    # Боломжит байрлалууд: Plate.PlateNumber (ITSAPI), TrafficCar.PlateNumber (CGI),
    # Picture.Plate.PlateNumber, дээд түвшний PlateNumber
    candidates = [
        event.get("Plate"),
        event.get("TrafficCar"),
        (event.get("Picture") or {}).get("Plate"),
        event,  # дээд түвшинд шууд байвал
    ]
    for c in candidates:
        if not isinstance(c, dict):
            continue
        num = c.get("PlateNumber") or c.get("PlateNo") or c.get("plateNumber")
        if num:
            conf = c.get("Confidence") or c.get("Accuracy") or event.get("Confidence") or 100
            try:
                conf = float(conf)
            except (ValueError, TypeError):
                conf = 100.0
            return str(num), conf
    return "", 0.0


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
        raw_plate, conf = _extract_plate(event)
        plate = normalize_plate(raw_plate)
        if not plate:
            # Дугаар танигдаагүй/формат таарахгүй бол raw-ийг логд хадгална (камер тохируулахад тусална)
            db.add(LprEvent(site_id=device.site_id, device_id=device.id, plate_number="?",
                            lane_dir=device.lane_dir, confidence=0, accepted=False,
                            reject_reason="plate not parsed", raw=event))
            db.commit()
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
    """Туршилтын event (хөгжүүлэлт/демонд). body: {device_key, plate, confidence?}
    Production-д (PARKING_ALLOW_SIMULATE=false эсвэл barrier бодит болмогц) хаагдана."""
    from ..config import settings
    if not settings.allow_simulate or not settings.barrier_mock:
        raise HTTPException(403, "Simulate endpoint production-д идэвхгүй байна")
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
