#!/usr/bin/env bash
# Easy Parking — шинэ Ubuntu сервер дээр бүрэн суулгах script.
# Ажиллуулах: sudo bash install.sh
# Шаардлага: Ubuntu 22.04/24.04, интернэт холболт (GitHub, apt).
set -euo pipefail

REPO="https://github.com/Temuujinhub/PARKING.git"
APP_DIR="/root/PARKING"
DB_NAME="parking"
DB_USER="parking"
DB_PASS="${PARKING_DB_PASS:-$(openssl rand -hex 16)}"   # PARKING_DB_PASS өгвөл түүнийг ашиглана
SECRET_KEY="$(openssl rand -hex 32)"
# Серверийн хандах хаяг (дотоод IP эсвэл домэйн). Аргумент болгон өгч болно: bash install.sh 172.16.100.21
PUBLIC_HOST="${1:-$(hostname -I | awk '{print $1}')}"

echo "==> 1/8 Систем шинэчлэх, багц суулгах"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git curl nginx postgresql postgresql-contrib python3-venv python3-pip openssl
curl -fsSL https://deb.nodesource.com/setup_20.x | bash - >/dev/null 2>&1
apt-get install -y -qq nodejs

echo "==> 2/8 Код татах"
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" pull --ff-only
else
  git clone --depth 1 "$REPO" "$APP_DIR"
fi

echo "==> 3/8 PostgreSQL өгөгдлийн сан үүсгэх"
sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname='$DB_USER'" | grep -q 1 || \
  sudo -u postgres psql -c "CREATE USER $DB_USER WITH PASSWORD '$DB_PASS';"
sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'" | grep -q 1 || \
  sudo -u postgres psql -c "CREATE DATABASE $DB_NAME OWNER $DB_USER;"

echo "==> 4/8 Backend орчин (venv + .env)"
cd "$APP_DIR/backend"
python3 -m venv venv
venv/bin/pip install -q --upgrade pip
venv/bin/pip install -q -r requirements.txt qrcode[pil]

if [ ! -f .env ]; then
  cat > .env <<EOF
PARKING_SECRET_KEY=$SECRET_KEY
PARKING_DATABASE_URL=postgresql+psycopg2://$DB_USER:$DB_PASS@localhost:5432/$DB_NAME
PARKING_PUBLIC_BASE_URL=http://$PUBLIC_HOST
PARKING_QPAY_MOCK=true
PARKING_EBARIMT_MOCK=true
PARKING_BARRIER_MOCK=true
PARKING_ALLOW_SIMULATE=false
PARKING_CORS_ORIGINS=*
EOF
  echo "    .env үүсгэв (нууц үг автоматаар үүсгэсэн)"
fi

echo "==> 5/8 Өгөгдлийн сан бэлдэх (seed эсвэл backup сэргээх)"
# migration.sql байвал сэргээнэ (хуучин серверээс дамжуулсан), үгүй бол шинэ seed
if [ -f "$APP_DIR/deploy/migration.sql" ]; then
  sudo -u postgres psql "$DB_NAME" < "$APP_DIR/deploy/migration.sql"
  echo "    migration.sql-аас сэргээв"
else
  venv/bin/python -m app.seed
fi

echo "==> 6/8 Frontend build"
cd "$APP_DIR/frontend"
npm install --no-audit --no-fund
NODE_OPTIONS=--max-old-space-size=1400 npm run build
mkdir -p /var/www/parking
cp -r dist/* /var/www/parking/
chown -R www-data:www-data /var/www/parking

echo "==> 7/8 systemd + nginx"
cp "$APP_DIR/deploy/parking-backend.service" /etc/systemd/system/
# nginx — домэйнгүй, зөвхөн энэ хостоор үйлчилнэ
cat > /etc/nginx/sites-available/parking <<'NGINX'
server {
    listen 80 default_server;
    server_name _;
    client_max_body_size 10M;
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    root /var/www/parking;
    index index.html;
    location / { try_files $uri $uri/ /index.html; }
    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
    location /ws/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_read_timeout 3600s;
    }
}
NGINX
ln -sf /etc/nginx/sites-available/parking /etc/nginx/sites-enabled/parking
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl daemon-reload
systemctl enable --now parking-backend
systemctl restart parking-backend nginx

echo "==> 8/8 Шалгалт"
sleep 3
curl -fsS "http://localhost/api/health" && echo
echo ""
echo "======================================================"
echo " Суулгалт дууслаа!"
echo " Хаяг:        http://$PUBLIC_HOST"
echo " DB нууц үг:  $DB_PASS   (энэ .env дотор хадгалагдсан)"
echo " Нэвтрэх:     temuujin / Temuujin@2026 (заавал солино!)"
echo "======================================================"
