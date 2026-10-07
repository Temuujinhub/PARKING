"""Explicit, previewed registration status changes; no date or entitlement edits."""
import hashlib
import json
from collections import Counter
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import text

from ..models import RegisteredDriver


def lock_driver_writes(db):
    # Cooperating admin/import writers serialize duplicate checks with inserts.
    # Nonblocking: a large import must not tie up request workers.
    if db.get_bind().dialect.name == "postgresql":
        if not db.execute(text("SELECT pg_try_advisory_xact_lock(73194027)")).scalar():
            raise HTTPException(409, "Бүртгэлийн өөр өөрчлөлт хийгдэж байна. Дахин шалгана уу.")


def registration_key(row):
    return (row.plate_number, row.site_id or "", row.tenant_id or "" if not row.site_id else "")


def plan_status(rows, active_rows, active, now=None):
    now = now or datetime.utcnow()
    selected_keys = Counter(registration_key(r) for r in rows if not r.is_active)
    active_keys = {registration_key(r) for r in active_rows}
    items = []
    for row in sorted(rows, key=lambda r: str(r.id)):
        reason = None
        if row.is_active == active:
            reason = "unchanged"
        elif active:
            if row.valid_to < row.valid_from:
                reason = "invalid_dates"
            elif row.valid_to < now:
                reason = "expired"
            elif row.valid_from > now:
                reason = "not_started"
            elif registration_key(row) in active_keys:
                reason = "active_duplicate"
            elif selected_keys[registration_key(row)] > 1:
                reason = "selected_duplicate"
        items.append({"id": str(row.id), "plate_number": row.plate_number,
                      "reason": reason or "change"})
    # Bind confirmation to the exact records, not only the count. Any intervening
    # edit or a changed conflict/validity result requires a new preview.
    states = [{c.name: getattr(r, c.name) for c in RegisteredDriver.__table__.columns}
              for r in sorted(rows, key=lambda r: str(r.id))]
    token = hashlib.sha256(json.dumps([active, states, items], sort_keys=True,
                                     default=str).encode()).hexdigest()
    counts = Counter(item["reason"] for item in items)
    return {"items": items, "preview_token": token, "selected": len(items),
            "change_count": counts["change"], "unchanged_count": counts["unchanged"],
            "blocked_count": len(items) - counts["change"] - counts["unchanged"]}
