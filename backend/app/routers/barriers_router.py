"""Хаалт (barrier) удирдлага: гараар нээх, статус, командын лог."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..auth import enforce_site, require
from ..database import get_db
from ..models import AuditLog, BarrierCommand, Device, User
from ..serializers import to_dict
from ..ws import manager

router = APIRouter(prefix="/api/barriers", tags=["barriers"])


@router.post("/{device_id}/open")
async def manual_open(device_id: str, body: dict | None = None, db: Session = Depends(get_db),
                      user: User = Depends(require("barriers", "cashier"))):
    """Гараар нээх. body: {session_id?, force?} — force=true үед forceBreaking
    (албадан онгойлгоод барих, гацсан үед)."""
    from ..services.barrier import open_barrier
    device = db.get(Device, device_id)
    if not device or device.device_type != "barrier":
        raise HTTPException(404, "Barrier төхөөрөмж олдсонгүй")
    enforce_site(user, device.site_id)  # оператор зөвхөн өөрийн зогсоолын хаалт
    force = bool((body or {}).get("force"))
    cmd = await open_barrier(db, device, (body or {}).get("session_id"),
                             "manual", issued_by=user.username, force=force)
    db.add(AuditLog(username=user.username, action="BARRIER_OPEN", entity="device",
                    entity_id=device_id, detail={"result": cmd.status, "force": force}))
    db.commit()
    await manager.broadcast(device.site_id, "BARRIER_MANUAL_OPEN", {
        "device_id": device_id, "device_name": device.name,
        "by": user.username, "status": cmd.status, "force": force,
    })
    return {"status": cmd.status, "response": cmd.response_text}


@router.post("/{device_id}/close")
async def manual_close(device_id: str, body: dict | None = None, db: Session = Depends(get_db),
                       user: User = Depends(require("barriers", "cashier"))):
    """Гараар хаах (closeStrobe). Албадан нээснийг буцаах, туршилтын дараа хаах гэх мэт."""
    from ..services.barrier import close_barrier
    device = db.get(Device, device_id)
    if not device or device.device_type != "barrier":
        raise HTTPException(404, "Barrier төхөөрөмж олдсонгүй")
    enforce_site(user, device.site_id)  # оператор зөвхөн өөрийн зогсоолын хаалт
    cmd = await close_barrier(db, device, (body or {}).get("session_id"),
                              "manual", issued_by=user.username)
    db.add(AuditLog(username=user.username, action="BARRIER_CLOSE", entity="device",
                    entity_id=device_id, detail={"result": cmd.status}))
    db.commit()
    await manager.broadcast(device.site_id, "BARRIER_MANUAL_CLOSE", {
        "device_id": device_id, "device_name": device.name,
        "by": user.username, "status": cmd.status,
    })
    return {"status": cmd.status, "response": cmd.response_text}


@router.post("/{device_id}/display")
async def screen_display(device_id: str, body: dict, db: Session = Depends(get_db),
                         user: User = Depends(require("barriers", "cashier"))):
    """LED дэлгэц тест — камерын дэлгэцэнд текст харуулна.
    body: {text, voice?} — voice=true үед дуут зарлал давхар явуулна.
    Камер эсвэл хаалт төхөөрөмжийн аль алиныг зааж болно (IP-г ижил дүрмээр олно)."""
    from ..services.barrier import _resolve_ip, display_on_screen
    device = db.get(Device, device_id)
    if not device:
        raise HTTPException(404, "Төхөөрөмж олдсонгүй")
    enforce_site(user, device.site_id)
    text = str(body.get("text") or "").strip()
    if not text or len(text) > 64:
        raise HTTPException(400, "text талбар шаардлагатай (1-64 тэмдэгт)")
    ip = _resolve_ip(db, device)
    if not ip:
        raise HTTPException(400, "Төхөөрөмжид IP бүртгэлгүй байна")
    err = await display_on_screen(ip, text, text if body.get("voice") else None)
    db.add(AuditLog(username=user.username, action="SCREEN_DISPLAY", entity="device",
                    entity_id=device_id, detail={"text": text, "error": err or None}))
    db.commit()
    if err:
        raise HTTPException(502, f"Дэлгэц рүү илгээж чадсангүй: {err}")
    return {"status": "SUCCESS", "ip": ip, "text": text}


@router.get("/commands")
def command_log(site_id: str | None = None, limit: int = 100,
                db: Session = Depends(get_db), user: User = Depends(require("barriers"))):
    q = db.query(BarrierCommand).join(Device, BarrierCommand.device_id == Device.id)
    if site_id:
        q = q.filter(Device.site_id == site_id)
    rows = q.order_by(BarrierCommand.created_at.desc()).limit(min(limit, 500)).all()
    return [to_dict(c, extra={"device_name": c.device.name if c.device else None,
                              "site_id": c.device.site_id if c.device else None}) for c in rows]
