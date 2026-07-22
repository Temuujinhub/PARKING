"""parking_sites.auto_close_hours — гацсан session-ийн авто цэвэрлэгээний босго

Startup-ийн migrations.py (bridge) мөн адил IF NOT EXISTS-ээр нэмдэг тул idempotent.

Revision ID: 0003_site_auto_close
Revises: 0002_user_perms_sites
Create Date: 2026-07-22
"""
from alembic import op

revision = "0003_site_auto_close"
down_revision = "0002_user_perms_sites"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE parking_sites ADD COLUMN IF NOT EXISTS auto_close_hours INTEGER")


def downgrade():
    op.execute("ALTER TABLE parking_sites DROP COLUMN IF EXISTS auto_close_hours")
