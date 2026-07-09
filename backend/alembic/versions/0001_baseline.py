"""baseline — одоо байгаа схемийг Alembic-т бүртгэх эхлэл цэг

Хоосон DB дээр models.py-аас бүх хүснэгтийг үүсгэнэ (create_all).
Одоо ажиллаж байгаа DB (хүснэгтүүд аль хэдийн үүссэн) дээр upgrade ажиллуулахгүй,
харин `alembic stamp 0001_baseline` гэж энэ хувилбарт тэмдэглэнэ.
Цаашид схемийн өөрчлөлт бүрийг `alembic revision --autogenerate` + `alembic upgrade head`.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-07-09
"""
from alembic import op

import app.models  # noqa: F401
from app.database import Base

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    Base.metadata.create_all(bind=op.get_bind())


def downgrade():
    Base.metadata.drop_all(bind=op.get_bind())
