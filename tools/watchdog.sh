#!/usr/bin/env bash
# parking-watchdog: backend /api/health хариулахгүй бол авто restart.
# Минут тутам cron-оос ажиллана (/etc/cron.d/parking-watchdog — update.sh суулгадаг).
# 20с зайтай 2 удаа дараалан унасан үед л restart хийнэ — түр зуурын
# саатлаар дэмий restart хийхгүй. Админ гараар зогсоосон (inactive) үед оролцохгүй.

URL="http://127.0.0.1:8000/api/health"

state=$(systemctl is-active parking-backend 2>/dev/null)
[ "$state" = "active" ] || [ "$state" = "activating" ] || exit 0

ok() { curl -fsS -m 10 "$URL" >/dev/null 2>&1; }

ok && exit 0
sleep 20
ok && exit 0

logger -t parking-watchdog "health 2 удаа дараалан унав — parking-backend restart хийж байна"
# Гацсан процесс SIGTERM-д хариулдаггүй тул эхлээд SIGKILL
systemctl kill -s SIGKILL parking-backend 2>/dev/null || true
systemctl restart parking-backend
