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
  "transaction_id": "{PosLink RefNum}"
}
```

Хариу `status=PAID, barrier_opened=true` + `print_data.lines` (хэвлэх мөрүүд) +
`lottery_code` (e-Barimt сугалаа).

⚠️ **Дүн зөрвөл 400 ирнэ.** Энэ тохиолдолд картын гүйлгээг VOID хийж, Fee Detail-ийг
дахин уншиж шинэ дүнгээр давтана.

⚠️ **Сүлжээ тасарч confirm явуулж чадаагүй бол:** transaction-ийг локал queue-д хадгалж
30 сек тутам retry хийнэ. `sender_invoice_no` unique тул давхар бүртгэгдэхгүй.

### 3.5 Receipt Print

`print_data.lines` массивыг thermal printer-ээр хэвлэнэ, дараа нь хариултын
`qr_data` талбарыг (e-Barimt POS API 3.0-ийн qrData) **QR код болгон хэвлэнэ** —
хэрэглэгч ebarimt апп-аар уншуулж баримтаа бүртгүүлнэ. Сугалааны кодыг (`lottery_code`)
текстээр давхар хэвлэнэ.

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

Barrier device_id-г `GET /api/admin/devices?site_id=...` (device_type=barrier, lane_dir=exit).

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
