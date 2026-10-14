"""SocialPay (Голомт): машин бүртгэл/устгал, bill, байршил, орох/гарах мэдэгдэл.

Зөвхөн in-memory SQLite + локал хуурамч HTTP сервер — банк руу огт хандахгүй.
PostgreSQL-ийн түгжээ/partial unique: tests/postgres/test_partner_vehicles_pg.py.
"""
import asyncio
import hashlib
import json
import threading
import time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import auth, models as M, ratelimit
from app.config import settings
from app.database import Base, get_db
from app.routers import admin_router as A, integration_router as I, socialpay_router as SP
from app.services import app_settings
from app.services import partner_vehicles as PV
from app.session_logic import amount_due, session_fee_info

KEY = "pk_socialpay_synthetic_key"
KEY2 = "pk_other_bank_synthetic_key"
KEY_RO = "pk_readonly_synthetic_key"
NOW = datetime(2026, 10, 14, 6, 0, 0)


def _key(name, raw, scopes, **kw):
    return M.PartnerKey(name=name, key_hash=hashlib.sha256(raw.encode()).hexdigest(),
                        key_prefix=raw[:10], scopes=scopes, **kw)


@pytest.fixture
def ctx(monkeypatch):
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with engine.begin() as c:
        c.execute(text("DROP INDEX uq_active_session"))
        c.execute(text("CREATE UNIQUE INDEX uq_active_session ON parking_sessions(site_id,plate_number) "
                       "WHERE status IN ('OPEN','AWAITING_PAYMENT','PAID')"))
    monkeypatch.setattr(settings, "vat_inclusive", True)
    monkeypatch.setattr(settings, "vat_rate", 0.1)
    monkeypatch.setattr(settings, "partner_keys", "")
    ratelimit._hits.clear()
    app_settings.invalidate_cache()
    with Session(engine, autoflush=False) as db:
        tariff = M.TariffTemplate(name="Synthetic", free_minutes=15, extra_hour_price=1000)
        tariff.tiers = [M.TariffTier(upto_minutes=60, price=1000), M.TariffTier(upto_minutes=120, price=2000)]
        site = M.ParkingSite(name="Outer", site_code="OUT", tariff_template=tariff,
                             address="Synthetic street", latitude=47.9, longitude=106.9, capacity=50)
        db.add(site); db.flush()
        child = M.ParkingSite(name="Inner", site_code="IN", tariff_template=tariff,
                              parent_site_id=site.id)
        other = M.ParkingSite(name="Other", site_code="OTH", tariff_template=tariff)
        gated = M.ParkingSite(name="Gated", site_code="GATED", tariff_template=tariff,
                              registered_only=True)
        key = _key("socialpay", KEY, "read,pay,vehicles",
                   vehicle_webhook_url="http://127.0.0.1:9/parking/notification")
        key2 = _key("otherbank", KEY2, "read,vehicles",
                    vehicle_webhook_url="http://127.0.0.1:9/other")
        ro = _key("readonly", KEY_RO, "read,pay")
        user = M.User(username="admin", password_hash="unused", role="SUPER_ADMIN")
        db.add_all([child, other, gated, key, key2, ro, user]); db.commit()
        app = FastAPI()
        for r in (I.router, SP.router, A.router):
            app.include_router(r)
        SP.install_error_handlers(app)
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[auth.get_current_user] = lambda: user
        with TestClient(app) as client:
            yield type("Ctx", (), dict(db=db, engine=engine, client=client, site=site, child=child,
                                       other=other, gated=gated, key=key, key2=key2, ro=ro,
                                       user=user))()
    app_settings.invalidate_cache()
    engine.dispose()


def post(c, path, body, key=KEY):
    headers = {"X-API-Key": key} if key else {}
    return c.client.post(f"/api/v1/socialpay/{path}", json=body, headers=headers)


def sub(c, plate="7886УКР", user="100264", key=None, created_at=None):
    row = M.PartnerVehicle(partner_key_id=(key or c.key).id, plate_number=plate,
                           external_user_id=user, created_via="test",
                           created_at=created_at or NOW - timedelta(days=1))
    c.db.add(row); c.db.commit()
    return row


def stay(c, plate="7886УКР", site=None, entry=None, created=None, **kw):
    entry = entry or NOW - timedelta(minutes=5)
    s = M.ParkingSession(site_id=(site or c.site).id, plate_number=plate, entry_time=entry,
                         created_at=created or entry, status=kw.pop("status", "OPEN"), **kw)
    c.db.add(s); c.db.commit()
    return s


def close(c, s, at, *, confirmed=True, device=False, status="FREE"):
    s.exit_time, s.exit_confirmed, s.status = at, confirmed, status
    s.duration_minutes = int((at - s.entry_time).total_seconds() // 60)
    s.total_fee = 0
    if device:
        cam = M.Device(site_id=s.site_id, name="exit", device_type="camera", lane_dir="exit",
                       device_key=f"cam-{s.id[:8]}")
        c.db.add(cam); c.db.flush()
        s.exit_device_id = cam.id
    c.db.commit()


def events(c, **flt):
    q = c.db.query(M.PartnerVehicleEvent)
    for k, v in flt.items():
        q = q.filter(getattr(M.PartnerVehicleEvent, k) == v)
    return q.order_by(M.PartnerVehicleEvent.created_at).all()


# ─────────────────────────── 4.1 / 4.2 ───────────────────────────

def test_register_exact_shape_idempotent_and_number_user_id(ctx):
    r = post(ctx, "register", {"plateNo": "7886УКР", "userId": "100264"})
    assert r.status_code == 200 and r.json() == {"Success": True, "Message": "OK"}
    assert r.headers["content-type"].startswith("application/json")
    # userId тоогоор ирсэн ч ижил хэрэглэгч; зай/жижиг үсэгтэй дугаар жигдэрнэ
    r = post(ctx, "register", {"plateNo": " 7886 укр ", "userId": 100264})
    assert r.json() == {"Success": True, "Message": "ALREADY_REGISTERED"}
    rows = ctx.db.query(M.PartnerVehicle).all()
    assert len(rows) == 1 and rows[0].plate_number == "7886УКР" and rows[0].external_user_id == "100264"
    assert rows[0].created_via == "socialpay"
    audit = ctx.db.query(M.AuditLog).filter_by(action="PARTNER_VEHICLE_REGISTER").all()
    assert len(audit) == 1 and audit[0].username == "partner:socialpay"


def test_auth_scope_and_error_shape(ctx):
    for key, code, msg in ((None, 401, "UNAUTHORIZED"), ("pk_wrong", 401, "UNAUTHORIZED"),
                           (KEY_RO, 403, "FORBIDDEN")):
        r = post(ctx, "register", {"plateNo": "7886УКР", "userId": "1"}, key=key)
        assert r.status_code == code, r.text
        body = r.json()
        assert body["Success"] is False and body["Message"] == msg and "detail" not in body
    ctx.key.is_active = False; ctx.db.commit()
    assert post(ctx, "register", {"plateNo": "7886УКР", "userId": "1"}).status_code == 401
    assert ctx.db.query(M.PartnerVehicle).count() == 0


@pytest.mark.parametrize("body,code", [
    ({"plateNo": "7886UKR", "userId": "1"}, "INVALID_PLATE"),      # латин — таамаглахгүй
    ({"plateNo": "1234YBA", "userId": "1"}, "INVALID_PLATE"),      # homoglyph ч хориотой
    ({"plateNo": "12345", "userId": "1"}, "INVALID_PLATE"),
    ({"plateNo": "1234УБ", "userId": "1"}, "INVALID_PLATE"),       # тайрагдсан уншилт
    ({"plateNo": 7886, "userId": "1"}, "INVALID_PLATE"),
    ({"plateNo": "Х" * 40, "userId": "1"}, "INVALID_PLATE"),
    ({"userId": "1"}, "INVALID_PLATE"),
    ({"plateNo": "7886УКР"}, "INVALID_USER_ID"),
    ({"plateNo": "7886УКР", "userId": True}, "INVALID_USER_ID"),
    ({"plateNo": "7886УКР", "userId": ""}, "INVALID_USER_ID"),
    ({"plateNo": "7886УКР", "userId": "a b"}, "INVALID_USER_ID"),
    ({"plateNo": "7886УКР", "userId": "x" * 65}, "INVALID_USER_ID"),
    ({"plateNo": "7886УКР", "userId": 1.5}, "INVALID_USER_ID"),
    ({"plateNo": "7886УКР", "userId": None}, "INVALID_USER_ID"),
])
def test_validation_rejects_junk(ctx, body, code):
    r = post(ctx, "register", body)
    assert r.status_code == 400 and r.json()["Message"] == code and r.json()["Success"] is False
    assert ctx.db.query(M.PartnerVehicle).count() == 0


def test_field_names_case_insensitive(ctx):
    assert post(ctx, "register", {"PlateNo": "7886УКР", "UserId": 7}).json()["Message"] == "OK"
    assert PV.is_registered_by(ctx.db, ctx.key.id, "7886УКР", "7")


def test_invalid_json_and_diplomatic_formats(ctx):
    r = ctx.client.post("/api/v1/socialpay/register", content=b"not json",
                        headers={"X-API-Key": KEY, "Content-Type": "application/json"})
    assert r.status_code == 400 and r.json()["Message"] == "INVALID_JSON"
    assert post(ctx, "register", [1, 2]).json()["Message"] == "INVALID_JSON"
    for plate in ("ДК0188", "1302ДК", "9914АК", "1234ӨҮЁ"):
        assert post(ctx, "register", {"plateNo": plate, "userId": "u-1"}).json()["Message"] == "OK"


def test_limit_per_user_and_shared_plate(ctx, monkeypatch):
    monkeypatch.setattr(settings, "partner_vehicle_max_per_user", 3)
    for i in range(3):
        assert post(ctx, "register", {"plateNo": f"100{i}УБА", "userId": "u1"}).json()["Message"] == "OK"
    r = post(ctx, "register", {"plateNo": "1009УБА", "userId": "u1"})
    assert r.status_code == 409 and r.json()["Message"] == "LIMIT_EXCEEDED"
    # Давтан бүртгэл хязгаарт тооцогдохгүй (идемпотент)
    assert post(ctx, "register", {"plateNo": "1000УБА", "userId": "u1"}).json()["Message"] == "ALREADY_REGISTERED"
    # Нэг дугаарыг өөр хэрэглэгч, өөр түнш бүртгэж болно
    assert post(ctx, "register", {"plateNo": "1000УБА", "userId": "u2"}).json()["Message"] == "OK"
    assert post(ctx, "register", {"plateNo": "1000УБА", "userId": "u1"}, key=KEY2).json()["Message"] == "OK"
    assert post(ctx, "delete", {"plateNo": "1000УБА", "userId": "u1"}).json()["Message"] == "OK"
    assert post(ctx, "register", {"plateNo": "1009УБА", "userId": "u1"}).json()["Message"] == "OK"


def test_delete_idempotent_soft_delete_and_reregister(ctx):
    assert post(ctx, "register", {"plateNo": "7886УКР", "userId": "100264"}).json()["Message"] == "OK"
    r = post(ctx, "delete", {"plateNo": "7886УКР", "userId": 100264})
    assert r.status_code == 200 and r.json() == {"Success": True, "Message": "OK"}
    again = post(ctx, "delete", {"plateNo": "7886УКР", "userId": "100264"})
    assert again.status_code == 200 and again.json() == {"Success": True, "Message": "NOT_FOUND"}
    # Өөр хэрэглэгчийн бүртгэлийг устгах боломжгүй
    post(ctx, "register", {"plateNo": "7886УКР", "userId": "A"})
    assert post(ctx, "delete", {"plateNo": "7886УКР", "userId": "B"}).json()["Message"] == "NOT_FOUND"
    assert post(ctx, "delete", {"plateNo": "7886УКР", "userId": "A"}, key=KEY2).json()["Message"] == "NOT_FOUND"
    assert PV.is_registered_by(ctx.db, ctx.key.id, "7886УКР", "A")
    assert post(ctx, "register", {"plateNo": "7886УКР", "userId": "100264"}).json()["Message"] == "OK"
    rows = ctx.db.query(M.PartnerVehicle).filter_by(external_user_id="100264").all()
    assert len(rows) == 2 and sum(r.deleted_at is None for r in rows) == 1
    assert ctx.db.query(M.AuditLog).filter_by(action="PARTNER_VEHICLE_DELETE").count() == 1


def test_rate_limit(ctx, monkeypatch):
    monkeypatch.setattr(settings, "partner_vehicle_rate_per_min", 2)
    assert post(ctx, "register", {"plateNo": "7886УКР", "userId": "1"}).status_code == 200
    assert post(ctx, "delete", {"plateNo": "7886УКР", "userId": "1"}).status_code == 200
    r = post(ctx, "register", {"plateNo": "7886УКР", "userId": "1"})
    assert r.status_code == 429 and r.json()["Message"] == "RATE_LIMITED"


# ─────────────────────────── Bill ───────────────────────────

def test_bill_numbers_match_session_fee_info_and_privacy(ctx):
    sub(ctx, user="A")
    entered = datetime.utcnow().replace(microsecond=375000) - timedelta(minutes=40)
    s = stay(ctx, entry=entered)
    stay(ctx, plate="7886УКР", site=ctx.child, entry=entered + timedelta(minutes=5))
    r = post(ctx, "bill", {"plateNo": "7886УКР", "userId": "A"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["Success"] is True and body["Message"] == "OK"
    d = body["data"]
    ctx.db.expire_all()
    s = ctx.db.get(M.ParkingSession, s.id)
    fee = session_fee_info(ctx.db, s)
    assert d["sessionId"] == s.id and d["siteCode"] == "OUT"          # гадна зогсоол, хүүхэд биш
    assert d["totalFee"] == fee["total_fee"] == 1000.0
    assert d["amountDue"] == amount_due(ctx.db, s, fee) == 1000.0
    assert d["isFree"] is False and d["freeMinutes"] == 15
    assert d["enteredDate"] == PV.iso_z(entered) and d["enteredDate"].endswith(".375Z")
    assert d["freeUntil"] == PV.iso_z(entered + timedelta(minutes=15))
    assert d["freeRemainingMinutes"] == 0
    assert d["latitude"] == 47.9 and d["address"] == "Synthetic street" and d["currency"] == "MNT"
    assert d["paymentId"] is None
    # Бусдын / өөр түншийн хүсэлт — байршил задрахгүй
    for user, key in (("B", KEY), ("A", KEY2)):
        r = post(ctx, "bill", {"plateNo": "7886УКР", "userId": user}, key=key)
        assert r.status_code == 404 and r.json()["Message"] == "NOT_REGISTERED" and "data" not in r.json()


def test_bill_free_window_intent_and_no_session(ctx):
    sub(ctx, user="A")
    assert post(ctx, "bill", {"plateNo": "7886УКР", "userId": "A"}).json() == {
        "Success": True, "Message": "NO_ACTIVE_SESSION", "data": None}
    s = stay(ctx, entry=datetime.utcnow() - timedelta(minutes=5))
    pay = M.Payment(session_id=s.id, provider="socialpay", payment_method="WALLET",
                    sender_invoice_no="SP-SYN-1", amount=0, status="PENDING")
    ctx.db.add(pay); ctx.db.commit()
    d = post(ctx, "bill", {"plateNo": "7886УКР", "userId": "A"}).json()["data"]
    assert d["isFree"] is True and d["amountDue"] == 0 and d["freeReason"].startswith("Эхний 15")
    assert 9 <= d["freeRemainingMinutes"] <= 10 and d["paymentId"] == pay.id


# ─────────────────────────── Байршил ───────────────────────────

def test_parkings_camel_case_outer_sites_only(ctx):
    for method in ("get", "post"):
        r = getattr(ctx.client, method)("/api/v1/socialpay/parkings", headers={"X-API-Key": KEY_RO})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["Success"] is True
        codes = [p["siteCode"] for p in body["data"]]
        assert "IN" not in codes and {"OUT", "OTH"} <= set(codes)
        out = next(p for p in body["data"] if p["siteCode"] == "OUT")
        assert out["latitude"] == 47.9 and out["locationAvailable"] is True
        assert out["freeMinutes"] == 15 and out["capacity"] == 50
    assert ctx.client.get("/api/v1/socialpay/parkings").json()["Message"] == "UNAUTHORIZED"


# ─────────────────────────── Мэдэгдэл: scanner ───────────────────────────

def test_entry_then_exit_exact_payload_and_idempotent(ctx):
    sub(ctx)
    entered = NOW - timedelta(minutes=10, microseconds=-375000)
    s = stay(ctx, entry=entered, created=entered)
    assert PV.scan_once(ctx.db, NOW) == {"entry": 1, "exit": 0}
    assert PV.scan_once(ctx.db, NOW + timedelta(seconds=20)) == {"entry": 0, "exit": 0}
    (ev,) = events(ctx)
    assert ev.payload == {"data": {"plateNo": "7886УКР", "enteredDate": PV.iso_z(entered)}}
    assert ev.payload["data"]["enteredDate"].endswith(".375Z") and ev.awaiting_exit
    assert ev.job_key == f"veh:{ctx.key.id}:{s.id}:ENTRY"
    close(ctx, s, NOW - timedelta(minutes=1), confirmed=True)
    assert PV.scan_once(ctx.db, NOW)["exit"] == 1
    assert PV.scan_once(ctx.db, NOW + timedelta(minutes=5))["exit"] == 0
    exit_ev = events(ctx, event="EXIT")[0]
    assert exit_ev.payload == {"data": {"plateNo": "7886УКР", "enteredDate": PV.iso_z(entered),
                                        "exitedDate": PV.iso_z(NOW - timedelta(minutes=1))}}
    ctx.db.refresh(ev)
    assert ev.awaiting_exit is False
    # Хоёр хэрэглэгч ижил дугаар бүртгүүлсэн ч түнш рүү нэг л мэдэгдэл
    sub(ctx, user="second")
    s2 = stay(ctx, entry=NOW - timedelta(minutes=2))
    PV.scan_once(ctx.db, NOW)
    assert len(events(ctx, session_id=s2.id)) == 1


def test_entry_settle_child_site_unsubscribed_and_scope(ctx):
    sub(ctx)
    stay(ctx, entry=NOW - timedelta(seconds=10))                       # settle хүлээнэ
    stay(ctx, site=ctx.child, entry=NOW - timedelta(minutes=3))         # доторх зогсоол
    stay(ctx, plate="1111УБА", entry=NOW - timedelta(minutes=3))       # бүртгэлгүй
    assert PV.scan_once(ctx.db, NOW)["entry"] == 0
    assert PV.scan_once(ctx.db, NOW + timedelta(seconds=31))["entry"] == 1
    # Зогсоолоор хязгаарласан түлхүүр өөр зогсоолын сешнийг мэдэхгүй
    ctx.key2.site_id = ctx.other.id; ctx.db.commit()
    sub(ctx, key=ctx.key2)
    PV.scan_once(ctx.db, NOW + timedelta(minutes=1))
    assert events(ctx, partner_key_id=ctx.key2.id) == []


def test_no_entry_for_stale_backfill_late_subscription_denied_and_no_entry_read(ctx):
    sub(ctx, created_at=NOW - timedelta(days=1))
    # camera_sync 3ц хоцорч нөхсөн — «саяхан орлоо» гэж спамлахгүй
    backfill = stay(ctx, entry=NOW - timedelta(hours=3), created=NOW - timedelta(minutes=2))
    # Хаалттай («зөвхөн бүртгэлтэй») зогсоолд татгалзагдсан машин
    stay(ctx, site=ctx.gated, entry=NOW - timedelta(minutes=3))
    # Гарцад үүссэн суурь хураамжийн сешн (орох уншилтгүй)
    flat = stay(ctx, site=ctx.other, entry=NOW - timedelta(minutes=3), fee_locked=True,
                status="AWAITING_PAYMENT", total_fee=2000)
    assert PV.scan_once(ctx.db, NOW)["entry"] == 0
    # Бүртгэл айлчлалаас ХОЙШ үүссэн бол тэр айлчлалын ENTRY явахгүй
    late = sub(ctx, plate="2222УБА", created_at=NOW - timedelta(minutes=1))
    stay(ctx, plate=late.plate_number, entry=NOW - timedelta(minutes=4))
    assert PV.scan_once(ctx.db, NOW)["entry"] == 0
    # Хослолгүй ч ШИНЭ бодит гарц илгээгдэнэ
    close(ctx, backfill, NOW - timedelta(minutes=1), confirmed=True)
    close(ctx, flat, NOW - timedelta(minutes=1), confirmed=False, device=True, status="CLOSED")
    assert PV.scan_once(ctx.db, NOW)["exit"] == 2
    ev = next(e for e in events(ctx, event="EXIT") if e.session_id == backfill.id)
    assert set(ev.payload["data"]) == {"plateNo", "enteredDate", "exitedDate"}


def test_inferred_close_not_sent_until_real_exit_after_reopen(ctx):
    sub(ctx)
    s = stay(ctx, entry=NOW - timedelta(hours=13), created=NOW - timedelta(hours=13))
    PV.scan_once(ctx.db, NOW - timedelta(hours=12, minutes=59))
    assert len(events(ctx, event="ENTRY")) == 1
    # 12ц phantom авто хаалт — гарах уншилтгүй: spec форматад EXIT явахгүй
    close(ctx, s, NOW - timedelta(minutes=1), confirmed=False, status="MANUAL_CLOSED")
    assert PV.scan_once(ctx.db, NOW)["exit"] == 0
    assert events(ctx, event="ENTRY")[0].awaiting_exit is True
    # auto_reopen → бодит гарц (3ц-ийн өмнөх цагтай camera_sync хаалт ч хос тул явна)
    s.status, s.exit_time = "OPEN", None; ctx.db.commit()
    close(ctx, s, NOW - timedelta(hours=3), confirmed=True)
    assert PV.scan_once(ctx.db, NOW)["exit"] == 1
    # Дахин сэргээгээд хаасан ч EXIT давтагдахгүй (нэг сешн — нэг EXIT)
    s.status, s.exit_time = "OPEN", None; ctx.db.commit()
    close(ctx, s, NOW, confirmed=True)
    assert PV.scan_once(ctx.db, NOW + timedelta(minutes=1))["exit"] == 0


def test_extended_format_sends_inferred_exit_with_flag(ctx):
    ctx.key.vehicle_webhook_format = "extended"; ctx.db.commit()
    sub(ctx)
    s = stay(ctx, entry=NOW - timedelta(minutes=30))
    PV.scan_once(ctx.db, NOW - timedelta(minutes=29))
    entry = events(ctx, event="ENTRY")[0].payload["data"]
    assert entry["event"] == "ENTRY" and entry["siteCode"] == "OUT" and entry["freeMinutes"] == 15
    close(ctx, s, NOW - timedelta(minutes=1), confirmed=False, status="MANUAL_CLOSED")
    assert PV.scan_once(ctx.db, NOW)["exit"] == 1
    data = events(ctx, event="EXIT")[0].payload["data"]
    assert data["exitConfirmed"] is False and data["sessionId"] == s.id and data["minutesParked"] == 29


def test_unconfigured_or_inactive_key_enqueues_nothing(ctx):
    sub(ctx)
    ctx.key.vehicle_webhook_url = None
    ctx.key2.is_active = False
    ctx.db.commit()
    sub(ctx, key=ctx.key2)
    stay(ctx, entry=NOW - timedelta(minutes=3))
    assert PV.scan_once(ctx.db, NOW) == {"entry": 0, "exit": 0}
    assert events(ctx) == []


def test_deleted_subscription_not_notified(ctx):
    row = sub(ctx)
    row.deleted_at = NOW - timedelta(hours=1); ctx.db.commit()
    stay(ctx, entry=NOW - timedelta(minutes=3))
    assert PV.scan_once(ctx.db, NOW)["entry"] == 0


# ─────────────────────────── Мэдэгдэл: хүргэлт ───────────────────────────

class FakeSocialPay:
    """Локал HTTP сервер — хариуг скриптээр өгнө, хүлээн авсныг хадгална."""

    def __init__(self):
        self.received, self.replies, self.delay = [], [], 0.0
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                outer.received.append({"path": self.path, "headers": dict(self.headers), "raw": raw})
                if outer.delay:
                    time.sleep(outer.delay)
                status, body = outer.replies.pop(0) if outer.replies else (200, {"Success": True, "Message": "OK"})
                data = body if isinstance(body, bytes) else json.dumps(body).encode()
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/api/parking/notification"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown(); self.server.server_close()


@pytest.fixture
def fake(ctx):
    srv = FakeSocialPay()
    ctx.key.vehicle_webhook_url = srv.url
    ctx.db.commit()
    yield srv
    srv.close()


def factory(ctx):
    return lambda: Session(ctx.engine, autoflush=False)


def enqueue(ctx, minutes=10, plate="7886УКР"):
    if not PV.is_registered_by(ctx.db, ctx.key.id, plate):
        sub(ctx, plate=plate)
    s = stay(ctx, plate=plate, entry=datetime.utcnow() - timedelta(minutes=minutes))
    PV.scan_once(ctx.db)
    return s


def test_delivery_success_exact_bytes_and_headers(ctx, fake):
    s = enqueue(ctx)
    assert asyncio.run(PV.deliver_due(factory(ctx))) == 1
    (req,) = fake.received
    assert req["path"] == "/api/parking/notification"
    assert req["headers"]["Content-Type"] == "application/json"
    sent = json.loads(req["raw"].decode("utf-8"))
    assert sent == {"data": {"plateNo": "7886УКР", "enteredDate": PV.iso_z(s.entry_time)}}
    assert "7886УКР".encode() in req["raw"]          # UTF-8, \\u escape биш
    ctx.db.expire_all()
    ev = events(ctx)[0]
    assert ev.status == "DONE" and ev.response_message == "OK" and ev.last_http_status == 200
    assert req["headers"]["Idempotency-Key"] == ev.id == req["headers"]["X-Parking-Event-Id"]
    assert asyncio.run(PV.deliver_due(factory(ctx))) == 0


def test_success_flag_as_text_accepted(ctx, fake):
    enqueue(ctx)
    fake.replies = [(200, {"success": "true", "message": "ok"})]
    asyncio.run(PV.deliver_due(factory(ctx)))
    ctx.db.expire_all()
    assert events(ctx)[0].status == "DONE" and events(ctx)[0].response_message == "ok"


@pytest.mark.parametrize("reply,error", [
    ((500, {"Success": False, "Message": "boom"}), "HTTP 500"),
    ((200, {"Success": False, "Message": "User not found"}), "Success=False"),
    ((200, b"<html>ok</html>"), "Success=None"),
])
def test_failed_delivery_backoff_until_dead(ctx, fake, reply, error):
    enqueue(ctx)
    fake.replies = [reply] * 20
    t0 = datetime.utcnow()
    asyncio.run(PV.deliver_due(factory(ctx)))
    ctx.db.expire_all()
    ev = events(ctx)[0]
    assert ev.status == "RETRY" and ev.attempts == 1 and ev.last_error == error
    assert 59 <= (ev.next_attempt_at - t0).total_seconds() <= 70
    if reply[1] == {"Success": False, "Message": "User not found"}:
        assert ev.response_message == "User not found"
    # Дараагийн оролдлого 5 мин; 24ц-ийн цонх дуусахад DEAD
    t2 = ev.next_attempt_at
    with Session(ctx.engine) as db:
        assert PV.claim_batch(db, now=t2 - timedelta(seconds=1)) == []
    with Session(ctx.engine) as db:
        claimed = PV.claim_batch(db, now=t2)
    assert len(claimed) == 1
    with Session(ctx.engine) as db:
        assert PV.record_result(db, claimed[0]["id"], claimed[0]["token"],
                                {"ok": False, "error": "HTTP 503"}, now=t2) == "RETRY"
    ctx.db.expire_all()
    ev = events(ctx)[0]
    assert ev.attempts == 2 and ev.next_attempt_at == t2 + timedelta(seconds=300)
    late = ev.created_at + timedelta(hours=23, minutes=59)
    with Session(ctx.engine) as db:
        c2 = PV.claim_batch(db, now=late)
        assert PV.record_result(db, c2[0]["id"], c2[0]["token"], {"ok": False, "error": "x"},
                                now=late) == "DEAD"


def test_timeout_and_unreachable_do_not_block(ctx, fake, monkeypatch):
    monkeypatch.setattr(settings, "partner_vehicle_http_timeout", 0.3)
    enqueue(ctx)
    fake.delay = 1.0
    t = time.monotonic()
    asyncio.run(PV.deliver_due(factory(ctx)))
    assert time.monotonic() - t < 1.5
    ctx.db.expire_all()
    assert events(ctx)[0].status == "RETRY" and events(ctx)[0].last_error == "ReadTimeout"
    # Хүрэх боломжгүй хост (порт хаалттай)
    ctx.key.vehicle_webhook_url = "http://127.0.0.1:9/x"; ctx.db.commit()
    e = events(ctx)[0]; e.next_attempt_at = datetime.utcnow(); ctx.db.commit()
    asyncio.run(PV.deliver_due(factory(ctx)))
    ctx.db.expire_all()
    assert events(ctx)[0].last_error in ("ConnectError", "ConnectTimeout")


def test_per_plate_order_and_deleted_subscription_skipped(ctx, fake):
    s = enqueue(ctx, minutes=20)
    close(ctx, s, datetime.utcnow() - timedelta(minutes=2), confirmed=True)
    PV.scan_once(ctx.db)
    entry, exit_ = events(ctx, event="ENTRY")[0], events(ctx, event="EXIT")[0]
    entry.status, entry.next_attempt_at = "RETRY", datetime.utcnow() + timedelta(minutes=5)
    ctx.db.commit()
    assert asyncio.run(PV.deliver_due(factory(ctx))) == 0       # EXIT нь ENTRY-г хүлээнэ
    entry.next_attempt_at = datetime.utcnow(); ctx.db.commit()
    assert asyncio.run(PV.deliver_due(factory(ctx))) == 1
    assert asyncio.run(PV.deliver_due(factory(ctx))) == 1
    assert [json.loads(r["raw"])["data"].get("exitedDate") is not None for r in fake.received] == [False, True]
    # Бүртгэл устгагдсан бол хүлээгдэж буй мэдэгдэл илгээгдэхгүй
    s2 = enqueue(ctx, minutes=5, plate="3333УБА")
    ctx.db.query(M.PartnerVehicle).filter_by(plate_number="3333УБА").update(
        {"deleted_at": datetime.utcnow()}); ctx.db.commit()
    asyncio.run(PV.deliver_due(factory(ctx)))
    ctx.db.expire_all()
    ev = events(ctx, session_id=s2.id)[0]
    assert ev.status == "SKIPPED" and len(fake.received) == 2


def test_reentry_new_session_new_entry_and_old_exit_goes_first(ctx, fake):
    """Төлөлгүй гарсан машин дахин орвол хуучин сешн «гарсан» болж хаагдана (exit_time =
    гарцад сүүлд харагдсан, шинэ оролтоос ӨМНӨ) — ГАРСАН(хуучин) нь ОРСОН(шинэ)-оос түрүүлнэ."""
    old = enqueue(ctx, minutes=90)
    asyncio.run(PV.deliver_due(factory(ctx)))                    # ОРСОН(хуучин) хүргэгдсэн
    now = datetime.utcnow()
    close(ctx, old, now - timedelta(minutes=30), confirmed=True, status="MANUAL_CLOSED")
    new = stay(ctx, entry=now - timedelta(minutes=1), created=now - timedelta(minutes=1))
    assert PV.scan_once(ctx.db) == {"entry": 1, "exit": 1}
    exit_old = events(ctx, event="EXIT")[0]
    entry_new = events(ctx, session_id=new.id)[0]
    exit_old.status = "RETRY"; exit_old.next_attempt_at = now + timedelta(minutes=1)
    ctx.db.commit()
    assert asyncio.run(PV.deliver_due(factory(ctx))) == 0        # шинэ ОРСОН хүлээнэ
    exit_old.next_attempt_at = now - timedelta(seconds=1); ctx.db.commit()
    asyncio.run(PV.deliver_due(factory(ctx)))
    asyncio.run(PV.deliver_due(factory(ctx)))
    sent = [json.loads(r["raw"])["data"] for r in fake.received]
    assert [("exitedDate" in d, d["enteredDate"]) for d in sent] == [
        (False, PV.iso_z(old.entry_time)), (True, PV.iso_z(old.entry_time)),
        (False, PV.iso_z(entry_new_time(ctx, entry_new)))]


def entry_new_time(ctx, ev):
    return ctx.db.get(M.ParkingSession, ev.session_id).entry_time


def test_expired_lease_reclaimed_and_stale_token_ignored(ctx, fake):
    enqueue(ctx)
    with Session(ctx.engine) as db:
        (first,) = PV.claim_batch(db)
    assert PV.claim_batch(Session(ctx.engine)) == []              # lease хүчинтэй
    later = datetime.utcnow() + timedelta(minutes=3)
    with Session(ctx.engine) as db:
        (second,) = PV.claim_batch(db, now=later)
    assert second["token"] != first["token"]
    with Session(ctx.engine) as db:
        assert PV.record_result(db, first["id"], first["token"], {"ok": True}) is None
        assert PV.record_result(db, second["id"], second["token"], {"ok": True}) == "DONE"
    ctx.db.expire_all()
    assert events(ctx)[0].attempts == 2


def test_cleanup_retention(ctx):
    sub(ctx)
    s = stay(ctx)
    old = NOW - timedelta(days=40)
    for i, st in enumerate(("DONE", "DEAD", "RETRY")):
        ctx.db.add(M.PartnerVehicleEvent(job_key=f"k{i}", partner_key_id=ctx.key.id, session_id=s.id,
                                         plate_number="7886УКР", event="ENTRY", event_time=old,
                                         status=st, attempts=1, next_attempt_at=old, payload={},
                                         awaiting_exit=False, created_at=old, updated_at=old))
    ctx.db.commit()
    assert PV.cleanup(ctx.db, NOW) == 1
    assert {e.status for e in events(ctx)} == {"DEAD", "RETRY"}


# ─────────────────────────── Админ ───────────────────────────

def test_admin_configure_list_events_retry_and_test(ctx, fake):
    k = ctx.ro
    r = ctx.client.post(f"/api/admin/partner-keys/{k.id}/vehicles",
                        json={"enabled": True, "url": fake.url, "format": "socialpay"})
    assert r.status_code == 200 and r.json()["scopes"] == "read,pay,vehicles"
    assert ctx.client.post(f"/api/admin/partner-keys/{k.id}/vehicles",
                           json={"format": "xml"}).status_code == 400
    assert ctx.client.post(f"/api/admin/partner-keys/{k.id}/vehicles",
                           json={"url": "ftp://x"}).status_code == 400
    keys = {x["name"]: x for x in ctx.client.get("/api/admin/partner-keys").json()["keys"]}
    assert keys["readonly"]["vehicles_enabled"] and keys["readonly"]["vehicle_webhook_url"] == fake.url
    test = ctx.client.post(f"/api/admin/partner-keys/{k.id}/vehicles/test", json={}).json()
    assert test["ok"] is True and test["message"] == "OK"
    assert json.loads(fake.received[-1]["raw"])["data"]["plateNo"] == "0000ТЕС"
    # DEAD → дахин илгээх
    enqueue(ctx)
    ev = events(ctx)[0]; ev.status = "DEAD"; ctx.db.commit()
    lst = ctx.client.get(f"/api/admin/partner-keys/{ctx.key.id}/vehicle-events?status=dead").json()
    assert [e["id"] for e in lst["events"]] == [ev.id]
    assert ctx.client.post(f"/api/admin/partner-vehicle-events/{ev.id}/retry").json()["status"] == "PENDING"
    assert ctx.client.post(f"/api/admin/partner-vehicle-events/{ev.id}/retry").status_code == 200
    asyncio.run(PV.deliver_due(factory(ctx)))
    ctx.db.expire_all()
    assert events(ctx)[0].status == "DONE"
    assert ctx.client.post(f"/api/admin/partner-vehicle-events/{ev.id}/retry").status_code == 409
    vehicles = ctx.client.get(f"/api/admin/partner-keys/{ctx.key.id}/vehicles?plate=7886укр").json()
    assert vehicles["vehicles"][0]["user_id"] == "100264"
    # Disable → scope хасагдана, бүртгэлийн API 403
    ctx.client.post(f"/api/admin/partner-keys/{ctx.key.id}/vehicles", json={"enabled": False})
    assert post(ctx, "register", {"plateNo": "7886УКР", "userId": "9"}).status_code == 403


def test_admin_vehicle_data_requires_global_admin(ctx):
    ctx.user.role = "ADMIN"
    ctx.user.site_ids = [ctx.site.id]
    ctx.db.commit()
    for method, path in (("get", f"/api/admin/partner-keys/{ctx.key.id}/vehicles"),
                         ("get", f"/api/admin/partner-keys/{ctx.key.id}/vehicle-events"),
                         ("post", f"/api/admin/partner-keys/{ctx.key.id}/vehicles")):
        kw = {"json": {}} if method == "post" else {}
        assert getattr(ctx.client, method)(path, **kw).status_code == 403


def test_create_key_with_vehicles_scope(ctx):
    r = ctx.client.post("/api/admin/partner-keys", json={"name": "socialpay-uat", "can_pay": True,
                                                         "can_vehicles": True})
    assert r.status_code == 200 and r.json()["scopes"] == "read,pay,vehicles"
    raw = r.json()["key"]
    assert post(ctx, "register", {"plateNo": "7886УКР", "userId": "1"}, key=raw).json()["Message"] == "OK"
