# Түншийн интеграцийн API (/api/v1)

tokI, Easy Wallet, MonPay, SocialPay зэрэг гадаад төлбөрийн систем зогсоолын төлбөрийг өөрийн апп
дотроо төлүүлэхэд зориулсан B2B API. Төлбөр баталгаажмагц систем хаалтыг нээнэ. **2026-11-03:**
хэрэглэгч автомат төлбөр (autoPay) сонгосон бүртгэлтэй машин гарах камерт уншигдмагц систем төлөх
дүнг wallet руу өөрөө илгээж, wallet батлахад хаалт шууд нээгдэнэ — §9.

> **e-Barimt-ыг ЗӨВХӨН EasyParking-ийн систем гаргана** (`receipt.ready` webhook ба
> `GET /payments/{id}`-ээр ДДТД/сугалаа; QR-ыг зөвхөн GET, баримт үүссэнээс 1 цагийн дотор). Түнш
> ЭНЭ төлбөрт өөрийн e-Barimt ГАРГАХГҮЙ — эс бөгөөс ТЕГ-т нэг борлуулалт 2 баримттай болно. Түншид
> өгөх бүрэн баримт: MonPay — `MonPay_EasyParking_API_v1.0.docx` (2026-10-15), SocialPay —
> `docs/SOCIALPAY_INTEGRATION.md`.
>
> **Прод дээрх одоогийн байдал (2026-10-15):** msgbill-ийн арга `CARD,CASH,QR,TRANSFER` — WALLET-гүй,
> тиймээс түншийн (`payment_method=WALLET`) төлбөрийн баримт PosAPI руу унаж `FAILED` (BLOCKED)
> болдог. **«Хэтэвч» (WALLET)-ийг msgbill-д ГЛОБАЛААР БҮҮ асаа:** баримтаа өөрөө гаргадаг түншийн
> төлбөрт ч систем баримт гаргаж ТЕГ-т давхардуулна. Түншийн FAILED хуучин баримтыг бөөнөөр «Дахин
> үүсгэх»-ийн өмнө тухайн түнш өөрөө баримт гаргасан эсэхийг ТЕГ экспортоор тулгана.
>
> **2026-10-22:** түлхүүр тус бүрээр асаах тохиргоо ХЭРЭГЖСЭН — Тохиргоо → Холболт → Гадаад API →
> мөрийн «e-Barimt: EasyParking заавал гаргана» (`partner_keys.receipt_issuer = SYSTEM`). Тэр
> түлхүүрийн WALLET төлбөрт msgbill-ийн ерөнхий аргуудаас үл хамааран систем баримт гаргана; бусад
> түнш (хоосон) хуучнаараа. Дэлгэрэнгүй, TEST/PROD шалгах хуудас — §6.5–6.6.

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

Мөр бүрд мөн захиалга (`reservable`, `reservation_spots`, `reservation_free_now` — §8) болон
EV цэнэглэгч (`has_ev_charger`, `ev_chargers[]` {`name`, `power_kw`, `connector_count`, `status`,
`connectors[]` {`connector_id`, `status`}}, `ev_connectors_total`, `ev_connectors_free` — §7.1).

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
  "is_free": false, "status": "AWAITING_PAYMENT", "paid": false,
  "debt_amount": 3000, "debt_count": 1,      // ← өмнөх өр — amount_due-д БАГТААГҮЙ (2026-10-22)
  "debts": [{"debt_id": "…", "amount": 3000,
             "created_at": "2026-10-01T05:31:00",           // өр ҮҮССЭН цаг (UTC)
             "reason": "unpaid_exit", "reason_text": "Төлбөр төлөлгүй гарсан",
             "site_code": "KH", "site_name": "Кэй Эйч",
             "session_id": "…", "entry_time": "2026-10-01T02:00:00",   // өр үүсгэсэн айлчлал
             "exit_time": "2026-10-01T05:30:00", "duration_minutes": 210}]}]}
```

**Өр (2026-10-22):** `debts` нь энэ машины (ТУХАЙН зогсоолын байгууллагын зогсоолуудын) төлөгдөөгүй
өрүүд, хуучнаас шинэ рүү. Wallet өрийг одоогийн төлбөрөөс (`amount_due`) ТУСАД НЬ, өр бүр хэзээ,
аль зогсоолд үүссэнтэй нь харуулна; хамт төлүүлэх бол intent-д `include_debts: true` (§3). Өргүй бол
`debts: []`. `reason`: `unpaid_exit` (төлөлгүй гарсан), `auto_close` (хугацаа хэтэрч авто хаагдсан),
`night_close`, `shift_close`, `manual`, `admin_remove`, `log_exit` — харуулахдаа `reason_text`.
Айлчлалгүй (гараар бүртгэсэн) өрт `session_id`/`entry_time`/`exit_time` нь `null`.

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
`parking_amount`, `debt_amount`, `debt_count` болон **`debts`** (энэ intent-д багтсан өр бүр — §2-ийн
хэлбэр, үүссэн огноотой; 2026-10-22); confirm нь өрүүдийг PAID болгоно. Өр 3+ (анхдагч
`block_exit_debt_count`) машиныг зөвхөн зогсолтын төлбөрөөр гарц НЭЭГДЭХГҮЙ. Хүлээгдэж буй intent-ийг
давтан буцаахад ч ижил задаргаа (`parking_amount`, `debt_amount`, `debts`) ирнэ.

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

`{"payment_id": "...", "status": "PENDING|PAID|CANCELLED", "amount": 2000, "paid_at": ...,
"parking_amount": 1000, "debt_amount": 1000, "debt_count": 1, "debts": [...],
"ebarimt": {...}|null, "ebarimts": [...]}`

`parking_amount`/`debts` — төлбөрийн задаргаа (зогсолт ба өр тус бүр, огноотой; 2026-10-22).
`ebarimts` — БҮХ баримт (§6). Confirm-ийн хариу мөн ижил талбартай.

### 6. e-Barimt (НӨАТ-ын баримт) — 2026-09-13; 2026-10-22 шинэчилсэн

**Товч:** баримтыг EasyParking гаргана → түнш **(1)** `receipt.ready` webhook-оор ДДТД/сугалааг
авна, **(2)** `GET /payments/{id}`-ээр QR-тай бүрэн мэдээллийг (`ebarimts[]` — бүх баримт) авна,
**(3)** хэрэглэгчдээ харуулна. Түнш ЭНЭ төлбөрт өөрөө e-Barimt гаргахгүй.

| Алхам | Хаанаас | Юу ирэх |
|---|---|---|
| confirm-ийн хариу | `POST /payments/{id}/confirm` | `ebarimts[]` ихэвчлэн `status: PENDING`, `ddtd: null` (баримт хэдхэн секундэд) |
| баримт гармагц | webhook `receipt.ready` (баримт тус бүрд нэг) | `ddtd`, `lottery`, `amount`, `vat_amount`, `kind` (PARKING/DEBT/RESERVATION), `debt_id` |
| QR | `GET /payments/{id}` → `ebarimts[i].qr_data` | QR текст. Санах ойд 1ц; түүнээс хойш msgbill-ийн баримтын QR-ыг сервер msgbill-ээс НӨХӨЖ татна (≤2.5с) |
| webhook-гүй түнш | confirm-оос 2–3с-ийн дараа, дараа нь 5–10с тутам 1 мин хүртэл `GET` | дээрхтэй ижил |

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
  "qr_data": "…",                                 // QR — SENT + 1ц түр санах ойд байвал (бусад үед null)
  "created_at": "2026-09-13T10:20:00"
}
```

Түншийн confirm баримтыг ХҮЛЭЭХГҮЙ (баримт outbox ажилтнаар хэдхэн секундэд үүснэ) — тиймээс
confirm-ийн хариунд `ebarimt.status` ихэвчлэн `PENDING`, `ddtd: null`. Эцсийн ДДТД-г
`receipt.ready` webhook-оор эсвэл 2–3 секундын дараа `GET /payments/{id}`-ээр авна.
`include_debts=true` үед нэг төлбөрт хэд хэдэн баримт (зогсолт + өр тус бүр) үүснэ. `ebarimt` талбар
(хуучин, хэвээр) хамгийн сүүлд үүссэн НЭГийг л харуулдаг тул **`ebarimts` массивыг ашиглана**
(2026-10-22) — баримт бүр:

```json
"ebarimts": [
  {"receipt_id": "…", "kind": "PARKING", "debt_id": null, "ddtd": "0301…", "lottery": "SO 63177600",
   "amount": 1000, "vat_amount": 91, "status": "SENT", "provider": "MSGBILL",
   "receiver_type": "CITIZEN", "error": null, "qr_data": "…", "created_at": "…"},
  {"receipt_id": "…", "kind": "DEBT", "debt_id": "<§2-ийн debts[].debt_id>", "ddtd": "0301…", …}]
```

`kind`: `PARKING` (зогсолт), `DEBT` (өр — `debt_id`-аар §2/§3-ийн `debts[]`-тэй холбоно),
`RESERVATION` (урьдчилсан захиалга, §8). Цуцлагдсан баримт жагсаалтад орохгүй.
QR (`qr_data`) — ЗӨВХӨН `GET /payments/{id}` (+ confirm-ийн хариу, кэштэй бол); `payment.paid`-аас
`qr_data`-г хасдаг, `receipt.ready`-д байхгүй. QR-ыг DB-д хадгалдаггүй (ТЕГ шаардлага): санах ойд
1 цаг, дараа нь msgbill-ийн баримтынхыг сервер msgbill-ээс дахин татаж өгнө (2026-10-22); QPay/PosAPI
сувгийн баримтын QR 1 цагийн дараа `null`.

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
 "ddtd": "0301…", "lottery": "SO 63177600", "amount": "2000.00", "vat_amount": "182.00",
 "kind": "PARKING", "debt_id": null, "status": "SENT"}
```

`receipt.ready`-ийн `kind`/`debt_id`/`vat_amount` — 2026-10-22-нд нэмэгдсэн (`amount`, `vat_amount`
нь ТЕКСТ — хуучин хэлбэрийг эвдэхгүйн тулд). `payment.paid` мөн `parking_amount`, `debt_amount`,
`debt_count`, `debts[]`-тэй.

- Амжилт = HTTP 2xx (body-г шалгахгүй). Хугацаа 10с.
- Амжилтгүй бол давтана: 1, 2, 4, 8, 16, 32 мин, дараа нь цаг тутам — нийт 24 оролдлого (~18 цаг),
  дараа нь `UNKNOWN` (админ шалгана). At-least-once — `event_id`-аар давхардлыг ялгана.
- `receipt.ready` нь `payment.paid`-ээс ӨМНӨ ирж болно (тусдаа дараалал) — `payment_id`-аар холбоно.
- Гарын үсэг (HMAC) БАЙХГҮЙ: түнш event-д итгэхээсээ өмнө `GET /payments/{payment_id}`-ээр
  өөрийн түлхүүрээр баталгаажуулах, эсвэл манай гадагшаа IP-г (прод `202.21.117.178`, тест
  `152.42.235.199`) зөвшөөрөх жагсаалтад оруулна.
- «Турших» товч `payment.paid.test` event-ийг (`"test": true`) сүүлийн бодит төлбөрийн өгөгдлөөр
  (эсвэл жишээгээр) нэг удаа илгээж HTTP код/хариуг харуулна (давтахгүй). Бодит event-ээс ЯЛГААТАЙ:
  `event_id` талбар, `X-Parking-Event-Id`/`Idempotency-Key` толгойгүй, хугацаа 6с, `ebarimt.qr_data`
  байж болно. Машин бүртгэлийн «Турших» нь format-аас үл хамааран зөвхөн
  `{"data":{"plateNo","enteredDate"}}` илгээнэ (`Idempotency-Key: test-…`).

#### 6.5 Баримт хэн гаргах — түлхүүр тус бүрээр (2026-10-22)

| `receipt_issuer` (Тохиргоо → Холболт → Гадаад API → мөрийн «e-Barimt») | Зан төлөв |
|---|---|
| хоосон (анхдагч) | msgbill-ийн ерөнхий «аргууд»-д `WALLET` байвал msgbill, үгүй бол PosAPI. **Прод дээр WALLET алга → баримт `FAILED`** |
| `SYSTEM` — «EasyParking заавал гаргана» | Энэ түлхүүрийн WALLET төлбөр бүрд msgbill-ээр (зогсоолын түрээслэгчийн эсвэл ерөнхий данс) баримт гаргана — ерөнхий аргуудыг өөрчлөхгүй. MonPay, SocialPay-д |

Баримтаа ӨӨРӨӨ гаргадаг түншид `SYSTEM` бүү тавь (ТЕГ давхардал). Тохиргоог зөвхөн бүх зогсоолын
ADMIN/SUPER_ADMIN өөрчилнө, аудит логт (`PARTNER_RECEIPT_ISSUER_SET`) үлдэнэ.

#### 6.6 Шалгалт 2026-10-22 (TEST 152.42.235.199) ба PROD-д гарах шалгах хуудас

TEST DB-ээр: хэтэвчийн PAID төлбөр 14 (socialpay 11, mobifinance 2 — 10-22, ardwallet 1) — **14-үүлээ
e-Barimt `FAILED`** (`MerchantMissing`: TEST түрээслэгч «ИйзиПаркинг ХХК»-д e-Barimt мерчант/ТТД
алга; mock баримт ч гарахгүй). Мөн **нэг ч түлхүүрт webhook URL тохируулаагүй** — `payment.paid`/
`receipt.ready` нэг ч удаа илгээгдээгүй (PARTNER job 0). Өөрөөр хэлбэл түншүүд TEST дээр ДДТД хэзээ ч
хүлээн аваагүй — API-ийн алдаа биш, тохиргооны дутуу.

| # | Ажил | Хаана |
|---|---|---|
| 1 | Түншийн webhook URL-ыг түлхүүрт тавих (төлбөр батлагдахаас ӨМНӨ) — эс бөгөөс `receipt.ready` ирэхгүй | TEST, PROD |
| 2 | TEST: `PARKING_EBARIMT_MOCK_RECEIPTS=true` (асаалттай) + туршилтын зогсоолын түрээслэгчид туршилтын ТТД/мерчант (эсвэл түрээслэгчгүй ZZ- зогсоол) | TEST |
| 3 | Түлхүүрт «e-Barimt: EasyParking заавал гаргана» (`SYSTEM`) — MonPay (`mobifinance`), `socialpay` | TEST, PROD |
| 4 | PROD: msgbill түлхүүр (түрээслэгчийн эсвэл ерөнхий) идэвхтэй эсэх; ерөнхий аргуудад WALLET-ийг НЭМЭХГҮЙ | PROD |
| 5 | Хуучин FAILED хэтэвчийн баримтыг «Дахин үүсгэх»-ийн өмнө түнш өөрөө баримт гаргаагүйг ТЕГ экспортоор тулгах | PROD |
| 6 | Нэг бодит төлбөрөөр: confirm → `receipt.ready` → `GET` → `ebarimts[].qr_data` | PROD |

### 7. Машин бүртгэл, bill, байршил, орох/гарах мэдэгдэл (SocialPay 2026-10-14; MonPay г.м 2026-10-15)

`vehicles` эрхтэй түлхүүрээр. Хоёр ижил зам — ЯГ ИЖИЛ handler, хариу, эрх, хязгаар:

| Түншээс үл хамаарах (MonPay г.м) | SocialPay (spec) | |
|---|---|---|
| `POST /api/v1/vehicles/register` | `POST /api/v1/socialpay/register` | `{"plateNo","userId","autoPay"?}` → `{"Success": true, "Message": "OK"\|"ALREADY_REGISTERED"}` (`autoPay` илгээсэн бол + `data` — §9) |
| `POST /api/v1/vehicles/autopay` | `POST /api/v1/socialpay/autopay` | `{"plateNo","userId","autoPay": true\|false}` — гарах хаалтан дээрх автомат төлбөрийг асаах/унтраах (§9) |
| `POST /api/v1/vehicles/delete` | `POST /api/v1/socialpay/delete` | → `OK` \| `NOT_FOUND` (Success=true) |
| `POST /api/v1/vehicles/bill` | `POST /api/v1/socialpay/bill` | бүртгүүлсэн хэрэглэгчийн машины одоогийн зогсолт/үнэгүй хугацаа/төлөх дүн + өр бүр огноотой (`debts[]`: `debtId`, `amount`, `createdAt`, `reason`, `reasonText`, `siteCode`, `siteName`, `enteredDate`, `exitedDate`, `minutesParked`; цаг `…Z`) |
| `GET\|POST /api/v1/parkings` | `GET\|POST /api/v1/socialpay/parkings` | гадна зогсоолууд, координат, `freeMinutes`, дүүргэлт (`capacity/occupied/free`), захиалга: `reservable`, `reservationSpots`, `reservationFreeNow`; EV цэнэглэгч: `hasEvCharger`, `evChargers[]`, `evConnectorsTotal`, `evConnectorsFree` (§7.1) (2026-10-22; `vehicles` эрх шаардахгүй) |

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

### 7.1 Газрын зураг: EV цэнэглэгч — 2026-10-22

`GET|POST /api/v1/parkings` (+ `/api/v1/socialpay/parkings`) мөр бүрд — зураг дээр цэнэглэгчтэй
зогсоолыг ялгаж (`hasEvCharger`), цэнэглэгч бүрийн нэр, чадал (кВт), бууцны (хошууны) тоо, амьд
төлвийг харуулна:

```json
{"siteCode": "NOMADS", "name": "Номадс", "latitude": 47.91, "longitude": 106.92, "free": 12,
 "hasEvCharger": true,
 "evChargers": [
   {"name": "DC #1", "powerKw": 40, "connectorCount": 2, "status": "AVAILABLE",
    "connectors": [{"connectorId": 1, "status": "CHARGING"}, {"connectorId": 2, "status": "AVAILABLE"}]},
   {"name": "DC #2", "powerKw": 40, "connectorCount": 2, "status": "OFFLINE",
    "connectors": [{"connectorId": 1, "status": "OFFLINE"}, {"connectorId": 2, "status": "OFFLINE"}]}
 ],
 "evConnectorsTotal": 4, "evConnectorsFree": 1}
```

`/api/v1/sites` мөн адил snake_case: `has_ev_charger`, `ev_chargers[]` {`name`, `power_kw`,
`connector_count`, `status`, `connectors[]` {`connector_id`, `status`}}, `ev_connectors_total`,
`ev_connectors_free`.

| Талбар | Утга |
|---|---|
| `hasEvCharger` | идэвхтэй цэнэглэгчтэй эсэх. Цэнэглэгчгүй зогсоолд `false`, `evChargers: []`, `evConnectorsTotal: 0`, `evConnectorsFree: 0` |
| `evChargers[].powerKw` | нэрлэсэн чадал, кВт (бутархай байж болно, ж: 7.4); тохируулаагүй бол `null` |
| `evChargers[].connectorCount` | бууцны тоо (= `connectors[]`-ийн урт, дугаар 1..N) |
| `evChargers[].status` | цэнэглэгчийн нэгтгэсэн төлөв — бууцнуудын хамгийн «сайн» нь: `AVAILABLE` > `CHARGING` > `UNKNOWN` > `FAULTED` > `OFFLINE` |
| `evConnectorsTotal` | зогсоолын бүх бууцны тоо |
| `evConnectorsFree` | одоо `AVAILABLE` бууцны тоо; амьд төлөв мэдэгдэхгүй үед `null` («0 сул» гэж бүү харуул) |

**Бууцны төлөв** (OCPP 1.6 → энгийн):

| `status` | OCPP төлөв | Утга |
|---|---|---|
| `AVAILABLE` | `Available` | сул, залгаж болно |
| `CHARGING` | `Preparing`, `Charging`, `SuspendedEV`, `SuspendedEVSE`, `Finishing`, `Reserved` | эзэлсэн (машин залгаастай / цэнэглэж буй / дуусгаж буй / захиалгатай) |
| `FAULTED` | `Faulted` | гэмтэлтэй |
| `OFFLINE` | `Unavailable`; эсвэл цэнэглэгч сервертэй холбогдоогүй | ашиглах боломжгүй |
| `UNKNOWN` | бусад / төлөв ирээгүй / цэнэглэгчийн сервер түр хариу өгөөгүй | мэдэгдэхгүй |

Цэнэглэгч бүхэлдээ (OCPP connector 0) `Faulted`/`Unavailable` бол сул бууцнууд мөн
`FAULTED`/`OFFLINE` гэж харагдана.

Тэмдэглэл: зөвхөн гадна зогсоол (доторх зогсоолд бүртгэсэн цэнэглэгч газрын зурагт гарахгүй);
идэвхгүй цэнэглэгч гарахгүй. Амьд төлөв ~30с кэштэй — цэнэглэгчийн сервер (hub) унтарсан/удаан үед
хариу удаашрахгүй, алдаа өгөхгүй, зөвхөн `UNKNOWN` болно. Цэнэглэгчийн дотоод таних тэмдэг (cp_id,
QR түлхүүр) гадагш гардаггүй. Цэнэглэлт эхлүүлэх нь цэнэглэгч дээрх QR-ээр (жолоочийн хуудас).

### 8. Урьдчилсан захиалга (reservation) — 2026-10-22

Хэрэглэгч wallet-аас зогсоолд байр урьдчилж захиалж, төлбөрийг урьдчилж төлнө. Баталгаажмагц
зогсоолын **харуулын дэлгэц** (Касс — «Урьдчилсан захиалга» самбар + мэдэгдэл, Хяналтын самбар)-д
шууд мэдэгдэл очно. Машиныг дугаараар нь таньдаг (QR/код шаардлагагүй): захиалгын цонхонд орох камерт
уншигдмагц захиалга сешнтэй холбогдож, **урьдчилж төлсөн дүн тэр зогсолтын төлбөрөөс хасагдана**.
Захиалсан хугацаандаа гарвал хаалт төлбөргүй нээгдэнэ; илүү зогссон бол зөвхөн зөрүүг ердийн
тарифаар (QR/касс/wallet) төлнө. Загвар: SpotHero/ParkWhiz/JustPark-ийн «урьдчилж төлсөн цонх +
дугаараар нэвтрэх + үнэгүй цуцлах хугацаа».

**Зогсоолд асаах:** Тохиргоо → Төлбөрийн дүрэм → «Урьдчилсан захиалга (wallet)» — зогсоол бүрээр:
`enabled`, `spots` (захиалгад зориулсан байр), `min_minutes` (60), `max_minutes` (720),
`max_days_ahead` (7), `min_lead_minutes` (0), `hold_minutes` (10), `arrive_early_minutes` (15),
`cancel_free_minutes` (30). Анхдагч УНТРААЛТТАЙ. Доторх (nested) зогсоолд захиалга авахгүй.

**Үнэ:** систем зогсоолын тарифаар захиалсан цонхны (start → end) дүнг бодно — сешний төлбөртэй ЯГ
ИЖИЛ томъёо. Түнш дүн бодохгүй.

**Төлөвүүд:** `PENDING_PAYMENT` (байрыг `hold_minutes` барина) → `CONFIRMED` → `CHECKED_IN` (машин
орсон) → `COMPLETED` (гарсан). Мөн `EXPIRED` (төлбөр хугацаандаа батлагдаагүй), `CANCELLED`,
`NO_SHOW` (цонх дуусталаа ирээгүй — буцаалтгүй).

| Endpoint | Эрх | |
|---|---|---|
| `GET /api/v1/reservations/availability?site_code=&start_time=&duration_minutes=` | read | Сул байр + үнэ (байр барихгүй) |
| `POST /api/v1/reservations` | pay | Үүсгэх → `PENDING_PAYMENT` (201). `transaction_id`+`amount`-тай бол шууд `CONFIRMED` (нэг алхам) |
| `POST /api/v1/reservations/{id}/confirm` | pay | `{"transaction_id", "amount"?}` — мөнгө татсаны дараа |
| `POST /api/v1/reservations/{id}/cancel` | pay | `{"reason"?, "accept_no_refund"?}` → `refund_amount` |
| `GET /api/v1/reservations/{id}` | read | Төлөв + `ebarimt`/`ebarimts` |
| `GET /api/v1/reservations?plate=&user_id=&external_ref=&status=` | read | Энэ түлхүүрийн захиалгууд (≤200) |

**Үүсгэх (create):**

```json
POST /api/v1/reservations
{"site_code": "NOMADS", "plate_number": "1234УБА",
 "start_time": "2026-10-23T10:00:00+08:00",   // цагийн бүс ЗААВАЛ (Z эсвэл +08:00)
 "duration_minutes": 120,                      // эсвэл "end_time"
 "external_ref": "MP-ORDER-778",               // зөвлөнө: давтан create-д ижил захиалга (идемпотент)
 "user_id": "100264", "phone": "99112233",     // заавал биш
 "transaction_id": "MP-TX-1", "amount": 2000}  // заавал биш — нэг алхмаар батлах үед
→ 201
{"reservation_id": "…", "reservation_no": "R7K3F9", "status": "PENDING_PAYMENT",
 "plate_number": "1234УБА", "site_code": "NOMADS", "site_name": "Номадс", "address": "…",
 "start_time": "2026-10-23T02:00:00.000Z", "end_time": "2026-10-23T04:00:00.000Z",
 "duration_minutes": 120, "amount": 2000.0, "vat_amount": 182.0, "currency": "MNT",
 "payment_id": "…", "transaction_id": null, "external_ref": "MP-ORDER-778", "user_id": "100264",
 "arrive_from": "2026-10-23T01:45:00.000Z", "free_cancel_until": "2026-10-23T01:30:00.000Z",
 "cancellable": true, "refund_if_cancelled_now": 0.0,
 "created_at": "…", "hold_expires_at": "2026-10-22T07:40:00.000Z",
 "confirmed_at": null, "checked_in_at": null, "completed_at": null, "cancelled_at": null,
 "cancel_reason": null, "refund_amount": null, "session_id": null,
 "ebarimt": null, "ebarimts": []}        // баримт confirm-ийн дараа үүснэ (kind: RESERVATION)
```

Цаг бүгд UTC «…Z» (мс). `reservation_no` — харуулд хэлэх богино код.

**Хоёр урсгал:** (а) **2 алхам (зөвлөнө):** availability → create (байр барина) → хэрэглэгчээс
`amount` татах → confirm. (б) **1 алхам:** availability-ийн `amount`-ыг татаад create-д
`transaction_id`+`amount`-тай илгээнэ; амжилтгүй (байр дууссан г.м) бол мөнгийг буцаах эрсдэлтэй.

**Цуцлалт:** `PENDING_PAYMENT` — ямагт (мөнгө татаагүй). `CONFIRMED` + `free_cancel_until`-аас өмнө
— **бүтэн буцаалт** (`refund_amount` = `amount`; систем e-Barimt-ыг цуцална; мөнгийг ТҮНШ буцаана).
Түүнээс хойш цонх дуусах хүртэл — 409 `NO_REFUND_WINDOW`; `accept_no_refund: true` илгээвэл
буцаалтгүй цуцалж байрыг чөлөөлнө. Ирсэн/дууссан → 409 `NOT_CANCELLABLE`. Давтан цуцлалт → 200
(идемпотент). Хэсэгчилсэн буцаалт байхгүй.

**Машин ирэх:** `arrive_from` (эхлэхээс `arrive_early_minutes` эрт) → `end_time` хооронд орох камерт
ЯГ ЭНЭ дугаар уншигдвал `CHECKED_IN`. «Зөвхөн бүртгэлтэй» зогсоолд ч захиалгатай машин нэвтэрнэ. Эрт
ирсэн хугацаа энгийн тарифаар; захиалснаас богино зогсвол буцаалтгүй. Түнш confirm хийхэд машин аль
хэдийн (энэ цонхонд) орчихсон бол шууд холбогдоно.

**Алдаа** `{"detail": "<Монгол тайлбар>", "code": "<КОД>", …}`:

| HTTP | code | |
|---|---|---|
| 400 | `INVALID_PLATE` / `INVALID_USER_ID` / `NOT_SUPPORTED` / `NOTHING_TO_CHARGE` / `AMOUNT_MISMATCH` | дугаар (кирилл), доторх зогсоол, 0₮ цонх, дүн зөрсөн (`amount` талбартай) |
| 403 | `FORBIDDEN` | түлхүүр өөр зогсоолоор хязгаарлагдсан |
| 404 | `SITE_NOT_FOUND` / `NOT_FOUND` | |
| 409 | `RESERVATIONS_DISABLED` / `NO_AVAILABILITY` / `DUPLICATE_RESERVATION` / `EXTERNAL_REF_CONFLICT` | захиалга авдаггүй, байр алга, машин энэ цагт захиалгатай, external_ref өөр параметртэй |
| 409 | `ALREADY_CONFIRMED` / `RESERVATION_CANCELLED` / `HOLD_EXPIRED` / `NO_REFUND_WINDOW` / `NOT_CANCELLABLE` | confirm/cancel-ийн төлөв |
| 422 | `INVALID_TIME` / `INVALID_DURATION` / `INVALID_WINDOW` / `START_TOO_SOON` / `START_TOO_FAR` / `INVALID_REQUEST` | |

`HOLD_EXPIRED`: барих хугацаа дууссан ч байр сул хэвээр бол confirm хүлээн авагдана; байр дууссан
бол 409 — татсан мөнгийг хэрэглэгчид буцаана.

**Webhook** (төлбөрийн webhook URL руу, §6-тай ижил толгой/давталт): `reservation.confirmed`,
`reservation.checked_in`, `reservation.completed`, `reservation.cancelled`, `reservation.no_show` —
`{"event", "event_id", "reservation": {…дээрх объект…}}`. Захиалгын e-Barimt гармагц
`receipt.ready` (`kind: RESERVATION`) ирнэ.

**e-Barimt:** confirm-оор урьдчилсан төлбөрийн баримт гарна (`kind: RESERVATION`); илүү зогссон
зөрүүг гарахдаа төлөхөд тусдаа баримт. Бүтэн буцаалтад баримт цуцлагдана.

**Газрын зураг:** `GET /api/v1/parkings`-ийн мөр бүр `reservable` (захиалга авдаг эсэх),
`reservationSpots` (захиалгын байрны тоо), `reservationFreeNow` (ОДОО сул захиалгын байр) — `/api/v1/sites`
мөн `reservable`, `reservation_spots`, `reservation_free_now`. Wallet: зураг дээр зогсоол (`latitude`,
`longitude`), дүүргэлт (`free`/`capacity`), «Захиалах» товч (`reservable`) → сонгосон цонхонд
`availability` → `create`. Захиалгын байр нь ерөнхий багтаамжаас ТУСДАА (зогсоол дүүрэн ч захиалгын байр
сул байж болно — тэр байрыг харуул захиалгатай машинд үлдээдэг).

**Харуулын жагсаалт (ажилтан, JWT):** `GET /api/reservations?site_id=&scope=upcoming|today|all`.

**TEST дээр турших өгөгдөл:** `backend/tools/test_demo_sites.py` — продын зогсоолуудыг (нэр, хаяг,
координат, тариф, тохиргоо) TEST-д хуулж, дутуу координат/багтаамжийг зохиомлоор нөхөж, захиалга асааж,
зохиомол машинаар (note `TEST-DEMO`) дүүргэлт үүсгэнэ. Зөвхөн `PARKING_DEPLOY_ROLE=staging` дээр.

### 9. Автомат төлбөр гарах хаалтан дээр (autoPay) — 2026-11-03

Хэрэглэгч wallet дотроо «энэ машины зогсоолын төлбөрийг автоматаар төл» гэж сонгосон бол машин
гарах камерт уншигдмагц систем **төлөх дүн, дугаар, `payment_id`-тай хүсэлтийг wallet руу ӨӨРӨӨ
илгээнэ** — жолооч апп нээх, QR уншуулах шаардлагагүй. Wallet хэрэглэгчээс мөнгө татаад ердийн
**`POST /api/v1/payments/{payment_id}/confirm`** (§4) дуудахад хаалт **шууд** нээгдэнэ, e-Barimt-ыг
систем гаргана. Төлбөрийн урсгал (intent → confirm, өр, баримт, webhook) нь §3–§6-тай ЯГ ИЖИЛ;
ялгаа нь intent-ийг wallet биш **систем үүсгэж** wallet руу түлхдэг.

```
Машин орох  ─► vehicle.entry мэдэгдэл (§7, хуучнаараа)
Гарах камер ─► систем: intent (PENDING) үүсгэнэ ─► POST {webhook_url}  event=payment.requested
                                                   │
         wallet: хэрэглэгчээс amount татна ◄──────┘
           ├─ амжилттай → POST /api/v1/payments/{id}/confirm  → PAID, ХААЛТ НЭЭГДЭНЭ, receipt.ready
           └─ татаж чадаагүй → POST /api/v1/payments/{id}/cancel {"outcome":"NOT_CHARGED","reason":"INSUFFICIENT_FUNDS"}
                               → жолооч QR/кассаар төлнө
   60с-д хариу ирэхгүй → intent CANCELLED, QR/касс нээгдэнэ ─► POST {webhook_url} event=payment.request_expired
```

#### 9.1 Бэлтгэл (түлхүүр)

| Шаардлага | Хаана |
|---|---|
| `pay` **ба** `vehicles` эрх | Тохиргоо → Холболт → Гадаад API |
| Төлбөрийн **webhook URL** (`payment.paid`, `receipt.ready`-тэй ИЖИЛ URL) | мөн тэнд — «Webhook» |
| Зогсоол түлхүүрийн хүрээнд (зогсоол / түрээслэгч) | түлхүүр үүсгэхэд |

Эдгээрийн аль нэг дутуу байхад `autoPay: true` илгээвэл **409 `AUTOPAY_NOT_CONFIGURED`** (`Detail`-д
шалтгаан) — чимээгүй бүртгээд гарцад хэзээ ч хүсэлт явуулахгүй байх нөхцөл үүсэхгүй.

#### 9.2 autoPay асаах / унтраах

Бүртгэхдээ:

```json
POST /api/v1/vehicles/register
{"plateNo": "7886УКР", "userId": "100264", "autoPay": true}
→ 200 {"Success": true, "Message": "OK", "data": {"plateNo": "7886УКР", "userId": "100264", "autoPay": true}}
```

Бүртгэлтэй машинд дараа нь (эсвэл register-ийг `autoPay`-тай давтан дуудаж — `ALREADY_REGISTERED` +
`data`):

```json
POST /api/v1/vehicles/autopay
{"plateNo": "7886УКР", "userId": "100264", "autoPay": false}
→ 200 {"Success": true, "Message": "OK", "data": {"plateNo": "7886УКР", "userId": "100264", "autoPay": false}}
```

- `autoPay` нь JSON boolean (`true`/`false`) — `"true"`, `1` → 400 `INVALID_AUTO_PAY`.
- `autoPay` талбаргүй register — хариу **хуучин хэлбэрээрээ** (`data`-гүй), сонголт хөндөгдөхгүй.
- Бүртгэлгүй машинд `/vehicles/autopay` → 404 `NOT_REGISTERED`. Машиныг устгахад (`/vehicles/delete`)
  autoPay мөн дуусна. Анхдагч — **унтраалттай** (хуучин бүх бүртгэл).
- `/vehicles/bill`-ийн `data`-д: `autoPay` (энэ хэрэглэгчийн сонголт), `autoPayStatus`
  (одоогийн intent автомат хүсэлт бол `PENDING|SENDING|SENT`, үгүй бол `null`), `autoPayExpiresAt` (`…Z`).

#### 9.3 `payment.requested` — систем → wallet

`POST {webhook_url}`; толгой: `Content-Type: application/json`, `X-Parking-Partner: <түншийн нэр>`,
`X-Parking-Event: payment.requested`, `X-Parking-Event-Id` = `Idempotency-Key` = `request_id`
(давталтад ИЖИЛ — wallet давхар татахгүйн тулд энэ түлхүүрээр ялгана).

```json
{"event": "payment.requested",
 "event_id": "3d9d…", "request_id": "3d9d…",
 "payment_id": "261e…",                 // ← confirm/cancel-д ЭНЭ id
 "session_id": "…",
 "plate": "7886УКР", "user_id": "100264",   // autoPay асаасан хэрэглэгч
 "site_code": "NOMADS", "site_name": "Номадс",
 "entry_time": "2026-11-03T02:10:00", "exit_read_at": "2026-11-03T03:50:12",   // UTC, бүсгүй (§2-тай ижил)
 "duration_minutes": 100,
 "amount": 4000.0,                      // ← хэрэглэгчээс ЭНЭ дүнг татна (өр багтсан)
 "vat_amount": 364.0, "currency": "MNT",
 "parking_amount": 1000.0, "debt_amount": 3000.0, "debt_count": 3,
 "debts": [{"debt_id": "…", "amount": 1000.0, "created_at": "…", "reason": "unpaid_exit",
            "reason_text": "Төлбөр төлөлгүй гарсан", "site_code": "…", "site_name": "…",
            "session_id": "…", "entry_time": "…", "exit_time": "…", "duration_minutes": 210}],
 "expires_at": "2026-11-03T03:51:12", "expires_in_seconds": 60,
 "confirm_url": "https://site.easy-parking.mn/api/v1/payments/261e…/confirm",
 "cancel_url": "https://site.easy-parking.mn/api/v1/payments/261e…/cancel"}
```

- **Хариу**: HTTP 2xx = хүлээж авсан (body-д `{"Success": false}` / `{"success": false}` ирвэл
  амжилтгүйд тооцно). Мөнгө татах, confirm хийхийг **асинхрон**оор хийнэ — webhook-ийн хариуг
  хүлээлгэхгүй (5с timeout).
- Хүргэгдээгүй бол 2с, 4с, 8с, 15с, 20с давтана — гэхдээ зөвхөн `expires_at` хүртэл.
- **Өр**: QR-тэй ижил — тухайн байгууллагын зогсоолуудын төлөгдөөгүй өр `amount`-д багтана
  (`include_debts=true`). Төлөгдвөл өрийн «3+ өр» дүрэм арилж хаалт нээгдэнэ. Хэрэглэгчид
  `parking_amount` ба `debts`-ийг тусад нь харуулахыг зөвлөнө.

#### 9.4 Wallet-ийн хариу үйлдэл

| Үр дүн | Дуудах | Систем |
|---|---|---|
| Мөнгө татсан | `POST /api/v1/payments/{payment_id}/confirm` `{"transaction_id": "…", "amount": 4000}` | PAID; машин гарцан дээр (тэр эгнээнд өөр машин уншигдаагүй) бол **хаалт шууд нээгдэнэ** (хариуны `barrier_opened`); e-Barimt → `receipt.ready` |
| Татаж чадаагүй (үлдэгдэл хүрэлцээгүй, хэрэглэгч автомат төлбөрөө унтраасан г.м) | `POST /api/v1/payments/{payment_id}/cancel` `{"outcome": "NOT_CHARGED", "reason": "INSUFFICIENT_FUNDS"}` | intent CANCELLED → жолооч QR/кассаар. Тэр айлчлалд wallet-ийг **дахин нэхэхгүй** |

`reason` заавал биш — 1–60 тэмдэгт код (`INSUFFICIENT_FUNDS`, `USER_DECLINED`, `AUTOPAY_DISABLED`,
`LIMIT_EXCEEDED` г.м; том үсэг болгож хадгална), аудит болон админы түүхэд харагдана. Буруу хэлбэр → 422.

`GET /api/v1/payments/{payment_id}`-ийн хариунд `autopay`: `{"request_id", "status", "expires_at",
"delivered_at", "decline_reason"}` (автомат хүсэлт биш бол `null`). `status`: `PENDING` (илгээх) →
`SENDING` → `SENT` (хүргэгдсэн) → `PAID` | `DECLINED` | `EXPIRED` (хүргэгдсэн ч хариугүй) |
`FAILED` (хугацаандаа хүргэж чадаагүй).

#### 9.5 Хугацаа (60с) ба `payment.request_expired`

Хүсэлт идэвхтэй байх хооронд тухайн зогсолтод QR нэхэмжлэл/касс **түр хаагдана** (давхар татахаас
сэргийлнэ; QR хуудас «…-аар автомат төлбөр хийгдэж байна» гэж харуулна). `expires_at` хүртэл
confirm/cancel ирэхгүй бол систем intent-ийг **CANCELLED** болгож QR/кассыг нээнэ, wallet руу:

```json
{"event": "payment.request_expired", "event_id": "3d9d…:expired", "request_id": "3d9d…",
 "payment_id": "261e…", "session_id": "…", "plate": "7886УКР", "user_id": "100264",
 "status": "CANCELLED", "reason": "TIMEOUT", "expired_at": "2026-11-03T03:51:12"}
```

- Энэ мэдэгдэл ирсний дараа хэрэглэгчээс **бүү тат**.
- Хэрэв аль хэдийн татчихсан бол **confirm-ыг ЗААВАЛ дуудна** — систем хүлээн авч бүртгэнэ (машин
  гарцан дээр хэвээр бол хаалт нээгдэнэ). Энэ хооронд жолооч өөрөөр төлсөн бол илүү төлөлт
  `overpaid_amount`-оор тэмдэглэгдэж санхүү буцаалт хийнэ — мөнгө хэзээ ч чимээгүй алга болохгүй.
- Мэдэгдлийг 2xx ирэх хүртэл эхний оролдлогоос хойш 15с, 1м, 5м-д давтана (нийт 4 оролдлого), дараа нь илгээхээ зогсооно.

#### 9.6 Дүрэм (хэзээ хүсэлт ЯВАХГҮЙ)

| Тохиолдол | Шийдвэр |
|---|---|
| autoPay унтраалттай / машин бүртгэлгүй | Явахгүй (жолооч QR/касс эсвэл wallet-ийн гар урсгал §3) |
| Камер дугаарыг ойролцоогоор уншсан (OCR тааж тохоосон сешн) | Явахгүй — гарцад байгаа машин тэр хэрэглэгчийнх биш байж болно |
| Үнэгүй хугацаанд / гэрээт / аль хэдийн төлсөн (grace дотор) | Явахгүй — хаалт шууд нээгдэнэ |
| Тэр зогсолтод өөр идэвхтэй төлбөр (QR нэхэмжлэл, POS) явж байна | Явахгүй |
| Давтан гарах уншилт (машин хаалтан дээр хэд хэд уншигдсан) | Нэг л хүсэлт |
| Wallet татгалзсан / хугацаа дууссан | Тэр айлчлалд дахин явахгүй |
| Төлсөн ч grace хэтэрч дахин гарцад ирсэн | Зөрүүд **шинэ** хүсэлт явна |
| Нэг машиныг хэд хэдэн wallet / хэрэглэгч autoPay-тай бүртгүүлсэн | **Хамгийн сүүлд autoPay сонгосон** нэг рүү л — хоёр wallet-аас зэрэг татахгүй |
| Түлхүүр хаагдсан, `pay`/`vehicles` эрхгүй, webhook URL хоосон, зогсоол хүрээнээс гадуур | Тэр түнш рүү явахгүй |
| Орох уншилтгүй машин (суурь хураамж) | Явна — суурь хураамжийн дүнгээр |

#### 9.7 Сервер талын тохиргоо

`.env`: `PARKING_PARTNER_AUTOPAY_ENABLED=true` (анхдагч), `PARKING_PARTNER_AUTOPAY_TIMEOUT_SEC=60`
(15–600). `PARKING_PARTNER_VEHICLE_NOTIFY=false` (прод dump-аас сэргээсэн TEST) үед автомат хүсэлт мөн
**илгээгдэхгүй** — жинхэнэ wallet-аас мөнгө татуулахгүй. Хугацаа дуусгалт ямагт ажиллана (intent
гацахгүй). `PARKING_PUBLIC_BASE_URL` нь `confirm_url`/`cancel_url`-д орно.

Админ: Тохиргоо → Холболт → Гадаад API — түлхүүр бүрд autoPay-тай машины тоо, хариу хүлээж буй
хүсэлт, бэлэн биш бол шалтгаан; API `GET /api/admin/partner-keys/{id}/autopay-requests`
(`?status=&plate=`), `GET /api/admin/partner-keys/{id}/vehicles`-д `auto_pay`. Аудит:
`PARTNER_VEHICLE_AUTOPAY`, `WALLET_AUTOPAY_REQUEST`, `WALLET_AUTOPAY_EXPIRED`, `WALLET_PAID`,
`WALLET_INTENT_CANCELLED`.

## Жишээ урсгал (curl)

```bash
K="X-API-Key: ТҮЛХҮҮР"
B="https://site.easy-parking.mn"

curl -H "$K" "$B/api/v1/sessions?plate=1234УБА"
curl -H "$K" -X POST "$B/api/v1/payments" -H "Content-Type: application/json" \
     -d '{"session_id":"SID"}'
curl -H "$K" -X POST "$B/api/v1/payments/PID/confirm" -H "Content-Type: application/json" \
     -d '{"transaction_id":"TX1","amount":2000}'

# Автомат төлбөр (§9): бүртгэхдээ асаана — гарах камерт уншигдмагц payment.requested ирнэ
curl -H "$K" -X POST "$B/api/v1/vehicles/register" -H "Content-Type: application/json" \
     -d '{"plateNo":"7886УКР","userId":"100264","autoPay":true}'
curl -H "$K" -X POST "$B/api/v1/payments/PID/cancel" -H "Content-Type: application/json" \
     -d '{"outcome":"NOT_CHARGED","reason":"INSUFFICIENT_FUNDS"}'
```

## POS терминалын зогсоол-харьяалал

PAX терминалыг **Тохиргоо → Төхөөрөмж**-д бүртгэнэ:
- Төрөл: `pax_terminal`, `device_key` = терминалын serial/ID.

POS апп асахдаа (оператор нэвтрэлттэй):
`GET /api/payments/pos/terminal/{terminal_id}` → өөрийн site_code/site_name +
тухайн зогсоолын төлбөр хүлээж буй машинууд. `pos/confirm`-д terminal_id
дамжуулбал өөр зогсоолын машинд төлбөр авахыг систем хориглоно.
