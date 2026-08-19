"""Mutating endpoint-үүдийн Pydantic хүсэлтийн схемүүд.

Зорилго: `body: dict` + `body["key"]` хэв маяг дутуу талбар дээр 500 KeyError
өгдөг байсныг 422 (талбар бүрийн ойлгомжтой алдаа) болгох. Endpoint доторх
логик өөрчлөгдөхгүй — схемийг `.dump()` (exclude_unset) хийгээд хуучин dict
логикоо хэвээр ажиллуулна: PUT-ийн "зөвхөн ирсэн талбарыг шинэчлэх" семантик
яг хадгалагдана. Мэдэхгүй талбарыг үл тоомсорлоно (frontend нэмэлт талбар
явуулбал эвдрэхгүй).
"""
from pydantic import BaseModel, ConfigDict, Field


class _In(BaseModel):
    """Суурь: мэдэхгүй талбарыг хаяна, dump() нь зөвхөн ирсэн талбарыг өгнө."""
    model_config = ConfigDict(extra="ignore")

    def dump(self) -> dict:
        return self.model_dump(exclude_unset=True)


# ── Зогсоол ──
class SiteCreate(_In):
    name: str = Field(min_length=1, max_length=120)
    site_code: str = Field(min_length=1, max_length=30)
    zone_code: str | None = None
    address: str | None = None
    capacity: int | None = None
    tariff_template_id: str | None = None
    auto_close_hours: int | None = None
    entry_only_free_hours: int | None = None
    registered_only: bool | None = None
    # Nested (дамжин) зогсоол — энэ зогсоол аль зогсоолын ДОТОР байна
    parent_site_id: str | None = None
    transit_max_hours: int | None = None
    barrier_close_sweep_min: int | None = None
    no_charge: bool | None = None
    qr_url: str | None = None
    qpay_username: str | None = None
    qpay_password: str | None = None
    qpay_invoice_code: str | None = None
    qpay_branch_code: str | None = None
    qpay_district_code: str | None = None
    bank_name: str | None = None
    bank_account: str | None = None
    bank_account_name: str | None = None
    screen_config: dict | None = None
    tenant_id: str | None = None


class SiteUpdate(SiteCreate):
    name: str | None = Field(default=None, max_length=120)
    site_code: str | None = Field(default=None, max_length=30)
    is_active: bool | None = None


# ── Тарифын загвар ──
class TariffTierIn(_In):
    upto_minutes: int
    price: float


class TariffTemplateCreate(_In):
    name: str = Field(min_length=1, max_length=120)
    free_minutes: int = 0
    grace_minutes: int = 15
    prepaid_price: float = 0
    extra_hour_price: float = 0
    daily_cap: float | None = None
    tiers: list[TariffTierIn] = []


class TariffTemplateUpdate(_In):
    name: str | None = None
    free_minutes: int | None = None
    grace_minutes: int | None = None
    prepaid_price: float | None = None
    extra_hour_price: float | None = None
    daily_cap: float | None = None
    is_active: bool | None = None
    tiers: list[TariffTierIn] | None = None


# ── Гэрээт жолооч ──
class DriverCreate(_In):
    plate_number: str = Field(min_length=4, max_length=20)
    full_name: str = ""
    phone: str = ""
    company: str = ""
    note: str = ""
    contract_type: str = "MONTHLY"
    site_id: str | None = None
    monthly_fee: float = 0
    # Үнэгүй цагийн цонх "HH:MM" (УБ) — хоёулаа байвал зөвхөн цонхонд үнэгүй
    free_from: str | None = None
    free_until: str | None = None
    valid_from: str | None = None   # ISO datetime — хуучин fromisoformat логик хэвээр
    valid_to: str                   # заавал (өмнө нь дутуу бол 500 өгдөг байсан)


class DriverUpdate(_In):
    plate_number: str | None = None
    full_name: str | None = None
    phone: str | None = None
    company: str | None = None
    note: str | None = None
    contract_type: str | None = None
    site_id: str | None = None
    monthly_fee: float | None = None
    is_active: bool | None = None
    free_from: str | None = None
    free_until: str | None = None
    valid_from: str | None = None
    valid_to: str | None = None


# ── Төхөөрөмж ──
class DeviceCreate(_In):
    site_id: str
    device_type: str = Field(min_length=1, max_length=30)
    name: str = ""
    vendor: str = "Dahua"
    model: str = ""
    ip_address: str = ""
    lane_no: int = 1
    lane_dir: str = "entry"
    auto_open: bool = True
    nested_inner: bool | None = None
    username: str | None = None
    password: str | None = None


class DeviceUpdate(_In):
    name: str | None = None
    device_type: str | None = None
    vendor: str | None = None
    model: str | None = None
    ip_address: str | None = None
    lane_no: int | None = None
    lane_dir: str | None = None
    auto_open: bool | None = None
    nested_inner: bool | None = None
    status: str | None = None
    site_id: str | None = None
    username: str | None = None
    password: str | None = None


# ── Хэрэглэгч ──
class UserCreate(_In):
    username: str = Field(min_length=1, max_length=60)
    password: str = Field(min_length=1)
    role: str
    full_name: str = ""
    phone: str = ""
    site_id: str | None = None
    site_ids: list[str] | None = None
    permissions: list[str] | None = None
    tenant_id: str | None = None


class UserUpdate(_In):
    full_name: str | None = None
    phone: str | None = None
    role: str | None = None
    site_id: str | None = None
    site_ids: list[str] | None = None
    permissions: list[str] | None = None
    is_active: bool | None = None
    password: str | None = None
    tenant_id: str | None = None


# ── Түрээслэгч (Tenant) ──
class TenantCreate(_In):
    name: str = Field(min_length=1, max_length=160)
    code: str = Field(min_length=1, max_length=30)
    register: str = ""
    contact_name: str = ""
    phone: str = ""
    email: str = ""
    note: str = ""
    # Хамт үүсгэх админ хэрэглэгч (заавал биш)
    admin_username: str | None = None
    admin_password: str | None = None
    admin_full_name: str = ""
    # Оноох зогсоолууд (одоо байгаа зогсоолын id-ууд)
    site_ids: list[str] | None = None
    # Түрээслэгчийн QPay данс — бүх зогсоолд нь үйлчилнэ
    qpay_username: str | None = None
    qpay_password: str | None = None
    qpay_invoice_code: str | None = None
    qpay_branch_code: str | None = None
    qpay_district_code: str | None = None


class TenantUpdate(_In):
    name: str | None = None
    code: str | None = None
    register: str | None = None
    contact_name: str | None = None
    phone: str | None = None
    email: str | None = None
    note: str | None = None
    is_active: bool | None = None
    site_ids: list[str] | None = None
    qpay_username: str | None = None
    qpay_password: str | None = None
    qpay_invoice_code: str | None = None
    qpay_branch_code: str | None = None
    qpay_district_code: str | None = None
    msgbill_api_key: str | None = None
