"""parking_sites.screen_config — зогсоол бүрийн LED дэлгэцийн мөрийн тохиргоо

Тохиргоо → LED дэлгэц таб: орох/гарах дэлгэцийн мөр бүрд юу харуулахыг
(цаг/дугаар/хугацаа/дүн/текст/төлбөрийн төрөл/үнэгүй шалтгаан) сонгоно.
Startup-ийн migrations.py (bridge) мөн адил IF NOT EXISTS-ээр нэмдэг тул idempotent.

Revision ID: 0007_site_screen_config
Revises: 0006_driver_company
Create Date: 2026-07-31
"""
from alembic import op

revision = "0007_site_screen_config"
down_revision = "0006_driver_company"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE parking_sites ADD COLUMN IF NOT EXISTS screen_config JSON")


def downgrade():
    op.execute("ALTER TABLE parking_sites DROP COLUMN IF EXISTS screen_config")
