"""POS терминал өөрөө НӨАТ баримт гаргадаг зогсоол — давхар e-Barimt (2026-10-13).

ТЕГ тулгалт (прод хуулбар): систем карт/бэлэн/дансаар төлбөр бүрд msgbill-ээр ЯГ НЭГ
баримт гаргадаг; давхардал нь PAX терминал Ontime-ийн (касс …10002990) баримтыг
ӨӨРӨӨ хэвлэдэг ч апп тэр ДДТД-г /pos/confirm, /cash-д илгээдэггүйгээс үүсдэг —
2026-08-24 Хангарьд 63 (39 бэлэн + 24 карт, pos.khangarid). Аль зогсоолын
терминал хэвлэдэг нь тодорхойгүй тул зогсоол бүрээр, анхдагч УНТРААЛТТАЙ.

Сүлжээ/банк/msgbill огт дуудахгүй — бүх суваг stub.
"""
import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
from io import BytesIO
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
from app.config import settings
from app.database import Base
from app.routers import compensations_router as C, payments_router as PR, reports_router as R
from app.routers import reports_excel as X, vat_recon as V
from app.services import app_settings as A, financial_jobs as J, payment_rules as PRULES
from app.services import payment_wait
from app.services.msgbill import MsgbillAccount
from app.services.receipts import choose_primary

ACC = MsgbillAccount(api_key='synthetic-key', base_url='https://msgbill.invalid')


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    async def blocked_async(*a, **k):
        raise AssertionError('Network forbidden')
    monkeypatch.setattr(httpx.AsyncClient, 'send', blocked_async)
    monkeypatch.setattr(settings, 'qpay_ebarimt', False)
    # msgbill тохируулсан зогсоол (прод шиг): карт/бэлэн → MSGBILL суваг
    monkeypatch.setattr(J.msgbill, 'account_enabled_for', lambda *a: ACC)
    monkeypatch.setattr(J.msgbill, 'api_key_for', lambda *a: ACC)


@pytest.fixture
def db():
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    A.invalidate_cache()
    with Session(engine, autoflush=False) as session:
        yield session
    A.invalidate_cache()
    engine.dispose()


def parking(db, code='HANGARID', plate='1234УБА'):
    tariff = M.TariffTemplate(name='Hourly', free_minutes=0, extra_hour_price=1000, daily_cap=50000)
    site = M.ParkingSite(name='Хангарьд', site_code=code, tariff_template=tariff)
    db.add(site); db.flush()
    now = datetime.utcnow()
    s = M.ParkingSession(site_id=site.id, plate_number=plate, entry_time=now - timedelta(minutes=59),
                         status='AWAITING_PAYMENT')
    db.add(s); db.flush(); db.refresh(s)
    payment_wait.begin_wait(db, s, SL.session_fee_info(db, s, at=now), now)
    db.commit()
    return s


def user(db, name='pos.khangarid'):
    u = M.User(username=name, role='SUPER_ADMIN', password_hash='unused')
    db.add(u); db.commit()
    return u


def req(app_version=None):
    headers = [(b'x-app-version', app_version.encode())] if app_version else []
    return Request({'type': 'http', 'method': 'POST', 'path': '/', 'headers': headers,
                    'client': ('pos', 1)})


def enable(db, site_id, methods='CARD,CASH', on=True):
    A.set_site_rules(db, A.EBARIMT_KEY, site_id,
                     {'pos_terminal_issues': on, 'pos_terminal_methods': methods}, 'admin')
    db.commit()


def card(db, s, u, txn='bank-1'):
    return asyncio.run(PR.pos_confirm({'session_id': s.id, 'amount': 1000, 'transaction_id': txn},
                                      req(), db, u))


def cash(db, s, u, app_version=None):
    return asyncio.run(PR.cash_payment({'session_id': s.id}, db, u, req(app_version)))


def receipts(db, payment_id):
    return db.query(M.VatReceipt).filter_by(payment_id=payment_id).all()


def jobs(db, payment_id):
    return db.query(M.FinancialJob).filter_by(payment_id=payment_id, kind='RECEIPT').all()


# ── 1. Тохиргоо УНТРААЛТТАЙ — хуучин зан: msgbill баримт + ажил ──────────────
def test_setting_off_keeps_msgbill_receipt_and_job(db):
    s, u = parking(db), user(db)
    out = card(db, s, u)
    p = db.get(M.Payment, out['payment_id'])
    assert p.source == 'POSAPP'                       # /pos/confirm = үргэлж POS апп
    [r] = receipts(db, p.id)
    assert (r.provider, r.status) == ('MSGBILL', 'PENDING')
    [j] = jobs(db, p.id)
    assert j.status == 'PENDING' and j.payload['provider'] == 'MSGBILL'
    assert out['receipt']['ebarimt_status'] == 'PENDING'
    # outbox 2–5с — ДДТД хүлээгдэж буйг шударгаар хэвлэнэ («ДДТД: -» биш)
    assert 'ДДТД: хүлээгдэж байна' in out['print_data']['lines']


def test_default_is_off_for_every_site(db):
    assert A.get_rules(db, A.EBARIMT_KEY)['pos_terminal_issues'] is False
    assert A.pos_terminal_receipt_methods(db, 'any-site') == set()


# ── 2. Асаалттай + POS карт → EXTERNAL, ажилгүй ────────────────────────────
def test_setting_on_pos_card_is_external_without_job(db):
    s, u = parking(db), user(db)
    enable(db, s.site_id)
    out = card(db, s, u)
    p = db.get(M.Payment, out['payment_id'])
    [r] = receipts(db, p.id)
    assert (r.provider, r.status, r.ebarimt_id) == ('POS-TERMINAL', 'EXTERNAL', None)
    assert r.receipt_url == J.TERMINAL_NOTE and r.ddtd_note is None
    assert r.amount == p.amount
    assert jobs(db, p.id) == [] and db.query(M.FinancialJob).count() == 0
    # Хэвлэх: «ДДТД: -» биш, терминалын мөр; төлөв EXTERNAL
    lines = out['print_data']['lines']
    assert 'НӨАТ баримт: POS терминал (Ontime) гаргасан' in lines
    assert not any(ln.startswith('ДДТД') for ln in lines)
    assert out['receipt']['ebarimt_status'] == 'EXTERNAL' and out['ebarimt_id'] is None
    assert out['receipt']['ebarimt_note'] == J.TERMINAL_NOTE


def test_app_sent_ebarimt_id_keeps_existing_external_path(db):
    """Апп терминалын ДДТД-г илгээвэл (урт хугацааны зөв шийдэл) — тэр нь SENT."""
    s, u = parking(db), user(db)
    enable(db, s.site_id)
    body = {'session_id': s.id, 'amount': 1000, 'transaction_id': 'bank-x',
            'ebarimt_id': '029100244106001097210230010045912', 'lottery_code': 'XA 1',
            'ebarimt_provider': 'ONTIME'}
    out = asyncio.run(PR.pos_confirm(body, req('1.4.0'), db, u))
    [r] = receipts(db, out['payment_id'])
    assert (r.provider, r.status, r.ebarimt_id) == ('ONTIME', 'SENT', body['ebarimt_id'])
    assert jobs(db, out['payment_id']) == []


# ── 3. Асаалттай + веб касс (толгойгүй) бэлэн → msgbill хэвээр ─────────────
def test_setting_on_web_cash_without_header_still_msgbill(db):
    s, u = parking(db), user(db, 'online.operator')
    enable(db, s.site_id)
    out = cash(db, s, u)
    p = db.get(M.Payment, out['payment_id'])
    assert p.source is None
    [r] = receipts(db, p.id)
    assert (r.provider, r.status) == ('MSGBILL', 'PENDING') and len(jobs(db, p.id)) == 1
    audit = db.query(M.AuditLog).filter_by(action='CASH_PAYMENT').one()
    assert audit.detail['source'] is None and audit.detail['app_version'] is None


# ── 4. Асаалттай + POS аппын бэлэн (толгойтой) → EXTERNAL ──────────────────
def test_setting_on_pos_app_cash_with_header_is_external(db):
    s, u = parking(db), user(db)
    enable(db, s.site_id)
    out = cash(db, s, u, app_version='1.4.0')
    p = db.get(M.Payment, out['payment_id'])
    assert p.source == 'POSAPP' and p.raw_payload['app_version'] == '1.4.0'
    [r] = receipts(db, p.id)
    assert (r.provider, r.status) == ('POS-TERMINAL', 'EXTERNAL') and jobs(db, p.id) == []
    assert db.query(M.AuditLog).filter_by(action='CASH_PAYMENT').one().detail['source'] == 'POSAPP'


def test_other_site_unaffected(db):
    s, u = parking(db), user(db)
    other = parking(db, code='MONNIS', plate='5678УБА')
    enable(db, other.site_id)                 # зөвхөн Моннист асаав
    out = cash(db, s, u, app_version='1.4.0')
    [r] = receipts(db, out['payment_id'])
    assert r.provider == 'MSGBILL'


# ── 5. Аргын шүүлтүүр ──────────────────────────────────────────────────────
def test_method_filter_card_only(db):
    s1, u = parking(db), user(db)
    s2 = M.ParkingSession(site_id=s1.site_id, plate_number='9999УБА',
                          entry_time=datetime.utcnow() - timedelta(minutes=59), status='AWAITING_PAYMENT')
    db.add(s2); db.flush(); db.refresh(s2)
    payment_wait.begin_wait(db, s2, SL.session_fee_info(db, s2), datetime.utcnow()); db.commit()
    enable(db, s1.site_id, methods='CARD')
    c_out = card(db, s1, u)
    k_out = cash(db, s2, u, app_version='1.4.0')
    assert receipts(db, c_out['payment_id'])[0].status == 'EXTERNAL'
    assert receipts(db, k_out['payment_id'])[0].provider == 'MSGBILL'


def test_methods_are_validated_and_normalized(db):
    vals = PRULES.clamp_values(A.EBARIMT_KEY, {'pos_terminal_methods': ' cash, card '})
    assert vals == {'pos_terminal_methods': 'CARD,CASH'}
    site = 'site-x'
    A.set_site_rules(db, A.EBARIMT_KEY, site, {'pos_terminal_issues': True,
                                                'pos_terminal_methods': 'QR'}, 'admin')
    db.commit()
    # Танигдахгүй арга хадгалагдахгүй → асаалттай үед анхдагч CARD+CASH
    assert A.pos_terminal_receipt_methods(db, site) == {'CARD', 'CASH'}
    A.set_site_rules(db, A.EBARIMT_KEY, site, {'pos_terminal_methods': 'CASH'}, 'admin'); db.commit()
    assert A.pos_terminal_receipt_methods(db, site) == {'CASH'}


def test_settings_endpoint_is_per_site_and_audited(db):
    from app.routers import admin_router as AR
    s, u = parking(db), user(db, 'admin')
    out = AR.payment_rules_site_save(s.site_id, {'ebarimt_rules': {
        'pos_terminal_issues': True, 'pos_terminal_methods': 'cash,card'}}, db, u)
    row = {r['key']: r for r in out['rules'] if r['group'] == A.EBARIMT_KEY}
    assert row['pos_terminal_issues']['value'] is True and row['pos_terminal_issues']['source'] == 'site'
    assert row['pos_terminal_methods']['value'] == 'CARD,CASH'
    assert row['pos_terminal_methods']['choices']
    log = db.query(M.AuditLog).filter_by(entity='payment_rules', entity_id=s.site_id).one()
    assert log.detail['ebarimt_rules']['pos_terminal_issues'] is True
    assert A.get_rules(db, A.EBARIMT_KEY)['pos_terminal_issues'] is False   # ерөнхий хөндөгдөөгүй


# ── 6. Retry/бөөн нөхөлт/ажил EXTERNAL-ийг хөндөхгүй ───────────────────────
def external_payment(db):
    s, u = parking(db), user(db)
    enable(db, s.site_id)
    out = card(db, s, u)
    return db.get(M.Payment, out['payment_id']), u, s


def test_retry_refuses_external_with_409(db):
    p, u, _ = external_payment(db)
    with pytest.raises(HTTPException) as e:
        asyncio.run(PR.retry_ebarimt_endpoint(p.id, {}, db, u))
    assert e.value.status_code == 409 and 'POS терминал' in e.value.detail
    db.rollback()
    # Тохиргоо асаалттай байхад «Систем баримт гаргах» ч татгалзана
    with pytest.raises(HTTPException) as e:
        asyncio.run(PR.retry_ebarimt_endpoint(p.id, {'reissue_external': True}, db, u))
    assert e.value.status_code == 409 and 'асаалттай' in e.value.detail
    db.rollback()
    assert receipts(db, p.id)[0].status == 'EXTERNAL' and db.query(M.FinancialJob).count() == 0
    assert J.retry_payment_receipts(db, p)['external'] is True
    db.rollback()


def test_reissue_only_after_setting_off_and_explicit_request(db):
    p, u, s = external_payment(db)
    enable(db, s.site_id, on=False)
    with pytest.raises(HTTPException) as e:        # тодорхой хүсэлтгүй бол унтраасан ч 409
        asyncio.run(PR.retry_ebarimt_endpoint(p.id, {}, db, u))
    assert e.value.status_code == 409
    db.rollback()
    res = asyncio.run(PR.retry_ebarimt_endpoint(p.id, {'reissue_external': True}, db, u))
    assert res['pending'] is True
    [r] = receipts(db, p.id)
    assert (r.provider, r.status) == ('MSGBILL', 'PENDING')
    [j] = jobs(db, p.id)
    assert j.receipt_id == r.id and j.payload['provider'] == 'MSGBILL' and j.status == 'PENDING'
    assert db.query(M.AuditLog).filter_by(action='EBARIMT_EXTERNAL_REISSUE').count() == 1


def test_worker_never_sends_external_receipt(db, monkeypatch):
    p, _, _ = external_payment(db)
    [r] = receipts(db, p.id)
    # Хэн нэгэн гараар ажил үүсгэсэн ч (DB засвар г.м) сувагт илгээхгүй
    db.add(M.FinancialJob(job_key=f'receipt:{r.id}', kind='RECEIPT', payment_id=p.id, receipt_id=r.id,
                          payload={'provider': 'MSGBILL', 'amount': '1000', 'vat': '91',
                                   'method': 'CARD', 'customer_tin': None, 'receiver_type': 'CITIZEN',
                                   'description': 'x', 'idempotency_key': 'k'}))
    db.commit()
    create = AsyncMock(side_effect=AssertionError('msgbill POST for EXTERNAL'))
    monkeypatch.setattr(J.msgbill, 'create_receipt', create)
    asyncio.run(J.run_once(factory=lambda: Session(db.bind, autoflush=False)))
    db.expire_all()
    assert create.await_count == 0
    assert db.query(M.FinancialJob).one().status == 'DONE'
    assert receipts(db, p.id)[0].status == 'EXTERNAL'


def test_reissue_reuses_job_left_done_by_worker_guard(db, monkeypatch):
    p, u, s = external_payment(db)
    [r] = receipts(db, p.id)
    db.add(M.FinancialJob(job_key=f'receipt:{r.id}', kind='RECEIPT', payment_id=p.id, receipt_id=r.id,
                          payload={'provider': 'MSGBILL'}, status='DONE'))
    db.commit()
    enable(db, s.site_id, on=False)
    asyncio.run(PR.retry_ebarimt_endpoint(p.id, {'reissue_external': True}, db, u))
    [j] = jobs(db, p.id)                            # job_key unique — шинэ мөр биш, дахин ашиглав
    assert j.status == 'PENDING' and j.payload['idempotency_key'] == f'external-{r.id}'
    assert '1234УБА' in j.payload['description']


def test_bulk_retry_skips_external(db, monkeypatch):
    import app.database as D
    p, _, _ = external_payment(db)
    monkeypatch.setattr(D, 'SessionLocal', lambda: Session(db.bind, autoflush=False))
    R._bulk_job.clear()
    R._bulk_job.update({'running': True, 'total': 1, 'done': 0, 'ok': 0, 'skipped': 0,
                        'failed': 0, 'errors': {}, 'username': 't'})
    monkeypatch.setattr(asyncio, 'sleep', AsyncMock())
    asyncio.run(R._run_bulk_retry([p.id]))
    assert R._bulk_job['skipped'] == 1 and R._bulk_job['failed'] == 0
    assert R._bulk_job['external_skipped'] == 1
    db.expire_all()
    assert db.query(M.FinancialJob).count() == 0 and receipts(db, p.id)[0].status == 'EXTERNAL'
    R._bulk_job.clear()


def test_cancel_skips_external(db):
    p, _, _ = external_payment(db)
    res = asyncio.run(PR.cancel_ebarimt(db, p, 'давхар'))
    assert res['ok'] is False and res['external'] is True and 'Ontime' in res['error']
    assert receipts(db, p.id)[0].status == 'EXTERNAL'


def test_attach_adopts_external_row(db):
    p, u, _ = external_payment(db)
    res = asyncio.run(PR.attach_external_ebarimt(p.id, {'ebarimt_id': 'ONTIME-DDTD-1',
                                                        'ebarimt_provider': 'ONTIME'}, db, u))
    assert res['ok'] and res['ebarimt_id'] == 'ONTIME-DDTD-1'
    [r] = receipts(db, p.id)                       # шинэ мөр нэмээгүй — EXTERNAL нь SENT болов
    assert (r.status, r.provider, r.ebarimt_id) == ('SENT', 'ONTIME', 'ONTIME-DDTD-1')


# ── 7. primary_receipt / хэвлэх ────────────────────────────────────────────
def _rec(status, mins, ebarimt_id=None):
    return NS(status=status, ebarimt_id=ebarimt_id, created_at=datetime(2026, 10, 13) + timedelta(minutes=mins))


def test_primary_receipt_ranks_external():
    ext, sent = _rec('EXTERNAL', 0), _rec('SENT', 5, 'D1')
    cancelled, pending, failed = _rec('CANCELLED', -5, 'OLD'), _rec('PENDING', -1), _rec('FAILED', -2)
    assert choose_primary([ext, sent]) is sent              # жинхэнэ ДДТД-тэй мөр давуу
    assert choose_primary([cancelled, ext]) is ext          # цуцалсан давхардлаас өмнө
    assert choose_primary([pending, failed, ext]) is ext    # эцсийн төлөв — түр зуурынхаас өмнө


def test_print_payload_and_pos_receipts_for_external(db):
    p, u, _ = external_payment(db)
    rows = PR.pos_recent_receipts(None, 10, db, u)
    assert rows[0]['receipt']['ebarimt_status'] == 'EXTERNAL'
    assert 'ДДТД: -' not in rows[0]['print_data']['lines']
    single = PR.payment_print(p.id, db, u)
    assert single['receipt']['ebarimt_issuer'] == 'POS_TERMINAL'


def test_print_returns_ddtd_once_outbox_delivers(db):
    """Тохиргоо унтраалттай: confirm-ийн мөчид PENDING, outbox дууссаны дараа /print
    болон /pos/receipts ДДТД-тэй (блоклох хүлээлтгүй — апп дахин татна)."""
    s, u = parking(db), user(db)
    out = card(db, s, u)
    assert out['receipt']['ebarimt_status'] == 'PENDING' and out['ebarimt_id'] is None
    [r] = receipts(db, out['payment_id'])
    r.ebarimt_id, r.status, r.lottery_code = '0301010000005595', 'SENT', 'HV 1'
    db.commit()
    again = PR.payment_print(out['payment_id'], db, u)
    assert again['ebarimt_id'] == '0301010000005595'
    assert 'ДДТД: 0301010000005595' in again['print_data']['lines']
    assert PR.pos_recent_receipts(None, 10, db, u)[0]['receipt']['ebarimt_status'] == 'SENT'


# ── 8. Өрийн төлөлт (Нөхөн төлбөр) ─────────────────────────────────────────
def debt(db, site_id):
    c = M.Compensation(site_id=site_id, plate_number='1234УБА', amount=Decimal('5000'))
    db.add(c); db.commit()
    return c


def test_compensation_pay_from_pos_app_is_external(db):
    s, u = parking(db), user(db)
    enable(db, s.site_id)
    c = debt(db, s.site_id)
    out = asyncio.run(C.pay_compensation(c.id, {'method': 'CASH'}, db, u, req('1.4.0')))
    p = db.get(M.Payment, out['payment_id'])
    assert p.source == 'POSAPP' and out['receipt_status'] == 'EXTERNAL'
    assert jobs(db, p.id) == []


def test_compensation_pay_from_web_keeps_msgbill(db):
    s, u = parking(db), user(db, 'operator')
    enable(db, s.site_id)
    c = debt(db, s.site_id)
    out = asyncio.run(C.pay_compensation(c.id, {'method': 'CASH'}, db, u, req()))
    p = db.get(M.Payment, out['payment_id'])
    assert p.source == 'POS' and out['receipt_status'] == 'PENDING'
    assert receipts(db, p.id)[0].provider == 'MSGBILL' and len(jobs(db, p.id)) == 1


def test_combined_payment_with_debts_is_one_terminal_receipt(db):
    """Терминал НИЙТ дүнгээр НЭГ баримт хэвлэдэг — өр тус бүрийн мөр хуваахгүй."""
    s, u = parking(db), user(db)
    enable(db, s.site_id)
    c = debt(db, s.site_id)
    p = M.Payment(session_id=s.id, provider='POS', payment_method='CARD', source='POSAPP',
                  sender_invoice_no='combo-1', amount=Decimal('6000'), vat_amount=Decimal('545'),
                  status='PENDING', fee_snapshot={'parking_amount': 1000.0,
                                                  'debts': [{'id': c.id, 'amount': 5000.0}]})
    db.add(p); db.commit()
    asyncio.run(PR._finalize_paid(db, p))
    [r] = receipts(db, p.id)
    assert r.status == 'EXTERNAL' and r.amount == Decimal('6000')
    assert db.get(M.Compensation, c.id).status == 'PAID'
    assert db.query(M.FinancialJob).count() == 0


# ── 9. Тайлан: ТЕГ тулгалт, Excel ──────────────────────────────────────────
def test_reconcile_explains_terminal_rows():
    t = datetime(2026, 8, 24, 10, 0, 0)
    left = [{'dt': t, 'amount': 2000.0, 'src': 'f', 'ddtd': 'X10002990'}]
    probe = [{'id': 'p1', 'paid_at': t, 'amount': 2000.0, 'site_id': 's', 'plate': '1',
              'site_name': 'Хангарьд', 'provider': 'POS', 'method': 'CARD', 'receipts': [],
              'terminal': True}]
    [row] = V.explain_unmatched_tax(left, probe, 0, 3, set(), None)
    assert row['verdict'] == 'TERMINAL' and 'TERMINAL' in V.VERDICTS
    probe[0]['terminal'] = False
    assert V.explain_unmatched_tax(left, probe, 0, 3, set(), None)[0]['verdict'] == 'PAYMENT_NO_RECEIPT'


def test_excel_counts_external_separately():
    from openpyxl import load_workbook
    mk = lambda st, prov, amt, url=None: NS(status=st, provider=prov, amount=amt, vat_amount=0,
                                            ebarimt_id=None if st == 'EXTERNAL' else 'D', lottery_code=None,
                                            created_at=datetime(2026, 10, 13), customer_tin=None,
                                            receipt_url=url)
    rows = [(mk('SENT', 'MSGBILL', 1000), '1', 'A'),
            (mk('EXTERNAL', 'POS-TERMINAL', 2000, J.TERMINAL_NOTE), '2', 'A')]
    body = asyncio.run(_collect(X.vat_receipts_excel(rows)))   # StreamingResponse
    ws = load_workbook(BytesIO(body)).active
    values = [[c.value for c in r] for r in ws.iter_rows()]
    assert values[2][8] == 'POS терминал гаргасан' and values[2][7] == 'POS терминал (Ontime)'
    assert values[2][10] == J.TERMINAL_NOTE
    assert 'POS терминал гаргасан: 1 мөр' in values[-1][10]


async def _collect(resp):
    out = b''
    async for chunk in resp.body_iterator:
        out += chunk if isinstance(chunk, bytes) else chunk.encode()
    return out
