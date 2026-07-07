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

    # Хаалт нээх командыг илгээх IP-г тодорхойлно. Бүх-нэг-дор ITC камер хаалтаа өөрийн
    # реле (NO1/NO2)-ээр нээдэг тул хаалт төхөөрөмжид IP байхгүй бол тухайн эгнээний
    # (эсвэл зогсоолын) камерын IP-г ашиглана.
    ip = device.ip_address
    if not ip:
        cam_q = db.query(Device).filter(
            Device.site_id == device.site_id, Device.device_type == "camera",
            Device.status == "active", Device.ip_address.isnot(None), Device.ip_address != "",
        )
        cam = cam_q.filter(Device.lane_no == device.lane_no).first() or cam_q.first()
        ip = cam.ip_address if cam else None

    if settings.barrier_mock or not ip:
        cmd.status = "SUCCESS"
        cmd.response_text = "MOCK: barrier opened" if settings.barrier_mock else "IP тодорхойлогдсонгүй"
        cmd.executed_at = datetime.utcnow()
        db.commit()
        return cmd

    # Dahua ITC ANPR камер хаалтаа өөрийн реле (NO1/NO2)-ээр нээдэг тул хаалт нээх командыг
    # КАМЕРЫН IP руу илгээнэ. Firmware-ээс хамаараад CGI өөр байдаг тул мэдэгдэж буй
    # командуудыг дарааллаар туршиж, эхний амжилттайг (HTTP 200) ашиглана.
    candidates = [
        # ITC зогсоолын камер — хамгийн түгээмэл (strobe = боом)
        (f"http://{ip}/cgi-bin/trafficParking.cgi",
         {"action": "openStrobe", "channel": 1, "info.openType": "Normal", "info.plateNumber": ""}),
        (f"http://{ip}/cgi-bin/trafficSnap.cgi",
         {"action": "openStrobe", "channel": 1, "info.openType": "Normal"}),
        # Alarm output реле-г шууд асаах (NO1 = index 0)
        (f"http://{ip}/cgi-bin/alarmOut.cgi",
         {"action": "setState", "index": 0, "state": "true"}),
        # Хаалганы контроллер (нөөц)
        (f"http://{ip}/cgi-bin/accessControl.cgi",
         {"action": "openDoor", "channel": 1, "UserID": 101, "Type": "Remote"}),
    ]
    # Тодорхой болсон командыг settings-ээр давхарлаж болно (PARKING_BARRIER_OPEN_PATH/QS)
    if settings.barrier_open_path:
        candidates.insert(0, (f"http://{ip}{settings.barrier_open_path}", {}))

    auth = httpx.DigestAuth(settings.barrier_username or settings.camera_username,
                            settings.barrier_password or settings.camera_password)
    last = "холбогдсонгүй"
    cmd.status = "FAILED"
    try:
        async with httpx.AsyncClient(timeout=settings.barrier_timeout_sec) as client:
            for url, params in candidates:
                try:
                    resp = await client.get(url, params=params, auth=auth)
                    body = (resp.text or "").strip()
                    last = f"{url.split('/cgi-bin/')[-1]} → {resp.status_code}: {body[:120]}"
                    # Dahua амжилтад 200 + 'OK' буцаадаг; error үед 400/'Error'
                    if resp.status_code == 200 and "error" not in body.lower():
                        cmd.status = "SUCCESS"
                        cmd.response_text = last
                        break
                except Exception as e:
                    last = f"{url.split('/cgi-bin/')[-1]} → алдаа: {str(e)[:100]}"
        if cmd.status != "SUCCESS":
            cmd.response_text = last
    except Exception as e:
        cmd.response_text = str(e)[:500]
    cmd.executed_at = datetime.utcnow()
    db.commit()
    return cmd
