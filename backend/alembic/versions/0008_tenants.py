"""Түрээслэгч (Tenant) — бие даасан зогсоол/хэрэглэгч/тооцооны нэгж

tenants хүснэгт + parking_sites.tenant_id + users.tenant_id.
Startup-ийн migrations.py (bridge) мөн адил IF NOT EXISTS-ээр нэмдэг тул idempotent.

Revision ID: 0008_tenants
Revises: 0007_site_screen_config
Create Date: 2026-08-01
"""
from alembic import op

revision = "0008_tenants"
down_revision = "0007_site_screen_config"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE IF NOT EXISTS tenants (
        id UUID PRIMARY KEY,
        name VARCHAR(160) NOT NULL,
        code VARCHAR(30) NOT NULL UNIQUE,
        register VARCHAR(20) DEFAULT '',
        contact_name VARCHAR(120) DEFAULT '',
        phone VARCHAR(20) DEFAULT '',
        email VARCHAR(120) DEFAULT '',
        note TEXT DEFAULT '',
        is_active BOOLEAN NOT NULL DEFAULT true,
        created_at TIMESTAMP NOT NULL DEFAULT (now() at time zone 'utc'))""")
    op.execute("CREATE INDEX IF NOT EXISTS ix_tenants_code ON tenants (code)")
    op.execute("ALTER TABLE parking_sites ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_parking_sites_tenant_id ON parking_sites (tenant_id)")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_users_tenant_id ON users (tenant_id)")


def downgrade():
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS tenant_id")
    op.execute("ALTER TABLE parking_sites DROP COLUMN IF EXISTS tenant_id")
    op.execute("DROP TABLE IF EXISTS tenants")
