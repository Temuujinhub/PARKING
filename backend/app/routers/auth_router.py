from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from ..auth import (create_access_token, effective_permissions, get_current_user,
                    is_leaked_password, oauth2_scheme, verify_password)
from ..database import get_db
from ..models import AuditLog, User
from ..serializers import to_dict

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _login_shift(db: Session, user: User):
    """Login-д суурилсан ээлж: оператор login хийхэд ээлж автоматаар нээгдэнэ.
    Тухайн зогсоолд ӨӨР операторын нээлттэй ээлж байвал хаана (ээлж хүлээлцэх) —
    'POS/системд дараагийн хүн login хийсэн = ээлж солигдсон' гэсэн логик."""
    from datetime import datetime
    from ..models import CashierShift
    if user.role != "OPERATOR":
        return
    # Тухайн зогсоолд бусад операторын нээлттэй ээлжийг хаах (хүлээлцэх)
    if user.site_id:
        for sh in db.query(CashierShift).filter(
                CashierShift.site_id == user.site_id, CashierShift.status == "OPEN",
                CashierShift.user_id != user.id).all():
            sh.closed_at = datetime.utcnow()
            sh.status = "CLOSED"
            db.add(AuditLog(username=user.username, action="SHIFT_HANDOVER",
                            entity="shift", entity_id=sh.id,
                            detail={"from": sh.user.username if sh.user else None,
                                    "to": user.username, "site_id": user.site_id}))
    # Энэ хэрэглэгчид нээлттэй ээлж байхгүй бол шинээр нээх
    if not db.query(CashierShift).filter(CashierShift.user_id == user.id,
                                         CashierShift.status == "OPEN").first():
        db.add(CashierShift(user_id=user.id, site_id=user.site_id))
        db.add(AuditLog(username=user.username, action="SHIFT_OPEN_LOGIN",
                        entity="shift", entity_id="", detail={"site_id": user.site_id}))


@router.post("/login")
def login(request: Request, form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    from ..ratelimit import record_failure, reset, retry_after
    ip = request.client.host if request.client else "?"
    rl_key = f"login:{form.username}:{ip}"
    wait = retry_after(rl_key)
    if wait:
        raise HTTPException(429, f"Хэт олон удаа буруу оролдлоо. {wait} секундын дараа дахин оролдоно уу.")
    user = db.query(User).filter(User.username == form.username).first()
    if not user or not verify_password(form.password, user.password_hash):
        record_failure(rl_key)  # brute-force тоолуур
        raise HTTPException(401, "Нэвтрэх нэр эсвэл нууц үг буруу байна.")
    if not user.is_active:
        raise HTTPException(403, "Таны эрх идэвхгүй байна. Админд хандана уу.")
    reset(rl_key)  # амжилттай нэвтэрлээ — тоолуур цэвэрлэнэ
    db.add(AuditLog(username=user.username, action="LOGIN", entity="user", entity_id=user.id,
                    ip_address=request.client.host if request.client else None))
    _login_shift(db, user)
    db.commit()
    from ..config import settings
    # Задарсан анхны нууц үгээр нэвтэрсэн бол токенд болон хариунд тэмдэглэнэ —
    # UI дээр анхааруулга гарч, солих хүртэл сануулсаар байна
    pw_weak = is_leaked_password(form.password)
    return {
        "access_token": create_access_token(user, pw_weak=pw_weak),
        "token_type": "bearer",
        "user": to_dict(user),
        "permissions": sorted(effective_permissions(user)),
        "test_mode": settings.allow_simulate,
        "pw_weak": pw_weak,
    }


@router.get("/me")
def me(token: str = Depends(oauth2_scheme), user: User = Depends(get_current_user)):
    from ..auth import decode_token
    from ..config import settings
    # pw_weak — задарсан анхны нууц үгээр нэвтэрсэн эсэх (нэвтрэх үед токенд
    # тэмдэглэгддэг). Хуудас дахин ачаалагдсан ч анхааруулга алга болохгүй.
    pw_weak = bool(decode_token(token).get("pw_weak"))
    return {"user": to_dict(user), "permissions": sorted(effective_permissions(user)),
            "test_mode": settings.allow_simulate, "pw_weak": pw_weak}


@router.post("/change-password")
def change_password(body: dict, db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    """Хэрэглэгч өөрийн нууц үгээ солино (easy-park UAT item 8)."""
    from ..auth import hash_password
    old, new = body.get("old_password", ""), body.get("new_password", "")
    if not verify_password(old, user.password_hash):
        raise HTTPException(400, "Одоогийн нууц үг буруу байна.")
    if len(new) < 8:
        raise HTTPException(400, "Шинэ нууц үг дор хаяж 8 тэмдэгт байх ёстой.")
    from datetime import datetime
    from ..auth import is_leaked_password as _leaked
    if _leaked(new):
        raise HTTPException(400, "Энэ нууц үг олон нийтэд задарсан байна — өөр нууц үг сонгоно уу.")
    if new == old:
        raise HTTPException(400, "Шинэ нууц үг хуучинтайгаа ижил байна.")
    user.password_hash = hash_password(new)
    # Хуучин токенуудыг хүчингүй болгоно (хулгайлагдсан байж болзошгүй)
    user.password_changed_at = datetime.utcnow()
    db.add(AuditLog(username=user.username, action="CHANGE_PASSWORD", entity="user", entity_id=user.id))
    db.commit()
    # Шинэ токен буцаана — хуучин нь энэ агшнаас хүчингүй тул UI шууд солино
    return {"ok": True, "access_token": create_access_token(user)}
