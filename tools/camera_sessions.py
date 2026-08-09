#!/usr/bin/env python3
"""Камерт ХЭН нэвтэрсэн байгааг харуулж, сешн эзэлж буй эх үүсвэрийг илрүүлэх.

2026-08-10-ны олдвор (камерын веб UI-ийн DevTools-оос):
    userManager.getActiveUserInfoAll →
      172.16.100.20  CGI  admin  05:55:35
      172.16.100.20  CGI  admin  05:56:05     ← 30 секунд тутам
      ... 9 сешн, нэг нь ч ХААГДААГҮЙ
      172.16.100.254 Web3.0 admin 06:01:30

Гуравдагч систем (172.16.100.20) 30 секунд тутам `admin`-аар нэвтэрч, сешнээ
ХААДАГГҮЙ. Dahua-гийн зэрэгцээ сешний хязгаар дүүрэхэд бүх шинэ хүсэлт
«Bad Request» (HTTP 400) авдаг — манай зураг татах/хаалт нээх ч үүнд өртөнө.

Энэ хэрэгсэл камер бүрийн ИДЭВХТЭЙ сешнийг жагсааж, хаяг тус бүрээр тоолж,
хамгийн хуучин сешний наснаас «алдагдал» (leak) байгааг илрүүлнэ. Гаралтыг
гуравдагч талд БАРИМТ болгон үзүүлж болно.

    sudo /root/PARKING/backend/venv/bin/python /root/PARKING/tools/camera_sessions.py --all
    sudo ... camera_sessions.py 10.0.106.10 --watch 60   # 60с ажиглаж өсөлтийг харах
"""
import argparse
import asyncio
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime

BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
sys.path.insert(0, BACKEND)
os.chdir(BACKEND)

import httpx  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import Device  # noqa: E402
from app.services.barrier import DahuaRpc  # noqa: E402
from app.services.device_auth import camera_credentials  # noqa: E402

OUR_HINT = ("172.16.100.21",)   # манай сервер


async def sessions_of(ip: str, user: str, pwd: str) -> list | None:
    async with httpx.AsyncClient(timeout=15) as c:
        rpc = DahuaRpc(c, ip, user, pwd)
        try:
            await rpc.login()
        except Exception as e:  # noqa: BLE001
            print(f"  ✗ нэвтэрч чадсангүй: {str(e)[:120]}")
            return None
        try:
            r = await rpc._call("userManager.getActiveUserInfoAll")
            return (r.get("params") or {}).get("users") or []
        finally:
            try:
                await rpc.logout()
            except Exception:  # noqa: BLE001
                pass


def report(ip: str, users: list) -> int:
    """Хэвлээд «гадны алдагдсан сешн»-ий тоог буцаана."""
    if users is None:
        return 0
    by_addr = Counter()
    oldest = {}
    for u in users:
        a = u.get("ClientAddress", "?")
        by_addr[a] += 1
        lt = u.get("LoginTime", "")
        if a not in oldest or lt < oldest[a]:
            oldest[a] = lt
    print(f"  Идэвхтэй сешн: {len(users)}")
    leaked = 0
    for addr, n in by_addr.most_common():
        who = "МАНАЙХ" if addr in OUR_HINT else "гадны"
        types = {u.get("ClientType") for u in users if u.get("ClientAddress") == addr}
        names = {u.get("Name") for u in users if u.get("ClientAddress") == addr}
        flag = ""
        if n >= 3:
            flag = "  ⚠ ОЛОН СЕШН — хаагдахгүй байна (leak)"
            if addr not in OUR_HINT:
                leaked += n
        print(f"    {addr:16} {n:>3} сешн · {','.join(sorted(types)):8} · "
              f"{','.join(sorted(str(x) for x in names)):12} · хамгийн эрт "
              f"{oldest.get(addr, '?')}{flag}")
    return leaked


async def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ip", nargs="?")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--watch", type=int, default=0,
                    help="N секунд ажиглаж, сешний өсөлтийг харуулах")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        if args.all:
            devs = (db.query(Device)
                    .filter(Device.device_type == "camera", Device.status == "active",
                            Device.ip_address != "").all())
            seen, targets = set(), []
            for d in devs:
                if d.ip_address in seen:
                    continue
                seen.add(d.ip_address)
                targets.append((d.ip_address, d.name, *camera_credentials(d)))
        elif args.ip:
            d = db.query(Device).filter(Device.ip_address == args.ip).first()
            targets = [(args.ip, d.name if d else "?", *camera_credentials(d))]
        else:
            print("IP эсвэл --all шаардлагатай")
            sys.exit(1)
    finally:
        db.close()

    total_leak = 0
    worst = []
    for ip, name, u, p in targets:
        print(f"\n═══ {ip} ({name}) ═══")
        users = await sessions_of(ip, u, p)
        n = report(ip, users)
        total_leak += n
        if users:
            worst.append((len(users), ip, name))

    if args.watch and targets:
        ip, name, u, p = targets[0]
        print(f"\n── {ip} · {args.watch}с ажиглалт ──")
        first = await sessions_of(ip, u, p)
        n0 = len(first or [])
        await asyncio.sleep(args.watch)
        second = await sessions_of(ip, u, p)
        n1 = len(second or [])
        print(f"  {n0} → {n1} сешн ({args.watch}с дотор {n1 - n0:+d})")
        if n1 > n0:
            rate = (n1 - n0) / args.watch * 60
            print(f"  ⚠ Минутад ~{rate:.1f} сешн НЭМЭГДЭЖ байна — хаагдахгүй байгаагийн")
            print("    шинж. Хязгаар дүүрэхэд бүх хүсэлт «Bad Request» авна.")

    if len(targets) > 1:
        print("\n── Хураангуй ──")
        for n, ip, name in sorted(worst, reverse=True)[:10]:
            print(f"  {ip:16} {n:>3} идэвхтэй сешн  [{name}]")
    if total_leak:
        print(f"\n⚠ ГАДНЫ систем нийт {total_leak} сешн эзэлж байна (хаагдаагүй).")
        print("  Энэ бол манай хүсэлт «Bad Request» авдаг ШУУД шалтгаан.")
        print("  Гуравдагч талд үзүүлэх баримт: дээрх хаяг/цагийн жагсаалт.")
        print("  Шийдэл: (1) тэд сешнээ logout хийдэг болох, эсвэл")
        print("          (2) манай систем ТУСДАА хэрэглэгчтэй болох.")


if __name__ == "__main__":
    asyncio.run(main())
