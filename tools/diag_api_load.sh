#!/usr/bin/env bash
# Easy Parking — API-ийн ачаалал, хариуны хугацааны оношилгоо (ЗӨВХӨН УНШИНА).
#
# «POS-ын дэлгэц солигдохдоо удаан» (2026-11-13) гомдлын ШАЛТГААНЫГ хэмжих:
#   1. Сервер: CPU / RAM / load, uvicorn (1 процесс) ба PostgreSQL-ийн ачаалал
#   2. Одоогийн хариуны хугацаа: backend-д ШУУД ба nginx-ээр (ялгаа = nginx/хязгаар)
#   3. Backend журнал: event loop ЦАРЦАЛТ (≥500мс), SLOW хүсэлт (≥2с), DB pool алдаа, restart
#   4. nginx урсгал: минут тутмын хүсэлт, зам, клиент (Easy Wallet / SocialPay / POS апп / вэб),
#      429 (хязгаар), 499 (клиент хүлээж чадалгүй тасалсан = УДААН), 5xx
#   5. nginx-ийн rate limit /api/-д идэвхтэй эсэх (бүх клиент нэг NAT IP-ээр ирдэг!)
#   6. PostgreSQL: холболт, удаан/түгжигдсэн query, seq scan их хүснэгт, pg_stat_statements
#   7. Түнш (wallet): түлхүүр, хязгаар, webhook, машин бүртгэл, гадагш явах дараалал
#   8. (сонголттой) апп доторх зам тутмын p95/max — ADMIN нэвтрэлтээр (/api/health/system)
#
# Юуг ч ӨӨРЧЛӨХГҮЙ, файл/DB/тохиргоонд БИЧИХГҮЙ. Ажиллуулах (VPN + SSH, PROD дээр):
#   sudo bash /root/PARKING/tools/diag_api_load.sh 2>&1 | tee /tmp/diag-api.txt
#   sudo env HOURS=6 bash /root/PARKING/tools/diag_api_load.sh        # 6 цагийн цонх
# Deploy-гүйгээр (энэ файл серверт ороогүй үед) — ӨӨРИЙН КОМПЬЮТЕРЭЭС (VPN, Git Bash/WSL);
# PROD GitHub-д хүрэхгүй байж болох тул файлыг компьютероор дамжуулна:
#   curl -fsSL -o diag_api_load.sh \
#     https://raw.githubusercontent.com/Temuujinhub/PARKING/refs/heads/<branch>/tools/diag_api_load.sh
#   scp diag_api_load.sh easyparking@172.16.100.21:/tmp/diag_api_load.sh
#   ssh -t easyparking@172.16.100.21 'sudo bash /tmp/diag_api_load.sh' | tee diag-api.txt
# ADMIN нэвтрэлтийг (8-р хэсэг) асуухгүй алгасах: sudo env SKIP_LOGIN=1 bash ...
set -uo pipefail

HOURS=${HOURS:-3}
APP=${APP:-/root/PARKING}
PY=${PY:-$APP/backend/venv/bin/python}
NGX_LOG=${NGX_LOG:-/var/log/nginx/access.log}
NGX_ERR=${NGX_ERR:-/var/log/nginx/error.log}
API=${API:-http://127.0.0.1:8000}
DOMAIN=${DOMAIN:-site.easy-parking.mn}
UNIT=${UNIT:-parking-backend}
DB=${DB:-$(grep -E '^PARKING_DATABASE_URL=' "$APP/backend/.env" 2>/dev/null | sed -E 's#.*/([^/?]+)(\?.*)?$#\1#')}
DB=${DB:-parking}
if [ -z "${PSQL:-}" ]; then
  if command -v runuser >/dev/null 2>&1; then PSQL="runuser -u postgres -- psql -X -P pager=off -d $DB"
  else PSQL="sudo -u postgres psql -X -P pager=off -d $DB"; fi
fi
[ -x "$PY" ] || PY=python3

hr() { printf '\n══════ %s ══════\n' "$*"; }
q()  { eval "$PSQL" -c "\"$1\"" 2>&1 | sed -e '/^$/d'; }

echo "Easy Parking API оношилгоо — $(date '+%Y-%m-%d %H:%M:%S %Z') · host $(hostname) · цонх ${HOURS}ц"
echo "Код: $(git -C "$APP" log -1 --pretty='%h %ad %s' --date=short 2>/dev/null | cut -c1-90)"

# ───────────────────────────── 1. Сервер ─────────────────────────────
hr "1. СЕРВЕР"
echo "CPU цөм: $(nproc) · load: $(cut -d' ' -f1-3 /proc/loadavg) · uptime: $(uptime -p 2>/dev/null)"
free -m | sed -n '1,3p'
df -h / 2>/dev/null | tail -1 | awk '{print "Диск /: " $3 " / " $2 " (" $5 ")"}'
UV=$(pgrep -f "uvicorn app.main:app" | head -1)
if [ -n "$UV" ]; then
  echo "uvicorn PID $UV · worker: $(pgrep -fc 'uvicorn app.main:app') процесс · thread: $(ps -o nlwp= -p "$UV" | tr -d ' ')" \
       "· RSS: $(( $(ps -o rss= -p "$UV" | tr -d ' ') / 1024 ))MB · ажилласан: $(ps -o etime= -p "$UV" | tr -d ' ')"
  # 5 секундийн ДУНДАЖ CPU (ps-ийн %CPU нь процессын амьдралын дундаж тул top-оор хэмжинэ)
  top -b -d 5 -n 2 -p "$UV" 2>/dev/null | awk -v p="$UV" '$1==p{c=$9} END{print "uvicorn CPU (5с дундаж): " c "%  (100% = нэг цөм бүтэн — 1 процесс тул энэ нь ДЭЭД ХЯЗГААР)"}'
else
  echo "⚠ uvicorn процесс олдсонгүй"
fi
ps -eo pcpu=,comm= | awk '$2 ~ /postgres/ {s+=$1; n++} END{printf "PostgreSQL: %d процесс, нийт CPU %.0f%% (ps дундаж)\n", n, s}'
echo "--- Хамгийн их CPU (ps) ---"
ps -eo pid,pcpu,pmem,etime,comm --sort=-pcpu | head -8
if command -v vmstat >/dev/null; then echo "--- vmstat 1 5 (us=хэрэглэгч, sy=систем, wa=диск хүлээлт, id=сул) ---"; vmstat 1 5 | tail -6; fi

# ───────────────────────────── 2. Одоогийн хариуны хугацаа ─────────────────────────────
hr "2. ОДООГИЙН ХАРИУНЫ ХУГАЦАА (20 удаа × 0.3с)"
probe() {  # probe <нэр> <curl аргументууд...>   (mawk-д ажиллана: asort ашиглахгүй)
  local name=$1; shift
  local out
  out=$(for i in $(seq 1 20); do curl --noproxy '*' -s -o /dev/null -m 20 -w '%{time_total} %{http_code}\n' "$@"; sleep 0.3; done)
  local codes
  codes=$(echo "$out" | awk '{c[$2]++} END{for(k in c) printf "%s×%d ", k, c[k]}')
  echo "$out" | awk '{print $1*1000}' | sort -n | awk -v n="$name" -v codes="$codes" '
      {t[NR]=$1; s+=$1} END{ if (!NR) {print n ": хариу алга"; exit}
      i=int(NR*0.95); if (i<1) i=1;
      printf "%-46s min %5.0fмс  дундаж %5.0fмс  p95 %5.0fмс  max %6.0fмс  [%s]\n", n, t[1], s/NR, t[i], t[NR], codes}'
}
probe "backend ШУУД  /api/health (хөнгөн DB)"         "$API/api/health"
SITE_CODE=$(q "select site_code from parking_sites where is_active order by site_code limit 1" 2>/dev/null | sed -n '3p' | tr -d ' ')
[ -n "$SITE_CODE" ] && probe "backend ШУУД  /api/public/site/$SITE_CODE (DB)" "$API/api/public/site/$SITE_CODE"
probe "nginx-ээр   https://$DOMAIN/api/health"         -k --resolve "$DOMAIN:443:127.0.0.1" "https://$DOMAIN/api/health"
echo "(/api/health нь зөвхөн хөнгөн схемийн шалгалт хийдэг (LIMIT 0, түгжээ үүсгэхгүй): ШУУД нь >50мс бол"
echo " event loop/процесс өөр ажилд эзлэгдсэн; nginx-ээр нь ШУУД-аас"
echo " хамаагүй удаан бол nginx/TLS/хязгаар; 429 гарвал nginx-ийн rate limit.)"

# ───────────────────────────── 2б. Секунд тутмын шугам ─────────────────────────────
hr "2б. ${TL:-40} СЕКУНДЫН ШУГАМ — секунд бүр /api/health (царцалт давтагддаг эсэх)"
TLF=$(mktemp)
for i in $(seq 0 $(( ${TL:-40} - 1 ))); do
  ( curl --noproxy '*' -s -o /dev/null -m 30 -w "$i %{time_total}\n" "$API/api/health" >> "$TLF" ) &
  sleep 1
done
wait
sort -n "$TLF" | awk '{ms=$2*1000; bar=""; for(k=0;k<ms/250 && k<40;k++) bar=bar "#";
    printf "t=%2dс %6.0fмс %s\n", $1, ms, bar; if (ms>1000) {slow++; if (!first) first=$1}; if (ms>mx) mx=ms}
    END{printf "-- 1с-ээс удаан: %d/%d, хамгийн удаан %.0fмс%s\n", slow, NR, mx,
        slow ? " → ДАВТАГДАХ ЦАРЦАЛТ (2026-11-13 хүртэл: 20с тутам ~8.5с — log_tail-ийн lpr_events хайлт)" : " → царцалтгүй"}'
rm -f "$TLF"

# ───────────────────────────── 3. Backend журнал ─────────────────────────────
hr "3. BACKEND ЖУРНАЛ (сүүлийн ${HOURS}ц)"
journalctl -u "$UNIT" --since "-${HOURS}h" --no-pager -o short-iso 2>/dev/null | "$PY" -c '
import re, sys, collections
freeze, slow, other = [], collections.defaultdict(list), collections.Counter()
partner, starts, lines = [], 0, 0
for ln in sys.stdin:
    lines += 1
    m = re.search(r"event loop (\d+)мс ЦАРЦЛАА", ln)
    if m: freeze.append((int(m.group(1)), ln[:19]))
    m = re.search(r"SLOW (\S+) (\S+) (\d+)мс \(HTTP (\d+)", ln)
    if m: slow[m.group(1) + " " + m.group(2)].append(int(m.group(3)))
    if "хурдны хязг" in ln: partner.append(ln.strip()[:200])
    if "Started server process" in ln: starts += 1
    for k in ("QueuePool limit", "TimeoutError", "OperationalError", "Traceback", "lock timeout", "statement timeout"):
        if k in ln: other[k] += 1
print(f"Журналын мөр: {lines} · backend restart: {starts}")
if freeze:
    tot = sum(f[0] for f in freeze)
    print(f"EVENT LOOP ЦАРЦАЛТ: {len(freeze)} удаа, нийт {tot/1000:.1f}с, хамгийн урт {max(freeze)[0]}мс")
    for ms, ts in sorted(freeze, reverse=True)[:8]: print(f"   {ts}  {ms}мс")
    by_hour = collections.Counter(ts[11:13] for _, ts in freeze)
    print("   цаг тутам: " + ", ".join(f"{h}ц:{n}" for h, n in sorted(by_hour.items())))
else:
    print("EVENT LOOP ЦАРЦАЛТ: 0 (≥500мс-ийн хоцрогдол алга)")
if slow:
    print(f"SLOW (≥2с) хүсэлт: {sum(len(v) for v in slow.values())} — зам тутам:")
    for k, v in sorted(slow.items(), key=lambda kv: -len(kv[1]))[:15]:
        v.sort(); print(f"   {len(v):>5}×  дундаж {sum(v)//len(v):>6}мс  max {v[-1]:>6}мс  {k}")
else:
    print("SLOW (≥2с) хүсэлт: 0")
if other: print("Алдааны тэмдэг: " + ", ".join(f"{k}×{n}" for k, n in other.most_common()))
if partner:
    print("Түншийн хурдны хязгаар (сүүлийн 3):")
    for p in partner[-3:]: print("   " + p)
' 2>&1 || echo "⚠ journalctl уншиж чадсангүй"

# ───────────────────────────── 4. nginx урсгал ─────────────────────────────
hr "4. NGINX УРСГАЛ (сүүлийн ${HOURS}ц) — $NGX_LOG"
"$PY" - "$NGX_LOG" "$HOURS" <<'PYEOF' 2>&1 || echo "⚠ nginx лог уншиж чадсангүй"
import re, sys, os, collections, datetime as dt
log, hours = sys.argv[1], float(sys.argv[2])
rx = re.compile(r'^(\S+) \S+ \S+ \[([^\]]+)\] "(\S+) (\S+)[^"]*" (\d{3}) \S+ "[^"]*" "([^"]*)"')
uuid = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', re.I)
def client(ua):
    u = ua.lower()
    if "apache-httpclient" in u: return "Easy Wallet (Apache-HttpClient/Java)"
    if "java/" in u: return "SocialPay (Java)"
    if "okhttp" in u: return "Android апп (okhttp) — POS?"
    if "dart/" in u: return "Flutter апп (Dart) — POS?"
    if "mozilla" in u: return "Хөтөч (админ/касс/QR хуудас)"
    if "python" in u or "curl" in u: return "Скрипт (python/curl)"
    return (ua or "-")[:40]
def route(path):
    p = path.split("?")[0]
    p = uuid.sub("{id}", p)
    p = re.sub(r"/api/public/(site|recent-exits|qr)/[^/]+", r"/api/public/\1/{code}", p)
    p = re.sub(r"/(checkout|check-cost)/[^/]+", r"/\1/{id}", p)
    return p
now = dt.datetime.now(dt.timezone.utc); since = now - dt.timedelta(hours=hours)
files = [f for f in (log + ".1", log) if os.path.exists(f)]
tot, per_min, by_client, by_route = 0, collections.Counter(), collections.Counter(), collections.Counter()
st_route = collections.defaultdict(collections.Counter); st_client = collections.defaultdict(collections.Counter)
uas, ips = collections.Counter(), collections.Counter()
for f in files:
    with open(f, errors="replace") as fh:
        for ln in fh:
            m = rx.match(ln)
            if not m: continue
            try: t = dt.datetime.strptime(m.group(2), "%d/%b/%Y:%H:%M:%S %z")
            except ValueError: continue
            if t < since: continue
            ip, meth, path, st, ua = m.group(1), m.group(3), m.group(4), int(m.group(5)), m.group(6)
            if not path.startswith(("/api/", "/ws/", "/pay", "/checkout", "/check-cost")): continue
            tot += 1; per_min[t.strftime("%H:%M")] += 1
            c, r = client(ua), meth + " " + route(path)
            by_client[c] += 1; by_route[r] += 1; uas[ua[:70]] += 1; ips[ip] += 1
            k = "429" if st == 429 else "499" if st == 499 else f"{st//100}xx"
            st_route[r][k] += 1; st_client[c][k] += 1
if not tot:
    print("Энэ цонхонд /api хүсэлт олдсонгүй (лог хоосон/эргэлдсэн эсвэл формат өөр)."); sys.exit(0)
mins = max(1, len(per_min))
print(f"Нийт /api хүсэлт: {tot} · минутад дундаж {tot/mins:.0f}, СЕКУНДЭД ~{tot/mins/60:.1f} · ачаалалтай минутууд: "
      + ", ".join(f"{k}={v}" for k, v in per_min.most_common(5)))
print("IP (NAT-аар нэгдсэн эсэх): " + ", ".join(f"{ip}={n} ({n*100/tot:.0f}%)" for ip, n in ips.most_common(4)))
print("--- Клиент тус бүр (хувь · 429 · 499 · 5xx) ---")
for c, n in by_client.most_common(10):
    s = st_client[c]
    print(f"   {n:>7} {n*100/tot:5.1f}%  429:{s['429']:<5} 499:{s['499']:<5} 5xx:{s['5xx']:<4} {c}")
print("--- Хамгийн их дуудагддаг 25 зам (тоо · минутад · 429 · 499 · 5xx) ---")
for r, n in by_route.most_common(25):
    s = st_route[r]
    print(f"   {n:>7} {n/mins:6.1f}/мин  429:{s['429']:<5} 499:{s['499']:<5} 5xx:{s['5xx']:<4} {r}")
bad = sorted(((s["499"] + s["5xx"], r) for r, s in st_route.items() if s["499"] + s["5xx"]), reverse=True)[:10]
if bad:
    print("--- 499 (клиент тасалсан) / 5xx ихтэй замууд — УДААШРАЛЫН шууд шинж ---")
    for n, r in bad: print(f"   {n:>6}  {r}")
print("--- User-Agent (POS аппыг танихад) ---")
for ua, n in uas.most_common(8): print(f"   {n:>7}  {ua}")
PYEOF

# ───────────────────────────── 5. nginx тохиргоо ─────────────────────────────
hr "5. NGINX ХЯЗГААР / TIMEOUT"
if command -v nginx >/dev/null; then
  nginx -T 2>/dev/null | grep -nE "^\s*(limit_req|limit_conn|proxy_read_timeout|proxy_connect_timeout|keepalive_timeout|worker_connections|access_log|log_format)" | head -20
  echo "nginx-ийн 'limiting requests' (хязгаарт цохигдсон) сүүлийн ${HOURS}ц-т: $(awk -v d="$(date -d "-${HOURS} hours" '+%Y/%m/%d %H:%M:%S')" '$1" "$2 >= d' "$NGX_ERR" 2>/dev/null | grep -c 'limiting requests')"
  awk -v d="$(date -d "-${HOURS} hours" '+%Y/%m/%d %H:%M:%S')" '$1" "$2 >= d' "$NGX_ERR" 2>/dev/null | grep -E 'limiting requests|upstream timed out|no live upstreams|Connection refused' | tail -5 | cut -c1-220
  grep -q 'request_time' <(nginx -T 2>/dev/null) || echo "ℹ access.log-д \$request_time алга — хүсэлт тус бүрийн хугацааг nginx хэмждэггүй (8-р хэсгийн апп доторх статистикийг харна уу)."
fi

# ───────────────────────────── 6. PostgreSQL ─────────────────────────────
hr "6. POSTGRESQL ($DB)"
q "select coalesce(state,'-') as state, coalesce(wait_event_type,'-') as wait, count(*) from pg_stat_activity where datname=current_database() group by 1,2 order by 3 desc"
echo "--- 0.5с-ээс удаан ажиллаж буй query ---"
q "select pid, round(extract(epoch from now()-query_start)::numeric,1) as sec, state, coalesce(wait_event_type||':'||wait_event,'-') as wait, left(regexp_replace(query,'\s+',' ','g'),110) as query from pg_stat_activity where datname=current_database() and state<>'idle' and pid<>pg_backend_pid() and now()-query_start > interval '0.5 second' order by sec desc limit 10"
echo "Түгжээ хүлээж буй: $(q "select count(*) from pg_locks where not granted" | sed -n '3p' | tr -d ' ')"
echo "--- Хүснэгтүүд (seq scan-аар уншсан мөр их = индексгүй хайлт) ---"
q "select relname, n_live_tup as rows, seq_scan, seq_tup_read, idx_scan, pg_size_pretty(pg_total_relation_size(relid)) as size from pg_stat_user_tables order by seq_tup_read desc limit 10"
if q "select 1 from pg_extension where extname='pg_stat_statements'" | grep -q '(1 row)'; then
  echo "--- pg_stat_statements: нийт хугацаагаар топ 12 ---"
  q "select calls, round(mean_exec_time::numeric,1) as mean_ms, round((total_exec_time/1000)::numeric,1) as total_s, left(regexp_replace(query,'\s+',' ','g'),100) as query from pg_stat_statements where dbid=(select oid from pg_database where datname=current_database()) order by total_exec_time desc limit 12"
else
  echo "ℹ pg_stat_statements суугаагүй — query тус бүрийн статистик алга (суулгах нь тохиргоо өөрчлөх тул энд хийхгүй)."
fi

# ───────────────────────────── 7. Түнш (wallet) ─────────────────────────────
hr "7. ТҮНШ (WALLET) — түлхүүр, webhook, гадагш явах дараалал"
q "select name, is_active as active, coalesce(rate_limit_per_min::text,'∞') as rpm, to_char(last_used_at,'MM-DD HH24:MI') as last_used, (coalesce(webhook_url,'')<>'') as pay_hook, (coalesce(vehicle_webhook_url,'')<>'') as veh_hook from partner_keys order by last_used_at desc nulls last"
q "select k.name as partner, count(*) as vehicles from partner_vehicles v join partner_keys k on k.id=v.partner_key_id group by 1 order by 2 desc"
q "select 'partner_vehicle_events' as queue, status, count(*) from partner_vehicle_events group by 2 union all select 'partner_autopay_requests', status, count(*) from partner_autopay_requests where created_at > now() - interval '${HOURS} hours' group by 2 union all select 'financial_jobs', status, count(*) from financial_jobs where status <> 'DONE' group by 2 order by 1,3 desc"
echo "--- Төхөөрөмж / POS терминал (backend-ийн нэг процесс эдгээрийг бүгдийг зэрэг барьдаг) ---"
q "select device_type, count(*) as total, count(*) filter (where last_seen > now() at time zone 'utc' - interval '5 minutes') as seen_5min from devices group by 1 order by 2 desc"
q "select count(*) as pos_terminals, count(*) filter (where last_seen_at > now() at time zone 'utc' - interval '1 day') as seen_24h from pos_clients"

# ───────────────────────────── 8. Апп доторх зам тутмын хугацаа ─────────────────────────────
hr "8. АПП ДОТОРХ ЗАМ ТУТМЫН ХУГАЦАА (RequestTimingMiddleware — ADMIN нэвтрэлт)"
if [ -n "${SKIP_LOGIN:-}" ] || [ ! -r /dev/tty ]; then
  echo "Алгаслаа (SKIP_LOGIN эсвэл терминалгүй). Вэбээс: Админ → Системийн эрүүл мэнд → «API хүсэлтийн хугацаа»."
else
  read -r -p "ADMIN/SUPER_ADMIN нэр (Enter = алгасах): " DU </dev/tty || DU=""
  if [ -n "$DU" ]; then
    read -r -s -p "Нууц үг (харагдахгүй, хаана ч хадгалахгүй): " DP </dev/tty; echo
    DU="$DU" DP="$DP" API="$API" "$PY" - <<'PYEOF'
import os, json, urllib.request, urllib.parse
api = os.environ["API"]
data = urllib.parse.urlencode({"username": os.environ["DU"], "password": os.environ["DP"]}).encode()
try:
    tok = json.load(urllib.request.urlopen(urllib.request.Request(api + "/api/auth/login", data=data), timeout=20))["access_token"]
    req = urllib.request.Request(api + "/api/health/system", headers={"Authorization": "Bearer " + tok})
    r = json.load(urllib.request.urlopen(req, timeout=60)).get("requests") or {}
except Exception as e:  # noqa: BLE001
    print("⚠ Нэвтэрч/уншиж чадсангүй:", e); raise SystemExit(0)
import datetime as dt
since = dt.datetime.fromtimestamp(r.get("since", 0)).strftime("%H:%M") if r.get("since") else "?"
print(f"Цонх {since}-аас: нийт {r.get('requests')} хүсэлт, ≥{r.get('slow_ms')}мс: {r.get('slow')}")
def show(title, rows):
    print(f"--- {title} ---")
    print(f"   {'тоо':>6} {'дундаж':>7} {'p95':>7} {'max':>7} {'удаан':>5} {'алдаа':>5}  зам")
    for x in rows:
        print(f"   {x['count']:>6} {x['avg_ms']:>6}мс {x['p95_ms']:>6}мс {x['max_ms']:>6}мс {x['slow']:>5} {x['errors']:>5}  {x['method']} {x['path']}")
show("POS-ын замууд", r.get("pos") or [])
show("p95-аар хамгийн удаан замууд", r.get("top") or [])
PYEOF
  else
    echo "Алгаслаа."
  fi
fi

hr "ТАЙЛБАР (гаралтыг Claude-д буцааж илгээнэ үү)"
cat <<'EOF'
• 2б-д 20с тутам ~8с царцалт → log_tail-ийн засвар (2026-11-13) deploy хийгдээгүй байна.
• 3-т ЦАРЦАЛТ олон/урт  → нэг процессын event loop блоклогдож БҮХ хүсэлт (POS ч) хүлээж байна:
                           камерын арын ажлыг тусдаа процесс болгох, API-г олон worker болгох.
• 4-т Wallet/SocialPay-ийн хувь их + 3-т ЦАРЦАЛТ давхцах → түншийн polling ачаалал (хязгаар/webhook).
• 4-т POS замуудад 499 их    → POS апп хариу хүлээж чадалгүй тасалсан (удаашрал серверт).
• 2-т ШУУД хурдан, nginx-ээр удаан, 5-т 'limiting requests' → nginx хязгаар нэг NAT IP-д цохигдож байна.
• 6-д удаан query / түгжээ → DB-ийн query эсвэл индексийн засвар.
• 8-д POS зам тус бүрийн p95 → яг аль дэлгэцийн шилжилт удаан болохыг заана.
EOF
