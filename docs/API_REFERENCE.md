# Easy Parking — API лавлах

Гадаад хөгжүүлэгчдэд (Mobile апп, PAX POS апп) зориулсан API баримт бичиг.

- **Base URL:** `http://152.42.235.199` (⚠️ одоогоор зөвхөн HTTP — домэйн холбогдмогц HTTPS болно)
- **Interactive docs (Swagger):** `GET /api/docs`
- **Postman collection:** [postman/EasyParking_POS.postman_collection.json](postman/EasyParking_POS.postman_collection.json)
  — Postman → Import хийгээд `1. Нэвтрэлт → Login`-г ажиллуулбал token автоматаар тохирно.
- **Формат:** JSON, UTF-8. Огноо ISO-8601 (UTC).
- **Мөнгөн дүн:** ₮ (MNT), НӨАТ 10% үнэд багтсан.

---

## 1. Нэвтрэлт (Authentication)

JWT Bearer token ашиглана. Token 12 цагийн хугацаатай.

### POST /api/auth/login
`Content-Type: application/x-www-form-urlencoded`

| Талбар | Тайлбар |
|---|---|
| username | Нэвтрэх нэр |
| password | Нууц үг |

**Хариу:**
```json
{
  "access_token": "eyJhbG...",
  "token_type": "bearer",
  "user": {"id": "...", "username": "operator", "role": "OPERATOR", "site_id": null},
  "permissions": ["cashier", "check", "dashboard", "..."]
}
```

Дараагийн бүх хүсэлтэд: `Authorization: Bearer {access_token}`

**Эрхийн түвшин:**

| Role | Хандах модулиуд |
|---|---|
| SUPER_ADMIN | Бүгд + хэрэглэгчийн удирдлага |
| ADMIN | Тохиргоо, бүх үйл ажиллагаа |
| FINANCE | Тайлан, төлбөр, НӨАТ, лог |
| OPERATOR | Касс, шалгах, түүх, хаалт, жолооч |

### GET /api/auth/me
Одоогийн хэрэглэгчийн мэдээлэл + эрхүүд.

---

## 2. Session (зогсолтын бүртгэл)

Session төлөвүүд:

```
OPEN ──(exit камерт уншигдана)──► AWAITING_PAYMENT ──(төлбөр)──► PAID ──(гарна)──► CLOSED
  └──(үнэгүй хугацаанд/гэрээт гарвал)──► FREE
```

### GET /api/sessions
Жагсаалт. Query: `site_id, status, plate, date_from, date_to, limit, offset`
Хариу: `{"total": 123, "rows": [...]}`

### GET /api/sessions/check?plate=1234АБВ
Дугаараар нээлттэй session хайх (fee задаргаатай).

### GET /api/sessions/recent-exits?site_id={uuid}&minutes=30
**PAX POS-ийн гол endpoint** — гарах камерт сүүлд уншигдсан, төлбөр хүлээж буй машинууд.

```json
[{
  "id": "45ad1f01-...",
  "plate_number": "5428УНО",
  "entry_time": "2026-07-06T09:07:04",
  "status": "AWAITING_PAYMENT",
  "total_fee": 2000.0,
  "fee": {"duration_minutes": 95, "base_fee": 1818, "vat_amount": 182, "total_fee": 2000, "is_free": false}
}]
```

### GET /api/sessions/{id}
Нэг session дэлгэрэнгүй.

### POST /api/sessions/{id}/apply-discount
`{"discount_id": "uuid эсвэл null"}` — хөнгөлөлт хэрэглэх/арилгах.

### POST /api/sessions/{id}/manual-exit
`{"open_barrier": true, "reason": "..."}` — оператор гараар гаргах.

---

## 3. Төлбөр

### POST /api/payments/qpay/invoice  (нэвтрэлт шаардахгүй)
`{"session_id": "uuid"}` → QPay нэхэмжлэл.

```json
{
  "payment_id": "74e9841c-...",
  "invoice_id": "QP-INV-...",
  "qr_text": "https://qpay.mn/q/...",
  "qr_image": "base64...",
  "deep_link": "qpay://q?invoice=...",
  "amount": 2000.0
}
```

### POST /api/payments/qpay/check/{payment_id}  (нэвтрэлт шаардахгүй)
Polling — 5 сек тутам дуудна. Хариу: `{"status": "PENDING" | "PAID"}`
PAID болмогц систем хаалтыг автоматаар нээж, e-Barimt үүсгэнэ.

### POST /api/payments/cash  (OPERATOR+)
`{"session_id": "uuid"}` — кассын бэлэн мөнгөний төлбөр. Хаалт автоматаар нээгдэнэ.

### POST /api/payments/pos/confirm  (OPERATOR+, **PAX аппын гол endpoint**)
Карт амжилттай уншигдсаны ДАРАА дуудна:

```json
{
  "session_id": "uuid",
  "amount": 2000.0,
  "auth_code": "ABC123",
  "card_last4": "4242",
  "card_brand": "Visa",
  "terminal_id": "TDB-PAX-SITE01-01",
  "transaction_id": "TDB-TXN-20260706-9988"
}
```

**Хариу** (хэвлэх өгөгдөлтэй):
```json
{
  "status": "PAID",
  "barrier_opened": true,
  "ebarimt_id": "EB-2026-...",
  "lottery_code": "AB123456",
  "print_data": {"lines": ["ЗОГСООЛЫН ТӨЛБӨРИЙН БАРИМТ", "Дугаар: 5428УНО", "..."]}
}
```

⚠️ `amount` нь системийн тооцсон дүнтэй таарахгүй бол `400` буцна — картаар авахын ӨМНӨ
`GET /api/sessions/{id}`-ээс дүнг АВЧ баталгаажуулна.

### GET /api/payments
Төлбөрийн жагсаалт (FINANCE+). Query: `site_id, status, provider, date_from, date_to`.

---

## 4. Public API (нэвтрэлтгүй — жолоочийн /pay хуудас)

| Endpoint | Тайлбар |
|---|---|
| GET /api/public/site/{site_code} | Зогсоолын нэр, үнэгүй минут |
| GET /api/public/recent-exits/{site_code} | Сүүлд гарах камерт уншигдсан дугаарууд |
| GET /api/public/sessions?plate=&site= | Session + төлбөрийн задаргаа |

---

## 5. Хаалт (Barrier)

### POST /api/barriers/{device_id}/open  (OPERATOR+)
Гараар нээх. Хариу: `{"status": "SUCCESS" | "FAILED"}`

### GET /api/barriers/commands
Командын аудит лог.

---

## 6. WebSocket (Real-time)

```
ws://152.42.235.199/ws/sites/{site_id}     — нэг зогсоолын events
ws://152.42.235.199/ws/sites/all           — бүх зогсоол
```

Мессежийн формат:
```json
{"type": "EXIT_LPR_EVENT", "site_id": "...", "ts": "2026-07-06T10:41:00", "data": {...}}
```

| type | Хэзээ | data |
|---|---|---|
| ENTRY_EVENT | Машин орох үед | session_id, plate, registered, barrier_opened |
| EXIT_LPR_EVENT | Гарах камерт уншигдаж төлбөр хүлээх үед | session_id, plate, total_fee, duration_minutes |
| PAYMENT_COMPLETED | Төлбөр төлөгдөх үед | session_id, plate, exit_deadline |
| EXIT_COMPLETED | Машин гарах үед | session_id, plate, status, barrier_opened |
| BLACKLIST_ALERT | Хар жагсаалтын машин орох үед | plate, reason |
| EXIT_NO_SESSION | Бүртгэлгүй машин гарах гэх үед | plate |
| BARRIER_MANUAL_OPEN | Гараар нээх үед | device_id, by |

Холболт тасарвал 3 секундын дараа дахин холбогдоно. 30 сек тутам `ping` илгээхийг зөвлөнө.

---

## 7. LPR Callback (камерын интеграц)

Dahua ITC436/IPMECS камерын ITSAPI тохиргоо:

```
URL: http://{SERVER}/api/lpr/callback?device_key={device_key}
```

`device_key`-г Тохиргоо → Төхөөрөмж хуудаснаас авна. Камер дараах JSON POST хийнэ:

```json
{"Events": [{"Code": "TrafficJunction",
  "TrafficCar": {"PlateNumber": "1234АБВ", "Confidence": 97},
  "UTC": "2026-07-06 10:00:00"}]}
```

- Confidence < 90 → event бүртгэгдэх боловч татгалзана
- 20 секунд доторх давхар event автоматаар нэгтгэгдэнэ (dedup)

### POST /api/lpr/simulate  (туршилт)
`{"device_key": "cam-entry-site01", "plate": "1234АБВ"}` — камергүйгээр урсгал турших.

---

## 8. Тайлан

| Endpoint | Тайлбар |
|---|---|
| GET /api/reports/dashboard | Нүүрний статистик |
| GET /api/reports/revenue?date_from=&date_to= | Зогсоол тус бүрийн орлого |
| GET /api/reports/revenue/excel | Excel татах |
| GET /api/reports/vat-receipts | НӨАТ баримтууд |
| GET /api/cashier/shifts | Касс хаалтын тайлан |
| GET /api/reports/audit-logs | Үйлдлийн лог |

## 9. Кассын ээлж

| Endpoint | Тайлбар |
|---|---|
| GET /api/cashier/shift/current | Одоогийн ээлж + нийлбэр |
| POST /api/cashier/shift/open `{"opening_amount": 0}` | Ээлж нээх |
| POST /api/cashier/shift/close | Ээлж хаах (нийлбэр буцаана) |

## 10. Алдааны формат

```json
{"detail": "Алдааны тайлбар (Монголоор)"}
```

| Код | Утга |
|---|---|
| 400 | Буруу хүсэлт / дүн зөрүү |
| 401 | Token хүчингүй — дахин login |
| 403 | Эрх хүрэхгүй |
| 404 | Олдсонгүй |
