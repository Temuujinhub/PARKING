"""parking_sessions.vehicle_color / vehicle_type — камерын нэмэлт таних мэдээлэл

Revision ID: 0013_vehicle_info
Revises: 0012_tenant_qpay
Create Date: 2026-08-02
"""
from alembic import op

revision = "0013_vehicle_info"
down_revision = "0012_tenant_qpay"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE parking_sessions ADD COLUMN IF NOT EXISTS vehicle_color VARCHAR(30)")
    op.execute("ALTER TABLE parking_sessions ADD COLUMN IF NOT EXISTS vehicle_type VARCHAR(30)")


def downgrade():
    op.execute("ALTER TABLE parking_sessions DROP COLUMN IF EXISTS vehicle_color")
    op.execute("ALTER TABLE parking_sessions DROP COLUMN IF EXISTS vehicle_type")
