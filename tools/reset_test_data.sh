#!/usr/bin/env bash
# Тестийн үеийн зогсолтын датаг цэвэрлэж тестийг шинээр эхлүүлнэ.
# ХАДГАЛАГДАНА: зогсоол, төхөөрөмж, тариф, хэрэглэгч, жолооч, хар жагсаалт, audit лог.
# УСТГАГДАНА: сешн, төлбөр, НӨАТ баримт, LPR лог, хаалтны команд, нөхөн төлбөр,
#             өдрийн тооцоо, кассын ээлж.
# Ажиллуулах: sudo bash /root/PARKING/tools/reset_test_data.sh
set -euo pipefail

BACKUP="/root/parking-backup-before-reset-$(date +%Y%m%d-%H%M%S).sql"
echo "==> 1/3 DB backup: $BACKUP"
sudo -u postgres pg_dump parking > "$BACKUP"

echo "==> 2/3 Тестийн транзакцын дата цэвэрлэж байна..."
sudo -u postgres psql parking <<'SQL'
BEGIN;
TRUNCATE TABLE vat_receipts, payments, barrier_commands, lpr_events,
  compensations, daily_settlements, cashier_shifts, parking_sessions;
COMMIT;
SQL

echo "==> 3/3 Backend дахин асааж байна..."
systemctl restart parking-backend

echo "Дууслаа. Тест шинээр эхлэхэд бэлэн. Backup: $BACKUP"
