# Шинэ сервер рүү шилжүүлэх заавар (172.16.100.21 г.м)

## Урьдчилсан нөхцөл
Target сервер (172.16.100.21) руу хүрэхийн тулд эхлээд **FortiClient VPN**-ээр
байгууллагын сүлжээнд холбогдсон байх ёстой (Remote Access таб → 202.21.117.178).
VPN холбогдсоны дараа л дараах алхмууд ажиллана.

## Арга А — Target сервер GitHub-аас шууд татах (хамгийн хялбар)

Target сервер интернэт гаралттай бол, түүн дээр SSH-ээр орж:

```bash
# 1. install script татаж ажиллуулах (бүх зүйлийг автоматаар хийнэ)
curl -fsSL https://raw.githubusercontent.com/Temuujinhub/PARKING/main/deploy/install.sh -o install.sh
sudo bash install.sh 172.16.100.21
```

Дуусахад http://172.16.100.21 дээр систем ажиллана.

## Арга Б — Одоогийн өгөгдлийг хамт зөөх (демо/тест дата авчрах бол)

**Одоогийн сервер дээр (152.42.235.199):**
```bash
sudo -u postgres pg_dump parking > /root/PARKING/deploy/migration.sql
```
Энэ файлыг git-д commit хийх ЭСВЭЛ scp-ээр target руу зөөнө.
`deploy/migration.sql` байгаа бол install.sh автоматаар seed-ийн оронд үүнийг сэргээнэ.

⚠️ migration.sql-д бодит өгөгдөл ордог тул нийтийн GitHub-д бүү commit хий —
scp/USB/хувийн сувгаар зөө.

## Арга В — Target интернэтгүй бол (offline)

Хөгжүүлэгчийн машинаас (VPN холбогдсон) файлыг зөөнө:
```bash
# repo архив + venv-гүй бүх код
git -C /root/PARKING archive --format=tar.gz -o /tmp/parking.tar.gz HEAD
scp /tmp/parking.tar.gz root@172.16.100.21:/root/
# target дээр задалж install.sh ажиллуулна
```

## VPN дээр систем ажиллуулах онцлог

- `PARKING_PUBLIC_BASE_URL` = `http://172.16.100.21` (дотоод IP) — QR код энэ хаягаар
  үүснэ. Гэхдээ жолоочийн утас VPN дээр байхгүй тул **дотоод IP-тэй QR утсаар нээгдэхгүй**.
  Иймд production-д домэйн + HTTPS хэрэгтэй (эсвэл байгууллага public IP forward хийнэ).
- Камерын LPR callback мөн адил: камер 172.16.100.21 руу зөвхөн ижил дотоод сүлжээнээс
  хүрнэ — энэ нь бүр ч зохимжтой (камер тэдний сүлжээнд байх учиртай).
