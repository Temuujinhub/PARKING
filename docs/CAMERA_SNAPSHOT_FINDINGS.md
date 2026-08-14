# Камерын зураг авах замууд — 2026-08-13/14-ний бүрэн судалгаа

## ✅ ШИЙДЭГДЛЭЭ (2026-08-14 19:15) — ажиллах жор

Зураг comet сувгаар JSON доторх base64-ээр ирдэг. Production дээр
батлагдсан: 40 секундэд **6 зураг, тус бүр 687–695 KB**.

```python
# 1. RPC2-оор нэвтэрч сешн авна (энгийн global.login, шифрлэлт ХЭРЭГГҮЙ)
session = await rpc.login()                     # 32 hex

# 2. Хүлээж авах хоолойг НЭЭНЭ (зургийн суваг = type=1)
GET /SubscribeNotify.cgi?sessionId={session}&type=1
    Cookie: WebClientHttpSessionID={session}
    → 200 «subscribe Successfully!», холболт нээлттэй үлдэнэ

# 3. ТЭР ЖИГД сешн дээр RPC2-оор захиална
snapManager.factory.instance          → object id
snapManager.attachFileProc  params={"filter": {"Channels": [0],
                                               "Types": ["jpg"]},
                                    "proc": 1}
                            object=<instance>    → result=True

# 4. Хоолойгоор ирнэ:
#    <script>var json={…};receiveMessage(json);</script>
#    method = "client.notifySnapFile"
#    params.Base64 = "/9j/4AAQSkZJRg…"   ← ЖИНХЭНЭ JPEG
```

Нэг мессежид **зураг ба улсын дугаар хоёулаа** байна:

| Талбар | Утга |
|---|---|
| `params.Base64` | JPEG (base64) — 687–695 KB |
| `params.info.Image[0]` | `Width`, `Height`, `Length`, `Offset`, `Type` |
| `params.info.PicID[0..1]` | зургийн ID (`ackUpload`-д хэрэгтэй) |
| `params.info.SOF` / `EOF` | эхлэл/төгсгөлийн тэмдэг — **олон хэсэгт хуваагдаж болно** |
| `params.info.Ack` / `Transfer` | баталгаажуулалтын горим |
| `params.info.Events[0].Data.TrafficCar.PlateNumber` | улсын дугаар |
| `params.info.Events[0].Code` | `TrafficJunction` / `TrafficManualSnap` |

Анхаарах зүйлс интеграц хийхээс өмнө:

- **`SOF`/`EOF`/`Offset`/`Length`** — том зураг олон мессежээр хуваагдаж
  ирж болно. Одоогийн 6 дээж бүгд нэг мессежид багтсан ч кодыг угсралттай
  бичих ёстой.
- **`ackUpload(PicID, ClientID, ClientIP, true)`** — вэб UI зураг авсныг
  баталгаажуулдаг. Хийхгүй бол камер илгээхээ болих магадлалтай.
- **Урсгалын хэмжээ**: event тутам ~690 KB. 22 камерт байнга асаавал
  диск/сүлжээ мэдэгдэхүйц ачаална ([диск дүүрэх эрсдэл](#) аль хэдийн бий).
- **`snapshot.cgi` хэвээр хэрэгтэй** — comet нь зөвхөн event үед зураг
  өгнө; оператор дурын үед «одоо харах» гэвэл өөр зам алга.

Шалгах хэрэгсэл:

```bash
sudo .../tools/subscribe_notify_probe.py 10.0.105.10 --seconds 40
sudo .../tools/subscribe_notify_probe.py --parse /tmp/subnotify/sub_snap.raw
```

## ⚠ 2026-08-14 орой — ДООРХ ДҮГНЭЛТ ХАГАС БУРУУ

DevTools-ийн `blob:` хариунаас олдсон нь:

```
blob:http://10.0.105.10/cb680b4c-…   Content-Length: 753,949
эхний байт: JFIF        → ЖИНХЭНЭ JPEG
дотор нь:   DHAV, DH_ITC, "Pulse", "Class", "ExtraPlateNumber", "ParkType"
```

Өөрөөр хэлбэл **зураг ба event НЭГ урсгалаар ирдэг** — гэхдээ бидний бүх
туршилтын сувгаар (`eventManager.cgi`) биш, **`SubscribeNotify.cgi`**-ээр.
Доорх 7 замын судалгаа бүхэлдээ БУРУУ СУВАГТ хайсан байна.

Вэб UI-ийн хүсэлт:

```
/SubscribeNotify.cgi?Security-cgi=2&salt=<512 hex>&content=<base64>
                    &cipher=RPAC-256&time=<ms>&link=1
```

`salt`/`content` нь ПАРАМЕТРИЙГ шифрлэсэн; ХАРИУ нь ил (DHAV + JPEG).
Шалгах хэрэгсэл: `tools/subscribe_notify_probe.py`.

### Comet сувгийн БАТЛАГДСАН байдал (2026-08-14 орой)

Шифрлэлт огт хэрэггүй нь тогтоогдов — вэб UI-ийн JS-д (`initComet`) ил зам
байна:

| Алхам | Үр дүн |
|---|---|
| `/SubscribeNotify.cgi?sessionId=<RPC2 сешн>` | ✅ `200` + `subscribe Successfully!` |
| Сешнгүй | ❌ `287637505 Invalid session in request data!` |
| Тэр сешн дээр `eventManager.attach` | ✅ `result=True` |
| Comet-оор event урсав | ✅ `TrafficJunction`, `TrafficManualSnap` (2.2KB/мессеж) |
| **Event дотор зураг** | ❌ 116 талбар, хамгийн урт нь 19 байт |
| **Event дотор зургийн ЗАМ** | ❌ `url`/`urlCarPano`/`FilePath` АЛГА |

Зургийн оронд `YuvPacket.AddrY/AddrU/AddrV`, `PhyAddrY…`, `Stride`, `Width`,
`Height` — өөрөөр хэлбэл **камерын дотоод санах ойн заагч**. Сүлжээгээр
татах боломжгүй; тэр кадрыг JPEG болгож notify сувагт түлхэх ажлыг зөвхөн
`snapManager` хийж чадна.

### Үлдсэн ГАНЦ саад: `snapManager.attachFileProc`-ийн параметр

```
snapManager.attach / attachFile / subscribe → 268894210 "Method not found!"
snapManager.attachFileProc                  → -267976701, message ""
```

Хоёр алдаа өөр учир **`attachFileProc` метод БАЙНА**, зөвхөн параметр нь
буруу. 5 параметрийн хувилбар × 2 объект (`factory.instance` = 57985996) ×
2 суваг = 20 оролдлого бүгд ижил `-267976701` өгсөн.

**Таамаглахаа болих ёстой.** Параметрийг вэб UI-ийн JS-ээс уншина:
DevTools → Sources → `Ctrl+Shift+F` → `attachFileProc`.

Доорх хэсэг нь `eventManager.cgi` сувгийн тухайд ХҮЧИНТЭЙ ХЭВЭЭР — тэр
сувгаар зураг ирдэггүй нь баттай.

---

**Дүгнэлт (`eventManager.cgi` сувгийн хүрээнд): `snapshot.cgi` бол цорын
ганц БАТЛАГДСАН зургийн зам.**
Долоон аргыг production дээр туршсан. Зургаа нь эцэслэн хаалттай; №2 (event
`codes` хувилбарууд) нь ЭРГЭЛЗЭЭТЭЙ ХЭВЭЭР — доорх «Хэмжилтийн сул тал»-ыг
хараарай. Энэ файлын зорилго — эцэслэгдсэн замаар дахин судалгаа явуулахгүй
байх, эцэслэгдээгүйг нь ЗӨВ аргаар дахин хэмжих.

## Хэмжилтийн сул тал (илэрч ЗАСАГДСАН)

`--compare` анхны хувилбар нь codes тус бүрийг 30 секунд сонсоод «0 event»
гарвал «код ажиллахгүй» гэж дүгнэдэг байв. Гэтэл тэр цонхонд **машин орсон
эсэхийг хянаагүй**. Хамгийн тод шинж: `[TrafficJunction]` ГАНЦААРАА ч 0 event
өгсөн — тэр нь ажилладаг хослолын ДЭД ОЛОНЛОГ учраас код нь буруутай байх
боломжгүй. Өөрөөр хэлбэл тэгүүд нь «машин ирээгүй» гэсэн үг байж таарна.

Зөв арга: камерын вэб UI-ийн `Live → Device Test → Test Capture` нь хиймэл
ANPR event үүсгэдэг. `--compare` одоо түүнийг хувилбар бүрд автоматаар (эсвэл
операторын товшилтоор) дуудаж, event БАТАЛГААТАЙ болсны дараа хэмжинэ.

Туршсан төхөөрөмж: Dahua ITC (Рашбулаг `10.0.106.10` = `sysadmin`,
Моннис `192.168.6.10` = `admin`) — өөр firmware, өөр сүлжээ, ижил дүн.

## Туршсан долоон зам

| # | Зам | Үр дүн | Тайлбар |
|---|---|---|---|
| 1 | `eventManager.cgi` + `httptype=multipart` | **JPEG ирэхгүй (эцэслэсэн)** | `Content-Type: multipart/x-mixed-replace; boundary=myboundary` зөв ирдэг. Test Capture-ээр БАТАЛГААТАЙ event үүсгэж 2 удаа давтахад: 5,367б / 1 event / **0 JPEG**. Event-д `WithSnap=True` гэж бичсэн ч хавсралт огт илгээдэггүй |
| 2 | `codes=[TrafficTollGate]` / `[Traffic]` attach | **эцэслэн татгалзсан** | Test Capture-ээр event БАТАЛГААТАЙ үүсгэж 2 удаа давтсан: `[TrafficJunction...]` → 1 event, бусад → 518б/0 event. Зөвхөн `TrafficJunction` агуулсан код event өгдөг |
| 3 | `mediaFileFind` (6 хувилбар) | **хоосон** | `findFile=False, infos=0`. `storage.getDeviceAllInfo` алдаа — **хадгалах төхөөрөмж алга** |
| 4 | `snapManager.cgi?action=attachFileProc` (CGI) | **400 / 500** | 5 параметрийн хувилбар бүгд |
| 5 | `snapManager.postSnap` (RPC2) | **Method not found** | 4 параметрийн хувилбар, ХОЁР firmware дээр ч |
| 6 | `Snapshot.getSnapshot`, `trafficSnap.manualSnap`, `trafficSnap.getSnapPicture` | **байхгүй** | `admin` камер: «Method not found». `sysadmin` камер: «Authority:check failure» — гэхдээ [эрхийн асуудал БИШ](#эрхийн-таамаг-няцаагдсан) |
| 7 | `RPC2_Loadfile` + `FilePath` | **зам алга** | `RecordFinder.doFind` бичлэгт `FilePath`/`PicName`/`ImageURL` талбар байхгүй, зөвхөн `RecNo` |

## Эрхийн таамаг няцаагдсан

`sysadmin` (бидний 2026-08-11-нд үүсгэсэн данс) дээр зарим метод
«Authority:check failure» өгсөн тул «данс эрхгүй» гэж таамагласан. Камерын
вэб UI-аар шалгахад:

- `sysadmin` нь **`admin` группт**
- System эрхийн **бүх чагт тавигдсан** (Account, System, System Info,
  File Backup, Storage, Event, Network, Peripheral, Camera, Security,
  Maintenance, Manual Control)

Мөн `admin` данстай камер дээр ижил методууд «Method not found» өгсөн.
Тэгэхээр ялгаа нь **данс биш, firmware**. Нэвтрэлт сэлгэсэн нь ямар нэг
эрхийг эвдээгүй.

## `snapshot.cgi`-ийн «татгалзах» төлөв

Амарсан камер эхний дуудлагад 300-700KB JPEG өгөөд, дараалсан дуудлагад
шууд (<0.1с) `400` буцаана. Тэр агшинд `factory.getCollect` нь `200`,
RPC2 login/RecordFinder/trafficSnap бүгд ажиллаж байдаг — өөрөөр хэлбэл
**веб сервер амьд, зөвхөн зургийн дэд систем татгалзана**.

Хурдасгадаг хүчин зүйл (2026-08-13-нд зассан):

- `camera_health` нь хуваалцсан клиент/RPC түгжээг тойрч ӨӨРИЙН холболт
  нээж, 6 камерыг зэрэг цохидог байсан → `0bf3e09`
- `_rpc_lock` / `camera_client` нь event loop хооронд хуваалцагдаж унадаг
  байсан → `ef91ab8`
- reboot-ын cooldown санах ойд байсан тул restart бүрд тэглэгдэж 15 минут
  тутам reboot хийгддэг байсан → `faba088`

Эмчилгээ нь **reboot** (модулийн толгойд баримтжуулсан: reboot-ийн дараа
`snapshot.cgi` 0/10 → 10/10). Тиймээс `camera_health`-ийн авто reboot нь
ЗӨВ шийдэл хэвээр — 120 минутын cooldown-той.

## Практик дүгнэлт

1. **`PARKING_SNAPSHOT_CGI_FALLBACK=false` БҮҮ тавь** — тавибал зураг огт
   авагдахаа болино. `snapshot.cgi` бол цорын ганц зам.
2. **Авто reboot АСААЛТТАЙ байх ёстой** (120 мин cooldown). Гацсан зургийн
   дэд системийг өөрөөр сэргээх арга мэдэгдээгүй.
3. «Manual Snapshot» бичлэг камер дээр үүссээр байх нь зайлшгүй — event
   бүрд `snapshot.cgi` дуудагдана.
4. Зургийн амжилт ~66%. Үүнээс дээш гаргах үлдсэн боломж: камерын firmware
   шинэчлэх, эсвэл RTSP-ээс кадр авах (22 камерт ffmpeg — 1 vCPU серверт
   үнэтэй, зөвхөн шаардлага гарвал).

## Дахин судлахаас өмнө

Долоон замыг ДАХИН турших шаардлагагүй — бүгд Test Capture-ээр детерминист
хэмжилтээр эцэслэгдсэн (2026-08-14, хоёр удаа давтсан).

ҮЛДСЭН ГАНЦ туршаагүй суваг: **ONVIF** (`tools/onvif_snap_probe.py`). ONVIF нь
Dahua-гийн CGI-гээс ТУСДАА дэд систем бөгөөд камерт «ONVIF User» гэсэн тусдаа
данс ч байдаг. CGI-ийн зургийн зам эвдэрсэн үед ONVIF-ийнх ажиллаж болзошгүй.

```bash
sudo .../tools/onvif_snap_probe.py 10.0.106.10
```

Шинэ санаа гарвал эхлээд эдгээр хэрэгслээр баримт цуглуул:

```bash
sudo /root/PARKING/backend/venv/bin/python /root/PARKING/tools/rpc_snap_probe.py <ip>
sudo /root/PARKING/backend/venv/bin/python /root/PARKING/tools/camera_records_diag2.py <ip>
cd /root/PARKING/backend && venv/bin/python tools/selftest.py --site <КОД>
```
