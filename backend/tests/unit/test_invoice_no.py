"""QPay `sender_invoice_no`-ийн урт — 45 тэмдэгтийн хязгаар.

2026-08-28 production: «Их Монгол ресторан» зогсоолын БҮХ жолооч QR-аар төлж
чадахгүй байв. Лог: HTTP 400
`{"sender_invoice_no":{"type":"MAX_LENGTH","message":"String max length (45)!"}}`

Шалтгаан: гүйлгээний дугаарыг `{зогсоолын_код}-{дугаар}-{огноо}-{6HEX}` гэж
угсардаг байсан ч уртыг нь ХЭЗЭЭ Ч шалгадаггүй байв:

    PARK_IKH_MONGOL_RESTORANT-0128УНМ-20260828-A1B2C3  → 49 тэмдэгт ❌

Бусад 16 зогсоолын код богино тул (хамгийн урт нь 38 тэмдэгт) огт мэдэгдэлгүй,
«урт кодтой зогсоол нэмэх хүртэл нам гүм хэвтэх» алдаа болж байсан. Эдгээр тест
нь ЯМАР Ч зогсоолын код / машины дугаарт хязгаар мөрдөгдөхийг барина.
"""
import pytest

from app.routers.payments_router import QPAY_INVOICE_NO_MAX, _fit_bytes, _invoice_no


class FakeSite:
    def __init__(self, code):
        self.site_code = code


class FakeSession:
    def __init__(self, code, plate):
        self.site = FakeSite(code) if code is not None else None
        self.plate_number = plate


PROD_CODES = [
    "PARK_IKH_MONGOL_RESTORANT",  # ← 2026-08-28-нд унасан зогсоол
    "PARK_BSB_MEBEL", "AREA_MARGAD", "AIRNETWORK", "3HOSPITAL", "HANGARID",
    "TUUSHIN", "MARSHIL", "BOSAHUT", "NOMADS", "YLALT1", "SITE10", "SPORT",
    "EREL", "RASH", "STO", "KH", "MONNIS",
]


@pytest.mark.parametrize("code", PROD_CODES)
def test_prod_sites_fit(code):
    """Production дээрх зогсоол бүр хязгаарт багтана."""
    no = _invoice_no(FakeSession(code, "0128УНМ"))
    assert len(no) <= QPAY_INVOICE_NO_MAX, no


@pytest.mark.parametrize("code", [
    "PARK_IKH_MONGOL_RESTORANT",
    "A" * 60,                                    # хэт урт код
    "ЗОГСООЛЫН_МАШ_УРТ_КИРИЛЛ_КОД",              # кирилл (2 байт/тэмдэгт)
    "X",
])
@pytest.mark.parametrize("plate", ["0128УНМ", "1234АБВ", "УБ1234567890", "A" * 12, ""])
def test_never_exceeds_limit(code, plate):
    """Код/дугаар ямар ч урттай байсан хязгаар мөрдөгдөнө — тэмдэгтээр Ч, байтаар Ч.
    (QPay-ийн хэмжих нэгж баримтжаагүй тул хоёуланг нь барина.)"""
    no = _invoice_no(FakeSession(code, plate))
    assert len(no) <= QPAY_INVOICE_NO_MAX, f"{len(no)} тэмдэгт: {no}"
    assert len(no.encode()) <= QPAY_INVOICE_NO_MAX, f"{len(no.encode())} байт: {no}"


def test_plate_survives_truncation():
    """Тайрахдаа МАШИНЫ ДУГААРЫГ хэвээр үлдээж, зогсоолын кодыг богиносгоно —
    банкны хуулга/QPay портал дээр дугаараар нь олох нь тулгалтын гол хэрэгсэл."""
    no = _invoice_no(FakeSession("PARK_IKH_MONGOL_RESTORANT", "0128УНМ"))
    assert "0128УНМ" in no
    assert no.startswith("PARK_IKH_MONGOL")  # код таних хэмжээнд үлдсэн


def test_tail_never_truncated():
    """Огноо + санамсаргүй сүүл нь давхцлаас хамгаалдаг — хэзээ ч тайрагдахгүй
    (sender_invoice_no нь DB-д unique)."""
    no = _invoice_no(FakeSession("A" * 60, "1234АБВ"))
    head, date, rand = no.rsplit("-", 2)
    assert len(date) == 8 and date.isdigit()
    assert len(rand) == 6


def test_unique_across_calls():
    """Ижил зогсоол, ижил машин — дугаар давхцахгүй."""
    ses = FakeSession("PARK_IKH_MONGOL_RESTORANT", "0128УНМ")
    assert len({_invoice_no(ses) for _ in range(200)}) == 200


def test_no_site():
    """Зогсоолгүй session (хуучин өгөгдөл) дээр ч унахгүй."""
    no = _invoice_no(FakeSession(None, "0128УНМ"))
    assert 0 < len(no) <= QPAY_INVOICE_NO_MAX


def test_fit_bytes_keeps_characters_whole():
    """Кирилл тэмдэгтийг хагасаар тасалж болохгүй (UTF-8 эвдэрнэ)."""
    assert _fit_bytes("УНМ", 3) == "У"      # 2 байт багтана, 2 дахь нь 4 байт болно
    assert _fit_bytes("УНМ", 0) == ""
    assert _fit_bytes("ABC", 10) == "ABC"


def test_service_rejects_oversized_number():
    """Дуудагч тал эвдэрсэн ч QPay руу хэт урт дугаар ЧИМЭЭГҮЙ явахгүй."""
    import asyncio

    from app.services import qpay
    with pytest.raises(ValueError, match="sender_invoice_no"):
        asyncio.run(qpay.create_invoice("X" * 46, "d", "t", "cb", []))


# ─────────── Бусад гүйлгээний дугаарууд ч ижил хязгаарт орно ───────────
# 2026-08-28: зөвхөн зогсоолын төлбөрийг зассан бол хангалтгүй байв. Ижил
# загварын дугаарыг өөр 3 газар угсардаг бөгөөд админы «Данс шалгах» нь яг 45
# дээр зогсож байсан — 26 тэмдэгтийн кодтой зогсоол нэмэхэд чимээгүй унах байв.
from app.services import qpay  # noqa: E402

SITE_CODE_MAX = 30  # schemas.SiteCreate.site_code


def test_admin_qpay_test_invoice_fits():
    """Админы «Данс шалгах»: TEST-{код}-{14 оронтой цаг}."""
    stamp = "-20260828071600"
    for code in ("A" * SITE_CODE_MAX, "PARK_IKH_MONGOL_RESTORANT", "KH"):
        no = qpay.fit_bytes(f"TEST-{code}",
                            qpay.SENDER_INVOICE_NO_MAX - len(stamp)) + stamp
        assert len(no.encode()) <= qpay.SENDER_INVOICE_NO_MAX, no


def test_wallet_topup_invoice_fits():
    """Данс цэнэглэх: WT-{дугаар}-{8 HEX}. Дугаар нь урт/кирилл байж болно."""
    for plate in ("0128УНМ", "А" * 20, "1234ABC"):
        no = qpay.fit_bytes(f"WT-{plate}", qpay.SENDER_INVOICE_NO_MAX - 9) + "-A1B2C3D4"
        assert len(no.encode()) <= qpay.SENDER_INVOICE_NO_MAX, no


def test_ev_invoice_fits():
    """EV цэнэглэлт: EV-{session 8}-{ocpp_tx_id} — tx_id урт байж болно."""
    no = qpay.fit_bytes(f"EV-{'a' * 8}-{'9' * 40}", qpay.SENDER_INVOICE_NO_MAX)
    assert len(no.encode()) <= qpay.SENDER_INVOICE_NO_MAX
