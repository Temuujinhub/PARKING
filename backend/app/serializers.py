"""SQLAlchemy model → dict хөрвүүлэгч (API хариултад)."""
from datetime import datetime
from decimal import Decimal


def to_dict(obj, *, exclude: set[str] = frozenset(), extra: dict | None = None) -> dict:
    if obj is None:
        return None
    out = {}
    for col in obj.__table__.columns:
        if col.name in exclude or col.name == "password_hash":
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
