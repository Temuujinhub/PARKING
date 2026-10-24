"""2026-10-12 regressions of the 10-11 exit-evidence release (synthetic, no network).

1. POS card payment without an exit read: open the lane the cashier stands at
   (request lane, or the site's only outer exit lane); several lanes -> wait.
2. Payment 3-15 min after the exit read: open unless another plate was read
   on that camera since.
3. 1-2 character exit misread: auto-match only a measured OCR confusion with a
   single nearby active stay; a genuine difference or ambiguity stays in review.
"""
import asyncio
from datetime import datetime, timedelta

import pytest
from starlette.requests import Request

from app import models as M, session_logic as SL
from app.routers import payments_router as PR
from app.services import app_settings as A

from test_exit_presence import ctx, stay, read, FEE  # noqa: F401  (fixture reuse)


def confirm(c, s, monkeypatch, **extra):
    monkeypatch.setattr(PR, '_print_payload', lambda *a: {})
    body = {'session_id': s.id, 'amount': 1000, 'terminal_id': 'TDB-PAX-SITE01-01',
            'transaction_id': f'bank-{s.id}', **extra}
    return asyncio.run(PR.pos_confirm(body, Request({'type': 'http', 'headers': []}), c.db, c.user))


def audits(c, action):
    return c.db.query(M.AuditLog).filter_by(action=action).all()


def single_lane(c):
    c.cams[1].status = 'inactive'; c.bars[1].status = 'inactive'; c.db.commit()


# ── Rule 1: POS lane ───────────────────────────────────────────────────────

def test_pos_without_read_on_multi_lane_site_and_no_lane_waits(ctx, monkeypatch):
    c = ctx; s = stay(c)
    out = confirm(c, s, monkeypatch)
    assert out['status'] == 'PAID' and not c.commands and s.status == 'PAID'
    assert out['exit_lane_context']['status'] == 'waiting_for_exit_evidence'
    assert not audits(c, 'POS_LANE_OPEN') and len(audits(c, 'PAYMENT_EXIT_WAIT')) == 1


@pytest.mark.parametrize('lane', [{'lane_no': 2}, {'device_id': 'bar2'}, {'exit_device_id': 'cam2'}])
def test_pos_request_lane_opens_that_lane_and_audits(ctx, monkeypatch, lane):
    c = ctx; s = stay(c)
    lane = {k: {'bar2': c.bars[1].id, 'cam2': c.cams[1].id}.get(v, v) for k, v in lane.items()}
    out = confirm(c, s, monkeypatch, **lane)
    assert c.commands == [(c.bars[1].id, s.id, 'payment')]
    assert s.status == 'CLOSED' and s.exit_confirmed is False and s.exit_device_id == c.cams[1].id
    (a,) = audits(c, 'POS_LANE_OPEN')
    assert a.username == c.user.username and a.entity_id == s.id
    assert a.detail['terminal_id'] == 'TDB-PAX-SITE01-01' and a.detail['cashier'] == c.user.username
    assert a.detail['lane_no'] == 2 and a.detail['session_id'] == s.id
    assert a.detail['lane_source'] == 'pos_request' and a.detail['presence_reason'] == 'waiting_for_exit_read'
    assert out['barrier_opened'] and out['exit_lane_context']['pos_lane_no'] == 2
    assert not audits(c, 'PAYMENT_EXIT_WAIT')


def test_pos_on_single_exit_lane_site_opens_without_lane_in_request(ctx, monkeypatch):
    c = ctx; single_lane(c); s = stay(c)
    confirm(c, s, monkeypatch)
    assert c.commands == [(c.bars[0].id, s.id, 'payment')] and s.status == 'CLOSED'
    assert audits(c, 'POS_LANE_OPEN')[0].detail['lane_source'] == 'single_exit_lane'


def test_inner_lane_does_not_count_as_second_exit_lane(ctx, monkeypatch):
    c = ctx; c.cams[1].nested_inner = True; c.bars[1].nested_inner = True; c.db.commit(); s = stay(c)
    confirm(c, s, monkeypatch)
    assert c.commands == [(c.bars[0].id, s.id, 'payment')]


@pytest.mark.parametrize('bad', [{'device_id': 'nonexistent'}, {'lane_no': 9}, {'lane_no': '2'},
                                 {'device_id': 'bar1', 'exit_device_id': 'cam2'}])
def test_bad_pos_lane_after_bank_charge_keeps_payment_and_does_not_open(ctx, monkeypatch, bad):
    c = ctx; s = stay(c)
    bad = {k: {'bar1': c.bars[0].id, 'cam2': c.cams[1].id}.get(v, v) for k, v in bad.items()}
    out = confirm(c, s, monkeypatch, **bad)
    assert out['status'] == 'PAID' and c.db.query(M.Payment).one().status == 'PAID'
    assert not c.commands and s.status == 'PAID'


def test_pos_lane_respects_debt_rule(ctx, monkeypatch):
    c = ctx; s = stay(c)
    A.set_site_rules(c.db, A.BLACKLIST_KEY, c.site.id, {'block_exit_debt_count': 1}, 'test')
    c.db.add(M.Compensation(site_id=c.site.id, plate_number=s.plate_number, amount=1000, status='PENDING', reason='test'))
    c.db.commit()
    confirm(c, s, monkeypatch, lane_no=1)
    assert not c.commands and s.status == 'PAID'
    assert audits(c, 'PAYMENT_EXIT_WAIT')[0].detail['reason'] == 'debt_rule'


def test_fresh_camera_read_wins_over_pos_lane(ctx, monkeypatch):
    c = ctx; s = stay(c); read(c, s, c.cams[1], age=30)
    confirm(c, s, monkeypatch, lane_no=1)
    assert c.commands == [(c.bars[1].id, s.id, 'payment')] and s.exit_confirmed is True
    assert not audits(c, 'POS_LANE_OPEN')


def test_qr_payment_never_uses_pos_lane(ctx):
    c = ctx; single_lane(c); s = stay(c)
    asyncio.run(SL.mark_paid_and_open(c.db, s, fee_snapshot=dict(FEE)))
    assert not c.commands and s.status == 'PAID'


def test_prepare_accepts_explicit_lane_without_read(ctx):
    c = ctx; s = stay(c)
    out = asyncio.run(PR.pos_prepare({'session_id': s.id, 'terminal_id': c.terminal.device_key,
                                      'expected_amount': 1000, 'lane_no': 2}, c.db, c.user))
    assert out['exit_context'] == 'pos_lane_selected' and out['exit_lane_context']['gate_id'] == c.bars[1].id
    assert not c.commands


# ── Rule 2: late payment after the exit read ───────────────────────────────

def pay(c, s):
    asyncio.run(SL.mark_paid_and_open(c.db, s, fee_snapshot=dict(FEE)))


@pytest.mark.parametrize('minutes', [4, 10, 14])
def test_payment_minutes_after_exit_read_opens_when_lane_quiet(ctx, minutes):
    c = ctx; s = stay(c, status='AWAITING_PAYMENT'); read(c, s, c.cams[0], age=minutes * 60)
    pay(c, s)
    assert c.commands == [(c.bars[0].id, s.id, 'payment')] and s.status == 'CLOSED'


def test_payment_after_16_minutes_is_stale(ctx):
    c = ctx; s = stay(c, status='AWAITING_PAYMENT'); read(c, s, c.cams[0], age=16 * 60)
    pay(c, s)
    assert not c.commands and audits(c, 'PAYMENT_EXIT_WAIT')[0].detail['reason'] == 'exit_read_stale'


@pytest.mark.parametrize('other', [dict(plate='9999УБА', accepted=True),
                                   dict(plate='9999УБА', accepted=False),
                                   dict(plate='?', accepted=False)])
def test_other_plate_read_on_that_camera_since_blocks(ctx, other):
    c = ctx; s = stay(c, status='AWAITING_PAYMENT'); read(c, s, c.cams[0], age=600)
    c.db.add(M.LprEvent(site_id=c.site.id, device_id=c.cams[0].id, lane_dir='exit', confidence=90,
                        created_at=datetime.utcnow() - timedelta(seconds=300), raw={},
                        plate_number=other['plate'], accepted=other['accepted']))
    c.db.commit()
    pay(c, s)
    assert not c.commands and s.status == 'PAID'
    assert audits(c, 'PAYMENT_EXIT_WAIT')[0].detail['reason'] == 'exit_presence_unconfirmed'


def test_same_plate_rejected_reread_and_other_lane_reads_do_not_block(ctx):
    c = ctx; s = stay(c, status='AWAITING_PAYMENT'); read(c, s, c.cams[0], age=600)
    c.db.add_all([
        M.LprEvent(site_id=c.site.id, device_id=c.cams[0].id, lane_dir='exit', confidence=14,
                   created_at=datetime.utcnow() - timedelta(seconds=300), raw={},
                   plate_number=s.plate_number, accepted=False, reject_reason='confidence<30.0'),
        M.LprEvent(site_id=c.site.id, device_id=c.cams[1].id, lane_dir='exit', confidence=90,
                   created_at=datetime.utcnow() - timedelta(seconds=200), raw={},
                   plate_number='9999УБА', accepted=True)])
    c.db.commit()
    pay(c, s)
    assert c.commands == [(c.bars[0].id, s.id, 'payment')]


def test_exit_read_window_is_configurable_per_site(ctx):
    c = ctx; s = stay(c, status='AWAITING_PAYMENT'); read(c, s, c.cams[0], age=600)
    A.set_site_rules(c.db, A.EXITRULES_KEY, c.site.id, {'exit_read_valid_minutes': 5}, 'test'); c.db.commit()
    pay(c, s)
    assert not c.commands


# ── Rule 3: OCR confusion auto-match ──────────────────────────────────────

@pytest.mark.parametrize('a,b,ok', [
    ('1041УНС', '1041УКС', True),     # К↔Н — 1,674 in 30 days, 1.8% wrong
    ('7524УБТ', '1524УБТ', True),     # 1↔7
    ('1234УБ0', '1234УБО', True),     # digit 0 vs letter О (invalid format read)
    ('8234УВА', 'В234УБА', True),     # 8↔В and В↔Б (canon) — two positions
    ('1234УБА', '6234УБА', False),    # 1↔6 not a measured confusion
    ('2234УБА', '3234УБА', False),    # 2↔3: 36% wrong in the baseline
    ('1041УНН', '1041УКК', True),
    ('1041ННН', '1041ККК', False),    # three positions
    ('524УБТ', '7524УБТ', False),     # truncation stays in review
    ('1234УБА', '1234УБА', False),    # identical is not a confusion
])
def test_plates_ocr_confusable(a, b, ok):
    assert SL.plates_ocr_confusable(a, b) is ok


def exit_read(c, plate, cam=None):
    return asyncio.run(SL.handle_exit(c.db, cam or c.cams[0], plate, 95, {}))


def test_ocr_confusion_matches_single_stay_and_audits(ctx):
    c = ctx; s = stay(c, plate='1041УКС')
    r = exit_read(c, '1041УНС')
    assert r['action'] == 'awaiting_payment' and r['session_id'] == s.id
    assert s.status == 'AWAITING_PAYMENT' and s.exit_device_id == c.cams[0].id
    (a,) = audits(c, 'EXIT_OCR_MATCH')
    assert a.detail['read_plate'] == '1041УНС' and a.detail['entry_plate'] == '1041УКС'
    ev = c.db.query(M.LprEvent).one()
    assert ev.accepted and ev.plate_number == '1041УНС'   # camera text is preserved


def test_ocr_matched_stay_then_payment_opens(ctx):
    c = ctx; s = stay(c, plate='1041УКС')
    exit_read(c, '1041УНС')
    pay(c, s)
    assert c.commands == [(c.bars[0].id, s.id, 'payment')] and s.status == 'CLOSED'


def test_ocr_confusion_on_paid_stay_opens(ctx):
    c = ctx; s = stay(c, plate='1041УКС', status='PAID')
    s.paid_at = datetime.utcnow(); s.exit_deadline = s.paid_at + timedelta(minutes=15); c.db.commit()
    r = exit_read(c, '1041УНС')
    assert r['barrier_opened'] and s.status == 'CLOSED'


def test_invalid_format_digit_letter_confusion_matches(ctx):
    c = ctx; s = stay(c, plate='1234УБО')
    r = exit_read(c, '1234УБ0')
    assert r.get('session_id') == s.id and audits(c, 'EXIT_OCR_MATCH')


def test_genuine_difference_stays_in_review(ctx):
    c = ctx; s = stay(c, plate='1234УБА')
    r = exit_read(c, '6234УБА')
    assert r['action'] == 'review_required' and r['reason'] == 'plate_mismatch_review'
    assert s.status == 'OPEN' and not audits(c, 'EXIT_OCR_MATCH')


def test_truncation_attaches_unpaid_stay_without_opening(ctx):
    # 2026-10-24: тайрагдсан уншилт ганц ТӨЛБӨРТЭЙ сешнд тохоно (QR төлбөр тэр
    # эгнээг нээнэ), гэхдээ өөрөө хаалт нээхгүй.
    c = ctx; s = stay(c, plate='1234УБА')
    r = exit_read(c, '234УБА')
    assert r['action'] == 'awaiting_payment' and s.status == 'AWAITING_PAYMENT' and not c.commands
    assert audits(c, 'EXIT_OCR_MATCH')[0].detail['rule'] == 'truncated_read'


def test_truncation_never_opens_free_stay(ctx, monkeypatch):
    c = ctx; s = stay(c, plate='1234УБА')
    free = dict(FEE, is_free=True, total_fee=0)
    monkeypatch.setattr(SL, 'session_fee_info', lambda *a, **kw: dict(free))
    r = exit_read(c, '234УБА')
    assert r['action'] == 'review_required' and r['reason'] == 'invalid_plate_review'
    assert s.status == 'OPEN' and not c.commands and not audits(c, 'EXIT_OCR_MATCH')


def test_two_nearby_stays_are_ambiguous(ctx):
    c = ctx; stay(c, plate='1041УКС'); stay(c, plate='1041УЛС')
    r = exit_read(c, '1041УНС')
    assert r['action'] == 'review_required' and not audits(c, 'EXIT_OCR_MATCH')


def registered(c, plate):
    c.db.add(M.RegisteredDriver(plate_number=plate, site_id=c.site.id,
                                valid_from=datetime.utcnow() - timedelta(days=1),
                                valid_to=datetime.utcnow() + timedelta(days=1), is_active=True))
    c.db.commit()


def test_registered_read_claims_its_misread_entry_stay(ctx):
    # Audit #7: entry camera misread a registered car (1041УНС → 1041УКС); the
    # exit read of the registered plate corrects the stay instead of billing it.
    c = ctx; s = stay(c, plate='1041УКС'); registered(c, '1041УНС')
    exit_read(c, '1041УНС')
    assert s.plate_number == '1041УНС'
    (a,) = audits(c, 'EXIT_PLATE_CORRECT')
    assert a.detail['corrected'] is True and a.detail['entry_plate'] == '1041УКС'


def test_registered_correction_makes_exit_free(ctx, monkeypatch):
    c = ctx; s = stay(c, plate='1041УКС'); registered(c, '1041УНС')
    free = dict(FEE, is_free=True, total_fee=0, base_fee=0, vat_amount=0)
    monkeypatch.setattr(SL, 'session_fee_info',
                        lambda db, ss, **kw: free if SL.find_registered(db, ss.plate_number, ss.site_id) else dict(FEE))
    r = exit_read(c, '1041УНС')
    assert r['barrier_opened'] and s.status == 'FREE' and s.plate_number == '1041УНС'


def test_two_registered_plates_are_not_guessed(ctx):
    c = ctx; s = stay(c, plate='1041УКС'); registered(c, '1041УНС'); registered(c, '1041УКС')
    exit_read(c, '1041УНС')
    assert s.plate_number == '1041УКС' and s.status == 'OPEN'
    assert not audits(c, 'EXIT_PLATE_CORRECT') and not audits(c, 'EXIT_OCR_MATCH')


def test_registered_read_with_unrelated_near_stay_exits_on_its_own_right(ctx):
    c = ctx; other = stay(c, plate='6234УБА'); registered(c, '1234УБА')
    r = exit_read(c, '1234УБА')
    assert r['action'] == 'registered_exit' and r['barrier_opened']
    assert other.status == 'OPEN' and other.plate_number == '6234УБА'


# ── Audit #3: low-confidence exit read that exactly matches an active stay ──

def cgi(c, monkeypatch, plate, confidence=14, extra=None):
    from app.services import cgi_poller as CP
    monkeypatch.setattr(CP, 'SessionLocal', lambda: c.db)
    monkeypatch.setattr(c.db, 'close', lambda: None)
    monkeypatch.setattr(CP, '_dump_event', lambda *a: None)
    raw = {'Code': 'TrafficJunction', 'TrafficCar': {'PlateNumber': plate, 'Confidence': confidence}}
    raw.update(extra or {})
    asyncio.run(CP._process_event(c.cams[0].id, raw))


def test_low_confidence_exact_active_stay_is_accepted(ctx, monkeypatch):
    c = ctx; s = stay(c)
    cgi(c, monkeypatch, s.plate_number, 14)
    e = c.db.query(M.LprEvent).one()
    assert e.accepted and e.confidence == 14
    assert s.status == 'AWAITING_PAYMENT' and s.exit_device_id == c.cams[0].id
    (a,) = audits(c, 'EXIT_LOWCONF_MATCH')
    assert a.entity_id == s.id and a.detail['reason'].startswith('confidence<')


def test_low_confidence_exact_paid_stay_opens(ctx, monkeypatch):
    c = ctx; s = stay(c, status='PAID')
    s.paid_at = datetime.utcnow(); s.exit_deadline = s.paid_at + timedelta(minutes=15); c.db.commit()
    cgi(c, monkeypatch, s.plate_number, 9)
    assert s.status == 'CLOSED' and c.commands == [(c.bars[0].id, s.id, 'auto_exit')]


@pytest.mark.parametrize('plate', ['9999УБА', '1234УКА', '1234УБ'])
def test_low_confidence_without_exact_active_stay_stays_rejected(ctx, monkeypatch, plate):
    c = ctx; s = stay(c, plate='1234УБА')
    cgi(c, monkeypatch, plate, 14)
    e = c.db.query(M.LprEvent).one()
    assert not e.accepted and e.reject_reason.startswith('confidence<')
    assert s.status == 'OPEN' and not c.commands and not audits(c, 'EXIT_LOWCONF_MATCH')


def test_conflicting_plate_texts_are_not_overridden(ctx, monkeypatch):
    c = ctx; s = stay(c)
    cgi(c, monkeypatch, s.plate_number, 14, {'Plate': {'PlateNumber': '9999УБА'}})
    assert not c.db.query(M.LprEvent).one().accepted and s.status == 'OPEN'


# ── Audit #2: «Төлөөд нээгдээгүй» special exit for a stay never read at exit ──

def paid_stay(c):
    s = stay(c, status='PAID'); s.paid_at = datetime.utcnow()
    s.exit_deadline = s.paid_at + timedelta(minutes=15)
    c.db.add(M.Payment(session_id=s.id, provider='POS', payment_method='CARD', sender_invoice_no=f'p-{s.id}',
                       status='PAID', amount=1000, vat_amount=91, paid_at=s.paid_at, raw_payload={}))
    c.db.commit()
    return s


def special(c, s, **body):
    from app.routers import sessions_router as SR
    return asyncio.run(SR.special_exit(s.id, {'kind': 'paid_no_open', **body}, c.db, c.user))


def test_paid_no_open_without_exit_read_on_single_lane_site(ctx):
    c = ctx; single_lane(c); s = paid_stay(c)
    out = special(c, s)
    assert out['barrier_opened'] and s.status == 'CLOSED' and s.exit_confirmed is False
    assert c.commands == [(c.bars[0].id, s.id, 'manual')]


def test_paid_no_open_without_exit_read_needs_lane_on_multi_lane_site(ctx):
    from fastapi import HTTPException
    c = ctx; s = paid_stay(c)
    with pytest.raises(HTTPException) as err:
        special(c, s)
    assert err.value.status_code == 409 and not c.commands and s.status == 'PAID'
    out = special(c, s, device_id=c.bars[1].id)
    assert out['barrier_opened'] and c.commands == [(c.bars[1].id, s.id, 'manual')]


# ── POS «Гарах машинууд» жагсаалт: minutes=30 (POS апп) 400 өгдөг байсан ──

@pytest.mark.parametrize('minutes,visible', [(30, True), (60, True), (0, False), (500, True)])
def test_recent_exits_minutes_is_clamped_not_rejected(ctx, minutes, visible):
    from app.routers import sessions_router as SR
    c = ctx; s = stay(c, status='AWAITING_PAYMENT'); read(c, s, c.cams[0], age=20 * 60)
    rows = SR.recent_exits(site_id=c.site.id, minutes=minutes, db=c.db, user=c.user)
    assert [r['id'] for r in rows] == ([s.id] if visible else [])


def test_pos_terminal_queue_survives_large_default_window(ctx, monkeypatch):
    from app.routers import sessions_router as SR
    from app.config import settings
    monkeypatch.setattr(settings, 'exit_queue_show_min', 30)
    c = ctx; s = stay(c, status='AWAITING_PAYMENT'); read(c, s, c.cams[1], age=600)
    assert SR.recent_exits(site_id=c.site.id, db=c.db, user=c.user)[0]['id'] == s.id


# ── Аудит #4: «Орох цаг олдоогүй машины суурь хураамж» тохиргоо дахин хүчинтэй ──

def test_no_session_fee_setting_creates_flat_invoice_and_payment_opens(ctx):
    c = ctx
    A.set_site_rules(c.db, A.EXITRULES_KEY, c.site.id, {'no_session_fee': 2000}, 'test'); c.db.commit()
    r = exit_read(c, '9999УБА')
    assert r['action'] == 'no_session_fee' and r['amount_due'] == 2000
    s = c.db.get(M.ParkingSession, r['session_id'])
    assert s.status == 'AWAITING_PAYMENT' and s.fee_locked and s.exit_device_id == c.cams[0].id
    pay(c, s)
    assert c.commands == [(c.bars[0].id, s.id, 'payment')] and s.status == 'CLOSED'


@pytest.mark.parametrize('setup', ['fee_zero', 'registered_only'])
def test_no_session_fee_zero_or_closed_site_goes_to_operator(ctx, setup):
    c = ctx
    if setup == 'fee_zero':
        A.set_site_rules(c.db, A.EXITRULES_KEY, c.site.id, {'no_session_fee': 0}, 'test')
    else:
        c.site.registered_only = True
    c.db.commit()
    r = exit_read(c, '9999УБА')
    assert r['action'] == 'review_required' and r['reason'] == 'no_matching_stay_review'
    assert c.db.query(M.ParkingSession).count() == 0 and not c.commands


def test_ambiguous_near_stays_never_get_flat_fee(ctx):
    c = ctx; stay(c, plate='1041УКС'); stay(c, plate='1041УЛС')
    A.set_site_rules(c.db, A.EXITRULES_KEY, c.site.id, {'no_session_fee': 2000}, 'test'); c.db.commit()
    r = exit_read(c, '1041УНС')
    assert r['action'] == 'review_required' and r['reason'] == 'plate_mismatch_review'
    assert c.db.query(M.ParkingSession).count() == 2
