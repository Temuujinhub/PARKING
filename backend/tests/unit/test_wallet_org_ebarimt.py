"""Wallet API — байгууллагын e-Barimt (2026-11-11): «байгууллагын ТТД-г хаашаа яаж явуулах нь байхгүй».

  1. intent / confirm-д `payer_reg_no` (хуучин нэр customer_tin) → COMPANY баримт, хариунд
     receipt_type/payer_reg_no; буруу дугаар 422 (юу ч үүсгэхгүй)
  2. GET /api/v1/ebarimt/payer — нэр шалгах (tin_lookup), формат буруу 422
  3. POST /api/v1/payments/{id}/ebarimt — илгээгээгүй баримтыг байгууллагынх болгоно;
     аль хэдийн гарсан (SENT) баримтад 409 RECEIPT_ALREADY_ISSUED (ТЕГ-ийн баримтыг автоматаар цуцлахгүй)
"""
import hashlib
import socket
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import models as M, ratelimit
from app.config import settings
from app.database import Base, get_db
from app.routers import integration_router as I
from app.services import app_settings as AS, financial_jobs as FJ

KEY = "pk_wallet_org_synthetic"


@pytest.fixture
def c(monkeypatch):
    def forbidden(*a, **k):
        raise AssertionError("Network forbidden")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(settings, "partner_keys", "")
    monkeypatch.setattr(settings, "ebarimt_mock_receipts", False)
    monkeypatch.setattr(settings, "qpay_ebarimt", False)
    monkeypatch.setattr(FJ.msgbill, "account_enabled_for", lambda *a: None)
    ratelimit._hits.clear()
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    AS.invalidate_cache()
    with Session(engine, autoflush=False) as db:
        tariff = M.TariffTemplate(name="Hourly", free_minutes=0, extra_hour_price=1000, grace_minutes=15)
        tariff.tiers = [M.TariffTier(upto_minutes=60, price=1000), M.TariffTier(upto_minutes=120, price=2000)]
        site = M.ParkingSite(name="Номадс", site_code="NOMADS", tariff_template=tariff)
        db.add(site)
        db.flush()
        key = M.PartnerKey(name="mobifinance", key_hash=hashlib.sha256(KEY.encode()).hexdigest(),
                           key_prefix=KEY[:10], scopes="read,pay")
        db.add(key)
        db.commit()
        app = FastAPI()
        app.include_router(I.router)
        app.dependency_overrides[get_db] = lambda: db
        with TestClient(app) as client:
            yield SimpleNamespace(db=db, client=client, site=site, key=key)
    AS.invalidate_cache()
    engine.dispose()


def _session(c, plate="1234УБА", minutes=90):
    s = M.ParkingSession(site_id=c.site.id, plate_number=plate, status="OPEN",
                         entry_time=datetime.utcnow() - timedelta(minutes=minutes))
    c.db.add(s)
    c.db.commit()
    return s


def post(c, path, body):
    return c.client.post(f"/api/v1/{path}", json=body, headers={"X-API-Key": KEY})


def test_intent_with_company_reg_issues_org_receipt(c):
    s = _session(c)
    r = post(c, "payments", {"session_id": s.id, "payer_reg_no": " 6 23 5 9 7 1 "})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["receipt_type"] == "ORGANIZATION" and d["payer_reg_no"] == "6235971"
    p = c.db.get(M.Payment, d["payment_id"])
    assert (p.customer_tin, p.ebarimt_receiver_type) == ("6235971", "COMPANY")
    r = post(c, f"payments/{d['payment_id']}/confirm", {"transaction_id": "MP-1", "amount": d["amount"]})
    assert r.status_code == 200, r.text
    assert r.json()["receipt_type"] == "ORGANIZATION"
    recs = c.db.query(M.VatReceipt).filter_by(payment_id=p.id).all()
    assert recs and all(x.customer_tin == "6235971" for x in recs)
    jobs = c.db.query(M.FinancialJob).filter_by(payment_id=p.id, kind="RECEIPT").all()
    assert jobs and all(j.payload.get("receiver_type") == "COMPANY" for j in jobs)
    st = c.client.get(f"/api/v1/payments/{p.id}", headers={"X-API-Key": KEY}).json()
    assert st["receipt_type"] == "ORGANIZATION" and st["payer_reg_no"] == "6235971"
    assert all(e["payer_reg_no"] == "6235971" and e["receiver_type"] == "COMPANY" for e in st["ebarimts"])


def test_invalid_reg_rejected_before_anything(c):
    s = _session(c)
    for bad in ("АА00112233", "123", "abcdefg"):
        r = post(c, "payments", {"session_id": s.id, "payer_reg_no": bad})
        assert r.status_code == 422, bad
    assert c.db.query(M.Payment).count() == 0
    r = post(c, "payments", {"session_id": s.id, "customer_tin": "12345678901"})   # хуучин нэр, ТТД
    assert r.status_code == 200 and r.json()["payer_reg_no"] == "12345678901"


def test_citizen_default_and_confirm_time_reg(c):
    s = _session(c)
    d = post(c, "payments", {"session_id": s.id}).json()
    assert d["receipt_type"] == "CITIZEN" and d["payer_reg_no"] is None
    # confirm-д өгсөн регистр баримт үүсэхээс ӨМНӨ хэрэглэгдэнэ
    r = post(c, f"payments/{d['payment_id']}/confirm",
             {"transaction_id": "MP-2", "amount": d["amount"], "payer_reg_no": "6235971"})
    assert r.status_code == 200 and r.json()["receipt_type"] == "ORGANIZATION"
    p = c.db.get(M.Payment, d["payment_id"])
    assert p.ebarimt_receiver_type == "COMPANY"
    # давтан confirm хүлээн авагчийг өөрчлөхгүй
    r = post(c, f"payments/{d['payment_id']}/confirm",
             {"transaction_id": "MP-2", "amount": d["amount"], "payer_reg_no": "7777777"})
    assert r.status_code == 200 and r.json()["payer_reg_no"] == "6235971"


def test_existing_pending_intent_updates_buyer(c):
    s = _session(c)
    d1 = post(c, "payments", {"session_id": s.id}).json()
    d2 = post(c, "payments", {"session_id": s.id, "payer_reg_no": "6235971"}).json()
    assert d2["payment_id"] == d1["payment_id"] and d2["receipt_type"] == "ORGANIZATION"
    assert c.db.query(M.AuditLog).filter_by(action="WALLET_EBARIMT_RECEIVER").count() == 1


def test_payer_lookup(c, monkeypatch):
    async def fake_lookup(reg):
        return {"available": True, "found": True, "name": "Ийзи Паркинг ХХК", "tin": "12345678901",
                "is_vat_payer": True}
    from app.services import tin_lookup
    monkeypatch.setattr(tin_lookup, "lookup", fake_lookup)
    r = c.client.get("/api/v1/ebarimt/payer", params={"reg_no": "6235971"}, headers={"X-API-Key": KEY})
    assert r.status_code == 200 and r.json()["name"] == "Ийзи Паркинг ХХК"
    assert r.json()["receipt_type"] == "ORGANIZATION"
    r = c.client.get("/api/v1/ebarimt/payer", params={"reg_no": "АА00112233"}, headers={"X-API-Key": KEY})
    assert r.status_code == 422


def test_after_payment_change_only_while_not_issued(c):
    s = _session(c)
    d = post(c, "payments", {"session_id": s.id}).json()
    assert post(c, f"payments/{d['payment_id']}/confirm",
                {"transaction_id": "MP-3", "amount": d["amount"]}).status_code == 200
    p = c.db.get(M.Payment, d["payment_id"])
    jobs = c.db.query(M.FinancialJob).filter_by(payment_id=p.id, kind="RECEIPT").all()
    recs = c.db.query(M.VatReceipt).filter_by(payment_id=p.id).all()
    if all(FJ.never_sent(r, [j for j in jobs if j.receipt_id == r.id]) for r in recs):
        r = post(c, f"payments/{p.id}/ebarimt", {"payer_reg_no": "6235971"})
        assert r.status_code == 200, r.text
        assert r.json()["receipt_type"] == "ORGANIZATION"
        c.db.expire_all()
        assert all(j.payload.get("receiver_type") == "COMPANY"
                   for j in c.db.query(M.FinancialJob).filter_by(payment_id=p.id, kind="RECEIPT"))
    # Баримт ГАРСАН мэт болгоно → 409, юу ч өөрчлөхгүй
    for rec in c.db.query(M.VatReceipt).filter_by(payment_id=p.id):
        rec.status, rec.ebarimt_id = "SENT", "0301" + "0" * 29
    for j in c.db.query(M.FinancialJob).filter_by(payment_id=p.id, kind="RECEIPT"):
        j.status, j.attempts = "DONE", 1
    c.db.commit()
    r = post(c, f"payments/{p.id}/ebarimt", {"payer_reg_no": "7777777"})
    assert r.status_code == 409 and "RECEIPT_ALREADY_ISSUED" in r.json()["detail"]
    r = post(c, f"payments/{p.id}/ebarimt", {"payer_reg_no": "bad"})
    assert r.status_code == 422
