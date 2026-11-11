#!/usr/bin/env bash
# TEST ба PROD-д SSH-ээр deploy хийх (өөрийн компьютерээс — VPN/дотоод сүлжээнд байх үед).
#
#   bash deploy/remote_deploy.sh test            # TEST-д origin/main (эсвэл заасан commit)
#   bash deploy/remote_deploy.sh prod            # PROD-д — TEST дээр deploy хийгдсэн ИЖИЛ commit л
#   bash deploy/remote_deploy.sh all             # TEST → шалгалт → (батлах) → PROD
#   bash deploy/remote_deploy.sh status          # хоёр серверийн одоогийн commit, health
#   bash deploy/remote_deploy.sh test <sha>      # тодорхой commit (origin/main-ийн өвөг байх ёстой)
#
# Хост (шаардлагатай бол орлуулна):
#   TEST_HOST=eptest@172.16.100.34  PROD_HOST=easyparking@172.16.100.21
#   SSH_OPTS="-p 22 -i ~/.ssh/id_ed25519"
#
# Сервер бүр дээр хийгдэх зүйл: энэ файлыг /tmp руу хуулж `sudo bash … --remote` ажиллуулна →
#   1. /etc/parking/deploy.env-ийн үүргийг шалгана (TEST=staging, PROD=production) — буруу хост руу
#      андуурч deploy хийхээс хамгаална
#   2. PROD: /etc/parking/approved-release-д commit-ийг бичнэ (өмнөхийг .prev-д)
#   3. deploy/update.sh — DB backup → код → deps → frontend build → restart → health
#      (GitHub хаалттай бол энэ компьютер дээр git bundle үүсгэж scp-ээр дамжуулна)
#   4. Шалгалт: health, deploy хийгдсэн commit, шинэ хүснэгт (partner_autopay_requests),
#      pillow хувилбар, autoPay-ийн .env тохиргоо, backend-ийн сүүлийн алдаа
# Windows: Git Bash эсвэл WSL-ээс ажиллуулна. Нууц үг олон асуухгүйн тулд ssh-copy-id хийхийг зөвлөнө.
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/Temuujinhub/PARKING.git}"
APP_DIR="${EP_APP_DIR:-/root/PARKING}"   # EP_APP_DIR — зөвхөн тестэд
REMOTE_TMP="/tmp/ep-remote-deploy.sh"
REMOTE_BUNDLE="/tmp/ep-upd.bundle"

# ════════════════════════════ СЕРВЕР ТАЛ (--remote) ════════════════════════════
remote_main() {
  local mode="$1" target="${2:-}" bundle="${3:-}"
  [ "$(id -u)" = 0 ] || { echo "sudo-гоор ажиллуулна"; exit 1; }
  cd "$APP_DIR"
  local role=""
  if [ -r /etc/parking/deploy.env ]; then
    role=$(sed -n 's/^[[:space:]]*PARKING_DEPLOY_ROLE[[:space:]]*=[[:space:]]*["'\'']\{0,1\}\([a-z]*\).*/\1/p' \
           /etc/parking/deploy.env | tail -1)
  fi
  echo "    сервер: $(hostname)  үүрэг: ${role:-тодорхойгүй (анхдагч production)}"
  echo "    одоогийн HEAD: $(git rev-parse --short HEAD)  deploy хийгдсэн: $(cut -c1-7 .git/parking-deployed-sha 2>/dev/null || echo —)"

  case "$mode" in
    status)
      curl --max-time 3 -fsS http://127.0.0.1:8000/api/health && echo || echo "    health: ХАРИУГҮЙ"
      echo "    approved-release: $(cut -c1-7 /etc/parking/approved-release 2>/dev/null || echo —)"
      return 0 ;;
    test)
      if [ "${role:-production}" != staging ]; then
        echo "    ЗОГСОВ: энэ сервер staging биш (deploy.env: ${role:-байхгүй}). TEST_HOST зөв эсэхийг шалга."
        echo "    TEST мөн бол нэг удаа: echo PARKING_DEPLOY_ROLE=staging | sudo tee -a /etc/parking/deploy.env"
        exit 2
      fi
      export PARKING_DEPLOY_ROLE=staging
      [ -n "$target" ] && export PARKING_DEPLOY_TARGET="$target"
      ;;
    prod)
      if [ "$role" = staging ]; then
        echo "    ЗОГСОВ: энэ бол staging (TEST) сервер — PROD_HOST буруу байна."
        exit 2
      fi
      [[ "$target" =~ ^[0-9a-f]{40}$ ]] || { echo "    ЗОГСОВ: PROD-д 40 тэмдэгт commit шаардлагатай"; exit 2; }
      export PARKING_DEPLOY_ROLE=production
      unset PARKING_DEPLOY_TARGET
      ;;
    *) echo "mode: test|prod|status"; exit 2 ;;
  esac

  # Autodeploy timer-тэй уралдахгүй: түгжээг ЭНД авна (update.sh дахин авахгүй). Явж буй
  # autodeploy байвал дуустал (≤15 мин) хүлээнэ; PROD-ийн approved-release-ийг түгжээний ДАРАА
  # бичдэг тул timer энэ commit-ийг зэрэг deploy хийж эхлэхгүй.
  exec 9>/run/lock/parking-deploy.lock
  echo "    deploy түгжээ хүлээж байна…"
  flock -w 900 9 || { echo "    ЗОГСОВ: өөр deploy 15 минутаас удаан явж байна"; exit 4; }
  export PARKING_DEPLOY_LOCK_HELD=1
  if [ "$mode" = prod ]; then
    mkdir -p /etc/parking
    [ -f /etc/parking/approved-release ] && cp -f /etc/parking/approved-release /etc/parking/approved-release.prev
    printf '%s\n' "$target" > /etc/parking/approved-release
    chmod 644 /etc/parking/approved-release
    echo "    approved-release ← $target"
  fi

  local update_sh="${EP_UPDATE_SH:-deploy/update.sh}"   # EP_UPDATE_SH — зөвхөн тестэд
  if [ -n "$bundle" ]; then
    bash "$update_sh" "$bundle"
  else
    bash "$update_sh"
  fi

  echo "==> Deploy-ийн дараах шалгалт"
  local deployed
  deployed=$(cat .git/parking-deployed-sha)
  echo "    deploy хийгдсэн: $deployed"
  # sudo-гүй уншигдах хуулбар — `remote_deploy.sh prod` TEST-ийн батлагдсан commit-ийг эндээс уншина
  mkdir -p /etc/parking && cp -f .git/parking-deployed-sha /etc/parking/deployed-sha
  chmod 644 /etc/parking/deployed-sha
  if [ -n "$target" ] && [ "$deployed" != "$target" ]; then
    echo "    АНХААР: хүссэн commit ($target)-оос өөр"; exit 3
  fi
  echo -n "    health: "; curl --max-time 3 -fsS http://127.0.0.1:8000/api/health || echo -n "ХАРИУГҮЙ"; echo
  echo -n "    autoPay хүснэгт: "
  sudo -u postgres psql -d parking -tAc \
    "SELECT 'partner_autopay_requests=' || count(*) FROM partner_autopay_requests" 2>&1 | tail -1 || true
  echo -n "    autoPay-тай машин: "
  sudo -u postgres psql -d parking -tAc \
    "SELECT count(*) FROM partner_vehicles WHERE deleted_at IS NULL AND auto_pay" 2>&1 | tail -1 || true
  # MonPay-ийн autoPay webhook (2026-11-11): backend асахдаа серверийн үүргээр тавина
  # (TEST → z-wallet.monpay.mn, PROD → wallet.monpay.mn — services/partner_presets.py)
  echo "    MonPay webhook:"
  sudo -u postgres psql -d parking -tAc \
    "SELECT '      ' || name || ' → ' || COALESCE(webhook_url, '(хоосон)') FROM partner_keys
     WHERE name IN ('mobifinance','monpay') AND is_active" 2>&1 | tail -3 || true
  # Камерын зураг (2026-11-11): хадгалалтын таг анхдагч 400GB (дискний 60%-иас хэтрэхгүй).
  # .env-д хуучин PARKING_RETENTION_SNAPSHOT_MAX_GB=20 г.м үлдсэн бол анхдагчийг ДАРНА — харуулна.
  echo "    Зургийн хадгалалт (.env-д ил тавьсан бол):"
  grep -E '^[[:space:]]*PARKING_(RETENTION_SNAPSHOT_[A-Z_]+|DISK_FREE_MIN_PERCENT|SNAPSHOT_CGI_FALLBACK|SCREEN_KEEP_[A-Z_]+)=' \
    backend/.env | sed 's/^/      /' || echo "      (тохируулаагүй — анхдагч: 400GB / дискний 60%)"
  echo -n "    pillow: "; backend/venv/bin/python -c 'import PIL; print(PIL.__version__)' 2>&1 | tail -1 || true
  echo "    .env (autoPay/мэдэгдэл — хоосон бол анхдагч: асаалттай):"
  grep -E '^[[:space:]]*PARKING_(PARTNER_VEHICLE_NOTIFY|PARTNER_AUTOPAY_[A-Z_]+|PUBLIC_BASE_URL)=' backend/.env \
    | sed 's/^/      /' || echo "      (тохируулаагүй — анхдагч утгууд)"
  echo "    backend-ийн сүүлийн 10 мин дэх алдаа/autopay мөрүүд:"
  journalctl -u parking-backend --since "-10 min" --no-pager 2>/dev/null \
    | grep -Ei "traceback|error|autopay" | tail -10 | sed 's/^/      /' || true
  rm -f "$REMOTE_BUNDLE"
}

if [ "${1:-}" = "--remote" ]; then
  shift
  remote_main "$@"
  exit $?
fi

# ════════════════════════════ ӨӨРИЙН КОМПЬЮТЕР ТАЛ ════════════════════════════
TEST_HOST="${TEST_HOST:-eptest@172.16.100.34}"
PROD_HOST="${PROD_HOST:-easyparking@172.16.100.21}"
# shellcheck disable=SC2206
SSH_ARGS=(-o ConnectTimeout=10 -o ServerAliveInterval=30 ${SSH_OPTS:-})
# shellcheck disable=SC2206
SCP_ARGS=(-o ConnectTimeout=10 ${SSH_OPTS:-})
SCP_ARGS=("${SCP_ARGS[@]/#-p/-P}")       # ssh -p → scp -P
SELF="${BASH_SOURCE[0]}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }

main_sha() {
  git ls-remote "$REPO_URL" refs/heads/main | cut -f1
}

push_self() {   # $1 = host
  scp -q "${SCP_ARGS[@]}" "$SELF" "$1:$REMOTE_TMP"
}

server_reaches_github() {   # $1 = host
  ssh -n "${SSH_ARGS[@]}" "$1" "timeout 20 git ls-remote $REPO_URL refs/heads/main >/dev/null 2>&1"
}

make_bundle() {   # энэ компьютер дээр main-ийн bundle (сервер GitHub-д хүрэхгүй үед)
  if [ ! -f "$WORK/upd.bundle" ]; then
    say "GitHub-аас татаж bundle үүсгэж байна (сервер GitHub-д хүрэхгүй)" >&2   # stdout = зам л
    git clone -q --bare --single-branch --branch main "$REPO_URL" "$WORK/repo.git" >&2
    git -C "$WORK/repo.git" bundle create "$WORK/upd.bundle" main >/dev/null 2>&1
  fi
  echo "$WORK/upd.bundle"
}

deploy_host() {   # $1 = test|prod, $2 = host, $3 = sha
  local mode="$1" host="$2" sha="$3" bundle_arg=""
  say "══ $(printf %s "$mode" | tr '[:lower:]' '[:upper:]'): $host → ${sha:0:7} ══"
  push_self "$host"
  if ! server_reaches_github "$host"; then
    local b; b=$(make_bundle)
    scp -q "${SCP_ARGS[@]}" "$b" "$host:$REMOTE_BUNDLE"
    bundle_arg="$REMOTE_BUNDLE"
    echo "    сервер GitHub-д хүрэхгүй — bundle-аар шинэчилнэ"
  fi
  # -t: sudo нууц үг асуух боломжтой
  ssh -t "${SSH_ARGS[@]}" "$host" "sudo bash $REMOTE_TMP --remote $mode $sha $bundle_arg; rc=\$?; rm -f $REMOTE_TMP; exit \$rc"
}

deployed_sha() {   # $1 = host — энэ скриптээр АМЖИЛТТАЙ deploy хийгдсэн commit (sudo-гүй уншина)
  ssh -n "${SSH_ARGS[@]}" "$1" "cat /etc/parking/deployed-sha 2>/dev/null" | tr -d '\r\n' || true
}

status() {
  for pair in "TEST:$TEST_HOST" "PROD:$PROD_HOST"; do
    say "${pair%%:*}: ${pair#*:}"
    push_self "${pair#*:}"
    ssh -t "${SSH_ARGS[@]}" "${pair#*:}" "sudo bash $REMOTE_TMP --remote status; rm -f $REMOTE_TMP" || true
  done
}

deploy_prod() {   # $1 = sha (заавал биш — үгүй бол TEST-ийн deploy хийгдсэн commit)
  local sha="${1:-}" test_sha
  test_sha=$(deployed_sha "$TEST_HOST" || true)
  [ -n "$sha" ] || sha="$test_sha"
  [[ "$sha" =~ ^[0-9a-f]{40}$ ]] || { echo "PROD-д гаргах commit тодорхойгүй (TEST-ээс уншиж чадсангүй)"; exit 1; }
  if [ "$sha" != "$test_sha" ]; then
    echo "ЗОГСОВ: ${sha:0:7} нь TEST дээр deploy хийгдээгүй (TEST: ${test_sha:-—}). Эхлээд: bash $SELF test $sha"
    exit 1
  fi
  echo "PROD-д гаргах: $sha"
  echo "  $(git ls-remote "$REPO_URL" refs/heads/main | cut -c1-7) = одоогийн origin/main"
  read -r -p "PROD (бодит зогсоолууд) руу deploy хийх үү? [yes/N] " ok
  [ "$ok" = yes ] || { echo "Цуцаллаа"; exit 1; }
  deploy_host prod "$PROD_HOST" "$sha"
}

cmd="${1:-}"
case "$cmd" in
  test)
    sha="${2:-$(main_sha)}"
    deploy_host test "$TEST_HOST" "$sha"
    ;;
  prod)
    deploy_prod "${2:-}"
    ;;
  all)
    sha="${2:-$(main_sha)}"
    deploy_host test "$TEST_HOST" "$sha"
    say "TEST амжилттай. https://test.easy-parking.mn дээр шалгаад үргэлжлүүлнэ үү."
    deploy_prod "$sha"
    ;;
  status)
    status
    ;;
  *)
    sed -n '2,8p' "$SELF"; exit 2 ;;
esac
say "Дууслаа."
