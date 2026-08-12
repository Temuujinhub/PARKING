#!/usr/bin/env python
"""Камерын нэвтрэх нэр/нууц үгийг ФЛОТ ДАЯАР солих (сонгосон түрээслэгчийг алгасаж).

    cd /root/PARKING/backend
    # 1) Юу болохыг ХАРАХ (юу ч өөрчлөхгүй) + шинэ бүртгэлээр нэвтрэлт шалгах
    read -rs NEW_CAM_PASS && export NEW_CAM_PASS
    venv/bin/python ../tools/set_camera_creds.py --user sysadmin --exclude-tenant Моннис

    # 2) Бүх камер ✅ болсны дараа Л бичих
    venv/bin/python ../tools/set_camera_creds.py --user sysadmin --exclude-tenant Моннис --apply

Нууц үгийг ЗӨВХӨН NEW_CAM_PASS орчны хувьсагчаас (эсвэл асуулгаас) авна —
argv-д бичвэл `ps`, shell history, лог руу ил гарна.

ЮУ ХИЙДЭГ:
  1. Зорилтот камер бүрт ШИНЭ бүртгэлээр RPC2 нэвтрэлт шалгана. Нэвтэрч чадаагүй
     камерыг ХӨНДӨХГҮЙ — буруу нэвтрэлт DB-д бичигдвэл тэр зогсоолын хаалт бүхэлдээ
     унана (Dahua олон буруу оролдлогын дараа бүртгэлийг ТҮГЖИНЭ).
  2. АЛГАСАХ түрээсчлэгчийн (--exclude-tenant) камеруудад ОДОО үйлчилж буй
     нэвтрэлтийг Device мөрөнд нь БЭХЛЭНЭ (pin). Учир нь тэдгээр камер ихэвчлэн
     .env-ийн глобал утгаар ажилладаг — глобалыг солиход тэд ЧИМЭЭГҮЙ унана.
  3. Зорилтот камеруудын Device.username/password-ыг шинэ утгаар бичнэ
     (нууц үг PARKING_SECRET_ENC_KEY-ээр шифрлэгдэнэ).
  4. --env өгвөл .env-ийн CAMERA_USERNAME/CAMERA_PASSWORD-ыг шинэчилнэ
     (backup үлдээнэ). Үүний дараа backend-ээ restart хийнэ.

Нууц үгийг хэзээ ч дэлгэц/лог/аудитад хэвлэхгүй.
"""
import argparse
import asyncio
import os
import shutil
import sys
from datetime import datetime

BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
sys.path.insert(0, BACKEND)
os.chdir(BACKEND)

import httpx  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models import AuditLog, Device, ParkingSite, Tenant  # noqa: E402
from app.secretbox import encrypt_secret  # noqa: E402
from app.services.barrier import DahuaRpc  # noqa: E402
from app.services.device_auth import camera_credentials  # noqa: E402

NET_TYPES = ("camera", "barrier")  # IP-тэй, RPC2-оор нэвтэрдэг төхөөрөмжүүд


async def can_login(host: str, user: str, pwd: str, timeout: float = 8.0) -> tuple[bool, str]:
    """Шинэ бүртгэлээр RPC2 нэвтрэлт шалгана. (амжилт, тайлбар)."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            rpc = DahuaRpc(client, host, user, pwd)
            await rpc.login()
            await rpc.logout()
        return True, "нэвтрэв"
    except Exception as e:  # noqa: BLE001 — ямар ч алдааг тайлбар болгож харуулна
        return False, f"{type(e).__name__}: {str(e)[:90]}"


def tenant_name_of(db, device: Device) -> str:
    site = db.get(ParkingSite, device.site_id) if device.site_id else None
    if site is None or not site.tenant_id:
        return ""
    t = db.get(Tenant, site.tenant_id)
    return t.name if t else ""


def main() -> int:
    ap = argparse.ArgumentParser(description="Камерын нэвтрэлтийг флот даяар солих")
    ap.add_argument("--user", required=True, help="Шинэ нэвтрэх нэр (ж: sysadmin)")
    ap.add_argument("--exclude-tenant", action="append", default=[],
                    help="Хөндөхгүй түрээслэгчийн нэр (хэсэгчилсэн таарал, олон удаа өгч болно)")
    ap.add_argument("--site", action="append", default=[],
                    help="Зөвхөн эдгээр зогсоолын кодод (өгөхгүй бол бүгд)")
    ap.add_argument("--apply", action="store_true", help="DB-д БИЧНЭ (эс бол зөвхөн харуулна)")
    ap.add_argument("--env", metavar="PATH", nargs="?", const="/root/PARKING/backend/.env",
                    help="Мөн .env-ийн CAMERA_USERNAME/PASSWORD-ыг шинэчилнэ")
    ap.add_argument("--skip-verify", action="store_true",
                    help="Нэвтрэлт шалгахгүй (АЮУЛТАЙ — сүлжээнд хүрэхгүй үед л)")
    args = ap.parse_args()

    new_pass = os.environ.get("NEW_CAM_PASS")
    if not new_pass:
        import getpass
        new_pass = getpass.getpass("Шинэ камерын нууц үг: ")
    if not new_pass:
        print("Нууц үг хоосон — зогслоо.", file=sys.stderr)
        return 1

    db = SessionLocal()
    try:
        q = db.query(Device).filter(Device.device_type.in_(NET_TYPES),
                                    Device.status != "deleted")
        devices = [d for d in q.all() if (d.ip_address or "").strip()]
        if args.site:
            codes = {c.upper() for c in args.site}
            ids = {s.id for s in db.query(ParkingSite)
                   .filter(ParkingSite.site_code.in_(codes)).all()}
            devices = [d for d in devices if d.site_id in ids]

        targets, excluded = [], []
        for d in devices:
            tn = tenant_name_of(db, d)
            if any(x.lower() in tn.lower() for x in args.exclude_tenant if x):
                excluded.append((d, tn))
            else:
                targets.append((d, tn))

        print(f"Нийт IP-тэй төхөөрөмж: {len(devices)}  ·  солих: {len(targets)}  ·  "
              f"алгасах: {len(excluded)}")
        if excluded:
            print("\n── АЛГАСАХ (нэвтрэлтийг нь мөрөнд бэхэлнэ) ──")
            for d, tn in excluded:
                pinned = "мөрөндөө бий" if (d.username or "").strip() else "ГЛОБАЛ .env-ээс"
                print(f"   {d.ip_address:16} {d.name or d.device_type:24} [{tn}]  нэвтрэлт: {pinned}")

        # ── Шинэ бүртгэлээр нэвтрэлт шалгах (нэг нэгээр — камерын сесс ховор) ──
        ok, bad = [], []
        if args.skip_verify:
            ok = targets
            print("\n⚠ --skip-verify: нэвтрэлт шалгаагүй.")
        else:
            print(f"\n── Шинэ бүртгэл «{args.user}»-аар нэвтрэлт шалгаж байна ──")
            for d, tn in targets:
                good, why = asyncio.run(can_login(d.ip_address, args.user, new_pass))
                print(f"   {'✅' if good else '❌'} {d.ip_address:16} "
                      f"{d.name or d.device_type:24} {'' if good else why}")
                (ok if good else bad).append((d, tn))

        if bad:
            print(f"\n⚠ {len(bad)} төхөөрөмжид шинэ бүртгэлээр нэвтэрч ЧАДСАНГҮЙ — "
                  "тэдгээрийг ХӨНДӨХГҮЙ.")
            print("   Тэр камерууд дээр «%s» хэрэглэгч үүсээгүй эсвэл нууц үг өөр байна."
                  % args.user)

        if not args.apply:
            print(f"\n(dry-run) Бичих байсан: {len(ok)} төхөөрөмж. "
                  "Бичихийн тулд --apply нэмнэ үү.")
            return 0
        if not ok:
            print("\nБичих төхөөрөмж алга — зогслоо.")
            return 1

        # ── 1. Алгасах түрээслэгчийн нэвтрэлтийг мөрөнд БЭХЛЭХ ──
        pinned = 0
        for d, tn in excluded:
            if (d.username or "").strip():
                continue  # аль хэдийн өөрийн нэвтрэлттэй
            u, p = camera_credentials(d)  # одоо үйлчилж буй (глобал) утга
            d.username, d.password = u, encrypt_secret(p)
            pinned += 1
        if pinned:
            print(f"\n{pinned} алгасах төхөөрөмжийн ОДООГИЙН нэвтрэлтийг мөрөнд бэхлэв "
                  "(глобал солигдоход тэд хөндөгдөхгүй).")

        # ── 2. Зорилтот төхөөрөмжүүдэд шинэ нэвтрэлт ──
        enc = encrypt_secret(new_pass)
        for d, tn in ok:
            d.username, d.password = args.user, enc
        db.add(AuditLog(username="set_camera_creds", action="DEVICE_CREDS_ROTATE",
                        entity="device", entity_id="-",
                        detail={"user": args.user, "changed": len(ok),
                                "pinned": pinned, "skipped_failed": len(bad),
                                "excluded_tenants": args.exclude_tenant}))
        db.commit()
        print(f"✅ {len(ok)} төхөөрөмжийн нэвтрэлт шинэчлэгдэв.")

        # ── 3. .env-ийн глобал default ──
        if args.env:
            update_env(args.env, args.user, new_pass)
            print("   Backend-ээ дахин асаана уу:  systemctl restart parking-backend")
        else:
            print("   (.env хөндөөгүй — глобал default солих бол --env нэмнэ үү)")
        return 0
    finally:
        db.close()


def update_env(path: str, user: str, pwd: str) -> None:
    """PARKING_CAMERA_USERNAME/PASSWORD-ыг .env дотор солино (backup үлдээнэ).
    Тохиргооны env угтвар нь PARKING_ (config.Settings.env_prefix)."""
    if not os.path.exists(path):
        print(f"⚠ .env олдсонгүй: {path}")
        return
    bak = f"{path}.bak-{datetime.now():%Y%m%d-%H%M%S}"
    shutil.copy2(path, bak)
    os.chmod(bak, 0o600)
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    # BARRIER_* -ыг ч хамт: Dahua ANPR кит дээр хаалт нь камерын релеэр ажилладаг
    # тул нэвтрэлт ижил. Хуучин admin үлдээвэл мөрийн нэвтрэлтгүй (шинээр нэмсэн)
    # хаалт глобал руу унаж ажиллахаа болино.
    want = {"PARKING_CAMERA_USERNAME": user, "PARKING_CAMERA_PASSWORD": pwd,
            "PARKING_BARRIER_USERNAME": user, "PARKING_BARRIER_PASSWORD": pwd}
    seen = set()
    out = []
    for ln in lines:
        key = ln.split("=", 1)[0].strip() if "=" in ln and not ln.lstrip().startswith("#") else None
        if key in want:
            out.append(f"{key}={want[key]}")
            seen.add(key)
        else:
            out.append(ln)
    for k, v in want.items():
        if k not in seen:
            out.append(f"{k}={v}")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    os.chmod(path, 0o600)
    print(f"✅ .env шинэчлэгдэв (нөөц: {bak})")


if __name__ == "__main__":
    sys.exit(main())
