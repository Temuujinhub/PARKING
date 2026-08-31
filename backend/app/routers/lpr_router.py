"""Dahua ITSAPI LPR callback.

Камерын тохиргоо: Setting > Network > Platform Access > ITSAPI
  URL: http://{server}/api/lpr/callback?device_key={device_key}
device_key нь тухайн камерыг СИСТЕМД бүртгэсэн Device.device_key-тэй таарна.
"""
import logging
import base64
import json as _json
import time as _time
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models import Device, LprEvent
from ..session_logic import (extract_confidence, handle_entry, handle_exit, handle_inner_pass,
                             normalize_plate, strip_images)

log = logging.getLogger("parking.lpr")

router = APIRouter(prefix="/api/lpr", tags=["lpr"])


async def _parse_push(request: Request) -> tuple[dict, bytes | None]:
    """ITSAPI push-ийг задлана. Камер хоёр хэлбэрээр илгээж болно:
      • JSON body (Picture.NormalPic.Content-д base64 зурагтай)
      • multipart/form-data — текст (JSON) хэсэг + зураг binary хэсэг
    Буцаах: (payload dict, зургийн raw bytes | None)."""
    ctype = (request.headers.get("content-type") or "").lower()
    if "multipart/form-data" in ctype:
        form = await request.form()
        payload: dict = {}
        image: bytes | None = None
        for _key, val in form.multi_items():
            if hasattr(val, "read"):  # файл хэсэг (зураг)
                data = await val.read()
                if isinstance(data, bytes) and data[:2] == b"\xff\xd8" and (
                        image is None or len(data) > len(image)):
                    image = data  # хамгийн том jpeg (бүтэн кадр)
            elif isinstance(val, str) and not payload:
                try:
                    payload = _json.loads(val)
                except Exception:
                    payload = {"_raw_text": val[:1000]}
        return payload, image
    try:
        return await request.json(), None
    except Exception:
        body = await request.body()
        return {"_raw_text": body[:1000].decode("utf-8", "replace")}, None


def _client_ip(request: Request) -> str:
    """Жинхэнэ эх IP.

    X-Forwarded-For-ыг ӨӨРӨӨ БҮҮ УНШ. nginx нь `$proxy_add_x_forwarded_for`-оор
    хэрэглэгчийн илгээсэн утгын АРД жинхэнэ IP-г залгадаг тул толгойн ЭХНИЙ утга
    нь бүхэлдээ халдагчийн мэдэлд байдаг. Өмнө нь энд `xff.split(",")[0]` байсан
    бөгөөд түүгээр интернэтээс камер болж дүр эсгэн, нэвтрэлтгүйгээр хаалт
    нээх боломжтой байв (2026-08-20-нд туршилтаар батлагдсан).

    uvicorn-ий ProxyHeadersMiddleware нь `forwarded_allow_ips` (default 127.0.0.1)
    -д багтсан proxy-гоос ирсэн үед л XFF-ийг уншиж, жагсаалтыг УРВУУГААР шалгаж
    итгэмжлэгдээгүй ЭХНИЙ хостыг `scope["client"]`-д тавьдаг. Тиймээс
    `request.client.host` нь nginx-ийн ард ч зөв, хуурагдахгүй утга өгнө.
    """
    return request.client.host if request.client else ""


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
            # БОДИТ итгэлцүүр: TrafficCar-т Confidence байдаггүй — жинхэнэ утга
            # Object.Confidence-д (аудит 2026-08-29: олдохгүй болохоор нь 100
            # тавьдаг байсан тул шүүлтүүр огт ажилладаггүй байв)
            return str(num), extract_confidence(event, c)
    return "", 0.0


@router.post("/callback")
async def lpr_callback(request: Request, device_key: str = "", db: Session = Depends(get_db)):
    payload, image = await _parse_push(request)
    client_ip = _client_ip(request)

    device = None
    if device_key:
        device = db.query(Device).filter(Device.device_key == device_key,
                                         Device.device_type == "camera",
                                         Device.status != "deleted").first()
    if not device and not settings.lpr_require_key:
        # ШИЛЖИЛТИЙН ЗАМ — device_key байхгүй бол эх IP-ээр таана.
        # Энэ нь НЭВТРЭЛТ БИШ: камерын IP нь нууц утга биш бөгөөд дотоод
        # диапазон нь амархан тааварлагддаг. Камер бүрийн push URL-д
        # `?device_key=...` нэмж дуусмагц PARKING_LPR_REQUIRE_KEY=true болгож
        # энэ замыг бүрмөсөн хаана.
        device = db.query(Device).filter(Device.ip_address == client_ip,
                                         Device.device_type == "camera",
                                         Device.status != "deleted").first()
        if device:
            log.warning(
                "LPR_NO_KEY: '%s' (%s) түлхүүргүй ирж, зөвхөн IP-ээр танигдав. "
                "Камерын ITSAPI URL-д ?device_key=%s нэмнэ үү — эс бөгөөс "
                "PARKING_LPR_REQUIRE_KEY=true болгоход энэ камер тасарна.",
                device.name, client_ip, device.device_key)

    events = _extract_events(payload)
    _p0 = _extract_plate(events[0])[0] if events and isinstance(events[0], dict) else ""
    # БҮХ ирж буй push-ийг (амжилттай/амжилтгүй) логлоно — камерын "Push Results" 0
    # байвал энэ мөрөөс шалтгааныг (device танихгүй / зураг ирээгүй) шууд харна.
    log.info(f"lpr_push: ip={client_ip} key={device_key or '-'} "
          f"device={device.name if device else 'ОЛДСОНГҮЙ'} plate={_p0 or '-'} "
          f"image={len(image) if image else 0}b events={len(events)} "
          f"ctype={(request.headers.get('content-type') or '')[:30]} "
          f"keys={list(payload)[:8]}")
    # Firmware яг ЯМАР бүтэцтэй илгээж байгааг харах (зураггүй, тайрсан) — парсинг
    # тохирч байгаа эсэхийг батлах/засахад
    try:
        log.info(f"lpr_push RAW: {_json.dumps(strip_images(payload), ensure_ascii=False)[:600]}")
    except Exception:
        pass
    if not device:
        # 200 буцаая — камер дахин дахин оролдож логоо дүүргэхгүйн тулд (шалтгаан логонд бий)
        return {"ok": False, "error": "camera not registered", "ip": client_ip}

    device.last_seen = datetime.utcnow()
    # Multipart-аар ирсэн зургийг event бүрт base64-оор шингээнэ — snapshot.cgi татах
    # шаардлагагүйгээр (найдвартай) яг event-ийн зураг хадгалагдана.
    if image:
        b64 = base64.b64encode(image).decode()
        for ev in events:
            if isinstance(ev, dict):
                ev.setdefault("Picture", {}).setdefault("NormalPic", {})["Content"] = b64
    results = []
    for event in events:
        raw_plate, conf = _extract_plate(event)
        plate = normalize_plate(raw_plate)
        if not plate:
            # Дугаар танигдаагүй/формат таарахгүй бол raw-ийг логд хадгална (камер тохируулахад тусална)
            db.add(LprEvent(site_id=device.site_id, device_id=device.id, plate_number="?",
                            lane_dir=device.lane_dir, confidence=0, accepted=False,
                            reject_reason="plate not parsed", raw=strip_images(event)))
            db.commit()
            continue
        if conf < settings.lpr_min_confidence:
            db.add(LprEvent(site_id=device.site_id, device_id=device.id, plate_number=plate,
                            lane_dir=device.lane_dir, confidence=conf, accepted=False,
                            reject_reason=f"confidence<{settings.lpr_min_confidence}", raw=strip_images(event)))
            db.commit()
            continue
        # Дугаар танигдмагц хаалт нээх хүртэлх БҮТЭН хугацааг хэмжинэ — «удаан
        # нээгдэж байна» гомдлыг лог дээрээс тоогоор шалгах боломжтой болно
        _t0 = _time.monotonic()
        if device.nested_inner:
            # Давхар зогсоолын ДОТООД хаалт — session нээхгүй/хаахгүй, зөвхөн
            # төлбөрийн тоолуурыг зогсоож/үргэлжлүүлээд хаалтаа нээнэ
            res = await handle_inner_pass(db, device, plate, conf, event)
        elif device.lane_dir == "exit":
            res = await handle_exit(db, device, plate, conf, event)
        else:
            res = await handle_entry(db, device, plate, conf, event)
        _ms = int((_time.monotonic() - _t0) * 1000)
        res["elapsed_ms"] = _ms
        (log.warning if _ms >= settings.lpr_slow_warn_ms else log.info)(
            "lpr %s %s → %s: %dмс", device.lane_dir, plate, res.get("action", "?"), _ms)
        results.append(res)
    return {"ok": True, "results": results}


@router.api_route("/keepalive", methods=["GET", "POST"])
@router.api_route("/register", methods=["GET", "POST"])
async def lpr_keepalive(request: Request, device_key: str = "", db: Session = Depends(get_db)):
    """Dahua ITSAPI Registration/Heartbeat (KeepAlive) — камер платформ амьд эсэхийг шалгана.
    200 буцаана. Камерыг device_key ЭСВЭЛ эх IP-ээр таньж last_seen шинэчилнэ (онлайн статус)."""
    dev = None
    if device_key:
        dev = db.query(Device).filter(Device.device_key == device_key,
                                      Device.status != "deleted").first()
    if not dev:
        # Heartbeat Interface-д device_key байхгүй бол эх IP-ээр таних
        dev = db.query(Device).filter(Device.ip_address == _client_ip(request),
                                      Device.device_type == "camera",
                                      Device.status != "deleted").first()
    if dev:
        dev.last_seen = datetime.utcnow()
        db.commit()
    return {"ok": True, "result": True}


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
    # Бодит callback-той ижил хугацааны хэмжилт — оношилгоонд simulate-аар
    # хэмжсэн үзүүлэлт бодитыг төлөөлнө
    _t0 = _time.monotonic()
    if device.nested_inner:
        res = await handle_inner_pass(db, device, plate, conf, raw)
    elif device.lane_dir == "exit":
        res = await handle_exit(db, device, plate, conf, raw)
    else:
        res = await handle_entry(db, device, plate, conf, raw)
    _ms = int((_time.monotonic() - _t0) * 1000)
    res["elapsed_ms"] = _ms
    (log.warning if _ms >= settings.lpr_slow_warn_ms else log.info)(
        "lpr(sim) %s %s → %s: %dмс", device.lane_dir, plate, res.get("action", "?"), _ms)
    return res
