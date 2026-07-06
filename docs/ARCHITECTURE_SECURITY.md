# Архитектур, аюулгүй байдал ба scale-ийн дүгнэлт

Энэ баримт нь Easy Parking системийн (а) кодын чанар, (б) аюулгүй байдал,
(в) service-үүдийг тусад нь сервер дээр ажиллуулах архитектурын дүгнэлтийг агуулна.

---

## 1. Аюулгүй байдал — халдлагын шинжилгээ

### Хаагдсан (2026-07-07 нөхөв)

| Эрсдэл | Байршил | Шийдэл |
|---|---|---|
| **QPay webhook хуурамчлах** — халдагч PAID webhook явуулж хаалт нээлгэх | payments_router.py | `PARKING_QPAY_WEBHOOK_SECRET` тохируулбал `?token=` шалгана (timing-safe). Дүнгийн шалгалт + idempotency өмнөөс байсан. |
| **simulate endpoint** — auth-гүйгээр session/barrier үүсгэх | lpr_router.py | Production-д (`ALLOW_SIMULATE=false` эсвэл barrier бодит) 403 |
| **CORS `*` + credentials** | main.py | `*` үед credentials унтардаг болгосон; production-д домэйн зааж өгнө |
| **Хамгаалалтын header дутуу** | nginx | HSTS, X-Frame-Options, X-Content-Type-Options, Referrer-Policy нэмэв |

### Аль хэдийн зөв байсан (давуу тал)

- **SQL injection**: SQLAlchemy ORM бүрэн ашигладаг, raw query байхгүй → эрсдэлгүй
- **Нууц үг**: bcrypt hash, plaintext хадгалдаггүй
- **JWT**: 12 цагийн TTL, роль/site токенд шифрлэгддэг
- **RBAC**: 4 түвшний эрх, endpoint бүрд `require(...)` шалгалт
- **device_key**: камер бүрд санамсаргүй 16-тэмдэгт түлхүүр — хуурамч LPR event хаана
- **Barrier audit**: команд бүр `barrier_commands` хүснэгтэд эх үүсвэр/хэн/үр дүнтэй
- **Дүнгийн баталгаа**: төлбөрийн дүн зөрвөл хаалт нээхгүй, manual review
- **e-Barimt QR**: DB-д хадгалахгүй (ТЕГ №11), 1 цагийн санах ой
- **НӨАТ тооцоо**: Decimal, ROUND_HALF_UP — floating point алдаагүй

### Production-д гарахын өмнө заавал (checklist)

- [ ] `PARKING_SECRET_KEY` — санамсаргүй 64+ тэмдэгт болгох (JWT хуурахаас)
- [ ] `PARKING_QPAY_WEBHOOK_SECRET` — QPay merchant тохиргоонд callback_url-тэй хамт өгөх
- [ ] `PARKING_ALLOW_SIMULATE=false`
- [ ] `PARKING_CORS_ORIGINS=https://таны-домэйн`
- [ ] PostgreSQL нууц үг солих + зөвхөн localhost-оос хандах (`pg_hba.conf`)
- [ ] Default хэрэглэгчдийн нууц үг солих (temuujin/admin/sanhuu/operator)
- [ ] UFW firewall: зөвхөн 80/443/SSH; 5432, 8000-г гадаад руу хаах
- [ ] Rate limiting (nginx `limit_req`) — login болон public endpoint-д brute-force хамгаалалт
- [ ] PostgreSQL өдөр тутмын backup (pg_dump + cron)

---

## 2. Кодын чанар

- **Модульчлал**: router бүр нэг домэйн (auth, lpr, sessions, payments, admin, reports,
  compensations, barriers, cashier, public). Нийт ~3000 мөр — цомхон, уншихад ойлгомжтой.
- **Давхардал бага**: billing логик нэг газар (`billing.py`), serializer дундын (`serializers.py`).
- **Сайжруулах цэг**: endpoint-үүд `body: dict` авдаг → Pydantic model руу шилжвэл
  input validation автомат болно (одоо гараар `.get()` шалгадаг). Энэ нь эрсдэл багатай
  боловч кодын найдвартай байдлыг сайжруулна.

---

## 3. Service-үүдийг тусад нь сервер дээр ажиллуулах — архитектурын дүгнэлт

Судалгааны баримт (parking_system_research)-д дурдсан **4 тусдаа сервер**
(App / Backend API / Payment+НӨАТ / Database) архитектур руу шилжих боломжтой юу?

### Одоогийн байдал: модульчлагдсан монолит

```
[nginx] → [FastAPI (1 процесс)] → [PostgreSQL]
                │
                ├── router-ууд (аль хэдийн домэйнээр тусгаарлагдсан)
                └── in-memory төлөв: WebSocket manager, QR cache, QPay token cache
```

Router-ууд домэйнээр цэвэр тусгаарлагдсан тул **логик хувьд микросервис болгоход бэлэн**.
Гэхдээ 3 in-memory төлөв нь **олон сервер/процесст саад болно**:

| In-memory төлөв | Файл | Асуудал | Шийдэл |
|---|---|---|---|
| WebSocket холболтууд | ws.py | Сервер А-д холбогдсон касс, сервер Б-гийн event авахгүй | **Redis pub/sub** — бүх сервер broadcast-ыг Redis-ээр дамжуулна |
| e-Barimt QR cache | ebarimt.py | Нэг серверт үүссэн QR нөгөөд байхгүй | **Redis** (TTL-тэй) |
| QPay token cache | qpay.py | Сервер бүр тусдаа token авна (ажиллана, бага үр ашиггүй) | Redis эсвэл хэвээр |

> **Тийм ч учраас одоо `--workers 1`-ээр ажиллаж байна** — олон worker байвал энэ 3 төлөв
> хуваалцагдахгүй. Redis нэмбэл олон worker/сервер рүү аюулгүй тэлнэ.

### Шилжих зам (өсөлтийн дагуу)

**Шат 0 — одоо (1 сервер, 1 процесс):** 1 зогсоол, багахан ачаалал. Хангалттай.

**Шат 1 — босоо өсөлт (энгийн):**
- Redis нэмэх → `--workers 4` болгох (нэг сервер дээр олон процесс)
- PostgreSQL-ийг тусдаа droplet руу гаргах (`PARKING_DATABASE_URL` өөрчлөх л хангалттай)
- PgBouncer connection pool

**Шат 2 — хэвтээ өсөлт (олон зогсоол, өндөр ачаалал):**
- Router-уудыг 2-3 service болгон хуваах. **Хамгийн эхэнд салгах ёстой нь Payment+НӨАТ**
  (PCI-DSS, татварын мэдээлэл тусгаарлах). FastAPI-д router файлыг өөр процесст зөөх нь
  амархан — код бэлэн, зөвхөн deploy тусгаарлана.
- Load balancer (nginx/Traefik) ард App + Backend API-г олон instance
- Redis pub/sub-аар WebSocket түгээлт

**Шат 3 — бүрэн тусгаарлал (судалгааны 4-сервер загвар):**
```
[LB] → App Server (Next.js/SPA)
     → Backend API Server (lpr, sessions, barriers, ws) ──┐
     → Payment+НӨАТ Server (qpay, pos, ebarimt) ──────────┼→ Redis
                                                           └→ PostgreSQL (master + replica)
```

### Дүгнэлт

Архитектур нь **шат 2 хүртэл ямар ч кодын томоохон өөрчлөлтгүйгээр** тэлэх боломжтой —
router-ууд аль хэдийн тусгаарлагдсан, DB/гадаад сервисийн хаяг бүгд `.env`-ээр
тохируулагддаг. Цорын ганц заавал хийх ажил бол **Redis нэмж 3 in-memory төлвийг
шилжүүлэх** — үүнгүйгээр олон процесс/сервер найдваргүй. Энэ нь ~1 өдрийн ажил бөгөөд
эхний зогсоол ажиллаж эхэлсний дараа өсөлтийн шаардлагаар хийхэд тохиромжтой.
