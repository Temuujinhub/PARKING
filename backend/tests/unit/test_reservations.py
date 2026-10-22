"""Урьдчилсан захиалга (wallet API, 2026-10-22) + өрийн огноо + түншийн e-Barimt — синтетик SQLite.

Сүлжээ (камер, банк, msgbill) хориотой. Шалгах зүйл:
  1. availability/create/confirm — үнэ тарифаар, идемпотент confirm, нэг алхмын create
  2. байрны тоо, давхар захиалга, external_ref давталт
  3. цуцлалт — бүтэн буцаалт (e-Barimt хүчингүй), буцаалтгүй цонх, accept_no_refund
  4. орох камер → CHECKED_IN, урьдчилсан төлбөр сешнээс хасагдаж гарахад хаалт нээгдэнэ;
     илүү зогссон хугацааг л нэхнэ
  5. sweep — EXPIRED / NO_SHOW / COMPLETED
  6. өрийн огноо (/sessions, bill) ба төлбөрийн задаргаа, баримтын төрөл, receipt_issuer
"""
import asyncio
import socket
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import models as M, session_logic as SL
from app.config import settings
from app.database import Base, get_db
from app.routers import integration_router as IR, reservations_router as RR
from app.services import app_settings as A, financial_jobs as FJ, reservations as R

PLATE = "1234УБА"


def iso(dt):
    return dt.replace(microsecond=0).isoformat() + "Z"


@pytest.fixture
def c(monkeypatch):
    def forbidden(*a, **k):
        raise AssertionError("Network forbidden in synthetic reservation test")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(settings, "ebarimt_mock_receipts", False)
    monkeypatch.setattr(settings, "qpay_ebarimt", False)
    monkeypatch.setattr(FJ.msgbill, "account_enabled_for", lambda *a: None)
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    A.invalidate_cache()
    with Session(engine, autoflush=False) as db:
        tariff = M.TariffTemplate(name="Hourly", free_minutes=0, extra_hour_price=1000, grace_minutes=15)
        site = M.ParkingSite(name="Номадс", site_code="NOMADS", tariff_template=tariff, address="Талбай")
        key = M.PartnerKey(name="mobifinance", key_hash="h" * 64, key_prefix="pk_test",
                           scopes="read,pay", webhook_url="https://wallet.example/hook")
        db.add_all([site, key]); db.flush()
        entry = M.Device(site_id=site.id, name="Entry", device_type="camera", lane_dir="entry",
                         lane_no=1, status="active", auto_open=False, ip_address="192.0.2.10")
        exit_cam = M.Device(site_id=site.id, name="Exit", device_type="camera", lane_dir="exit",
                            lane_no=1, status="active", ip_address="192.0.2.11")
        bar = M.Device(site_id=site.id, name="Barrier", device_type="barrier", lane_dir="exit",
                       lane_no=1, status="active")
        db.add_all([entry, exit_cam, bar]); db.commit()
        A.set_site_rules(db, A.RESERVATION_KEY, site.id, {"enabled": True, "spots": 1}, "test")
        A.set_site_rules(db, A.EXITRULES_KEY, site.id, {"wallet_auto_deduct": False}, "test")
        db.commit()
        staff, commands = [], []
        monkeypatch.setattr("app.ws.notify", lambda sid, kind, data: staff.append((kind, data)))
        monkeypatch.setattr(SL, "notify", lambda *a, **k: None)
        monkeypatch.setattr(SL, "schedule_capture", lambda *a, **k: None)
        monkeypatch.setattr(SL, "schedule_display", lambda *a, **k: None)
        monkeypatch.setattr(SL, "_inner_lane_devices", lambda *a: [])

        async def open_fake(db, barrier, session_id, source, **kw):
            commands.append((barrier.id, session_id, source))
            cmd = M.BarrierCommand(device_id=barrier.id, session_id=session_id, command="open",
                                   command_source=source, status="SUCCESS")
            db.add(cmd); db.commit()
            return cmd
        monkeypatch.setattr(SL, "open_barrier", open_fake)
        p = IR.PartnerAuth("mobifinance"); p.scopes = "read,pay"; p.key_id = key.id
        yield SimpleNamespace(db=db, site=site, key=key, entry=entry, exit=exit_cam, partner=p,
                              staff=staff, commands=commands)
    A.invalidate_cache(); engine.dispose()


def body(start, minutes=60, **extra):
    return {"site_code": "NOMADS", "plate_number": PLATE, "start_time": iso(start),
            "duration_minutes": minutes, **extra}


def create(c, start, minutes=60, **extra):
    r, created = R.create(c.db, c.partner, body(start, minutes, **extra))
    c.db.commit()
    return r, created


def confirm(c, r, tx="MP-1", **extra):
    return asyncio.run(R.confirm(c.db, c.partner, r, {"transaction_id": tx, **extra}))


# ── 1. Үүсгэх / батлах ─────────────────────────────────────────────────────

def test_quote_create_confirm_uses_site_tariff_and_notifies_guard(c):
    start = datetime.utcnow() + timedelta(hours=2)
    q = R.availability(c.db, c.partner, "NOMADS", {"start_time": iso(start), "duration_minutes": 120})
    assert q["available"] and q["amount"] == 2000.0 and q["spots"] == 1 and q["free_spots"] == 1
    r, created = create(c, start, 120)
    assert created and r.status == R.PENDING and float(r.amount) == 2000.0
    pay = c.db.get(M.Payment, r.payment_id)
    assert pay.kind == "RESERVATION" and pay.status == "PENDING" and pay.session_id is None
    assert pay.site_id == c.site.id and pay.payment_method == "WALLET"
    r = confirm(c, r, amount=2000)
    assert r.status == R.CONFIRMED and r.confirmed_at and r.hold_expires_at is None
    pay = c.db.get(M.Payment, r.payment_id)
    assert pay.status == "PAID" and pay.provider_payment_id == "MP-1"
    # e-Barimt — систем гаргана (энэ тестэд суваг тохиргоогүй тул FAILED мөр, гэхдээ мөр үүснэ)
    assert c.db.query(M.VatReceipt).filter_by(payment_id=pay.id).count() == 1
    # Харуулын дэлгэц + түншийн webhook (receipt.ready-д зориулсан partner-paid түлхүүртэй)
    assert c.staff[-1][0] == "RESERVATION_CONFIRMED" and c.staff[-1][1]["plate"] == PLATE
    job = c.db.query(M.FinancialJob).filter_by(job_key=f"partner-paid:{pay.id}").one()
    assert job.payload["event"]["event"] == "reservation.confirmed"
    assert job.payload["event"]["reservation"]["reservation_no"] == r.reservation_no
    # Идемпотент: ижил гүйлгээ → алдаагүй; өөр гүйлгээ → 409
    assert confirm(c, r, amount=2000).status == R.CONFIRMED
    with pytest.raises(R.ReservationError) as e:
        confirm(c, r, tx="MP-2")
    assert e.value.code == "ALREADY_CONFIRMED" and e.value.status == 409


def test_confirm_amount_mismatch_and_cancelled(c):
    r, _ = create(c, datetime.utcnow() + timedelta(hours=2))
    with pytest.raises(R.ReservationError) as e:
        confirm(c, r, amount=999)
    assert e.value.code == "AMOUNT_MISMATCH"
    c.db.rollback()
    asyncio.run(R.cancel(c.db, c.partner, r, {}))
    with pytest.raises(R.ReservationError) as e:
        confirm(c, r)
    assert e.value.code == "RESERVATION_CANCELLED"


def test_validation_rules(c):
    now = datetime.utcnow()
    with pytest.raises(R.ReservationError) as e:
        R.create(c.db, c.partner, {**body(now + timedelta(hours=1)), "start_time": "2026-10-22T10:00:00"})
    assert e.value.code == "INVALID_TIME"     # цагийн бүсгүй утгыг таамаглахгүй
    with pytest.raises(R.ReservationError) as e:
        R.create(c.db, c.partner, body(now + timedelta(hours=1), 30))
    assert e.value.code == "INVALID_DURATION"
    with pytest.raises(R.ReservationError) as e:
        R.create(c.db, c.partner, body(now - timedelta(hours=1)))
    assert e.value.code == "START_TOO_SOON"
    with pytest.raises(R.ReservationError) as e:
        R.create(c.db, c.partner, body(now + timedelta(days=30)))
    assert e.value.code == "START_TOO_FAR"
    with pytest.raises(R.ReservationError) as e:
        R.create(c.db, c.partner, {**body(now + timedelta(hours=1)), "plate_number": "1234YBA"})
    assert e.value.code == "INVALID_PLATE"    # латиныг таамаглаж хөрвүүлэхгүй
    A.set_site_rules(c.db, A.RESERVATION_KEY, c.site.id, {"enabled": False}, "test"); c.db.commit()
    with pytest.raises(R.ReservationError) as e:
        R.create(c.db, c.partner, body(now + timedelta(hours=1)))
    assert e.value.code == "RESERVATIONS_DISABLED"


def test_capacity_duplicate_and_external_ref(c):
    start = datetime.utcnow() + timedelta(hours=3)
    r1, created = create(c, start, external_ref="order-1")
    assert created
    again, created = create(c, start, external_ref="order-1")
    assert not created and again.id == r1.id            # timeout-ийн дараах давталт
    with pytest.raises(R.ReservationError) as e:
        R.create(c.db, c.partner, {**body(start, 90), "external_ref": "order-1"})
    assert e.value.code == "EXTERNAL_REF_CONFLICT"
    with pytest.raises(R.ReservationError) as e:
        R.create(c.db, c.partner, {**body(start + timedelta(minutes=30)), "plate_number": "5678УБА"})
    assert e.value.code == "NO_AVAILABILITY"            # spots=1, давхцаж байна
    with pytest.raises(R.ReservationError) as e:
        R.create(c.db, c.partner, body(start + timedelta(minutes=30)))
    assert e.value.code == "DUPLICATE_RESERVATION"
    r2, _ = create(c, start + timedelta(hours=2), external_ref="order-2")   # давхцахгүй цонх
    assert r2.status == R.PENDING
    # Барих хугацаа дууссан PENDING байр эзэлдэггүй
    r1.hold_expires_at = datetime.utcnow() - timedelta(seconds=1); c.db.commit()
    r3, _ = create(c, start, external_ref="order-3", plate_number="5678УБА")
    assert r3.status == R.PENDING


# ── 2. Цуцлалт ─────────────────────────────────────────────────────────────

def test_cancel_refund_windows(c):
    far, _ = create(c, datetime.utcnow() + timedelta(hours=5), external_ref="a")
    confirm(c, far, tx="T-far")
    r = asyncio.run(R.cancel(c.db, c.partner, far, {"reason": "хэрэглэгч"}))
    assert r.status == R.CANCELLED and float(r.refund_amount) == 1000.0
    pay = c.db.get(M.Payment, r.payment_id)
    assert pay.status == "REFUNDED"            # орлогод тоологдохгүй
    assert all(x.status == "CANCELLED" for x in c.db.query(M.VatReceipt).filter_by(payment_id=pay.id))
    assert c.staff[-1][0] == "RESERVATION_CANCELLED"
    assert asyncio.run(R.cancel(c.db, c.partner, r, {})).status == R.CANCELLED   # идемпотент

    soon, _ = create(c, datetime.utcnow() + timedelta(minutes=20), external_ref="b")
    confirm(c, soon, tx="T-soon")
    view = R.serialize(c.db, soon)
    assert view["cancellable"] and view["refund_if_cancelled_now"] == 0.0
    with pytest.raises(R.ReservationError) as e:
        asyncio.run(R.cancel(c.db, c.partner, soon, {}))
    assert e.value.code == "NO_REFUND_WINDOW"
    c.db.rollback()
    r = asyncio.run(R.cancel(c.db, c.partner, soon, {"accept_no_refund": True}))
    assert r.status == R.CANCELLED and float(r.refund_amount) == 0.0
    assert c.db.get(M.Payment, r.payment_id).status == "PAID"   # буцаалтгүй — орлого хэвээр


def test_other_partner_cannot_touch(c):
    r, _ = create(c, datetime.utcnow() + timedelta(hours=2))
    other = IR.PartnerAuth("socialpay"); other.scopes = "read,pay"; other.key_id = None
    with pytest.raises(R.ReservationError) as e:
        R.get_owned(c.db, other, r.id)
    assert e.value.status == 404


# ── 3. Орох камер → урьдчилсан төлбөр сешнд ────────────────────────────────

def _arrive(c, r, minutes_ago):
    asyncio.run(SL.handle_entry(c.db, c.entry, PLATE, 95, {}))
    s = c.db.query(M.ParkingSession).filter_by(plate_number=PLATE).one()
    s.entry_time = datetime.utcnow() - timedelta(minutes=minutes_ago); c.db.commit()
    return s


def test_entry_links_prepaid_and_exit_opens_within_window(c):
    r, _ = create(c, datetime.utcnow() + timedelta(minutes=5))
    confirm(c, r)
    s = _arrive(c, r, 40)
    c.db.refresh(r)
    assert r.status == R.CHECKED_IN and r.session_id == s.id
    assert c.db.get(M.Payment, r.payment_id).session_id == s.id
    assert c.staff[-1][0] == "RESERVATION_ARRIVED"
    assert SL.amount_due(c.db, s, SL.session_fee_info(c.db, s)) == 0.0
    out = asyncio.run(SL.handle_exit(c.db, c.exit, PLATE, 95, {}))
    assert out["action"] == "exit_completed", out
    c.db.refresh(s)
    assert s.status == "CLOSED" and c.commands     # 0₮ үлдэгдэлтэй AWAITING_PAYMENT-д гацахгүй
    assert R.sweep(c.db)["completed"] == 1
    c.db.refresh(r)
    assert r.status == R.COMPLETED


def test_overstay_charges_only_the_difference(c):
    r, _ = create(c, datetime.utcnow() + timedelta(minutes=5))
    confirm(c, r)
    s = _arrive(c, r, 100)        # 100 мин → 2000₮, урьдчилж 1000₮ төлсөн
    out = asyncio.run(SL.handle_exit(c.db, c.exit, PLATE, 95, {}))
    assert out["action"] == "awaiting_payment" and out["amount_due"] == 1000.0, out


def test_confirm_after_arrival_links_existing_stay(c):
    r, _ = create(c, datetime.utcnow() + timedelta(minutes=5))
    asyncio.run(SL.handle_entry(c.db, c.entry, PLATE, 95, {}))   # захиалга баталгаажаагүй
    assert c.db.get(M.Reservation, r.id).status == R.PENDING
    r = confirm(c, r)
    assert r.status == R.CHECKED_IN and r.session_id


def test_registered_only_site_admits_reserved_car(c):
    c.site.registered_only = True; c.db.commit()
    r, _ = create(c, datetime.utcnow() + timedelta(minutes=5))
    confirm(c, r)
    out = asyncio.run(SL.handle_entry(c.db, c.entry, PLATE, 95, {}))
    assert out["action"] == "entry"
    assert c.db.get(M.Reservation, r.id).status == R.CHECKED_IN


# ── 4. Арын цэвэрлэгээ ─────────────────────────────────────────────────────

def test_sweep_expired_and_no_show(c):
    a, _ = create(c, datetime.utcnow() + timedelta(hours=1), external_ref="x")
    a.hold_expires_at = datetime.utcnow() - timedelta(minutes=1); c.db.commit()
    b, _ = create(c, datetime.utcnow() + timedelta(hours=3), external_ref="y", plate_number="5678УБА")
    confirm(c, b, tx="T-b")
    b.start_time -= timedelta(hours=5); b.end_time -= timedelta(hours=5); c.db.commit()
    out = R.sweep(c.db)
    assert out["expired"] == 1 and out["no_show"] == 1
    assert c.db.get(M.Reservation, a.id).status == R.EXPIRED
    assert c.db.get(M.Payment, a.payment_id).status == "CANCELLED"
    assert c.db.get(M.Reservation, b.id).status == R.NO_SHOW
    assert c.db.get(M.Payment, b.payment_id).status == "PAID"     # ирээгүй — буцаалтгүй
    assert c.db.query(M.FinancialJob).filter_by(job_key=f"reservation:{b.id}:reservation.no_show").count() == 1


# ── 5. HTTP API хэлбэр ─────────────────────────────────────────────────────

def test_http_one_step_create_and_error_shape(c):
    app = FastAPI()
    app.include_router(RR.router)
    app.dependency_overrides[get_db] = lambda: c.db
    app.dependency_overrides[IR.require_partner] = lambda: c.partner
    client = TestClient(app)
    start = datetime.utcnow() + timedelta(hours=2)
    res = client.post("/api/v1/reservations", json={**body(start), "transaction_id": "MP-9", "amount": 1000})
    assert res.status_code == 201, res.text
    data = res.json()
    assert data["status"] == "CONFIRMED" and data["transaction_id"] == "MP-9"
    assert data["start_time"].endswith("Z") and data["reservation_no"].startswith("R")
    got = client.get(f"/api/v1/reservations/{data['reservation_id']}").json()
    assert got["status"] == "CONFIRMED" and isinstance(got["ebarimts"], list)
    res = client.post("/api/v1/reservations", json={**body(start), "plate_number": "5678УБА"})
    assert res.status_code == 409 and res.json()["code"] == "NO_AVAILABILITY"
    assert isinstance(res.json()["detail"], str)
    av = client.get("/api/v1/reservations/availability",
                    params={"site_code": "NOMADS", "start_time": iso(start), "duration_minutes": 60})
    assert av.status_code == 200 and av.json()["available"] is False
    lst = client.get("/api/v1/reservations", params={"plate": PLATE}).json()
    assert [x["reservation_id"] for x in lst["reservations"]] == [data["reservation_id"]]


# ── 6. Өрийн огноо, задаргаа, баримт ───────────────────────────────────────

def test_debt_items_carry_dates_and_origin(c):
    other = M.ParkingSite(name="Кэй Эйч", site_code="KH")     # ижил (түрээслэгчгүй) байгууллага
    c.db.add(other); c.db.flush()
    old = M.ParkingSession(site_id=other.id, plate_number=PLATE, status="MANUAL_CLOSED",
                           entry_time=datetime(2026, 10, 1, 2, 0), exit_time=datetime(2026, 10, 1, 5, 30),
                           duration_minutes=210)
    c.db.add(old); c.db.flush()
    debt = M.Compensation(site_id=other.id, session_id=old.id, plate_number=PLATE, amount=3000,
                          reason="unpaid_exit", status="PENDING", created_at=datetime(2026, 10, 1, 5, 31))
    now_stay = M.ParkingSession(site_id=c.site.id, plate_number=PLATE, status="OPEN",
                                entry_time=datetime.utcnow() - timedelta(minutes=30))
    c.db.add_all([debt, now_stay]); c.db.commit()
    payload = IR._session_payload(c.db, now_stay)
    assert payload["debt_amount"] == 3000.0 and payload["debt_count"] == 1
    d = payload["debts"][0]
    assert d["created_at"] == "2026-10-01T05:31:00" and d["reason_text"] == "Төлбөр төлөлгүй гарсан"
    assert d["entry_time"] == "2026-10-01T02:00:00" and d["exit_time"] == "2026-10-01T05:30:00"
    assert d["site_name"] == "Кэй Эйч" and d["duration_minutes"] == 210
    intent = IR.create_payment_intent({"session_id": now_stay.id, "include_debts": True}, c.db, c.partner)
    assert intent["debt_amount"] == 3000.0 and intent["debts"][0]["debt_id"] == debt.id
    pay = c.db.get(M.Payment, intent["payment_id"])
    br = IR.payment_breakdown(c.db, pay)
    assert br["parking_amount"] == intent["parking_amount"] and br["debts"][0]["created_at"]
    from app.routers.socialpay_router import _bill_debts
    camel = _bill_debts(c.db, [debt])[0]
    assert camel["createdAt"] == "2026-10-01T05:31:00.000Z" and camel["enteredDate"].endswith("Z")


def test_receipt_kinds_and_partner_receipt_issuer(c, monkeypatch):
    stay = M.ParkingSession(site_id=c.site.id, plate_number=PLATE, status="PAID",
                            entry_time=datetime.utcnow())
    c.db.add(stay); c.db.flush()
    pay = M.Payment(session_id=stay.id, provider="mobifinance", payment_method="WALLET",
                    partner_key_id=c.key.id, sender_invoice_no="T-1", amount=4000, vat_amount=364,
                    status="PAID", paid_at=datetime.utcnow())
    c.db.add(pay); c.db.flush()
    main = M.VatReceipt(payment_id=pay.id, session_id=stay.id, amount=1000, vat_amount=91,
                        status="SENT", ebarimt_id="1" * 33, lottery_code="AB 1")
    debt = M.VatReceipt(payment_id=pay.id, session_id=None, amount=3000, vat_amount=273,
                        status="SENT", ebarimt_id="2" * 33, lottery_code="AB 2")
    c.db.add_all([main, debt]); c.db.flush()
    c.db.add(M.FinancialJob(job_key=f"receipt:{debt.id}", kind="RECEIPT", payment_id=pay.id,
                            receipt_id=debt.id, payload={"idempotency_key": f"pay-{pay.id}-comp-DEBT1"},
                            status="DONE"))
    c.db.commit()
    rows = IR.receipt_list(c.db, pay)
    assert [(r["kind"], r["debt_id"]) for r in rows] == [("PARKING", None), ("DEBT", "DEBT1")]
    # receipt_issuer: хоосон → ерөнхий дүрэм (энд msgbill унтраалттай → POSAPI)
    assert FJ.receipt_provider(pay) == "POSAPI"
    c.key.receipt_issuer = "SYSTEM"; c.db.commit()
    monkeypatch.setattr(FJ.msgbill, "api_key_for", lambda site: FJ.msgbill.MsgbillAccount(api_key="k"))
    assert FJ.receipt_provider(pay) == "MSGBILL"
    pay.payment_method = "CARD"     # түншийн тохиргоо зөвхөн WALLET төлбөрт
    assert FJ.receipt_provider(pay) == "POSAPI"
