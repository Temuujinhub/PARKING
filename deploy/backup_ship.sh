#!/usr/bin/env bash
# Production DB backup-ыг test сервер (152.42.235.199) лүү илгээх — DR төлөвлөгөө.
#
# Суулгах (production дээр, НЭГ удаа):
#   1. SSH түлхүүр үүсгэнэ (нууц үггүй, cron-д зориулсан):
#        sudo ssh-keygen -t ed25519 -N "" -f /root/.ssh/id_ed25519_backup
#        sudo cat /root/.ssh/id_ed25519_backup.pub   ← энэ мөрийг test серверийн
#        /root/.ssh/authorized_keys-д нэмнэ
#   2. Гараар нэг удаа туршина:
#        sudo bash /root/PARKING/deploy/backup_ship.sh
#   3. Цаг тутам автоматаар (өдөрт 24 удаа, сүүлийн 1 цагийн дата л алдагдана):
#        echo '15 * * * * root bash /root/PARKING/deploy/backup_ship.sh >/dev/null 2>&1' | sudo tee /etc/cron.d/parking-backup-ship
#
# Test сервер дээр хуримтлагдсан backup-аас сэргээх: tools/restore_backup.sh
set -euo pipefail

DEST="root@152.42.235.199"
DEST_DIR="/root/prod-backups"
KEY="/root/.ssh/id_ed25519_backup"
STAMP=$(date +%Y%m%d-%H%M%S)
F="/tmp/parking-prod-$STAMP.sql.gz"

umask 077
sudo -u postgres pg_dump parking | gzip > "$F"

if scp -i "$KEY" -o ConnectTimeout=20 -o BatchMode=yes -o StrictHostKeyChecking=accept-new \
      "$F" "$DEST:$DEST_DIR/" 2>/tmp/backup-ship.err; then
  logger -t parking-backup "OK: $(basename "$F") ($(du -h "$F" | cut -f1)) → test сервер"
  rm -f "$F"
else
  # Илгээж чадаагүй бол локалд үлдээнэ (дараагийн амжилттай илгээлт хүртэл нотолгоо)
  mv "$F" /root/ 2>/dev/null || true
  logger -t parking-backup "АЛДАА: илгээж чадсангүй — $(cat /tmp/backup-ship.err | head -1)"
  exit 1
fi
