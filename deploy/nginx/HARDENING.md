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
curl -sI http://127.0.0.1/api/health && systemctl is-active nginx parking-backend
```

---

## `server_tokens off` (нэмэлт, гараар)

Скрипт үүнийг ОРУУЛАХГҮЙ — http түвшний тунхагтай давхцаж `nginx -t`-г
унагаадаг. `/etc/nginx/nginx.conf`-ийн http блокт нэг удаа нээнэ:

```bash
sudo sed -i 's/^\s*#\s*server_tokens off;/\tserver_tokens off;/' /etc/nginx/nginx.conf && sudo nginx -t && sudo systemctl reload nginx
```

---

## Үе шат 2 — Сканнерын шуугианг таслах (эрсдэл бага)

vhost-ийн `server { }` дотор гараар нэмнэ (SPA-гийн `try_files` улмаас
`/xxx.php` → `index.html` 200 буцаж ботуудад «амьд» мэт харагддаг):

```nginx
location ~* \.(php|asp|aspx|jsp|cgi)$ { access_log off; return 444; }
location ~  /\.(env|git|svn|aws)      { access_log off; return 444; }
```

Дараа нь `sudo nginx -t && sudo systemctl reload nginx`.

---

## Үе шат 3 — Rate limit (ЭХЛЭЭД ХЭМЖИНЭ)

Нэг IP-ээс минутад хэдэн хүсэлт ирдгийг бодит логоос:

```bash
sudo awk '{print $1}' /var/log/nginx/access.log | sort | uniq -c | sort -rn | head -20
```

Хамгийн идэвхтэй IP нь `600r/m`-ээс доогуур байвал л үргэлжлүүл. NAT ард олон
оператор байвал энэ тоо асар өндөр гарна — тэр тохиолдолд зөвхөн login-ы
хязгаарыг (`parking_login`, 10r/m) хэрэглэ, ерөнхий `/api/`-д БҮҮ тавь.

```bash
sudo cp /root/PARKING/deploy/nginx/parking-ratelimit.conf /etc/nginx/conf.d/
# vhost-ийн location = /api/auth/login дотор:  limit_req zone=parking_login burst=5 nodelay;
sudo nginx -t && sudo systemctl reload nginx
```

7 хоног `grep 'limiting requests' /var/log/nginx/error.log` — бодит оператор
цохигдоогүй бол л `/api/` дээр `parking_api`-г нэмнэ.

---

## Үе шат 4 — LPR callback хаах (ЭХЛЭЭД ХЭМЖИНЭ)

Прод дээр камер HTTP push ҮНЭХЭЭР хийдэг эсэх, ямар IP-ээс ирдгийг:

```bash
sudo journalctl -u parking-backend --since '-7 days' | grep -oE 'lpr_push: ip=[0-9.]+' | sort | uniq -c | sort -rn
```

* **Хоосон** (TEST дээр 10 хоногт 0 байсан — event нь comet сувгаар ирдэг) →
  `allow 127.0.0.1; deny all;` тавихад аюулгүй.
* **IP гарч ирвэл** → тэдгээрийн дэд сүлжээг ЗААВАЛ `allow`-д нэмнэ.
  Таамгаар бүү бич — жагсаалтад гарсан IP-г л бич.

```nginx
location /api/lpr/ {
    include snippets/parking-security.conf;
    allow 127.0.0.1;
    # allow <ХЭМЖИЛТЭЭР ГАРСАН дэд сүлжээ>;
    deny all;
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $remote_addr;   # хуурах гинжийг таслана
}
```

`X-Forwarded-For $remote_addr` нь халдагчийн илгээсэн XFF гинжийг таслах гол мөр
(443 ба 80 БОТЬ блокт хийнэ).

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
