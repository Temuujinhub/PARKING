"""Түншийн машин бүртгэл + орох/гарах мэдэгдэл (SocialPay / Голомт банк, 2026-10-14).

Spec «[SP4.0][1.0.0][2026.10.09] Easy Parking»:
  4.1 register / 4.2 delete — түнш хэрэглэгчийнхээ машиныг бидэнд бүртгүүлнэ
      (routers/socialpay_router.py; энд зөвхөн логик).
  4.3 бүртгэлтэй машин орох/гарах бүрд бид түншийн URL руу POST:
      {"data": {"plateNo": "7886УКР", "enteredDate": "...Z", "exitedDate": "...Z"?}}

МЭДЭГДЭЛ ЯАГААД SCANNER-ААР (hook биш) ВЭ:
  Сешн 10+ замаар үүсч/хаагддаг (live LPR, inner pass, camera_sync нөхөлт, оператор,
  POS, онцгой гаргалт, auto_close, reentry close …) — зам бүрд hook суулгавал хаалт/LPR-ийн
  халуун замд сүлжээ/DB-ийн шинэ эрсдэл орж, нэг зам мартагдахад чимээгүй алдагдана.
  Энд 10с тутам parking_sessions-ийг (индекстэй entry_time/exit_time мужаар) бүртгэлтэй
  дугаартай JOIN хийж шинэ үйл явдлыг outbox-д бичнэ; HTTP нь тусдаа, транзакцгүй.
  Хаалт/LPR-ийн зам огт хөндөгдөхгүй, түнш унасан ч хаалт удаашрахгүй.

ДҮРЭМ (docs/SOCIALPAY_INTEGRATION.md-д тайлбартай):
  • Зөвхөн ГАДНА зогсоол (parent_site_id IS NULL) — доторх (хүүхэд) зогсоолын сешн
    чимээгүй; гадна сешн нь машины бодит айлчлал.
  • ENTRY: бүртгэл сешн БҮРТГЭГДЭХЭЭС өмнө байсан; орсон цаг ≤ entry_max_age (camera_sync
    хэдэн цагийн дараа нөхсөн хуучин оролтыг «саяхан орлоо» гэж бүү спамла); settle
    (30с) — burst autocorrect дугаарыг засах завсар; «зөвхөн бүртгэлтэй» зогсоолд
    татгалзагдсан (хаалт нээгдээгүй) машин орсонд тооцогдохгүй.
  • EXIT: зөвхөн БОДИТ гарц (exit_confirmed эсвэл exit_device_id — камер/оператор/POS).
    Гарах уншилтгүй албадан хаалт (72ц үнэгүй, 12ц phantom, төлсөн-таамаг гарц) spec
    форматад ИЛГЭЭГДЭХГҮЙ — тэр цаг нь гарсан цаг биш (хожим auto_reopen хийгдвэл бодит
    гарц ирнэ). 'extended' форматад exitConfirmed=false-тэй илгээнэ.
    Орох мэдэгдэл илгээгдсэн бол гарцыг нас харгалзахгүй (pair_days хүртэл) илгээнэ —
    түншийн «зогсоолд байна» төлөв хаагдана; хослолгүй гарцыг зөвхөн шинэ бол.
  • Идемпотент: нэг (түнш, сешн, ENTRY|EXIT) — нэг л мэдэгдэл (job_key UNIQUE).
    Давхар орох уншилт шинэ сешн үүсгэдэггүй → давхар ENTRY үгүй; auto_reopen хийсэн
    сешн дахин хаагдахад EXIT дахин явахгүй (эхнийх нь л).
  • Хүргэлт: 2xx + body Success==true = амжилт; бусад нь 1м,5м,15м,1ц,2ц,4ц… 24ц → DEAD.
    Нэг (түнш, дугаар)-ын үйл явдал event_time-ын дарааллаар (өмнөх нь хүлээгдэж байвал
    дараагийнх нь хүлээнэ). Илгээхийн өмнө бүртгэл идэвхтэй эсэхийг дахин шалгана
    (устгасан бол SKIPPED).
"""
import asyncio
import json
import logging
import re
import time
import uuid
from datetime import datetime, timedelta

from sqlalchemy import and_, exists, func, or_, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import aliased

from ..config import settings
from ..database import SessionLocal
from ..models import (AuditLog, ParkingSession, ParkingSite, PartnerKey, PartnerVehicle,
                      PartnerVehicleEvent)
from ..session_logic import PLATE_RE, normalize_plate

log = logging.getLogger("parking.partner_vehicles")

SCOPE = "vehicles"
FORMATS = ("socialpay", "extended")
ACTIVE = ("OPEN", "AWAITING_PAYMENT", "PAID")
CLOSED = ("CLOSED", "FREE", "MANUAL_CLOSED")
IN_FLIGHT = ("PENDING", "RETRY", "SENDING")
RETRY_DELAYS = (60, 300, 900, 3600, 7200, 14400)   # дараа нь 4ц тутам, retry_hours хүртэл
LEASE = timedelta(minutes=2)
# Хэрэглэгчийн ID: тоо эсвэл текст (UUID, утас, имэйл…) — хяналтын тэмдэгт/зай хориотой
_USER_RE = re.compile(r"^[\w.:@+\-]{1,64}$")


class VehicleInputError(ValueError):
    """Түншид буцаах алдаа: code = Message талбарт очих машинаар уншигдах код."""

    def __init__(self, code: str, detail: str, status: int = 400):
        super().__init__(detail)
        self.code, self.detail, self.status = code, detail, status


def has_scope(scopes: str | None, scope: str = SCOPE) -> bool:
    return scope in {s.strip() for s in (scopes or "").split(",")}


def parse_plate(value) -> str:
    """Spec-ийн plateNo → дотоод хэлбэр (normalize_plate: том үсэг, зай/зураасгүй).

    Латин үсгийг кирилл рүү ЗОРИУД хөрвүүлэхгүй: «1234YBA»-г УВА гэж таамаглавал
    ӨӨР хүний машинд бүртгэл үүсч түүний орох/гарах мэдээлэл задарна. PLATE_RE
    (4 цифр + 3 кирилл, дипломат хэлбэрүүд) таарахгүй бол татгалзана."""
    if not isinstance(value, str) or len(value) > 32:
        raise VehicleInputError("INVALID_PLATE", "plateNo нь машины дугаар (текст) байх ёстой")
    plate = normalize_plate(value)
    if not PLATE_RE.match(plate):
        raise VehicleInputError("INVALID_PLATE",
                                "Дугаарын формат буруу — кирилл үсгээр, ж: 7886УКР")
    return plate


def parse_user_id(value) -> str:
    """userId: spec-ийн жишээ 100264 — тоо эсвэл текстээр ирж болно (bool/хоосон хориотой)."""
    if isinstance(value, bool) or value is None:
        raise VehicleInputError("INVALID_USER_ID", "userId заавал")
    if isinstance(value, int):
        value = str(value)
    if not isinstance(value, str):
        raise VehicleInputError("INVALID_USER_ID", "userId нь тоо эсвэл текст байх ёстой")
    value = value.strip()
    if not _USER_RE.match(value):
        raise VehicleInputError("INVALID_USER_ID",
                                "userId 1–64 тэмдэгт (үсэг, цифр, . _ - : @ +) байх ёстой")
    return value


def iso_z(dt: datetime | None) -> str | None:
    """DB-ийн naive UTC → spec-ийн яг хэлбэр «2026-10-06T06:12:48.375Z» (мс, Z)."""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        from datetime import timezone
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


# ─────────────────────────── Бүртгэл (4.1 / 4.2) ───────────────────────────

def _user_lock(db, key_id: str, user_id: str):
    """Нэг хэрэглэгчийн зэрэгцээ register-ийг цувруулна (тоо хязгаарын race). PG-д л."""
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
                   {"k": f"partner-vehicle:{key_id}:{user_id}"})


def _active(db, key_id: str, plate: str, user_id: str):
    return (db.query(PartnerVehicle)
            .filter(PartnerVehicle.partner_key_id == key_id,
                    PartnerVehicle.plate_number == plate,
                    PartnerVehicle.external_user_id == user_id,
                    PartnerVehicle.deleted_at.is_(None)).first())


def register_vehicle(db, key_id: str, partner_name: str, plate: str, user_id: str,
                     via: str = "api", ip: str | None = None) -> str:
    """Идемпотент: аль хэдийн идэвхтэй бол «ALREADY_REGISTERED» (Success=true).
    Буцаах: "OK" | "ALREADY_REGISTERED". Хязгаар хэтэрвэл VehicleInputError(409)."""
    _user_lock(db, key_id, user_id)
    if _active(db, key_id, plate, user_id):
        db.rollback()
        return "ALREADY_REGISTERED"
    count = (db.query(func.count(PartnerVehicle.id))
             .filter(PartnerVehicle.partner_key_id == key_id,
                     PartnerVehicle.external_user_id == user_id,
                     PartnerVehicle.deleted_at.is_(None)).scalar() or 0)
    limit = max(1, int(settings.partner_vehicle_max_per_user))
    if count >= limit:
        db.rollback()
        raise VehicleInputError("LIMIT_EXCEEDED",
                                f"Нэг хэрэглэгч {limit}-аас олон машин бүртгүүлэх боломжгүй", 409)
    row = PartnerVehicle(partner_key_id=key_id, plate_number=plate,
                         external_user_id=user_id, created_via=via)
    db.add(row)
    db.flush()
    db.add(AuditLog(username=f"partner:{partner_name}"[:60], action="PARTNER_VEHICLE_REGISTER",
                    entity="partner_vehicle", entity_id=row.id, ip_address=ip,
                    detail={"plate": plate, "user_id": user_id, "via": via}))
    try:
        db.commit()
    except IntegrityError:
        # Зэрэгцээ ижил хүсэлт (partial unique индекс) — нөгөө нь бүртгэчихсэн
        db.rollback()
        return "ALREADY_REGISTERED"
    return "OK"


def delete_vehicle(db, key_id: str, partner_name: str, plate: str, user_id: str,
                   ip: str | None = None) -> str:
    """Мөрийг устгахгүй — deleted_at тавина. Бүртгэлгүй бол «NOT_FOUND» (Success=true):
    timeout-ийн дараах давтан хүсэлт аюулгүй (DELETE-ийн идемпотент утга)."""
    now = datetime.utcnow()
    rows = (db.query(PartnerVehicle)
            .filter(PartnerVehicle.partner_key_id == key_id,
                    PartnerVehicle.plate_number == plate,
                    PartnerVehicle.external_user_id == user_id,
                    PartnerVehicle.deleted_at.is_(None))
            .with_for_update().all())
    if not rows:
        db.rollback()
        return "NOT_FOUND"
    for row in rows:
        row.deleted_at = now
        db.add(AuditLog(username=f"partner:{partner_name}"[:60], action="PARTNER_VEHICLE_DELETE",
                        entity="partner_vehicle", entity_id=row.id, ip_address=ip,
                        detail={"plate": plate, "user_id": user_id}))
    db.commit()
    return "OK"


def is_registered_by(db, key_id: str, plate: str, user_id: str | None = None) -> bool:
    q = db.query(PartnerVehicle.id).filter(PartnerVehicle.partner_key_id == key_id,
                                           PartnerVehicle.plate_number == plate,
                                           PartnerVehicle.deleted_at.is_(None))
    if user_id is not None:
        q = q.filter(PartnerVehicle.external_user_id == user_id)
    return q.first() is not None


# ─────────────────────────── Үнэгүй хугацаа ───────────────────────────

def free_minutes_for(db, s: ParkingSession) -> int:
    """Үндсэн үнэгүй хугацаа: тарифын free_minutes + гэрээний «эхний N минут».
    (Доторх зогсоолын хасалт, цонхтой гэрээ, хөнгөлөлт amountDue/isFree-д тусна.)"""
    from ..session_logic import find_registered
    site = s.site
    template = site.tariff_template if site else None
    minutes = int(getattr(template, "free_minutes", 0) or 0)
    drv = find_registered(db, s.plate_number, s.site_id)
    if drv is not None and getattr(drv, "free_first_minutes", None):
        minutes += int(drv.free_first_minutes)
    return minutes


# ─────────────────────────── Payload ───────────────────────────

def build_payload(db, fmt: str | None, event: str, s, exit_confirmed: bool = True) -> dict:
    """Spec 4.3: ENTRY → plateNo+enteredDate; EXIT → + exitedDate. 'socialpay' (анхдагч)
    форматад ӨӨР ТАЛБАР НЭМЭХГҮЙ. 'extended' нь data дотор нэмэлт мэдээлэлтэй."""
    data = {"plateNo": s.plate_number, "enteredDate": iso_z(s.entry_time)}
    if event == "EXIT":
        data["exitedDate"] = iso_z(s.exit_time)
    if (fmt or "socialpay") == "extended":
        site = s.site
        data.update({"event": event, "sessionId": s.id,
                     "siteCode": site.site_code if site else None,
                     "siteName": site.name if site else None})
        if event == "ENTRY":
            free = free_minutes_for(db, s)
            data.update({"freeMinutes": free,
                         "freeUntil": iso_z(s.entry_time + timedelta(minutes=free)) if free else None})
        else:
            data.update({"exitConfirmed": bool(exit_confirmed),
                         "minutesParked": s.duration_minutes,
                         "totalFee": float(s.total_fee) if s.total_fee is not None else None,
                         "isFree": s.status == "FREE"})
    return {"data": data}


# ─────────────────────────── Scanner ───────────────────────────

def _job_key(key_id: str, session_id: str, event: str) -> str:
    return f"veh:{key_id}:{session_id}:{event}"


def _configured_keys(db) -> dict:
    rows = (db.query(PartnerKey)
            .filter(PartnerKey.is_active.is_(True), PartnerKey.vehicle_webhook_url.isnot(None),
                    PartnerKey.vehicle_webhook_url != "").all())
    return {k.id: k for k in rows if has_scope(k.scopes)}


def _candidates(db, key_ids, *conds) -> dict:
    """{(key_id, session_id): (row, хамгийн эртний идэвхтэй бүртгэлийн created_at)}."""
    S, PV = ParkingSession, PartnerVehicle
    q = (db.query(S.id, S.site_id, S.plate_number, S.entry_time, S.exit_time, S.created_at,
                  S.status, S.exit_confirmed, S.exit_device_id, S.is_registered,
                  S.entry_device_id, S.fee_locked, ParkingSite.registered_only, PV.partner_key_id,
                  PV.created_at.label("sub_created_at"))
         .join(PV, and_(PV.plate_number == S.plate_number, PV.deleted_at.is_(None),
                        PV.partner_key_id.in_(key_ids)))
         .join(ParkingSite, ParkingSite.id == S.site_id)
         .filter(ParkingSite.parent_site_id.is_(None), *conds))
    out = {}
    for r in q.limit(5000).all():
        k = (r.partner_key_id, r.id)
        if k not in out or r.sub_created_at < out[k][1]:
            out[k] = (r, r.sub_created_at)
    return out


def _real_exit(r) -> bool:
    return r.exit_time is not None and bool(r.exit_confirmed or r.exit_device_id)


def _add_event(db, key: PartnerKey, sid: str, event: str, now: datetime,
               exit_confirmed: bool = True) -> PartnerVehicleEvent | None:
    s = db.get(ParkingSession, sid)
    if s is None:
        return None
    ev = PartnerVehicleEvent(
        job_key=_job_key(key.id, sid, event), partner_key_id=key.id, session_id=sid,
        plate_number=s.plate_number, event=event,
        event_time=s.entry_time if event == "ENTRY" else s.exit_time,
        status="PENDING", attempts=0, next_attempt_at=now,
        payload=build_payload(db, key.vehicle_webhook_format, event, s, exit_confirmed),
        awaiting_exit=event == "ENTRY")
    try:
        with db.begin_nested():
            db.add(ev)
    except IntegrityError:
        return None   # зэрэгцээ scan аль хэдийн бичсэн
    return ev


def scan_once(db, now: datetime | None = None) -> dict:
    """Шинэ орох/гарах үйл явдлыг outbox-д бичнэ. Буцаах: {"entry": n, "exit": m}."""
    now = now or datetime.utcnow()
    keys = _configured_keys(db)
    if not keys:
        db.rollback()
        return {"entry": 0, "exit": 0}
    key_ids = list(keys)
    S, E = ParkingSession, PartnerVehicleEvent
    settle = now - timedelta(seconds=max(0, settings.partner_vehicle_settle_sec))
    entry_since = now - timedelta(minutes=settings.partner_vehicle_entry_max_age_min)
    exit_since = now - timedelta(minutes=settings.partner_vehicle_exit_max_age_min)

    # Хос гарцыг хянах хугацаа дууссан ENTRY мөрүүд
    (db.query(E).filter(E.awaiting_exit.is_(True),
                        E.created_at < now - timedelta(days=settings.partner_vehicle_pair_days))
     .update({"awaiting_exit": False}, synchronize_session=False))

    # ── ENTRY ──
    entries = _candidates(db, key_ids, S.entry_time >= entry_since, S.created_at <= settle)
    want = {}
    for (kid, sid), (r, sub_created) in entries.items():
        key = keys[kid]
        if key.site_id and key.site_id != r.site_id:
            continue
        if sub_created > r.created_at:
            continue     # бүртгэлээс ӨМНӨ орсон айлчлал
        if r.registered_only and not r.is_registered:
            continue     # «зөвхөн бүртгэлтэй» зогсоолд татгалзсан — хаалт нээгдээгүй
        if r.fee_locked and r.entry_device_id is None:
            continue     # орох уншилтгүй (гарцад үүссэн суурь хураамжийн) сешн — орсон биш
        want[_job_key(kid, sid, "ENTRY")] = (key, sid)
    existing = {k for (k,) in db.query(E.job_key).filter(E.job_key.in_(list(want)))} if want else set()
    n_entry = 0
    for jk, (key, sid) in want.items():
        if jk not in existing and _add_event(db, key, sid, "ENTRY", now):
            n_entry += 1

    # ── EXIT ──
    awaiting = {(kid, sid): eid for eid, kid, sid in
                db.query(E.id, E.partner_key_id, E.session_id)
                .filter(E.awaiting_exit.is_(True), E.event == "ENTRY",
                        E.partner_key_id.in_(key_ids)).all()}
    cands = _candidates(db, key_ids, S.status.in_(CLOSED), S.exit_time >= exit_since,
                        S.exit_time <= settle)
    if awaiting:
        cands.update({k: v for k, v in _candidates(
            db, key_ids, S.status.in_(CLOSED), S.exit_time <= settle,
            S.id.in_([sid for _, sid in awaiting])).items() if k in awaiting})
    exit_keys = [_job_key(kid, sid, "EXIT") for kid, sid in cands]
    done = {k for (k,) in db.query(E.job_key).filter(E.job_key.in_(exit_keys))} if exit_keys else set()
    # Хос (ENTRY илгээгдсэн) эсэхийг awaiting-аас гадна бүх ENTRY мөрөөр шалгана.
    # Дугаар нь ИЖИЛ байх ёстой: ENTRY-ийн дараа burst autocorrect сешний дугаарыг
    # өөр (бас бүртгэлтэй) дугаар болговол «P орсон → Q гарсан» хос үүсэхгүй.
    entry_keys = [_job_key(kid, sid, "ENTRY") for kid, sid in cands]
    entry_plate = (dict(db.query(E.job_key, E.plate_number).filter(E.job_key.in_(entry_keys)).all())
                   if entry_keys else {})
    n_exit = 0
    for (kid, sid), (r, sub_created) in cands.items():
        key = keys[kid]
        if key.site_id and key.site_id != r.site_id:
            continue
        jk = _job_key(kid, sid, "EXIT")
        paired = entry_plate.get(_job_key(kid, sid, "ENTRY")) == r.plate_number
        if jk in done:
            if (kid, sid) in awaiting:
                db.query(E).filter(E.id == awaiting[(kid, sid)]).update(
                    {"awaiting_exit": False}, synchronize_session=False)
            continue
        fresh = r.exit_time >= exit_since and sub_created <= r.exit_time
        if not (paired or fresh):
            continue
        real = _real_exit(r)
        if not real and (key.vehicle_webhook_format or "socialpay") != "extended":
            # Гарах уншилтгүй албадан хаалт — spec форматад илгээхгүй. ENTRY-г хянасаар
            # үлдээнэ: auto_reopen → бодит гарц ирвэл тэр үед илгээнэ.
            continue
        if _add_event(db, key, sid, "EXIT", now, exit_confirmed=real):
            n_exit += 1
            if (kid, sid) in awaiting:
                db.query(E).filter(E.id == awaiting[(kid, sid)]).update(
                    {"awaiting_exit": False}, synchronize_session=False)
    db.commit()
    if n_entry or n_exit:
        log.info("түншийн машины мэдэгдэл: %d орох, %d гарах outbox-д", n_entry, n_exit)
    return {"entry": n_entry, "exit": n_exit}


# ─────────────────────────── Хүргэлт ───────────────────────────

def claim_batch(db, now: datetime | None = None, limit: int = 20) -> list[dict]:
    """Хүргэх ёстой мэдэгдлүүдийг SENDING болгож lease-тэй буцаана (commit хийнэ).
    Нэг (түнш, дугаар)-ын өмнөх үйл явдал хүргэгдээгүй бол дараагийнхыг БАРИНА."""
    now = now or datetime.utcnow()
    E = PartnerVehicleEvent
    O = aliased(PartnerVehicleEvent)
    earlier = exists().where(
        O.partner_key_id == E.partner_key_id, O.plate_number == E.plate_number, O.id != E.id,
        O.status.in_(IN_FLIGHT),
        or_(O.event_time < E.event_time,
            and_(O.event_time == E.event_time, O.created_at < E.created_at)))
    due = or_(and_(E.status.in_(("PENDING", "RETRY")), E.next_attempt_at <= now),
              and_(E.status == "SENDING", E.lease_until <= now))
    rows = (db.query(E).filter(due, ~earlier)
            .order_by(E.next_attempt_at, E.event_time, E.created_at)
            .limit(limit).with_for_update(skip_locked=True).all())
    out = []
    for ev in rows:
        key = db.get(PartnerKey, ev.partner_key_id)
        url = (key.vehicle_webhook_url or "").strip() if key else ""
        reason = None
        if key is None or not key.is_active or not has_scope(key.scopes):
            reason = "Түлхүүр идэвхгүй/эрхгүй болсон"
        elif not url:
            reason = "Мэдэгдлийн URL тохируулаагүй"
        elif not is_registered_by(db, ev.partner_key_id, ev.plate_number):
            reason = "Машины бүртгэл устсан — илгээхгүй"
        if reason:
            ev.status, ev.last_error = "SKIPPED", reason
            ev.lease_token = ev.lease_until = None
            ev.awaiting_exit = False
            continue
        ev.status = "SENDING"
        ev.attempts = (ev.attempts or 0) + 1
        ev.lease_token = str(uuid.uuid4())
        ev.lease_until = now + LEASE
        out.append({"id": ev.id, "token": ev.lease_token, "url": url, "payload": ev.payload,
                    "event": ev.event, "partner": key.name})
    db.commit()
    return out


async def post_event(url: str, payload: dict, event_id: str, event: str,
                     timeout: float | None = None, transport=None) -> dict:
    """Түншийн URL руу POST. Амжилт = 2xx БА body.Success is true. Хэзээ ч raise хийхгүй."""
    import httpx
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/json",
               "Idempotency-Key": event_id, "X-Parking-Event-Id": event_id,
               "X-Parking-Event": f"vehicle.{event.lower()}"}
    t = float(timeout or settings.partner_vehicle_http_timeout)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(t), follow_redirects=False,
                                     transport=transport) as client:
            resp = await client.post(url, content=body, headers=headers)
    except Exception as exc:  # noqa: BLE001 — сүлжээ/timeout: зөвхөн төрөл (URL/нууц задлахгүй)
        return {"ok": False, "status_code": None, "success": None, "message": None,
                "error": type(exc).__name__}
    success, message = None, None
    try:
        data = resp.json()
    except ValueError:
        data = None
    if isinstance(data, dict):
        success = data.get("Success", data.get("success"))
        message = data.get("Message", data.get("message"))
    if isinstance(success, str):          # "true" текстээр ирвэл ч хүлээн авна
        success = {"true": True, "false": False}.get(success.strip().lower(), success)
    ok = 200 <= resp.status_code < 300 and success is True
    error = None
    if not ok:
        error = (f"HTTP {resp.status_code}" if not 200 <= resp.status_code < 300
                 else f"Success={success!r}")
    return {"ok": ok, "status_code": resp.status_code, "success": success,
            "message": None if message is None else str(message)[:300], "error": error}


def _next_delay(attempts: int) -> int:
    return RETRY_DELAYS[min(max(attempts, 1), len(RETRY_DELAYS)) - 1]


def record_result(db, event_id: str, token: str, res: dict, now: datetime | None = None) -> str | None:
    now = now or datetime.utcnow()
    E = PartnerVehicleEvent
    ev = (db.query(E).filter(E.id == event_id).populate_existing().with_for_update().first())
    if ev is None or ev.lease_token != token or ev.status != "SENDING":
        db.rollback()
        return None
    ev.last_http_status = res.get("status_code")
    ev.response_message = res.get("message")
    ev.lease_token = ev.lease_until = None
    if res.get("ok"):
        ev.status, ev.delivered_at, ev.last_error = "DONE", now, None
        log.info("түншийн мэдэгдэл хүргэгдлээ: %s %s (%s)", ev.event, ev.plate_number,
                 res.get("message"))
    else:
        ev.last_error = (res.get("error") or "алдаа")[:500]
        nxt = now + timedelta(seconds=_next_delay(ev.attempts))
        if nxt > ev.created_at + timedelta(hours=settings.partner_vehicle_retry_hours):
            ev.status = "DEAD"
            log.warning("түншийн мэдэгдэл DEAD: %s %s — %s (%s)", ev.event, ev.plate_number,
                        ev.last_error, res.get("message"))
        else:
            ev.status, ev.next_attempt_at = "RETRY", nxt
            log.warning("түншийн мэдэгдэл амжилтгүй (%d-р оролдлого): %s %s — %s (%s)",
                        ev.attempts, ev.event, ev.plate_number, ev.last_error, res.get("message"))
    db.commit()
    return ev.status


async def deliver_due(factory=SessionLocal, limit: int = 20, transport=None) -> int:
    """Нэг багц: claim (thread) → HTTP зэрэг (async) → үр дүн (thread). DB транзакц
    HTTP хүлээх хооронд нээлттэй үлдэхгүй."""
    def _claim():
        with factory() as db:
            return claim_batch(db, limit=limit)

    items = await asyncio.to_thread(_claim)
    if not items:
        return 0
    results = await asyncio.gather(*(post_event(it["url"], it["payload"], it["id"], it["event"],
                                                transport=transport) for it in items))

    def _record():
        with factory() as db:
            for it, res in zip(items, results):
                record_result(db, it["id"], it["token"], res)

    await asyncio.to_thread(_record)
    return len(items)


def cleanup(db, now: datetime | None = None) -> int:
    """Хуучин мөр: DONE/SKIPPED 30 хоног, DEAD 90 хоног."""
    now = now or datetime.utcnow()
    E = PartnerVehicleEvent
    n = (db.query(E).filter(or_(and_(E.status.in_(("DONE", "SKIPPED")),
                                     E.created_at < now - timedelta(days=30)),
                                and_(E.status == "DEAD", E.created_at < now - timedelta(days=90))))
         .delete(synchronize_session=False))
    db.commit()
    return n


async def supervisor():
    """main.py-ийн background task. Түнш тохируулаагүй бол scan нэг л query хийнэ."""
    if not settings.partner_vehicle_notify:
        log.info("түншийн машины мэдэгдэл унтраалттай (PARKING_PARTNER_VEHICLE_NOTIFY=false)")
        return
    await asyncio.sleep(20)
    last_cleanup = 0.0

    def _scan():
        with SessionLocal() as db:
            return scan_once(db)

    def _clean():
        with SessionLocal() as db:
            return cleanup(db)

    while True:
        try:
            await asyncio.to_thread(_scan)
            for _ in range(5):            # нөөц хуримтлагдсан бол хэдэн багц
                if await deliver_due() < 20:
                    break
            if time.monotonic() - last_cleanup > 3600:
                await asyncio.to_thread(_clean)
                last_cleanup = time.monotonic()
        except Exception:  # noqa: BLE001
            log.exception("түншийн машины мэдэгдлийн мөчлөг алдаа")
        await asyncio.sleep(max(2, int(settings.partner_vehicle_scan_sec)))
