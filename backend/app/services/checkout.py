"""One parking-session reservation policy for every payment instrument."""
from fastapi import HTTPException
from sqlalchemy.exc import OperationalError

from ..models import Payment, ParkingSession

ACTIVE_ATTEMPT_STATUSES = ("PENDING", "CREATING", "UNKNOWN", "REVIEW")


def lock_session(db, session_id):
    db.flush()
    try:
        session = (db.query(ParkingSession).enable_eagerloads(False)
                   .filter(ParkingSession.id == session_id).populate_existing()
                   .with_for_update(nowait=True).first())
    except OperationalError as exc:
        db.rollback()
        raise HTTPException(409, "Энэ машины төлбөр боловсруулагдаж байна. Түр хүлээнэ үү") from exc
    if session is None:
        raise HTTPException(404, "Зогсолтын бүртгэл олдсонгүй")
    return session


def active_attempts(db, session_id):
    return (db.query(Payment).filter(Payment.session_id == session_id,
                                    Payment.status.in_(ACTIVE_ATTEMPT_STATUSES))
            .order_by(Payment.created_at, Payment.id).all())


def assert_available(db, session_id, *, allow_qpay=False):
    for payment in active_attempts(db, session_id):
        if allow_qpay and payment.provider == "QPAY" and payment.status == "PENDING":
            continue  # caller must cancel the invoice before collecting another payment
        raise HTTPException(409, "Өмнөх төлбөрийн оролдлогыг эхлээд баталгаажуулах эсвэл цуцлах шаардлагатай")
