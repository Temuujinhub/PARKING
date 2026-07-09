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
    },
    "FINANCE": {"dashboard", "history", "reports", "vat", "payments", "logs"},
    # Хяналтын самбар (dashboard) зөвхөн ADMIN/FINANCE-д — OPERATOR-т өгөхгүй
    "OPERATOR": {"cashier", "check", "history", "barriers", "drivers"},
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
