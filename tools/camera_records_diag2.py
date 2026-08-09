#!/usr/bin/env python3
"""Snapshot Records оношилгоо v3 — вэб UI-ийн EUSO модулийг задалж олсон ЖИНХЭНЭ урсгалаар.

222222.txt (chunk 35 + EUSO модуль)-аас олдсон баримтууд:
  * Snapshot Records хуудас = RecordFinder("TrafficSnapEventInfo") + нөхцөл нь
    {Time: ["<>", эхлэл_epoch_UTC, төгсгөл_epoch_UTC]} (өмнө нь StartTime/EndTime
    string явуулж байсан нь БУРУУ формат байсан).
  * Бичлэгүүд нь метадата: Time(UTC epoch), PlateNumber, Event(тоон код: 34=гарц,
    62/63=зогсоол, 201=гараар), SnapSource, PlateColor, VehicleColor, Location...
  * Файл татахдаа вэб UI /RPC2_Loadfile/<зам> ашигладаг (сешн cookie-той GET).
  * Бодит зургийн файлууд SD/flash дээр — mediaFileFind-ээр зам нь олдох ёстой.

Ажиллуулах (камерын web tab-ууд ХААЛТТАЙ үед):
    sudo /root/PARKING/backend/venv/bin/python /root/PARKING/tools/camera_records_diag2.py 10.0.106.10
    # нууц үг нь .env/DB-ээс өөр камерт — browser-т ордог нэр/нууц үгээ өг:
    sudo /root/PARKING/backend/venv/bin/python /root/PARKING/tools/camera_records_diag2.py 10.0.106.10 admin НууцҮг

Гаралтыг БҮТНЭЭР нь хуулж өгнө үү.
"""
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timedelta

os.chdir("/root/PARKING/backend")  # config env_file=".env" нь CWD-д харьцангуй
sys.path.insert(0, "/root/PARKING/backend")
import httpx  # noqa: E402
from app.config import settings  # noqa: E402
from app.services.barrier import DahuaRpc  # noqa: E402


def resolve_creds(ip: str, argv: list) -> tuple:
    """Нэвтрэлт: CLI аргумент → DB-ийн Device.username/password → .env глобал.

    Dahua олон буруу оролдлогод түгждэг тул ГАНЦХАН хамгийн магадлалтай
    хослолыг сонгоно (олныг ээлжлэн оролддоггүй)."""
    if len(argv) >= 4:
        return argv[2], argv[3], "CLI аргумент"
    try:
        from app.database import SessionLocal
        from app.models import Device
        from app.services.device_auth import camera_credentials
        db = SessionLocal()
        try:
            dev = (db.query(Device).filter(Device.ip_address == ip)
                   .filter(Device.status != "deleted").first()
                   if hasattr(Device, "status") else
                   db.query(Device).filter(Device.ip_address == ip).first())
            if dev is not None and (getattr(dev, "username", None) or "").strip():
                u, p = camera_credentials(dev)
                return u, p, f"DB төхөөрөмж «{dev.name}»"
        finally:
            db.close()
    except Exception as e:
        print(f"  (DB лукап бүтсэнгүй: {type(e).__name__}: {str(e)[:60]} — .env рүү унана)")
    return settings.camera_username, settings.camera_password, ".env глобал"

FMT = "%Y-%m-%d %H:%M:%S"

# EUSO модулиас олдсон Event кодууд
EVENT_NAMES = {34: "TrafficTollGate(гарц/хаалт)", 201: "TrafficManualSnap(гараар)",
               62: "SpaceOccupied(зогсоол эзэлсэн)", 63: "SpaceAvailable(зогсоол суларсан)",
               160: "CityMotorParking", 1: "CrossRegionDetection", 6: "WanderDetection"}

MFF_CONDITIONS = [
    {"Channel": 0, "Types": ["jpg"], "Flags": ["Event"], "Events": None},
    {"Channel": 0, "Types": ["jpg"], "Flags": ["*"]},
    {"Channel": 1, "Types": ["jpg"], "Flags": ["Event"], "Events": None},
    {"Channel": 1, "Types": ["jpg"], "Flags": ["*"]},
    {"Channel": 0, "Types": ["jpg"]},
    {"Channel": 0, "Types": ["jpg", "dav"]},
]


async def show_web_caps(ip: str):
    """Query/Search caps-д юу зарлагдсаныг харна (нэвтрэлт шаардахгүй статик файл)."""
    print("--- 0. /web_caps/webCapsConfig ---")
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"http://{ip}/web_caps/webCapsConfig", params={"version": "2.400"})
            print(f"  HTTP {r.status_code}, {len(r.content)}b")
            try:
                caps = r.json()
                for key in ("Query", "Search", "Snap", "Storage", "Record"):
                    if key in caps:
                        print(f"  {key}: {json.dumps(caps[key], ensure_ascii=False)[:400]}")
            except ValueError:
                print(f"  JSON биш; эхлэл: {r.text[:300]!r}")
    except Exception as e:
        print(f"  АЛДАА: {e}")


async def try_snap_records(rpc):
    """Вэб UI-ийн яг хийдэг үйлдэл: RecordFinder + TrafficSnapEventInfo + Time epoch."""
    print("\n--- 1. RecordFinder «TrafficSnapEventInfo» (вэб UI-ийн жинхэнэ формат) ---")
    now = int(time.time())
    start = now - 24 * 3600
    inst = await rpc._call("RecordFinder.factory.create", {"name": "TrafficSnapEventInfo"})
    obj = inst.get("result")
    print(f"  create → {json.dumps(inst, ensure_ascii=False)[:140]}")
    if not obj:
        return
    try:
        cond = {"Time": ["<>", start, now]}
        st = await rpc._call("RecordFinder.startFind", {"condition": cond}, obj=obj)
        print(f"  startFind {json.dumps(cond)} → {json.dumps(st, ensure_ascii=False)[:160]}")
        total = 0
        for i in range(3):
            df = await rpc._call("RecordFinder.doFind", {"count": 16}, obj=obj)
            params = df.get("params") or {}
            infos = params.get("infos") or []
            found = params.get("found")
            total += len(infos)
            print(f"  doFind[{i}] → found={found} infos={len(infos)}")
            if infos:
                for rec in infos[:3]:
                    ev = rec.get("Event")
                    t = rec.get("Time")
                    ts = datetime.utcfromtimestamp(t).strftime(FMT) if isinstance(t, int) else t
                    print(f"    UTC {ts}  Plate={rec.get('PlateNumber')!r}  "
                          f"Event={ev}({EVENT_NAMES.get(ev, '?')})  Source={rec.get('SnapSource')}")
                print(f"  БИЧЛЭГ[0] бүтэн: {json.dumps(infos[0], ensure_ascii=False)[:800]}")
                break
            if not infos:
                break
        print(f"  Нийт уншсан: {total}")
    finally:
        try:
            await rpc._call("RecordFinder.stopFind", obj=obj)
            await rpc._call("RecordFinder.destroy", obj=obj)
        except Exception:
            pass


async def download_variants(rpc, client_plain, ip, file_path):
    """Олдсон FilePath-ийг вэб UI-ийн ашигладаг /RPC2_Loadfile замаар татаж үзнэ."""
    print(f"\n--- Татах туршилт: {file_path} ---")
    user, pwd = rpc.username, rpc.password
    sess = rpc.session_id
    variants = [
        ("/RPC2_Loadfile + сешн cookie",
         f"http://{ip}/RPC2_Loadfile{file_path}",
         {"Cookie": f"WebClientHttpSessionID={sess}; DhWebClientSessionID={sess}"}, None),
        ("/RPC2_Loadfile + x-api-session",
         f"http://{ip}/RPC2_Loadfile{file_path}",
         {"x-api-session": str(sess), "Cookie": f"WebClientHttpSessionID={sess}"}, None),
        ("/RPC_Loadfile + сешн cookie (хуучин firmware)",
         f"http://{ip}/RPC_Loadfile{file_path}",
         {"Cookie": f"WebClientHttpSessionID={sess}; DhWebClientSessionID={sess}"}, None),
        ("CGI loadfile + digest",
         f"http://{ip}/cgi-bin/RPC_Loadfile{file_path}",
         {}, httpx.DigestAuth(user, pwd)),
    ]
    for label, url, headers, auth in variants:
        try:
            r = await client_plain.get(url, headers=headers, auth=auth)
            is_jpeg = r.content[:2] == b"\xff\xd8"
            print(f"  {label}: HTTP {r.status_code}, {len(r.content)}b, JPEG={is_jpeg}")
            if is_jpeg:
                out = f"/tmp/records_diag_{datetime.now().strftime('%H%M%S')}.jpg"
                open(out, "wb").write(r.content)
                print(f"  ✅ АМЖИЛТТАЙ — зураг {out} руу хадгаллаа. Энэ хувилбарыг кодонд ашиглана.")
                return True
            if r.status_code != 200:
                print(f"    body: {r.content[:120]!r}")
        except Exception as e:
            print(f"  {label}: АЛДАА {type(e).__name__}: {str(e)[:80]}")
    return False


async def try_media_find(rpc, client_plain, ip, start, end):
    print("\n--- 2. mediaFileFind (зургийн файлын зам хайх) ---")
    for extra in MFF_CONDITIONS:
        inst = await rpc._call("mediaFileFind.factory.create")
        obj = inst.get("result")
        if not obj:
            print(f"  factory.create бүтсэнгүй: {json.dumps(inst)[:140]}")
            return
        try:
            cond = {"StartTime": start.strftime(FMT), "EndTime": end.strftime(FMT)}
            cond.update({k: v for k, v in extra.items() if v is not None or k == "Events"})
            ff = await rpc._call("mediaFileFind.findFile", {"condition": cond}, obj=obj)
            nf = await rpc._call("mediaFileFind.findNextFile", {"count": 8}, obj=obj)
            params = nf.get("params") or {}
            infos = params.get("infos") or []
            print(f"  {json.dumps(extra, ensure_ascii=False)} → findFile={ff.get('result')} "
                  f"infos={len(infos)}")
            if infos:
                print(f"  ФАЙЛ[0]: {json.dumps(infos[0], ensure_ascii=False)[:600]}")
                fp = infos[0].get("FilePath")
                if fp:
                    await download_variants(rpc, client_plain, ip, fp)
                return
        except Exception as e:
            print(f"  {extra}: АЛДАА {type(e).__name__}: {str(e)[:100]}")
        finally:
            try:
                await rpc._call("mediaFileFind.close", obj=obj)
                await rpc._call("mediaFileFind.destroy", obj=obj)
            except Exception:
                pass


async def main(ip):
    print(f"=== Камер {ip} — Snapshot Records оношилгоо v3.1 ===")
    user, pwd, src = resolve_creds(ip, sys.argv)
    print(f"Нэвтрэлт: {user} ({src})")
    await show_web_caps(ip)
    async with httpx.AsyncClient(timeout=20) as c:
        rpc = DahuaRpc(c, ip, user, pwd)
        try:
            await rpc.login()
        except Exception as e:
            print(f"\n❌ RPC2 login АМЖИЛТГҮЙ: {e}")
            print("Камер олон буруу оролдлогод ТҮГЖДЭГ тул өөр нууц үг таамаглаагүй.")
            print("Browser-т энэ камерын web рүү ордог нэр/нууц үгээ CLI-гээр өгнө үү:")
            print(f"  sudo /root/PARKING/backend/venv/bin/python "
                  f"/root/PARKING/tools/camera_records_diag2.py {ip} admin <НууцҮг>")
            return
        print(f"\nRPC2 login OK (session={rpc.session_id})")
        await try_snap_records(rpc)
        now_local = datetime.utcnow() + timedelta(hours=settings.camera_tz_offset_hours)
        start = now_local - timedelta(hours=24)
        print(f"\nmediaFileFind-ийн муж (камерын цаг): {start.strftime(FMT)} → {now_local.strftime(FMT)}")
        await try_media_find(rpc, c, ip, start, now_local)
        await rpc.logout()
    print("\n=== Дууслаа — гаралтыг БҮТНЭЭР нь хуулж өгнө үү ===")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Хэрэглээ: camera_records_diag2.py <камерын IP>")
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))
