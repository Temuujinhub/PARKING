# Dahua камерын ITSAPI тохиргоо — талбар бүрийн утга

Гарах/орох камерыг Easy Parking системд холбох. Камер бүрийн Web UI дээр
(браузераар камерын IP руу орж, admin-аар нэвтрэх) дараах тохиргоог хийнэ.

## Урьдчилсан нөхцөл

1. Камер парктны сервер (172.16.100.21) руу сүлжээгээр хүрэх ёстой.
   Камер дээр туршина: `System → ...` эсвэл камерын ping хэрэгсэл. Хүрэхгүй бол
   байгууллагын сүлжээнд 10.0.113.0/26 ↔ 172.16.100.0/24 routing нэмүүлнэ.
2. Системд нэвтэрч **Тохиргоо → Төхөөрөмж** хуудаснаас камер бүрийн `device_key`-г
   (Callback түлхүүр багана) хуулж авна. Жишээ: `cam-entry-site01`, `cam-exit-site01`.

## Аль камер аль нь вэ

Физик байрлалаар шийднэ:
- **Орох эгнээнд** харсан камер → орох камерын device_key ашиглана
- **Гарах эгнээнд** харсан камер → гарах камерын device_key ашиглана

## Тохиргоо: Network → Platform Access → ITSAPI таб

| Талбар | Утга | Тайлбар |
|---|---|---|
| **Enable** | ✅ ON | ITSAPI идэвхжүүлнэ |
| **Registration** | ⬜ OFF | Handshake шаардлагагүй (хэрэв ON шаардвал доор үзнэ үү) |
| **Heartbeat** | ⬜ OFF | (хэрэв ON бол Heartbeat Interface = `/api/lpr/keepalive`) |
| **Authentication** | ⬜ OFF | device_key-ээр таних тул digest auth хэрэггүй |
| **Protocol Version** | V1.19 | (өөрчлөх боломжгүй) |
| **Platform Server** | `http://172.16.100.21` | ⚠️ Одоо `http://192.168.0.1:7070` байгааг СОЛИНО |
| **Device ID** | (байгаагаар) | Хэвээр |
| **ANPR Info Interface** | `/api/lpr/callback?device_key=cam-entry-site01` | ⚠️ Гол талбар — device_key-ээ зогсоол/чиглэлд тааруулна |
| **Heartbeat Interface** | `/api/lpr/keepalive` | (Heartbeat ON бол) |

### Data хэсэг (доод тал)

| Талбар | Утга |
|---|---|
| **Data Type** | ✅ **ANPR Info** (заавал) · ⬜ Device Basic Info · ⬜ Barrier Opening |
| **ANPR Info Interface** | дээрхтэй ижил: `/api/lpr/callback?device_key=...` |
| **Uploading Info** | ✅ **Plate No.** (заавал) · бусад (Vehicle Color, Time, Accuracy, Vehicle in Blocklist) сонголтоор |

**Apply** дарж хадгална.

## Жишээ — 2 камер

| Камер | IP | Чиглэл | ANPR Info Interface |
|---|---|---|---|
| Камер 1 | 10.0.113.10 | Орох | `/api/lpr/callback?device_key=cam-entry-site01` |
| Камер 2 | 10.0.113.11 | Гарах | `/api/lpr/callback?device_key=cam-exit-site01` |

Хоёуланд **Platform Server = `http://172.16.100.21`** ижил.

## Хэрэв камер `?device_key=...` (query) зөвшөөрөхгүй бол — IP-ээр таних нөөц арга

Зарим firmware interface талбарт `?` тэмдэг оруулахыг зөвшөөрөхгүй. Тэгвэл:
1. ANPR Info Interface = `/api/lpr/callback` (query-гүй)
2. Системд **Тохиргоо → Төхөөрөмж → Засах** дээр тухайн камерын **IP хаяг**-ийг
   бодит утгаар нь оруулна (жишээ 10.0.113.10)
3. Систем POST хүсэлтийн эх IP-ээр камерыг таньж, зөв зогсоол/чиглэлд онооно

## Хэрэв Registration/Heartbeat заавал ON байх шаардвал

Зарим firmware ANPR илгээхийн тулд эхлээд бүртгүүлэхийг шаарддаг:
- **Registration**: ON, Registration Interface = `/api/lpr/register`
- **Heartbeat**: ON, Heartbeat Interface = `/api/lpr/keepalive`, Interval = `30` секунд

Хоёулаа `{"ok":true,"result":true}` (200) буцаадаг тул камер платформыг "амьд" гэж үзнэ.

## Тохиргооны дараа шалгах (маш чухал)

1. Камерт машин уншуулна (эсвэл тестийн машин оруулна)
2. Системд **Лог → Камерын event лог** хуудсыг нээнэ:
   - **Дугаар зөв гарч ирвэл** → бүх зүйл ажиллаж байна ✅
   - **"?" дугаартай, "plate not parsed"** мөр гарвал → камерын JSON формат өөр байна.
     Тэр мөрийг дарж raw JSON-ийг хараад надад/хөгжүүлэгчид хэлнэ — parser-ийг тааруулна.
   - **Юу ч гарч ирэхгүй бол** → камер серверт хүрэхгүй байна (routing/Platform Server буруу)
3. Хяналтын самбарт орох/гарах event шууд харагдах ёстой
4. Орох талд хаалт нээгдэх эсэх, гарах талд Касс дэлгэцэд машин гарч ирэх эсэхийг шалгана

## Snapshot / RTSP (нэмэлт)

- Evidence зураг: камер `microSD`-д хадгална, хэрэгцээтэй бол RTSP-ээр татна:
  `rtsp://admin:{нууцүг}@10.0.113.10:554/cam/realmonitor?channel=1&subtype=0`
