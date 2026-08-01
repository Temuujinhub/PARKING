"""Байгууллагын сарын нэхэмжлэл — үүсгэх, Excel татах, и-мэйлээр илгээх, төлөлт хөтлөх."""
import asyncio
import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..auth import require
from ..database import get_db
from ..models import AuditLog, CompanyContact, CompanyInvoice, User
from ..serializers import to_dict
from ..services import mailer
from ..services.invoicing import company_scope, generate_invoices, prev_period
from . import reports_excel as _excel

log = logging.getLogger("parking.billing")
router = APIRouter(prefix="/api/invoices", tags=["invoices"])


def _audit(db, user, action, entity_id, detail=None):
    db.add(AuditLog(username=user.username, action=action, entity="invoice",
                    entity_id=str(entity_id), detail=detail or {}))


def _hm(m: int) -> str:
    return f"{m // 60}ц {m % 60:02d}м" if m else "0м"


@router.get("")
def list_invoices(period: str | None = None, db: Session = Depends(get_db),
                  user: User = Depends(require("reports"))):
    q = db.query(CompanyInvoice).order_by(CompanyInvoice.period.desc(), CompanyInvoice.company)
    if period:
        q = q.filter(CompanyInvoice.period == period)
    scope = company_scope(db, user)  # түрээслэгч зөвхөн өөрийн байгууллагуудыг харна
    rows = [r for r in q.limit(1000).all() if scope is None or r.company in scope][:500]
    # Байгууллагын хадгалсан и-мэйл/горим (илгээх модал + горимын тэмдэг)
    contacts = {c.company: c for c in db.query(CompanyContact).all()}
    return [to_dict(r, extra={
        "company_email": getattr(contacts.get(r.company), "email", ""),
        "billing_mode": getattr(contacts.get(r.company), "billing_mode", "POSTPAID"),
    }) for r in rows]


@router.get("/periods")
def list_periods(db: Session = Depends(get_db), user: User = Depends(require("reports"))):
    rows = (db.query(CompanyInvoice.period).distinct()
            .order_by(CompanyInvoice.period.desc()).all())
    return [p for (p,) in rows]


@router.post("/generate")
def generate(body: dict, db: Session = Depends(get_db), user: User = Depends(require("reports"))):
    period = (body.get("period") or prev_period()).strip()
    if len(period) != 7 or period[4] != "-":
        raise HTTPException(400, "period нь YYYY-MM хэлбэртэй байна")
    created = generate_invoices(db, period, created_by=user.username,
                                companies=company_scope(db, user))
    _audit(db, user, "INVOICE_GENERATE", period, {"created": len(created)})
    db.commit()
    return {"period": period, "created": len(created)}


def _invoice_excel(inv: CompanyInvoice):
    cars = (inv.detail or {}).get("cars", [])
    rows = [[i + 1, c["plate"], c.get("name", ""), f"{c['fee']:,.0f}"] for i, c in enumerate(cars)]
    usage = (inv.detail or {}).get("usage", {})
    title = (f"{inv.invoice_no} · {inv.company} · {inv.period} сарын зогсоолын нэхэмжлэл — "
             f"ашиглалт: {inv.sessions} удаа, {_hm(inv.minutes)}")
    import re as _re
    safe = _re.sub(r"[^A-Za-z0-9_-]+", "_", inv.company).strip("_")[:24] or "invoice"
    return _excel._xlsx(
        f"invoice_{inv.period}_{safe}", title,
        ["№", "Улсын дугаар", "Эзэмшигч", "Сарын хураамж (₮)"],
        rows, widths=[6, 14, 24, 18],
        total_row=["НИЙТ", f"{inv.car_count} машин", "", f"{float(inv.amount):,.0f}"])


def _get_scoped(db, user, invoice_id: str) -> CompanyInvoice:
    inv = db.get(CompanyInvoice, invoice_id)
    if not inv:
        raise HTTPException(404, "Нэхэмжлэл олдсонгүй")
    scope = company_scope(db, user)
    if scope is not None and inv.company not in scope:
        raise HTTPException(403, "Энэ нэхэмжлэл таны байгууллагынх биш байна")
    return inv


@router.post("/contacts")
def set_contact(body: dict, db: Session = Depends(get_db),
                user: User = Depends(require("reports"))):
    """Байгууллагын и-мэйл/төлбөрийн горим тохируулах (Нэхэмжлэл хуудасны Тохиргоо)."""
    company = (body.get("company") or "").strip()
    if not company:
        raise HTTPException(400, "company заавал")
    scope = company_scope(db, user)
    if scope is not None and company not in scope:
        raise HTTPException(403, "Энэ байгууллага таных биш байна")
    mode = (body.get("billing_mode") or "POSTPAID").upper()
    if mode not in ("POSTPAID", "PREPAID", "NONE"):
        raise HTTPException(400, "billing_mode: POSTPAID/PREPAID/NONE")
    c = db.query(CompanyContact).filter(CompanyContact.company == company).first()
    if not c:
        c = CompanyContact(company=company)
        db.add(c)
    c.email = (body.get("email") or c.email or "").strip()
    c.billing_mode = mode
    if body.get("register") is not None:
        c.register = str(body["register"]).strip()
    _audit(db, user, "COMPANY_CONTACT", company, {"mode": mode, "email": c.email})
    db.commit()
    return {"company": company, "email": c.email, "billing_mode": c.billing_mode}


@router.get("/{invoice_id}/excel")
def invoice_excel(invoice_id: str, db: Session = Depends(get_db),
                  user: User = Depends(require("reports"))):
    return _invoice_excel(_get_scoped(db, user, invoice_id))


@router.post("/{invoice_id}/send")
async def send_invoice(invoice_id: str, body: dict, db: Session = Depends(get_db),
                       user: User = Depends(require("reports"))):
    """Нэхэмжлэлийг Excel хавсралттай и-мэйлээр илгээж, байгууллагын и-мэйлийг
    цаашид санана (дараагийн сард автоматаар бөглөгдөнө)."""
    inv = _get_scoped(db, user, invoice_id)
    if inv.status == "CANCELLED":
        raise HTTPException(400, "Цуцлагдсан нэхэмжлэлийг илгээхгүй")
    email = (body.get("email") or "").strip()
    if not email or "@" not in email:
        raise HTTPException(400, "Зөв и-мэйл хаяг оруулна уу")
    if not mailer.smtp_ready():
        raise HTTPException(400, "SMTP тохируулаагүй — .env-д PARKING_SMTP_HOST/USER/PASSWORD "
                                 "нэмээд restart хийнэ үү (Gmail: App Password ашиглана)")
    # Excel-ийг StreamingResponse-оос bytes болгож авна
    resp = _invoice_excel(inv)
    chunks = [c async for c in resp.body_iterator]
    xlsx = b"".join(c if isinstance(c, bytes) else c.encode() for c in chunks)
    subject = f"Зогсоолын нэхэмжлэл {inv.period} — {inv.company} ({inv.invoice_no})"
    text = (f"Сайн байна уу,\n\n{inv.company}-ийн {inv.period} сарын зогсоолын нэхэмжлэлийг "
            f"хүргүүлж байна.\n\n"
            f"  Нэхэмжлэлийн №: {inv.invoice_no}\n"
            f"  Гэрээт машин:   {inv.car_count}\n"
            f"  Нийт дүн:       {float(inv.amount):,.0f}₮\n"
            f"  Сарын ашиглалт: {inv.sessions} удаа, {_hm(inv.minutes)}\n\n"
            f"Машин тус бүрийн задаргаа хавсралтад байгаа.\n\n"
            f"Хүндэтгэсэн,\nEasy Parking")
    try:
        await asyncio.to_thread(mailer.send_mail, email, subject, text,
                                xlsx, f"{inv.invoice_no}.xlsx")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"Илгээж чадсангүй: {e}") from e
    inv.status = "SENT" if inv.status != "PAID" else inv.status
    inv.sent_to = email
    inv.sent_at = datetime.utcnow()
    # Байгууллагын и-мэйлийг санана
    c = db.query(CompanyContact).filter(CompanyContact.company == inv.company).first()
    if not c:
        db.add(CompanyContact(company=inv.company, email=email))
    else:
        c.email = email
    _audit(db, user, "INVOICE_SEND", inv.id, {"to": email, "no": inv.invoice_no})
    db.commit()
    return {"ok": True, "sent_to": email}


@router.post("/{invoice_id}/paid")
def mark_paid(invoice_id: str, body: dict, db: Session = Depends(get_db),
              user: User = Depends(require("reports"))):
    inv = _get_scoped(db, user, invoice_id)
    inv.status = "PAID"
    inv.paid_at = datetime.utcnow()
    if body.get("note"):
        inv.note = str(body["note"])[:500]
    _audit(db, user, "INVOICE_PAID", inv.id, {"no": inv.invoice_no})
    db.commit()
    return to_dict(inv)


@router.post("/{invoice_id}/cancel")
def cancel_invoice(invoice_id: str, body: dict, db: Session = Depends(get_db),
                   user: User = Depends(require("reports"))):
    inv = _get_scoped(db, user, invoice_id)
    if inv.status == "PAID":
        raise HTTPException(400, "Төлөгдсөн нэхэмжлэлийг цуцлахгүй")
    inv.status = "CANCELLED"
    if body.get("note"):
        inv.note = str(body["note"])[:500]
    _audit(db, user, "INVOICE_CANCEL", inv.id, {"no": inv.invoice_no})
    db.commit()
    return to_dict(inv)
