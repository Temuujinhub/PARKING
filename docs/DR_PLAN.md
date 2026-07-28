# Гэнэтийн ослын төлөвлөгөө (DR) — production унавал test серверээс түр ажиллуулах

Хоёр сервер: **production** 172.16.100.21 (site/app.easy-parking.mn, NAT 202.21.117.179)
ба **test** 152.42.235.199 (test.easy-parking.mn, DigitalOcean).

## Байнгын бэлтгэл (нэг удаа тохируулаад мартана)

1. **DB backup цаг тутам test рүү** — production дээр:
   ```
   sudo ssh-keygen -t ed25519 -N "" -f /root/.ssh/id_ed25519_backup
   sudo cat /root/.ssh/id_ed25519_backup.pub    # ← test-ийн /root/.ssh/authorized_keys-д нэмнэ
   sudo bash /root/PARKING/deploy/backup_ship.sh   # гар туршилт
   echo '15 * * * * root bash /root/PARKING/deploy/backup_ship.sh >/dev/null 2>&1' | sudo tee /etc/cron.d/parking-backup-ship
   ```
   Backup test-ийн `/root/prod-backups/`-д хуримтлагдана (30 хоногоос хуучныг test
   өөрөө цэвэрлэнэ). Алдагдах дата: хамгийн ихдээ сүүлийн 1 цаг.
   АНХААР: энэ нь prod→test чиглэлд SSH (порт 22) гарах урсгал нээлттэй байхыг
   шаардана — хаалттай бол байгууллагын IT-ээс «172.16.100.21 → 152.42.235.199:22»
   нээлгэнэ.

2. **Код** — үргэлж GitHub + test сервер дээр адилхан байдаг (bundle-ууд эндээс
   гардаг тул test нь кодоор ямагт шинэ).

## Осол болоход (production унасан) — сэргээх дараалал (~15 минут)

1. **Test дээр сүүлийн backup-ыг сэргээнэ:**
   ```
   sudo bash /root/PARKING/tools/restore_backup.sh
   ```
2. **DNS шилжүүлнэ:** `site.easy-parking.mn` ба `app.easy-parking.mn` A бичлэгийг
   202.21.117.179 → **152.42.235.199** болгоно (DNS удирдлагын самбараас; TTL-ээс
   хамаарч 5-30 мин). Түр зуур шууд https://test.easy-parking.mn -ээр ч ажиллаж болно.
3. **Test-ийн .env шалгах:** `PARKING_BARRIER_MOCK=true` хэвээр байх ёстой (клоудаас
   камерт хүрэхгүй). QPay бодит түлхүүрүүд test-ийн .env-д бөглөгдсөн эсэхийг шалгаж,
   шаардлагатай бол production-ы .env-ээс хуулна (backup дотор .env ОРДОГГҮЙ!).
4. **Ажиллах горим:** касс, тайлан, төлбөр (QR), түүх бүрэн ажиллана. **Хаалт/камер
   ажиллахгүй** — зогсоолын хаалтуудыг гараар нээлттэй горимд тавьж, оператор
   бүртгэлийг гараар хөтөлнө (эсвэл түр үнэгүй нэвтрүүлнэ).
5. Production сэргэмэгц: тэнд сүүлийн datаг буцааж хэрэгтэй бол test-ээс pg_dump
   хийж зөөнө, DNS-ээ буцаана.

## Тогтмол шалгалт (сард нэг)

- Test дээр: `ls -lt /root/prod-backups | head -3` — цаг тутмын файл ирж байгаа юу?
- `sudo bash /root/PARKING/tools/restore_backup.sh` — жинхэнэ сэргээлт хийж
  тайлан нээгдэж буйг нүдээр батлах (test дата parking_old-д хадгалагдана).
- Production дээр: `journalctl -t parking-backup -n 5` — илгээлтийн лог.

## PITR — цэг хүртэл сэргээх (2026-07-29 нэмэгдсэн)

pg_dump нь өдөрт 2 удаа тул хамгийн муу тохиолдолд 12 цагийн дата алдагдана.
WAL архивлалт үүнийг ЯМАР Ч АГШИН хүртэл сэргээх боломжтой болгоно (жишээ:
«буруу устгал хийхээс 1 минутын өмнө»).

Тохируулах (production дээр нэг удаа; postgres ~3 секунд restart хийнэ):
```
sudo bash /root/PARKING/deploy/setup_pitr.sh
sudo bash /root/PARKING/deploy/setup_pitr.sh --status   # хэдийд ч байдлыг харах
```
Юу болох вэ: WAL → `/var/lib/parking/wal-archive`, 7 хоног тутам basebackup →
`/var/lib/parking/basebackup/<огноо>`, 14 хоногоос хуучныг cron цэвэрлэнэ.
Диск: энэ ачаалалд 14 хоногт ~1-2GB.

**Сэргээх (тодорхой агшин хүртэл):**
```
sudo systemctl stop parking-backend postgresql
sudo mv /var/lib/postgresql/16/main /var/lib/postgresql/16/main.old
sudo -u postgres mkdir -p /var/lib/postgresql/16/main
# Сүүлийн basebackup-ыг задлана
sudo -u postgres tar -xzf /var/lib/parking/basebackup/<огноо>/base.tar.gz -C /var/lib/postgresql/16/main
sudo -u postgres tee /var/lib/postgresql/16/main/postgresql.auto.conf >/dev/null <<CONF
restore_command = 'cp /var/lib/parking/wal-archive/%f %p'
recovery_target_time = '2026-07-29 14:32:00'   # ← хүссэн агшин (серверийн цагаар)
recovery_target_action = 'promote'
CONF
sudo -u postgres touch /var/lib/postgresql/16/main/recovery.signal
sudo systemctl start postgresql && sudo systemctl start parking-backend
```
Буцаах бол `main.old`-ыг эргүүлж нэрлэнэ. **Заавал урьдчилж туршина** — жинхэнэ
осол болоход анх удаагаа хийх нь эрсдэлтэй (test сервер дээр туршилт хийсэн).

## Хуучин датаны цэвэрлэгээ (retention)

Backend өдөрт нэг удаа автоматаар: lpr_events 90ө, barrier_commands 180ө,
audit_logs 365ө, snapshot зураг 120ө-оос хуучныг устгана (.env: PARKING_RETENTION_*,
0 = унтраах). **Санхүүгийн датад (session/payment/vat_receipt/compensation)
ХЭЗЭЭ Ч хүрэхгүй** — хуулийн шаардлагаар мөнхөд хадгална.
