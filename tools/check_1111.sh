#!/usr/bin/env bash
# 2026-11-11-ний өөрчлөлтүүдийг ШАЛГАХ (PR #29–#32) — ЗӨВХӨН УНШИНА: DB, файл, тохиргоонд юу ч бичихгүй.
#
#   sudo bash /root/PARKING/tools/check_1111.sh 2>&1 | tee /tmp/check-1111.txt
#
# Сервер дээр энэ файл хараахан байхгүй бол (deploy хийлгүйгээр) GitHub-аас шууд:
#   sudo git -C /root/PARKING fetch -q origin main && sudo git -C /root/PARKING show origin/main:tools/check_1111.sh > /tmp/check_1111.sh && sudo bash /tmp/check_1111.sh 2>&1 | tee /tmp/check-1111.txt
#
# Хэсэг бүрийн гарчигт «✓ хүлээгдэх» үр дүн бичигдсэн. HOURS=48 гэвэл 48 цагийг харна.
set -u
APP="${APP:-/root/PARKING}"
SNAP=/var/lib/parking/snapshots
HOURS="${HOURS:-24}"
PSQL=(sudo -u postgres psql -d parking -X -P pager=off -v ON_ERROR_STOP=0)
# Тестэд: DIAG_PSQL="psql -h 127.0.0.1 -p 55432 -U postgres -d parking -X -P pager=off"
if [ -n "${DIAG_PSQL:-}" ]; then read -r -a PSQL <<<"$DIAG_PSQL"; fi
q() { "${PSQL[@]}" -c "$1" 2>&1; }
h() { printf '\n==================== %s ====================\n' "$*"; }
SINCE="timezone('UTC', now()) - interval '$HOURS hours'"
J=$(mktemp /tmp/check-journal.XXXXXX)
trap 'rm -f "$J"' EXIT
journalctl -u parking-backend --since "-$HOURS hours" --no-pager -o short-iso > "$J" 2>/dev/null
n() { grep -c -- "$1" "$J"; }

h "0. Хувилбар — ✓ commit b8486e5 (эсвэл шинэ), schema 40549cd51a96"
date -u '+UTC %F %T'
git -C "$APP" log -1 --format='%h %ci %s' 2>&1 | cut -c1-110
curl -s --max-time 5 http://127.0.0.1:8000/api/health; echo
systemctl show -p ActiveEnterTimestamp parking-backend 2>/dev/null
echo "--- deploy-ийн DB backup (= deploy хийсэн цаг, UTC):"
ls -l --time-style=+%m-%d_%H:%M /root/parking-backup-*.sql 2>/dev/null | awk '{print $6}' | tail -6
printf 'журнал (сүүлийн %sц): %s мөр\n' "$HOURS" "$(wc -l < "$J")"

h "1. Камерын зураг — ✓ цаг бүр орох/гарах ≥90% (11-11 22:35 УБ-аас хойш); зураггүй нь тодорхой камерт"
q "WITH e AS (SELECT date_trunc('hour', entry_time) h, count(*) n, count(entry_snapshot) s FROM parking_sessions
             WHERE entry_device_id IS NOT NULL AND entry_time >= $SINCE GROUP BY 1),
        x AS (SELECT date_trunc('hour', exit_time) h, count(*) n, count(exit_snapshot) s FROM parking_sessions
             WHERE exit_device_id IS NOT NULL AND exit_time >= $SINCE GROUP BY 1)
   SELECT to_char(coalesce(e.h, x.h) + interval '8 hour', 'MM-DD HH24:00') AS \"УБ цаг\",
          e.n AS орсон, round(100.0 * e.s / nullif(e.n, 0)) AS \"орох %\",
          x.n AS гарсан, round(100.0 * x.s / nullif(x.n, 0)) AS \"гарах %\"
     FROM e FULL JOIN x ON e.h = x.h ORDER BY coalesce(e.h, x.h);"
echo "--- Зураггүй үлдсэн машин — камераар (сүүлийн ${HOURS}ц, ихээс нь):"
q "SELECT 'орох' AS чиглэл, st.name AS зогсоол, d.name AS камер, d.ip_address, count(*) AS нийт,
          count(*) - count(s.entry_snapshot) AS зураггүй
     FROM parking_sessions s JOIN devices d ON d.id = s.entry_device_id JOIN parking_sites st ON st.id = s.site_id
    WHERE s.entry_time >= $SINCE GROUP BY st.name, d.name, d.ip_address HAVING count(*) > count(s.entry_snapshot)
   UNION ALL
   SELECT 'гарах', st.name, d.name, d.ip_address, count(*), count(*) - count(s.exit_snapshot)
     FROM parking_sessions s JOIN devices d ON d.id = s.exit_device_id JOIN parking_sites st ON st.id = s.site_id
    WHERE s.exit_time >= $SINCE GROUP BY st.name, d.name, d.ip_address HAVING count(*) > count(s.exit_snapshot)
   ORDER BY 6 DESC LIMIT 15;"
printf 'snapshot.cgi OK: %s | зураг ОЛДСОНГҮЙ: %s | хаалтанд тасарсан: %s | камер түр зогссон: %s\n' \
  "$(n 'OK (snapshot.cgi')" "$(n 'зураг ОЛДСОНГҮЙ')" "$(n 'хаалтны команд ирлээ')" "$(n 'дараалан амжилтгүй — ')"
echo "--- «зураг ОЛДСОНГҮЙ»-ийн шалтгаан (камер: шалтгаан):"
grep 'зураг ОЛДСОНГҮЙ' "$J" | sed 's/^.*(payload-д алга, камер //; s/[0-9]* мин түр/N мин түр/' \
  | sort | uniq -c | sort -rn | head -12
echo "--- Зургийн хавтас / диск (хадгалалт 400GB, дискний 60% хүртэл):"
du -sh "$SNAP" 2>/dev/null
df -h "$SNAP" 2>/dev/null | tail -1
grep -E '^[[:space:]]*PARKING_(RETENTION_SNAPSHOT_[A-Z_]+|SNAPSHOT_[A-Z_]+|SCREEN_KEEP_[A-Z_]+)=' "$APP/backend/.env" 2>/dev/null \
  || echo "(.env-д тусгай тохиргоогүй — анхдагч утгууд)"

h "2. Гарах LED-ийн төлөх дүн — ✓ «дахин илгээж барив» олон; «зураг хүлээж байна» тасалсан нь гарах зургийн үед"
printf 'дүнг дахин илгээж барьсан (машин): %s\n' "$(n 'дахин илгээж барив')"
grep 'дахин илгээж барив' "$J" | sed -nE 's/.*дүнг ([0-9]+) удаа.*/\1/p' \
  | awk '{s+=$1; if ($1>m) m=$1} END {if (NR) printf "  дахин илгээлт: дундаж %.1f, хамгийн их %d удаа\n", s/NR, m}'
printf 'давталтыг тасалсан — хаалтанд: %s | зурагт: %s\n' \
  "$(n 'хаалт хүлээж байна — давталтыг')" "$(n 'зураг хүлээж байна — давталтыг')"
printf 'LED бичиж чадаагүй: %s\n' "$(n 'бичиж чадсангүй')"

h "3. Easy Wallet хурдны хязгаар — ✓ easywallet 120/мин; 429 зөвхөн easywallet-ийн /sessions-д"
q "SELECT name, rate_limit_per_min AS \"хязгаар/мин\", to_char(last_used_at + interval '8 hour', 'MM-DD HH24:MI') AS сүүлд
     FROM partner_keys WHERE is_active ORDER BY name;"
grep 'Түншийн хурдны хязгаар' "$J" | tail -3 | cut -c1-200
LOG=/var/log/nginx/access.log
if [ -r "$LOG" ]; then
  echo "--- nginx /api/v1 — зам, HTTP код (сүүлийн 200 мянган мөр; $(tail -n 200000 "$LOG" | head -1 | awk '{print $4}' | tr -d '[') →):"
  tail -n 200000 "$LOG" | awk '$7 ~ /^\/api\/v1\// {split($7, a, "?"); split(a[1], s, "/"); k = s[4];
      if (k == "socialpay" || k == "vehicles") k = k "/" s[5]; print k, $9}' | sort | uniq -c | sort -rn | head -14
else
  echo "nginx access.log алга"
fi

h "4. MonPay webhook — ✓ PROD-д MonPay-ийн түлхүүр үүсэх хүртэл «алга» (үүсгэсний дараах restart-д тавигдана)"
q "SELECT name, is_active, webhook_url FROM partner_keys WHERE name ILIKE '%monpay%' OR name ILIKE '%mobifinance%';"
grep 'MonPay webhook preset' "$J" | tail -2 | cut -c1-220

h "5. POS хурд — ✓ confirm p50 ≲0.5с; SLOW (2с+) мөр цөөн"
grep 'POS confirm:' "$J" | sed -nE 's/.*POS confirm: ([0-9]+)мс.*баримт ([0-9]+)мс.*/\1 \2/p' | sort -n -k1 | awk '
  {a[NR]=$1; b+=$2} END {
    if (!NR) {print "мөр алга"; exit}
    printf "n=%d  мөнгө+хаалт: p50=%d p90=%d max=%d мс | баримт дундаж=%d мс\n",
           NR, a[int(NR*0.5)+0], a[int(NR*0.9)+0], a[NR], b/NR }'
printf 'SLOW мөр: %s\n' "$(n ' SLOW ')"
grep ' SLOW ' "$J" | sed -nE 's/.*SLOW ([A-Z]+ [^ ]+) .*/\1/p' | sort | uniq -c | sort -rn | head -8

h "6. Зогсоолоос хасах — ✓ хадгалсан (★) шалтгаан; хасалтад шалтгаан бичигдсэн"
q "SELECT json_array_elements_text((value::json)->'items') AS \"хадгалсан шалтгаан\" FROM app_settings WHERE key = 'remove_reasons';"
q "SELECT coalesce(nullif(detail->>'reason', ''), '(шалтгаангүй)') AS шалтгаан, count(*) AS удаа,
          sum((detail->>'count')::int) AS машин
     FROM audit_logs WHERE action = 'ADMIN_REMOVE' AND created_at >= $SINCE
    GROUP BY 1 ORDER BY 2 DESC LIMIT 10;"

h "7. Машин блоклох — ✓ бодлого (DENY/ALLOW/дүрмээр) + шалтгаан; операторын блок"
q "SELECT coalesce(entry_policy, '(дүрмээр)') AS бодлого, count(*) AS идэвхтэй FROM blacklist WHERE is_active GROUP BY 1;"
q "SELECT to_char(created_at + interval '8 hour', 'MM-DD HH24:MI') AS цаг, username, action,
          detail->>'plate' AS дугаар, detail->>'entry_policy' AS бодлого, left(detail->>'reason', 60) AS шалтгаан
     FROM audit_logs WHERE entity = 'blacklist' AND created_at >= $SINCE ORDER BY created_at DESC LIMIT 10;"

h "8. Wallet — байгууллагын e-Barimt (payer_reg_no) — түнш ашиглаж эхэлсэн эсэх"
q "SELECT provider, coalesce(ebarimt_receiver_type, '(хувь хүн)') AS баримт, count(*) AS төлбөр
     FROM payments WHERE status = 'PAID' AND paid_at >= $SINCE
      AND provider NOT IN ('QPAY', 'POS', 'CASH', 'TRANSFER', 'WALLET')
    GROUP BY 1, 2 ORDER BY 1, 2;"

h "9. Ачаалал"
uptime
ps -eo pid,pcpu,pmem,etime,cmd --sort=-pcpu | head -5 | cut -c1-120

h "10. UI-д гараар шалгах"
cat <<'EOF'
  [ ] Түүх — орох/гарах зураг шинэ машинуудад байна (10.0.120.10/.11-ийг тусад нь)
  [ ] Гарах LED — төлөх дүн машин төлөх хүртэл харагдана (эхэнд 2–3с алга болж болно)
  [ ] Өр цэвэрлэх — дугаараар хайхад БҮХ зогсоолын өр гарна (хуудасны зогсоолын сонголтоос үл хамаарч)
  [ ] Шалгах → «Зогсоолоос хасах» — ★ хадгалсан болон сүүлийн шалтгаан нэг товчоор
  [ ] Касс/Шалгах — машин блоклох: нэвтрүүлэх/нэвтрүүлэхгүй + шалтгаан, дараагийн операторт харагдана
  [ ] Тохиргоо → Холболт → Гадаад API — easywallet «Хязгаар: 120/мин · 429: N»
  [ ] Системийн эрүүл мэнд — «Камерын зураг (24ц)», «API хүсэлтийн хугацаа»
EOF
echo; echo "ДУУСЛАА — /tmp/check-1111.txt-ийн агуулгыг илгээнэ үү."
