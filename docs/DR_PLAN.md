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
