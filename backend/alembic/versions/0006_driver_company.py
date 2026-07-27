"""registered_drivers.company / note — Excel-ээс импортлох гэрээт машины жагсаалт

Startup-ийн migrations.py (bridge) мөн адил IF NOT EXISTS-ээр нэмдэг тул idempotent.

Revision ID: 0006_driver_company
Revises: 0005_site_qpay_device_creds
Create Date: 2026-07-27
"""
from alembic import op

revision = "0006_driver_company"
down_revision = "0005_site_qpay_device_creds"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE registered_drivers ADD COLUMN IF NOT EXISTS company VARCHAR(160)")
    op.execute("ALTER TABLE registered_drivers ADD COLUMN IF NOT EXISTS note TEXT")
    op.execute("CREATE INDEX IF NOT EXISTS ix_registered_drivers_company "
               "ON registered_drivers (company)")


def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_registered_drivers_company")
    op.execute("ALTER TABLE registered_drivers DROP COLUMN IF EXISTS note")
    op.execute("ALTER TABLE registered_drivers DROP COLUMN IF EXISTS company")
