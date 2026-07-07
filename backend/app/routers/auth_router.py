from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from ..auth import ROLE_PERMISSIONS, create_access_token, get_current_user, verify_password
from ..database import get_db
from ..models import AuditLog, User
from ..serializers import to_dict

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login")
def login(request: Request, form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == form.username).first()
    if not user or not verify_password(form.password, user.password_hash):
        raise HTTPException(401, "Нэвтрэх нэр эсвэл нууц үг буруу байна.")
    if not user.is_active:
        raise HTTPException(403, "Таны эрх идэвхгүй байна. Админд хандана уу.")
    db.add(AuditLog(username=user.username, action="LOGIN", entity="user", entity_id=user.id,
                    ip_address=request.client.host if request.client else None))
    db.commit()
    from ..config import settings
    return {
        "access_token": create_access_token(user),
        "token_type": "bearer",
        "user": to_dict(user),
        "permissions": sorted(ROLE_PERMISSIONS.get(user.role, set())),
        "test_mode": settings.allow_simulate,
    }


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    from ..config import settings
    return {"user": to_dict(user), "permissions": sorted(ROLE_PERMISSIONS.get(user.role, set())),
            "test_mode": settings.allow_simulate}


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
    user.password_hash = hash_password(new)
    db.add(AuditLog(username=user.username, action="CHANGE_PASSWORD", entity="user", entity_id=user.id))
    db.commit()
    return {"ok": True}
