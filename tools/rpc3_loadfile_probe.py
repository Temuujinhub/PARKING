#!/usr/bin/env python3
"""RPC3_Loadfile + FileManager.downloadPiece — вэб UI-ийн ЖИНХЭНЭ зургийн зам.

ОЛДВОР (2026-08-14, камерын вэб UI-ийн JS-ээс, DevTools Sources):

    method: "FileManager.downloadPiece"
    url:    "/RPC3_Loadfile"        (POST, Content-Type: application/json)
    ...
    var t = e.data.split("\\n"), a = "";
    for (n = 5; n <= 13; n++) a += t[n];        // 5-13 мөр = JSON толгой
    a = JSON.parse(a);
    var r = t[19].replace("--myboundary\\r", "");
    a.params.EncryptFileSlice = atob(r);        // 19-р мөр = base64 зүсэм

Мөн event → зургийн замын харгалзаа:
    TrafficJunction   → "urlCarPano"
    TrafficManualSnap → "url"
    (TrafficTollGate нь TrafficJunction РҮҮ буудаг — бидний codes зөв байсан)

Тэгэхээр урсгал нь: event-д зургийн ЗАМ ирнэ → тэр замыг RPC3_Loadfile-аар
хэсэгчлэн татна. `snapshot.cgi` огт хэрэггүй, «Manual Snapshot» бичлэг ч
үүсэхгүй.

Энэ хэрэгсэл:
  1. RPC2-оор нэвтэрч сешн авна
  2. FileManager-ийн боломжит методуудыг пробоор шалгана
  3. RecordFinder-ээс бичлэг авч, доторх ЗАМЫН талбаруудыг хайна
  4. Зам олдвол RPC3_Loadfile-аар татаж, хариуны БҮТЭН бүтцийг хэвлэнэ

Ажиллуулах:
    sudo /root/PARKING/backend/venv/bin/python /root/PARKING/tools/rpc3_loadfile_probe.py 10.0.105.10
"""
import asyncio
import base64
import json
import os
import re
import sys

os.chdir("/root/PARKING/backend")  # config-ийн env_file=".env" нь CWD-д харьцангуй
sys.path.insert(0, "/root/PARKING/backend")

import httpx  # noqa: E402

from app.services.barrier import DahuaRpc  # noqa: E402

# Зам байж болох талбарын нэрс (JS-ээс: urlCarPano, url)
PATH_KEYS = ("urlCarPano", "url", "URL", "Url", "FilePath", "filePath",
             "PicPath", "Path", "FileName", "PicName", "ImageURL")

FILEMANAGER_PROBES = [
    ("FileManager.factory.instance", None),
    ("FileManager.getCaps", None),
    ("FileManager.listFile", {"path": "/"}),
    ("FileManager.getFileList", {"path": "/"}),
]


def creds_for(ip: str):
    try:
        from app.database import SessionLocal
        from app.models import Device
        from app.services.device_auth import camera_credentials
        db = SessionLocal()
        try:
            dev = (db.query(Device)
                   .filter(Device.ip_address == ip, Device.device_type == "camera")
                   .filter(Device.status != "deleted").first())
            if dev is not None:
                return (*camera_credentials(dev), f"DB «{dev.name}»")
        finally:
            db.close()
    except Exception as e:  # noqa: BLE001
        return None, None, f"DB лукап бүтсэнгүй: {type(e).__name__}: {str(e)[:70]}"
    return None, None, "камер DB-д олдсонгүй"


def find_paths(obj, path="", out=None):
    """Бичлэг/event дотроос ЗАМ байж болох талбаруудыг цуглуулна."""
    if out is None:
        out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else k
            if k in PATH_KEYS and isinstance(v, str) and v:
                out.append((p, v))
            find_paths(v, p, out)
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:30]):
            find_paths(v, f"{path}[{i}]", out)
    return out


async def loadfile(c: httpx.AsyncClient, ip: str, session, params: dict):
    """POST /RPC3_Loadfile — вэб UI-ийн яг тэр дуудлага."""
    payload = {"method": "FileManager.downloadPiece", "params": params,
               "session": session, "id": 9001}
    r = await c.post(f"http://{ip}/RPC3_Loadfile", json=payload,
                     headers={"Content-Type": "application/json",
                              "Cookie": f"WebClientHttpSessionID={session}",
                              "x-api-session": str(session)})
    return r


def show_loadfile(ip: str, r: httpx.Response, tagname: str):
    """Хариуны бүтцийг ХЭВЛЭЖ, base64 зүсэм байвал задална."""
    print(f"      HTTP {r.status_code}  ·  {len(r.content):,} байт  ·  "
          f"Content-Type: {r.headers.get('content-type', '?')}")
    if not r.content:
        return False
    lines = r.text.split("\n")
    print(f"      мөрийн тоо: {len(lines)}")
    for i, ln in enumerate(lines[:24]):
        s = ln.strip()
        if s:
            print(f"        [{i:>2}] {s[:110]}")
    # JS-ийн логик: 5-13 мөр = JSON, 19-р мөр = base64
    if len(lines) > 19:
        blob = lines[19].replace("--myboundary\r", "").strip()
        try:
            dec = base64.b64decode(blob + "=" * (-len(blob) % 4))
            out = f"/tmp/rpc3_{ip}_{tagname}.bin"
            with open(out, "wb") as f:
                f.write(dec)
            note = "  🎉 JPEG!" if dec[:2] == b"\xff\xd8" else ""
            print(f"      19-р мөр base64 → {len(dec):,} байт{note}  ({out})")
            return dec[:2] == b"\xff\xd8"
        except Exception as e:  # noqa: BLE001
            print(f"      19-р мөр base64 задарсангүй: {type(e).__name__}")
    return False


async def main(ip: str):
    user, pwd, src = creds_for(ip)
    if not user:
        print(f"⛔ {src} — ОРОЛДОХГҮЙ (буруу нэвтрэлт камерыг түгжинэ).")
        return
    print(f"=== {ip} — RPC3_Loadfile / FileManager судалгаа ===")
    print(f"Нэвтрэлт: {user} ({src})\n")

    async with httpx.AsyncClient(timeout=20) as c:
        rpc = DahuaRpc(c, ip, user, pwd)
        try:
            await rpc.login()
        except Exception as e:  # noqa: BLE001
            print(f"❌ RPC2 login БҮТСЭНГҮЙ: {e}")
            return
        sess = rpc.session_id
        print(f"RPC2 login OK (session={sess})")

        print("\n── 1. FileManager-ийн методууд")
        for method, params in FILEMANAGER_PROBES:
            try:
                res = await asyncio.wait_for(rpc._call(method, params), timeout=10)
            except Exception as e:  # noqa: BLE001
                print(f"   ❌ {method:<38} {type(e).__name__}")
                continue
            ok = bool(res.get("result"))
            err = (res.get("error") or {}).get("message", "")
            print(f"   {'✅' if ok else '·'} {method:<38} {err[:50] or str(res)[:60]}")

        print("\n── 2. RecordFinder-ийн бичлэгээс ЗАМ хайх")
        paths = []
        try:
            inst = await rpc._call("RecordFinder.factory.create",
                                   {"name": "TrafficSnapEventInfo"})
            obj = inst.get("result")
            import time as _t
            now = int(_t.time())
            await rpc._call("RecordFinder.startFind",
                            {"condition": {"Time": ["<>", now - 86400, now]}}, obj=obj)
            df = await rpc._call("RecordFinder.doFind", {"count": 4}, obj=obj)
            recs = (df.get("params") or {}).get("records") or []
            print(f"   бичлэг: {len(recs)}")
            for i, rec in enumerate(recs[:3], 1):
                found = find_paths(rec)
                print(f"   [{i}] {rec.get('PlateNumber', '?')}  RecNo={rec.get('RecNo')}"
                      f"  зам-талбар: {found or 'АЛГА'}")
                paths += [v for _, v in found]
            if not paths and recs:
                print(f"   бичлэгийн бүх талбар: {sorted(recs[0])}")
            await rpc._call("RecordFinder.stopFind", obj=obj)
            await rpc._call("RecordFinder.destroy", obj=obj)
        except Exception as e:  # noqa: BLE001
            print(f"   ❌ RecordFinder: {type(e).__name__}: {e}")

        print("\n── 3. RPC3_Loadfile — параметрийн хувилбарууд")
        # Зам олдсон бол түүгээр, эс бол бичлэгийн дугаараар оролдоно
        trials = []
        for p in paths[:3]:
            trials += [{"FileName": p, "Offset": 0, "Length": 65536},
                       {"path": p, "offset": 0, "length": 65536}]
        if not trials:
            trials = [{"FileName": "/mnt/sd/2026-08-14/snapshot.jpg",
                       "Offset": 0, "Length": 65536},
                      {"Offset": 0, "Length": 65536}]
        won = False
        for i, params in enumerate(trials[:6], 1):
            print(f"   [{i}] {json.dumps(params, ensure_ascii=False)[:88]}")
            try:
                r = await loadfile(c, ip, sess, params)
            except Exception as e:  # noqa: BLE001
                print(f"      ❌ {type(e).__name__}: {e}")
                continue
            if show_loadfile(ip, r, f"t{i}"):
                won = True
                break
            await asyncio.sleep(0.6)

        try:
            await rpc._call("global.logout")
        except Exception:  # noqa: BLE001
            pass

    print()
    if won:
        print("🎉 RPC3_Loadfile-аар ЗУРАГ АВЛАА — snapshot.cgi-г орлуулах зам нээгдлээ.")
    else:
        print("Зураг гарсангүй. Дараагийн алхам: event дотор `urlCarPano` талбар")
        print("байгаа эсэхийг харах — тэр зам байвал энэ хэрэгсэлд өгч татна.")
        print("   sudo ... tools/stream_dump.py <ip> 120 --test-capture")
        print("   (гаралтын «ЗУРАГ-шинжтэй» мөрөөс urlCarPano/url-ыг хайна)")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))
