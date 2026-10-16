"""Байгууллагын НӨАТ баримт (2026-10-16 гомдол «байгууллагаар НӨАТ гаргахад алдаа»).

F1 — /cash, /transfer, өрийн төлөлт, /qpay/invoice апп илгээдэг `payer_reg_no`-ийг уншдаггүй
     байв (зөвхөн customer_tin): audit1014 — бэлэн 0/6,239, дансаар 0/932 байгууллагын баримттай.
F2 — outbox зөвхөн алдааны ангийн нэрийг («HTTPStatusError») хадгалж, QPay/msgbill-ийн ТОДОРХОЙ
     татгалзлыг UNKNOWN/REVIEW болгодог байв → «Дугаар засах»/«Иргэнээр» хэзээ ч гардаггүй.
F3 — retry-ebarimt хүлээн авагч солихыг attempts>0 үед ҮРГЭЛЖ 409 — цуцалсны дараа ч засах замгүй.
Хяналт (2026-10-16) — мөрийн тоо/дүнгийн хамгаалалт (нэг төлбөрт 2 бүтэн баримт), create-ийн 409 /
«аль хэдийн үүссэн» тодорхойгүй, QPay цуцалсан ДДТД буцаах, наалдамтгай тодорхойгүй тэмдэг, терминалын
дүрэм ЗӨВХӨН COMPANY-д, safe_text, REJECTED_RECEIVER дахин илгээхгүй, QPay иргэний регистр, UI туг,
/qpay/invoice ТТД → иргэн + buyer_warning.

Сүлжээ огт дуудахгүй — QPay/msgbill бүгд stub; DB = SQLite (санах ой).
"""
import asyncio
import json
from datetime import datetime, timedelta
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
from app.services import app_settings as A, ebarimt, financial_jobs as J, payment_wait, pos_app as PA
from app.services.msgbill import MsgbillAccount, MsgbillError

ACC = MsgbillAccount(api_key='synthetic-key', base_url='https://msgbill.invalid')
DDTD = '015200020090001097760067510045952'
DDTD2 = '015200020090001097760067510045953'
QDDTD = '030101065006001097760067510014863'


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
    J._qr_failed.clear(); J._qr_fetching.clear(); J._INFLIGHT.clear()
    yield
    ebarimt._qr_cache.clear()
    J._qr_failed.clear(); J._qr_fetching.clear(); J._INFLIGHT.clear()


@pytest.fixture
def db():
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    A.invalidate_cache()
    with Session(engine, autoflush=False) as session:
        yield session
    A.invalidate_cache()
    engine.dispose()


def site(db):
    tariff = M.TariffTemplate(name='Hourly', free_minutes=0, extra_hour_price=1000, daily_cap=50000)
    st = M.ParkingSite(name='100 айлын зогсоол', site_code='100OROS', tariff_template=tariff)
    db.add(st); db.flush()
    return st


def parking(db, plate='1234УБА', st=None):
    st = st or site(db)
    now = datetime.utcnow()
    s = M.ParkingSession(site_id=st.id, plate_number=plate, entry_time=now - timedelta(minutes=59),
                         status='AWAITING_PAYMENT')
    db.add(s); db.flush(); db.refresh(s)
    payment_wait.begin_wait(db, s, SL.session_fee_info(db, s, at=now), now)
    db.commit()
    return s


def user(db, name='admin', role='SUPER_ADMIN'):
    u = M.User(username=name, role=role, password_hash='unused')
    db.add(u); db.commit()
    return u


def req(app_version='1.6.0'):
    headers = [(b'x-app-version', app_version.encode())] if app_version else []
    return Request({'type': 'http', 'method': 'POST', 'path': '/', 'headers': headers,
                    'client': ('pos', 1)})


def created(no=DDTD, rid='rcp-1', lottery=None):
    return {'status': 'SUCCESS', 'billId': no, 'lottery': lottery, 'qrData': 'QR-' + no[-4:],
            'msgbillId': rid, 'state': 'CREATED', 'raw': {'id': rid, 'receipt_no': no, 'state': 'CREATED'}}


def http_error(status, body, url='https://merchant.qpay.mn/v2/ebarimt_v3/create'):
    request = httpx.Request('POST', url)
    response = httpx.Response(status, json=body, request=request) if not isinstance(body, str) \
        else httpx.Response(status, text=body, request=request)
    return httpx.HTTPStatusError(f'HTTP {status}', request=request, response=response)


def factory(db):
    return lambda: Session(db.bind, autoflush=False)


def run_outbox(db, n=1):
    for _ in range(n):
        db.expire_all()
        for j in db.query(M.FinancialJob).filter(M.FinancialJob.status == 'RETRY').all():
            j.next_attempt_at = datetime.utcnow() - timedelta(seconds=1)
        db.commit()
        asyncio.run(J.run_once(factory=factory(db)))
    db.expire_all()


def rows(db, model, **kw):
    db.expire_all()
    return db.query(model).filter_by(**kw).order_by(model.created_at, model.id).all()


def paid(db, *, provider='CASH', method='CASH', tin='6853959', rtype='COMPANY', qpay_id=None):
    s = parking(db)
    p = M.Payment(session_id=s.id, provider=provider, payment_method=method, amount=1000, vat_amount=91,
                  status='PAID', paid_at=datetime.utcnow(), sender_invoice_no=f'co-{provider}',
                  provider_payment_id=qpay_id, customer_tin=tin, ebarimt_receiver_type=rtype)
    db.add(p); db.flush()
    J.enqueue_receipt(db, p, description='Зогсоолын үйлчилгээ · 1234УБА')
    db.commit()
    return p


def qpay_paid(db, monkeypatch, tin='6853959'):
    monkeypatch.setattr(J.settings, 'qpay_ebarimt', True)
    return paid(db, provider='QPAY', method='QR', tin=tin, qpay_id='781636913529316')


def active(db, pid):
    return [r for r in rows(db, M.VatReceipt, payment_id=pid) if r.status != 'CANCELLED']


# ═════════════════════════════════ F1 ═════════════════════════════════
def test_cash_payer_reg_no_issues_company_receipt_and_echoes_buyer(db, monkeypatch):
    create = AsyncMock(return_value=created())
    monkeypatch.setattr(J.msgbill, 'create_receipt', create)
    s, u = parking(db), user(db, 'pos.oros', 'POS')
    out = asyncio.run(PR.cash_payment({'session_id': s.id, 'payer_reg_no': '5037409'}, db, u, req()))
    p = db.get(M.Payment, out['payment_id'])
    assert (p.customer_tin, p.ebarimt_receiver_type) == ('5037409', 'COMPANY')
    assert create.await_args.kwargs['payer_reg_no'] == '5037409'
    assert out['receipt_type'] == 'ORGANIZATION' and out['payer_reg_no'] == '5037409'
    assert out['receipt']['receipt_type'] == 'ORGANIZATION' and out['receipt']['payer_reg_no'] == '5037409'
    [r] = rows(db, M.VatReceipt, payment_id=p.id)
    assert r.status == 'SENT' and r.customer_tin == '5037409' and r.lottery_code is None
    [job] = rows(db, M.FinancialJob, payment_id=p.id)
    assert job.payload['receiver_type'] == 'COMPANY' and job.payload['customer_tin'] == '5037409'


@pytest.mark.parametrize('body,expected', [
    ({'customer_tin': '1234567'}, ('1234567', 'COMPANY')),                 # хуучин талбар
    ({'payer_reg_no': '71101242183'}, ('71101242183', 'COMPANY')),         # ТТД (msgbill)
    ({'payer_reg_no': 'аа00112233'}, ('АА00112233', 'CITIZEN')),           # иргэний нэрийн баримт
    ({'payer_reg_no': '12345'}, (None, 'CITIZEN')),                        # танигдахгүй — алдаагүй
    ({}, (None, 'CITIZEN')),
])
def test_cash_buyer_same_rules_as_pos_confirm(db, monkeypatch, body, expected):
    monkeypatch.setattr(J.msgbill, 'create_receipt', AsyncMock(return_value=created()))
    s, u = parking(db), user(db)
    out = asyncio.run(PR.cash_payment({'session_id': s.id, **body}, db, u, req(None)))
    p = db.get(M.Payment, out['payment_id'])
    assert (p.customer_tin, p.ebarimt_receiver_type) == expected
    assert PR._buyer(body) == expected
    assert out['payer_reg_no'] == expected[0]


def test_transfer_reads_payer_reg_no(db, monkeypatch):
    monkeypatch.setattr(J.msgbill, 'create_receipt', AsyncMock(return_value=created()))
    s, u = parking(db), user(db)
    out = asyncio.run(PR.transfer_payment({'session_id': s.id, 'payer_reg_no': '2702673'}, db, u))
    p = db.get(M.Payment, out['payment_id'])
    assert (p.customer_tin, p.ebarimt_receiver_type) == ('2702673', 'COMPANY')
    assert out['receipt_type'] == 'ORGANIZATION' and out['payer_reg_no'] == '2702673'
    [job] = rows(db, M.FinancialJob, payment_id=p.id)
    assert job.payload['receiver_type'] == 'COMPANY'


def test_compensation_pay_reads_payer_reg_no(db, monkeypatch):
    st = site(db)
    u = user(db)
    comp = M.Compensation(site_id=st.id, plate_number='1234УБА', amount=1500)
    db.add(comp); db.commit()
    out = asyncio.run(C.pay_compensation(comp.id, {'method': 'CASH', 'payer_reg_no': '6283993'}, db, u))
    p = db.get(M.Payment, out['payment_id'])
    assert (p.customer_tin, p.ebarimt_receiver_type) == ('6283993', 'COMPANY')
    assert out['receipt_type'] == 'ORGANIZATION' and out['payer_reg_no'] == '6283993'
    [job] = rows(db, M.FinancialJob, payment_id=p.id)
    assert job.payload['receiver_type'] == 'COMPANY' and job.payload['customer_tin'] == '6283993'
    # Хуучин зан: дурын customer_tin-ийг COMPANY гэж үздэг байсан — одоо /pos/confirm-ийн дүрэм
    comp2 = M.Compensation(site_id=st.id, plate_number='5555УБА', amount=1500)
    db.add(comp2); db.commit()
    out2 = asyncio.run(C.pay_compensation(comp2.id, {'method': 'CASH', 'customer_tin': 'abc'}, db, u))
    p2 = db.get(M.Payment, out2['payment_id'])
    assert (p2.customer_tin, p2.ebarimt_receiver_type) == (None, 'CITIZEN')


def _invoice_stub(monkeypatch):
    calls = []

    async def create_invoice(*a, **k):
        calls.append(k)
        return {'invoice_id': f'INV-{len(calls)}', 'qr_text': 'qr', 'qr_image': '',
                'deep_link': '', 'urls': [], 'mock': True}
    monkeypatch.setattr(PR.qpay, 'create_invoice', create_invoice)
    return calls


def test_qpay_invoice_company_requires_7_digit_register(db, monkeypatch):
    calls = _invoice_stub(monkeypatch)
    s = parking(db)
    out = asyncio.run(PR.qpay_invoice({'session_id': s.id, 'payer_reg_no': '5037409'}, req(None), db))
    p = db.get(M.Payment, out['payment_id'])
    assert (p.customer_tin, p.ebarimt_receiver_type) == ('5037409', 'COMPANY')
    assert calls[0]['receiver_data'] == {'register': '5037409'}
    assert out['receipt_type'] == 'ORGANIZATION' and out['payer_reg_no'] == '5037409'
    assert 'buyer_warning' not in out
    # ИЛ «байгууллага» (жолоочийн QR хуудас receiver_type=COMPANY) + 7 оронтой биш customer_tin —
    # QPay MAX_LENGTH(7)-оор 100% унана → нэхэмжлэл үүсгэхээс ӨМНӨ 422 (засаад дахин)
    s2 = parking(db, '5555УБА', st=db.get(M.ParkingSite, s.site_id))
    for body in ({'customer_tin': '71101242183', 'receiver_type': 'COMPANY'},
                 {'customer_tin': '12345', 'receiver_type': 'COMPANY'}):
        with pytest.raises(HTTPException) as e:
            asyncio.run(PR.qpay_invoice({'session_id': s2.id, **body}, req(None), db))
        assert e.value.status_code == 422 and '7 оронтой' in e.value.detail
        db.rollback()
    assert db.query(M.Payment).filter_by(session_id=s2.id).count() == 0 and len(calls) == 1
    # Иргэний регистр — QPay-д дамжуулах боломжгүй → нэргүй иргэн + анхааруулга
    out3 = asyncio.run(PR.qpay_invoice({'session_id': s2.id, 'payer_reg_no': 'АА00112233'}, req(None), db))
    p3 = db.get(M.Payment, out3['payment_id'])
    assert (p3.customer_tin, p3.ebarimt_receiver_type) == (None, 'CITIZEN')
    assert calls[-1]['receiver_data'] is None and out3['receipt_type'] == 'CITIZEN'
    assert 'иргэний регистр' in out3['buyer_warning']


@pytest.mark.parametrize('body,needle', [
    ({'payer_reg_no': '15200020090', 'source': 'POS'}, 'ТТД (15200020090)'),   # POS апп ТТД илгээв
    ({'payer_reg_no': '12345'}, 'танигдсангүй'),
    ({'customer_tin': '71101242183'}, 'ТТД (71101242183)'),     # receiver_type-гүй хуучин талбар
    ({'customer_tin': 'abc'}, 'танигдсангүй'),
])
def test_qpay_invoice_tin_or_unknown_never_blocks_payment(db, monkeypatch, body, needle):
    """Хяналт #10: ТТД/танигдахгүй дугаар QR төлбөрийг 422-оор ХААДАГ байв — одоо нэргүй иргэн +
    buyer_warning (төлбөр авах нь баримтын төрлөөс чухал)."""
    calls = _invoice_stub(monkeypatch)
    s = parking(db)
    out = asyncio.run(PR.qpay_invoice({'session_id': s.id, **body}, req(None), db))
    p = db.get(M.Payment, out['payment_id'])
    assert (p.customer_tin, p.ebarimt_receiver_type) == (None, 'CITIZEN') and len(calls) == 1
    assert calls[0]['receiver_data'] is None and out['receipt_type'] == 'CITIZEN'
    assert needle in out['buyer_warning']


def test_qpay_invoice_reuse_applies_buyer_but_empty_request_keeps_it(db, monkeypatch):
    calls = _invoice_stub(monkeypatch)
    s = parking(db)
    first = asyncio.run(PR.qpay_invoice({'session_id': s.id}, req(None), db))
    assert first['receipt_type'] == 'CITIZEN'
    # Жолооч «Буцах» → «Байгууллага» → ижил нэхэмжлэл дахин ашиглагдана, хүлээн авагч шинэчлэгдэнэ
    again = asyncio.run(PR.qpay_invoice({'session_id': s.id, 'customer_tin': '5037409'}, req(None), db))
    assert again['payment_id'] == first['payment_id'] and len(calls) == 1
    p = db.get(M.Payment, first['payment_id'])
    assert (p.customer_tin, p.ebarimt_receiver_type) == ('5037409', 'COMPANY')
    assert again['receipt_type'] == 'ORGANIZATION'
    # Хуудас сэргээх (хоосон хүсэлт) байгууллагыг ДАРАХГҮЙ
    asyncio.run(PR.qpay_invoice({'session_id': s.id}, req(None), db))
    db.expire_all()
    assert db.get(M.Payment, first['payment_id']).ebarimt_receiver_type == 'COMPANY'


# ═════════════════════════════════ F2 ═════════════════════════════════
CUSTOMER_TIN_400 = {'error': 'EBARIMT_ERROR',
                    'message': 'receipt.customerTin талбарын утга буруу байна. /Шалгасан нөхцөл: [0-9]{11,14}/'}


def test_qpay_400_customer_tin_is_definite_receiver_rejection(db, monkeypatch):
    p = qpay_paid(db, monkeypatch)
    create = AsyncMock(side_effect=http_error(400, CUSTOMER_TIN_400))
    monkeypatch.setattr(J.qpay, 'create_ebarimt', create)
    run_outbox(db, 2)
    [rec] = rows(db, M.VatReceipt, payment_id=p.id)
    [job] = rows(db, M.FinancialJob, payment_id=p.id)
    assert rec.status == 'FAILED' and job.status == 'REJECTED'
    assert rec.raw['error']['class'] == 'REJECTED_RECEIVER' and rec.raw['error']['http_status'] == 400
    assert 'QPay HTTP 400' in rec.receipt_url and 'customerTin' in rec.receipt_url
    assert job.last_error == rec.receipt_url and create.await_count == 1   # автоматаар давтахгүй
    vat = R.vat_receipts('2020-01-01', '2030-01-01', db=db, user=user(db))
    assert vat[0]['error_class'] == 'REJECTED_RECEIVER'
    audit = db.query(M.AuditLog).filter_by(action='FINANCIAL_JOB_RESULT').one()
    assert audit.detail['error_class'] == 'REJECTED_RECEIVER' and audit.detail['http_status'] == 400


def test_qpay_max_length_dict_message_is_receiver_rejection(db, monkeypatch):
    p = qpay_paid(db, monkeypatch, tin='71101242183')
    body = {'message': {'ebarimt_receiver': {'type': 'MAX_LENGTH', 'message': 'String max length (7)!'}}}
    monkeypatch.setattr(J.qpay, 'create_ebarimt', AsyncMock(side_effect=http_error(400, body)))
    run_outbox(db)
    [rec] = rows(db, M.VatReceipt, payment_id=p.id)
    assert rec.status == 'FAILED' and J.error_class(rec) == 'REJECTED_RECEIVER'
    assert isinstance(rec.receipt_url, str) and 'MAX_LENGTH' in rec.receipt_url


def test_qpay_merchant_tenant_list_rejection_is_not_receiver(db, monkeypatch):
    p = qpay_paid(db, monkeypatch)
    msg = ("'30101065006' ТТД бүхий ААН-н түрээслэгчийн жагсаалтанд '15200020090' ТТД "
           "бүртгэлгүй байна.")
    monkeypatch.setattr(J.qpay, 'create_ebarimt', AsyncMock(side_effect=http_error(400, {'message': msg})))
    run_outbox(db)
    [rec] = rows(db, M.VatReceipt, payment_id=p.id)
    [job] = rows(db, M.FinancialJob, payment_id=p.id)
    assert rec.status == 'FAILED' and job.status == 'REJECTED' and J.error_class(rec) == 'REJECTED'
    assert 'түрээслэгчийн жагсаалтанд' in rec.receipt_url


def test_msgbill_422_is_definite_rejection_not_retried(db, monkeypatch):
    p = paid(db)
    err = MsgbillError('msgbill 422: Худалдан авагчийн регистр буруу', code='INVALID_PAYER_REG_NO', status=422)
    create = AsyncMock(side_effect=err)
    monkeypatch.setattr(J.msgbill, 'create_receipt', create)
    run_outbox(db, 2)
    [rec] = rows(db, M.VatReceipt, payment_id=p.id)
    [job] = rows(db, M.FinancialJob, payment_id=p.id)
    assert rec.status == 'FAILED' and job.status == 'REJECTED' and create.await_count == 1
    assert J.error_class(rec) == 'REJECTED_RECEIVER'
    assert 'msgbill 422' in rec.receipt_url and 'INVALID_PAYER_REG_NO' in rec.receipt_url


@pytest.mark.parametrize('exc,text', [
    (httpx.ReadTimeout('response lost'), 'ReadTimeout'),
    (http_error(502, '<html>Bad gateway</html>'), 'QPay HTTP 502'),
    (http_error(409, {'message': 'ebarimt already exists for payment'}), 'QPay HTTP 409'),
    (http_error(409, {'error': 'EBARIMT_ALREADY_CREATED', 'message': 'Ebarimt was created'}), 'QPay HTTP 409'),
    (http_error(409, {'message': 'conflict'}), 'QPay HTTP 409'),                 # 409 дангаараа ч
    (http_error(400, {'message': 'Энэ төлбөрт И-баримт үүссэн байна'}), 'QPay HTTP 400'),
])
def test_qpay_uncertain_outcomes_stay_review(db, monkeypatch, exc, text):
    p = qpay_paid(db, monkeypatch)
    create = AsyncMock(side_effect=exc)
    monkeypatch.setattr(J.qpay, 'create_ebarimt', create)
    run_outbox(db, 2)
    [rec] = rows(db, M.VatReceipt, payment_id=p.id)
    [job] = rows(db, M.FinancialJob, payment_id=p.id)
    assert rec.status == 'REVIEW' and job.status == 'UNKNOWN' and create.await_count == 1
    assert rec.receipt_url.startswith(text) and J.error_class(rec) == 'UNCERTAIN'
    # «Дахин үүсгэх» тодорхойгүй үр дүнг давтахгүй (QPay create идемпотент биш)
    assert asyncio.run(PR.retry_ebarimt(db, db.get(M.Payment, p.id)))['review_required']
    run_outbox(db)
    assert create.await_count == 1


def test_msgbill_5xx_and_get_404_keep_safe_retry(db, monkeypatch):
    p = paid(db)
    monkeypatch.setattr(J.msgbill, 'create_receipt', AsyncMock(side_effect=MsgbillError(
        'msgbill 503: түр саатал', code='UNAVAILABLE', status=503)))
    run_outbox(db)
    [rec] = rows(db, M.VatReceipt, payment_id=p.id)
    [job] = rows(db, M.FinancialJob, payment_id=p.id)
    assert job.status == 'RETRY' and rec.status == 'PENDING' and 'msgbill 503' in job.last_error
    # provider_ref мэдэгдэж буй үед GET-ийн 404 нь баримт үүсээгүйг БАТЛАХГҮЙ — татгалзал биш
    rec.provider_ref = 'rcp-known'; db.commit()
    monkeypatch.setattr(J.msgbill, 'get_receipt', AsyncMock(side_effect=MsgbillError(
        'msgbill 404: receipt not found', code='RECEIPT_NOT_FOUND', status=404)))
    run_outbox(db)
    [job] = rows(db, M.FinancialJob, payment_id=p.id)
    assert job.status == 'RETRY' and J.error_class(rows(db, M.VatReceipt, payment_id=p.id)[0]) == 'UNCERTAIN'


def test_error_text_is_truncated_and_sanitized(db, monkeypatch):
    p = qpay_paid(db, monkeypatch)
    # «message»-гүй хариу — бие бүхэлдээ JSON болж хадгалагдана; нууц эхэнд нь
    body = {'Authorization': 'Bearer eyJhbGciOi.secretpart.sig', 'api_key': 'bsk_live_abcdef123',
            'access_token': 'tok-123456', 'detail': 'customerTin буруу ' + 'x' * 1000}
    monkeypatch.setattr(J.qpay, 'create_ebarimt', AsyncMock(side_effect=http_error(400, body)))
    run_outbox(db)
    [rec] = rows(db, M.VatReceipt, payment_id=p.id)
    [job] = rows(db, M.FinancialJob, payment_id=p.id)
    assert len(rec.receipt_url) <= 300 and len(rec.raw['error']['text']) <= 300
    assert rec.receipt_url.startswith('QPay HTTP 400') and 'customerTin' in rec.receipt_url
    assert J.error_class(rec) == 'REJECTED_RECEIVER'
    text = J.safe_text(json.dumps(body), limit=2000)
    for secret in ('secretpart', 'bsk_live_abcdef123', 'tok-123456', 'eyJhbGciOi'):
        assert secret not in text and secret not in rec.receipt_url and secret not in job.last_error
    assert J.safe_text({'message': {'a': 1}}) == '{"message": {"a": 1}}' and J.safe_text(None) == ''


@pytest.mark.parametrize('exc,provider,creating,cls', [
    (MsgbillError('msgbill 400: bad request', code='INVALID_TIN', status=400), 'MSGBILL', True, 'REJECTED_RECEIVER'),
    (MsgbillError('msgbill 422: amount must be positive', code='VALIDATION', status=422), 'MSGBILL', True, 'REJECTED'),
    (MsgbillError('msgbill 400: merchantTin 15200020090 is not registered on the POS API instance',
                  status=400), 'MSGBILL', True, 'REJECTED'),
    (MsgbillError('msgbill 409: key reused', code='IDEMPOTENCY_CONFLICT', status=409), 'MSGBILL', True, 'UNCERTAIN'),
    (MsgbillError('msgbill 429: квот', code='RECEIPT_QUOTA_EXCEEDED', status=429), 'MSGBILL', True, 'UNCERTAIN'),
    (http_error(400, CUSTOMER_TIN_400), 'QPAY', False, 'UNCERTAIN'),     # create биш (GET/шалгалт)
    (http_error(400, CUSTOMER_TIN_400), 'POSAPI', True, 'UNCERTAIN'),    # PosAPI-г ангилахгүй
    (http_error(401, {'message': 'Хандах эрхгүй'}), 'QPAY', True, 'UNCERTAIN'),
    (http_error(404, {'message': 'payment not found'}), 'QPAY', True, 'REJECTED'),
    (httpx.ConnectError('down'), 'QPAY', True, 'UNCERTAIN'),
])
def test_provider_error_classification(exc, provider, creating, cls):
    text, got, status = J.provider_error(exc, provider, creating=creating)
    assert got == cls and len(text) <= 300
    if isinstance(exc, httpx.ConnectError):
        assert (text, status) == ('ConnectError', None)


def test_plain_retry_after_definite_rejection_requeues_same_job(db, monkeypatch):
    p = qpay_paid(db, monkeypatch)
    create = AsyncMock(side_effect=[http_error(400, {'message': "'30101065006' ТТД бүхий ААН-н "
                                                     "түрээслэгчийн жагсаалтанд ТТД бүртгэлгүй"}),
                                    {'billId': QDDTD, 'lottery': None}])
    monkeypatch.setattr(J.qpay, 'create_ebarimt', create)
    run_outbox(db)
    u = user(db)
    res = asyncio.run(PR.retry_ebarimt_endpoint(p.id, {}, db, u))
    assert res['pending']
    [job] = rows(db, M.FinancialJob, payment_id=p.id)
    assert job.status == 'PENDING'
    [rec] = rows(db, M.VatReceipt, payment_id=p.id)
    # Хуучин ангилал идэвхтэй гэж харагдахгүй — түүх errors_prev-д
    assert 'error' not in rec.raw and rec.raw['errors_prev'][-1]['class'] == 'REJECTED'
    run_outbox(db)
    [rec] = rows(db, M.VatReceipt, payment_id=p.id)
    assert rec.status == 'SENT' and rec.ebarimt_id == QDDTD and create.await_count == 2
    assert create.await_args_list[0].kwargs == create.await_args_list[1].kwargs


# ═════════════════════════════════ F3 ═════════════════════════════════
def _sent_msgbill(db, monkeypatch, rtype='CITIZEN', tin=None):
    p = paid(db, tin=tin, rtype=rtype)
    monkeypatch.setattr(J.msgbill, 'create_receipt', AsyncMock(return_value=created(lottery='IW 1')))
    run_outbox(db)
    [rec] = rows(db, M.VatReceipt, payment_id=p.id)
    assert rec.status == 'SENT'
    return p, rec


def test_receiver_change_after_cancel_creates_new_receipt_with_new_key(db, monkeypatch):
    p, old = _sent_msgbill(db, monkeypatch)
    u = user(db, 'sanhuu')
    # SENT баримт дээр шууд солих → 409 (эхлээд цуцлах)
    with pytest.raises(HTTPException) as e:
        asyncio.run(PR.retry_ebarimt_endpoint(p.id, {'customer_tin': '7654321'}, db, u))
    assert e.value.status_code == 409
    assert db.query(M.AuditLog).filter_by(action='EBARIMT_RETRY_REFUSED').count() == 1
    monkeypatch.setattr(PR.msgbill, 'cancel_receipt_any',
                        AsyncMock(return_value={'state': 'CANCELLED', 'key_scope': 'global'}))
    assert asyncio.run(PR.cancel_ebarimt_endpoint(p.id, {'note': 'Байгууллагаар дахин гаргах'}, db, u))['ok']
    old_job = rows(db, M.FinancialJob, payment_id=p.id)[0]
    old_payload = dict(old_job.payload)
    res = asyncio.run(PR.retry_ebarimt_endpoint(p.id, {'customer_tin': '7654321'}, db, u))
    assert res['receiver_changed'] and res['keys'] == [f'pay-{p.id}-rcv-1']
    assert res['from'] == {'customer_tin': None, 'receiver_type': 'CITIZEN'}
    assert res['to'] == {'customer_tin': '7654321', 'receiver_type': 'COMPANY'}
    recs = rows(db, M.VatReceipt, payment_id=p.id)
    assert len(recs) == 2 and len(active(db, p.id)) == 1
    new = active(db, p.id)[0]
    assert new.status == 'PENDING' and new.customer_tin == '7654321'
    assert db.get(M.VatReceipt, old.id).raw['superseded_by'] == new.id
    db.expire_all()
    assert db.get(M.FinancialJob, old_job.id).payload == old_payload          # хуучин payload хөндөгдөөгүй
    audit = db.query(M.AuditLog).filter_by(action='EBARIMT_RECEIVER_CHANGE').one()
    assert audit.username == 'sanhuu' and audit.detail['to']['customer_tin'] == '7654321'
    assert audit.detail['from']['receiver_type'] == 'CITIZEN' and audit.detail['replaced'] == [old.id]
    create = AsyncMock(return_value=created(no=DDTD2, rid='rcp-2'))
    monkeypatch.setattr(J.msgbill, 'create_receipt', create)
    run_outbox(db)
    assert create.await_args.kwargs['idempotency_key'] == f'pay-{p.id}-rcv-1'
    assert create.await_args.kwargs['payer_reg_no'] == '7654321'
    new = db.get(M.VatReceipt, new.id)
    assert new.status == 'SENT' and new.ebarimt_id == DDTD2 and new.lottery_code is None
    pr = PR._print_payload(db, db.get(M.Payment, p.id))
    assert pr['ebarimt_id'] == DDTD2 and pr['receipt']['receipt_type'] == 'ORGANIZATION'
    # Дахин цуцлаад өөр регистр → rcv-2, нэг л идэвхтэй баримт
    monkeypatch.setattr(PR.msgbill, 'cancel_receipt_any',
                        AsyncMock(return_value={'state': 'CANCELLED', 'key_scope': 'global'}))
    assert asyncio.run(PR.cancel_ebarimt_endpoint(p.id, {}, db, u))['ok']
    res2 = asyncio.run(PR.retry_ebarimt_endpoint(p.id, {'customer_tin': '2702673'}, db, u))
    assert res2['keys'] == [f'pay-{p.id}-rcv-2'] and res2['replaced'] == [new.id]
    assert len(active(db, p.id)) == 1 and len(rows(db, M.VatReceipt, payment_id=p.id)) == 3


def test_receiver_change_after_definite_rejection_is_allowed(db, monkeypatch):
    p = qpay_paid(db, monkeypatch)
    create = AsyncMock(side_effect=[http_error(400, CUSTOMER_TIN_400), {'billId': QDDTD, 'lottery': None}])
    monkeypatch.setattr(J.qpay, 'create_ebarimt', create)
    run_outbox(db)
    [old] = rows(db, M.VatReceipt, payment_id=p.id)
    u = user(db)
    # QPay-д ТТД (11 орон) — 422, юу ч өөрчлөхгүй
    with pytest.raises(HTTPException) as e:
        asyncio.run(PR.retry_ebarimt_endpoint(p.id, {'customer_tin': '71101242183'}, db, u))
    assert e.value.status_code == 422
    db.rollback()
    res = asyncio.run(PR.retry_ebarimt_endpoint(p.id, {'customer_tin': '6853995'}, db, u))
    assert res['receiver_changed'] and res['reasons'] == {old.id: 'REJECTED_RECEIVER'}
    old = db.get(M.VatReceipt, old.id)
    assert old.status == 'CANCELLED' and old.raw['superseded']['never_issued'] is True
    assert old.ebarimt_id is None
    [new] = active(db, p.id)
    run_outbox(db)
    assert create.await_count == 2
    assert create.await_args.args[:2] == ('781636913529316', 'COMPANY')
    assert create.await_args.kwargs['receiver'] == '6853995'
    new = db.get(M.VatReceipt, new.id)
    assert new.status == 'SENT' and new.ebarimt_id == QDDTD
    # Орлогдсон мөрийн ажлыг «Дахин үүсгэх»/бөөн нөхөлт дахин хөдөлгөхгүй
    assert asyncio.run(PR.retry_ebarimt(db, db.get(M.Payment, p.id)))['ok']
    run_outbox(db)
    assert create.await_count == 2


def test_citizen_after_receiver_rejection(db, monkeypatch):
    p = qpay_paid(db, monkeypatch)
    create = AsyncMock(side_effect=[http_error(400, CUSTOMER_TIN_400), {'billId': QDDTD, 'lottery': 'AB 1'}])
    monkeypatch.setattr(J.qpay, 'create_ebarimt', create)
    run_outbox(db)
    res = asyncio.run(PR.retry_ebarimt_endpoint(p.id, {'receiver_type': 'CITIZEN'}, db, user(db)))
    assert res['to'] == {'customer_tin': None, 'receiver_type': 'CITIZEN'}
    run_outbox(db)
    assert create.await_args.args[1] == 'CITIZEN' and create.await_args.kwargs['receiver'] is None
    [new] = active(db, p.id)
    assert new.status == 'SENT' and new.lottery_code == 'AB 1'


@pytest.mark.parametrize('setup', ['unknown', 'retry', 'sending'])
def test_uncertain_or_in_flight_receipt_refuses_receiver_change(db, monkeypatch, setup):
    if setup == 'unknown':
        p = qpay_paid(db, monkeypatch)
        monkeypatch.setattr(J.qpay, 'create_ebarimt', AsyncMock(side_effect=http_error(500, 'boom')))
        run_outbox(db)
    elif setup == 'retry':
        p = paid(db)
        monkeypatch.setattr(J.msgbill, 'create_receipt', AsyncMock(side_effect=httpx.ConnectError('down')))
        run_outbox(db)
    else:
        p = paid(db)
        assert J.claim_one(db) not in (None, 'quarantined')
    before = [(r.id, r.status) for r in rows(db, M.VatReceipt, payment_id=p.id)]
    with pytest.raises(HTTPException) as e:
        asyncio.run(PR.retry_ebarimt_endpoint(p.id, {'customer_tin': '1234567'}, db, user(db)))
    assert e.value.status_code == 409
    assert [(r.id, r.status) for r in rows(db, M.VatReceipt, payment_id=p.id)] == before
    assert len(rows(db, M.FinancialJob, payment_id=p.id)) == 1
    pay = db.get(M.Payment, p.id)
    assert pay.customer_tin != '1234567'
    refused = db.query(M.AuditLog).filter_by(action='EBARIMT_RETRY_REFUSED').one()
    assert refused.detail['reason'] == 'RECEIVER_CHANGE_LOCKED'


def test_never_sent_job_is_still_fixed_in_place(db, monkeypatch):
    p = paid(db, tin='6853959')
    [job] = rows(db, M.FinancialJob, payment_id=p.id)
    job.status = 'BLOCKED'; db.commit()
    res = asyncio.run(PR.retry_ebarimt_endpoint(p.id, {'customer_tin': '1234567'}, db, user(db)))
    assert res['pending'] and not res.get('receiver_changed')
    [rec] = rows(db, M.VatReceipt, payment_id=p.id)
    [job] = rows(db, M.FinancialJob, payment_id=p.id)
    assert rec.customer_tin == '1234567' and job.payload['customer_tin'] == '1234567'
    assert job.payload['idempotency_key'] == f'pay-{p.id}'


def test_duplicate_cancelled_receipt_needs_explicit_permission(db, monkeypatch):
    p, old = _sent_msgbill(db, monkeypatch)
    from app.services.receipts import DUPLICATE_OF_POS
    old = db.get(M.VatReceipt, old.id)
    old.status, old.raw = 'CANCELLED', {**(old.raw or {}), 'cancel': {'reason': DUPLICATE_OF_POS}}
    db.commit()
    u = user(db)
    with pytest.raises(HTTPException) as e:
        asyncio.run(PR.retry_ebarimt_endpoint(p.id, {'customer_tin': '1234567'}, db, u))
    assert e.value.status_code == 409 and 'ДАВХАРДСАН' in e.value.detail
    res = asyncio.run(PR.retry_ebarimt_endpoint(p.id, {'customer_tin': '1234567', 'allow_duplicate': True},
                                                db, u))
    [new] = active(db, p.id)
    assert res['receiver_changed'] and new.raw['reissue']['allow_duplicate'] is True


def test_plain_retry_after_cancel_of_replacement_uses_latest_row(db, monkeypatch):
    p, old = _sent_msgbill(db, monkeypatch)
    u = user(db)
    monkeypatch.setattr(PR.msgbill, 'cancel_receipt_any',
                        AsyncMock(return_value={'state': 'CANCELLED', 'key_scope': 'global'}))
    asyncio.run(PR.cancel_ebarimt_endpoint(p.id, {}, db, u))
    asyncio.run(PR.retry_ebarimt_endpoint(p.id, {'customer_tin': '7654321'}, db, u))
    monkeypatch.setattr(J.msgbill, 'create_receipt', AsyncMock(return_value=created(no=DDTD2, rid='rcp-2')))
    run_outbox(db)
    asyncio.run(PR.cancel_ebarimt_endpoint(p.id, {}, db, u))
    # Хоёр цуцалсан мөр — гэхдээ нэг нь орлогдсон тул «Дахин үүсгэх» сүүлийнхийг орлоно
    res = asyncio.run(PR.retry_ebarimt_endpoint(p.id, {}, db, u))
    assert res['pending']
    [new] = active(db, p.id)
    [job] = rows(db, M.FinancialJob, receipt_id=new.id)
    leaf = [r for r in rows(db, M.VatReceipt, payment_id=p.id) if r.ebarimt_id == DDTD2][0]
    assert job.payload['idempotency_key'] == f'replace-{leaf.id}' and new.customer_tin == '7654321'


# ═════════════════════════ ХЯНАЛТ (2026-10-16) ═════════════════════════
def _cancel_ok(monkeypatch):
    monkeypatch.setattr(PR.msgbill, 'cancel_receipt_any',
                        AsyncMock(return_value={'state': 'CANCELLED', 'key_scope': 'global'}))
    monkeypatch.setattr(PR.qpay, 'cancel_ebarimt', AsyncMock(return_value=True))


def _refused(db, pid, body, u):
    with pytest.raises(HTTPException) as e:
        asyncio.run(PR.retry_ebarimt_endpoint(pid, body, db, u))
    assert e.value.status_code == 409
    return e.value.detail


# ── #1 HIGH: мөрийн тоо/дүнгийн хамгаалалт ─────────────────────────────────────
def test_two_full_amount_cancelled_rows_refuse_receiver_change(db, monkeypatch):
    """Хуучин (ажилгүй) давхар мөр + үндсэн мөр — бүгд CANCELLED: мөр бүрээс ШИНЭ баримт гаргаж
    1000₮-ийн төлбөрт 2000₮-ийн баримт гаргадаг байв → 409, шинэ мөр/ажил үүсэхгүй."""
    p = paid(db, tin=None, rtype='CITIZEN')
    monkeypatch.setattr(J.msgbill, 'create_receipt', AsyncMock(return_value=created(lottery='X 1')))
    run_outbox(db)
    db.add(M.VatReceipt(id='00000000-0000-4000-8000-00000000d0d0', payment_id=p.id, session_id=p.session_id,
                        amount=1000, vat_amount=91, provider='MSGBILL', status='CANCELLED', ebarimt_id=DDTD2,
                        receipt_url='давхардал цуцлав'))
    db.commit()
    u = user(db)
    _cancel_ok(monkeypatch)
    assert asyncio.run(PR.cancel_ebarimt_endpoint(p.id, {}, db, u))['ok']
    before = len(rows(db, M.VatReceipt, payment_id=p.id)), len(rows(db, M.FinancialJob, payment_id=p.id))
    detail = _refused(db, p.id, {'customer_tin': '7654321'}, u)
    assert 'ДАВХАРДАНА' in detail and '2000' in detail.replace(',', '')
    assert (len(rows(db, M.VatReceipt, payment_id=p.id)), len(rows(db, M.FinancialJob, payment_id=p.id))) == before
    assert active(db, p.id) == [] and db.get(M.Payment, p.id).customer_tin is None
    refused = db.query(M.AuditLog).filter_by(action='EBARIMT_RETRY_REFUSED').one()
    assert refused.detail['code'] == 'MULTI_LEAF'


def test_legacy_cancel_then_retry_pattern_is_refused(db, monkeypatch):
    """09-19-өөс өмнөх «Цуцлах → Дахин үүсгэх» (ажилгүй 2 мөр): дахин гаргавал 2 идэвхтэй баримт."""
    import uuid as _u
    s = parking(db)
    p = M.Payment(session_id=s.id, provider='CASH', payment_method='CASH', amount=1000, vat_amount=91,
                  status='PAID', paid_at=datetime.utcnow(), sender_invoice_no='legacy',
                  customer_tin=None, ebarimt_receiver_type='CITIZEN')
    db.add(p); db.flush()
    t0 = datetime.utcnow() - timedelta(days=40)
    db.add_all([M.VatReceipt(id=str(_u.uuid4()), payment_id=p.id, session_id=s.id, amount=1000, vat_amount=91,
                             provider='MSGBILL', provider_ref='rcp-old1', status='CANCELLED', ebarimt_id=DDTD,
                             receipt_url='Цуцлав', created_at=t0),
                M.VatReceipt(id=str(_u.uuid4()), payment_id=p.id, session_id=s.id, amount=1000, vat_amount=91,
                             provider='MSGBILL', provider_ref='rcp-old2', status='SENT', ebarimt_id=DDTD2,
                             created_at=t0 + timedelta(hours=1))])
    db.commit()
    u = user(db)
    _cancel_ok(monkeypatch)
    vat = {r['id']: r for r in R.vat_receipts('2000-01-01', '2030-01-01', db=db, user=u)}
    assert not any(r['can_change_receiver'] for r in vat.values())     # UI товч ч гарахгүй
    assert asyncio.run(PR.cancel_ebarimt_endpoint(p.id, {}, db, u))['ok']
    create = AsyncMock(side_effect=AssertionError('must not issue'))
    monkeypatch.setattr(J.msgbill, 'create_receipt', create)
    _refused(db, p.id, {'customer_tin': '7654321'}, u)
    run_outbox(db)
    assert create.await_count == 0 and rows(db, M.FinancialJob, payment_id=p.id) == []


def test_qpay_two_sent_rows_prod_pattern_is_refused(db, monkeypatch):
    """audit1014 (10146653, a153bd38, ba638bc4, c1a1108d, f7bb0ae7): QPay төлбөрт бүтэн дүнтэй 2 SENT
    мөр, ажилгүй. «Байгууллагаар дахин гаргах» нэг QPay payment_id дээр 2 create хийдэг байв."""
    import uuid as _u
    monkeypatch.setattr(J.settings, 'qpay_ebarimt', True)
    monkeypatch.setattr(J.settings, 'qpay_recreate_after_cancel', True)   # тусгаарлаж: зөвхөн мөрийн тоо
    s = parking(db)
    p = M.Payment(session_id=s.id, provider='QPAY', payment_method='QR', amount=5000, vat_amount=455,
                  status='PAID', paid_at=datetime.utcnow(), sender_invoice_no='dupq',
                  provider_payment_id='781636913529316', customer_tin=None, ebarimt_receiver_type='CITIZEN')
    db.add(p); db.flush()
    t0 = datetime.utcnow() - timedelta(days=50)
    for i in range(2):
        db.add(M.VatReceipt(id=str(_u.uuid4()), payment_id=p.id, session_id=s.id, amount=5000, vat_amount=455,
                            provider='QPAY', status='SENT', ebarimt_id=QDDTD, created_at=t0 + timedelta(seconds=i)))
    db.commit()
    u = user(db)
    _cancel_ok(monkeypatch)
    assert asyncio.run(PR.cancel_ebarimt_endpoint(p.id, {}, db, u))['ok']
    assert asyncio.run(PR.retry_ebarimt(db, db.get(M.Payment, p.id))).get('review_required')
    db.rollback()
    create = AsyncMock(side_effect=AssertionError('must not call QPay create'))
    monkeypatch.setattr(J.qpay, 'create_ebarimt', create)
    detail = _refused(db, p.id, {'customer_tin': '6853995'}, u)
    assert 'QPay нэг гүйлгээнд НЭГ л баримт' in detail
    run_outbox(db)
    assert create.await_count == 0 and len(rows(db, M.VatReceipt, payment_id=p.id)) == 2


def test_qpay_shared_payment_id_is_refused(db, monkeypatch):
    p = qpay_paid(db, monkeypatch)
    monkeypatch.setattr(J.qpay, 'create_ebarimt', AsyncMock(side_effect=http_error(400, CUSTOMER_TIN_400)))
    run_outbox(db)
    # Ижил QPay гүйлгээ өөр төлбөрийн мөрт (давхар бүртгэл)
    db.add(M.Payment(session_id=p.session_id, provider='QPAY', payment_method='QR', amount=1000, vat_amount=91,
                     status='PAID', paid_at=datetime.utcnow(), sender_invoice_no='dup-qpay',
                     provider_payment_id='781636913529316'))
    db.commit()
    u = user(db)
    vat = {r['id']: r for r in R.vat_receipts('2000-01-01', '2030-01-01', db=db, user=u)}
    [rec] = rows(db, M.VatReceipt, payment_id=p.id)
    assert vat[rec.id]['error_class'] == 'REJECTED_RECEIVER' and not vat[rec.id]['can_change_receiver']
    assert 'өөр төлбөрийн' in _refused(db, p.id, {'customer_tin': '6853995'}, u)
    assert len(rows(db, M.VatReceipt, payment_id=p.id)) == 1


def _split_paid(db, monkeypatch, *, amount=1500):
    """Үндсэн (1000) + өрийн (500, pay-{id}-comp-*) баримттай бэлэн төлбөр — msgbill-ээр SENT."""
    s = parking(db)
    p = M.Payment(session_id=s.id, provider='CASH', payment_method='CASH', amount=amount, vat_amount=136,
                  status='PAID', paid_at=datetime.utcnow(), sender_invoice_no='split',
                  customer_tin=None, ebarimt_receiver_type='CITIZEN')
    db.add(p); db.flush()
    J.enqueue_receipt(db, p, amount=1000, vat=91, description='Зогсоолын үйлчилгээ')
    J.enqueue_receipt(db, p, amount=500, vat=45, key=f'pay-{p.id}-comp-c1', description='өр')
    db.commit()
    n = [0]

    async def create(acc, amount, **kw):
        n[0] += 1
        return created(no=f'{n[0]:033d}', rid=f'rcp-{n[0]}')
    monkeypatch.setattr(J.msgbill, 'create_receipt', create)
    run_outbox(db)
    assert [r.status for r in rows(db, M.VatReceipt, payment_id=p.id)] == ['SENT', 'SENT']
    return p, n


def test_main_plus_debt_split_receiver_change_is_allowed_and_chains(db, monkeypatch):
    p, n = _split_paid(db, monkeypatch)
    u = user(db)
    vat = R.vat_receipts('2000-01-01', '2030-01-01', db=db, user=u)
    assert all(r['can_change_receiver'] for r in vat) and len(vat) == 2
    assert sorted(vat[0]['cancel_ddtds']) == sorted(r.ebarimt_id for r in rows(db, M.VatReceipt, payment_id=p.id))
    _cancel_ok(monkeypatch)
    assert asyncio.run(PR.cancel_ebarimt_endpoint(p.id, {}, db, u))['ok']
    res = asyncio.run(PR.retry_ebarimt_endpoint(p.id, {'customer_tin': '7654321'}, db, u))
    assert res['keys'] == [f'pay-{p.id}-rcv-1', f'pay-{p.id}-rcv-1-2']
    run_outbox(db)
    live = active(db, p.id)
    assert [r.status for r in live] == ['SENT', 'SENT'] and n[0] == 4
    assert sum(float(r.amount) for r in live) == 1500.0 and {r.customer_tin for r in live} == {'7654321'}
    # Дахин цуцлаад өөр регистр — гинжийг эх түлхүүр (pay-{id}, pay-{id}-comp-c1) хүртэл мөшгөнө
    assert asyncio.run(PR.cancel_ebarimt_endpoint(p.id, {}, db, u))['ok']
    res2 = asyncio.run(PR.retry_ebarimt_endpoint(p.id, {'customer_tin': '2702673'}, db, u))
    assert res2['keys'] == [f'pay-{p.id}-rcv-2', f'pay-{p.id}-rcv-2-2']
    assert len(active(db, p.id)) == 2 and len(rows(db, M.VatReceipt, payment_id=p.id)) == 6


def test_split_with_amount_mismatch_is_refused(db, monkeypatch):
    p, n = _split_paid(db, monkeypatch, amount=2000)     # мөрүүд 1000+500 ≠ төлбөр 2000
    u = user(db)
    _cancel_ok(monkeypatch)
    assert asyncio.run(PR.cancel_ebarimt_endpoint(p.id, {}, db, u))['ok']
    assert 'ДАВХАРДАНА' in _refused(db, p.id, {'customer_tin': '7654321'}, u)
    assert len(rows(db, M.VatReceipt, payment_id=p.id)) == 2


# ── #2: create-ийн 409 / «аль хэдийн үүссэн» = ТОДОРХОЙГҮЙ (хоёр суваг) ────────────
@pytest.mark.parametrize('provider,exc', [
    ('QPAY', http_error(409, {'error': 'EBARIMT_ALREADY_CREATED', 'message': 'Ebarimt was created'})),
    ('QPAY', http_error(409, {'error': 'ALREADY_EXISTS', 'message': 'exists'})),
    ('QPAY', http_error(409, {'message': 'Энэ төлбөрт И-баримт үүссэн байна'})),
    ('QPAY', http_error(409, {'message': 'ebarimt_already_issued'})),
    ('QPAY', http_error(409, {'message': 'conflict'})),
    ('QPAY', http_error(400, {'error': 'EBARIMT_ALREADY_CREATED', 'message': 'Ebarimt was created'})),
    ('QPAY', http_error(400, {'message': 'ebarimt_already_issued'})),
    ('QPAY', http_error(422, {'message': 'Энэ төлбөрт И-баримт үүссэн байна'})),
    ('MSGBILL', MsgbillError('msgbill 409: request is still being processed', code='REQUEST_IN_PROGRESS', status=409)),
    ('MSGBILL', MsgbillError('msgbill 400: bad state', code='REQUEST_IN_PROGRESS', status=400)),
    ('MSGBILL', MsgbillError('msgbill 422: receipt already exists', code='ALREADY_EXISTS', status=422)),
    ('MSGBILL', MsgbillError('msgbill 409: conflict', status=409)),
])
def test_create_409_or_already_created_is_uncertain(provider, exc):
    text, cls, status = J.provider_error(exc, provider, creating=True)
    assert cls == 'UNCERTAIN', text
    assert J.maybe_created(status, text)


def test_msgbill_uncertain_then_409_in_progress_stays_retry_and_locks_receiver(db, monkeypatch):
    p = paid(db)
    create = AsyncMock(side_effect=[httpx.ReadTimeout('lost'),
                                    MsgbillError('msgbill 409: request is still being processed',
                                                 code='REQUEST_IN_PROGRESS', status=409)])
    monkeypatch.setattr(J.msgbill, 'create_receipt', create)
    run_outbox(db, 2)
    [rec] = rows(db, M.VatReceipt, payment_id=p.id)
    [job] = rows(db, M.FinancialJob, payment_id=p.id)
    assert job.status == 'RETRY' and job.attempts == 2 and J.error_class(rec) == 'UNCERTAIN'
    assert rec.raw['uncertain']['attempts'] == [1, 2]
    _refused(db, p.id, {'receiver_type': 'CITIZEN'}, user(db))
    assert len(rows(db, M.FinancialJob, payment_id=p.id)) == 1


# ── #4: тодорхойгүй оролдлогын дараах 4xx — тодорхой татгалзал БИШ ──────────────
@pytest.mark.parametrize('second,cls', [
    (MsgbillError('msgbill 422: Худалдан авагчийн регистр буруу', code='INVALID_PAYER_REG_NO', status=422),
     'REJECTED_RECEIVER'),
    (MsgbillError('msgbill 400: amount invalid', code='VALIDATION', status=400), 'REJECTED'),
])
def test_uncertain_attempt_then_4xx_is_not_definite(db, monkeypatch, second, cls):
    p = paid(db)
    create = AsyncMock(side_effect=[httpx.ReadTimeout('lost'), second])
    monkeypatch.setattr(J.msgbill, 'create_receipt', create)
    run_outbox(db, 2)
    [rec] = rows(db, M.VatReceipt, payment_id=p.id)
    [job] = rows(db, M.FinancialJob, payment_id=p.id)
    assert (job.status, rec.status, J.error_class(rec)) == ('REJECTED', 'FAILED', cls)
    assert rec.raw['uncertain']['attempts'] == [1] and J.had_uncertain(rec)
    assert not J.definitely_rejected(rec, [job])
    u = user(db)
    vat = {r['id']: r for r in R.vat_receipts('2000-01-01', '2030-01-01', db=db, user=u)}
    assert not vat[rec.id]['can_change_receiver']
    _refused(db, p.id, {'customer_tin': '7654321'}, u)
    assert len(rows(db, M.VatReceipt, payment_id=p.id)) == 1 and create.await_count == 2


def test_qpay_rejected_after_uncertain_attempt_is_not_requeued(db, monkeypatch):
    p = qpay_paid(db, monkeypatch)
    create = AsyncMock(side_effect=http_error(400, {'message': "'30101065006' ТТД бүхий ААН-н "
                                                               "түрээслэгчийн жагсаалтанд ТТД бүртгэлгүй"}))
    monkeypatch.setattr(J.qpay, 'create_ebarimt', create)
    run_outbox(db)
    [rec] = rows(db, M.VatReceipt, payment_id=p.id)
    J.mark_uncertain(rec, attempt=0, text='ebarimt-diag ReadTimeout', source='test'); db.commit()
    res = asyncio.run(PR.retry_ebarimt(db, db.get(M.Payment, p.id)))
    assert res.get('review_required') and 'ТОДОРХОЙГҮЙ' in res['error']
    db.rollback()
    run_outbox(db)
    assert create.await_count == 1 and rows(db, M.FinancialJob, payment_id=p.id)[0].status == 'REJECTED'


def test_definitely_rejected_respects_review_attempts_and_marker():
    class R_: pass
    r = R_(); r.status = 'FAILED'; r.ebarimt_id = None; r.provider_ref = None
    class Jb: status = 'REJECTED'
    r.raw = {'qpay_review_attempts': [{'outcome': 'UNKNOWN'}]}
    assert J.definitely_rejected(r, [Jb()]) is False
    r.raw = {'qpay_review_attempts': [{'outcome': 'REJECTED'}]}
    assert J.definitely_rejected(r, [Jb()]) is True
    r.raw = {'uncertain': {'count': 1}}
    assert J.definitely_rejected(r, [Jb()]) is False


def test_claim_one_quarantine_marks_uncertain(db, monkeypatch):
    p = qpay_paid(db, monkeypatch)
    [rec] = rows(db, M.VatReceipt, payment_id=p.id)
    [job] = rows(db, M.FinancialJob, payment_id=p.id)
    rec.status = 'FAILED'
    rec.raw = {'error': {'class': 'REJECTED_RECEIVER'}}
    job.status, job.attempts = 'SENDING', 1
    job.lease_until = datetime.utcnow() - timedelta(minutes=1)
    db.commit()
    assert J.claim_one(db) == 'quarantined'
    [rec] = rows(db, M.VatReceipt, payment_id=p.id)
    assert rec.status == 'REVIEW' and J.error_class(rec) == 'UNCERTAIN'
    assert rec.raw['uncertain']['source'] == 'claim_one'


# ── #3: QPay-ийн цуцалсны дараах дахин create ──────────────────────────────────
def _qpay_sent_citizen(db, monkeypatch):
    p = qpay_paid(db, monkeypatch, tin=None)
    pay = db.get(M.Payment, p.id); pay.ebarimt_receiver_type = 'CITIZEN'
    [j0] = rows(db, M.FinancialJob, payment_id=p.id)
    j0.payload = {**j0.payload, 'receiver_type': 'CITIZEN', 'customer_tin': None}
    db.commit()
    create = AsyncMock(return_value={'billId': QDDTD, 'lottery': 'AA 1'})
    monkeypatch.setattr(J.qpay, 'create_ebarimt', create)
    run_outbox(db)
    [rec] = rows(db, M.VatReceipt, payment_id=p.id)
    assert rec.status == 'SENT' and rec.ebarimt_id == QDDTD
    return p, rec, create


def test_qpay_sent_then_cancelled_receiver_change_refused_by_default(db, monkeypatch):
    assert settings.qpay_recreate_after_cancel is False
    p, rec, create = _qpay_sent_citizen(db, monkeypatch)
    u = user(db)
    vat = {r['id']: r for r in R.vat_receipts('2000-01-01', '2030-01-01', db=db, user=u)}
    assert vat[rec.id]['can_change_receiver'] is False     # UI «Байгууллагаар дахин гаргах» гарахгүй
    _cancel_ok(monkeypatch)
    assert asyncio.run(PR.cancel_ebarimt_endpoint(p.id, {}, db, u))['ok']
    assert 'TEST дээр батлагдаагүй' in _refused(db, p.id, {'customer_tin': '6853995'}, u)
    run_outbox(db)
    assert create.await_count == 1 and len(rows(db, M.VatReceipt, payment_id=p.id)) == 1
    assert db.query(M.AuditLog).filter_by(action='EBARIMT_RETRY_REFUSED').one().detail['code'] == 'QPAY_RECREATE'
    # Тохиргоо асаавал SENT QPay мөрөнд ч товч гарна (TEST дээр батлагдсаны дараа)
    monkeypatch.setattr(settings, 'qpay_recreate_after_cancel', True)
    vat = {r['id']: r for r in R.vat_receipts('2000-01-01', '2030-01-01', db=db, user=u)}
    assert vat[rec.id]['can_change_receiver'] is True and vat[rec.id]['superseded'] is False


def test_qpay_recreate_returning_cancelled_ddtd_goes_to_review(db, monkeypatch):
    monkeypatch.setattr(settings, 'qpay_recreate_after_cancel', True)
    p, old, create = _qpay_sent_citizen(db, monkeypatch)
    u = user(db)
    _cancel_ok(monkeypatch)
    assert asyncio.run(PR.cancel_ebarimt_endpoint(p.id, {}, db, u))['ok']
    res = asyncio.run(PR.retry_ebarimt_endpoint(p.id, {'customer_tin': '6853995'}, db, u))
    assert res['receiver_changed']
    run_outbox(db)    # QPay ижил payment_id-д ЦУЦЛАГДСАН хуучин ДДТД-г буцаав
    [new] = active(db, p.id)
    [job] = rows(db, M.FinancialJob, receipt_id=new.id)
    assert new.status == 'REVIEW' and new.ebarimt_id is None and job.status == 'UNKNOWN'
    assert 'QPay цуцалсан ДДТД-г буцаав — тулгана уу' in new.receipt_url
    assert new.raw['returned_cancelled']['bill_id'] == QDDTD and J.error_class(new) == 'UNCERTAIN'
    assert not J.definitely_rejected(new, [job])
    assert asyncio.run(PR.retry_ebarimt(db, db.get(M.Payment, p.id)))['review_required']
    db.rollback()
    run_outbox(db)
    assert create.await_count == 2
    vat = {r['id']: r for r in R.vat_receipts('2000-01-01', '2030-01-01', db=db, user=u)}
    assert vat[old.id]['superseded'] is True and vat[new.id]['can_change_receiver'] is False


# ── #5: терминалын дүрэм ЗӨВХӨН байгууллагын баримтад алгасна ──────────────────
def test_terminal_rule_skips_only_company_receipts(db, monkeypatch):
    create = AsyncMock(return_value=created())
    monkeypatch.setattr(J.msgbill, 'create_receipt', create)
    PA.invalidate_cache()
    s, u = parking(db), user(db, 'pos.x', 'POS')
    st = db.get(M.ParkingSite, s.site_id)
    A.set_site_rules(db, A.EBARIMT_KEY, st.id, {'pos_terminal_issues': True}, 'admin'); db.commit()
    # Иргэний регистр — терминал (Ontime) иргэний баримт хэвлэдэг → систем ДАВХАР гаргахгүй
    out = asyncio.run(PR.cash_payment({'session_id': s.id, 'payer_reg_no': 'АА00112233'}, db, u, req('1.6.0')))
    [r] = rows(db, M.VatReceipt, payment_id=out['payment_id'])
    p = db.get(M.Payment, out['payment_id'])
    assert (p.customer_tin, p.ebarimt_receiver_type) == ('АА00112233', 'CITIZEN')
    assert r.status == 'EXTERNAL' and create.await_count == 0
    # Байгууллага — систем баримт гаргана
    s2 = parking(db, '5555УБА', st=st)
    out2 = asyncio.run(PR.cash_payment({'session_id': s2.id, 'payer_reg_no': '5037409'}, db, u, req('1.6.0')))
    [r2] = rows(db, M.VatReceipt, payment_id=out2['payment_id'])
    assert r2.status == 'SENT' and r2.provider == 'MSGBILL' and create.await_count == 1
    PA.invalidate_cache()


# ── #6: safe_text ────────────────────────────────────────────────────────────
@pytest.mark.parametrize('raw,secret', [
    ('Authorization: Basic dXNlcjpwYXNzd29yZA==', 'dXNlcjpwYXNzd29yZA'),
    ('authorization: bearer abc.def-ghi', 'abc.def-ghi'),
    ('{"password": "my secret pass"}', 'secret pass'),
    ('{"client_secret":"abc123xyz"}', 'abc123xyz'),
    ('{"access_token": "tok en 42"}', 'tok en 42'),
    ('X-Api-Key: live_9f8e7d6c5b4a', 'live_9f8e7d6c5b4a'),
    ('?token=abcdef123456&x=1', 'abcdef123456'),
    ('"api_key": "bsk_live_zzzzzzzz"', 'bsk_live_zzzzzzzz'),
])
def test_safe_text_masks_secrets(raw, secret):
    out = J.safe_text(raw)
    assert secret not in out and '***' in out


def test_safe_text_basic_keeps_scheme_label():
    assert J.safe_text('Authorization: Basic dXNlcjpwYXNz') == 'Authorization: Basic ***'
    assert J.safe_text('{"password": "a b"}') == '{"password": "***"}'


# ── #7: худалдан авагчаас болж татгалзсаныг ИЖИЛ дугаараар дахин илгээхгүй ────
def test_plain_retry_refuses_receiver_rejection(db, monkeypatch):
    p = qpay_paid(db, monkeypatch)
    create = AsyncMock(side_effect=http_error(400, CUSTOMER_TIN_400))
    monkeypatch.setattr(J.qpay, 'create_ebarimt', create)
    run_outbox(db)
    with pytest.raises(HTTPException) as e:
        asyncio.run(PR.retry_ebarimt_endpoint(p.id, {}, db, user(db)))
    assert e.value.status_code == 409 and '«Дугаар засах»' in e.value.detail and '«Иргэнээр»' in e.value.detail
    run_outbox(db)
    [job] = rows(db, M.FinancialJob, payment_id=p.id)
    assert job.status == 'REJECTED' and create.await_count == 1


def test_bulk_retry_skips_receiver_rejection(db, monkeypatch):
    from app import database
    p = qpay_paid(db, monkeypatch)
    create = AsyncMock(side_effect=http_error(400, CUSTOMER_TIN_400))
    monkeypatch.setattr(J.qpay, 'create_ebarimt', create)
    run_outbox(db)
    monkeypatch.setattr(database, 'SessionLocal', factory(db))
    R._bulk_job.clear()
    R._bulk_job.update({'running': True, 'done': 0, 'ok': 0, 'skipped': 0, 'failed': 0, 'errors': {},
                        'stopped': None, 'username': 'admin'})
    try:
        asyncio.run(R._run_bulk_retry([p.id]))
        assert R._bulk_job['skipped_receiver_rejected'] == 1 and R._bulk_job['failed'] == 0
    finally:
        R._bulk_job.clear()
    run_outbox(db)
    [job] = rows(db, M.FinancialJob, payment_id=p.id)
    assert job.status == 'REJECTED' and create.await_count == 1


# ── #8: QPay + иргэний регистр → нэргүй иргэн ────────────────────────────────
def test_qpay_citizen_register_maps_to_anonymous_citizen(db, monkeypatch):
    p = qpay_paid(db, monkeypatch)
    create = AsyncMock(side_effect=[http_error(400, CUSTOMER_TIN_400), {'billId': QDDTD, 'lottery': 'AB 1'}])
    monkeypatch.setattr(J.qpay, 'create_ebarimt', create)
    run_outbox(db)
    res = asyncio.run(PR.retry_ebarimt_endpoint(p.id, {'customer_tin': 'аа00112233'}, db, user(db)))
    assert res['to'] == {'customer_tin': None, 'receiver_type': 'CITIZEN'}
    pay = db.get(M.Payment, p.id)
    assert (pay.customer_tin, pay.ebarimt_receiver_type) == (None, 'CITIZEN')
    run_outbox(db)
    assert create.await_args.args[1] == 'CITIZEN' and create.await_args.kwargs['receiver'] is None


def test_ddtd_already_live_on_other_payment_is_not_sent_twice(db, monkeypatch):
    """Суваг өөр төлбөрийн ИДЭВХТЭЙ баримтын ДДТД-г буцаавал хоёр дахь SENT мөр үүсгэхгүй (10-16)."""
    p1, rec1, create = _qpay_sent_citizen(db, monkeypatch)       # rec1 SENT QDDTD
    st = db.get(M.ParkingSite, db.get(M.ParkingSession, db.get(M.Payment, p1.id).session_id).site_id)
    s2 = parking(db, plate='5678УБА', st=st)
    p2 = M.Payment(session_id=s2.id, provider='QPAY', payment_method='QR', amount=1000, vat_amount=91,
                   status='PAID', paid_at=datetime.utcnow(), sender_invoice_no='co-QPAY-2',
                   provider_payment_id='781636913529999', customer_tin=None, ebarimt_receiver_type='CITIZEN')
    db.add(p2); db.flush()
    J.enqueue_receipt(db, p2, description='Зогсоолын үйлчилгээ · 5678УБА')
    db.commit()
    [j2] = rows(db, M.FinancialJob, payment_id=p2.id)
    j2.payload = {**j2.payload, 'receiver_type': 'CITIZEN', 'customer_tin': None}
    db.commit()
    monkeypatch.setattr(J.qpay, 'create_ebarimt', AsyncMock(return_value={'billId': QDDTD, 'lottery': 'AA 2'}))
    run_outbox(db)
    [rec2] = rows(db, M.VatReceipt, payment_id=p2.id)
    assert rec2.status == 'REVIEW' and rec2.ebarimt_id is None
    assert 'өөр баримтад бүртгэлтэй ДДТД-г буцаав' in rec2.receipt_url
    assert J.error_class(rec2) == 'UNCERTAIN' and not J.definitely_rejected(rec2, [db.get(M.FinancialJob, j2.id)])
    assert db.get(M.VatReceipt, rec1.id).status == 'SENT'
    assert db.query(M.VatReceipt).filter_by(ebarimt_id=QDDTD, status='SENT').count() == 1
