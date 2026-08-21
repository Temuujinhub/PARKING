"""msgbill `payer_reg_no` — гурван форматыг таньж receipt_type-ыг тодорхойлох.

2026-08-21-нээс msgbill гурван форматыг зэрэг хүлээж авдаг болсон:
  • байгууллагын регистр — 7 орон
  • ТТД — 11–14 орон
  • иргэний регистр — 2 кирилл үсэг + 8 орон

Өмнө нь бид ЗӨВХӨН ААН-ийн ТТД дамжуулдаг байсан тул иргэн хүн баримтаа нэр
дээрээ авч, сугалаанд оролцох боломжгүй байв.
"""
import pytest

from app.services.msgbill import classify_reg_no


@pytest.mark.parametrize("value,expected", [
    # ААН регистр (7 орон)
    ("1234567", ("1234567", "ORGANIZATION")),
    # ТТД (11–14 орон)
    ("12345678901", ("12345678901", "ORGANIZATION")),
    ("12345678901234", ("12345678901234", "ORGANIZATION")),
    # Иргэний регистр
    ("АА00112233", ("АА00112233", "CITIZEN")),
    ("уб12345678", ("УБ12345678", "CITIZEN")),      # жижиг үсгийг том болгоно
    ("ӨҮ11223344", ("ӨҮ11223344", "CITIZEN")),      # Ө/Ү үсэг
])
def test_known_formats(value, expected):
    assert classify_reg_no(value) == expected


@pytest.mark.parametrize("value", [
    "", None, "   ", "12345", "123456789", "123456789012345",
    "AA00112233",          # ЛАТИН үсэг — иргэний регистр биш
    "АА0011223",           # цифр дутуу
    "ААА00112233",         # үсэг илүү
    "abc", "1234567890123456789",
])
def test_unknown_is_anonymous_citizen(value):
    """Танигдахгүй утга алдаа өгөхгүй — нэргүй энгийн баримт болно.
    POS дээр жолооч буруу бичихэд төлбөр таслагдах ёсгүй."""
    assert classify_reg_no(value) == (None, "CITIZEN")


@pytest.mark.parametrize("value,cleaned", [
    ("  1234567  ", "1234567"),
    ("АА-0011-2233", "АА00112233"),
    ("1234 5678 901", "12345678901"),
])
def test_separators_are_stripped(value, cleaned):
    """Зай/зураас нь POS-ийн гар оролтод элбэг — цэвэрлэнэ."""
    assert classify_reg_no(value)[0] == cleaned
