"""SocialPay (Голомт банк) — машин бүртгэл, үнэгүй хугацаа/төлбөр, зогсоолын байршил.

Spec «[SP4.0][1.0.0][2026.10.09] Easy Parking»: {EasyParkingUrl}/socialpay/register,
/socialpay/delete — бүх хүсэлт POST + JSON, хариу {"Success": bool, "Message": str}.
EasyParkingUrl = https://site.easy-parking.mn/api/v1 (тест: https://test.easy-parking.mn/api/v1).

Нэвтрэлт (spec: «EasyParking талд өөрсдийн дотоод журмаар»): X-API-Key — Тохиргоо →
Холболт → Гадаад API-гийн түлхүүр, «vehicles» эрхтэй. Алдааны хариу ч ИЖИЛ хэлбэртэй
(Success=false, Message=машинаар уншигдах код, Detail=тайлбар) + зохих HTTP код —
түншийн клиент аль нэгийг нь шалгаж болно. FastAPI-ийн {"detail": …} хэлбэр энд гарахгүй.

Endpoint-ууд sync (def) — FastAPI threadpool-д ажиллуулна: DB хүлээлт event loop-ийг
(хаалт/LPR) царцаахгүй. Бизнес логик: services/partner_vehicles.py.
Баримт бичиг: docs/SOCIALPAY_INTEGRATION.md.
"""
import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..config import settings
from ..database import get_db
from ..models import ParkingSession, ParkingSite, Payment
from ..ratelimit import throttle
from ..services import partner_vehicles as PV
from .integration_router import PartnerAuth, require_partner, site_catalog

PREFIX = "/api/v1/socialpay"
log = logging.getLogger("parking.socialpay")

_HTTP_CODES = {400: "BAD_REQUEST", 401: "UNAUTHORIZED", 403: "FORBIDDEN", 404: "NOT_FOUND",
               405: "METHOD_NOT_ALLOWED", 422: "INVALID_REQUEST", 429: "RATE_LIMITED",
               500: "INTERNAL_ERROR", 503: "UNAVAILABLE"}


class _SocialPayRoute(APIRoute):
    """Энэ router-ийн гэнэтийн алдааг (500) ч {"Success": false, …} хэлбэрээр буцаана
    (хяналт m6). HTTPException/422-ыг доорх install_error_handlers хэлбэржүүлнэ."""

    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request: Request):
            try:
                return await original(request)
            except (StarletteHTTPException, RequestValidationError):
                raise
            except Exception:  # noqa: BLE001
                log.exception("socialpay %s алдаа", request.url.path)
                return _fail("INTERNAL_ERROR", "Дотоод алдаа — түр хүлээгээд дахин оролдоно уу", 500)
        return handler


router = APIRouter(prefix=PREFIX, tags=["socialpay"], route_class=_SocialPayRoute)


def install_error_handlers(app):
    """/api/v1/socialpay/* дээрх 404/405/422 (байхгүй зам, буруу method, validation)-ийг
    Success/Message/Detail хэлбэрт оруулна. Бусад API-д FastAPI-ийн АНХДАГЧ handler
    хэвээр (зан төлөв өөрчлөгдөхгүй) — зөвхөн замаар шүүнэ."""
    from fastapi.exception_handlers import (http_exception_handler,
                                            request_validation_exception_handler)

    def _ours(request: Request) -> bool:
        path = request.url.path
        return path == PREFIX or path.startswith(PREFIX + "/")

    async def on_http(request: Request, exc: StarletteHTTPException):
        if not _ours(request):
            return await http_exception_handler(request, exc)
        resp = _fail(_HTTP_CODES.get(exc.status_code, "ERROR"), str(exc.detail), exc.status_code)
        for k, v in (getattr(exc, "headers", None) or {}).items():
            resp.headers[k] = v
        return resp

    async def on_validation(request: Request, exc: RequestValidationError):
        if not _ours(request):
            return await request_validation_exception_handler(request, exc)
        return _fail("INVALID_REQUEST", "Хүсэлтийн бүтэц буруу", 422)

    app.add_exception_handler(StarletteHTTPException, on_http)
    app.add_exception_handler(RequestValidationError, on_validation)


def _resp(success: bool, message: str, status: int = 200, **extra) -> JSONResponse:
    return JSONResponse({"Success": success, "Message": message, **extra}, status_code=status)


def _fail(code: str, detail: str, status: int) -> JSONResponse:
    return _resp(False, code, status, Detail=detail)


async def _json_body(request: Request):
    """JSON объект эсвэл None (буруу/хоосон body) — 422 {"detail"} биш өөрсдийн хариу өгнө."""
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        return None
    return data if isinstance(data, dict) else None


def _partner(request: Request, db: Session = Depends(get_db)):
    """require_partner-ийн HTTPException-ийг барьж буцаана (хариуг өөрсдөө хэлбэржүүлнэ)."""
    try:
        return require_partner(request, request.headers.get("x-api-key", ""), db)
    except HTTPException as exc:
        return exc


def _guard(partner, *, need_vehicles: bool = True) -> JSONResponse | None:
    if isinstance(partner, HTTPException):
        return _fail(_HTTP_CODES.get(partner.status_code, "ERROR"), str(partner.detail),
                     partner.status_code)
    if need_vehicles:
        if not partner.key_id:
            return _fail("FORBIDDEN", "Машин бүртгэлд Тохиргоо → Холболт → Гадаад API-гийн "
                                      "түлхүүр шаардлагатай (.env түлхүүр биш)", 403)
        if not partner.has_scope(PV.SCOPE):
            return _fail("FORBIDDEN", "Энэ түлхүүрт «vehicles» (машин бүртгэх) эрх олгоогүй", 403)
        if throttle(f"partner-vehicles:{partner.key_id}",
                    max(1, int(settings.partner_vehicle_rate_per_min)), 60):
            return _fail("RATE_LIMITED", "Минутын хүсэлтийн хязгаар хэтэрлээ", 429)
    return None


def _plate_user(body) -> tuple[str, str]:
    if body is None:
        raise PV.VehicleInputError("INVALID_JSON", 'JSON объект шаардлагатай: {"plateNo","userId"}')
    # Талбарын нэрийн том/жижиг үсгийг үл харгалзана (spec: plateNo/userId; хариу нь
    # Success/Message том үсгээр эхэлдэг тул «PlateNo» ирэх магадлалтай — launch-ийн өмнө
    # дэмий 400-аар цаг алдахгүй).
    lower = {str(k).lower(): v for k, v in body.items()}
    if "plateno" not in lower:
        raise PV.VehicleInputError("INVALID_PLATE", "plateNo заавал")
    if "userid" not in lower:
        raise PV.VehicleInputError("INVALID_USER_ID", "userId заавал")
    return PV.parse_plate(lower["plateno"]), PV.parse_user_id(lower["userid"])


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.post("/register")
def register(request: Request, body=Depends(_json_body), partner=Depends(_partner),
             db: Session = Depends(get_db)):
    """4.1 Машин бүртгэх. Идемпотент: давтан дуудахад Success=true, Message=ALREADY_REGISTERED."""
    err = _guard(partner)
    if err:
        return err
    try:
        plate, user = _plate_user(body)
        msg = PV.register_vehicle(db, partner.key_id, str(partner), plate, user,
                                  via="socialpay", ip=_ip(request))
    except PV.VehicleInputError as exc:
        return _fail(exc.code, exc.detail, exc.status)
    return _resp(True, msg)


@router.post("/delete")
def delete(request: Request, body=Depends(_json_body), partner=Depends(_partner),
           db: Session = Depends(get_db)):
    """4.2 Машин устгах. Бүртгэлгүй бол Success=true, Message=NOT_FOUND (давталт аюулгүй)."""
    err = _guard(partner)
    if err:
        return err
    try:
        plate, user = _plate_user(body)
        msg = PV.delete_vehicle(db, partner.key_id, str(partner), plate, user, ip=_ip(request))
    except PV.VehicleInputError as exc:
        return _fail(exc.code, exc.detail, exc.status)
    return _resp(True, msg)


def _bill_data(db: Session, partner: PartnerAuth, s: ParkingSession) -> dict:
    from ..billing import is_free_minutes_reason
    from ..services.nested import effective_paused_minutes
    from ..session_logic import amount_due, exit_policy_block, paid_total, session_fee_info
    from .payments_router import _pending_debts
    now = datetime.utcnow()
    fee = session_fee_info(db, s)           # ← хаалт/касс/wallet-тай ИЖИЛ тооцоо
    due = amount_due(db, s, fee)
    site = s.site
    free = PV.free_minutes_for(db, s)
    free_until = None
    reason = fee.get("reason") or None
    # «Эхний N минут»-аас ӨӨР шалтгаанаар үнэгүй (гэрээт, төлбөргүй зогсоол, эмнэлгийн
    # хөнгөлөлт, хөнгөлөлт) бол үнэгүй хугацааны дуусах цаг утгагүй — null (хяналт m7)
    window_applies = not fee["is_free"] or is_free_minutes_reason(reason)
    if free and s.entry_time and window_applies:
        # Доторх (nested) зогсоолд өнгөрүүлсэн хугацаа тоолуураас хасагддаг тул сунгана
        free_until = s.entry_time + timedelta(minutes=free + effective_paused_minutes(db, s, now))
    # Өр (хяналт M2): өргүйгээр төлсөн машиныг «N+ өр» дүрэм гарцад БАРЬДАГ. Энэ
    # түрээслэгчийн өрийг include_debts=true intent-ээр хамт төлж болно.
    debts = _pending_debts(db, s.plate_number, site)
    debt_amount = round(sum(float(c.amount) for c in debts), 2)
    block = exit_policy_block(db, s, now)
    block_with_debts = (exit_policy_block(db, s, now, exclude_debt_ids={c.id for c in debts})
                        if debts else block)
    # Онлайн төлөх боломж (хяналт m1): PAID сешний хэтрэлтийг гарах камер уншмагц л
    # нэхэмжилдэг (AWAITING_PAYMENT) — тэр хүртэл POST /payments 400 өгнө (QR-тэй ижил)
    if s.status not in ("OPEN", "AWAITING_PAYMENT"):
        not_payable = "PAID_OVERSTAY" if due > 0 else "ALREADY_PAID"
    elif due <= 0 and debt_amount <= 0:
        not_payable = "NOTHING_DUE"
    else:
        not_payable = None
    intent = (db.query(Payment).filter(Payment.session_id == s.id, Payment.provider == partner,
                                       Payment.status == "PENDING")
              .order_by(Payment.created_at.desc()).first())
    located = site is not None and site.latitude is not None and site.longitude is not None
    return {
        "plateNo": s.plate_number, "sessionId": s.id, "status": s.status,
        "siteCode": site.site_code if site else None, "siteName": site.name if site else None,
        "address": (site.address or "") if site else "",
        "latitude": float(site.latitude) if located else None,
        "longitude": float(site.longitude) if located else None,
        "enteredDate": PV.iso_z(s.entry_time),
        "minutesParked": fee["duration_minutes"],
        "freeMinutes": free,
        "freeUntil": PV.iso_z(free_until),
        "freeRemainingMinutes": (max(0, int((free_until - now).total_seconds() // 60))
                                 if free_until else None),
        "isFree": bool(fee["is_free"]),
        "freeReason": reason,
        "totalFee": fee["total_fee"],
        "paidAmount": paid_total(db, s),
        "amountDue": due,
        "debtAmount": debt_amount,
        "debtCount": len(debts),
        "amountDueWithDebts": round(due + debt_amount, 2),
        "exitBlockReason": block,
        "exitBlockReasonWithDebts": block_with_debts,
        "payableOnline": not_payable is None,
        "notPayableReason": not_payable,
        "currency": "MNT",
        "exitDeadline": PV.iso_z(s.exit_deadline) if s.status == "PAID" else None,
        "paymentId": intent.id if intent else None,
        "paymentAmount": float(intent.amount) if intent else None,
    }


@router.post("/bill")
def bill(body=Depends(_json_body), partner=Depends(_partner), db: Session = Depends(get_db)):
    """Bill дээр харуулах: одоогийн зогсолт, үнэгүй хугацаа, төлөх дүн. Зөвхөн ЭНЭ
    хэрэглэгчийн ЭНЭ түншээр бүртгүүлсэн машинд (бусдын машины байршил задрахгүй)."""
    err = _guard(partner)
    if err:
        return err
    try:
        plate, user = _plate_user(body)
    except PV.VehicleInputError as exc:
        return _fail(exc.code, exc.detail, exc.status)
    if not PV.is_registered_by(db, partner.key_id, plate, user):
        return _fail("NOT_REGISTERED", "Энэ хэрэглэгч энэ машиныг бүртгүүлээгүй байна", 404)
    q = (db.query(ParkingSession).join(ParkingSite, ParkingSite.id == ParkingSession.site_id)
         .filter(ParkingSession.plate_number == plate,
                 ParkingSession.status.in_(PV.ACTIVE),
                 ParkingSite.parent_site_id.is_(None),
                 # идэвхгүй болгосон зогсоолын үлдсэн OPEN сешн «одоо энд байна» биш (m12)
                 ParkingSite.is_active.is_(True)))
    if partner.site_id:
        q = q.filter(ParkingSession.site_id == partner.site_id)
    s = q.order_by(ParkingSession.entry_time.desc()).first()
    if s is None:
        return _resp(True, "NO_ACTIVE_SESSION", data=None)
    data = _bill_data(db, partner, s)
    # session_fee_info is_registered-ийг тэмдэглэж болзошгүй — уншилтын хүсэлтэд хадгалахгүй
    db.rollback()
    return _resp(True, "OK", data=data)


def _parkings(db: Session, partner) -> JSONResponse:
    err = _guard(partner, need_vehicles=False)
    if err:
        return err
    sites, rows, sampled_at = site_catalog(db, partner)
    data = []
    for site, row in zip(sites, rows):
        if site.parent_site_id:
            continue     # доторх зогсоол — гадна зогсоолынхоо дотор, тусдаа цэг биш
        template = site.tariff_template
        data.append({
            "siteCode": row["site_code"], "name": row["name"], "address": row["address"],
            "latitude": row["latitude"], "longitude": row["longitude"],
            "googleMapsUrl": row["google_maps_url"], "locationAvailable": row["location_available"],
            "capacity": row["capacity"], "occupied": row["occupied"], "free": row["free"],
            "freeMinutes": int(getattr(template, "free_minutes", 0) or 0),
            "noCharge": bool(getattr(site, "no_charge", False)),
        })
    return _resp(True, "OK", data=data, sampledAt=sampled_at)


@router.get("/parkings")
def parkings_get(partner=Depends(_partner), db: Session = Depends(get_db)):
    """Зогсоолын байршил (camelCase). /api/v1/sites-тэй ижил эх (site_catalog)."""
    return _parkings(db, partner)


@router.post("/parkings")
def parkings_post(partner=Depends(_partner), db: Session = Depends(get_db)):
    """Spec-ийн «бүх хүсэлт POST» журамд нийцүүлсэн хувилбар (body шаардлагагүй)."""
    return _parkings(db, partner)
