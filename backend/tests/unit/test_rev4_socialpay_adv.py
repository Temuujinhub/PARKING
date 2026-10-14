"""Reviewer (rev4) adversarial probes — засварын ДАРААХ зан төлөвийг баталгаажуулна."""
import asyncio
import time
from datetime import datetime, timedelta

import httpx

from app import models as M
from app.services import partner_vehicles as PV
from test_socialpay_vehicles import (KEY, NOW, close, ctx, events, factory, fake, post,  # noqa: F401
                                     stay, sub)


def test_paid_overstay_bill_flags_not_payable_online(ctx):
    """PAID сешн deadline хэтэрсэн: bill amountDue>0 боловч онлайнаар төлөх боломжгүйг
    (payableOnline=false, PAID_OVERSTAY) тодорхой хэлнэ — intent 400 хэвээр (QR-тэй ижил)."""
    sub(ctx, user="A")
    now = datetime.utcnow()
    s = stay(ctx, entry=now - timedelta(minutes=130), status="PAID")
    s.paid_at = now - timedelta(minutes=100)
    s.exit_deadline = s.paid_at + timedelta(minutes=15)
    ctx.db.add(M.Payment(session_id=s.id, provider="QPAY", payment_method="QPAY",
                         sender_invoice_no="X1", amount=1000, status="PAID",
                         paid_at=s.paid_at, fee_snapshot={"parking_amount": 1000}))
    ctx.db.commit()
    d = post(ctx, "bill", {"plateNo": "7886УКР", "userId": "A"}).json()["data"]
    r = ctx.client.post("/api/v1/payments", json={"session_id": s.id}, headers={"X-API-Key": KEY})
    assert d["amountDue"] > 0 and r.status_code == 400
    assert d["payableOnline"] is False and d["notPayableReason"] == "PAID_OVERSTAY"


def test_inferred_paid_exit_with_stale_exit_device_is_not_sent(ctx):
    """close_paid_as_inferred_exit: exit_device_id үлддэг, exit_time = exit_deadline,
    exit_confirmed=False → бодит гарц БИШ, spec форматад EXIT илгээхгүй."""
    sub(ctx)
    entered = NOW - timedelta(minutes=90)
    s = stay(ctx, entry=entered, created=entered)
    cam = M.Device(site_id=s.site_id, name="exit", device_type="camera", lane_dir="exit",
                   device_key="cam-x")
    ctx.db.add(cam); ctx.db.flush()
    s.exit_device_id, s.status, s.exit_confirmed = cam.id, "CLOSED", False
    s.paid_at = NOW - timedelta(minutes=55)
    s.exit_deadline = s.paid_at + timedelta(minutes=15)
    s.exit_time = s.exit_deadline                       # = deadline (inferred)
    s.note = "төлсөн ч гарах уншилт алдагдсан — deadline … дээр гарсан гэж үзэж"
    ctx.db.commit()
    assert PV.scan_once(ctx.db, NOW)["exit"] == 0


def test_inferred_paid_exit_without_deadline_and_real_grace_exit(ctx):
    sub(ctx)
    entered = NOW - timedelta(minutes=90)
    s = stay(ctx, entry=entered, created=entered)
    s.status, s.exit_confirmed, s.paid_at = "CLOSED", False, NOW - timedelta(minutes=50)
    s.exit_time, s.exit_device_id = s.paid_at, None     # exit_deadline байхгүй → paid_at
    ctx.db.commit()
    assert PV.scan_once(ctx.db, NOW)["exit"] == 0
    # Grace дотор POS эгнээгээр гарсан (exit_confirmed=False, төхөөрөмжтэй, deadline-аас өмнө)
    s2 = stay(ctx, plate="2222УБА", entry=entered, created=entered)
    sub(ctx, plate="2222УБА")
    close(ctx, s2, NOW - timedelta(minutes=10), confirmed=False, device=True, status="CLOSED")
    s2.paid_at, s2.exit_deadline = NOW - timedelta(minutes=10, seconds=1), NOW + timedelta(minutes=5)
    ctx.db.commit()
    assert PV.scan_once(ctx.db, NOW)["exit"] == 1


def test_site_scoped_admin_cannot_mint_all_sites_or_vehicles_key(ctx):
    ctx.user.role = "ADMIN"
    ctx.user.site_id = ctx.site.id          # tenant/site-scoped admin
    ctx.db.commit()
    for body in ({"name": "rogue", "can_vehicles": True},
                 {"name": "rogue2"},
                 {"name": "rogue3", "site_id": ctx.other.id},
                 {"name": "rogue4", "site_id": ctx.site.id, "can_vehicles": True}):
        assert ctx.client.post("/api/admin/partner-keys", json=body).status_code == 403, body
    ok = ctx.client.post("/api/admin/partner-keys", json={"name": "own", "site_id": ctx.site.id})
    assert ok.status_code == 200 and "vehicles" not in ok.json()["scopes"]


def test_unicode_user_id_variants_rejected(ctx):
    a = post(ctx, "register", {"plateNo": "7886УКР", "userId": "100264"}).json()
    b = post(ctx, "register", {"plateNo": "7886УКР", "userId": "１００２６４"})
    assert a["Message"] == "OK"
    assert b.status_code == 400 and b.json()["Message"] == "INVALID_USER_ID"


def test_plate_changed_after_entry_never_closes(ctx):
    """ENTRY P-д илгээгдсэн; дугаар бүртгэлгүй Q болж засагдсан → EXIT илгээгдэхгүй
    (бусдын/бүртгэлгүй машины мэдээ задрахгүй); P-ийн awaiting хос 7 хоногийн дараа дуусна."""
    sub(ctx)
    entered = NOW - timedelta(minutes=30)
    s = stay(ctx, entry=entered, created=entered)
    assert PV.scan_once(ctx.db, NOW)["entry"] == 1
    s.plate_number = "1111ААА"
    ctx.db.commit()
    close(ctx, s, NOW + timedelta(minutes=5), confirmed=True, device=True)
    assert PV.scan_once(ctx.db, NOW + timedelta(minutes=6))["exit"] == 0


def test_event_loop_not_blocked_by_per_event_client(ctx, fake, monkeypatch):
    """20 мэдэгдэл: SSL context НЭГ л удаа (thread-д), loop бараг царцахгүй."""
    built = []
    real = httpx.create_ssl_context
    monkeypatch.setattr(httpx, "create_ssl_context", lambda *a, **k: built.append(1) or real(*a, **k))
    for i in range(20):
        plate = f"{1000 + i}УКР"
        sub(ctx, plate=plate)
        stay(ctx, plate=plate, entry=datetime.utcnow() - timedelta(minutes=5))
    PV.scan_once(ctx.db)

    async def run():
        lags, stop = [], asyncio.Event()

        async def tick():
            while not stop.is_set():
                t = time.perf_counter(); await asyncio.sleep(0.005)
                lags.append(time.perf_counter() - t - 0.005)
        tk = asyncio.create_task(tick())
        await asyncio.sleep(0.05)
        n = await PV.deliver_due(factory(ctx))
        stop.set(); await tk
        return n, max(lags)
    n, lag = asyncio.run(run())
    print(f"DELIVERED {n} max_loop_lag={lag*1000:.0f}ms ssl_contexts={len(built)}")
    assert n == 20 and len(built) == 1 and len(fake.received) == 20
    assert lag < 0.5      # өмнө нь 0.67–2.7с; ачаалалтай 1 vCPU дээр ч хангалттай зай
