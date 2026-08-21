#!/usr/bin/env bash
# nginx хамгаалалтын header-ийг ЭНЭ сервер дээр хэрэгжүүлнэ (үе шат 1).
#
# ЭРСДЭЛГҮЙ: backend-д хүрэхгүй, `reload` нь graceful (идэвхтэй холболт,
# WebSocket таслагдахгүй), `nginx -t` унавал нөөцөөс АВТОМАТААР буцаана.
# Дахин дахин ажиллуулж болно — өөрчлөх зүйл байхгүй бол юу ч хийхгүй.
#
#   sudo bash /root/PARKING/deploy/nginx/apply_headers.sh          # хэрэгжүүлнэ
#   sudo bash /root/PARKING/deploy/nginx/apply_headers.sh --dry-run # зөвхөн харна
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DRY=""
[ "${1:-}" = "--dry-run" ] && DRY="yes"

[ "$(id -u)" -eq 0 ] || { echo "✗ root эрхээр ажиллуулна уу (sudo)"; exit 1; }
command -v nginx >/dev/null || { echo "✗ nginx олдсонгүй"; exit 1; }

# ── 1. Snippet суулгах ────────────────────────────────────────────────────
if [ -z "$DRY" ]; then
  install -D -m 0644 "$HERE/parking-security.conf" /etc/nginx/snippets/parking-security.conf
  echo "✓ snippets/parking-security.conf суулгав"
fi

# ── 2. vhost бүрд include тараах ──────────────────────────────────────────
shopt -s nullglob
VHOSTS=(/etc/nginx/sites-enabled/* /etc/nginx/conf.d/*.conf)
CHANGED=()
for f in "${VHOSTS[@]}"; do
  [ -f "$f" ] || continue
  case "$f" in *parking-ratelimit.conf|*.backup-*) continue ;; esac
  grep -q "server\s*{\|location\s" "$f" || continue        # vhost биш файлыг алгасна
  if [ -n "$DRY" ]; then
    python3 "$HERE/apply_security_headers.py" "$f"
  else
    before=$(md5sum "$f" | cut -d' ' -f1)
    python3 "$HERE/apply_security_headers.py" "$f" --write
    [ "$(md5sum "$f" | cut -d' ' -f1)" != "$before" ] && CHANGED+=("$f")
  fi
done
[ -n "$DRY" ] && { echo; echo "(--dry-run — юу ч бичээгүй)"; exit 0; }

# ── 3. Шалгаад reload; унавал БУЦААНА ─────────────────────────────────────
if nginx -t 2>&1 | sed 's/^/  /'; then
  systemctl reload nginx && echo "✓ nginx reload хийгдлээ (тасалдалгүй)"
else
  echo "✗ nginx -t УНАЛАА — нөөцөөс буцааж байна…"
  for f in "${CHANGED[@]}"; do
    b=$(ls -t "$f".backup-* 2>/dev/null | head -1)
    [ -n "$b" ] && cp "$b" "$f" && echo "  ↩ $f ← $b"
  done
  nginx -t >/dev/null 2>&1 && echo "✓ буцаалт амжилттай — тохиргоо хэвээр" \
    || echo "✗✗ АНХААР: буцаалтын дараа ч nginx -t унаж байна — гараар шалгана уу"
  exit 1
fi

# ── 4. Үр дүнг харуулах ───────────────────────────────────────────────────
echo
echo "── Одоогийн header (localhost-оор) ──"
curl -skI https://127.0.0.1/ 2>/dev/null | grep -iE 'x-frame|content-type-options|strict-transport|referrer-policy|^server:' \
  || curl -sI http://127.0.0.1/ | grep -iE 'x-frame|content-type-options|referrer-policy|^server:'
echo
echo "Дараагийн үе шат: deploy/nginx/HARDENING.md (rate limit / LPR / UFW — хэмжилт шаардана)"
