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
from ..models import CompanyContact, CompanyInvoice, ParkingSession, RegisteredDriver

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


def _tenant_sites(db) -> dict:
    """tenant_id → тухайн түрээслэгчийн зогсоолын id-ууд (NULL-site машидын хүрээ)."""
    from ..models import ParkingSite
    out: dict = {}
    for sid, tid in db.query(ParkingSite.id, ParkingSite.tenant_id).all():
        out.setdefault(tid, set()).add(sid)
    return out


def _usage(db, plates: set[str], site_ids: set, start, end) -> dict:
    """Тухайн байгууллагын машидын тухайн сарын ашиглалт (by_company-тэй ижил дүрэм).
    site_ids-д None байвал дуудагч түрээслэгчийн зогсоолуудаар аль хэдийн орлуулсан байх ёстой."""
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


def company_scope(db, user) -> set[str] | None:
    """Хэрэглэгчийн харж болох байгууллагууд (нэхэмжлэлийн tenant салгалт).
    Байгууллага нь идэвхтэй машидынхаа бүртгэлтэй зогсоолоор тодорхойлогдоно:
    түрээслэгчийн хэрэглэгч зөвхөн ӨӨРИЙН зогсоолуудад машинтай байгууллагыг
    харна («Бүх зогсоол» эрхтэй машид операторын түвшнийх тул орохгүй).
    None = хязгааргүй (EasyParking-ийн компанийн түвшний хэрэглэгч)."""
    from ..auth import operator_sites
    allowed = operator_sites(user)
    if allowed is None:
        return None
    aset = set(allowed)
    comps = set()
    for d in db.query(RegisteredDriver).filter(RegisteredDriver.is_active.is_(True)).all():
        comp = (d.company or "").strip()
        if comp and (d.site_id in aset
                     or (d.site_id is None and d.tenant_id
                         and d.tenant_id == getattr(user, "tenant_id", None))):
            comps.add(comp)
    return comps


def billing_modes(db) -> dict[str, str]:
    """company → billing_mode (тохиргоогүй бол POSTPAID)."""
    return {c.company: (c.billing_mode or "POSTPAID")
            for c in db.query(CompanyContact).all()}


def generate_invoices(db, period: str, created_by: str = "system",
                      companies: set[str] | None = None) -> list[CompanyInvoice]:
    """Тухайн сард (period=YYYY-MM) байгууллага бүрд DRAFT нэхэмжлэл үүсгэнэ.
    Аль хэдийн байгаа (period, company) хосыг алгасна — дахин дуудахад аюулгүй.
    companies өгвөл зөвхөн тэдгээрт (tenant scope); NONE горимтой байгууллагыг
    (нэхэмжлэхгүй, зөвхөн бүртгэл) ямагт алгасна."""
    start, end = month_range_utc(period)
    existing = {c for (c,) in db.query(CompanyInvoice.company)
                .filter(CompanyInvoice.period == period).all()}
    modes = billing_modes(db)
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
        if companies is not None and comp not in companies:
            continue
        if modes.get(comp, "POSTPAID") == "NONE":
            continue  # нэхэмжлэхгүй — зөвхөн бүртгэл/тайлангийн зорилготой
        cars = [{"plate": d.plate_number, "fee": float(d.monthly_fee or 0),
                 "name": d.full_name or ""} for d in drivers]
        amount = sum(c["fee"] for c in cars)
        # «Бүх зогсоол» машиныг түрээслэгчийнх нь зогсоолуудаар орлуулна (дамнахгүй)
        tsites = _tenant_sites(db)
        dsites: set = set()
        for d in drivers:
            if d.site_id:
                dsites.add(d.site_id)
            else:
                dsites |= tsites.get(d.tenant_id, set()) if d.tenant_id else {None}
        usage = _usage(db, {c["plate"] for c in cars}, dsites, start, end)
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
                    modes = billing_modes(db)
                    post = {c for c, m in modes.items() if m == "POSTPAID"}
                    pre = {c for c, m in modes.items() if m == "PREPAID"}
                    # Тохиргоогүй байгууллага = POSTPAID (өмнөх сарынх)
                    all_comps = {(d.company or "").strip() for d in
                                 db.query(RegisteredDriver).filter(RegisteredDriver.is_active.is_(True)).all()}
                    all_comps.discard("")
                    post |= all_comps - pre - {c for c, m in modes.items() if m == "NONE"}
                    # Сарын эцэст авдаг: ӨМНӨХ сарын нэхэмжлэл (бодит ашиглалттай)
                    generate_invoices(db, prev_period(), created_by="auto", companies=post)
                    # Урьдчилгаа: ТУХАЙН сарын нэхэмжлэл
                    generate_invoices(db, local.strftime("%Y-%m"), created_by="auto", companies=pre)
                finally:
                    db.close()
        except Exception as e:  # noqa: BLE001
            log.error(f"авто нэхэмжлэлийн алдаа: {e}")
        await asyncio.sleep(6 * 3600)  # өдөрт 4 удаа шалгана (1-нд л ажиллана)
