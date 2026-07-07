#!/usr/bin/env bash
# Production сервер дээр кодыг шинэчлэх (bug fix / feature deploy).
# Ажиллуулах: sudo bash /root/PARKING/deploy/update.sh
# Хийх зүйл: DB backup → git pull → deps → frontend build → restart → health.
# ЗӨВХӨН шинэчлэл — өгөгдөл, .env-д хүрэхгүй. Схемийн багана автоматаар нэмэгдэнэ (migrations.py).
set -euo pipefail
cd /root/PARKING

echo "==> 1/6 DB backup (аюулгүй байдлын үүднээс)"
BACKUP="/root/parking-backup-$(date +%Y%m%d-%H%M%S).sql"
sudo -u postgres pg_dump parking > "$BACKUP"
echo "    хадгалав: $BACKUP"

echo "==> 2/6 Код татах (GitHub main)"
git fetch --quiet origin
git reset --hard origin/main   # локал өөрчлөлт байвал дарж бичнэ (production дээр гараар засдаггүй)

echo "==> 3/6 Backend deps"
backend/venv/bin/pip install -q -r backend/requirements.txt

echo "==> 4/6 Frontend build"
cd frontend
npm install --no-audit --no-fund --silent
NODE_OPTIONS=--max-old-space-size=1400 npm run build
cp -r dist/* /var/www/parking/
chown -R www-data:www-data /var/www/parking
cd ..

echo "==> 5/6 Backend дахин асаах (схем автоматаар шинэчилнэ)"
systemctl restart parking-backend
systemctl reload nginx

echo "==> 6/6 Шалгах"
sleep 3
curl -fsS http://localhost/api/health && echo
echo "Шинэчлэлт дууслаа. Backup: $BACKUP"
