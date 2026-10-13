"""POS терминалын давхардлын ЦУЦЛАЛТ: msgbill түлхүүрийн уналт, нэг төлбөрийн цуцлалт,
«Дахин үүсгэх»-ийн 409, арын job, серверийн CLI. msgbill-ийн HTTP давхаргыг хуурамчаар
сольдог — гадагш НЭГ Ч хүсэлт явахгүй. DB нь санах ойн SQLite.

Яагаад (2026-10-13): 08-24-нд Хангарьдын касс дээр Ontime POS баримт хэвлэсэн 63
төлбөрт манай msgbill баримт давхар үүссэн. Бөөн цуцлалт нэг хүсэлтэд 60+ HTTP
дуудаж nginx 60с-т 504 өгдөг, Моннисын 08-20…09-06-ны баримт ГЛОБАЛ түлхүүрээр
гарсан тул одоогийн (түрээслэгчийн) түлхүүрээр «олдсонгүй» гэдэг, цуцалсан
төлбөрт «Дахин үүсгэх» дарвал давхардал чимээгүй буцаж үүсдэг байв.
"""
import asyncio
import importlib.util
import io
import os
from datetime import datetime, timedelta
from decimal import Decimal

import openpyxl
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import models as M
from app.config import settings
from app.database import Base
from app.routers import payments_router as P, reports_router as R
from app.services import financial_jobs as J, msgbill, vat_duplicates as VD
from app.services.receipts import DUPLICATE_OF_POS, is_duplicate_cancel

MSG_DDTD = "029100244106001097260066610045952"
BASE = datetime(2026, 8, 24, 2, 24, 3)


# ───────────────────────── хуурамч msgbill HTTP ─────────────────────────────

class FakeResp:
    def __init__(self, status, j=None):
        self.status_code, self._j = status, j
        self.content = b"x" if j is not None else b""
        self.text = str(j)

    def json(self):
        return self._j


class FakeHTTP:
    """POST …/cancel, GET …/receipts/{id}. `script` = [(status, json)…] дарааллаар;
    `calls` = (method, url, X-Api-Key)."""
    script: list = []
    calls: list = []

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None, headers=None):
        FakeHTTP.calls.append(("POST", url, (headers or {}).get("X-Api-Key")))
        return FakeResp(*FakeHTTP.script.pop(0))

    async def get(self, url, headers=None):
        FakeHTTP.calls.append(("GET", url, (headers or {}).get("X-Api-Key")))
        return FakeResp(*FakeHTTP.script.pop(0))


@pytest.fixture(autouse=True)
def fake_http(monkeypatch):
    monkeypatch.setattr(msgbill.httpx, "AsyncClient", FakeHTTP)
    monkeypatch.setattr(settings, "msgbill_api_key", "bsk_live_GLOBAL")
    monkeypatch.setattr(settings, "msgbill_base_url", "https://msgbill.mn/api/v1")
    monkeypatch.setattr(settings, "ebarimt_mock_receipts", False)
    monkeypatch.setattr(settings, "secret_enc_key", "")
    # Бодит цуцлалтын хамгаалалт (server_problems): ажиллаж буй «parking» DB + прод сервер.
    # Тестийн engine нь санах ойн SQLite — энэ URL-аар холбогдохгүй, зөвхөн нэрийг нь шалгана
    monkeypatch.setattr(settings, "database_url", "postgresql+psycopg2://u:p@localhost/parking")
    monkeypatch.setattr(settings, "public_base_url", "https://easy-parking.mn")
    monkeypatch.delenv("PARKING_DEPLOY_ROLE", raising=False)
    msgbill.invalidate_cache()
    FakeHTTP.script, FakeHTTP.calls = [], []
    yield
    msgbill.invalidate_cache()


@pytest.fixture
def engine():
    eng = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def db(engine):
    with Session(engine, autoflush=False) as s:
        yield s


def _setup(db, *, tenant_key="bsk_live_TENANT", n=1, method="CASH", provider="CASH",
           rprov="MSGBILL", ddtd=MSG_DDTD, own_qpay=False):
    ten = M.Tenant(name="ИйзиПаркинг", code="EASY", msgbill_api_key=tenant_key)
    db.add(ten)
    db.flush()
    site = M.ParkingSite(name="Хангарьд", site_code="HANGARID", tenant_id=ten.id,
                         qpay_username="u" if own_qpay else None, qpay_password="p" if own_qpay else None)
    db.add(site)
    db.flush()
    pays = []
    for i in range(n):
        p = M.Payment(site_id=site.id, provider=provider, payment_method=method, amount=Decimal("1000"),
                      vat_amount=Decimal("90.91"), status="PAID", sender_invoice_no=f"inv-{i}",
                      paid_at=BASE + timedelta(minutes=i), raw_payload={"plate_number": f"{1000 + i}УБА"})
        db.add(p)
        db.flush()
        db.add(M.VatReceipt(payment_id=p.id, ebarimt_id=ddtd[:-12] + "%04d" % i + ddtd[-8:],
                            amount=Decimal("1000"), vat_amount=Decimal("90.91"), status="SENT",
                            provider=rprov, provider_ref=f"rcp-{i}"))
        pays.append(p)
    db.commit()
    return site, pays


def _recs(db, pid):
    db.expire_all()
    return db.query(M.VatReceipt).filter_by(payment_id=pid).all()


# ───────────────────────── (e) түлхүүрийн уналт ─────────────────────────────

def test_cancel_falls_back_to_other_key_on_not_found(db):
    """Одоогийн (түрээслэгчийн) түлхүүрээр «олдсонгүй» → глобал түлхүүрээр НЭГ удаа."""
    site, _ = _setup(db)
    FakeHTTP.script = [(404, {"code": "RECEIPT_NOT_FOUND", "message_mn": "Баримт олдсонгүй"}),
                       (200, {"id": "rcp-0", "state": "CANCELLED"})]
    out = asyncio.run(msgbill.cancel_receipt_any(site, "rcp-0", "давхардал"))
    assert out["state"] == "CANCELLED" and out["key_scope"] == "global" and out["key_fallback"]
    assert [c[2] for c in FakeHTTP.calls] == ["bsk_live_TENANT", "bsk_live_GLOBAL"]


def test_cancel_falls_back_on_401_but_not_on_server_error_or_missing_endpoint(db):
    site, _ = _setup(db)
    FakeHTTP.script = [(401, {"code": "UNAUTHORIZED"}), (200, {"state": "CANCELLED"})]
    assert asyncio.run(msgbill.cancel_receipt_any(site, "rcp-0"))["key_scope"] == "global"
    FakeHTTP.calls, FakeHTTP.script = [], [(500, {"message": "дотоод алдаа"})]
    with pytest.raises(msgbill.MsgbillError):
        asyncio.run(msgbill.cancel_receipt_any(site, "rcp-0"))
    assert len(FakeHTTP.calls) == 1          # 5xx = түлхүүрийн асуудал биш — нөгөөгөөр оролдохгүй
    FakeHTTP.calls, FakeHTTP.script = [], [(404, {"message": "Cannot POST /partner/receipts/x/cancel"})]
    with pytest.raises(msgbill.MsgbillError) as e:
        asyncio.run(msgbill.cancel_receipt_any(site, "rcp-0"))
    assert e.value.code == "NOT_SUPPORTED" and len(FakeHTTP.calls) == 1


def test_single_key_no_second_attempt_and_own_qpay_site_uses_global_for_cancel(db, monkeypatch):
    """Өөрийн QPay данстай, түрээслэгчийн түлхүүргүй зогсоол (Моннис 09-06-аас өмнө):
    шинэ баримтад глобал түлхүүр ХЭРЭГЛЭХГҮЙ ч, тэр түлхүүрээр гарсан ХУУЧИН баримтыг цуцлана."""
    site, _ = _setup(db, tenant_key=None, own_qpay=True)
    assert not msgbill.api_key_for(site).enabled
    assert [a.scope for a in msgbill.cancel_accounts(site)] == ["global"]
    FakeHTTP.script = [(404, {"code": "RECEIPT_NOT_FOUND"})]
    with pytest.raises(msgbill.MsgbillError):
        asyncio.run(msgbill.cancel_receipt_any(site, "rcp-0"))
    assert len(FakeHTTP.calls) == 1
    monkeypatch.setattr(settings, "msgbill_api_key", "")
    msgbill.invalidate_cache()
    with pytest.raises(msgbill.MsgbillError) as e:
        asyncio.run(msgbill.cancel_receipt_any(site, "rcp-0"))
    assert e.value.code == "NOT_CONFIGURED"


# ───────────────────────── нэг төлбөрийн цуцлалт ─────────────────────────────

def test_cancel_one_marks_duplicate_audits_and_is_idempotent(db):
    site, (pay,) = _setup(db)
    FakeHTTP.script = [(404, {"code": "RECEIPT_NOT_FOUND"}), (200, {"state": "CANCELLED"})]
    res = asyncio.run(VD.cancel_one(db, pay.id, note="ТЕГ 08-24", username="tester",
                                    teg={"teg_ddtd": "015200020090000097260000110002990",
                                         "teg_register": "10002990"}))
    assert res["ok"] and res["key_scopes"] == {_recs(db, pay.id)[0].ebarimt_id: "global"}
    rec = _recs(db, pay.id)[0]
    assert rec.status == "CANCELLED" and is_duplicate_cancel(rec)
    assert rec.ddtd_note.startswith(DUPLICATE_OF_POS) and "10002990" in rec.ddtd_note
    assert rec.raw["cancel"]["key_scope"] == "global" and rec.raw["cancel"]["by"] == "tester"
    a = db.query(M.AuditLog).filter_by(action="EBARIMT_CANCEL_BULK").one()
    assert a.entity_id == pay.id and a.detail["reason"] == DUPLICATE_OF_POS
    # Дахин ажиллуулахад HTTP хийхгүй, алгасна
    FakeHTTP.calls = []
    res2 = asyncio.run(VD.cancel_one(db, pay.id, note="x", username="tester"))
    assert res2["ok"] and res2["skipped"] == "already_cancelled" and FakeHTTP.calls == []


def test_cancel_one_refuses_qr_and_mock_without_http(db):
    _, (qr,) = _setup(db, method="QR", provider="QPAY", rprov="QPAY",
                      ddtd="030101065006000097260000410014863")
    res = asyncio.run(VD.cancel_one(db, qr.id, note="x", username="t"))
    assert not res["ok"] and "QR" in res["error"]
    assert FakeHTTP.calls == [] and _recs(db, qr.id)[0].status == "SENT"


def test_cancel_one_refuses_legacy_mock_receipt(engine):
    with Session(engine, autoflush=False) as db:
        _, (p,) = _setup(db, rprov=None, ddtd="9" * 40)
        res = asyncio.run(VD.cancel_one(db, p.id, note="x", username="t"))
        assert not res["ok"] and "msgbill биш" in res["error"] and FakeHTTP.calls == []


def test_cancel_pending_then_resolved_by_get(db):
    _, (pay,) = _setup(db)
    FakeHTTP.script = [(200, {"state": "CANCEL_PENDING", "error": "ТЕГ түр"})]
    res = asyncio.run(VD.cancel_one(db, pay.id, note="x", username="t"))
    assert res.get("pending") and not res["ok"]
    rec = _recs(db, pay.id)[0]
    assert rec.status == "CANCEL_PENDING" and is_duplicate_cancel(rec)
    FakeHTTP.calls, FakeHTTP.script = [], [(200, {"state": "CANCELLED"})]
    res = asyncio.run(VD.cancel_one(db, pay.id, note="x", username="t"))
    assert res["ok"] and [c[0] for c in FakeHTTP.calls] == ["GET"]      # дахин cancel биш
    assert _recs(db, pay.id)[0].status == "CANCELLED"


# ───────────────────────── (f) «Дахин үүсгэх» 409 ─────────────────────────────

def test_retry_refused_for_duplicate_cancel_unless_override(db):
    _, (pay,) = _setup(db)
    FakeHTTP.script = [(200, {"state": "CANCELLED"})]
    # ТЕГ ДДТД өгөөгүй → терминалын мөр ДДТД-гүй EXTERNAL (нэгтгэл: record_terminal_receipt)
    out = asyncio.run(VD.cancel_one(db, pay.id, note="x", username="t"))
    assert out["terminal_receipt"]["status"] == "EXTERNAL"
    res = J.retry_payment_receipts(db, pay)
    assert res["duplicate_of_pos"] and "POS терминал" in res["error"]
    assert sorted(r.status for r in _recs(db, pay.id)) == ["CANCELLED", "EXTERNAL"]   # шинэ msgbill алга
    user = M.User(username="fin", password_hash="x", role="SUPER_ADMIN")
    db.add(user)
    db.commit()
    with pytest.raises(HTTPException) as e:                 # энгийн retry — терминалынх
        asyncio.run(P.retry_ebarimt_endpoint(pay.id, {}, db=db, user=user))
    assert e.value.status_code == 409 and "POS терминал" in e.value.detail
    db.rollback()
    with pytest.raises(HTTPException) as e:                 # «Систем баримт гаргах» ч давхардал
        asyncio.run(P.retry_ebarimt_endpoint(pay.id, {"reissue_external": True}, db=db, user=user))
    assert e.value.status_code == 409 and "ДАВХАРДСАН" in e.value.detail
    assert db.query(M.AuditLog).filter_by(action="EBARIMT_RETRY_REFUSED").count() == 1
    db.rollback()
    res = asyncio.run(P.retry_ebarimt_endpoint(pay.id, {"reissue_external": True,
                                                        "allow_duplicate": True}, db=db, user=user))
    assert res.get("pending")                               # ил тод зөвшөөрөлтэй л
    assert sorted(r.status for r in _recs(db, pay.id)) == ["CANCELLED", "PENDING"]


def test_ordinary_cancel_still_allows_retry(db):
    """Энгийн «Цуцлах» (буруу ТТД г.м) — давхардлын тэмдэггүй тул дахин үүсгэж болно."""
    _, (pay,) = _setup(db)
    FakeHTTP.script = [(200, {"state": "CANCELLED"})]
    res = asyncio.run(P.cancel_ebarimt(db, P._lock_payment(db, pay.id), "буруу ТТД"))
    assert res["ok"] and not is_duplicate_cancel(_recs(db, pay.id)[0])
    assert J.retry_payment_receipts(db, pay).get("pending")


# ───────────────────────── (d) арын job ─────────────────────────────

def _user(db, role="SUPER_ADMIN"):
    u = db.query(M.User).filter_by(username=f"u-{role}").first()
    if u is None:
        u = M.User(username=f"u-{role}", password_hash="x", role=role)
        db.add(u)
        db.commit()
    return u


async def _run_job(db, ids, note="ТЕГ 08-24"):
    r = await R.cancel_duplicate_receipts({"payment_ids": ids, "dry_run": False, "note": note},
                                          db=db, user=_user(db))
    assert r["started"]
    for _ in range(500):
        if not R._dup_job.get("running"):
            break
        await asyncio.sleep(0.01)
    return R.cancel_duplicates_status(user=None)


@pytest.fixture
def job_env(engine, monkeypatch):
    import app.database as D
    monkeypatch.setattr(D, "SessionLocal", sessionmaker(bind=engine, autoflush=False))
    monkeypatch.setattr(R, "_DUP_DELAY", 0)
    R._dup_job.clear()
    yield
    R._dup_job.clear()


def test_job_cancels_skips_and_counts(db, job_env):
    _, pays = _setup(db, n=3)
    FakeHTTP.script = [(200, {"state": "CANCELLED"}), (200, {"state": "CANCEL_PENDING"})]
    ids = [p.id for p in pays[:2]]
    st = asyncio.run(_run_job(db, ids))
    assert (st["done"], st["ok"], st["pending"], st["failed"], st["running"]) == (2, 1, 1, 0, False)
    # Дахин ажиллуулахад цуцлагдсан нь алгасагдана, pending нь GET-ээр эцэслэгдэнэ
    FakeHTTP.script = [(200, {"state": "CANCELLED"}), (200, {"state": "CANCELLED"})]
    st = asyncio.run(_run_job(db, ids + [pays[2].id]))
    assert (st["skipped"], st["ok"], st["failed"]) == (1, 2, 0)
    summ = db.query(M.AuditLog).filter_by(action="EBARIMT_CANCEL_BULK_SUMMARY").count()
    assert summ == 2 and db.query(M.AuditLog).filter_by(action="EBARIMT_CANCEL_BULK").count() == 4


def test_job_stops_after_repeated_auth_errors(db, job_env):
    _, pays = _setup(db, n=5)
    FakeHTTP.script = [(401, {"code": "UNAUTHORIZED"})] * 20
    st = asyncio.run(_run_job(db, [p.id for p in pays]))
    assert st["done"] == 3 and st["failed"] == 3 and "3 удаа" in st["stopped"]
    assert all(r.status == "SENT" for p in pays for r in _recs(db, p.id))


def test_job_stops_on_quota(db, job_env):
    _, pays = _setup(db, n=3)
    FakeHTTP.script = [(429, {"code": "RECEIPT_QUOTA_EXCEEDED"})] * 4
    st = asyncio.run(_run_job(db, [p.id for p in pays]))
    assert st["done"] == 1 and "квот" in st["stopped"]


def test_single_job_at_a_time_and_dry_run_is_synchronous(db, job_env):
    _, pays = _setup(db, n=2)
    R._dup_job.update({"running": True})
    with pytest.raises(HTTPException) as e:
        asyncio.run(R.cancel_duplicate_receipts({"payment_ids": [pays[0].id], "dry_run": False,
                                                 "note": "x"}, db=db, user=_user(db)))
    assert e.value.status_code == 409
    R._dup_job.clear()
    out = asyncio.run(R.cancel_duplicate_receipts({"payment_ids": [p.id for p in pays]},
                                                  db=db, user=_user(db, "ADMIN")))
    assert out["dry_run"] and out["ok"] == 2 and FakeHTTP.calls == []
    assert out["items"][0]["will_cancel"][0]["provider"] == "MSGBILL"


def test_apply_refused_when_msgbill_is_mocked(db, job_env, monkeypatch):
    _, pays = _setup(db)
    monkeypatch.setattr(settings, "ebarimt_mock_receipts", True)
    with pytest.raises(HTTPException) as e:
        asyncio.run(R.cancel_duplicate_receipts({"payment_ids": [pays[0].id], "dry_run": False,
                                                 "note": "x"}, db=db, user=_user(db)))
    assert e.value.status_code == 400 and "MOCK" in e.value.detail and not R._dup_job


# ───────────────────────── серверийн CLI ─────────────────────────────

def _cli():
    path = os.path.join(os.path.dirname(__file__), "..", "..", "tools", "vat_cancel_duplicates.py")
    spec = importlib.util.spec_from_file_location("vat_cancel_duplicates", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _teg_file(tmp_path, pays, extra_qr=None):
    """Продын бүтэцтэй ТЕГ экспорт: Ontime мөр (UTC, төлбөрөөс 4с хойно) + манай
    msgbill мөр (УБ локал, 1с хойно) төлбөр бүрд."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Пос дугаар", "ДДТД", "Огноо", "Нийт дүн", "НХАТ", "НӨАТ", "Цэвэр дүн", "Х/А регистр",
               "Х/А нэр", "Хаанаас", "НӨАТ төлөгч эсэх", "Пос дугаар", "Систем нийлүүлэгч", "Байршлын алба"])
    for i, p in enumerate(pays + (extra_qr or [])):
        ws.append(["7524322", "0152000200900000972600%03d10002990" % i,
                   (p.paid_at + timedelta(seconds=4)).strftime("%Y-%m-%d %H:%M:%S") + ".0", 1000, "",
                   90.91, 909.09, "", "", "POS", "", "10002990", "Ontime", "Баянзүрх"])
        ws.append(["8164522", "0152000200900010972600%03d10045952" % i,
                   (p.paid_at + timedelta(hours=8, seconds=1)).strftime("%Y-%m-%d %H:%M:%S") + ".0", 1000,
                   "", 90.91, 909.09, "", "", "POS", "", "001", "Онлайм нетворк", "Хан-Уул"])
    path = tmp_path / "teg.xlsx"
    wb.save(path)
    return str(path)


def test_cli_dry_run_reports_and_writes_nothing(db, engine, tmp_path, monkeypatch, capsys):
    cli = _cli()
    monkeypatch.setattr(cli, "SessionLocal", sessionmaker(bind=engine, autoflush=False))
    _, pays = _setup(db, n=3)
    f = _teg_file(tmp_path, pays)
    out = tmp_path / "rep.csv"
    assert cli.main(["--file", f, "--out", str(out)]) == 0
    text = capsys.readouterr().out
    assert "DRY-RUN" in text and "3 төлбөр" in text and "3,000₮" in text
    rows = out.read_text(encoding="utf-8-sig").splitlines()
    assert len(rows) == 4 and rows[1].count("10002990") >= 1
    assert (tmp_path / "rep_unmatched.csv").exists()
    assert db.query(M.AuditLog).count() == 0 and FakeHTTP.calls == []
    assert all(r.status == "SENT" for p in pays for r in _recs(db, p.id))


def test_cli_apply_guards_and_resumes(db, engine, tmp_path, monkeypatch, capsys):
    cli = _cli()
    monkeypatch.setattr(cli, "SessionLocal", sessionmaker(bind=engine, autoflush=False))
    _, pays = _setup(db, n=3)
    f = _teg_file(tmp_path, pays)
    out = str(tmp_path / "rep.csv")
    base = ["--file", f, "--out", out, "--apply", "--yes", "--delay", "0", "--user", "temuujin",
            "--tenant", "EASY"]
    assert cli.main(base + ["--max", "2"]) == 2                    # --max-аас олон → зогсоно
    monkeypatch.setattr(settings, "ebarimt_mock_receipts", True)
    assert cli.main(base) == 3                                     # MOCK горим → зогсоно
    monkeypatch.setattr(settings, "ebarimt_mock_receipts", False)
    assert FakeHTTP.calls == []
    FakeHTTP.script = [(200, {"state": "CANCELLED"})]
    assert cli.main(base + ["--limit", "1"]) == 0                  # эхний 1-ийг л
    assert len(FakeHTTP.calls) == 1
    FakeHTTP.script = [(200, {"state": "CANCELLED"})] * 2
    assert cli.main(base) == 0                                     # дахин: 1 нь алгасагдана
    assert len(FakeHTTP.calls) == 3
    text = capsys.readouterr().out
    assert "CANCELLED" in text
    # Манай msgbill баримт бүгд цуцлагдаж тэмдэглэгдсэн; ТЕГ-ийн Ontime ДДТД-ээр терминалын
    # (ONTIME/SENT) мөр бүртгэгдсэн (нэгтгэл: vat_duplicates.cancel_one → record_terminal_receipt)
    for p in pays:
        rs = _recs(db, p.id)
        assert [r.status for r in rs if r.provider == "MSGBILL"] == ["CANCELLED"]
        assert all(is_duplicate_cancel(r) for r in rs if r.provider == "MSGBILL")
        [t] = [r for r in rs if r.provider != "MSGBILL"]
        assert (t.provider, t.status) == ("ONTIME", "SENT") and t.ebarimt_id.endswith("10002990")
    assert db.query(M.AuditLog).filter_by(action="EBARIMT_CANCEL_BULK", username="temuujin").count() == 3
    assert (tmp_path / "rep_applied.csv").exists()
    # Бүгд цуцлагдсаны дараа жагсаалт хоосон — дахин юу ч хийхгүй
    FakeHTTP.calls = []
    assert cli.main(base) == 0 and FakeHTTP.calls == []


def test_cli_never_touches_qr_payment(db, engine, tmp_path, monkeypatch):
    """QR төлбөрт Ontime-тай ижил цаг/дүнтэй ТЕГ мөр байсан ч жагсаалтад орохгүй."""
    cli = _cli()
    monkeypatch.setattr(cli, "SessionLocal", sessionmaker(bind=engine, autoflush=False))
    _, pays = _setup(db, n=1)
    qr = M.Payment(site_id=pays[0].site_id, provider="QPAY", payment_method="QR", amount=Decimal("1000"),
                   vat_amount=Decimal("90.91"), status="PAID", sender_invoice_no="inv-qr",
                   paid_at=BASE + timedelta(hours=1), raw_payload={})
    db.add(qr)
    db.flush()
    db.add(M.VatReceipt(payment_id=qr.id, ebarimt_id="030101065006000097260000410014863",
                        amount=Decimal("1000"), vat_amount=Decimal("90.91"), status="SENT", provider="QPAY"))
    db.commit()
    f = _teg_file(tmp_path, pays, extra_qr=[qr])
    out = tmp_path / "rep.csv"
    assert cli.main(["--file", f, "--out", str(out)]) == 0
    body = out.read_text(encoding="utf-8-sig")
    assert pays[0].id in body and qr.id not in body


# ───────────────────────── 2026-10-13 хяналтын засвар ─────────────────────────

@pytest.mark.parametrize("db_url,base_url,role,why", [
    ("postgresql+psycopg2://u:p@localhost/audit1013", "https://easy-parking.mn", None, "ХУУЛБАР"),
    ("postgresql+psycopg2://u:p@localhost/parking", "https://test.easy-parking.mn", None, "ТЕСТ сервер"),
    ("postgresql+psycopg2://u:p@localhost/parking", "https://easy-parking.mn", "staging", "staging"),
], ids=["продын-хуулбар", "тест-сервер", "staging"])
def test_apply_refused_on_prod_copy_or_test_server(db, job_env, engine, tmp_path, monkeypatch, capsys,
                                                   db_url, base_url, role, why):
    """TEST сервер дээр audit1013 (продын жинхэнэ msgbill түлхүүртэй хуулбар) руу
    --apply хийхэд юу ч саадгүй байв — продын баримт цуцлагдах эрсдэл."""
    _, pays = _setup(db)
    monkeypatch.setattr(settings, "database_url", db_url)
    monkeypatch.setattr(settings, "public_base_url", base_url)
    if role:
        monkeypatch.setenv("PARKING_DEPLOY_ROLE", role)
    with pytest.raises(HTTPException) as e:
        asyncio.run(R.cancel_duplicate_receipts({"payment_ids": [pays[0].id], "dry_run": False,
                                                 "note": "x"}, db=db, user=_user(db)))
    assert e.value.status_code == 400 and why in e.value.detail and not R._dup_job
    cli = _cli()
    monkeypatch.setattr(cli, "SessionLocal", sessionmaker(bind=engine, autoflush=False))
    f = _teg_file(tmp_path, pays)
    assert cli.main(["--file", f, "--out", str(tmp_path / "r.csv"), "--tenant", "EASY",
                     "--apply", "--yes", "--delay", "0"]) == 3
    assert why in capsys.readouterr().out and FakeHTTP.calls == []
    assert _recs(db, pays[0].id)[0].status == "SENT"


def test_explicit_production_role_allows_other_db_name(monkeypatch):
    monkeypatch.setattr(settings, "database_url", "postgresql+psycopg2://u:p@localhost/parking_prod")
    assert VD.server_problems()
    monkeypatch.setenv("PARKING_DEPLOY_ROLE", "production")
    assert VD.server_problems() == []


def test_cli_apply_requires_tenant(db, engine, tmp_path, monkeypatch):
    cli = _cli()
    monkeypatch.setattr(cli, "SessionLocal", sessionmaker(bind=engine, autoflush=False))
    _, pays = _setup(db)
    with pytest.raises(SystemExit) as e:
        cli.main(["--file", _teg_file(tmp_path, pays), "--apply", "--yes"])
    assert "--tenant" in str(e.value) and FakeHTTP.calls == []


def test_cli_rerun_same_file_after_limit_1_does_not_cancel_legit_payment(db, engine, tmp_path, monkeypatch,
                                                                       capsys):
    """Хяналтын ГОЛ олдвор (CLI-ээр): P1 (бэлэн, Ontime давхардалтай) + 26с хойно ижил
    дүнтэй хууль ёсны ШИЛЖҮҮЛЭГ P2 (зөвхөн манай msgbill). --limit 1 → P1 цуцлагдана;
    ИЖИЛ файлаар дахин ажиллуулахад P2 хэзээ ч цуцлагдах ёсгүй."""
    cli = _cli()
    monkeypatch.setattr(cli, "SessionLocal", sessionmaker(bind=engine, autoflush=False))
    _, (p1,) = _setup(db)
    p2 = M.Payment(site_id=p1.site_id, provider="BANK", payment_method="TRANSFER", amount=Decimal("1000"),
                   vat_amount=Decimal("90.91"), status="PAID", sender_invoice_no="inv-tr",
                   paid_at=p1.paid_at + timedelta(seconds=26), raw_payload={"plate_number": "2222УБА"})
    db.add(p2)
    db.flush()
    msg2 = MSG_DDTD[:-12] + "0999" + MSG_DDTD[-8:]
    db.add(M.VatReceipt(payment_id=p2.id, ebarimt_id=msg2, amount=Decimal("1000"), vat_amount=Decimal("90.91"),
                        status="SENT", provider="MSGBILL", provider_ref="rcp-tr"))
    db.commit()
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Пос дугаар", "ДДТД", "Огноо", "Нийт дүн", "Систем нийлүүлэгч"])
    ws.append(["7524322", "015200020090000097260000110002990",          # Ontime (UTC, P1-ээс 4с өмнө)
               (p1.paid_at - timedelta(seconds=4)).strftime("%Y-%m-%d %H:%M:%S"), 1000, "Ontime"])
    for p, rid in ((p1, _recs(db, p1.id)[0].ebarimt_id), (p2, msg2)):  # манай msgbill (УБ локал)
        ws.append(["8164522", rid[:-11] + "1" + rid[-10:],
                   (p.paid_at + timedelta(hours=8, seconds=1)).strftime("%Y-%m-%d %H:%M:%S"), 1000,
                   "Онлайм нетворк"])
    f = str(tmp_path / "teg.xlsx")
    wb.save(f)
    base = ["--file", f, "--out", str(tmp_path / "r.csv"), "--tenant", "EASY", "--apply", "--yes",
            "--delay", "0"]
    FakeHTTP.script = [(200, {"state": "CANCELLED"})]
    assert cli.main(base + ["--limit", "1"]) == 0
    assert [c[1].rsplit("/", 2)[-2] for c in FakeHTTP.calls] == ["rcp-0"]
    FakeHTTP.script = [(200, {"state": "CANCELLED"})] * 3
    assert cli.main(base) == 0
    assert len(FakeHTTP.calls) == 1                                  # P2-т ХҮРЭЭГҮЙ
    assert _recs(db, p2.id)[0].status == "SENT"
    assert cli.main(["--file", f, "--out", str(tmp_path / "r2.csv"), "--tenant", "EASY"]) == 0
    text = capsys.readouterr().out
    assert "DUPLICATE_CANCELLED" in text and "ЦУЦЛАГДСАН баримтын мөр файлд 1" in text


def test_retry_guard_with_failed_sibling_row_and_bulk_job_skips(db, job_env):
    """(f) хамгаалалт өмнө нь БҮХ баримт CANCELLED үед л ажилладаг байв — цуцалсан msgbill +
    ДДТД-гүй FAILED мөртэй төлбөрийг бөөн нөхөлт (vat-retry-failed) дахин илгээнэ."""
    _, (pay,) = _setup(db)
    FakeHTTP.script = [(200, {"state": "CANCELLED"})]
    asyncio.run(VD.cancel_one(db, pay.id, note="x", username="t"))
    db.add(M.VatReceipt(payment_id=pay.id, amount=Decimal("1000"), vat_amount=Decimal("90.91"),
                        status="FAILED", provider="MSGBILL", receipt_url="quota"))
    db.commit()
    res = J.retry_payment_receipts(db, pay)
    assert res.get("duplicate_of_pos")
    assert J.retry_payment_receipts(db, pay, allow_duplicate=True).get("duplicate_of_pos") is None
    db.rollback()
    R._bulk_job.clear()
    R._bulk_job.update({"running": True, "total": 1, "done": 0, "ok": 0, "skipped": 0, "failed": 0,
                        "stopped": None, "errors": {}, "username": "fin"})
    try:
        asyncio.run(R._run_bulk_retry([pay.id]))
        assert (R._bulk_job["skipped"], R._bulk_job["failed"], R._bulk_job["ok"]) == (1, 0, 0)
        assert R._bulk_job["skipped_duplicate_of_pos"] == 1
    finally:
        R._bulk_job.clear()
    assert db.query(M.FinancialJob).count() == 0                       # юу ч дараалалд ороогүй


def test_job_status_details_only_for_starter_or_global_user(db, job_env):
    site, pays = _setup(db)
    FakeHTTP.script = [(500, {"code": "X"})] * 4
    asyncio.run(_run_job(db, [pays[0].id]))
    assert R.cancel_duplicates_status(user=_user(db))["failures"]          # эхлүүлсэн
    other = M.User(username="tenant-admin", password_hash="x", role="ADMIN", site_ids=[site.id])
    db.add(other)
    db.commit()
    st = R.cancel_duplicates_status(user=other)
    assert st["failures"] == [] and st["top_errors"] == [] and "note" not in st and st["failed"] == 1


@pytest.mark.parametrize("rprov", ["QPAY", None], ids=["QPAY", "POSAPI"])
def test_cancel_ebarimt_qpay_and_posapi_branches(db, monkeypatch, rprov):
    """cancel_ebarimt-ийн QPAY/POSAPI замыг хамгаална — тэднийг шалгадаг байсан
    tests/test_ebarimt_cancel.py өмнөөсөө (хуучирсан fake) унадаг. msgbill руу
    НЭГ Ч дуудлага явахгүй, msgbill-ийн давхардлын тэмдэг тавигдахгүй."""
    from app.services import ebarimt, qpay
    seen = {}

    async def _qcancel(pid, note="", acc=None):
        seen["qpay"] = (pid, note)
        return True

    async def _pdel(bill, date):
        seen["posapi"] = (bill, date)
        return True
    monkeypatch.setattr(qpay, "cancel_ebarimt", _qcancel)
    monkeypatch.setattr(ebarimt, "delete_receipt", _pdel)
    _, (pay,) = _setup(db, method="QR" if rprov else "CASH", provider="QPAY" if rprov else "CASH",
                       rprov=rprov, ddtd="030101065006000097260000410014863")
    pay.provider_payment_id = "GP1"
    db.commit()
    res = asyncio.run(P.cancel_ebarimt(db, P._lock_payment(db, pay.id), "буруу ТТД",
                                       mark={"reason": DUPLICATE_OF_POS}))
    rec = _recs(db, pay.id)[0]
    assert res["ok"] and rec.status == "CANCELLED" and rec.receipt_url == "буруу ТТД"
    if rprov == "QPAY":
        assert seen == {"qpay": ("GP1", "буруу ТТД")}
    else:
        assert list(seen) == ["posapi"] and seen["posapi"][0] == rec.ebarimt_id
    # Давхардлын тэмдэг зөвхөн msgbill-д; QPay/PosAPI-д цуцлалтын нотолгоо л (raw.cancel)
    assert FakeHTTP.calls == [] and not is_duplicate_cancel(rec)
    assert rec.raw["cancel"]["state"] == "CANCELLED" and "reason" not in rec.raw["cancel"]


# ───────────── нэгтгэл (2026-10-13): терминалын мөр «манай баримт» биш ─────────────

def test_pilot_with_recorded_ontime_row_keeps_remaining_duplicates(db, engine, tmp_path, monkeypatch,
                                                                  capsys):
    """Pilot `--limit 1` цуцлалт терминалын баримтыг ONTIME/SENT (ДДТД …10002990) мөрөөр
    бүртгэдэг болсон. Тэр мөр тулгалтын «манайх»-т орвол Ontime касс «манай касс» болж,
    үлдсэн давхардал илрэхгүй, ONTIME мөр нь «msgbill биш» гэж андуурагдана. Дахин
    ажиллуулахад үлдсэн 2 нь цуцлагдаж, ONTIME мөр ХЭЗЭЭ Ч хөндөгдөхгүй."""
    cli = _cli()
    monkeypatch.setattr(cli, "SessionLocal", sessionmaker(bind=engine, autoflush=False))
    site, pays = _setup(db, n=3)
    f = _teg_file(tmp_path, pays)
    base = ["--file", f, "--out", str(tmp_path / "r.csv"), "--tenant", "EASY", "--delay", "0",
            "--yes", "--user", "temuujin"]
    FakeHTTP.script = [(200, {"state": "CANCELLED"})]
    assert cli.main(base + ["--apply", "--limit", "1"]) == 0
    db.expire_all()
    ontime = [r for r in db.query(M.VatReceipt).all() if r.provider == "ONTIME"]
    assert len(ontime) == 1 and ontime[0].status == "SENT" and ontime[0].ebarimt_id.endswith("10002990")
    # Dry-run: Ontime касс өөр систем хэвээр, 2 давхардал + 1 цуцлагдсан
    from app.routers import vat_recon as VRM
    tax, _ = VRM.parse_tax_export("teg.xlsx", open(f, "rb").read())
    a = VD.reconcile_db(db, tax, shifts=[0.0, -8.0], tol=60, ours_sites=[site.id],
                        scope_ids={site.id})
    assert "10002990" in a["r"]["foreign_registers"]
    verdicts = sorted(x["verdict"] for x in a["explained"])
    assert verdicts == ["DUPLICATE", "DUPLICATE", "DUPLICATE_CANCELLED"]
    assert {d["payment_id"] for d in a["duplicates"]} == {pays[1].id, pays[2].id}
    assert a["terminal"]["missing"] == []
    capsys.readouterr()
    FakeHTTP.calls = []
    FakeHTTP.script = [(200, {"state": "CANCELLED"})] * 2
    assert cli.main(base + ["--apply"]) == 0
    assert len(FakeHTTP.calls) == 2 and all("rcp-0" not in c[1] for c in FakeHTTP.calls)
    db.expire_all()
    for p in pays:
        st = sorted((r.provider, r.status) for r in _recs(db, p.id))
        assert st == [("MSGBILL", "CANCELLED"), ("ONTIME", "SENT")]
    # Ижил файлаар дахин — юу ч цуцлахгүй, ONTIME мөр хэвээр
    FakeHTTP.calls = []
    assert cli.main(base + ["--apply"]) == 0 and FakeHTTP.calls == []
    assert [r.status for r in db.query(M.VatReceipt).filter_by(provider="ONTIME")] == ["SENT"] * 3


def test_cancel_one_never_touches_terminal_row_even_if_active(db):
    """ONTIME/SENT мөр + идэвхтэй msgbill (ж: гараар дахин гаргасан) — цуцлалт зөвхөн
    msgbill-ийг (only_ids); терминалын мөр SENT хэвээр, давхар бүртгэгдэхгүй."""
    _, (pay,) = _setup(db)
    db.add(M.VatReceipt(payment_id=pay.id, ebarimt_id="015200020090000097260000110002990",
                        amount=Decimal("1000"), vat_amount=Decimal("90.91"), status="SENT",
                        provider="ONTIME"))
    db.commit()
    FakeHTTP.script = [(200, {"state": "CANCELLED"})]
    out = asyncio.run(VD.cancel_one(db, pay.id, note="x", username="t",
                                    teg={"teg_ddtd": "015200020090000097260000110002990"}))
    assert out["ok"] and len(FakeHTTP.calls) == 1
    st = sorted((r.provider, r.status) for r in _recs(db, pay.id))
    assert st == [("MSGBILL", "CANCELLED"), ("ONTIME", "SENT")]       # шинэ мөр нэмэгдээгүй


def test_already_cancelled_without_terminal_row_is_completed_on_rerun(db):
    """Цуцлалт амжилттай болсны дараа терминалын мөр бүртгэгдэлгүй тасарсан (түгжээ) —
    дахин ажиллуулахад msgbill-ийг ДАХИН цуцлахгүй, зөвхөн терминалын мөрийг нөхнө."""
    _, (pay,) = _setup(db)
    rec = _recs(db, pay.id)[0]
    rec.status = "CANCELLED"
    rec.ddtd_note = f"{DUPLICATE_OF_POS}: өмнө цуцалсан"
    db.commit()
    out = asyncio.run(VD.cancel_one(db, pay.id, note="x", username="t",
                                    teg={"teg_ddtd": "015200020090000097260000110002990"}))
    assert out["skipped"] == "already_cancelled" and FakeHTTP.calls == []
    assert out["terminal_receipt"]["provider"] == "ONTIME"
    out = asyncio.run(VD.cancel_one(db, pay.id, note="x", username="t", teg={}))
    assert out["skipped"] == "already_cancelled" and "terminal_receipt" not in out
    assert len(_recs(db, pay.id)) == 2
