#!/usr/bin/env bash
# Домэйн + SSL — зөвхөн 443 портоор (TLS-ALPN-01, acme.sh ашиглана).
# 80 порт гаднаас хаалттай үед setup_domain.sh-ийн ОРОНД ажиллуулна.
# Ажиллуулах: sudo bash /root/PARKING/deploy/setup_domain_alpn.sh site.easy-parking.mn
#
# УРЬДЧИЛСАН НӨХЦӨЛ: гадаад 443 → энэ серверийн 443 port forwarding ажилладаг байх
# (2026-07-20-нд гаднаас батлагдсан).
#
# Сертификат 60 хоног тутам автоматаар сунгагдана (acme.sh cron);
# сунгалтын үед nginx ~5 секунд зогсоно (шөнө ажилладаг тул мэдэгдэхгүй).
set -euo pipefail

DOMAIN="${1:?Домэйн заана уу. Жишээ: sudo bash setup_domain_alpn.sh site.easy-parking.mn}"
EMAIL="${2:-stemuujin@gmail.com}"
APP_DIR=/root/PARKING
ENV_FILE=$APP_DIR/backend/.env
CERT_DIR=/etc/ssl/parking

echo "==> 1/6 socat + acme.sh суулгах"
apt-get install -y -qq socat >/dev/null
if [ ! -f /root/.acme.sh/acme.sh ]; then
  curl -s https://get.acme.sh | sh -s email="$EMAIL"
fi
ACME=/root/.acme.sh/acme.sh
"$ACME" --set-default-ca --server letsencrypt >/dev/null

echo "==> 2/6 Түр 443 сонсогч байвал арилгах (портыг чөлөөлнө)"
rm -f /etc/nginx/sites-enabled/temp443
nginx -t -q && systemctl reload nginx || true

echo "==> 3/6 Сертификат авах (TLS-ALPN, 443 портоор)"
# nginx 443-ыг эзэлж байвал түр зогсооно — hooks нь сунгалтад мөн хадгалагдана
"$ACME" --issue --alpn -d "$DOMAIN" \
  --pre-hook "systemctl stop nginx" \
  --post-hook "systemctl start nginx"

echo "==> 4/6 Сертификатыг nginx-д суулгах"
mkdir -p "$CERT_DIR"
"$ACME" --install-cert -d "$DOMAIN" \
  --key-file       "$CERT_DIR/$DOMAIN.key" \
  --fullchain-file "$CERT_DIR/$DOMAIN.crt" \
  --reloadcmd "systemctl reload nginx"

echo "==> 5/6 nginx: HTTPS домэйн блок + LAN HTTP блок"
cat > /etc/nginx/sites-available/parking <<NGINX
# ─── Домэйн HTTPS (гаднаас, жолоочийн QR төлбөр) ───
server {
    listen 443 ssl;
    server_name $DOMAIN;
    ssl_certificate     $CERT_DIR/$DOMAIN.crt;
    ssl_certificate_key $CERT_DIR/$DOMAIN.key;
    client_max_body_size 10M;
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    add_header Strict-Transport-Security "max-age=31536000" always;
    root /var/www/parking;
    index index.html;
    location / { try_files \$uri \$uri/ /index.html; }
    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_read_timeout 60s;
    }
    location /ws/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_read_timeout 3600s;
    }
    location /assets/ { expires 30d; add_header Cache-Control "public, immutable"; }
}

# ─── Домэйн HTTP → HTTPS (80 порт хожим нээгдвэл ажиллана) ───
server {
    listen 80;
    server_name $DOMAIN;
    return 301 https://$DOMAIN\$request_uri;
}

# ─── Дотоод LAN (172.16.100.21 IP-ээр) — HTTP хэвээр (админ UI, камерууд) ───
server {
    listen 80 default_server;
    server_name _;
    client_max_body_size 10M;
    root /var/www/parking;
    index index.html;
    location / { try_files \$uri \$uri/ /index.html; }
    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_read_timeout 60s;
    }
    location /ws/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_read_timeout 3600s;
    }
}
NGINX
nginx -t
systemctl reload nginx

echo "==> 6/6 .env + backend restart + шалгах"
if grep -q '^PARKING_PUBLIC_BASE_URL=' "$ENV_FILE"; then
  sed -i "s|^PARKING_PUBLIC_BASE_URL=.*|PARKING_PUBLIC_BASE_URL=https://$DOMAIN|" "$ENV_FILE"
else
  echo "PARKING_PUBLIC_BASE_URL=https://$DOMAIN" >> "$ENV_FILE"
fi
systemctl restart parking-backend
sleep 3
curl -fsS --resolve "$DOMAIN:443:127.0.0.1" "https://$DOMAIN/api/health" && echo
echo
echo "Дууслаа!"
echo "  - Гаднаас: https://$DOMAIN (жолоочийн QR энэ рүү орно)"
echo "  - Дотоод LAN: http://172.16.100.21 хэвээр"
echo "  - Сунгалт: автомат (acme.sh cron, 60 хоног тутам, nginx ~5с зогсоно)"
echo "  - АНХААР: Тохиргоо → Зогсоол → QR-ээ ДАХИН ТАТАЖ хэвлэнэ үү!"
