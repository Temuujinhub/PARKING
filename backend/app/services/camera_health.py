"""Гацсан камерыг автоматаар илрүүлж, шаардвал reboot хийх (snapshot эрүүл мэнд).

Юуны учир (2026-08-10, батлагдсан): Dahua ITC камерын веб/зургийн дэд систем
хааяа ГАЦдаг — event стрим АМЬД (200, машин бүртгэгдсээр) атлаа snapshot.cgi
ШУУД (<0.2с) HTTP 400 «Bad Request!» буцаана. Хүлээгээд сэргэхгүй, тохиргоо ч
биш, сешн ч биш — REBOOT л засна (Рашбулаг 10.0.106.10 дээр батлагдсан:
reboot-ийн дараа snapshot.cgi 0/10 → 10/10).

Энэ нь `camera_recovery` (deadman)-аас ӨӨР: тэр нь event стрим БҮРЭН үхсэн
(last_seen хуучирсан) камерыг засдаг. Энд event АМЬД байхад snapshot гацсаныг
илрүүлнэ — deadman үүнийг хардаггүй.

Тохиргоо: Тохиргоо → Авто цэвэрлэгээ → «Камерын эрүүл мэнд» (app_settings).
Хамгаалалт: хаалт хүлээж буй үед reboot ХИЙХГҮЙ; камер тутамд cooldown;
зөвхөн ГАЦСАН (event 200 + snapshot шууд 400) гэж БАТЛАГДСАНЫГ л reboot хийнэ;
бүх reboot AuditLog + WARNING лог + кассын мэдэгдэлтэй.
"""
import asyncio
import logging
import time
from datetime import datetime

import httpx

from ..database import SessionLocal
from ..models import AuditLog, Device, ParkingSite
from ..ws import notify
from .app_settings import get_rules, get_state, set_state
from .camera_recovery import reboot_camera
from .device_auth import camera_credentials

log = logging.getLogger("parking.camera_health")

CAMHEALTH_KEY = "camhealth_rules"
CAMHEALTH_STATE = "camhealth_state"

SNAP_URLS = ("cgi-bin/snapshot.cgi", "cgi-bin/snapshot.cgi?channel=1",
             "cgi-bin/snapshot.cgi?channel=0")
INSTANT_400 = 0.2   # секунд — үүнээс хурдан 400 = «үүдэн дээр татгалзсан» = гацсан шинж

_last_reboot: dict[str, float] = {}   # ip -> monotonic


async def _snapshot_probe(c, ip, auth, samples: int) -> dict:
    ok, bad, lat_bad, jpeg_bytes = 0, 0, [], 0
    good_url = None
    for i in range(samples):
        for path in (SNAP_URLS if good_url is None else (good_url,)):
            t0 = time.monotonic()
            try:
                r = await c.get(f"http://{ip}/{path}", auth=auth,
                                timeout=httpx.Timeout(4, read=15))
                dt = time.monotonic() - t0
                if r.status_code == 200 and r.content[:2] == b"\xff\xd8":
                    ok += 1
                    jpeg_bytes = len(r.content)
                    good_url = path
                    break
                bad += 1
                lat_bad.append(dt)
            except Exception:  # noqa: BLE001
                bad += 1
                lat_bad.append(time.monotonic() - t0)
        if i < samples - 1:
            await asyncio.sleep(0.6)
    return {"ok": ok, "bad": bad, "jpeg_kb": jpeg_bytes // 1024,
            "min_bad_lat": min(lat_bad) if lat_bad else None}


async def _event_alive(c, ip, auth) -> bool | None:
    try:
        async with c.stream("GET", f"http://{ip}/cgi-bin/eventManager.cgi"
                                    f"?action=attach&codes=[All]&heartbeat=5",
                            auth=auth, timeout=httpx.Timeout(6, read=4)) as r:
            return r.status_code == 200
    except httpx.ReadTimeout:
        return True   # холбогдсон ч энэ агшинд event гараагүй — веб амьд
    except Exception:  # noqa: BLE001
        return None


def classify_verdict(snap_ok: bool, event_alive: bool | None,
                     min_bad_lat: float | None) -> str:
    """Цэвэр шийдэл (сүлжээгүй, тестлэх боломжтой):
      • healthy     — snapshot JPEG өгсөн
      • hung        — веб АМЬД (event 200) атлаа snapshot ШУУД (<0.2с) 400
      • unreachable — веб хариугүй (event ч 200 өгсөнгүй)
      • busy        — snapshot унасан ч ШУУД биш (ачаалал/давхцал байж болно)"""
    if snap_ok:
        return "healthy"
    if event_alive and min_bad_lat is not None and min_bad_lat < INSTANT_400:
        return "hung"
    if event_alive is None:
        return "unreachable"
    return "busy"


async def _classify(ip: str, name: str, creds: tuple[str, str], samples: int) -> dict:
    auth = httpx.DigestAuth(*creds)
    async with httpx.AsyncClient(timeout=20) as c:
        snap = await _snapshot_probe(c, ip, auth, samples)
        ev = None if snap["ok"] else await _event_alive(c, ip, auth)
        verdict = classify_verdict(bool(snap["ok"]), ev, snap["min_bad_lat"])
    return {"ip": ip, "name": name, "verdict": verdict, **snap}


async def _run_all(dry_run: bool, rules: dict) -> dict:
    """Бүх идэвхтэй камерыг шалгаж, ГАЦСАНЫГ (боломжтой бол) reboot хийнэ."""
    from .barrier import barrier_is_waiting

    db = SessionLocal()
    try:
        cams = (db.query(Device).join(ParkingSite, Device.site_id == ParkingSite.id)
                .filter(Device.device_type == "camera", Device.status == "active",
                        ParkingSite.is_active.is_(True),
                        Device.ip_address.isnot(None), Device.ip_address != "")
                .all())
        seen, targets = set(), []
        for c in cams:
            if c.ip_address in seen:
                continue
            seen.add(c.ip_address)
            targets.append((c.id, c.site_id, c.name, c.ip_address, camera_credentials(c)))
    finally:
        db.close()

    samples = max(1, int(rules.get("samples", 3)))
    sem = asyncio.Semaphore(6)   # камеруудыг зэрэг цохихгүй

    async def _one(name, ip, creds):
        async with sem:
            try:
                return await _classify(ip, name, creds, samples)
            except Exception as e:  # noqa: BLE001
                return {"ip": ip, "name": name, "verdict": "error",
                        "min_bad_lat": None, "note": str(e)[:120]}

    results = await asyncio.gather(*[_one(n, ip, cr)
                                     for _, _, n, ip, cr in targets])
    by_ip = {r["ip"]: r for r in results}

    hung = [r for r in results if r["verdict"] == "hung"]
    rebooted, skipped = [], []
    if rules.get("auto_reboot") and not dry_run:
        cool = max(1, int(rules.get("cooldown_min", 120))) * 60
        meta = {t[3]: t for t in targets}   # ip -> target tuple
        for r in hung:
            ip = r["ip"]
            # 1) Хаалт хүлээж буй бол reboot ХИЙХГҮЙ (машин хаалганы өмнө)
            if barrier_is_waiting(ip):
                skipped.append({"ip": ip, "why": "хаалт хүлээж байна"})
                continue
            # 2) Cooldown — reboot-ын шуурга гаргахгүй
            if time.monotonic() - _last_reboot.get(ip, 0.0) < cool:
                skipped.append({"ip": ip, "why": "cooldown"})
                continue
            _did, site_id, name, _, creds = meta[ip]
            err = await reboot_camera(ip, creds)
            _last_reboot[ip] = time.monotonic()
            db = SessionLocal()
            try:
                db.add(AuditLog(username="system", action="CAMERA_HEALTH_REBOOT",
                                entity="device", entity_id=ip,
                                detail={"name": name, "verdict": "hung",
                                        "min_bad_lat": r.get("min_bad_lat"),
                                        "error": err or None}))
                db.commit()
            finally:
                db.close()
            if err:
                log.error("%s (%s): гацсан камер reboot амжилтгүй — %s", ip, name, err)
                skipped.append({"ip": ip, "why": f"reboot алдаа: {err}"})
            else:
                log.warning("%s (%s): snapshot ГАЦСАН (event амьд, %.2fс 400) — "
                            "reboot илгээв", ip, name, r.get("min_bad_lat") or 0)
                rebooted.append({"ip": ip, "name": name})
                try:
                    notify(site_id, "CAMERA_REBOOT",
                           {"device": name, "ip": ip,
                            "reason": "snapshot гацсан (авто эрүүл мэнд)"})
                except Exception:  # noqa: BLE001
                    pass
            await asyncio.sleep(3)

    return {"checked_at": datetime.utcnow().isoformat(),
            "results": results, "hung": [r["ip"] for r in hung],
            "rebooted": rebooted, "skipped": skipped,
            "counts": {
                "healthy": sum(1 for r in results if r["verdict"] == "healthy"),
                "hung": len(hung),
                "busy": sum(1 for r in results if r["verdict"] == "busy"),
                "unreachable": sum(1 for r in results if r["verdict"] == "unreachable"),
                "total": len(results)}}


def run_once(dry_run: bool = False) -> dict:
    """Бүх камерын snapshot эрүүл мэндийг нэг удаа шалгана.

    ЧУХАЛ: дотроо `asyncio.run` ашигладаг тул event loop-ийн ДОТРООС дуудаж
    БОЛОХГҮЙ — supervisor нь `asyncio.to_thread`-ээр дуудна. API endpoint нь
    sync (threadpool) тул шууд дуудаж болно."""
    db = SessionLocal()
    try:
        rules = get_rules(db, CAMHEALTH_KEY)
    finally:
        db.close()
    if not rules["enabled"] and not dry_run:
        return {"note": "унтраалттай", "results": [], "counts": {}}
    out = asyncio.run(_run_all(dry_run, rules))
    # Сүүлийн дүнг UI-д харуулахаар хадгална (dry_run ч болно — оператор харна)
    db = SessionLocal()
    try:
        set_state(db, CAMHEALTH_STATE, {
            "checked_at": out["checked_at"], "counts": out["counts"],
            "hung": out["hung"], "rebooted": out["rebooted"],
            "skipped": out["skipped"],
            # Зөвхөн асуудалтайг хадгална (жагсаалт богино байлгах)
            "problems": [r for r in out["results"] if r["verdict"] != "healthy"]})
        db.commit()
    finally:
        db.close()
    return out


def last_state() -> dict:
    db = SessionLocal()
    try:
        return get_state(db, CAMHEALTH_STATE)
    finally:
        db.close()
