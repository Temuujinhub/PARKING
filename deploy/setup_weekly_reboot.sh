#!/bin/bash
# Долоо хоног тутмын АЮУЛГҮЙ сервер reboot — kernel/багцын шинэчлэлтийг тогтмол
# идэвхжүүлж «Сервер дахин ачаалах шаардлагатай» сануулгыг хуримтлуулахгүй.
#
#   sudo bash /root/PARKING/deploy/setup_weekly_reboot.sh          # суулгах
#   sudo bash /root/PARKING/deploy/setup_weekly_reboot.sh remove   # болиулах
#
# Хэрхэн ажилладаг (Даваа гараг 04:30, УБ цагаар = Ням 20:30 UTC — хамгийн
# зогсоол хөдөлгөөнгүй цаг):
#   1. УНТРАХААС ӨМНӨ: сүүлийн 10 минутад хаалтны команд (машин орох/гарах)
#      явагдаагүй эсэхийг шалгана. Явагдсаар байвал 5 минутаар хойшлуулж
#      6 хүртэл удаа хүлээнэ; түүнээс удаан завгүй бол ЭНЭ ДОЛОО ХОНОГТ АЛГАСНА
#      (дараагийн Даваа дахин оролдоно) — үйлчилгээг ХЭЗЭЭ Ч таслахгүй.
#   2. Reboot-ийн өмнө хийсэн шийдвэрээ syslog-д (logger -t parking-reboot) бичнэ.
#   3. АССАНЫ ДАРАА: parking-boot-check.service нь backend health-ийг 3 минут
#      хүртэл хүлээж, үр дүнг syslog + /var/lib/parking/last_boot_check-д бичнэ.
#      Backend унасан хэвээр бол минут тутмын parking-watchdog өөрөө сэргээнэ.
#   Сүүлд асаасан цаг «Системийн эрүүл мэнд» хуудсанд харагдана.
#
# Лог харах: journalctl -t parking-reboot ; cat /var/lib/parking/last_boot_check
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "sudo-гоор ажиллуулна уу"; exit 1; }

if [ "${1:-}" = "remove" ]; then
    systemctl disable --now parking-weekly-reboot.timer 2>/dev/null || true
    systemctl disable parking-boot-check.service 2>/dev/null || true
    rm -f /etc/systemd/system/parking-weekly-reboot.{timer,service} \
          /etc/systemd/system/parking-boot-check.service \
          /usr/local/bin/parking-safe-reboot /usr/local/bin/parking-boot-check
    systemctl daemon-reload
    echo "Долоо хоногийн reboot болиулагдлаа."
    exit 0
fi

# ── 1. Аюулгүй reboot скрипт ─────────────────────────────────────────────────
cat > /usr/local/bin/parking-safe-reboot <<'EOF'
#!/bin/bash
# Зогсоол ЗАВГҮЙ үед reboot хийхгүй: сүүлийн 10 мин-д хаалтны команд байвал хүлээнэ.
DB_URL=$(grep -oP '(?<=PARKING_DATABASE_URL=).*' /root/PARKING/backend/.env 2>/dev/null | head -1)
busy() {
    # DB унших боломжгүй бол "завгүй биш" гэж үзнэ (DB асуудалтай үед ч reboot хийж болно)
    local n
    n=$(sudo -u postgres psql -d parking -tAc \
        "SELECT count(*) FROM barrier_commands WHERE created_at > now() at time zone 'utc' - interval '10 minutes'" 2>/dev/null) || return 1
    [ "${n:-0}" -gt 0 ]
}
for i in 1 2 3 4 5 6; do
    if ! busy; then
        logger -t parking-reboot "зогсоол чөлөөтэй — reboot хийж байна (оролдлого $i)"
        # Дараагийн boot дээр шалгах тэмдэг
        mkdir -p /var/lib/parking && date -u +"%Y-%m-%dT%H:%M:%SZ planned-reboot" > /var/lib/parking/last_reboot_reason
        systemctl reboot
        exit 0
    fi
    logger -t parking-reboot "сүүлийн 10 мин-д хаалтны хөдөлгөөн бий — 5 мин хойшлууллаа ($i/6)"
    sleep 300
done
logger -t parking-reboot "30 мин турш зогсоол завгүй байсан тул ЭНЭ УДААД АЛГАСЛАА (дараагийн хуваарьт дахин оролдоно)"
EOF
chmod +x /usr/local/bin/parking-safe-reboot

# ── 2. Ассаны дараах шалгалт ─────────────────────────────────────────────────
cat > /usr/local/bin/parking-boot-check <<'EOF'
#!/bin/bash
# Boot болмогц backend health-ийг 3 минут хүртэл хүлээж үр дүнг тэмдэглэнэ.
mkdir -p /var/lib/parking
for i in $(seq 1 36); do
    if curl -sf -m 3 http://127.0.0.1:8000/api/health >/dev/null 2>&1; then
        msg="OK: backend $((i*5))с дотор эрүүл боллоо ($(date -u +%FT%TZ))"
        logger -t parking-reboot "boot-check $msg"
        echo "$msg" > /var/lib/parking/last_boot_check
        exit 0
    fi
    sleep 5
done
msg="АНХААР: backend 3 минутад эрүүл болсонгүй ($(date -u +%FT%TZ)) — watchdog сэргээхийг хүлээнэ"
logger -t parking-reboot "boot-check $msg"
echo "$msg" > /var/lib/parking/last_boot_check
exit 0
EOF
chmod +x /usr/local/bin/parking-boot-check

# ── 3. systemd unit-ууд ──────────────────────────────────────────────────────
cat > /etc/systemd/system/parking-weekly-reboot.service <<'EOF'
[Unit]
Description=Easy Parking - долоо хоногийн аюулгүй reboot (зогсоол чөлөөтэй үед)

[Service]
Type=oneshot
ExecStart=/usr/local/bin/parking-safe-reboot
EOF

cat > /etc/systemd/system/parking-weekly-reboot.timer <<'EOF'
[Unit]
Description=Easy Parking - Даваа 04:30 (УБ, = Ням 20:30 UTC) долоо хоног тутам

[Timer]
OnCalendar=Sun 20:30 UTC
Persistent=false

[Install]
WantedBy=timers.target
EOF

cat > /etc/systemd/system/parking-boot-check.service <<'EOF'
[Unit]
Description=Easy Parking - boot-ийн дараах health шалгалт
After=network-online.target parking-backend.service
Wants=parking-backend.service

[Service]
Type=oneshot
ExecStart=/usr/local/bin/parking-boot-check

[Install]
WantedBy=multi-user.target
EOF

# Сервисүүд boot дээр өөрсдөө асдаг эсэхийг баталгаажуулна (reboot-ийн гол болзол)
systemctl enable parking-backend nginx postgresql 2>/dev/null || true
systemctl daemon-reload
systemctl enable --now parking-weekly-reboot.timer
systemctl enable parking-boot-check.service

echo "OK. Дараагийн reboot:"
systemctl list-timers parking-weekly-reboot.timer --no-pager | head -3
