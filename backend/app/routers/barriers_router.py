"""Хаалт (barrier) удирдлага: гараар нээх, статус, командын лог."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..auth import enforce_site, has_permission, operator_sites, require
from ..database import get_db
from ..models import AuditLog, BarrierCommand, Device, User
from ..serializers import to_dict
from ..ws import manager

router = APIRouter(prefix="/api/barriers", tags=["barriers"])


@router.post("/{device_id}/open")
async def manual_open(device_id: str, body: dict | None = None, db: Session = Depends(get_db),
                      user: User = Depends(require("barriers", "free_exit"))):
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
                       user: User = Depends(require("barriers", "free_exit"))):
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
                         user: User = Depends(require("barriers", "free_exit"))):
    """LED дэлгэц тест — камерын дэлгэцэнд текст харуулна.
    body: {text, voice?} — voice=true үед дуут зарлал давхар явуулна.
    Камер эсвэл хаалт төхөөрөмжийн аль алиныг зааж болно (IP-г ижил дүрмээр олно)."""
    from ..services.barrier import _resolve_device, display_on_screen
    from ..services.device_auth import barrier_credentials
    device = db.get(Device, device_id)
    if not device:
        raise HTTPException(404, "Төхөөрөмж олдсонгүй")
    enforce_site(user, device.site_id)
    text = str(body.get("text") or "").strip()
    # 4 мөр (|-ээр тусгаарласан) + кирилл багтахаар 128 хүртэл
    if not text or len(text) > 128:
        raise HTTPException(400, "text талбар шаардлагатай (1-128 тэмдэгт)")
    ip, target = _resolve_device(db, device)
    if not ip:
        raise HTTPException(400, "Төхөөрөмжид IP бүртгэлгүй байна")
    err = await display_on_screen(ip, text, text if body.get("voice") else None,
                            creds=barrier_credentials(target))
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


@router.get("/devices")
def barrier_devices(site_id: str | None = None, db: Session = Depends(get_db),
                    user: User = Depends(require("barriers", "free_exit", "cashier",
                                                "devices", "settings"))):
    """Хаалт нээх товчинд хэрэгтэй НИМГЭН жагсаалт (POS/касс).

    Яагаад тусдаа endpoint вэ: `GET /api/admin/devices` нь 2026-08-20-ны аюулгүй
    байдлын хатууруулалтаар `devices/settings/barriers` эрхээр хаагдсан — хариу
    нь камерын `device_key` агуулдаг бөгөөд тэр түлхүүрээр хуурамч LPR event
    илгээж хаалт нээх боломжтой байсан. Гэтэл PAX POS нь хаалтаа нээхийн тулд
    `device_id`-г ЗӨВХӨН тэндээс олдог байсан тул операторын «хаалт нээх» товч
    403 иддэг болов (prod дээр 13 удаагийн 403-оор батлагдсан).

    Тиймээс энд ЗӨВХӨН хаалтны id/нэр/эгнээ буцаана — `device_key`, IP, нэвтрэх
    нэр/нууц үг ОГТ БАЙХГҮЙ. Ингэснээр хатууруулалтын зорилго хэвээр үлдэж,
    `free_exit`/`barriers` эрхтэй оператор хаалтаа нээх боломжтой болно.
    """
    return lean_barrier_rows(db, user, site_id)


def lean_barrier_rows(db: Session, user: User, site_id: str | None = None) -> list[dict]:
    """Хаалтны НУУЦ ТАЛБАРГҮЙ мөрүүд. `GET /admin/devices` нь эрх багатай
    хэрэглэгчид (касс/POS) мөн энэ хэлбэрээр хариулдаг тул ХУУЧИН POS build ч
    ажиллана — доорх талбаруудаас өөр юу ч задрахгүй."""
    # ХААЛТ НЭЭХ эрхтэй эсэх — POS/касс энэ тугаар «Хаалт нээх» товчийг
    # ХАРУУЛАХ/НУУНА. Жагсаалтыг кассын эрхээр өгдөг (машины эгнээ харуулахад
    # хэрэгтэй) ч нээх нь `free_exit`/`barriers` эрх шаардана — товчийг нуухгүй
    # бол оператор дараад 403 иддэг, шалтгаан нь ойлгомжгүй байдаг.
    can_open = has_permission(user, "barriers") or has_permission(user, "free_exit")
    q = db.query(Device).filter(Device.device_type == "barrier", Device.status != "deleted")
    allowed = operator_sites(user)
    if site_id:
        enforce_site(user, site_id)  # эрхгүй зогсоолын хаалт руу IDOR хийхээс сэргийлнэ
        q = q.filter(Device.site_id == site_id)
    elif allowed:
        q = q.filter(Device.site_id.in_(allowed))
    return [{"id": d.id, "site_id": d.site_id, "name": d.name,
             "device_type": "barrier",  # хуучин POS энэ талбараар шүүдэг
             "lane_no": d.lane_no, "lane_dir": d.lane_dir,
             "auto_open": d.auto_open, "status": d.status,
             # POS/касс: false бол «Хаалт нээх» товчийг ХАРУУЛАХГҮЙ
             "can_open": can_open,
             "last_seen": d.last_seen.isoformat() if d.last_seen else None}
            for d in q.order_by(Device.lane_dir, Device.lane_no, Device.created_at).all()]
