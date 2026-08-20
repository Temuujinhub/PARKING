"""Админ ӨӨРИЙНХӨӨ эрх/хамрах хүрээг ахиулах замыг хаасныг баталгаажуулна.

ОСЛЫН ТҮҮХ (2026-08-20): `update_user` нь `u.id != user.id` үед л
`_enforce_user_scope` дууддаг байсан — өөрийгөө засахад ямар ч шалгалт
байгаагүй. Нэг зогсоолоор хязгаарлагдсан ADMIN дараахыг илгээхэд:

    PUT /api/admin/users/<өөрийн id>   {"site_ids": [], "site_id": null}

`_clean_site_ids([])` нь None буцаадаг → `operator_sites()` «бүх зогсоол» гэж
үзнэ → тэр админ БҮХ түрээслэгчийн дата руу хандах эрхтэй болдог байв.

ХОЁР ТАЛТ ШААРДЛАГА — тестийн гол утга нь энэ:
  1. Эрх ахиулах оролдлогыг ХААХ.
  2. Хэвийн профайл хадгалалтыг ЭВДЭХГҮЙ. Users.jsx нь нэр/утас/нууц үг
     хадгалахад ч бүтэн объектыг (role, site_ids, permissions, is_active,
     tenant_id хамт) буцааж илгээдэг тул «талбар байгаа эсэх»-ээр шалгавал
     админ өөрийн нэрээ ч засаж чадахгүй болно.
"""
import pytest
from fastapi import HTTPException

from app.models import User
from app.routers.admin_router import _guard_self_privileges


def _admin(**kw):
    d = dict(id="u1", username="admin1", password_hash="x", role="ADMIN",
             is_active=True, site_ids=["siteA"], site_id="siteA",
             permissions=None, tenant_id="t1")
    d.update(kw)
    return User(**d)


# Users.jsx нь ямагт бүтэн объект илгээдэг — энэ нь «өөрчлөлтгүй» суурь.
UNCHANGED = {"role": "ADMIN", "site_ids": ["siteA"], "site_id": "siteA",
             "permissions": None, "is_active": True, "tenant_id": "t1"}


# ── Эвдэрч болохгүй: хэвийн урсгал ──────────────────────────────────────

def test_profile_name_and_phone_allowed():
    _guard_self_privileges(_admin(), {**UNCHANGED, "full_name": "Шинэ нэр",
                                      "phone": "99119911"})


def test_password_change_allowed():
    _guard_self_privileges(_admin(), {**UNCHANGED, "password": "ШинэНууц2026!"})


def test_partial_body_allowed():
    _guard_self_privileges(_admin(), {"full_name": "Х"})


# ── Заавал хаах: эрх ахиулалт ───────────────────────────────────────────

def test_empty_site_ids_blocked():
    """Яг ослын вектор: хоосон жагсаалт = «бүх зогсоол»."""
    with pytest.raises(HTTPException) as e:
        _guard_self_privileges(_admin(), {"site_ids": [], "site_id": None})
    assert e.value.status_code == 403


def test_adding_other_site_blocked():
    with pytest.raises(HTTPException):
        _guard_self_privileges(_admin(), {"site_ids": ["siteA", "siteB"],
                                          "site_id": "siteA"})


def test_role_change_blocked():
    with pytest.raises(HTTPException):
        _guard_self_privileges(_admin(), {"role": "FINANCE"})


def test_granting_self_free_exit_blocked():
    with pytest.raises(HTTPException):
        _guard_self_privileges(_admin(), {"permissions": ["dashboard", "free_exit"],
                                          "role": "ADMIN"})


def test_tenant_switch_blocked():
    with pytest.raises(HTTPException):
        _guard_self_privileges(_admin(), {"tenant_id": "t2"})


def test_is_active_change_blocked():
    with pytest.raises(HTTPException):
        _guard_self_privileges(_admin(), {"is_active": False})
