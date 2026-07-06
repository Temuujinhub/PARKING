from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Ерөнхий
    app_name: str = "Easy Parking"
    debug: bool = False
    secret_key: str = "change-me-in-production-9f8a7b6c5d4e"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 12  # 12 цаг

    # Database
    database_url: str = "postgresql+psycopg2://parking:parking_secret_2026@localhost:5432/parking"

    # Public pay page-ийн үндсэн URL (QR код энэ URL руу чиглэнэ)
    public_base_url: str = "http://localhost"

    # QPay
    qpay_base_url: str = "https://merchant.qpay.mn/v2"
    qpay_username: str = ""
    qpay_password: str = ""
    qpay_invoice_code: str = "PARKING_INVOICE"
    qpay_mock: bool = True  # Бодит credentials байхгүй үед mock горим
    # Webhook нууц токен — тохируулсан бол /qpay/webhook хүсэлтэд ?token= таарах ёстой
    # (QPay callback_url-д нэмж өгнө). Хоосон бол шалгахгүй (mock/туршилтын үед).
    qpay_webhook_secret: str = ""

    # Аюулгүй байдал
    # /api/lpr/simulate туршилтын endpoint-ийг production-д хаах (barrier бодит болмогц автоматаар хаагдана)
    allow_simulate: bool = True
    # CORS: production-д домэйноо зааж өгнө (жишээ: "https://test.easy-parking.mn")
    cors_origins: str = "*"

    # e-Barimt — POS API 3.0 (татварын PosAPI сервис локал дээр суусан байна)
    ebarimt_posapi_url: str = "http://localhost:7080/rest"
    ebarimt_merchant_tin: str = ""
    ebarimt_pos_no: str = "10000001"
    ebarimt_branch_no: str = "001"
    ebarimt_district_code: str = "3420"
    ebarimt_classification_code: str = "5221190"  # Зогсоолын үйлчилгээний ангиллын код (бүртгэлээр тодруулна)
    ebarimt_mock: bool = True

    # НӨАТ
    vat_rate: float = 0.10
    vat_inclusive: bool = True  # Тарифын үнэ НӨАТ багтсан эсэх

    # Barrier
    barrier_mock: bool = True  # Бодит төхөөрөмжгүй үед mock
    barrier_timeout_sec: float = 3.0
    barrier_username: str = "admin"
    barrier_password: str = ""

    # LPR
    lpr_min_confidence: float = 90.0
    # Гарсны дараах давхар event хамгаалалт (секунд)
    lpr_dedup_seconds: int = 20

    class Config:
        env_file = ".env"
        env_prefix = "PARKING_"


settings = Settings()
