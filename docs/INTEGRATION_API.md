# Түншийн интеграцийн API (/api/v1)

tokI, Easy Wallet, MonPay, SocialPay зэрэг гадаад төлбөрийн систем зогсоолын төлбөрийг өөрийн апп
дотроо төлүүлэхэд зориулсан B2B API. Төлбөр баталгаажмагц систем хаалтыг нээнэ.

> **e-Barimt-ыг ЗӨВХӨН EasyParking-ийн систем гаргана** (confirm-ийн хариу, `payment.paid` /
> `receipt.ready` webhook-оор ДДТД/сугалаа түнш рүү очно). Түнш ЭНЭ төлбөрт өөрийн e-Barimt
> гаргавал ТЕГ-т нэг борлуулалт 2 баримттай болно (Easy Wallet дээр ажиглагдсан). Түншид өгөх
> бүрэн баримт: MonPay — `MonPay_EasyParking_API_v1.0.docx` (2026-10-15), SocialPay —
> `docs/SOCIALPAY_INTEGRATION.md`.
>
> **Урьдчилсан нөхцөл:** Тохиргоо → Холболт → e-Barimt API (msgbill)-д **«Хэтэвч» (WALLET)** арга
> асаалттай байх ёстой. Үгүй бол түншийн (WALLET) төлбөрийн баримт PosAPI руу унаж `FAILED`
> болно (audit1014: прод дээр msgbill арга `CARD,CASH,QR,TRANSFER` — WALLET-гүй; 08-31…10-14-ний
> easywallet-ийн 30 төлбөр / 95,500₮ бүгд FAILED). Тэдгээрийг бөөнөөр «Дахин үүсгэх»-ийн ӨМНӨ
> Easy Wallet өөрөө баримт гаргасан эсэхийг ТЕГ экспортоор тулгана — эс бол давхардал.

## Холболт идэвхжүүлэх (сервер талд)

`.env`-д түнш бүрд түлхүүр нэмнэ (**код өөрчлөгдөхгүй**):

```
PARKING_PARTNER_KEYS=toki:УРТ_НУУЦ_ТҮЛХҮҮР1,easywallet:УРТ_НУУЦ_ТҮЛХҮҮР2
```

Дараа нь: `sudo systemctl restart parking-backend`

Түншийн нэр (toki → TOKI) нь Payment.provider болж бүх тайланд ялгарна.

## Нэвтрэлт

Бүх хүсэлтэд толгой: `X-API-Key: <түлхүүр>`
Буруу түлхүүр → 401. Минутад 20-оос олон буруу оролдлого → 429.

Түлхүүрийг Тохиргоо → Холболт → Гадаад API-д үүсгэнэ (DB-д зөвхөн SHA-256 hash; түлхүүр
ҮҮСГЭХ мөчид НЭГ л удаа харагдана). Эрх: `read` (лавлах), `pay` (intent/confirm/cancel),
`vehicles` (машин бүртгэл, bill, орох/гарах мэдэгдэл). «Хаах» нь тэр дороо хүчингүй болгоно;
«Сэргээх» нь ИЖИЛ түлхүүрийг дахин идэвхжүүлнэ. Хаагдсан түлхүүрийн мөрөнд Webhook болон
«Машин бүртгэл» тохиргооны талбар харагдахгүй («—») — сэргээсний дараа гарч ирнэ.

## Endpoints

### 1. GET /api/v1/sites — зогсоолууд + сул байр

```json
{"sites": [{"site_code": "NIC", "name": "NIC зогсоол", "zone_code": "A",
            "address": "...", "capacity": 20, "occupied": 5, "free": 15}]}
```
`capacity=0` бол `free=null` (дүүргэлт хянадаггүй).

### 2. GET /api/v1/sessions?plate=1234УБА[&site_code=NIC] — дугаараар хайх

site_code өгөхгүй бол бүх зогсоолоос хайна. Хариу:

```json
{"sessions": [{
  "session_id": "...", "plate_number": "1234УБА",
  "site_code": "NIC", "site_name": "NIC зогсоол",
  "entry_time": "2026-07-25T04:30:00", "duration_minutes": 95,
  "base_fee": 1818, "vat_amount": 182, "discount_amount": 0,
  "total_fee": 2000,
  "amount_due": 2000,     // ← ЭНЭ дүнг хэрэглэгчээс нэхэмжилнэ
  "is_free": false, "status": "AWAITING_PAYMENT", "paid": false}]}
```

### 2.1 GET /api/v1/recent-exits — бүх зогсоолын гарцын урсгал (2026-09-01)

`?minutes=5` (1..60, default 5) — сүүлийн N минутад гарах камерт уншигдаад
**төлбөр хүлээж буй** машинууд, бүх зогсоолоос (түлхүүр зогсоолоор
хязгаарлагдсан бол зөвхөн тэр). Wallet апп хэрэглэгчийн машиныг гарц дээр
ирмэгц таньж төлбөрийн санал харуулахад зориулагдсан; 30 секунд тутам
polling хийхэд тохиромжтой.

Нэмэлт параметр: `site_code=NIC` — нэг зогсоол руу шүүх;
`include_completed=true` — мөн хугацаанд гарч дууссан (төлөгдсөн/үнэгүй)
машинуудыг `completed` жагсаалтаар нэмж өгнө.

```json
{"minutes": 5,
 "waiting": [{
   "session_id": "...", "plate_number": "1234УБА",
   "site_code": "NIC", "site_name": "NIC зогсоол",
   "entry_time": "2026-09-01T04:30:00", "duration_minutes": 95,
   "total_fee": 2000, "amount_due": 2000, "is_free": false,
   "status": "AWAITING_PAYMENT", "paid": false,
   "exit_read_at": "2026-09-01T06:05:12",   // гарах камерт уншигдсан цаг
   "exit_time": "2026-09-01T06:05:12"}]}
```

Төлөх бол мөр бүрийн `session_id`-гаар ердийн урсгал: POST /payments →
/confirm (хаалт автоматаар нээгдэнэ).

### 3. POST /api/v1/payments — төлбөрийн intent

Body: `{"session_id": "..."}` → `{"payment_id": "...", "amount": 2000, "status": "PENDING"}`

Wallet энэ `amount`-аар хэрэглэгчээс мөнгө авна. Идэвхтэй PENDING intent
байвал (дүн өөрчлөгдөөгүй бол) ижил payment_id дахин буцаана.

`"include_debts": true` (заавал биш, 2026-10-14; анхдагч `false` — хуучин зан төлөв): тухайн
түрээслэгчийн зогсоолуудын төлөгдөөгүй өрийг QR-тэй ижил нэг intent-д нийлүүлнэ — хариунд
`parking_amount`, `debt_amount`, `debt_count`; confirm нь өрүүдийг PAID болгоно. Өр 3+ (анхдагч
`block_exit_debt_count`) машиныг зөвхөн зогсолтын төлбөрөөр гарц НЭЭГДЭХГҮЙ.

### 4. POST /api/v1/payments/{payment_id}/confirm — баталгаажуулах

Body: `{"transaction_id": "TX123", "amount": 2000}` — `amount` **заавал биш** (2026-10-14):
талбар огт байхгүй бол intent-ийн дүнгээр баталгаажна; ирсэн бол яг тэр дүнтэй тэнцүү байх ёстой
(`null`/буруу утга → 422). Аюулгүй байдлын үүднээс `amount`-ыг илгээхийг зөвлөнө.
`transaction_id` текст эсвэл бүхэл тоо байж болно (1–120 тэмдэгт).

- Дүн ±1₮-өөс зөрвөл 400 (хаалт нээгдэхгүй).
- Амжилттай → `{"status": "PAID"}` + систем session-ийг PAID болгож **хаалт нээнэ**,
  e-Barimt үүсгэнэ.
- Idempotent: давхар дуудахад 200 + PAID (алдаа өгөхгүй).

### 5. GET /api/v1/payments/{payment_id} — төлөв шалгах

`{"payment_id": "...", "status": "PENDING|PAID|CANCELLED", "amount": 2000, "paid_at": ..., "ebarimt": {...}|null}`

### 6. e-Barimt (НӨАТ-ын баримт) — 2026-09-13

Төлбөр PAID болмогц систем баримтыг өөрөө үүсгэнэ (QPay/msgbill). Түнш баримтыг
хоёр замаар авна:

**а) Хариунд** — `confirm` ба `GET /payments/{id}` хоёулаа `ebarimt` талбартай:

```json
"ebarimt": {
  "ddtd": "030101065006000097410004910014863",   // ДДТД (33 орон)
  "lottery": "SO 63177600",                       // сугалааны дугаар (ААН-д null)
  "amount": 2000, "vat_amount": 182,
  "status": "SENT",                               // SENT | PENDING | FAILED | REVIEW
  "provider": "MSGBILL",                          // MSGBILL | POSAPI | QPAY
  "error": null,                                  // FAILED үед шалтгаан
  "qr_data": "…",                                 // ebarimt QR (түр санах ойд байвал)
  "created_at": "2026-09-13T10:20:00"
}
```

Түншийн confirm баримтыг ХҮЛЭЭХГҮЙ (баримт outbox ажилтнаар хэдхэн секундэд үүснэ) — тиймээс
confirm-ийн хариунд `ebarimt.status` ихэвчлэн `PENDING`, `ddtd: null`. Эцсийн ДДТД-г
`receipt.ready` webhook-оор эсвэл 2–3 секундын дараа `GET /payments/{id}`-ээр авна.
`include_debts=true` үед нэг төлбөрт хэд хэдэн баримт (зогсолт + өр тус бүр) үүсч, `ebarimt`
талбарт хамгийн сүүлд үүссэн НЭГ нь л харагдана — бүгдийг `receipt.ready` (баримт тус бүрд нэг)-ээр.

**б) Webhook (POST)** — Тохиргоо → Холболт → Гадаад API → түлхүүрийн «Webhook (төлбөр + баримт)»
талбарт түншийн URL оруулбал (төлбөр батлагдах ҮЕД URL тохируулсан байх ёстой) систем 2 төрлийн
event илгээнэ. Толгой: `Content-Type: application/json`, `X-Parking-Partner: <нэр>`,
`X-Parking-Event: payment.paid|receipt.ready`, `X-Parking-Event-Id` = `Idempotency-Key` = `event_id`.

```json
{"event": "payment.paid", "event_id": "…", "payment_id": "…", "status": "PAID",
 "transaction_id": "TX123", "amount": 2000, "vat_amount": 182, "paid_at": "…",
 "plate": "1234УБА", "site_code": "NIC", "site_name": "NIC",
 "ebarimt": {…дээрхтэй ижил, qr_data-гүй; ихэвчлэн status PENDING…},
 "session_status": "PAID", "amount_due": 0, "needs_additional_payment": false, "overpaid_amount": 0}

{"event": "receipt.ready", "event_id": "…", "payment_id": "…", "receipt_id": "…",
 "ddtd": "0301…", "lottery": "SO 63177600", "amount": "2000.00", "status": "SENT"}
```

- Амжилт = HTTP 2xx (body-г шалгахгүй). Хугацаа 10с.
- Амжилтгүй бол давтана: 1, 2, 4, 8, 16, 32 мин, дараа нь цаг тутам — нийт 24 оролдлого (~18 цаг),
  дараа нь `UNKNOWN` (админ шалгана). At-least-once — `event_id`-аар давхардлыг ялгана.
- `receipt.ready` нь `payment.paid`-ээс ӨМНӨ ирж болно (тусдаа дараалал) — `payment_id`-аар холбоно.
- Гарын үсэг (HMAC) БАЙХГҮЙ: түнш event-д итгэхээсээ өмнө `GET /payments/{payment_id}`-ээр
  өөрийн түлхүүрээр баталгаажуулах, эсвэл манай гадагшаа IP-г (прод `202.21.117.178`, тест
  `152.42.235.199`) зөвшөөрөх жагсаалтад оруулна.
- «Турших» товч `payment.paid.test` event-ийг (`"test": true`) сүүлийн бодит төлбөрийн өгөгдлөөр
  (эсвэл жишээгээр) нэг удаа илгээж HTTP код/хариуг харуулна (давтахгүй).

### 7. Машин бүртгэл, bill, байршил, орох/гарах мэдэгдэл (SocialPay 2026-10-14; MonPay г.м 2026-10-15)

`vehicles` эрхтэй түлхүүрээр. Хоёр ижил зам — ЯГ ИЖИЛ handler, хариу, эрх, хязгаар:

| Түншээс үл хамаарах (MonPay г.м) | SocialPay (spec) | |
|---|---|---|
| `POST /api/v1/vehicles/register` | `POST /api/v1/socialpay/register` | `{"plateNo","userId"}` → `{"Success": true, "Message": "OK"\|"ALREADY_REGISTERED"}` |
| `POST /api/v1/vehicles/delete` | `POST /api/v1/socialpay/delete` | → `OK` \| `NOT_FOUND` (Success=true) |
| `POST /api/v1/vehicles/bill` | `POST /api/v1/socialpay/bill` | бүртгүүлсэн хэрэглэгчийн машины одоогийн зогсолт/үнэгүй хугацаа/төлөх дүн |
| `GET\|POST /api/v1/parkings` | `GET\|POST /api/v1/socialpay/parkings` | гадна зогсоолууд, координат, `freeMinutes` (`vehicles` эрх шаардахгүй) |

Хариу `{"Success", "Message", "Detail"?, "data"?}` (camelCase), алдаа ч ижил хэлбэр + HTTP код
(404/405/422 мөн). Ялгаа нь зөвхөн бүртгэлийн `created_via` (`api` / `socialpay`). Хурдны хязгаар
түлхүүр тутам НИЙТ минутад 600 (`PARKING_PARTNER_VEHICLE_RATE_PER_MIN`), нэг хэрэглэгч ≤10 машин.

Бүртгэлтэй машин орох/гарах бүрд түлхүүрийн «Машин бүртгэл» мэдэгдлийн URL руу POST
(`X-Parking-Event: vehicle.entry|vehicle.exit`, `Idempotency-Key`). Формат:
`socialpay` (үндсэн) — `{"data":{"plateNo","enteredDate","exitedDate"?}}`;
`extended` (MonPay-д) — нэмэлт `event`, `sessionId`, `siteCode`, `siteName`, ENTRY-д
`freeMinutes`/`freeUntil`, EXIT-д `exitConfirmed`/`minutesParked`/`totalFee`/`isFree`
(гарах уншилтгүй албадан хаалтыг ч `exitConfirmed:false`-оор илгээнэ). Амжилт = 2xx БА body-д
`"Success": true` эсвэл `"success": true`; үгүй бол 1м, 5м, 15м, 1ц, 2ц, дараа нь 4ц тутам 24ц
хүртэл → DEAD. Дэлгэрэнгүй дүрэм: `docs/SOCIALPAY_INTEGRATION.md`.

## Жишээ урсгал (curl)

```bash
K="X-API-Key: ТҮЛХҮҮР"
B="https://site.easy-parking.mn"

curl -H "$K" "$B/api/v1/sessions?plate=1234УБА"
curl -H "$K" -X POST "$B/api/v1/payments" -H "Content-Type: application/json" \
     -d '{"session_id":"SID"}'
curl -H "$K" -X POST "$B/api/v1/payments/PID/confirm" -H "Content-Type: application/json" \
     -d '{"transaction_id":"TX1","amount":2000}'
```

## POS терминалын зогсоол-харьяалал

PAX терминалыг **Тохиргоо → Төхөөрөмж**-д бүртгэнэ:
- Төрөл: `pax_terminal`, `device_key` = терминалын serial/ID.

POS апп асахдаа (оператор нэвтрэлттэй):
`GET /api/payments/pos/terminal/{terminal_id}` → өөрийн site_code/site_name +
тухайн зогсоолын төлбөр хүлээж буй машинууд. `pos/confirm`-д terminal_id
дамжуулбал өөр зогсоолын машинд төлбөр авахыг систем хориглоно.
