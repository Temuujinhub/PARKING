import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, Numeric, String, Text, JSON,
    UniqueConstraint, text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from .database import Base


def uid():
    return str(uuid.uuid4())


class User(Base):
    """Системийн хэрэглэгч. Үүрэг (role):
    SUPER_ADMIN — бүх эрх + хэрэглэгчийн удирдлага
    ADMIN       — системийн тохиргоо (зогсоол, тариф, төхөөрөмж)
    FINANCE     — санхүү: тайлан, төлбөр, НӨАТ
    OPERATOR    — зогсоол дээрх ажилтан: касс, шалгах, хаалт нээх
    """
    __tablename__ = "users"
    id = Column(UUID(as_uuid=False), primary_key=True, default=uid)
    username = Column(String(60), unique=True, nullable=False, index=True)
    password_hash = Column(String(200), nullable=False)
    full_name = Column(String(120), nullable=False, default="")
    phone = Column(String(20), default="")
    role = Column(String(20), nullable=False, default="OPERATOR")
    site_id = Column(UUID(as_uuid=False), ForeignKey("parking_sites.id"), nullable=True)  # үндсэн зогсоол (ээлж энд нээгдэнэ)
    # Хуудасны эрхийн матриц (чекбокс) — null бол role-ийн default эрхүүд үйлчилнэ
    permissions = Column(JSON, nullable=True)
    # OPERATOR-ийн хандах зогсоолууд (олон сонголт) — null бол [site_id]
    site_ids = Column(JSON, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class ParkingSite(Base):
    __tablename__ = "parking_sites"
    id = Column(UUID(as_uuid=False), primary_key=True, default=uid)
    name = Column(String(120), nullable=False)
    site_code = Column(String(30), unique=True, nullable=False, index=True)  # QR URL-д ашиглана
    zone_code = Column(String(10), nullable=False, default="A")
    address = Column(Text, default="")
    capacity = Column(Integer, nullable=False, default=0)
    tariff_template_id = Column(UUID(as_uuid=False), ForeignKey("tariff_templates.id"), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    tariff_template = relationship("TariffTemplate", lazy="joined")


class Device(Base):
    __tablename__ = "devices"
    id = Column(UUID(as_uuid=False), primary_key=True, default=uid)
    site_id = Column(UUID(as_uuid=False), ForeignKey("parking_sites.id"), nullable=False)
    name = Column(String(120), nullable=False, default="")
    device_type = Column(String(30), nullable=False)  # camera, barrier, pax_terminal, led
    vendor = Column(String(60), default="Dahua")
    model = Column(String(80), default="")
    ip_address = Column(String(50), default="")
    lane_no = Column(Integer, default=1)
    lane_dir = Column(String(10), default="entry")  # entry, exit, both
    auto_open = Column(Boolean, nullable=False, default=True)  # entry lane: дугаар уншмагц автоматаар нээх
    status = Column(String(30), nullable=False, default="active")
    device_key = Column(String(80), unique=True, nullable=True)  # LPR callback-д төхөөрөмж таних түлхүүр
    extra = Column(JSON, nullable=False, default=dict)
    last_seen = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    site = relationship("ParkingSite", lazy="joined")


class TariffTemplate(Base):
    """Тарифын загвар — easy-park-ийн 'Тарифын загвар'-тай ижил ойлголт."""
    __tablename__ = "tariff_templates"
    id = Column(UUID(as_uuid=False), primary_key=True, default=uid)
    name = Column(String(120), nullable=False)
    free_minutes = Column(Integer, nullable=False, default=0)             # Үнэгүй байх хугацаа
    grace_minutes = Column(Integer, nullable=False, default=15)           # Төлбөрийн дараах үнэгүй гарах хугацаа
    prepaid_price = Column(Numeric(12, 2), nullable=False, default=0)     # Урьдчилсан захиалгын үнэ
    extra_hour_price = Column(Numeric(12, 2), nullable=False, default=0)  # Шатлалаас хэтэрсэн цаг тутмын үнэ
    daily_cap = Column(Numeric(12, 2), nullable=True)                     # Хоногийн дээд хязгаар (заавал биш)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    tiers = relationship("TariffTier", lazy="selectin", order_by="TariffTier.upto_minutes",
                         cascade="all, delete-orphan")


class TariffTier(Base):
    """Шатлалын мөр: upto_minutes хүртэл нийт үнэ price (кумулятив)."""
    __tablename__ = "tariff_tiers"
    id = Column(UUID(as_uuid=False), primary_key=True, default=uid)
    template_id = Column(UUID(as_uuid=False), ForeignKey("tariff_templates.id"), nullable=False)
    upto_minutes = Column(Integer, nullable=False)   # жишээ: 60, 120, 180
    price = Column(Numeric(12, 2), nullable=False)   # жишээ: 1000, 2000, 5000


class Discount(Base):
    __tablename__ = "discounts"
    id = Column(UUID(as_uuid=False), primary_key=True, default=uid)
    name = Column(String(120), nullable=False)
    discount_type = Column(String(20), nullable=False)  # PERCENT, FIXED, FREE_MINUTES
    value = Column(Numeric(12, 2), nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class RegisteredDriver(Base):
    """Бүртгэлтэй жолооч — гэрээт / сарын эрхтэй / whitelist."""
    __tablename__ = "registered_drivers"
    id = Column(UUID(as_uuid=False), primary_key=True, default=uid)
    plate_number = Column(String(20), nullable=False, index=True)
    full_name = Column(String(120), default="")
    phone = Column(String(20), default="")
    contract_type = Column(String(20), nullable=False, default="MONTHLY")  # MONTHLY, CONTRACT, VIP, STAFF
    site_id = Column(UUID(as_uuid=False), ForeignKey("parking_sites.id"), nullable=True)  # null = бүх зогсоол
    monthly_fee = Column(Numeric(12, 2), nullable=False, default=0)
    valid_from = Column(DateTime, nullable=False, default=datetime.utcnow)
    valid_to = Column(DateTime, nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    site = relationship("ParkingSite", lazy="joined")


class BlacklistEntry(Base):
    __tablename__ = "blacklist"
    id = Column(UUID(as_uuid=False), primary_key=True, default=uid)
    plate_number = Column(String(20), nullable=False, index=True)
    reason = Column(Text, default="")
    is_active = Column(Boolean, nullable=False, default=True)
    created_by = Column(String(60), default="")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class ParkingSession(Base):
    __tablename__ = "parking_sessions"
    id = Column(UUID(as_uuid=False), primary_key=True, default=uid)
    site_id = Column(UUID(as_uuid=False), ForeignKey("parking_sites.id"), nullable=False, index=True)
    plate_number = Column(String(20), nullable=False, index=True)
    entry_time = Column(DateTime, nullable=False)
    exit_time = Column(DateTime, nullable=True)
    duration_minutes = Column(Integer, nullable=True)
    # OPEN → (exit камерт уншигдсан) AWAITING_PAYMENT → PAID → CLOSED; FREE = үнэгүй гарсан
    status = Column(String(30), nullable=False, default="OPEN", index=True)
    entry_device_id = Column(UUID(as_uuid=False), ForeignKey("devices.id"), nullable=True)
    exit_device_id = Column(UUID(as_uuid=False), ForeignKey("devices.id"), nullable=True)
    confidence_entry = Column(Float, nullable=True)
    confidence_exit = Column(Float, nullable=True)
    is_registered = Column(Boolean, nullable=False, default=False)  # гэрээт жолооч эсэх
    base_fee = Column(Numeric(12, 2), nullable=True)
    discount_id = Column(UUID(as_uuid=False), ForeignKey("discounts.id"), nullable=True)
    discount_amount = Column(Numeric(12, 2), nullable=False, default=0)
    vat_amount = Column(Numeric(12, 2), nullable=True)
    total_fee = Column(Numeric(12, 2), nullable=True)
    paid_at = Column(DateTime, nullable=True)
    exit_deadline = Column(DateTime, nullable=True)  # paid_at + grace_minutes
    note = Column(Text, nullable=True)  # операторын нэмэлт тэмдэглэл (касс)
    entry_snapshot = Column(String(255), nullable=True)  # орох камерын зураг (snapshot_dir доторх зам)
    exit_snapshot = Column(String(255), nullable=True)   # гарах камерын зураг
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    site = relationship("ParkingSite", lazy="joined")
    discount = relationship("Discount", lazy="joined")

    __table_args__ = (
        Index("ix_sessions_entry_time", "entry_time"),
        Index("ix_sessions_exit_time", "exit_time"),
        Index("ix_sessions_site_plate_status", "site_id", "plate_number", "status"),
        # Нэг зогсоолд дугаараар нэгэн зэрэг ганц идэвхтэй session (LPR race хамгаалалт)
        Index("uq_active_session", "site_id", "plate_number", unique=True,
              postgresql_where=text("status IN ('OPEN','AWAITING_PAYMENT','PAID')")),
    )


class Payment(Base):
    __tablename__ = "payments"
    id = Column(UUID(as_uuid=False), primary_key=True, default=uid)
    session_id = Column(UUID(as_uuid=False), ForeignKey("parking_sessions.id"), nullable=False, index=True)
    provider = Column(String(30), nullable=False)        # QPAY, POS, CASH
    payment_method = Column(String(30), nullable=False)  # QR, CARD, CASH
    # Эх сурвалж (QPay-д): POS=кассын пос дээр, QR=жолооч утаснаасаа. Тооцоонд ялгана.
    source = Column(String(10), nullable=True)
    provider_invoice_id = Column(String(120), nullable=True)
    # QPay-ийн g_payment_id (payment/check-ээс) — QPay ebarimt_v3 үүсгэхэд ашиглана
    provider_payment_id = Column(String(120), nullable=True)
    # e-Barimt хүлээн авагчийн төрөл: CITIZEN (иргэн) | COMPANY (ААН)
    ebarimt_receiver_type = Column(String(20), nullable=True)
    sender_invoice_no = Column(String(120), unique=True, nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    vat_amount = Column(Numeric(12, 2), nullable=False, default=0)
    status = Column(String(30), nullable=False, default="PENDING", index=True)  # PENDING, PAID, FAILED, CANCELLED
    paid_at = Column(DateTime, nullable=True)
    card_last4 = Column(String(4), nullable=True)
    card_brand = Column(String(20), nullable=True)
    terminal_id = Column(String(60), nullable=True)
    customer_tin = Column(String(20), nullable=True)  # Байгууллагаар НӨАТ авах бол ТТД
    cashier_id = Column(UUID(as_uuid=False), ForeignKey("users.id"), nullable=True)
    shift_id = Column(UUID(as_uuid=False), ForeignKey("cashier_shifts.id"), nullable=True)
    qr_text = Column(Text, nullable=True)
    deep_link = Column(Text, nullable=True)
    raw_payload = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    session = relationship("ParkingSession", lazy="joined")

    __table_args__ = (
        Index("ix_payments_status_paid_at", "status", "paid_at"),  # орлогын тайлангийн hot path
        Index("ix_payments_created_at", "created_at"),
        Index("ix_payments_shift_id", "shift_id"),
        Index("ix_payments_provider", "provider"),
    )


class VatReceipt(Base):
    __tablename__ = "vat_receipts"
    id = Column(UUID(as_uuid=False), primary_key=True, default=uid)
    payment_id = Column(UUID(as_uuid=False), ForeignKey("payments.id"), nullable=False)
    session_id = Column(UUID(as_uuid=False), ForeignKey("parking_sessions.id"), nullable=False)
    ebarimt_id = Column(String(120), nullable=True)
    lottery_code = Column(String(60), nullable=True)
    amount = Column(Numeric(12, 2), nullable=False)
    vat_amount = Column(Numeric(12, 2), nullable=False)
    receipt_url = Column(Text, nullable=True)
    customer_tin = Column(String(20), nullable=True)  # байгууллагаар авах бол
    status = Column(String(30), nullable=False, default="PENDING")  # PENDING, SENT, FAILED
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_vat_receipts_session", "session_id"),
        Index("ix_vat_receipts_payment", "payment_id"),
    )


class BarrierCommand(Base):
    __tablename__ = "barrier_commands"
    id = Column(UUID(as_uuid=False), primary_key=True, default=uid)
    session_id = Column(UUID(as_uuid=False), ForeignKey("parking_sessions.id"), nullable=True)
    device_id = Column(UUID(as_uuid=False), ForeignKey("devices.id"), nullable=False)
    command = Column(String(30), nullable=False)         # open, close
    command_source = Column(String(30), nullable=False)  # auto_entry, auto_exit, payment, manual, whitelist
    issued_by = Column(String(60), nullable=True)        # хэрэглэгчийн username (manual үед)
    status = Column(String(30), nullable=False, default="PENDING")  # PENDING, SUCCESS, FAILED
    response_text = Column(Text, default="")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    executed_at = Column(DateTime, nullable=True)

    device = relationship("Device", lazy="joined")


class LprEvent(Base):
    """Түүхий LPR event лог — гарах талын 'сүүлд уншигдсан дугаарууд' үүнээс гарна."""
    __tablename__ = "lpr_events"
    id = Column(UUID(as_uuid=False), primary_key=True, default=uid)
    site_id = Column(UUID(as_uuid=False), ForeignKey("parking_sites.id"), nullable=False, index=True)
    device_id = Column(UUID(as_uuid=False), ForeignKey("devices.id"), nullable=True)
    plate_number = Column(String(20), nullable=False, index=True)
    lane_dir = Column(String(10), nullable=False)  # entry, exit
    confidence = Column(Float, nullable=True)
    accepted = Column(Boolean, nullable=False, default=True)
    reject_reason = Column(String(120), nullable=True)
    raw = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)


class CashierShift(Base):
    __tablename__ = "cashier_shifts"
    id = Column(UUID(as_uuid=False), primary_key=True, default=uid)
    user_id = Column(UUID(as_uuid=False), ForeignKey("users.id"), nullable=False)
    site_id = Column(UUID(as_uuid=False), ForeignKey("parking_sites.id"), nullable=True)
    opened_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    closed_at = Column(DateTime, nullable=True)
    opening_amount = Column(Numeric(12, 2), nullable=False, default=0)
    status = Column(String(20), nullable=False, default="OPEN")  # OPEN, CLOSED
    # Ээлж хаах тооцоо: операторын данс руу шилжүүлэхээр баталгаажуулсан бэлэн + тэмдэглэл
    cash_confirmed = Column(Numeric(12, 2), nullable=True)
    closed_cars = Column(Integer, nullable=True)  # ээлж хаахад гаргасан машины тоо
    note = Column(Text, nullable=True)

    user = relationship("User", lazy="joined")
    site = relationship("ParkingSite", lazy="joined")

    __table_args__ = (
        Index("ix_shifts_status", "status"),
        Index("ix_shifts_opened_at", "opened_at"),
        Index("ix_shifts_user", "user_id"),
        Index("ix_shifts_site", "site_id"),
    )


class Compensation(Base):
    """Нөхөн төлбөр — төлбөргүй гарсан/шөнийн хаалтаар гаргасан машины нэхэмжлэл.
    Төлөгдөөгүй 3+ нэхэмжлэлтэй дугаар автоматаар хар жагсаалтад орно (JGA спек)."""
    __tablename__ = "compensations"
    id = Column(UUID(as_uuid=False), primary_key=True, default=uid)
    session_id = Column(UUID(as_uuid=False), ForeignKey("parking_sessions.id"), nullable=True)
    site_id = Column(UUID(as_uuid=False), ForeignKey("parking_sites.id"), nullable=False)
    plate_number = Column(String(20), nullable=False, index=True)
    amount = Column(Numeric(12, 2), nullable=False)
    reason = Column(String(200), nullable=False, default="")  # unpaid_exit, night_close, manual
    status = Column(String(20), nullable=False, default="PENDING", index=True)  # PENDING, PAID, CANCELLED
    created_by = Column(String(60), default="system")
    paid_at = Column(DateTime, nullable=True)
    paid_by = Column(String(60), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    site = relationship("ParkingSite", lazy="joined")


class DailySettlement(Base):
    """Санхүүгийн өдрийн мөнгөн тооцоо — зогсоол/өдрөөр системийн борлуулалт ба дансны
    бодит орлогыг тулгах. Системийн дүн (карт/QPay/бэлэн) төлбөрөөс тооцогдоно; санхүү
    ажилтан дансны хуулгаас баталгаажсан дүнг оруулж, зөрүүг тулгаад тооцоог хаадаг."""
    __tablename__ = "daily_settlements"
    id = Column(UUID(as_uuid=False), primary_key=True, default=uid)
    site_id = Column(UUID(as_uuid=False), ForeignKey("parking_sites.id"), nullable=False, index=True)
    date = Column(String(10), nullable=False, index=True)  # YYYY-MM-DD (тухайн өдөр)
    # Санхүүгийн баталгаажуулсан бодит дүн (дансны хуулгаас)
    confirmed_card = Column(Numeric(12, 2), nullable=False, default=0)
    confirmed_qpay = Column(Numeric(12, 2), nullable=False, default=0)
    confirmed_cash = Column(Numeric(12, 2), nullable=False, default=0)
    note = Column(Text, nullable=True)
    status = Column(String(20), nullable=False, default="OPEN")  # OPEN, CLOSED
    closed_by = Column(String(60), nullable=True)
    closed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    site = relationship("ParkingSite", lazy="joined")
    __table_args__ = (UniqueConstraint("site_id", "date", name="uq_settlement_site_date"),)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id = Column(UUID(as_uuid=False), primary_key=True, default=uid)
    username = Column(String(60), nullable=False, default="system")
    action = Column(String(60), nullable=False)      # LOGIN, CREATE, UPDATE, DELETE, BARRIER_OPEN, PAYMENT...
    entity = Column(String(60), nullable=False, default="")
    entity_id = Column(String(60), nullable=True)
    detail = Column(JSON, nullable=False, default=dict)
    ip_address = Column(String(50), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    __table_args__ = (
        Index("ix_audit_username", "username"),
        Index("ix_audit_action", "action"),
    )
