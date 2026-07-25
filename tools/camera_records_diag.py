#!/usr/bin/env python3
"""Камерын 'Snapshot Records' (ANPR зургийн бүртгэл)-ийг RPC2-оор татаж болох эсэхийг судлах.

Камерын web UI-ийн Search → Snapshot Records хуудас бүх зургийг харуулдаг тул
камер зургаа хадгалдаг нь тодорхой. Энэ хэрэгсэл нь тухайн бүртгэлийг RPC2-оор
(RecordFinder / mediaFileFind) татаж авах API-г олохыг оролдоно — амжилттай бол
"Камераас татах" (backfill) нь хуучин машинуудын зургийг ч нөхөж чадна.

Ажиллуулах (камерын web tab-ууд ХААЛТТАЙ үед — холболт чөлөөтэй байх ёстой):
    sudo /root/PARKING/backend/venv/bin/python /root/PARKING/tools/camera_records_diag.py 10.0.113.11

Гаралтыг бүтнээр нь хуулж өгнө үү — record-ийн бүтэц (талбарын нэр) дээр үндэслэн
backfill-ийн query-г тааруулна.
"""
import asyncio
import json
import sys
from datetime import datetime, timedelta

sys.path.insert(0, "/root/PARKING/backend")
import httpx  # noqa: E402
from app.config import settings  # noqa: E402
from app.services.barrier import DahuaRpc  # noqa: E402

FMT = "%Y-%m-%d %H:%M:%S"
# Firmware бүр traffic snapshot record-оо өөр нэрлэдэг — боломжит нэрсийг дараалан үзнэ
RECORD_NAMES = ["TrafficSnapEventInfo", "TrafficSnapEvent", "SnapEvent",
                "TrafficRedList", "TrafficGateEvent", "TrafficCarPassEvent"]


async def try_record_finder(rpc, name, start, end):
    inst = await rpc._call("RecordFinder.factory.create", {"name": name})
    obj = inst.get("result")
    print(f"\n── RecordFinder «{name}»")
    print(f"   create → {json.dumps(inst, ensure_ascii=False)[:140]}")
    if not obj:
        return
    try:
        cond = {"StartTime": start.strftime(FMT), "EndTime": end.strftime(FMT)}
        st = await rpc._call("RecordFinder.startFind", {"condition": cond}, obj=obj)
        print(f"   startFind → {json.dumps(st, ensure_ascii=False)[:180]}")
        got = 0
        for _ in range(3):
            df = await rpc._call("RecordFinder.doFind", {"count": 10}, obj=obj)
            params = df.get("params") or {}
            infos = params.get("infos") or []
            if not infos:
                if got == 0:
                    print(f"   doFind → found={params.get('found')} infos=0  ({json.dumps(df)[:120]})")
                break
            got += len(infos)
            print(f"   doFind → {len(infos)} бичлэг (нийт found={params.get('found')})")
            print(f"   БИЧЛЭГ[0] бүтэц: {json.dumps(infos[0], ensure_ascii=False)[:700]}")
            break
    finally:
        try:
            await rpc._call("RecordFinder.stopFind", obj=obj)
            await rpc._call("RecordFinder.destroy", obj=obj)
        except Exception:
            pass


async def try_media_find(rpc, start, end):
    inst = await rpc._call("mediaFileFind.factory.create")
    obj = inst.get("result")
    print(f"\n── mediaFileFind (RPC2)")
    print(f"   create → {json.dumps(inst, ensure_ascii=False)[:140]}")
    if not obj:
        return
    try:
        for extra in ({"Channel": 0, "Types": ["jpg"]}, {"Channel": 1, "Types": ["jpg"]},
                      {"Channel": 0}):
            cond = {"StartTime": start.strftime(FMT), "EndTime": end.strftime(FMT), **extra}
            ff = await rpc._call("mediaFileFind.findFile", {"condition": cond}, obj=obj)
            nf = await rpc._call("mediaFileFind.findNextFile", {"count": 10}, obj=obj)
            params = nf.get("params") or {}
            infos = params.get("infos") or []
            print(f"   {extra} → findFile.result={ff.get('result')} infos={len(infos)}")
            if infos:
                print(f"   ФАЙЛ[0]: {json.dumps(infos[0], ensure_ascii=False)[:500]}")
                break
    finally:
        try:
            await rpc._call("mediaFileFind.close", obj=obj)
            await rpc._call("mediaFileFind.destroy", obj=obj)
        except Exception:
            pass


async def main(ip):
    print(f"=== Камер {ip} — Snapshot Records RPC2 судалгаа ===")
    async with httpx.AsyncClient(timeout=20) as c:
        rpc = DahuaRpc(c, ip, settings.camera_username, settings.camera_password)
        await rpc.login()
        print(f"RPC2 login OK (session={rpc.session_id})")
        # Камерын ЛОКАЛ цагаар сүүлийн 24 цаг
        now_local = datetime.utcnow() + timedelta(hours=settings.camera_tz_offset_hours)
        start = now_local - timedelta(hours=24)
        print(f"Хайх муж (камерын цаг): {start.strftime(FMT)} → {now_local.strftime(FMT)}")
        for name in RECORD_NAMES:
            try:
                await try_record_finder(rpc, name, start, now_local)
            except Exception as e:
                print(f"   ERROR {name}: {type(e).__name__}: {str(e)[:80]}")
        try:
            await try_media_find(rpc, start, now_local)
        except Exception as e:
            print(f"   mediaFileFind ERROR: {type(e).__name__}: {str(e)[:80]}")
        await rpc.logout()
    print("\n=== Дууслаа — гаралтыг бүтнээр нь хуулж өгнө үү ===")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Хэрэглээ: camera_records_diag.py <камерын IP>")
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))
