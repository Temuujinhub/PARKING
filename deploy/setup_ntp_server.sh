#!/bin/bash
# Продакшн серверийг камеруудын NTP (цагийн) сервер болгоно.
#
#   sudo bash /root/PARKING/deploy/setup_ntp_server.sh          # суулгах
#   sudo bash /root/PARKING/deploy/setup_ntp_server.sh status   # төлөв харах
#   sudo bash /root/PARKING/deploy/setup_ntp_server.sh remove   # болиулах
#
# ЯАГААД ХЭРЭГТЭЙ ВЭ: Dahua камерууд тусгаарлагдсан LAN (10.0.x, 192.168.6.x)
# дээр байдаг тул интернэтийн pool.ntp.org-д ХҮРДЭГГҮЙ. Цаг нь тааруулах эх
# сурвалжгүй болж гулсдаг (2026-08-17: ялалт/Эрэл-13/Хангарьд -7 минут). Камерын
# цаг зөрвөл camera_sync нөхөлт хийхдээ зогсолтын орсон/гарсан цагийг БУРУУ
# бичиж, төлбөрийн хугацаа гажина.
#
# Энэ скрипт chrony-г тохируулж, ЭНЭ СЕРВЕРИЙГ дотоод сүлжээнд NTP сервер болгоно:
#   • сервер өөрөө дээд эх сурвалж (интернэт байвал pool, эс бол өөрийн цаг) -тай
#     синк болно;
#   • камерын дэд сүлжээнүүдэд (RFC1918: 10/8, 172.16/12, 192.168/16) NTP өгнө;
#   • интернэтгүй байсан ч `local stratum 10`-аар өөрийн цагийг үйлчилнэ.
# Дараа нь: venv/bin/python tools/camera_ntp_config.py --apply
# (камеруудыг энэ сервер рүү заана).
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "sudo-гоор ажиллуулна уу"; exit 1; }

CONF=/etc/chrony/chrony.conf
DROPIN=/etc/chrony/conf.d/parking-ntp-server.conf

status() {
    echo "── chrony төлөв ──"
    systemctl is-active chrony 2>/dev/null || systemctl is-active chronyd 2>/dev/null || echo "идэвхгүй"
    echo "── үйлчлүүлэгчид (камерууд) ──"
    chronyc clients 2>/dev/null | head -30 || echo "chronyc clients уншсангүй"
    echo "── эх сурвалж ──"
    chronyc sources 2>/dev/null | head -10 || true
    echo "── 123/udp сонсож байгаа эсэх ──"
    ss -ulnp 2>/dev/null | grep ':123' || echo "123/udp сонсохгүй байна (⚠)"
}

if [ "${1:-}" = "status" ]; then status; exit 0; fi

SVC=chrony
if [ "${1:-}" = "remove" ]; then
    rm -f "$DROPIN"
    # firewall дүрмийг арилгах (байвал)
    if command -v ufw >/dev/null 2>&1; then ufw delete allow 123/udp >/dev/null 2>&1 || true; fi
    systemctl restart chrony 2>/dev/null || systemctl restart chronyd 2>/dev/null || true
    echo "Устгав. Камерууд NTP авахаа болино (тэдгээрийн NTP тохиргоо хэвээр)."
    exit 0
fi

# 1. chrony суулгах (байхгүй бол)
if ! command -v chronyd >/dev/null 2>&1; then
    echo "chrony суулгаж байна…"
    apt-get update -qq && apt-get install -y -qq chrony
fi

# 2. Камерын дэд сүлжээнүүдийг DB-ээс тодорхойлно (idempotent — дахин ажиллаж болно).
#    Камер бүрийн IP-ийн /24-г allow болгоно (RFC1918 л зөвшөөрнө — гадныг биш).
SUBNETS=$(sudo -u postgres psql -tA -d parking -c \
  "SELECT DISTINCT host(ip_address::inet) FROM devices \
   WHERE device_type='camera' AND ip_address<>'' AND status='active'" 2>/dev/null \
  | awk -F. 'NF==4{print $1"."$2"."$3".0/24"}' | sort -u || true)

# DB уншигдаагүй бол RFC1918-ийн стандарт мужуудыг бүхэлд нь зөвшөөрнө
if [ -z "$SUBNETS" ]; then
    SUBNETS=$'10.0.0.0/8\n172.16.0.0/12\n192.168.0.0/16'
    echo "⚠ DB-ээс камерын дэд сүлжээ уншсангүй — RFC1918 бүхэлд нь зөвшөөрнө."
fi

# 3. drop-in тохиргоо бичнэ (үндсэн chrony.conf-д ХҮРЭХГҮЙ)
mkdir -p "$(dirname "$DROPIN")"
{
    echo "# Parking — камеруудад NTP өгөх (setup_ntp_server.sh үүсгэв)"
    while read -r net; do [ -n "$net" ] && echo "allow $net"; done <<< "$SUBNETS"
    # Дээд эх сурвалжгүй (интернэтгүй) байсан ч өөрийн цагийг үйлчилнэ
    echo "local stratum 10"
} > "$DROPIN"

# 4. conf.d-г үндсэн conf уншдаг эсэхийг батална (зарим дистрод байхгүй)
if [ -f "$CONF" ] && ! grep -q "conf.d" "$CONF" 2>/dev/null; then
    echo "confdir /etc/chrony/conf.d" >> "$CONF"
fi

# 5. firewall: 123/udp нээх (ufw идэвхтэй бол)
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
    for net in $SUBNETS; do ufw allow from "$net" to any port 123 proto udp >/dev/null 2>&1 || true; done
    echo "ufw: 123/udp камерын сүлжээнд нээв."
fi

# 6. сервис нэрийг тодорхойлоод restart
systemctl enable chrony >/dev/null 2>&1 || systemctl enable chronyd >/dev/null 2>&1 || true
systemctl restart chrony 2>/dev/null || { SVC=chronyd; systemctl restart chronyd; }

echo
echo "✅ NTP сервер асаалттай. Зөвшөөрсөн дэд сүлжээ:"
echo "$SUBNETS" | sed 's/^/     /'
echo
echo "Дараагийн алхам — камеруудыг энэ сервер рүү заах:"
echo "     cd /root/PARKING/backend"
echo "     venv/bin/python tools/camera_ntp_config.py            # харах"
echo "     venv/bin/python tools/camera_ntp_config.py --apply    # заах"
echo
echo "Төлөв шалгах:  sudo bash $0 status"
