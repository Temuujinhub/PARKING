#!/usr/bin/env bash
# Тестийн үеийн зогсолтын датаг цэвэрлэж тестийг шинээр эхлүүлнэ.
# ХАДГАЛАГДАНА: зогсоол, төхөөрөмж, тариф, хэрэглэгч, жолооч, хар жагсаалт, audit лог.
# УСТГАГДАНА: сешн, төлбөр, НӨАТ баримт, LPR лог, хаалтны команд, нөхөн төлбөр,
#             өдрийн тооцоо, кассын ээлж.
# Ажиллуулах: sudo bash /root/PARKING/tools/reset_test_data.sh
set -euo pipefail

[ "${PARKING_DEPLOY_ROLE:-}" = staging ] && [ "${PARKING_RESET_TEST_DATA:-}" = DELETE_TEST_TRANSACTIONS ] || {
  echo "Refusing reset: explicit staging role and DELETE_TEST_TRANSACTIONS confirmation required" >&2
  exit 1
}
TEST_DB=${PARKING_TEST_DATABASE_NAME:-}
[[ "$TEST_DB" =~ ^parking_test(_[a-zA-Z0-9]+)?$ ]] || {
  echo "PARKING_TEST_DATABASE_NAME must name a parking_test database; production 'parking' is forbidden" >&2
  exit 1
}
umask 077

BACKUP="/root/parking-backup-before-reset-$(date +%Y%m%d-%H%M%S).sql"
echo "==> 1/3 DB backup: $BACKUP"
sudo -u postgres pg_dump "$TEST_DB" > "$BACKUP"

echo "==> 2/3 Тестийн транзакцын дата цэвэрлэж байна..."
sudo -u postgres psql "$TEST_DB" <<'SQL'
BEGIN;
TRUNCATE TABLE vat_receipts, payments, barrier_commands, lpr_events,
  compensations, daily_settlements, cashier_shifts, parking_sessions;
COMMIT;
SQL

echo "==> 3/3 Backend дахин асааж байна..."
systemctl restart parking-backend

echo "Дууслаа. Тест шинээр эхлэхэд бэлэн. Backup: $BACKUP"
