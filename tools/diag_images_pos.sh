#!/usr/bin/env bash
# Оношилгоо (2026-11-11): (1) камерын зураг ХЭЗЭЭНЭЭС хадгалагдахаа больсон, тэр үед юу болсон;
# (2) POS-ын үйлдэл хоорондын хугацаа (гарцад ирэх → POS prepare → төлсөн → хаалт), серверийн ачаалал.
# ЗӨВХӨН УНШИНА — DB, файл, тохиргоонд юу ч бичихгүй.
#
#   sudo bash tools/diag_images_pos.sh 2>&1 | tee /tmp/diag-1111.txt
#   (POS-ын зогсоолыг өөрчлөх:  SITE_LIKE=%Гэгээнтэн% sudo -E bash tools/diag_images_pos.sh)
set -u
SITE_LIKE="${SITE_LIKE:-%Гэгээнтэн%}"
DAYS="${DAYS:-21}"
APP=/root/PARKING
SNAP=/var/lib/parking/snapshots
PSQL=(sudo -u postgres psql -d parking -X -P pager=off -v ON_ERROR_STOP=0)
# Тестэд: DIAG_PSQL="psql -h 127.0.0.1 -p 55432 -U postgres -d parking -X -P pager=off"
if [ -n "${DIAG_PSQL:-}" ]; then read -r -a PSQL <<<"$DIAG_PSQL"; fi
q() { "${PSQL[@]}" -c "$1" 2>&1; }
h() { printf '\n==================== %s ====================\n' "$*"; }

h "0. Сервер, хувилбар, deploy-ийн цаг"
date -u '+UTC %F %T'; uptime; nproc; free -m | head -2
git -C "$APP" log -12 --format='%h %ad %s' --date=format:'%m-%d %H:%M' 2>&1 | cut -c1-110
echo "--- deploy-ийн DB backup (= deploy хийсэн цаг):"
ls -l --time-style=+%m-%d_%H:%M /root/parking-backup-*.sql 2>/dev/null | awk '{print $6, $5}' | tail -15
du -ch /root/parking-backup-*.sql 2>/dev/null | tail -1
grep -h PARKING_DEPLOY_ROLE /etc/parking/deploy.env 2>/dev/null
grep -E '^[[:space:]]*PARKING_(SNAPSHOT|SNAP_|RETENTION|DISK_FREE|PUBLIC_BASE_URL|SCREEN_)' "$APP/backend/.env" 2>/dev/null

h "1. Диск"
df -h / "$SNAP" 2>/dev/null | uniq
du -sh "$SNAP" 2>/dev/null
du -sh /var/lib/postgresql/*/main/pg_wal 2>/dev/null
echo "--- Өдөр тутмын зургийн хавтас (файл, хэмжээ) — сүүлийн $DAYS:"
for d in $(ls -1 "$SNAP" 2>/dev/null | grep -E '^[0-9]{8}$' | tail -n "$DAYS"); do
  printf '%s %6s файл %8s\n' "$d" "$(find "$SNAP/$d" -type f | wc -l)" "$(du -sh "$SNAP/$d" | cut -f1)"
done

h "2. Зурагтай сешний хувь — өдрөөр (УБ цаг)"
q "SELECT to_char(entry_time + interval '8 hour','MM-DD') AS өдөр,
        count(*) AS орсон, count(entry_snapshot) AS зурагтай,
        round(100.0*count(entry_snapshot)/nullif(count(*),0),1) AS pct
   FROM parking_sessions
  WHERE entry_device_id IS NOT NULL AND entry_time >= timezone('UTC', now()) - interval '$DAYS days'
  GROUP BY 1 ORDER BY 1;"
q "SELECT to_char(exit_time + interval '8 hour','MM-DD') AS өдөр,
        count(*) AS гарсан, count(exit_snapshot) AS зурагтай,
        round(100.0*count(exit_snapshot)/nullif(count(*),0),1) AS pct
   FROM parking_sessions
  WHERE exit_device_id IS NOT NULL AND exit_time >= timezone('UTC', now()) - interval '$DAYS days'
  GROUP BY 1 ORDER BY 1;"

h "3. Орох зургийн эх үүсвэр — өдрөөр (payload / snapshot.cgi / comet …)"
q "SELECT to_char(entry_time + interval '8 hour','MM-DD') AS өдөр,
        coalesce(entry_snapshot_source, CASE WHEN entry_snapshot IS NULL THEN '(зураггүй)' ELSE '(тодорхойгүй)' END) AS эх,
        count(*)
   FROM parking_sessions
  WHERE entry_device_id IS NOT NULL AND entry_time >= timezone('UTC', now()) - interval '14 days'
  GROUP BY 1, 2 ORDER BY 1, 2;"

h "4. Зураг ТАСАРСАН цаг — сүүлийн 14 хоногийн цаг тутмын орох зургийн хувь (≤60% цагууд)"
q "SELECT to_char(date_trunc('hour', entry_time) + interval '8 hour','MM-DD HH24:00') AS цаг,
        count(*) AS орсон, count(entry_snapshot) AS зурагтай,
        round(100.0*count(entry_snapshot)/nullif(count(*),0)) AS pct
   FROM parking_sessions
  WHERE entry_device_id IS NOT NULL AND entry_time >= timezone('UTC', now()) - interval '14 days'
  GROUP BY 1 HAVING count(*) >= 10 AND 100.0*count(entry_snapshot)/count(*) <= 60
  ORDER BY 1 LIMIT 60;"

h "5. Зогсоол тус бүр — сүүлийн 3 хоног"
q "SELECT s.name AS зогсоол, count(*) AS орсон, count(ps.entry_snapshot) AS орох_зураг,
        round(100.0*count(ps.entry_snapshot)/nullif(count(*),0)) AS pct
   FROM parking_sessions ps JOIN parking_sites s ON s.id = ps.site_id
  WHERE ps.entry_device_id IS NOT NULL AND ps.entry_time >= timezone('UTC', now()) - interval '3 days'
  GROUP BY 1 ORDER BY 4 NULLS FIRST, 2 DESC;"

h "6. «Камераас гар зураг (Manual Snapshot)» тохиргоо (barrier_rules)"
q "SELECT updated_at, updated_by,
        value->'manual_snapshot' AS ерөнхий,
        (SELECT count(*) FROM json_each(coalesce(value->'_sites','{}'::json)) e
          WHERE e.value->>'manual_snapshot' = 'true') AS зогсоол_асаасан,
        (SELECT count(*) FROM json_each(coalesce(value->'_sites','{}'::json)) e
          WHERE e.value->>'manual_snapshot' = 'false') AS зогсоол_унтраасан
   FROM app_settings WHERE key = 'barrier_rules';"
q "SELECT created_at + interval '8 hour' AS цаг, username, left(detail::text, 160) AS дэлгэрэнгүй
   FROM audit_logs WHERE entity IN ('barrier_rules','payment_rules') OR action ILIKE '%RULE%'
  ORDER BY created_at DESC LIMIT 10;"

h "7. Backend лог — зураг (сүүлийн 2 хоног)"
J=$(mktemp /tmp/diag-journal.XXXXXX)
trap 'rm -f "$J"' EXIT
journalctl -u parking-backend --since "-2 days" --no-pager -o short-iso > "$J" 2>/dev/null
printf 'журнал: %s мөр (%s → %s)\n' "$(wc -l < "$J")" "$(head -1 "$J" | cut -c1-19)" "$(tail -1 "$J" | cut -c1-19)"
printf 'Manual Snapshot унтраалттай → зураггүй : %s\n' "$(grep -c 'Manual Snapshot тохиргоогоор унтраалттай' "$J")"
printf 'snapshot.cgi OK                         : %s\n' "$(grep -c 'OK (snapshot.cgi' "$J")"
printf 'snapshot.cgi failed                     : %s\n' "$(grep -c 'snapshot.cgi failed' "$J")"
printf 'зураг ОЛДСОНГҮЙ                         : %s\n' "$(grep -c 'зураг ОЛДСОНГҮЙ' "$J")"
printf 'event image deadline expired (4с хүлээв): %s\n' "$(grep -c 'event image deadline expired' "$J")"
printf 'диск рүү хадгалж чадсангүй              : %s\n' "$(grep -c 'хадгалж чадсангүй' "$J")"
grep -E 'retention|No space' "$J" | tail -5 | cut -c1-200
echo "--- Журналын нийт хэмжээ / хамгийн эртний мөр:"
journalctl --disk-usage 2>/dev/null
timeout 30 journalctl -u parking-backend --no-pager -o short-iso 2>/dev/null | head -1 | cut -c1-19

h "8. POS — confirm хугацаа (лог: мөнгө+хаалт мс, баримт мс) сүүлийн 2 хоног"
grep 'POS confirm:' "$J" | sed -nE 's/.*POS confirm: ([0-9]+)мс.*баримт ([0-9]+)мс.*/\1 \2/p' | sort -n -k1 | awk '
  {a[NR]=$1; b[NR]=$2; sb+=$2} END {
    if (!NR) {print "мөр алга"; exit}
    printf "n=%d  мөнгө+хаалт: p50=%d p90=%d max=%d мс | баримт дундаж=%d мс\n",
           NR, a[int(NR*0.5)+0], a[int(NR*0.9)+0], a[NR], sb/NR }'
grep 'POS confirm:' "$J" | tail -8 | cut -c1-160
printf 'SLOW мөр (2с+ хүсэлт, шинэ хувилбараас): %s\n' "$(grep -c ' SLOW ' "$J")"
grep ' SLOW ' "$J" | sed -nE 's/.*SLOW ([A-Z]+ [^ ]+) .*/\1/p' | sort | uniq -c | sort -rn | head -10

h "9. POS урсгалын хугацаа — гарцад ирсэн → POS prepare → төлсөн → хаалт (7 хоног, зогсоолоор, сек)"
q "WITH p AS (
  SELECT s.name AS site, pay.created_at AS c, pay.paid_at AS pd, ps.payment_wait_started_at AS w,
         (SELECT b.created_at FROM barrier_commands b WHERE b.session_id = pay.session_id AND b.command = 'open'
            AND b.created_at >= pay.paid_at - interval '5 second' ORDER BY b.created_at LIMIT 1) AS g,
         (SELECT b.duration_ms FROM barrier_commands b WHERE b.session_id = pay.session_id AND b.command = 'open'
            AND b.created_at >= pay.paid_at - interval '5 second' ORDER BY b.created_at LIMIT 1) AS gms
    FROM payments pay JOIN parking_sessions ps ON ps.id = pay.session_id
    JOIN parking_sites s ON s.id = ps.site_id
   WHERE pay.provider = 'POS' AND pay.status = 'PAID'
     AND pay.paid_at >= timezone('UTC', now()) - interval '7 days')
SELECT site, count(*) AS n,
  round(percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM pd - w))::numeric) AS ирсэн_төлсөн_p50,
  round(percentile_cont(0.9) WITHIN GROUP (ORDER BY extract(epoch FROM pd - w))::numeric) AS ирсэн_төлсөн_p90,
  round(percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM pd - c))::numeric, 1) AS prepare_төлсөн_p50,
  round(percentile_cont(0.9) WITHIN GROUP (ORDER BY extract(epoch FROM pd - c))::numeric, 1) AS prepare_төлсөн_p90,
  round(percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM g - pd))::numeric, 1) AS төлсөн_хаалт_p50,
  round(percentile_cont(0.9) WITHIN GROUP (ORDER BY extract(epoch FROM g - pd))::numeric, 1) AS төлсөн_хаалт_p90,
  round(percentile_cont(0.5) WITHIN GROUP (ORDER BY gms)::numeric) AS хаалт_мс_p50
FROM p GROUP BY site ORDER BY n DESC LIMIT 15;"

h "10. $SITE_LIKE — сүүлийн 25 POS төлбөр (УБ цаг; сек)"
q "SELECT to_char(ps.payment_wait_started_at + interval '8 hour','MM-DD HH24:MI:SS') AS ирсэн,
        round(extract(epoch FROM pay.created_at - ps.payment_wait_started_at)) AS ирсэн_prepare,
        round(extract(epoch FROM pay.paid_at - pay.created_at), 1) AS prepare_төлсөн,
        round(extract(epoch FROM (SELECT min(b.created_at) FROM barrier_commands b WHERE b.session_id = pay.session_id
              AND b.command = 'open' AND b.created_at >= pay.paid_at - interval '5 second') - pay.paid_at), 1) AS төлсөн_хаалт,
        (SELECT b.duration_ms FROM barrier_commands b WHERE b.session_id = pay.session_id AND b.command = 'open'
              AND b.created_at >= pay.paid_at - interval '5 second' ORDER BY b.created_at LIMIT 1) AS хаалт_мс,
        pay.amount::int AS дүн, pay.terminal_id
   FROM payments pay JOIN parking_sessions ps ON ps.id = pay.session_id
   JOIN parking_sites s ON s.id = ps.site_id
  WHERE pay.provider = 'POS' AND pay.status = 'PAID' AND s.name ILIKE '$SITE_LIKE'
  ORDER BY pay.paid_at DESC LIMIT 25;"
q "SELECT username, terminal_id, pos_version, status, network,
        to_char(last_seen_at + interval '8 hour','MM-DD HH24:MI') AS сүүлд, request_count
   FROM pos_clients WHERE site_id IN (SELECT id FROM parking_sites WHERE name ILIKE '$SITE_LIKE')
  ORDER BY last_seen_at DESC NULLS LAST LIMIT 10;"

h "11. Серверийн ачаалал"
ps -eo pid,pcpu,pmem,etime,cmd --sort=-pcpu | head -8 | cut -c1-140
ss -tn state established '( sport = :8000 )' 2>/dev/null | tail -n +2 | wc -l | sed 's/^/backend холболт: /'
q "SELECT count(*) FILTER (WHERE state <> 'idle') AS идэвхтэй, count(*) AS нийт FROM pg_stat_activity;"
q "SELECT round(extract(epoch FROM now() - query_start)) AS сек, state, left(regexp_replace(query, '\s+', ' ', 'g'), 110) AS query
   FROM pg_stat_activity WHERE state <> 'idle' AND pid <> pg_backend_pid() ORDER BY 1 DESC LIMIT 6;"

h "12. Аль API хамгийн олон дуудагдаж байна (nginx-ийн сүүлийн 200 мянган мөр)"
LOG=/var/log/nginx/access.log
if [ -r "$LOG" ]; then
  echo "муж: $(tail -n 200000 "$LOG" | head -1 | awk '{print $4}') → $(tail -n 1 "$LOG" | awk '{print $4}')"
  tail -n 200000 "$LOG" | awk '{print $7}' | sed -E 's/\?.*//; s/[0-9a-f]{8}-[0-9a-f-]{27}/{id}/g' \
    | grep '^/api/' | sort | uniq -c | sort -rn | head -15
  echo "--- /api/reports/dashboard ба /api/sessions (Шалгах) дуудсан IP:"
  tail -n 200000 "$LOG" | awk '$7 ~ /^\/api\/(reports\/dashboard|sessions\?)/ {print $1}' | sort | uniq -c | sort -rn | head -8
else
  echo "nginx access.log алга"
fi
echo; echo "ДУУСЛАА — /tmp/diag-1111.txt-ийн агуулгыг илгээнэ үү."
