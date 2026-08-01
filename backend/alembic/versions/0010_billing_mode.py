"""company_contacts.billing_mode — урьдчилгаа/сарын эцэст/нэхэмжлэхгүй горим

Revision ID: 0010_billing_mode
Revises: 0009_company_invoices
Create Date: 2026-08-01
"""
from alembic import op

revision = "0010_billing_mode"
down_revision = "0009_company_invoices"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE company_contacts ADD COLUMN IF NOT EXISTS "
               "billing_mode VARCHAR(10) NOT NULL DEFAULT 'POSTPAID'")


def downgrade():
    op.execute("ALTER TABLE company_contacts DROP COLUMN IF EXISTS billing_mode")
