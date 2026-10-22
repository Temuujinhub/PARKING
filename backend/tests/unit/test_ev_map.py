"""Газрын зурагт EV цэнэглэгч (2026-10-22) — /api/v1/parkings (+socialpay), /api/v1/sites.

Синтетик SQLite, hub-ийг хуурамчаар солино (бодит сүлжээгүй, timeout-ын тестээс бусад нь
— тэр нь 127.0.0.1 дээрх хариу өгдөггүй сокет). Шалгах зүйл:
  1. OCPP → энгийн төлөвийн зураглал, цэнэглэгчийн нэгтгэсэн төлөв, connector 0
  2. camelCase/snake_case талбарууд, гадна зогсоол л, идэвхгүй цэнэглэгч алгасна
  3. cp_id / charger_key / дотоод id гадагш гарахгүй
  4. hub алдаа/тохируулаагүй → UNKNOWN (5xx биш), 30с кэш (алдааг ч), цэнэглэгчгүй бол hub-гүй
  5. ~1.5с хатуу timeout
  6. админ API: power_kw үүсгэх/засах/шалгалт
"""
import hashlib
import json
import socket
import time
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import auth, models as M, ratelimit
from app.config import settings
from app.database import Base, get_db
from app.routers import ev_router as EV, integration_router as I, socialpay_router as SP
from app.services import app_settings, ev_hub, ev_map

KEY = "pk_ev_map_synthetic_key"


def hub_row(cp_id, online=True, **conns):
    return {"cp_id": cp_id, "online": online, "status": "ACTIVE", "vendor": "Winline",
            "connectors": [{"connector_id": int(k[1:]), "status": v, "active_tx_id": None}
                           for k, v in conns.items()]}


class FakeHub:
    def __init__(self):
        self.calls, self.rows, self.error = 0, [], None

    def __call__(self, timeout):
        self.calls += 1
        if self.error:
            raise self.error
        return self.rows


@pytest.fixture
def ctx(monkeypatch):
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    monkeypatch.setattr(settings, "partner_keys", "")
    monkeypatch.setattr(settings, "evhub_url", "http://hub.invalid:8100")
    hub = FakeHub()
    monkeypatch.setattr(ev_hub, "list_chargers_sync", hub)
    ratelimit._hits.clear()
    app_settings.invalidate_cache()
    ev_map.invalidate_cache()
    with Session(engine, autoflush=False) as db:
        tariff = M.TariffTemplate(name="Synthetic", free_minutes=15, extra_hour_price=1000)
        site = M.ParkingSite(name="Outer", site_code="OUT", tariff_template=tariff,
                             latitude=47.9, longitude=106.9, capacity=50)
        db.add(site); db.flush()
        child = M.ParkingSite(name="Inner", site_code="IN", tariff_template=tariff,
                              parent_site_id=site.id)
        other = M.ParkingSite(name="Other", site_code="OTH", tariff_template=tariff)
        key = M.PartnerKey(name="mobifinance", key_hash=hashlib.sha256(KEY.encode()).hexdigest(),
                           key_prefix=KEY[:10], scopes="read,pay")
        user = M.User(username="admin", password_hash="unused", role="SUPER_ADMIN")
        db.add_all([child, other, key, user]); db.flush()
        db.add_all([
            M.EvCharger(site_id=site.id, cp_id="WL-0001", name="DC #1", charger_key="AAAA",
                        connector_count=2, power_kw=40),
            # нэр = cp_id (бүртгэлийн анхдагч) → «Цэнэглэгч 2» болж нэрлэгдэнэ
            M.EvCharger(site_id=site.id, cp_id="WL-0002", name="WL-0002", charger_key="BBBB",
                        connector_count=2, power_kw=7.4),
            M.EvCharger(site_id=site.id, cp_id="WL-0003", name="Унтраасан", charger_key="CCCC",
                        connector_count=2, is_active=False),
            M.EvCharger(site_id=child.id, cp_id="WL-0009", name="Доторх", charger_key="DDDD",
                        connector_count=1),
        ])
        db.commit()
        # created_at дарааллыг тогтвортой болгоно (нэрлэлт «Цэнэглэгч N»)
        for i, c in enumerate(db.query(M.EvCharger).order_by(M.EvCharger.cp_id)):
            c.created_at = datetime(2026, 10, 1) + timedelta(minutes=i)
        db.commit()
        app = FastAPI()
        for r in (I.router, SP.router, SP.neutral_router, EV.router):
            app.include_router(r)
        SP.install_error_handlers(app)
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[auth.get_current_user] = lambda: user
        with TestClient(app) as client:
            yield type("Ctx", (), dict(db=db, client=client, site=site, child=child,
                                       other=other, hub=hub, user=user))()
    ev_map.invalidate_cache()
    app_settings.invalidate_cache()
    engine.dispose()


def parkings(c, path="/api/v1/parkings"):
    r = c.client.get(path, headers={"X-API-Key": KEY})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["Success"] is True
    return {p["siteCode"]: p for p in body["data"]}, r.text


def sites(c):
    r = c.client.get("/api/v1/sites", headers={"X-API-Key": KEY})
    assert r.status_code == 200, r.text
    return {s["site_code"]: s for s in r.json()["sites"]}, r.text


# ─────────────────────────── зураглал ───────────────────────────

@pytest.mark.parametrize("ocpp, simple", [
    ("Available", "AVAILABLE"), ("Preparing", "CHARGING"), ("Charging", "CHARGING"),
    ("SuspendedEV", "CHARGING"), ("SuspendedEVSE", "CHARGING"), ("Finishing", "CHARGING"),
    ("Reserved", "CHARGING"), ("Unavailable", "OFFLINE"), ("Faulted", "FAULTED"),
    ("Unknown", "UNKNOWN"), (None, "UNKNOWN"), ("charging", "UNKNOWN"),
])
def test_ocpp_status_mapping(ocpp, simple):
    assert ev_map.map_status(ocpp) == simple


def test_charger_level_status_rules():
    ch = M.EvCharger(cp_id="X", connector_count=2)
    st = ev_map.charger_live_status
    assert st(ch, None) == ("UNKNOWN", [(1, "UNKNOWN"), (2, "UNKNOWN")])
    assert st(ch, {}) == ("OFFLINE", [(1, "OFFLINE"), (2, "OFFLINE")])      # hub-д алга
    assert st(ch, {"X": hub_row("X", online=False, c1="Available")})[0] == "OFFLINE"
    assert st(ch, {"X": hub_row("X", c1="Charging", c2="Available")})[0] == "AVAILABLE"
    assert st(ch, {"X": hub_row("X", c1="Charging", c2="Preparing")})[0] == "CHARGING"
    assert st(ch, {"X": hub_row("X", c1="Faulted", c2="Faulted")})[0] == "FAULTED"
    assert st(ch, {"X": hub_row("X", c1="Faulted", c2="Unavailable")})[0] == "FAULTED"
    assert st(ch, {"X": hub_row("X", c1="Unavailable", c2="Unavailable")})[0] == "OFFLINE"
    assert st(ch, {"X": hub_row("X")}) == ("UNKNOWN", [(1, "UNKNOWN"), (2, "UNKNOWN")])
    # connector 0 = цэнэглэгч бүхэлдээ гэмтэлтэй → сул бууц ч ашиглах боломжгүй
    assert st(ch, {"X": hub_row("X", c0="Faulted", c1="Available", c2="Charging")}) == \
        ("CHARGING", [(1, "FAULTED"), (2, "CHARGING")])
    assert st(ch, {"X": hub_row("X", c0="Unavailable", c1="Available")}) == \
        ("OFFLINE", [(1, "OFFLINE"), (2, "OFFLINE")])


# ─────────────────────────── газрын зураг ───────────────────────────

def test_parkings_and_sites_show_ev_chargers_with_live_status(ctx):
    ctx.hub.rows = [hub_row("WL-0001", c0="Available", c1="Charging", c2="Available"),
                    hub_row("WL-0002", c1="Faulted", c2="Faulted"),
                    hub_row("WL-0009", c1="Available")]
    for path in ("/api/v1/parkings", "/api/v1/socialpay/parkings"):
        data, raw = parkings(ctx, path)
        assert "IN" not in data
        out = data["OUT"]
        assert out["hasEvCharger"] is True
        assert out["evConnectorsTotal"] == 4 and out["evConnectorsFree"] == 1
        assert out["evChargers"] == [
            {"name": "DC #1", "powerKw": 40, "connectorCount": 2, "status": "AVAILABLE",
             "connectors": [{"connectorId": 1, "status": "CHARGING"},
                            {"connectorId": 2, "status": "AVAILABLE"}]},
            {"name": "Цэнэглэгч 2", "powerKw": 7.4, "connectorCount": 2, "status": "FAULTED",
             "connectors": [{"connectorId": 1, "status": "FAULTED"},
                            {"connectorId": 2, "status": "FAULTED"}]},
        ]
        oth = data["OTH"]
        assert oth["hasEvCharger"] is False and oth["evChargers"] == []
        assert oth["evConnectorsTotal"] == 0 and oth["evConnectorsFree"] == 0
        # Захиалгын талбарууд (1ea3207) хэвээр
        assert {"reservable", "reservationSpots", "reservationFreeNow"} <= set(out)
        for secret in ("WL-0001", "WL-0002", "AAAA", "BBBB", "Унтраасан", "cpId", "chargerKey"):
            assert secret not in raw
    rows, raw = sites(ctx)
    out = rows["OUT"]
    assert out["has_ev_charger"] is True and out["ev_connectors_free"] == 1
    assert out["ev_connectors_total"] == 4
    assert out["ev_chargers"][0] == {
        "name": "DC #1", "power_kw": 40, "connector_count": 2, "status": "AVAILABLE",
        "connectors": [{"connector_id": 1, "status": "CHARGING"},
                       {"connector_id": 2, "status": "AVAILABLE"}]}
    # доторх зогсоол /sites-д мөр болж гарна, гэхдээ EV-гүй (газрын зурагт тусдаа цэг биш)
    assert rows["IN"]["has_ev_charger"] is False and rows["IN"]["ev_chargers"] == []
    assert "WL-000" not in raw and "DDDD" not in raw
    # 3 хүсэлт — hub руу ганц дуудлага (30с кэш)
    assert ctx.hub.calls == 1


def test_hub_error_gives_unknown_never_5xx_and_error_is_cached(ctx):
    ctx.hub.error = ev_hub.HubUnavailable("hub холбогдсонгүй: timed out")
    for _ in range(2):
        data, _ = parkings(ctx)
        out = data["OUT"]
        assert out["hasEvCharger"] is True and out["evConnectorsTotal"] == 4
        assert out["evConnectorsFree"] is None
        assert {ch["status"] for ch in out["evChargers"]} == {"UNKNOWN"}
        assert {x["status"] for ch in out["evChargers"] for x in ch["connectors"]} == {"UNKNOWN"}
    assert ctx.hub.calls == 1
    # Буруу хэлбэрийн хариу ч UNKNOWN (бүгдийг OFFLINE гэж худал хэлэхгүй)
    ev_map.invalidate_cache()
    ctx.hub.error, ctx.hub.rows = None, {"chargers": []}
    data, _ = parkings(ctx)
    assert data["OUT"]["evChargers"][0]["status"] == "UNKNOWN"


def test_cache_expires_and_refetches(ctx, monkeypatch):
    ctx.hub.rows = [hub_row("WL-0001", c1="Available", c2="Available")]
    assert parkings(ctx)[0]["OUT"]["evConnectorsFree"] == 2
    ctx.hub.rows = [hub_row("WL-0001", c1="Charging", c2="Charging")]
    assert parkings(ctx)[0]["OUT"]["evConnectorsFree"] == 2          # кэшээс
    now = time.monotonic()
    monkeypatch.setattr(ev_map.time, "monotonic", lambda: now + ev_map.CACHE_SEC + 1)
    out = parkings(ctx)[0]["OUT"]
    assert out["evConnectorsFree"] == 0 and out["evChargers"][0]["status"] == "CHARGING"
    assert ctx.hub.calls == 2


def test_no_hub_call_without_chargers_or_hub_url(ctx, monkeypatch):
    ctx.db.query(M.EvCharger).update({"is_active": False}); ctx.db.commit()
    data, _ = parkings(ctx)
    assert data["OUT"]["hasEvCharger"] is False and ctx.hub.calls == 0
    ctx.db.query(M.EvCharger).update({"is_active": True}); ctx.db.commit()
    monkeypatch.setattr(settings, "evhub_url", "")
    data, _ = parkings(ctx)
    assert data["OUT"]["evChargers"][0]["status"] == "UNKNOWN" and ctx.hub.calls == 0


def test_real_hub_timeout_is_strict(ctx, monkeypatch):
    """Хариу өгдөггүй hub (холболт backlog-д хүлээгдэнэ) — ~1с-д тасарч UNKNOWN."""
    monkeypatch.undo()   # жинхэнэ list_chargers_sync
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0)); srv.listen(1)
    try:
        monkeypatch.setattr(settings, "partner_keys", "")
        monkeypatch.setattr(settings, "evhub_url", f"http://127.0.0.1:{srv.getsockname()[1]}")
        ev_map.invalidate_cache()
        t0 = time.monotonic()
        assert ev_map.live_chargers() is None
        assert time.monotonic() - t0 < 1.8
        t0 = time.monotonic()
        assert ev_map.live_chargers() is None        # алдаа кэшлэгдсэн — дахин хүлээхгүй
        assert time.monotonic() - t0 < 0.1
    finally:
        srv.close()
        ev_map.invalidate_cache()


# ─────────────────────────── админ API ───────────────────────────

def test_admin_power_kw_create_update_validate(ctx, monkeypatch):
    async def hub_ok(cp_id, body):
        return {"ok": True}
    monkeypatch.setattr(ev_hub, "update_charger", hub_ok)

    async def no_hub():
        return []
    monkeypatch.setattr(ev_hub, "list_chargers", no_hub)
    r = ctx.client.post("/api/admin/ev/chargers",
                        json={"cp_id": "WL-0100", "site_id": ctx.site.id, "power_kw": "60"})
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    assert float(ctx.db.get(M.EvCharger, cid).power_kw) == 60
    r = ctx.client.post("/api/admin/ev/chargers",
                        json={"cp_id": "WL-0101", "site_id": ctx.site.id})
    assert r.status_code == 201 and ctx.db.get(M.EvCharger, r.json()["id"]).power_kw is None
    for bad in (0, -5, 5000, "abc"):
        r = ctx.client.put(f"/api/admin/ev/chargers/{cid}", json={"power_kw": bad})
        assert r.status_code == 422, (bad, r.text)
    r = ctx.client.put(f"/api/admin/ev/chargers/{cid}", json={"power_kw": 22, "name": "AC"})
    assert r.status_code == 200, r.text
    ctx.db.expire_all()
    assert float(ctx.db.get(M.EvCharger, cid).power_kw) == 22
    listed = {c["cp_id"]: c for c in ctx.client.get("/api/admin/ev/chargers").json()["chargers"]}
    assert listed["WL-0100"]["power_kw"] == 22
    assert listed["WL-0101"]["power_kw"] is None
    r = ctx.client.put(f"/api/admin/ev/chargers/{cid}", json={"power_kw": None})
    assert r.status_code == 200
    ctx.db.expire_all()
    assert ctx.db.get(M.EvCharger, cid).power_kw is None
    # нууц үг audit-д бичигдэхгүй
    ctx.client.put(f"/api/admin/ev/chargers/{cid}", json={"auth_password": "s3cret!"})
    logs = ctx.db.query(M.AuditLog).filter(M.AuditLog.action == "EV_CHARGER_UPDATE").all()
    assert logs and all("s3cret!" not in json.dumps(l.detail, ensure_ascii=False) for l in logs)
