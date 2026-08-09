#!/usr/bin/env python3
"""Камерын ЗУРАГ ИЛГЭЭХ тохиргоог RPC2-оор алсаас шалгах/асаах.

2026-08-10-ны олдвор: камерууд зургаа манай сервер (172.16.100.21) рүү SFTP-ээр
илгээхээр аль хэдийн тохируулагдсан байсан (Picture → Storage → Server1,
port 22, хэрэглэгч admin). Гэвч манай серверт тэр хэрэглэгч байхгүй тул
илгээлт бүтдэггүй бөгөөд зураг зөвхөн snapshot.cgi-гээр л (амжилт ~55%)
авагдаж байна.

Энэ хэрэгсэл RPC2-оор дараах тохиргоог УНШИНА (default) эсвэл өөрчилнө:
  • NAS            — FTP/SFTP сервер, порт, хэрэглэгч, идэвхтэй эсэх
  • TrafficSnapshot / AlarmSnapShot — аль event-д ямар зураг илгээх
  • Snap           — зургийн чанар/нягтрал

    # ЗӨВХӨН ХАРАХ (юу ч өөрчлөхгүй):
    sudo /root/PARKING/backend/venv/bin/python /root/PARKING/tools/camera_picture_setup.py 10.0.103.10
    # бүх камерыг нэг дор:
    sudo ... camera_picture_setup.py --all
    # түүхий JSON бүтнээр (тохиргооны бүтцийг судлах):
    sudo ... camera_picture_setup.py 10.0.103.10 --raw
    # SFTP серверийг өөрчлөх (ЗӨВХӨН --apply өгвөл бичнэ):
    sudo ... camera_picture_setup.py 10.0.103.10 --set-host 172.16.100.21 \
        --set-user parkingpics --set-password '***' --set-port 22 --apply
"""
import argparse
import asyncio
import json
import os
import sys

os.chdir("/root/PARKING/backend")
sys.path.insert(0, "/root/PARKING/backend")

import httpx  # noqa: E402
from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import Device  # noqa: E402
from app.services.barrier import DahuaRpc  # noqa: E402
from app.services.device_auth import camera_credentials  # noqa: E402

# Уншиж үзэх тохиргооны нэрс (firmware бүрд байхгүй байж болно — алдааг зөөлөн барина)
CONFIGS = ["NAS", "TrafficSnapshot", "AlarmSnapShot", "TrafficGlobal", "Snap"]


def _brief_nas(cfg) -> list:
    """NAS тохиргооноос хүн уншихад ойлгомжтой хураангуй."""
    out = []
    items = cfg if isinstance(cfg, list) else [cfg]
    for i, n in enumerate(items):
        if not isinstance(n, dict):
            continue
        proto = n.get("Protocol") or n.get("ProtocolType") or "?"
        out.append({
            "№": i + 1,
            "Идэвхтэй": bool(n.get("Enable")),
            "Протокол": proto,
            "Хаяг": n.get("Address") or n.get("ServerAddress") or "-",
            "Порт": n.get("Port", "-"),
            "Хэрэглэгч": n.get("UserName") or n.get("User") or "-",
            "Зам": n.get("Directory") or n.get("Path") or "-",
        })
    return out


async def inspect(ip: str, user: str, pwd: str, raw: bool, args) -> None:
    print(f"\n═══ {ip} ═══")
    async with httpx.AsyncClient(timeout=20) as c:
        rpc = DahuaRpc(c, ip, user, pwd)
        try:
            await rpc.login()
        except Exception as e:  # noqa: BLE001
            print(f"  ❌ RPC2 нэвтэрч чадсангүй: {str(e)[:140]}")
            return
        try:
            configs = {}
            for name in CONFIGS:
                try:
                    r = await rpc._call("configManager.getConfig", {"name": name})
                    if r.get("result"):
                        configs[name] = (r.get("params") or {}).get("table")
                    else:
                        err = (r.get("error") or {}).get("message", "")
                        print(f"  {name:16} — байхгүй ({err[:50]})")
                except Exception as e:  # noqa: BLE001
                    print(f"  {name:16} — алдаа {type(e).__name__}")

            if raw:
                print(json.dumps(configs, ensure_ascii=False, indent=2)[:6000])
                return

            nas = configs.get("NAS")
            if nas is not None:
                rows = _brief_nas(nas)
                print("  ── Зураг илгээх сервер (NAS/FTP/SFTP) ──")
                if not rows:
                    print("    (тохируулаагүй)")
                for r in rows:
                    mark = "✓" if r["Идэвхтэй"] else "·"
                    print(f"    {mark} #{r['№']} {r['Протокол']:8} "
                          f"{r['Хаяг']}:{r['Порт']} хэрэглэгч={r['Хэрэглэгч']} "
                          f"зам={r['Зам']}")
            for name in ("TrafficSnapshot", "AlarmSnapShot"):
                cfg = configs.get(name)
                if cfg is None:
                    continue
                s = json.dumps(cfg, ensure_ascii=False)
                print(f"  ── {name} ({len(s)} тэмдэгт) ──")
                for key in ("Enable", "Flags", "Types", "CutoutTypes", "Events",
                            "UploadMode", "ImageSize"):
                    if f'"{key}"' in s:
                        # Утгыг ойролцоогоор гаргаж харуулна (бүтэц firmware бүрд өөр)
                        idx = s.find(f'"{key}"')
                        print(f"    {s[idx:idx + 90]}")

            # ─── Өөрчлөх (заавал --apply) ───
            if not (args.set_host or args.set_user or args.set_password or args.set_port):
                print("\n  (Өөрчлөх бол --set-host/--set-user/--set-password/--set-port + --apply)")
                return
            if nas is None:
                print("  ⚠ NAS тохиргоо уншигдаагүй тул өөрчлөх боломжгүй.")
                return
            items = nas if isinstance(nas, list) else [nas]
            target = items[0] if items else None
            if not isinstance(target, dict):
                print("  ⚠ NAS бүтэц таарсангүй — --raw-аар шалгана уу.")
                return
            before = dict(target)
            if args.set_host:
                for k in ("Address", "ServerAddress"):
                    if k in target:
                        target[k] = args.set_host
            if args.set_port:
                target["Port"] = args.set_port
            if args.set_user:
                for k in ("UserName", "User"):
                    if k in target:
                        target[k] = args.set_user
            if args.set_password:
                target["Password"] = args.set_password
            target["Enable"] = True
            print("\n  ── Өөрчлөлт ──")
            for k in ("Enable", "Protocol", "Address", "ServerAddress", "Port",
                      "UserName", "User", "Directory"):
                if k in target and before.get(k) != target[k]:
                    shown = "***" if k == "Password" else target[k]
                    print(f"    {k}: {before.get(k)} → {shown}")
            if not args.apply:
                print("  DRY-RUN — бодитоор бичихийн тулд --apply нэмнэ.")
                return
            r = await rpc._call("configManager.setConfig", {"name": "NAS", "table": items})
            print(f"  setConfig → {json.dumps(r, ensure_ascii=False)[:200]}")
            if r.get("result"):
                print("  ✅ Хадгалагдлаа. Камер зургаа энэ сервер рүү илгээж эхэлнэ.")
        finally:
            try:
                await rpc.logout()
            except Exception:  # noqa: BLE001
                pass


async def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ip", nargs="?", help="Камерын IP (эсвэл --all)")
    ap.add_argument("--all", action="store_true", help="Бүх идэвхтэй камер")
    ap.add_argument("--raw", action="store_true", help="Түүхий JSON бүтнээр")
    ap.add_argument("--set-host", default=None)
    ap.add_argument("--set-port", type=int, default=None)
    ap.add_argument("--set-user", default=None)
    ap.add_argument("--set-password", default=None)
    ap.add_argument("--user", default=None, help="RPC нэвтрэлт (default: DB/.env)")
    ap.add_argument("--password", default=None)
    ap.add_argument("--apply", action="store_true", help="Бодитоор бичих")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        if args.all:
            devs = (db.query(Device)
                    .filter(Device.device_type == "camera", Device.status == "active",
                            Device.ip_address != "").all())
            targets = [(d.ip_address, *camera_credentials(d)) for d in devs]
        elif args.ip:
            d = db.query(Device).filter(Device.ip_address == args.ip).first()
            u, p = camera_credentials(d)
            targets = [(args.ip, args.user or u, args.password or p)]
        else:
            print("IP эсвэл --all шаардлагатай")
            sys.exit(1)
    finally:
        db.close()

    print(f"Шалгах камер: {len(targets)}")
    for ip, u, p in targets:
        try:
            await inspect(ip, u, p, args.raw, args)
        except Exception as e:  # noqa: BLE001
            print(f"  {ip}: алдаа {type(e).__name__}: {str(e)[:120]}")

    print("\n── Санамж ──")
    print("  Камер зургаа SFTP-ээр илгээх нь ХАМГИЙН ЗӨВ арга: CGI хүсэлт огт")
    print("  шаардахгүй, event subscription булаагдсан ч ажиллана. Үүний тулд")
    print("  манай серверт тухайн хэрэглэгч (ж: parkingpics) байх ёстой.")


if __name__ == "__main__":
    asyncio.run(main())
