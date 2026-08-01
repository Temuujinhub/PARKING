"""Гэрээт байгууллагын сарын нэхэмжлэл (company_invoices) + харилцах (company_contacts)

Startup-ийн migrations.py (bridge) мөн адил IF NOT EXISTS-ээр нэмдэг тул idempotent.

Revision ID: 0009_company_invoices
Revises: 0008_tenants
Create Date: 2026-08-01
"""
from alembic import op

revision = "0009_company_invoices"
down_revision = "0008_tenants"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE IF NOT EXISTS company_contacts (
        id UUID PRIMARY KEY,
        company VARCHAR(160) NOT NULL UNIQUE,
        email VARCHAR(120) DEFAULT '',
        register VARCHAR(20) DEFAULT '',
        phone VARCHAR(20) DEFAULT '',
        created_at TIMESTAMP NOT NULL DEFAULT (now() at time zone 'utc'))""")
    op.execute("""CREATE TABLE IF NOT EXISTS company_invoices (
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
        CONSTRAINT uq_invoice_period_company UNIQUE (period, company))""")
    op.execute("CREATE INDEX IF NOT EXISTS ix_company_invoices_period ON company_invoices (period)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_company_invoices_status ON company_invoices (status)")


def downgrade():
    op.execute("DROP TABLE IF EXISTS company_invoices")
    op.execute("DROP TABLE IF EXISTS company_contacts")
