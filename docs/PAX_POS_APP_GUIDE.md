# POS терминал апп хөгжүүлэгчийн заавар (PAX A9000 / Bonum)

> **Тэмдэглэл:** Backend API нь терминал-хамааралгүй. Худалдан авагч тал **Bonum POS**
> сонгосон бол мөн адил ажиллана — зөвхөн картын SDK хэсэг (3.3) Bonum-ийн SDK-гаар
> солигдоно, `POST /api/payments/pos/confirm` болон бусад бүх интеграц өөрчлөгдөхгүй.

Гарах хаалтны дэргэдэх **PAX A9000** терминал дээр ажиллах Android/Flutter апп.
Зорилго: гарах гэж буй машиныг сонгоод **TDB Bank картаар** төлбөр авч, баримт хэвлэж, хаалт нээлгэх.

## 1. Төхөөрөмж

| Үзүүлэлт | Утга |
|---|---|
| OS | Android (PAX certified) |
| Дэлгэц | 5" touch |
| Card reader | EMV chip, NFC, magnetic |
| Printer | Built-in thermal 50мм |
| SDK | PAX PosLink SDK (TDB Bank-аас acquiring гэрээтэй хамт авна) |
| Холболт | Wi-Fi / Ethernet / 4G |

Апп суулгалт: PAX Store эсвэл TDB Bank-ийн зөвшөөрөлтэй sideload.

## 2. Нэвтрэлт

Терминал бүрд OPERATOR эрхтэй хэрэглэгч үүсгэнэ (Хэрэглэгчид хуудаснаас).
Апп асахад login → token хадгална → 401 ирвэл дахин login.

```
POST /api/auth/login  (form-urlencoded)
username=pos_site01&password=***
```

## 3. Дэлгэцүүд ба урсгал

### 3.1 Recent Exits (үндсэн дэлгэц)

WebSocket-ээр real-time шинэчлэгдэнэ:

```
ws://SERVER/ws/sites/{site_id}
```

- Эхлэхдээ: `GET /api/sessions/recent-exits?site_id={site_id}` — одоогийн жагсаалт
- `EXIT_LPR_EVENT` ирэхэд жагсаалтын эхэнд нэмнэ
- `EXIT_COMPLETED` / `PAYMENT_COMPLETED` ирэхэд жагсаалтаас хасна
- Мөр бүрт: **дугаар (том, font-mono), орсон цаг, хугацаа, дүн**
- Гараар хайх товч → `GET /api/sessions/check?plate=...`

### 3.2 Fee Detail

Сонгосон session-ийн задаргаа. **Заавал** төлбөрийн өмнө дахин уншина:

```
GET /api/sessions/{id}   →  fee.total_fee
```

(хугацаа өссөн байж болзошгүй тул картаар авах дүнг ЭНЭ утгаас авна)

### 3.3 Card Payment (PAX PosLink)

```java
PosLink posLink = new PosLink();
PaymentRequest req = new PaymentRequest();
req.setTransType(TransType.SALE);
req.setAmount(String.valueOf(totalFeeMNT));   // системээс авсан дүн
req.setCurrencyCode("496");                    // MNT
posLink.payment = req;
ReturnCode rc = posLink.ProcessTrans();        // карт уншуулахыг хүлээнэ

if (rc == ReturnCode.OK && "000".equals(posLink.payment.getResultCode())) {
    confirmToBackend(posLink.payment);         // 3.4 руу
} else {
    showFailed(posLink.payment.getMessage());  // Payment Result дэлгэц
}
```

### 3.4 Backend баталгаажуулалт

Карт **амжилттай** уншигдсаны дараа л дуудна:

```
POST /api/payments/pos/confirm
Authorization: Bearer {token}
{
  "session_id": "...",
  "amount": 2000.0,
  "auth_code": "{PosLink AuthCode}",
  "card_last4": "4242",
  "card_brand": "Visa",
  "terminal_id": "TDB-PAX-SITE01-01",
  "transaction_id": "{PosLink RefNum}",
  "payer_reg_no": "АА00112233"
}
```

**`payer_reg_no` (сонголт, 2026-08-21-нээс)** — худалдан авагчийн дугаар. Гурван
формат хүчинтэй бөгөөд систем нь форматаар нь таньж баримтын төрлийг сонгоно:

| Формат | Жишээ | Баримт |
|---|---|---|
| ААН регистр (7 орон) | `1234567` | ORGANIZATION (байгууллагын нэр дээр) |
| ТТД (11–14 орон) | `12345678901` | ORGANIZATION |
| Иргэний регистр (2 кирилл + 8 орон) | `АА00112233` | CITIZEN (иргэний нэр дээр, сугалаатай) |
| Хоосон / танигдахгүй | `""` | CITIZEN (нэргүй, сугалаатай) |

Буруу бичсэн утга **алдаа өгөхгүй** — нэргүй баримт болно (жолоочийн бичилтийн
алдаанаас болж төлбөр таслагдах ёсгүй). Хуучин `customer_tin` талбар ажилласаар
байна (ижил утгатай, зөвхөн ААН).

Хариу `status=PAID, barrier_opened=true` + доорх баримтын багц.

ℹ️ **Терминал өөрөө e-Barimt гаргадаг бол** (банкны PosLink-д Ибаримт үйлчилгээ идэвхтэй):
PosLink-ийн хариунаас ДДТД/сугалаа/QR-ыг `"ebarimt_id"`, `"lottery_code"`, `"qr_data"`
талбараар нэмж дамжуулна (`"ebarimt_provider": "TDB-POSLINK"` гэх мэт эх сурвалжийн нэр
сонголтоор) — систем **давхар баримт үүсгэхгүй**, терминалынхыг бүртгэнэ (Ибаримт хуудсанд
суваг = тэр нэр). Дамжуулахгүй бол систем өөрөө (msgbill.mn) үүсгэнэ. Баримтыг ДАРАА нь
холбох бол `POST /api/payments/{payment_id}/ebarimt` (API_REFERENCE).

⚠️ **Дүн зөрвөл 400 ирнэ.** Энэ тохиолдолд картын гүйлгээг VOID хийж, Fee Detail-ийг
дахин уншиж шинэ дүнгээр давтана.

⚠️ **Сүлжээ тасарч confirm явуулж чадаагүй бол:** transaction-ийг локал queue-д хадгалж
30 сек тутам retry хийнэ. `sender_invoice_no` unique тул давхар бүртгэгдэхгүй.

### 3.5 Receipt Print

Систем e-Barimt-ыг **өөрөө үүсгээд** (msgbill.mn) хэвлэхэд бэлэн болгож буцаана.
Хариунд ХОЁР хэлбэр зэрэг ирнэ — аль нэгийг нь сонгож хэрэглэнэ:

```jsonc
{
  "status": "PAID",
  "payment_id": "…",
  "barrier_opened": true,
  "ebarimt_id": "030101…05595",
  "lottery_code": "HV 83198235",
  "qr_data": "138431709…",              // QR болгон хэвлэнэ
  "receipt": {                           // ← ТАЛБАРУУД (2026-08-21-нээс)
    "site_name": "Хангарьд",
    "plate_number": "1234УБА",
    "entry_time": "2026-08-21T11:50:00", // УБ цагаар (UTC+8)
    "exit_time":  "2026-08-21T16:35:00",
    "duration_minutes": 285,
    "duration_text": "4ц 45м",
    "amount": 11000.0,
    "vat_amount": 1000.0,
    "payment_method": "CARD",
    "receipt_type": "CITIZEN",           // эсвэл ORGANIZATION
    "payer_reg_no": "АА00112233",
    "ebarimt_id": "030101…05595",
    "lottery_code": "HV 83198235",
    "ebarimt_status": "SENT",            // SENT | FAILED
    "ebarimt_error": null                // FAILED үед шалтгаан
  },
  "print_data": { "lines": ["ЗОГСООЛЫН ТӨЛБӨРИЙН БАРИМТ", "…"] }
}
```

- **`print_data.lines`** — бэлэн форматласан мөрүүд (зогсоолын нэр, дугаар,
  орсон/гарсан цаг, зогссон хугацаа, төлсөн хэлбэр, дүн, НӨАТ, ДДТД, сугалаа).
  Дараалан хэвлэхэд л хангалттай.
- **`receipt`** — ижил мэдээлэл ТАЛБАРААР. Апп өөрийн загвараар (лого, фонт,
  байрлал) хэвлэх бол үүнийг ашиглана — мөр задлах шаардлагагүй.
- Дараа нь `qr_data`-г **QR код болгон** хэвлэнэ — жолооч ebarimt апп-аар
  уншуулж баримтаа бүртгүүлнэ.
- `ebarimt_status = "FAILED"` бол баримт үүсээгүй (msgbill алдаа) — төлбөр
  амжилттай хэвээр, хаалт нээгдсэн. Баримтыг дараа нь системээс дахин үүсгэнэ.

### 3.7 QPay QR төлбөр (жолооч утсаараа уншуулах)

Картаас гадна **QPay QR**-аар төлүүлж болно. Fee Detail дэлгэц дээр **төлбөрийн төрөл**
сонголт (Карт / QPay / Бэлэн) харуулж, QPay сонгосон үед:

```
1) POST /api/payments/qpay/invoice
   { "session_id": "...", "customer_tin": "1234567" (сонголт: ААН баримт) }
   → { payment_id, qr_image (base64 PNG), qr_text, deep_link, urls[], amount }
```

- Хариунд ирсэн **`qr_image`-г дэлгэцэн дээр том харуулна** (эсвэл `qr_text`-ийг өөрөө QR болгоно).
  Энэ бол QPay талаас ирсэн дата — POS апп зөвхөн харуулах үүрэгтэй.
- Жолооч өөрийн банк/QPay апп-аараа QR-ийг уншиж төлнө.

```
2) POST /api/payments/qpay/check/{payment_id}   (3–5 сек тутам polling)
   → { "status": "PENDING" }                      // хүлээх
   → { "status": "PAID",                           // төлөгдсөн
       "ebarimt_id": "030101...05595",
       "lottery_code": "HV 83198235",
       "qr_data": "138431709...",
       "print_data": { "lines": ["ЗОГСООЛЫН ТӨЛБӨРИЙН БАРИМТ", "..."] } }
```

- `status=PAID` болмогц QPay төлбөрийг баталгаажуулж, систем **e-Barimt-ыг QPay ebarimt_v3-аар
  автоматаар үүсгэсэн** байна. Хаалт мөн автоматаар нээгдсэн.
- `print_data.lines`-г thermal printer-ээр хэвлэж, дараа нь `qr_data`-г **QR код болгон хэвлэж**
  (§3.5-тай ижил) жолоочид өгнө. Картын урсгал шиг `pos/confirm` дуудах шаардлагагүй —
  бүх мэдээлэл `check`-ийн хариунд ирнэ.
- Хугацаа хэтэрсэн/буруу QR-аас сэргийлж polling-ийг ~3 минутын дараа зогсоож,
  «Дахин оролдох» товч харуулна (шинэ invoice үүснэ).

### 3.6 Operator Override

Зөвшөөрөлтэй кассир хаалт гараар нээх:

```
POST /api/barriers/{device_id}/open
```

Barrier device_id-г **`GET /api/barriers/devices?site_id=...`**-ээс авна
(`lane_dir=exit` мөрийг сонгоно). Хариу:
`[{id, site_id, name, device_type, lane_no, lane_dir, auto_open, status, can_open, last_seen}]`.

⚠️ **`can_open` (2026-08-21-нээс) — «Хаалт нээх» товчийг ХАРУУЛАХ эсэхийг заана.**
`false` бол товчийг огт харуулахгүй: тухайн оператор «Гараар/төлбөргүй гаргах +
хаалт гараар нээх (касс/POS)» эрхгүй тул дарвал 403 иднэ. Эрхийг ажилтны картаас
(Ажилтан → эрхийн матриц) олгоно. Мөн `GET /api/auth/me` → `permissions` массивт
`free_exit` байгаа эсэхээр шалгаж болно.

> ⚠ **2026-08-21 өөрчлөлт (заавал шинэчилнэ).** Өмнө нь `GET /api/admin/devices`
> ашиглаж байсан. Тэр endpoint нь камерын `device_key`-г буцаадаг тул
> 2026-08-20-ны аюулгүй байдлын хатууруулалтаар `devices/settings/barriers`
> эрхээр хаагдсан — OPERATOR эрхтэй POS хэрэглэгч **403 «Танд энэ үйлдлийг хийх
> эрх байхгүй»** авдаг болсон. Шинэ endpoint нь `cashier`/`free_exit`/`barriers`
> эрхийн аль нэгээр ажиллах бөгөөд ямар ч нууц талбар агуулахгүй.

## 4. Тохиргооны файл (апп дотор)

```json
{
  "server_url": "https://test.easy-parking.mn",
  "site_id": "{Тохиргоо→Зогсоол хуудаснаас UUID}",
  "terminal_id": "TDB-PAX-SITE01-01",
  "operator_username": "pos_site01"
}
```

## 5. Туршилт (бодит терминалгүйгээр)

1. `POST /api/lpr/simulate {"device_key": "cam-entry-site01", "plate": "8888ТТТ"}`
2. `POST /api/lpr/simulate {"device_key": "cam-exit-site01", "plate": "8888ТТТ"}`
   → Recent Exits дэлгэцэд гарч ирэх ёстой (WebSocket)
3. Card Payment-ийг mock хийж `POST /api/payments/pos/confirm` дуудна
   → `print_data` хариу ирвэл амжилттай

## 6. Чанарын шаардлага

- Бүх дэлгэц Монгол хэлээр, дугаар нь font-mono, том (≥24sp)
- Товч ≥ 48×48dp, dark theme (гадаа нарны гэрэлд тод харагдах контраст)
- Гүйлгээ бүр локал SQLite-д лог хийгдэнэ (аудит + offline queue)
- Апп unattended режимд 24/7 ажиллана: crash-д auto-restart, watchdog
- WebSocket тасарвал 3 сек тутам дахин холбогдох, жагсаалтыг REST-ээр sync
