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
    # Ирээдүйд багана нэмэхэд ДООР нь ALTER ... ADD COLUMN IF NOT EXISTS бичнэ ↓
]


def run_migrations():
    with engine.begin() as conn:
        for stmt in MIGRATIONS:
            try:
                conn.execute(text(stmt))
            except Exception as e:  # нэг миграц алдвал бусдыг зогсоохгүй
                print(f"[migration skip] {stmt[:60]}... — {e}")
