#!/usr/bin/env bash
# Production DB backup-ыг test сервер (152.42.235.199) лүү илгээх — DR төлөвлөгөө.
#
# Сувгууд (дарааллаар оролдоно):
#   1. scp (порт 22) — байгууллагын firewall SSH протоколыг таславал бүтэхгүй
#      (2026-07-29: TCP тогтдог ч "banner exchange timeout" — DPI таслалт батлагдсан)
#   2. HTTP(S) — dump-ыг AES-256-ээр ШИФРЛЭЖ /api/dr/upload руу POST хийнэ
#      (токен: /root/.parking-dr-token — test серверийн PARKING_DR_UPLOAD_TOKEN-той ижил)
#
# Суулгах (production, нэг удаа):
#   1. SSH түлхүүр (суваг 1-д): ssh-keygen -t ed25519 -N "" -f /root/.ssh/id_ed25519_backup
#      → .pub-ыг test-ийн authorized_keys-д
#   2. Токен (суваг 2-т): echo '<токен>' | sudo tee /root/.parking-dr-token; sudo chmod 600 /root/.parking-dr-token
#   3. Тест: sudo bash /root/PARKING/deploy/backup_ship.sh
#   4. Цаг тутам: echo '15 * * * * root bash /root/PARKING/deploy/backup_ship.sh >/dev/null 2>&1' | sudo tee /etc/cron.d/parking-backup-ship
set -euo pipefail

DEST="root@152.42.235.199"
DEST_DIR="/root/prod-backups"
KEY="/root/.ssh/id_ed25519_backup"
TOKEN_FILE="/root/.parking-dr-token"
URLS=("https://test.easy-parking.mn/api/dr/upload"
      "http://test.easy-parking.mn/api/dr/upload"
      "http://152.42.235.199:8080/api/dr/upload"
      "https://152.42.235.199:8443/api/dr/upload")
STAMP=$(date +%Y%m%d-%H%M%S)
F="/tmp/parking-prod-$STAMP.sql.gz"

umask 077
sudo -u postgres pg_dump parking | gzip > "$F"

ship_scp() {
  [ -f "$KEY" ] || return 1
  timeout 40 scp -i "$KEY" -o ConnectTimeout=15 -o BatchMode=yes \
    -o StrictHostKeyChecking=accept-new "$F" "$DEST:$DEST_DIR/" 2>/tmp/backup-ship.err
}

ship_http() {
  [ -s "$TOKEN_FILE" ] || { echo "токен файл алга: $TOKEN_FILE" >> /tmp/backup-ship.err; return 1; }
  local ENC="$F.enc" NAME u
  # HTTP-ээр ил явахаас сэргийлж ЗААВАЛ шифрлэнэ (задлахад мөн энэ токен хэрэгтэй)
  openssl enc -aes-256-cbc -pbkdf2 -salt -in "$F" -out "$ENC" -pass "file:$TOKEN_FILE"
  NAME="$(basename "$ENC")"
  for u in "${URLS[@]}"; do
    # ЧУХАЛ: зөвхөн HTTP код биш, серверийн {"ok":true} хариуг шалгана —
    # nginx-ийн 301 redirect-ийг curl «амжилт» гэж андуурдаг байсан (2026-07-29)
    local resp
    if resp=$(curl -fsS -m 180 -k -X POST -H "X-Backup-Token: $(cat "$TOKEN_FILE")" \
         -H "X-Backup-Name: $NAME" --data-binary @"$ENC" "$u" 2>>/tmp/backup-ship.err) \
       && echo "$resp" | grep -q '"ok"'; then
      rm -f "$ENC"
      echo "$u" > /tmp/backup-ship.last-url
      return 0
    fi
  done
  rm -f "$ENC"
  return 1
}

if ship_scp; then
  logger -t parking-backup "OK(scp): $(basename "$F") ($(du -h "$F" | cut -f1))"
  echo "OK: scp-ээр илгээв"
  rm -f "$F"
elif ship_http; then
  logger -t parking-backup "OK(http): $(basename "$F") → $(cat /tmp/backup-ship.last-url)"
  echo "OK: шифрлээд $(cat /tmp/backup-ship.last-url) руу илгээв"
  rm -f "$F"
else
  mv "$F" /root/ 2>/dev/null || true
  logger -t parking-backup "АЛДАА: бүх сувгаар бүтсэнгүй — $(tail -c200 /tmp/backup-ship.err 2>/dev/null)"
  echo "АЛДАА: илгээж чадсангүй (backup /root/-д үлдэв, алдаа: /tmp/backup-ship.err)"
  exit 1
fi
