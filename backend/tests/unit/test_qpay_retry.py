"""QPay дуудлагын НАЙДВАРТАЙ БАЙДАЛ — 401 сэргээлт, түр зуурын алдааны давталт.

2026-08-28: «QPay-тэй холбогдож чадсангүй — QR үүсгэж чадаагүй» гэж жолооч
төлж чадахгүй болох гомдол. Кодын нүх: дуудлага бүр «кэшлэсэн токеноор НЭГ
УДАА POST → алдаа бол шууд жолоочид» байсан. QPay нэг мерчант дансанд нэг л
токен амьд байлгадаг тул НЭГ дансаар ажиллаж буй өөр сервер шинэ токен авмагц
энэ серверийн кэш ҮХЭЖ, дараагийн БҮХ нэхэмжлэл 401 болно — кэшийн хугацаа
(QPay epoch-оор ~24ц) дуустал бүх зогсоол дээр QR үүсэхээ болино.

Эдгээр тест нь тэр хоёр гогцоог хаалттай байлгана.
"""
import asyncio
from datetime import datetime, timedelta

import httpx
import pytest

from app.services import qpay
from app.services.qpay import QpayAccount

def run(coro):
    """pytest-asyncio байхгүй тул корутиныг энгийнээр ажиллуулна."""
    return asyncio.run(coro)


ACC = QpayAccount(username="U", password="P", base_url="https://qp.test/v2",
                  invoice_code="I", branch_code="B", district_code="0000",
                  tax_type="1", classification_code="0000", mock=False)


class FakeResponse:
    def __init__(self, status: int, data: dict | None = None):
        self.status_code = status
        self._data = data or {}
        self.text = str(self._data)
        self.request = httpx.Request("POST", "https://qp.test/v2/x")

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(f"HTTP {self.status_code}",
                                        request=self.request, response=self)


TOKEN_OK = FakeResponse(200, {"access_token": "TOK", "refresh_token": "REF",
                              "expires_in": 3600})


@pytest.fixture
def calls(monkeypatch):
    """Дуудлагуудыг бичиж, өмнө нь тохируулсан хариунуудыг ээлжлэн буцаана."""
    seen: list[tuple[str, str]] = []
    queue: list[FakeResponse] = []

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def request(self, method, url, json=None, headers=None):
            seen.append((method, url.rsplit("/v2", 1)[-1]))
            assert queue, f"хүлээгээгүй дуудлага: {method} {url}"
            return queue.pop(0)

        async def post(self, url, json=None, headers=None):
            return await self.request("POST", url, json, headers)

    qpay._tokens.clear()
    qpay._stats.clear()
    monkeypatch.setattr(qpay, "_BACKOFF_SEC", (0.0, 0.0))  # тест хүлээхгүй
    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    yield seen, queue
    qpay._tokens.clear()
    qpay._stats.clear()


def paths(seen):
    return [p for _, p in seen]


# ────────────────────── 401: токен үхсэн ──────────────────────
def test_401_reauths_and_succeeds(calls):
    """Кэшлэсэн токен QPay талд үхсэн (401) → ШИНЭЭР нэвтэрч дахин илгээнэ.

    Энэ бол production дээрх гол гогцоо: өмнө нь 401 шууд жолоочид гардаг байв."""
    seen, queue = calls
    queue += [TOKEN_OK,                       # эхний нэвтрэлт
              FakeResponse(401, {"error": "UNAUTHORIZED"}),   # токен үхсэн
              TOKEN_OK,                       # ДАХИН нэвтрэлт (Basic)
              FakeResponse(200, {"invoice_id": "INV1", "qr_text": "Q"})]
    inv = run(qpay.create_invoice("S1", "тест", "terminal", "https://cb", [], acc=ACC))
    assert inv["invoice_id"] == "INV1"
    assert paths(seen) == ["/auth/token", "/invoice", "/auth/token", "/invoice"]


def test_401_uses_basic_not_refresh(calls):
    """Дахин нэвтрэхдээ refresh_token-ыг ХЭРЭГЛЭХГҮЙ — тэр нь мөн хүчингүй болсон
    байдаг тул refresh-ээр оролдвол дахин 401 авч гогцоо үргэлжилнэ."""
    seen, queue = calls
    queue += [TOKEN_OK, FakeResponse(401), TOKEN_OK,
              FakeResponse(200, {"invoice_id": "INV2", "qr_text": "Q"})]
    run(qpay.create_invoice("S2", "тест", "terminal", "https://cb", [], acc=ACC))
    assert "/auth/refresh" not in paths(seen)


def test_401_twice_gives_up(calls):
    """Хоёр дахь удаагаа 401 бол нэр/нууц үг үнэхээр буруу — эцэслэн алдаа өгнө
    (эцэс төгсгөлгүй давтахгүй)."""
    seen, queue = calls
    queue += [TOKEN_OK, FakeResponse(401), TOKEN_OK, FakeResponse(401),
              TOKEN_OK, FakeResponse(401)]
    with pytest.raises(httpx.HTTPStatusError):
        run(qpay.create_invoice("S3", "тест", "terminal", "https://cb", [], acc=ACC))
    assert paths(seen).count("/invoice") == qpay._MAX_ATTEMPTS


# ────────────────── Түр зуурын алдаа: 5xx / сүлжээ ──────────────────
def test_502_retried(calls):
    """QPay-ийн 502 нь түр зуурын — дахин илгээнэ, жолооч мэдэхгүй."""
    seen, queue = calls
    queue += [TOKEN_OK, FakeResponse(502), FakeResponse(200, {"invoice_id": "I", "qr_text": "Q"})]
    inv = run(qpay.create_invoice("S4", "тест", "terminal", "https://cb", [], acc=ACC))
    assert inv["invoice_id"] == "I"
    assert paths(seen).count("/invoice") == 2


def test_timeout_retried(monkeypatch):
    """Сүлжээний timeout — дахин илгээнэ (жолоочид алдаа гаргахгүй)."""
    seen = []
    state = {"invoice_calls": 0}

    class FlakyClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def request(self, method, url, json=None, headers=None):
            seen.append(url.rsplit("/v2", 1)[-1])
            if url.endswith("/auth/token"):
                return TOKEN_OK
            state["invoice_calls"] += 1
            if state["invoice_calls"] == 1:
                raise httpx.ReadTimeout("хугацаа хэтэрлээ")
            return FakeResponse(200, {"invoice_id": "I2", "qr_text": "Q"})

        async def post(self, url, json=None, headers=None):
            return await self.request("POST", url, json, headers)

    qpay._tokens.clear()
    monkeypatch.setattr(qpay, "_BACKOFF_SEC", (0.0, 0.0))
    monkeypatch.setattr(httpx, "AsyncClient", FlakyClient)
    inv = run(qpay.create_invoice("S5", "тест", "terminal", "https://cb", [], acc=ACC))
    assert inv["invoice_id"] == "I2"
    assert state["invoice_calls"] == 2


def test_400_not_retried(calls):
    """Бодит татгалзал (VAT_AMOUNT_INVALID гэх мэт) — давтах утгагүй, шууд гаргана."""
    seen, queue = calls
    queue += [TOKEN_OK, FakeResponse(400, {"error": "VAT_AMOUNT_INVALID"})]
    with pytest.raises(httpx.HTTPStatusError):
        run(qpay.create_invoice("S6", "тест", "terminal", "https://cb", [], acc=ACC))
    assert paths(seen).count("/invoice") == 1


# ────────────────────── Токены кэшийн хугацаа ──────────────────────
def test_token_cache_capped():
    """QPay «24 цаг» гэж хэлсэн ч 50 минутаас удаан кэшлэхгүй — олон сервер нэг
    данс хуваалцахад хуучин токен чимээгүй үхдэг тул өөрөө эдгэрэх давхарга."""
    now = datetime(2026, 8, 28, 12, 0, 0)
    epoch = (now + timedelta(hours=24) - datetime(1970, 1, 1)).total_seconds()
    exp = qpay._parse_expiry(epoch, now)
    assert exp <= now + qpay.TOKEN_MAX_LIFETIME
    assert exp > now + timedelta(minutes=40)


def test_short_expiry_still_respected():
    """QPay богино хугацаа өгвөл түүнийг нь дагана (хязгаар нь зөвхөн ДЭЭД тал)."""
    now = datetime(2026, 8, 28, 12, 0, 0)
    exp = qpay._parse_expiry(300, now)
    assert exp == now + timedelta(seconds=240)


# ────────────────────── 0₮ мөрийн хамгаалалт ──────────────────────
def test_zero_line_dropped():
    """Үнэгүй хугацаанд багтсан ч ӨМНӨХ ӨРТЭЙ машины «одоогийн төлбөр 0₮» мөрийг
    QPay татгалздаг — илгээхгүй. Нийт дүн өөрчлөгдөхгүй."""
    lines = qpay.build_lines([{"description": "Зогсоол", "unit_price": 0},
                              {"description": "Өмнөх өр", "unit_price": 3000}], ACC)
    assert len(lines) == 1
    assert lines[0]["line_unit_price"] == "3000.00"


def test_all_zero_raises():
    """Бүх мөр 0₮ бол нэхэмжлэл үүсгэх утгагүй — ойлгомжтой алдаа."""
    with pytest.raises(ValueError):
        qpay.build_lines([{"description": "a", "unit_price": 0}], ACC)


def test_negative_line_raises():
    """Сөрөг дүн бол логикийн алдаа — QPay руу илгээхээс өмнө барина."""
    with pytest.raises(ValueError):
        qpay.build_lines([{"description": "a", "unit_price": -100}], ACC)
