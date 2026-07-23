"""Камерын ЖИНХЭНЭ event зургийг татах — Web 5.0 клиентийн ашигладаг WS/RPC2 арга.

Энэ firmware зургийн CGI-уудад (snapManager.cgi, mediaFileFind.cgi, snapshot.cgi)
"Bad Request" өгдөг нь production дээр батлагдсан (tools/camera_snap_diag.py) —
web клиент бүгдийг RPC2 + WebSocket-оор хийдэг (docs/barrier_test3 клиент JS):

1. ЛАЙВ: ws://<ip>/webappoverwebsocket — RPC2 login-ий session-тэйгээр холбогдож
   snapManager.factory.instance → snapManager.attachFileProc {filter, proc:1}
   (SubScribe) гэж бүртгүүлбэл event бүрд client.notifySnapFile notification
   БИНАРИ ЗУРАГТАЙГАА ирдэг. Frame формат: [2 байт header урт LE][header JSON]
   [payload JSON (+ BinSize байт бинари сүүл)].

2. НӨХӨЛТ: RPC2 mediaFileFind.factory.create → findFile {condition} →
   findNextFile → RPC_Loadfile — камерт хадгалагдсан зургийг цагийн мужаар татна.

Туршилт (DB-гүй, production сервер дээр):
    venv/bin/python -m app.services.snap_puller 10.0.113.10
"""
import asyncio
import json
import re
import sys
import time
from datetime import datetime, timedelta

import httpx

from ..config import settings
from ..database import SessionLocal
from ..models import Device, ParkingSession
from .barrier import DahuaRpc

_tasks: dict[str, asyncio.Task] = {}

_PLATE_JSON_RE = re.compile(r'"PlateNumber"\s*:\s*"([^"]+)"')

# Firmware бүр filter-ийн өөр хэлбэр хүлээдэг — амжилттай болтол дарааллаар оролдоно.
# PRODUCTION ДЭЭР БАТЛАГДСАН (2026-07-23, ITC ANPR Web 5.0): Channels заавал [1]
# (0-ээр 268959743 өгдөг) — тиймээс ялсан хувилбар эхэндээ.
ATTACH_FILTERS = [
    {"Channels": [1], "Events": ["All"], "NeedData": True, "Flags": ["Event", "Manual"]},
    {"Channels": [1], "Events": ["All"], "NeedData": True, "Flags": ["Event", "Manual"],
     "Internal": 1, "OfflineParam": {"ClientIP": "", "ClientID": ""},
     "Support": ["Ack"], "Transfer": ["Realtime"]},
    {"Channels": [0], "Events": ["All"], "NeedData": True, "Flags": ["Event", "Manual"]},
    {"Channels": [0], "Events": ["TrafficJunction"], "NeedData": True, "Flags": ["Event"]},
    {"Channels": [1], "Events": ["TrafficJunction"], "NeedData": True, "Flags": ["Event"]},
    {"Channels": [0], "Events": ["All"], "Flags": ["Event", "Manual"]},
]


# ─── WS frame кодлол (клиент JS-ийн _send/_receiveMessage-ээс) ───────────────

def ws_encode(session, payload: dict, subscribe: bool = False) -> bytes:
    body = json.dumps(payload, ensure_ascii=False).encode()
    # ЧУХАЛ: URL талбар ямагт явна — Request үед "RPC2" (клиент JS-ийн default),
    # үгүй бол камер frame-ийг чиглүүлж чадахгүй, хариу өгдөггүй
    header = {"TotalSize": len(body),
              "Type": "SubScribe" if subscribe else "Request",
              "SessionID": session,
              "URL": "SubscribeNotify" if subscribe else "RPC2"}
    h = json.dumps(header).encode()
    return bytes([len(h) & 255, (len(h) >> 8) & 255]) + h + body


def ws_decode(data: bytes) -> tuple[dict, dict | None, bytes]:
    """→ (header, payload, binary). Notification-д BinSize байт бинари сүүлтэй."""
    hlen = data[0] | (data[1] << 8)
    header = json.loads(data[2:2 + hlen])
    rest = data[2 + hlen:]
    binary = b""
    if header.get("Type") == "Notification" and header.get("BinSize"):
        json_size = int(header.get("TotalSize", len(rest))) - int(header["BinSize"])
        payload = json.loads(rest[:json_size]) if json_size > 0 else None
        binary = bytes(rest[json_size:json_size + int(header["BinSize"])])
    else:
        payload = json.loads(rest) if rest else None
    return header, payload, binary


def plate_from_notify(payload: dict | None) -> str | None:
    if not payload:
        return None
    m = _PLATE_JSON_RE.search(json.dumps(payload, ensure_ascii=False))
    return m.group(1).strip() if m else None


# ─── Session-д холбох ────────────────────────────────────────────────────────

async def _attach_to_session(device_id: str, plate: str, lane_dir: str, data: bytes):
    """Зургийг хадгалаад тухайн дугаарын хамгийн сүүлийн session-д холбоно.
    Event боловсруулалт (cgi_poller) зургаас хоцорч болзошгүй тул хэдэнтээ оролдоно."""
    from ..session_logic import normalize_plate
    from .snapshot import _save
    plate_n = normalize_plate(plate) or plate.strip().upper()
    rel = _save(data, plate_n, lane_dir)
    if not rel:
        return
    for attempt in range(5):
        db = SessionLocal()
        try:
            device = db.get(Device, device_id)
            if not device:
                return
            s = (db.query(ParkingSession)
                 .filter(ParkingSession.site_id == device.site_id,
                         ParkingSession.plate_number == plate_n,
                         ParkingSession.entry_time >= datetime.utcnow() - timedelta(hours=48))
                 .order_by(ParkingSession.entry_time.desc()).first())
            if s:
                if lane_dir == "exit":
                    s.exit_snapshot = rel
                else:
                    s.entry_snapshot = rel
                db.commit()
                print(f"[snap_pull] {plate_n} {lane_dir}: event зураг OK ({len(data)}b) → {rel}")
                return
        except Exception as e:
            print(f"[snap_pull] {plate_n}: session холбох алдаа: {e}")
        finally:
            db.close()
        await asyncio.sleep(1.5)
    print(f"[snap_pull] {plate_n} {lane_dir}: session олдсонгүй, файл {rel} хадгалагдав")


# ─── Лайв WS стрим ───────────────────────────────────────────────────────────

class AttachRejected(RuntimeError):
    """Камер энэ filter хувилбарыг гологдуулав — дараагийн хувилбарыг шинэ
    холболт дээр туршина (нэг холболтод эхний алдааны дараа камер дүлийрдэг)."""


async def _ws_session(ip: str, on_picture, flt: dict, test_mode: bool = False):
    """Нэг WS холболтын амьдрал: login → detach(хуучин) → attach → notification.
    on_picture(plate, jpeg_bytes) — plate-тай бүрэн jpeg бүрд дуудагдана."""
    import websockets

    username = settings.camera_username
    password = settings.camera_password
    async with httpx.AsyncClient(timeout=15) as hc:
        rpc = DahuaRpc(hc, ip, username, password)
        await rpc.login()
        sid = rpc.session_id
        try:
            headers = {"Cookie": f"WebClientHttpSessionID={sid}", "x-api-session": str(sid)}
            async with websockets.connect(
                    f"ws://{ip}/webappoverwebsocket", additional_headers=headers,
                    max_size=32 * 1024 * 1024, open_timeout=10, ping_interval=None) as ws:
                msg_id = 100

                async def call(method: str, params=None, subscribe=False, wait=8,
                               extra: dict | None = None):
                    """Дуудлага явуулж ижил id-тэй хариуг хүлээнэ (None = хариугүй)."""
                    nonlocal msg_id
                    msg_id += 1
                    payload = {"method": method, "id": msg_id, "session": sid}
                    if params is not None:
                        payload["params"] = params
                    if extra:
                        payload.update(extra)
                    await ws.send(ws_encode(sid, payload, subscribe=subscribe))
                    want = msg_id
                    deadline = time.monotonic() + wait
                    while time.monotonic() < deadline:
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=2)
                        except asyncio.TimeoutError:
                            continue
                        if not isinstance(raw, (bytes, bytearray)):
                            continue
                        _, resp, _ = ws_decode(bytes(raw))
                        if test_mode and resp:
                            print(f"  frame: {json.dumps(resp, ensure_ascii=False)[:180]}")
                        if resp and resp.get("id") == want:
                            return resp
                    return None

                inst = await call("snapManager.factory.instance", wait=12)
                obj = inst.get("result") if inst else None
                if not obj:
                    raise RuntimeError("snapManager.factory.instance хариу ирсэнгүй (12с)")

                # Хуучин гацсан бүртгэлийг цэвэрлэнэ (OfflineParam-тай бүртгэл
                # session үхсэн ч үлдэж, шинэ attach-ийг 268959743-аар гологдуулдаг)
                await call("snapManager.detachFileProc", {"filter": flt, "proc": 1},
                           extra={"object": obj}, wait=3)

                resp = await call("snapManager.attachFileProc", {"filter": flt, "proc": 1},
                                  subscribe=True, extra={"object": obj}, wait=8)
                ok = resp and (resp.get("result") or (resp.get("params") or {}).get("SID"))
                if not ok:
                    err = json.dumps((resp or {}).get("error") or resp or "хариугүй",
                                     ensure_ascii=False)[:150]
                    raise AttachRejected(err)
                print(f"[snap_pull] {ip}: WS зургийн суваг ХОЛБОГДЛОО (subscribe OK, "
                      f"filter={flt.get('Flags')}/{flt.get('Events')})")

                # Дугааргүй notification-д хамгийн сүүлийн дугаарыг оноох (event-ийн
                # зургууд хэдэн секундын дотор цувж ирдэг)
                last_plate: str | None = None
                last_plate_ts = 0.0
                last_ka = time.monotonic()
                while True:
                    # keepAlive: RPC2 session 60с-д хөрдөг тул 25с тутам сунгана
                    if time.monotonic() - last_ka > 25:
                        last_ka = time.monotonic()
                        msg_id += 1
                        await ws.send(ws_encode(sid, {"method": "global.keepAlive",
                                                      "params": {"timeout": 300, "active": True},
                                                      "id": msg_id, "session": sid}))
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=30)
                    except asyncio.TimeoutError:
                        continue  # event байхгүй чимээгүй үе — keepAlive л явуулна
                    if not isinstance(raw, (bytes, bytearray)):
                        continue
                    try:
                        hdr, payload, binary = ws_decode(bytes(raw))
                    except Exception:
                        continue
                    if test_mode and payload:
                        print(f"  frame[{hdr.get('Type')}]: bin={len(binary)}b "
                              f"{json.dumps(payload, ensure_ascii=False)[:180]}")
                    if not payload or payload.get("method") != "client.notifySnapFile":
                        continue
                    # Support:["Ack"] амласан тул файл бүрийг хүлээж авснаа мэдэгдэнэ —
                    # эс бол камер дараагийн зургуудаа түр саатуулж болзошгүй
                    meta = json.dumps(payload.get("params") or {}, ensure_ascii=False)
                    pid = re.search(r'"PicID"\s*:\s*"?([\w.-]+)"?', meta)
                    if pid:
                        msg_id += 1
                        pic_id = int(pid.group(1)) if pid.group(1).isdigit() else pid.group(1)
                        await ws.send(ws_encode(sid, {"method": "snapManager.ackUpload",
                                                      "params": {"PicID": pic_id, "ClientID": "",
                                                                 "ClientIP": "", "result": True},
                                                      "object": obj, "id": msg_id, "session": sid}))
                    plate = plate_from_notify(payload)
                    now = time.monotonic()
                    if plate:
                        last_plate, last_plate_ts = plate, now
                    elif last_plate and now - last_plate_ts < 5:
                        plate = last_plate
                    if test_mode:
                        keys = list((payload.get("params") or {}).keys())
                        print(f"  notify: plate={plate!r} binary={len(binary)}b params_keys={keys}")
                    if binary[:2] == b"\xff\xd8" and plate:
                        await on_picture(plate, binary)
        finally:
            await rpc.logout()


async def _pull_one(device_id: str, ip: str, lane_dir: str):
    """Нэг камерын зургийн WS сувгийг тасралтгүй барина (reconnect-тэй).
    Event бүрд хэд хэдэн зураг (бүтэн кадр + тайрмал) ирдэг — 2.5с цонхонд
    дугаар тус бүрийн ХАМГИЙН ТОМЫГ нь session-д холбоно."""
    best: dict[str, tuple[float, bytes]] = {}  # plate → (ирсэн цаг, хамгийн том jpeg)

    async def flush_stale(force: bool = False):
        now = time.monotonic()
        for plate in list(best):
            ts, data = best[plate]
            if force or now - ts > 2.5:
                del best[plate]
                asyncio.create_task(_attach_to_session(device_id, plate, lane_dir, data))

    async def on_picture(plate: str, data: bytes):
        ts, old = best.get(plate, (0.0, b""))
        best[plate] = (time.monotonic(), data if len(data) > len(old) else old)
        await flush_stale()

    vi = 0  # амжилттай болсон filter хувилбар дээрээ тогтоно
    while True:
        flt = ATTACH_FILTERS[vi % len(ATTACH_FILTERS)]
        try:
            await _ws_session(ip, on_picture, flt)
        except AttachRejected as e:
            print(f"[snap_pull] {ip}: filter #{vi % len(ATTACH_FILTERS) + 1} гологдов ({e}) — "
                  f"дараагийн хувилбар 10с дараа")
            vi += 1
            await flush_stale(force=True)
            await asyncio.sleep(10)
            continue
        except Exception as e:
            print(f"[snap_pull] {ip}: WS тасарлаа ({type(e).__name__}: {str(e)[:120]}) — 15с дараа дахин")
        await flush_stale(force=True)
        await asyncio.sleep(15)


async def supervisor():
    """Идэвхтэй камер бүрд зургийн WS task ажиллуулна (cgi_poller-тай ижил хэв маяг)."""
    if not settings.snap_pull:
        return
    print("[snap_pull] идэвхжлээ — камеруудаас event зураг татаж эхэлж байна (WS/RPC2)")
    while True:
        db = SessionLocal()
        try:
            cams = db.query(Device).filter(
                Device.device_type == "camera", Device.status == "active",
                Device.ip_address.isnot(None), Device.ip_address != "",
            ).all()
            active = set()
            for c in cams:
                active.add(c.id)
                if c.id not in _tasks or _tasks[c.id].done():
                    _tasks[c.id] = asyncio.create_task(
                        _pull_one(c.id, c.ip_address, c.lane_dir or "entry"))
                    print(f"[snap_pull] {c.name} ({c.ip_address}) зургийн стрим эхэллээ")
            for did in list(_tasks):
                if did not in active:
                    _tasks[did].cancel()
                    del _tasks[did]
        except Exception as e:
            print(f"[snap_pull] supervisor алдаа: {e}")
        finally:
            db.close()
        await asyncio.sleep(60)


# ─── Нөхөн таталт: RPC2 mediaFileFind + RPC_Loadfile ─────────────────────────

async def fetch_stored_picture(ip: str, start: datetime, end: datetime) -> tuple[bytes | None, str]:
    """Камерын санах ойгоос [start, end] (КАМЕРЫН ЦАГААР) мужийн хамгийн том
    jpg-г RPC2-оор татна. Буцаах: (зураг|None, алдааны тайлбар)."""
    fmt = "%Y-%m-%d %H:%M:%S"
    username = settings.camera_username
    password = settings.camera_password
    try:
        async with httpx.AsyncClient(timeout=25) as client:
            rpc = DahuaRpc(client, ip, username, password)
            await rpc.login()
            obj = None
            try:
                inst = await rpc._call("mediaFileFind.factory.create")
                obj = inst.get("result")
                if not obj:
                    return None, f"mediaFileFind.factory.create: {json.dumps(inst)[:100]}"
                base_cond = {"StartTime": start.strftime(fmt), "EndTime": end.strftime(fmt)}
                infos = []
                # Firmware-ээс хамаарч нөхцөлийн хэлбэр ялгаатай — хувилбаруудыг дарааллаар
                for extra in ({"Channel": 0, "Types": ["jpg"], "Flags": ["Event"]},
                              {"Channel": 0, "Types": ["jpg"]},
                              {"Channel": 1, "Types": ["jpg"]},
                              {"Channel": 0}):
                    ff = await rpc._call("mediaFileFind.findFile",
                                         {"condition": {**base_cond, **extra}}, obj=obj)
                    if not ff.get("result"):
                        continue
                    nf = await rpc._call("mediaFileFind.findNextFile", {"count": 64}, obj=obj)
                    infos = (nf.get("params") or {}).get("infos") or []
                    if infos:
                        break
                infos = [i for i in infos if i.get("FilePath")]
                if not infos:
                    return None, "камерт энэ мужид хадгалагдсан зураг олдсонгүй"
                best = max(infos, key=lambda i: int(i.get("Length") or 0))
                path = best["FilePath"]
                headers = {"Cookie": f"WebClientHttpSessionID={rpc.session_id}",
                           "x-api-session": str(rpc.session_id)}
                last = ""
                for url in (f"http://{ip}/RPC_Loadfile{path}",
                            f"http://{ip}/cgi-bin/RPC_Loadfile{path}"):
                    r = await client.get(url, headers=headers)
                    if r.status_code == 200 and r.content[:2] == b"\xff\xd8":
                        return r.content, ""
                    last = f"HTTP {r.status_code} ({len(r.content)}b)"
                return None, f"RPC_Loadfile татаж чадсангүй: {last} — {path}"
            finally:
                if obj:
                    try:
                        await rpc._call("mediaFileFind.close", obj=obj)
                        await rpc._call("mediaFileFind.destroy", obj=obj)
                    except Exception:
                        pass
                await rpc.logout()
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:120]}"


# ─── Туршилтын горим: DB-гүйгээр WS сувгийг шалгах ──────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Хэрэглээ: python -m app.services.snap_puller <камерын IP>")
        sys.exit(1)
    _ip = sys.argv[1]
    print(f"{_ip}: WS зургийн суваг руу холбогдож 120 секунд сонсоно (Ctrl+C зогсооно).")
    print("Машин өнгөрөхөд notify мөр гарч, зураг /tmp/snaptest-д хадгалагдана.")
    print("АНХААР: камер subscribe-ийг ганц сувагт өгдөг — backend сервис ажиллаж"
          " байвал эхлээд: sudo systemctl stop parking-backend (дараа нь start)\n")

    async def _test():
        import os
        os.makedirs("/tmp/snaptest", exist_ok=True)
        n = 0

        async def on_pic(plate, data):
            nonlocal n
            n += 1
            fn = f"/tmp/snaptest/{plate}_{n}.jpg"
            open(fn, "wb").write(data)
            print(f"  ЗУРАГ: {plate} {len(data)}b → {fn}")

        for i, flt in enumerate(ATTACH_FILTERS, 1):
            print(f"— filter #{i}: Flags={flt.get('Flags')} Events={flt.get('Events')}"
                  f"{' +OfflineParam' if 'OfflineParam' in flt else ''}")
            try:
                await asyncio.wait_for(_ws_session(_ip, on_pic, flt, test_mode=True), timeout=120)
            except asyncio.TimeoutError:
                print(f"\n120с дууслаа — {n} зураг ирэв.")
                break
            except AttachRejected as e:
                print(f"  гологдов: {e}\n")
                await asyncio.sleep(3)
            except Exception as e:
                print(f"\nАЛДАА: {type(e).__name__}: {e}")
                break

    asyncio.run(_test())
