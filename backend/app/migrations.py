"""Хөнгөн idempotent миграци — production DB-г кодтой тааруулна.

SQLAlchemy create_all() нь шинэ ХҮСНЭГТ үүсгэдэг ч байгаа хүснэгтэд шинэ БАГАНА нэмдэггүй.
Тиймээс шинэ багана нэмэх бүрд энд `ADD COLUMN IF NOT EXISTS` мөр нэмнэ.
Startup бүрт ажиллах ба аль хэдийн байгаа бол алгасна (аюулгүй, давтагдах боломжтой).
"""
from sqlalchemy import text

from .database import engine

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

    # Ирээдүйд багана нэмэхэд ДООР нь ALTER ... ADD COLUMN IF NOT EXISTS бичнэ ↓
]


def run_migrations():
    with engine.begin() as conn:
        for stmt in MIGRATIONS:
            try:
                conn.execute(text(stmt))
            except Exception as e:  # нэг миграц алдвал бусдыг зогсоохгүй
                print(f"[migration skip] {stmt[:60]}... — {e}")
