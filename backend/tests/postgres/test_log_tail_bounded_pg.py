"""Disposable PostgreSQL only (PARKING_TEST_DATABASE_URL): log_tail-ийн «чимээгүй камер» хайлт
lpr_events-ийг ХУГАЦААГААР ХЯЗГААРЛАН уншина, утга нь өөрчлөгдөөгүй.

Яагаад (2026-11-13, «POS-ын дэлгэц удаан»): `_silent_devices` камер бүрийн max(created_at)-ыг
хугацааны хязгааргүй авч, 90 хоногийн lpr_events-ийг (device_id-д индексгүй) бүхэлд нь уншиж,
мөр бүрийн raw JSON-ыг задалдаг байв. 20с тутам event loop дээр синхрон ажиллаж PROD-ын backend-ийг
~8.5с БҮХЭЛД НЬ царцаадаг байсан (гаднаас хэмжсэн — /api/health хүртэл хүлээсэн).

    PARKING_TEST_DATABASE_URL=postgresql+psycopg2://…/t_integ \
        venv/bin/python -m pytest tests/postgres/test_log_tail_bounded_pg.py -q
"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from test_payment_migrations import engine, URL  # noqa: F401 — fixture
from app import migrations, models as M
from app.config import settings
from app.services import log_tail as LT

pytestmark = pytest.mark.skipif(not URL, reason="Disposable PostgreSQL DSN required")


def test_silent_devices_bounded_by_time_and_semantics_unchanged(engine):
    migrations.run_migrations()
    now = datetime.utcnow()
    old = now - timedelta(seconds=settings.log_tail_silence_sec + 60)
    with Session(engine) as db:
        site = M.ParkingSite(name="LogTail PG", site_code="LTPG", is_active=True)
        db.add(site)
        db.flush()

        def cam(name, ip):
            return M.Device(site_id=site.id, name=name, device_type="camera", device_key="lt-" + name,
                            lane_dir="entry", status="active", ip_address=ip)

        live, quiet, injected, never = (cam("live", "10.9.0.1"), cam("quiet", "10.9.0.2"),
                                        cam("injected", "10.9.0.3"), cam("never", "10.9.0.4"))
        db.add_all([live, quiet, injected, never])
        db.flush()

        def ev(dev, at, raw, accepted=True, plate="1234УБА"):
            return M.LprEvent(site_id=site.id, device_id=dev.id, plate_number=plate, lane_dir="entry",
                              accepted=accepted, raw=raw, created_at=at)

        db.add_all([
            ev(live, now, {}), ev(live, old, {}),            # саяхны уншилттай → амьд
            ev(quiet, old, {}),                              # зөвхөн хуучин уншилт → чимээгүй
            ev(quiet, now, {}, accepted=False, plate="9999ЖЖЖ"),   # татгалзсан уншилт тооцохгүй
            ev(injected, now, {"log_tail": True}),           # өөрийн оруулсан → «амьд» гэж хуурагдахгүй
        ])
        db.commit()

        sqls = []

        def capture(conn, cursor, statement, parameters, context, executemany):
            sqls.append(statement)

        event.listen(engine, "before_cursor_execute", capture)
        try:
            silent = {d.name for d in LT._silent_devices_sync(db, site.id)}
        finally:
            event.remove(engine, "before_cursor_execute", capture)

    assert silent == {"quiet", "injected", "never"}
    lpr = [s for s in sqls if "lpr_events" in s]
    assert lpr, "lpr_events-ийг асуусан query олдсонгүй"
    # Бүх lpr_events query хугацааны доод хязгаартай (индекс ашиглана, хүснэгтийг бүхэлд нь уншихгүй)
    assert all("lpr_events.created_at >=" in s for s in lpr), lpr
