"""Hospital verification grants benefits; it cannot confirm payments or open gates."""
import hashlib
import re
import secrets
import time
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictBool, StrictInt, ValidationError, field_validator
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from ..auth import enforce_site, operator_sites, require
from ..config import settings
from ..database import get_db
from ..models import (AuditLog, HospitalDailyGrant, HospitalGrantRequest,
                      HospitalIntegration, ParkingSession, ParkingSite, User)
from ..ratelimit import throttle
from ..secretbox import decrypt_secret, encrypt_secret
from ..services.hospital_benefits import (ACTIVE, apply_hospital_benefit, assert_grantable,
                                         attach_daily_grant, local_date)
from ..services.hospital_signing import PATH, verify

router = APIRouter(tags=["hospital"])
ADMIN_PATH = "/api/admin/hospital-integrations"


class IntegrationInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=120)
    site_id: UUID | None = None
    daily_minutes: StrictInt = Field(default=120, ge=1, le=1440)
    is_active: StrictBool = False


class VisitInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    visit_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.:-]+$")
    site_code: str = Field(min_length=1, max_length=60, pattern=r"^[A-Za-z0-9_-]+$")
    plate_number: str = Field(min_length=6, max_length=7)
    served_at: AwareDatetime

    @field_validator("plate_number")
    @classmethod
    def canonical_plate(cls, value):
        if not re.fullmatch(r"(?:[0-9]{4}[А-ЯЁӨҮ]{3}|[А-ЯЁӨҮ]{2}[0-9]{4}|[0-9]{4}(?:ДК|АК))", value):
            raise ValueError("Canonical Mongolian plate required, e.g. 1234УБА")
        return value


def require_admin(user: User = Depends(require("settings"))):
    if user.role not in ("ADMIN", "SUPER_ADMIN"):
        raise HTTPException(403, "Тохиргооны админ эрх шаардлагатай")
    return user


def check_scope(user, site_id):
    if site_id is None and operator_sites(user) is not None:
        raise HTTPException(403, "Зогсоолын эрхтэй админ өөрийн зогсоолыг сонгоно уу")
    enforce_site(user, site_id)


def admin_row(db, row_id, user):
    try:
        row = db.query(HospitalIntegration).filter(HospitalIntegration.id == str(row_id)).with_for_update(nowait=True).first()
    except OperationalError as exc:
        db.rollback()
        raise HTTPException(409, "Тохиргоог өөр хэрэглэгч засаж байна. Дахин оролдоно уу") from exc
    if row is None:
        raise HTTPException(404, "Холболт олдсонгүй")
    check_scope(user, row.site_id)
    return row


def public_config(row):
    return {"id": row.id, "name": row.name, "site_id": row.site_id,
            "daily_minutes": row.daily_minutes, "is_active": row.is_active,
            "key_set": bool(row.signing_secret), "key_version": row.key_version}


def audit(db, user, action, row):
    db.add(AuditLog(username=user.username, action=action, entity="hospital_integration",
                    entity_id=row.id, detail=public_config(row)))


def apply_config(db, row, body, user):
    site_id = str(body.site_id) if body.site_id else None
    check_scope(user, site_id)
    site = db.get(ParkingSite, site_id) if site_id else None
    if site_id and site is None:
        raise HTTPException(422, "Зогсоол олдсонгүй")
    if body.is_active and (not site or not site.is_active or not row.signing_secret):
        raise HTTPException(422, "Идэвхжүүлэхийн өмнө идэвхтэй зогсоол сонгож, API түлхүүр үүсгэнэ үү")
    # Reassigning a key to another site would silently transfer authority.
    if row.signing_secret and site_id != row.site_id:
        raise HTTPException(409, "Түлхүүртэй холболтын зогсоолыг солихгүй; шинэ холболт үүсгэнэ үү")
    row.name, row.site_id = body.name, site_id
    row.daily_minutes, row.is_active = body.daily_minutes, body.is_active


@router.get(ADMIN_PATH)
def list_integrations(db: Session = Depends(get_db), user: User = Depends(require_admin)):
    query = db.query(HospitalIntegration)
    allowed = operator_sites(user)
    if allowed is not None:
        query = query.filter(HospitalIntegration.site_id.in_(allowed))
    return {"integrations": [public_config(row) for row in query.order_by(HospitalIntegration.created_at).all()],
            "can_leave_unassigned": allowed is None,
            "encryption_ready": bool(settings.secret_enc_key), "endpoint": PATH}


@router.post(ADMIN_PATH)
def create_integration(body: IntegrationInput, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    row = HospitalIntegration(is_active=False)
    apply_config(db, row, body, user)
    db.add(row); db.flush()
    audit(db, user, "HOSPITAL_CREATE", row)
    db.commit()
    return public_config(row)


@router.put(ADMIN_PATH + "/{row_id}")
def update_integration(row_id: UUID, body: IntegrationInput, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    row = admin_row(db, row_id, user)
    apply_config(db, row, body, user)
    audit(db, user, "HOSPITAL_UPDATE", row)
    db.commit()
    return public_config(row)


@router.post(ADMIN_PATH + "/{row_id}/rotate-key")
def rotate_key(row_id: UUID, response: Response, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    row = admin_row(db, row_id, user)
    if not settings.secret_enc_key:
        raise HTTPException(503, "Серверийн нууц түлхүүрийн шифрлэлт тохируулагдаагүй")
    if not row.site_id:
        raise HTTPException(422, "Түлхүүр үүсгэхээс өмнө зогсоол сонгоно уу")
    secret = secrets.token_urlsafe(48)
    row.signing_secret = encrypt_secret(secret)
    row.key_version = (row.key_version or 0) + 1
    audit(db, user, "HOSPITAL_ROTATE_KEY", row)
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return {**public_config(row), "signing_secret": secret, "shown_once": True}


def accept_visit(db, integration, visit, body_hash, now):
    previous = db.query(HospitalGrantRequest).filter(
        HospitalGrantRequest.integration_id == integration.id,
        HospitalGrantRequest.visit_id == visit.visit_id).first()
    if previous:
        if previous.body_hash != body_hash:
            raise HTTPException(409, "VISIT_ID_CONFLICT")
        return {**previous.response, "replayed": True}
    if local_date(visit.served_at) != local_date(now) or (visit.served_at - now).total_seconds() > 300:
        raise HTTPException(422, "SERVICE_DATE_MUST_BE_TODAY")
    site = db.get(ParkingSite, integration.site_id)
    if not site or not site.is_active or site.site_code != visit.site_code:
        raise HTTPException(403, "SITE_NOT_AUTHORIZED")
    stay = (db.query(ParkingSession).enable_eagerloads(False).filter(
        ParkingSession.site_id == site.id, ParkingSession.plate_number == visit.plate_number,
        ParkingSession.status.in_(ACTIVE)).populate_existing().with_for_update(nowait=True).first())
    today = local_date(now)
    if stay and local_date(stay.entry_time) != today:
        raise HTTPException(409, "STAY_STARTED_ON_ANOTHER_DAY")
    if stay and not stay.hospital_grant_id:
        assert_grantable(db, stay)
    grant = db.query(HospitalDailyGrant).filter(
        HospitalDailyGrant.site_id == site.id, HospitalDailyGrant.plate_number == visit.plate_number,
        HospitalDailyGrant.benefit_date == today).with_for_update(nowait=True).first()
    if not grant:
        grant = HospitalDailyGrant(integration_id=integration.id, site_id=site.id,
            plate_number=visit.plate_number, benefit_date=today, daily_minutes=integration.daily_minutes,
            created_at=now.astimezone(timezone.utc).replace(tzinfo=None))
        db.add(grant); db.flush()
    if stay:
        attach_daily_grant(db, stay, allow_existing=True)
        if stay.payment_quote and stay.hospital_grant_id:
            # Preserve the exact exit quote/deadline; never restart the 2-hour timer.
            stay.payment_quote = apply_hospital_benefit(stay, stay.payment_quote, site.tariff_template)
        if stay.status == "AWAITING_PAYMENT":
            from ..session_logic import session_fee_info
            fee = session_fee_info(db, stay, at=now.astimezone(timezone.utc).replace(tzinfo=None))
            stay.base_fee, stay.vat_amount, stay.total_fee = fee["base_fee"], fee["vat_amount"], fee["total_fee"]
            stay.discount_amount = fee["discount_amount"]
    response = {"status": "GRANTED", "grant_id": grant.id, "benefit_date": today.isoformat(),
                "daily_minutes": grant.daily_minutes, "attached_to_current_stay": bool(stay and stay.hospital_grant_id),
                "replayed": False}
    db.add(HospitalGrantRequest(integration_id=integration.id, visit_id=visit.visit_id,
        body_hash=body_hash, grant_id=grant.id, response=response))
    db.add(AuditLog(username="hospital:" + integration.id, action="HOSPITAL_GRANT", entity="hospital_grant",
                    entity_id=grant.id, detail={"site_id": site.id, "benefit_date": today.isoformat(),
                                              "daily_minutes": grant.daily_minutes}))
    db.flush()
    return response


@router.post(PATH)
async def grant_visit(request: Request, db: Session = Depends(get_db)):
    if request.url.scheme != "https":
        raise HTTPException(400, "HTTPS_REQUIRED")
    ip = request.client.host if request.client else "unknown"
    if throttle("hospital-ip:" + ip, 120):
        raise HTTPException(429, "RATE_LIMITED", headers={"Retry-After": "60"})
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
        raise HTTPException(415, "JSON_REQUIRED")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 4096:
            raise HTTPException(413, "BODY_TOO_LARGE")
    raw = bytes(body)
    try:
        integration_id = str(UUID(request.headers.get("x-hospital-id", "")))
    except ValueError:
        raise HTTPException(401, "INVALID_HOSPITAL_SIGNATURE")
    try:
        integration = db.query(HospitalIntegration).filter(HospitalIntegration.id == integration_id).first()
        if not integration or not integration.is_active or not integration.site_id or not integration.signing_secret:
            raise HTTPException(401, "INVALID_HOSPITAL_SIGNATURE")
        if not verify(decrypt_secret(integration.signing_secret), integration_id,
                      request.headers.get("x-hospital-timestamp", ""), raw,
                      request.headers.get("x-hospital-signature", ""), int(time.time())):
            raise HTTPException(401, "INVALID_HOSPITAL_SIGNATURE")
        if throttle("hospital-key:" + integration_id, 60):
            raise HTTPException(429, "RATE_LIMITED", headers={"Retry-After": "60"})
        # Authenticate before locking, then revalidate any concurrent key rotation.
        integration = (db.query(HospitalIntegration).filter(HospitalIntegration.id == integration_id)
                       .populate_existing().with_for_update(nowait=True).one())
        if not integration.is_active or not verify(decrypt_secret(integration.signing_secret),
                integration_id, request.headers.get("x-hospital-timestamp", ""), raw,
                request.headers.get("x-hospital-signature", ""), int(time.time())):
            raise HTTPException(401, "INVALID_HOSPITAL_SIGNATURE")
        try:
            visit = VisitInput.model_validate_json(raw)
        except ValidationError:
            raise HTTPException(422, "INVALID_VISIT: visit_id, site_code, canonical plate_number, timezone-aware served_at required")
        result = accept_visit(db, integration, visit, hashlib.sha256(raw).hexdigest(), datetime.now(timezone.utc))
        db.commit()
        return result
    except (OperationalError, IntegrityError) as exc:
        db.rollback()
        raise HTTPException(409, "RETRY_SAME_VISIT_ID") from exc
