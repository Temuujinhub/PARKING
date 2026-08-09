#!/usr/bin/env python3
"""Камераас ЗУРАГ авах БҮХ сувгийг нэг бүрчлэн туршиж, ЯГ ямар хариу ирснийг харуулах.

Хийсвэрлэлгүй, зөвхөн баримт: суваг тус бүрд HTTP статус, Content-Type,
хариуны эхний байтууд, JPEG хэдэн ширхэг ирснийг тоолж харуулна.

Турших сувгууд:
  A. eventManager.cgi attach          — одоо ашиглаж буй суваг (JSON л ирдэг үү?)
  B. snapManager.cgi attachFileProc   — Dahua-гийн ЗУРГИЙН суваг (олон параметрээр)
  C. snapshot.cgi                     — 2 секунд тутам 10 удаа (бусад системүүд
                                        ингэж ажилладаг гэсэн; амжилтын хувь + алдаа)

    sudo /root/PARKING/backend/venv/bin/python /root/PARKING/tools/picture_channel_test.py 10.0.102.10
    sudo ... picture_channel_test.py 10.0.102.10 --seconds 40   # A/B сувгийг удаан сонсох
"""
import argparse
import asyncio
import os
import re
import sys
import time

os.chdir("/root/PARKING/backend")
sys.path.insert(0, "/root/PARKING/backend")

import httpx  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import Device  # noqa: E402
from app.services.device_auth import camera_credentials  # noqa: E402

SOI = b"\xff\xd8\xff"
EOI = b"\xff\xd9"


def count_jpegs(buf: bytes) -> int:
    n, i = 0, 0
    while True:
        s = buf.find(SOI, i)
        if s < 0:
            break
        e = buf.find(EOI, s)
        if e < 0:
            break
        n += 1
        i = e + 2
    return n


async def listen(client, url, auth, seconds, label):
    """Стримийг N секунд сонсоод JSON/JPEG-ийн тоог гаргана."""
    print(f"\n── {label}")
    print(f"   {url}")
    try:
        async with client.stream("GET", url, auth=auth,
                                 timeout=httpx.Timeout(10, read=seconds + 5)) as r:
            ct = r.headers.get("content-type", "-")
            print(f"   HTTP {r.status_code} · Content-Type: {ct}")
            if r.status_code != 200:
                body = (await r.aread())[:300]
                print(f"   ХАРИУ: {body!r}")
                return 0, 0
            buf = b""
            t0 = time.monotonic()
            async for chunk in r.aiter_bytes():
                buf += chunk
                if time.monotonic() - t0 > seconds:
                    break
                if len(buf) > 40 * 1024 * 1024:
                    break
            jpegs = count_jpegs(buf)
            texts = len(re.findall(rb"Code=|\"Code\"", buf))
            print(f"   {seconds}с сонслоо → {len(buf):,} байт · "
                  f"JSON/event ≈ {texts} · ЗУРАГ (JPEG) = {jpegs}")
            if jpegs:
                print("   ✅ ЭНЭ СУВГААР ЗУРАГ ИРЖ БАЙНА")
            elif texts:
                print("   ⚠ event ирж байна ч ЗУРАГГҮЙ")
            else:
                print("   · юу ч ирсэнгүй (энэ хугацаанд машин өнгөрөөгүй байж болно)")
            return texts, jpegs
    except Exception as e:  # noqa: BLE001
        print(f"   АЛДАА: {type(e).__name__}: {str(e)[:160]}")
        return 0, 0


async def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ip")
    ap.add_argument("--seconds", type=int, default=25, help="Стрим сонсох хугацаа")
    ap.add_argument("--shots", type=int, default=10, help="snapshot.cgi хэдэн удаа")
    ap.add_argument("--gap", type=float, default=2.0, help="snapshot.cgi-ийн зай (сек)")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        dev = db.query(Device).filter(Device.ip_address == args.ip).first()
        user, pwd = camera_credentials(dev)
        name = dev.name if dev else "?"
    finally:
        db.close()
    print(f"=== {args.ip} ({name}) · хэрэглэгч {user} ===")
    auth = httpx.DigestAuth(user, pwd)

    async with httpx.AsyncClient(timeout=20) as c:
        # ── A. Одоо ашиглаж буй суваг ──
        await listen(c, f"http://{args.ip}/cgi-bin/eventManager.cgi"
                        f"?action=attach&codes=[All]&heartbeat=5",
                     auth, args.seconds, "A. eventManager.cgi attach (одоогийн суваг)")

        # ── B. Dahua-гийн ЗУРГИЙН суваг, параметрийн хувилбарууд ──
        variants = [
            ("B1 attachFileProc Flags=Event Events=[All]",
             "snapManager.cgi?action=attachFileProc&Flags[0]=Event&Events=[All]&heartbeat=5"),
            ("B2 attachFileProc + Channel=0",
             "snapManager.cgi?action=attachFileProc&channel=0&Flags[0]=Event&Events=[All]&heartbeat=5"),
            ("B3 attachFileProc Events=[TrafficJunction]",
             "snapManager.cgi?action=attachFileProc&Flags[0]=Event&Events=[TrafficJunction]&heartbeat=5"),
            ("B4 attachFileProc Flags=Event,Manual",
             "snapManager.cgi?action=attachFileProc&Flags[0]=Event&Flags[1]=Manual&Events=[All]&heartbeat=5"),
            ("B5 eventManager attach + pictureenable",
             "eventManager.cgi?action=attach&codes=[All]&heartbeat=5&pictureenable=1"),
        ]
        for label, path in variants:
            texts, jpegs = await listen(c, f"http://{args.ip}/cgi-bin/{path}",
                                        auth, max(8, args.seconds // 3), label)
            if jpegs:
                print(f"\n   ⇒ ОЛДЛОО: «{label}» суваг зураг өгч байна.")
                break

        # ── C. snapshot.cgi-г бусад системүүдийн адилаар давтан дуудах ──
        print(f"\n── C. snapshot.cgi · {args.gap:g}с зайтай {args.shots} удаа ──")
        ok = 0
        errs = {}
        for i in range(1, args.shots + 1):
            t0 = time.monotonic()
            try:
                r = await c.get(f"http://{args.ip}/cgi-bin/snapshot.cgi", auth=auth,
                                timeout=httpx.Timeout(5, read=25))
                dt = time.monotonic() - t0
                if r.status_code == 200 and r.content[:2] == b"\xff\xd8":
                    ok += 1
                    print(f"   {i:>2}. ✓ {len(r.content):>8,}б · {dt:.1f}с")
                else:
                    key = f"HTTP {r.status_code}"
                    errs[key] = errs.get(key, 0) + 1
                    print(f"   {i:>2}. ✗ HTTP {r.status_code} · {dt:.1f}с · "
                          f"{r.content[:120]!r}")
            except Exception as e:  # noqa: BLE001
                dt = time.monotonic() - t0
                key = type(e).__name__
                errs[key] = errs.get(key, 0) + 1
                print(f"   {i:>2}. ✗ {key} · {dt:.1f}с · {str(e)[:90]}")
            if i < args.shots:
                await asyncio.sleep(args.gap)
        print(f"\n   Амжилт: {ok}/{args.shots} ({ok / args.shots * 100:.0f}%)")
        if errs:
            print("   Алдаанууд: " + ", ".join(f"{k}×{v}" for k, v in errs.items()))
            print("   → Хэрэв энд амжилт өндөр байвал асуудал нь ДАВХЦАЛ (event үед")
            print("     хаалт/дэлгэц/зураг зэрэг явж камер завгүй) болно.")


if __name__ == "__main__":
    asyncio.run(main())
