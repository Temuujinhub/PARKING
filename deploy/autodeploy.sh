#!/usr/bin/env bash
# Серверийг origin/main-тай автомат тааруулна (тест болон production хоёуланд).
# systemd timer (parking-autodeploy.timer) 2 минут тутам дуудна — origin/main
# урагшилсан үед Л deploy/update.sh-г ажиллуулна (DB backup → deps → build →
# restart → health шалгалт бүгд түүнд бий). GitHub хүрэхгүй бол чимээгүй
# өнгөрөөд дараагийн удаа дахин оролдоно (байгууллагын firewall GitHub-ыг
# үе үе хаадаг — тэр үед гараар bundle-аар: update.sh /зам/upd.bundle).
#
# Суулгах (нэг удаа, сервер бүр дээр):
#   sudo cp /root/PARKING/deploy/parking-autodeploy.service \
#           /root/PARKING/deploy/parking-autodeploy.timer /etc/systemd/system/
#   sudo systemctl daemon-reload
#   sudo systemctl enable --now parking-autodeploy.timer
# Лог харах:  journalctl -u parking-autodeploy -n 50
set -uo pipefail
cd /root/PARKING || exit 0

BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)
[ "$BRANCH" = "main" ] || exit 0   # feature branch дээр байвал хөндөхгүй (гар preview)

# Гар (commit хийгээгүй) өөрчлөлттэй үед дарж бичихгүй — хөгжүүлэлт явж буй
# тэст сервер дээр update.sh-ийн reset --hard ажлыг устгахаас сэргийлнэ.
git diff --quiet 2>/dev/null && git diff --cached --quiet 2>/dev/null || {
  echo "[autodeploy] commit хийгээгүй өөрчлөлт байна — алгасав"; exit 0; }

export GIT_SSH_COMMAND="ssh -o ConnectTimeout=10 -o BatchMode=yes"
timeout 60 git fetch origin main --quiet 2>/dev/null || exit 0   # GitHub хаалттай — дараа дахин
LOCAL=$(git rev-parse HEAD 2>/dev/null)
REMOTE=$(git rev-parse origin/main 2>/dev/null)
[ "$LOCAL" = "$REMOTE" ] && exit 0

echo "[autodeploy $(date -u +%FT%TZ)] $LOCAL → $REMOTE — update.sh эхэллээ"
bash deploy/update.sh
