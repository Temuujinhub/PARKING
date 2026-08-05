"""Tenant салгалт: operator_sites бүх рольд үйлчилдэг болсныг баталгаажуулна."""
import pytest
from fastapi import HTTPException

from app.auth import grant_site, operator_sites
from app.models import User
from app.routers.reports_router import _scope


def _user(role, site_ids=None, site_id=None):
    return User(id="u1", username="test", password_hash="x", role=role,
                is_active=True, site_ids=site_ids, site_id=site_id)


def test_super_admin_sees_all():
    assert operator_sites(_user("SUPER_ADMIN", site_ids=["s1"])) is None


def test_finance_with_sites_is_scoped():
    assert operator_sites(_user("FINANCE", site_ids=["monnis"])) == ["monnis"]


def test_admin_with_primary_site_is_scoped():
    assert operator_sites(_user("ADMIN", site_id="monnis")) == ["monnis"]


def test_company_level_user_sees_all():
    assert operator_sites(_user("FINANCE")) is None


def test_scope_blocks_foreign_site():
    with pytest.raises(HTTPException) as e:
        _scope(_user("FINANCE", site_ids=["monnis"]), "nic")
    assert e.value.status_code == 403


def test_scope_allows_own_site():
    assert _scope(_user("FINANCE", site_ids=["monnis"]), "monnis") == "monnis"


def test_scope_defaults_to_all_own_sites():
    assert _scope(_user("OPERATOR", site_ids=["a", "b"])) == ["a", "b"]
    assert _scope(_user("OPERATOR", site_ids=["a"])) == "a"
    assert _scope(_user("SUPER_ADMIN")) is None


# --- grant_site: зогсоол үүсгэгч шинэ зогсоолоо удирдаж чадах ёстой ---

def test_grant_site_extends_scoped_admin():
    """Хариуцах зогсоолтой админ шинэ зогсоол үүсгэвэл хүрээ нь өргөжнө —
    эс бол wizard-ын 2-р алхам (төхөөрөмж холбох) enforce_site 403 өгдөг байв."""
    u = _user("ADMIN", site_ids=["nic", "sport"])
    grant_site(u, "new")
    assert operator_sites(u) == ["nic", "sport", "new"]


def test_grant_site_from_primary_site():
    u = _user("ADMIN", site_id="nic")
    grant_site(u, "new")
    assert operator_sites(u) == ["nic", "new"]


def test_grant_site_noop_for_unscoped_and_super():
    u = _user("ADMIN")  # компанийн түвшний админ — бүх зогсоол хардаг
    grant_site(u, "new")
    assert u.site_ids is None
    s = _user("SUPER_ADMIN", site_ids=["x"])
    grant_site(s, "new")
    assert s.site_ids == ["x"]


def test_grant_site_idempotent():
    u = _user("ADMIN", site_ids=["nic"])
    grant_site(u, "nic")
    assert u.site_ids == ["nic"]
