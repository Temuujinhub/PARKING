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


async def supervisor():
    """Startup-аас create_task-аар ажиллана: эхний удаа 5 минутын дараа, дараа нь 30 мин тутам."""
    await asyncio.sleep(300)
    while True:
        try:
            n = run_once()
            if n:
                log.info(f"нийт {n} гацсан session хаагдлаа")
        except Exception as e:  # noqa: BLE001
            log.error(f"давталтын алдаа: {e}")
        await asyncio.sleep(1800)
