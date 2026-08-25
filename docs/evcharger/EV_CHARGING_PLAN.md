# EV цэнэглэгч — техникийн холболт ба хөгжүүлэлтийн төлөвлөгөө

> Огноо: 2026-08-25 (v2 — цэвэр техникийн хувилбар)
> Хамрах хүрээ: нэг зогсоолд суурилуулсан **10 × 40 kW Winline UX DC** цэнэглэгч.
> **Хамрахгүй:** эрх зүйн зөвшөөрөл, тарифын өртөг, ашгийн тооцоо — эдгээрийг
> тусад нь шийднэ. Энэ баримт нь *одоогийн систем шинэ үйлчилгээг хэрхэн авч,
> төлбөрийг нь хэрхэн тооцох* тухай.
> v1 (эрх зүй + өртгийн судалгаа орсон) хувилбар: `git show 7066401`.

---

## 0. Шийдэгдсэн зүйлс

| Асуудал | Шийдэл |
|---|---|
| Илүү төлсөн мөнгө | **Буцаахгүй** — жолоочийн ДАНС-нд үлдэнэ, дараагийн зогсоол/цэнэглэлтэд зарцуулагдана |
| Данс юугаар танигдах | **Машины дугаар + утасны дугаар** |
| Үнэ | **1 кВт.ц = 1,000₮** → **1 Wh = 1₮** (бүхэл тоо, дугуйлалтгүй) |
| Цэнэглэгчтэй холбогдох | **OCPP 1.6J** — тусдаа **WSS сервер** (device hub) |
| Камер | Мөн адил hub рүү нүүнэ (2-р шатанд, тусдаа) |
| Жолоочийн орох цэг | **Хэвлэсэн QR стикер** (үндсэн) + цэнэглэгчийн HMI дэлгэцийн QR + гарах камерын LED |
| Хаана хөгжүүлэх | Нэг репо, `backend/hub/` шинэ багц |

---

## 1. Гол загвар — «нэг машин, нэг данс»

Одоогийн систем нь **гүйлгээ төвтэй**: зогсолт бүр өөрийн төлбөртэй, төлбөр бүр
өөрийн QPay нэхэмжлэхтэй. Цэнэглэлт нэмэгдэхэд энэ загвар задарна — учир нь
урьдчилж авсан мөнгө нь тодорхой нэг гүйлгээнд харьяалагдахаа больдог.

Тиймээс дунд нь **данс (wallet)** тавина:

```
                    ┌──────────────────────────┐
   QPay төлбөр ────►│  ДАНС                    │
   (нэмэлт цэнэглэх)│  дугаар + утас           │
                    │  үлдэгдэл = N₮           │
                    └────────┬─────────────────┘
                             │ хасалт
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
        цэнэглэлт      зогсоолын       дараагийн
        (Wh × 1₮)      төлбөр          удаа
```

**Дүрэм:**
- Мөнгө данс руу орох, данснаас гарах бүр `wallet_ledger`-т мөр үүснэ — **зөвхөн
  нэмэх (append-only)**, устгахгүй, засахгүй.
- `wallets.balance` нь кэш; үнэн нь ledger-ийн нийлбэр. `tools/wallet_audit.py`
  өдөр бүр тулгана (`tools/debt_audit.py`-ийн загвараар).
- Үлдэгдэл өөрчлөх бүх үйлдэл **нэг транзакцид, `SELECT … FOR UPDATE`** мөрийн
  түгжээтэй — жолооч цэнэглэж байхдаа хаалтаар гарах гэх мэт зэрэгцээ хасалтаас
  хамгаална.

### 1.1 ⚠ Данс нь ТҮРЭЭСЛЭГЧИЙН хүрээнд

Түрээслэгч бүр өөрийн QPay данстай (`services/qpay.py: account_for`). Түрээслэгч
A-ийн данс руу орсон мөнгө түрээслэгч B-ийн зогсоолд зарцуулагдаж болохгүй —
өөр хуулийн этгээдийн хооронд мөнгө шилжинэ.

→ `wallets.tenant_id` заавал. Нэг жолооч 2 операторын зогсоол ашиглавал 2 данстай
болно. UI-д «энэ данс: Их Монгол ХХК» гэж тодорхой бичнэ.

### 1.2 ⚠ Үлдэгдлийг хамгаалах — OTP-гүй шийдэл

Дугаар + утас нь **нууц биш**. Хэн ч өөр хүний дугаарыг бичээд үлдэгдлийг нь
зарцуулж чадах эрсдэл үүснэ. SMS OTP нь msgbill-д цэвэр SMS суваг байхгүйгээс
болж одоогоор боломжгүй.

**Шийдэл: үлдэгдлийг зөвхөн ФИЗИК үйл явдалтай хамт зарцуулна.**

| Зарцуулалт | Ямар физик нотолгоо шаардана |
|---|---|
| Цэнэглэх | Тухайн цэнэглэгчид бууц **үнэхээр залгагдсан** (`StatusNotification: Preparing`) |
| Зогсоолын төлбөр | Гарах камерт **тэр дугаар уншигдсан** |
| Бэлнээр буцааж авах | Оператор баталгаажуулна (касс, audit log) |

Өөрөөр хэлбэл машин байхгүй бол үлдэгдэлд хүрэх боломжгүй. Утасны дугаар нь
зөвхөн **мэдэгдэл ба сэргээлтэд** хэрэглэгдэнэ, эрх олгодоггүй.

### 1.3 Барьцаа (hold)

Цэнэглэлт эхлэхэд зөвшөөрөгдсөн БҮТЭН дүнг данснаас `CHARGE_HOLD` гэж хасна.
Дуусахад бодит дүнг үлдээж, зөрүүг `CHARGE_RELEASE` гэж буцаан нэмнэ.
Ингэснээр цэнэглэж байх зуур тэр мөнгийг өөр газар зарцуулах боломжгүй.

```
20,000₮ hold  ──►  бодит 12,400₮  ──►  7,600₮ release  ──►  үлдэгдэл сэргэнэ
```

---

## 2. Үнийн тооцоо — 1 Wh = 1₮

1 кВт.ц = 1,000₮ гэдэг нь **1 Wh = 1₮**. Энэ нь тооцоог бүхэл тоогоор,
дугуйлалтын алдаагүй болгоно:

```python
amount_mnt = energy_wh * price_per_wh      # price_per_wh = 1 (default)
wh_limit   = amount_mnt // price_per_wh    # 20,000₮ → 20,000 Wh = 20 кВт.ц
```

- Тоолуур OCPP-ээр **Wh** нэгжээр ирдэг (`Energy.Active.Import.Register`) тул
  хөрвүүлэлт огт хийхгүй. Float ашиглахгүй — QPay-ийн 0.0001₮ таслалтын
  алдааны төрөл давтагдахгүй.
- `ev_price_plans.price_per_wh` нь **Numeric** — ирээдүйд 1.2₮/Wh г.м болж болно;
  кодод тогтмол 1 гэж бичихгүй.
- Цагийн бүсчлэл (шөнө хямд) шаардлагатай бол `night_price_per_wh` + цагийн муж.
  Үнэ нь **session эхлэхэд түгжигдэнэ** — дунд нь тариф солигдох нь жолоочид
  нөлөөлөхгүй.

---

## 3. Тусдаа WSS сервер — Device Hub

### 3.1 Яагаад тусдаа

| Шалтгаан | Тайлбар |
|---|---|
| **Deploy давтамж** | autodeploy 2 минут тутам татдаг. Backend restart болгонд 10 цэнэглэгчийн тогтмол WS холболт сална — цэнэглэлтийн дунд бол эрсдэлтэй. Hub нь **ховор шинэчлэгдэх** тусдаа циклтэй. |
| **Event loop** | Одоо синхрон psycopg2-оос болж loop царцахад хаалт 101 секунд болж байсан түүх бий (`main.py: loop_lag_monitor`). Төхөөрөмжийн I/O-г салгавал тэр эрсдэл арилна. |
| **Камер = нэг эзэн** | Dahua RPC session зэрэгцээ хандалтад түгждэг. Hub нь тэр цорын ганц эзэн болно (2026-08-09-ний сервер салгах төлөвлөгөөний S2 роль). |
| **Хэмжээ** | 10 цэнэглэгч × 10 сек = өдөрт 86,400 MeterValues. Core API-г үүгээр бөглөх шаардлагагүй. |

### 3.2 Хариуцлагын хуваарилалт

```
┌─ hub (порт 8100) ────────────────┐      ┌─ core (порт 8000) ──────────────┐
│ • OCPP 1.6J WS терминаци         │      │ • REST API, UI                  │
│ • цэнэглэгчийн холболтын бүртгэл │◄────►│ • данс, ledger, төлбөр          │
│ • түүхий үйл явдал → DB          │  bus │ • зогсолтын логик, тариф        │
│ • команд гүйцэтгэл (алсаас)      │      │ • QPay, e-Barimt                │
│ • (2-р шат) камер RPC + LED      │      │ • тайлан, касс                  │
│ ✗ бизнес логик АГУУЛАХГҮЙ        │      │ ✗ төхөөрөмжтэй ШУУД ярихгүй     │
└──────────────────────────────────┘      └─────────────────────────────────┘
                    └──────── нэг PostgreSQL ────────┘
```

**Гол дүрэм:** hub нь «энэ цэнэглэлт хэдэн төгрөг вэ» гэдгийг МЭДЭХГҮЙ. Тэр зөвхөн
«transaction 41 дээр 12,400 Wh боллоо» гэж бичээд нийтэлнэ. Тооцоог core хийнэ.
Ингэснээр тарифын өөрчлөлт hub-ыг огт хөндөхгүй.

### 3.3 Хоорондын холбоо

- **Өгөгдөл:** нийтлэг PostgreSQL (hub бичнэ, core уншина).
- **Дохио:** Redis pub/sub (`ev:tx:started`, `ev:meter`, `ev:tx:stopped`,
  `cmd:remote-start`). Redis нь сервер салгах төлөвлөгөөнд аль хэдийн P0
  бэлтгэлд орсон — энд анх нэвтэрнэ.
- **Redis-гүй эхлэх нөөц зам:** `charger_commands` DB хүснэгт + `SKIP LOCKED`
  поллинг (`barrier_commands`-ийн загвар). Эхний хувилбарт үүнийг сонговол
  дэд бүтэц нэмэхгүй — Redis-ийг 6-р шатанд нэвтрүүлж болно. **Санал: DB
  дараалалаар эхлэ**, 1 секундын поллинг цэнэглэлтэд хангалттай.

### 3.4 Сүлжээ ба хамгаалалт

```nginx
location /ocpp/ {
    proxy_pass http://127.0.0.1:8100;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 3600s;
    proxy_send_timeout 3600s;
}
```

- `wss://` **заавал** — `ws://` хүлээж авахгүй.
- Цэнэглэгч бүрт өөрийн Basic нууц үг (`Auth1 password`), DB-д `secretbox`-оор
  шифрлэнэ (`services/device_auth.py`-ийн загвар).
- URL дэх `cp_id` ≡ Basic username байх ёстой, өөр бол **403**.
- Нэг `cp_id`-д нэг л идэвхтэй холболт; шинэ нь хуучныг хаана. Холболтын бүртгэл
  **`finally`-д заавал цэвэрлэгдэнэ** — `_open_inflight` леакийн алдаа давтахгүй.
- `BootNotification` давтагдвал exponential backoff.

### 3.5 Failover (2-р шат, камер нүүсний дараа)

`device_leases` хүснэгт: heartbeat 10с, TTL 30с, fencing хүлээлт ~120с.
Хоёр hub зэрэг ажиллахад нэг нь л камер/цэнэглэгчийг эзэмшинэ.
Эхний хувилбарт **нэг hub** — lease-ийг 6-р шатанд.

---

## 4. Репо зохион байгуулалт

### 4.1 Backend

```
backend/
  app/                     # core — одоогийнх, БАРАГ хөндөгдөхгүй
    routers/
      ev_router.py         # ШИНЭ: /api/admin/chargers, /api/admin/ev/*
      wallet_router.py     # ШИНЭ: /api/admin/wallets, касс дээрх үйлдэл
      public_router.py     # + /api/public/ev/*, /api/public/wallet/*
    services/
      ev_billing.py        # ШИНЭ: Wh → ₮, hold/release, тооцоо
      wallet.py            # ШИНЭ: данс, ledger, FOR UPDATE хасалт
    models.py              # + wallets, wallet_ledger, chargers, charge_sessions…

  hub/                     # ШИНЭ СЕРВИС — тусдаа процесс
    main.py                # FastAPI, зөвхөн WS + дотоод health
    ocpp/
      protocol.py          # [2,id,action,payload] хүрээ, CallResult/CallError
      registry.py          # cp_id → холболт (leak-гүй, finally цэвэрлэгээ)
      handlers.py          # BootNotification, Heartbeat, StatusNotification,
                           # Authorize, Start/StopTransaction, MeterValues
      commands.py          # RemoteStart/Stop, ChangeConfiguration,
                           # SetChargingProfile, Reset, UnlockConnector
      config_profile.py    # холбогдмогц тавих тохиргоо (доор §4.3)
    queue.py               # charger_commands дараалал (SKIP LOCKED)
    devices/               # 2-р шат: камерын RPC энд нүүнэ
    __main__.py            # uvicorn hub.main:app --port 8100
```

**Импортын дүрэм:** hub нь `app.models`, `app.database`, `app.config`,
`app.secretbox`-ыг импортлоно (нэг репо, нэг venv, нэг миграц). `app` нь hub-ыг
**хэзээ ч** импортлохгүй — эсрэг чиглэлийн хамаарал үүсвэл салгасан утга алга.

### 4.2 Deploy

```
deploy/
  parking-hub.service      # ШИНЭ systemd unit, порт 8100
  nginx/ocpp.conf          # ШИНЭ location блок
  update.sh                # + hub-ыг тусад нь restart хийх сонголт
```

`autodeploy.sh`-д: `backend/hub/` дотор өөрчлөлт байвал л `parking-hub` restart.
Core-ийн өөрчлөлт цэнэглэгчийн холболтыг таслахгүй.

### 4.3 Цэнэглэгч холбогдмогц автоматаар тавих тохиргоо

`BootNotification` ирмэгц hub нь `ChangeConfiguration`-оор доорхийг тулгана —
гараар HMI дээр тохируулах алдааг үндсээр нь арилгана:

| Түлхүүр | Утга | Яагаад |
|---|---|---|
| `MeterValueSampleInterval` | `10` | Wh watchdog-ийн үндэс |
| `MeterValuesSampledData` | `Energy.Active.Import.Register,Power.Active.Import,SoC` | Тооцоо + UI |
| `HeartbeatInterval` | `300` | nginx 3600с timeout-д багтана |
| `WebSocketPingInterval` | `60` | Үхсэн холболт эрт илэрнэ |
| `AllowOfflineTxForUnknownId` | `false` | Урьдчилсан төлбөртэй тул танихгүйг эхлүүлэхгүй |
| `LocalPreAuthorize` | `false` | Эрхийг зөвхөн бид шийднэ |
| `StopTransactionOnInvalidId` | `true` | Эрхгүй болвол зогсоно |
| `ConnectionTimeOut` | `120` | Залгаагүй үед хүлээх хугацаа |

### 4.4 Frontend

```
frontend/src/pages/
  ev/
    EvCharge.jsx           # нийтийн QR хуудас  /ev/:key/:conn
    EvWallet.jsx           # данс, үлдэгдэл, түүх  /wallet/:token
    EvBoard.jsx            # админ: 10 цэнэглэгчийн амьд самбар
  settings/
    IntegrationsSection.jsx  # «Цэнэглэгч» таб — жагсаалт, QR хэвлэх, алсын команд
```

---

## 5. Өгөгдлийн загвар

```
wallets
  id, tenant_id, plate_number(norm), phone, name,
  balance NUMERIC(12,2), status, created_at, updated_at
  UNIQUE(tenant_id, plate_number)

wallet_ledger                       # append-only, УСТГАХГҮЙ
  id, wallet_id, direction(CREDIT|DEBIT), amount, balance_after,
  kind(TOPUP|CHARGE_HOLD|CHARGE_RELEASE|CHARGE_SETTLE|PARKING|CASH_OUT|ADJUST),
  ref_type, ref_id, operator_id, note, created_at
  INDEX(wallet_id, created_at)

chargers
  id, site_id, device_id, cp_id UNIQUE, serial, vendor, model, fw_version,
  auth_user, auth_pass_enc, connector_count, price_plan_id,
  status, last_boot_at, last_heartbeat_at, ocpp_proto

charger_connectors
  charger_id, connector_id, status, error_code, last_meter_wh,
  active_tx_id, updated_at

charger_commands                    # hub ↔ core дараалал (SKIP LOCKED)
  id, charger_id, action, payload JSON, status, attempts,
  result JSON, created_at, sent_at, done_at

ev_price_plans
  id, tenant_id, site_id, name, price_per_wh NUMERIC(8,4) DEFAULT 1,
  night_price_per_wh, night_from, night_to,
  min_amount, max_amount, idle_grace_min, idle_fee_per_min,
  parking_exempt_mode, parking_exempt_cap_min

charge_sessions
  id, charger_id, connector_id, wallet_id, plate, phone, id_tag UNIQUE,
  authorized_amount, price_per_wh, wh_limit,
  ocpp_tx_id, meter_start_wh, meter_stop_wh, energy_wh, max_power_w,
  soc_start, soc_end, started_at, stopped_at, stop_reason,
  energy_amount, idle_minutes, idle_amount, total_amount,
  status, parking_session_id, payment_id, vat_receipt_id, created_at

ocpp_messages                       # оношилгоо — 7 хоногийн retention
  id, cp_id, direction, action, message_id, payload, created_at
```

### 5.1 Одоогийн загварт хийх засвар

| Юу | Яагаад |
|---|---|
| `Payment.session_id` → **nullable** | Данс цэнэглэх төлбөр нь зогсолтод харьяалагдахгүй |
| `Payment.kind` ШИНЭ (`PARKING`\|`WALLET_TOPUP`\|`EV`) | Тайлан, ээлжийн тооцоо, e-Barimt-д ялгана |
| `ParkingSession.paid_from_wallet` ШИНЭ (bool) | Данснаас хасагдсаныг тайланд ялгах |
| `Device.device_type = 'ev_charger'` | UI-д аль хэдийн бэлэн |
| `AppSetting['ev_rules']` | Глобал default-ууд, UI-аас удирдана |

---

## 6. Урсгалууд

### 6.1 Цэнэглэх (QR стикерээс)

```
1  Жолооч стикерийн QR уншина  →  /ev/A7K2
2  GET  /api/public/ev/A7K2                  төлөв, ₮/кВт.ц, залгаастай эсэх
3  Дугаар + утас оруулна                     → данс олдоно / шинээр үүснэ
                                             → үлдэгдэл харагдана
4a үлдэгдэл ХҮРЭЛЦВЭЛ:                        дүн сонгоод шууд эхлүүлнэ
4b хүрэлцэхгүй бол:                           QPay QR → төлнө → данс цэнэгдэнэ
5  POST /api/public/ev/A7K2/start             {amount}
     ├ ФИЗИК ШАЛГАЛТ: connector.status ∈ (Preparing, SuspendedEV)
     ├ wallet: CHARGE_HOLD −amount  (FOR UPDATE)
     ├ charge_session үүснэ, wh_limit = amount / price_per_wh
     └ charger_commands ← RemoteStartTransaction(connectorId, idTag)
6  hub → цэнэглэгч → Authorize(idTag) → StartTransaction(meterStart)
7  MeterValues бүрд:  energy = meter − meter_start
     └ energy ≥ wh_limit × 0.98  →  RemoteStopTransaction
8  StopTransaction(meterStop, reason)
     ├ energy_amount = energy_wh × price_per_wh
     ├ wallet: CHARGE_RELEASE +(amount − energy_amount)
     ├ CHARGE_SETTLE  (ledger-т бодит зарцуулалт)
     └ e-Barimt бодит дүнгээр
9  SMS/дэлгэц: «12,400 Wh · 12,400₮ · үлдэгдэл 7,600₮»
```

### 6.2 Зогсоолоос гарах — данснаас автомат хасалт

Одоо: гарах камер дугаар уншина → `amount_due` → жолооч QR-аар төлнө → хаалт.
Шинээр: **дунд нь данс шалгах алхам** орно.

```
гарах камер: дугаар уншигдав
  └► session_fee_info() → amount_due = 5,000₮
     └► wallet олдов, үлдэгдэл 7,600₮  ≥ 5,000₮
        ├ ledger: PARKING −5,000  (FOR UPDATE, нэг транзакц)
        ├ session.status = PAID, paid_from_wallet = true
        ├ e-Barimt
        └ хаалт НЭЭГДЭНЭ — жолооч юу ч хийхгүй
     └► үлдэгдэл ХҮРЭЛЦЭХГҮЙ (2,000₮)
        ├ 2,000₮-г данснаас хасна
        ├ үлдсэн 3,000₮-д QPay QR гаргана (одоогийн урсгал)
        └ төлөгдмөгц хаалт
```

Энэ нь ЕВ-гүй жолоочид ч ажиллана — данс нь цэнэглэлтээс хамаарахгүй бие даасан
боломж болно.

### 6.3 Зогсоолын төлбөрөөс чөлөөлөх

`services/nested.py`-ийн `pause_session()` / `resume_session(cap)`-ыг дахин
ашиглана — өдрийн дээд хязгаар (`transit_max_hours`) хүртэл бэлэн.

**⚠ Гол нюанс:** тоолуур зөвхөн `StatusNotification: Charging` төлөвт зогсоно.
`SuspendedEV` (машин дүүрсэн), `SuspendedEVSE`, `Finishing` үед **шууд
үргэлжилнэ** + сул зогсолтын тоолуур эхэлнэ. Үгүй бол дүүрсэн машинаа залгаад
орхиод өдөржин үнэгүй зогсох схем үүснэ.

Горимууд (`ev_price_plans.parking_exempt_mode`, зогсоол тус бүрээр):

| Горим | Логик |
|---|---|
| `PAUSE` | Цэнэглэж байх хугацаанд тоолуур зогсоно, өдрийн таг (ж: 120 мин) |
| `FREE_MINUTES` | 1 кВт.ц тутамд N минут үнэгүй, дээд хязгаартай |
| `DISCOUNT` | ≥X кВт.ц цэнэглэвэл зогсоолын төлбөрөөс Y% |
| `NONE` | Чөлөөлөлтгүй |

Дугаарыг зогсолттой холбохдоо `normalize_plate` + Рашбулагт бичсэн OCR-fuzzy
логик. Олдохгүй бол цэнэглэлт хэвийн явж, чөлөөлөлт л хэрэгжихгүй.

### 6.4 «Дүн дуустал» — 3 давхар хамгаалалт

1. **Watchdog** — `MeterValues` дээр 98%-д зогсоох команд. 40 kW дээр 10 секунд
   ≈ 110 Wh ≈ 110₮; хэтрэлтийг систем дааж, жолоочид **хэзээ ч илүү нэхэмжлэхгүй**.
2. **`SetChargingProfile`** — `chargingSchedule.duration = wh_limit / чадал × 1.3`.
   Сүлжээ тасарсан ч цэнэглэгч өөрөө зогсоно.
3. **Локал** — `offlineLimitTime`, `unsettleLimitNums`.

Мөн: `RemoteStartTransaction`-оос хойш **90 секунд**-д `StartTransaction` ирэхгүй
бол hold-ыг бүрэн буцаана (`CHARGE_RELEASE`) — жолоочийн мөнгө гацахгүй.

### 6.5 Офлайн ба давхардал

- Холболт сэргэхэд цэнэглэгч хуримтлагдсан `StopTransaction`-уудаа дараалуулж
  илгээнэ → **`ocpp_tx_id`-аар давхардлыг шүүнэ** (Ontime POS-ийн давхардлын
  сургамж).
- `charge_sessions.ocpp_tx_id` дээр UNIQUE индекс — давхар тооцоо DB түвшинд
  боломжгүй.

---

## 7. Дэлгэц ба QR

### 7.1 Хэвлэсэн QR стикер (үндсэн суваг)

- Цэнэглэгч бүрийн холбогч бүрд өвөрмөц богино код: `/ev/A7K2`
  (`charger_key` + connector-ыг агуулна, таамаглах боломжгүй).
- `GET /qr/ev/{key}.png` — `public_router`-ийн `/qr/{site_code}.png`-ийн загвараар.
- Админд **«QR хэвлэх» товч**: 10 цэнэглэгчийн стикерийг A4 хуудсанд
  (нэр, дугаар, QR, заавар) нэг PDF болгож гаргана.

### 7.2 Цэнэглэгчийн HMI дэлгэц

Гарын авлагын §2.2.5-д `[1QR code]` / `[2QR code]` — дэлгэц дээр гарах QR-ын
агуулгыг **бид тавьж болно**. Стикертэй ижил URL тавина (нэг нь гэмтвэл нөгөө нь
ажиллана).

Мөн `feeModuleshow`, `Info Unit Hide`, `Info Cost Hide` = off → цэнэглэгчийн
дэлгэц дээрх ₮ дүнг НУУНА. Валютын жагсаалтад ₮ байхгүй тул тэнд буруу тоо
харагдана; төлбөрийг зөвхөн манай хуудас/LED харуулна.

### 7.3 Камерын LED дэлгэц

LED нь Dahua камерын дотор (`trafficParking.setScreenDisplay`,
`services/barrier.py:221`, `render_screen_text` :1006) — 3 мөр текст. Гарах
хаалтанд одоо «дугаар / хугацаа / төлбөр» гардаг.

EV-д нэмэлт орлуулагч: `{balance}`, `{ev_kwh}`.

```
1234 UBA
CENEGLEV 12.4 kWh
USDEGDEL 7600T
```

⚠ Кирилл ажилладаг нь 2026-07-29-нд батлагдсан ч ₮ тэмдэг дэмжигдэхгүй тул `T`.
LED нь **заавал биш RPC** — камер завгүй үед алгасагдана (одоогийн
`optional RPC` дүрэм хэвээр).

---

## 8. API гадаргуу

```
# hub (8100) — зөвхөн машин↔машин
WS   /ocpp/1.6/{cp_id}                        Basic auth
GET  /internal/health                          холболтын тоо, сүүлийн heartbeat

# Нийтийн (жолооч)
GET  /api/public/ev/{key}                      цэнэглэгчийн төлөв, үнэ
POST /api/public/ev/{key}/lookup               {plate, phone} → данс, үлдэгдэл
POST /api/public/ev/{key}/start                {amount} → эхлүүлэх
GET  /api/public/ev/session/{token}            амьд явц (Wh, ₮, SOC, үлдсэн)
POST /api/public/ev/session/{token}/stop       гараар зогсоох
GET  /api/public/wallet/{token}                үлдэгдэл + сүүлийн 20 хөдөлгөөн
POST /api/public/wallet/{token}/topup          QPay нэхэмжлэх үүсгэх

# Админ / касс
GET  /api/admin/chargers                       жагсаалт + амьд төлөв
POST /api/admin/chargers/{id}/command          reset | unlock | stop-tx | config
GET  /api/admin/chargers/{id}/qr.pdf           стикер хэвлэх
GET  /api/admin/ev/sessions                    түүх, шүүлт, Excel экспорт
GET  /api/admin/wallets                        хайлт (дугаар/утас)
POST /api/admin/wallets/{id}/adjust            гар засвар (audit log-той)
POST /api/admin/wallets/{id}/cash-out          бэлнээр буцаах (касс)
GET/POST /api/admin/ev/price-plans             тариф

# Түншийн (OCPP-гүй гуравдагч цэнэглэгчид — UI-д амласан хэвээр)
POST /api/v1/chargers/{charger_key}/plug-in    {plate}
POST /api/v1/chargers/{charger_key}/plug-out   {plate}
```

---

## 9. Шат дараалсан төлөвлөгөө

### Шат 1 — Hub-ийн араг яс + OCPP уншилт (2 долоо хоног)
- `backend/hub/` багц, `parking-hub.service`, nginx `/ocpp/`
- WS endpoint + Basic auth + `registry.py` (leak-гүй, `finally` цэвэрлэгээ)
- `BootNotification`, `Heartbeat`, `StatusNotification`, `MeterValues`,
  `Authorize`, `Start/StopTransaction` — хүлээж авах, DB-д бичих
- `chargers`, `charger_connectors`, `ocpp_messages` + 7 хоногийн retention
- Холбогдмогц §4.3-ийн тохиргоог автоматаар тулгах
- Админ самбар: 10 цэнэглэгчийн амьд төлөв
- **Шалгуур:** HMI-аас гараар RFID-аар цэнэглэхэд манай дэлгэцэнд бодит цагийн
  Wh, чадал харагдана; дуусахад зөв нийт Wh бичигдэнэ. Мөнгө оролцохгүй.

### Шат 2 — Данс (мөнгө, цэнэглэгчгүй) (1.5 долоо хоног)
- `wallets`, `wallet_ledger`, `services/wallet.py` (FOR UPDATE, append-only)
- `Payment.session_id` nullable + `Payment.kind` миграц
- QPay-ээр данс цэнэглэх нийтийн хуудас `/wallet/:token`
- Гарах хаалтанд **автомат хасалт** (§6.2) — үлдэгдэл хүрэлцвэл QR огт гарахгүй
- Касс дээр: хайх, гар засвар, бэлнээр буцаах (audit log)
- `tools/wallet_audit.py` — ledger ↔ balance тулгалт
- **Шалгуур:** цэнэглэгчгүйгээр данс ажиллана; 20 гарц дээр автомат хасалт зөв.

### Шат 3 — Алсын удирдлага + Wh хязгаар (1.5 долоо хоног)
- `charger_commands` дараалал (SKIP LOCKED), hub-ийн 1 сек поллинг
- `RemoteStart/Stop`, `ChangeConfiguration`, `SetChargingProfile`, `Reset`,
  `UnlockConnector`
- Watchdog: 98% + charging profile + 90 сек эхлэхгүй бол hold буцаах
- **Шалгуур:** «5,000 Wh» өгөхөд 4,900–5,100 дээр зогсоно; сүлжээ таслахад
  charging profile ажиллана; hold зөв суларна.

### Шат 4 — QR урсгал + тооцоо (2 долоо хоног)
- `charge_sessions`, `ev_price_plans` + тарифын админ UI
- Нийтийн хуудас `/ev/:key` — дугаар, утас, үлдэгдэл, дүн, амьд явц
- Физик шалгалт (§1.2), hold → release → settle (§1.3)
- e-Barimt бодит дүнгээр (msgbill `Үйлчилгээ 3`, Idempotency-Key = session id)
- QR стикер PNG/PDF үүсгэх + HMI-ийн `1QR code` тохируулах
- **Шалгуур:** 10 бодит гүйлгээ; ledger-ийн нийлбэр ↔ балансын зөрүү 0.

### Шат 5 — Чөлөөлөлт, сул зогсолт, LED (1 долоо хоног)
- 4 горим + `Charging`↔`SuspendedEV` төлөвт суурилсан pause/resume
- Сул зогсолтын хураамж
- LED-д `{balance}`, `{ev_kwh}` орлуулагч
- **Шалгуур:** дүүрсэн машин залгаастай үлдэхэд зогсоолын тоолуур **ажиллана**.

### Шат 6 — Ачаалал, failover, камер нүүлгэлт (2 долоо хоног)
- Талбайн нийт чадлын таг (`ChargePointMaxProfile`) + динамик хуваарилалт
- `device_leases` + Redis pub/sub
- Камерын RPC эзэмшлийг hub рүү нүүлгэх (**тусдаа төсөл гэж үзэх**)
- Тайлан: ашиглалт %, Wh, орлого, оргилын график

**Нийт ~10 долоо хоног.** Шат 1–2 нь бие биеэсээ хамааралгүй — зэрэг явж болно.

---

## 10. Эрсдэл

| Эрсдэл | Хамгаалалт |
|---|---|
| Хэн нэгэн өөр дугаар бичээд үлдэгдэл зарцуулах | §1.2 физик шалгалт — залгагдсан / камерт уншигдсан байх |
| Зэрэгцээ хасалт (цэнэглэж байхад гарах) | `FOR UPDATE` + hold |
| Balance ↔ ledger зөрөх | append-only + өдрийн `wallet_audit.py` |
| Deploy үед холболт тасрах | hub тусдаа сервис, ховор шинэчлэлт |
| Офлайнаас ирсэн давхар StopTransaction | `ocpp_tx_id` UNIQUE |
| MeterValues-ээр диск дүүрэх | 7 хоногийн retention эхнээс нь |
| Дүүрсэн машин байрыг эзэлнэ | Сул зогсолтын хураамж + чөлөөлөлт зогсох |
| Firmware OCPP-г бүрэн дэмжихгүй | Шат 1-ийг **нэг** цэнэглэгч дээр батлаад дараа тарах |

---

## 11. Дараагийн шууд алхам

1. Нэг цэнэглэгчийн HMI-аас `Server url`-ыг тест сервер рүү чиглүүлж, `wss`
   холболт тогтох эсэхийг nginx + echo-оор батлах (код бичихгүй).
2. Winline-аас OCPP-ийн яг хувилбар + дэмждэг мессежийн жагсаалтыг авах.
3. Батлагдмагц **Шат 1** ба **Шат 2**-ыг зэрэг эхлүүлнэ.
