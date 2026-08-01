"""Гэрээт байгууллагын САРЫН нэхэмжлэл — үүсгэх логик + сар бүрийн авто үүсгэлт.

Нэхэмжлэлийн дүн = тухайн байгууллагын ИДЭВХТЭЙ гэрээт машидын monthly_fee нийлбэр.
Хавсарган мэдээлэлд тухайн САРЫН бодит ашиглалт (орсон удаа, нийт зогссон минут)
орно — тооцоо нийлэхэд нотолгоо болно. Байгууллага бүр сард НЭГ л нэхэмжлэлтэй
(uq_invoice_period_company) тул generate-ийг олон удаа дуудахад давхардахгүй.
"""
import asyncio
import logging
from datetime import datetime, timedelta

from ..config import settings
from ..database import SessionLocal
from ..models import CompanyInvoice, ParkingSession, RegisteredDriver

log = logging.getLogger("parking.invoicing")

TZ = timedelta(hours=8)  # УБ-ын цагаар сарын зааг


def month_range_utc(period: str) -> tuple[datetime, datetime]:
    """"2026-07" → тухайн сарын [эхлэл, төгсгөл) UTC-ээр (УБ-ын цагийн зөрүүтэй)."""
    y, m = int(period[:4]), int(period[5:7])
    start = datetime(y, m, 1) - TZ
    end = (datetime(y + 1, 1, 1) if m == 12 else datetime(y, m + 1, 1)) - TZ
    return start, end


def prev_period(now_utc: datetime | None = None) -> str:
    local = (now_utc or datetime.utcnow()) + TZ
    first = local.replace(day=1)
    prev = first - timedelta(days=1)
    return prev.strftime("%Y-%m")


def _usage(db, plates: set[str], site_ids: set, start, end) -> dict:
    """Тухайн байгууллагын машидын тухайн сарын ашиглалт (by_company-тэй ижил дүрэм)."""
    if not plates:
        return {"sessions": 0, "minutes": 0, "visited": 0}
    q = (db.query(ParkingSession)
         .filter(ParkingSession.entry_time >= start, ParkingSession.entry_time < end,
                 ParkingSession.plate_number.in_(list(plates))))
    sessions = 0
    minutes = 0.0
    visited = set()
    for s in q.all():
        if not (None in site_ids or s.site_id in site_ids):
            continue
        sessions += 1
        visited.add(s.plate_number)
        m = s.duration_minutes
        if m is None:
            m = ((s.exit_time or end) - s.entry_time).total_seconds() / 60
        minutes += float(m)
    return {"sessions": sessions, "minutes": round(minutes), "visited": len(visited)}


def generate_invoices(db, period: str, created_by: str = "system") -> list[CompanyInvoice]:
    """Тухайн сард (period=YYYY-MM) байгууллага бүрд DRAFT нэхэмжлэл үүсгэнэ.
    Аль хэдийн байгаа (period, company) хосыг алгасна — дахин дуудахад аюулгүй."""
    start, end = month_range_utc(period)
    existing = {c for (c,) in db.query(CompanyInvoice.company)
                .filter(CompanyInvoice.period == period).all()}
    # Байгууллага бүрийн идэвхтэй машид
    by_company: dict[str, list[RegisteredDriver]] = {}
    for d in db.query(RegisteredDriver).filter(RegisteredDriver.is_active.is_(True)).all():
        comp = (d.company or "").strip()
        if comp:
            by_company.setdefault(comp, []).append(d)

    seq = db.query(CompanyInvoice).filter(CompanyInvoice.period == period).count()
    out = []
    for comp, drivers in sorted(by_company.items()):
        if comp in existing:
            continue
        cars = [{"plate": d.plate_number, "fee": float(d.monthly_fee or 0),
                 "name": d.full_name or ""} for d in drivers]
        amount = sum(c["fee"] for c in cars)
        usage = _usage(db, {c["plate"] for c in cars},
                       {d.site_id for d in drivers}, start, end)
        seq += 1
        inv = CompanyInvoice(
            invoice_no=f"INV-{period.replace('-', '')}-{seq:03d}",
            period=period, company=comp, car_count=len(cars), amount=amount,
            sessions=usage["sessions"], minutes=usage["minutes"],
            detail={"cars": cars, "usage": usage, "created_by": created_by})
        db.add(inv)
        out.append(inv)
    if out:
        db.commit()
        log.info(f"{period}: {len(out)} байгууллагад нэхэмжлэл үүслээ (нийт {len(by_company)})")
    return out


async def supervisor():
    """Сар бүрийн 1-нд (УБ цагаар, өглөө) өмнөх сарын нэхэмжлэлүүдийг DRAFT-аар
    авто үүсгэнэ. Идемпотент тул өдөрт нэг шалгахад хангалттай."""
    if not settings.invoice_auto_generate:
        return
    await asyncio.sleep(600)  # startup шуугианаас зайлсхийнэ
    while True:
        try:
            local = datetime.utcnow() + TZ
            if local.day == 1:
                db = SessionLocal()
                try:
                    generate_invoices(db, prev_period(), created_by="auto")
                finally:
                    db.close()
        except Exception as e:  # noqa: BLE001
            log.error(f"авто нэхэмжлэлийн алдаа: {e}")
        await asyncio.sleep(6 * 3600)  # өдөрт 4 удаа шалгана (1-нд л ажиллана)
