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

**Хүсэлт бүрд `X-App-Version: 1.4.0` толгой илгээнэ** (2026-08-21-нээс). Систем
терминалын бүртгэлд хадгалж логонд бичнэ — «энэ алдаа аль хувилбар дээр гарч
байна вэ» гэдгийг тогтоох цорын ганц арга. Хувилбар ахиулах бүрд шинэчилнэ.

Нэвтэрсний дараа `permissions` массивыг хадгалж, **товч бүрийг эрхээр нь нуана**:

| Эрх | Байхгүй бол нуух |
|---|---|
| `cashier` | Төлбөр авах бүх товч, ээлж |
| `check` | Дугаараар хайх |
| `free_exit` | Хаалт нээх (мөн `can_open`-оор давхар шалгана) |

Ингэснээр оператор дараад 403 иддэг байдал арилна.

```
POST /api/auth/login  (form-urlencoded)
username=pos_site01&password=***
```

### 2.1 Зогсоол + хаалт (эгнээ) сонголт — 2026-09-07-оос

Зарим зогсоол **2 орох + 2 гарах** хаалттай (4 камер, 4 хаалт) болсон тул
«орох / гарах» гэсэн хоёр сонголт хүрэлцэхгүй. Нэвтэрсний дараа НЭГ дуудлагаар
эрх + хандах зогсоолууд + зогсоол бүрийн ЭГНЭЭНИЙ жагсаалт ирнэ:

```
GET /api/payments/pos/bootstrap?terminal_id=TDB-PAX-SITE01-01
```

```json
{
  "user": {"username": "pos_site01", "full_name": "…", "role": "OPERATOR"},
  "permissions": ["cashier", "check", "free_exit"],
  "terminal": {"terminal_id": "TDB-PAX-SITE01-01", "site_id": "…", "site_name": "Хангарьд"},
  "open_reasons": [{"code": "vip", "label": "VIP / гэрээт зочин"}, "…  (§3.7)"],
  "sites": [{
    "id": "…", "name": "Хангарьд", "site_code": "site01", "address": "…",
    "lanes": [
      {"lane_dir": "entry", "lane_no": 1, "barrier_id": "…", "barrier_name": "Орох хаалт 1", "can_open": true,  "camera_id": "…", "camera_name": "Орох камер 1"},
      {"lane_dir": "entry", "lane_no": 2, "barrier_id": "…", "barrier_name": "Орох хаалт 2", "can_open": true,  "camera_id": "…", "camera_name": "Орох камер 2"},
      {"lane_dir": "exit",  "lane_no": 1, "barrier_id": "…", "barrier_name": "Гарах хаалт 1", "can_open": true,  "camera_id": "…", "camera_name": "Гарах камер 1"},
      {"lane_dir": "exit",  "lane_no": 2, "barrier_id": "…", "barrier_name": "Гарах хаалт 2", "can_open": true,  "camera_id": "…", "camera_name": "Гарах камер 2"}
    ],
    "barriers": [ {id, site_id, name, lane_no, lane_dir, auto_open, status, can_open, last_seen} ],
    "cameras":  [ {id, site_id, name, lane_no, lane_dir, nested_inner, status, last_seen} ]
  }]
}
```

Апп дээрх урсгал:

1. `sites` нэгээс олон бол **зогсоол сонгуулна** (`terminal.site_id` байвал анхдагчаар сонгоно).
2. Тухайн зогсоолын `lanes`-аас **удирдах эгнээ (хаалт)** сонгуулна — `lane_dir=exit`
   мөрүүдийг «Гарах хаалт 1 / 2» гэж, `entry`-г «Орох хаалт 1 / 2» гэж харуулна.
   `barrier_id` = null бол тэр эгнээнд хаалт бүртгэгдээгүй (сонгуулахгүй).
3. Сонголтыг локалд хадгална: `site_id`, `lane_no`, `barrier_id`. Тохиргооны
   дэлгэцээс дахин солих боломжтой байх.
4. Хаалт нээх: `POST /api/barriers/{barrier_id}/open` — сонгосон эгнээний хаалт.
   `can_open=false` бол товчийг нуух (§3.6).
5. Recent Exits: `GET /api/sessions/recent-exits?site_id=…&lane_no={lane_no}` —
   зөвхөн сонгосон эгнээний камерт уншигдсан машинууд (§3.1). Эгнээ сонгоогүй/бүх
   эгнээг харах бол `lane_no`-гүй дуудна.

Хариунд `device_key`, IP, нууц үг ОГТ ирэхгүй (аюулгүй байдлын хатууруулалт хэвээр).
Хуучин `GET /api/admin/sites` + `GET /api/barriers/devices?site_id=` хоёр хэвээр
ажиллана — шинэ апп `bootstrap`-ыг л ашиглана.

## 3. Дэлгэцүүд ба урсгал

### 3.1 Recent Exits (үндсэн дэлгэц)

WebSocket-ээр real-time шинэчлэгдэнэ:

```
ws://SERVER/ws/sites/{site_id}
```

- Эхлэхдээ: `GET /api/sessions/recent-exits?site_id={site_id}&lane_no={lane_no}` — одоогийн
  жагсаалт. `lane_no` (2026-09-07) = сонгосон эгнээ; өгвөл ЗӨВХӨН тэр эгнээний гарах
  камерт уншигдсан машин ирнэ (2 гарах хаалттай зогсоолд нөгөө эгнээний машин
  холилдохгүй). Эсвэл `device_id={barrier_id}` өгч болно (ижил эгнээний камераар таарна).
  Мөр бүрд `exit_lane_no`, `exit_lane_dir`, `exit_device_name` нэмэлт талбар ирнэ —
  `lane_no`-гүй (бүх эгнээ) горимд машин аль хаалтан дээр байгааг харуулна.
- `EXIT_LPR_EVENT` ирэхэд: `device_id`/эгнээ нь сонгосон эгнээтэй таарахгүй бол алгасна
  (эгнээ шүүлттэй горимд)
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

Хариу `status=PAID, barrier_opened=true` + доорх баримтын багц (талбар бүрийг §3.5-д).

**2026-10-15-аас ДДТД/сугалаа/QR confirm-ийн хариунд шууд ирнэ.** Сервер мөнгийг бүртгэж хаалтыг
нээсний ДАРАА msgbill.mn-ээр баримтыг шууд үүсгэж **нийт ≤6 секунд** хүлээнэ. Тиймээс:

- confirm/cash-ийн HTTP **read timeout ≥ 30 секунд** байлгана (хаалтын команд ≤12с + баримт ≤6с).
  Timeout болбол ижил `transaction_id`-аар давтахад (offline queue) систем ШИНЭ төлбөр/баримт
  үүсгэхгүй — анхных нь хариуг (илгээж буй баримтыг хүлээгээд) буцаана.
- msgbill 6с-д хариулаагүй бол `receipt.ebarimt_status = "PENDING"` — §3.5-ын дүрмээр `/print`-ийг
  дахин татна. Баримт арын горимд дуусна; давхар баримт хэзээ ч үүсэхгүй.

⚠️ **Терминал өөрөө e-Barimt гаргадаг бол — ЗААВАЛ ДДТД-г илгээнэ** (2026-10-13).
Терминал (Ontime, касс …10002990; эсвэл банкны PosLink-ийн Ибаримт үйлчилгээ) карт/бэлэн
төлбөрт баримт хэвлэдэг бол түүний ДДТД/сугалаа/QR-ыг **`/pos/confirm` болон `/cash`-д**
нэмж дамжуулна:

```jsonc
{
  "...": "дээрх талбарууд",
  "ebarimt_id": "0291002441060010972102300100459…",  // терминалын ДДТД
  "lottery_code": "XA 66306294",
  "qr_data": "594649643…",
  "ebarimt_provider": "ONTIME"                       // эх сурвалжийн нэр (≤20)
}
```

Систем **давхар баримт үүсгэхгүй**, терминалынхыг бүртгэнэ (Ибаримт хуудсанд суваг = тэр
нэр, ТЕГ тулгалт ДДТД-тэй таарна). Дамжуулахгүй бол систем өөрөө (msgbill.mn) ӨӨР баримт
гаргаж **ТЕГ-т нэг гүйлгээнд хоёр баримт** болно — 2026-08-24-нд Хангарьд дээр 63 ийм
давхардал (39 бэлэн + 24 карт) илэрсэн. Энэ бол **урт хугацааны зөв шийдэл**.

**2026-10-15 — аппын шинэ хувилбар e-Barimt-ыг ЗӨВХӨН манай API-аас хэвлэдэг (Ontime-гүй).**
10-14-нд «терминал (Ontime) баримтаа өөрөө гаргадаг» дүрмийг бүх зогсоолд асаасан байсан (систем
msgbill баримт гаргахгүй, `EXTERNAL`). Аппын шинэ хувилбар Ontime ашиглахгүй тул (ТЕГ экспорт: Ontime-ийн
мөр 10-08 54 → 10-09 4 → 10-10…12 0) дүрмийн ерөнхий анхдагч **УНТРААЛТТАЙ** болов — POS аппын
карт/бэлэн төлбөр бүрд систем msgbill.mn-ээр баримт гаргаж confirm/cash/print-ийн хариунд өгнө.
Тохиргоо → Төлбөрийн дүрэм → e-Barimt → «POS терминал (PAX апп) өөрөө e-Barimt гаргадаг»-ийг терминал
баримтаа ӨӨРӨӨ хэвлэдэг нь батлагдсан зогсоолд л асаана (асаалттай зогсоолд `ebarimt_status:
"EXTERNAL"`, ДДТД/QR-гүй — жолоочид терминалын баримт өгнө). 10-13-нд 7 зогсоолд бичсэн ON мөрийг
`tools/pos_terminal_setting.py --all --set global --apply`-аар ерөнхий рүү буцаана.

Аль төлбөр «POS аппынх» вэ (`services/pos_app.py` — нэг тодорхойлолт):

| Нөхцөл | Тэмдэг |
|---|---|
| `X-App-Version` толгойтой хүсэлт (`/cash`, `/compensations/{id}/pay`) | `source=POSAPP`, `raw_payload.app_version` |
| `/pos/confirm` (картын баталгаажуулалт — зөвхөн POS апп дууддаг) | `source=POSAPP` |
| Толгойгүй (хуучин build) ч кассчин нь POS аппын хэрэглэгч: role **POS**, эсвэл «POS аппын хэрэглэгчид» жагсаалтад, эсвэл POS аппаар **≥20 карт** авсан | `source=POSAPP`, `raw_payload.pos_app_via = role/user_list/card_history` |

10-14-ний deploy-оос хойш POS аппын бэлэн төлбөр **20/20 толгойтой** ирсэн (1.5.1/1.5.3) —
`X-App-Version` толгойг бүх хүсэлтэд (бэлэн, өрийн төлөлт ч) **ЗААВАЛ** илгээсээр байна.
Толгойгүй fallback нь зөвхөн веб хөтөч БИШ клиентэд (User-Agent «Mozilla/…» биш — Android
апп) үйлчилнэ: POS аппын хэрэглэгч ВЕБ кассаар (хөтчөөр) бэлэн авбал систем msgbill баримт
гаргана; толгойгүй үед `CASH_PAYMENT` аудитад `user_agent` хадгална (аппын UA-г прод дээр
батлах). Веб кассын ажилтан (sogii, bamanna г.м) POS картын түүхгүй тул тэдний бэлэн төлбөрт
систем баримт гаргасаар. Түүхэн цуцлалт (`vat_cancel_duplicates --pos-app`) 2026-10-14 04:48
UTC-ээс хойшхи төлбөрийг ЗӨВХӨН `source=POSAPP` тамгаар таньна (хэрэглэгчийн нэрээр биш).

Үл хамаарах: **ААН/ТТД-тэй баримт** (`payer_reg_no`/`customer_tin` — терминал ИРГЭНИЙ баримт
хэвлэдэг тул байгууллагын НӨАТ-ын баримтыг систем msgbill-ээр гаргасаар), QPay QR, дансаар,
хэтэвч, апп `ebarimt_id` илгээсэн төлбөр (терминалын ДДТД-тэй SENT). Баримтыг ДАРАА нь холбох
бол `POST /api/payments/{payment_id}/ebarimt` (API_REFERENCE) — EXTERNAL мөр терминалын
ДДТД-тэй SENT болно. Терминал баримт ХЭВЛЭЭГҮЙ нь батлагдвал Ибаримт хуудасны «Систем
баримт гаргах» (баталгаажуулалттай, `no_terminal_receipt`) эсвэл ТЕГ экспортоор бөөнөөр
`tools/vat_reissue_missing.py` системийн баримтыг гаргана.

### 3.4b Бэлэн төлбөр

```
POST /api/payments/cash
X-App-Version: 1.4.0
{ "session_id": "...", "expected_amount": 2000, "customer_tin": "1234567" (сонголт),
  "ebarimt_id": "…", "lottery_code": "…", "qr_data": "…", "ebarimt_provider": "ONTIME" }
```

`ebarimt_*` — терминал бэлэн төлбөрт баримт хэвлэсэн бол (дээрхтэй ижил дүрэм; шинэ апп илгээхгүй).

Хариу (2026-10-15-аас — `/pos/confirm`-той ИЖИЛ хэвлэх багцтай, хуучин түлхүүрүүд хэвээр):

```jsonc
{
  "ok": true, "status": "PAID", "payment_id": "…", "amount": 2000.0,
  "session_status": "PAID", "amount_due": 0, "needs_additional_payment": false, "overpaid_amount": 0,
  "barrier_opened": true, "barrier_command_status": "SUCCESS", "barrier_command_id": "…",
  "exit_deadline": "…", "exit_waiting": true, "gate_acknowledged": true, "physical_gate_state": "UNKNOWN",
  "ebarimt_id": "015200020090…10045952", "lottery_code": "IW 41850824", "qr_data": "…",
  "receipt": { … §3.5 … }, "print_data": { "lines": [ … ] }
}
```

Хэвлэх дүрэм §3.5-тай ижил (`PENDING` бол `/print`-ийг дахин татна). Баримтыг шууд илгээж ≤6с
хүлээх нь ЗӨВХӨН POS аппын хүсэлтэд (`X-App-Version` толгойтой) — веб касс (хөтөч) хүлээхгүй,
багц нь ихэвчлэн `PENDING`-ээр ирнэ.

### 3.4a Худалдан авагчийг ШАЛГАХ (нэр харуулах)

Оператор регистр оруулмагц дэлгэц дээр **нэрийг нь харуулж** зөв бичсэн эсэхийг
батална:

```
GET /api/payments/ebarimt/payer?reg_no=1234567
→ { "available": true, "found": true,
    "name": "МОННИС ПРОПЭРТИ ХХК",
    "receipt_type": "ORGANIZATION",
    "is_vat_payer": true, "tin": "71101242183" }
```

Жишээ (аппын дуудлага, 2026-10-15): `reg_no=5037409` (Моннис пропэрти ХХК-ийн **регистр**, 7 орон) ба
`reg_no=71101242183` (мөн тэр байгууллагын e-Barimt 3.0 **ТТД**, 11 орон — иргэний дугаар БИШ: прод
өгөгдөлд Моннисын msgbill баримтын ДДТД `071101242183…`-аар эхэлдэг; ТТД өгвөл регистр→ТТД алхам
алгасагдана) хоёулаа `receipt_type: ORGANIZATION`; нэр/ТТД нь api.ebarimt.mn-ээс (`available: false`
бол суваг хүрэхгүй — §доорх).
`/pos/confirm`-д `payer_reg_no`-оор аль нэгийг нь илгээхэд ААН-ийн (B2B, сугалаагүй) баримт гарна,
`print_data`-д «Худалдан авагч ТТД: …» мөр орно.

- **`receipt_type: ORGANIZATION`** → байгууллагын **нэрийг** том харуулна.
- **`receipt_type: CITIZEN`** → хүний нэр + **«НӨАТ төлөгч мөн / биш»**
  (`is_vat_payer`). `null` бол «НӨАТ төлөгч эсэх мэдэгдэхгүй» гэж бичнэ —
  «биш» гэж БҮҮ бич.
- **`available: false`** → шалгах суваг ажиллахгүй байна. «Шалгах боломжгүй»
  гэж харуулаад **төлбөрийг үргэлжлүүлнэ** — «олдсонгүй» ГЭЖ БИШ.
- `found: false` (available=true) → «Ийм дугаартай татвар төлөгч олдсонгүй».
  Оператор дахин шалгана, гэхдээ хүсвэл ингээд үргэлжлүүлж болно.

Хариу кэшлэгддэг тул давтан хайлт шуурхай. Оролт бүрд БИШ, оператор бичиж
дуусмагц (эсвэл 500мс завсарлагаанд) нэг л удаа дуудна.

⚠️ **Дүн зөрвөл 400 ирнэ.** Энэ тохиолдолд картын гүйлгээг VOID хийж, Fee Detail-ийг
дахин уншиж шинэ дүнгээр давтана.

⚠️ **Сүлжээ тасарч confirm явуулж чадаагүй бол:** transaction-ийг локал queue-д хадгалж
30 сек тутам retry хийнэ. `sender_invoice_no` unique тул давхар бүртгэгдэхгүй.

### 3.5 Receipt Print

Систем e-Barimt-ыг **өөрөө үүсгээд** (msgbill.mn) хэвлэхэд бэлэн болгож буцаана. ИЖИЛ хэвлэх
багц дөрвөн газар ирнэ: `POST /pos/confirm`, `POST /cash` (2026-10-15-аас), `GET /{payment_id}/print`,
`GET|POST /qpay/check/{payment_id}` (PAID үед), мөн `GET /pos/receipts` мөр бүрд.

```jsonc
{
  "status": "PAID",
  "payment_id": "…",
  "barrier_opened": true,                // confirm/cash/qpay-check-д (gate_result)
  "ebarimt_id": "015200020090…10045952", // ДДТД — receipt.ebarimt_id-тай ижил
  "lottery_code": "IW 41850824",         // ААН-ийн баримтад null
  "qr_data": "138431709…",               // QR болгон хэвлэх ТЕКСТ (зураг биш); байхгүй бол null
  "receipt": {                           // ← ТАЛБАРУУД
    "site_name": "Хангарьд",
    "plate_number": "1234УБА",
    "entry_time": "2026-08-21T11:50:00", // УБ цагаар (UTC+8)
    "exit_time":  "2026-08-21T16:35:00",
    "duration_minutes": 285,
    "duration_text": "4ц 45м",
    "amount": 11000.0,
    "vat_amount": 1000.0,
    "payment_method": "CARD",            // CARD | CASH | QR | TRANSFER
    "receipt_type": "CITIZEN",           // эсвэл ORGANIZATION
    "payer_reg_no": "АА00112233",
    "ebarimt_id": "015200020090…10045952",
    "lottery_code": "IW 41850824",
    "ebarimt_status": "SENT",            // SENT | PENDING | EXTERNAL | FAILED | REVIEW | CANCELLED
    "ebarimt_error": null,               // FAILED үед шалтгаан
    "ebarimt_note": null,                // EXTERNAL үед тайлбар
    "ebarimt_issuer": "MSGBILL"          // MSGBILL | QPAY | POS_TERMINAL | ONTIME …
  },
  "print_data": { "lines": ["ЗОГСООЛЫН ТӨЛБӨРИЙН БАРИМТ", "…", "ДДТД: 0152…", "Сугалаа: IW 41850824"] }
}
```

`/pos/confirm` нэмж `session_status, amount_due, needs_additional_payment, overpaid_amount,
barrier_command_status, barrier_command_id, exit_deadline, exit_waiting, gate_acknowledged,
physical_gate_state, exit_lane_context`; `/cash` нэмж `ok, amount` + ижил settlement/gate талбарууд;
`/print` зөвхөн `payment_id, status` + дээрх багц.

- **`print_data.lines`** — бэлэн форматласан мөрүүд (зогсоолын нэр, дугаар, орсон/гарсан цаг,
  хугацаа, төлсөн хэлбэр, дүн, НӨАТ, ДДТД, сугалаа). Дараалан хэвлэхэд л хангалттай.
- **`receipt`** — ижил мэдээлэл ТАЛБАРААР. Апп өөрийн загвараар (лого, фонт, байрлал) хэвлэх бол
  үүнийг ашиглана — мөр задлах шаардлагагүй.

**ХЭЗЭЭ ДАХИН ТАТАХ (`GET /api/payments/{payment_id}/print`):**

| `receipt.ebarimt_status` | `qr_data` | Апп юу хийх |
|---|---|---|
| `SENT` | байгаа | Шууд хэвлэнэ (мөрүүд + QR) |
| `SENT` | `null` | `/print`-ийг **1 удаа** дахин татна (сервер QR-ыг msgbill-ээс нөхөж кэшилнэ, ≤3с). Мөн `null` бол QR-гүй хэвлэнэ — ДДТД + сугалаа мөрөөр жолооч ebarimt апп-д гараар бүртгүүлж болно |
| `PENDING` | `null` | `/print`-ийг **1–2 секунд тутам ~15 секунд хүртэл** татна; `PENDING`-ээс өөр болмогц хэвлэнэ. Хугацаа хэтэрвэл одоогийн өгөгдлөөр хэвлэнэ («ДДТД: хүлээгдэж байна») — ДДТД бэлэн болсны дараа §3.5a-аар дахин хэвлэнэ |
| `FAILED` / `REVIEW` | `null` | Дахин татахгүй — төлбөр амжилттай, хаалт нээгдсэн; баримтыг админ системээс дахин үүсгэнэ (`ebarimt_error`-ийг логонд) |
| `EXTERNAL` | `null` | Терминал (Ontime) баримтаа өөрөө хэвлэдэг зогсоол (§3.4, анхдагч унтраалттай) — терминалын баримтыг өгнө; `print_data`-д «НӨАТ баримт: POS терминал (Ontime) гаргасан» |

`/print` нь баримтыг ДАХИН ҮҮСГЭДЭГГҮЙ (зөвхөн уншина) — хэдэн удаа ч дуудсан давхар баримт гарахгүй.

**QR:** `qr_data` нь ebarimt-ийн QR-ын ТЕКСТ агуулга — апп өөрөө QR код болгон (ECC M, ≥25мм)
хэвлэнэ. ТЕГ-ийн шаардлагаар QR-ыг сервер DB-д ХАДГАЛДАГГҮЙ (санах ойд 1 цаг; дараа нь msgbill-ээс
дахин татна) — апп ч локал SQLite-д хадгалахгүй, хэвлэсний дараа хаяна. Өр нийлүүлсэн төлбөрт
хэвлэх QR нь үргэлж ЗОГСООЛЫН (үндсэн) баримтынх.

### 3.5a Баримт ДАХИН хэвлэх

Цаас дуусах, зам гацах нь өдөр тутмын явдал. Сүүлийн баримтуудыг дахин авна:

```
GET /api/payments/pos/receipts?terminal_id=TDB-PAX-SITE01-01&limit=10   // limit ≤ 50
→ [{ payment_id, paid_at, plate_number, ebarimt_id, lottery_code, qr_data, receipt, print_data }]

GET /api/payments/{payment_id}/print     // ганц баримт
GET /api/payments?limit=10               // сүүлийн төлбөрүүд (зөвхөн өөрийн зогсоолынх; хэвлэх
                                         // өгөгдөлгүй — хэвлэхдээ /print)
```

Аппын «Сүүлийн гүйлгээ» дэлгэцээс сонгоод «Дахин хэвлэх» товч. Хоёр endpoint хоёулаа
баримтын ОДООГИЙН төлөвийг (хүсэлтийн мөчид) уншдаг — confirm-ийн үед `PENDING` байсан
баримтын ДДТД бэлэн болмогц эндээс ирнэ (§3.5); QR санах ойд алга бол сервер msgbill-ээс
нөхнө (`pos/receipts` нийт ≤4с).

### 3.6a Ээлжийн Z-тайлан хэвлэх

`GET /api/cashier/shift/current` болон `POST /api/cashier/shift/close` хоёулаа
`print_data.lines` буцаана — кассын бүх сувгийн задаргаа, нийт дүн, тушаасан
бэлэн мөнгө, зөрүү. Ээлж хаахад автоматаар хэвлэвэл гар тооцоо шаардлагагүй
болно (хаахаас өмнө ч завсрын X-тайлан хэвлэж болно).

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
2) GET /api/payments/qpay/check/{payment_id}    (3–5 сек тутам polling; POST ч ижил)
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

### 3.7 Төлбөргүй гаргах — шалтгаантай (2026-09-07-оос POS-д)

Вэб кассын «Төлбөргүй гаргах» цонхтой ИЖИЛ: оператор шалтгааныг ЖАГСААЛТААС
сонгож, шаардлагатай бол тайлбар бичээд машиныг төлбөргүй гаргана. Шалтгаан бүр
тайланд тоологдоно (хэн, ямар шалтгаанаар хэдэн удаа үнэгүй гаргасан).
Эрх: `free_exit` (§2-ын хүснэгт) — байхгүй бол товчийг нуух.

Шалтгааны жагсаалт `bootstrap` хариунд `open_reasons` талбараар ирнэ (эсвэл
`GET /api/admin/open-reasons?active_only=true`). Жагсаалтыг админ Тохиргоо → Нээх
шалтгаанаас өөрчилдөг тул апп дотор хатуу бичихгүй, нэвтрэх бүрд шинэчилнэ:

```json
"open_reasons": [
  {"code": "vip",          "label": "VIP / гэрээт зочин"},
  {"code": "staff",        "label": "Ажилтны машин"},
  {"code": "wrong_plate",  "label": "Дугаар буруу уншсан"},
  {"code": "no_session",   "label": "Бүртгэл олдоогүй"},
  {"code": "system_error", "label": "Системийн алдаа"},
  {"code": "device_fault", "label": "Хаалт/камер эвдэрсэн"},
  {"code": "emergency",    "label": "Онцгой байдал (түргэн, гал)"},
  {"code": "test",         "label": "Туршилт"},
  {"code": "other",        "label": "Бусад"}
]
```

Дэлгэц: дугаар + бодогдсон төлбөр (`fee.total_fee`) → **Шалтгаан** (dropdown,
заавал) → **Тайлбар** (текст; `code=other` бол ЗААВАЛ 3+ тэмдэгт, бусдад заавал
биш) → төлбөртэй бол «Нөхөн төлбөрийн нэхэмжлэл үүсгэх» checkbox → **Гаргах**.

```
POST /api/sessions/{session_id}/manual-exit
{
  "reason_code": "wrong_plate",        // open_reasons[].code — заавал (эсвэл reason)
  "reason": "Уншсан 1721УБТ → 1727УБТ", // тайлбар; other үед заавал
  "open_barrier": true,
  "device_id": "{сонгосон эгнээний barrier_id}",   // §2.1; өгөхгүй бол зогсоолын эхний гарах хаалт
  "create_compensation": false         // true = төлөгдөөгүй дүнгээр өр (нэхэмжлэл) үүснэ
}
```

Хариу: session (`status: MANUAL_CLOSED`, `note`-д шалтгаан + тайлбар). Алдаа:
`400` шалтгаан жагсаалтад алга / шалтгаангүй / «Бусад»-д тайлбаргүй; `403` free_exit эрхгүй.
Аудит: `MANUAL_EXIT` (reason_code, reason, plate, device_id) — Лог хуудсанд харагдана.

### 3.8 Онцгой гаргалт — зурагтай (2026-09-14-өөс, free_exit ЭРХГҮЙ POS-д)

POS/операторт `free_exit` олгохгүй гэсэн шийдвэртэй. Харин зогсоол дээр ШИЙДЭХ
ёстой 4 тохиолдлыг явцуу урсгалаар зөвшөөрнө — `cashier` эрх хангалттай:

| kind | Товч | Юу болно |
|---|---|---|
| `paid_no_open` | Төлөөд нээгдээгүй | Төлбөр бүрэн төлөгдсөн session — хаалтыг дахин нээнэ (зураг заавал биш) |
| `hbi` | ХБИ | Хөгжлийн бэрхшээлтэй иргэний машин — төлбөргүй гаргана |
| `emergency` | Түргэн / Цагдаа | Онцгой (түргэн, цагдаа, гал) — төлбөргүй гаргана |
| `no_session` | Бүртгэлгүй | Орох уншилтгүй (суурь хураамжтай) машин — хураамжгүй гаргана |

Жагсаалт `bootstrap` хариунд `special_exit_kinds[]` (`kind`, `label`, `needs_snapshot`).

Урсгал — **ХОЁР алхам**, зураг нь баталгаажуулалт (аудитад үлдэнэ):

```
1) POST /api/sessions/{session_id}/special-exit/snapshot
   {"device_id": "{сонгосон эгнээний barrier_id эсвэл camera_id}"}   // заавал биш
   → {"ok": true, "camera": "Гарах камер 2", "taken_at": "...", "path": "..."}
   Гарах хаалтны дугаар уншигч камераас ОДООГИЙН кадрыг татаж session-д хадгална.
   Зургийг GET /api/sessions/{id}/snapshot/verify -ээр татаж дэлгэцэнд харуулна —
   кассчин орох зурагтай (snapshot/entry) тулгаж машин мөн эсэхийг нүдээр батална.
   502 = камер зураг өгсөнгүй → «Дахин оролдох»; зураггүйгээр 2-р алхам явахгүй.

2) POST /api/sessions/{session_id}/special-exit
   {"kind": "hbi", "note": "тэргэнцэртэй", "device_id": "{barrier_id}"}  // note/device_id заавал биш
   → session (+ "barrier_opened": true/false, "special": "hbi")
```

Дүрэм: `hbi`/`emergency`/`no_session`-д 1-р алхмын зургийг ЯМАГТ оролдоно; камер
зураг өгөхгүй (502) бол зураггүй ч 2-р алхам явна — аудитад `snapshot_fresh=false`
гэж тусдаа тэмдэглэгдэнэ (апп дээр «Зураггүй гаргах» гэж анхааруулж харуулна). `paid_no_open` нь
төлөгдөөгүй бол `400`, нэмэлт үлдэгдэлтэй (grace хэтэрсэн) бол `400`. Нөхөн
төлбөр (өр) ҮҮСГЭХГҮЙ. Аудит: `SPECIAL_EXIT_SNAPSHOT`, `SPECIAL_EXIT` (kind,
snapshot зам) — Түүхэнд «Онцгой гаргалт (зурагтай)» шошготой.

## 4. Тохиргооны файл (апп дотор)

```json
{
  "server_url": "https://test.easy-parking.mn",
  "terminal_id": "TDB-PAX-SITE01-01",
  "operator_username": "pos_site01",
  "site_id":    "{нэвтрэлтийн дараа bootstrap-аас СОНГОСОН — хатуу бичихгүй}",
  "lane_no":    2,
  "barrier_id": "{сонгосон эгнээний хаалт — bootstrap lanes[].barrier_id}"
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
