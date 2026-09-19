#!/usr/bin/env bash
# Root cron entry point. Keep the ACME home and existing renewal policy intact.
# TLS-ALPN renewal still briefly owns port 443; this is recovery, not zero downtime.
set -Eeuo pipefail
umask 077
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
[ "$EUID" = 0 ] || { echo 'Root required' >&2; exit 2; }
for command_name in flock timeout systemctl nginx logger; do
  command -v "$command_name" >/dev/null || exit 2
done
[ -x /root/.acme.sh/acme.sh ] || exit 2
exec 9>/run/lock/parking-deploy.lock
if ! flock -n 9; then
  logger -t parking-acme-renewal 'SKIPPED: deployment or another renewal holds lock'
  exit 75
fi
# A scheduled renewal must not start while the site is already under maintenance.
systemctl is-active --quiet nginx.service || { logger -t parking-acme-renewal 'SKIPPED: nginx was not active'; exit 75; }
nginx -t -q || { logger -t parking-acme-renewal 'SKIPPED: nginx configuration invalid'; exit 2; }
finish() {
  local result=$?
  trap - EXIT HUP INT TERM
  if ! systemctl is-active --quiet nginx.service; then
    if nginx -t -q && systemctl start nginx.service && systemctl is-active --quiet nginx.service; then
      logger -t parking-acme-renewal "RECOVERED: nginx restored after ACME exit=$result"
    else
      logger -p daemon.err -t parking-acme-renewal "RECOVERY_FAILED: ACME exit=$result; nginx needs attention"
      exit 1
    fi
  fi
  logger -t parking-acme-renewal "COMPLETE: ACME exit=$result; nginx active"
  exit "$result"
}
trap finish EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
# timeout terminates the process group, including the standalone ALPN listener,
# before the EXIT handler starts nginx. Keep details local, not in cron mail.
log=/var/log/parking-acme-renewal.log
touch "$log"
chmod 600 "$log"
logger -t parking-acme-renewal 'START: certificate renewal with nginx recovery guard'
timeout --signal=TERM --kill-after=10s 300s /root/.acme.sh/acme.sh \
  --cron --home /root/.acme.sh >> "$log" 2>&1
