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
DRY=""; EXTRA=""; EXTRA_FILES=""
for a in "$@"; do
  case "$a" in
    --dry-run) DRY="yes" ;;
    --scanner) EXTRA="$EXTRA,parking-scanner-guard.conf"
               EXTRA_FILES="$EXTRA_FILES parking-scanner-guard.conf" ;;
    --lpr)     EXTRA="$EXTRA,parking-lpr-guard.conf"
               EXTRA_FILES="$EXTRA_FILES parking-lpr-guard.conf" ;;
    *) echo "✗ мэдэхгүй сонголт: $a"; exit 2 ;;
  esac
done
EXTRA="${EXTRA#,}"
WITH=""; [ -n "$EXTRA" ] && WITH="--with=$EXTRA"

[ "$(id -u)" -eq 0 ] || { echo "✗ root эрхээр ажиллуулна уу (sudo)"; exit 1; }
command -v nginx >/dev/null || { echo "✗ nginx олдсонгүй"; exit 1; }

BACKUP_DIR=/var/backups/parking-nginx
export PARKING_NGINX_BACKUP_DIR="$BACKUP_DIR"
mkdir -p "$BACKUP_DIR"

# ── 0. Тэнэсэн нөөц файлыг зайлуулах ──────────────────────────────────────
# nginx-ийн `include sites-enabled/*` нь ӨРГӨТГӨЛӨӨР ШҮҮДЭГГҮЙ тул тэнд үлдсэн
# нөөц файлыг ч ачаалж, server блок давхардуулж `nginx -t`-г унагаана.
shopt -s nullglob
STRAY=(/etc/nginx/sites-enabled/*.backup-* /etc/nginx/conf.d/*.backup-*)
if [ ${#STRAY[@]} -gt 0 ]; then
  echo "⚠ nginx-ийн ачаалах хавтсанд нөөц файл байна — $BACKUP_DIR руу зөөж байна:"
  for s in "${STRAY[@]}"; do mv -v "$s" "$BACKUP_DIR/" | sed 's/^/    /'; done
fi

# ── 0b. Суурь төлөв — ЭХЛЭХИЙН ӨМНӨ тохиргоо эрүүл байх ёстой ─────────────
if ! nginx -t >/dev/null 2>&1; then
  echo "✗ nginx -t нь ӨӨРЧЛӨЛТ ХИЙХИЙН ӨМНӨ аль хэдийн унаж байна:"
  nginx -t 2>&1 | sed 's/^/    /'
  echo "  Эхлээд үүнийг засна уу — энэ скрипт юу ч хийхгүй гарлаа."
  exit 1
fi

# ── 1. Snippet суулгах ────────────────────────────────────────────────────
if [ -z "$DRY" ]; then
  install -D -m 0644 "$HERE/parking-security.conf" /etc/nginx/snippets/parking-security.conf
  echo "✓ snippets/parking-security.conf суулгав"
  for f in $EXTRA_FILES; do
    # Хэрэв өмнө нь суулгаад ГАРААР засаж (камерын дэд сүлжээ нэмэх г.м) байвал
    # дарж бичихгүй — тэр засвар нь зориудынх.
    if [ -f "/etc/nginx/snippets/$f" ] && ! cmp -s "$HERE/$f" "/etc/nginx/snippets/$f"; then
      echo "• snippets/$f — гараар засагдсан байна, ХЭВЭЭР үлдээв"
    else
      install -D -m 0644 "$HERE/$f" "/etc/nginx/snippets/$f"
      echo "✓ snippets/$f суулгав"
    fi
  done
fi

# ── 2. vhost бүрд include тараах ──────────────────────────────────────────
VHOSTS=(/etc/nginx/sites-enabled/* /etc/nginx/conf.d/*.conf)
CHANGED=()
for f in "${VHOSTS[@]}"; do
  [ -f "$f" ] || continue
  case "$f" in *parking-ratelimit.conf|*.backup-*) continue ;; esac
  grep -q "server\s*{\|location\s" "$f" || continue        # vhost биш файлыг алгасна
  if [ -n "$DRY" ]; then
    python3 "$HERE/apply_security_headers.py" "$f" $WITH
  else
    before=$(md5sum "$f" | cut -d' ' -f1)
    python3 "$HERE/apply_security_headers.py" "$f" --write $WITH
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
    b=$(ls -t "$BACKUP_DIR/$(basename "$f")".backup-* 2>/dev/null | head -1)
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
echo "Хэрэглээ:"
echo "  --scanner   сканнерын шуугианг таслах (үе шат 2, эрсдэл бага)"
echo "  --lpr       LPR callback хаах (үе шат 4 — ЭХЛЭЭД ХЭМЖИНЭ, HARDENING.md үз)"
