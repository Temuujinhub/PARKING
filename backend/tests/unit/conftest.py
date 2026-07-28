"""Pytest багцын нийтлэг тохиргоо.

Энэ багц нь ЦЭВЭР ЛОГИК-ийн тест — DB, сүлжээ, төхөөрөмж шаардахгүй. Тиймээс
CI дээр ямар ч дэд бүтэцгүйгээр ажиллана. (tests/*.py дахь хуучин скриптүүд нь
амьд Postgres шаарддаг тул pytest.ini-д зөвхөн tests/unit цуглуулагддаг.)
"""
import os
import sys

# Бодит .env-ийн production утгууд тестэд нөлөөлөхгүй байх
os.environ.setdefault("PARKING_DEBUG", "true")
os.environ.setdefault("PARKING_QPAY_MOCK", "true")
os.environ.setdefault("PARKING_EBARIMT_MOCK", "true")
os.environ.setdefault("PARKING_BARRIER_MOCK", "true")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pytest  # noqa: E402

from app.config import settings  # noqa: E402


@pytest.fixture
def vat_inclusive():
    """НӨАТ үнэд багтсан, 10% — тарифын тооцооллын үндсэн горим."""
    old = (settings.vat_rate, settings.vat_inclusive)
    settings.vat_rate, settings.vat_inclusive = 0.10, True
    yield
    settings.vat_rate, settings.vat_inclusive = old
