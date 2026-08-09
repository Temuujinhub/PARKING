"""Хар жагсаалтын дүрэм (app_settings) — default, хадгалалт, кэш."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import app_settings as A  # noqa: E402


class FakeRow:
    def __init__(self, value=None):
        self.key = A.BLACKLIST_KEY
        self.value = value if value is not None else {}
        self.updated_by = ""


class FakeDB:
    """get/add-ыг л ашигладаг хамгийн бага загвар."""
    def __init__(self, row=None):
        self.row = row
        self.added = []

    def get(self, model, key):
        return self.row

    def add(self, obj):
        self.added.append(obj)
        self.row = obj


def setup_function():
    A.invalidate_cache()


def test_defaults_when_no_row():
    r = A.get_blacklist_rules(FakeDB())
    assert r["auto_enabled"] is True
    assert r["debt_count"] == 3
    # Орох хаалт default-аар ХОРИГЛОХГҮЙ — оруулаад операторт анхааруулна
    assert r["block_entry"] is False
    assert r["block_exit_debt_count"] == 3


def test_db_values_override_defaults():
    db = FakeDB(FakeRow({"debt_count": 5, "block_entry": True}))
    r = A.get_blacklist_rules(db)
    assert r["debt_count"] == 5 and r["block_entry"] is True
    assert r["auto_enabled"] is True   # хөндөөгүй талбар default хэвээр


def test_unknown_keys_ignored():
    db = FakeDB(FakeRow({"debt_count": 4, "hack": "rm -rf"}))
    r = A.get_blacklist_rules(db)
    assert "hack" not in r and r["debt_count"] == 4


def test_set_coerces_types_and_ignores_junk():
    db = FakeDB(FakeRow({}))
    out = A.set_blacklist_rules(
        db, {"debt_count": "7", "debt_amount": -50, "auto_enabled": 0,
             "block_entry": 1, "nonsense": 1, "block_exit_debt_count": "хоёр"},
        "admin")
    assert out["debt_count"] == 7          # текст тоо болов
    assert out["debt_amount"] == 0         # сөрөг → 0
    assert out["auto_enabled"] is False    # 0 → False
    assert out["block_entry"] is True
    assert "nonsense" not in out
    assert out["block_exit_debt_count"] == 3   # буруу утга хадгалагдахгүй, default
    assert db.row.updated_by == "admin"


def test_set_invalidates_cache():
    db = FakeDB(FakeRow({"debt_count": 3}))
    assert A.get_blacklist_rules(db)["debt_count"] == 3
    A.set_blacklist_rules(db, {"debt_count": 9}, "admin")
    assert A.get_blacklist_rules(db)["debt_count"] == 9


def test_read_failure_falls_back_to_defaults():
    class Broken:
        def get(self, *a):
            raise RuntimeError("DB унтарсан")
    r = A.get_blacklist_rules(Broken())
    assert r == A.BLACKLIST_DEFAULTS
