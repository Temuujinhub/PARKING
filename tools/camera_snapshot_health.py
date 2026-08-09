#!/usr/bin/env python3
"""Флот даяар ГАЦСАН камерыг илрүүлж, шаардвал reboot хийх.

Юуны учир (2026-08-10, батлагдсан):
  Рашбулаг (10.0.106.10) snapshot.cgi 0/10, ШУУД (0.0с) HTTP 400 «Bad Request!»
  өгч байсан — event стрим (eventManager.cgi) 200 өгсөөр. easys тусдаа
  хэрэглэгч ч, admin ч адил унасан тул СЕШНИЙ асуудал БИШ байв. Камерыг
  REBOOT хиймэгц snapshot.cgi 10/10, B4 суваг 538KB JPEG өгч эхэлсэн.

  ⇒ Дүгнэлт: камерын веб/зургийн дэд систем ГАЦдаг (өдөржин олон хүсэлт,
  урт ажилласны дараа). Гацсан камерын гарын үсэг: event стрим АМЬД (200)
  атлаа snapshot.cgi ШУУД (<0.2с) 400 буцаана. Энэ тохиолдолд reboot л засна
  (хүлээгээд сэргэхгүй, тохиргоо ч биш).

Энэ хэрэгсэл камер бүрд snapshot.cgi-г хэдэн удаа дуудаж ХОЦРОГДОЛ+статусыг
хэмжиж, event стрим амьд эсэхийг шалгаж, дараах байдлаар АНГИЛНА:
  • ЭРҮҮЛ    — snapshot.cgi JPEG өгч байна
  • ГАЦСАН   — event 200 атлаа snapshot ШУУД 400 (→ REBOOT нэр дэвшигч)
  • ЗАВГҮЙ   — 400/timeout ч ШУУД биш (ачаалал/давхцал — хүлээвэл сэргэж болзошгүй)
  • ХҮРЭХГҮЙ — TCP/HTTP хүрэхгүй (сүлжээ/тэжээл)

    # Зөвхөн ХЭМЖИХ (юу ч reboot хийхгүй):
    sudo /root/PARKING/backend/venv/bin/python /root/PARKING/tools/camera_snapshot_health.py --all
    sudo ... camera_snapshot_health.py 10.0.106.10 10.0.102.10

    # ГАЦСАН гэж тэмдэглэгдсэнийг reboot хийх (хаалт 1-2 мин ажиллахгүй!):
    sudo ... camera_snapshot_health.py --all --reboot-hung        # хуурай — жагсаалт
    sudo ... camera_snapshot_health.py --all --reboot-hung --yes  # бодитоор reboot

Аюулгүй байдал: --yes-гүй бол reboot ХИЙХГҮЙ. Reboot нэг нэгээр, хооронд нь
завсартай явна; хаалганы өмнө машингүй үед л хийнэ (reboot үед хаалт нээгдэхгүй).
"""
import argparse
import asyncio
import os
import sys
import time

BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
sys.path.insert(0, BACKEND)
os.chdir(BACKEND)

import httpx  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import Device  # noqa: E402
from app.services.device_auth import camera_credentials  # noqa: E402

SNAP_URLS = ("cgi-bin/snapshot.cgi", "cgi-bin/snapshot.cgi?channel=1",
             "cgi-bin/snapshot.cgi?channel=0")
INSTANT_400 = 0.2   # секунд — үүнээс хурдан 400 = «үүдэн дээр татгалзсан» = гацсан шинж


async def _snapshot_probe(c, ip, auth, samples=3) -> dict:
    """snapshot.cgi-г хэдэн удаа дуудаж дүнг нэгтгэнэ."""
    ok, bad, lat_bad, jpeg_bytes = 0, 0, [], 0
    good_url = None
    for i in range(samples):
        for path in (SNAP_URLS if good_url is None else (good_url,)):
            t0 = time.monotonic()
            try:
                r = await c.get(f"http://{ip}/{path}", auth=auth,
                                timeout=httpx.Timeout(4, read=15))
                dt = time.monotonic() - t0
                if r.status_code == 200 and r.content[:2] == b"\xff\xd8":
                    ok += 1
                    jpeg_bytes = len(r.content)
                    good_url = path
                    break
                bad += 1
                lat_bad.append(dt)
            except Exception:  # noqa: BLE001
                bad += 1
                lat_bad.append(time.monotonic() - t0)
        if i < samples - 1:
            await asyncio.sleep(0.8)
    return {"ok": ok, "bad": bad, "jpeg_bytes": jpeg_bytes,
            "min_bad_lat": min(lat_bad) if lat_bad else None, "good_url": good_url}


async def _event_alive(c, ip, auth) -> bool | None:
    """eventManager.cgi attach 200 өгч байна уу (камерын үндсэн веб амьд эсэх)."""
    try:
        async with c.stream("GET", f"http://{ip}/cgi-bin/eventManager.cgi"
                                    f"?action=attach&codes=[All]&heartbeat=5",
                            auth=auth, timeout=httpx.Timeout(6, read=4)) as r:
            return r.status_code == 200
    except httpx.ReadTimeout:
        return True   # холбогдсон ч энэ агшинд event гараагүй — веб амьд
    except Exception:  # noqa: BLE001
        return None


async def classify(ip: str, name: str, user: str, pwd: str) -> dict:
    auth = httpx.DigestAuth(user, pwd)
    async with httpx.AsyncClient(timeout=20) as c:
        # Эхлээд TCP/HTTP хүрч байгаа эсэх (snapshot дуудахаас өмнө)
        snap = await _snapshot_probe(c, ip, auth)
        if snap["ok"]:
            verdict = "ЭРҮҮЛ"
        else:
            ev = await _event_alive(c, ip, auth)
            if ev is None and snap["bad"] and snap["min_bad_lat"] is not None \
                    and snap["min_bad_lat"] >= 3:
                verdict = "ХҮРЭХГҮЙ"
            elif ev and snap["min_bad_lat"] is not None and snap["min_bad_lat"] < INSTANT_400:
                verdict = "ГАЦСАН"        # веб амьд + snapshot ШУУД татгалзсан
            elif ev is None:
                verdict = "ХҮРЭХГҮЙ"
            else:
                verdict = "ЗАВГҮЙ"        # 400/удаан ч шууд биш — ачаалал байж болно
    return {"ip": ip, "name": name, "verdict": verdict, **snap}


async def do_reboot(ip: str) -> str | None:
    from app.services.camera_recovery import reboot_camera
    db = SessionLocal()
    try:
        dev = db.query(Device).filter(Device.ip_address == ip).first()
        creds = camera_credentials(dev)
    finally:
        db.close()
    return await reboot_camera(ip, creds)


def _targets(ip_args: list, use_all: bool) -> list:
    db = SessionLocal()
    try:
        if use_all:
            devs = (db.query(Device)
                    .filter(Device.device_type == "camera", Device.status == "active",
                            Device.ip_address.isnot(None), Device.ip_address != "")
                    .all())
            seen, out = set(), []
            for d in devs:
                if d.ip_address in seen:
                    continue
                seen.add(d.ip_address)
                out.append((d.ip_address, d.name, *camera_credentials(d)))
            return out
        out = []
        for ip in ip_args:
            d = db.query(Device).filter(Device.ip_address == ip).first()
            out.append((ip, d.name if d else "?", *camera_credentials(d)))
        return out
    finally:
        db.close()


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ips", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--reboot-hung", action="store_true",
                    help="ГАЦСАН гэж тэмдэглэгдсэн камеруудыг reboot хийх")
    ap.add_argument("--yes", action="store_true",
                    help="reboot-ыг БОДИТООР хийх (эс бөгөөс зөвхөн жагсаана)")
    args = ap.parse_args()

    targets = _targets(args.ips, args.all)
    if not targets:
        print("Камер алга (IP эсвэл --all).")
        return

    icon = {"ЭРҮҮЛ": "✓", "ГАЦСАН": "⛔", "ЗАВГҮЙ": "~", "ХҮРЭХГҮЙ": "✗"}
    results = []
    print(f"{'IP':16} {'Төлөв':9} {'snapshot':10} {'хамгийн хурдан 400':18} Нэр")
    for ip, name, u, p in targets:
        r = await classify(ip, name, u, p)
        results.append(r)
        snap = (f"{r['jpeg_bytes'] // 1024}KB" if r["ok"]
                else f"{r['ok']}/{r['ok'] + r['bad']}")
        lat = (f"{r['min_bad_lat']:.2f}с" if r["min_bad_lat"] is not None else "-")
        print(f"{ip:16} {icon.get(r['verdict'], '?')} {r['verdict']:7} "
              f"{snap:10} {lat:18} {name}")
        await asyncio.sleep(1)   # камеруудыг зэрэг цохихгүй

    hung = [r for r in results if r["verdict"] == "ГАЦСАН"]
    busy = [r for r in results if r["verdict"] == "ЗАВГҮЙ"]
    healthy = [r for r in results if r["verdict"] == "ЭРҮҮЛ"]
    print(f"\nДүн: {len(healthy)} эрүүл · {len(hung)} гацсан · {len(busy)} завгүй · "
          f"{len(results) - len(healthy) - len(hung) - len(busy)} хүрэхгүй")

    if busy:
        print("\n~ ЗАВГҮЙ камерууд: ачаалал/давхцлаас байж болно — эхлээд 10 мин")
        print("  хүлээгээд дахин шалга (reboot хийхээсээ өмнө):")
        for r in busy:
            print(f"    {r['ip']}  {r['name']}")

    if not hung:
        print("\n✅ Гацсан камер алга.")
        return

    print("\n⛔ ГАЦСАН камерууд (event амьд ч snapshot шууд 400) — REBOOT засна:")
    for r in hung:
        print(f"    {r['ip']}  {r['name']}")
    if not args.reboot_hung:
        print("\n  Reboot хийхийг хүсвэл: --reboot-hung нэмнэ "
              "(эхлээд хуурай жагсаана, дараа нь --yes).")
        return
    if not args.yes:
        print("\n  ⚠ ХУУРАЙ — эдгээрийг reboot хийх БОЛОВЧ --yes өгөөгүй тул хийхгүй.")
        print("  Reboot үед хаалт 1-2 мин ажиллахгүй. Хаалганы өмнө машингүй үед")
        print("  --yes нэмж ажиллуулна уу.")
        return

    print("\n  Reboot эхэлж байна (нэг нэгээр, завсартай)...")
    for r in hung:
        err = await do_reboot(r["ip"])
        if err:
            print(f"    ✗ {r['ip']} reboot амжилтгүй: {err}")
            print("      (бүрэн гацсан бол тэжээлийг нь салгаж залгах л арга)")
        else:
            print(f"    ✓ {r['ip']} reboot хүлээн авлаа — 1-2 мин дараа сэргэнэ")
        await asyncio.sleep(5)
    print("\n  Сэргэсний дараа дахин шалгаж баталгаажуул: "
          "camera_snapshot_health.py <IP>")


if __name__ == "__main__":
    asyncio.run(main())
