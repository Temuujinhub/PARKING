"""parking_sites.qr_url — талбайд хэвлэгдсэн самбар дээрх QR линк

Бөглөгдсөн бол /api/public/qr/<код>.png нь ЯГ энэ мөрөөр QR үүсгэнэ — ингэснээр
хэвлэгдчихсэн самбаруудыг солихгүйгээр үргэлжлүүлэн ашиглана.

Startup-ийн migrations.py (bridge) мөн адил IF NOT EXISTS-ээр нэмдэг тул idempotent.

Revision ID: 0004_site_qr_url
Revises: 0003_site_auto_close
Create Date: 2026-07-26
"""
from alembic import op

revision = "0004_site_qr_url"
down_revision = "0003_site_auto_close"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE parking_sites ADD COLUMN IF NOT EXISTS qr_url TEXT")


def downgrade():
    op.execute("ALTER TABLE parking_sites DROP COLUMN IF EXISTS qr_url")
