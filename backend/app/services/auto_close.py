"""Гацсан session-ийн авто цэвэрлэгээ.

Ажилтангүй зогсоолд төлбөргүй гарсан/мартагдсан машины session OPEN/AWAITING_PAYMENT
төлөвтэй хуримтлагддаг (шөнийн хаалт/ээлж хаах хийгддэггүй). Энэ даалгавар 30 минут
тутам босго цагаас (site.auto_close_hours, null бол глобал default) дээш идэвхтэй
үлдсэн session-ийг хааж, төлөгдөөгүй дүнгээр өр (нөхөн төлбөр) үүсгэнэ.

Хамгаалалт:
  - Сүүлийн 1 цагт event-тэй (updated_at) session-д хүрэхгүй — бодитоор идэвхтэй машин.
  - PAID session-д хүрэхгүй (grace/зөрүүг гарах урсгал өөрөө шийднэ).
  - Босго 0 бол тухайн зогсоолд унтарсан.
  - Session бүр өөрийн try/except — нэг алдаа бусдыг зогсоохгүй.
"""
import asyncio
from datetime import datetime, timedelta

from ..config import settings
from ..database import SessionLocal
from ..models import AuditLog, ParkingSession, ParkingSite
from ..session_logic import close_session_forced


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
            if not hours or hours <= 0:
                continue
            stale = (db.query(ParkingSession)
                     .filter(ParkingSession.site_id == site.id,
                             ParkingSession.status.in_(["OPEN", "AWAITING_PAYMENT"]),
                             ParkingSession.entry_time < now - timedelta(hours=hours),
                             ParkingSession.updated_at < recent_guard)
                     .limit(100).all())
            for s in stale:
                try:
                    debt = close_session_forced(db, s, "auto_close", "system",
                                                settings.auto_close_create_debt)
                    db.add(AuditLog(username="system", action="AUTO_CLOSE", entity="session",
                                    entity_id=s.id,
                                    detail={"plate": s.plate_number, "site": site.name,
                                            "hours": hours, "debt": debt}))
                    db.commit()
                    closed += 1
                    print(f"[auto_close] {site.name}: {s.plate_number} хаагдлаа "
                          f"({hours}ц+, өр {debt:.0f}₮)")
                except Exception as e:  # noqa: BLE001 — нэг session бусдыг зогсоохгүй
                    db.rollback()
                    print(f"[auto_close] {s.plate_number} хааж чадсангүй: {e}")
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
                print(f"[auto_close] нийт {n} гацсан session хаагдлаа")
        except Exception as e:  # noqa: BLE001
            print(f"[auto_close] давталтын алдаа: {e}")
        await asyncio.sleep(1800)
