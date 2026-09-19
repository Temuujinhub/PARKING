"""Run only against an explicitly supplied disposable PostgreSQL test database."""
import threading

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import AppSetting
from app.services import app_settings as A

from test_payment_migrations import engine, URL
pytestmark = pytest.mark.skipif(not URL, reason='Dedicated PostgreSQL test database required')


@pytest.mark.parametrize('existing', [False, True])
def test_two_admins_preserve_both_site_overrides(existing, engine):
    # The shared fixture owns a fresh audit_* schema and removes it afterwards.
    # Never delete settings in the test database's public schema.
    with engine.connect() as conn:
        assert conn.execute(text('SELECT current_schema()')).scalar().startswith('audit_')
    AppSetting.__table__.create(engine, checkfirst=True)
    with Session(engine) as db:
        assert db.get(AppSetting, A.EXITRULES_KEY) is None
        if existing:
            db.add(AppSetting(key=A.EXITRULES_KEY, value={}))
        db.commit()
    ready = threading.Barrier(2)
    errors = []
    def writer(site_id, amount):
        try:
            with Session(engine, autoflush=False) as db:
                # Keep an old identity-map instance alive: a lock alone without
                # refreshing it would still lose the other administrator's edit.
                original = db.get(AppSetting, A.EXITRULES_KEY)
                ready.wait(timeout=10)
                A.set_site_rules(db, A.EXITRULES_KEY, site_id, {'no_session_fee': amount}, 'test')
                db.commit()
        except Exception as exc:
            errors.append(exc)
    threads = [threading.Thread(target=writer, args=('site-a',1000), daemon=True),
               threading.Thread(target=writer, args=('site-b',2000), daemon=True)]
    for worker in threads:
        worker.start()
    for worker in threads:
        worker.join(timeout=15)
    assert not any(worker.is_alive() for worker in threads), 'Settings writers deadlocked'
    assert not errors, errors
    with Session(engine) as db:
        overlays = db.get(AppSetting, A.EXITRULES_KEY).value[A.SITE_OVERLAY]
        assert overlays['site-a']['no_session_fee'] == 1000
        assert overlays['site-b']['no_session_fee'] == 2000
    A.invalidate_cache()
