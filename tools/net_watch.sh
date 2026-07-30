#!/usr/bin/env bash
# Манай сегмент (172.16.100.0/24) дээрх ШИНЭ/АЛГА болсон хостыг цаг тэмдэглэлтэй
# бүртгэнэ — «яг тэр минутад ямар төхөөрөмж асав/унтав» гэдгийг ХОЙШОО нотлоход.
#
#   sudo nohup bash /root/PARKING/tools/net_watch.sh > /dev/null 2>&1 &
#   cat /var/log/parking-net-watch.log        # түүхийг харах
#   pkill -f net_watch.sh                     # зогсоох
#
# Юу хийдэг (default 60с тутам, ПАССИВ — зөвхөн өөрийн сегмент):
#   • ARP/neighbour хүснэгтийг уншиж (broadcast ping-ээр сэрээж) хостуудыг тоолно
#   • Шинэ IP гарвал:  «+ 172.16.100.20 (MAC aa:bb:...)»
#   • Алга болвол:     «- 172.16.100.20»
#   • VLAN-уудын хүрэлцээ (gateway + камерын сүлжээ) өөрчлөгдвөл тэмдэглэнэ
# Бусад VLAN-ийг СКАН ХИЙХГҮЙ (админын зөвшөөрөл шаардана) — зөвхөн ping.
set -uo pipefail

IFACE="${IFACE:-ens34}"
NET="${NET:-172.16.100}"
OUT="/var/log/parking-net-watch.log"
INTERVAL="${INTERVAL:-60}"
# Хүрэлцээг шалгах чиглэлүүд (VLAN тус бүрээс нэг хаяг)
PROBES=("172.16.100.1" "192.168.6.10" "10.0.101.10" "10.0.113.10")

prev_hosts=""
prev_reach=""
echo "$(date '+%F %T') === net_watch эхэллээ (сегмент $NET.0/24, $INTERVAL секунд тутам) ===" >> "$OUT"

while true; do
  # ARP хүснэгтийг сэрээх — сегментийн хостуудыг илрүүлэхэд (хөнгөн, зэрэгцээ)
  for i in $(seq 1 254); do
    (ping -c 1 -W 1 "$NET.$i" >/dev/null 2>&1) &
  done
  wait 2>/dev/null

  hosts=$(ip neigh show dev "$IFACE" 2>/dev/null \
          | awk '$1 ~ /^'"$NET"'\./ && $0 !~ /FAILED|INCOMPLETE/ {print $1" "$5}' | sort)
  cur_ips=$(echo "$hosts" | awk '{print $1}')

  if [ -n "$prev_hosts" ]; then
    prev_ips=$(echo "$prev_hosts" | awk '{print $1}')
    # Шинээр гарсан
    comm -13 <(echo "$prev_ips") <(echo "$cur_ips") | while read -r ip; do
      [ -n "$ip" ] || continue
      mac=$(echo "$hosts" | awk -v i="$ip" '$1==i{print $2}')
      echo "$(date '+%F %T') + ШИНЭ ХОСТ  $ip  (MAC ${mac:-?})" >> "$OUT"
      logger -t parking-net "ШИНЭ ХОСТ сегментэд: $ip (${mac:-?})"
    done
    # Алга болсон
    comm -23 <(echo "$prev_ips") <(echo "$cur_ips") | while read -r ip; do
      [ -n "$ip" ] || continue
      echo "$(date '+%F %T') - АЛГА БОЛОВ  $ip" >> "$OUT"
    done
  else
    echo "$(date '+%F %T') Эхний тоолол: $(echo "$cur_ips" | grep -c .) хост" >> "$OUT"
    echo "$hosts" | sed "s/^/$(date '+%F %T')   · /" >> "$OUT"
  fi
  prev_hosts="$hosts"

  # VLAN-уудын хүрэлцээ өөрчлөгдсөн эсэх (route унах/сэргэхийг цагтай нь барина)
  reach=""
  for p in "${PROBES[@]}"; do
    if ping -c 1 -W 1 "$p" >/dev/null 2>&1; then reach="$reach $p:OK"; else reach="$reach $p:ХҮРЭХГҮЙ"; fi
  done
  if [ "$reach" != "$prev_reach" ] && [ -n "$prev_reach" ]; then
    echo "$(date '+%F %T') ! ХҮРЭЛЦЭЭ ӨӨРЧЛӨГДЛӨӨ:$reach" >> "$OUT"
    logger -t parking-net "VLAN хүрэлцээ өөрчлөгдлөө:$reach"
  fi
  prev_reach="$reach"

  sleep "$INTERVAL"
done
