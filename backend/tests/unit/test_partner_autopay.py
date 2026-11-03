"""Wallet-ийн автомат төлбөр гарах хаалтан дээр (2026-11-03) — синтетик SQLite.

Сүлжээ (камер, банк, wallet) хориотой; түншийн webhook-ийг httpx.MockTransport орлоно.
Шалгах зүйл:
  1. Бүртгэл: `autoPay` (register / vehicles/autopay), түлхүүр бэлэн эсэх, хариуны хэлбэр
  2. Гарах камер → intent (өртэй) + `payment.requested`, confirm → хаалт ШУУД нээгдэнэ
  3. Татгалзал (cancel + reason), нэг айлчлалд нэг оролдлого, QR дахин нээгдэнэ
  4. Хугацаа дуусгалт → intent CANCELLED + `payment.request_expired`; хожуу confirm хүлээн авна
  5. Хамгаалалт: ЯГ дугаар, autoPay унтраалттай, түлхүүрийн хүрээ, олон түнш, идэвхтэй QPay,
     PARKING_PARTNER_VEHICLE_NOTIFY=false
"""
import asyncio
import hashlib
import json
import socket
from datetime import datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import auth, models as M, ratelimit, session_logic as SL
from app.config import settings
from app.database import Base, get_db
from app.routers import (admin_router as A, integration_router as I, payments_router as P,
                         socialpay_router as SP)
from app.services import app_settings as AS, financial_jobs as FJ, partner_autopay as PA

PLATE = "7886УКР"
KEY = "pk_monpay_synthetic_key"
KEY2 = "pk_socialpay_synthetic_key"
KEY_NOHOOK = "pk_nohook_synthetic_key"
HOOK = "https://wallet.example/parking/hook"


def _key(name, raw, scopes, **kw):
    return M.PartnerKey(name=name, key_hash=hashlib.sha256(raw.encode()).hexdigest(),
                        key_prefix=raw[:10], scopes=scopes, **kw)


@pytest.fixture
def c(monkeypatch):
    def forbidden(*a, **k):
        raise AssertionError("Network forbidden in synthetic autopay test")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(settings, "ebarimt_mock_receipts", False)
    monkeypatch.setattr(settings, "qpay_ebarimt", False)
    monkeypatch.setattr(settings, "partner_keys", "")
    monkeypatch.setattr(settings, "partner_autopay_enabled", True)
    monkeypatch.setattr(settings, "partner_vehicle_notify", True)
    monkeypatch.setattr(settings, "partner_autopay_timeout_sec", 60)
    monkeypatch.setattr(settings, "public_base_url", "https://site.easy-parking.mn")
    monkeypatch.setattr(FJ.msgbill, "account_enabled_for", lambda *a: None)
    ratelimit._hits.clear()
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    AS.invalidate_cache()
    with Session(engine, autoflush=False) as db:
        tariff = M.TariffTemplate(name="Hourly", free_minutes=0, extra_hour_price=1000, grace_minutes=15)
        tariff.tiers = [M.TariffTier(upto_minutes=60, price=1000), M.TariffTier(upto_minutes=120, price=2000)]
        site = M.ParkingSite(name="Номадс", site_code="NOMADS", tariff_template=tariff, address="Талбай")
        other = M.ParkingSite(name="Other", site_code="OTH", tariff_template=tariff)
        db.add_all([site, other]); db.flush()
        key = _key("monpay", KEY, "read,pay,vehicles", webhook_url=HOOK)
        key2 = _key("socialpay", KEY2, "read,pay,vehicles", webhook_url="https://sp.example/hook")
        nohook = _key("nohook", KEY_NOHOOK, "read,pay,vehicles")
        entry = M.Device(site_id=site.id, name="Entry", device_type="camera", lane_dir="entry",
                         lane_no=1, status="active", auto_open=False, ip_address="192.0.2.10")
        exit_cam = M.Device(site_id=site.id, name="Exit", device_type="camera", lane_dir="exit",
                            lane_no=1, status="active", ip_address="192.0.2.11")
        bar = M.Device(site_id=site.id, name="Barrier", device_type="barrier", lane_dir="exit",
                       lane_no=1, status="active")
        user = M.User(username="admin", password_hash="unused", role="SUPER_ADMIN")
        db.add_all([key, key2, nohook, entry, exit_cam, bar, user]); db.commit()
        AS.set_site_rules(db, AS.EXITRULES_KEY, site.id, {"wallet_auto_deduct": False}, "test")
        db.commit()
        staff, commands, scheduled = [], [], []
        monkeypatch.setattr("app.ws.notify", lambda sid, kind, data: staff.append((kind, data)))
        monkeypatch.setattr(SL, "notify", lambda *a, **k: staff.append((a[1], a[2])))
        monkeypatch.setattr(SL, "schedule_capture", lambda *a, **k: None)
        monkeypatch.setattr(SL, "schedule_display", lambda *a, **k: None)
        monkeypatch.setattr(SL, "_inner_lane_devices", lambda *a: [])
        monkeypatch.setattr(PA, "schedule_delivery", lambda rid: scheduled.append(rid))

        async def open_fake(db, barrier, session_id, source, **kw):
            commands.append((barrier.id, session_id, source))
            cmd = M.BarrierCommand(device_id=barrier.id, session_id=session_id, command="open",
                                   command_source=source, status="SUCCESS")
            db.add(cmd); db.commit()
            return cmd
        monkeypatch.setattr(SL, "open_barrier", open_fake)
        app = FastAPI()
        for r in (I.router, SP.router, SP.neutral_router, A.router, P.router):
            app.include_router(r)
        SP.install_error_handlers(app)
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[auth.get_current_user] = lambda: user
        factory = sessionmaker(bind=engine, autoflush=False)
        with TestClient(app) as client:
            yield SimpleNamespace(db=db, engine=engine, client=client, site=site, other=other,
                                  key=key, key2=key2, nohook=nohook, entry=entry, exit=exit_cam,
                                  bar=bar, staff=staff, commands=commands, scheduled=scheduled,
                                  factory=factory)
    AS.invalidate_cache(); engine.dispose()


def api(c, path, body, key=KEY):
    return c.client.post(f"/api/v1/{path}", json=body, headers={"X-API-Key": key})


def register(c, plate=PLATE, user="100264", auto=True, key=KEY):
    r = api(c, "vehicles/register", {"plateNo": plate, "userId": user, "autoPay": auto}, key)
    assert r.status_code == 200, r.text
    return r


def arrive(c, plate=PLATE, minutes_ago=100):
    s = M.ParkingSession(site_id=c.site.id, plate_number=plate, status="OPEN",
                         entry_time=datetime.utcnow() - timedelta(minutes=minutes_ago),
                         entry_device_id=c.entry.id)
    c.db.add(s); c.db.commit()
    return s


def exit_read(c, plate=PLATE):
    """Гарах камерын уншилт: LprEvent (lpr_router-ийн адил) + handle_exit."""
    c.db.add(M.LprEvent(site_id=c.site.id, device_id=c.exit.id, plate_number=plate, lane_dir="exit",
                        accepted=True, confidence=95, created_at=datetime.utcnow()))
    c.db.commit()
    return asyncio.run(SL.handle_exit(c.db, c.exit, plate, 95, {}))


def requests_(c):
    return c.db.query(M.PartnerAutopayRequest).order_by(M.PartnerAutopayRequest.created_at).all()


def deliver(c, handler):
    """Хүлээгдэж буй хүсэлтийг MockTransport-оор хүргэнэ; хүлээн авсан (headers, body)-г буцаана."""
    seen = []

    def _h(request):
        seen.append((dict(request.headers), json.loads(request.content)))
        return handler(request)
    asyncio.run(PA.deliver_due(factory=c.factory, transport=httpx.MockTransport(_h)))
    c.db.expire_all()
    return seen


def confirm(c, payment_id, tx="MP-TX-1", amount=None, key=KEY):
    body = {"transaction_id": tx}
    if amount is not None:
        body["amount"] = amount
    return api(c, f"payments/{payment_id}/confirm", body, key)


# ─────────────────────────── 1. Бүртгэл ───────────────────────────

def test_register_with_autopay_and_toggle(c):
    r = api(c, "vehicles/register", {"plateNo": PLATE, "userId": "100264", "autoPay": True})
    assert r.json() == {"Success": True, "Message": "OK",
                        "data": {"plateNo": PLATE, "userId": "100264", "autoPay": True}}
    v = c.db.query(M.PartnerVehicle).one()
    assert v.auto_pay is True and v.auto_pay_updated_at is not None
    # autoPay-гүй давтан бүртгэл — хариу ЯГ хуучин хэлбэр, сонголт хөндөгдөхгүй
    r = api(c, "vehicles/register", {"plateNo": PLATE, "userId": "100264"})
    assert r.json() == {"Success": True, "Message": "ALREADY_REGISTERED"}
    c.db.refresh(v); assert v.auto_pay is True
    # Байгаа бүртгэлийг унтраах (register-ээр) → ALREADY_REGISTERED + data
    r = api(c, "vehicles/register", {"plateNo": PLATE, "userId": "100264", "autoPay": False})
    assert r.json()["Message"] == "ALREADY_REGISTERED" and r.json()["data"]["autoPay"] is False
    c.db.refresh(v); assert v.auto_pay is False
    # Тусдаа endpoint (SocialPay зам ч ижил)
    r = c.client.post("/api/v1/socialpay/autopay", json={"plateNo": PLATE, "userId": "100264",
                                                         "autoPay": True}, headers={"X-API-Key": KEY})
    assert r.status_code == 200 and r.json()["data"]["autoPay"] is True
    c.db.refresh(v); assert v.auto_pay is True
    audit = c.db.query(M.AuditLog).filter_by(action="PARTNER_VEHICLE_AUTOPAY").count()
    assert audit == 2


def test_register_autopay_validation_and_key_readiness(c):
    r = api(c, "vehicles/register", {"plateNo": PLATE, "userId": "1", "autoPay": "true"})
    assert r.status_code == 400 and r.json()["Message"] == "INVALID_AUTO_PAY"
    # webhook URL-гүй түлхүүр — гарцад хүсэлт хэзээ ч явахгүй тул ШУУД 409
    r = api(c, "vehicles/register", {"plateNo": PLATE, "userId": "1", "autoPay": True}, KEY_NOHOOK)
    assert r.status_code == 409 and r.json()["Message"] == "AUTOPAY_NOT_CONFIGURED"
    assert c.db.query(M.PartnerVehicle).count() == 0
    # autoPay=false бол бэлэн биш түлхүүрээр ч бүртгэнэ
    r = api(c, "vehicles/register", {"plateNo": PLATE, "userId": "1", "autoPay": False}, KEY_NOHOOK)
    assert r.status_code == 200
    r = api(c, "vehicles/autopay", {"plateNo": "1111УБА", "userId": "1", "autoPay": True})
    assert r.status_code == 404 and r.json()["Message"] == "NOT_REGISTERED"
    r = api(c, "vehicles/autopay", {"plateNo": PLATE, "userId": "1"})
    assert r.status_code == 400 and r.json()["Message"] == "INVALID_AUTO_PAY"


# ─────────────────────────── 2. Бүтэн урсгал ───────────────────────────

def test_exit_requests_wallet_then_confirm_opens_gate(c):
    register(c)
    s = arrive(c, minutes_ago=100)                 # 100 мин → 2000₮
    out = exit_read(c)
    assert out["action"] == "awaiting_payment" and out["amount_due"] == 2000.0, out
    ap = out["autopay"]
    assert ap and ap["partner"] == "monpay" and ap["amount"] == 2000.0
    assert c.scheduled == [ap["request_id"]]       # ард нь шууд илгээхээр товлосон
    pay = c.db.get(M.Payment, ap["payment_id"])
    assert (pay.status, pay.provider, pay.payment_method, pay.partner_key_id, pay.source) == (
        "PENDING", "monpay", "WALLET", c.key.id, "WALLET")
    assert pay.raw_payload["autopay"] is True
    kinds = [k for k, d in c.staff if k == "EXIT_LPR_EVENT"]
    assert kinds and dict(c.staff)["EXIT_LPR_EVENT"]["autopay_partner"] == "monpay"

    seen = deliver(c, lambda req: httpx.Response(200, json={"success": True}))
    assert len(seen) == 1
    headers, body = seen[0]
    assert headers["x-parking-event"] == "payment.requested"
    assert headers["idempotency-key"] == ap["request_id"] == body["event_id"]
    assert headers["x-parking-partner"] == "monpay"
    assert body["payment_id"] == pay.id and body["session_id"] == s.id
    assert body["plate"] == PLATE and body["user_id"] == "100264"
    assert body["amount"] == 2000.0 and body["parking_amount"] == 2000.0 and body["debt_amount"] == 0
    assert body["site_code"] == "NOMADS" and body["currency"] == "MNT"
    assert body["confirm_url"] == f"https://site.easy-parking.mn/api/v1/payments/{pay.id}/confirm"
    assert body["expires_in_seconds"] == 60 and body["expires_at"]
    req = requests_(c)[0]
    assert req.status == "SENT" and req.delivered_at and req.attempts == 1

    # Давтан уншилт — шинэ хүсэлт/давхар intent үүсэхгүй
    exit_read(c)
    assert len(requests_(c)) == 1
    assert c.db.query(M.Payment).count() == 1

    r = confirm(c, pay.id, amount=2000)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "PAID" and r.json()["barrier_opened"] is True
    c.db.expire_all()
    assert c.db.get(M.ParkingSession, s.id).status == "CLOSED"
    assert c.commands and c.commands[-1][1] == s.id
    req = requests_(c)[0]
    assert req.status == "PAID" and req.resolved_at
    st = c.client.get(f"/api/v1/payments/{pay.id}", headers={"X-API-Key": KEY}).json()
    assert st["autopay"]["status"] == "PAID"


def test_autopay_includes_debts_and_clears_debt_block(c):
    register(c)
    for i in range(3):     # 3 өр = block_exit_debt_count (анхдагч) → зөвхөн зогсолтоор нээгдэхгүй
        c.db.add(M.Compensation(site_id=c.site.id, plate_number=PLATE, amount=1000,
                                reason="unpaid_exit", status="PENDING"))
    c.db.commit()
    s = arrive(c, minutes_ago=50)                  # 1000₮
    out = exit_read(c)
    assert out["action"] == "debt_blocked" and out["autopay"], out
    pay = c.db.get(M.Payment, out["autopay"]["payment_id"])
    assert float(pay.amount) == 4000.0             # 1000 зогсолт + 3×1000 өр
    seen = deliver(c, lambda req: httpx.Response(200, json={}))
    body = seen[0][1]
    assert body["parking_amount"] == 1000.0 and body["debt_amount"] == 3000.0
    assert body["debt_count"] == 3 and len(body["debts"]) == 3
    r = confirm(c, pay.id, amount=4000)
    assert r.status_code == 200 and r.json()["barrier_opened"] is True, r.text
    c.db.expire_all()
    assert c.db.query(M.Compensation).filter_by(status="PENDING").count() == 0
    assert c.db.get(M.ParkingSession, s.id).status == "CLOSED"


# ─────────────────────────── 3. Татгалзал ───────────────────────────

def test_decline_releases_qr_and_no_second_request(c):
    register(c)
    s = arrive(c)
    out = exit_read(c)
    pid = out["autopay"]["payment_id"]
    # Хариу ирэх хүртэл QR нэхэмжлэл автомат төлбөрийн тайлбартай 409
    r = c.client.post("/api/payments/qpay/invoice", json={"session_id": s.id})
    assert r.status_code == 409 and "автомат төлбөр" in r.json()["detail"]
    r = api(c, f"payments/{pid}/cancel", {"outcome": "NOT_CHARGED", "reason": "insufficient_funds"})
    assert r.status_code == 200 and r.json()["status"] == "CANCELLED"
    req = requests_(c)[0]
    c.db.refresh(req)
    assert req.status == "DECLINED" and req.decline_reason == "INSUFFICIENT_FUNDS"
    audit = c.db.query(M.AuditLog).filter_by(action="WALLET_INTENT_CANCELLED").one()
    assert audit.detail["reason"] == "INSUFFICIENT_FUNDS"
    # Нэг айлчлалд нэг оролдлого — дахин уншигдсан ч wallet-ийг дахин нэхэхгүй
    out = exit_read(c)
    assert out["action"] == "awaiting_payment" and out["autopay"] is None
    from app.services.checkout import active_attempts
    assert active_attempts(c.db, s.id) == []       # QR/касс чөлөөтэй
    r = api(c, f"payments/{pid}/cancel", {"outcome": "NOT_CHARGED", "reason": "bad reason!"})
    assert r.status_code == 422


# ─────────────────────────── 4. Хугацаа дуусгалт ───────────────────────────

def test_expiry_cancels_intent_notifies_and_late_confirm_still_settles(c):
    register(c)
    s = arrive(c)
    out = exit_read(c)
    pid = out["autopay"]["payment_id"]
    deliver(c, lambda req: httpx.Response(200, json={"Success": True}))
    req = requests_(c)[0]
    assert PA.expire_due(c.db, now=datetime.utcnow()) == 0       # хугацаа болоогүй
    assert PA.expire_due(c.db, now=req.expires_at + timedelta(seconds=1)) == 1
    c.db.expire_all()
    req = requests_(c)[0]
    assert req.status == "EXPIRED" and req.notice_status == "PENDING"
    assert c.db.get(M.Payment, pid).status == "CANCELLED"
    from app.services.checkout import active_attempts
    assert active_attempts(c.db, s.id) == []       # QR/касс нээгдсэн
    assert c.db.query(M.AuditLog).filter_by(action="WALLET_AUTOPAY_EXPIRED").count() == 1

    # «Бүү тат» мэдэгдэл
    seen = []

    def _h(request):
        seen.append((dict(request.headers), json.loads(request.content)))
        return httpx.Response(200, json={"success": True})
    later = req.expires_at + timedelta(seconds=2)
    items = PA.claim_notices(c.db, now=later)
    assert len(items) == 1
    res = asyncio.run(PA.post(items[0]["url"], items[0]["payload"], items[0]["event_id"],
                              items[0]["event"], items[0]["partner"], transport=httpx.MockTransport(_h)))
    assert PA.record_notice(c.db, items[0]["id"], items[0]["token"], res) == "DONE"
    assert seen[0][0]["x-parking-event"] == "payment.request_expired"
    assert seen[0][1]["payment_id"] == pid and seen[0][1]["reason"] == "TIMEOUT"

    # Түнш аль хэдийн татчихсан байсан — хожуу confirm-ыг хүлээн авч хаалт нээнэ
    r = confirm(c, pid, amount=2000)
    assert r.status_code == 200 and r.json()["status"] == "PAID", r.text
    c.db.expire_all()
    assert requests_(c)[0].status == "PAID"
    assert c.db.get(M.ParkingSession, s.id).status == "CLOSED"


def test_undelivered_request_fails_at_expiry_and_retries_before(c):
    register(c)
    arrive(c)
    out = exit_read(c)
    seen = deliver(c, lambda req: httpx.Response(503))
    assert len(seen) == 1
    req = requests_(c)[0]
    assert req.status == "PENDING" and req.last_http_status == 503 and req.next_attempt_at > datetime.utcnow()
    # {"Success": false} хариу ч амжилтгүй
    req.next_attempt_at = datetime.utcnow() - timedelta(seconds=1); c.db.commit()
    deliver(c, lambda req: httpx.Response(200, json={"Success": False, "Message": "unknown user"}))
    req = requests_(c)[0]
    assert req.status == "PENDING" and req.last_error == "Success=false" and req.attempts == 2
    assert PA.expire_due(c.db, now=req.expires_at + timedelta(seconds=1)) == 1
    c.db.expire_all()
    req = requests_(c)[0]
    assert req.status == "FAILED" and req.notice_status == "PENDING"
    assert c.db.get(M.Payment, out["autopay"]["payment_id"]).status == "CANCELLED"


# ─────────────────────────── 5. Хамгаалалт ───────────────────────────

def test_no_request_without_autopay_or_when_disabled(c, monkeypatch):
    register(c, auto=False)
    arrive(c)
    out = exit_read(c)
    assert out["action"] == "awaiting_payment" and out["autopay"] is None
    assert requests_(c) == [] and c.db.query(M.Payment).count() == 0
    # TEST сервер (прод dump): мэдэгдэл унтраалттай бол автомат төлбөр ч явахгүй
    register(c, plate="1234УБА", auto=True)
    arrive(c, plate="1234УБА")
    monkeypatch.setattr(settings, "partner_vehicle_notify", False)
    assert exit_read(c, "1234УБА")["autopay"] is None


def test_exact_plate_only(c):
    register(c)
    s = arrive(c)
    s.status = "AWAITING_PAYMENT"; c.db.commit()
    assert PA.request_on_exit(c.db, s, "7886УКА") is None          # өөр уншилт
    assert PA.request_on_exit(c.db, s, PLATE, fuzzy=True) is None  # тааж тохоосон сешн
    assert requests_(c) == []


def test_latest_choice_wins_and_scope_respected(c):
    register(c, user="sp-77", key=KEY2)            # SocialPay-д ЭРТ бүртгүүлсэн
    register(c, key=KEY)                           # MonPay-д дараа нь
    # …гэвч хэрэглэгч SocialPay-аа дахин сонгосон (бүртгэлийн огноо биш, СОНГОЛТЫН огноо)
    for flag in (False, True):
        r = api(c, "vehicles/autopay", {"plateNo": PLATE, "userId": "sp-77", "autoPay": flag}, KEY2)
        assert r.status_code == 200
    s = arrive(c)
    out = exit_read(c)
    assert out["autopay"]["partner"] == "socialpay"
    assert c.db.get(M.Payment, out["autopay"]["payment_id"]).partner_key_id == c.key2.id
    assert c.db.query(M.Payment).count() == 1      # хоёр wallet-аас ЗЭРЭГ татахгүй
    # Өөр зогсоолоор хязгаарласан түлхүүр сонгогдохгүй
    c.db.query(M.Payment).delete(); c.db.query(M.PartnerAutopayRequest).delete()
    s.status = "AWAITING_PAYMENT"
    c.key2.site_id = c.other.id; c.db.commit()
    picked = PA.choose(c.db, s)
    assert picked is not None and picked[0].id == c.key.id


def test_existing_payment_attempt_blocks_request(c):
    register(c)
    s = arrive(c)
    s.status = "AWAITING_PAYMENT"; c.db.commit()
    c.db.add(M.Payment(session_id=s.id, provider="QPAY", payment_method="QR", sender_invoice_no="INV-1",
                       amount=2000, vat_amount=0, status="PENDING", raw_payload={}))
    c.db.commit()
    assert PA.request_on_exit(c.db, s, PLATE) is None
    assert requests_(c) == []


def test_bill_and_admin_show_autopay(c):
    register(c)
    arrive(c)
    out = exit_read(c)
    r = api(c, "vehicles/bill", {"plateNo": PLATE, "userId": "100264"}).json()["data"]
    assert r["autoPay"] is True and r["autoPayStatus"] == "PENDING"
    assert r["paymentId"] == out["autopay"]["payment_id"] and r["autoPayExpiresAt"].endswith("Z")
    keys = {k["name"]: k for k in c.client.get("/api/admin/partner-keys").json()["keys"]}
    assert keys["monpay"]["autopay_vehicle_count"] == 1 and keys["monpay"]["autopay_open"] == 1
    assert keys["monpay"]["autopay_problem"] is None
    assert "webhook" in keys["nohook"]["autopay_problem"]
    rows = c.client.get(f"/api/admin/partner-keys/{c.key.id}/autopay-requests").json()["requests"]
    assert len(rows) == 1 and rows[0]["plate"] == PLATE and rows[0]["amount"] == 2000.0
    veh = c.client.get(f"/api/admin/partner-keys/{c.key.id}/vehicles").json()["vehicles"]
    assert veh[0]["auto_pay"] is True
