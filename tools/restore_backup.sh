#!/usr/bin/env bash
# Test сервер дээр production-ы backup-ыг сэргээх (DR болон тайлан харах).
#
#   sudo bash /root/PARKING/tools/restore_backup.sh              # хамгийн сүүлийн backup
#   sudo bash /root/PARKING/tools/restore_backup.sh /root/prod-backups/parking-prod-YYYYMMDD-HHMMSS.sql.gz
#
# Юу хийдэг: одоогийн parking DB-г parking_old болгон хадгалаад (буцаах боломжтой),
# сонгосон backup-ыг parking нэрээр сэргээж backend-ийг асаана. Үүний дараа
# test.easy-parking.mn дээр production-ы бүх дата (тайлан, түүх, төлбөр) харагдана.
# АНХААР: test серверийн өмнөх дата parking_old-д үлдэнэ; дахин restore хийвэл
# өмнөх parking_old дарагдана.
set -euo pipefail

F="${1:-$(ls -t /root/prod-backups/parking-prod-*.sql.gz 2>/dev/null | head -1)}"
[ -n "$F" ] && [ -f "$F" ] || { echo "✗ Backup файл олдсонгүй (/root/prod-backups/ хоосон байна)"; exit 1; }

echo "СЭРГЭЭХ ФАЙЛ: $F ($(du -h "$F" | cut -f1), $(date -r "$F" '+%Y-%m-%d %H:%M'))"
echo "Одоогийн parking DB → parking_old болж хадгалагдана."
read -r -p "Үргэлжлүүлэх үү? (yes гэж бичнэ): " a
[ "$a" = "yes" ] || { echo "цуцлав"; exit 1; }

systemctl stop parking-backend
sudo -u postgres psql -qc "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='parking' AND pid<>pg_backend_pid();" >/dev/null
sudo -u postgres psql -qc "DROP DATABASE IF EXISTS parking_old;"
sudo -u postgres psql -qc "ALTER DATABASE parking RENAME TO parking_old;"
sudo -u postgres createdb -O parking parking
gunzip -c "$F" | sudo -u postgres psql -q -d parking >/dev/null
systemctl start parking-backend
sleep 3
curl -fsS http://127.0.0.1:8000/api/health && echo
echo "✓ Сэргээлээ. Буцаах бол: parking-г устгаад parking_old-ыг parking болгож нэрлэнэ."
