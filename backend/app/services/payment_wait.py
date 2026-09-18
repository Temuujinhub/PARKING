"""A persistent exit quote; camera retries must not extend the payment window."""
from datetime import datetime, timedelta

from .app_settings import get_exit_rules


def begin_wait(db, session, fee: dict, now: datetime, *, restart: bool = False):
    if restart or session.payment_wait_started_at is None:
        minutes = max(0, min(60, int(get_exit_rules(db, session.site_id)
                                   .get("payment_hold_minutes", 3))))
        session.payment_wait_started_at = now
        session.payment_quote_until = now + timedelta(minutes=minutes)
        session.payment_quote = dict(fee)
    session.last_exit_seen_at = now


def held_fee(session, at: datetime) -> dict | None:
    start = getattr(session, "payment_wait_started_at", None)
    until = getattr(session, "payment_quote_until", None)
    quote = getattr(session, "payment_quote", None)
    if start and until and start <= at < until and isinstance(quote, dict) and quote:
        return {**quote, "price_held_until": until.isoformat()}
    return None
