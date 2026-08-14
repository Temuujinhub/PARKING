# Камерын зураг авах замууд — 2026-08-13/14-ний бүрэн судалгаа

**Дүгнэлт: `snapshot.cgi` бол энэ флотын цорын ганц БАТЛАГДСАН зургийн зам.**
Долоон аргыг production дээр туршсан. Зургаа нь эцэслэн хаалттай; №2 (event
`codes` хувилбарууд) нь ЭРГЭЛЗЭЭТЭЙ ХЭВЭЭР — доорх «Хэмжилтийн сул тал»-ыг
хараарай. Энэ файлын зорилго — эцэслэгдсэн замаар дахин судалгаа явуулахгүй
байх, эцэслэгдээгүйг нь ЗӨВ аргаар дахин хэмжих.

## Хэмжилтийн сул тал (2026-08-14-нд илэрсэн)

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
| 1 | `eventManager.cgi` + `httptype=multipart` | **JPEG ирэхгүй** | 20/20 камер `200` хүлээж авсан. `selftest`: «6 event, 0 зураг». Event-д `WithSnap=True` гэж бичсэн ч хавсралт илгээдэггүй |
| 2 | `codes=[TrafficTollGate]` / `[Traffic]` attach | **эргэлзээтэй** | 200 буцаагаад 518б heartbeat л өгсөн. ГЭХДЭЭ тэр цонхонд машин орсон эсэхийг хянаагүй — `[TrafficJunction]` ганцаараа ч 0 event өгсөн нь хэмжилтийн сул тал болохыг харуулна. Детерминист дахин хэмжилт шаардлагатай (`--compare` нь одоо Test Capture-ээр event үүсгэдэг) |
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

№1, №3-№7 замыг ДАХИН турших шаардлагагүй — эцэслэгдсэн. №2 (event `codes`)
нь дээрх сул талын улмаас ДАХИН хэмжигдэх ёстой:

```bash
sudo systemctl stop parking-backend
sudo .../tools/stream_dump.py 10.0.106.10 30 --compare    # Test Capture-тэй
sudo systemctl start parking-backend
```

Шинэ санаа гарвал эхлээд эдгээр хэрэгслээр баримт цуглуул:

```bash
sudo /root/PARKING/backend/venv/bin/python /root/PARKING/tools/rpc_snap_probe.py <ip>
sudo /root/PARKING/backend/venv/bin/python /root/PARKING/tools/camera_records_diag2.py <ip>
cd /root/PARKING/backend && venv/bin/python tools/selftest.py --site <КОД>
```
