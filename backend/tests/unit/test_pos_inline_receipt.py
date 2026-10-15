"""PAX POS апп e-Barimt-ыг ЗӨВХӨН манай API-аас хэвлэдэг (2026-10-15).

Эзэн: «POST /pos/confirm дээр ebarimt lottery ирээд байгаа» ч QR 10-09-өөс хэвлэгдэхгүй.
Шалтгаан (код + audit1014): confirm-ийн хэвлэх багц outbox-оос ӨМНӨ бүрддэг (PENDING), /cash-д
хэвлэх багц огт байхгүй, /print нь QR-ыг зөвхөн outbox-ын POST хариунаас санах ойд орсон үед л
өгдөг, webhook-оор дууссан баримтын QR кэшлэгддэггүй, cache_qr(id, None) хасдаггүй, кэш төлбөрөөр
түлхүүрлэгдсэн (өрийн баримт дарна). ТТД 71101242183 = Моннис пропэрти ХХК-ийн e-Barimt 3.0 ТТД
(ДДТД-ийн угтвар 071101242183 — audit1014-д 10-р сард 162 баримт), 5037409 = түүний регистр.

Сүлжээ огт дуудахгүй — msgbill бүгд stub; DB = SQLite (санах ой).
"""
import asyncio
import json
import time
from datetime import datetime, timedelta
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

from app import models as M, session_logic as SL
from app.auth import require
from app.config import settings
from app.database import Base
from app.routers import payments_router as PR
from app.services import app_settings as A, ebarimt, financial_jobs as J, payment_rules as PRULES
from app.services import payment_wait, tin_lookup
from app.services.msgbill import MsgbillAccount

ACC = MsgbillAccount(api_key='synthetic-key', base_url='https://msgbill.invalid')
DDTD = '015200020090001097760067510045952'
QR = 'QR-5395fb81-main'


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    async def blocked(*a, **k):
        raise AssertionError('Network forbidden')
    monkeypatch.setattr(httpx.AsyncClient, 'send', blocked)
    monkeypatch.setattr(settings, 'qpay_ebarimt', False)
    monkeypatch.setattr(settings, 'receipt_inline_wait_sec', 6.0)
    monkeypatch.setattr(J.msgbill, 'account_enabled_for', lambda *a: ACC)
    monkeypatch.setattr(J.msgbill, 'api_key_for', lambda *a: ACC)
    ebarimt._qr_cache.clear()
    J._qr_attempts.clear()
    J._INFLIGHT.clear()
    yield
    ebarimt._qr_cache.clear()
    J._qr_attempts.clear()


@pytest.fixture
def db():
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    A.invalidate_cache()
    with Session(engine, autoflush=False) as session:
        yield session
    A.invalidate_cache()
    engine.dispose()


def parking(db, plate='1234УБА', site=None):
    if site is None:
        tariff = M.TariffTemplate(name='Hourly', free_minutes=0, extra_hour_price=1000, daily_cap=50000)
        site = M.ParkingSite(name='Спортын төв ордон', site_code='SPORT', tariff_template=tariff)
        db.add(site); db.flush()
    now = datetime.utcnow()
    s = M.ParkingSession(site_id=site.id, plate_number=plate, entry_time=now - timedelta(minutes=59),
                         status='AWAITING_PAYMENT')
    db.add(s); db.flush(); db.refresh(s)
    payment_wait.begin_wait(db, s, SL.session_fee_info(db, s, at=now), now)
    db.commit()
    return s


def user(db, name='pos.sport', role='POS', **kw):
    u = M.User(username=name, role=role, password_hash='unused', **kw)
    db.add(u); db.commit()
    return u


def req(app_version='1.6.0'):
    headers = [(b'x-app-version', app_version.encode())] if app_version else []
    return Request({'type': 'http', 'method': 'POST', 'path': '/', 'headers': headers,
                    'client': ('pos', 1)})


def created(no=DDTD, qr=QR, rid='rcp-1', lottery='IW 41850824'):
    return {'status': 'SUCCESS', 'billId': no, 'lottery': lottery, 'qrData': qr,
            'msgbillId': rid, 'state': 'CREATED', 'raw': {'id': rid, 'receipt_no': no,
                                                          'state': 'CREATED', 'qr_data': qr}}


async def confirm(db, s, u, txn='bank-1', **extra):
    return await PR.pos_confirm({'session_id': s.id, 'amount': 1000, 'transaction_id': txn,
                                 'terminal_id': 'PAX-1', **extra}, req(), db, u)


async def cash(db, s, u, **extra):
    return await PR.cash_payment({'session_id': s.id, **extra}, db, u, req())


def rows(db, model, **kw):
    db.expire_all()
    return db.query(model).filter_by(**kw).all()


# ── 1. Анхдагч УНТРААЛТТАЙ (Ontime-гүй апп) ───────────────────────────────────
def test_global_default_is_off_and_site_on_still_possible(db, monkeypatch):
    assert A.get_rules(db, A.EBARIMT_KEY)['pos_terminal_issues'] is False
    assert A.pos_terminal_receipt_methods(db, None) == set()
    create = AsyncMock(return_value=created())
    monkeypatch.setattr(J.msgbill, 'create_receipt', create)
    s, u = parking(db), user(db)
    out = asyncio.run(confirm(db, s, u))
    [r] = rows(db, M.VatReceipt, payment_id=out['payment_id'])
    assert (r.provider, r.status, r.ebarimt_id) == ('MSGBILL', 'SENT', DDTD)
    # Ontime-ийг дахин хэрэглэвэл зогсоолоор асааж болно — тэр үед EXTERNAL, msgbill-гүй
    A.set_site_rules(db, A.EBARIMT_KEY, s.site_id, {'pos_terminal_issues': True}, 'admin'); db.commit()
    s2 = parking(db, '5555УБА', site=db.get(M.ParkingSite, s.site_id))
    out2 = asyncio.run(confirm(db, s2, u, txn='bank-2'))
    [r2] = rows(db, M.VatReceipt, payment_id=out2['payment_id'])
    assert r2.status == 'EXTERNAL' and out2['receipt']['ebarimt_status'] == 'EXTERNAL'
    assert create.await_count == 1
    # Ерөнхийг асаахад ч ажиллана (ерөнхий/зогсоолын механизм хэвээр)
    A.set_rules(db, A.EBARIMT_KEY, {'pos_terminal_issues': True}, 'admin'); db.commit()
    assert A.pos_terminal_receipt_methods(db, 'other-site') == {'CARD', 'CASH'}


def test_app_ebarimt_id_path_is_unchanged(db, monkeypatch):
    create = AsyncMock(side_effect=AssertionError('must not issue'))
    monkeypatch.setattr(J.msgbill, 'create_receipt', create)
    s, u = parking(db), user(db)
    out = asyncio.run(confirm(db, s, u, ebarimt_id='029100244106001097210230010045912',
                              lottery_code='XA 1', qr_data='TERMINAL-QR', ebarimt_provider='ONTIME'))
    [r] = rows(db, M.VatReceipt, payment_id=out['payment_id'])
    assert (r.provider, r.status) == ('ONTIME', 'SENT') and out['qr_data'] == 'TERMINAL-QR'
    assert create.await_count == 0
    # Терминалын QR DB-д (payment.raw_payload, vat_receipts.raw) бичигдэхгүй
    p = db.get(M.Payment, out['payment_id'])
    assert 'TERMINAL-QR' not in json.dumps(p.raw_payload or {}) + json.dumps(r.raw or {})


# ── 2. confirm/cash: ДДТД + сугалаа + QR хариунд нь ───────────────────────────
def test_confirm_returns_ddtd_lottery_and_qr_inline(db, monkeypatch):
    create = AsyncMock(return_value=created())
    monkeypatch.setattr(J.msgbill, 'create_receipt', create)
    s, u = parking(db), user(db)
    out = asyncio.run(confirm(db, s, u))
    assert out['ebarimt_id'] == DDTD and out['lottery_code'] == 'IW 41850824'
    assert out['qr_data'] == QR
    assert out['receipt']['ebarimt_status'] == 'SENT' and out['receipt']['ebarimt_id'] == DDTD
    assert f'ДДТД: {DDTD}' in out['print_data']['lines']
    assert 'Сугалаа: IW 41850824' in out['print_data']['lines']
    [job] = rows(db, M.FinancialJob, payment_id=out['payment_id'])
    assert job.status == 'DONE' and job.attempts == 1 and job.lease_token is None
    # outbox-ын raw — QR ирсэн эсэх л (QR өөрөө DB-д ҮГҮЙ)
    [r] = rows(db, M.VatReceipt, payment_id=out['payment_id'])
    assert r.raw['outbox']['has_qr'] is True and QR not in json.dumps(r.raw)
    # Worker дараа нь давхар илгээхгүй
    factory = J.session_factory(db)
    asyncio.run(J.run_once(factory=factory))
    assert create.await_count == 1
    # Ижил idempotency түлхүүр (pay-<id>) — outbox-той нэг
    assert create.await_args.kwargs['idempotency_key'] == f"pay-{out['payment_id']}"


def test_post_without_qr_is_completed_by_one_get_inside_the_same_budget(db, monkeypatch):
    """msgbill POST-ын хариунд QR ирээгүй (10-09-өөс QR хэвлэгдэхгүй байсны боломжит шалтгаан) —
    confirm ДДТД-г POST-оос, QR-ыг НЭГ GET-ээр нөхөж хариунд нь өгнө; raw.outbox.has_qr=False."""
    no_qr = created(); no_qr['qrData'] = None
    monkeypatch.setattr(J.msgbill, 'create_receipt', AsyncMock(return_value=no_qr))
    get = AsyncMock(return_value=created())
    monkeypatch.setattr(J.msgbill, 'get_receipt_any', get)
    s, u = parking(db), user(db)
    out = asyncio.run(confirm(db, s, u))
    assert out['ebarimt_id'] == DDTD and out['qr_data'] == QR and get.await_count == 1
    assert 0 < get.await_args.kwargs['timeout'] <= J.QR_FETCH_TIMEOUT
    [r] = rows(db, M.VatReceipt, payment_id=out['payment_id'])
    assert r.raw['outbox']['has_qr'] is False


def test_zero_budget_disables_inline_and_qr_fetch_on_confirm_but_not_on_print(db, monkeypatch):
    monkeypatch.setattr(settings, 'receipt_inline_wait_sec', 0)
    no_qr = created(); no_qr['qrData'] = None
    create = AsyncMock(return_value=no_qr)
    monkeypatch.setattr(J.msgbill, 'create_receipt', create)
    get = AsyncMock(return_value=created())
    monkeypatch.setattr(J.msgbill, 'get_receipt_any', get)
    s, u = parking(db), user(db)
    out = asyncio.run(confirm(db, s, u))
    assert out['receipt']['ebarimt_status'] == 'PENDING'
    assert create.await_count == 0 and get.await_count == 0       # хуучин зан — outbox л
    asyncio.run(J.run_once(factory=J.session_factory(db)))         # worker илгээнэ (QR-гүй)
    again = asyncio.run(PR.payment_print(out['payment_id'], db, u))
    assert again['ebarimt_id'] == DDTD and again['qr_data'] == QR  # /print QR-ыг нөхнө
    assert create.await_count == 1 and get.await_count == 1


def test_cash_returns_same_print_payload_and_keeps_old_keys(db, monkeypatch):
    monkeypatch.setattr(J.msgbill, 'create_receipt', AsyncMock(return_value=created()))
    s, u = parking(db), user(db)
    out = asyncio.run(cash(db, s, u))
    old_keys = {'ok', 'status', 'payment_id', 'amount', 'session_status', 'amount_due',
                'needs_additional_payment', 'overpaid_amount', 'barrier_opened',
                'barrier_command_status', 'barrier_command_id', 'exit_deadline', 'exit_waiting',
                'gate_acknowledged', 'physical_gate_state'}
    print_keys = {'ebarimt_id', 'lottery_code', 'qr_data', 'receipt', 'print_data'}
    assert old_keys | print_keys <= set(out)
    assert out['ok'] is True and out['status'] == 'PAID'
    assert (out['ebarimt_id'], out['qr_data']) == (DDTD, QR)
    assert out['receipt']['ebarimt_status'] == 'SENT' and out['receipt']['payment_method'] == 'CASH'
    # /pos/confirm-той ижил хэвлэх бүтэц
    s2 = parking(db, '7777УБА', site=db.get(M.ParkingSite, s.site_id))
    conf = asyncio.run(confirm(db, s2, u, txn='bank-9'))
    assert set(conf['receipt']) == set(out['receipt'])


def test_web_cash_gets_print_payload_without_waiting(db, monkeypatch):
    """Веб касс (хөтөч, толгойгүй) баримт хэвлэдэггүй — хүлээлгэхгүй; хэвлэх багц нь ирнэ."""
    create = AsyncMock(return_value=created())
    monkeypatch.setattr(J.msgbill, 'create_receipt', create)
    s, u = parking(db), user(db, 'sogii', role='OPERATOR')
    browser = Request({'type': 'http', 'method': 'POST', 'path': '/', 'client': ('web', 1),
                       'headers': [(b'user-agent', b'Mozilla/5.0 (Windows NT 10.0)')]})
    out = asyncio.run(PR.cash_payment({'session_id': s.id}, db, u, browser))
    p = db.get(M.Payment, out['payment_id'])
    assert p.source is None and create.await_count == 0
    assert out['receipt']['ebarimt_status'] == 'PENDING' and {'print_data', 'qr_data'} <= set(out)
    asyncio.run(J.run_once(factory=J.session_factory(db)))       # worker ≤5с-д илгээнэ
    assert create.await_count == 1


def test_msgbill_slow_returns_pending_then_task_finishes_once(db, monkeypatch):
    """Төсөв дуусахад task-ийг ЦУЦЛАХГҮЙ — lease-тэйгээ дуусна; worker давхар илгээхгүй."""
    monkeypatch.setattr(settings, 'receipt_inline_wait_sec', 0.2)

    async def slow(*a, **k):
        await asyncio.sleep(0.6)
        return created()
    create = AsyncMock(side_effect=slow)
    monkeypatch.setattr(J.msgbill, 'create_receipt', create)
    s, u = parking(db), user(db)
    factory = J.session_factory(db)

    async def scenario():
        out = await confirm(db, s, u)
        assert out['receipt']['ebarimt_status'] == 'PENDING' and out['qr_data'] is None
        assert 'ДДТД: хүлээгдэж байна' in out['print_data']['lines']
        # msgbill хариулаагүй байхад хариу буцсан — task lease-тэйгээ үргэлжилж байна
        assert len(J._INFLIGHT) == 1
        [job] = rows(db, M.FinancialJob, payment_id=out['payment_id'])
        assert job.status == 'SENDING' and job.lease_token
        # Илгээж байх үед worker ажлыг АВАХГҮЙ (SENDING lease)
        await J.run_once(factory=factory)
        assert create.await_count == 1
        await asyncio.gather(*list(J._INFLIGHT.values()))
        return out
    out = asyncio.run(scenario())
    [job] = rows(db, M.FinancialJob, payment_id=out['payment_id'])
    [r] = rows(db, M.VatReceipt, payment_id=out['payment_id'])
    assert job.status == 'DONE' and (r.status, r.ebarimt_id) == ('SENT', DDTD)
    assert create.await_count == 1
    # Апп /print-ээр дахин татахад ДДТД + QR (санах ойд)
    again = asyncio.run(PR.payment_print(out['payment_id'], db, u))
    assert again['ebarimt_id'] == DDTD and again['qr_data'] == QR


def test_event_loop_not_blocked_while_waiting(db, monkeypatch):
    """msgbill хариу хүлээх үед event loop чөлөөтэй (хаалт/LPR-тай нэг loop) — хүлээлтийн
    цонхонд ticker тасралтгүй ажиллана."""
    window = {}

    async def slow(*a, **k):
        window['start'] = time.monotonic()
        await asyncio.sleep(0.4)
        window['end'] = time.monotonic()
        return created()
    monkeypatch.setattr(J.msgbill, 'create_receipt', AsyncMock(side_effect=slow))
    s, u = parking(db), user(db)

    async def scenario():
        ticks, stop = [], False

        async def ticker():
            while not stop:
                await asyncio.sleep(0.01)
                ticks.append(time.monotonic())
        t = asyncio.ensure_future(ticker())
        out = await confirm(db, s, u)
        stop = True
        await t
        return out, ticks
    out, ticks = asyncio.run(scenario())
    assert out['ebarimt_id'] == DDTD
    inside = [x for x in ticks if window['start'] <= x <= window['end']]
    gaps = [b - a for a, b in zip(inside, inside[1:])]
    assert len(inside) >= 10 and max(gaps) < 0.2


def test_msgbill_down_returns_pending_and_worker_retries_same_key(db, monkeypatch):
    create = AsyncMock(side_effect=[httpx.ConnectError('down'), created()])
    monkeypatch.setattr(J.msgbill, 'create_receipt', create)
    s, u = parking(db), user(db)
    out = asyncio.run(confirm(db, s, u))
    assert out['status'] == 'PAID' and out['receipt']['ebarimt_status'] == 'PENDING'
    [job] = rows(db, M.FinancialJob, payment_id=out['payment_id'])
    assert job.status == 'RETRY' and job.last_error == 'ConnectError'
    job.next_attempt_at = datetime.utcnow() - timedelta(seconds=1); db.commit()
    asyncio.run(J.run_once(factory=J.session_factory(db)))
    [r] = rows(db, M.VatReceipt, payment_id=out['payment_id'])
    assert r.status == 'SENT' and create.await_count == 2
    keys = {c.kwargs['idempotency_key'] for c in create.await_args_list}
    assert keys == {f"pay-{out['payment_id']}"}


def test_retry_in_backoff_is_not_hammered_by_print(db, monkeypatch):
    create = AsyncMock(side_effect=httpx.ConnectError('down'))
    monkeypatch.setattr(J.msgbill, 'create_receipt', create)
    s, u = parking(db), user(db)
    out = asyncio.run(confirm(db, s, u))
    for _ in range(3):
        asyncio.run(PR.payment_print(out['payment_id'], db, u))
    assert create.await_count == 1        # /print нь баримт ИЛГЭЭХГҮЙ (зөвхөн уншина)


def test_process_death_mid_send_worker_reclaims_with_same_key(db, monkeypatch):
    """Task-ийг тасалбал (процесс унасан) lease 3 мин дуусч worker ИЖИЛ түлхүүрээр нөхнө."""
    async def hang(*a, **k):
        await asyncio.sleep(30)
    create = AsyncMock(side_effect=hang)
    monkeypatch.setattr(J.msgbill, 'create_receipt', create)
    monkeypatch.setattr(settings, 'receipt_inline_wait_sec', 0.05)
    s, u = parking(db), user(db)
    out = asyncio.run(confirm(db, s, u))   # asyncio.run хаагдахад task цуцлагдана (= унасан)
    [job] = rows(db, M.FinancialJob, payment_id=out['payment_id'])
    assert job.status == 'SENDING' and job.lease_until > datetime.utcnow()
    assert J.claim_one(db) is None                       # lease дуусаагүй — хэн ч авахгүй
    claim = J.claim_one(db, datetime.utcnow() + timedelta(minutes=4))
    assert claim not in (None, 'quarantined')            # MSGBILL — тусгаарлахгүй, дахин илгээнэ
    create.side_effect = None
    create.return_value = created()
    asyncio.run(J.deliver(*claim, factory=J.session_factory(db)))
    [r] = rows(db, M.VatReceipt, payment_id=out['payment_id'])
    assert r.status == 'SENT'
    keys = {c.kwargs['idempotency_key'] for c in create.await_args_list}
    assert keys == {f"pay-{out['payment_id']}"} and create.await_count == 2


def test_idempotent_confirm_retry_awaits_inflight_without_second_send(db, monkeypatch):
    monkeypatch.setattr(settings, 'receipt_inline_wait_sec', 0.1)

    async def slow(*a, **k):
        await asyncio.sleep(0.4)
        return created()
    create = AsyncMock(side_effect=slow)
    monkeypatch.setattr(J.msgbill, 'create_receipt', create)
    s, u = parking(db), user(db)

    async def scenario():
        first = await confirm(db, s, u, txn='bank-77')
        assert first['receipt']['ebarimt_status'] == 'PENDING'
        J.settings.receipt_inline_wait_sec = 2.0
        second = await confirm(db, s, u, txn='bank-77')     # offline queue-ийн давталт
        return first, second
    first, second = asyncio.run(scenario())
    assert second['payment_id'] == first['payment_id']
    assert second['ebarimt_id'] == DDTD and second['qr_data'] == QR
    assert create.await_count == 1
    assert len(rows(db, M.Payment, session_id=s.id)) == 1
    assert len(rows(db, M.VatReceipt, payment_id=first['payment_id'])) == 1


def test_inline_waits_for_worker_already_sending(db, monkeypatch):
    """Worker хаалт нээгдэх хооронд ажлыг аль хэдийн авсан бол confirm давхар илгээхгүй."""
    async def slow(*a, **k):
        await asyncio.sleep(0.2)
        return created()
    create = AsyncMock(side_effect=slow)
    monkeypatch.setattr(J.msgbill, 'create_receipt', create)
    s, u = parking(db), user(db)
    factory = J.session_factory(db)
    p = M.Payment(session_id=s.id, provider='CASH', payment_method='CASH', amount=1000, vat_amount=91,
                  status='PAID', paid_at=datetime.utcnow(), sender_invoice_no='w-1')
    db.add(p); db.flush()
    J.enqueue_receipt(db, p, terminal=False); db.commit()
    pid = p.id

    async def scenario():
        worker = asyncio.ensure_future(J.run_once(factory=factory, limit=1))
        await asyncio.sleep(0.05)                     # worker SENDING
        res = await J.deliver_now(db, pid, budget=2.0)
        await worker
        return res
    res = asyncio.run(scenario())
    assert res['claimed'] == 0 and res['awaited'] == 1 and res['pending'] == 0
    assert create.await_count == 1


def test_inline_only_for_msgbill_jobs(db, monkeypatch):
    s = parking(db)
    p = M.Payment(session_id=s.id, provider='QPAY', payment_method='QR', amount=1000, vat_amount=91,
                  status='PAID', paid_at=datetime.utcnow(), sender_invoice_no='q-1',
                  provider_payment_id='bank-id')
    db.add(p); db.flush()
    monkeypatch.setattr(settings, 'qpay_ebarimt', True)
    J.enqueue_receipt(db, p); db.commit()
    qcreate = AsyncMock(side_effect=AssertionError('QPay is not idempotent'))
    monkeypatch.setattr(J.qpay, 'create_ebarimt', qcreate)
    res = asyncio.run(J.deliver_now(db, p.id, budget=1.0))
    assert res['claimed'] == 0 and qcreate.await_count == 0
    [job] = rows(db, M.FinancialJob, payment_id=p.id)
    assert job.status == 'PENDING' and job.attempts == 0


def test_inline_disabled_by_zero_budget(db, monkeypatch):
    monkeypatch.setattr(settings, 'receipt_inline_wait_sec', 0)
    create = AsyncMock(return_value=created())
    monkeypatch.setattr(J.msgbill, 'create_receipt', create)
    s, u = parking(db), user(db)
    out = asyncio.run(confirm(db, s, u))
    assert out['receipt']['ebarimt_status'] == 'PENDING' and create.await_count == 0
    [job] = rows(db, M.FinancialJob, payment_id=out['payment_id'])
    assert job.status == 'PENDING'
    # Тохиргоо 6с-ээс их байсан ч хүлээлт 6с-ээр хязгаарлагдана (INLINE_MAX_SEC)
    monkeypatch.setattr(settings, 'receipt_inline_wait_sec', 999)
    waited = []
    real_wait = asyncio.wait

    async def spy(tasks, timeout=None):
        waited.append(timeout)
        return await real_wait(tasks, timeout=timeout)
    monkeypatch.setattr(J.asyncio, 'wait', spy)
    asyncio.run(J.deliver_now(db, out['payment_id']))
    assert waited and 0 < waited[0] <= J.INLINE_MAX_SEC == 6.0
    assert create.await_count == 1


# ── 3. QR: /print нөхөлт, кэшийн түлхүүр, хасалт ────────────────────────────
def sent_payment(db, s, *, qr_cached=False, ref='rcp-9', no=DDTD, method='CARD', provider='POS'):
    p = M.Payment(session_id=s.id, provider=provider, payment_method=method, amount=1000,
                  vat_amount=91, status='PAID', paid_at=datetime.utcnow(),
                  sender_invoice_no=f'x-{ref}', source='POSAPP')
    db.add(p); db.flush()
    r = M.VatReceipt(payment_id=p.id, session_id=s.id, amount=1000, vat_amount=91,
                     provider='MSGBILL', provider_ref=ref, ebarimt_id=no, lottery_code='IW 1',
                     status='SENT')
    db.add(r); db.commit()
    if qr_cached:
        ebarimt.cache_qr(r.id, QR)
    return p, r


def test_print_fetches_missing_qr_once_and_caches(db, monkeypatch):
    s, u = parking(db), user(db)
    p, r = sent_payment(db, s)
    get = AsyncMock(return_value=created(rid='rcp-9'))
    monkeypatch.setattr(J.msgbill, 'get_receipt_any', get)
    out = asyncio.run(PR.payment_print(p.id, db, u))
    assert out['qr_data'] == QR and get.await_count == 1
    assert get.await_args.kwargs['timeout'] <= 3.0 and get.await_args.args[1] == 'rcp-9'
    assert ebarimt.get_cached_qr(r.id) == QR
    out2 = asyncio.run(PR.payment_print(p.id, db, u))
    assert out2['qr_data'] == QR and get.await_count == 1      # кэшээс
    # DB-д QR бичигдээгүй
    db.expire_all()
    assert QR not in json.dumps(db.get(M.VatReceipt, r.id).raw or {})


def test_print_qr_fetch_failure_does_not_break_and_is_not_hammered(db, monkeypatch):
    s, u = parking(db), user(db)
    p, _r = sent_payment(db, s)
    get = AsyncMock(side_effect=httpx.ReadTimeout('slow'))
    monkeypatch.setattr(J.msgbill, 'get_receipt_any', get)
    for _ in range(3):
        out = asyncio.run(PR.payment_print(p.id, db, u))
        assert out['qr_data'] is None and out['ebarimt_id'] == DDTD
    assert get.await_count == 1                     # 60с дотор дахин оролдохгүй
    J._qr_attempts.clear()
    get.side_effect = None
    get.return_value = created(rid='rcp-9')
    assert asyncio.run(PR.payment_print(p.id, db, u))['qr_data'] == QR


def test_print_ignores_qr_of_a_different_receipt_number(db, monkeypatch):
    s, u = parking(db), user(db)
    p, _r = sent_payment(db, s)
    monkeypatch.setattr(J.msgbill, 'get_receipt_any',
                        AsyncMock(return_value=created(no='0152000200900010977600000000OTHER')))
    assert asyncio.run(PR.payment_print(p.id, db, u))['qr_data'] is None


def test_print_does_not_fetch_for_non_msgbill_or_unsent(db, monkeypatch):
    s, u = parking(db), user(db)
    get = AsyncMock(side_effect=AssertionError('no fetch'))
    monkeypatch.setattr(J.msgbill, 'get_receipt_any', get)
    p, r = sent_payment(db, s)
    r.status, r.ebarimt_id = 'PENDING', None; db.commit()
    assert asyncio.run(PR.payment_print(p.id, db, u))['qr_data'] is None
    r.status, r.ebarimt_id, r.provider = 'SENT', DDTD, 'QPAY'; db.commit()
    assert asyncio.run(PR.payment_print(p.id, db, u))['qr_data'] is None
    assert get.await_count == 0


def test_pos_receipts_fill_missing_qr(db, monkeypatch):
    s, u = parking(db), user(db)
    p1, _ = sent_payment(db, s, ref='rcp-a')
    p2, _ = sent_payment(db, s, ref='rcp-b', qr_cached=True)
    get = AsyncMock(return_value=created(rid='rcp-a'))
    monkeypatch.setattr(J.msgbill, 'get_receipt_any', get)
    out = asyncio.run(PR.pos_recent_receipts(None, 10, db, u))
    by = {row['payment_id']: row for row in out}
    assert by[p1.id]['qr_data'] == QR and by[p2.id]['qr_data'] == QR
    assert get.await_count == 1                       # кэштэйг дахин татахгүй
    # msgbill унасан ч жагсаалт ирнэ
    ebarimt._qr_cache.clear(); J._qr_attempts.clear()
    get.side_effect = RuntimeError('down')
    out = asyncio.run(PR.pos_recent_receipts(None, 10, db, u))
    assert len(out) == 2 and all(row['qr_data'] is None for row in out)


def test_qr_cache_is_keyed_by_receipt_debt_cannot_overwrite_main(db, monkeypatch):
    s = parking(db)
    p = M.Payment(session_id=s.id, provider='CASH', payment_method='CASH', amount=2500, vat_amount=227,
                  status='PAID', paid_at=datetime.utcnow(), sender_invoice_no='d-1')
    db.add(p); db.flush()
    main = J.enqueue_receipt(db, p, amount=1000, vat=91, terminal=False)
    debt = J.enqueue_receipt(db, p, amount=1500, vat=136, key=f'pay-{p.id}-comp-1', terminal=False)
    db.commit()
    by_key = {f'pay-{p.id}': created(no=DDTD, qr='MAIN-QR', rid='rcp-main'),
              f'pay-{p.id}-comp-1': created(no=DDTD[:-1] + '9', qr='DEBT-QR', rid='rcp-debt')}

    async def issue(*a, **k):
        if k['idempotency_key'].endswith('comp-1'):
            await asyncio.sleep(0.05)               # өрийнх СҮҮЛД дуусна
        return by_key[k['idempotency_key']]
    monkeypatch.setattr(J.msgbill, 'create_receipt', AsyncMock(side_effect=issue))
    res = asyncio.run(J.deliver_now(db, p.id, budget=2.0))
    assert res['claimed'] == 2 and res['pending'] == 0
    db.expire_all()
    payload = PR._print_payload(db, db.get(M.Payment, p.id))
    assert payload['ebarimt_id'] == DDTD and payload['qr_data'] == 'MAIN-QR'
    assert ebarimt.get_cached_qr(debt.id) == 'DEBT-QR' and ebarimt.get_cached_qr(main.id) == 'MAIN-QR'
    assert ebarimt.get_cached_qr(p.id) is None


def test_print_prefers_main_receipt_over_sent_debt_receipt(db):
    """Үндсэн баримт PENDING, өрийнх SENT — хэвлэлт өрийн ДДТД/QR-ыг ҮНДСЭН мэт харуулахгүй."""
    s = parking(db)
    other = parking(db, '9999УБА', site=db.get(M.ParkingSite, s.site_id))
    p = M.Payment(session_id=s.id, provider='CASH', payment_method='CASH', amount=2500, vat_amount=227,
                  status='PAID', paid_at=datetime.utcnow(), sender_invoice_no='d-2')
    db.add(p); db.flush()
    main = J.enqueue_receipt(db, p, amount=1000, vat=91, terminal=False)
    debt = J.enqueue_receipt(db, p, amount=1500, vat=136, session_id=other.id,
                             key=f'pay-{p.id}-comp-9', terminal=False)
    debt.status, debt.ebarimt_id, debt.provider_ref = 'SENT', DDTD, 'rcp-debt'
    db.commit()
    ebarimt.cache_qr(debt.id, 'DEBT-QR')
    out = PR._print_payload(db, p)
    assert out['receipt']['ebarimt_status'] == 'PENDING' and out['ebarimt_id'] is None
    assert out['qr_data'] is None and main.status == 'PENDING'


def test_print_prefers_main_receipt_over_sent_debt_receipt(db, monkeypatch):
    """Үндсэн баримт PENDING, өрийн баримт SENT — хэвлэлт өрийн ДДТД/QR-ыг зогсоолынх мэт
    гаргахгүй (PENDING → апп дахин татна)."""
    s = parking(db)
    old = parking(db, '9999УБА', site=db.get(M.ParkingSite, s.site_id))
    p = M.Payment(session_id=s.id, provider='CASH', payment_method='CASH', amount=2500, vat_amount=227,
                  status='PAID', paid_at=datetime.utcnow(), sender_invoice_no='d-2')
    db.add(p); db.flush()
    J.enqueue_receipt(db, p, amount=1000, vat=91, terminal=False)
    debt = J.enqueue_receipt(db, p, amount=1500, vat=136, session_id=old.id,
                             key=f'pay-{p.id}-comp-2', terminal=False)
    debt.status, debt.ebarimt_id, debt.provider_ref = 'SENT', DDTD, 'rcp-debt'
    db.commit()
    ebarimt.cache_qr(debt.id, 'DEBT-QR')
    get = AsyncMock(side_effect=AssertionError('no fetch for PENDING main'))
    monkeypatch.setattr(J.msgbill, 'get_receipt_any', get)
    u = user(db)
    out = asyncio.run(PR.payment_print(p.id, db, u))
    assert out['receipt']['ebarimt_status'] == 'PENDING'
    assert out['ebarimt_id'] is None and out['qr_data'] is None and get.await_count == 0


def test_cache_qr_none_evicts():
    ebarimt.cache_qr('rec-1', 'X')
    assert ebarimt.get_cached_qr('rec-1') == 'X'
    ebarimt.cache_qr('rec-1', None)
    assert ebarimt.get_cached_qr('rec-1') is None
    ebarimt.cache_qr('', 'Y')                     # хоосон түлхүүр — юу ч хийхгүй
    assert '' not in ebarimt._qr_cache


def test_webhook_caches_qr_without_storing_it_and_cancel_evicts(db):
    s = parking(db)
    p, r = sent_payment(db, s, ref='rcp-w')
    r.ebarimt_id, r.status = None, 'PENDING'; db.commit()
    data = {'receipt_id': 'rcp-w', 'receipt_no': DDTD, 'lottery': 'IW 2', 'qr_data': QR}
    assert PR._apply_msgbill_event(db, r, 'receipt.created', data) == 'set'
    db.commit()
    assert ebarimt.get_cached_qr(r.id) == QR and r.status == 'SENT'
    raw = json.dumps(r.raw)
    assert QR not in raw and r.raw['webhook.receipt.created']['has_qr'] is True
    assert PR._print_payload(db, p)['qr_data'] == QR
    # Өөр дугаартай webhook (зөрчил) QR-ыг дарахгүй
    other = {**data, 'receipt_no': DDTD[:-1] + '0', 'qr_data': 'OTHER-QR'}
    assert PR._apply_msgbill_event(db, r, 'receipt.created', other) == 'conflict'
    assert ebarimt.get_cached_qr(r.id) == QR
    assert PR._apply_msgbill_event(db, r, 'receipt.cancelled', {'receipt_no': DDTD}) == 'cancelled'
    assert ebarimt.get_cached_qr(r.id) is None and PR._print_payload(db, p)['qr_data'] is None


def test_webhook_before_outbox_result_does_not_double_send(db, monkeypatch):
    """Outbox POST-ын хариу ирэхээс ӨМНӨ webhook ДДТД-г бичсэн: outbox ижил дугаар → «same»,
    дахин илгээхгүй; хэвлэлт нэг л ДДТД."""
    s, u = parking(db), user(db)

    async def racing(*a, **k):
        # msgbill баримтыг үүсгээд webhook-ийг POST-ын хариунаас ӨМНӨ илгээсэн
        with J.session_factory(db)() as other:
            rec = other.query(M.VatReceipt).one()
            rec.provider_ref = 'rcp-1'; other.commit()
            PR._apply_msgbill_event(other, rec, 'receipt.created',
                                    {'receipt_id': 'rcp-1', 'receipt_no': DDTD, 'lottery': 'IW 41850824'})
            other.commit()
        return created()
    create = AsyncMock(side_effect=racing)
    monkeypatch.setattr(J.msgbill, 'create_receipt', create)
    out = asyncio.run(confirm(db, s, u))
    [r] = rows(db, M.VatReceipt, payment_id=out['payment_id'])
    assert (r.status, r.ebarimt_id) == ('SENT', DDTD) and r.ddtd_note is None
    assert out['ebarimt_id'] == DDTD and out['qr_data'] == QR and create.await_count == 1
    assert db.query(M.AuditLog).filter_by(action='EBARIMT_ID_CONFLICT').count() == 0


# ── 4. Аппын бусад дуудлага ───────────────────────────────────────────────────
def test_qpay_check_accepts_get_and_post(db):
    """PAX апп GET-ээр дууддаг — өмнө нь зөвхөн POST тул 405 буцдаг байв."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(PR.router)
    app.dependency_overrides[PR.get_db] = lambda: db
    client = TestClient(app)
    for method in ('get', 'post'):
        r = getattr(client, method)('/api/payments/qpay/check/does-not-exist')
        assert r.status_code == 404, (method, r.status_code)   # 405 биш — маршрут таарна
    s = parking(db)
    p = M.Payment(session_id=s.id, provider='QPAY', payment_method='QR', amount=1000, vat_amount=91,
                  status='PENDING', sender_invoice_no='pending-get')
    db.add(p); db.commit()
    assert client.get(f'/api/payments/qpay/check/{p.id}').json() == {'status': 'PENDING'}


def test_qpay_check_returns_print_payload_when_paid(db, monkeypatch):
    s = parking(db)
    p, r = sent_payment(db, s, provider='QPAY', method='QR', qr_cached=True)
    rq = Request({'type': 'http', 'method': 'GET', 'path': '/', 'headers': [], 'client': ('1.2.3.4', 1)})
    out = asyncio.run(PR.qpay_check(p.id, rq, db))
    assert out['status'] == 'PAID' and out['ebarimt_id'] == DDTD and out['qr_data'] == QR
    p2 = M.Payment(session_id=s.id, provider='QPAY', payment_method='QR', amount=1000, vat_amount=91,
                   status='PENDING', sender_invoice_no='pending-1')
    db.add(p2); db.commit()
    assert asyncio.run(PR.qpay_check(p2.id, rq, db)) == {'status': 'PENDING'}


@pytest.mark.parametrize('reg_no,calls_regno', [('5037409', True), ('71101242183', False)])
def test_payer_lookup_company_register_and_tin(reg_no, calls_regno, monkeypatch):
    """5037409 = Моннис пропэрти ХХК регистр; 71101242183 = түүний e-Barimt 3.0 ТТД (11 орон,
    ААН — иргэний ТТД БИШ). Хоёулаа ORGANIZATION; ТТД бол getTinInfo алгасна."""
    seen = []

    async def fake_get(self, url, params=None, **k):
        seen.append((url.rsplit('/', 1)[-1], dict(params or {})))
        if url.endswith('getTinInfo'):
            return httpx.Response(200, json={'data': '71101242183'}, request=httpx.Request('GET', url))
        return httpx.Response(200, json={'data': {'name': 'Моннис пропэрти', 'vatPayer': True}},
                              request=httpx.Request('GET', url))
    monkeypatch.setattr(httpx.AsyncClient, 'get', fake_get)
    monkeypatch.setattr(settings, 'ebarimt_mock', True)
    monkeypatch.setattr(settings, 'ebarimt_tin_lookup_url', 'https://api.ebarimt.invalid/api')
    tin_lookup._cache.clear()
    pos = NS(role='POS', username='pos.sport', permissions=None, site_ids=[], site_id=None)
    out = asyncio.run(PR.ebarimt_payer(reg_no, pos))
    assert out['receipt_type'] == 'ORGANIZATION' and out['reg_no'] == reg_no
    assert out['available'] and out['found'] and out['tin'] == '71101242183'
    assert out['name'] == 'Моннис пропэрти' and out['is_vat_payer'] is True
    assert (seen[0][0] == 'getTinInfo') is calls_regno
    assert seen[-1] == ('getInfo', {'tin': '71101242183'})
    tin_lookup._cache.clear()
    # POS роль «cashier» эрхтэй — endpoint-ийн хамгаалалтыг давна
    checker = require('cashier')
    assert checker(user=NS(role='POS', permissions=None)) is not None


def test_confirm_with_11_digit_tin_issues_organization_receipt(db, monkeypatch):
    create = AsyncMock(return_value={**created(), 'lottery': None})
    monkeypatch.setattr(J.msgbill, 'create_receipt', create)
    s, u = parking(db), user(db)
    out = asyncio.run(confirm(db, s, u, payer_reg_no='71101242183'))
    p = db.get(M.Payment, out['payment_id'])
    assert (p.customer_tin, p.ebarimt_receiver_type) == ('71101242183', 'COMPANY')
    assert create.await_args.kwargs['payer_reg_no'] == '71101242183'
    assert out['receipt']['receipt_type'] == 'ORGANIZATION'
    assert 'Худалдан авагч ТТД: 71101242183' in out['print_data']['lines']
    assert out['lottery_code'] is None                  # ААН — сугалаагүй
    from app.services.msgbill import classify_reg_no
    assert classify_reg_no('5037409') == ('5037409', 'ORGANIZATION')
    assert classify_reg_no('71101242183') == ('71101242183', 'ORGANIZATION')


def _site(db, code, tenant=None):
    site = M.ParkingSite(name=code, site_code=code, tenant_id=tenant.id if tenant else None)
    db.add(site); db.flush()
    return site


def _paid(db, site, n):
    s = M.ParkingSession(site_id=site.id, plate_number=f'{n}000УБА', entry_time=datetime.utcnow(),
                         status='PAID')
    db.add(s); db.flush()
    p = M.Payment(session_id=s.id, provider='CASH', payment_method='CASH', amount=1000, vat_amount=91,
                  status='PAID', paid_at=datetime.utcnow(), sender_invoice_no=f'l-{site.site_code}-{n}',
                  created_at=datetime.utcnow() - timedelta(seconds=n))
    db.add(p); db.flush()
    return p


def test_list_payments_pos_role_scope_and_limit(db):
    ten = M.Tenant(name='Моннис', code='MONNIS'); db.add(ten); db.flush()
    a, b, c = _site(db, 'A'), _site(db, 'B'), _site(db, 'MON', ten)
    for i in range(12):
        _paid(db, a, i)
    _paid(db, b, 50); _paid(db, c, 60)
    db.commit()
    scoped = user(db, 'pos.a', site_ids=[a.id])
    out = PR.list_payments(limit=10, db=db, user=scoped)
    assert out['total'] == 12 and len(out['rows']) == 10
    assert {r['site_name'] for r in out['rows']} == {'A'}
    # өөр зогсоол сонгоход өөрийнх рүү буцна
    assert {r['site_name'] for r in PR.list_payments(site_id=b.id, limit=10, db=db, user=scoped)['rows']} == {'A'}
    tenant_pos = user(db, 'pos.monnis', tenant_id=ten.id)
    rows_t = PR.list_payments(limit=10, db=db, user=tenant_pos)['rows']
    assert [r['site_name'] for r in rows_t] == ['MON']
    with pytest.raises(HTTPException) as e:
        asyncio.run(PR.payment_print(rows_t[0]['id'], db, scoped))     # өөр зогсоолын баримт
    assert e.value.status_code == 403


def test_print_of_sessionless_debt_payment_is_site_scoped(db):
    a, b = _site(db, 'A'), _site(db, 'B')
    p = M.Payment(site_id=b.id, kind='DEBT', provider='CASH', payment_method='CASH', amount=500,
                  vat_amount=45, status='PAID', paid_at=datetime.utcnow(), sender_invoice_no='debt-1',
                  raw_payload={'plate_number': '1111УБА'})
    db.add(p); db.commit()
    scoped = user(db, 'pos.a', site_ids=[a.id])
    with pytest.raises(HTTPException) as e:
        asyncio.run(PR.payment_print(p.id, db, scoped))
    assert e.value.status_code == 403


# ── 5. Тохиргооны анхааруулга (урвуу) ────────────────────────────────────────
def test_warning_now_fires_when_rule_on_with_pos_traffic(db, monkeypatch):
    monkeypatch.setattr(J.msgbill, 'create_receipt', AsyncMock(return_value=created()))
    s, u = parking(db), user(db)
    site = db.get(M.ParkingSite, s.site_id)
    titles = lambda: [c['title'] for c in PRULES.check_conflicts(db, site)]   # noqa: E731
    asyncio.run(confirm(db, s, u))                              # анхдагч OFF → msgbill
    assert not any('POS аппын төлбөртэй' in t for t in titles())
    A.set_site_rules(db, A.EBARIMT_KEY, site.id, {'pos_terminal_issues': True}, 'admin'); db.commit()
    s2 = parking(db, '6666УБА', site=site)
    asyncio.run(confirm(db, s2, u, txn='bank-6'))               # асаалттай → EXTERNAL
    found = [c for c in PRULES.check_conflicts(db, site) if 'POS аппын төлбөртэй' in c['title']]
    assert found and found[0]['level'] == 'high' and 'асаалттай' in found[0]['title']
    on = PRULES.pos_terminal_on_traffic(db, site.id)
    assert on == {'count': 2, 'methods': ['CARD', 'CASH'], 'external': 1}
