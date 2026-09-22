"""Real PostgreSQL credential expansion and legacy widening; no provider calls."""
import hashlib

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlalchemy.orm import Session

from test_payment_migrations import engine, URL
from app import migrations, models as M
from app.config import settings
from app.secretbox import encrypt_secret, decrypt_secret

pytestmark = pytest.mark.skipif(not URL, reason="Disposable PostgreSQL DSN required")
FIELDS = (("tenants", "qpay_password"), ("tenants", "msgbill_api_key"),
          ("tenants", "msgbill_webhook_secret"), ("parking_sites", "qpay_password"),
          ("devices", "password"))


def test_widening_preserves_legacy_values_and_long_encrypted_credentials(engine, monkeypatch):
    migrations.run_migrations()
    monkeypatch.setattr(settings, "secret_enc_key", Fernet.generate_key().decode())
    with Session(engine) as db:
        tenant = M.Tenant(name="Synthetic credential storage")
        site = M.ParkingSite(name="Synthetic credential storage", site_code="CRED_STORAGE")
        db.add_all([tenant, site]); db.flush()
        device = M.Device(site_id=site.id, name="Synthetic", device_type="camera")
        db.add(device); db.flush()
        rows = [tenant, tenant, tenant, site, device]
        legacy = [None, "legacy-plain", encrypt_secret("short"), "", "old-device-password"]
        for (_, field), row, value in zip(FIELDS, rows, legacy):
            setattr(row, field, value)
        db.commit()
        ids = [row.id for row in rows]
    # Reproduce the old physical varchar columns and its pre-widening ledger.
    with engine.begin() as c:
        for table, field in FIELDS:
            c.execute(text(f"ALTER TABLE {table} ALTER COLUMN {field} TYPE VARCHAR(160)"))
            statement = f"ALTER TABLE {table} ALTER COLUMN {field} TYPE TEXT"
            c.execute(text("DELETE FROM parking_schema_migrations WHERE version=:version"),
                      {"version": hashlib.sha256(statement.encode()).hexdigest()})
    with pytest.raises(RuntimeError, match="Encrypted credential storage"):
        migrations.check_ready()
    migrations.run_migrations(); migrations.run_migrations()
    assert migrations.check_ready()["database"] == "ok"
    plaintext = "synthetic-secret-" * 10 + "эмнэлэг"
    encrypted = encrypt_secret(plaintext)
    assert len(encrypted) > 160
    models = [M.Tenant, M.Tenant, M.Tenant, M.ParkingSite, M.Device]
    with Session(engine) as db:
        for model, row_id, (_, field), value in zip(models, ids, FIELDS, legacy):
            row = db.get(model, row_id)
            assert getattr(row, field) == value
            setattr(row, field, encrypted)
        db.commit()
    with Session(engine) as db:
        for model, row_id, (_, field) in zip(models, ids, FIELDS):
            saved = getattr(db.get(model, row_id), field)
            assert saved == encrypted and decrypt_secret(saved) == plaintext


def test_readiness_detects_accidentally_rebounded_credential_column(engine):
    migrations.run_migrations()
    with engine.begin() as c:
        c.execute(text("ALTER TABLE devices ALTER COLUMN password TYPE VARCHAR(160)"))
    with pytest.raises(RuntimeError, match="Encrypted credential storage"):
        migrations.check_ready()
