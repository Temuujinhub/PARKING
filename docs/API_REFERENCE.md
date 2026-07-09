# Easy Parking — API лавлах

Гадаад хөгжүүлэгчдэд (Mobile апп, PAX POS апп) зориулсан API баримт бичиг.

- **Base URL:** `https://test.easy-parking.mn` (HTTPS — Let's Encrypt)
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

### POST /api/sessions/manual-entry  (OPERATOR+)
Орох камерт уншигдалгүй орсон машиныг ажилтан гараар бүртгэнэ (эргүүлийн шалгалт):
`{"site_id": "uuid", "plate_number": "1234АБВ", "entry_time": "2026-07-06T10:00:00"}`
`entry_time` заавал биш (default = одоо). Давхар бүртгэлээс хамгаална (400).

---

## 3. Төлбөр

### POST /api/payments/qpay/invoice  (нэвтрэлт шаардахгүй)
`{"session_id": "uuid", "customer_tin": "1234567" (сонголт — ААН баримт)}` → QPay нэхэмжлэл.

Нэхэмжлэл нь **e-Barimt-тэй** нэхэмжлэхийн кодоор (`EB_EASY_2PARKING_INVOICE`) үүсэх ба
бүтээгдэхүүн бүрээр `lines` (`classification_code`, НӨАТ 4 орны нарийвчлалтай) задлагдана.
`customer_tin` өгвөл ААН (COMPANY) баримт, үгүй бол иргэн (CITIZEN).

```json
{
  "payment_id": "74e9841c-...",
  "invoice_id": "d50f49f2-9032-4a74-8929-530531f28f63",
  "qr_text": "0002010102121531...C66D",
  "qr_image": "base64 PNG...",
  "deep_link": "qpaywallet://q?qPay_QRcode=...",
  "urls": [{"name": "Khan bank", "logo": "...", "link": "khanbank://q?..."}],
  "amount": 5000.0
}
```

### GET|POST /api/payments/qpay/webhook?payment_id=&qpay_payment_id=  (QPay callback)
QPay төлбөр амжилттай болмогц дуудна. Стандартын дагуу **plain text `SUCCESS`** буцаана.
Систем `payment/check`-ээр эргэж баталгаажуулж, `qpay_payment_id`-аар **e-Barimt (ebarimt_v3)**
үүсгэж, хаалтыг нээнэ. `PARKING_QPAY_WEBHOOK_SECRET` тохируулсан бол `&token=` таарах ёстой.

### POST /api/payments/qpay/check/{payment_id}  (нэвтрэлт шаардахгүй)
Polling — 5 сек тутам дуудна (webhook ирээгүй тохиолдолд). Хариу `PENDING` эсвэл PAID үед
**хэвлэх өгөгдөл хамт**:

```json
{
  "status": "PAID",
  "ebarimt_id": "030101065006000090690000210005595",
  "lottery_code": "HV 83198235",
  "qr_data": "138431709437501...",
  "print_data": {"lines": ["ЗОГСООЛЫН ТӨЛБӨРИЙН БАРИМТ", "..."]}
}
```

PAID болмогц систем хаалтыг автоматаар нээж, e-Barimt үүсгэнэ. POS терминал энэ хариунаас
`print_data` + `qr_data`-г шууд хэвлэнэ (доор §11).

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
  "ebarimt_id": "1234567890123456789012345678901234567890",
  "lottery_code": "65432101",
  "qr_data": "1234567890...543210",
  "print_data": {"lines": ["ЗОГСООЛЫН ТӨЛБӨРИЙН БАРИМТ", "Дугаар: 5428УНО", "..."]}
}
```

e-Barimt: `ebarimt_id` = ДДТД (баримтын дугаар), `qr_data`-г thermal printer дээр
**QR код болгон хэвлэнэ** — хэрэглэгч ebarimt апп-аар уншуулна. Картаар (POS) болон бэлнээр
төлсөн баримт нь локал **PosAPI 3.0**-аар, QR-аар (QPay) төлсөн баримт нь **QPay ebarimt_v3**-аар
үүснэ (§11).

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
| GET /api/public/search?site=&q=0028 | Хялбар хайлт: эхний тоогоор таарах машинууд (үсэг шаардахгүй, 2+ тэмдэгт) |
| GET /api/public/sessions?plate=&site= | Session + төлбөрийн задаргаа |
| GET /api/public/qr/{site_code}.png | Зогсоолын төлбөрийн QR (хэвлэхэд бэлэн PNG) |
| GET /api/public/receipt/{payment_id} | Төлбөрийн дараах e-Barimt баримт (billId/ДДТД, lottery, qr_data) |
| GET /api/public/receipt/{payment_id}/qr.png | Баримтын qrData-г QR зураг болгосон PNG (ebarimt апп уншина) |

---

## 5. Хаалт (Barrier)

### POST /api/barriers/{device_id}/open  (OPERATOR+)
Гараар нээх. Body: `{"force": true}` өгвөл forceBreaking — хаалт онгорхой хэвээр
үлдэнэ (гараар хаах хүртэл). Хариу: `{"status": "SUCCESS" | "FAILED", "response": "..."}`

### POST /api/barriers/{device_id}/close  (OPERATOR+)
Гараар хаах (closeStrobe) — албадан нээснийг буцаах, туршилтын дараа хаах.
Хариу: `{"status": "SUCCESS" | "FAILED", "response": "..."}`

### GET /api/barriers/commands
Командын аудит лог.

> Хаалтын команд Dahua ITC камерын **RPC2** (JSON-RPC) интерфэйсээр явдаг:
> `global.login` (2 алхамт MD5) → `trafficSnap.openStrobe / closeStrobe / forceBreaking`.
> Хаалт төхөөрөмжид IP байхгүй бол ижил эгнээний камерын IP автоматаар ашиглагдана.

---

## 6. WebSocket (Real-time)

```
wss://test.easy-parking.mn/ws/sites/{site_id}     — нэг зогсоолын events
wss://test.easy-parking.mn/ws/sites/all           — бүх зогсоол
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

---

## 11. e-Barimt 3.0 (QPay-ээр дамжуулсан) + POS QPay QR урсгал

QR-аар (QPay) төлсөн тохиолдолд e-Barimt-ыг **QPay-ийн ebarimt_v3 API**-аар үүсгэнэ
(татварын PosAPI-г шаардахгүй). QPay гэрээ:

- **Client:** `EASY_2PARKING` / **Password:** *(нууц — зөвхөн серверийн `.env`-д, репод хадгалахгүй)*
- **Invoice code:** `EB_EASY_2PARKING_INVOICE` (НӨАТ-ийн мэдээлэлтэй)
- **e-Barimt URL:** `https://merchant.qpay.mn/v2/ebarimt_v3/create`

`.env` (production — нууц үгийг зөвхөн энд, git-д хэзээ ч бүү оруул):
```
PARKING_QPAY_MOCK=false
PARKING_QPAY_SANDBOX=false
PARKING_QPAY_USERNAME=EASY_2PARKING
PARKING_QPAY_PASSWORD=<QPay-ээс өгсөн нууц үг>
PARKING_QPAY_INVOICE_CODE=EB_EASY_2PARKING_INVOICE
PARKING_QPAY_EBARIMT=true
PARKING_QPAY_DISTRICT_CODE=3505      # үйл ажиллагааны байршил (4 орон)
PARKING_QPAY_CLASSIFICATION_CODE=5221190  # зогсоолын үйлчилгээ
PARKING_QPAY_WEBHOOK_SECRET=<санамсаргүй урт тэмдэгт>
```

**Урсгал (сервер тал):**
1. `invoice` — `EB_..._INVOICE` кодоор, `tax_type=1`, `district_code`, `lines[]`
   (бүтээгдэхүүн бүрээр, мөрд НӨАТ = үнэ×0.1/1.1-ийг **4 орноор тасалж**) → `qr_text/qr_image`.
2. Жолооч QR уншиж төлнө → QPay `callback` (GET, `SUCCESS`) эсвэл `payment/check` polling.
3. `payment/check`-ээс QPay `g_payment_id` авна.
4. `ebarimt_v3/create` `{payment_id, ebarimt_receiver_type: CITIZEN|COMPANY}` →
   `ebarimt_qr_data`, `ebarimt_lottery`, `ebarimt_receipt_id` (ДДТД).
5. Баримтыг pay хуудсанд харуулж / POS-д хэвлэнэ. QR **DB-д хадгалахгүй** (түр санах ой, 1 цаг).

**POS терминал дээрх QPay QR урсгал** (жолооч утсаараа QR уншуулж төлөх):
1. Оператор машин сонгоод **төлбөрийн төрөл → QPay** сонгоно.
2. `POST /api/payments/qpay/invoice {session_id}` → `qr_image` (эсвэл `qr_text`).
3. POS апп **QR-ийг дэлгэцэндээ харуулна**; жолооч банкны апп-аараа уншиж төлнө.
4. POS апп `POST /api/payments/qpay/check/{payment_id}`-г 3–5 сек тутам дуудна.
5. Хариу `status=PAID` болмогц дотор нь ирсэн `print_data.lines` (текст) +
   `qr_data` (e-Barimt QR)-г thermal printer-ээр хэвлэж жолоочид өгнө. Хаалт автоматаар нээгдсэн байна.
