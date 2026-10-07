"""Validate printed payment links without rewriting their durable identifiers."""
import re
from urllib.parse import parse_qsl, urlsplit

from ..config import settings

QR_URL_ERROR = (
    "QR линк буруу байна. Давхар залгасан холбоосыг арилгаад хэвлэсэн самбарын "
    "нэг бүтэн /checkout/… эсвэл /check-cost/… эсвэл /pay?site=… холбоос оруулна уу."
)


def validate_printed_qr_url(value: str | None) -> str | None:
    """Keep the printed path, including UUIDs belonging to recreated sites.

    Do not silently repair an ambiguous value or replace a legacy UUID with the
    current site's ID: find_site uses this exact link as the old board's alias.
    """
    if value is None or not value.strip():
        return None
    value = value.strip()
    if (len(value) > 2048 or re.search(r"[\s\x00-\x1f\x7f\\]", value)
            or len(re.findall(r"https?://", value, re.I)) != 1):
        raise ValueError(QR_URL_ERROR)
    try:
        parts = urlsplit(value)
        allowed = {"https://app.easy-parking.mn", "https://site.easy-parking.mn"}
        base = urlsplit(settings.public_base_url)
        allowed.add(f"{base.scheme}://{base.netloc}".lower())
        origin = f"{parts.scheme}://{parts.netloc}".lower()
        if (parts.scheme not in {"http", "https"} or not parts.hostname
                or parts.username is not None or parts.password is not None
                or parts.fragment or origin not in allowed):
            raise ValueError(QR_URL_ERROR)
        if parts.path == "/pay":
            query = parse_qsl(parts.query, keep_blank_values=True, strict_parsing=True)
            valid = (len(query) == 1 and query[0][0] == "site"
                     and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", query[0][1]))
        else:
            valid = (not parts.query and re.fullmatch(
                r"/(checkout|check-cost)/[A-Za-z0-9_-]{1,64}", parts.path))
        if not valid:
            raise ValueError(QR_URL_ERROR)
    except ValueError:
        raise ValueError(QR_URL_ERROR) from None
    return value
