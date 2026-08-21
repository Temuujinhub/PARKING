"""Камерын дотоод event санг (TrafficSnapEventInfo) унших — метадата backfill/аудит.

Dahua ITC камер SD картгүй ч event бүрийн метадатаг дотоод санд хадгалдаг
(RecNo-той цагираг сан). Вэб UI-ийн "Snapshot Records" хуудас яг үүнийг
харуулдаг. Урсгал (вэб UI-ийн EUSO модулиас тайлсан, 2026-08-09):

    RecordFinder.factory.create {name: "TrafficSnapEventInfo"}
    RecordFinder.startFind {condition: {Time: ["<>", эхлэл, төгсгөл]}}   # UTC epoch сек
    RecordFinder.doFind {count: N}  → params.records (params.infos БИШ!)
    RecordFinder.stopFind / destroy

Бичлэгийн талбарууд: Time(UTC epoch), PlateNumber(кирилл, "Unlicensed"=уншаагүй),
Event(34=TrafficTollGate гарц, 201=ManualSnap, 62/63=зогсоол эзэлсэн/суларсан),
SnapSource(Video/Manual/Force), Category, VehicleSign(марк), VehicleColor,
PlateColor, Lane, JunctionDirection, RecNo, SubscribeIP.

Зураг энд БАЙХГЫЙ — SD-гүй камер зургийг зөвхөн event-ийн агшинд шууд
илгээдэг (snap_puller-ийн WS суваг). Энэ модуль нь сервер унтарсан/алдсан
үеийн event-ийг НӨХӨЖ тулгахад (аудит) зориулагдсан.
"""
import asyncio
from datetime import datetime, timedelta, timezone

import httpx

from ..config import settings
from .barrier import DahuaRpc, DahuaRpcError


def _cam_offset_sec() -> int:
    return int(settings.camera_tz_offset_hours) * 3600


def to_camera_epoch(dt_utc: datetime) -> int:
    """UTC → камерын ЛОКАЛ epoch.

    RecordFinder-ийн `Time` талбар нь нэрнээсээ үл хамааран төхөөрөмжийн
    ЛОКАЛ цагийг epoch болгосон утга (Dahua-ийн live event-д ч мөн адил:
    `UTC` талбар=локал, `RealUTC`=жинхэнэ UTC). Батлагдсан (2026-08-22,
    10.0.106.12): камерын бичлэг Time→«UTC» 08-20 18:59:41 гэж уншигдсан
    машин ҮНЭНДЭЭ УБ 18:59:47-д амьд event-ээр ирсэн — өөрөөр хэлбэл Time нь
    УБ локал цаг байв.
    """
    return int(dt_utc.timestamp()) + _cam_offset_sec()


def from_camera_epoch(t) -> datetime:
    """Камерын локал epoch → жинхэнэ UTC (naive)."""
    return (datetime.fromtimestamp(t - _cam_offset_sec(), tz=timezone.utc)
            .replace(tzinfo=None))

EVENT_NAMES = {
    34: "gate_pass",        # TrafficTollGate — хаалтын гарцаар машин өнгөрсөн
    201: "manual_snap",     # оператор гараар зураг авсан
    62: "space_occupied",   # зогсоолын нүд эзэлсэн (ParkingDetector)
    63: "space_available",  # зогсоолын нүд суларсан
    160: "city_parking",
    1: "cross_region",
    6: "wander",
}

FINDER_NAME = "TrafficSnapEventInfo"
BATCH = 16          # вэб UI ижил хэмжээгээр татдаг
MAX_RECORDS = 5000  # хамгаалалт: цагираг сан том байж болно


async def fetch_snap_events(ip: str, username: str, password: str,
                            start_utc: datetime, end_utc: datetime,
                            plate: str | None = None,
                            client: httpx.AsyncClient | None = None) -> list[dict]:
    """Камерын event бичлэгүүдийг [start_utc, end_utc] мужид уншина.

    plate өгвөл камер талдаа "*plate*" wildcard-аар шүүнэ (вэб UI-ийн адил).
    Буцаах утга: түүхий record dict-үүд + нэмэлт "event_name"/"time_utc" талбар.
    """
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=20)
    try:
        rpc = DahuaRpc(client, ip, username, password)
        await rpc.login()
        try:
            return await _fetch_with_rpc(rpc, start_utc, end_utc, plate)
        finally:
            try:
                await rpc.logout()
            except Exception:
                pass
    finally:
        if own_client:
            await client.aclose()


async def _fetch_with_rpc(rpc: DahuaRpc, start_utc: datetime, end_utc: datetime,
                          plate: str | None) -> list[dict]:
    inst = await rpc._call("RecordFinder.factory.create", {"name": FINDER_NAME})
    obj = inst.get("result")
    if not obj:
        raise DahuaRpcError(f"RecordFinder.create бүтсэнгүй: {inst}")
    records: list[dict] = []
    try:
        cond: dict = {"Time": ["<>", to_camera_epoch(start_utc), to_camera_epoch(end_utc)]}
        if plate:
            cond["PlateNumber"] = f"*{plate}*"
        st = await rpc._call("RecordFinder.startFind", {"condition": cond}, obj=obj)
        if not st.get("result"):
            raise DahuaRpcError(f"startFind бүтсэнгүй: {st}")
        while len(records) < MAX_RECORDS:
            df = await rpc._call("RecordFinder.doFind", {"count": BATCH}, obj=obj)
            params = df.get("params") or {}
            batch = params.get("records") or []
            for rec in batch:
                t = rec.get("Time")
                rec["time_utc"] = (from_camera_epoch(t).strftime("%Y-%m-%d %H:%M:%S")
                                   if isinstance(t, (int, float)) else None)
                rec["event_name"] = EVENT_NAMES.get(rec.get("Event"),
                                                    str(rec.get("Event")))
                records.append(rec)
            if len(batch) < BATCH:
                break
    finally:
        try:
            await rpc._call("RecordFinder.stopFind", obj=obj)
            await rpc._call("RecordFinder.destroy", obj=obj)
        except Exception:
            pass
    return records


def normalized_plate(rec: dict) -> str | None:
    """Уншигдаагүй/хоосон дугаарыг None болгоно (тулгалтад ашиглахгүй)."""
    p = (rec.get("PlateNumber") or "").strip()
    if not p or p.lower() in ("unlicensed", "unknown", "none"):
        return None
    return p.upper().replace(" ", "")


def plates_similar(a: str, b: str) -> bool:
    """OCR-ийн 1 тэмдэгтийн зөрүүг илрүүлнэ (солигдсон/дутуу/илүү 1 тэмдэгт).

    Ж: 2420УХР ~ 2420УКР (Х↔К), 220УХР ~ 2420УХР (нэг орон дутуу уншсан)."""
    if not a or not b or a == b:
        return False
    la, lb = len(a), len(b)
    if abs(la - lb) > 1 or min(la, lb) < 4:
        return False
    if la == lb:
        return sum(1 for x, y in zip(a, b) if x != y) == 1
    if la > lb:
        a, b, la, lb = b, a, lb, la
    # a богино: нэг тэмдэгт алгасаад тэнцэх эсэх
    i = 0
    while i < la and a[i] == b[i]:
        i += 1
    return a[i:] == b[i + 1:]


# ─── Аудитын нэгдсэн таталт (сайтын бүх камер, TTL кэштэй) ──────────────────
# Шалгах хуудас WS event болгонд дахин ачаалдаг тул камер руу байнга хандахгүйн
# тулд сайт бүрд AUDIT_CACHE_SEC хугацаанд кэшлэнэ.
AUDIT_CACHE_SEC = 60.0
AUDIT_HOURS = 48.0
_audit_cache: dict[tuple, tuple[float, dict]] = {}


def site_camera_events(db, site_id: str, hours: float = AUDIT_HOURS) -> dict:
    """Зогсоолын бүх идэвхтэй камерын дотоод логийг зэрэг татаж нэгтгэнэ (sync).

    Буцаах: {window_hours, cameras:[{name,ip,lane_dir,events,error}],
             events:[{plate, raw_plate, time(UTC naive), lane_dir, event, camera}]}
    Камер тус бүрийн алдаа тусдаа бичигдэнэ — нэг камер унасан ч бусад нь тулгагдана.
    """
    import time as _time
    # Кэшийн түлхүүрт `hours`-ийг ЗААВАЛ оруулна — эс бол өөр цонх (48ц vs 72ц)
    # асуусан хэрэгслүүд бие биенийхээ кэшийг авч, харилцан адилгүй хариу
    # буцаадаг байв (2026-08-17: parked_audit 48ц кэшилсэн дараа exit_reconcile
    # 72ц түүнийг авч, гарах уншилт «0» гэж гарсан).
    ckey = (site_id, round(float(hours), 1))
    cached = _audit_cache.get(ckey)
    if cached and _time.monotonic() - cached[0] < AUDIT_CACHE_SEC:
        return cached[1]

    from ..models import Device
    from .device_auth import camera_credentials
    cams = (db.query(Device)
            .filter(Device.site_id == site_id, Device.device_type == "camera",
                    Device.status == "active", Device.ip_address != "")
            .all())
    # creds-ийг db session амьд байхад энгийн мөр болгож шийднэ
    targets = [(c.name or c.ip_address, c.ip_address, c.lane_dir or "entry",
                camera_credentials(c), bool(c.nested_inner)) for c in cams]

    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=hours)

    async def _one(name, ip, lane_dir, creds, inner):
        try:
            recs = await asyncio.wait_for(
                fetch_snap_events(ip, creds[0], creds[1], start, end), timeout=15)
            return name, ip, lane_dir, recs, None, inner
        except Exception as e:  # noqa: BLE001
            return name, ip, lane_dir, [], f"{type(e).__name__}: {str(e)[:120]}", inner

    async def _all():
        return await asyncio.gather(*(_one(*t) for t in targets))

    results = asyncio.run(_all()) if targets else []

    cameras, events, inner_events = [], [], []
    for name, ip, lane_dir, recs, err, inner in results:
        cameras.append({"name": name, "ip": ip, "lane_dir": lane_dir,
                        "events": len(recs), "error": err, "nested_inner": inner})
        for r in recs:
            t = r.get("Time")
            if not isinstance(t, (int, float)):
                continue
            ev = {
                "plate": normalized_plate(r),
                "raw_plate": r.get("PlateNumber"),
                "time": from_camera_epoch(t),
                "lane_dir": lane_dir,
                "event": r.get("event_name"),
                "source": r.get("SnapSource"),
                "camera": name,
                "nested_inner": inner,
            }
            # ДОТООД (дамжин) хаалтны уншилтыг `events`-т ОРУУЛАХГҮЙ. Дуудагчид
            # бүгд «entry = зогсоолд орлоо, exit = зогсоолоос гарлаа» гэж үздэг:
            #   • camera_sync — дотоод орох уншилтаар ШИНЭ session үүсгэж,
            #     дотоод гарах уншилтаар ГАДНА session-ийг «гарсан» гэж хаадаг
            #     байв. Машин шороон зогсоол руу орж байхад «зогсоолоос гарлаа»
            #     гэж бүртгэгдээд, жинхэнэ гарцад нь «бүртгэлгүй» болдог.
            #   • /audit ба exit_reconcile — мөн адил «гарсан нь тогтоогдлоо» гэнэ.
            # Дотоод уншилт нь ТӨЛБӨРИЙН ТООЛУУР зогсоох/үргэлжлүүлэх утгатай
            # болохоос зогсолт нээх/хаах утгагүй тул тусад нь буцаана.
            (inner_events if inner else events).append(ev)
    out = {"window_hours": hours, "cameras": cameras, "events": events,
           "inner_events": inner_events}
    _audit_cache[ckey] = (_time.monotonic(), out)
    return out
