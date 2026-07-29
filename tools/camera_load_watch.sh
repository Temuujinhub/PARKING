#!/usr/bin/env bash
# Камерын ачааллыг ЗӨӨЛӨН, тасралтгүй хэмжинэ — «хэзээ, аль камер хариу өгөхөө
# больдог вэ» гэдгийг цагаар нь баримтжуулна.
#
#   sudo nohup bash /root/PARKING/tools/camera_load_watch.sh > /dev/null 2>&1 &
#   tail -f /var/log/parking-camera-load.csv        # ажиглах
#   pkill -f camera_load_watch                       # зогсоох
#
# Юу хэмжих (30 секунд тутам, камерт МАШ БАГА ачаалалтай):
#   • ping 3 багц → алдагдлын хувь, дундаж RTT (ICMP — камерын CPU ханасныг заана)
#   • ганц TCP холболт → веб серверийн хариу өгөх хугацаа (SYN дараалал дүүрсэн эсэх)
# Гаралт: CSV — цаг, IP, алдагдал%, RTT, TCP секунд. Дараа нь хаалтны алдаатай
# тулгаж «камер яг тэр агшинд хариугүй байсан» гэдгийг батална (доор жишээ).
#
# ЖИЧ: энэ нь оношилгооны ТҮР зуурын хэрэгсэл — хэдэн цаг/өдөр ажиллуулаад
# зогсооно. Байнгын хяналт нь Системийн эрүүл мэнд хуудсанд бий.
set -uo pipefail

IPS=("${@:-192.168.6.10 192.168.6.11}")
[ $# -gt 0 ] && IPS=("$@") || IPS=(192.168.6.10 192.168.6.11)
OUT="/var/log/parking-camera-load.csv"
INTERVAL="${INTERVAL:-30}"

[ -f "$OUT" ] || echo "time,ip,loss_pct,rtt_avg_ms,tcp_sec" > "$OUT"

while true; do
  for ip in "${IPS[@]}"; do
    p=$(ping -c 3 -i 0.3 -W 1 -q "$ip" 2>/dev/null)
    loss=$(echo "$p" | grep -oP '\d+(?=% packet loss)' || echo "100")
    rtt=$(echo "$p" | grep -oP 'rtt [^=]+= \K[0-9.]+/\K[0-9.]+' | head -1)
    [ -z "$rtt" ] && rtt=$(echo "$p" | awk -F'/' '/^rtt/ {print $5}')
    [ -z "$rtt" ] && rtt=""
    t=$( { TIMEFORMAT=%R; time timeout 4 bash -c "echo > /dev/tcp/$ip/80" 2>/dev/null; } 2>&1 )
    echo "$(date '+%Y-%m-%d %H:%M:%S'),$ip,$loss,$rtt,$t" >> "$OUT"
  done
  sleep "$INTERVAL"
done
