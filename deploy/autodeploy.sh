#!/usr/bin/env bash
# Тэст серверийг (152.42.235.199) origin/main-тай автомат тааруулна.
# systemd timer 2 минут тутам дуудна. origin/main өөрчлөгдсөн үед л build+restart хийнэ.
# GitHub дээр PR merge хийсний дараа гараар юу ч ажиллуулахгүйгээр тэст сервер шинэчлэгдэнэ.
set -uo pipefail
cd /root/PARKING || exit 0

git fetch origin main --quiet 2>/dev/null || exit 0
LOCAL=$(git rev-parse HEAD 2>/dev/null)
REMOTE=$(git rev-parse origin/main 2>/dev/null)
BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)

# Зөвхөн main дээр байгаа бөгөөд origin/main урагшилсан үед л deploy хийнэ.
# (feature branch дээр байвал хөндөхгүй — гар preview-г эвдэхгүй.)
[ "$BRANCH" = "main" ] || exit 0
[ "$LOCAL" = "$REMOTE" ] && exit 0

echo "[autodeploy $(date -u +%FT%TZ)] $LOCAL → $REMOTE"
git reset --hard origin/main --quiet
backend/venv/bin/pip install -q -r backend/requirements.txt 2>/dev/null
( cd frontend && NODE_OPTIONS=--max-old-space-size=1400 npm run build >/dev/null 2>&1 \
  && cp -r dist/* /var/www/parking/ && chown -R www-data:www-data /var/www/parking )
systemctl restart parking-backend
echo "[autodeploy] дууслаа: $(git rev-parse --short HEAD)"
