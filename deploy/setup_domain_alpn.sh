#!/usr/bin/env bash
# Домэйн + SSL — зөвхөн 443 портоор (TLS-ALPN-01, acme.sh ашиглана).
# 80 порт гаднаас хаалттай үед setup_domain.sh-ийн ОРОНД ажиллуулна.
#
# Ажиллуулах (олон домэйн дэмжинэ, ЭХНИЙХ нь үндсэн = PUBLIC_BASE_URL):
#   sudo bash /root/PARKING/deploy/setup_domain_alpn.sh app.easy-parking.mn site.easy-parking.mn
#
# Домэйн бүрд ТУСДАА сертификат авна (нэг нь амжилтгүй болсон ч нөгөө нь эвдрэхгүй).
# Аль хэдийн авсан сертификатыг дахин авахгүй — скриптийг олон удаа ажиллуулж болно.
#
# УРЬДЧИЛСАН НӨХЦӨЛ: домэйн бүр энэ серверийн гадаад IP рүү заасан байх ба
# гадаад 443 → энэ серверийн 443 port forwarding ажилладаг байх.
#
# Сертификат 60 хоног тутам автоматаар сунгагдана (acme.sh cron);
# сунгалтын үед nginx ~5 секунд зогсоно (шөнө ажилладаг тул мэдэгдэхгүй).
set -euo pipefail

if [ $# -lt 1 ]; then
  echo "Домэйн заана уу. Жишээ:" >&2
  echo "  sudo bash setup_domain_alpn.sh app.easy-parking.mn site.easy-parking.mn" >&2
  exit 1
fi

DOMAINS=("$@")
PRIMARY="${DOMAINS[0]}"
EMAIL="${EMAIL:-stemuujin@gmail.com}"
APP_DIR=/root/PARKING
ENV_FILE=$APP_DIR/backend/.env
CERT_DIR=/etc/ssl/parking

echo "==> Домэйнүүд: ${DOMAINS[*]}"
echo "==> Үндсэн (QR/callback-д ашиглагдах): $PRIMARY"

DRY_RUN="${DRY_RUN:-0}"

if [ "$DRY_RUN" = "1" ]; then
  # Туршилтын горим: сертификат авахгүй, nginx-ийг хөндөхгүй — зөвхөн үүсэх
  # тохиргоог DRY_OUT файлд (default: stdout) бичнэ.
  echo "==> DRY_RUN: сертификат/nginx хөндөхгүй, зөвхөн тохиргоо үүсгэнэ" >&2
  OK_DOMAINS=("${DOMAINS[@]}")
else

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
mkdir -p "$CERT_DIR"
for d in "${DOMAINS[@]}"; do
  if [ -s "$CERT_DIR/$d.crt" ] && openssl x509 -checkend 604800 -noout -in "$CERT_DIR/$d.crt" >/dev/null 2>&1; then
    echo "    - $d: сертификат хүчинтэй байна, алгасав"
    continue
  fi
  echo "    - $d: шинээр авч байна…"
  # nginx 443-ыг эзэлж байвал түр зогсооно — hooks нь сунгалтад мөн хадгалагдана
  "$ACME" --issue --alpn -d "$d" \
    --pre-hook "systemctl stop nginx" \
    --post-hook "systemctl start nginx" || true

  echo "    - $d: сертификатыг nginx-д суулгах"
  "$ACME" --install-cert -d "$d" \
    --key-file       "$CERT_DIR/$d.key" \
    --fullchain-file "$CERT_DIR/$d.crt" \
    --reloadcmd "systemctl reload nginx"
done

echo "==> 4/6 Сертификат бүрэн эсэхийг шалгах"
OK_DOMAINS=()
for d in "${DOMAINS[@]}"; do
  if [ -s "$CERT_DIR/$d.crt" ] && [ -s "$CERT_DIR/$d.key" ]; then
    OK_DOMAINS+=("$d")
  else
    echo "    !! $d: сертификат АВАГДСАНГҮЙ — энэ домэйныг nginx-д нэмэхгүй." >&2
    echo "       (DNS $d → энэ серверийн гадаад IP заасан эсэх, 443 forwarding-ыг шалгана уу)" >&2
  fi
done
if [ ${#OK_DOMAINS[@]} -eq 0 ]; then
  echo "Ямар ч сертификат авагдсангүй — nginx-ийг хөндөхгүйгээр зогслоо." >&2
  exit 1
fi

fi  # DRY_RUN

echo "==> 5/6 nginx: домэйн бүрд HTTPS блок + LAN HTTP блок" >&2
NGINX_CONF=/etc/nginx/sites-available/parking
NGINX_TMP=$(mktemp)

# Домэйн бүрд ижил апп үйлчилгээ (SPA + /api + /ws) — SNI-гаар ялгагдана
for d in "${OK_DOMAINS[@]}"; do
  cat >> "$NGINX_TMP" <<NGINX
# ─── Домэйн HTTPS: $d (жолоочийн QR төлбөр) ───
server {
    listen 443 ssl;
    server_name $d;
    ssl_certificate     $CERT_DIR/$d.crt;
    ssl_certificate_key $CERT_DIR/$d.key;
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

# ─── $d HTTP → HTTPS (80 порт хожим нээгдвэл ажиллана) ───
server {
    listen 80;
    server_name $d;
    return 301 https://$d\$request_uri;
}

NGINX
done

# Эхний HTTPS сервер блокийг default_server болгож, танихгүй SNI-д ч апп үйлчилнэ
sed -i "0,/^    listen 443 ssl;$/s//    listen 443 ssl default_server;/" "$NGINX_TMP"

cat >> "$NGINX_TMP" <<NGINX
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

if [ "$DRY_RUN" = "1" ]; then
  if [ -n "${DRY_OUT:-}" ]; then
    mv "$NGINX_TMP" "$DRY_OUT"
    echo "==> DRY_RUN: тохиргоог $DRY_OUT файлд бичлээ" >&2
  else
    cat "$NGINX_TMP"
    rm -f "$NGINX_TMP"
  fi
  exit 0
fi

# Ажиллаж байгаа тохиргоог эвдэхгүйн тулд эхлээд шалгаад дараа нь солино
cp "$NGINX_CONF" "$NGINX_CONF.bak.$(date +%Y%m%d%H%M%S)" 2>/dev/null || true
cp "$NGINX_TMP" "$NGINX_CONF"
rm -f "$NGINX_TMP"
ln -sf "$NGINX_CONF" /etc/nginx/sites-enabled/parking
if ! nginx -t; then
  echo "nginx тохиргоо буруу — өмнөх хувилбарыг сэргээж байна" >&2
  LAST_BAK=$(ls -t "$NGINX_CONF".bak.* 2>/dev/null | head -1 || true)
  [ -n "$LAST_BAK" ] && cp "$LAST_BAK" "$NGINX_CONF" && nginx -t && systemctl reload nginx
  exit 1
fi
systemctl reload nginx

echo "==> 6/6 .env + backend restart + шалгах"
if grep -q '^PARKING_PUBLIC_BASE_URL=' "$ENV_FILE"; then
  sed -i "s|^PARKING_PUBLIC_BASE_URL=.*|PARKING_PUBLIC_BASE_URL=https://$PRIMARY|" "$ENV_FILE"
else
  echo "PARKING_PUBLIC_BASE_URL=https://$PRIMARY" >> "$ENV_FILE"
fi
systemctl restart parking-backend
sleep 3
for d in "${OK_DOMAINS[@]}"; do
  printf "    %-28s " "$d"
  curl -fsS --resolve "$d:443:127.0.0.1" "https://$d/api/health" && echo
done

echo
echo "Дууслаа!"
echo "  - Үндсэн домэйн (QR + QPay callback): https://$PRIMARY"
for d in "${OK_DOMAINS[@]}"; do echo "  - Ажиллаж байгаа: https://$d"; done
echo "  - Дотоод LAN: http://172.16.100.21 хэвээр"
echo "  - Сунгалт: автомат (acme.sh cron, 60 хоног тутам, nginx ~5с зогсоно)"
echo "  - АНХААР: Тохиргоо → Зогсоол → QR-ээ ДАХИН ТАТАЖ хэвлэнэ үү"
echo "    (хэвлэгдчихсэн QR нь $PRIMARY рүү заасан бол дахин хэвлэх шаардлагагүй)"
