# Deployment workflow (test → production)

## Орчнууд

| Орчин | Сервер | Хаяг | Зорилго |
|---|---|---|---|
| **TEST/DEV** | DigitalOcean droplet 152.42.235.199 | https://test.easy-parking.mn | Хөгжүүлэлт, туршилт, mock горим |
| **PRODUCTION** | Дотоод сервер 172.16.100.21 (VPN) | http://172.16.100.21 | Бодит зогсоолууд (40 хүртэл), бодит төлбөр |

Хоёулаа GitHub `Temuujinhub/PARKING` (main branch)-аас код авна.

## Алтан дүрэм

```
Хөгжүүлэлт/bug fix  →  TEST дээр турших  →  GitHub main-д push  →  PRODUCTION update
     (droplet)            (test.easy-parking.mn)                        (172.16.100.21)
```

**PRODUCTION дээр код гараар БҮҮ засаарай** — update.sh нь `git reset --hard` хийдэг тул
гар засвар устана. Бүх өөрчлөлт TEST дээр хийгдэж, push-оор production-д очно.

## Алхмууд

### 1. Хөгжүүлэлт (TEST/droplet дээр)
Bug засах, feature нэмэх → test.easy-parking.mn дээр турших → GitHub main-д push.

### 2. Production-д deploy хийх
VPN холбогдож, 172.16.100.21 руу SSH хийгээд:
```bash
sudo bash /root/PARKING/deploy/update.sh
```
Энэ нь автоматаар: **DB backup** → git pull → deps → frontend build → restart → health.
Схемийн шинэ багана автоматаар нэмэгдэнэ (`migrations.py` — `ADD COLUMN IF NOT EXISTS`).

### 3. Буцаах (rollback) — алдаа гарвал
update.sh backup үүсгэдэг (`/root/parking-backup-*.sql`). Асуудал гарвал:
```bash
git -C /root/PARKING reset --hard <өмнөх commit>   # код буцаах
sudo -u postgres psql parking < /root/parking-backup-YYYYMMDD-HHMMSS.sql   # DB буцаах
sudo systemctl restart parking-backend
```

## Production-д гараар хийх анхны тохиргоо (нэг удаа)

```bash
# 1. .env — бодит үйлчилгээ асаах
sudo nano /root/PARKING/backend/.env
#   PARKING_QPAY_MOCK=false + QPay credentials
#   PARKING_QPAY_WEBHOOK_SECRET=<санамсаргүй урт мөр>
#   PARKING_EBARIMT_MOCK=false + PosAPI
#   PARKING_BARRIER_MOCK=false (хэрэв сервер хаалт удирддаг бол)
#   PARKING_PUBLIC_BASE_URL=http://172.16.100.21  (эсвэл домэйн)
sudo systemctl restart parking-backend

# 2. Нууц үг солих (заавал!)
#   Системд temuujin-ээр нэвтэрч Хэрэглэгчид хуудаснаас бүх default нууц үг солих

# 3. DNS байнгын (аль хэдийн хийсэн бол алгас)
printf "[Resolve]\nDNS=8.8.8.8 1.1.1.1\n" | sudo tee /etc/systemd/resolved.conf.d/dns.conf
sudo systemctl restart systemd-resolved
```

## 40 зогсоол холбох масштаб

Нэг сервер (172.16.100.21) 40 зогсоолыг бүгдийг зохицуулна. Зогсоол бүрийг
**Тохиргоо → Зогсоол нэмэх** wizard-аар үүсгэж, төхөөрөмжийг нь холбоно. Ачаалал
ихсэвэл (docs/ARCHITECTURE_SECURITY.md §3): Redis + олон worker → тусдаа сервер.
