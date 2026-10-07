"""Disposable PostgreSQL verifies the shared registration writer lock."""
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from test_payment_migrations import engine, URL
from app import migrations, models as M, schemas
from app.routers.admin_router import bulk_driver_status, create_driver, update_driver
from app.services.driver_status import lock_driver_writes
from app.services.driver_import import import_rows

pytestmark = pytest.mark.skipif(not URL, reason='Disposable PostgreSQL DSN required')


def test_concurrent_bulk_single_and_import_writers_cannot_bypass_duplicate_check(engine):
    migrations.run_migrations()
    with Session(engine) as setup:
        user = M.User(username='synthetic',role='SUPER_ADMIN',password_hash='unused')
        rows = [M.RegisteredDriver(plate_number='1234УБА',is_active=False,
                valid_from=datetime.utcnow()-timedelta(days=1),
                valid_to=datetime.utcnow()+timedelta(days=10)) for _ in range(2)]
        setup.add_all([user,*rows]); setup.commit()
        uid, ids = user.id, [r.id for r in rows]
    with Session(engine,autoflush=False) as first, Session(engine,autoflush=False) as second:
        one, two = first.get(M.User,uid), second.get(M.User,uid)
        proof = bulk_driver_status(schemas.DriverBulkStatus(ids=[ids[0]],is_active=True),first,one)
        for action in (
            lambda: bulk_driver_status(schemas.DriverBulkStatus(ids=[ids[1]],is_active=True),second,two),
            lambda: update_driver(ids[1],schemas.DriverUpdate(is_active=True),second,two),
            lambda: create_driver(schemas.DriverCreate(plate_number='1234УБА',
                valid_to='2027-12-31'),second,two),
            lambda: import_rows(second,[{'plate':'1234УБА','full_name':'Synthetic','company':'Synthetic','note':''}],None),
        ):
            with pytest.raises(HTTPException) as error: action()
            assert error.value.status_code == 409
            second.rollback()
        bulk_driver_status(schemas.DriverBulkStatus(ids=[ids[0]],is_active=True,dry_run=False,
                           preview_token=proof['preview_token']),first,one)
        final = bulk_driver_status(schemas.DriverBulkStatus(ids=[ids[1]],is_active=True),second,two)
        assert final['change_count']==0
        assert final['items'][0]['reason']=='active_duplicate'
        second.rollback()
        lock_driver_writes(first); first.rollback()
        lock_driver_writes(second); second.rollback()
