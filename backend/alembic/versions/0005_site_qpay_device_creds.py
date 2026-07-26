"""Зогсоол бүрийн QPay мерчант данс + төхөөрөмж бүрийн нэвтрэх мэдээлэл

Түрээслэгч байгууллага бүр өөрийн QPay гэрээтэй байж болно (төлбөр нь тэдний
данс руу, e-Barimt нь тэдний ТТД-ээр). Мөн зогсоол бүрийн камерууд өөр өөр
нууц үгтэй байж болно. Хоосон бол .env-ийн глобал утга үйлчилнэ.

Startup-ийн migrations.py (bridge) мөн адил IF NOT EXISTS-ээр нэмдэг тул idempotent.

Revision ID: 0005_site_qpay_device_creds
Revises: 0004_site_qr_url
Create Date: 2026-07-26
"""
from alembic import op

revision = "0005_site_qpay_device_creds"
down_revision = "0004_site_qr_url"
branch_labels = None
depends_on = None

SITE_COLS = [
    ("qpay_username", "VARCHAR(80)"),
    ("qpay_password", "VARCHAR(160)"),
    ("qpay_invoice_code", "VARCHAR(80)"),
    ("qpay_branch_code", "VARCHAR(40)"),
    ("qpay_district_code", "VARCHAR(10)"),
]
DEVICE_COLS = [("username", "VARCHAR(60)"), ("password", "VARCHAR(160)")]


def upgrade():
    for col, typ in SITE_COLS:
        op.execute(f"ALTER TABLE parking_sites ADD COLUMN IF NOT EXISTS {col} {typ}")
    for col, typ in DEVICE_COLS:
        op.execute(f"ALTER TABLE devices ADD COLUMN IF NOT EXISTS {col} {typ}")


def downgrade():
    for col, _ in SITE_COLS:
        op.execute(f"ALTER TABLE parking_sites DROP COLUMN IF EXISTS {col}")
    for col, _ in DEVICE_COLS:
        op.execute(f"ALTER TABLE devices DROP COLUMN IF EXISTS {col}")
