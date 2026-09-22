"""Daily hospital allowance. No network calls or commits inside these helpers."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.exc import OperationalError

from ..billing import calculate_fee
from ..models import HospitalDailyGrant, ParkingSession, Payment

ACTIVE = ("OPEN", "AWAITING_PAYMENT", "PAID")
MONGOLIA = timezone(timedelta(hours=8))


def local_date(at: datetime):
    return at.replace(tzinfo=timezone.utc).astimezone(MONGOLIA).date() if at.tzinfo is None else at.astimezone(MONGOLIA).date()


def assert_grantable(db, stay):
    if stay.status not in ("OPEN", "AWAITING_PAYMENT") or stay.fee_locked:
        raise HTTPException(409, "STAY_NOT_ELIGIBLE")
    # Unknown/failed provider outcomes cannot be assumed unpaid. A new benefit
    # must be granted before any non-cancelled checkout has been created.
    if db.query(Payment.id).filter(Payment.session_id == stay.id,
                                  Payment.status.notin_(("CANCELLED", "FAILED"))).first():
        raise HTTPException(409, "PAYMENT_IN_PROGRESS_OR_PAID")


def attach_daily_grant(db, stay, *, allow_existing=False):
    """Reserve remaining daily minutes when entering, or during a signed grant.

    The existing unique active (site, plate) index serializes re-entry. A terminal
    stay without proven consumption reserves its entire allocation, never zero.
    """
    if getattr(stay, "hospital_grant_id", None) or stay.status not in ("OPEN", "AWAITING_PAYMENT") or stay.fee_locked:
        return
    try:
        grant = (db.query(HospitalDailyGrant).filter(
            HospitalDailyGrant.site_id == stay.site_id,
            HospitalDailyGrant.plate_number == stay.plate_number,
            HospitalDailyGrant.benefit_date == local_date(stay.entry_time))
            .populate_existing().with_for_update(nowait=True).first())
    except OperationalError as exc:
        raise HTTPException(409, "HOSPITAL_ALLOWANCE_BUSY") from exc
    if grant is None:
        return
    if not allow_existing and stay.entry_time < grant.created_at:
        return  # backfilled historical stays never retroactively consume today's grant
    assert_grantable(db, stay)
    consumed = db.query(func.coalesce(func.sum(
        func.coalesce(ParkingSession.hospital_used_minutes,
                      ParkingSession.hospital_allowance_minutes)), 0)).filter(
        ParkingSession.hospital_grant_id == grant.id,
        ParkingSession.id != stay.id).scalar()
    remaining = max(0, grant.daily_minutes - int(consumed))
    stay.hospital_grant_id = grant.id
    stay.hospital_allowance_minutes = remaining
    stay.hospital_used_minutes = None


def apply_hospital_benefit(stay, fee, template):
    """F(full duration) - F(eligible minutes), never F(duration - minutes)."""
    grant_id = getattr(stay, "hospital_grant_id", None)
    if not grant_id or template is None or getattr(stay, "fee_locked", False):
        return fee
    if fee.get("hospital_grant_id") == grant_id:
        return fee  # immutable held quote already includes the deduction
    result = dict(fee)
    limit = max(0, int(getattr(stay, "hospital_allowance_minutes", 0) or 0))
    used = min(limit, max(0, int(fee.get("chargeable_minutes", 0))))
    start = datetime(2000, 1, 1)
    benefit = calculate_fee(template, start, start + timedelta(minutes=used))
    original = Decimal(str(fee["total_fee"]))
    credit = min(original, Decimal(str(benefit["total_fee"])))
    total = max(Decimal(0), original - credit)
    # Preserve the tax proportion of the quoted amount, including exclusive VAT.
    vat = (Decimal(str(fee["vat_amount"])) * total / original if original else Decimal(0))
    vat = vat.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    result.update(total_fee=float(total), vat_amount=float(vat), base_fee=float(total - vat),
                  discount_amount=float(Decimal(str(fee.get("discount_amount", 0))) + credit),
                  is_free=total == 0, hospital_grant_id=grant_id,
                  hospital_allowance_minutes=limit, hospital_used_minutes=used,
                  hospital_discount_amount=float(credit), hospital_original_fee=float(original))
    if credit:
        result["reason"] = f"Эмнэлгийн өдрийн хөнгөлөлт: {used} минут"
    return result


def finish_hospital_usage(stay, fee):
    """Release unused reservation exactly at finalization; reopening cannot expand it."""
    if not getattr(stay, "hospital_grant_id", None):
        return
    if fee.get("hospital_grant_id") != stay.hospital_grant_id:
        return  # retain full reservation when actual consumption is unknown
    used = min(int(stay.hospital_allowance_minutes or 0), max(0, int(fee["hospital_used_minutes"])))
    stay.hospital_used_minutes = used
    stay.hospital_allowance_minutes = used
    stay.hospital_fee_snapshot = dict(fee)
    if not stay.paid_at:
        # An earlier exit quote may be cached on the row. Final reports/debts
        # must use this final discounted quote, without rewriting settled money.
        stay.base_fee, stay.vat_amount, stay.total_fee = fee["base_fee"], fee["vat_amount"], fee["total_fee"]
        stay.discount_amount = fee["discount_amount"]
