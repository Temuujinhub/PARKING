from datetime import datetime, timedelta

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from .config import settings
from .database import get_db
from .models import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

# Хуудас/модуль тус бүрийн эрхийн матриц
ROLE_PERMISSIONS = {
    "SUPER_ADMIN": {"*"},
    "ADMIN": {
        "dashboard", "cashier", "check", "history", "discounts", "settings",
        "reports", "drivers", "vat", "barriers", "blacklist", "logs", "devices",
        "compensations", "users", "health",  # health — системийн эрүүл мэнд мониторинг
    },
    # FINANCE — тайлан/төлбөр/НӨАТ + хөнгөлөлт, хар жагсаалт удирдана, лог харна
    "FINANCE": {"dashboard", "history", "reports", "vat", "payments", "logs",
                "compensations", "discounts", "blacklist"},
    # HR (Хүний нөөц) — зөвхөн ажилтан нэмж/хасах, ажилласан өдрийн тайлан
    "HR": {"users"},
    # OPERATOR: Касс, Шалгах, Түүх, Нөхөн төлбөр (өөрийн зогсоолын өрийг касс дээр цуглуулна)
    "OPERATOR": {"cashier", "check", "history", "compensations"},
}


def hash_password(password: str) -> str:
    # bcrypt 72 байтын хязгаартай — UTF-8 болгож таслана
    return bcrypt.hashpw(password.encode("utf-8")[:72], bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8")[:72], hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def create_access_token(user: User) -> str:
    payload = {
        "sub": user.id,
        "username": user.username,
        "role": user.role,
        "site_id": user.site_id,
        "exp": datetime.utcnow() + timedelta(minutes=settings.jwt_expire_minutes),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Токены хугацаа дууссан. Дахин нэвтэрнэ үү.")
    except jwt.InvalidTokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Токен буруу байна.")


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    payload = decode_token(token)
    user = db.get(User, payload["sub"])
    if not user or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Хэрэглэгч идэвхгүй байна.")
    return user


def has_permission(role: str, module: str) -> bool:
    perms = ROLE_PERMISSIONS.get(role, set())
    return "*" in perms or module in perms


def require(*modules: str):
    """Тухайн модулиудын аль нэгэнд хандах эрх шаардана."""
    def checker(user: User = Depends(get_current_user)) -> User:
        if any(has_permission(user.role, m) for m in modules):
            return user
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Танд энэ үйлдлийг хийх эрх байхгүй.")
    return checker


def require_role(*roles: str):
    def checker(user: User = Depends(get_current_user)) -> User:
        if user.role in roles:
            return user
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Танд энэ үйлдлийг хийх эрх байхгүй.")
    return checker


def operator_site(user: User) -> str | None:
    """Оператор бол хязгаарлагдах зогсоолын site_id, үгүй бол None (бүх зогсоол).
    Endpoint-ууд энэ утгаар site_id-г албадан хязгаарлана — оператор өөр зогсоолын
    өгөгдөл харах/өөрчлөхөөс сэргийлнэ."""
    return user.site_id if user.role == "OPERATOR" and user.site_id else None


def enforce_site(user: User, site_id: str | None):
    """Оператор өөрийн зогсоолоос ӨӨР зогсоолын өгөгдлийг өөрчлөхийг хориглоно.
    Мутаци хийдэг endpoint бүр (хаалт нээх, session засах, төлбөр авах) дуудна —
    device_id/session_id таамаглаж өөр зогсоол руу IDOR хийхээс сэргийлнэ."""
    osid = operator_site(user)
    if osid and site_id and site_id != osid:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "Энэ үйлдэл таны хариуцах зогсоолынх биш байна.")
