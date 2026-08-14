#!/usr/bin/env python3
"""RPC2-оор зураг авах боломжийг шалгах — CGI зам 400 өгч байхад RPC2 ажилладаг.

ЯАГААД (2026-08-13, 10.0.105.11-ийн оношилгооноос):
    snapshot.cgi (4 хувилбар)        → HTTP 400
    snapManager.cgi?attachFileProc   → HTTP 400/500
    mediaFileFind                    → Bad Request / infos=0
    storageDevice.getDeviceAllInfo   → HTTP 400
  ГЭТЭЛ ижил агшинд:
    RPC2 login                       → OK
    RecordFinder.doFind              → 64 бичлэг
    trafficSnap.closeStrobe          → result: true
    factory.getCollect               → HTTP 200

Өөрөөр хэлбэл камерын веб сервер АМЬД, RPC2 суваг АЖИЛЛАЖ байхад зөвхөн
CGI-ийн ЗУРГИЙН дэд систем татгалзаж байна. Тиймээс зургийг RPC2-оор
шууд авах арга байвал эвдэрсэн CGI замыг БҮРЭН ТОЙРНО.

Энэ хэрэгсэл RPC2-ийн боломжит зургийн метод бүрийг дараалан оролдож,
аль нь (а) result:true өгөх, (б) хариунд base64/JPEG дата авчрахыг тогтооно.

Ажиллуулах:
    sudo /root/PARKING/backend/venv/bin/python /root/PARKING/tools/rpc_snap_probe.py 10.0.105.11
    # зураг олдвол /tmp/rpc_snap_<ip>.jpg болгож хадгална

Камерын вэб tab-уудыг ХААХ хэрэгтэй — Dahua цөөн холболт л зөвшөөрдөг.
"""
import asyncio
import base64
import json
import os
import re
import sys

os.chdir("/root/PARKING/backend")  # ЧУХАЛ: config-ийн env_file=".env" нь CWD-д
# харьцангуй тул app.* импортоос ӨМНӨ шилжинэ. Функц дотор хийвэл ХОЖУУ —
# `settings` singleton аль хэдийн буруу утгаар үүссэн байна (DB руу localhost).
sys.path.insert(0, "/root/PARKING/backend")

import httpx  # noqa: E402

from app.services.barrier import DahuaRpc  # noqa: E402

# (метод, параметр) нэр дэвшигчид. Dahua загвар/firmware бүр өөр дэмждэг тул
# бүгдийг дараалан оролдоно — аль нь ажиллахыг ТААМАГЛАЖ болохгүй.
CANDIDATES = [
    ("snapManager.postSnap", {"dispChannel": 0, "snapType": 0}),
    ("snapManager.postSnap", {"channel": 0}),
    ("snapManager.postSnap", {"Channel": 0, "SnapType": 0}),
    ("snapManager.postSnap", None),
    ("snapManager.getCaps", None),
    ("snapManager.attachFileProc", {"Flags": ["Event"], "Events": ["All"]}),
    ("Snapshot.getSnapshot", {"channel": 0}),
    ("snapshot.getSnapshot", {"channel": 0}),
    ("mediaFileFind.factory.create", None),
    ("trafficSnap.manualSnap", {"channel": 0}),
    ("trafficSnap.getSnapPicture", {"channel": 0}),
    ("TrafficSnap.manualSnap", {"channel": 0}),
    ("devVideoInput.getCaps", {"channel": 0}),
]

# base64/зургийн дата байж болох талбарын нэрс
DATA_KEYS = ("data", "Data", "picture", "Picture", "image", "Image",
             "buffer", "Buffer", "content", "Content", "jpg", "Jpeg")


def creds_for(ip: str):
    """DB-д бүртгэсэн камерын нэвтрэлт → .env глобал."""
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
        print(f"  (DB лукап бүтсэнгүй: {type(e).__name__}: {str(e)[:70]})")
    from app.config import settings
    return settings.camera_username, settings.camera_password, ".env глобал"


def find_image(obj, path="", depth=0):
    """Хариунаас base64/JPEG байж болох талбарыг гүнзгий хайна."""
    if depth > 6:
        return None
    if isinstance(obj, str) and len(obj) > 500:
        # base64 JPEG нь /9j/ гэж эхэлдэг (0xFFD8 base64-д)
        if obj.startswith("/9j/") or obj.startswith("data:image"):
            return path, obj
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else k
            if k in DATA_KEYS and isinstance(v, str) and len(v) > 200:
                return p, v
            hit = find_image(v, p, depth + 1)
            if hit:
                return hit
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:20]):
            hit = find_image(v, f"{path}[{i}]", depth + 1)
            if hit:
                return hit
    return None


async def main(ip: str):
    user, pwd, src = creds_for(ip)
    print(f"=== {ip} — RPC2 зургийн метод судалгаа ===")
    print(f"Нэвтрэлт: {user} ({src})\n")

    # ХАМГААЛАЛТ: камерууд 2026-08-11-нээс өөр өөрийн (DB) нэвтрэлттэй. DB-ээс
    # уншиж чадаагүй бол .env-ийн ГЛОБАЛ нэр буруу байх магадлал өндөр бөгөөд
    # Dahua буруу оролдлогыг тоолж (remainLoginTimes) 0 болмогц камерыг
    # ТҮГЖДЭГ. Тиймээс энд огт оролдохгүй, шалтгааныг хэлээд зогсоно.
    if src != "DB" and not src.startswith("DB"):
        print("⛔ Нэвтрэлтийг DB-ээс уншиж чадсангүй — .env глобалаар ОРОЛДОХГҮЙ.")
        print("   Буруу нэвтрэлт давтвал камер ТҮГЖИГДЭНЭ (remainLoginTimes).")
        print("   Шалтгааныг дээрх «DB лукап бүтсэнгүй» мөрөөс хараарай.")
        return

    async with httpx.AsyncClient(timeout=15) as c:
        rpc = DahuaRpc(c, ip, user, pwd)
        try:
            await rpc.login()
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            print(f"❌ RPC2 login БҮТСЭНГҮЙ: {msg}")
            m = re.search(r"remainLoginTimes'?:\s*(\d+)", msg)
            if m:
                left = int(m.group(1))
                print(f"\n⛔ ЭНЭ КАМЕРТ {left} ОРОЛДЛОГО ҮЛДЛЭЭ. Дахин БҮҮ оролдоорой —")
                print("   0 болмогц камер түгжигдэж, хаалт/event хүртэл зогсоно.")
                print("   Зөв нэвтрэлтийг: tools/cam_status.py эсвэл Тохиргоо→Төхөөрөмж")
            return
        print(f"RPC2 login OK (session={rpc.session_id})\n")

        winners = []
        for method, params in CANDIDATES:
            label = f"{method}({json.dumps(params, ensure_ascii=False) if params else ''})"
            try:
                res = await asyncio.wait_for(rpc._call(method, params), timeout=10)
            except Exception as e:  # noqa: BLE001
                print(f"  ❌ {label[:62]:<64} {type(e).__name__}")
                continue
            ok = bool(res.get("result"))
            err = (res.get("error") or {}).get("message", "")
            hit = find_image(res)
            mark = "✅" if ok else "·"
            note = ""
            if hit:
                path, b64 = hit
                try:
                    raw = base64.b64decode(b64[:400000] + "==")
                    note = f"  🎉 ЗУРАГ: {path} → {len(raw)}b"
                    if raw[:2] == b"\xff\xd8":
                        out = f"/tmp/rpc_snap_{ip}.jpg"
                        with open(out, "wb") as f:
                            f.write(base64.b64decode(b64))
                        note += f"  JPEG! хадгалав: {out}"
                        winners.append((method, path, out))
                except Exception:  # noqa: BLE001
                    note = f"  (base64 задарсангүй: {path})"
            elif not ok and err:
                note = f"  {err[:56]}"
            print(f"  {mark} {label[:62]:<64}{note}")

        try:
            await rpc._call("global.logout")
        except Exception:  # noqa: BLE001
            pass

    print()
    if winners:
        print("🎉 ЗУРАГ АВЧ ЧАДЛАА:")
        for m, p, out in winners:
            print(f"   {m}  →  {p}  →  {out}")
        print("\nЭнэ метод ажиллаж байвал snapshot.cgi-г БҮРЭН орлуулж болно.")
    else:
        print("Зураг буцаасан метод ОЛДСОНГҮЙ. result:true өгсөн методууд дээр")
        print("параметрийн өөр хослол шаардлагатай байж магадгүй — гаралтыг хуулж өгнө үү.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))
