"""Dahua DZBL-A / DZE-BL barrier удирдлага — CGI команд (Digest auth).

barrier_mock=True үед бодит төхөөрөмж рүү хүсэлт явуулахгүй, амжилттай гэж бүртгэнэ
(төхөөрөмж холбогдоогүй хөгжүүлэлтийн орчинд).
"""
from datetime import datetime

import httpx
from sqlalchemy.orm import Session

from ..config import settings
from ..models import BarrierCommand, Device


async def open_barrier(db: Session, device: Device, session_id: str | None,
                       source: str, issued_by: str | None = None) -> BarrierCommand:
    cmd = BarrierCommand(
        session_id=session_id, device_id=device.id, command="open",
        command_source=source, issued_by=issued_by,
    )
    db.add(cmd)
    db.flush()

    if settings.barrier_mock or not device.ip_address:
        cmd.status = "SUCCESS"
        cmd.response_text = "MOCK: barrier opened"
        cmd.executed_at = datetime.utcnow()
        db.commit()
        return cmd

    url = f"http://{device.ip_address}/cgi-bin/accessControl.cgi"
    params = {"action": "openDoor", "channel": 1, "UserID": 101, "Type": "Remote"}
    try:
        async with httpx.AsyncClient(timeout=settings.barrier_timeout_sec) as client:
            resp = await client.get(
                url, params=params,
                auth=httpx.DigestAuth(settings.barrier_username, settings.barrier_password),
            )
        cmd.status = "SUCCESS" if resp.status_code == 200 else "FAILED"
        cmd.response_text = resp.text[:500]
    except Exception as e:
        cmd.status = "FAILED"
        cmd.response_text = str(e)[:500]
    cmd.executed_at = datetime.utcnow()
    db.commit()
    return cmd
