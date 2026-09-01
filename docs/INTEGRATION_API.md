# Түншийн интеграцийн API (/api/v1)

tokI, Easy Wallet зэрэг гадаад төлбөрийн систем зогсоолын төлбөрийг өөрийн апп
дотроо төлүүлэхэд зориулсан B2B API. Төлбөр баталгаажмагц систем хаалтыг нээнэ.

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

### 4. POST /api/v1/payments/{payment_id}/confirm — баталгаажуулах

Body: `{"transaction_id": "TX123", "amount": 2000}`

- Дүн ±1₮-өөс зөрвөл 400 (хаалт нээгдэхгүй).
- Амжилттай → `{"status": "PAID"}` + систем session-ийг PAID болгож **хаалт нээнэ**,
  e-Barimt үүсгэнэ.
- Idempotent: давхар дуудахад 200 + PAID (алдаа өгөхгүй).

### 5. GET /api/v1/payments/{payment_id} — төлөв шалгах

`{"payment_id": "...", "status": "PENDING|PAID|CANCELLED", "amount": 2000, "paid_at": ...}`

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
