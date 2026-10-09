"""Disposable PostgreSQL only: migration, cursor order and shared writer locking."""
from datetime import datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from test_payment_migrations import engine, URL
from app import migrations, models as M
from app.routers.driver_directory_router import delete_drivers, DeleteRequest
from app.services.driver_directory import page
from app.services.driver_status import lock_driver_writes

pytestmark = pytest.mark.skipif(not URL, reason='Disposable PostgreSQL DSN required')


def test_location_upgrade_preserves_rows_and_paginates_tied_sort_keys(engine, monkeypatch):
    current=migrations.MIGRATIONS
    assert 'google_maps_url' in current[-5]
    monkeypatch.setattr(migrations,'MIGRATIONS',current[:-5])
    migrations.run_migrations()
    with Session(engine) as db:
        user=M.User(username='synthetic',password_hash='unused',role='SUPER_ADMIN')
        site=M.ParkingSite(name='Synthetic',site_code='MAP',capacity=20)
        db.add_all([user,site]);db.flush()
        db.add_all([M.RegisteredDriver(site_id=site.id,plate_number='1234УБА',company='',valid_to=datetime(2027,1,1)) for _ in range(207)])
        db.commit(); uid=user.id;sid=site.id
    with engine.begin() as conn:
        for column in ('google_maps_url','latitude','longitude'):
            conn.execute(text(f'ALTER TABLE parking_sites DROP COLUMN {column}'))
    monkeypatch.setattr(migrations,'MIGRATIONS',current)
    migrations.run_migrations();migrations.run_migrations()
    assert migrations.check_ready()['database']=='ok'
    with Session(engine) as db:
        assert db.get(M.ParkingSite,sid).capacity==20 and db.get(M.ParkingSite,sid).latitude is None
        user=db.get(M.User,uid); seen=[];cursor=None
        while True:
            result=page(db,user,{},cursor)
            assert result['total']==207
            seen.extend(r['id'] for r in result['items'])
            cursor=result['next_cursor']
            if not cursor: break
        assert len(seen)==len(set(seen))==207
        indexes=db.execute(text("SELECT indexname FROM pg_indexes WHERE schemaname=current_schema() AND tablename='registered_drivers'")).scalars().all()
        assert {'ix_driver_directory_cursor','ix_driver_directory_site'} <= set(indexes)


def test_bulk_delete_shares_writer_lock_and_preserves_payment_rows(engine):
    migrations.run_migrations()
    with Session(engine) as db:
        user=M.User(username='synthetic',password_hash='unused',role='SUPER_ADMIN')
        row=M.RegisteredDriver(plate_number='1234УБА',valid_to=datetime(2027,1,1))
        db.add_all([user,row]);db.commit(); uid=user.id;rid=row.id
    with Session(engine) as first, Session(engine) as second:
        user=first.get(M.User,uid)
        proof=delete_drivers(DeleteRequest(ids=[rid]),first,user)
        with pytest.raises(HTTPException) as error: lock_driver_writes(second)
        assert error.value.status_code==409
        second.rollback()
        delete_drivers(DeleteRequest(ids=[rid],dry_run=False,preview_token=proof['preview_token']),first,user)
        lock_driver_writes(second)
        assert second.get(M.RegisteredDriver,rid) is None
        assert second.query(M.Payment).count()==second.query(M.BarrierCommand).count()==0
        second.rollback()
