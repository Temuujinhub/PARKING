"""SocialPay хяналтын засварууд (2026-10-14, rev4): өр/гарцын хориг, include_debts,
онлайн төлөх боломж, үнэгүй шалтгаан, алдааны хэлбэр, URL хоосон үеийн хүлээлгэ,
идэвхгүй зогсоол, kill switch, тоон transaction_id, scan хязгаар."""
import asyncio
import logging
from datetime import datetime, timedelta

import pytest

from app import models as M
from app.config import settings
from app.services import app_settings
from app.services import partner_vehicles as PV
from app.session_logic import amount_due, session_fee_info
from test_socialpay_vehicles import (KEY, NOW, close, ctx, events, factory, fake, post,  # noqa: F401
                                     stay, sub)


def debt(c, amount=2000, site=None, plate="7886УКР"):
    row = M.Compensation(site_id=(site or c.site).id, plate_number=plate, amount=amount,
                         reason="unpaid_exit", status="PENDING")
    c.db.add(row); c.db.commit()
    return row


def bill(c, user="A"):
    r = post(c, "bill", {"plateNo": "7886УКР", "userId": user})
    assert r.status_code == 200, r.text
    return r.json()["data"]


# ─────────────── M2: өр гарцыг барьдаг — bill харуулна, include_debts-ээр төлнө ───────────────

def test_bill_shows_debts_and_exit_block(ctx):
    sub(ctx, user="A")
    stay(ctx, entry=datetime.utcnow() - timedelta(minutes=40))
    d = bill(ctx)
    assert d["debtAmount"] == 0 and d["debtCount"] == 0 and d["exitBlockReason"] is None
    for _ in range(3):                       # анхдагч block_exit_debt_count = 3
        debt(ctx)
    debt(ctx, amount=500, site=ctx.other)    # түрээслэгчгүй зогсоол — ижил бүлэг
    d = bill(ctx)
    assert d["debtCount"] == 4 and d["debtAmount"] == 6500.0
    assert d["amountDueWithDebts"] == d["amountDue"] + 6500.0
    assert d["exitBlockReason"] == "debt_rule"            # зөвхөн amountDue төлбөл гарахгүй
    assert d["exitBlockReasonWithDebts"] is None          # өртэй нь хамт төлбөл нээгдэнэ
    assert d["payableOnline"] is True


def test_bill_debt_of_other_tenant_does_not_block(ctx):
    # 2026-10-24 шийдвэр (Тэмүүжин): өрийн хориг QR цуглуулалттай ИЖИЛ хүрээтэй —
    # зөвхөн тухайн түрээслэгчийн өр. Өмнө нь өөр компанийн өр бүх төлбөрөө төлсөн
    # машиныг гарцад гацаадаг байв.
    tenant = M.Tenant(name="Other tenant", code="OT")
    ctx.db.add(tenant); ctx.db.flush()
    ctx.other.tenant_id = tenant.id
    ctx.db.commit()
    sub(ctx, user="A")
    stay(ctx, entry=datetime.utcnow() - timedelta(minutes=40))
    debt(ctx)
    for _ in range(3):
        debt(ctx, site=ctx.other)            # өөр түрээслэгчийн өр — энд төлөгдөхгүй
    d = bill(ctx)
    assert d["debtCount"] == 1 and d["debtAmount"] == 2000.0 and d["exitBlockReason"] is None
    assert d["exitBlockReasonWithDebts"] is None
    app_settings.invalidate_cache()


def test_intent_include_debts_settles_debts_on_confirm(ctx):
    sub(ctx, user="A")
    s = stay(ctx, entry=datetime.utcnow() - timedelta(minutes=40))
    comps = [debt(ctx) for _ in range(3)]
    hdr = {"X-API-Key": KEY}
    plain = ctx.client.post("/api/v1/payments", json={"session_id": s.id}, headers=hdr).json()
    assert plain["amount"] == 1000.0 and plain["debt_amount"] == 0          # анхдагч хэвээр
    # Өмнөх intent-ийн дүн өөр → 409; цуцлаад өртэй нь шинээр
    assert ctx.client.post("/api/v1/payments", json={"session_id": s.id, "include_debts": True},
                           headers=hdr).status_code == 409
    ctx.client.post(f"/api/v1/payments/{plain['payment_id']}/cancel", json={"outcome": "NOT_CHARGED"},
                    headers=hdr)
    assert ctx.client.post("/api/v1/payments", json={"session_id": s.id, "include_debts": "yes"},
                           headers=hdr).status_code == 422
    r = ctx.client.post("/api/v1/payments", json={"session_id": s.id, "include_debts": True},
                        headers=hdr).json()
    assert r["amount"] == 7000.0 and r["debt_amount"] == 6000.0 and r["debt_count"] == 3
    assert r["parking_amount"] == 1000.0
    # Давтан хүсэлт ижил intent-ийг буцаана
    again = ctx.client.post("/api/v1/payments", json={"session_id": s.id, "include_debts": True},
                            headers=hdr).json()
    assert again["payment_id"] == r["payment_id"]
    assert bill(ctx)["paymentId"] == r["payment_id"]
    # Тоон transaction_id (m5) + amount-гүй
    ok = ctx.client.post(f"/api/v1/payments/{r['payment_id']}/confirm",
                         json={"transaction_id": 987654321}, headers=hdr)
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "PAID"
    ctx.db.expire_all()
    assert all(ctx.db.get(M.Compensation, c.id).status == "PAID" for c in comps)
    pay = ctx.db.get(M.Payment, r["payment_id"])
    assert pay.provider_payment_id == "987654321"
    assert ctx.db.get(M.ParkingSession, s.id).status in ("PAID", "CLOSED")
    from app.session_logic import exit_policy_block
    assert exit_policy_block(ctx.db, ctx.db.get(M.ParkingSession, s.id), datetime.utcnow()) is None
    # Ижил тоогоор давтан confirm идемпотент
    assert ctx.client.post(f"/api/v1/payments/{r['payment_id']}/confirm",
                           json={"transaction_id": 987654321}, headers=hdr).status_code == 200
    for bad in (True, 1.5, 1e20, "", None):
        p2 = ctx.client.post(f"/api/v1/payments/{r['payment_id']}/confirm",
                             json={"transaction_id": bad}, headers=hdr)
        assert p2.status_code in (409, 422), (bad, p2.status_code)


# ─────────────── m1 / m7: онлайн төлөх боломж, үнэгүй шалтгаан ───────────────

def test_payable_flags(ctx):
    sub(ctx, user="A")
    s = stay(ctx, entry=datetime.utcnow() - timedelta(minutes=5))       # үнэгүй 15 мин дотор
    d = bill(ctx)
    assert d["payableOnline"] is False and d["notPayableReason"] == "NOTHING_DUE"
    assert d["freeUntil"] is not None and d["freeRemainingMinutes"] >= 9
    debt(ctx)
    d = bill(ctx)
    assert d["payableOnline"] is True and d["amountDue"] == 0 and d["debtAmount"] == 2000.0
    s.status, s.paid_at = "PAID", datetime.utcnow()
    s.exit_deadline = s.paid_at + timedelta(minutes=15)
    ctx.db.commit()
    assert bill(ctx)["notPayableReason"] == "ALREADY_PAID"


def test_contract_and_no_charge_free_has_no_free_until(ctx):
    sub(ctx, user="A")
    ctx.db.add(M.RegisteredDriver(plate_number="7886УКР", site_id=ctx.site.id,
                                  valid_from=datetime.utcnow() - timedelta(days=1),
                                  valid_to=datetime.utcnow() + timedelta(days=30)))
    ctx.db.commit()
    stay(ctx, entry=datetime.utcnow() - timedelta(minutes=5))
    d = bill(ctx)
    assert d["isFree"] is True and d["freeReason"] == "Бүртгэлтэй жолооч"
    assert d["freeUntil"] is None and d["freeRemainingMinutes"] is None
    ctx.db.query(M.RegisteredDriver).delete(); ctx.site.no_charge = True; ctx.db.commit()
    d = bill(ctx)
    assert d["isFree"] is True and d["freeReason"] == "Төлбөргүй зогсоол" and d["freeUntil"] is None


def test_hospital_free_has_no_free_until(ctx, monkeypatch):
    sub(ctx, user="A")
    s = stay(ctx, entry=datetime.utcnow() - timedelta(minutes=40))
    from app.routers import socialpay_router as SPR
    import app.session_logic as SL

    def fake_fee(db, sess, at=None):
        return {"duration_minutes": 40, "paused_minutes": 0, "chargeable_minutes": 40,
                "base_fee": 0.0, "discount_amount": 1000.0, "vat_amount": 0.0, "total_fee": 0.0,
                "is_free": True, "reason": "Эмнэлгийн өдрийн хөнгөлөлт: 40 минут"}
    monkeypatch.setattr(SL, "session_fee_info", fake_fee)
    d = bill(ctx)
    assert d["isFree"] is True and d["freeUntil"] is None and d["freeReason"].startswith("Эмнэлгийн")
    assert SPR  # module imported


# ─────────────── m6: алдааны хэлбэр зөвхөн /socialpay/* дээр ───────────────

def test_error_shapes_scoped_to_socialpay(ctx, monkeypatch):
    r = ctx.client.get("/api/v1/socialpay/register", headers={"X-API-Key": KEY})
    assert r.status_code == 405 and r.json()["Success"] is False
    assert r.json()["Message"] == "METHOD_NOT_ALLOWED"
    r = ctx.client.post("/api/v1/socialpay/nope", json={}, headers={"X-API-Key": KEY})
    assert r.status_code == 404 and r.json()["Message"] == "NOT_FOUND" and "Detail" in r.json()
    # Бусад API-ийн хэлбэр өөрчлөгдөөгүй
    other = ctx.client.get("/api/v1/nope", headers={"X-API-Key": KEY})
    assert other.status_code == 404 and other.json() == {"detail": "Not Found"}
    other = ctx.client.get("/api/v1/sites")
    assert other.status_code == 401 and "detail" in other.json()

    def boom(*a, **k):
        raise RuntimeError("synthetic")
    monkeypatch.setattr(PV, "register_vehicle", boom)
    r = ctx.client.post("/api/v1/socialpay/register", json={"plateNo": "7886УКР", "userId": "1"},
                        headers={"X-API-Key": KEY})
    assert r.status_code == 500 and r.json()["Message"] == "INTERNAL_ERROR"


# ─────────────── m9: URL хоосон/идэвхгүй үед мэдэгдэл ХҮЛЭЭГДЭНЭ ───────────────

def test_cleared_url_holds_events_then_delivers(ctx, fake):
    sub(ctx)
    stay(ctx, entry=datetime.utcnow() - timedelta(minutes=5))
    PV.scan_once(ctx.db)
    url = ctx.key.vehicle_webhook_url
    ctx.key.vehicle_webhook_url = None; ctx.db.commit()
    assert asyncio.run(PV.deliver_due(factory(ctx))) == 0
    ctx.db.expire_all()
    assert events(ctx)[0].status == "PENDING"
    ctx.key.scopes = "read,pay"; ctx.key.vehicle_webhook_url = url; ctx.db.commit()
    assert asyncio.run(PV.deliver_due(factory(ctx))) == 0              # эрхгүй — хүлээнэ
    ctx.key.scopes = "read,pay,vehicles"; ctx.db.commit()
    assert asyncio.run(PV.deliver_due(factory(ctx))) == 1
    ctx.db.expire_all()
    assert events(ctx)[0].status == "DONE" and len(fake.received) == 1


def test_held_events_expire_after_retry_window(ctx):
    sub(ctx)
    s = stay(ctx, entry=datetime.utcnow() - timedelta(minutes=5))
    PV.scan_once(ctx.db)
    ctx.key.vehicle_webhook_url = None; ctx.db.commit()
    ev = events(ctx)[0]
    with factory(ctx)() as db:
        assert PV.claim_batch(db, now=ev.created_at + timedelta(hours=23)) == []
    ctx.db.expire_all()
    assert events(ctx)[0].status == "PENDING"
    with factory(ctx)() as db:
        PV.claim_batch(db, now=ev.created_at + timedelta(hours=25))
    ctx.db.expire_all()
    ev = events(ctx)[0]
    assert ev.status == "DEAD" and "24ц" in ev.last_error and ev.awaiting_exit is False
    # Илгээх боломжтой түлхүүрийн хуучин мэдэгдлийг (админы retry) энд DEAD болгохгүй
    ctx.key.vehicle_webhook_url = "http://127.0.0.1:9/x"
    ev.status = "PENDING"; ctx.db.commit()
    with factory(ctx)() as db:
        claimed = PV.claim_batch(db, now=ev.created_at + timedelta(hours=30))
    assert [c["id"] for c in claimed] == [ev.id]
    assert s.id == ev.session_id


# ─────────────── m11: kill switch ───────────────

def test_notify_kill_switch(ctx, fake, monkeypatch):
    monkeypatch.setattr(settings, "partner_vehicle_notify", False)
    sub(ctx)
    stay(ctx, entry=datetime.utcnow() - timedelta(minutes=5))
    PV.scan_once(ctx.db)
    asyncio.run(PV.deliver_due(factory(ctx)))
    assert fake.received == []
    ctx.db.expire_all()
    assert events(ctx)[0].status == "RETRY" and events(ctx)[0].last_error == "DISABLED"
    r = ctx.client.post(f"/api/admin/partner-keys/{ctx.key.id}/vehicles/test", json={}).json()
    assert r["ok"] is False and r["error"] == "DISABLED" and fake.received == []
    assert ctx.client.get("/api/admin/partner-keys").json()["vehicle_notify_enabled"] is False
    assert asyncio.run(asyncio.wait_for(PV.supervisor(), 1)) is None   # шууд буцна


# ─────────────── m12: идэвхгүй зогсоол ───────────────

def test_inactive_site_no_entry_and_no_bill(ctx):
    sub(ctx, user="A")
    s = stay(ctx, entry=NOW - timedelta(minutes=5))
    ctx.site.is_active = False; ctx.db.commit()
    assert PV.scan_once(ctx.db, NOW)["entry"] == 0
    assert post(ctx, "bill", {"plateNo": "7886УКР", "userId": "A"}).json()["Message"] == "NO_ACTIVE_SESSION"
    # Идэвхтэй байхад илгээсэн ОРСОН-ы хос гарц идэвхгүй болсон ч явна
    ctx.site.is_active = True; ctx.db.commit()
    assert PV.scan_once(ctx.db, NOW)["entry"] == 1
    ctx.site.is_active = False; ctx.db.commit()
    close(ctx, s, NOW + timedelta(minutes=1), confirmed=True)
    assert PV.scan_once(ctx.db, NOW + timedelta(minutes=2))["exit"] == 1


# ─────────────── m8: тогтвортой дараалал, хязгаар, IN-жагсаалт ───────────────

def test_candidate_limit_warns_and_awaiting_chunked(ctx, monkeypatch, caplog):
    monkeypatch.setattr(PV, "_MAX_CANDIDATES", 2)
    monkeypatch.setattr(PV, "_IN_CHUNK", 1)
    for i in range(3):
        plate = f"30{i}0УБА"
        sub(ctx, plate=plate)
        stay(ctx, plate=plate, entry=NOW - timedelta(minutes=10 + i), created=NOW - timedelta(minutes=10 + i))
    with caplog.at_level(logging.WARNING, logger="parking.partner_vehicles"):
        first = PV.scan_once(ctx.db, NOW)
    assert first["entry"] == 2 and "хязгаарт хүрлээ" in caplog.text
    assert PV.scan_once(ctx.db, NOW)["entry"] == 1          # дараагийн scan үлдсэнийг авна
    for e in events(ctx, event="ENTRY"):
        s = ctx.db.get(M.ParkingSession, e.session_id)
        close(ctx, s, NOW - timedelta(seconds=40), confirmed=True)
    monkeypatch.setattr(PV, "_MAX_CANDIDATES", 5000)
    # exit_since-ээс гадуур (хос зам, 1-ээр хуваасан IN) — бүгд олдоно
    monkeypatch.setattr(settings, "partner_vehicle_exit_max_age_min", 0)
    assert PV.scan_once(ctx.db, NOW)["exit"] == 3
