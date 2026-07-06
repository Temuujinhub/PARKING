from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Ерөнхий
    app_name: str = "Smart Parking MN"
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

    # e-Barimt
    ebarimt_base_url: str = "https://ebarimt-pos.eba.mn/api/1.0"
    ebarimt_merchant_tin: str = ""
    ebarimt_pos_no: str = "POS-001"
    ebarimt_branch_no: str = "001"
    ebarimt_district_code: str = "23"
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
