"""Хаалт (barrier) удирдлага: гараар нээх, статус, командын лог."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..auth import require
from ..database import get_db
from ..models import AuditLog, BarrierCommand, Device, User
from ..serializers import to_dict
from ..ws import manager

router = APIRouter(prefix="/api/barriers", tags=["barriers"])


@router.post("/{device_id}/open")
async def manual_open(device_id: str, body: dict | None = None, db: Session = Depends(get_db),
                      user: User = Depends(require("barriers", "cashier"))):
    from ..services.barrier import open_barrier
    device = db.get(Device, device_id)
    if not device or device.device_type != "barrier":
        raise HTTPException(404, "Barrier төхөөрөмж олдсонгүй")
    cmd = await open_barrier(db, device, (body or {}).get("session_id"),
                             "manual", issued_by=user.username)
    db.add(AuditLog(username=user.username, action="BARRIER_OPEN", entity="device",
                    entity_id=device_id, detail={"result": cmd.status}))
    db.commit()
    await manager.broadcast(device.site_id, "BARRIER_MANUAL_OPEN", {
        "device_id": device_id, "device_name": device.name,
        "by": user.username, "status": cmd.status,
    })
    return {"status": cmd.status, "response": cmd.response_text}


@router.get("/commands")
def command_log(site_id: str | None = None, limit: int = 100,
                db: Session = Depends(get_db), user: User = Depends(require("barriers"))):
    q = db.query(BarrierCommand).join(Device, BarrierCommand.device_id == Device.id)
    if site_id:
        q = q.filter(Device.site_id == site_id)
    rows = q.order_by(BarrierCommand.created_at.desc()).limit(min(limit, 500)).all()
    return [to_dict(c, extra={"device_name": c.device.name if c.device else None,
                              "site_id": c.device.site_id if c.device else None}) for c in rows]
