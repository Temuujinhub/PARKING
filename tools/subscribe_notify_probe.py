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
import json
import os
import sys
import time

os.chdir("/root/PARKING/backend")  # config-ийн env_file=".env" нь CWD-д харьцангуй
sys.path.insert(0, "/root/PARKING/backend")

import httpx  # noqa: E402

OUT_DIR = "/tmp/subnotify"

# Вэб UI-ийн JS (`initComet`, `_getUrl`)-ээс АВСАН — таамаг БИШ:
#
#     var n = "/SubscribeNotify.cgi?sessionId=".concat(e);
#     t && (n = "/SubscribeNotify.cgi"),          // httpOnly горим
#     ...
#     "snapNotify" === e && (c += "&type=1")      // ← ЗУРГИЙН суваг
#
# Өөрөөр хэлбэл `Security-cgi=2&salt=…&cipher=RPAC-256` бол ЗӨВХӨН НЭГ горим;
# хажууд нь сешн дугаараар дуудах ЭНГИЙН зам байдаг.
#
# {S} нь RPC2 login-оос авсан сешнээр солигдоно.
PLAIN_VARIANTS = [
    ("sessionId + type=1 (snapNotify)", "?sessionId={S}&type=1&link=1"),
    ("sessionId",                       "?sessionId={S}&link=1"),
    ("sessionId (link-гүй)",            "?sessionId={S}"),
    ("httpOnly — параметргүй",          ""),
    ("type=1 (сешнгүй)",                "?type=1&link=1"),
]

# Comet урсгалын агуулгыг таних тэмдгүүд
MARKERS = (b"receiveMessage", b"DHAV", b"JFIF", b"DH_ITC", b"PlateNumber",
           b"<script", b"Heartbeat", b"TrafficJunction")

# «Test Capture» товчийг ПРОГРАММААР дуудах оролдлогууд (stream_dump-аас)
TEST_TRIGGERS = [
    "cgi-bin/trafficSnap.cgi?action=manualSnap&channel=1",
    "cgi-bin/trafficSnap.cgi?action=manualSnap",
    "cgi-bin/trafficParking.cgi?action=manualSnap&channel=1",
    "cgi-bin/snapManager.cgi?action=manualSnap&channel=1",
    "cgi-bin/devTest.cgi?action=testCapture&channel=1&plateNumber=AB12345",
]

EVENT_CODES = ["TrafficJunction", "TrafficSnapPicture", "TrafficControl",
               "TrafficManualSnap", "TrafficSnapPictureSuccess"]

# Comet хоолойд ЮУ урсгахыг захиалах RPC2 дуудлагууд.
# `snapManager.attachFileProc` бол Dahua-гийн ЗУРАГ түлхэх механизм —
# `SubscribeNotify.cgi?…&type=1` (snapNotify) сувагтай хосолдог.
SUBSCRIBE_CALLS = [
    ("eventManager.attach", {"codes": EVENT_CODES}),
    ("snapManager.attachFileProc",
     {"condition": {"Channel": 0}, "Types": ["jpg"], "Flags": ["Event"]}),
    ("snapManager.attachFileProc",
     {"Types": ["jpg"], "Devices": [{"Channels": [1]}]}),
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


def parse_comet(path: str) -> None:
    """Comet урсгалын түүхий файлыг задалж, `receiveMessage(...)` доторх
    JSON-ыг хэвлэнэ. Зорилго: event дотор ЗУРАГ эсвэл зургийн ЗАМ байгаа
    эсэхийг тогтоох."""
    import json
    raw = open(path, "rb").read()
    txt = raw.decode("utf-8", "replace")
    print(f"=== {path} — {len(raw):,} байт ===")

    msgs, pos = [], 0
    while True:
        i = txt.find("receiveMessage(", pos)
        if i < 0:
            break
        j, depth = i + len("receiveMessage("), 0
        start = j
        while j < len(txt):
            if txt[j] in "({[":
                depth += 1
            elif txt[j] in ")}]":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        msgs.append(txt[start:j + 1])
        pos = j + 1

    print(f"receiveMessage дуудлага: {len(msgs)}\n")
    seen_keys: dict[str, int] = {}
    for n, m in enumerate(msgs, 1):
        try:
            obj = json.loads(m)
        except Exception:  # noqa: BLE001
            print(f"[{n}] JSON биш ({len(m)}б): {m[:160]}")
            continue
        method = obj.get("method", "?")
        params = obj.get("params") or {}
        # Талбар бүрийг УРТААР нь эрэмбэлж хэвлэнэ — зураг байвал урт нь
        # бусдаас олон дахин том байх ёстой
        flat = flatten(params)
        for k in flat:
            seen_keys[k] = seen_keys.get(k, 0) + 1
        big = sorted(flat.items(), key=lambda kv: -len(str(kv[1])))[:6]
        print(f"[{n}] {method}  ({len(m):,}б, талбар {len(flat)})")
        for k, v in big:
            s = str(v)
            note = ""
            if len(s) > 200:
                note = "  ← ЭНЭ ЗУРАГ БАЙЖ МАГАДГҮЙ"
            print(f"      {k} = {len(s):>7}б  {s[:70]}{note}")

    print("\n── Бүх талбарын нэр (давтамжтай)")
    for k, cnt in sorted(seen_keys.items()):
        print(f"   {cnt:>3}×  {k}")
    # Зургийн зам байж болох нэрсийг тусад нь тодруулна
    hits = [k for k in seen_keys if any(p in k.lower() for p in
                                        ("url", "pano", "path", "file", "pic",
                                         "image", "snap", "base64", "data"))]
    print(f"\nЗУРАГТАЙ холбоотой байж болох талбарууд: {hits or 'АЛГА'}")


def flatten(obj, prefix="", out=None) -> dict:
    """Үүрлэсэн dict/list-ийг «a.b[0].c» хэлбэрийн хавтгай түлхүүр болгоно."""
    if out is None:
        out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            flatten(v, f"{prefix}.{k}" if prefix else k, out)
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:20]):
            flatten(v, f"{prefix}[{i}]", out)
    else:
        out[prefix] = obj
    return out


async def fire_test_capture(ip: str, creds) -> bool:
    """«Test Capture»-ийг программаар дуудна — гараар товч дарах шаардлагагүй."""
    auth = httpx.DigestAuth(*creds)
    async with httpx.AsyncClient(timeout=httpx.Timeout(5, read=10)) as c:
        for path in TEST_TRIGGERS:
            try:
                r = await c.get(f"http://{ip}/{path}", auth=auth)
            except Exception:  # noqa: BLE001
                continue
            if r.status_code == 200 and b"Error" not in r.content[:32]:
                print(f"      Test Capture дуудав: {path}")
                return True
            await asyncio.sleep(0.5)
    print("      ⚠ Test Capture программаар дуудагдсангүй — ГАРААР дарна уу")
    return False


async def listen(c: httpx.AsyncClient, url: str, headers: dict, auth, tag: str,
                 seconds: int) -> tuple[int, int, int]:
    """Урсгалыг `seconds` секунд сонсоод байт цуглуулж, JPEG сугална."""
    buf = bytearray()
    deadline = time.monotonic() + seconds
    try:
        async with c.stream("GET", url, headers=headers, auth=auth) as r:
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
        marks = [m.decode() for m in MARKERS if m in got]
        print(f"      тэмдэг: {', '.join(marks) if marks else '(алга)'}")
        with open(f"{OUT_DIR}/{tag}.raw", "wb") as f:
            f.write(got)
        # Comet урсгал бол HTML — эхний хэсгийг нүдээр харах нь чухал
        head = got[:400].decode("utf-8", "replace").replace("\n", " ")
        print(f"      эхлэл: {head[:180]}")
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

        # Вэб UI шиг RPC2-оор нэвтэрч сешн авна — `?sessionId=` үүнийг хүснэ
        from app.services.barrier import DahuaRpc
        rpc = DahuaRpc(c, ip, user, pwd)
        try:
            await rpc.login()
            sess = rpc.session_id
            print(f"RPC2 login OK — session={sess}")
        except Exception as e:  # noqa: BLE001
            print(f"⚠ RPC2 login бүтсэнгүй ({e}) — зөвхөн digest-ээр оролдоно")
            sess = None

        # Вэб UI сешнээ Cookie-гоор дамжуулдаг (WebClientHttpSessionID)
        hdrs = {"Referer": f"http://{ip}/",
                "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
        if sess:
            hdrs["Cookie"] = f"WebClientHttpSessionID={sess}"

        if raw_url:
            print("\n── A. DevTools-оос хуулсан URL-ыг ДАВТАХ")
            print("   (тэр сешн хаагдсан бол 401/403 гарна — тэр ч бас хариулт)")
            st, n, imgs = await listen(c, raw_url, hdrs, None, "replay", seconds)
            total_imgs += imgs
            await asyncio.sleep(1)

        print("\n── B. Шифргүй зам (JS-ийн `initComet`-ээс)")
        for i, (name, tmpl) in enumerate(PLAIN_VARIANTS, 1):
            if "{S}" in tmpl and not sess:
                print(f"   [{i}] {name}: сешн байхгүй тул алгаслаа")
                continue
            path = "/SubscribeNotify.cgi" + tmpl.replace("{S}", sess or "")
            print(f"   [{i}] {name}: {path}")
            # Энэ алхмын зорилго нь ЗӨВХӨН «хоолой нээгдэж байна уу» —
            # захиалгагүй тул удаан сонсох нь утгагүй (C алхам үүнийг хийнэ)
            st, n, imgs = await listen(c, f"http://{ip}{path}", hdrs, auth,
                                       f"plain{i}", min(seconds, 6))
            total_imgs += imgs
            if imgs:
                print(f"   ✅ «{name}» ажиллалаа — энэ бол бидний хайж байсан зам.")
                break
            await asyncio.sleep(1.5)   # камерыг дараалуулан цохихгүй

        # ── C. Хоолойг НЭЭЭД, тэр сешн дээр ЗАХИАЛГА өгнө
        # Comet бол зөвхөн доош чиглэсэн хоолой; юу урсгахыг RPC2-оор
        # захиалдаг. B алхамд «subscribe Successfully!» гараад чимээгүй
        # байсны шалтгаан яг энэ — захиалга өгөөгүй.
        if sess and not total_imgs:
            print("\n── C. Хоолой + захиалга (comet нээгээд eventManager.attach)")
            for chan, tmpl in (("snapNotify(type=1)", "?sessionId={S}&type=1"),
                               ("devNotify", "?sessionId={S}")):
                url = f"http://{ip}/SubscribeNotify.cgi" + tmpl.replace("{S}", sess)
                print(f"   [{chan}]")
                task = asyncio.create_task(
                    listen(c, url, hdrs, None, f"sub_{chan[:4]}", seconds))
                await asyncio.sleep(2.0)      # хоолой тогтвортой нээгдэх хүртэл

                for method, params in SUBSCRIBE_CALLS:
                    try:
                        res = await asyncio.wait_for(
                            rpc._call(method, params), timeout=10)
                    except Exception as e:  # noqa: BLE001
                        print(f"      · {method:<30} {type(e).__name__}")
                        continue
                    if res.get("result"):
                        print(f"      ✅ {method:<30} result=True")
                    else:
                        # Бүтэлгүйтлийн ЯГ шалтгааныг харах — «result=False»
                        # гэдэг нь метод байхгүй юу, параметр буруу юу гэдгийг
                        # ялгаж хэлдэггүй
                        print(f"      ·  {method:<30} "
                              f"{json.dumps(res.get('error') or res, ensure_ascii=False)[:80]}")

                await fire_test_capture(ip, (user, pwd))
                _, _, imgs = await task
                total_imgs += imgs
                if imgs:
                    print(f"   ✅ «{chan}» сувгаар зураг ирлээ.")
                    break
                await asyncio.sleep(1.5)

        try:
            await rpc._call("global.logout")
        except Exception:  # noqa: BLE001
            pass

    print()
    if total_imgs:
        print(f"🎉 SubscribeNotify.cgi-ЭЭР {total_imgs} ЗУРАГ АВЛАА → {OUT_DIR}/")
        print("   Дараагийн алхам: snapshot.py-д энэ сувгийг нэмж, эхний сонголт")
        print("   болгоно. snapshot.cgi нь fallback болж үлдэнэ.")
    else:
        print(f"Зураг гарсангүй. Түүхий байтууд: {OUT_DIR}/*.raw")
        print("Эхлээд тэдгээрийг хараарай — урсгал нээгдсэн ч дотор нь юу")
        print("ирснийг «тэмдэг» ба «эхлэл» мөрүүд хэлнэ:")
        print(f"   head -c 600 {OUT_DIR}/plain1.raw")
        print("Хэрэв 401/403 бол сешн хүчингүй; хэрэв 200 боловч хоосон бол")
        print("Test Capture дарж event үүсгэх шаардлагатай.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    if sys.argv[1] == "--parse":
        # Цуглуулсан түүхий comet урсгалыг задлах (камерт хандахгүй)
        for _f in sys.argv[2:] or [f"{OUT_DIR}/sub_devN.raw"]:
            parse_comet(_f)
            print()
        sys.exit(0)
    _args = sys.argv[2:]
    _url, _sec = None, 25
    for i, a in enumerate(_args):
        if a == "--url" and i + 1 < len(_args):
            _url = _args[i + 1]
        elif a in ("--seconds", "-s") and i + 1 < len(_args):
            _sec = int(_args[i + 1])
    asyncio.run(main(sys.argv[1], _url, _sec))
