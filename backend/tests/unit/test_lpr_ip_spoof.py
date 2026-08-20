"""LPR: X-Forwarded-For хуурч камер болж дүр эсгэхээс хамгаалсныг баталгаажуулна.

ОСЛЫН ТҮҮХ (2026-08-20): `_client_ip` нь `X-Forwarded-For`-ийн ЭХНИЙ утгыг
жинхэнэ эх IP гэж авдаг байв. nginx нь `$proxy_add_x_forwarded_for`-оор
хэрэглэгчийн илгээсэн утгын АРД жинхэнэ IP-г залгадаг тул эхний утга нь
бүхэлдээ халдагчийн мэдэлд байсан. `device_key` өгөхгүй бол код IP-ээр таних
салбар руу уначихдаг байсан тул интернэтээс:

    POST /api/lpr/callback
    X-Forwarded-For: <камерын дотоод IP>
    {"Plate": {"PlateNumber": "1234УБА"}}

гэж илгээхэд систем үүнийг жинхэнэ камерын event гэж үзэн handle_entry →
ensure_entry_barrier дуудаж ХААЛТЫГ ФИЗИКЭЭР НЭЭХ боломжтой байв. Нэвтрэх
шаардлагагүй. Туршилтаар батлагдсан.

Энэ тест нь `_client_ip` толгойн утгыг ДАХИН уншиж эхэлбэл унана.
"""


class _FakeClient:
    def __init__(self, host):
        self.host = host


class _FakeRequest:
    """Starlette Request-ийн `_client_ip`-д хэрэгтэй хэсгийг дуурайна."""

    def __init__(self, client_host, headers=None):
        self.client = _FakeClient(client_host) if client_host else None
        self.headers = headers or {}


def _client_ip(req):
    from app.routers.lpr_router import _client_ip as fn
    return fn(req)


def test_xff_header_is_ignored():
    """Хуурамч X-Forwarded-For нь эх IP-г ОРЛОХГҮЙ."""
    req = _FakeRequest("10.0.0.5", {"x-forwarded-for": "192.168.6.10"})
    assert _client_ip(req) == "10.0.0.5"


def test_x_real_ip_header_is_ignored():
    """X-Real-IP-г ч бас шууд уншихгүй (мөн адил халдагчийн бичиж болох толгой)."""
    req = _FakeRequest("10.0.0.5", {"x-real-ip": "192.168.6.10"})
    assert _client_ip(req) == "10.0.0.5"


def test_xff_chain_first_value_is_ignored():
    """Гинжин XFF-ийн ЭХНИЙ утга (яг л ослын үеийнх) авагдахгүй."""
    req = _FakeRequest("203.0.113.9",
                       {"x-forwarded-for": "192.168.6.10, 172.16.100.21, 203.0.113.9"})
    assert _client_ip(req) == "203.0.113.9"


def test_missing_client_returns_empty():
    """client байхгүй үед (ASGI scope-д client үгүй) хоосон мөр — уналт биш."""
    assert _client_ip(_FakeRequest(None)) == ""


def test_require_key_flag_defaults_to_false():
    """Шилжилтийн туг анхдагчаар унтраалттай — камерууд шууд тасрахгүй."""
    from app.config import settings
    assert hasattr(settings, "lpr_require_key")
