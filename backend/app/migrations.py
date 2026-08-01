"""Хөнгөн idempotent миграци — production DB-г кодтой тааруулна.

SQLAlchemy create_all() нь шинэ ХҮСНЭГТ үүсгэдэг ч байгаа хүснэгтэд шинэ БАГАНА нэмдэггүй.
Тиймээс шинэ багана нэмэх бүрд энд `ADD COLUMN IF NOT EXISTS` мөр нэмнэ.
Startup бүрт ажиллах ба аль хэдийн байгаа бол алгасна (аюулгүй, давтагдах боломжтой).
"""
import logging

from sqlalchemy import text

from .database import engine

log = logging.getLogger("parking.migrations")

MIGRATIONS = [
    # v1.1 — НӨАТ байгууллагаар авах (ТТД)
    "ALTER TABLE payments ADD COLUMN IF NOT EXISTS customer_tin VARCHAR(20)",
    "ALTER TABLE vat_receipts ADD COLUMN IF NOT EXISTS customer_tin VARCHAR(20)",
    # v1.2 — QPay-ээр дамжуулсан e-Barimt 3.0 (ebarimt_v3)
    "ALTER TABLE payments ADD COLUMN IF NOT EXISTS provider_payment_id VARCHAR(120)",
    "ALTER TABLE payments ADD COLUMN IF NOT EXISTS ebarimt_receiver_type VARCHAR(20)",
    # v1.3 — операторын нэмэлт тэмдэглэл (касс)
    "ALTER TABLE parking_sessions ADD COLUMN IF NOT EXISTS note TEXT",
    # v1.4 — ээлж хаах тооцоо
    "ALTER TABLE cashier_shifts ADD COLUMN IF NOT EXISTS cash_confirmed NUMERIC(12,2)",
    "ALTER TABLE cashier_shifts ADD COLUMN IF NOT EXISTS closed_cars INTEGER",
    "ALTER TABLE cashier_shifts ADD COLUMN IF NOT EXISTS note TEXT",
    # v1.5 — QPay эх сурвалж (POS/QR) — санхүүгийн тооцоонд ялгах
    "ALTER TABLE payments ADD COLUMN IF NOT EXISTS source VARCHAR(10)",

    # v1.6 — Гүйцэтгэлийн index-үүд (тайлан/жагсаалтын hot path; scale дээр full scan-аас сэргийлнэ)
    # Орлогын тайлан бараг бүгд PAID + paid_at-аар шүүдэг
    "CREATE INDEX IF NOT EXISTS ix_payments_status_paid_at ON payments (status, paid_at)",
    "CREATE INDEX IF NOT EXISTS ix_payments_created_at ON payments (created_at)",
    "CREATE INDEX IF NOT EXISTS ix_payments_shift_id ON payments (shift_id)",
    "CREATE INDEX IF NOT EXISTS ix_payments_provider ON payments (provider)",
    # Session жагсаалт entry_time-аар эрэмбэлдэг; идэвхтэй session хайлт (site+plate+status)
    "CREATE INDEX IF NOT EXISTS ix_sessions_entry_time ON parking_sessions (entry_time)",
    "CREATE INDEX IF NOT EXISTS ix_sessions_exit_time ON parking_sessions (exit_time)",
    "CREATE INDEX IF NOT EXISTS ix_sessions_site_plate_status ON parking_sessions (site_id, plate_number, status)",
    # Ээлж + баримт + аудит
    "CREATE INDEX IF NOT EXISTS ix_shifts_status ON cashier_shifts (status)",
    "CREATE INDEX IF NOT EXISTS ix_shifts_opened_at ON cashier_shifts (opened_at)",
    "CREATE INDEX IF NOT EXISTS ix_shifts_user ON cashier_shifts (user_id)",
    "CREATE INDEX IF NOT EXISTS ix_shifts_site ON cashier_shifts (site_id)",
    "CREATE INDEX IF NOT EXISTS ix_vat_receipts_session ON vat_receipts (session_id)",
    "CREATE INDEX IF NOT EXISTS ix_vat_receipts_payment ON vat_receipts (payment_id)",
    "CREATE INDEX IF NOT EXISTS ix_audit_username ON audit_logs (username)",
    "CREATE INDEX IF NOT EXISTS ix_audit_action ON audit_logs (action)",

    # v1.7 — Бүрэн бүтэн байдал: нэг зогсоолд нэг дугаараар нэгэн зэрэг ганц идэвхтэй session
    # (LPR орох урсгалын race-ээс сэргийлнэ). Хэрэв одоо давхардсан идэвхтэй session байвал
    # энэ index үүсэхгүй (алгасна) — тухайн үед л гараар цэвэрлэнэ.
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_active_session ON parking_sessions (site_id, plate_number) "
    "WHERE status IN ('OPEN','AWAITING_PAYMENT','PAID')",

    # v1.8 — LPR snapshot (орох/гарах камерын зураг)
    "ALTER TABLE parking_sessions ADD COLUMN IF NOT EXISTS entry_snapshot VARCHAR(255)",
    "ALTER TABLE parking_sessions ADD COLUMN IF NOT EXISTS exit_snapshot VARCHAR(255)",

    # v1.9 — Хэрэглэгчийн эрхийн матриц (чекбокс) + операторын олон зогсоол
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS permissions JSON",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS site_ids JSON",

    # v2.0 — Гацсан session-ийн авто цэвэрлэгээ (зогсоол бүрийн босго)
    "ALTER TABLE parking_sites ADD COLUMN IF NOT EXISTS auto_close_hours INTEGER",

    # v2.1 — QR-аар өрийг нийлүүлж төлөх: нэгдсэн Payment-ийн холбоос
    "ALTER TABLE compensations ADD COLUMN IF NOT EXISTS payment_id UUID REFERENCES payments(id)",
    "CREATE INDEX IF NOT EXISTS ix_compensations_payment_id ON compensations (payment_id)",

    # v2.2 — Хэвлэгдсэн самбар дээрх QR линк (систем үүсгэх QR түүнтэй таарна)
    "ALTER TABLE parking_sites ADD COLUMN IF NOT EXISTS qr_url TEXT",

    # v2.3 — Зогсоол бүрийн өөрийн QPay мерчант данс (түрээслэгч тус бүрд)
    "ALTER TABLE parking_sites ADD COLUMN IF NOT EXISTS qpay_username VARCHAR(80)",
    "ALTER TABLE parking_sites ADD COLUMN IF NOT EXISTS qpay_password VARCHAR(160)",
    "ALTER TABLE parking_sites ADD COLUMN IF NOT EXISTS qpay_invoice_code VARCHAR(80)",
    "ALTER TABLE parking_sites ADD COLUMN IF NOT EXISTS qpay_branch_code VARCHAR(40)",
    "ALTER TABLE parking_sites ADD COLUMN IF NOT EXISTS qpay_district_code VARCHAR(10)",

    # v2.4 — Төхөөрөмж бүрийн өөрийн нэвтрэх мэдээлэл (камер бүр өөр нууц үгтэй байж болно)
    "ALTER TABLE devices ADD COLUMN IF NOT EXISTS username VARCHAR(60)",
    "ALTER TABLE devices ADD COLUMN IF NOT EXISTS password VARCHAR(160)",

    # v2.5 — Гэрээт жолоочийн байгууллага + тэмдэглэл (Excel импорт)
    "ALTER TABLE registered_drivers ADD COLUMN IF NOT EXISTS company VARCHAR(160)",
    "ALTER TABLE registered_drivers ADD COLUMN IF NOT EXISTS note TEXT",
    "CREATE INDEX IF NOT EXISTS ix_registered_drivers_company ON registered_drivers (company)",

    # v2.6 — Хаалтны командын гүйцэтгэлийн хугацаа (мс) — удаашралыг хэмжих
    "ALTER TABLE barrier_commands ADD COLUMN IF NOT EXISTS duration_ms INTEGER",

    # v2.7 — Нууц үг солигдсон хугацаа (өмнөх токенуудыг хүчингүй болгоход)
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS password_changed_at TIMESTAMP",

    # Ирээдүйд багана нэмэхэд ДООР нь ALTER ... ADD COLUMN IF NOT EXISTS бичнэ ↓

    # v1.x — зөвхөн орох камерт уншигдсан session-ийг үнэгүйгээр авто хаах босго
    "ALTER TABLE parking_sites ADD COLUMN IF NOT EXISTS entry_only_free_hours INTEGER",

    # v1.9 — ХАЛУУН ЗАМЫН индексүүд (2026-07-29 аудит). Машин орох/гарах бүрд
    # ажилладаг query-үүд болон retention-ы устгалт эдгээргүйгээр бүтэн хүснэгт
    # уншдаг байв (lpr_events нь DB-ийн 36%).
    # 1) dedup/burst шалгалт: site_id + lane_dir + created_at (event бүрд 2 удаа)
    "CREATE INDEX IF NOT EXISTS ix_lpr_site_lane_created ON lpr_events (site_id, lane_dir, created_at)",
    # 2) хаалтны cooldown шалгалт + эрүүл мэндийн самбар: device_id + created_at
    "CREATE INDEX IF NOT EXISTS ix_cmd_device_created ON barrier_commands (device_id, created_at)",
    # 3) retention устгалт + гүйцэтгэлийн тайлан: created_at
    "CREATE INDEX IF NOT EXISTS ix_cmd_created_at ON barrier_commands (created_at)",
    # 4) гарах бүрд өр шалгах: plate_number + status
    "CREATE INDEX IF NOT EXISTS ix_comp_plate_status ON compensations (plate_number, status)",
    # 5) гэрээт машин шалгах (гарах/орох бүрд): plate + идэвхтэй эсэх
    "CREATE INDEX IF NOT EXISTS ix_driver_plate_active ON registered_drivers (plate_number, is_active)",

    # v2.5 — зогсоол бүрийн LED дэлгэцийн мөрийн тохиргоо (Тохиргоо → LED дэлгэц)
    "ALTER TABLE parking_sites ADD COLUMN IF NOT EXISTS screen_config JSON",

    # v2.6 — Түрээслэгч (Tenant): бие даасан зогсоол/хэрэглэгч/тооцооны нэгж
    """CREATE TABLE IF NOT EXISTS tenants (
        id UUID PRIMARY KEY,
        name VARCHAR(160) NOT NULL,
        code VARCHAR(30) NOT NULL UNIQUE,
        register VARCHAR(20) DEFAULT '',
        contact_name VARCHAR(120) DEFAULT '',
        phone VARCHAR(20) DEFAULT '',
        email VARCHAR(120) DEFAULT '',
        note TEXT DEFAULT '',
        is_active BOOLEAN NOT NULL DEFAULT true,
        created_at TIMESTAMP NOT NULL DEFAULT (now() at time zone 'utc'))""",
    "CREATE INDEX IF NOT EXISTS ix_tenants_code ON tenants (code)",
    "ALTER TABLE parking_sites ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id)",
    "CREATE INDEX IF NOT EXISTS ix_parking_sites_tenant_id ON parking_sites (tenant_id)",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id)",
    "CREATE INDEX IF NOT EXISTS ix_users_tenant_id ON users (tenant_id)",

    # v2.7 — Гэрээт байгууллагын сарын нэхэмжлэл + харилцах мэдээлэл
    """CREATE TABLE IF NOT EXISTS company_contacts (
        id UUID PRIMARY KEY,
        company VARCHAR(160) NOT NULL UNIQUE,
        email VARCHAR(120) DEFAULT '',
        register VARCHAR(20) DEFAULT '',
        phone VARCHAR(20) DEFAULT '',
        created_at TIMESTAMP NOT NULL DEFAULT (now() at time zone 'utc'))""",
    """CREATE TABLE IF NOT EXISTS company_invoices (
        id UUID PRIMARY KEY,
        invoice_no VARCHAR(40) NOT NULL UNIQUE,
        period VARCHAR(7) NOT NULL,
        company VARCHAR(160) NOT NULL,
        car_count INTEGER NOT NULL DEFAULT 0,
        amount NUMERIC(12,2) NOT NULL DEFAULT 0,
        sessions INTEGER NOT NULL DEFAULT 0,
        minutes INTEGER NOT NULL DEFAULT 0,
        detail JSON NOT NULL DEFAULT '{}',
        status VARCHAR(20) NOT NULL DEFAULT 'DRAFT',
        sent_to VARCHAR(120) DEFAULT '',
        sent_at TIMESTAMP,
        paid_at TIMESTAMP,
        note TEXT DEFAULT '',
        created_at TIMESTAMP NOT NULL DEFAULT (now() at time zone 'utc'),
        CONSTRAINT uq_invoice_period_company UNIQUE (period, company))""",
    "CREATE INDEX IF NOT EXISTS ix_company_invoices_period ON company_invoices (period)",
    "CREATE INDEX IF NOT EXISTS ix_company_invoices_status ON company_invoices (status)",

    # v2.8 — байгууллага бүрийн төлбөрийн горим (урьдчилгаа/сарын эцэст/нэхэмжлэхгүй)
    "ALTER TABLE company_contacts ADD COLUMN IF NOT EXISTS billing_mode VARCHAR(10) NOT NULL DEFAULT 'POSTPAID'",
]


def run_migrations():
    with engine.begin() as conn:
        for stmt in MIGRATIONS:
            try:
                conn.execute(text(stmt))
            except Exception as e:  # нэг миграц алдвал бусдыг зогсоохгүй
                log.warning(f"[migration skip] {stmt[:60]}... — {e}")
