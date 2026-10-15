"""Түншээс үл хамаарах машин бүртгэл/bill/байршлын зам (MonPay г.м, 2026-10-15).

`/api/v1/vehicles/register|delete|bill`, `GET|POST /api/v1/parkings` нь
`/api/v1/socialpay/*`-ийн ЯГ ИЖИЛ handler — хариуны хэлбэр, эрх, хурдны хязгаар,
бүртгэлийн хүснэгт нэг. SocialPay-ийн замууд өөрчлөгдөөгүй (бүртгэлийн created_via
«socialpay» хэвээр; энд «api»). Мөн MonPay-д баримтжуулсан мэдэгдлийн гэрээ:
2xx + {"success": true} (жижиг үсгээр ч) = хүргэгдсэн, хоосон 2xx = давтана.

Зөвхөн in-memory SQLite + локал хуурамч HTTP сервер — гадагш огт хандахгүй.
"""
import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app import models as M
from app.config import settings
from app.routers import socialpay_router as SP
from app.services import partner_vehicles as PV
from test_socialpay_vehicles import (KEY, KEY_RO, NOW, ctx, events, factory, fake,  # noqa: F401
                                     stay, sub)

BODY = {"plateNo": "7886УКР", "userId": "100264"}


@pytest.fixture
def nctx(ctx):
    """ctx + түншээс үл хамаарах router (main.py-тэй ижил дараалал: error handler аль хэдийн суусан)."""
    ctx.client.app.include_router(SP.neutral_router)
    return ctx


def npost(c, path, body, key=KEY):
    headers = {"X-API-Key": key} if key else {}
    return c.client.post(f"/api/v1/{path}", json=body, headers=headers)


def sp_post(c, path, body, key=KEY):
    headers = {"X-API-Key": key} if key else {}
    return c.client.post(f"/api/v1/socialpay/{path}", json=body, headers=headers)


def test_register_delete_same_contract_and_shared_registry(nctx):
    r = npost(nctx, "vehicles/register", BODY)
    assert r.status_code == 200 and r.json() == {"Success": True, "Message": "OK"}
    assert npost(nctx, "vehicles/register", {"plateNo": " 7886 укр ", "userId": 100264}).json() == {
        "Success": True, "Message": "ALREADY_REGISTERED"}
    # Нэг бүртгэл — SocialPay-ийн замаар ч «аль хэдийн бүртгэлтэй»
    assert sp_post(nctx, "register", BODY).json()["Message"] == "ALREADY_REGISTERED"
    (row,) = nctx.db.query(M.PartnerVehicle).all()
    assert row.created_via == "api" and row.plate_number == "7886УКР" and row.external_user_id == "100264"
    audit = nctx.db.query(M.AuditLog).filter_by(action="PARTNER_VEHICLE_REGISTER").one()
    assert audit.username == "partner:socialpay" and audit.detail["via"] == "api"

    assert npost(nctx, "vehicles/delete", BODY).json() == {"Success": True, "Message": "OK"}
    assert npost(nctx, "vehicles/delete", BODY).json() == {"Success": True, "Message": "NOT_FOUND"}
    assert sp_post(nctx, "delete", BODY).json() == {"Success": True, "Message": "NOT_FOUND"}
    nctx.db.expire_all()
    assert nctx.db.query(M.PartnerVehicle).one().deleted_at is not None


def test_socialpay_path_still_records_socialpay(nctx):
    assert sp_post(nctx, "register", BODY).json() == {"Success": True, "Message": "OK"}
    assert nctx.db.query(M.PartnerVehicle).one().created_via == "socialpay"


def test_bill_identical_to_socialpay_and_private(nctx):
    sub(nctx, user="A")
    entered = datetime.utcnow().replace(microsecond=375000) - timedelta(minutes=40)
    s = stay(nctx, entry=entered)
    stay(nctx, site=nctx.child, entry=entered + timedelta(minutes=5))     # доторх — харагдахгүй
    a = npost(nctx, "vehicles/bill", {"plateNo": "7886УКР", "userId": "A"})
    b = sp_post(nctx, "bill", {"plateNo": "7886УКР", "userId": "A"})
    assert a.status_code == b.status_code == 200, a.text
    da, db_ = a.json()["data"], b.json()["data"]
    assert a.json()["Success"] is True and a.json()["Message"] == "OK"
    assert set(da) == set(db_)
    for k in ("sessionId", "siteCode", "status", "enteredDate", "freeMinutes", "freeUntil",
              "totalFee", "amountDue", "debtAmount", "payableOnline", "currency", "paymentId"):
        assert da[k] == db_[k], k
    assert da["sessionId"] == s.id and da["siteCode"] == "OUT" and da["amountDue"] == 1000.0
    # Өөр хэрэглэгч / бүртгэлгүй — байршил задрахгүй
    r = npost(nctx, "vehicles/bill", {"plateNo": "7886УКР", "userId": "B"})
    assert r.status_code == 404 and r.json()["Message"] == "NOT_REGISTERED" and "data" not in r.json()


def test_bill_no_active_session(nctx):
    sub(nctx, user="A")
    assert npost(nctx, "vehicles/bill", {"plateNo": "7886УКР", "userId": "A"}).json() == {
        "Success": True, "Message": "NO_ACTIVE_SESSION", "data": None}


def test_auth_scope_and_error_shapes_on_neutral_paths(nctx, monkeypatch):
    for key, code, msg in ((None, 401, "UNAUTHORIZED"), ("pk_wrong", 401, "UNAUTHORIZED"),
                           (KEY_RO, 403, "FORBIDDEN")):
        for path in ("vehicles/register", "vehicles/delete", "vehicles/bill"):
            r = npost(nctx, path, BODY, key=key)
            assert r.status_code == code, (path, r.text)
            assert r.json()["Success"] is False and r.json()["Message"] == msg and "detail" not in r.json()
    r = nctx.client.post("/api/v1/vehicles/register", content=b"not json",
                         headers={"X-API-Key": KEY, "Content-Type": "application/json"})
    assert r.status_code == 400 and r.json()["Message"] == "INVALID_JSON"
    r = npost(nctx, "vehicles/register", {"plateNo": "7886UKR", "userId": "1"})   # латин
    assert r.status_code == 400 and r.json()["Message"] == "INVALID_PLATE"
    r = nctx.client.get("/api/v1/vehicles/register", headers={"X-API-Key": KEY})
    assert r.status_code == 405 and r.json()["Message"] == "METHOD_NOT_ALLOWED" and r.json()["Success"] is False
    r = npost(nctx, "vehicles/nope", {})
    assert r.status_code == 404 and r.json()["Message"] == "NOT_FOUND" and "Detail" in r.json()
    r = nctx.client.put("/api/v1/parkings", headers={"X-API-Key": KEY})
    assert r.status_code == 405 and r.json()["Success"] is False
    # Төлбөрийн API болон төстэй нэртэй зам FastAPI-ийн анхдагч хэлбэрээ хадгална
    other = nctx.client.get("/api/v1/vehiclesx", headers={"X-API-Key": KEY})
    assert other.status_code == 404 and other.json() == {"detail": "Not Found"}
    other = nctx.client.get("/api/v1/sessions", params={"plate": "7886УКР"})
    assert other.status_code == 401 and "detail" in other.json()
    other = nctx.client.post("/api/v1/payments/nope/confirm", json={"transaction_id": "T1"},
                             headers={"X-API-Key": KEY})
    assert other.status_code == 404 and "detail" in other.json()

    def boom(*a, **k):
        raise RuntimeError("synthetic")
    monkeypatch.setattr(PV, "register_vehicle", boom)
    r = npost(nctx, "vehicles/register", BODY)
    assert r.status_code == 500 and r.json()["Message"] == "INTERNAL_ERROR"
    assert nctx.db.query(M.PartnerVehicle).count() == 0


def test_rate_limit_budget_shared_between_paths(nctx, monkeypatch):
    monkeypatch.setattr(settings, "partner_vehicle_rate_per_min", 2)
    assert npost(nctx, "vehicles/register", BODY).status_code == 200
    assert sp_post(nctx, "register", BODY).status_code == 200
    r = npost(nctx, "vehicles/bill", BODY)
    assert r.status_code == 429 and r.json()["Message"] == "RATE_LIMITED"


def test_parkings_alias_outer_sites_read_only_key(nctx):
    ref = nctx.client.get("/api/v1/socialpay/parkings", headers={"X-API-Key": KEY_RO}).json()
    for method in ("get", "post"):
        r = getattr(nctx.client, method)("/api/v1/parkings", headers={"X-API-Key": KEY_RO})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["Success"] is True and body["Message"] == "OK" and body["sampledAt"].endswith("Z")
        assert body["data"] == ref["data"]
        codes = [p["siteCode"] for p in body["data"]]
        assert "IN" not in codes and {"OUT", "OTH"} <= set(codes)
        out = next(p for p in body["data"] if p["siteCode"] == "OUT")
        assert out["latitude"] == 47.9 and out["freeMinutes"] == 15 and out["locationAvailable"] is True
    assert nctx.client.get("/api/v1/parkings").json()["Message"] == "UNAUTHORIZED"


def test_main_registers_neutral_router_before_error_handlers():
    # app.main-ийг import хийхгүй (startup migration ажилладаг) — эх кодоор шалгана
    src = (Path(__file__).resolve().parents[2] / "app" / "main.py").read_text(encoding="utf-8")
    inc = src.index("app.include_router(socialpay_router.neutral_router)")
    assert inc < src.index("socialpay_router.install_error_handlers(app)")


# ───── MonPay-д баримтжуулсан мэдэгдлийн гэрээ (extended формат) ─────

def _enqueue(c, minutes=10):
    sub(c)
    s = stay(c, entry=datetime.utcnow() - timedelta(minutes=minutes))
    PV.scan_once(c.db)
    return s


def test_extended_entry_payload_and_lowercase_success_reply(nctx, fake):
    nctx.key.vehicle_webhook_format = "extended"; nctx.db.commit()
    fake.replies = [(200, {"success": True})]
    s = _enqueue(nctx)
    assert asyncio.run(PV.deliver_due(factory(nctx))) == 1
    (req,) = fake.received
    assert req["headers"]["X-Parking-Event"] == "vehicle.entry"
    assert req["headers"]["Idempotency-Key"] == req["headers"]["X-Parking-Event-Id"]
    data = json.loads(req["raw"].decode("utf-8"))["data"]
    assert data["event"] == "ENTRY" and data["sessionId"] == s.id and data["plateNo"] == "7886УКР"
    assert data["siteCode"] == "OUT" and data["siteName"] == "Outer" and data["freeMinutes"] == 15
    assert data["enteredDate"] == PV.iso_z(s.entry_time) and data["freeUntil"].endswith("Z")
    nctx.db.expire_all()
    assert events(nctx)[0].status == "DONE"


@pytest.mark.parametrize("reply", [(200, b""), (204, b""), (200, {"ok": True}),
                                   (200, {"success": False}), (500, {"success": True})])
def test_reply_without_success_true_is_retried(nctx, fake, reply):
    fake.replies = [reply]
    _enqueue(nctx)
    assert asyncio.run(PV.deliver_due(factory(nctx))) == 1
    nctx.db.expire_all()
    ev = events(nctx)[0]
    assert ev.status == "RETRY" and ev.attempts == 1
    assert ev.next_attempt_at >= datetime.utcnow() + timedelta(seconds=50)   # 1 минутын дараа
