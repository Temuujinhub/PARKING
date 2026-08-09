#!/usr/bin/env python
"""Камер дээр МАНАЙ системд зориулсан ТУСДАА хэрэглэгч үүсгэж, DB-д бүртгэнэ.

    # 1. Эхлээд камер дээр ямар хэрэглэгч байгааг ХАРАХ (юу ч өөрчлөхгүй):
    sudo /root/PARKING/backend/venv/bin/python /root/PARKING/tools/camera_add_user.py 192.168.6.10 --list

    # 2. Хэрэглэгч үүсгэж, тухайн төхөөрөмжийн нэвтрэлтийг DB-д шинэчлэх:
    sudo ... camera_add_user.py 192.168.6.10 --create

    # 3. Олон камерт нэг дор:
    sudo ... camera_add_user.py 192.168.6.10 192.168.6.11 --create

    # 4. БҮХ камер (эсвэл зөвхөн admin-аар үлдсэнийг):
    sudo ... camera_add_user.py --all --list
    sudo ... camera_add_user.py --all --only-admin --create

Юуны учир (2026-07-29 аудит): камеруудад гадны системүүд МАНАЙ ашигладаг admin
бүртгэлээр ханддаг нь батлагдсан (admin@172.10.20.55/60, admin@172.16.100.254).
Тэдний нэгэн буруу нэвтрэлт admin-ыг ТҮГЖИХЭД манай хаалт нээх команд ч хамт
унадаг. Тусдаа хэрэглэгчтэй болсноор тэр эрсдэл арилна (мөн камерын логоос хэн
юу хийснийг ялгаж харна).

Аюулгүй байдал: нууц үг CSPRNG-ээр үүсч, DB-д шифрлэгдэн хадгалагдана
(PARKING_SECRET_ENC_KEY тохируулсан бол), дэлгэцэнд НЭГ л удаа харагдана.
Камерын admin бүртгэлд хүрэхгүй — зөвхөн шинэ хэрэглэгч НЭМНЭ.
"""
import asyncio
import os
import secrets
import string
import sys

BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
sys.path.insert(0, BACKEND)
os.chdir(BACKEND)

import httpx  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models import AuditLog, Device  # noqa: E402
from app.secretbox import encrypt_secret  # noqa: E402
from app.services.barrier import DahuaRpc  # noqa: E402
from app.services.device_auth import camera_credentials  # noqa: E402

NEW_USER = "easypark"


def _gen_password() -> str:
    """Dahua-гийн шаардлагад нийцэх хүчтэй нууц үг (том/жижиг/тоо, тусгай тэмдэггүй —
    зарим firmware тусгай тэмдэгтийг татгалздаг)."""
    alpha = string.ascii_lowercase
    upper = string.ascii_uppercase
    digits = string.digits
    pool = alpha + upper + digits
    while True:
        pw = "".join(secrets.choice(pool) for _ in range(14))
        if any(c in alpha for c in pw) and any(c in upper for c in pw) and any(c in digits for c in pw):
            return pw


def _device_for(db, ip: str):
    return db.query(Device).filter(Device.ip_address == ip,
                                   Device.status != "deleted").all()


async def handle(ip: str, create: bool) -> None:
    db = SessionLocal()
    try:
        devices = _device_for(db, ip)
        if not devices:
            print(f"\n═══ {ip} ═══\n  ✗ Энэ IP-тэй төхөөрөмж бүртгэлгүй байна")
            return
        creds = camera_credentials(devices[0])
        print(f"\n═══ {ip} ═══  ({', '.join(d.name for d in devices)})")
        print(f"  Одоогийн нэвтрэлт: {creds[0]!r}")

        async with httpx.AsyncClient(timeout=15.0) as client:
            rpc = DahuaRpc(client, ip, *creds)
            try:
                await rpc.login()
            except Exception as e:  # noqa: BLE001
                print(f"  ✗ Нэвтэрч чадсангүй: {str(e)[:160]}")
                return
            print("  ✓ RPC2 нэвтрэлт амжилттай")
            try:
                # ── Байгаа хэрэглэгчид ──
                existing = []
                res = await rpc._call("userManager.getUserInfoAll")
                for u in (res.get("params") or {}).get("users") or []:
                    existing.append(u.get("Name"))
                    print(f"    · хэрэглэгч: {u.get('Name'):<12} бүлэг={u.get('Group')} "
                          f"тэмдэглэл={str(u.get('Memo'))[:40]}")
                if not existing:
                    print("    (хэрэглэгчийн жагсаалт уншигдсангүй — firmware дэмжихгүй байж болно)")

                if not create:
                    print("  → Үүсгэхийн тулд --create нэмнэ үү")
                    return
                if NEW_USER in existing:
                    print(f"  ! «{NEW_USER}» аль хэдийн байна — нууц үгийг нь ШИНЭЧИЛНЭ")

                pw = _gen_password()
                params = {"user": {
                    "Name": NEW_USER, "Password": pw, "Group": "admin",
                    "Memo": "Easy Parking system", "Sharable": True, "Reserved": False,
                    "AuthorityList": ["Monitor_01", "PlayBack_01", "SystemInfo",
                                      "Event", "Storage", "Record", "Network"],
                }}
                method = "userManager.modifyUser" if NEW_USER in existing else "userManager.addUser"
                if method == "userManager.modifyUser":
                    params = {"name": NEW_USER, "user": params["user"], "pwdModified": True}
                r = await rpc._call(method, params)
                if not r.get("result"):
                    print(f"  ✗ {method} амжилтгүй: {str(r)[:200]}")
                    print("    (камерын веб → Тохиргоо → Данс хэсгээс ГАРААР үүсгээд, "
                          "Тохиргоо → Төхөөрөмж дээр нэвтрэлтийг оруулж болно)")
                    return
                print(f"  ✓ «{NEW_USER}» хэрэглэгч {'шинэчлэгдлээ' if NEW_USER in existing else 'үүслээ'}")
            finally:
                await rpc.logout()

        # ── Шинэ хэрэглэгчээр НЭВТЭРЧ БАТАЛГААЖУУЛНА (DB-д бичихийн өмнө) ──
        await asyncio.sleep(1.5)
        async with httpx.AsyncClient(timeout=15.0) as client:
            test = DahuaRpc(client, ip, NEW_USER, pw)
            try:
                await test.login()
                await test.logout()
                print("  ✓ Шинэ нэвтрэлт баталгаажлаа")
            except Exception as e:  # noqa: BLE001
                print(f"  ✗ Шинэ хэрэглэгчээр нэвтэрч чадсангүй: {str(e)[:160]}")
                print("    DB-д ХАДГАЛСАНГҮЙ — хуучин admin нэвтрэлт хэвээр ажиллана.")
                return

        for d in devices:
            d.username = NEW_USER
            d.password = encrypt_secret(pw)
            db.add(AuditLog(username="system", action="CAMERA_USER_ROTATE", entity="device",
                            entity_id=d.id, detail={"ip": ip, "user": NEW_USER}))
        db.commit()
        print(f"  ✓ DB-д бүртгэв ({len(devices)} төхөөрөмж) — систем шинэ хэрэглэгчээр ажиллана")
        print(f"  НУУЦ ҮГ (нэг л удаа харагдана, аюулгүй газар хадгална уу): {pw}")
    finally:
        db.close()


async def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    create = "--create" in sys.argv
    # --all: DB дэх БҮХ идэвхтэй камер. 22 камерыг гараар жагсаах шаардлагагүй,
    # мөн admin-аар үлдсэнийг л сонгох (--only-admin) боломжтой.
    if "--all" in sys.argv:
        db = SessionLocal()
        try:
            q = (db.query(Device)
                 .filter(Device.device_type == "camera", Device.status == "active",
                         Device.ip_address != ""))
            devs = q.all()
            if "--only-admin" in sys.argv:
                devs = [d for d in devs if camera_credentials(d)[0] == "admin"]
            args = sorted({d.ip_address for d in devs})
        finally:
            db.close()
        print(f"Хамрах камер: {len(args)}")
    if not args or ("--list" not in sys.argv and not create):
        print(__doc__)
        return 1
    for ip in args:
        await handle(ip, create)
    if create:
        print("\nДараа нь: systemctl restart parking-backend  (шинэ нэвтрэлт шууд үйлчилнэ)")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
