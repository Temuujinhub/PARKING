"""Гацсан session-ийн авто цэвэрлэгээ.

Ажилтангүй зогсоолд төлбөргүй гарсан/мартагдсан машины session OPEN/AWAITING_PAYMENT
төлөвтэй хуримтлагддаг (шөнийн хаалт/ээлж хаах хийгддэггүй). Энэ даалгавар 30 минут
тутам босго цагаас (site.auto_close_hours, null бол глобал default) дээш идэвхтэй
үлдсэн session-ийг хааж, төлөгдөөгүй дүнгээр өр (нөхөн төлбөр) үүсгэнэ.

Хамгаалалт:
  - Сүүлийн 1 цагт event-тэй (updated_at) session-д хүрэхгүй — бодитоор идэвхтэй машин.
  - PAID-ийг ч хаана (төлсөн ч гарах уншилтгүй гацсаныг цэвэрлэнэ) — гэхдээ
    төлбөрийг deadline дээр царцаадаг тул худал өр үүсгэхгүй.
  - Босго 0 бол тухайн зогсоолд унтарсан.
  - Session бүр өөрийн try/except — нэг алдаа бусдыг зогсоохгүй.
"""
import asyncio
import logging
from datetime import datetime, timedelta

from ..config import settings
from ..database import SessionLocal
from ..models import AuditLog, ParkingSession, ParkingSite
from ..session_logic import close_session_forced, is_valid_plate

log = logging.getLogger("parking.auto_close")


def run_once() -> int:
    """Нэг удаагийн цэвэрлэгээ — хаасан session-ийн тоог буцаана."""
    db = SessionLocal()
    closed = 0
    try:
        # Дүрмүүд Тохиргоо → Авто цэвэрлэгээ хэсгээс (app_settings). .env-ийн
        # утга нь зөвхөн ЭХНИЙ анхдагч — админ UI-аас deploy-гүйгээр өөрчилнө.
        from .app_settings import get_autoclose_rules
        rules = get_autoclose_rules(db)
        if not rules["enabled"]:
            log.info("авто цэвэрлэгээ Тохиргооноос УНТРААЛТТАЙ — алгаслаа")
            return 0
        now = datetime.utcnow()
        recent_guard = now - timedelta(hours=1)
        for site in db.query(ParkingSite).filter(ParkingSite.is_active.is_(True)).all():
            hours = site.auto_close_hours if site.auto_close_hours is not None \
                else rules["stale_hours"]

            # ФОРМАТ БУРУУ phantom (үсэггүй/дутуу дугаар, ж «4132») — ХУРДАН цэвэрлэнэ.
            # Ийм уншилт жинхэнэ гарах дугаартай тохирдоггүй тул мөнхөд гацдаг;
            # 72ц entry-only хүлээхийн оронд invalid_plate_close_hours (2ц) дараа
            # ӨРГҮЙГЭЭР үнэгүй хааж дашбоардыг цэвэрхэн байлгана.
            ip_hours = rules["invalid_plate_hours"]
            if ip_hours and ip_hours > 0:
                junk = (db.query(ParkingSession)
                        .filter(ParkingSession.site_id == site.id,
                                ParkingSession.status.in_(["OPEN", "AWAITING_PAYMENT"]),
                                ParkingSession.entry_time < now - timedelta(hours=ip_hours),
                                ParkingSession.updated_at < recent_guard)
                        .limit(200).all())
                for s in junk:
                    if is_valid_plate(s.plate_number):
                        continue  # зөв форматтай — энэ дүрэмд хамаарахгүй
                    try:
                        s.status = "FREE"
                        s.exit_time = now
                        # Хугацааг бичихгүй: машин ХЭЗЭЭ гарсныг мэдэхгүй (гарах
                        # уншилт байхгүй). «Одоо − орсон» гэж бичвэл тайлангийн
                        # Хугацаа багана хуурамчаар хөөрөгддөг (Моннис 73%).
                        s.duration_minutes = None
                        s.base_fee, s.vat_amount, s.total_fee = 0, 0, 0
                        s.note = f"{s.note + ' | ' if s.note else ''}авто: формат буруу (дутуу уншсан) phantom — {ip_hours}ц дараа үнэгүй хаав"[:1000]
                        db.add(AuditLog(username="system", action="AUTO_JUNK_CLOSE",
                                        entity="session", entity_id=s.id,
                                        detail={"plate": s.plate_number, "site": site.name,
                                                "hours": ip_hours, "reason": "invalid_plate"}))
                        db.commit()
                        closed += 1
                        log.info(f"{site.name}: «{s.plate_number}» формат буруу phantom — "
                                 f"{ip_hours}ц дараа ҮНЭГҮЙ хаав")
                    except Exception as e:  # noqa: BLE001
                        db.rollback()
                        log.error(f"junk close алдаа ({s.plate_number}): {e}")

            # ЗӨВХӨН ОРОХ камерт уншигдсан (гарах уншилт огт байхгүй) OPEN session —
            # ихэвчлэн гарах уншилт алдагдсан phantom тул N цагийн дараа ӨРГҮЙГЭЭР
            # үнэгүй хаана (2026-07-29: эдгээрийг өр болгодог байсан нь худал өрийн
            # уул үүсгэж, гэрээт/энгийн машиныг гарахад нь гацаадаг байв).
            eo_hours = site.entry_only_free_hours if site.entry_only_free_hours is not None \
                else rules["entry_only_free_hours"]
            if eo_hours and eo_hours > 0:
                entry_only = (db.query(ParkingSession)
                              .filter(ParkingSession.site_id == site.id,
                                      ParkingSession.status == "OPEN",
                                      ParkingSession.exit_device_id.is_(None),
                                      ParkingSession.entry_time < now - timedelta(hours=eo_hours),
                                      ParkingSession.updated_at < recent_guard)
                              .limit(200).all())
                for s in entry_only:
                    try:
                        s.status = "FREE"
                        s.exit_time = now
                        # Гарах уншилт огт байхгүй тул бодит зогсолтын хугацаа
                        # ТОДОРХОЙГҮЙ — хуурамч дүн бичихгүй (дээрхтэй ижил шалтгаан)
                        s.duration_minutes = None
                        s.base_fee, s.vat_amount, s.total_fee = 0, 0, 0
                        s.note = f"{s.note + ' | ' if s.note else ''}авто: зөвхөн орох уншилттай тул {eo_hours}ц дараа үнэгүй хаав"[:1000]
                        db.add(AuditLog(username="system", action="AUTO_FREE_CLOSE",
                                        entity="session", entity_id=s.id,
                                        detail={"plate": s.plate_number, "site": site.name,
                                                "hours": eo_hours}))
                        db.commit()
                        closed += 1
                        log.info(f"{site.name}: {s.plate_number} зөвхөн орох уншилттай — "
                                 f"{eo_hours}ц дараа ҮНЭГҮЙ хаав")
                    except Exception as e:  # noqa: BLE001
                        db.rollback()
                        log.error(f"{s.plate_number} үнэгүй хааж чадсангүй: {e}")

            if not hours or hours <= 0:
                continue
            # PAID-ийг ч хамруулна: төлсөн ч гарах камерт уншигдаагүй тул хаагдалгүй
            # "зогсоолд байгаа"-д гацсан машинууд (close_session_forced нь PAID-ийг
            # deadline дээр царцаадаг тул худал өр үүсгэхгүй).
            # ЧУХАЛ: зөвхөн-орох OPEN session-ийг ӨРИЙН замд ОРУУЛАХГҮЙ (дээрх
            # eo_hours дүрэм хариуцна) — eo дүрэм унтраалттай (0) үед л хуучин
            # зан төлөвөөр өртэй хаана.
            _q = (db.query(ParkingSession)
                  .filter(ParkingSession.site_id == site.id,
                          ParkingSession.status.in_(["OPEN", "AWAITING_PAYMENT", "PAID"]),
                          ParkingSession.entry_time < now - timedelta(hours=hours),
                          ParkingSession.updated_at < recent_guard))
            if eo_hours and eo_hours > 0:
                from sqlalchemy import or_
                _q = _q.filter(or_(ParkingSession.status != "OPEN",
                                   ParkingSession.exit_device_id.isnot(None)))
            stale = _q.limit(100).all()
            # Дагаж гарсан (tailgating) машиныг ХУРДАН өр болгох: гарах хаалтанд
            # уншигдсан (AWAITING_PAYMENT) ч төлөлгүй N цаг ямар ч хөдөлгөөнгүй бол
            # явчихсан — төлбөр нь сүүлд харагдсан үед царцаж, өр бүртгэгдэнэ.
            aw_hours = rules["awaiting_hours"]
            if aw_hours and aw_hours > 0:
                awaiting = (db.query(ParkingSession)
                            .filter(ParkingSession.site_id == site.id,
                                    ParkingSession.status == "AWAITING_PAYMENT",
                                    ParkingSession.updated_at < now - timedelta(hours=aw_hours))
                            .limit(100).all())
                # Хоёр query-д давхар таарсан session-ийг нэг л удаа хаана
                seen_ids = {s.id for s in stale}
                stale += [s for s in awaiting if s.id not in seen_ids]
            for s in stale:
                try:
                    # Junk (буруу форматтай) дугаар нь камерын буруу уншилт — жинхэнэ
                    # машин биш тул өр үүсгэхгүйгээр чимээгүй хаана.
                    make_debt = rules["create_debt"] and is_valid_plate(s.plate_number)
                    debt = close_session_forced(db, s, "auto_close", "system", make_debt)
                    db.add(AuditLog(username="system", action="AUTO_CLOSE", entity="session",
                                    entity_id=s.id,
                                    detail={"plate": s.plate_number, "site": site.name,
                                            "hours": hours, "debt": debt}))
                    db.commit()
                    closed += 1
                    log.info(f"{site.name}: {s.plate_number} хаагдлаа "
                             f"({hours}ц+, өр {debt:.0f}₮)")
                except Exception as e:  # noqa: BLE001 — нэг session бусдыг зогсоохгүй
                    db.rollback()
                    log.error(f"{s.plate_number} хааж чадсангүй: {e}")
    finally:
        db.close()
    return closed


def retention_once() -> dict:
    """Хуучирсан техникийн датаг устгана (санхүүгийн дата — session/payment/
    vat_receipt/compensation-д ХҮРЭХГҮЙ). Юуг хэдэн хоног хадгалахыг .env-ээс:
      lpr_events        → retention_lpr_days      (танилтын түүхий лог)
      barrier_commands  → retention_cmd_days      (хаалтны командын лог)
      audit_logs        → retention_audit_days    (үйлдлийн лог)
      snapshot зургууд  → retention_snapshot_days (диск дүүрэхээс сэргийлнэ)
    0 = тухайн төрлийг устгахгүй. Бүгд «өдөрт нэг удаа» supervisor-оос дуудагдана."""
    import os
    import time as _t

    from sqlalchemy import text

    from ..models import AuditLog as _A, BarrierCommand as _B, LprEvent as _E
    now = datetime.utcnow()
    out = {}
    db = SessionLocal()
    try:
        for label, model, days in (("lpr_events", _E, settings.retention_lpr_days),
                                   ("barrier_commands", _B, settings.retention_cmd_days),
                                   ("audit_logs", _A, settings.retention_audit_days)):
            if days and days > 0:
                n = (db.query(model)
                     .filter(model.created_at < now - timedelta(days=days))
                     .delete(synchronize_session=False))
                db.commit()
                if n:
                    out[label] = n
        # VACUUM биш — энгийн delete хангалттай (autovacuum цэвэрлэнэ)
    except Exception as e:  # noqa: BLE001
        db.rollback()
        log.error("retention DB алдаа: %r", e)
    finally:
        db.close()
    # Snapshot зургууд — хамгийн их диск иддэг төрөл. ХОЁР дүрмээр цэвэрлэнэ:
    #   1) хугацаа (retention_snapshot_days)
    #   2) НИЙТ ХЭМЖЭЭ (retention_snapshot_max_gb) — хугацааны дүрэм хүрэлцэхгүй
    #      үед хатуу таг. Сард ~20GB хуримтлагддаг тул 120 хоногийн дүрэм
    #      ганцаараа 98GB дискийг дүүргэдэг байв (2026-08-09).
    days = settings.retention_snapshot_days
    snap_dir = settings.snapshot_dir
    if snap_dir and os.path.isdir(snap_dir):
        # Файлын жагсаалтыг НЭГ удаа цуглуулна (77GB-ийн дор дахин алхах үнэтэй)
        entries = []   # (mtime, size, path)
        for root_, _dirs, files in os.walk(snap_dir):
            for fn in files:
                p = os.path.join(root_, fn)
                try:
                    st = os.stat(p)
                    entries.append((st.st_mtime, st.st_size, p))
                except OSError:
                    pass

        removed = 0
        freed = 0
        if days and days > 0:
            cutoff = _t.time() - days * 86400
            keep = []
            for mtime, size, p in entries:
                if mtime < cutoff:
                    try:
                        os.remove(p)
                        removed += 1
                        freed += size
                        continue
                    except OSError:
                        pass
                keep.append((mtime, size, p))
            entries = keep

        max_bytes = int(settings.retention_snapshot_max_gb * 1024 ** 3)
        total = sum(size for _m, size, _p in entries)
        if max_bytes > 0 and total > max_bytes:
            # Хамгийн ХУУЧНААС нь эхлэн хязгаарт орох хүртэл устгана
            for mtime, size, p in sorted(entries):
                if total <= max_bytes:
                    break
                try:
                    os.remove(p)
                    total -= size
                    removed += 1
                    freed += size
                except OSError:
                    pass
            log.warning("retention: зургийн хэмжээ %.1fGB хязгаараас хэтэрсэн тул "
                        "хуучныг устгав → %.1fGB",
                        (total + freed) / 1024 ** 3, total / 1024 ** 3)
        if removed:
            out["snapshots"] = removed
            out["snapshots_freed_mb"] = round(freed / 1024 ** 2)
    if out:
        log.info("retention: устгав %s", out)
    return out


def disk_free_percent(path: str = "/") -> float:
    """Дискний сул зайн хувь (алдаа гарвал 100 — цэвэрлэгээ өдөөхгүй)."""
    import shutil
    try:
        usage = shutil.disk_usage(path)
        return usage.free / usage.total * 100 if usage.total else 100.0
    except OSError:
        return 100.0


async def supervisor():
    """Startup-аас create_task-аар ажиллана: эхний удаа 5 минутын дараа, дараа нь 30 мин тутам.
    Хуучин датаны цэвэрлэгээ (retention) өдөрт нэг л удаа хийгдэнэ."""
    await asyncio.sleep(300)
    last_retention = 0.0
    last_camsync = 0.0
    last_camhealth = 0.0
    import time as _t
    while True:
        try:
            n = run_once()
            if n:
                log.info(f"нийт {n} гацсан session хаагдлаа")
            # Ердийн хуваарь: өдөрт нэг. ГЭХДЭЭ диск дүүрч эхэлбэл хүлээхгүй —
            # 30 минут тутмын энэ давталтад шууд цэвэрлэнэ (диск дүүрвэл
            # backend бичих боломжгүй болж бүхэлдээ зогсдог).
            # Камерын лог нөхөлт — өдөрт times_per_day удаа (watermark-тай тул
            # давхардахгүй). Тохиргооноос унтраалттай бол run_once өөрөө буцна.
            try:
                from .app_settings import get_camsync_rules
                from .camera_sync import run_once as camsync_once
                _db = SessionLocal()
                try:
                    _n = max(1, get_camsync_rules(_db)["times_per_day"])
                finally:
                    _db.close()
                if _t.monotonic() - last_camsync > 24 * 3600 / _n:
                    last_camsync = _t.monotonic()
                    # ЗААВАЛ thread дээр: camsync дотроо asyncio.run ашигладаг
                    # (event loop дотроос дуудвал RuntimeError) бөгөөд камерын
                    # хүсэлт 15с үргэлжилж болох тул loop-ийг блоклоно.
                    await asyncio.to_thread(camsync_once)
            except Exception as e:  # noqa: BLE001
                log.error("камерын лог нөхөлтийн алдаа: %r", e)

            # Камерын эрүүл мэнд — өдөрт times_per_day удаа гацсан камерыг
            # илрүүлж, тохиргоо зөвшөөрвөл reboot хийнэ. camsync-тэй адил
            # ЗААВАЛ thread дээр (дотроо asyncio.run ашигладаг).
            try:
                from .app_settings import CAMHEALTH_KEY, get_rules
                from .camera_health import run_once as camhealth_once
                _db = SessionLocal()
                try:
                    _hr = get_rules(_db, CAMHEALTH_KEY)
                finally:
                    _db.close()
                _hn = max(1, _hr["times_per_day"])
                if _hr["enabled"] and _t.monotonic() - last_camhealth > 24 * 3600 / _hn:
                    last_camhealth = _t.monotonic()
                    await asyncio.to_thread(camhealth_once)
            except Exception as e:  # noqa: BLE001
                log.error("камерын эрүүл мэнд шалгалтын алдаа: %r", e)

            free_pct = disk_free_percent(settings.snapshot_dir or "/")
            tight = free_pct < settings.disk_free_min_percent
            if tight or _t.monotonic() - last_retention > 24 * 3600:
                if tight:
                    log.warning("дискний сул зай %.1f%% — retention-ийг хуваариас "
                                "өмнө ажиллуулж байна", free_pct)
                last_retention = _t.monotonic()
                retention_once()
        except Exception as e:  # noqa: BLE001
            log.error(f"давталтын алдаа: {e}")
        await asyncio.sleep(1800)
