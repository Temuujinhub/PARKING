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

# UI-ийн чекбокс матриц + create/update_user validation-д ашиглах бүх модуль
ALL_MODULES = sorted({m for perms in ROLE_PERMISSIONS.values() for m in perms if m != "*"})


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


def effective_permissions(user: User) -> set[str]:
    """Хэрэглэгчийн бодит эрхүүд: permissions матриц тохируулсан бол түүгээр,
    үгүй бол role-ийн default. SUPER_ADMIN ямагт бүх эрхтэй."""
    if user.role == "SUPER_ADMIN":
        return {"*"}
    if user.permissions is not None:
        return set(user.permissions)
    return set(ROLE_PERMISSIONS.get(user.role, set()))


def has_permission(user: User, module: str) -> bool:
    perms = effective_permissions(user)
    return "*" in perms or module in perms


def require(*modules: str):
    """Тухайн модулиудын аль нэгэнд хандах эрх шаардана."""
    def checker(user: User = Depends(get_current_user)) -> User:
        if any(has_permission(user, m) for m in modules):
            return user
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Танд энэ үйлдлийг хийх эрх байхгүй.")
    return checker


def require_role(*roles: str):
    def checker(user: User = Depends(get_current_user)) -> User:
        if user.role in roles:
            return user
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Танд энэ үйлдлийг хийх эрх байхгүй.")
    return checker


def operator_sites(user: User) -> list[str] | None:
    """Оператор бол хандах эрхтэй зогсоолуудын жагсаалт, үгүй бол None (бүх зогсоол).
    site_ids (олон сонголт) тохируулсан бол түүгээр, үгүй бол [site_id]."""
    if user.role != "OPERATOR":
        return None
    ids = [s for s in (user.site_ids or []) if s]
    if not ids and user.site_id:
        ids = [user.site_id]
    return ids or None


def operator_site(user: User) -> str | None:
    """Оператор бол ҮНДСЭН зогсоолын site_id (ганц site шаардлагатай газарт —
    ээлж, кассын default). Олон зогсоолын шүүлтэд operator_sites/scoped_site ашиглана."""
    if user.role == "OPERATOR" and user.site_id:
        return user.site_id
    ids = operator_sites(user)
    return ids[0] if ids else None


def scoped_site(user: User, site_id: str | None) -> tuple[str | None, list[str] | None]:
    """Жагсаалт/тайлангийн endpoint-д зориулсан site шүүлт:
    (site_id, site_ids) буцаана — site_id байвал `== site_id`, үгүй бол site_ids
    байвал `in_(site_ids)` шүүлт хийнэ. Оператор эрхгүй site сонговол өөрийнх рүү буцаана."""
    allowed = operator_sites(user)
    if not allowed:
        return site_id, None
    if site_id and site_id in allowed:
        return site_id, None
    if len(allowed) == 1:
        return allowed[0], None
    return None, allowed


def enforce_site(user: User, site_id: str | None):
    """Оператор өөрийн зогсоолуудаас ӨӨР зогсоолын өгөгдлийг өөрчлөхийг хориглоно.
    Мутаци хийдэг endpoint бүр (хаалт нээх, session засах, төлбөр авах) дуудна —
    device_id/session_id таамаглаж өөр зогсоол руу IDOR хийхээс сэргийлнэ."""
    allowed = operator_sites(user)
    if allowed and site_id and site_id not in allowed:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "Энэ үйлдэл таны хариуцах зогсоолынх биш байна.")
