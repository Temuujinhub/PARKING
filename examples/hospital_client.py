"""Hospital-server example. Keep keys in a secret store, never in browser code.

Persist raw_body with its opaque visit_id before the first request. A timeout is
unknown: retry the exact bytes, using a fresh timestamp/signature. Do not redirect
signed requests or log headers. This module does not send anything on import.
"""
import hashlib
import hmac
import time
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import UUID

PATH = "/api/v1/hospital/visits"


def headers(integration_id: str, signing_secret: str, raw_body: bytes,
            timestamp: int | None = None) -> dict:
    integration_id = str(UUID(integration_id))
    stamp = str(int(time.time()) if timestamp is None else timestamp)
    message = "\n".join(("hospital-v1", integration_id, stamp, "POST", PATH,
                         hashlib.sha256(raw_body).hexdigest()))
    signature = hmac.new(signing_secret.encode(), message.encode(), hashlib.sha256).hexdigest()
    return {"Content-Type": "application/json", "X-Hospital-ID": integration_id,
            "X-Hospital-Timestamp": stamp, "X-Hospital-Signature": signature}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, hdrs, newurl):
        return None


def submit(endpoint: str, integration_id: str, signing_secret: str, raw_body: bytes):
    address = urlsplit(endpoint)
    if (address.scheme != "https" or not address.hostname or address.path != PATH
            or address.query or address.fragment or address.username or address.password):
        raise ValueError("Exact HTTPS hospital endpoint required")
    if not isinstance(raw_body, bytes) or not 0 < len(raw_body) <= 4096:
        raise ValueError("Persist the UTF-8 JSON request bytes; maximum 4096 bytes")
    request = Request(endpoint, data=raw_body, method="POST",
                      headers=headers(integration_id, signing_secret, raw_body))
    # The default HTTPS handler verifies certificates. Redirects are rejected.
    with build_opener(NoRedirect()).open(request, timeout=15) as response:  # nosec B310
        return response.status, response.read(16384)
