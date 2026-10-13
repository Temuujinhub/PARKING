"""Дотор (nested) зогсоол — 2026-10-13 нэгтгэлийн хяналтын олдворууд (санах ойн SQLite).

E1: доторх гарах уншилт live алдагдаж, машин ДАХИН орж нээлттэй хүүхэд сешнийг дахин
    ашигласан. camera_sync хожуу (өнгөрсөн цагийн) ГАРАХ логоор хүүхэд сешнийг хаахад
    гадна тоолуурыг тэр цагаас эхлүүлж, машин ДОТОР байх хугацааг нэхдэг байв (5ц дотор
    байхад 60 мин хасалт; прод 240).
E4: сэргээгдсэн хүүхэд сешнд хуучин exit_confirmed=True үлдэж, нотолгоогүй албадан
    хаалтын дараа эцэг сэргэхэд тэр хугацаа «батлагдсан» (хязгааргүй) гэж хасагддаг байв.
"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import models as M
from app.config import settings
from app.database import Base
from app.services import app_settings
from app.services import nested as N
from app.session_logic import close_session_forced


@pytest.fixture
def db(monkeypatch):
    app_settings.invalidate_cache()
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(settings, "transit_max_hours", 4)
    with Session(engine, autoflush=True) as s:
        yield s
    engine.dispose()
    app_settings.invalidate_cache()


def pair(db):
    tpl = M.TariffTemplate(name="t", free_minutes=0, grace_minutes=15, extra_hour_price=1000)
    db.add(tpl)
    db.flush()
    db.add(M.TariffTier(template_id=tpl.id, upto_minutes=60, price=1000))
    db.flush()
    ps = M.ParkingSite(name="P", site_code="PP", transit_max_hours=0, tariff_template_id=tpl.id)
    db.add(ps)
    db.flush()
    cs = M.ParkingSite(name="C", site_code="CC", parent_site_id=ps.id, no_charge=True,
                       tariff_template_id=tpl.id)
    db.add(cs)
    db.flush()
    return ps, cs


def _camsync_close(db, c, t):
    c.exit_time, c.exit_confirmed, c.status = t, True, "AWAITING_PAYMENT"
    close_session_forced(db, c, "camera_sync_exit", "system", create_comp=False)
    db.flush()


@pytest.mark.parametrize("reentered", [True, False])
def test_camsync_late_exit_does_not_restart_parent_after_reentry(db, reentered):
    ps, cs = pair(db)
    now = datetime.utcnow()
    plate = "9901ЖЖА"
    P = M.ParkingSession(site_id=ps.id, plate_number=plate, entry_time=now - timedelta(hours=10),
                         status="OPEN")
    C = M.ParkingSession(site_id=cs.id, plate_number=plate, entry_time=now - timedelta(hours=9),
                         status="OPEN")
    db.add_all([P, C])
    db.flush()
    N.on_inner_entry(db, cs, plate, C.entry_time)
    assert P.paused_since == C.entry_time
    if reentered:
        # -8ц доторх гарц live алдагдсан, -5ц дахин орсон (нээлттэй C-г дахин ашигласан)
        db.add(M.LprEvent(site_id=cs.id, plate_number=plate, lane_dir="entry", accepted=True,
                          created_at=now - timedelta(hours=5)))
        db.flush()
    _camsync_close(db, C, now - timedelta(hours=8))
    if reentered:
        # Машин одоо ДОТОР — гадна тоолуур зогссон хэвээр (гадна гарцад батлагдаагүй
        # хязгаараар барагдана — прод-той ижил), 60 минутаар таслагдаагүй
        assert P.paused_since == C.entry_time and not P.paused_minutes
    else:
        # Дахин ороогүй — логийн гарцаар гадна тоолуур тэр цагаас үргэлжилнэ (хэвээр)
        assert P.paused_since is None and P.paused_minutes == 60


def test_forced_close_without_exit_clears_stale_confirmed_flag(db):
    ps, cs = pair(db)
    now = datetime.utcnow()
    plate = "9902ЖЖА"
    P = M.ParkingSession(site_id=ps.id, plate_number=plate, entry_time=now - timedelta(hours=12),
                         exit_time=now - timedelta(hours=6), exit_confirmed=True, status="MANUAL_CLOSED")
    # хүүхэд: cross-read-ээр хаагдаж → гараар сэргээгдсэн (хуучин exit_confirmed=True үлдсэн)
    C = M.ParkingSession(site_id=cs.id, plate_number=plate, entry_time=now - timedelta(hours=11),
                         exit_confirmed=True, status="OPEN")
    db.add_all([P, C])
    db.flush()
    close_session_forced(db, C, "auto_close", "system", create_comp=False)   # нотолгоогүй
    db.flush()
    assert C.exit_confirmed is False
    added = N.reapply_child_pauses(db, P, P.exit_time, now)
    assert added == 0                                    # таамаг цаг — хасалт өгөхгүй


def test_camsync_close_keeps_confirmed_flag(db):
    """Логийн бодит гарах цагтай хаалт (exit_time урьдчилан тавьсан) батлагдсан хэвээр."""
    ps, cs = pair(db)
    now = datetime.utcnow()
    C = M.ParkingSession(site_id=cs.id, plate_number="9903ЖЖА", entry_time=now - timedelta(hours=3),
                         status="OPEN")
    db.add(C)
    db.flush()
    _camsync_close(db, C, now - timedelta(hours=1))
    assert C.exit_confirmed is True
