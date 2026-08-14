#!/usr/bin/env python3
"""SubscribeNotify.cgi — зураг АГУУЛСАН урсгалыг барих туршилт.

ОЛДВОР (2026-08-14, DevTools):
    вэб UI-ийн `blob:` хариунд  Content-Length: 753,949
    эхний байтууд: `JFIF`      → ЖИНХЭНЭ JPEG
    дотор нь:      `DHAV`, `DH_ITC`, "Pulse", "Class", "ExtraPlateNumber",
                   "ParkType", "SafeBelt", "Province" → ANPR event JSON

Өөрөөр хэлбэл ЗУРАГ ба EVENT нэг урсгалаар ирдэг, гэхдээ `eventManager.cgi`
биш — **`SubscribeNotify.cgi`**-ээр. Бидний 7 туршилт бүгд буруу сувагт
хайж байсан.

Хүсэлтийн бүтэц (вэб UI):
    /SubscribeNotify.cgi?Security-cgi=2&salt=<512 hex>&content=<base64>
                        &cipher=RPAC-256&time=<ms>&link=1

`salt`/`content` нь ПАРАМЕТРИЙГ шифрлэсэн; ХАРИУ нь ил (DHAV + JPEG).
Тиймээс дараах 2 асуултыг шалгана:

  A. Хуулсан URL-ыг ДАВТАХАД зураг ирэх үү?  (суваг батлагдана)
  B. Шифргүй, ИЛ параметрээр дуудаж болох уу? (бидний код бичих боломж)

Ажиллуулах:
    # A — DevTools-оос хуулсан бүтэн URL-ыг хашилтад хийж өг
    sudo /root/PARKING/backend/venv/bin/python \\
        /root/PARKING/tools/subscribe_notify_probe.py 10.0.105.10 \\
        --url 'http://10.0.105.10/SubscribeNotify.cgi?Security-cgi=2&salt=...'

    # B — ил параметрийн хувилбаруудыг шалгах
    sudo ... /root/PARKING/tools/subscribe_notify_probe.py 10.0.105.10

Backend-ийг ЗОГСООХ шаардлагагүй (энэ нь өөр суваг), гэхдээ камерын вэб
tab-уудыг хаасан байвал сайн.
"""
import asyncio
import os
import sys
import time

os.chdir("/root/PARKING/backend")  # config-ийн env_file=".env" нь CWD-д харьцангуй
sys.path.insert(0, "/root/PARKING/backend")

import httpx  # noqa: E402

OUT_DIR = "/tmp/subnotify"

# Шифргүй дуудаж болох эсэхийг шалгах параметрийн хувилбарууд.
# Dahua-гийн бусад CGI-ийн хэв маягаас гаргасан таамаг.
PLAIN_VARIANTS = [
    ("толгойгүй",           {}),
    ("link=1",              {"link": "1"}),
    ("heartbeat",           {"heartbeat": "5", "link": "1"}),
    ("codes бүгд",          {"codes": "[All]", "heartbeat": "5", "link": "1"}),
    ("codes TrafficJunction",
     {"codes": "[TrafficJunction]", "heartbeat": "5", "link": "1"}),
    ("events",              {"events": "[TrafficJunction]", "link": "1"}),
    ("channel+pic",         {"channel": "1", "picture": "1", "link": "1"}),
]


def creds_for(ip: str):
    """DB-д бүртгэсэн камерын нэвтрэлт. Олдохгүй бол оролдохгүй —
    буруу нэвтрэлт `remainLoginTimes`-ыг барж камерыг түгжинэ."""
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


def carve_jpegs(buf: bytes, tag: str) -> int:
    """Урсгалын байтуудаас JPEG-үүдийг сугалж файл болгоно.

    DHAV фрейм дотор JPEG нь SOI(ffd8ff) … EOI(ffd9)-ээр хүрээлэгдсэн байдаг
    тул фрейм задлахгүйгээр шууд огтолж болно."""
    os.makedirs(OUT_DIR, exist_ok=True)
    n, pos = 0, 0
    while True:
        soi = buf.find(b"\xff\xd8\xff", pos)
        if soi < 0:
            break
        eoi = buf.find(b"\xff\xd9", soi + 3)
        if eoi < 0:
            break
        img = buf[soi:eoi + 2]
        pos = eoi + 2
        if len(img) < 4096:      # жижиг нь thumbnail/хог байх магадлалтай
            continue
        n += 1
        out = f"{OUT_DIR}/{tag}_{n}.jpg"
        with open(out, "wb") as f:
            f.write(img)
        print(f"      🎉 JPEG #{n}: {len(img) // 1024}KB → {out}")
    return n


async def listen(c: httpx.AsyncClient, url: str, auth, tag: str,
                 seconds: int) -> tuple[int, int, int]:
    """Урсгалыг `seconds` секунд сонсоод байт цуглуулж, JPEG сугална."""
    buf = bytearray()
    deadline = time.monotonic() + seconds
    try:
        async with c.stream("GET", url, auth=auth) as r:
            ct = r.headers.get("content-type", "?")
            print(f"      HTTP {r.status_code}  ·  Content-Type: {ct}")
            if r.status_code != 200:
                await r.aread()
                return r.status_code, 0, 0
            async for chunk in r.aiter_bytes():
                buf += chunk
                if time.monotonic() > deadline:
                    break
    except (httpx.ReadTimeout, asyncio.TimeoutError):
        pass
    except Exception as e:  # noqa: BLE001
        print(f"      ❌ {type(e).__name__}: {str(e)[:70]}")
        return -1, len(buf), 0
    got = bytes(buf)
    print(f"      {len(got):,} байт хүлээж авав")
    if got:
        marks = [m for m in (b"DHAV", b"JFIF", b"DH_ITC", b"PlateNumber")
                 if m in got]
        if marks:
            print(f"      тэмдэг: {', '.join(m.decode() for m in marks)}")
        with open(f"{OUT_DIR}/{tag}.raw", "wb") as f:
            f.write(got)
    return 200, len(got), carve_jpegs(got, tag)


async def main(ip: str, raw_url: str | None, seconds: int):
    user, pwd, src = creds_for(ip)
    if not user:
        print(f"⛔ {src} — ОРОЛДОХГҮЙ (буруу нэвтрэлт камерыг түгжинэ).")
        return
    print(f"=== {ip} — SubscribeNotify.cgi (зураг агуулсан суваг) ===")
    print(f"Нэвтрэлт: {user} ({src})")
    os.makedirs(OUT_DIR, exist_ok=True)

    total_imgs = 0
    # read timeout нь сонсох хугацаанаас урт байх ёстой — comet урсгал чимээгүй
    # байх үе бий
    timeout = httpx.Timeout(10, read=seconds + 10)
    async with httpx.AsyncClient(timeout=timeout) as c:
        auth = httpx.DigestAuth(user, pwd)

        if raw_url:
            print("\n── A. DevTools-оос хуулсан URL-ыг ДАВТАХ")
            print("   (сешн хугацаа дууссан бол 401/403 гарна — тэр ч бас хариулт)")
            # Хуулсан URL нь аль хэдийн эрх агуулсан байж болзошгүй тул
            # эхлээд auth-гүй, дараа нь digest-тэй
            for label, a in (("auth-гүй", None), ("digest", auth)):
                print(f"   [{label}]")
                st, n, imgs = await listen(c, raw_url, a, f"replay_{label}", seconds)
                total_imgs += imgs
                if imgs:
                    break
                await asyncio.sleep(1)

        print("\n── B. Шифргүй, ИЛ параметрийн хувилбарууд")
        for i, (name, params) in enumerate(PLAIN_VARIANTS, 1):
            q = "&".join(f"{k}={v}" for k, v in params.items())
            url = f"http://{ip}/SubscribeNotify.cgi" + (f"?{q}" if q else "")
            print(f"   [{i}] {name}: {url[len(f'http://{ip}'):]}")
            st, n, imgs = await listen(c, url, auth, f"plain{i}", seconds)
            total_imgs += imgs
            if imgs:
                print(f"   ✅ «{name}» ажиллалаа — энэ бол бидний хайж байсан зам.")
                break
            await asyncio.sleep(1.5)   # камерыг дараалуулан цохихгүй

    print()
    if total_imgs:
        print(f"🎉 SubscribeNotify.cgi-ЭЭР {total_imgs} ЗУРАГ АВЛАА → {OUT_DIR}/")
        print("   Дараагийн алхам: snapshot.py-д энэ сувгийг нэмж, эхний сонголт")
        print("   болгоно. snapshot.cgi нь fallback болж үлдэнэ.")
    else:
        print("Зураг гарсангүй.")
        print("Дараагийн алхам — вэб UI-ийн JS-ээс шифрлэлтийг унших:")
        print("  DevTools → Sources → Ctrl+Shift+F → «RPAC» гэж хайх")
        print("  (эсвэл «Security-cgi», «SubscribeNotify», «salt»)")
        print("  Олдсон функцийг илгээвэл Python-д хөрвүүлнэ.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    _args = sys.argv[2:]
    _url, _sec = None, 25
    for i, a in enumerate(_args):
        if a == "--url" and i + 1 < len(_args):
            _url = _args[i + 1]
        elif a in ("--seconds", "-s") and i + 1 < len(_args):
            _sec = int(_args[i + 1])
    asyncio.run(main(sys.argv[1], _url, _sec))
