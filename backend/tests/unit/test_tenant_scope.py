"""Tenant салгалт: operator_sites бүх рольд үйлчилдэг болсныг баталгаажуулна."""
import pytest
from fastapi import HTTPException

from app.auth import operator_sites
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
