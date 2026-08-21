"""Өр үүсгэдэг БҮХ зам тохиргоогоор удирдагддаг эсэх.

2026-08-21: «өрөө нэг цэвэрлэсэн ч долоо хоногийн дараа дахин ургадаг» гэдгийн
шалтгаан нь өр үүсгэдэг замуудын зарим нь КОДОД ХАТУУ бичигдсэн байсанд:
ээлж хаах, шөнийн хаалт, гарцад уншигдаад төлөөгүй, дахин орж ирэх. Одоо
бүгдийг нь Тохиргоо → Авто цэвэрлэгээ хуудаснаас унтраана.
"""
import pytest

from app.services.app_settings import AUTOCLOSE_KEY, DEFAULTS, set_rules

# Өр үүсгэдэг бүх зам → тохиргооны түлхүүр
DEBT_PATHS = {
    "авто хаалт (гарах уншилтгүй)": "create_debt",
    "авто хаалт (гарцад уншигдсан ч төлөөгүй)": "create_debt_unpaid_exit",
    "төлөлгүй машин дахин орж ирэх": "create_debt_reentry",
    "ээлж хаах (бүх машиныг гаргах)": "create_debt_shift_close",
    "шөнийн бөөнөөр хаалт": "create_debt_night_close",
}


@pytest.mark.parametrize("path,key", DEBT_PATHS.items())
def test_every_debt_path_has_a_switch(path, key):
    """Өр үүсгэдэг зам бүр тохиргооны түлхүүртэй байх ёстой."""
    assert key in DEFAULTS[AUTOCLOSE_KEY], f"«{path}» тохиргоогүй байна"
    assert isinstance(DEFAULTS[AUTOCLOSE_KEY][key], bool)


def test_evidence_free_debt_is_off_by_default():
    """Гарах уншилтгүй (нотолгоогүй) өр анхдагчаар УНТРААЛТТАЙ хэвээр."""
    assert DEFAULTS[AUTOCLOSE_KEY]["create_debt"] is False


class _FakeRow:
    def __init__(self):
        self.value, self.updated_by = {}, None


class _FakeDb:
    """set_rules-д хэрэгтэй хамгийн бага интерфейс (DB хэрэггүй)."""

    def __init__(self):
        self.row = _FakeRow()

    def get(self, _model, _key):
        return self.row

    def add(self, _obj):
        pass


@pytest.mark.parametrize("key", list(DEBT_PATHS.values()))
def test_switch_can_be_turned_off_and_on(key):
    """Түлхүүр бүрийг хадгалж болно — unknown key гэж хаягдахгүй."""
    db = _FakeDb()
    saved = set_rules(db, AUTOCLOSE_KEY, {key: False}, "test")
    assert saved[key] is False
    saved = set_rules(db, AUTOCLOSE_KEY, {key: True}, "test")
    assert saved[key] is True


def test_unknown_keys_are_ignored():
    """Мэдэгдэхгүй түлхүүр хадгалагдахгүй (хуучин UI-аас ирсэн хог)."""
    db = _FakeDb()
    saved = set_rules(db, AUTOCLOSE_KEY, {"create_debt_xyz": True}, "test")
    assert "create_debt_xyz" not in saved
