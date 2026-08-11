"""PENDING үлдсэн QPay төлбөрийг сервер талаас тогтмол дахин шалгах.

Жолооч QR-аар төлөөд Pay хуудсаа хаачихвал 5 секундын polling зогсдог; QPay-ийн
webhook ямар нэг шалтгаанаар (сүлжээ, public_base_url, QPay талын саатал)
ирэхгүй бол төлбөр МӨНХӨД PENDING үлдэж, жолооч «төлсөн ч хаалт нээгдэхгүй»
гацдаг (2026-08-11: нэг хоногт 58 PENDING хуримтлагдсан нь энэ).

Энэ даалгавар минут тутам сүүлийн 24 цагийн PENDING QPAY төлбөрүүдийг QPay-аас
асууж, төлөгдсөн нь тогтоогдвол finalize хийнэ — e-Barimt үүсч, машин гарцад
байвал хаалт шууд нээгдэнэ.

Хамгаалалтууд:
  - 2 минутаас шинэ төлбөрт хүрэхгүй — Pay хуудасны өөрийн polling ажиллаж байна.
  - Нэг давталтад хамгийн ихдээ 20 — QPay API-ийн хязгаарыг дарамтлахгүй.
  - qpay_mock үед ОГТ ажиллахгүй — mock check үргэлж «төлөгдсөн» гэдэг тул
    туршилтын орчинд бүх PENDING-ийг худал PAID болгочихно.
  - Webhook-той уралдвал асуудалгүй: _finalize_paid идемпотент (PAID бол алгасна).
"""
import asyncio
import logging
from datetime import datetime, timedelta

from ..config import settings
from ..database import SessionLocal
from ..models import Payment

log = logging.getLogger("parking.qpay_recheck")


async def run_once() -> int:
    """Нэг давталт — PAID болгож сэргээсэн төлбөрийн тоог буцаана."""
    if settings.qpay_mock or not settings.qpay_recheck_sec:
        return 0
    db = SessionLocal()
    fixed = 0
    try:
        # Circular import-оос зайлсхийж энд импортолно (router нь service-үүдийг татдаг)
        from ..routers.payments_router import _confirm_qpay
        now = datetime.utcnow()
        ids = [pid for (pid,) in db.query(Payment.id)
               .filter(Payment.provider == "QPAY", Payment.status == "PENDING",
                       Payment.provider_invoice_id.isnot(None),
                       Payment.created_at >= now - timedelta(hours=24),
                       Payment.created_at <= now - timedelta(minutes=2))
               .order_by(Payment.created_at.desc()).limit(20).all()]
        for pid in ids:
            try:
                p = db.get(Payment, pid)
                if p is None or p.status != "PENDING":
                    continue  # webhook/polling түрүүлж авчихсан
                if await _confirm_qpay(db, p):
                    fixed += 1
                    log.info("PENDING → PAID сэргээв: %s %.0f₮ (payment %s)",
                             p.sender_invoice_no, float(p.amount), p.id)
                else:
                    db.rollback()  # төлөгдөөгүй хэвээр — өөрчлөлтгүй
            except Exception as e:  # noqa: BLE001 — нэг төлбөрийн алдаа бусдыг зогсоохгүй
                log.warning("recheck алдаа (payment %s): %r", pid, e)
                db.rollback()
            await asyncio.sleep(0.5)  # QPay API-д завсарлага
    finally:
        db.close()
    return fixed


async def supervisor():
    """Startup-аас create_task-аар ажиллана: эхний удаа 2 минутын дараа,
    дараа нь qpay_recheck_sec (default 60с) тутам."""
    await asyncio.sleep(120)
    while True:
        try:
            n = await run_once()
            if n:
                log.info("нийт %d гацсан QPay төлбөр сэргээгдлээ", n)
        except Exception as e:  # noqa: BLE001
            log.error("qpay_recheck давталт унав: %r", e)
        await asyncio.sleep(max(30, int(settings.qpay_recheck_sec or 60)))
