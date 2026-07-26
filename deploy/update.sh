#!/usr/bin/env bash
# Production сервер дээр кодыг шинэчлэх (bug fix / feature deploy).
#
# Ажиллуулах:
#   sudo bash /root/PARKING/deploy/update.sh              # GitHub-аас татна (энгийн)
#   sudo bash /root/PARKING/deploy/update.sh /root/x.bundle   # GitHub хаалттай үед bundle-аас
#
# Хийх зүйл: DB backup → код татах → deps → snapshot хавтас → frontend build →
# restart → шалгах. ЗӨВХӨН код шинэчилнэ — өгөгдөл, .env-д хүрэхгүй. Схемийн
# багана автоматаар нэмэгдэнэ (migrations.py backend асахад ажилладаг).
#
# GitHub:443 асуудал: байгууллагын дотоод сүлжээнээс GitHub үе үе хаагддаг. Тийм үед
# өөр машин дээр:  git -C /root/PARKING bundle create upd.bundle origin/main
# гээд upd.bundle-г VPN/scp-ээр хуулж, дараа нь энэ script-д замыг нь өгнө.
set -euo pipefail

APP_DIR="/root/PARKING"
BUNDLE="${1:-}"                 # заавал биш: git bundle файлын зам
SNAP_DIR="${PARKING_SNAPSHOT_DIR:-/var/lib/parking/snapshots}"
cd "$APP_DIR"

echo "==> 1/7 DB backup (аюулгүй байдлын үүднээс)"
BACKUP="/root/parking-backup-$(date +%Y%m%d-%H%M%S).sql"
sudo -u postgres pg_dump parking > "$BACKUP"
echo "    хадгалав: $BACKUP"

echo "==> 2/7 Код татах"
if [ -n "$BUNDLE" ]; then
  # ── Bundle горим (GitHub хаалттай үед) ──────────────────────────────────
  [ -f "$BUNDLE" ] || { echo "    АЛДАА: bundle олдсонгүй: $BUNDLE"; exit 1; }
  git bundle verify "$BUNDLE" >/dev/null 2>&1 || { echo "    АЛДАА: bundle эвдэрсэн"; exit 1; }
  git fetch --quiet "$BUNDLE" 'refs/heads/main:refs/remotes/bundle/main'
  git reset --hard bundle/main
  echo "    bundle-аас шинэчлэв: $BUNDLE"
else
  # ── GitHub горим (default) ──────────────────────────────────────────────
  if ! git fetch --quiet origin main; then
    echo "    АЛДАА: GitHub-аас татаж чадсангүй (сүлжээ/443 хаалттай байж болзошгүй)."
    echo "    Bundle-аар шинэчлэхийг оролдоно уу:"
    echo "      sudo bash deploy/update.sh /зам/upd.bundle"
    exit 1
  fi
  git reset --hard origin/main   # локал өөрчлөлт байвал дарж бичнэ (production дээр гараар засдаггүй)
fi
echo "    HEAD: $(git rev-parse --short HEAD)  $(git log -1 --pretty=%s | cut -c1-60)"

echo "==> 3/7 Backend deps"
backend/venv/bin/pip install -q -r backend/requirements.txt

echo "==> 4/7 Snapshot хавтас бэлэн эсэхийг шалгах"
# LPR зургийн нөхөн таталт энэ хавтас руу бичдэг. Байхгүй бол backend бичиж
# чадахгүй тул урьдчилан үүсгэнэ (service нь root-оор ажилладаг).
mkdir -p "$SNAP_DIR"
echo "    $SNAP_DIR ($(df -h "$SNAP_DIR" | awk 'NR==2{print $4}') сул зай)"

echo "==> 5/7 Frontend build"
cd frontend
npm install --no-audit --no-fund --silent
NODE_OPTIONS=--max-old-space-size=1400 npm run build
cp -r dist/* /var/www/parking/
chown -R www-data:www-data /var/www/parking
cd ..

echo "==> 6/7 Backend дахин асаах (схем автоматаар шинэчилнэ)"
# Watchdog: минут тутам health шалгаж, гацсан/унасан бол авто restart (идемпотент)
install -m 755 tools/watchdog.sh /usr/local/bin/parking-watchdog
printf '* * * * * root /usr/local/bin/parking-watchdog\\n' > /etc/cron.d/parking-watchdog
chmod 644 /etc/cron.d/parking-watchdog
# systemd unit өөрчлөгдсөн бол шинэчилнэ (TimeoutStopSec г.м)
if ! cmp -s deploy/parking-backend.service /etc/systemd/system/parking-backend.service; then
  cp deploy/parking-backend.service /etc/systemd/system/parking-backend.service
  systemctl daemon-reload
  echo "    systemd unit шинэчлэв"
fi
systemctl restart parking-backend
systemctl reload nginx

echo "==> 7/7 Шалгах"
sleep 3
# Backend руу ШУУД (127.0.0.1:8000) — nginx-ийн HTTP→HTTPS redirect-д баригдахгүй
if curl -fsS http://127.0.0.1:8000/api/health >/dev/null; then
  curl -fsS http://127.0.0.1:8000/api/health && echo
else
  echo "    health БҮТЭЛГҮЙ — сүүлийн лог:"
  journalctl -u parking-backend -n 25 --no-pager
  exit 1
fi
# Зургийн нөхөн таталтын шинэ логик ачаалагдсаныг батлах диагностик мөрүүд
echo "----- snapshot / snap_pull лог (сүүлийн 15) -----"
journalctl -u parking-backend -n 200 --no-pager | grep -Ei "snap_pull|snapshot|нөхөн таталт" | tail -15 || true
echo "-------------------------------------------------"
echo "Шинэчлэлт дууслаа. Backup: $BACKUP"
