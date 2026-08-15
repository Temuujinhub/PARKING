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
import logging
import re
import sys
import time
from datetime import datetime, timedelta

import httpx

from ..config import settings
from .device_auth import camera_credentials
from ..database import SessionLocal
from ..models import Device, ParkingSession
from .barrier import DahuaRpc

log = logging.getLogger("parking.snap_puller")

_tasks: dict[str, asyncio.Task] = {}

# ip → сүүлд WS-ээр БОДИТ ЗУРАГ ирсэн цаг (time.monotonic). snapshot.py үүгээр
# «энэ камер event зургаа WS-ээр өгдөг тул хүлээх үү, шууд snapshot.cgi руу орох
# уу» гэдгээ шийднэ. Зориуд frame/subscribe биш ЗУРГААР хэмждэг: одоогийн ITC
# firmware subscribe-ийг зөвшөөрөөд зураг огт өгдөггүй (2026-07-25) — түүн дээр
# энэ хэзээ ч true болохгүй тул одоо ажиллаж буй snapshot.cgi зам огт өөрчлөгдөхгүй.
_last_pic: dict[str, float] = {}


def puller_delivers(ip: str, max_age_sec: float = 1800.0) -> bool:
    """Тухайн камер сүүлийн max_age_sec (30 мин)-д WS-ээр зураг өгсөн эсэх."""
    if not settings.snap_pull or not ip:
        return False
    ts = _last_pic.get(ip)
    return ts is not None and (time.monotonic() - ts) <= max_age_sec

_PLATE_JSON_RE = re.compile(r'"PlateNumber"\s*:\s*"([^"]+)"')

# Firmware бүр filter-ийн өөр хэлбэр хүлээдэг — амжилттай болтол дарааллаар оролдоно.
# PRODUCTION ДЭЭР БАТЛАГДСАН (2026-07-23, ITC ANPR Web 5.0): Channels заавал [1]
# (0-ээр 268959743 өгдөг) — тиймээс ялсан хувилбар эхэндээ.
ATTACH_FILTERS = [
    # Бүрэн хэлбэр эхэндээ: Transfer:["Realtime"] байхгүй бол attach амжилттай
    # болсон ч камер зургаа бодит цагт илгээдэггүй байж болзошгүй
    {"Channels": [1], "Events": ["All"], "NeedData": True, "Flags": ["Event", "Manual"],
     "Internal": 1, "OfflineParam": {"ClientIP": "", "ClientID": ""},
     "Support": ["Ack"], "Transfer": ["Realtime"]},
    {"Channels": [1], "Events": ["All"], "NeedData": True, "Flags": ["Event", "Manual"]},
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

async def _attach_to_session(device_id: str, plate: str, lane_dir: str, data: bytes,
                             src: str = "ws"):
    """Зургийг хадгалаад тухайн дугаарын хамгийн сүүлийн session-д холбоно.
    Event боловсруулалт (cgi_poller) зургаас хоцорч болзошгүй тул хэдэнтээ оролдоно."""
    from ..session_logic import normalize_plate
    from .snapshot import _save, note_source
    plate_n = normalize_plate(plate) or plate.strip().upper()
    # Дискний бичилт thread дээр — event loop блоклохгүй (хаалт нээх хугацаанд нөлөөлнө)
    rel = await asyncio.to_thread(_save, data, plate_n, lane_dir)
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
                note_source(src)
                log.info(f"{plate_n} {lane_dir}: OK ({src}, {len(data)}b) → {rel}")
                return
        except Exception as e:
            log.error(f"{plate_n}: session холбох алдаа: {e}")
        finally:
            db.close()
        await asyncio.sleep(1.5)
    log.warning(f"{plate_n} {lane_dir}: session олдсонгүй, файл {rel} хадгалагдав")


# ─── Лайв WS стрим ───────────────────────────────────────────────────────────

class AttachRejected(RuntimeError):
    """Камер энэ filter хувилбарыг гологдуулав — дараагийн хувилбарыг шинэ
    холболт дээр туршина (нэг холболтод эхний алдааны дараа камер дүлийрдэг)."""


async def _ws_session(ip: str, on_picture, flt: dict, test_mode: bool = False,
                      creds: tuple[str, str] | None = None):
    """Нэг WS холболтын амьдрал: login → detach(хуучин) → attach → notification.
    on_picture(plate, jpeg_bytes) — plate-тай бүрэн jpeg бүрд дуудагдана."""
    import websockets

    username, password = creds or camera_credentials(None)
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
                log.info(f"{ip}: WS зургийн суваг ХОЛБОГДЛОО (subscribe OK, "
                         f"filter={flt.get('Flags')}/{flt.get('Events')})")

                # Дугааргүй notification-д хамгийн сүүлийн дугаарыг оноох (event-ийн
                # зургууд хэдэн секундын дотор цувж ирдэг)
                last_plate: str | None = None
                last_plate_ts = 0.0
                last_ka = time.monotonic()
                seen_methods: set[str] = set()  # оношилгоо: анх ирсэн method бүрийг логлоно
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
                    if not payload:
                        continue
                    # Оношилгоо: энэ холболтод анх удаа ирж буй notification method
                    # бүрийг нэг удаа логлоно (юу ирж байгааг харахад)
                    m = payload.get("method")
                    if m and m not in seen_methods:
                        seen_methods.add(m)
                        log.info(f"{ip}: notification «{m}» ирж эхлэв "
                                 f"(bin={len(binary)}b, эхлэл={binary[:4].hex() if binary else '-'})")
                    if m != "client.notifySnapFile":
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


# ─── Лайв COMET стрим (HTTP) ─────────────────────────────────────────────────
#
# WS-ийн оронд камерын вэб UI-ийн ХОЁРДУГААР зам. 2026-08-14-нд production
# дээр ажилласан цорын ганц зургийн суваг (docs/CAMERA_SNAPSHOT_FINDINGS.md):
#
#   1. RPC2 global.login              → 32 hex сешн (шифрлэлт хэрэггүй)
#   2. GET /SubscribeNotify.cgi?sessionId=<сешн>&type=1   (хоолой нээлттэй)
#   3. snapManager.factory.instance → object
#      snapManager.attachFileProc {"filter": {...}, "proc": 1}
#   4. Хоолойгоор <script>var json={…};receiveMessage(json);</script> ирнэ;
#      method="client.notifySnapFile", params.Base64 = JPEG
#
# `filter`/`proc` гэсэн ТҮЛХҮҮРИЙН НЭРС нь камерын вэб UI-ийн JS-ээс авсан —
# `condition`/`Types`/`Flags` гэсэн таамаг 20 удаа -267976701 өгсөн.
COMET_FILTERS = [
    {"Channels": [0], "Types": ["jpg"]},   # production дээр АЖИЛЛАСАН
    {"Channels": [1], "Types": ["jpg"]},   # зарим firmware 1-ээс тоолдог
    {"Channels": [0]},
]

_COMET_MARK = "var json="
# Хоолойг хэсэгчлэн уншихад ашиглах алхам (секунд). Чимээгүй байдлыг ЭНЭ
# нарийвчлалаар хэмжинэ — хэт богино байх нь илүүц сэрэлт, хэт урт нь удаан
# оношилгоо гэсэн үг.
_COMET_POLL_SEC = 20.0

# ip → comet сувгийн төлөв. 2026-08-15-ны оношилгооны дараа нэмэгдсэн: суваг
# «attach хийгдсэн ч чимээгүй» байдалд орж, ямар ч алдаа өгөхгүй мөнхөд гацдаг
# байсныг барихад хэрэгтэй (доорх `_comet_session`-ы watchdog хэрэглэнэ).
#   ok_filter — тухайн камер дээр ЗУРАГ ӨГСӨН нь батлагдсан филтерийн дугаар.
#     Нэг удаа батлагдсаны дараа татгалзал гарсан ч ӨӨР филтер рүү ШИЛЖИХГҮЙ:
#     14:20-нд гарсан түр зуурын татгалзлын улмаас ажиллаж байсан суваг
#     орхигдож, зураг өгдөггүй Channels:[1] дээр 11 цаг гацсан явдал давтагдахгүй.
_comet_ok_filter: dict[str, int] = {}
_comet_state: dict[str, dict] = {}


def comet_state() -> dict:
    """Оношилгоонд: камер бүрийн comet сувгийн одоогийн байдал."""
    now = time.monotonic()
    out = {}
    for ip, st in _comet_state.items():
        out[ip] = {
            "filter_no": st.get("filter_no"),
            "proven_filter_no": (_comet_ok_filter[ip] + 1) if ip in _comet_ok_filter else None,
            "attached_sec": round(now - st["attached"]) if st.get("attached") else None,
            "pics": st.get("pics", 0),
            "last_pic_sec": round(now - st["last_pic"]) if st.get("last_pic") else None,
            "reconnects": st.get("reconnects", 0),
            "last_error": st.get("last_error"),
        }
    return out


class CometSilent(RuntimeError):
    """Attach амжилттай боловч суваг зураг өгөхгүй чимээгүй байна — филтер
    буруу байх магадлалтай тул дараагийнхыг туршина."""


def _comet_messages(buf: str):
    """Comet буферээс БҮРЭН JSON мессежүүдийг гаргаж, үлдсэн хэсгийг буцаана.

    → (мессежийн жагсаалт, боловсруулаагүй үлдэгдэл). Дутуу ирсэн мессежийг
    буферт үлдээж дараагийн чанк хүлээнэ (нэг зураг ~950KB тул мессеж олон
    TCP чанкаар ирдэг)."""
    out, pos = [], 0
    while True:
        i = buf.find(_COMET_MARK, pos)
        if i < 0:
            break
        start = buf.find("{", i)
        if start < 0:
            break
        end = _match_brace(buf, start)
        if end < 0:            # мессеж бүрэн ирээгүй — эндээс цааш хүлээнэ
            return out, buf[i:]
        out.append(buf[start:end + 1])
        pos = end + 1
    # Сүүлийн бүрэн мессежийн ард үлдсэн хэсгийг л барина
    return out, buf[pos:] if pos else buf


def _match_brace(s: str, start: int) -> int:
    """`s[start]` дэх `{`-ийн хос `}`. Мөрийн дотор `{`/`}` таарч болох тул
    хашилт ба escape-ыг тооцно (улсын дугаар, хаяганд юу ч орж болно)."""
    depth, i, in_str, esc = 0, start, False, False
    while i < len(s):
        ch = s[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _plate_from_snap(params: dict) -> str:
    """notifySnapFile мессежээс улсын дугаарыг гаргана."""
    try:
        ev = (params.get("info") or {}).get("Events") or []
        car = ((ev[0] or {}).get("Data") or {}).get("TrafficCar") or {}
        return (car.get("PlateNumber") or "").strip()
    except Exception:  # noqa: BLE001
        return ""


async def _comet_keepalive(rpc, ip: str, dead: dict):
    """RPC2 сешнийг амьд байлгана (WS зам ижил зүйлийг 25с тутам хийдэг).

    Хоёр үүрэгтэй: (1) сешн хугацаагаар хөрөхөөс сэргийлнэ, (2) сешн ҮХСЭН
    эсэхийг ХЭМЖИНЭ — машин ирэхгүй чимээгүй шөнө ба суваг үхсэн байдлыг
    ялгах цорын ганц хямд арга. Алдаа гармагц `dead`-д шалтгааныг бичихэд
    үндсэн давталт холболтоо тасалж дахин холбоно."""
    from .barrier import _rpc_lock, note_rpc_done, wait_rpc_gap
    while True:
        await asyncio.sleep(settings.snap_comet_keepalive_sec)
        try:
            async with _rpc_lock(ip):
                await wait_rpc_gap(ip)
                res = await rpc._call("global.keepAlive",
                                      {"timeout": 300, "active": True})
                note_rpc_done(ip)
            if not res.get("result"):
                dead["why"] = f"keepAlive: {json.dumps(res.get('error') or {})[:70]}"
                return
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            dead["why"] = f"keepAlive {type(e).__name__}: {str(e)[:70]}"
            return


async def _comet_session(ip: str, on_picture, flt: dict,
                         creds: tuple[str, str] | None = None,
                         filter_no: int = 1):
    """Нэг comet холболтын амьдрал: login → хоолой нээх → attach → зураг.

    `on_picture(plate, jpeg)` бүрэн JPEG бүрд дуудагдана."""
    import base64 as _b64
    from .barrier import _rpc_lock, note_rpc_done, wait_rpc_gap

    username, password = creds or camera_credentials(None)
    # read=None — comet хоолой чимээгүй байх нь ХЭВИЙН (event хүртэл хүлээнэ)
    timeout = httpx.Timeout(connect=10.0, read=None, write=10.0, pool=10.0)
    # ЗОРИУД тусдаа клиент: barrier-ийн хуваалцсан клиентийн холболтын санг
    # байнгын урсгалаар эзэлбэл хаалтны команд хүлээгдэнэ
    async with httpx.AsyncClient(timeout=timeout) as hc:
        rpc = DahuaRpc(hc, ip, username, password)
        async with _rpc_lock(ip):
            await wait_rpc_gap(ip)
            await rpc.login()
            note_rpc_done(ip)
        sid = rpc.session_id
        url = f"http://{ip}/SubscribeNotify.cgi?sessionId={sid}&type=1"
        headers = {"Cookie": f"WebClientHttpSessionID={sid}",
                   "Referer": f"http://{ip}/"}

        async with hc.stream("GET", url, headers=headers) as r:
            if r.status_code != 200:
                raise RuntimeError(f"SubscribeNotify HTTP {r.status_code}")

            # Хоолой нээгдсэний ДАРАА захиална — эсрэгээр хийвэл эхний
            # notification алдагдана
            async with _rpc_lock(ip):
                await wait_rpc_gap(ip)
                inst = await rpc._call("snapManager.factory.instance")
                obj = inst.get("result")
                res = await rpc._call("snapManager.attachFileProc",
                                      {"filter": flt, "proc": 1}, obj=obj)
                note_rpc_done(ip)
            if not res.get("result"):
                raise AttachRejected(
                    json.dumps(res.get("error") or res, ensure_ascii=False)[:90])

            st = _comet_state.setdefault(ip, {})
            st.update(attached=time.monotonic(), filter_no=filter_no, pics=0,
                      last_error=None)
            buf, parts = "", {}
            dead: dict[str, str] = {}
            ka = asyncio.create_task(_comet_keepalive(rpc, ip, dead))
            # Урсгалыг ХЭСГЭЭР нь уншина — `async for` нь хугацааны хязгааргүй
            # тул хоолой чимээгүй болоход мөнхөд гацдаг байв (2026-08-15).
            it = r.aiter_bytes()
            silent = 0.0
            try:
              while True:
                try:
                    chunk = await asyncio.wait_for(it.__anext__(), timeout=_COMET_POLL_SEC)
                except StopAsyncIteration:
                    raise RuntimeError("хоолой хаагдав")
                except asyncio.TimeoutError:
                    silent += _COMET_POLL_SEC
                    if dead.get("why"):
                        raise RuntimeError(dead["why"])
                    # Зураг ОГТ өгөөгүй суваг — филтер буруу байх магадлалтай
                    if not st["pics"] and silent >= settings.snap_comet_probe_sec:
                        raise CometSilent(f"{silent:.0f}с зураг ирсэнгүй")
                    # Ажиллаж байсан суваг ч удаан чимээгүй байвал сэргээнэ
                    if silent >= settings.snap_comet_idle_sec:
                        raise RuntimeError(f"{silent:.0f}с чимээгүй — сэргээнэ")
                    continue
                silent = 0.0
                if dead.get("why"):
                    raise RuntimeError(dead["why"])
                buf += chunk.decode("utf-8", "replace")
                msgs, buf = _comet_messages(buf)
                for raw in msgs:
                    try:
                        obj_ = json.loads(raw)
                    except Exception:  # noqa: BLE001
                        continue
                    if obj_.get("method") != "client.notifySnapFile":
                        continue
                    params = obj_.get("params") or {}
                    b64 = params.get("Base64")
                    if not isinstance(b64, str) or len(b64) < 512:
                        continue
                    try:
                        data = _b64.b64decode(b64 + "=" * (-len(b64) % 4))
                    except Exception:  # noqa: BLE001
                        continue
                    info = params.get("info") or {}
                    # Том зураг олон мессежээр ирж болно: PicID-гээр угсарна
                    key = json.dumps(info.get("PicID") or [])
                    parts[key] = parts.get(key, b"") + data
                    whole = parts[key]
                    if whole[:3] != b"\xff\xd8\xff":
                        parts.pop(key, None)      # JPEG биш — хаяна
                        continue
                    if whole[-2:] != b"\xff\xd9":
                        continue                  # үргэлжлэл хүлээнэ
                    parts.pop(key, None)
                    await on_picture(_plate_from_snap(params), whole)
                    # Вэб UI шиг хүлээж авсныг баталгаажуулна — эсрэгээр
                    # камер илгээхээ болих магадлалтай (JS: ackUpload)
                    pic_id = info.get("PicID") or []
                    try:
                        await rpc._call("snapManager.ackUpload",
                                        {"PicID": pic_id, "ClientID": "",
                                         "ClientIP": "WEB", "result": True})
                    except Exception:  # noqa: BLE001
                        pass
                if len(buf) > 8 * 1024 * 1024:
                    # Хог хуримтлагдвал (мессежийн тэмдэг олдохгүй) цэвэрлэнэ
                    log.warning("%s: comet буфер хэтэрлээ — цэвэрлэв", ip)
                    buf = ""
            finally:
                ka.cancel()


async def _comet_one(device_id: str, ip: str, lane_dir: str,
                     creds: tuple[str, str] | None = None,
                     start_delay: float = 0.0):
    """Нэг камерын comet зургийн сувгийг тасралтгүй барина (reconnect-тэй)."""
    best: dict[str, tuple[float, bytes]] = {}

    async def flush_stale(force: bool = False):
        now = time.monotonic()
        for plate in list(best):
            ts, data = best[plate]
            if force or now - ts > 2.5:
                del best[plate]
                asyncio.create_task(
                    _attach_to_session(device_id, plate, lane_dir, data, src="comet"))

    cur = {"idx": 0}

    async def on_picture(plate: str, data: bytes):
        _last_pic[ip] = time.monotonic()
        st = _comet_state.setdefault(ip, {})
        st["pics"] = st.get("pics", 0) + 1
        st["last_pic"] = time.monotonic()
        # Энэ филтер ЗУРАГ ӨГЧ БАЙНА — цаашид түүнээс салахгүй
        if _comet_ok_filter.get(ip) != cur["idx"]:
            _comet_ok_filter[ip] = cur["idx"]
            log.info("%s: comet filter #%d зураг өгч байна — цаашид түүнийг барина",
                     ip, cur["idx"] + 1)
        # snapshot.py-д «энэ камер зургаа стримээр өгдөг» гэж мэдэгдэнэ —
        # ингэснээр _capture_and_store нь snapshot.cgi рүү унахаа больж,
        # камер дээр илүүц «Manual Snapshot» бичлэг үүсэхээ болино
        from .snapshot import offer_stream_image
        offer_stream_image(ip, data, src="comet")
        if not plate:
            return          # дугааргүй зураг — стримийн санамжид л үлдэнэ
        ts, old = best.get(plate, (0.0, b""))
        best[plate] = (time.monotonic(), data if len(data) > len(old) else old)
        await flush_stale()

    # Бүх камер НЭГ агшинд login хийвэл камерын RPC үйлчилгээ ачаалагдаж
    # attachFileProc массаар татгалздаг (2026-08-14: 20 камерын 14 нь нэг
    # секундэд гологдов) — тиймээс эхлэлийг зориуд тараана.
    if start_delay:
        await asyncio.sleep(start_delay)

    vi = 0
    while True:
        # Батлагдсан филтер байвал ҮРГЭЛЖ түүгээр — татгалзал нь түр зуурын
        # ачаалал байж болох тул зураг өгдөггүй хувилбар руу шилжихгүй
        proven = _comet_ok_filter.get(ip)
        idx = proven if proven is not None else vi % len(COMET_FILTERS)
        cur["idx"] = idx
        st = _comet_state.setdefault(ip, {})
        try:
            await _comet_session(ip, on_picture, COMET_FILTERS[idx],
                                 creds=creds, filter_no=idx + 1)
        except AttachRejected as e:
            st["last_error"] = f"attach: {e}"
            if proven is None:
                vi += 1
                log.warning("%s: comet filter #%d гологдов (%s) — дараагийнх 10с дараа",
                            ip, idx + 1, e)
            else:
                log.warning("%s: comet filter #%d (батлагдсан) түр гологдов (%s) "
                            "— 10с дараа ДАХИН түүгээр", ip, idx + 1, e)
            await flush_stale(force=True)
            await asyncio.sleep(10)
            continue
        except CometSilent as e:
            st["last_error"] = f"чимээгүй: {e}"
            if proven is None:
                vi += 1
                log.warning("%s: comet filter #%d attach хийгдсэн ч %s — дараагийнхыг "
                            "туршина", ip, idx + 1, e)
            else:
                log.warning("%s: comet filter #%d %s — дахин холбоно", ip, idx + 1, e)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            st["last_error"] = f"{type(e).__name__}: {str(e)[:70]}"
            log.warning("%s: comet тасарлаа (%s: %s) — 15с дараа дахин",
                        ip, type(e).__name__, str(e)[:110])
        st["reconnects"] = st.get("reconnects", 0) + 1
        st["attached"] = None
        await flush_stale(force=True)
        await asyncio.sleep(15)


def comet_enabled_for(ip: str) -> bool:
    """Тухайн камер дээр comet суваг асаалттай эсэх (аажим нэвтрүүлэлт)."""
    if not settings.snap_comet or not ip:
        return False
    allow = [s.strip() for s in (settings.snap_comet_ips or "").split(",") if s.strip()]
    return not allow or ip in allow


async def _pull_one(device_id: str, ip: str, lane_dir: str,
                    creds: tuple[str, str] | None = None,
                    start_delay: float = 0.0):
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
        _last_pic[ip] = time.monotonic()  # энэ камер зургаа WS-ээр өгдөг нь батлагдлаа
        ts, old = best.get(plate, (0.0, b""))
        best[plate] = (time.monotonic(), data if len(data) > len(old) else old)
        await flush_stale()

    if start_delay:
        await asyncio.sleep(start_delay)

    vi = 0  # амжилттай болсон filter хувилбар дээрээ тогтоно
    while True:
        flt = ATTACH_FILTERS[vi % len(ATTACH_FILTERS)]
        try:
            await _ws_session(ip, on_picture, flt, creds=creds)
        except AttachRejected as e:
            log.warning(f"{ip}: filter #{vi % len(ATTACH_FILTERS) + 1} гологдов ({e}) — "
                        f"дараагийн хувилбар 10с дараа")
            vi += 1
            await flush_stale(force=True)
            await asyncio.sleep(10)
            continue
        except Exception as e:
            log.warning(f"{ip}: WS тасарлаа ({type(e).__name__}: {str(e)[:120]}) — 15с дараа дахин")
        await flush_stale(force=True)
        await asyncio.sleep(15)


async def supervisor():
    """Идэвхтэй камер бүрд зургийн task ажиллуулна (cgi_poller-тай ижил хэв маяг).

    Хоёр суваг: WS (`snap_pull`) ба comet (`snap_comet`). Comet нь production
    дээр батлагдсан тул шинэ зогсоолд түүнийг ашиглана; хоёулаа асаалттай
    байвал камер тус бүрд НЭГИЙГ нь л ажиллуулна (илүү холболт эзлэхгүй)."""
    if not settings.snap_pull and not settings.snap_comet:
        return
    log.info("идэвхжлээ — камеруудаас event зураг татаж эхэлж байна (WS=%s, comet=%s%s)",
             settings.snap_pull, settings.snap_comet,
             f" [{settings.snap_comet_ips}]" if settings.snap_comet_ips else "")
    while True:
        db = SessionLocal()
        try:
            cams = db.query(Device).filter(
                Device.device_type == "camera", Device.status == "active",
                Device.ip_address.isnot(None), Device.ip_address != "",
            ).all()
            active = set()
            started = 0        # энэ эргэлтэд шинээр асаасан сувгийн тоо
            for c in cams:
                # Comet нь батлагдсан зам тул түүнийг эхэнд тавина; аль нэгийг
                # л ажиллуулна — хоёр байнгын холболт нээвэл камерын зэрэгцээ
                # холболтын хязгаар дүүрч хаалтны команд хүлээгддэг
                use_comet = comet_enabled_for(c.ip_address)
                if not use_comet and not settings.snap_pull:
                    continue
                active.add(c.id)
                if c.id not in _tasks or _tasks[c.id].done():
                    runner = _comet_one if use_comet else _pull_one
                    delay = started * settings.snap_comet_start_stagger_sec
                    started += 1
                    _tasks[c.id] = asyncio.create_task(
                        runner(c.id, c.ip_address, c.lane_dir or "entry",
                               camera_credentials(c), start_delay=delay))
                    log.info("%s (%s) зургийн стрим эхэллээ — %s (+%.0fс)", c.name,
                             c.ip_address, "comet" if use_comet else "WS", delay)
            for did in list(_tasks):
                if did not in active:
                    _tasks[did].cancel()
                    del _tasks[did]
        except Exception as e:
            log.error(f"supervisor алдаа: {e}")
        finally:
            db.close()
        await asyncio.sleep(60)


# ─── Нөхөн таталт: ОЛОН АРГААР камерын хадгалсан зургийг татах ────────────────
#
# Нэг арга (mediaFileFind) найдваргүй байсан тул 3 бие даасан аргыг дараалан
# оролдоно. Эхнийх нь амжилттай болонгуут зогсоно, бүх аргын оношийг цуглуулна:
#   1. RecordFinder(TrafficSnapEventInfo) — ANPR event-д ШУУД холбогдсон зураг
#      (дугаарын event-ийн бичлэгээс замыг авдаг тул хамгийн зөв эх сурвалж)
#   2. mediaFileFind — цагийн мужийн хадгалсан jpg файлуудыг жагсааж татна
#   3. snapshot.cgi — камерын ОДООГИЙН амьд кадр (event зураг огт олдоогүйн эцсийн арга)
#
# Цаг/бүсийн тохиргоо буруу байх эргэлзээг даван туулахын тулд:
#   • хайлтын цонхыг аажим өргөтгөнө (window → ×5 → ×20)
#   • тохируулсан бүсийн зөрүү (tz_offset_hours)-г БОЛОН 0-г хоёуланг оролдож,
#     олдсон файлуудаас target цагт хамгийн ОЙРхныг сонгоно.

_FMT = "%Y-%m-%d %H:%M:%S"


def _extract_paths(node) -> list[str]:
    """Record/file info дотроос зургийн зам төстэй мөр бүрийг рекурсивээр цуглуулна.
    Firmware бүр талбараа өөр нэрлэдэг (FilePath / ImageURL / PicPath …) тул
    бүтцээс биш агуулгаас («/…\\.jpg») хайна."""
    out: list[str] = []
    if isinstance(node, str):
        s = node.strip()
        low = s.lower()
        if s.startswith("/") and (".jpg" in low or ".jpeg" in low):
            out.append(s)
    elif isinstance(node, dict):
        for v in node.values():
            out.extend(_extract_paths(v))
    elif isinstance(node, (list, tuple)):
        for v in node:
            out.extend(_extract_paths(v))
    return out


def _info_time(info: dict) -> datetime | None:
    """Бичлэгээс цаг талбарыг олж datetime болгоно (ойрын сонголтод хэрэглэнэ)."""
    if not isinstance(info, dict):
        return None
    for k in ("StartTime", "Time", "BeginTime", "CreateTime"):
        v = info.get(k)
        if isinstance(v, list) and v:
            v = v[0]
        if isinstance(v, str) and len(v) >= 19:
            try:
                return datetime.strptime(v[:19], _FMT)
            except ValueError:
                continue
    return None


async def _download_file(client: httpx.AsyncClient, ip: str, session_id, path: str) -> bytes | None:
    """RPC_Loadfile-ээр нэг файлыг татна (боломжит хоёр URL хэлбэрийг оролдоно)."""
    headers = {"Cookie": f"WebClientHttpSessionID={session_id}",
               "x-api-session": str(session_id)}
    for url in (f"http://{ip}/RPC_Loadfile{path}",
                f"http://{ip}/cgi-bin/RPC_Loadfile{path}"):
        try:
            r = await client.get(url, headers=headers)
        except Exception:
            continue
        if r.status_code == 200 and r.content[:2] == b"\xff\xd8":
            return r.content
    return None


async def _pick_and_download(client: httpx.AsyncClient, ip: str, session_id,
                             infos: list, target: datetime) -> bytes | None:
    """Олдсон бичлэгүүдээс target цагт хамгийн ОЙРыг (цаггүй бол хамгийн ТОМыг)
    эрэмбэлж, эхний бүтэн jpg татагдтал дараалан оролдоно."""
    def rank(info):
        t = _info_time(info)
        if t is not None:
            return (0, abs((t - target).total_seconds()))
        return (1, -int((info or {}).get("Length") or 0))

    for info in sorted(infos, key=rank):
        for path in _extract_paths(info):
            data = await _download_file(client, ip, session_id, path)
            if data:
                return data
    return None


async def _find_via_record(rpc: DahuaRpc, client: httpx.AsyncClient, ip: str,
                           start: datetime, end: datetime, target: datetime) -> tuple[bytes | None, str]:
    """Арга #1 — RecordFinder(TrafficSnapEventInfo): ANPR event бичлэгээс шууд зураг."""
    inst = await rpc._call("RecordFinder.factory.create", {"name": "TrafficSnapEventInfo"})
    obj = inst.get("result")
    if not obj:
        return None, f"factory.create: {json.dumps(inst, ensure_ascii=False)[:70]}"
    try:
        cond = {"StartTime": start.strftime(_FMT), "EndTime": end.strftime(_FMT), "Order": "Ascent"}
        started = await rpc._call("RecordFinder.startFind", {"condition": cond}, obj=obj)
        if not started.get("result") and not (started.get("params") or {}).get("token"):
            # Зарим firmware Time массив хүлээдэг
            alt = {"Time": [start.strftime(_FMT), end.strftime(_FMT)]}
            started = await rpc._call("RecordFinder.startFind", {"condition": alt}, obj=obj)
        infos: list = []
        for _ in range(4):
            df = await rpc._call("RecordFinder.doFind", {"count": 100}, obj=obj)
            batch = (df.get("params") or {}).get("infos") or []
            if not batch:
                break
            infos.extend(batch)
            if len(batch) < 100:
                break
        if not infos:
            return None, "event бичлэг олдсонгүй"
        data = await _pick_and_download(client, ip, rpc.session_id, infos, target)
        return (data, "" if data else f"{len(infos)} event олдсон ч зураг татагдсангүй")
    finally:
        try:
            await rpc._call("RecordFinder.stopFind", obj=obj)
            await rpc._call("RecordFinder.destroy", obj=obj)
        except Exception:
            pass


async def _find_via_media(rpc: DahuaRpc, client: httpx.AsyncClient, ip: str,
                          start: datetime, end: datetime, target: datetime) -> tuple[bytes | None, str]:
    """Арга #2 — mediaFileFind: цагийн мужаар хадгалсан jpg файлуудыг жагсаана."""
    inst = await rpc._call("mediaFileFind.factory.create")
    obj = inst.get("result")
    if not obj:
        return None, f"factory.create: {json.dumps(inst, ensure_ascii=False)[:70]}"
    try:
        base_cond = {"StartTime": start.strftime(_FMT), "EndTime": end.strftime(_FMT)}
        infos: list = []
        # Firmware-ээс хамаарч нөхцөлийн хэлбэр ялгаатай — хувилбаруудыг дарааллаар
        for extra in ({"Channel": 0, "Types": ["jpg"], "Flags": ["Event"]},
                      {"Channel": 0, "Types": ["jpg"]},
                      {"Channel": 1, "Types": ["jpg"]},
                      {"Channel": 0}):
            ff = await rpc._call("mediaFileFind.findFile",
                                 {"condition": {**base_cond, **extra}}, obj=obj)
            if not ff.get("result"):
                continue
            nf = await rpc._call("mediaFileFind.findNextFile", {"count": 100}, obj=obj)
            infos = (nf.get("params") or {}).get("infos") or []
            if infos:
                break
        if not infos:
            return None, "файл олдсонгүй"
        data = await _pick_and_download(client, ip, rpc.session_id, infos, target)
        return (data, "" if data else f"{len(infos)} файл олдсон ч татагдсангүй")
    finally:
        try:
            await rpc._call("mediaFileFind.close", obj=obj)
            await rpc._call("mediaFileFind.destroy", obj=obj)
        except Exception:
            pass


async def fetch_stored_picture(ip: str, event_time_utc: datetime, *,
                               creds: tuple[str, str] | None = None,
                               tz_offset_hours: int = 8,
                               window_seconds: int = 180) -> tuple[bytes | None, str]:
    """event-ийн зургийг камераас ОЛОН АРГААР дараалан нөхөж татна.

    event_time_utc — session-ий орох/гарах цаг (DB-ийн UTC цагаар).
    Дотроо бүсийн зөрүү + өргөтгөх цонхыг өөрөө боддог тул дуудагч талд
    цагийн тооцоо хийх шаардлагагүй.

    Буцаах: (зураг|None, тайлбар). Амжилттай бол тайлбар хоосон; үгүй бол
    оролдсон бүх аргын товч оношийг агуулна."""
    # Бүсийн зөрүүг БОЛОН 0-г оролдоно (камерын цаг эсвэл тохиргоо буруу байж болзошгүй)
    offsets: list[int] = []
    for off in (tz_offset_hours, 0):
        if off not in offsets:
            offsets.append(off)
    windows = [window_seconds, window_seconds * 5, window_seconds * 20]
    diag: list[str] = []
    # RPC2 stored-find (RecordFinder/mediaFileFind) энэ firmware дээр ажилладаггүй бол
    # (default) алгасна — дэмий RPC2 login хийж admin эрх түгжихээс сэргийлж, шууд
    # snapshot.cgi (амьд кадр) рүү очно.
    if settings.snapshot_stored_find:
      try:
        async with httpx.AsyncClient(timeout=25) as client:
            rpc = DahuaRpc(client, ip, *(creds or camera_credentials(None)))
            await rpc.login()
            try:
                for w in windows:
                    for off in offsets:
                        target = event_time_utc + timedelta(hours=off)
                        start = target - timedelta(seconds=w)
                        end = target + timedelta(seconds=w)
                        for name, fn in (("record", _find_via_record),
                                         ("media", _find_via_media)):
                            try:
                                data, note = await fn(rpc, client, ip, start, end, target)
                            except Exception as e:
                                data, note = None, f"{type(e).__name__}: {str(e)[:60]}"
                            if data:
                                log.info(f"{ip}: нөхөн таталт OK — "
                                         f"{name} (off{off:+d}/±{w}s, {len(data)}b)")
                                return data, ""
                            diag.append(f"{name}[off{off:+d}/±{w}s]: {note}")
            finally:
                await rpc.logout()
      except Exception as e:
        diag.append(f"холболт: {type(e).__name__}: {str(e)[:80]}")

    # Амьд кадр — snapshot.cgi (энэ firmware дээр цорын ганц ажилладаг зургийн эх сурвалж)
    try:
        from .snapshot import _fetch_from_camera
        live = await _fetch_from_camera(ip, creds)
        if live:
            log.info(f"{ip}: нөхөн таталт — амьд кадраар нөхөв ({len(live)}b)")
            return live, ""
        diag.append("snapshot.cgi: амьд кадр татагдсангүй")
    except Exception as e:
        diag.append(f"snapshot.cgi: {type(e).__name__}")

    return None, " | ".join(diag[-6:]) or "камераас зураг олдсонгүй"


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

        only = int(sys.argv[2]) if len(sys.argv) > 2 else None  # зөвхөн N-р filter-ийг турших
        for i, flt in enumerate(ATTACH_FILTERS, 1):
            if only and i != only:
                continue
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
