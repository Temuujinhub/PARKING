"""Нууц үг солиход ХУУЧИН токен хүчингүй болох эсэх.

Яагаад чухал вэ (2026-07-28): seed.py-ийн нууц үг нээлттэй repo-д задарч,
production дээр ажиллаж байсан. Нууц үгээ сольсон ч JWT нь 12 цаг хүчинтэй тул
задарсан нууц үгээр нэвтэрсэн хэн нэгэн 12 цаг хандсаар байх боломжтой байв.
Одоо users.password_changed_at-аас ӨМНӨ олгогдсон токен шууд татгалзагдана.
"""
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException

from app import auth as A
from app.models import User


def _user(**kw):
    u = User(id="11111111-1111-1111-1111-111111111111", username="tester",
             password_hash="x", role="OPERATOR", is_active=True, site_id=None)
    for k, v in kw.items():
        setattr(u, k, v)
    return u


class _DB:
    def __init__(self, user): self._u = user
    def get(self, model, pk): return self._u


def _check(user):
    """get_current_user-ийн шалгалтыг DB-гүйгээр давтана."""
    token = A.create_access_token(user)
    return A.get_current_user(token=token, db=_DB(user))


def test_token_valid_when_password_never_changed():
    u = _user(password_changed_at=None)
    assert _check(u).username == "tester"


def test_token_valid_when_issued_after_change():
    # Нууц үг 1 цагийн өмнө солигдсон, токен ОДОО олгогдсон
    u = _user(password_changed_at=datetime.utcnow() - timedelta(hours=1))
    assert _check(u).username == "tester"


def test_token_rejected_when_issued_before_change():
    u = _user(password_changed_at=None)
    token = A.create_access_token(u)          # эхлээд токен олгоно
    u.password_changed_at = datetime.utcnow() + timedelta(seconds=5)  # дараа нь солино
    with pytest.raises(HTTPException) as e:
        A.get_current_user(token=token, db=_DB(u))
    assert e.value.status_code == 401
    assert "дахин нэвтэрнэ" in e.value.detail


def test_inactive_user_rejected():
    u = _user(is_active=False)
    with pytest.raises(HTTPException) as e:
        _check(u)
    assert e.value.status_code == 401


def test_token_carries_iat():
    import jwt as _jwt
    from app.config import settings as _s
    payload = _jwt.decode(A.create_access_token(_user()), _s.secret_key,
                          algorithms=[_s.jwt_algorithm])
    assert "iat" in payload and "exp" in payload
