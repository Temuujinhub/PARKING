"""users.permissions (эрхийн чекбокс матриц) + users.site_ids (операторын олон зогсоол)

Startup-ийн migrations.py (bridge) мөн адил IF NOT EXISTS-ээр нэмдэг тул
аль нь түрүүлж ажилласан ч алдаа гарахгүй (idempotent).

Revision ID: 0002_user_perms_sites
Revises: 0001_baseline
Create Date: 2026-07-22
"""
from alembic import op

revision = "0002_user_perms_sites"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS permissions JSON")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS site_ids JSON")


def downgrade():
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS site_ids")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS permissions")
