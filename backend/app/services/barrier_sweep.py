"""Гарах хаалт ОНГОРХОЙ гацсаныг өөрөө засах цэвэрлэгээ.

Dahua ANPR кит хаалтаа газрын мэдрэгч/радараар өөрөө хаадаг. Мэдрэгч ажиллахгүй
байх, машин хаалганы доор удаан зогсох, эсвэл «албадан нээх» (forceBreaking)
командын дараа хаалт ОНГОРХОЙ үлдэж, зогсоол хяналтгүй болдог — машин төлбөргүй
чөлөөтэй гарна (2026-08-09 NIC).

Энэ даалгавар зогсоол бүрийн `barrier_close_sweep_min` (NULL бол глобал
`settings.barrier_close_sweep_min`) минут тутам гарах хаалтад «хаах» команд
илгээнэ. 0 = унтраалттай (default).

ХАМГААЛАЛТ — хөдөлгөөнтэй үед ХЭЗЭЭ Ч команд илгээхгүй. Хаалганы доор явж буй
машин дээр хаалт буувал эд хөрөнгө/амь насны эрсдэлтэй тул дараах бүх нөхцөл
хангагдсан үед Л хаана (`barrier_sweep_quiet_min`, default 5 мин):

  1. Тухайн зогсоолд сүүлийн N минутад ГАРАХ уншилт болоогүй
  2. Тэр хаалтад сүүлийн N минутад ямар ч команд илгээгээгүй (нээлт дөнгөж
     болсон бол машин гарч байгаа гэсэн үг)
  3. Хаалтад ЯГ ОДОО явж буй нээх команд алга (`open_in_flight`)
  4. Зогсоолд гарцад төлбөр хүлээж буй (AWAITING_PAYMENT) машин алга —
     хаалганы өмнө зогсож байгаа машин байж болзошгүй

Мөн команд бүрийн хооронд завсарлага өгнө: хаалтууд ихэвчлэн камерын реле дээр
байдаг тул нэг дор олон RPC явуулбал камерын ховор сесс шавхагдана.
"""
import asyncio
import logging
from datetime import datetime, timedelta

from ..config import settings
from ..database import SessionLocal
from ..models import BarrierCommand, Device, LprEvent, ParkingSession, ParkingSite

log = logging.getLogger("parking.barrier_sweep")

# Хаалт хоорондын завсар (секунд) — камерын RPC сесс шавхахгүйн тулд
_GAP_SEC = 2.0


def _sweep_minutes(site: ParkingSite) -> int:
    """Тухайн зогсоолын цэвэрлэгээний давтамж (минут). 0 = унтраалттай."""
    v = site.barrier_close_sweep_min
    if v is None:
        v = settings.barrier_close_sweep_min
    return max(0, int(v or 0))


def due_barriers(db, now: datetime) -> list[tuple[ParkingSite, Device]]:
    """Одоо хаах ёстой (бүх хамгаалалтыг давсан) гарах хаалтуудыг буцаана."""
    from .barrier import open_in_flight

    quiet = max(1, int(settings.barrier_sweep_quiet_min))
    out: list[tuple[ParkingSite, Device]] = []
    for site in db.query(ParkingSite).filter(ParkingSite.is_active.is_(True)).all():
        every = _sweep_minutes(site)
        if not every:
            continue
        quiet_since = now - timedelta(minutes=quiet)

        # (1) зогсоолд саяхан гарах уншилт болсон бол бүхэлд нь алгасна
        if (db.query(LprEvent.id)
                .filter(LprEvent.site_id == site.id, LprEvent.lane_dir == "exit",
                        LprEvent.created_at >= quiet_since).first()):
            continue
        # (4) гарцад төлбөр хүлээж буй машин байвал алгасна
        if (db.query(ParkingSession.id)
                .filter(ParkingSession.site_id == site.id,
                        ParkingSession.status == "AWAITING_PAYMENT").first()):
            continue

        barriers = (db.query(Device)
                    .filter(Device.site_id == site.id, Device.device_type == "barrier",
                            Device.status == "active",
                            Device.lane_dir.in_(["exit", "both"]))
                    .order_by(Device.lane_no, Device.id).all())
        for b in barriers:
            # (3) яг одоо нээх команд явж байвал хөндөхгүй
            if open_in_flight(b.id):
                continue
            # (2) сүүлийн команд хэр удсан бэ — «чимээгүй» цонхонд команд байвал алгасна,
            #     мөн өмнөх ЦЭВЭРЛЭГЭЭнээс хойш `every` минут өнгөрсөн байх ёстой
            last = (db.query(BarrierCommand)
                    .filter(BarrierCommand.device_id == b.id)
                    .order_by(BarrierCommand.created_at.desc()).first())
            if last is not None:
                if last.created_at >= quiet_since:
                    continue
                if last.command_source == "sweep" and \
                        last.created_at >= now - timedelta(minutes=every):
                    continue
            out.append((site, b))
    return out


async def run_once() -> int:
    """Нэг удаагийн цэвэрлэгээ — илгээсэн командын тоог буцаана."""
    from .barrier import close_barrier

    db = SessionLocal()
    sent = 0
    try:
        targets = due_barriers(db, datetime.utcnow())
        for site, barrier in targets:
            try:
                cmd = await close_barrier(db, barrier, source="sweep", issued_by="system")
                sent += 1
                (log.info if cmd.status == "SUCCESS" else log.warning)(
                    "%s: «%s» хаах команд — %s", site.name, barrier.name, cmd.status)
            except Exception as e:  # noqa: BLE001 — нэг хаалтын алдаа бусдыг зогсоохгүй
                db.rollback()
                log.error("%s «%s» хаахад алдаа: %r", site.name, barrier.name, e)
            await asyncio.sleep(_GAP_SEC)
    finally:
        db.close()
    return sent


async def supervisor():
    """Startup-аас ажиллана. Минут тутам сэрж, хугацаа нь болсон хаалтыг л хаана.

    Хугацааны нарийвчлалыг тухайн зогсоолын `barrier_close_sweep_min` тодорхойлно
    (сүүлийн `sweep` командаас хойш төдөн минут өнгөрсөн эсэхээр шалгана) — тиймээс
    энд байнга сэрэх нь хямд бөгөөд restart-д ч зөв үргэлжилнэ (төлөв нь DB-д).
    """
    await asyncio.sleep(120)   # асаалтын шуугиан намжтал
    while True:
        try:
            n = await run_once()
            if n:
                log.info("нийт %d гарах хаалтад хаах команд илгээв", n)
        except Exception as e:  # noqa: BLE001
            log.error("давталтын алдаа: %r", e)
        await asyncio.sleep(60)
