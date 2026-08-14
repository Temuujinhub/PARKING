#!/usr/bin/env python3
"""Event стримийн ТҮҮХИЙ байтыг хадгалж, дотор нь ЮУ БАЙГААГ шинжилнэ.

Зорилго: «камер зургаа стримээр илгээдэг, бидний задлагч алддаг» гэсэн
таамгийг МАРГААНГҮЙ шийдэх. Кодыг уншиж маргахын оронд камер юу илгээж
байгааг байт байтаар нь харна.

Юу хийх вэ:
  1. eventManager.cgi (multipart)-д холбогдож N секунд СОНСОНО
  2. Ирсэн БҮХ байтыг /tmp/stream_<ip>.bin болгож хадгална
  3. Дотроос нь тоолно: boundary, Content-Type толгойнууд, JPEG SOI/EOI,
     `data={` блокууд
  4. Зураг олдвол /tmp/stream_<ip>_<N>.jpg болгож задлана
  5. Мөн snapshot-ийн ӨӨР URL хувилбаруудыг шалгана (magicBox г.м)

Ажиллуулах (машин орж/гарч байх үед — event гарах ёстой):
    sudo /root/PARKING/backend/venv/bin/python /root/PARKING/tools/stream_dump.py 10.0.106.10 60

АНХААР: backend аль хэдийн энэ камерт event стрим барьж байгаа. Dahua цөөн
холболт л зөвшөөрдөг тул энэ хэрэгсэл түүнтэй ӨРСӨЛДӨНӨ — «event гараагүй»
гэж гарвал тэр нь камерын бус, өрсөлдөөний үр дүн байж болно. Хамгийн цэвэр
хэмжилт: backend-ийг түр зогсоогоод ажиллуулах (эсвэл үр дүнг backend-ийн
логтой хамт унших).
"""
import asyncio
import os
import re
import sys

os.chdir("/root/PARKING/backend")  # ЧУХАЛ: config-ийн env_file=".env" нь CWD-д
# харьцангуй тул app.* импортоос ӨМНӨ шилжинэ (эс бол буруу нэвтрэлтээр
# оролдож камерыг ТҮГЖИХ эрсдэлтэй — remainLoginTimes).
sys.path.insert(0, "/root/PARKING/backend")

import httpx  # noqa: E402

SOI, EOI = b"\xff\xd8\xff", b"\xff\xd9"

# Туршиж үзэх snapshot URL-ууд (magicBox нь зөвлөмжид дурдагдсан, бидэнд шинэ)
SNAP_URLS = [
    "cgi-bin/snapshot.cgi",
    "cgi-bin/snapshot.cgi?channel=1&type=0",
    "cgi-bin/magicBox.cgi?action=getSnapshot&channel=1",
    "cgi-bin/magicBox.cgi?action=getSnapshot",
    "cgi-bin/configManager.cgi?action=getSnapshot&channel=1",
]


def creds_for(ip: str):
    """DB-д бүртгэсэн камерын нэвтрэлт. Олдохгүй бол (None, None, шалтгаан)."""
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


async def dump_stream(ip: str, creds, secs: int) -> bytes:
    url = (f"http://{ip}/cgi-bin/eventManager.cgi?action=attach"
           f"&codes=[TrafficJunction,TrafficSnapPicture,TrafficControl]"
           f"&heartbeat=5&httptype=multipart")
    print(f"\n── Стрим сонсож байна ({secs}с)\n   {url}")
    buf = bytearray()
    auth = httpx.DigestAuth(*creds)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10, read=secs + 10)) as c:
            async with c.stream("GET", url, auth=auth) as r:
                print(f"   HTTP {r.status_code}")
                for k, v in r.headers.items():
                    if "content-type" in k.lower():
                        print(f"   хариуны Content-Type: {v}")
                if r.status_code != 200:
                    return bytes(buf)
                loop = asyncio.get_running_loop()
                deadline = loop.time() + secs
                async for chunk in r.aiter_bytes():
                    buf += chunk
                    if loop.time() > deadline:
                        break
    except httpx.ReadTimeout:
        print("   (read timeout — энэ хугацаанд дата ирсэнгүй)")
    except Exception as e:  # noqa: BLE001
        print(f"   ❌ {type(e).__name__}: {e}")
    return bytes(buf)


def analyse(ip: str, raw: bytes):
    print(f"\n── Шинжилгээ: нийт {len(raw):,} байт")
    if not raw:
        print("   Дата огт ирээгүй. Машин орж/гарсан уу? backend өрсөлдөж байж болно.")
        return
    out = f"/tmp/stream_{ip}.bin"
    with open(out, "wb") as f:
        f.write(raw)
    print(f"   түүхий дата: {out}")

    # multipart-ийн хэсгийн толгойнууд
    ctypes = re.findall(rb"Content-Type:\s*([^\r\n]+)", raw, re.I)
    clens = re.findall(rb"Content-Length:\s*(\d+)", raw, re.I)
    bounds = re.findall(rb"--[A-Za-z0-9_\-]{4,40}\r\n", raw)
    print(f"   Content-Type толгой: {len(ctypes)}")
    for t in sorted({t.decode('ascii', 'replace').strip() for t in ctypes}):
        print(f"      • {t}")
    print(f"   Content-Length толгой: {len(clens)}   boundary мөр: {len(bounds)}")
    if bounds:
        print(f"      жишээ boundary: {bounds[0].decode('ascii', 'replace').strip()}")

    print(f"   `Code=` тоо: {raw.count(b'Code=')}    `data={{` тоо: {raw.count(b'data={')}")
    print(f"   JPEG SOI (\\xff\\xd8\\xff): {raw.count(SOI)}    EOI (\\xff\\xd9): {raw.count(EOI)}")

    # Бүрэн JPEG-үүдийг таслаж хадгална
    n, pos = 0, 0
    while True:
        i = raw.find(SOI, pos)
        if i < 0:
            break
        j = raw.find(EOI, i + 3)
        if j < 0:
            print(f"   ⚠ SOI олдсон ч EOI алга (offset {i}) — зураг бүрэн ирээгүй")
            break
        n += 1
        p = f"/tmp/stream_{ip}_{n}.jpg"
        with open(p, "wb") as f:
            f.write(raw[i:j + 2])
        print(f"   🎉 ЗУРАГ {n}: {j + 2 - i:,} байт → {p}")
        pos = j + 2
    if not n:
        print("   ❌ Бүрэн JPEG ОЛДСОНГҮЙ — камер стримээр зураг илгээгээгүй.")
        head = raw[:400].decode("utf-8", "replace").replace("\r", "\\r").replace("\n", "\\n")
        print(f"   Эхний 400 байт: {head}")


async def try_snap_urls(ip: str, creds):
    print("\n── snapshot URL хувилбарууд")
    auth = httpx.DigestAuth(*creds)
    async with httpx.AsyncClient(timeout=httpx.Timeout(5, read=15)) as c:
        for path in SNAP_URLS:
            try:
                r = await c.get(f"http://{ip}/{path}", auth=auth)
            except Exception as e:  # noqa: BLE001
                print(f"   ❌ {path:<52} {type(e).__name__}")
                continue
            ok = r.status_code == 200 and r.content[:2] == b"\xff\xd8"
            mark = "✅" if ok else "· "
            note = (f"{len(r.content) // 1024}KB JPEG" if ok
                    else f"HTTP {r.status_code} {r.content[:40]!r}")
            print(f"   {mark} {path:<52} {note}")
            await asyncio.sleep(1.0)   # камерыг дараалуулан цохихгүй


async def main(ip: str, secs: int):
    user, pwd, src = creds_for(ip)
    print(f"=== {ip} — event стримийн түүхий шинжилгээ ===")
    if not user:
        print(f"⛔ {src}")
        print("   Нэвтрэлтийг DB-ээс уншиж чадсангүй — ОРОЛДОХГҮЙ.")
        print("   Буруу нэвтрэлт давтвал камер ТҮГЖИГДЭНЭ (remainLoginTimes).")
        return
    print(f"Нэвтрэлт: {user} ({src})")
    raw = await dump_stream(ip, (user, pwd), secs)
    analyse(ip, raw)
    await try_snap_urls(ip, (user, pwd))
    print()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    asyncio.run(main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 60))
