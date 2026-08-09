"""app_settings — UI-аас тохируулдаг систем дүрэм (хар жагсаалтын босго г.м)

Revision ID: 0014_app_settings
Revises: 0013_vehicle_info
Create Date: 2026-08-09
"""
from alembic import op

revision = "0014_app_settings"
down_revision = "0013_vehicle_info"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE IF NOT EXISTS app_settings (
        key VARCHAR(60) PRIMARY KEY,
        value JSON NOT NULL DEFAULT '{}',
        updated_by VARCHAR(60),
        updated_at TIMESTAMP NOT NULL DEFAULT now()
    )""")


def downgrade():
    op.execute("DROP TABLE IF EXISTS app_settings")
