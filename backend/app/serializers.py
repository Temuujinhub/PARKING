"""SQLAlchemy model → dict хөрвүүлэгч (API хариултад)."""
from datetime import datetime
from decimal import Decimal


def site_pay_url(site) -> str:
    """Зогсоолын жолоочийн төлбөрийн линк — QR-т кодлогдох ЯГ тэр мөр.

    `qr_url` талбар нь "талбайд хэвлэгдчихсэн самбар дээрх линк". Хэрэв бөглөгдсөн
    бол QR зураг ҮҮГЭЭР үүснэ — ингэснээр систем үүсгэсэн QR нь хэвлэгдсэнтэйгээ
    яг таарч, хуучин самбаруудыг солихгүйгээр үргэлжлүүлэн ашиглаж болно.
    Хоосон бол стандарт /pay?site=<код> хэлбэр.
    """
    from .config import settings
    return getattr(site, "qr_url", None) or f"{settings.public_base_url}/pay?site={site.site_code}"


# API-аар ХЭЗЭЭ Ч буцаахгүй багана (нууц үг/түлхүүр). Оронд нь <нэр>_set: bool өгнө.
SECRET_COLUMNS = frozenset({"password_hash", "password", "qpay_password", "msgbill_api_key", "msgbill_webhook_secret"})


def to_dict(obj, *, exclude: set[str] = frozenset(), extra: dict | None = None) -> dict:
    if obj is None:
        return None
    out = {}
    for col in obj.__table__.columns:
        if col.name in SECRET_COLUMNS:
            # Тохируулсан эсэхийг л мэдэгдэнэ — утгыг нь биш
            if col.name != "password_hash":
                out[f"{col.name}_set"] = bool(getattr(obj, col.name))
            continue
        if col.name in exclude:
            continue
        val = getattr(obj, col.name)
        if isinstance(val, datetime):
            val = val.isoformat()
        elif isinstance(val, Decimal):
            val = float(val)
        out[col.name] = val
    if extra:
        out.update(extra)
    return out
