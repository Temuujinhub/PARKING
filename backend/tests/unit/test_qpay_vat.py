"""QPay нэхэмжлэлийн НӨАТ — float-ын алдаанаас QR үүсэхгүй болж байсан регресс.

2026-08-21: Хангарьд/Моннис дээр «QPay-тэй холбогдож чадсангүй» гэж QR огт
үүсэхгүй байв. Лог: HTTP 400 `VAT_AMOUNT_INVALID`. Шалтгаан нь НӨАТ-ыг float-оор
бодоод 4 орноор ТАСАЛЖ байсанд:

    11000 * 0.1 / 1.1 = 999.9999999999999 → тасалбал 999.9999 (зөв нь 1000.0000)

11-т хуваагддаг БҮХ дүн (1,100 / 5,500 / 11,000 / 22,000 …) ингэж нэг нэгжээр
доогуур явж, QPay татгалзана. Тэдгээр нь яг л түгээмэл тарифын дүнгүүд.

Мөн: QPay НӨАТ-ыг МӨР БҮРЭЭР шалгадаг тул нийлбэрт «тэнцүүлж» үлдэгдлийг нэг
мөрд нэмэх нь тэр мөрийг буруу болгоно (бодит дансаар туршиж баталсан).
"""
import pytest

from app.config import settings
from app.services.qpay import QpayAccount, _vat_of, _vat_units, build_lines


@pytest.fixture(autouse=True)
def vat10():
    old = settings.vat_rate
    settings.vat_rate = 0.10
    yield
    settings.vat_rate = old


ACC = QpayAccount(username="T", password="T", base_url="https://x", invoice_code="I",
                  branch_code="B", district_code="0000", tax_type="1",
                  classification_code="0000", mock=False)


@pytest.mark.parametrize("amount,expected", [
    (1100, 100.0),        # ← float-оор 99.9999 болдог байсан
    (5500, 500.0),        # ←            499.9999
    (11000, 1000.0),      # ←            999.9999  (Хангарьдын бодит дүн)
    (22000, 2000.0),
    (110000, 10000.0),
])
def test_exact_vat_not_lost(amount, expected):
    """11-т хуваагддаг дүнгийн НӨАТ ЯГ гарна — нэг нэгжээр ч доошоо явахгүй."""
    assert _vat_of(amount) == expected


@pytest.mark.parametrize("amount,expected", [
    (1000, 90.909),       # 90.9090909… → 4 орноор ТАСАЛНА
    (1500, 136.3636),
    (2000, 181.8181),
    (8000, 727.2727),
    (23000, 2090.909),
])
def test_fractional_vat_truncated(amount, expected):
    """Тасархайтай дүнг 4 орноор ТАСАЛНА (бөөрөнхийлөхгүй) — QPay-ийн дүрэм."""
    assert _vat_of(amount) == expected


def test_vat_never_rounds_up():
    """Бөөрөнхийлбөл QPay татгалзана — ямар ч дүнд бодит утгаас ИХ гарахгүй."""
    for amount in range(100, 30000, 137):
        assert _vat_of(amount) <= amount * 0.1 / 1.1 + 1e-9


def test_each_line_keeps_own_vat():
    """Мөр бүрийн НӨАТ нь ЗӨВХӨН өөрийнхөө дүнгээс гарна — нийлбэрт тэнцүүлэхийн
    тулд аль нэг мөрд үлдэгдэл нэмэхгүй (QPay мөр бүрээр шалгадаг)."""
    items = [{"description": "a", "unit_price": 1000},
             {"description": "b", "unit_price": 2000},
             {"description": "c", "unit_price": 5500}]
    lines = build_lines(items, ACC)
    got = [ln["taxes"][0]["amount"] for ln in lines]
    assert got == [_vat_of(1000), _vat_of(2000), _vat_of(5500)]
    assert got == [90.909, 181.8181, 500.0]


def test_no_taxes_when_merchant_not_vat():
    """tax_type 2/3 (НӨАТ тооцохгүй) үед татварын мөр огт нэмэхгүй."""
    acc = QpayAccount(username="T", password="T", base_url="https://x", invoice_code="I",
                      branch_code="B", district_code="0000", tax_type="3",
                      classification_code="0000", mock=False)
    lines = build_lines([{"description": "a", "unit_price": 1000}], acc)
    assert "taxes" not in lines[0]


def test_line_price_format():
    """Дүн нь ЯМАГТ 2 оронтой мөр — QPay форматын шаардлага."""
    lines = build_lines([{"description": "a", "unit_price": 11000}], ACC)
    assert lines[0]["line_unit_price"] == "11000.00"
    assert lines[0]["line_quantity"] == "1.00"


def test_vat_units_are_integers():
    """1/10000 нэгж нь БҮХЭЛ — float хуримтлал үүсэхгүй."""
    for amount in (999, 1000, 1100, 5500, 11000):
        assert isinstance(_vat_units(amount), int)
