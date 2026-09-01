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
        "free_exit",  # гараар/төлбөргүй гаргах + хаалт гараар удирдах (санхүүгийн эрсдэлтэй)
        # pay_transfer — кассын «Бэлнээр»-ийн оронд «Дансаар» (шилжүүлэг) сонголт.
        # Online operator-т эрхийн матрицаас гараар олгоно; энгийн операторт байхгүй.
        "pay_transfer",
    },
    # FINANCE — тайлан/төлбөр/НӨАТ + хөнгөлөлт, хар жагсаалт удирдана, лог харна
    "FINANCE": {"dashboard", "history", "reports", "vat", "payments", "logs",
                "compensations", "discounts", "blacklist"},
    # HR (Хүний нөөц) — зөвхөн ажилтан нэмж/хасах, ажилласан өдрийн тайлан
    "HR": {"users"},
    # OPERATOR: Касс, Шалгах, Түүх, Нөхөн төлбөр (өөрийн зогсоолын өрийг касс дээр цуглуулна)
    # ЧУХАЛ: OPERATOR-д free_exit ОРООГҮЙ — оператор танилаа үнэгүй гаргах,
    # хаалт дур мэдэн нээх санхүүгийн эрсдэлээс сэргийлнэ. Итгэмжит операторт
    # админ эрхийн матрицаас free_exit-ийг гараар нэмнэ.
    # pay_transfer — банкны API хараахан холбогдоогүй тул шилжүүлгээр төлсөн
    # жолоочийг оператор ГАРААР баталгаажуулна. Ээлж хаахад «Дансаар» нийлбэр
    # тусад нь харагддаг тул аудитын мөр үлдэнэ. API холбогдсоны дараа энэ
    # эрхийг буцаан хасах эсэхийг дахин шийднэ.
    "OPERATOR": {"cashier", "check", "history", "compensations", "pay_transfer"},
    # ONLINE_OPERATOR — оффисоос (зогсоол дээр биечлэн байхгүй) олон зогсоолын
    # төлбөрийг хянадаг оператор: кассын эрх + «Дансаар» (pay_transfer) төлбөр
    # баталгаажуулах. free_exit ОРООГҮЙ — хаалт дур мэдэн нээхгүй.
    "ONLINE_OPERATOR": {"cashier", "check", "history", "compensations", "pay_transfer"},
    # POS — кассын ажилтан: зөвхөн төлбөр авах + дугаар шалгах. Түүх/тайлан,
    # нөхөн төлбөр, «Дансаар» эрх байхгүй — хамгийн явцуу үйл ажиллагааны роль.
    "POS": {"cashier", "check"},
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


# Нээлттэй repo-д задарсан анхны нууц үгүүд (seed.py-д ил бичигдэж байсан).
# Эдгээрээр нэвтэрсэн хэрэглэгчид дэлгэц дээр анхааруулга харуулж, солиулна.
LEAKED_PASSWORDS = frozenset({
    "Temuujin@2026", "Admin@2026", "Operator@2026",
    "Sanhuu@2026", "Cashier@2026", "Manager@2026", "Finance@2026",
})


def is_leaked_password(pw: str) -> bool:
    return pw in LEAKED_PASSWORDS


def create_access_token(user: User, pw_weak: bool = False) -> str:
    now = datetime.utcnow()
    payload = {
        "sub": user.id,
        "username": user.username,
        "role": user.role,
        "site_id": user.site_id,
        # iat — токен хэзээ олгогдсон. Нууц үг солигдсоны ДАРАА олгогдсон эсэхийг
        # шалгаж хуучин (хулгайлагдсан байж болзошгүй) токеныг хүчингүй болгоно.
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_expire_minutes),
    }
    if pw_weak:
        # Задарсан/анхны нууц үгээр нэвтэрсэн — UI дээр анхааруулга харуулна
        payload["pw_weak"] = True
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
    # Нууц үг солигдсоны дараа ХУУЧИН токеныг хүчингүй болгоно. Ингэснээр
    # задарсан нууц үгээр нэвтэрсэн хэн нэгэн нууц үг солигдсоны дараа ч
    # 12 цаг хандсаар байх боломжгүй болно.
    if user.password_changed_at:
        iat = payload.get("iat")
        if iat is None or datetime.utcfromtimestamp(int(iat)) < user.password_changed_at:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                                "Нууц үг солигдсон тул дахин нэвтэрнэ үү.")
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
    # Шаардлагыг функц дээрээ тэмдэглэнэ — эрхийн матриц бүхэл системээрээ зөв
    # эсэхийг тест/оношилгоо ROUTE-уудаас уншиж шалгах боломжтой болгоно
    # (POS-ийн урсгал операторын эрхээр бүрэн ажиллах эсэх regression тест).
    checker.required_modules = modules
    return checker


def require_role(*roles: str):
    def checker(user: User = Depends(get_current_user)) -> User:
        if user.role in roles:
            return user
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Танд энэ үйлдлийг хийх эрх байхгүй.")
    checker.required_roles = roles
    return checker


def operator_sites(user: User) -> list[str] | None:
    """Хэрэглэгчийн хандах эрхтэй зогсоолуудын жагсаалт, үгүй бол None (бүх зогсоол).
    Tenant салгалт: SUPER_ADMIN-аас бусад ямар ч роль (ADMIN/FINANCE/HR/OPERATOR)
    "Хариуцах зогсоолууд" (site_ids) эсвэл үндсэн зогсоол (site_id) тохируулсан бол
    зөвхөн тэдгээр зогсоолын хүрээнд хязгаарлагдана. Тохируулаагүй хэрэглэгч =
    компанийн (EasyParking) түвшний хэрэглэгч, бүх зогсоол хардаг."""
    if user.role == "SUPER_ADMIN":
        return None
    ids = [s for s in (user.site_ids or []) if s]
    if not ids and user.site_id:
        ids = [user.site_id]
    if not ids and getattr(user, "tenant_id", None):
        # Түрээслэгчийн хэрэглэгч: тодорхой зогсоол заагаагүй бол түрээслэгчийнхээ
        # БҮХ зогсоолыг хардаг (шинээр нэмэгдсэн зогсоол автоматаар орно).
        from sqlalchemy.orm import object_session
        from .models import ParkingSite
        db = object_session(user)
        if db is not None:
            ids = [r[0] for r in db.query(ParkingSite.id)
                   .filter(ParkingSite.tenant_id == user.tenant_id).all()]
            # Түрээслэгчид зогсоол хараахан оноогоогүй бол ЮУ Ч харахгүй
            # (None буцаавал бүх зогсоол харагдах аюултай)
            return ids or ["00000000-0000-0000-0000-000000000000"]
    return ids or None


def operator_site(user: User) -> str | None:
    """ҮНДСЭН зогсоолын site_id (ганц site шаардлагатай газарт — ээлж, кассын
    default). Олон зогсоолын шүүлтэд operator_sites/scoped_site ашиглана."""
    if user.role != "SUPER_ADMIN" and user.site_id:
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


def grant_site(user: User, site_id: str):
    """Шинээр үүсгэсэн зогсоолыг үүсгэгчийнх нь хамрах хүрээнд нэмнэ.
    "Хариуцах зогсоолууд" зааж хязгаарласан админ зогсоол үүсгэвэл шинэ зогсоол
    нь жагсаалтад нь ороогүйгээс дараагийн алхамд (төхөөрөмж холбох) enforce_site
    403 өгч, өөрийн үүсгэсэн зогсоолоо удирдаж чадахгүй болдог — үүнээс сэргийлнэ.
    Tenant-аар хамардаг хэрэглэгчид (site_ids хоосон) өөрчлөлт хэрэггүй."""
    if user.role == "SUPER_ADMIN":
        return
    ids = [s for s in (user.site_ids or []) if s]
    if not ids and user.site_id:
        ids = [user.site_id]
    if ids and site_id not in ids:
        user.site_ids = ids + [site_id]


def enforce_site(user: User, site_id: str | None):
    """Оператор өөрийн зогсоолуудаас ӨӨР зогсоолын өгөгдлийг өөрчлөхийг хориглоно.
    Мутаци хийдэг endpoint бүр (хаалт нээх, session засах, төлбөр авах) дуудна —
    device_id/session_id таамаглаж өөр зогсоол руу IDOR хийхээс сэргийлнэ."""
    allowed = operator_sites(user)
    if allowed and site_id and site_id not in allowed:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "Энэ үйлдэл таны хариуцах зогсоолынх биш байна.")
