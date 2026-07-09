"""Тохиргооны CRUD: зогсоол, төхөөрөмж, тарифын загвар, хөнгөлөлт, жолооч, хар жагсаалт, хэрэглэгч."""
import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..auth import get_current_user, hash_password, require, require_role
from ..database import get_db
from ..models import (
    AuditLog, BlacklistEntry, Device, Discount, ParkingSession, ParkingSite,
    RegisteredDriver, TariffTemplate, TariffTier, User,
)
from ..serializers import to_dict

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _audit(db: Session, user: User, action: str, entity: str, entity_id: str, detail: dict | None = None):
    db.add(AuditLog(username=user.username, action=action, entity=entity,
                    entity_id=str(entity_id), detail=detail or {}))


# ─────────────────────────── Зогсоол ───────────────────────────
@router.get("/sites")
def list_sites(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    sites = db.query(ParkingSite).order_by(ParkingSite.created_at).all()
    out = []
    for s in sites:
        occupied = db.query(ParkingSession).filter(
            ParkingSession.site_id == s.id,
            ParkingSession.status.in_(["OPEN", "AWAITING_PAYMENT", "PAID"]),
        ).count()
        out.append(to_dict(s, extra={
            "occupied": occupied,
            "free_spaces": max(0, (s.capacity or 0) - occupied),
            "tariff_template_name": s.tariff_template.name if s.tariff_template else None,
        }))
    return out


@router.post("/sites")
def create_site(body: dict, db: Session = Depends(get_db), user: User = Depends(require("settings"))):
    if db.query(ParkingSite).filter(ParkingSite.site_code == body["site_code"]).first():
        raise HTTPException(400, "site_code давхардаж байна")
    site = ParkingSite(**{k: body[k] for k in
                          ("name", "site_code", "zone_code", "address", "capacity", "tariff_template_id")
                          if k in body})
    db.add(site)
    db.flush()
    _audit(db, user, "CREATE", "site", site.id, body)
    db.commit()
    return to_dict(site)


@router.put("/sites/{site_id}")
def update_site(site_id: str, body: dict, db: Session = Depends(get_db),
                user: User = Depends(require("settings"))):
    site = db.get(ParkingSite, site_id)
    if not site:
        raise HTTPException(404, "Зогсоол олдсонгүй")
    for k in ("name", "site_code", "zone_code", "address", "capacity", "tariff_template_id", "is_active"):
        if k in body:
            setattr(site, k, body[k])
    _audit(db, user, "UPDATE", "site", site_id, body)
    db.commit()
    return to_dict(site)


# ─────────────────────────── Төхөөрөмж ───────────────────────────
@router.get("/devices")
def list_devices(site_id: str | None = None, db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    from datetime import timedelta
    q = db.query(Device)
    if site_id:
        q = q.filter(Device.site_id == site_id)
    # Онлайн = сүүлийн 3 минутад холбогдсон (heartbeat эсвэл LPR event)
    online_cutoff = datetime.utcnow() - timedelta(minutes=3)
    out = []
    for d in q.order_by(Device.created_at).all():
        online = bool(d.last_seen and d.last_seen >= online_cutoff)
        out.append(to_dict(d, extra={"site_name": d.site.name if d.site else None,
                                     "online": online}))
    return out


@router.post("/devices")
def create_device(body: dict, db: Session = Depends(get_db), user: User = Depends(require("settings"))):
    device = Device(**{k: body[k] for k in
                       ("site_id", "name", "device_type", "vendor", "model", "ip_address",
                        "lane_no", "lane_dir", "auto_open") if k in body})
    device.device_key = f"{body.get('device_type','dev')}-{secrets.token_hex(8)}"
    db.add(device)
    db.flush()
    _audit(db, user, "CREATE", "device", device.id, body)
    db.commit()
    return to_dict(device)


@router.put("/devices/{device_id}")
def update_device(device_id: str, body: dict, db: Session = Depends(get_db),
                  user: User = Depends(require("settings"))):
    device = db.get(Device, device_id)
    if not device:
        raise HTTPException(404, "Төхөөрөмж олдсонгүй")
    for k in ("name", "device_type", "vendor", "model", "ip_address", "lane_no",
              "lane_dir", "auto_open", "status", "site_id"):
        if k in body:
            setattr(device, k, body[k])
    _audit(db, user, "UPDATE", "device", device_id, body)
    db.commit()
    return to_dict(device)


@router.post("/devices/{device_id}/test-connection")
async def test_device_connection(device_id: str, db: Session = Depends(get_db),
                                 user: User = Depends(require("settings", "barriers"))):
    """Сервер → төхөөрөмж холболт шалгах (TCP connect камерын web порт руу).
    Хариу: {reachable, ms, detail}. Камерын IP-г урьдчилан бүртгэсэн байх ёстой."""
    import asyncio
    import time
    device = db.get(Device, device_id)
    if not device:
        raise HTTPException(404, "Төхөөрөмж олдсонгүй")
    if not device.ip_address:
        return {"reachable": False, "detail": "IP хаяг бүртгэгдээгүй байна"}
    port = 80
    t0 = time.monotonic()
    try:
        fut = asyncio.open_connection(device.ip_address, port)
        reader, writer = await asyncio.wait_for(fut, timeout=3.0)
        writer.close()
        ms = int((time.monotonic() - t0) * 1000)
        return {"reachable": True, "ms": ms,
                "detail": f"Сервер {device.ip_address}:{port} руу хүрч байна ({ms}ms)"}
    except asyncio.TimeoutError:
        return {"reachable": False, "detail": f"{device.ip_address}:{port} — timeout (routing/firewall)"}
    except Exception as e:
        return {"reachable": False, "detail": f"{device.ip_address} — {e}"}


@router.delete("/devices/{device_id}")
def delete_device(device_id: str, db: Session = Depends(get_db), user: User = Depends(require("settings"))):
    device = db.get(Device, device_id)
    if not device:
        raise HTTPException(404, "Төхөөрөмж олдсонгүй")
    device.status = "deleted"
    _audit(db, user, "DELETE", "device", device_id)
    db.commit()
    return {"ok": True}


# ─────────────────────────── Тарифын загвар ───────────────────────────
@router.get("/tariff-templates")
def list_templates(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    templates = db.query(TariffTemplate).order_by(TariffTemplate.created_at).all()
    return [to_dict(t, extra={"tiers": [to_dict(x) for x in t.tiers]}) for t in templates]


@router.post("/tariff-templates")
def create_template(body: dict, db: Session = Depends(get_db), user: User = Depends(require("settings"))):
    t = TariffTemplate(
        name=body["name"],
        free_minutes=body.get("free_minutes", 0),
        grace_minutes=body.get("grace_minutes", 15),
        prepaid_price=body.get("prepaid_price", 0),
        extra_hour_price=body.get("extra_hour_price", 0),
        daily_cap=body.get("daily_cap"),
    )
    db.add(t)
    db.flush()
    for tier in body.get("tiers", []):
        db.add(TariffTier(template_id=t.id, upto_minutes=tier["upto_minutes"], price=tier["price"]))
    _audit(db, user, "CREATE", "tariff_template", t.id, body)
    db.commit()
    db.refresh(t)
    return to_dict(t, extra={"tiers": [to_dict(x) for x in t.tiers]})


@router.put("/tariff-templates/{template_id}")
def update_template(template_id: str, body: dict, db: Session = Depends(get_db),
                    user: User = Depends(require("settings"))):
    t = db.get(TariffTemplate, template_id)
    if not t:
        raise HTTPException(404, "Загвар олдсонгүй")
    for k in ("name", "free_minutes", "grace_minutes", "prepaid_price",
              "extra_hour_price", "daily_cap", "is_active"):
        if k in body:
            setattr(t, k, body[k])
    if "tiers" in body:
        db.query(TariffTier).filter(TariffTier.template_id == t.id).delete()
        for tier in body["tiers"]:
            db.add(TariffTier(template_id=t.id, upto_minutes=tier["upto_minutes"], price=tier["price"]))
    _audit(db, user, "UPDATE", "tariff_template", template_id, body)
    db.commit()
    db.refresh(t)
    return to_dict(t, extra={"tiers": [to_dict(x) for x in t.tiers]})


# ─────────────────────────── Хөнгөлөлт ───────────────────────────
@router.get("/discounts")
def list_discounts(db: Session = Depends(get_db), user: User = Depends(require("discounts", "cashier"))):
    return [to_dict(d) for d in db.query(Discount).order_by(Discount.created_at).all()]


@router.post("/discounts")
def create_discount(body: dict, db: Session = Depends(get_db), user: User = Depends(require("discounts"))):
    d = Discount(name=body["name"], discount_type=body["discount_type"], value=body["value"])
    db.add(d)
    db.flush()
    _audit(db, user, "CREATE", "discount", d.id, body)
    db.commit()
    return to_dict(d)


@router.put("/discounts/{discount_id}")
def update_discount(discount_id: str, body: dict, db: Session = Depends(get_db),
                    user: User = Depends(require("discounts"))):
    d = db.get(Discount, discount_id)
    if not d:
        raise HTTPException(404, "Хөнгөлөлт олдсонгүй")
    for k in ("name", "discount_type", "value", "is_active"):
        if k in body:
            setattr(d, k, body[k])
    _audit(db, user, "UPDATE", "discount", discount_id, body)
    db.commit()
    return to_dict(d)


# ─────────────────────────── Бүртгэлтэй жолооч ───────────────────────────
@router.get("/drivers")
def list_drivers(q: str | None = None, db: Session = Depends(get_db),
                 user: User = Depends(require("drivers"))):
    query = db.query(RegisteredDriver).order_by(RegisteredDriver.created_at.desc())
    if q:
        query = query.filter(RegisteredDriver.plate_number.ilike(f"%{q.upper()}%"))
    return [to_dict(d, extra={"site_name": d.site.name if d.site else "Бүх зогсоол"})
            for d in query.limit(500).all()]


@router.post("/drivers")
def create_driver(body: dict, db: Session = Depends(get_db), user: User = Depends(require("drivers"))):
    d = RegisteredDriver(
        plate_number=body["plate_number"].upper().replace(" ", ""),
        full_name=body.get("full_name", ""), phone=body.get("phone", ""),
        contract_type=body.get("contract_type", "MONTHLY"),
        site_id=body.get("site_id"), monthly_fee=body.get("monthly_fee", 0),
        valid_from=datetime.fromisoformat(body["valid_from"]) if body.get("valid_from") else datetime.utcnow(),
        valid_to=datetime.fromisoformat(body["valid_to"]),
    )
    db.add(d)
    db.flush()
    _audit(db, user, "CREATE", "driver", d.id, body)
    db.commit()
    return to_dict(d)


@router.put("/drivers/{driver_id}")
def update_driver(driver_id: str, body: dict, db: Session = Depends(get_db),
                  user: User = Depends(require("drivers"))):
    d = db.get(RegisteredDriver, driver_id)
    if not d:
        raise HTTPException(404, "Жолооч олдсонгүй")
    for k in ("full_name", "phone", "contract_type", "site_id", "monthly_fee", "is_active"):
        if k in body:
            setattr(d, k, body[k])
    if body.get("plate_number"):
        d.plate_number = body["plate_number"].upper().replace(" ", "")
    for k in ("valid_from", "valid_to"):
        if body.get(k):
            setattr(d, k, datetime.fromisoformat(body[k]))
    _audit(db, user, "UPDATE", "driver", driver_id, body)
    db.commit()
    return to_dict(d)


# ─────────────────────────── Хар жагсаалт ───────────────────────────
@router.get("/blacklist")
def list_blacklist(db: Session = Depends(get_db), user: User = Depends(require("blacklist", "cashier"))):
    return [to_dict(b) for b in
            db.query(BlacklistEntry).order_by(BlacklistEntry.created_at.desc()).limit(500).all()]


@router.post("/blacklist")
def add_blacklist(body: dict, db: Session = Depends(get_db), user: User = Depends(require("blacklist"))):
    b = BlacklistEntry(plate_number=body["plate_number"].upper().replace(" ", ""),
                       reason=body.get("reason", ""), created_by=user.username)
    db.add(b)
    db.flush()
    _audit(db, user, "CREATE", "blacklist", b.id, body)
    db.commit()
    return to_dict(b)


@router.put("/blacklist/{entry_id}")
def update_blacklist(entry_id: str, body: dict, db: Session = Depends(get_db),
                     user: User = Depends(require("blacklist"))):
    b = db.get(BlacklistEntry, entry_id)
    if not b:
        raise HTTPException(404, "Бичлэг олдсонгүй")
    for k in ("reason", "is_active"):
        if k in body:
            setattr(b, k, body[k])
    _audit(db, user, "UPDATE", "blacklist", entry_id, body)
    db.commit()
    return to_dict(b)


# ─────────────────────────── Хэрэглэгч (SUPER_ADMIN) ───────────────────────────
@router.get("/users")
def list_users(db: Session = Depends(get_db), user: User = Depends(require_role("ADMIN", "SUPER_ADMIN"))):
    return [to_dict(u) for u in db.query(User).order_by(User.created_at).all()]


@router.post("/users")
def create_user(body: dict, db: Session = Depends(get_db), user: User = Depends(require_role("ADMIN", "SUPER_ADMIN"))):
    if db.query(User).filter(User.username == body["username"]).first():
        raise HTTPException(400, "Нэвтрэх нэр давхардаж байна")
    # SUPER_ADMIN-ыг API/UI-аас үүсгэхийг хориглоно (зөвхөн DB-ээр) — аюулгүй байдал
    if body.get("role") not in ("ADMIN", "FINANCE", "OPERATOR"):
        raise HTTPException(400, "role буруу байна (SUPER_ADMIN-ыг зөвхөн DB-ээр үүсгэнэ)")
    u = User(username=body["username"], password_hash=hash_password(body["password"]),
             full_name=body.get("full_name", ""), phone=body.get("phone", ""),
             role=body["role"], site_id=body.get("site_id"))
    db.add(u)
    db.flush()
    _audit(db, user, "CREATE", "user", u.id, {"username": body["username"], "role": body["role"]})
    db.commit()
    return to_dict(u)


@router.put("/users/{user_id}")
def update_user(user_id: str, body: dict, db: Session = Depends(get_db),
                user: User = Depends(require_role("ADMIN", "SUPER_ADMIN"))):
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(404, "Хэрэглэгч олдсонгүй")
    # SUPER_ADMIN руу ахиулах, эсвэл SUPER_ADMIN-ыг API-аар засахыг хориглоно
    if body.get("role") == "SUPER_ADMIN" or u.role == "SUPER_ADMIN":
        raise HTTPException(403, "SUPER_ADMIN хэрэглэгчийг зөвхөн DB-ээр удирдана")
    for k in ("full_name", "phone", "role", "site_id", "is_active"):
        if k in body:
            setattr(u, k, body[k])
    if body.get("password"):
        u.password_hash = hash_password(body["password"])
    _audit(db, user, "UPDATE", "user", user_id, {k: v for k, v in body.items() if k != "password"})
    db.commit()
    return to_dict(u)
