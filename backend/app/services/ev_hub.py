"""EV Hub (EVrepo, тусдаа WSS сервер) рүү хандах клиент.

Hub нь техник (OCPP) тал; энэ клиентээр core нь:
  • цэнэглэгчийн амьд төлөв асууна (физик шалгалт §1.2 — Preparing эсэх)
  • команд илгээнэ (RemoteStart/Stop, Reset, config…)
  • тулгалт хийнэ (transactions)

Бүх дуудлага богино timeout-той — hub унтарсан үед core-ийн event loop
гацахгүй, алдаагаа тодорхой шидне.
"""
import logging

import httpx

from ..config import settings

log = logging.getLogger("parking.ev_hub")


class HubError(Exception):
    pass


class HubUnavailable(HubError):
    pass


def _base() -> str:
    if not settings.evhub_url:
        raise HubError("PARKING_EVHUB_URL тохируулаагүй")
    return settings.evhub_url.rstrip("/")


def _headers() -> dict:
    return {"Authorization": f"Bearer {settings.evhub_api_key}"}


async def _get(path: str, params: dict | None = None):
    try:
        async with httpx.AsyncClient(timeout=settings.evhub_timeout_sec) as c:
            r = await c.get(f"{_base()}{path}", params=params, headers=_headers())
    except httpx.HTTPError as e:
        raise HubUnavailable(f"hub холбогдсонгүй: {e}") from e
    if r.status_code >= 400:
        raise HubError(f"hub {path} → {r.status_code}: {r.text[:200]}")
    return r.json()


async def _send(method: str, path: str, body: dict):
    try:
        async with httpx.AsyncClient(timeout=settings.evhub_timeout_sec) as c:
            r = await c.request(method, f"{_base()}{path}", json=body,
                                headers=_headers())
    except httpx.HTTPError as e:
        raise HubUnavailable(f"hub холбогдсонгүй: {e}") from e
    if r.status_code >= 400:
        raise HubError(f"hub {path} → {r.status_code}: {r.text[:200]}")
    return r.json()


async def health() -> dict:
    return await _get("/internal/health")


async def list_chargers() -> list[dict]:
    return await _get("/internal/chargers")


async def charger_status(cp_id: str) -> dict:
    return await _get(f"/internal/chargers/{cp_id}")


async def connector_status(cp_id: str, connector_id: int) -> dict | None:
    info = await charger_status(cp_id)
    for c in info.get("connectors", []):
        if c["connector_id"] == connector_id:
            c["online"] = info.get("online", False)
            return c
    return {"connector_id": connector_id, "status": "Unknown",
            "online": info.get("online", False)}


async def update_charger(cp_id: str, body: dict) -> dict:
    return await _send("PUT", f"/internal/chargers/{cp_id}", body)


async def send_command(cp_id: str, action: str, payload: dict | None = None,
                       expires_in: int = 300, requested_by: str = "core") -> str:
    """Команд дараалалд нэмээд command_id буцаана (үр дүнг get_command-оор)."""
    r = await _send("POST", f"/internal/chargers/{cp_id}/commands",
                    {"action": action, "payload": payload or {},
                     "expires_in": expires_in, "requested_by": requested_by})
    return r["command_id"]


async def get_command(command_id: str) -> dict:
    return await _get(f"/internal/commands/{command_id}")


async def remote_start(cp_id: str, connector_id: int, id_tag: str) -> str:
    return await send_command(cp_id, "RemoteStartTransaction",
                              {"connectorId": connector_id, "idTag": id_tag},
                              expires_in=120)


async def remote_stop(cp_id: str, ocpp_tx_id: int) -> str:
    return await send_command(cp_id, "RemoteStopTransaction",
                              {"transactionId": ocpp_tx_id}, expires_in=120)


async def set_charging_profile(cp_id: str, connector_id: int, ocpp_tx_id: int,
                               limit_w: float, duration_sec: int) -> str:
    """§6.4 хамгаалалт 2: сүлжээ тасарсан ч цэнэглэгч өөрөө зогсоно."""
    profile = {
        "connectorId": connector_id,
        "csChargingProfiles": {
            "chargingProfileId": ocpp_tx_id % 1000000,
            "transactionId": ocpp_tx_id,
            "stackLevel": 0,
            "chargingProfilePurpose": "TxProfile",
            "chargingProfileKind": "Absolute",
            "chargingSchedule": {
                "duration": duration_sec,
                "chargingRateUnit": "W",
                "chargingSchedulePeriod": [{"startPeriod": 0, "limit": limit_w}],
            },
        },
    }
    return await send_command(cp_id, "SetChargingProfile", profile)


async def list_transactions(since_tx_id: int = 0, limit: int = 200) -> list[dict]:
    return await _get("/internal/transactions",
                      {"since_tx_id": since_tx_id, "limit": limit})
