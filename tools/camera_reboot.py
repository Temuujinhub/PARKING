#!/usr/bin/env python
"""Камерыг ГАРААР унтрааж асаах (оператор нүдээр баталгаажуулсны дараа).

    sudo /root/PARKING/backend/venv/bin/python /root/PARKING/tools/camera_reboot.py 192.168.6.10 --yes

АНХААР: reboot 60-120 секунд үргэлжилнэ — энэ хугацаанд тухайн хаалт дугаар
танихгүй, хаалт нээгдэхгүй, LED унтарна. Хаалганы өмнө машингүй үед л хийнэ.
Богино (1-3 мин) өөрөө сэргэдэг тасалдалд reboot ХЭРЭГГҮЙ — хүлээхэд л сэргэдэг.
--yes өгөхгүй бол юу ч илгээхгүй, зөвхөн анхааруулга харуулна."""
import asyncio
import os
import sys

BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
sys.path.insert(0, BACKEND)
os.chdir(BACKEND)


def _device_for(ip: str):
    try:
        from app.database import SessionLocal
        from app.models import Device
        db = SessionLocal()
        try:
            return db.query(Device).filter(Device.ip_address == ip).first()
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        return None


async def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--yes"]
    if len(args) != 1:
        print(__doc__)
        return 1
    ip = args[0]
    if "--yes" not in sys.argv:
        print(f"{ip}-г unтрааж асаахад 1-2 минут хаалт бүрэн ажиллахгүй.")
        print("Хаалганы өмнө машингүй гэдгээ нүдээр шалгаад --yes нэмж ажиллуулна уу.")
        return 1
    from app.services.camera_recovery import reboot_camera
    from app.services.device_auth import camera_credentials
    err = await reboot_camera(ip, camera_credentials(_device_for(ip)))
    if err:
        print(f"✗ Reboot амжилтгүй: {err}")
        print("  (камер бүрэн унтарсан бол цахилгааныг нь салгаж залгах л арга үлдэнэ)")
        return 1
    print("✓ Reboot хүлээн авлаа — 1-2 минутад сэргэнэ (стрим өөрөө дахин холбогдоно)")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
