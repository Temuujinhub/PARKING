"""Камер БҮРЭН гацсан үеийн сүүлчийн арга — deadman авто-reboot (default УНТРААЛТТАЙ).

Юуны учир (2026-07-28 дүн шинжилгээ): Monnis-ийн 15с тасалдлууд нь 10-15 мин
тутам давтагдаад 1-3 минутад ӨӨРӨӨ сэргэдэг богино цонхнууд — тэдгээрт reboot
ХЭРЭГГҮЙ (reboot 60-120с üргэлжилдэг тул хохирол нь ашгаасаа их). Харин Dahua
камер хааяа ҮНЭХЭЭР бүрэн гацдаг: event стрим тасарч, дахин холболт ч бүтэхгүй.
Тэр ховор тохиолдолд л энэ сервис ажиллана.

Trigger нь event-ийн чимээгүй байдал БИШ (шөнө машин байхгүй = хэвийн чимээгүй),
харин device.last_seen — стрим амьд байхад heartbeat-ээр 60с тутам шинэчлэгддэг
тул last_seen нь camera_reboot_after_min-ээс хуучирвал камер жинхэнэ хариугүй
гэсэн үг. Хамгаалалтууд:
  • default УНТРААЛТТАЙ — .env-д PARKING_CAMERA_AUTO_REBOOT=true гэж асаана
  • камер тутамд cooldown (default 60 мин) — reboot-ын шуурга гарахгүй
  • бүх оролдлого AuditLog(CAMERA_REBOOT) + WARNING лог + кассын мэдэгдэлтэй
"""
import asyncio
import logging
import time
from datetime import datetime, timedelta

from ..config import settings
from ..database import SessionLocal
from ..models import AuditLog, Device, ParkingSite
from ..ws import notify
from .barrier import DahuaRpc, _rpc_lock, camera_client
from .device_auth import camera_credentials

log = logging.getLogger("parking.camera_recovery")

_last_reboot: dict[str, float] = {}   # ip -> monotonic


async def reboot_camera(ip: str, creds: tuple[str, str]) -> str:
    """magicBox.reboot илгээнэ. Амжилтад хоосон мөр, алдаанд тайлбар буцаана."""
    try:
        async with _rpc_lock(ip):
            rpc = DahuaRpc(camera_client(ip), ip, *creds)
            await asyncio.wait_for(rpc.login(), timeout=8)
            try:
                res = await asyncio.wait_for(rpc._call("magicBox.reboot"), timeout=8)
                if not res.get("result"):
                    return f"камер татгалзав: {str(res)[:120]}"
            finally:
                # Reboot хийгдэж байгаа тул logout бүтэхгүй байж болно — үл хайхарна
                try:
                    await asyncio.wait_for(rpc.logout(), timeout=3)
                except Exception:  # noqa: BLE001
                    pass
        return ""
    except Exception as e:  # noqa: BLE001
        return f"{type(e).__name__}: {str(e)[:120]}"


async def supervisor():
    if settings.barrier_mock or not settings.camera_auto_reboot:
        return
    log.info("камерын deadman recovery идэвхжлээ (last_seen %d мин хуучирвал reboot, "
             "cooldown %d мин)", settings.camera_reboot_after_min,
             settings.camera_reboot_cooldown_min)
    while True:
        await asyncio.sleep(60)
        cutoff = datetime.utcnow() - timedelta(minutes=settings.camera_reboot_after_min)
        db = SessionLocal()
        try:
            cams = (db.query(Device).join(ParkingSite, Device.site_id == ParkingSite.id)
                    .filter(Device.device_type == "camera", Device.status == "active",
                            ParkingSite.is_active.is_(True),
                            Device.ip_address.isnot(None), Device.ip_address != "",
                            Device.last_seen.isnot(None), Device.last_seen < cutoff)
                    .all())
            targets = [(c.id, c.site_id, c.name, c.ip_address, camera_credentials(c),
                        c.last_seen) for c in cams]
        except Exception as e:  # noqa: BLE001
            targets = []
            log.error("deadman шалгалт: DB алдаа %r", e)
        finally:
            db.close()
        for did, site_id, name, ip, creds, last_seen in targets:
            cool = settings.camera_reboot_cooldown_min * 60
            if time.monotonic() - _last_reboot.get(ip, -cool) < cool:
                continue
            _last_reboot[ip] = time.monotonic()
            silent_min = (datetime.utcnow() - last_seen).total_seconds() / 60
            log.warning("%s (%s): %.0f минут бүрэн хариугүй — REBOOT илгээж байна",
                        name, ip, silent_min)
            err = await reboot_camera(ip, creds)
            db = SessionLocal()
            try:
                db.add(AuditLog(username="system", action="CAMERA_REBOOT", entity="device",
                                entity_id=did,
                                detail={"ip": ip, "silent_min": round(silent_min),
                                        "result": err or "OK"}))
                db.commit()
            finally:
                db.close()
            notify(site_id, "CAMERA_REBOOT", {"device": name, "ip": ip,
                                              "silent_min": round(silent_min),
                                              "ok": not err, "error": err})
            if err:
                log.error("%s: reboot амжилтгүй — %s (камер бүрэн унтарсан бол "
                          "цахилгааныг нь салгаж залгах шаардлагатай)", ip, err)
            else:
                log.warning("%s: reboot хүлээн авлаа — 1-2 минутад сэргэнэ", ip)
