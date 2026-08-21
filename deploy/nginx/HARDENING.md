# nginx аюулгүй байдлын хатууруулалт — прод серверт хэрэгжүүлэх дараалал

2026-08-20-ны OWASP багцын **кодын** хэсэг (LPR IP хуурах нүх, IDOR-ууд,
`/admin/devices` хаалт, эрх ахиулах зам, dependency pin) `55d273e` commit-оор
main-д орж, autodeploy-аар прод серверүүдэд аль хэдийн хүрсэн. **Дутуу үлдсэн нь
зөвхөн nginx/сервер түвшний тохиргоо** — тэр үед «гараар, сервер бүрд» гэж
тэмдэглэгдсэн боловч тохиргооны файлууд repo-д commit хийгдээгүй тул зөвхөн TEST
серверийн дискэн дээр үлдсэн байв. Энэ хавтас түүнийг засав.

Файлууд (autodeploy-аар сервер бүрд аль хэдийн байгаа):

| Файл | Хаана тавих |
|---|---|
| `parking-security.conf` | `/etc/nginx/snippets/` |
| `parking-ratelimit.conf` | `/etc/nginx/conf.d/` |
| `apply_security_headers.py` | ажиллуулах (файл хуулахгүй) |

---

## ⚠ Үйлчилгээнд нөлөөлөх эсэх — үнэн байдал

| Үе шат | Тасалдал | Тайлбар |
|---|---|---|
| 1. Header + `server_tokens` | **Байхгүй** | `reload` = graceful. Хуучин worker идэвхтэй холболтоо (WebSocket орсон) дуустал үргэлжлүүлнэ, шинэ worker шинэ хүсэлт авна. `update.sh` өөрөө deploy бүрд `reload nginx` хийдэг — батлагдсан. Backend-д ОГТ хүрэхгүй → хаалт, төлбөр, камер бүгд хэвийн |
| 2. Сканнерын шуугиан (444) | **Байхгүй** | `.php/.env` замууд — бодит хэрэглэгч хэзээ ч ханддаггүй |
| 3. Rate limit | **ЭРСДЭЛТЭЙ** | Прод дээр олон оператор НЭГ NAT гарцаар гарвал бүгд нэг IP болж тоологдоно → 429. Заавал ХЭМЖИЖ баталгаажуулна (доор) |
| 4. LPR хаах (`deny all`) | **ӨНДӨР ЭРСДЭЛТЭЙ** | Хэрэв прод дээр камер ҮНЭХЭЭР HTTP push хийдэг бол хаалт нээгдэхээ болино. Заавал ХЭМЖИНЭ |
| 5. UFW галт хана | **ӨНДӨР ЭРСДЭЛТЭЙ** | Дараалал буруу бол SSH тасарч сервер рүү орох аргагүй болно |

**Дүгнэлт:** 1-2 дугаар үе шатыг ажлын цагт ч аюулгүй хийж болно.
3-5 дугаарыг хэмжилтгүйгээр БҮҮ хий.

---

## Үе шат 1 — Хамгаалалтын header (эрсдэлгүй, ЭНЭ ҮЕ ШАТААР ЭХЛЭ)

Прод сервер бүр дээр (`site.easy-parking.mn` ба Monnis `172.16.100.21`):

```bash
sudo bash /root/PARKING/deploy/nginx/apply_headers.sh
```

Скрипт нь: snippet суулгах → vhost-уудыг нөөцлөх → `include`-ийг location бүрд
тарааж байрлуулах → `nginx -t` → амжилттай бол `reload`, **алдаатай бол нөөцөөс
автоматаар буцаана**. Дахин дахин ажиллуулж болно (idempotent).

Шалгах:

```bash
curl -sI https://site.easy-parking.mn/assets/ | grep -iE 'x-frame|nosniff|strict-transport|^server:'
```

Буцаах (шаардвал):

```bash
ls -t /etc/nginx/sites-enabled/*.backup-* | head -1 | xargs -I{} sh -c 'cp {} "${0%.backup-*}"' {} && sudo nginx -t && sudo systemctl reload nginx
```

---

## 🚑 Онцгой байдал: `nginx -t` унасан бол

**Эхлээд мэд: `reload` нь ЗӨВХӨН `nginx -t` амжилттай болсны дараа ажилладаг.**
Тиймээс тест унасан бол ажиллаж буй nginx санах ойд байгаа СҮҮЛИЙН САЙН
тохиргоогоороо үйлчилсээр байна — сайт унахгүй. Гэхдээ `restart`/reboot хийвэл
босохгүй тул яаралтай засна.

### 1. Хамгийн түгээмэл: нөөц файл nginx-ийн ачаалах хавтсанд үлдсэн

`include /etc/nginx/sites-enabled/*;` нь **өргөтгөлөөр шүүдэггүй** — тэнд үлдсэн
`*.backup-*` файлыг ч nginx ачаалж, `server` блокууд давхардана.

```bash
sudo mkdir -p /var/backups/parking-nginx
sudo mv /etc/nginx/sites-enabled/*.backup-* /var/backups/parking-nginx/ 2>/dev/null
sudo mv /etc/nginx/conf.d/*.backup-*        /var/backups/parking-nginx/ 2>/dev/null
sudo nginx -t
```

### 2. `"server_tokens" directive is duplicate`

`/etc/nginx/nginx.conf`-ийн http блокт аль хэдийн байхад vhost дотор дахин
тунхаглагдсан. vhost доторхыг нь устгана (http түвшнийх бүх server-т үйлчилнэ):

```bash
sudo sed -i '/^server_tokens/d' /etc/nginx/sites-enabled/parking && sudo nginx -t
```

### 3. Гараар бүрэн буцаах

```bash
ls -t /var/backups/parking-nginx/parking.backup-* | head -1
sudo cp /var/backups/parking-nginx/parking.backup-<ЦАГ> /etc/nginx/sites-enabled/parking
sudo nginx -t && sudo systemctl reload nginx
```

### 4. Үйлчилгээ амьд эсэхийг батлах

```bash
curl -s http://127.0.0.1/api/health; systemctl is-active nginx parking-backend
```

---

## `server_tokens off` (нэмэлт, гараар)

Скрипт үүнийг ОРУУЛАХГҮЙ — http түвшний тунхагтай давхцаж `nginx -t`-г
унагаадаг. `/etc/nginx/nginx.conf`-ийн http блокт нэг удаа нээнэ:

Эхлээд мөр байгаа эсэхийг хар — Debian-ы зарим суулгацад тэр мөр огт байдаггүй
тул `sed` чимээгүй юу ч хийхгүй өнгөрдөг (nginx -t өнгөрч, хувилбар ил хэвээр):

```bash
grep -n server_tokens /etc/nginx/nginx.conf || echo "МӨР АЛГА — доорхыг ажиллуул"
```

Тайлбар болсон мөр байвал нээнэ; огт байхгүй бол `http {` блокт шинээр нэмнэ:

```bash
sudo sed -i 's/^\s*#\s*server_tokens off;/\tserver_tokens off;/' /etc/nginx/nginx.conf
grep -q '^\s*server_tokens' /etc/nginx/nginx.conf \
  || sudo sed -i '0,/^http {/s//http {\n\tserver_tokens off;/' /etc/nginx/nginx.conf
sudo nginx -t && sudo systemctl reload nginx
```

Батлах (хувилбаргүй `Server: nginx` байх ёстой):

```bash
curl -sI http://127.0.0.1/ | grep -i '^server:'
```

---

### HSTS дотоод HTTP хаяг дээр аюулгүй юу?

Тийм. Snippet-д HSTS байгаа ч **энгийн HTTP-ээр ирсэн HSTS-ийг браузер зарчмын
хувьд үл тоомсорлодог** (RFC 6797). Мөн IP хаягт (172.16.100.21) HSTS огт
хамаарахгүй. Тиймээс дотоод админ UI хүчээр HTTPS рүү шилжихгүй.

## Үе шат 2 — Сканнерын шуугианг таслах (эрсдэл бага)

SPA-гийн `try_files` улмаас `/xxx.php` → `index.html` болж 200 буцдаг тул
ботуудад «амьд» мэт харагдаж, өдөрт хэдэн зуун хүсэлт татдаг. Дүрэм нь
`snippets/parking-scanner-guard.conf`-д байгаа — **гараар засах шаардлагагүй**:

```bash
sudo bash /root/PARKING/deploy/nginx/apply_headers.sh --scanner
```

---

## Үе шат 3 — Rate limit (ЭХЛЭЭД ХЭМЖИНЭ)

Нэг IP-ээс хэдэн хүсэлт ирдгийг бодит логоос:

```bash
sudo awk '{print $1}' /var/log/nginx/access.log | sort | uniq -c | sort -rn | head -20
```

⚠ Энэ нь логийн бүх хугацааны НИЙТ тоо, минутын хурд БИШ. Хамгийн дээд IP нь
дотоод gateway (ж: `172.16.100.1`) бол **бүх оператор нэг IP-ээр гарч байна** —
тэр тохиолдолд ерөнхий `/api/`-д хязгаар тавивал бодит ажилтан 429 иднэ.

Аюулгүй хувилбар — зөвхөн login (бодит урсгал: 2 хүсэлт/ХОНОГ, хязгаар 10/мин):

```bash
sudo cp /root/PARKING/deploy/nginx/parking-ratelimit.conf /etc/nginx/conf.d/
sudo nginx -t && sudo systemctl reload nginx
```

Файлыг хуулах нь ЗӨВХӨН бүсийг тодорхойлно — `limit_req` бичих хүртэл юунд ч
нөлөөлөхгүй. Login дээр идэвхжүүлэхийн тулд vhost-ийн
`location = /api/auth/login` дотор нэг мөр нэмнэ:

> ⚠ **ЭНЭ БОЛ ФАЙЛД БИЧИХ АГУУЛГА, ТЕРМИНАЛЫН КОМАНД БИШ.**
> `sudo nano /etc/nginx/sites-enabled/parking`

```nginx
limit_req zone=parking_login burst=5 nodelay;
```

7 хоног ажиглаад бодит оператор цохигдоогүй бол л `/api/`-г авч үзнэ:

```bash
grep 'limiting requests' /var/log/nginx/error.log
```

---

## Үе шат 4 — LPR callback хаах (ЭХЛЭЭД ХЭМЖИНЭ)

Камер HTTP push **үнэхээр хийдэг эсэхийг** эхлээд тогтооно:

```bash
sudo journalctl -u parking-backend --since '-7 days' | grep -oE 'lpr_push: ip=[0-9.]+' | sort | uniq -c | sort -rn
```

Хоосон гарвал логийн хугацаа үнэхээр хамарсан эсэхийг ч батал (лог эргэлдсэн
бол хэмжилт хүчингүй):

```bash
sudo journalctl -u parking-backend --since '-7 days' | head -1
```

* **Хоосон бол** (event нь comet сувгаар ирдэг) хаахад аюулгүй:

```bash
sudo bash /root/PARKING/deploy/nginx/apply_headers.sh --lpr
```

* **IP гарч ирвэл** эхлээд `snippets/parking-lpr-guard.conf`-д тэр дэд сүлжээг
  `allow`-оор нэмээд дараа нь ажиллуул. Скрипт нь гараар засагдсан snippet-ийг
  дарж бичихгүй.

---

## Үе шат 5 — UFW галт хана (SSH тасрах эрсдэлтэй)

**Дараалал чухал** — 22-ыг нээхээс өмнө `enable` хийвэл сервер рүү орох аргагүй
болно. Боломжтой бол консолын хандалт (VPS panel / IPMI) гар дор байхад хий.

```bash
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw status numbered          # 22 БАЙГААГ нүдээрээ бат
sudo ufw --force enable
```

Даатгал: асаахын өмнө өөр терминалд `sleep 600 && ufw disable` ажиллуулж
үлдээвэл, буруу зүйл болбол 10 минутын дараа өөрөө буцна:

```bash
sudo sh -c 'nohup sh -c "sleep 600 && ufw disable" >/dev/null 2>&1 &'
```

Бүх зүйл хэвийн бол тэр даатгалыг цуцлана: `sudo pkill -f "sleep 600"`.
