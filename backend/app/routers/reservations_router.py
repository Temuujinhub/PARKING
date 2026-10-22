"""Зогсоолын урьдчилсан захиалгын API (2026-10-22).

Түнш (wallet) — X-API-Key, /api/v1/reservations (docs/INTEGRATION_API.md §8):
  GET  /api/v1/reservations/availability?site_code&start_time&duration_minutes  — сул байр + үнэ
  POST /api/v1/reservations                    — үүсгэх (PENDING_PAYMENT; transaction_id-тай бол шууд CONFIRMED)
  POST /api/v1/reservations/{id}/confirm       — мөнгө татсаны дараа баталгаажуулах
  POST /api/v1/reservations/{id}/cancel        — цуцлах (буцаалтын дүрэмтэй)
  GET  /api/v1/reservations/{id}               — төлөв + e-Barimt
  GET  /api/v1/reservations?plate=&user_id=&external_ref=&status=  — түншийн өөрийн захиалгууд

Ажилтан (харуул/касс) — JWT, /api/reservations: зогсоолын ойрын/идэвхтэй захиалгууд.
Бизнес логик: services/reservations.py. Алдааны хариу: {"detail": "<тайлбар>", "code": "<КОД>"}.
"""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from ..auth import operator_sites, require
from ..database import get_db
from ..models import Reservation, User
from ..services import reservations as R
from .integration_router import PartnerAuth, _require_pay, require_partner

router = APIRouter(prefix="/api/v1/reservations", tags=["reservations"])
staff_router = APIRouter(prefix="/api/reservations", tags=["reservations"])


def _error(exc: R.ReservationError) -> JSONResponse:
    return JSONResponse({"detail": exc.detail, "code": exc.code, **exc.extra}, status_code=exc.status)


async def _body(request: Request) -> dict:
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        raise HTTPException(422, "JSON объект шаардлагатай") from None
    if not isinstance(data, dict):
        raise HTTPException(422, "JSON объект шаардлагатай")
    return data


@router.get("/availability")
def availability(site_code: str = "", start_time: str = "", duration_minutes: int | None = None,
                 end_time: str | None = None, db: Session = Depends(get_db),
                 partner: PartnerAuth = Depends(require_partner)):
    """Захиалахаас өмнө: тухайн цонхонд сул байр байгаа эсэх, үнэ (байр барихгүй)."""
    try:
        return R.availability(db, partner, site_code, {"start_time": start_time,
                                                        "duration_minutes": duration_minutes,
                                                        "end_time": end_time})
    except R.ReservationError as exc:
        return _error(exc)


@router.post("")
async def create(request: Request, db: Session = Depends(get_db),
                 partner: PartnerAuth = Depends(require_partner)):
    """Захиалга үүсгэнэ. `transaction_id` (+`amount`) ирвэл (мөнгө аль хэдийн татсан) шууд
    баталгаажуулна — нэг алхмын урсгал. Шинэ бол 201, external_ref давталт бол 200."""
    _require_pay(partner)
    body = await _body(request)
    try:
        r, created = R.create(db, partner, body)
        db.commit()
    except R.ReservationError as exc:
        db.rollback()
        return _error(exc)
    if body.get("transaction_id") not in (None, ""):
        try:
            r = await R.confirm(db, partner, r, body)
        except R.ReservationError as exc:
            # Захиалга (PENDING_PAYMENT) үүссэн хэвээр — түнш confirm-ийг давтах эсвэл цуцална
            db.rollback()
            exc.extra.setdefault("reservation_id", r.id)
            return _error(exc)
    return JSONResponse(R.serialize(db, r), status_code=201 if created else 200)


@router.get("")
def list_own(plate: str = "", user_id: str = "", external_ref: str = "", status: str = "",
             limit: int = 50, db: Session = Depends(get_db),
             partner: PartnerAuth = Depends(require_partner)):
    from ..session_logic import normalize_plate
    q = db.query(Reservation).filter(Reservation.provider == str(partner))
    if partner.key_id:
        q = q.filter(Reservation.partner_key_id == partner.key_id)
    if plate:
        q = q.filter(Reservation.plate_number == normalize_plate(plate))
    if user_id:
        q = q.filter(Reservation.external_user_id == user_id.strip())
    if external_ref:
        q = q.filter(Reservation.external_ref == external_ref.strip())
    if status:
        q = q.filter(Reservation.status.in_([s.strip().upper() for s in status.split(",") if s.strip()]))
    rows = q.order_by(Reservation.start_time.desc()).limit(max(1, min(int(limit or 50), 200))).all()
    now = datetime.utcnow()
    return {"reservations": [R.serialize(db, r, now, receipts=False) for r in rows]}


@router.get("/{reservation_id}")
async def get_one(reservation_id: str, db: Session = Depends(get_db),
                  partner: PartnerAuth = Depends(require_partner)):
    from ..models import Payment
    from .integration_router import fetch_missing_qr, receipt_list
    try:
        r = R.get_owned(db, partner, reservation_id)
    except R.ReservationError as exc:
        return _error(exc)
    out = R.serialize(db, r)
    payment = db.get(Payment, r.payment_id) if r.payment_id else None
    if payment is not None and payment.status == "PAID":
        qr = await fetch_missing_qr(db, payment)
        if qr:
            out["ebarimts"] = receipt_list(db, payment, qr)
            if out.get("ebarimt") and not out["ebarimt"].get("qr_data"):
                out["ebarimt"]["qr_data"] = next((x["qr_data"] for x in out["ebarimts"]
                                                  if x["ddtd"] == out["ebarimt"]["ddtd"]), None)
    return out


@router.post("/{reservation_id}/confirm")
async def confirm(reservation_id: str, request: Request, db: Session = Depends(get_db),
                  partner: PartnerAuth = Depends(require_partner)):
    _require_pay(partner)
    body = await _body(request)
    try:
        r = R.get_owned(db, partner, reservation_id)
        r = await R.confirm(db, partner, r, body)
        return R.serialize(db, r)
    except R.ReservationError as exc:
        db.rollback()
        return _error(exc)


@router.post("/{reservation_id}/cancel")
async def cancel(reservation_id: str, request: Request, db: Session = Depends(get_db),
                 partner: PartnerAuth = Depends(require_partner)):
    _require_pay(partner)
    try:
        body = await _body(request)
    except HTTPException:
        body = {}
    try:
        r = R.get_owned(db, partner, reservation_id)
        r = await R.cancel(db, partner, r, body)
        return R.serialize(db, r)
    except R.ReservationError as exc:
        db.rollback()
        return _error(exc)


# ───────────────────────── Ажилтан (харуул, касс, самбар) ─────────────────────────

@staff_router.get("")
def staff_list(site_id: str = "", scope: str = "upcoming", limit: int = 100,
               db: Session = Depends(get_db),
               user: User = Depends(require("cashier", "dashboard", "check"))):
    """scope: upcoming (одоогоос хойш 24ц эхлэх + идэвхтэй), today (өнөөдрийн бүх), all (сүүлийн 7 хоног)."""
    now = datetime.utcnow()
    q = db.query(Reservation)
    allowed = operator_sites(user)
    if site_id:
        if allowed is not None and site_id not in allowed:
            raise HTTPException(403, "Зогсоолын эрхгүй")
        q = q.filter(Reservation.site_id == site_id)
    elif allowed is not None:
        q = q.filter(Reservation.site_id.in_(allowed))
    if scope == "today":
        day0 = (now + timedelta(hours=8)).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(hours=8)
        q = q.filter(Reservation.start_time < day0 + timedelta(days=1), Reservation.end_time > day0)
    elif scope == "all":
        q = q.filter(Reservation.start_time >= now - timedelta(days=7))
    else:
        q = q.filter(Reservation.status.in_((R.CONFIRMED, R.CHECKED_IN)),
                     Reservation.end_time > now, Reservation.start_time < now + timedelta(hours=24))
    rows = q.order_by(Reservation.start_time).limit(max(1, min(int(limit or 100), 300))).all()
    return {"reservations": [R.staff_row(db, r, now) for r in rows]}
