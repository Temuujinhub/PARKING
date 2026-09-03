"""Зогсоол бүрийн дүрмийн давхарга (`_sites`) ба төлбөрийн дүрмийн бүртгэл.

2026-09-03: төлбөр/хаалтны дүрэм .env, app_settings, зогсоолын багана гэсэн
гурван газар тархсанаас «нэг зогсоолд тохирсон утга нөгөөг нь гацаадаг»
асуудал үүсдэг байв. Одоо бүлэг бүр НЭГ механизмаар (`get_rules(..., site_id)`)
зогсоолоор дарагдана — энэ тест тэр механизмын хилийг барина.
"""
import pytest

from app.services import app_settings as A
from app.services import payment_rules as PR

SITE_A, SITE_B = "site-aaa", "site-bbb"


class _FakeRow:
    def __init__(self):
        self.value, self.updated_by = {}, None


class _FakeDb:
    """get/set_rules-д хэрэгтэй хамгийн бага интерфейс (DB хэрэггүй)."""

    def __init__(self):
        self.rows = {}

    def get(self, _model, key):
        return self.rows.get(key)

    def add(self, obj):
        self.rows[obj.key] = obj


@pytest.fixture(autouse=True)
def _clean_cache():
    A.invalidate_cache()
    yield
    A.invalidate_cache()


def _db_with(key, values=None, site_values=None, site_id=SITE_A):
    """Глобал ба зогсоолын утгуудыг агуулсан хуурамч DB."""
    db = _FakeDb()
    row = _FakeRow()
    row.key = key
    row.value = dict(values or {})
    if site_values:
        row.value[A.SITE_OVERLAY] = {site_id: dict(site_values)}
    db.rows[key] = row
    return db


# ── Давхаргын үндсэн зан төлөв ─────────────────────────────────────────────
def test_site_value_overrides_global():
    db = _db_with(A.AUTOCLOSE_KEY, {"stale_hours": 12}, {"stale_hours": 36})
    assert A.get_rules(db, A.AUTOCLOSE_KEY)["stale_hours"] == 12
    assert A.get_rules(db, A.AUTOCLOSE_KEY, SITE_A)["stale_hours"] == 36
    # Давхаргагүй зогсоол глобалаар л ажиллана
    assert A.get_rules(db, A.AUTOCLOSE_KEY, SITE_B)["stale_hours"] == 12


def test_overlay_never_leaks_into_rule_consumers():
    """`_sites` нь дүрмийн dict-д ХЭЗЭЭ Ч түлхүүр болж орохгүй — эс бол
    хэрэглэгч код `rules["_sites"]`-ыг дүрэм гэж андуурна."""
    db = _db_with(A.AUTOCLOSE_KEY, {}, {"stale_hours": 5})
    for rules in (A.get_rules(db, A.AUTOCLOSE_KEY),
                  A.get_rules(db, A.AUTOCLOSE_KEY, SITE_A)):
        assert A.SITE_OVERLAY not in rules


def test_keys_not_marked_per_site_are_ignored():
    """PER_SITE-д зөвшөөрөөгүй түлхүүр давхаргаас үл хамааран глобал хэвээр."""
    db = _db_with(A.CAMSYNC_KEY, {"lookback_hours": 12}, {"lookback_hours": 99})
    assert A.get_rules(db, A.CAMSYNC_KEY, SITE_A)["lookback_hours"] == 12


def test_bad_value_falls_back_to_global():
    """Гараар/хуучин UI-аас орсон хог утга биллингийг унагаахгүй."""
    db = _db_with(A.EXITRULES_KEY, {"no_session_fee": 2000},
                  {"no_session_fee": "хоосон биш ч тоо биш"})
    assert A.get_rules(db, A.EXITRULES_KEY, SITE_A)["no_session_fee"] == 2000


def test_string_numbers_are_coerced():
    """DB-д мөр болж хадгалагдсан тоо int болж уншигдана (JSON-оос ирдэг)."""
    db = _db_with(A.EXITRULES_KEY, {}, {"no_session_fee": "3500"})
    assert A.get_rules(db, A.EXITRULES_KEY, SITE_A)["no_session_fee"] == 3500


def test_enum_value_outside_choices_ignored():
    db = _db_with(A.ENTRYPLATE_KEY, {"policy": "hold"}, {"policy": "уншихгүй"})
    assert A.get_rules(db, A.ENTRYPLATE_KEY, SITE_A)["policy"] == "hold"


# ── Бичилт ────────────────────────────────────────────────────────────────
def test_set_site_rules_roundtrip_and_reset():
    db = _FakeDb()
    A.set_site_rules(db, A.EXITRULES_KEY, SITE_A, {"no_session_fee": 5000}, "test")
    A.invalidate_cache()
    assert A.get_rules(db, A.EXITRULES_KEY, SITE_A)["no_session_fee"] == 5000
    # None = глобал руу буцаах
    A.set_site_rules(db, A.EXITRULES_KEY, SITE_A, {"no_session_fee": None}, "test")
    A.invalidate_cache()
    rules = A.get_rules(db, A.EXITRULES_KEY, SITE_A)
    assert rules["no_session_fee"] == A.DEFAULTS[A.EXITRULES_KEY]["no_session_fee"]
    # Түлхүүр устсаны дараа зогсоолын мөр өөрөө үлдэхгүй
    assert SITE_A not in A.get_site_overrides(db, A.EXITRULES_KEY)


def test_set_rules_keeps_site_overlay():
    """Глобал утгыг хадгалахад зогсоолын давхарга АРИЛАХГҮЙ."""
    db = _FakeDb()
    A.set_site_rules(db, A.AUTOCLOSE_KEY, SITE_A, {"stale_hours": 48}, "test")
    A.set_rules(db, A.AUTOCLOSE_KEY, {"stale_hours": 6}, "test")
    A.invalidate_cache()
    assert A.get_rules(db, A.AUTOCLOSE_KEY)["stale_hours"] == 6
    assert A.get_rules(db, A.AUTOCLOSE_KEY, SITE_A)["stale_hours"] == 48


def test_set_site_rules_rejects_non_per_site_key():
    db = _FakeDb()
    A.set_site_rules(db, A.CAMSYNC_KEY, SITE_A, {"lookback_hours": 99}, "test")
    assert A.get_site_overrides(db, A.CAMSYNC_KEY).get(SITE_A) in (None, {})


# ── .env-ийн амьд уналт ────────────────────────────────────────────────────
def test_env_fallback_is_live_not_frozen_at_import():
    """`.env`-ийн утга import-д ЦАРЦААГҮЙ байх ёстой — эс бол ажиллаж буй
    системд .env өөрчлөгдөхөд дүрэм хуучин утгаараа үлдэнэ."""
    from app.config import settings
    old = settings.entry_burst_seconds
    try:
        settings.entry_burst_seconds = 11
        assert A._base(A.BARRIER_KEY)["entry_burst_seconds"] == 11
    finally:
        settings.entry_burst_seconds = old


def test_db_value_beats_env_fallback():
    db = _db_with(A.BARRIER_KEY, {"entry_burst_seconds": 3})
    assert A.get_rules(db, A.BARRIER_KEY)["entry_burst_seconds"] == 3


# ── Бүртгэл (catalog) бүрэн эсэх ───────────────────────────────────────────
def test_catalog_covers_every_per_site_key():
    """PER_SITE-д зарласан түлхүүр бүр UI-д ТАЙЛБАРТАЙ гарах ёстой — эс бол
    админ юу тохируулж байгаагаа мэдэхгүй хэвээр үлдэнэ."""
    documented = {(r["group"], r["key"]) for r in PR.CATALOG}
    for group, keys in A.PER_SITE.items():
        for key in keys:
            assert (group, key) in documented, f"{group}.{key} бүртгэлд алга"


def test_catalog_keys_exist_in_defaults():
    for row in PR.CATALOG:
        assert row["key"] in A.DEFAULTS[row["group"]], row


def test_catalog_rows_are_described():
    for row in PR.CATALOG:
        assert row["name"] and row["desc"] and row["applies"], row
