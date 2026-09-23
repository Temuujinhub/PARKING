"""Explicit recovery of one command whose executor predates a verified host boot.

This is an operator tool, not an age-based automatic retry. Call only after
verifying this is the sole application host for the selected physical gate.
It never sends a device command or records an unobserved physical outcome.
"""
from datetime import datetime

from app.models import AuditLog, BarrierCommand, Device


def recover_preboot_command(db, command_id, boot_at, save_evidence, *, now=None):
    """Hold device then command locks; caller commits status and audit atomically.

    save_evidence must persist the original row privately before any mutation.
    UNKNOWN retains the original stay's no-replay guard in barrier._execute,
    while allowing a different stay through the usual authorization checks.
    """
    now = now or datetime.utcnow()
    if boot_at.tzinfo is not None or boot_at > now:
        raise ValueError('Boot timestamp must be verified naive UTC, not in the future')
    device_id = db.query(BarrierCommand.device_id).filter(
        BarrierCommand.id == command_id).one()[0]
    device = (db.query(Device).enable_eagerloads(False).filter(Device.id == device_id)
              .with_for_update(nowait=True).one())
    command = (db.query(BarrierCommand).enable_eagerloads(False)
               .filter(BarrierCommand.id == command_id)
               .populate_existing().with_for_update(nowait=True).one())
    if command.status != 'PENDING':
        return {'changed': False, 'status': command.status}
    if device.status != 'active' or device.device_type != 'barrier':
        raise ValueError('Selected device is not an active barrier')
    if command.created_at >= boot_at:
        raise ValueError('Command does not predate host boot; preserve a possibly live executor')
    if command.executed_at is not None or command.response_text:
        raise ValueError('Command contains outcome evidence; inspect it before recovery')
    if command.command != 'open' or command.command_source != 'auto_entry' or not command.session_id:
        raise ValueError('Only an identified automatic entry stay is eligible')
    before = {column.name: getattr(command, column.name) for column in command.__table__.columns}
    save_evidence(before)
    command.status = 'UNKNOWN'
    command.response_text = 'Executor interrupted before host boot; physical result unknown. No command replayed.'
    db.add(AuditLog(username='system', action='BARRIER_PREBOOT_RECOVERY',
                    entity='barrier_command', entity_id=command.id,
                    detail={'previous_status':'PENDING','status':'UNKNOWN',
                            'boot_at':boot_at.isoformat(),'device_id':device.id,
                            'reason':'verified_single_host_reboot','replayed':False}))
    db.flush()
    return {'changed': True, 'previous_status':'PENDING','status':'UNKNOWN',
            'replayed':False,'original_stay_retry':'operator_review_required'}
