"""Өрийн хайлт: огноо/цагийн муж, хуудаслалт, Excel (2026-10-22, харилцагчийн №58).

«Системийн өр хэсэгт хайлтын хүрээг нэмэх — хэдний өдөр хэдэн цагаас хэдэн цагийн
хооронд аль зогсоол дээр ямар машин дээр өр үүссэн». DB нь naive UTC, UI нь УБ-ын
цаг (UTC+8) — муж 8 цагаар зөрөхгүй байхыг заагийн мөрүүдээр барина. Мөн зогсоолын
хүрээ (tenant салгалт) жагсаалт/Excel хоёуланд хэвээр байх ёстой.

DB = SQLite (санах ой), сүлжээгүй.
"""
import asyncio
import io
from datetime import datetime, timedelta

import openpyxl
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import models as M
from app.config import settings
from app.database import Base
from app.routers import compensations_router as C


@pytest.fixture
def db():
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    with Session(engine, autoflush=False) as session:
        yield session
    engine.dispose()


@pytest.fixture(autouse=True)
def ub_tz(monkeypatch):
    monkeypatch.setattr(settings, 'tz_offset_hours', 8)


def _site(db, code):
    st = M.ParkingSite(name=f'Зогсоол {code}', site_code=code)
    db.add(st); db.flush()
    return st


def _user(db, role='SUPER_ADMIN', site_ids=None, name=None):
    u = M.User(username=name or f'u-{role}-{len(site_ids or [])}', role=role,
               password_hash='x', site_ids=site_ids)
    db.add(u); db.flush()
    return u


def _comp(db, st, plate, created, amount=1000, reason='unpaid_exit', status='PENDING', session=None):
    c = M.Compensation(site_id=st.id, plate_number=plate, amount=amount, reason=reason,
                       status=status, created_at=created, session_id=session.id if session else None)
    db.add(c); db.flush()
    return c


def _list(db, user, **kw):
    return C.list_compensations(**{'status': None, 'plate': None, 'site_id': None, 'min_days': 0,
                                   'created_from': None, 'created_to': None, 'reason': None,
                                   'limit': 200, 'offset': 0, **kw}, db=db, user=user)


def _excel(db, user, **kw):
    resp = C.compensations_excel(**{'status': None, 'plate': None, 'site_id': None, 'min_days': 0,
                                    'created_from': None, 'created_to': None, 'reason': None, **kw},
                                 db=db, user=user)
    assert resp.status_code == 200

    async def _drain():
        return b''.join([c async for c in resp.body_iterator])
    ws = openpyxl.load_workbook(io.BytesIO(asyncio.run(_drain()))).active
    return [[c.value for c in r] for r in ws.iter_rows()]


# ── 1. Локал → UTC хөрвүүлэлт (заагийн дүрэм) ─────────────────────────────

def test_date_only_is_whole_local_day():
    """2026-10-21 (УБ) = 10-20 16:00 UTC … 10-21 16:00 UTC (дээд зааг ОРОХГҮЙ)."""
    assert C._local_to_utc('2026-10-21', 'f') == datetime(2026, 10, 20, 16, 0)
    assert C._local_to_utc('2026-10-21', 't', end=True) == datetime(2026, 10, 21, 16, 0)


def test_minute_precision_end_includes_that_minute():
    """«18:00 хүртэл» = 18:00:59 хүртэл — datetime-local минутын нарийвчлалтай."""
    assert C._local_to_utc('2026-10-21T10:00', 'f') == datetime(2026, 10, 21, 2, 0)
    assert C._local_to_utc('2026-10-21T18:00', 't', end=True) == datetime(2026, 10, 21, 10, 1)
    assert C._local_to_utc('2026-10-21 18:00', 't', end=True) == datetime(2026, 10, 21, 10, 1)
    assert C._local_to_utc('2026-10-21T18:00:30', 't', end=True) == datetime(2026, 10, 21, 10, 0, 31)


def test_explicit_zone_is_respected():
    assert C._local_to_utc('2026-10-21T02:00Z', 'f') == datetime(2026, 10, 21, 2, 0)
    assert C._local_to_utc('2026-10-21T10:00+08:00', 'f') == datetime(2026, 10, 21, 2, 0)


def test_empty_and_malformed_values():
    assert C._local_to_utc(None, 'f') is None
    assert C._local_to_utc('  ', 'f') is None
    for bad in ('21/10/2026', '2026-13-01', 'өнөөдөр', '0001-01-01'):
        with pytest.raises(HTTPException) as e:
            C._local_to_utc(bad, 'created_from')
        assert e.value.status_code == 400


# ── 2. Жагсаалт: муж, нийлбэр, хуудаслалт ─────────────────────────────────

def _boundary_set(db):
    a = _site(db, 'A')
    rows = {
        'before': _comp(db, a, '1111УБА', datetime(2026, 10, 20, 15, 59, 59)),   # 10-20 23:59:59 УБ
        'first': _comp(db, a, '2222УБА', datetime(2026, 10, 20, 16, 0)),        # 10-21 00:00 УБ
        'morning': _comp(db, a, '3333УБА', datetime(2026, 10, 21, 2, 0)),       # 10-21 10:00 УБ
        'eve': _comp(db, a, '3333УБА', datetime(2026, 10, 21, 10, 0, 59)),      # 10-21 18:00:59 УБ
        'late': _comp(db, a, '4444УБА', datetime(2026, 10, 21, 10, 1)),         # 10-21 18:01 УБ
        'last': _comp(db, a, '5555УБА', datetime(2026, 10, 21, 15, 59, 59)),    # 10-21 23:59:59 УБ
        'next': _comp(db, a, '6666УБА', datetime(2026, 10, 21, 16, 0)),         # 10-22 00:00 УБ
    }
    db.commit()
    return a, rows


def test_local_day_filter_has_no_8h_shift(db):
    _, r = _boundary_set(db)
    u = _user(db)
    out = _list(db, u, created_from='2026-10-21', created_to='2026-10-21')
    got = {c['id'] for c in out['rows']}
    assert got == {r[k].id for k in ('first', 'morning', 'eve', 'late', 'last')}
    assert out['total'] == 5 and out['sum_amount'] == 5000 and out['plates'] == 4


def test_time_window_within_day(db):
    _, r = _boundary_set(db)
    u = _user(db)
    out = _list(db, u, created_from='2026-10-21T10:00', created_to='2026-10-21T18:00')
    assert {c['id'] for c in out['rows']} == {r['morning'].id, r['eve'].id}


def test_reversed_range_is_400(db):
    u = _user(db)
    with pytest.raises(HTTPException) as e:
        _list(db, u, created_from='2026-10-22', created_to='2026-10-21')
    assert e.value.status_code == 400


def test_paging_keeps_filter_totals(db):
    _, r = _boundary_set(db)
    u = _user(db)
    p1 = _list(db, u, limit=3, offset=0)
    p2 = _list(db, u, limit=3, offset=3)
    p3 = _list(db, u, limit=3, offset=6)
    ids = [c['id'] for c in p1['rows'] + p2['rows'] + p3['rows']]
    assert len(ids) == 7 == len(set(ids))
    assert ids[0] == r['next'].id and ids[-1] == r['before'].id   # шинэ нь эхэнд
    assert p1['total'] == p2['total'] == 7 and p2['offset'] == 3 and p2['limit'] == 3
    assert _list(db, u, limit=5000)['limit'] == C.DEBT_LIST_MAX


def test_reason_filter_accepts_list(db):
    a = _site(db, 'A')
    _comp(db, a, '1111УБА', datetime(2026, 10, 21), reason='auto_close')
    _comp(db, a, '2222УБА', datetime(2026, 10, 21), reason='camera_sync')
    _comp(db, a, '3333УБА', datetime(2026, 10, 21), reason='unpaid_exit')
    db.commit()
    u = _user(db)
    out = _list(db, u, reason='auto_close, camera_sync')
    assert sorted(c['plate_number'] for c in out['rows']) == ['1111УБА', '2222УБА']


def test_rows_carry_session_entry_exit(db):
    a = _site(db, 'A')
    s = M.ParkingSession(site_id=a.id, plate_number='7777УБА', entry_time=datetime(2026, 10, 21, 1, 0),
                         exit_time=datetime(2026, 10, 21, 3, 0), status='MANUAL_CLOSED')
    db.add(s); db.flush()
    _comp(db, a, '7777УБА', datetime(2026, 10, 21, 3, 0), session=s)
    db.commit()
    row = _list(db, _user(db))['rows'][0]
    assert row['entry_time'] == '2026-10-21T01:00:00' and row['exit_time'] == '2026-10-21T03:00:00'
    assert row['site_name'] == 'Зогсоол A'


def test_summary_aggregates_match_old_python_buckets(db):
    """total_pending/aging нь SQL нэгтгэл болсон — хуучин (now - created_at).days дүрэмтэй ижил."""
    a = _site(db, 'A')
    now = datetime.utcnow()
    _comp(db, a, '1111УБА', now - timedelta(days=8) + timedelta(minutes=5), amount=100)   # 7 хоног → 0-7
    _comp(db, a, '2222УБА', now - timedelta(days=8) - timedelta(minutes=5), amount=200)   # 8 → 8-30
    _comp(db, a, '3333УБА', now - timedelta(days=31) + timedelta(minutes=5), amount=300)  # 30 → 8-30
    _comp(db, a, '4444УБА', now - timedelta(days=91) + timedelta(minutes=5), amount=400)  # 90 → 31-90
    _comp(db, a, '5555УБА', now - timedelta(days=91) - timedelta(minutes=5), amount=500)  # 91 → 90+
    _comp(db, a, '6666УБА', now - timedelta(days=2), amount=700, status='PAID')
    _comp(db, a, '7777УБА', now - timedelta(days=2), amount=900, status='CANCELLED')
    db.commit()
    out = _list(db, _user(db), status='PENDING')
    assert out['aging'] == {'0-7': 100.0, '8-30': 500.0, '31-90': 400.0, '90+': 500.0}
    assert out['total_pending'] == 1500.0 and out['pending_count'] == 5
    assert out['total_collected'] == 700.0
    assert {c['plate_number']: c['age_bucket'] for c in out['rows']} == {
        '1111УБА': '0-7', '2222УБА': '8-30', '3333УБА': '8-30', '4444УБА': '31-90', '5555УБА': '90+'}


# ── 3. Зогсоолын хүрээ (tenant салгалт) ──────────────────────────────────

def test_scoped_user_sees_only_own_sites(db):
    a, b = _site(db, 'A'), _site(db, 'B')
    _comp(db, a, '1111УБА', datetime(2026, 10, 21, 2, 0))
    _comp(db, b, '2222УБА', datetime(2026, 10, 21, 2, 0), amount=5000)
    db.commit()
    fin = _user(db, 'FINANCE', site_ids=[a.id])
    out = _list(db, fin, created_from='2026-10-21', created_to='2026-10-21')
    assert [c['plate_number'] for c in out['rows']] == ['1111УБА']
    assert out['total'] == 1 and out['sum_amount'] == 1000 and out['total_pending'] == 1000
    with pytest.raises(HTTPException) as e:
        _list(db, fin, site_id=b.id)
    assert e.value.status_code == 403
    with pytest.raises(HTTPException) as e:
        _excel(db, fin, site_id=b.id)
    assert e.value.status_code == 403
    sheet = _excel(db, fin)
    assert [r[2] for r in sheet[1:-1]] == ['1111УБА']


def test_site_filter_is_server_side(db):
    """Ээлж хаах → Нөхөн төлбөр: site_id серверт очиж шүүгдэнэ (өмнө нь клиент талд,
    200 мөрийн хязгаар дотроос — бусад зогсоолын өр чимээгүй алга болдог байв)."""
    a, b = _site(db, 'A'), _site(db, 'B')
    for i in range(5):
        _comp(db, a, f'{1000 + i}УБА', datetime(2026, 10, 21, 5, i))
    _comp(db, b, '9999УБА', datetime(2026, 10, 1))
    db.commit()
    out = _list(db, _user(db), site_id=b.id, limit=2)
    assert [c['plate_number'] for c in out['rows']] == ['9999УБА'] and out['total'] == 1


# ── 4. Excel ──────────────────────────────────────────────────────────────

def test_excel_local_times_and_totals(db):
    a = _site(db, 'A')
    s = M.ParkingSession(site_id=a.id, plate_number='7777УБА', entry_time=datetime(2026, 10, 20, 15, 30),
                         exit_time=datetime(2026, 10, 20, 16, 5), status='MANUAL_CLOSED')
    db.add(s); db.flush()
    _comp(db, a, '7777УБА', datetime(2026, 10, 20, 16, 5), amount=2500, reason='auto_close', session=s)
    _comp(db, a, '8888УБА', datetime(2026, 10, 19, 1, 0), amount=1000)   # мужаас гадуур
    db.commit()
    sheet = _excel(db, _user(db), created_from='2026-10-21T00:00', created_to='2026-10-21T23:59')
    head, row, total = sheet[0], sheet[1], sheet[2]
    assert head[:5] == ['Үүссэн огноо', 'Үүссэн цаг', 'Дугаар', 'Зогсоол', 'Дүн (₮)']
    assert row[:7] == ['2026-10-21', '00:05', '7777УБА', 'Зогсоол A', 2500, 'Авто хаалт', 'Төлөгдөөгүй']
    assert row[7] == '2026-10-20 23:30' and row[8] == '2026-10-21 00:05'
    assert total[0] == 'НИЙТ' and total[4] == 2500
    assert len(sheet) == 3


def test_excel_truncation_is_announced(db, monkeypatch):
    a = _site(db, 'A')
    for i in range(3):
        _comp(db, a, f'{1000 + i}УБА', datetime(2026, 10, 21, 5, i))
    db.commit()
    monkeypatch.setattr(C, 'DEBT_EXCEL_MAX', 2)
    sheet = _excel(db, _user(db))
    assert len(sheet) == 1 + 2 + 2           # толгой + 2 мөр + НИЙТ + анхааруулга
    assert 'Анхаар' in sheet[-1][0] and '3 өр' in sheet[-1][0]


def test_excel_route_registered():
    paths = [(r.path, sorted(r.methods)) for r in C.router.routes]
    assert ('/api/compensations/excel', ['GET']) in paths
