"""ANPR гүүр + нээх шалтгааны жагсаалт — DB-гүй логикийн тестүүд."""
import pytest

from app.config import settings
from app.services import cgi_poller
from app.services.app_settings import OPEN_REASON_DEFAULTS, set_open_reasons


# ── Гэрчийн дохио (witness) ──────────────────────────────────────────────
def test_force_reconnect_fires_once():
    """Дохио НЭГ л удаа ажиллана — эс бол стрим тасралтгүй дахин холбогдоно."""
    cgi_poller._force.clear()
    cgi_poller.force_reconnect("dev-1")
    assert cgi_poller.take_force("dev-1") is True
    assert cgi_poller.take_force("dev-1") is False


def test_force_reconnect_is_per_camera():
    """Нэг камерын дохио нөгөөг нь хөндөхгүй."""
    cgi_poller._force.clear()
    cgi_poller.force_reconnect("dev-1")
    assert cgi_poller.take_force("dev-2") is False
    assert cgi_poller.take_force("dev-1") is True


def test_empty_device_id_ignored():
    cgi_poller._force.clear()
    cgi_poller.force_reconnect("")
    assert not cgi_poller._force


# ── Гүүр анхдагчаар УНТРААЛТТАЙ ──────────────────────────────────────────
def test_bridge_off_by_default():
    """Гүүр анхдагчаар унтраалттай — гадны системд өөрөө холбогдохгүй."""
    assert settings.anpr_bridge_mode == "off"


def test_bridge_match_window_tolerates_camera_clock_drift():
    """Тэдний timestamp камерын цагаар ирдэг (Рашбулаг +32 мин гулссан
    түүхтэй) тул тулгах цонх өргөн байх ёстой."""
    assert settings.anpr_bridge_match_window_sec >= 120


# ── Нээх шалтгаан ────────────────────────────────────────────────────────
class _FakeDb:
    def __init__(self):
        self.saved = None

    def get(self, _m, _k):
        return None

    def add(self, obj):
        self.saved = obj


def test_default_reasons_have_stable_codes():
    """Кодууд нь тайлангийн түлхүүр — латин, зайгүй, давхардалгүй."""
    codes = [r["code"] for r in OPEN_REASON_DEFAULTS]
    assert len(codes) == len(set(codes))
    assert all(c and c.replace("_", "").isalnum() and c.islower() for c in codes)
    assert "other" in codes          # «Бусад» ямагт байна


def test_set_reasons_cleans_and_dedupes():
    db = _FakeDb()
    out = set_open_reasons(db, [
        {"code": " VIP ", "label": "  Зочин  "},          # цэвэрлэгдэнэ
        {"code": "vip", "label": "давхардсан"},            # хаягдана
        {"code": "bad code!", "label": "тэмдэгт цэвэрлэнэ"},
        {"code": "", "label": "кодгүй"},                   # хаягдана
        {"code": "x", "label": ""},                        # нэргүй — хаягдана
        {"code": "ok", "label": "Зөв", "is_active": False},
    ], "test")
    assert [r["code"] for r in out] == ["vip", "badcode", "ok"]
    assert out[0]["label"] == "Зочин"
    assert out[2]["is_active"] is False


def test_set_reasons_refuses_empty_list():
    """Бүгдийг устгавал оператор юу ч сонгож чадахгүй болно."""
    with pytest.raises(ValueError):
        set_open_reasons(_FakeDb(), [], "test")
    with pytest.raises(ValueError):
        set_open_reasons(_FakeDb(), [{"code": "", "label": ""}], "test")
