"""Versioned HMAC protocol; the signed visit_id is a durable idempotency key."""
import hashlib
import hmac
import re

PATH = "/api/v1/hospital/visits"
MAX_SKEW_SECONDS = 300


def signing_message(integration_id: str, timestamp: str, body: bytes) -> bytes:
    digest = hashlib.sha256(body).hexdigest()
    return f"hospital-v1\n{integration_id}\n{timestamp}\nPOST\n{PATH}\n{digest}".encode("ascii")


def sign(secret: str, integration_id: str, timestamp: str, body: bytes) -> str:
    return hmac.new(secret.encode("ascii"), signing_message(integration_id, timestamp, body), hashlib.sha256).hexdigest()


def verify(secret: str, integration_id: str, timestamp: str, body: bytes, signature: str, now: int) -> bool:
    if not re.fullmatch(r"[0-9]{10,11}", timestamp or "") or not re.fullmatch(r"[0-9a-f]{64}", signature or ""):
        return False
    if abs(now - int(timestamp)) > MAX_SKEW_SECONDS:
        return False
    try:
        return hmac.compare_digest(sign(secret, integration_id, timestamp, body), signature)
    except (UnicodeError, ValueError):
        return False
