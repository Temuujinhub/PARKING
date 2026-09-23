"""Exercise the ciphertext size that broke bounded legacy credential storage."""
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import Text

from app import models as M
from app.config import settings
from app.secretbox import encrypt_secret, decrypt_secret


@pytest.mark.parametrize("plain", ["x" * 64, "synthetic-" * 120, "нууц-үг" * 30])
def test_long_ciphertext_roundtrips_without_a_bounded_storage_column(monkeypatch, plain):
    monkeypatch.setattr(settings, "secret_enc_key", Fernet.generate_key().decode())
    encrypted = encrypt_secret(plain)
    assert encrypted.startswith("enc:") and len(encrypted) > 160
    assert decrypt_secret(encrypted) == plain
    assert encrypt_secret(encrypted) == encrypted
    for model, field in ((M.Tenant, "qpay_password"), (M.Tenant, "msgbill_api_key"),
                         (M.Tenant, "msgbill_webhook_secret"), (M.ParkingSite, "qpay_password"),
                         (M.Device, "password")):
        assert isinstance(model.__table__.c[field].type, Text)
