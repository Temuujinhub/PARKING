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
        now = datetime.utcnow()
        recent_guard = now - timedelta(hours=1)
        for site in db.query(ParkingSite).filter(ParkingSite.is_active.is_(True)).all():
            hours = site.auto_close_hours if site.auto_close_hours is not None \
                else settings.auto_close_hours

            # ФОРМАТ БУРУУ phantom (үсэггүй/дутуу дугаар, ж «4132») — ХУРДАН цэвэрлэнэ.
            # Ийм уншилт жинхэнэ гарах дугаартай тохирдоггүй тул мөнхөд гацдаг;
            # 72ц entry-only хүлээхийн оронд invalid_plate_close_hours (2ц) дараа
            # ӨРГҮЙГЭЭР үнэгүй хааж дашбоардыг цэвэрхэн байлгана.
            ip_hours = settings.invalid_plate_close_hours
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
                        s.duration_minutes = int((now - s.entry_time).total_seconds() // 60)
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
                else settings.entry_only_free_hours
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
                        s.duration_minutes = int((now - s.entry_time).total_seconds() // 60)
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
            aw_hours = settings.auto_close_awaiting_hours
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
                    make_debt = settings.auto_close_create_debt and is_valid_plate(s.plate_number)
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
    # Snapshot зургууд — хамгийн их диск иддэг төрөл
    days = settings.retention_snapshot_days
    snap_dir = settings.snapshot_dir
    if days and days > 0 and snap_dir and os.path.isdir(snap_dir):
        cutoff = _t.time() - days * 86400
        removed = 0
        for root_, _dirs, files in os.walk(snap_dir):
            for fn in files:
                p = os.path.join(root_, fn)
                try:
                    if os.path.getmtime(p) < cutoff:
                        os.remove(p)
                        removed += 1
                except OSError:
                    pass
        if removed:
            out["snapshots"] = removed
    if out:
        log.info("retention: устгав %s", out)
    return out


async def supervisor():
    """Startup-аас create_task-аар ажиллана: эхний удаа 5 минутын дараа, дараа нь 30 мин тутам.
    Хуучин датаны цэвэрлэгээ (retention) өдөрт нэг л удаа хийгдэнэ."""
    await asyncio.sleep(300)
    last_retention = 0.0
    import time as _t
    while True:
        try:
            n = run_once()
            if n:
                log.info(f"нийт {n} гацсан session хаагдлаа")
            if _t.monotonic() - last_retention > 24 * 3600:
                last_retention = _t.monotonic()
                retention_once()
        except Exception as e:  # noqa: BLE001
            log.error(f"давталтын алдаа: {e}")
        await asyncio.sleep(1800)
