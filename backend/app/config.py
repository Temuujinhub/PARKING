from pydantic_settings import BaseSettings

# Кодод бичсэн default secret — production-д энэ утга үлдвэл startup зогсоно (main.py)
DEFAULT_SECRET_KEY = "change-me-in-production-9f8a7b6c5d4e"


class Settings(BaseSettings):
    # Ерөнхий
    app_name: str = "Easy Parking"
    debug: bool = False
    # ⚠️ Production-д ЗААВАЛ .env-д PARKING_SECRET_KEY-г CSPRNG-ээр тавина
    # (`python3 -c "import secrets;print(secrets.token_urlsafe(48))"`).
    # Доорх default утга үлдвэл main.py startup дээр (debug=False үед) алдаа өгч зогсоно.
    secret_key: str = "change-me-in-production-9f8a7b6c5d4e"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 12  # 12 цаг
    # DB доторх нууц утгын (QPay client_secret, төхөөрөмжийн нууц үг) Fernet түлхүүр.
    # Хоосон бол шифрлэхгүй. Үүсгэх:
    # venv/bin/python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    secret_enc_key: str = ""

    # Database
    database_url: str = "postgresql+psycopg2://parking:parking_secret_2026@localhost:5432/parking"

    # Public pay page-ийн үндсэн URL (QR код энэ URL руу чиглэнэ)
    public_base_url: str = "http://localhost"

    # QPay v2 (developer.qpay.mn)
    # Камерын нэвтрэлт амжилтгүй (401) үед дахин холбогдох хүртэл хүлээх хугацаа.
    # Dahua камер олон удаагийн буруу оролдлогод бүртгэлийг түгждэг тул урт байна —
    # эс бол зөв нууц үг оруулсны дараа ч түгжээ тайлагдахгүй.
    camera_auth_retry_sec: int = 300
    # Хэдэн дараалсан ЭРХИЙН алдааны дараа тухайн камерт хандахаа түр зогсоох.
    # Dahua нь ихэвчлэн 5 буруу оролдлогын дараа бүртгэлийг түгжинэ — 2 дээр
    # зогсооход түгжигдэх завсар үлдэнэ (нэг камерыг хоёр систем ашиглаж
    # байвал нөгөө системийн нэвтрэлт хохирохгүй).
    camera_auth_fail_limit: int = 2

    # Камерын event stream (eventManager.cgi attach) тохиргоо.
    # Камер нэгэн зэрэг ӨӨР систем рүү (тухайн байгууллагын хуучин платформ г.м)
    # мөн дата илгээж байвал heartbeat саатаж, богино read timeout нь холболтыг
    # дэмий тасалж reconnect-ийн давталт үүсгэдэг. Уртасгавал зэрэгцэн ажиллана.
    camera_event_heartbeat_sec: int = 10   # камерын heartbeat давтамж (CGI параметр)
    # eventManager.cgi-д асуух event-ийн төрөл. [All] нь firmware бүрд ажилладаг тул
    # default. Камерыг ӨӨР СИСТЕМТЭЙ хуваалцаж байгаа/ачаалал ихтэй үед зөвхөн ANPR:
    #   PARKING_CAMERA_EVENT_CODES=[TrafficJunction,TrafficSnapPicture]
    camera_event_codes: str = "[All]"
    # Стримээс салгасан боловсруулах worker-ийн тоо ба дарааллын багтаамж
    # 2 байхад нэг хаалтны команд (15с хүртэл) worker-ийг эзлэхэд дараалал гацаж,
    # event-үүд хоцорч боловсруулагддаг байв (Monnis: хаалт 40с хожуу нээгдэх).
    # Камер-IP тус бүрийн RPC түгжээ давхардлаас хамгаалдаг тул олон worker аюулгүй.
    camera_event_workers: int = 4
    camera_event_queue_size: int = 500
    # Event үүсгэснээс хойш энэ хугацаанаас ХОЖУУ боловсруулагдсан (дараалалд
    # гацсан) event хаалт НЭЭХГҮЙ — машиныг ажилтан гараар оруулчихсан байхад
    # хоосон зам руу хаалт онгойхоос сэргийлнэ (2026-07-28 Monnis: оруулснаас
    # 20с дараа хоосон онгойсон). Session/өрийн бүртгэл хэвийн үүснэ.
    camera_event_stale_open_sec: float = 12.0
    camera_event_read_timeout_sec: int = 120  # энэ хугацаанд юу ч ирэхгүй бол дахин холбоно
    camera_event_reconnect_sec: int = 15   # тасарсны дараа дахин холбогдох хүлээлт

    qpay_sandbox: bool = True  # True=merchant-sandbox.qpay.mn, False=merchant.qpay.mn
    qpay_username: str = ""    # client_id (QPay merchant гэрээнээс, ж: EASY_2PARKING)
    qpay_password: str = ""    # client_secret — зөвхөн .env-д (git-д бүү бич)
    # e-Barimt үүсгэдэг НӨАТ-ийн мэдээлэлтэй нэхэмжлэхийн код (QPay-ээс олгоно)
    qpay_invoice_code: str = "EB_EASY_2PARKING_INVOICE"
    qpay_mock: bool = True     # Бодит credentials байхгүй үед mock горим

    # QPay-ээр дамжуулсан e-Barimt 3.0 (ebarimt_v3/create). True үед локал PosAPI-ийн
    # оронд QPay-ийн ebarimt_v3 endpoint ашиглаж баримт үүсгэнэ (QR-аар төлсөн үед).
    qpay_ebarimt: bool = True
    # tax_type: "1"=НӨАТ тооцогдох, "2"=чөлөөлөгдөх, "3"=0% (2,3 үед VAT тооцохгүй)
    qpay_tax_type: str = "1"
    # Нэхэмжлэх/баримтын мөрийн ангиллын код — GS1: 6743000 "Автомашины зогсоолын үйлчилгээ"
    # (QPay баталгаажуулсан; 5221190 буруу → CLASSIFICATION_CODE_INVALID)
    qpay_classification_code: str = "6743000"
    # Нэхэмжлэх хүлээн авагчийн салбарын код (QPay гэрээнд бүртгэсэн салбар)
    qpay_branch_code: str = "PARKING"
    # Баримтын district_code (үйл ажиллагаа явуулж буй байршил, 4 орон)
    qpay_district_code: str = "3505"

    @property
    def qpay_base_url(self) -> str:
        host = "merchant-sandbox.qpay.mn" if self.qpay_sandbox else "merchant.qpay.mn"
        return f"https://{host}/v2"
    # Webhook нууц токен — тохируулсан бол /qpay/webhook хүсэлтэд ?token= таарах ёстой
    # (QPay callback_url-д нэмж өгнө). Хоосон бол шалгахгүй (mock/туршилтын үед).
    qpay_webhook_secret: str = ""

    # Аюулгүй байдал
    # /api/lpr/simulate туршилтын endpoint. Default=False (production аюулгүй).
    # Хөгжүүлэлт/демо серверт л .env-д PARKING_ALLOW_SIMULATE=true гэж тодорхой асаана.
    allow_simulate: bool = False
    # CORS: production-д домэйноо зааж өгнө (жишээ: "https://test.easy-parking.mn")
    cors_origins: str = "*"

    # e-Barimt — POS API 3.0 (татварын PosAPI сервис локал дээр суусан байна)
    ebarimt_posapi_url: str = "http://localhost:7080/rest"
    ebarimt_merchant_tin: str = ""
    ebarimt_pos_no: str = "10000001"
    ebarimt_branch_no: str = "001"
    ebarimt_district_code: str = "3420"
    ebarimt_classification_code: str = "6743000"  # GS1: Автомашины зогсоолын үйлчилгээ
    ebarimt_mock: bool = True

    # Ээлж солигдох цаг (0–23) — "ээлжээр" тайланд өдрийг энэ цагаар тасалж бүлэглэнэ
    # (жишээ: 9 = өглөө 9ц-аас маргааш 9ц хүртэл нэг ээлжийн өдөр). Шөнө дундаар биш.
    shift_change_hour: int = 9

    # НӨАТ
    vat_rate: float = 0.10
    vat_inclusive: bool = True  # Тарифын үнэ НӨАТ багтсан эсэх

    # Barrier — Dahua ITC камерын RPC2 (trafficSnap.openStrobe/closeStrobe)
    barrier_mock: bool = True  # Бодит төхөөрөмжгүй үед mock
    # Хаалтны RPC2 timeout. 5с богино байсан тул ачаалалтай үед ConnectTimeout/
    # ReadTimeout болж хаалт нээгддэггүй байв (камер ANPR боловсруулалтад завгүй).
    barrier_timeout_sec: float = 12.0
    # ЭХНИЙ оролдлогын timeout — богино байлгаж хурдан дахин оролдоно. Хариу
    # өгөхгүй төхөөрөмж дээр 12с бүтэн хүлээвэл жолооч хаалганы өмнө тэр чигээрээ
    # зогсдог; 4с-д хариу ирээгүй бол дахин илгээх нь илүү хурдан нээдэг
    # (open команд идемпотент — давхар илгээхэд хортой зүйлгүй).
    barrier_first_timeout_sec: float = 4.0
    # Оролдлого БҮРИЙН timeout (жигд). Камер хариу өгөх юм бол 1 секундэд өгдөг;
    # өгөхгүй бол удаан хүлээх нь утгагүй — хурдан дахин илгээх нь илүү үр дүнтэй.
    barrier_attempt_timeout_sec: float = 3.5
    # Камер тус бүрд НЭГЭН ЗЭРЭГ хэдэн HTTP холболт барих вэ. Dahua ITC-ийн веб
    # сервер цөөн холболт л зөвшөөрдөг тул бага байлгана — үйлдэл бүрд шинэ
    # холболт нээвэл камер хариу өгөхөө болино (2026-07-28 MONNIS-ийн шалтгаан).
    camera_max_connections: int = 2
    # Заавал биш RPC (LED дэлгэц, сессийн хяналт) нь сүүлийн RPC-ээс хойш энэ
    # хугацаа өнгөрөх хүртэл хүлээнэ. Учир: камер logout-ын дараа сессийн слотоо
    # ШУУД чөлөөлдөггүй — тэр агшинд нэвтэрвэл «User or password not valid» гэсэн
    # ХУДАЛ хариу авахаас гадна remainLoginTimes буурч ТҮГЖЭЭ рүү ойртдог
    # (2026-07-29 Monnis: LED-ийн 51 алдаа бүгд ийм). 0 = унтраах.
    camera_rpc_gap_sec: float = 2.5
    # Хаалт нээх командын дотор дэлгэц бичихэд өгөх хугацаа (хаалт аль хэдийн
    # нээгдсэн байдаг тул энэ хугацаа хэтэрсэн ч командын үр дүнд нөлөөгүй)
    barrier_screen_timeout_sec: float = 2.0
    # Ижил текстийг энэ хугацаанд камерт ДАХИН бичихгүй (хаалттай хамт бичигдсэнийг
    # дараагийн schedule_display давхардуулахаас сэргийлнэ)
    screen_dedup_sec: float = 10.0
    # Зураг татахын өмнө хаалтны командыг хэдэн секунд хүртэл хүлээх вэ (хаалт
    # тэргүүлэх эрхтэй; машин хаалганы өмнө зогсож байхад зураг 0.5с хожуу
    # татагдах нь хамаагүй). Түүний дараа RPC дараалалд орох хүлээлт.
    snapshot_barrier_wait_sec: float = 3.0
    snapshot_lock_wait_sec: float = 3.0
    # Сүлжээний түр саатлын улмаас команд унасан бол хэдэн удаа дахин оролдох.
    # Машин хаалганы өмнө зогсож байгаа тул нэг оролдлого хангалтгүй.
    # 15с төсөвт 4 оролдлого багтана (3.5с × 4 + 0.4с × 3 ≈ 15.2с)
    barrier_retries: int = 3
    barrier_retry_delay_sec: float = 0.4
    # Хаалт нээх нийт хугацаа энэ хэмжээнээс (мс) удвал WARNING логлоно —
    # «удаан нээгдэж байна» гомдлыг лог дээрээс шууд тоогоор нотлох боломжтой.
    barrier_slow_warn_ms: int = 1500
    # Хаалтны команд нь камерын RPC түгжээг хэдэн секунд хүлээх вэ. Хугацаа
    # дуусвал ХҮЛЭЭЛГҮЙ команд явуулна — хаалт нээх нь бүхнээс чухал.
    barrier_lock_wait_sec: float = 2.0
    # Хаалтны нэг командын НИЙТ дээд хугацаа. Оролдлогууд нийлээд үүнээс
    # хэтрэхгүй — жолооч хаалганы өмнө хязгааргүй хүлээхгүй.
    barrier_total_budget_sec: float = 15.0
    # LPR event ирснээс хаалт нээх/шийдвэр гарах хүртэлх нийт хугацааны WARNING босго (мс)
    lpr_slow_warn_ms: int = 2000
    # Төлбөр баталгаажуулснаас хаалт нээх хүртэлх хугацааны WARNING босго (мс)
    payment_slow_warn_ms: int = 3000
    # Event loop хэдэн мс царцвал WARNING логлох вэ (синхрон DB дуудлага бүх
    # системийг зэрэг зогсоодог — үүнийг тоогоор илрүүлнэ)
    loop_lag_warn_ms: int = 500
    # Камер хэдэн минут чимээгүй бол «event илгээхээ больсон» гэж анхааруулах вэ.
    # Хөдөлгөөн багатай зогсоолд шөнөдөө удаан чимээгүй байж болох тул өгөөмөр.
    camera_silence_warn_min: int = 60
    camera_silence_check_sec: int = 300
    # Камерын нэвтрэлтийн логийг хэдэн секунд тутам асуух вэ (0 = унтраах).
    # Манайхаас өөр хэрэглэгч/IP илэрвэл Тохиргоо → Төхөөрөмжийн «Гадны хандалт»
    # баганад харуулж, WARNING логлоно — өөр систем камерыг зэрэг ашиглаж буйн
    # баримт (2026-07-28 Monnis: user=Meguun ip=172.10.0.18 5 мин тутам).
    # 2026-07-29 хэмжилт: 5 мин тутам шалгах нь камерын нэвтрэлтийн 60%-ийг эзэлж
    # (цагт 12 удаа) ӨӨРӨӨ ачаалал болж байв — оношилгоо нь хаалттай өрсөлдөх ёсгүй.
    # 30 мин тутам хангалттай (гадны систем ирвэл 30 минутын дотор илэрнэ).
    camera_sessions_check_sec: int = 1800
    # Нэвтрэлтийн логоос хэдэн минутын мужийг харах вэ (сүүлийн үеийн хандалт л сонирхолтой)
    camera_sessions_window_min: int = 60
    # Deadman авто-reboot: камерын last_seen (стримийн heartbeat) энэ минутаас
    # хуучирвал камер БҮРЭН гацсан гэж үзэж magicBox.reboot илгээнэ. Богино
    # (1-3 мин) өөрөө сэргэдэг тасалдалд reboot ХОРТОЙ тул default УНТРААЛТТАЙ —
    # асаах бол PARKING_CAMERA_AUTO_REBOOT=true (дэлгэрэнгүй: camera_recovery.py).
    camera_auto_reboot: bool = False
    camera_reboot_after_min: int = 15
    camera_reboot_cooldown_min: int = 60
    barrier_username: str = "admin"
    barrier_password: str = ""
    barrier_channel: int = 0       # trafficSnap.factory.instance-ийн channel (баталгаажсан: 0)
    barrier_open_type: str = "Test"  # openStrobe info.openType (баталгаажсан: "Test")
    # RPC2 дэмждэггүй ӨӨР загварын төхөөрөмжид CGI замыг гараар зааж болно. Жишээ:
    # "/cgi-bin/trafficParking.cgi?action=openStrobe&channel=1&info.openType=Normal"
    # Тохируулсан үед "нээх" команд RPC2-ийн оронд энэ CGI-гээр явна.
    barrier_open_path: str = ""

    # Цагийн бүс — DB бүх цагийг UTC-ээр хадгалдаг; тайлан/графикт локал цаг руу
    # хөрвүүлэхэд ашиглана (Улаанбаатар = UTC+8)
    tz_offset_hours: int = 8

    # LPR
    lpr_min_confidence: float = 90.0
    # Гарсны дараах давхар event хамгаалалт (секунд)
    lpr_dedup_seconds: int = 20
    # Орох "цуврал уншилтын" цонх (секунд): хаалттай гарцаар энэ хугацаанд физикийн
    # хувьд ганц л машин орно. Камер нэг машиныг ойртох явцад нь ХЭД ХЭДЭН ӨӨР
    # дугаараар уншсан ч (1101ЭН→1310ХЭН→7370ХЭН) нэг session үүсгэж, дугаарыг
    # сүүлийн зөв форматтай уншилтаар автоматаар засна.
    entry_burst_seconds: int = 6
    # Хаалтыг дахин нээх хүртэлх хүлээлт: энэ хугацаанд амжилттай нээсэн бол
    # давхар уншилтад дахин команд илгээхгүй (хаалт нээлттэй хэвээр байдаг).
    barrier_reopen_cooldown_sec: int = 5

    # CGI event pull — сервер камераас ANPR датаг ТАТАЖ авах (хуучин easy-park шиг).
    # Камер→сервер push (ITSAPI) ажиллахгүй, гэхдээ сервер→камер ажилладаг үед ашиглана.
    cgi_poll: bool = False
    camera_username: str = "admin"   # камерын web admin нэвтрэх нэр
    camera_password: str = ""        # камерын web admin нууц үг (.env-д)

    # LPR snapshot — event бүрд камераас зураг татаж хадгална (нотолгоо/маргаан шийдэхэд)
    snapshot_enabled: bool = True
    snapshot_dir: str = "/var/lib/parking/snapshots"
    # snapManager.attachFileProc WS стрим — ОДОО ажиллаж буй ITC firmware дээр
    # subscribe амжилттай ч зураг ОГТ ирдэггүй нь батлагдсан (2026-07-25). Гэтэл
    # камер бүрд RPC session + WebSocket (2 холболт) эзэлж, камерын нэгэн зэрэг
    # холболтын хязгаарыг дүүргэн snapshot.cgi-г "Bad Request" болгодог. Тиймээс
    # DEFAULT UNTRAASAN. Зургийг event үед snapshot.cgi-ээр барина (найдвартай).
    # Realtime WS push дэмждэг firmware гарвал PARKING_SNAP_PULL=true болгоно.
    snap_pull: bool = False
    # WS суваг тухайн камераас БОДИТ зураг өгдөг нь батлагдсан үед snapshot.cgi
    # руу орохын өмнө event зургийг энэ хугацаагаар хүлээнэ — ингэснээр камер дээр
    # илүүц "Manual Snapshot" бичлэг үүсгэхгүй. 0 = хүлээхгүй (хуучин зан төлөв).
    # WS зураг өгдөггүй камерт огт нөлөөгүй (puller_delivers=False → шууд fallback).
    snapshot_wait_event_sec: float = 8.0

    # ── И-мэйл (SMTP) — байгууллагын сарын нэхэмжлэл илгээхэд ──
    # Жишээ (Gmail): HOST=smtp.gmail.com PORT=587 TLS=true USER=таны@gmail.com
    # PASSWORD=app-password (Google App Password). Хоосон бол илгээх товч идэвхгүй.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""          # хоосон бол smtp_user
    smtp_tls: bool = True
    # Сар бүрийн 1-нд өмнөх сарын нэхэмжлэлийг автоматаар DRAFT үүсгэх
    invoice_auto_generate: bool = True
    # Камерын цаг серверийн UTC-ээс хэдэн цагаар түрүүлж явдаг вэ (УБ=+8) —
    # нөхөн таталтын хайлтын мужид хэрэглэнэ. Камерын цаг эргэлзээтэй байвал
    # нөхөн таталт бүсийн зөрүүг БОЛОН 0-г хоёуланг оролдоно (тохиргоо буруу байсан ч олдоно).
    camera_tz_offset_hours: int = 8
    # Нөхөн таталтын анхны хайлтын хагас-цонх (секунд). Олдохгүй бол ×5, ×20 болгож
    # аажим өргөтгөнө — камерын цаг зөрсөн ч зургийг барьж авахын тулд.
    snapshot_search_window_seconds: int = 180
    # Камерын хадгалсан зургийг RPC2 (RecordFinder/mediaFileFind)-оор татах эсэх.
    # ODOOGIIN ITC firmware эдгээрийг ДЭМЖДЭГГҮЙ ("Bad Request") тул DEFAULT УНТРААСАН —
    # backfill дэмий RPC2 login хийж admin эрх түгжих эрсдэл үүсгэхээс сэргийлж, шууд
    # snapshot.cgi (амьд кадр) рүү очно. Дэмждэг firmware дээр PARKING_SNAPSHOT_STORED_FIND=true.
    snapshot_stored_find: bool = False

    # Гарах хаалтны LED дэлгэц (trafficParking.setScreenDisplay) — гарах камерын
    # LED-д төлбөрийн дүн/мэндчилгээ харуулна. Template-д {amount}, {plate} орлуулна.
    # Анхдагч нь латинаар — LED-ийн фонт кирилл дэмжих эсэхийг "Дэлгэц тест"
    # товчоор шалгаад .env-ээс кирилл болгож болно.
    screen_enabled: bool = True
    # Авто цэвэрлэгээ: X цагаас дээш идэвхтэй үлдсэн session-ийг авто хаах.
    # Гарах камерт уншигдалгүй/буруу уншигдаж гацсан, junk дугаар зэрэг phantom
    # бүртгэлүүд "зогсоолд байгаа" тоог хуурамчаар өсгөдгийг цэвэрлэнэ.
    # (зогсоол бүрээр site.auto_close_hours-аар дарж болно; 0 = унтраах)
    auto_close_hours: int = 12
    auto_close_create_debt: bool = True  # авто хаахад төлөгдөөгүй дүнгээр өр үүсгэх
    # Зөвхөн ОРОХ камерт уншигдсан (гарах уншилт огт байхгүй) OPEN session нь
    # ихэвчлэн гарах уншилт алдагдсан phantom — N цагийн дараа ӨРГҮЙГЭЭР үнэгүй
    # хаана (өр болгодог байсан нь худал өрийн уул үүсгэдэг байв, 2026-07-29).
    # Зогсоол бүрээр site.entry_only_free_hours-аар дарж болно; 0 = унтраах.
    entry_only_free_hours: int = 72
    # ФОРМАТ БУРУУ (үсэггүй/дутуу) дугаартай phantom-ыг ХУРДАН цэвэрлэх босго (цаг).
    # «4132» шиг форматгүй уншилтыг өргүйгээр үнэгүй хаах босго (цаг). ГЭХДЭЭ
    # хэт хурдан хаавал бодит машин гарч зөв уншуулахаас (match_open_session
    # 3-р шат түүнийг барьж төлбөр авдаг) ӨМНӨ phantom-ыг хаачихад төлбөр
    # алдагдана. Тиймээс энгийн зогсолтоос ДЛЭЭ (6ц) — машин гарч төлбөр
    # авагдах бүрэн боломж өгнө, гараагүй жинхэнэ phantom-ыг л 6ц дараа цэвэрлэнэ.
    invalid_plate_close_hours: int = 6

    # DR backup хүлээн авагчийн токен — ЗӨВХӨН test сервер дээр тохируулна
    # (тохируулаагүй сервер дээр /api/dr/upload огт ажиллахгүй). Production-ы
    # backup_ship.sh мөн энэ токеноор шифрлэж илгээнэ (/root/.parking-dr-token).
    dr_upload_token: str = ""

    # ─── Хуучин датаны цэвэрлэгээ (retention) — өдөрт нэг удаа, 0 = унтраах ───
    # Санхүүгийн датад (session/payment/vat/compensation) ХЭЗЭЭ Ч хүрэхгүй,
    # зөвхөн техникийн лог: танилтын түүхий event, хаалтны команд, аудит, зураг.
    retention_lpr_days: int = 90
    retention_cmd_days: int = 180
    retention_audit_days: int = 365
    retention_snapshot_days: int = 120
    # Гарах хаалтанд уншигдсан ч төлөлгүй алга болсон (дагаж гарсан) машиныг хурдан
    # өр болгох босго: AWAITING_PAYMENT төлөвт энэ цагаас удаан ямар ч хөдөлгөөнгүй
    # бол явчихсан гэж үзэж өртэй хаана. 0 = унтраах (ердийн 12ц-аар хаагдана).
    auto_close_awaiting_hours: int = 2

    # 3 мөр: дугаар / зогссон хугацаа / төлбөр (LED-ийн Custom-д «\n» = мөр таслал).
    # {duration} = зогссон хугацаа («2ts 05min»). .env-д мөр таслалыг «|» эсвэл
    # «\n»-ээр бичиж болно (render_screen_text хөрвүүлнэ). Хэрэв LED бүх текстийг
    # НЭГ мөрөнд урсгаж байвал мөр таслалын тэмдэг нь тухайн firmware-д таарахгүй
    # байна гэсэн үг — tools/screen_probe.py-ээр туршиж PARKING_SCREEN_LINE_BREAK-ийг
    # тохируулна («\r\n» г.м).
    # Мөр бүр БОГИНО байх ёстой: 64px өргөн lattice дэлгэцэд урт мөр урсаж эхэлдэг
    # тул жолооч дүнгээ харах гэж хүлээнэ. Тиймээс 3-р мөр нь «Tulbur: 5000» биш
    # зөвхөн «5000T» (₮ тэмдгийг LED фонт дэмжихгүй бол T болгож .env-ээс солино).
    screen_fee_text: str = "{plate}\n{duration}\n{amount}T"  # AWAITING_PAYMENT үед
    # Гарах үеийн 3 хувилбар — жолоочид ЯАГААД үнэгүй гарч байгааг нь хэлнэ
    # (кирилл LED дээр ажилладаг нь 2026-07-29 туршилтаар батлагдсан).
    screen_bye_text: str = "{plate}\n{duration}\nБаяртай"              # төлбөрөө төлж гарахад
    screen_bye_registered_text: str = "{plate}\n{duration}\nГэрээт"     # бүртгэлтэй машин
    screen_bye_free_text: str = "{plate}\n{duration}\nТүр зогссон"      # үнэгүй хугацаанд багтсан
    screen_nosession_text: str = "Burtgel oldsongui"  # session олдоогүй үед
    # Орох LED — 3 мөр: орсон цаг / дугаар / мэндчилгээ ({time} = локал HH:MM)
    screen_welcome_text: str = "{time}\n{plate}\nTavtai moril"
    # Дуут зарлал (trafficParking.setVoiceBroadcast) — анхдагчаар унтраалттай
    # (TTS хөдөлгүүр латин/кирилл текстийг зөв уншихгүй байж болзошгүй)
    screen_voice: bool = False
    # LED текстийг хэдэн удаа, ямар зайтай давтаж илгээх — камер Vehicle Passing
    # горимдоо текстийг хурдан дардаг тул давтаж илгээснээр ~5-6 секунд барина
    # Дэлгэцийн давталт — камерын RPC сессийг хэр удаан барихыг тодорхойлно.
    # 6×3.0с = 18 СЕКУНД байсан нь хаалтны командтай байнга мөргөлдөж, Dahua
    # «нууц үг буруу» гэж худал татгалзаж, хаалт 10-30 секунд нээгдэхгүй байв.
    # 4×1.5с = 6с — дэлгэц уншихад хангалттай, мөргөлдөх магадлал 3 дахин бага.
    # (Хаалт ирвэл давталт дунд нь ТАСАРЧ сесс шууд чөлөөлөгдөнө.)
    # Дэлгэц амжилтгүй болбол ХОЖУУ дахин оролдоно (секундээр, таслалаар).
    # Учир нь жолооч гарахдаа төлбөрөө төлж хэдэн арван секунд зогсдог — тэр
    # хооронд суваг чөлөөлөгдвөл дүнг харуулах нь ХЭРЭГТЭЙ хэвээр. Оролдлого
    # бүрийн өмнө хаалт хүлээж байгаа эсэх / камер өвчтэй эсэхийг шалгана, мөн
    # тухайн камерт ШИНЭ текст ирсэн бол хуучныг орхино (давхардуулж цохихгүй).
    screen_retry_delays: str = "8,25"
    screen_repeat: int = 4
    screen_repeat_interval: float = 1.5
    # Камер «хэдэн секунд харуулах» талбар дэмждэг бол НЭГ команд хангалттай —
    # давталт унтарч RPC сесс маш богино болно. Ямар талбар нэртэй болохыг
    # tools/screen_probe.py-ээр бодит камераас тогтоогоод энд бичнэ. Жишээ:
    #   PARKING_SCREEN_HOLD_FIELD=Time
    #   PARKING_SCREEN_HOLD_SEC=30
    # HOLD_SEC > 0 болмогц давталт автоматаар 1 болно.
    screen_hold_field: str = ""
    screen_hold_sec: int = 0
    # LED дэлгэцийн мөр таслалын тэмдэг. Зарим firmware «\n»-ийг ойлгодоггүй тул
    # «\r\n» эсвэл хоосон зайгаар туршиж болно (камер солихгүйгээр .env-ээс).
    screen_line_break: str = "\n"

    # e-Barimt нээлттэй лавлагаа — байгууллагын регистрээр нэр шалгах
    # (зөвхөн Монголын IP-ээс хандагдана; гаднаас timeout өгвөл frontend
    # "шалгаж чадсангүй" гэж зөөлөн анхааруулна)
    org_info_url: str = "https://info.ebarimt.mn/rest/merchant/info"
    org_info_timeout_sec: float = 6.0

    # ── Түншийн интеграцийн API (/api/v1) — wallet/апп-уудад зориулсан B2B түлхүүрүүд ──
    # Формат: "нэр:түлхүүр,нэр2:түлхүүр2"  ж: "toki:AbC123,easywallet:XyZ789"
    # Түлхүүр бүр X-API-Key толгойгоор ирж, нэр нь Payment.provider болно (TOKI, EASYWALLET).
    # Шинэ wallet нэмэхэд КОД ӨӨРЧЛӨХГҮЙ — зөвхөн .env-д түлхүүр нэмнэ.
    partner_keys: str = ""

    def partner_map(self) -> dict[str, str]:
        """{api_key: PARTNER_NAME} — partner_keys-ийг задалсан хүснэгт."""
        out = {}
        for pair in (self.partner_keys or "").split(","):
            if ":" in pair:
                name, key = pair.split(":", 1)
                if name.strip() and key.strip():
                    out[key.strip()] = name.strip().upper()
        return out

    class Config:
        env_file = ".env"
        env_prefix = "PARKING_"


settings = Settings()
