#!/usr/bin/env bash
# PostgreSQL PITR (Point-In-Time Recovery) — WAL архивлалт + үечилсэн basebackup.
#
#   sudo bash /root/PARKING/deploy/setup_pitr.sh          # тохируулна (postgres RESTART хийнэ!)
#   sudo bash /root/PARKING/deploy/setup_pitr.sh --status # зөвхөн байдлыг харуулна
#
# Юуг шийддэг вэ: pg_dump нь өдөрт 2 удаа авдаг тул хамгийн муу тохиолдолд 12
# цагийн дата алдагдана. WAL архивлалттай бол ЯМАР Ч АГШИН хүртэл (жишээ нь
# «өчигдөр 14:32-т буруу устгал хийхээс өмнө») сэргээх боломжтой болно.
#
# Хийх зүйл:
#   • archive_mode=on, archive_command → /var/lib/parking/wal-archive
#   • wal_level=replica (default мөн адил), postgres RESTART (~3 секунд)
#   • Долоо хоног бүр pg_basebackup → /var/lib/parking/basebackup/<огноо>
#   • Хуучин WAL/basebackup-ыг pitr_days-аас хойш цэвэрлэнэ (cron)
# Диск: WAL сегмент 16MB; энэ ачаалалд өдөрт хэдхэн ширхэг. 14 хоногт ~1-2GB.
set -euo pipefail

ARCHIVE_DIR="/var/lib/parking/wal-archive"
BASE_DIR="/var/lib/parking/basebackup"
PITR_DAYS="${PITR_DAYS:-14}"

status() {
  echo "── PITR байдал ──"
  sudo -u postgres psql -tAc "SELECT name||' = '||setting FROM pg_settings WHERE name IN ('archive_mode','archive_command','wal_level');"
  echo "архив: $(ls -1 "$ARCHIVE_DIR" 2>/dev/null | wc -l) сегмент · $(du -sh "$ARCHIVE_DIR" 2>/dev/null | cut -f1 || echo '-')"
  echo "basebackup: $(ls -1d "$BASE_DIR"/* 2>/dev/null | wc -l) ширхэг · сүүлийнх $(ls -1dt "$BASE_DIR"/* 2>/dev/null | head -1 || echo '-')"
  sudo -u postgres psql -tAc "SELECT 'архивлагдсан='||archived_count||' амжилтгүй='||failed_count||COALESCE(' сүүлийн_алдаа='||last_failed_time::text,'') FROM pg_stat_archiver;"
}

[ "${1:-}" = "--status" ] && { status; exit 0; }

echo "==> 1/5 Хавтас бэлдэх"
mkdir -p "$ARCHIVE_DIR" "$BASE_DIR"
chown postgres:postgres "$ARCHIVE_DIR" "$BASE_DIR"
chmod 700 "$ARCHIVE_DIR" "$BASE_DIR"

echo "==> 2/5 PostgreSQL тохиргоо (ALTER SYSTEM)"
# %p = архивлах файлын зам, %f = нэр. Аль хэдийн байвал ДАРЖ БИЧИХГҮЙ (test -f)
# — давхардсан архив нь PITR-ийг эвдэж болзошгүй.
sudo -u postgres psql -q <<SQL
ALTER SYSTEM SET wal_level = 'replica';
ALTER SYSTEM SET archive_mode = 'on';
ALTER SYSTEM SET archive_command = 'test ! -f $ARCHIVE_DIR/%f && cp %p $ARCHIVE_DIR/%f';
ALTER SYSTEM SET archive_timeout = '300';
SQL

echo "==> 3/5 PostgreSQL restart (archive_mode restart шаарддаг, ~3 сек)"
systemctl restart postgresql
sleep 3
sudo -u postgres psql -tAc "SELECT 'archive_mode='||current_setting('archive_mode');"

echo "==> 4/5 Анхны basebackup"
STAMP=$(date +%Y%m%d-%H%M%S)
sudo -u postgres pg_basebackup -D "$BASE_DIR/$STAMP" -Ft -z -Xs -P
echo "    $BASE_DIR/$STAMP ($(du -sh "$BASE_DIR/$STAMP" | cut -f1))"

echo "==> 5/5 Cron: 7 хоног тутам basebackup + хуучин архив цэвэрлэх"
cat > /usr/local/bin/parking-pitr-maint <<'MAINT'
#!/usr/bin/env bash
# 7 хоног тутмын basebackup + хуучирсан WAL/basebackup цэвэрлэгээ
set -euo pipefail
ARCHIVE_DIR="/var/lib/parking/wal-archive"
BASE_DIR="/var/lib/parking/basebackup"
DAYS="${PITR_DAYS:-14}"
STAMP=$(date +%Y%m%d-%H%M%S)
sudo -u postgres pg_basebackup -D "$BASE_DIR/$STAMP" -Ft -z -Xs 2>/dev/null
# Хадгалах хугацаанаас хуучин basebackup-ууд (хамгийн сүүлийн 2-ыг ЯМАГТ үлдээнэ)
ls -1dt "$BASE_DIR"/* 2>/dev/null | tail -n +3 | while read -r d; do
  [ "$(find "$d" -maxdepth 0 -mtime +"$DAYS")" ] && rm -rf "$d"
done
# Хамгийн хуучин үлдсэн basebackup-аас өмнөх WAL хэрэггүй
OLDEST=$(ls -1dt "$BASE_DIR"/* 2>/dev/null | tail -1)
if [ -n "$OLDEST" ]; then
  LABEL=$(find "$ARCHIVE_DIR" -name "*.backup" -newermt "$(date -r "$OLDEST" '+%Y-%m-%d %H:%M:%S')" 2>/dev/null | sort | head -1)
  [ -n "$LABEL" ] && pg_archivecleanup "$ARCHIVE_DIR" "$(basename "${LABEL%%.*}")" 2>/dev/null || true
fi
# Аюулгүйн сүүл: хугацаа хэтэрсэн WAL-ыг ч цэвэрлэнэ (диск дүүрэхээс сэргийлнэ)
find "$ARCHIVE_DIR" -type f -mtime +$((DAYS + 7)) -delete 2>/dev/null || true
logger -t parking-pitr "basebackup $STAMP · архив $(du -sh "$ARCHIVE_DIR" | cut -f1)"
MAINT
chmod 755 /usr/local/bin/parking-pitr-maint
printf '30 4 * * 0 root PITR_DAYS=%s /usr/local/bin/parking-pitr-maint >/dev/null 2>&1\n' "$PITR_DAYS" \
  > /etc/cron.d/parking-pitr
chmod 644 /etc/cron.d/parking-pitr

echo
status
echo
echo "✓ PITR идэвхжлээ. Сэргээх заавар: docs/DR_PLAN.md → «PITR-ээр сэргээх»"
