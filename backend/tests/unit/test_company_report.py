"""Тайлан «Байгууллагаар» (харилцагчийн №61, 2026-10-22).

  • тулгалт SQL-д: зогсолтын орсон цаг бүртгэлийн valid_from..valid_to дотор
    байвал л тоологдоно; зогсоолын бүртгэл «бүх зогсоол»-ынхоос түрүүлнэ;
    түрээслэгч дамнахгүй; идэвхгүй бүртгэл тоологдохгүй; давхар тоологдохгүй;
  • «Тарифаар тооцсон дүн» — зогсоолын тарифаар (гэрээгүй) бодно, (зогсоол,
    минут) хос бүрийг нэг удаа; no_charge зогсоол 0;
  • хязгаараас олон зогсолттой бол дүн null + тайлбар, тоо хэвээр.
Synthetic SQLite — прод/тест DB хөндөхгүй.
"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import models as M
from app.database import Base
from app.routers import reports_router as R

T0 = datetime(2026, 9, 10, 2, 0)      # UTC (УБ 10:00)


@pytest.fixture
def env(vat_inclusive):
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    # Моделийн PostgreSQL-ийн partial index SQLite-д нөхцөлгүй болдог — ижил дугаарын
    # түүхэн (хаагдсан) зогсолтуудыг оруулахын тулд жинхэнэ partial index-ээр солино
    with engine.begin() as conn:
        conn.execute(text('DROP INDEX uq_active_session'))
        conn.execute(text("CREATE UNIQUE INDEX uq_active_session ON parking_sessions "
                          "(site_id, plate_number) WHERE status IN ('OPEN','AWAITING_PAYMENT','PAID')"))
    with Session(engine, autoflush=False) as db:
        tmpl = M.TariffTemplate(name='T', free_minutes=15, extra_hour_price=1000)
        db.add(tmpl); db.flush()
        db.add_all([M.TariffTier(template_id=tmpl.id, upto_minutes=60, price=1000),
                    M.TariffTier(template_id=tmpl.id, upto_minutes=120, price=2000)])
        ta = M.Tenant(name='A', code='A'); tb = M.Tenant(name='B', code='B')
        db.add_all([ta, tb]); db.flush()
        s1 = M.ParkingSite(name='S1', site_code='S1', tenant_id=ta.id, tariff_template_id=tmpl.id)
        s2 = M.ParkingSite(name='S2', site_code='S2', tenant_id=ta.id, tariff_template_id=tmpl.id)
        s3 = M.ParkingSite(name='S3', site_code='S3', tenant_id=tb.id, tariff_template_id=tmpl.id)
        free = M.ParkingSite(name='FREE', site_code='FR', tenant_id=ta.id, tariff_template_id=tmpl.id,
                             no_charge=True)
        db.add_all([s1, s2, s3, free]); db.flush()
        user = M.User(username='su', password_hash='x', role='SUPER_ADMIN')
        db.add(user); db.commit()
        yield db, user, {'s1': s1, 's2': s2, 's3': s3, 'free': free, 'ta': ta, 'tb': tb}
    engine.dispose()


def drv(db, plate, company, site=None, tenant=None, days=(-30, 30), active=True, **kw):
    r = M.RegisteredDriver(plate_number=plate, company=company, contract_type='CONTRACT',
                           site_id=site.id if site else None,
                           tenant_id=tenant.id if tenant else (site.tenant_id if site else None),
                           valid_from=T0 + timedelta(days=days[0]), valid_to=T0 + timedelta(days=days[1]),
                           is_active=active, **kw)
    db.add(r); db.flush(); return r


def stay(db, plate, site, start, minutes, **kw):
    s = M.ParkingSession(plate_number=plate, site_id=site.id, entry_time=start,
                         exit_time=start + timedelta(minutes=minutes), duration_minutes=minutes,
                         status='FREE', is_registered=True, **kw)
    db.add(s); db.flush(); return s


def report(db, user, **kw):
    return R._by_company_data(db, user, kw.pop('date_from', '2026-09-01'), kw.pop('date_to', '2026-09-30'),
                              kw.pop('site_id', None), kw.pop('company', None))


def rows_by(data):
    return {r['company']: r for r in data['rows']}


def test_validity_window_and_scope_rules(env):
    db, user, S = env
    # Зогсоолын бүртгэл ба «бүх зогсоол» бүртгэл өөр байгууллагатай — зогсоолынх түрүүлнэ
    drv(db, '1111УБА', 'Голомт банк', site=S['s1'])
    drv(db, '1111УБА', 'Хаан банк', tenant=S['ta'])
    stay(db, '1111УБА', S['s1'], T0, 90)              # → Голомт (зогсоолынх)
    stay(db, '1111УБА', S['s2'], T0, 30)              # → Хаан (S2-т зөвхөн «бүх зогсоол»)
    # Бүртгэлээс ӨМНӨ орсон зогсолт тоологдохгүй
    drv(db, '2222УБА', 'SGS', site=S['s1'], days=(0, 30))
    stay(db, '2222УБА', S['s1'], T0 - timedelta(days=2), 600)
    stay(db, '2222УБА', S['s1'], T0 + timedelta(hours=1), 60)
    # Өөр түрээслэгчийн «бүх зогсоол» бүртгэл S3-т (tenant B) хамаарахгүй
    stay(db, '1111УБА', S['s3'], T0, 45)
    # Идэвхгүй бүртгэл тоологдохгүй
    drv(db, '3333УБА', 'Идэвхгүй ХХК', site=S['s1'], active=False)
    stay(db, '3333УБА', S['s1'], T0, 45)
    # Бүртгэлгүй машин
    stay(db, '9999УБА', S['s1'], T0, 45)
    db.commit()

    d = report(db, user)
    by = rows_by(d)
    assert by['Голомт банк']['sessions'] == 1 and by['Голомт банк']['total_minutes'] == 90
    assert by['Хаан банк']['sessions'] == 1 and by['Хаан банк']['total_minutes'] == 30
    assert by['SGS']['sessions'] == 1 and by['SGS']['total_minutes'] == 60
    assert 'Идэвхгүй ХХК' not in by
    assert d['total_sessions'] == 3          # давхар тоологдоогүй, S3/бүртгэлгүй орохгүй
    assert by['SGS']['registered_cars'] == 1 and by['SGS']['visited_cars'] == 1


def test_validity_end_excludes_later_stay(env):
    db, user, S = env
    drv(db, '4444УБА', 'Дууссан', site=S['s1'], days=(-30, 1))
    stay(db, '4444УБА', S['s1'], T0, 60)
    stay(db, '4444УБА', S['s1'], T0 + timedelta(days=5), 60)    # гэрээ дууссаны дараа
    db.commit()
    assert rows_by(report(db, user))['Дууссан']['sessions'] == 1


def test_shadow_fee_uses_site_tariff_and_paid(env):
    db, user, S = env
    drv(db, '5555УБА', 'Голомт банк', site=S['s1'])
    a = stay(db, '5555УБА', S['s1'], T0, 10)                       # free_minutes 15 → 0
    stay(db, '5555УБА', S['s1'], T0 + timedelta(hours=3), 90)     # ≤120 → 2000
    stay(db, '5555УБА', S['s1'], T0 + timedelta(hours=6), 200)    # 2000 + 2ц×1000 → 4000
    stay(db, '5555УБА', S['s1'], T0 + timedelta(hours=12), 150, paused_minutes=100)  # төлбөрт 50 → 1000
    drv(db, '5555УБА', 'Голомт банк', site=S['free'])
    stay(db, '5555УБА', S['free'], T0, 500)                        # no_charge → 0
    db.add(M.Payment(session_id=a.id, provider='CASH', payment_method='CASH', sender_invoice_no='x1',
                     amount=500, status='PAID'))
    db.add(M.Payment(session_id=a.id, provider='CASH', payment_method='CASH', sender_invoice_no='x2',
                     amount=900, status='FAILED'))
    db.commit()
    d = report(db, user)
    g = rows_by(d)['Голомт банк']
    assert g['sessions'] == 5
    assert g['tariff_amount'] == 7000
    assert g['paid_amount'] == 500 and g['diff_amount'] == 6500
    assert d['tariff_amount'] == 7000 and d['amounts_capped'] is False

    rows = R._company_sessions(db, user, 'Голомт банк', *R._range('2026-09-01', '2026-09-30'), None)
    assert sorted(r['tariff_fee'] for r in rows) == [0, 0, 1000, 2000, 4000]
    assert sum(r['paid'] for r in rows) == 500
    assert all(r['contract'] == 'Гэрээт' for r in rows)


def test_shadow_fee_computed_once_per_site_minutes(env, monkeypatch):
    db, user, S = env
    drv(db, '6666УБА', 'Хаан банк', site=S['s1'])
    for i in range(6):
        stay(db, '6666УБА', S['s1'], T0 + timedelta(hours=3 * i), 90)
    db.commit()
    import app.billing as B
    calls = []
    real = B.calculate_fee

    def spy(*a, **kw):
        calls.append(1)
        return real(*a, **kw)
    monkeypatch.setattr(B, 'calculate_fee', spy)
    assert rows_by(report(db, user))['Хаан банк']['tariff_amount'] == 6 * 2000
    assert len(calls) == 1


def test_cap_returns_null_amounts_with_note(env, monkeypatch):
    db, user, S = env
    drv(db, '7777УБА', 'Хаан банк', site=S['s1'])
    stay(db, '7777УБА', S['s1'], T0, 90)
    stay(db, '7777УБА', S['s1'], T0 + timedelta(hours=5), 90)
    db.commit()
    monkeypatch.setattr(R, 'COMPANY_FEE_MAX_STAYS', 1)
    d = report(db, user)
    assert d['amounts_capped'] is True and d['tariff_amount'] is None and 'богиносгоно' in d['note']
    r = rows_by(d)['Хаан банк']
    assert r['tariff_amount'] is None and r['diff_amount'] is None
    assert r['sessions'] == 2 and r['total_minutes'] == 180       # тоо/хугацаа хэвээр


def test_company_filter_and_no_company_bucket(env):
    db, user, S = env
    drv(db, '8888УБА', 'Голомт банк', site=S['s1'])
    drv(db, '8889УБА', '  ', site=S['s1'])
    stay(db, '8888УБА', S['s1'], T0, 90)
    stay(db, '8889УБА', S['s1'], T0, 30)
    db.commit()
    d = report(db, user, company='Голомт банк')
    assert [r['company'] for r in d['rows']] == ['Голомт банк']
    d = report(db, user, company=R.NO_COMPANY)
    assert [r['company'] for r in d['rows']] == [R.NO_COMPANY] and d['rows'][0]['sessions'] == 1


def test_multi_site_scoped_user(env):
    """site_ids жагсаалттай хэрэглэгч (_scope → list) — өмнө нь site_tenant.get(list)-ээр унадаг байв."""
    db, _, S = env
    drv(db, '1212УБА', 'Хаан банк', tenant=S['ta'])
    drv(db, '1313УБА', 'Төрийн банк', tenant=S['tb'])
    stay(db, '1212УБА', S['s2'], T0, 90)
    db.commit()
    scoped = M.User(username='op', password_hash='x', role='ADMIN', site_ids=[S['s1'].id, S['s2'].id])
    db.add(scoped); db.commit()
    by = rows_by(report(db, scoped))
    assert by['Хаан банк']['sessions'] == 1 and by['Хаан банк']['registered_cars'] == 1
    assert 'Төрийн банк' not in by


def test_junk_company_flagged(env):
    db, user, S = env
    drv(db, '1414УБА', 'Улсын дугаар', site=S['s1'])
    stay(db, '1414УБА', S['s1'], T0, 90)
    db.commit()
    assert rows_by(report(db, user))['Улсын дугаар']['junk'] is True
