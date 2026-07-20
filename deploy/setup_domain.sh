#!/usr/bin/env bash
# Домэйн + Let's Encrypt SSL тохируулга (production дээр НЭГ УДАА ажиллуулна).
# Ажиллуулах: sudo bash /root/PARKING/deploy/setup_domain.sh site.easy-parking.mn
#
# УРЬДЧИЛСАН НӨХЦӨЛ:
#   1. DNS A бичлэг: домэйн → байгууллагын гадаад IP (жишээ: 202.21.117.179)
#   2. Router/Fortigate дээр port forwarding: гадаад 80, 443 → энэ сервер (80, 443)
#      (үгүй бол Let's Encrypt баталгаажуулалт бүтэхгүй, жолоочийн утас QR-оор хандаж чадахгүй)
#
# Хийх зүйл: certbot суулгах → nginx-д домэйн блок → сертификат авах →
# .env-ийн PARKING_PUBLIC_BASE_URL-ийг https://домэйн болгох → restart.
# Дотоод LAN (IP-ээр) хандалт HTTP хэвээр үлдэнэ (админ UI, камерын callback).
set -euo pipefail

DOMAIN="${1:?Домэйн заана уу. Жишээ: sudo bash setup_domain.sh site.easy-parking.mn}"
EMAIL="${2:-stemuujin@gmail.com}"
APP_DIR=/root/PARKING
ENV_FILE=$APP_DIR/backend/.env

echo "==> 0/5 DNS шалгах"
IP=$(getent hosts "$DOMAIN" | awk '{print $1}' | head -1 || true)
if [ -z "$IP" ]; then
  echo "АЛДАА: $DOMAIN DNS-ээс олдсонгүй. A бичлэгээ шалгана уу."; exit 1
fi
echo "    $DOMAIN → $IP"

echo "==> 1/5 certbot суулгах"
apt-get update -qq
apt-get install -y -qq certbot python3-certbot-nginx

echo "==> 2/5 nginx: домэйн нэртэй тусдаа server block"
cat > /etc/nginx/sites-available/parking <<NGINX
# ─── Домэйн (гаднаас, жолоочийн QR төлбөр) — certbot энэ блокт SSL нэмнэ ───
server {
    listen 80;
    server_name $DOMAIN;
    client_max_body_size 10M;
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
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

# ─── Дотоод LAN (172.16.100.21 IP-ээр) — HTTP хэвээр (админ UI, камерын callback) ───
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

echo "==> 3/5 Let's Encrypt сертификат ($DOMAIN)"
certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "$EMAIL" --redirect

echo "==> 4/5 .env: PARKING_PUBLIC_BASE_URL=https://$DOMAIN"
if grep -q '^PARKING_PUBLIC_BASE_URL=' "$ENV_FILE"; then
  sed -i "s|^PARKING_PUBLIC_BASE_URL=.*|PARKING_PUBLIC_BASE_URL=https://$DOMAIN|" "$ENV_FILE"
else
  echo "PARKING_PUBLIC_BASE_URL=https://$DOMAIN" >> "$ENV_FILE"
fi
systemctl restart parking-backend

echo "==> 5/5 Шалгах"
sleep 3
# Дотоод сүлжээнээс hairpin NAT ажиллахгүй байж болзошгүй тул localhost руу resolve хийж шалгана
curl -fsS --resolve "$DOMAIN:443:127.0.0.1" "https://$DOMAIN/api/health" && echo
echo
echo "Дууслаа! Одоо:"
echo "  - Гаднаас: https://$DOMAIN (жолоочийн утас QR уншаад энэ рүү орно)"
echo "  - Дотоод LAN: http://172.16.100.21 хэвээр ажиллана"
echo "  - Сертификат 90 хоног тутам АВТОМАТААР сунгагдана (certbot systemd timer)"
echo "  - АНХААР: Тохиргоо → Зогсоол → QR-ээ ДАХИН ТАТАЖ хэвлэнэ үү (хуучин QR хуучин хаягтай)"
