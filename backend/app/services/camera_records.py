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
from datetime import datetime, timezone

import httpx

from .barrier import DahuaRpc, DahuaRpcError

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
        cond: dict = {"Time": ["<>", int(start_utc.timestamp()), int(end_utc.timestamp())]}
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
                rec["time_utc"] = (datetime.fromtimestamp(t, tz=timezone.utc)
                                   .strftime("%Y-%m-%d %H:%M:%S")
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
