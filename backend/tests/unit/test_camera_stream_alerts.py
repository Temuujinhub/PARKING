"""Камерын стрим сэргэхгүй байгаа анхааруулга + «Камерыг дахин асаах» товч.

2026-11-05 BTALBAI орох камер 6 цаг 87% event алдсан, reboot хийхэд засагдсан.
Одоо log_tail-ийн гэрч дахин холболтоор засагдахгүй гэдгийг нотолмогц
анхааруулга үүсгэж, оператор/админ UI-аас камерыг дахин асаана.

Шалгах зүйл:
  • анхааруулга үүсч, шинэ алдагдалгүй бол өөрөө арилна
  • оператор зөвхөн ӨӨРИЙН зогсоолын, ИДЭВХТЭЙ анхааруулгатай камерыг асаана
  • админ (settings) анхааруулгагүй ч асаана; reboot амжилттай бол анхааруулга арилна
"""
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import auth, models as M
from app.database import Base, get_db
from app.routers import admin_router as R
from app.services import camera_recovery, cgi_poller, log_tail


def _reset():
    log_tail._alerts.clear()
    log_tail._witness_hist.clear()
    log_tail._witness_alarm.clear()
    cgi_poller._force.clear()


def _raise_alarm(device_id, site_id, monkeypatch, missed=1):
    """3 удаа cooldown-оор дахин холбуулж анхааруулга гаргана."""
    clock = [1000.0]
    monkeypatch.setattr(log_tail.time, "monotonic", lambda: clock[0])
    for _ in range(log_tail.settings.log_tail_witness_escalate_after):
        log_tail._witness(device_id, site_id, "б.талбай-10", "10.0.116.10", missed)
        clock[0] += log_tail.settings.log_tail_witness_cooldown_sec + 1


# ── log_tail-ийн анхааруулгын төлөв ─────────────────────────────────────────
def test_гурван_дахин_холболтын_дараа_анхааруулга_үүснэ(monkeypatch):
    _reset()
    _raise_alarm("dev-a", "site-1", monkeypatch, missed=2)
    rows = log_tail.active_alerts()
    assert len(rows) == 1
    a = rows[0]
    assert a["device_id"] == "dev-a" and a["site_id"] == "site-1"
    assert a["reconnects"] == 3 and a["missed"] == 2


def test_анхааруулгагүй_үед_жагсаалт_хоосон(monkeypatch):
    _reset()
    clock = [1000.0]
    monkeypatch.setattr(log_tail.time, "monotonic", lambda: clock[0])
    log_tail._witness("dev-b", "site-1", "x", "10.0.0.1", 1)
    assert log_tail.active_alerts() == []


def test_дараагийн_алдагдал_тоог_нэмнэ(monkeypatch):
    _reset()
    _raise_alarm("dev-c", "site-1", monkeypatch, missed=1)
    log_tail._witness("dev-c", "site-1", "б.талбай-10", "10.0.116.10", 5)
    assert log_tail.active_alerts()[0]["missed"] == 6


def test_шинэ_алдагдалгүй_бол_анхааруулга_өөрөө_арилна(monkeypatch):
    _reset()
    _raise_alarm("dev-d", "site-1", monkeypatch)
    later = datetime.utcnow() + timedelta(minutes=log_tail.settings.log_tail_witness_window_min + 1)
    monkeypatch.setattr(log_tail, "_utcnow", lambda: later)
    assert log_tail.active_alerts() == []


def test_зогсоолоор_шүүнэ(monkeypatch):
    _reset()
    _raise_alarm("dev-e", "site-1", monkeypatch)
    assert log_tail.active_alerts(["site-2"]) == []
    assert len(log_tail.active_alerts(["site-1"])) == 1


def test_clear_alert_гэрчийн_түүхийг_тэглэнэ(monkeypatch):
    _reset()
    _raise_alarm("dev-f", "site-1", monkeypatch)
    log_tail.clear_alert("dev-f")
    assert not log_tail.has_alert("dev-f")
    assert "dev-f" not in log_tail._witness_hist and "dev-f" not in log_tail._witness_alarm


# ── HTTP: жагсаалт ба дахин асаах ───────────────────────────────────────────
@pytest.fixture
def ctx(monkeypatch):
    _reset()
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    reboot = AsyncMock(return_value="")
    monkeypatch.setattr(camera_recovery, "reboot_camera", reboot)
    with Session(engine, autoflush=False) as db:
        s1 = M.ParkingSite(name="Synthetic 1", site_code="SYN1", is_active=True)
        s2 = M.ParkingSite(name="Synthetic 2", site_code="SYN2", is_active=True)
        db.add_all([s1, s2]); db.flush()
        cam1 = M.Device(site_id=s1.id, name="орох-1", device_type="camera", lane_dir="entry",
                        ip_address="203.0.113.10", status="active")
        cam2 = M.Device(site_id=s2.id, name="орох-2", device_type="camera", lane_dir="entry",
                        ip_address="203.0.113.20", status="active")
        op = M.User(username="op", password_hash="unused", role="OPERATOR", site_id=s1.id)
        admin = M.User(username="adm", password_hash="unused", role="SUPER_ADMIN")
        db.add_all([cam1, cam2, op, admin]); db.commit()
        who = {"user": op}
        app = FastAPI(); app.include_router(R.router)
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[auth.get_current_user] = lambda: who["user"]
        with TestClient(app) as client:
            yield dict(db=db, client=client, who=who, op=op, admin=admin,
                       cam1=cam1, cam2=cam2, s1=s1, s2=s2, reboot=reboot)
    engine.dispose()
    _reset()


def test_оператор_өөрийн_зогсоолын_анхааруулгыг_л_харна(ctx, monkeypatch):
    _raise_alarm(ctx["cam1"].id, ctx["s1"].id, monkeypatch)
    _raise_alarm(ctx["cam2"].id, ctx["s2"].id, monkeypatch)
    rows = ctx["client"].get("/api/admin/camera-alerts").json()
    assert [r["device_id"] for r in rows] == [ctx["cam1"].id]
    assert rows[0]["can_reboot"] is True


def test_оператор_анхааруулгатай_камерыг_асаана(ctx, monkeypatch):
    _raise_alarm(ctx["cam1"].id, ctx["s1"].id, monkeypatch)
    r = ctx["client"].post(f"/api/admin/camera-alerts/{ctx['cam1'].id}/reboot")
    assert r.status_code == 200 and r.json()["ok"]
    ctx["reboot"].assert_awaited_once()
    assert not log_tail.has_alert(ctx["cam1"].id)
    log = ctx["db"].query(M.AuditLog).filter_by(action="CAMERA_REBOOT").one()
    assert log.username == "op" and log.detail["reason"] == "stream_loss_alert"


def test_оператор_анхааруулгагүй_камерыг_асааж_чадахгүй(ctx):
    r = ctx["client"].post(f"/api/admin/camera-alerts/{ctx['cam1'].id}/reboot")
    assert r.status_code == 409
    ctx["reboot"].assert_not_awaited()


def test_оператор_өөр_зогсоолын_камерыг_асааж_чадахгүй(ctx, monkeypatch):
    _raise_alarm(ctx["cam2"].id, ctx["s2"].id, monkeypatch)
    r = ctx["client"].post(f"/api/admin/camera-alerts/{ctx['cam2'].id}/reboot")
    assert r.status_code == 403
    ctx["reboot"].assert_not_awaited()


def test_админ_анхааруулгагүй_ч_асаана(ctx):
    ctx["who"]["user"] = ctx["admin"]
    r = ctx["client"].post(f"/api/admin/camera-alerts/{ctx['cam2'].id}/reboot")
    assert r.status_code == 200 and r.json()["ok"]


def test_reboot_амжилтгүй_бол_анхааруулга_үлдэнэ(ctx, monkeypatch):
    _raise_alarm(ctx["cam1"].id, ctx["s1"].id, monkeypatch)
    ctx["reboot"].return_value = "ConnectError: timeout"
    r = ctx["client"].post(f"/api/admin/camera-alerts/{ctx['cam1'].id}/reboot")
    assert r.status_code == 200 and not r.json()["ok"]
    assert log_tail.has_alert(ctx["cam1"].id)


def test_хаалтны_команд_хүлээгдэж_байвал_асаахгүй(ctx, monkeypatch):
    _raise_alarm(ctx["cam1"].id, ctx["s1"].id, monkeypatch)
    ctx["db"].add(M.BarrierCommand(device_id=ctx["cam1"].id, command="open", command_source="manual", status="PENDING"))
    ctx["db"].commit()
    r = ctx["client"].post(f"/api/admin/camera-alerts/{ctx['cam1'].id}/reboot")
    assert r.status_code == 409
    ctx["reboot"].assert_not_awaited()


def test_касс_эрхгүй_хэрэглэгч_хандахгүй(ctx):
    ctx["who"]["user"] = M.User(username="hr", password_hash="x", role="HR")
    assert ctx["client"].get("/api/admin/camera-alerts").status_code == 403
    assert ctx["client"].post(f"/api/admin/camera-alerts/{ctx['cam1'].id}/reboot").status_code == 403
