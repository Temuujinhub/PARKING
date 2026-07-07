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
    # Ирээдүйд багана нэмэхэд ДООР нь ALTER ... ADD COLUMN IF NOT EXISTS бичнэ ↓
]


def run_migrations():
    with engine.begin() as conn:
        for stmt in MIGRATIONS:
            try:
                conn.execute(text(stmt))
            except Exception as e:  # нэг миграц алдвал бусдыг зогсоохгүй
                print(f"[migration skip] {stmt[:60]}... — {e}")
