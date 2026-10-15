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

### GET|POST /api/payments/qpay/check/{payment_id}  (нэвтрэлт шаардахгүй)
Polling — 5 сек тутам дуудна (webhook ирээгүй тохиолдолд). GET (PAX апп, 2026-10-15) ба POST
(веб) ИЖИЛ — хоёулаа хадгалсан үр дүнг уншина, банк руу хүсэлт илгээхгүй. Хариу `PENDING` эсвэл
PAID үед **хэвлэх өгөгдөл хамт** (`receipt`, `print_data` — бүтэн жагсаалт PAX_POS_APP_GUIDE §3.5):

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
`{"session_id": "uuid", "customer_tin"?: "ААН ТТД", + гадна баримтын талбарууд (доор)}` —
кассын бэлэн мөнгөний төлбөр. Хаалт автоматаар нээгдэнэ, e-Barimt систем үүсгэнэ.
Хариу (2026-10-15-аас): `{ok, status, payment_id, amount, session_status, amount_due, …,
barrier_*}` + `pos/confirm`-той ИЖИЛ хэвлэх багц `ebarimt_id, lottery_code, qr_data, receipt,
print_data` (POS аппын хүсэлтэд msgbill баримтыг шууд илгээж ≤6с хүлээнэ — доор; веб касс
хүлээхгүй).

### POST /api/payments/transfer  (pay_transfer эрхтэй)
`{"session_id": "uuid", "customer_tin"?, + гадна баримтын талбарууд}` — дансаар (шилжүүлгээр)
төлбөр баталгаажуулах (online operator). e-Barimt msgbill-ээр BANK_TRANSFER аргаар үүснэ.

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
**QR код болгон хэвлэнэ** — хэрэглэгч ebarimt апп-аар уншуулна.

**Шууд илгээлт (2026-10-15):** `pos/confirm` ба `cash` мөнгийг commit хийсний ДАРАА ЭНЭ
төлбөрийн msgbill баримтыг outbox-ын ажлыг lease-ээр авч (worker давхар илгээхгүй) шууд
илгээж, ДДТД + QR-ыг нийт **≤6 секунд** хүлээнэ (`PARKING_RECEIPT_INLINE_WAIT_SEC`, 0 =
унтраах). Хариулбал `receipt.ebarimt_status=SENT` + `ebarimt_id/lottery_code/qr_data`; үгүй бол
`PENDING` — апп `GET /api/payments/{id}/print`-ээр дахин татна (баримтыг ДАХИН илгээхгүй,
зөвхөн уншина; SENT msgbill баримтын QR санах ойд алга бол msgbill-ээс НЭГ удаа татна).
Давтан `pos/confirm` (ижил `transaction_id`) шинэ баримт үүсгэхгүй, илгээж буй баримтыг хүлээнэ.
Хариуны бүтэн талбар, дахин татах дүрэм: docs/PAX_POS_APP_GUIDE.md §3.5. Картаар (POS), бэлнээр,
дансаар төлсөн баримтыг систем **msgbill.mn eBarimt API**-аар (2026-08-19-өөс; тохируулаагүй
зогсоолд локал PosAPI 3.0), QR-аар (QPay) төлсөн баримтыг **QPay ebarimt_v3**-аар үүсгэнэ (§11).

**Гадна системд үүссэн баримт (сонголт):** терминал/банкны PosLink/өөр e-Barimt API
баримтыг АЛЬ ХЭДИЙН гаргасан бол давхар баримт үүсгэхгүйн тулд хүсэлтэд нэмж өгнө —
`pos/confirm`, `cash`, `transfer` гурвуулаа хүлээн авна:

```json
{
  "...": "дээрх талбарууд",
  "ebarimt_id": "0291002441060010972102300100459…",   // ДДТД (заавал — байвал систем үүсгэхгүй)
  "lottery_code": "XA 66306294",                        // сонголт
  "qr_data": "594649643…",                              // сонголт — ebarimt QR агуулга
  "ebarimt_provider": "TDB-POSLINK"                     // сонголт, ≤20 тэмдэгт, анхдагч TERMINAL
}
```
Хариунд ижил `ebarimt_id/lottery_code/qr_data` буцна; Ибаримт хуудсанд суваг = тухайн нэр.

**POS терминал (PAX апп) өөрөө баримт гаргадаг (2026-10-13; 10-14 бүх зогсоолд; 10-15 анхдагч
УНТРААЛТТАЙ):** Тохиргоо → Төлбөрийн дүрэм → «e-Barimt» бүлэг (`ebarimt_rules.pos_terminal_issues`
+ `pos_terminal_methods`). PAX аппын шинэ хувилбар баримтыг ЗӨВХӨН манай API-аас хэвлэдэг
(Ontime-гүй) тул ЕРӨНХИЙ анхдагч 2026-10-15-аас УНТРААЛТТАЙ; терминал баримтаа өөрөө хэвлэдэг нь
батлагдсан зогсоолд л асаана. Асаалттай зогсоолд POS аппын төлбөр (`pos/confirm` — үргэлж; `cash` болон
`compensations/{id}/pay` — `X-App-Version` толгойтой, эсвэл кассчин нь POS аппын хэрэглэгч: role
POS / `pos_terminal_users` / POS аппаар ≥20 карт — зогсоолоос үл хамааран; `payments.source=POSAPP`)
ДДТД-гүй ирвэл систем msgbill баримт ГАРГАХГҮЙ: `vat_receipts.status=EXTERNAL`,
`provider=POS-TERMINAL`, FinancialJob үүсэхгүй. ААН/ТТД-тэй баримтыг систем гаргасаар.
EXTERNAL-ийг retry/бөөн нөхөлт/цуцлалт хөндөхгүй (retry → 409);
`retry-ebarimt {"reissue_external": true, "no_terminal_receipt": true}` (терминал хэвлээгүй нь
батлагдсан) л системийн баримт болгоно. `{payment_id}/ebarimt` (холбох) EXTERNAL мөрийг SENT болгоно.

### POST /api/payments/{payment_id}/ebarimt  (OPERATOR+ / vat)
Төлөгдсөн төлбөрт **гадна системд үүссэн баримтыг ДАРАА нь холбох** (систем баримт
үүсгэж чадаагүй/амжилтгүй үед, эсвэл баримтыг өөр e-Barimt API-аар гаргасан үед):
`{"ebarimt_id": "ДДТД", "lottery_code"?, "qr_data"?, "ebarimt_provider"?, "ebarimt_ref"?}`
→ `{ok, ebarimt_id, provider, lottery}`. Идэвхтэй баримт аль хэдийн байвал **409** (эхлээд
`POST /api/payments/{payment_id}/cancel-ebarimt`).

### POST /api/payments/{payment_id}/cancel-ebarimt  (vat/reports)
Баримтыг сувгаар нь буцаана (QPay / msgbill / PosAPI); гадна сувгийн баримтад зөвхөн
манай бүртгэл CANCELLED болно. `{note?}` → `{ok, cancelled[], pending[], error}`.

### POST /api/payments/{payment_id}/retry-ebarimt  (vat/reports)
Амжилтгүй/цуцалсан баримтыг системээр дахин үүсгэнэ (мөнгө дахин авахгүй). EXTERNAL
(POS терминал гаргасан) баримттай төлбөрт **409**; `{"reissue_external": true}` +
(дүрэм энэ төлбөрт үйлчлэхгүй ЭСВЭЛ `"no_terminal_receipt": true` — терминал баримт
хэвлээгүйг баталгаажуулсан) үед л системийн баримт болгоно (AuditLog-д тэмдэглэнэ). POS терминалын давхардал
гэж ТЕГ тулгалтаар цуцалсан (`DUPLICATE_OF_POS_TERMINAL`) баримтыг **409**-өөр хамгаална —
`{"allow_duplicate": true}` ил зөвшөөрлөөр л. Бүгд цуцлагдсан төлбөрийн орлох мөр тохиргоо
асаалттай тул EXTERNAL болж бүртгэгдвэл `200 {created: true, external: true}`.

### POST /api/reports/vat-reconcile/cancel-duplicates  (vat/reports)
`{payment_ids[], note, dry_run=true, teg?: {payment_id: {teg_ddtd, teg_register}}}` — `teg`-ийг
`vat-reconcile` хариуны `duplicates[]`-ээс авна. `dry_run=true` синхрон (юу цуцлагдах/яагаад
үгүй); `dry_run=false` АРЫН АЖИЛ — явцыг `GET …/cancel-duplicates/status`. Дүрэм нь серверийн
CLI `tools/vat_cancel_duplicates.py`-тэй НЭГ (`services/vat_duplicates.cancel_one`): QR биш
төлбөрийн зөвхөн МАНАЙ msgbill баримтыг цуцалж `DUPLICATE_OF_POS_TERMINAL` гэж тэмдэглэнэ,
дараа нь терминалын баримтыг бүртгэнэ — итгэх ТЕГ ДДТД (33 орон, манай кассынх биш, давхар
бүртгэлгүй) бол `ONTIME/SENT`, эс бөгөөс ДДТД-гүй `EXTERNAL`. Терминалын мөрийг хөндөхгүй.
`vat-reconcile` хариунд `terminal_issued` / `terminal_confirmed` / `terminal_missing[]`
(EXTERNAL боловч ТЕГ-т баримт алга — баримтгүй борлуулалт байж болзошгүй); терминалын хэвийн
мөр (`TERMINAL` шалтгаан) `unmatched_tax`-д орохгүй, `tax_verdicts`-д тоологдоно.

⚠️ `amount` нь системийн тооцсон дүнтэй таарахгүй бол `400` буцна — картаар авахын ӨМНӨ
`GET /api/sessions/{id}`-ээс дүнг АВЧ баталгаажуулна.

### GET /api/payments
Төлбөрийн жагсаалт (FINANCE+). Query: `site_id, status, provider, date_from, date_to`.

### POST /api/payments/pos/heartbeat  (cashier, 2026-10-15)
POS аппын амьд байдал/төлөв: `{terminal_id, pos_version?, status? ≤40, printer?, battery? 0–100,
network?, site_id?, extra? ≤1KB}` (`terminal_id`-г `X-Terminal-Id`-аар ч болно) → `{ok, server_time,
min_app_version, update_required, next_heartbeat_sec}`; буруу утга **422**. Бүх хүсэлтэд сонголттой
`X-Terminal-Id`, `X-POS-Status` толгой. Дэлгэрэнгүй: PAX_POS_APP_GUIDE §2.2.

### GET /api/admin/pos-clients?site_id=&days=30  (ADMIN/SUPER_ADMIN)
«Холбогдсон POS» (хэрэглэгч × терминал): хувилбар (`version_state` ok/behind/outdated/unknown), төлөв,
`presence` (online ≤5 мин / offline / stale 24ц+), сүүлд холбогдсон, IP, 24ц/7х POS аппын төлбөр,
e-Barimt горим (`receipt_mode` system/terminal), 7х EXTERNAL, сүүлийн баримтын төлөв. Түрээслэгчийн
админ өөрийн зогсоолынхоо мөрийг л авна. `days≤0` → бүх түүх.

### GET|PUT /api/admin/pos-settings  (ADMIN/SUPER_ADMIN; PUT — ерөнхий тохиргооны эрх)
POS-той холбоотой одоо байгаа тохиргооны хураангуй (e-Barimt терминалын дүрэм ерөнхий/зогсоол,
баримтын inline хүлээлт, бүртгэлтэй PAX терминал, POS аппын хэрэглэгчид, нээх шалтгаан, онцгой
гаргалт, `payment_hold_minutes`, зогсоолын POS эгнээ). PUT `{min_app_version: "1.5.3" | ""}` —
зөвхөн тодруулга/`update_required`, хүсэлт хаахгүй.

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
PARKING_QPAY_CLASSIFICATION_CODE=6743000  # GS1: Автомашины зогсоолын үйлчилгээ
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
