"""tenants.qpay_* — QPay данс түрээслэгчийн түвшинд

Багана нэмнэ (өгөгдлийн засвар/өргөлт нь migrations.py bridge-д, идемпотент).

Revision ID: 0012_tenant_qpay
Revises: 0011_driver_tenant
Create Date: 2026-08-01
"""
from alembic import op

revision = "0012_tenant_qpay"
down_revision = "0011_driver_tenant"
branch_labels = None
depends_on = None


def upgrade():
    for col, typ in (("qpay_username", "VARCHAR(80)"), ("qpay_password", "VARCHAR(160)"),
                     ("qpay_invoice_code", "VARCHAR(80)"), ("qpay_branch_code", "VARCHAR(40)"),
                     ("qpay_district_code", "VARCHAR(10)")):
        op.execute(f"ALTER TABLE tenants ADD COLUMN IF NOT EXISTS {col} {typ}")


def downgrade():
    for col in ("qpay_username", "qpay_password", "qpay_invoice_code",
                "qpay_branch_code", "qpay_district_code"):
        op.execute(f"ALTER TABLE tenants DROP COLUMN IF EXISTS {col}")
