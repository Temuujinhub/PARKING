"""DR backup хүлээн авагч — production-оос DB dump-ыг HTTP(S)-ээр авах нөөц суваг.

Юуны учир (2026-07-29): production-ы гадагш SSH-ийг байгууллагын firewall
протоколын түвшинд таслав (TCP тогтоно, SSH banner ирэхгүй). Тиймээс scp
ажиллахгүй үед backup_ship.sh энэ endpoint руу шифрлэсэн dump-аа POST хийнэ.

Хамгаалалт:
  • PARKING_DR_UPLOAD_TOKEN тохируулаагүй серверт endpoint огт ажиллахгүй (404)
    — зөвхөн test сервер дээр асаана.
  • X-Backup-Token толгой токентой яг таарах ёстой.
  • Агуулга нь илгээгч талдаа AES-256-ээр шифрлэгдсэн (.enc) — токенгүйгээр
    задлах боломжгүй тул HTTP-ээр явсан ч дата ил гарахгүй.
  • Хэмжээний хязгаар 512MB.
Хадгалах зам: /root/prod-backups/ (restore: tools/restore_backup.sh).
"""
import hmac
import logging
import os
import re
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request

from ..config import settings

log = logging.getLogger("parking.dr")

router = APIRouter(prefix="/api/dr", tags=["dr"])

DEST_DIR = "/root/prod-backups"
MAX_BYTES = 512 * 1024 * 1024


@router.post("/upload")
async def upload_backup(request: Request):
    if not settings.dr_upload_token:
        raise HTTPException(404, "Not found")   # энэ сервер хүлээн авагч биш
    token = request.headers.get("x-backup-token", "")
    if not hmac.compare_digest(token, settings.dr_upload_token):
        log.warning("DR upload: буруу токен (ip=%s)", request.client.host if request.client else "?")
        raise HTTPException(403, "invalid token")
    name = request.headers.get("x-backup-name", "")
    # Замын injection-оос сэргийлж нэрийг хатуу шүүнэ
    if not re.fullmatch(r"parking-prod-[0-9\-]+\.sql\.gz(\.enc)?", name or ""):
        name = f"parking-prod-{datetime.utcnow():%Y%m%d-%H%M%S}.sql.gz.enc"
    body = await request.body()
    if not body:
        raise HTTPException(400, "хоосон агуулга")
    if len(body) > MAX_BYTES:
        raise HTTPException(413, "хэт том")
    os.makedirs(DEST_DIR, exist_ok=True)
    path = os.path.join(DEST_DIR, name)
    with open(path, "wb") as f:
        f.write(body)
    os.chmod(path, 0o600)
    log.info("DR backup хүлээн авлаа: %s (%.1f MB)", name, len(body) / 1e6)
    return {"ok": True, "saved": name, "bytes": len(body)}
