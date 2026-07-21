"""Төхөөрөмжийн авто тохиргоо.

Бүх-нэг-дор Dahua ANPR кит: камер хаалтаа ӨӨРИЙН релеэр (NO1/NO2) удирддаг тул
эгнээ бүр камер + хаалт ХОС байх ёстой. Энэ модуль:

1. ensure_lane_barriers() — идэвхтэй камер бүрд ижил эгнээний идэвхтэй barrier
   байгааг баталгаажуулна: устгагдсан бол СЭРГЭЭНЭ, огт байхгүй бол ҮҮСГЭНЭ.
   Startup бүрт + камер шинээр бүртгэх бүрт ажиллана — админаас нэмэлт ажил шаардахгүй.
2. fetch_camera_model() — камерын марк/загварыг өөрөөс нь (magicBox CGI) татна.
"""
import secrets

import httpx
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Device


def fetch_camera_model(ip: str) -> str | None:
    """Dahua камерын загварыг өөрөөс нь асууна: /cgi-bin/magicBox.cgi?action=getDeviceType
    → "type=IPMECS-2234-IZ". Хүрэхгүй/өөр брэнд бол None (алдаа шидэхгүй)."""
    if not ip:
        return None
    try:
        r = httpx.get(f"http://{ip}/cgi-bin/magicBox.cgi?action=getDeviceType",
                      auth=httpx.DigestAuth(settings.camera_username, settings.camera_password),
                      timeout=4)
        if r.status_code == 200 and "type=" in r.text:
            return r.text.split("type=", 1)[1].strip().splitlines()[0][:80] or None
    except Exception:  # noqa: BLE001 — камер унтарсан ч бүртгэл саадгүй үргэлжилнэ
        pass
    return None


def ensure_lane_barriers(db: Session) -> dict:
    """Идэвхтэй камер бүрд ижил зогсоол+эгнээний идэвхтэй barrier байлгана.
    Буцаана: {"restored": n, "created": n} — лог/мэдээлэлд."""
    restored = created = 0
    cams = db.query(Device).filter(Device.device_type == "camera",
                                   Device.status == "active").all()
    for c in cams:
        active_bar = db.query(Device).filter(
            Device.site_id == c.site_id, Device.device_type == "barrier",
            Device.lane_no == c.lane_no, Device.status == "active").first()
        if active_bar:
            continue
        # 1) Устгагдсан хос байвал сэргээнэ (device_key, тохиргоо хэвээр)
        deleted_bar = (db.query(Device).filter(
            Device.site_id == c.site_id, Device.device_type == "barrier",
            Device.lane_no == c.lane_no, Device.status == "deleted")
            .order_by(Device.created_at.desc()).first())
        if deleted_bar:
            deleted_bar.status = "active"
            restored += 1
            continue
        # 2) Огт байхгүй бол камерынхаа эгнээнд шинээр үүсгэнэ
        name = "Орох хаалт" if c.lane_dir == "entry" else "Гарах хаалт"
        db.add(Device(site_id=c.site_id, name=f"{name} (авто)", device_type="barrier",
                      vendor="Dahua", model="DZBL-A / DZE-BL", ip_address="",
                      lane_no=c.lane_no, lane_dir=c.lane_dir, auto_open=False,
                      device_key=f"barrier-{secrets.token_hex(8)}"))
        created += 1
    if restored or created:
        db.commit()
        print(f"[device_auto] хаалт баталгаажуулалт: {restored} сэргээв, {created} шинээр үүсгэв")
    return {"restored": restored, "created": created}
