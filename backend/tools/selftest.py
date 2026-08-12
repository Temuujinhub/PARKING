"""Флотын нэгдсэн ТЕСТ — камер бүрд 4 шалгалт, нэг командаар.

Гурван асуулт нэг дор:
  1. ЗУРАГ авч байна уу?        — snapshot.cgi JPEG буцааж байна уу
  2. EVENT ирж байна уу?         — eventManager.cgi стримээс машины event ирдэг үү
  3. EVENT дотор ЗУРАГ бий юу?   — тэр стрим JSON-той хамт JPEG авчирдаг уу
  4. ХААЛТ команд хүлээж авдаг уу — RPC2 trafficSnap хариу өгч байна уу

№3 нь яагаад чухал вэ: систем session-ий зургийг ЭХЛЭЭД event стримээс авдаг
(offer_stream_image → _take_stream_image), snapshot.cgi нь зөвхөн fallback.
Event зураггүй ирдэг камер дээр зураг үргэлж хоцорч/дутуу авагдана.

ХААЛТЫН ШАЛГАЛТ — анхдагчаар АЮУЛГҮЙ:
  `trafficSnap.closeStrobe` илгээнэ. Хаалт хэвийн үедээ ХААЛТТАЙ байдаг тул
  машин ГАРАХГҮЙ, гэхдээ командын суваг ажиллаж байгаа нь батлагдана.
  `--open` өгвөл ҮНЭХЭЭР нээгээд шууд хаана — ЗӨВХӨН газар дээрээ байхдаа,
  машин байхгүй үед ашиглана.

Камеруудыг ДАРААЛЖ шалгана (зэрэг биш) — Dahua ITC цөөн холболт л зөвшөөрдөг,
зэрэг цохивол камер хариу өгөхөө болино. Тиймээс 22 камер × --secs 12 ≈ 6-7 минут.

Ажиллуулах:
    cd /root/PARKING/backend
    venv/bin/python tools/selftest.py                  # бүх зогсоол
    venv/bin/python tools/selftest.py --site HANGARID  # нэг зогсоол
    venv/bin/python tools/selftest.py --secs 20        # event илүү удаан сонсох
    venv/bin/python tools/selftest.py --site KH --open # хаалтыг ҮНЭХЭЭР нээж үзэх
"""
import argparse
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx

from app.database import SessionLocal
from app.models import Device, ParkingSite
from app.services.barrier import DahuaRpc, camera_client
from app.services.device_auth import barrier_credentials, camera_credentials

OK, BAD, WARN, SKIP = "✅", "❌", "⚠️ ", "—"


async def check_snapshot(ip: str, creds) -> tuple[str, str]:
    """snapshot.cgi JPEG буцааж байна уу."""
    auth = httpx.DigestAuth(*creds)
    async with httpx.AsyncClient(timeout=httpx.Timeout(5, read=12)) as c:
        for path in ("cgi-bin/snapshot.cgi", "cgi-bin/snapshot.cgi?channel=1"):
            t0 = time.monotonic()
            try:
                r = await c.get(f"http://{ip}/{path}", auth=auth)
            except Exception as e:  # noqa: BLE001
                return BAD, f"{type(e).__name__}"
            dt = time.monotonic() - t0
            if r.status_code == 200 and r.content[:2] == b"\xff\xd8":
                return OK, f"{len(r.content) // 1024}KB {dt:.1f}с"
            last = f"HTTP {r.status_code} {dt:.2f}с"
        # <0.2с 400 = камерын зургийн дэд систем ГАЦСАН (reboot л засдаг)
        return BAD, last + (" (ГАЦСАН)" if r.status_code == 400 and dt < 0.2 else "")


async def check_event_stream(ip: str, creds, secs: int) -> tuple[str, str, str]:
    """Event стримээс JSON event ба JPEG зураг ирж байгааг тоолно.

    Буцаах: (event төлөв, event тайлбар, зургийн төлөв+тайлбар)"""
    auth = httpx.DigestAuth(*creds)
    url = (f"http://{ip}/cgi-bin/eventManager.cgi"
           f"?action=attach&codes=[All]&heartbeat=5")
    buf, events, jpegs = b"", 0, 0
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(6, read=secs + 5)) as c:
            async with c.stream("GET", url, auth=auth) as r:
                if r.status_code != 200:
                    return BAD, f"HTTP {r.status_code}", f"{SKIP} стрим алга"
                deadline = time.monotonic() + secs
                async for chunk in r.aiter_bytes():
                    buf += chunk
                    events += chunk.count(b"Code=")
                    jpegs += chunk.count(b"\xff\xd8\xff")   # JPEG SOI
                    if time.monotonic() > deadline:
                        break
                    if len(buf) > 8 * 1024 * 1024:
                        buf = b""
    except httpx.ReadTimeout:
        pass    # холбогдсон ч энэ хэсэгт event гараагүй — веб амьд
    except Exception as e:  # noqa: BLE001
        return BAD, f"{type(e).__name__}", f"{SKIP} стрим алга"

    if events == 0:
        return WARN, f"{secs}с-д event гараагүй", f"{SKIP} event байхгүй"
    if jpegs:
        return OK, f"{events} event", f"{OK} {jpegs} зураг"
    # Энэ бол чимээгүй алдаа: event ирдэг ч зураггүй → session зураг үргэлж
    # snapshot.cgi-гээс хоцорч авагдана (эсвэл огт авагдахгүй)
    return OK, f"{events} event", f"{BAD} ЗУРАГГҮЙ ирдэг"


async def check_barrier(ip: str, creds, really_open: bool) -> tuple[str, str]:
    """RPC2 trafficSnap команд хүлээж авч байгаа эсэх."""
    try:
        rpc = DahuaRpc(camera_client(ip), ip, *creds)
        await asyncio.wait_for(rpc.login(), timeout=8)
    except Exception as e:  # noqa: BLE001
        return BAD, f"нэвтэрч чадсангүй: {type(e).__name__}"
    try:
        # ХААХ команд нь аюулгүй: хаалт хэвийндээ хаалттай тул машин гарахгүй
        res = await asyncio.wait_for(rpc._call("trafficSnap.closeStrobe"), timeout=8)
        if not res.get("result"):
            return BAD, f"closeStrobe татгалзав: {str(res)[:60]}"
        if not really_open:
            return OK, "closeStrobe хүлээж авав"
        res = await asyncio.wait_for(rpc._call("trafficSnap.openStrobe"), timeout=8)
        if not res.get("result"):
            return BAD, f"openStrobe татгалзав: {str(res)[:60]}"
        await asyncio.sleep(2)
        await asyncio.wait_for(rpc._call("trafficSnap.closeStrobe"), timeout=8)
        return OK, "НЭЭГЭЭД хаалаа"
    except Exception as e:  # noqa: BLE001
        return BAD, f"{type(e).__name__}"
    finally:
        try:
            await asyncio.wait_for(rpc._call("global.logout"), timeout=4)
        except Exception:  # noqa: BLE001
            pass


async def run(args):
    db = SessionLocal()
    try:
        q = (db.query(Device).join(ParkingSite, Device.site_id == ParkingSite.id)
             .filter(Device.device_type == "camera", Device.status == "active",
                     ParkingSite.is_active.is_(True),
                     Device.ip_address.isnot(None), Device.ip_address != ""))
        if args.site:
            q = q.filter(ParkingSite.site_code == args.site)
        cams = q.order_by(ParkingSite.name, Device.lane_dir, Device.name).all()
        targets = [(c.site.name, c.name, c.ip_address, c.lane_dir,
                    camera_credentials(c), barrier_credentials(c))
                   for c in cams]
        # Хаалт нь тусдаа төхөөрөмж байж болно — камерын IP дээр RPC явдаг
        # (Dahua ITC-д хаалт камерын релеэр удирдагддаг)
    finally:
        db.close()

    if not targets:
        print("Камер олдсонгүй.")
        return

    print(f"\n═══ ФЛОТЫН ТЕСТ · {len(targets)} камер · event {args.secs}с сонсоно ═══")
    if args.open:
        print("⚠️  --open: ХААЛТЫГ ҮНЭХЭЭР НЭЭНЭ. Машин байхгүйг баталсан байх ёстой!")
    print()

    cur_site, rows = None, []
    for site, name, ip, lane, ccreds, bcreds in targets:
        if site != cur_site:
            cur_site = site
            print(f"── {site}")
        snap, snap_note = await check_snapshot(ip, ccreds)
        ev, ev_note, img = await check_event_stream(ip, ccreds, args.secs)
        bar, bar_note = await check_barrier(ip, bcreds or ccreds, args.open)
        print(f"   {ip:<16} {(name or '')[:16]:<17} {lane or '?':<6}"
              f"  зураг {snap} {snap_note:<18}"
              f"  event {ev} {ev_note:<16}  {img:<20}  хаалт {bar} {bar_note}")
        rows.append((site, ip, snap, ev, img, bar))

    print("\n─── ДҮГНЭЛТ " + "─" * 60)
    n = len(rows)
    bad_snap = [r for r in rows if r[2] == BAD]
    no_img = [r for r in rows if r[4].startswith(BAD)]
    bad_bar = [r for r in rows if r[5] == BAD]
    quiet = [r for r in rows if r[3] == WARN]
    print(f"  Зураг авдаггүй        {len(bad_snap):>3}/{n}"
          + (f"   {', '.join(r[1] for r in bad_snap[:6])}" if bad_snap else ""))
    print(f"  Event ЗУРАГГҮЙ ирдэг  {len(no_img):>3}/{n}"
          + (f"   {', '.join(r[1] for r in no_img[:6])}" if no_img else ""))
    print(f"  Event огт гараагүй    {len(quiet):>3}/{n}"
          f"   ({args.secs}с богино байж болно — --secs 30 туршина уу)")
    print(f"  Хаалт хүлээж авдаггүй {len(bad_bar):>3}/{n}"
          + (f"   {', '.join(r[1] for r in bad_bar[:6])}" if bad_bar else ""))
    print()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", help="зогсоолын код (жишээ: HANGARID)")
    ap.add_argument("--secs", type=int, default=12, help="event стрим сонсох хугацаа")
    ap.add_argument("--open", action="store_true",
                    help="ХААЛТЫГ ҮНЭХЭЭР нээгээд хаана (зөвхөн газар дээрээ!)")
    asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    main()
