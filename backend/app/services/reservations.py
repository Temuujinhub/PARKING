"""Зогсоолын урьдчилсан захиалга — wallet-ийн апп (MonPay, SocialPay, Easy Wallet, Ард …).

Загвар нь гадаадын захиалгын платформуудтай ижил (SpotHero, ParkWhiz, JustPark):
  • Үнийг ЗӨВХӨН систем бодно — зогсоолын тарифаар, захиалсан цонхны (start → end) дүн.
  • Байрны тоо (inventory) зогсоол бүрд тусдаа (`reservation_rules.spots`) — зогсоолын нийт
    багтаамж биш; харуул эдгээр байрыг захиалгатай машинд үлдээнэ.
  • Төлбөрийн урсгал нь одоогийн /api/v1/payments-тай ИЖИЛ: create (PENDING_PAYMENT, байрыг
    `hold_minutes` барина) → түнш мөнгө татна → confirm. Нэг алхмаар: create-д
    `transaction_id` + `amount` илгээвэл шууд баталгаажна.
  • Машиныг дугаараар нь таньдаг (LPR) — QR/код шаардлагагүй: эхлэхээс `arrive_early_minutes`
    эрт → дуусах хүртэл орсон машин захиалгатай автоматаар холбогдоно (CHECKED_IN). Урьдчилж
    төлсөн дүн ТЭР сешний төлбөрөөс хасагдана (Payment.session_id) — илүү зогссон хугацааг
    гарахдаа энгийн тарифаар төлнө, эрт гарсан бол буцаалтгүй.
  • Цуцлалт: эхлэхээс `cancel_free_minutes`-ийн өмнө хүртэл бүтэн буцаалт (e-Barimt
    цуцлагдана); түүнээс хойш буцаалтгүй (`accept_no_refund: true`-ээр байрыг чөлөөлж болно);
    эхэлсэн/ирсэн захиалгыг цуцлахгүй. Ирээгүй бол дуусахад NO_SHOW (буцаалтгүй).
  • Баталгаажмагц зогсоолын харуулын дэлгэц (Касс, Хяналтын самбар)-д WebSocket мэдэгдэл,
    түншид webhook (`reservation.*`), e-Barimt-ыг EasyParking гаргана (төлбөрийн API-тай ижил).

Төлөвүүд: PENDING_PAYMENT → CONFIRMED → CHECKED_IN → COMPLETED;
          PENDING_PAYMENT → EXPIRED | CANCELLED;  CONFIRMED → CANCELLED | NO_SHOW.
Баримт бичиг: docs/INTEGRATION_API.md §8.
"""
import asyncio
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, or_, text

from ..models import (AuditLog, FinancialJob, ParkingSession, ParkingSite, PartnerKey, Payment,
                      Reservation, VatReceipt)
from .app_settings import get_reservation_rules

log = logging.getLogger("parking.reservations")

PENDING, CONFIRMED, CHECKED_IN = "PENDING_PAYMENT", "CONFIRMED", "CHECKED_IN"
COMPLETED, CANCELLED, EXPIRED, NO_SHOW = "COMPLETED", "CANCELLED", "EXPIRED", "NO_SHOW"
PAID_STATES = (CONFIRMED, CHECKED_IN, COMPLETED, NO_SHOW)
START_SKEW = timedelta(minutes=5)          # «одоо эхлэх» захиалгад цагийн зөрүүний хүлцэл
_NO_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # 0/O, 1/I-гүй — харуул чангаар уншина
WS_EVENTS = {CONFIRMED: "RESERVATION_CONFIRMED", CHECKED_IN: "RESERVATION_ARRIVED",
             CANCELLED: "RESERVATION_CANCELLED", NO_SHOW: "RESERVATION_NO_SHOW"}


class ReservationError(Exception):
    """Түншид буцаах алдаа: code = машинаар уншигдах код, detail = Монгол тайлбар."""

    def __init__(self, code: str, detail: str, status: int = 400, **extra):
        super().__init__(detail)
        self.code, self.detail, self.status, self.extra = code, detail, status, extra


# ───────────────────────────── Туслах ─────────────────────────────

def iso_z(dt: datetime | None) -> str | None:
    from .partner_vehicles import iso_z as _iso
    return _iso(dt)


def parse_time(value, field: str) -> datetime:
    """ISO-8601 → naive UTC (минут хүртэл). Цагийн бүсгүй утгыг ТААМАГЛАХГҮЙ: «10:00» нь
    УБ эсвэл UTC уу гэдэг 8 цагийн алдаа (тайлангийн 8ц зөрүүний сургамж) — Z/+08:00 заавал."""
    if not isinstance(value, str) or not value.strip():
        raise ReservationError("INVALID_TIME", f"{field} заавал (ISO-8601, ж: 2026-10-22T10:00:00+08:00)", 422)
    raw = value.strip()
    try:
        dt = datetime.fromisoformat(raw[:-1] + "+00:00" if raw.endswith(("Z", "z")) else raw)
    except ValueError:
        raise ReservationError("INVALID_TIME", f"{field} ISO-8601 хэлбэртэй байна", 422) from None
    if dt.tzinfo is None:
        raise ReservationError("INVALID_TIME", f"{field}-д цагийн бүс заавал (Z эсвэл +08:00)", 422)
    return dt.astimezone(timezone.utc).replace(tzinfo=None, second=0, microsecond=0)


def _int(value, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ReservationError("INVALID_REQUEST", f"{field} нь бүхэл тоо (минут)", 422)
    try:
        number = int(str(value).strip())
    except ValueError:
        raise ReservationError("INVALID_REQUEST", f"{field} нь бүхэл тоо (минут)", 422) from None
    return number


def window_from(body: dict) -> tuple[datetime, datetime]:
    """start_time + (duration_minutes эсвэл end_time) → (start, end). Хоёулаа ирвэл тохирох ёстой."""
    start = parse_time(body.get("start_time"), "start_time")
    duration = body.get("duration_minutes")
    end_raw = body.get("end_time")
    if duration is None and end_raw is None:
        raise ReservationError("INVALID_REQUEST", "duration_minutes эсвэл end_time заавал", 422)
    end = parse_time(end_raw, "end_time") if end_raw is not None else None
    if duration is not None:
        minutes = _int(duration, "duration_minutes")
        by_duration = start + timedelta(minutes=minutes)
        if end is not None and end != by_duration:
            raise ReservationError("INVALID_REQUEST", "end_time ба duration_minutes зөрж байна", 422)
        end = by_duration
    if end <= start:
        raise ReservationError("INVALID_WINDOW", "Дуусах цаг эхлэх цагаас хойш байна", 422)
    return start, end


def _minutes(start: datetime, end: datetime) -> int:
    return int((end - start).total_seconds() // 60)


def validate_window(rules: dict, start: datetime, end: datetime, now: datetime) -> None:
    minutes = _minutes(start, end)
    lo, hi = int(rules["min_minutes"]), int(rules["max_minutes"])
    if minutes < lo or minutes > hi:
        raise ReservationError("INVALID_DURATION", f"Захиалгын хугацаа {lo}–{hi} минут байна", 422,
                               min_minutes=lo, max_minutes=hi)
    lead = int(rules["min_lead_minutes"] or 0)
    earliest = now + timedelta(minutes=lead) - (START_SKEW if lead == 0 else timedelta(0))
    if start < earliest:
        raise ReservationError("START_TOO_SOON",
                               "Эхлэх цаг өнгөрсөн байна" if lead == 0
                               else f"Эхлэхээс дор хаяж {lead} минутын өмнө захиална", 422,
                               min_lead_minutes=lead)
    if start > now + timedelta(days=int(rules["max_days_ahead"])):
        raise ReservationError("START_TOO_FAR",
                               f"{int(rules['max_days_ahead'])} хоногоос хойших захиалга авахгүй", 422,
                               max_days_ahead=int(rules["max_days_ahead"]))


def site_for(db, site_code, partner) -> ParkingSite:
    if not isinstance(site_code, str) or not site_code.strip():
        raise ReservationError("INVALID_REQUEST", "site_code заавал", 422)
    site = db.query(ParkingSite).filter(ParkingSite.site_code == site_code.strip()).first()
    if site is None or not site.is_active:
        raise ReservationError("SITE_NOT_FOUND", "Зогсоол олдсонгүй", 404)
    from .partner_scope import allows, partner_ids
    if not allows(partner_ids(partner), site.id):
        raise ReservationError("FORBIDDEN", "Энэ түлхүүр өөр зогсоолд хандах эрхгүй", 403)
    if site.parent_site_id:
        raise ReservationError("NOT_SUPPORTED", "Доторх (nested) зогсоолд захиалга авахгүй — "
                                                "гадна зогсоолыг захиална", 400)
    return site


def site_rules(db, site: ParkingSite) -> dict:
    rules = get_reservation_rules(db, site.id)
    if not rules["enabled"] or int(rules["spots"] or 0) <= 0:
        raise ReservationError("RESERVATIONS_DISABLED", "Энэ зогсоол захиалга авдаггүй", 409)
    return rules


def quote(site: ParkingSite, start: datetime, end: datetime) -> dict:
    """Цонхны дүн — сешний төлбөртэй ЯГ ИЖИЛ тариф (calculate_fee), тиймээс урьдчилсан төлбөр
    машин орсон сешнээс хасагдахад давхардал/зөрүү үүсэхгүй."""
    from ..billing import calculate_fee
    fee = calculate_fee(site.tariff_template, start, end,
                        no_charge=bool(getattr(site, "no_charge", False)))
    return {"amount": float(fee["total_fee"]), "vat_amount": float(fee["vat_amount"]),
            "base_fee": float(fee["base_fee"]), "fee": fee}


def _blocking(now: datetime):
    """Байр эзэлдэг захиалга: баталгаажсан/ирсэн, эсвэл төлбөр хүлээж буй (барих хугацаандаа)."""
    return or_(Reservation.status.in_((CONFIRMED, CHECKED_IN)),
               and_(Reservation.status == PENDING, Reservation.hold_expires_at > now))


def reserved_count(db, site_id: str, start: datetime, end: datetime, now: datetime,
                   exclude_id: str | None = None) -> int:
    q = db.query(func.count(Reservation.id)).filter(
        Reservation.site_id == site_id, Reservation.start_time < end,
        Reservation.end_time > start, _blocking(now))
    if exclude_id:
        q = q.filter(Reservation.id != exclude_id)
    return int(q.scalar() or 0)


def reservation_summary(db, sites, now: datetime | None = None) -> dict:
    """Газрын зурагт (/parkings, /sites): зогсоол бүр захиалга авдаг эсэх, байрны тоо, ОДОО
    сул захиалгын байр. Тодорхой цонхны сул байрыг availability-ээр шалгана."""
    now = now or datetime.utcnow()
    ids = [s.id for s in sites]
    busy = dict(db.query(Reservation.site_id, func.count(Reservation.id)).filter(
        Reservation.site_id.in_(ids), Reservation.start_time <= now, Reservation.end_time > now,
        _blocking(now)).group_by(Reservation.site_id).all()) if ids else {}
    out = {}
    for s in sites:
        rules = get_reservation_rules(db, s.id)
        spots = int(rules["spots"] or 0)
        on = (bool(rules["enabled"]) and spots > 0 and not s.parent_site_id
              and not bool(getattr(s, "no_charge", False)))
        out[s.id] = {"reservable": on, "spots": spots if on else 0,
                     "free_now": max(0, spots - int(busy.get(s.id, 0))) if on else 0}
    return out


def _lock_site(db, site_id: str) -> None:
    """Нэг зогсоолын захиалгуудыг цувруулна — хоёр зэрэг хүсэлт сүүлийн нэг байрыг хоёулаа авахгүй."""
    try:
        dialect = db.get_bind().dialect.name
    except Exception:  # noqa: BLE001 — тестийн хуурамч session
        return
    if dialect == "postgresql":   # тестийн SQLite-д advisory lock байхгүй
        db.execute(text("SELECT pg_advisory_xact_lock(:k)"),
                   {"k": int(uuid.UUID(str(site_id)).hex[:15], 16)})


def _new_no(db) -> str:
    for _ in range(20):
        no = "R" + "".join(secrets.choice(_NO_ALPHABET) for _ in range(5))
        if not db.query(Reservation.id).filter(Reservation.reservation_no == no).first():
            return no
    raise ReservationError("INTERNAL_ERROR", "Захиалгын дугаар үүсгэж чадсангүй", 500)


def _partner_hook(db, r: Reservation) -> str | None:
    if not r.partner_key_id:
        return None
    key = db.get(PartnerKey, r.partner_key_id)
    if key is None or not key.is_active:
        return None
    return (key.webhook_url or "").strip() or None


def _lock(db, reservation_id: str) -> Reservation:
    r = (db.query(Reservation).enable_eagerloads(False).filter(Reservation.id == reservation_id)
         .populate_existing().with_for_update().first())
    if r is None:
        raise ReservationError("NOT_FOUND", "Захиалга олдсонгүй", 404)
    return r


# ───────────────────────────── Харуулах хэлбэр ─────────────────────────────

def serialize(db, r: Reservation, now: datetime | None = None, *, rules: dict | None = None,
              receipts: bool = True) -> dict:
    """Түншид буцаах захиалгын объект (snake_case, бүх цаг UTC «…Z»)."""
    now = now or datetime.utcnow()
    rules = rules or get_reservation_rules(db, r.site_id)
    early = timedelta(minutes=int(rules["arrive_early_minutes"] or 0))
    free_until = r.start_time - timedelta(minutes=int(rules["cancel_free_minutes"] or 0))
    payment = db.get(Payment, r.payment_id) if r.payment_id else None
    if r.status == PENDING:
        cancellable, refund_now = True, 0.0
    elif r.status == CONFIRMED and now < r.end_time:
        cancellable = True
        refund_now = float(r.amount) if now <= free_until else 0.0
    else:
        cancellable, refund_now = False, None
    out = {
        "reservation_id": r.id, "reservation_no": r.reservation_no, "status": r.status,
        "plate_number": r.plate_number,
        "site_code": r.site.site_code if r.site else None,
        "site_name": r.site.name if r.site else None,
        "address": (r.site.address or "") if r.site else "",
        "start_time": iso_z(r.start_time), "end_time": iso_z(r.end_time),
        "duration_minutes": r.duration_minutes,
        "amount": float(r.amount), "vat_amount": float(r.vat_amount or 0), "currency": "MNT",
        "payment_id": r.payment_id,
        "transaction_id": payment.provider_payment_id if payment else None,
        "external_ref": r.external_ref, "user_id": r.external_user_id,
        "arrive_from": iso_z(r.start_time - early),
        "free_cancel_until": iso_z(free_until),
        "cancellable": cancellable, "refund_if_cancelled_now": refund_now,
        "created_at": iso_z(r.created_at), "hold_expires_at": iso_z(r.hold_expires_at)
        if r.status == PENDING else None,
        "confirmed_at": iso_z(r.confirmed_at), "checked_in_at": iso_z(r.checked_in_at),
        "completed_at": iso_z(r.completed_at), "cancelled_at": iso_z(r.cancelled_at),
        "cancel_reason": r.cancel_reason,
        "refund_amount": float(r.refund_amount) if r.refund_amount is not None else None,
        "session_id": r.session_id,
    }
    if receipts and payment is not None:
        from ..routers.integration_router import receipt_info, receipt_list
        out["ebarimt"] = receipt_info(db, payment)
        out["ebarimts"] = receipt_list(db, payment)
    return out


def staff_row(db, r: Reservation, now: datetime | None = None) -> dict:
    """Харуул/операторын дэлгэцийн мөр (UB-ын цагийг frontend хөрвүүлнэ — UTC «Z»)."""
    return {**serialize(db, r, now, receipts=False), "provider": r.provider,
            "contact_phone": r.contact_phone, "site_id": r.site_id}


def ws_payload(r: Reservation) -> dict:
    return {"reservation_id": r.id, "reservation_no": r.reservation_no, "plate": r.plate_number,
            "status": r.status, "provider": r.provider,
            "start_time": iso_z(r.start_time), "end_time": iso_z(r.end_time),
            "amount": float(r.amount), "session_id": r.session_id}


def notify_staff(r: Reservation) -> None:
    """Харуулын дэлгэц рүү (хүлээлгэлгүй). Sync thread-ээс дуудвал loop байхгүй — алгасна."""
    event = WS_EVENTS.get(r.status)
    if not event:
        return
    from ..ws import notify
    notify(r.site_id, event, ws_payload(r))


def enqueue_event(db, r: Reservation, event: str) -> None:
    """Түншийн webhook (`reservation.*`) — төлбөрийн webhook-той ИЖИЛ outbox (24 оролдлого).
    `reservation.confirmed` нь `partner-paid:{payment_id}` түлхүүртэй: e-Barimt гармагц
    `receipt.ready` ч энэ URL руу автоматаар очно (financial_jobs.enqueue_receipt_ready)."""
    hook = _partner_hook(db, r)
    if not hook or not r.payment_id:
        return
    key = (f"partner-paid:{r.payment_id}" if event == "reservation.confirmed"
           else f"reservation:{r.id}:{event}")
    if db.query(FinancialJob.id).filter(FinancialJob.job_key == key).first():
        return
    event_id = str(uuid.uuid4())
    body = serialize(db, r, receipts=False)
    db.add(FinancialJob(id=event_id, job_key=key, kind="PARTNER", payment_id=r.payment_id,
                        payload={"url": hook, "partner": r.provider,
                                 "event": {"event": event, "event_id": event_id,
                                           "reservation": body}}))


def _audit(db, r: Reservation, action: str, who: str, **detail) -> None:
    db.add(AuditLog(username=who, action=action, entity="reservation", entity_id=r.id,
                    detail={"no": r.reservation_no, "plate": r.plate_number,
                            "status": r.status, **detail}))


# ───────────────────────────── Түншийн үйлдлүүд ─────────────────────────────

def availability(db, partner, site_code, body: dict, now: datetime | None = None) -> dict:
    now = now or datetime.utcnow()
    site = site_for(db, site_code, partner)
    rules = site_rules(db, site)
    start, end = window_from(body)
    validate_window(rules, start, end, now)
    used = reserved_count(db, site.id, start, end, now)
    spots = int(rules["spots"])
    price = quote(site, start, end)
    return {"site_code": site.site_code, "site_name": site.name,
            "start_time": iso_z(start), "end_time": iso_z(end),
            "duration_minutes": _minutes(start, end),
            "available": used < spots and price["amount"] > 0,
            "spots": spots, "reserved": used, "free_spots": max(0, spots - used),
            "amount": price["amount"], "vat_amount": price["vat_amount"], "currency": "MNT",
            "rules": {k: rules[k] for k in ("min_minutes", "max_minutes", "max_days_ahead",
                                            "min_lead_minutes", "hold_minutes",
                                            "arrive_early_minutes", "cancel_free_minutes")}}


def _same_request(r: Reservation, site: ParkingSite, plate: str, start: datetime, end: datetime) -> bool:
    return (r.site_id == site.id and r.plate_number == plate
            and r.start_time == start and r.end_time == end)


def create(db, partner, body: dict, now: datetime | None = None) -> tuple[Reservation, bool]:
    """Захиалга үүсгэнэ (PENDING_PAYMENT). Буцаана: (захиалга, шинээр_үүссэн_эсэх).
    `external_ref` давтагдвал (timeout-ийн дараах давталт) ИЖИЛ захиалгыг буцаана."""
    from .partner_vehicles import VehicleInputError, parse_plate, parse_user_id
    now = now or datetime.utcnow()
    site = site_for(db, body.get("site_code"), partner)
    rules = site_rules(db, site)
    try:
        plate = parse_plate(body.get("plate_number"))
        user_id = parse_user_id(body["user_id"]) if body.get("user_id") not in (None, "") else None
    except VehicleInputError as exc:
        code = "INVALID_PLATE" if exc.code == "INVALID_PLATE" else "INVALID_USER_ID"
        raise ReservationError(code, exc.detail, 400) from None
    start, end = window_from(body)
    ref = body.get("external_ref")
    if ref is not None:
        if isinstance(ref, int) and not isinstance(ref, bool):
            ref = str(ref)
        if not isinstance(ref, str) or not ref.strip() or len(ref.strip()) > 120:
            raise ReservationError("INVALID_REQUEST", "external_ref нь 1–120 тэмдэгт", 422)
        ref = ref.strip()
    phone = body.get("phone")
    phone = str(phone).strip()[:20] if phone not in (None, "") else None

    _lock_site(db, site.id)
    if ref:
        prev = (db.query(Reservation).filter(Reservation.partner_key_id == partner.key_id,
                                             Reservation.provider == str(partner),
                                             Reservation.external_ref == ref).first())
        if prev is not None:
            if not _same_request(prev, site, plate, start, end):
                raise ReservationError("EXTERNAL_REF_CONFLICT",
                                       "Энэ external_ref өөр захиалгад ашиглагдсан", 409,
                                       reservation_id=prev.id)
            return prev, False
    validate_window(rules, start, end, now)
    clash = (db.query(Reservation).filter(Reservation.plate_number == plate,
                                          Reservation.start_time < end, Reservation.end_time > start,
                                          _blocking(now)).first())
    if clash is not None:
        raise ReservationError("DUPLICATE_RESERVATION",
                               "Энэ машин энэ цагт аль хэдийн захиалгатай", 409,
                               reservation_id=clash.id if clash.provider == str(partner) else None)
    spots = int(rules["spots"])
    if reserved_count(db, site.id, start, end, now) >= spots:
        raise ReservationError("NO_AVAILABILITY", "Энэ цагт захиалах сул байр алга", 409, spots=spots)
    price = quote(site, start, end)
    if price["amount"] <= 0:
        raise ReservationError("NOTHING_TO_CHARGE", "Энэ цонхны төлбөр 0₮ — захиалга шаардлагагүй", 400)

    no = _new_no(db)
    r = Reservation(id=str(uuid.uuid4()), reservation_no=no, partner_key_id=partner.key_id,
                    provider=str(partner), site_id=site.id, plate_number=plate,
                    start_time=start, end_time=end, duration_minutes=_minutes(start, end),
                    amount=price["amount"], vat_amount=price["vat_amount"], status=PENDING,
                    external_ref=ref, external_user_id=user_id, contact_phone=phone,
                    hold_expires_at=now + timedelta(minutes=int(rules["hold_minutes"])),
                    raw={"quote": {k: price["fee"].get(k) for k in
                                   ("base_fee", "vat_amount", "total_fee", "chargeable_minutes")}},
                    created_at=now, updated_at=now)
    # Урьдчилсан төлбөр — зогсолтгүй (session_id NULL) Payment; машин ормогц тэр сешнд холбогдож
    # paid_total-д тоологдоно (fee_snapshot.parking_amount = бүтэн дүн, өргүй).
    payment = Payment(id=str(uuid.uuid4()), kind="RESERVATION", site_id=site.id, session_id=None,
                      provider=str(partner), payment_method="WALLET", source="WALLET",
                      partner_key_id=partner.key_id,
                      sender_invoice_no=f"RSV-{no}-{uuid.uuid4().hex[:6]}",
                      amount=price["amount"], vat_amount=price["vat_amount"], status="PENDING",
                      fee_snapshot={**price["fee"], "parking_amount": price["amount"], "debts": [],
                                    "reservation_id": r.id},
                      raw_payload={"reservation_no": no, "plate_number": plate}, created_at=now)
    db.add(payment)
    db.flush()
    r.payment_id = payment.id
    db.add(r)
    _audit(db, r, "RESERVATION_CREATED", f"partner:{partner}", amount=price["amount"],
           start=iso_z(start), end=iso_z(end), site=site.site_code)
    db.flush()
    return r, True


async def confirm(db, partner, r: Reservation, body: dict, now: datetime | None = None) -> Reservation:
    """Түнш мөнгө татсаны дараа. Идемпотент: ижил transaction_id + дүнгээр давтахад алдаагүй."""
    from .financial_jobs import enqueue_receipt
    from .payment_validation import claim_reference, money, transaction_reference
    now = now or datetime.utcnow()
    _check_owner(r, partner)
    reference = transaction_reference(body.get("transaction_id"))
    r = _lock(db, r.id)
    payment = (db.query(Payment).enable_eagerloads(False).filter(Payment.id == r.payment_id)
               .populate_existing().with_for_update().one())
    paid_amount = money(body["amount"]) if "amount" in body else money(r.amount)
    if r.status in PAID_STATES:
        if payment.provider_payment_id != reference or paid_amount != money(payment.amount):
            raise ReservationError("ALREADY_CONFIRMED",
                                   "Захиалга өөр гүйлгээ/дүнгээр аль хэдийн баталгаажсан", 409)
        return r
    if r.status == CANCELLED:
        raise ReservationError("RESERVATION_CANCELLED",
                               "Захиалга цуцлагдсан — татсан мөнгийг хэрэглэгчид буцаана уу", 409)
    if paid_amount != money(r.amount):
        raise ReservationError("AMOUNT_MISMATCH", f"Дүн зөрүүтэй: систем {float(r.amount)}₮ хүлээж байна",
                               400, amount=float(r.amount))
    if r.status == EXPIRED or (r.hold_expires_at and r.hold_expires_at <= now):
        # Барих хугацаа дууссан ч мөнгө татагдсан: байр сул, цонх хүчинтэй бол хүлээн авна.
        rules = get_reservation_rules(db, r.site_id)
        _lock_site(db, r.site_id)
        if (r.end_time <= now
                or reserved_count(db, r.site_id, r.start_time, r.end_time, now, exclude_id=r.id)
                >= int(rules["spots"] or 0)):
            r.status = EXPIRED
            payment.status = "CANCELLED"
            db.commit()
            raise ReservationError("HOLD_EXPIRED", "Төлбөр хүлээх хугацаа дууссан, байр сул алга — "
                                                   "татсан мөнгийг хэрэглэгчид буцаана уу", 409)
    claim_reference(db, payment, partner.key_id or f"legacy:{partner}", reference)
    payment.status, payment.paid_at = "PAID", now
    payment.raw_payload = {**(payment.raw_payload or {}), "partner": str(partner),
                           "transaction_id": reference}
    r.status, r.confirmed_at, r.hold_expires_at, r.updated_at = CONFIRMED, now, None, now
    enqueue_receipt(db, payment, description=f"Зогсоолын захиалга {r.reservation_no} · {r.plate_number}")
    _audit(db, r, "RESERVATION_CONFIRMED", f"partner:{partner}", transaction_id=reference,
           amount=float(r.amount))
    _link_if_already_inside(db, r, now)
    db.flush()
    enqueue_event(db, r, "reservation.confirmed")
    if r.status == CHECKED_IN:
        enqueue_event(db, r, "reservation.checked_in")
    db.commit()
    notify_staff(r)
    return r


def _check_owner(r: Reservation, partner) -> None:
    if r.provider != str(partner) or (r.partner_key_id and r.partner_key_id != partner.key_id):
        raise ReservationError("NOT_FOUND", "Захиалга олдсонгүй", 404)


def get_owned(db, partner, reservation_id: str) -> Reservation:
    try:
        uuid.UUID(str(reservation_id))
    except ValueError:
        raise ReservationError("NOT_FOUND", "Захиалга олдсонгүй", 404) from None
    r = db.get(Reservation, reservation_id)
    if r is None:
        raise ReservationError("NOT_FOUND", "Захиалга олдсонгүй", 404)
    _check_owner(r, partner)
    return r


async def cancel(db, partner, r: Reservation, body: dict, now: datetime | None = None,
                 who: str | None = None) -> Reservation:
    """Цуцлах. Буцаах дүнг `refund_amount`-д буцаана — мөнгийг ТҮНШ хэрэглэгчдээ буцаана."""
    now = now or datetime.utcnow()
    if partner is not None:
        _check_owner(r, partner)
    who = who or f"partner:{partner}"
    reason = str(body.get("reason") or "").strip()[:300] or None
    r = _lock(db, r.id)
    if r.status == CANCELLED:
        return r
    payment = (db.query(Payment).enable_eagerloads(False).filter(Payment.id == r.payment_id)
               .populate_existing().with_for_update().first()) if r.payment_id else None
    rules = get_reservation_rules(db, r.site_id)
    free_until = r.start_time - timedelta(minutes=int(rules["cancel_free_minutes"] or 0))
    refund = 0.0
    if r.status in (PENDING, EXPIRED):
        if payment is not None and payment.status == "PENDING":
            payment.status = "CANCELLED"
    elif r.status == CONFIRMED and now < r.end_time:
        if now <= free_until:
            refund = float(r.amount)
        elif body.get("accept_no_refund") is not True:
            raise ReservationError("NO_REFUND_WINDOW",
                                   "Үнэгүй цуцлах хугацаа өнгөрсөн — буцаалтгүй цуцлах бол "
                                   "accept_no_refund: true илгээнэ", 409,
                                   free_cancel_until=iso_z(free_until))
    else:
        raise ReservationError("NOT_CANCELLABLE", f"{r.status} төлөвтэй захиалгыг цуцлахгүй", 409)
    r.status, r.cancelled_at, r.cancelled_by, r.cancel_reason = CANCELLED, now, who, reason
    r.refund_amount, r.hold_expires_at, r.updated_at = refund, None, now
    if refund and payment is not None:
        payment.status = "REFUNDED"
        payment.raw_payload = {**(payment.raw_payload or {}), "refunded_at": now.isoformat(),
                               "refund_amount": refund}
        _void_unsent_receipts(db, payment)
    _audit(db, r, "RESERVATION_CANCELLED", who, refund_amount=refund, reason=reason)
    db.flush()
    enqueue_event(db, r, "reservation.cancelled")
    db.commit()
    if refund and payment is not None:
        await _cancel_sent_receipts(db, r, payment)
    notify_staff(r)
    return r


def _void_unsent_receipts(db, payment: Payment) -> None:
    """Илгээгээгүй (PENDING/FAILED) баримтыг буцаалттай хамт хүчингүй болгоно — outbox нь
    PAID биш төлбөрт баримт гаргахгүй (deliver: «Payment is not PAID»)."""
    for rec in (db.query(VatReceipt).filter(VatReceipt.payment_id == payment.id,
                                            VatReceipt.status.in_(("PENDING", "FAILED"))).all()):
        job = db.query(FinancialJob).filter(FinancialJob.receipt_id == rec.id).first()
        if job is not None and job.status == "SENDING":
            continue   # яг илгээж байна — үр дүнг нь санхүү Ибаримт хуудаснаас цуцална
        rec.status, rec.receipt_url = "CANCELLED", "Захиалга цуцлагдаж буцаагдсан — баримт гаргаагүй"
        if job is not None and job.status not in ("DONE",):
            job.status, job.last_error = "BLOCKED", "Захиалга цуцлагдсан (буцаалт)"


async def _cancel_sent_receipts(db, r: Reservation, payment: Payment) -> None:
    """Гарсан (SENT) e-Barimt-ыг буцаалтаар цуцална. Амжилтгүй бол санхүүд аудит үлдээнэ —
    захиалгын цуцлалт (мөнгөний шийдвэр) баримтын сувгаас хамаарахгүй."""
    if not db.query(VatReceipt.id).filter(VatReceipt.payment_id == payment.id,
                                          VatReceipt.status.in_(("SENT", "CANCEL_PENDING"))).first():
        return
    from ..routers.payments_router import cancel_ebarimt
    try:
        result = await asyncio.wait_for(
            cancel_ebarimt(db, payment, f"Зогсоолын захиалга {r.reservation_no} цуцлагдаж буцаагдсан"),
            timeout=20)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        result = {"ok": False, "error": str(exc)[:200]}
    if not result.get("ok"):
        db.add(AuditLog(username="system", action="RESERVATION_RECEIPT_CANCEL_FAILED",
                        entity="payment", entity_id=payment.id,
                        detail={"reservation": r.reservation_no, "error": result.get("error"),
                                "pending": result.get("pending")}))
        db.commit()
        log.warning("захиалга %s: буцаалтын e-Barimt цуцлагдсангүй — %s", r.reservation_no,
                    result.get("error"))


# ───────────────────────────── Орох камер ─────────────────────────────

def find_for_entry(db, site_id: str, plate: str, now: datetime) -> Reservation | None:
    """Энэ зогсоолд орж буй машины БАТАЛГААЖСАН захиалга (эрт ирэх хүлцэлтэй)."""
    if not plate:
        return None
    early = int(get_reservation_rules(db, site_id)["arrive_early_minutes"] or 0)
    return (db.query(Reservation).filter(
        Reservation.site_id == site_id, Reservation.plate_number == plate,
        Reservation.status == CONFIRMED,
        Reservation.start_time <= now + timedelta(minutes=early),
        Reservation.end_time > now).order_by(Reservation.start_time).first())


def check_in(db, r: Reservation, session: ParkingSession, now: datetime) -> None:
    """Захиалгыг сешнтэй холбоно: урьдчилсан төлбөр энэ сешний paid_total-д орно. Commit
    ХИЙХГҮЙ (орох камерын транзакцтай хамт); мэдэгдлийг дуудагч commit-ийн дараа илгээнэ."""
    r.status, r.checked_in_at, r.session_id, r.updated_at = CHECKED_IN, now, session.id, now
    payment = db.get(Payment, r.payment_id) if r.payment_id else None
    if payment is not None and payment.status == "PAID" and payment.session_id is None:
        payment.session_id = session.id
    _audit(db, r, "RESERVATION_CHECKED_IN", "system", session_id=session.id)
    db.flush()
    enqueue_event(db, r, "reservation.checked_in")


def _link_if_already_inside(db, r: Reservation, now: datetime) -> None:
    """Баталгаажих мөчид машин аль хэдийн (энэ захиалгаар) орчихсон бол шууд холбоно."""
    early = timedelta(minutes=int(get_reservation_rules(db, r.site_id)["arrive_early_minutes"] or 0))
    if now < r.start_time - early:
        return
    session = (db.query(ParkingSession).filter(
        ParkingSession.site_id == r.site_id, ParkingSession.plate_number == r.plate_number,
        ParkingSession.status.in_(("OPEN", "AWAITING_PAYMENT")),
        ParkingSession.entry_time >= r.start_time - early,
        ParkingSession.entry_time < r.end_time).order_by(ParkingSession.entry_time.desc()).first())
    if session is not None and not (db.query(Reservation.id).filter(
            Reservation.session_id == session.id, Reservation.id != r.id).first()):
        check_in(db, r, session, now)


# ───────────────────────────── Арын цэвэрлэгээ ─────────────────────────────

def sweep(db, now: datetime | None = None) -> dict:
    """Барих хугацаа дууссан → EXPIRED; ирээгүй дууссан → NO_SHOW; сешн хаагдсан → COMPLETED."""
    now = now or datetime.utcnow()
    out = {"expired": 0, "no_show": 0, "completed": 0}
    changed: list[Reservation] = []
    # FOR UPDATE + joined eager load (site) нь PG-д «nullable side of outer join» алдаа өгнө
    for r in (db.query(Reservation).enable_eagerloads(False)
              .filter(Reservation.status == PENDING, Reservation.hold_expires_at <= now)
              .with_for_update(skip_locked=True).limit(500).all()):
        r.status, r.updated_at = EXPIRED, now
        payment = db.get(Payment, r.payment_id) if r.payment_id else None
        if payment is not None and payment.status == "PENDING":
            payment.status = "CANCELLED"
        _audit(db, r, "RESERVATION_EXPIRED", "system")
        out["expired"] += 1
    for r in (db.query(Reservation).enable_eagerloads(False)
              .filter(Reservation.status == CONFIRMED, Reservation.end_time <= now)
              .with_for_update(skip_locked=True).limit(500).all()):
        r.status, r.updated_at = NO_SHOW, now
        _audit(db, r, "RESERVATION_NO_SHOW", "system")
        db.flush()
        enqueue_event(db, r, "reservation.no_show")
        changed.append(r)
        out["no_show"] += 1
    rows = (db.query(Reservation, ParkingSession.status, ParkingSession.exit_time)
            .join(ParkingSession, ParkingSession.id == Reservation.session_id)
            .filter(Reservation.status == CHECKED_IN,
                    ParkingSession.status.notin_(("OPEN", "AWAITING_PAYMENT", "PAID")))
            .limit(500).all())
    for r, _status, exit_time in rows:
        r.status, r.completed_at, r.updated_at = COMPLETED, exit_time or now, now
        _audit(db, r, "RESERVATION_COMPLETED", "system")
        db.flush()
        enqueue_event(db, r, "reservation.completed")
        out["completed"] += 1
    db.commit()
    for r in changed:
        notify_staff(r)
    return out


async def supervisor():
    from ..database import SessionLocal
    await asyncio.sleep(20)
    while True:
        try:
            with SessionLocal() as db:
                done = sweep(db)
            if any(done.values()):
                log.info("захиалгын цэвэрлэгээ: %s", done)
        except Exception:  # noqa: BLE001
            log.exception("захиалгын цэвэрлэгээ алдаа")
        await asyncio.sleep(60)
