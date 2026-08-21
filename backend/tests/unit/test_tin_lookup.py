"""Регистрээр татвар төлөгч шалгах — POS дээр НЭР харуулахад.

ГОЛ ЗАРЧИМ: суваг ажиллахгүй байгааг «олдсонгүй»-гээс ЯЛГАНА. Оператор буруу
нэр хараад итгэх нь огт шалгаагүй байхаас дор.
"""
import asyncio

import pytest

from app.config import settings
from app.services import tin_lookup


def look(reg_no):
    """`lookup` нь async — багц pytest-asyncio-гүй тул энд синхроноор дуудна."""
    return asyncio.run(tin_lookup.lookup(reg_no))


@pytest.fixture(autouse=True)
def clean_cache():
    tin_lookup._cache.clear()
    yield
    tin_lookup._cache.clear()


@pytest.fixture
def no_channel():
    old = (settings.ebarimt_mock, settings.ebarimt_tin_lookup_url)
    settings.ebarimt_mock, settings.ebarimt_tin_lookup_url = True, ""
    yield
    settings.ebarimt_mock, settings.ebarimt_tin_lookup_url = old


def test_bad_format_is_answered_not_looked_up(no_channel):
    """Формат буруу бол сүлжээ рүү огт явахгүй — шууд хариулна."""
    r = look("abc")
    assert r["available"] is True and r["found"] is False
    assert r["error"] == "Формат буруу"


def test_no_channel_is_unavailable_not_not_found(no_channel):
    """Суваг байхгүй бол `available=false` — «олдсонгүй» ГЭЖ ХЭЛЭХГҮЙ."""
    r = look("1234567")
    assert r["available"] is False
    assert r["found"] is False
    assert "суваг" in r["error"].lower()


def test_format_drives_receipt_type(no_channel):
    assert look("1234567")["receipt_type"] == "ORGANIZATION"
    assert look("12345678901")["receipt_type"] == "ORGANIZATION"
    assert look("АА00112233")["receipt_type"] == "CITIZEN"


def test_cache_serves_second_call(no_channel, monkeypatch):
    """Хоёр дахь дуудлага кэшээс — POS дээр хариу шуурхай гарна."""
    tin_lookup._remember("1234567", {"available": True, "found": True,
                                     "name": "Тест ХХК", "reg_no": "1234567",
                                     "receipt_type": "ORGANIZATION",
                                     "is_vat_payer": True, "tin": "12345678901",
                                     "source": "cache-test", "error": None})
    r = look("1234567")
    assert r["name"] == "Тест ХХК" and r["source"] == "cache-test"


def test_vat_payer_detection():
    """НӨАТ төлөгч эсэхийг талбарын өөр өөр нэрээр таних."""
    assert tin_lookup._is_vat_payer({"vatPayerRegisteredDate": "2020-01-01"}) is True
    assert tin_lookup._is_vat_payer({"isVatPayer": True}) is True
    assert tin_lookup._is_vat_payer({"isVatPayer": False}) is False
    # Мэдэгдэхгүй — False ГЭЖ ХЭЛЭХГҮЙ (POS дээр «мэдэгдэхгүй» гэж харуулна)
    assert tin_lookup._is_vat_payer({"name": "Тест"}) is None
    assert tin_lookup._is_vat_payer({"vatPayerRegisteredDate": ""}) is None


def test_channels_prefer_local_posapi():
    """PosAPI суусан бол түүнийг ЭХЭЛЖ ашиглана (сүлжээнээс хамаарахгүй)."""
    old = (settings.ebarimt_mock, settings.ebarimt_tin_lookup_url,
           settings.ebarimt_posapi_url)
    try:
        settings.ebarimt_mock = False
        settings.ebarimt_posapi_url = "http://localhost:7080/rest"
        settings.ebarimt_tin_lookup_url = "https://api.ebarimt.mn/api"
        names = [n for n, _ in tin_lookup._bases()]
        assert names == ["posapi", "ebarimt.mn"]
        settings.ebarimt_mock = True          # PosAPI суугаагүй
        assert [n for n, _ in tin_lookup._bases()] == ["ebarimt.mn"]
    finally:
        (settings.ebarimt_mock, settings.ebarimt_tin_lookup_url,
         settings.ebarimt_posapi_url) = old
