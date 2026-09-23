"""Interrupted entry reservation recovery never invents ACKs or retries old cars."""
import asyncio
from datetime import datetime, timedelta
from unittest.mock import Mock

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import models as M
from app.config import settings
from app.database import Base
from app.services import barrier as B
from app.services.gate_recovery import recover_preboot_command


@pytest.fixture
def case(monkeypatch):
    def forbidden(*a,**kw): raise AssertionError('No camera/bank requests in recovery tests')
    monkeypatch.setattr(httpx.Client,'send',forbidden)
    monkeypatch.setattr(httpx.AsyncClient,'send',forbidden)
    monkeypatch.setattr(settings,'barrier_mock',True)
    engine=create_engine('sqlite://');Base.metadata.create_all(engine)
    with Session(engine) as db:
        site=M.ParkingSite(name='Synthetic',site_code='RECOVERY')
        db.add(site);db.flush()
        device=M.Device(site_id=site.id,name='Gate',device_key='recovery-test',
            device_type='barrier',status='active',ip_address='203.0.113.10')
        old=M.ParkingSession(site_id=site.id,plate_number='1111УБА',entry_time=datetime.utcnow(),status='OPEN')
        new=M.ParkingSession(site_id=site.id,plate_number='2222УБА',entry_time=datetime.utcnow(),status='OPEN')
        db.add_all([device,old,new]);db.flush()
        now=datetime.utcnow();boot=now-timedelta(minutes=45)
        cmd=M.BarrierCommand(device_id=device.id,session_id=old.id,command='open',
            command_source='auto_entry',status='PENDING',created_at=boot-timedelta(seconds=13))
        db.add(cmd);db.commit()
        yield db,device,old,new,cmd,boot,now
    engine.dispose();B._open_inflight.clear()


def test_stale_reservation_expires_new_stay_released_old_stay_cannot_repeat(case):
    db,device,old,new,cmd,boot,now=case
    # 2026-09-23 Андууд: гацсан PENDING нь ӨӨР машиныг мөнхөд хорьдог байв
    # (21 цагт 482 уншилт, 0 команд). Одоо хуучирсан PENDING автоматаар UNKNOWN
    # болж шинэ машин нээгдэнэ; хуучин машинд импульс ДАВТАХГҮЙ хэвээр.
    first=asyncio.run(B._execute(db,device,'open',new.id,'auto_entry'))
    assert first.id!=cmd.id and first.status=='SUCCESS'
    db.refresh(cmd)
    assert cmd.status=='UNKNOWN' and cmd.executed_at is None
    assert asyncio.run(B._execute(db,device,'open',old.id,'auto_entry')).id==cmd.id
    assert db.query(M.BarrierCommand).count()==2
    assert db.query(M.Payment).count()==db.query(M.Compensation).count()==0
    db.refresh(old);assert old.status=='OPEN' and old.total_fee is None
    assert db.query(M.AuditLog).filter_by(action='BARRIER_STALE_PENDING').count()==1
    # Гар сэргээлтийн хэрэгсэл аль хэдийн ангилагдсан мөрийг дахин өөрчлөхгүй
    original=[]
    assert recover_preboot_command(db,cmd.id,boot,original.append,now=now)=={'changed':False,'status':'UNKNOWN'}
    assert original==[]


@pytest.mark.parametrize('status',['UNKNOWN','REVIEWED','SUCCESS','FAILED'])
def test_resolved_or_classified_outcome_is_preserved(case,status):
    db,_,_,_,cmd,boot,now=case
    cmd.status=status;db.commit();save=Mock()
    assert recover_preboot_command(db,cmd.id,boot,save,now=now)=={'changed':False,'status':status}
    assert db.query(M.AuditLog).count()==0;save.assert_not_called()


@pytest.mark.parametrize('change',['postboot','executed','response','no_stay','manual','deleted','future_boot'])
def test_ambiguous_target_is_not_mutated(case,change):
    db,device,_,_,cmd,boot,now=case
    if change=='postboot':cmd.created_at=boot
    elif change=='executed':cmd.executed_at=boot
    elif change=='response':cmd.response_text='Existing outcome evidence'
    elif change=='no_stay':cmd.session_id=None
    elif change=='manual':cmd.command_source='manual'
    elif change=='deleted':device.status='deleted'
    elif change=='future_boot':boot=now+timedelta(seconds=1)
    db.commit();save=Mock()
    with pytest.raises(ValueError):recover_preboot_command(db,cmd.id,boot,save,now=now)
    assert cmd.status=='PENDING';assert db.query(M.AuditLog).count()==0;save.assert_not_called()


def test_backup_failure_keeps_original_reservation(case):
    db,_,_,_,cmd,boot,now=case
    with pytest.raises(OSError):
        recover_preboot_command(db,cmd.id,boot,Mock(side_effect=OSError('Disk full')),now=now)
    db.rollback();db.refresh(cmd)
    assert cmd.status=='PENDING' and db.query(M.AuditLog).count()==0


def test_retry_is_idempotent_and_rollback_is_atomic(case):
    db,_,_,_,cmd,boot,now=case
    recover_preboot_command(db,cmd.id,boot,lambda row:None,now=now)
    db.rollback();db.refresh(cmd)
    assert cmd.status=='PENDING' and db.query(M.AuditLog).count()==0
    recover_preboot_command(db,cmd.id,boot,lambda row:None,now=now);db.commit()
    assert not recover_preboot_command(db,cmd.id,boot,lambda row:None,now=now)['changed']
    assert db.query(M.AuditLog).count()==1
