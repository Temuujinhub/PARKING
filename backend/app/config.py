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

    # Database
    database_url: str = "postgresql+psycopg2://parking:parking_secret_2026@localhost:5432/parking"

    # Public pay page-ийн үндсэн URL (QR код энэ URL руу чиглэнэ)
    public_base_url: str = "http://localhost"

    # QPay v2 (developer.qpay.mn)
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
    barrier_timeout_sec: float = 5.0
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

    # CGI event pull — сервер камераас ANPR датаг ТАТАЖ авах (хуучин easy-park шиг).
    # Камер→сервер push (ITSAPI) ажиллахгүй, гэхдээ сервер→камер ажилладаг үед ашиглана.
    cgi_poll: bool = False
    camera_username: str = "admin"   # камерын web admin нэвтрэх нэр
    camera_password: str = ""        # камерын web admin нууц үг (.env-д)

    # LPR snapshot — event бүрд камераас зураг татаж хадгална (нотолгоо/маргаан шийдэхэд)
    snapshot_enabled: bool = True
    snapshot_dir: str = "/var/lib/parking/snapshots"

    # Гарах хаалтны LED дэлгэц (trafficParking.setScreenDisplay) — гарах камерын
    # LED-д төлбөрийн дүн/мэндчилгээ харуулна. Template-д {amount}, {plate} орлуулна.
    # Анхдагч нь латинаар — LED-ийн фонт кирилл дэмжих эсэхийг "Дэлгэц тест"
    # товчоор шалгаад .env-ээс кирилл болгож болно.
    screen_enabled: bool = True
    screen_fee_text: str = "Tulbur: {amount}"      # AWAITING_PAYMENT үед
    screen_bye_text: str = "Sain yavaarai!"        # төлөгдөж/үнэгүй гарахад
    screen_nosession_text: str = "Burtgel oldsongui"  # session олдоогүй үед
    # Дуут зарлал (trafficParking.setVoiceBroadcast) — анхдагчаар унтраалттай
    # (TTS хөдөлгүүр латин/кирилл текстийг зөв уншихгүй байж болзошгүй)
    screen_voice: bool = False

    # e-Barimt нээлттэй лавлагаа — байгууллагын регистрээр нэр шалгах
    # (зөвхөн Монголын IP-ээс хандагдана; гаднаас timeout өгвөл frontend
    # "шалгаж чадсангүй" гэж зөөлөн анхааруулна)
    org_info_url: str = "https://info.ebarimt.mn/rest/merchant/info"
    org_info_timeout_sec: float = 6.0

    class Config:
        env_file = ".env"
        env_prefix = "PARKING_"


settings = Settings()
