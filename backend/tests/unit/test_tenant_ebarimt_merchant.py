"""Баримт ХЭНИЙ ТТД-ээр гарах вэ — түрээслэгч бүр өөрийнхөөрөө.

2026-08-21: Моннисын зогсоолын картын баримт EasyParking-ийн ТТД-ээр гардаг байв
(`.env`-ийн глобал `merchantTin`). Өөр татвар төлөгчийн нэр дээр баримт гаргаж
байсан хэрэг. Одоо зогсоол → түрээслэгч → түүний ТТД.

ЧУХАЛ: түрээслэгчид ТТД тохируулаагүй бол глобал руу УНАХГҮЙ — `MerchantMissing`
шидэж баримтыг FAILED гэж бүртгэнэ. Чимээгүй буруу ТТД-ээр гаргахаас тэрбум
дахин дээр.
"""
import pytest

from app.config import settings
from app.services.ebarimt import (Merchant, MerchantMissing, _build_payload,
                                  merchant_for)


class _Tenant:
    def __init__(self, name, tin=None, district=None, branch=None):
        self.name = name
        self.ebarimt_merchant_tin = tin
        self.ebarimt_district_code = district
        self.ebarimt_branch_no = branch


class _Site:
    def __init__(self, tenant=None):
        self.tenant = tenant
        self.tenant_id = "t1" if tenant else None


EASYPARKING = _Tenant("ИйзиПаркинг ХХК", "15200020090", "2318")
MONNIS = _Tenant("Моннис пропэрти", "71101242183", "2318")


@pytest.mark.parametrize("tenant,tin", [
    (EASYPARKING, "15200020090"),
    (MONNIS, "71101242183"),
])
def test_receipt_uses_own_tenant_tin(tenant, tin):
    m = merchant_for(_Site(tenant))
    assert m.tin == tin
    assert m.source == "tenant"
    assert m.district_code == "2318"


def test_two_tenants_never_share_a_tin():
    """Хоёр түрээслэгчийн баримт ХЭЗЭЭ Ч нэг ТТД дээр гарахгүй."""
    a = merchant_for(_Site(EASYPARKING))
    b = merchant_for(_Site(MONNIS))
    assert a.tin != b.tin


def test_tenant_without_tin_refuses_global_fallback():
    """ТТД тохируулаагүй түрээслэгчийн баримт глобалаар ОРЛОГДОХГҮЙ."""
    with pytest.raises(MerchantMissing) as e:
        merchant_for(_Site(_Tenant("Шинэ түрээслэгч")))
    assert "ТТД" in str(e.value)          # засах заавар мессежинд байна
    assert "Түрээслэгч" in str(e.value)


def test_site_without_tenant_uses_global():
    """Түрээслэгчгүй (EasyParking өөрөө ажиллуулдаг) зогсоол → глобал .env."""
    m = merchant_for(_Site(None))
    assert m.source == "global"
    assert m.tin == settings.ebarimt_merchant_tin


def test_district_falls_back_but_tin_never_does():
    """Дүүргийн код глобалаас нөхөгдөж болно — ТТД ХЭЗЭЭ Ч үгүй."""
    m = merchant_for(_Site(_Tenant("Дүүрэггүй", "71101242183")))
    assert m.tin == "71101242183"
    assert m.district_code == settings.ebarimt_district_code


def test_payload_carries_tenant_tin_everywhere():
    """PosAPI payload-ын БҮХ merchantTin талбар түрээслэгчийнх байх ёстой —
    нэг нь ч глобал үлдэж болохгүй."""
    m = merchant_for(_Site(MONNIS))
    p = _build_payload(11000, 1000, "CARD", None, m)
    assert p["merchantTin"] == "71101242183"
    assert p["districtCode"] == "2318"
    assert all(r["merchantTin"] == "71101242183" for r in p["receipts"])


def test_payload_without_merchant_is_global():
    """`merchant` өгөөгүй хуучин дуудлага хэвээр ажиллана (глобал)."""
    p = _build_payload(1000, 91, "CASH", None)
    assert p["merchantTin"] == settings.ebarimt_merchant_tin


def test_merchant_is_immutable():
    """Санамсаргүй дарж бичихээс хамгаална — баримтын ТТД гүйлгээ дунд солигдох ёсгүй."""
    m = Merchant("123", "2318", "1", "1", "tenant")
    with pytest.raises(Exception):
        m.tin = "999"
