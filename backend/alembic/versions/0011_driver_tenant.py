"""registered_drivers.tenant_id — «Бүх зогсоол» эрх түрээслэгчээр хязгаарлагдана

Revision ID: 0011_driver_tenant
Revises: 0010_billing_mode
Create Date: 2026-08-01
"""
from alembic import op

revision = "0011_driver_tenant"
down_revision = "0010_billing_mode"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE registered_drivers ADD COLUMN IF NOT EXISTS "
               "tenant_id UUID REFERENCES tenants(id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_registered_drivers_tenant_id "
               "ON registered_drivers (tenant_id)")


def downgrade():
    op.execute("ALTER TABLE registered_drivers DROP COLUMN IF EXISTS tenant_id")
