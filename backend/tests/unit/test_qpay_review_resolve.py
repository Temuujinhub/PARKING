"""QPay e-Barimt REVIEW нөхөлт (2026-10-15) — `services/qpay_review` + `tools/qpay_review_resolve.py`.

ebarimt-diag-ийн create хэсэг нэг функц болсон (endpoint-ийн хариу ижил), CLI нь ЯГ тэр функцийг
дуудна. QPay клиент бүрэн stub (create_ebarimt/check_payment), httpx-ийн сүлжээ хориотой.
DB нь санах ойн SQLite.
"""
import asyncio
import importlib.util
import os
from datetime import datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import models as M
from app.config import settings
from app.database import Base
from app.services import financial_jobs as FJ, qpay, qpay_review as QR

T0 = datetime(2026, 10, 8, 3, 0, 21)          # UTC (УБ 11:00:21) — 10-08-ны тасалдал
QPY = "10014863"


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setattr(settings, "database_url", "postgresql+psycopg2://u:p@localhost/parking")
    monkeypatch.setattr(settings, "public_base_url", "https://site.easy-parking.mn")
    monkeypatch.setattr(settings, "qpay_mock", False)
    monkeypatch.setattr(settings, "qpay_sandbox", False)
    monkeypatch.setattr(settings, "qpay_username", "EASY_2PARKING")
    monkeypatch.setattr(settings, "qpay_password", "secret")
    monkeypatch.setattr(settings, "qpay_invoice_code", "EB_EASY_2PARKING_INVOICE")
    monkeypatch.setattr(settings, "qpay_ebarimt", True)
    monkeypatch.setattr(settings, "secret_enc_key", "")
    monkeypatch.delenv("PARKING_DEPLOY_ROLE", raising=False)

    async def no_net(*a, **k):
        raise AssertionError("Network forbidden")
    monkeypatch.setattr(httpx.AsyncClient, "send", no_net)


class FakeQpay:
    """qpay.create_ebarimt-ийн stub: дуудлагыг тэмдэглэж, урьдчилан тавьсан хариу/алдааг буцаана."""

    def __init__(self, monkeypatch):
        self.calls, self.n, self.fail = [], 0, None
        monkeypatch.setattr(qpay, "create_ebarimt", self.create)

    async def create(self, payment_id, receiver_type="CITIZEN", receiver=None, district_code=None, acc=None):
        self.calls.append({"payment_id": payment_id, "receiver_type": receiver_type,
                           "receiver": receiver, "acc": acc.username if acc else None})
        if self.fail is not None:
            raise self.fail
        self.n += 1
        bill = "0301010650060000977700%03d%s" % (self.n, QPY)
        return {"status": "SUCCESS", "billId": bill, "id": f"eb-{self.n}", "lottery": f"TN {self.n:08d}",
                "qrData": "QR", "raw": {"id": f"eb-{self.n}", "ebarimt_receipt_id": bill,
                                        "barimt_status": "REGISTERED"}}


def http_error(status=400, body=None):
    req = httpx.Request("POST", "https://merchant.qpay.mn/v2/ebarimt_v3/create")
    resp = httpx.Response(status, json=body if body is not None else {"message": "SYSTEM_BUSY"}, request=req)
    return httpx.HTTPStatusError(f"HTTP {status}", request=req, response=resp)


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


class W:
    n = 0


def world(db):
    w = W()
    w.ep = M.Tenant(name="ИйзиПаркинг ХХК", code="EASYPARKING")
    w.mon = M.Tenant(name="Моннис пропэрти", code="MONNIS")
    db.add_all([w.ep, w.mon])
    db.flush()
    w.site = M.ParkingSite(name="Спорт", site_code="SPORT", tenant_id=w.ep.id)
    w.site2 = M.ParkingSite(name="3-р эмнэлэг", site_code="3HOSPITAL", tenant_id=w.ep.id)
    w.mon_site = M.ParkingSite(name="Моннис", site_code="MONNIS", tenant_id=w.mon.id)
    db.add_all([w.site, w.site2, w.mon_site])
    db.flush()
    return w


def ddtd(seq, prefix="015200020090"):
    return f"{prefix}0000976{seq:06d}{QPY}"


def pay(db, w, at, amount, *, site=None, rstatus="REVIEW", jstatus="UNKNOWN", provider="QPAY",
        pstatus="PAID", tin=None, rtype=None, ebarimt_id=None, qpay_id=True, attempts=1):
    """QR төлбөр + нэг баримт + (jstatus бол) outbox-ын ажил (HTTPStatusError-оор UNKNOWN)."""
    W.n += 1
    site = site or w.site
    p = M.Payment(site_id=site.id, provider="QPAY", payment_method="QR", source="QR",
                  amount=Decimal(amount), vat_amount=Decimal("0"), status=pstatus,
                  sender_invoice_no=f"inv{W.n}", paid_at=at, customer_tin=tin,
                  ebarimt_receiver_type=rtype or ("COMPANY" if tin else "CITIZEN"),
                  provider_invoice_id=f"qinv-{W.n}",
                  provider_payment_id=f"78163691352{W.n:04d}" if qpay_id else None,
                  raw_payload={"plate_number": f"{W.n:04d}УБА"})
    db.add(p)
    db.flush()
    r = M.VatReceipt(payment_id=p.id, amount=p.amount, vat_amount=p.vat_amount, provider=provider,
                     status=rstatus, customer_tin=tin, ebarimt_id=ebarimt_id,
                     receipt_url="HTTPStatusError" if rstatus in ("REVIEW", "FAILED") else None)
    db.add(r)
    db.flush()
    if jstatus:
        _acc, fp = FJ._account("QPAY", site)
        db.add(M.FinancialJob(job_key=f"receipt:{r.id}", kind="RECEIPT", payment_id=p.id, receipt_id=r.id,
                              status=jstatus, attempts=attempts,
                              last_error="HTTPStatusError" if jstatus == "UNKNOWN" else "BLOCKED: тохиргоо",
                              payload={"provider": provider, "account_fingerprint": fp,
                                       "receiver_type": p.ebarimt_receiver_type, "customer_tin": tin,
                                       "provider_payment_id": p.provider_payment_id}))
    db.commit()
    return p, r


def _tool():
    path = os.path.join(os.path.dirname(__file__), "..", "..", "tools", "qpay_review_resolve.py")
    spec = importlib.util.spec_from_file_location("qpay_review_resolve", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _xlsx(path, rows):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Пос дугаар", "ДДТД", "Огноо", "Нийт дүн", "Систем нийлүүлэгч"])
    for d, dt, amount in rows:
        ws.append(["1", d, dt.strftime("%Y-%m-%d %H:%M:%S"), amount, "QPayPos"])
    wb.save(path)
    return path


def _rec(db, rid):
    db.expire_all()
    return db.get(M.VatReceipt, rid)


def _job(db, rid):
    db.expire_all()
    return db.query(M.FinancialJob).filter_by(receipt_id=rid).one_or_none()


def _actions(db, action):
    db.expire_all()
    return db.query(M.AuditLog).filter_by(action=action).all()


# ───────────────────────── сонголт ─────────────────────────

def test_selection_filters(db):
    w = world(db)
    ok, r_ok = pay(db, w, T0, 1000)
    sep, _ = pay(db, w, datetime(2026, 9, 23, 15, 0), 2000)                       # 9-р сар (УБ 09-23)
    edge, r_edge = pay(db, w, datetime(2026, 9, 30, 16, 30), 1500)               # УБ 10-01 00:30
    _sent, _ = pay(db, w, T0, 1000, rstatus="SENT", jstatus="DONE", ebarimt_id=ddtd(1, "030101065006"))
    _msg, _ = pay(db, w, T0, 1000, provider="MSGBILL")                            # msgbill-ийг ХӨНДӨХГҮЙ
    _pend, _ = pay(db, w, T0, 1000, pstatus="PENDING")
    noid, _ = pay(db, w, T0, 1000, qpay_id=False)
    _blk, _ = pay(db, w, T0, 1000, rstatus="FAILED", jstatus="BLOCKED", attempts=0)
    legacy, r_legacy = pay(db, w, T0, 4000, rstatus="FAILED", jstatus=None)      # хуучин мөр (ажилгүй)
    mon, _ = pay(db, w, T0, 5000, site=w.mon_site)
    other, r_other = pay(db, w, T0, 1000)
    db.add(M.VatReceipt(payment_id=other.id, amount=other.amount, vat_amount=0, provider="QPAY",
                        status="SENT", ebarimt_id=ddtd(9, "030101065006")))
    db.commit()
    lo, hi = QR.ub_window("2026-10-01", "2026-10-31")
    ep_sites = [w.site.id, w.site2.id]
    sel = QR.select_candidates(db, site_ids=ep_sites, lo=lo, hi=hi)
    assert [c["payment_id"] for c in sel["candidates"]] == [edge.id, ok.id]
    assert sel["skipped"] == {"NO_QPAY_ID": 1, "HAS_RECEIPT": 1}
    c = sel["candidates"][1]
    assert (c["paid_at_ub"], c["site_code"], c["tenant"], c["receipt_status"], c["job_status"],
            c["job_attempts"], c["last_error"]) == ("2026-10-08 11:00:21", "SPORT", "EASYPARKING",
                                                     "REVIEW", "UNKNOWN", 1, "HTTPStatusError")
    # --include-failed: хуучин FAILED (ажилгүй) орно, тохиргоогоор хаагдсан (BLOCKED) — үгүй
    sel = QR.select_candidates(db, site_ids=ep_sites, lo=lo, hi=hi, include_failed=True)
    assert legacy.id in {c["payment_id"] for c in sel["candidates"]}
    assert sel["skipped"]["JOB_BLOCKED"] == 1
    # огноогүй → 9-р сар ч; зогсоол/түрээслэгчээр
    sel = QR.select_candidates(db, site_ids=ep_sites)
    assert sep.id in {c["payment_id"] for c in sel["candidates"]}
    assert mon.id not in {c["payment_id"] for c in sel["candidates"]}
    sel = QR.select_candidates(db, site_ids=[w.mon_site.id], lo=lo, hi=hi)
    assert [c["payment_id"] for c in sel["candidates"]] == [mon.id]
    sel = QR.select_candidates(db, site_ids=ep_sites, lo=lo, hi=hi, payment_ids=[ok.id])
    assert [c["payment_id"] for c in sel["candidates"]] == [ok.id]


# ───────────────────────── endpoint = нэг код зам ─────────────────────────

def test_ebarimt_diag_endpoint_uses_shared_create_with_same_shape(db, monkeypatch):
    from unittest.mock import AsyncMock

    from app.routers import admin_router as AR
    w = world(db)
    p, r = pay(db, w, T0, 2000)
    fake = FakeQpay(monkeypatch)
    monkeypatch.setattr(qpay, "check_payment", AsyncMock(return_value={"raw": {"count": 1}}))
    admin = M.User(username="temuujin", password_hash="x", role="SUPER_ADMIN")
    out = asyncio.run(AR.qpay_ebarimt_diag({"payment_id": p.id, "create": True}, db, admin))
    cr = out["create_result"]
    assert set(cr) == {"ok", "raw", "elapsed_ms", "receipt_updated"} and cr["ok"] and cr["receipt_updated"]
    rec = _rec(db, r.id)
    assert rec.status == "SENT" and rec.ebarimt_id.endswith(QPY) and rec.receipt_url is None
    assert rec.lottery_code == "TN 00000001" and "qpay-diag" in rec.raw
    assert fake.calls == [{"payment_id": p.provider_payment_id, "receiver_type": "CITIZEN",
                           "receiver": None, "acc": "EASY_2PARKING"}]
    assert [a.detail for a in _actions(db, "QPAY_EBARIMT_DIAG")] == [{"create": True, "result": True}]
    assert _job(db, r.id).status == "UNKNOWN"          # endpoint ажилд хүрдэггүй (хуучнаараа)
    # HTTP алдаа: хариуны бүтэц хуучнаараа, баримт REVIEW хэвээр
    p2, r2 = pay(db, w, T0, 1000)
    fake.fail = http_error(400, {"message": "SYSTEM_BUSY"})
    out = asyncio.run(AR.qpay_ebarimt_diag({"payment_id": p2.id, "create": True}, db, admin))
    cr = out["create_result"]
    assert set(cr) == {"ok", "http_status", "raw_body", "elapsed_ms"}
    assert cr["ok"] is False and cr["http_status"] == 400 and cr["raw_body"] == {"message": "SYSTEM_BUSY"}
    assert _rec(db, r2.id).status == "REVIEW"


# ───────────────────────── resolve_one ─────────────────────────

def _cand(db, p_id):
    sel = QR.select_candidates(db, site_ids=None, payment_ids=[p_id])
    assert len(sel["candidates"]) == 1, sel["skipped"]
    return sel["candidates"][0]


def test_resolve_success_assigns_like_endpoint_and_closes_job(db, monkeypatch):
    w = world(db)
    p, r = pay(db, w, T0, 5000)
    fake = FakeQpay(monkeypatch)
    res = asyncio.run(QR.resolve_one(db, _cand(db, p.id), action="CREATE", username="temuujin"))
    assert res["outcome"] == "SENT" and res["ebarimt_id"].endswith(QPY)
    rec = _rec(db, r.id)
    assert (rec.status, rec.receipt_url, rec.lottery_code) == ("SENT", None, "TN 00000001")
    assert QR.TOOL_SOURCE in rec.raw and rec.customer_tin is None
    assert _job(db, r.id).status == "DONE"
    a = _actions(db, QR.AUDIT_ACTION)
    assert len(a) == 1 and a[0].entity_id == p.id and a[0].detail["ok"] is True
    assert a[0].detail["ebarimt_id"] == rec.ebarimt_id and a[0].username == "temuujin"
    assert len(_actions(db, "EBARIMT_ID_SET")) == 1
    # Дахин: SENT болсон тул сонгогдохгүй, QPay ДАХИН дуудагдахгүй
    assert QR.select_candidates(db, site_ids=None)["candidates"] == []
    assert len(fake.calls) == 1


def test_error_leaves_review_and_records_error(db, monkeypatch):
    w = world(db)
    p, r = pay(db, w, T0, 1000)
    fake = FakeQpay(monkeypatch)
    fake.fail = http_error(400, {"message": "'30101065006' ТТД бүхий ААН-н түрээслэгчийн жагсаалтанд бүртгэлгүй"})
    res = asyncio.run(QR.resolve_one(db, _cand(db, p.id), action="CREATE", username="t"))
    assert res["outcome"] == "FAILED" and res["http_status"] == 400 and "бүртгэлгүй" in res["error"]
    rec = _rec(db, r.id)
    assert (rec.status, rec.ebarimt_id, rec.receipt_url) == ("REVIEW", None, "HTTPStatusError")
    assert _job(db, r.id).status == "UNKNOWN"
    a = _actions(db, QR.AUDIT_ACTION)
    assert len(a) == 1 and a[0].detail["ok"] is False and a[0].detail["http_status"] == 400
    # Сүлжээний (HTTP биш) алдаа ч REVIEW хэвээр
    fake.fail = httpx.ReadTimeout("timeout")
    res = asyncio.run(QR.resolve_one(db, _cand(db, p.id), action="CREATE", username="t"))
    assert res["outcome"] == "FAILED" and "ReadTimeout" in res["error"]
    assert _rec(db, r.id).status == "REVIEW"


def test_company_tin_passthrough_unchanged(db, monkeypatch):
    w = world(db)
    p, r = pay(db, w, T0, 14000, tin="6283393")                  # ААН-ы 7 оронтой регистр
    p2, r2 = pay(db, w, T0 + timedelta(minutes=1), 1000)          # иргэн
    fake = FakeQpay(monkeypatch)
    for pid in (p.id, p2.id):
        assert asyncio.run(QR.resolve_one(db, _cand(db, pid), action="CREATE", username="t"))["outcome"] == "SENT"
    assert [(c["receiver_type"], c["receiver"]) for c in fake.calls] == [("COMPANY", "6283393"), ("CITIZEN", None)]
    rec = _rec(db, r.id)
    assert rec.lottery_code is None and rec.customer_tin == "6283393"     # ААН-д сугалаагүй
    db.expire_all()
    assert db.get(M.Payment, p.id).customer_tin == "6283393" and db.get(M.Payment, p.id).ebarimt_receiver_type == "COMPANY"
    assert _rec(db, r2.id).lottery_code == "TN 00000002"


def test_already_resolved_or_raced_never_calls_qpay(db, monkeypatch):
    w = world(db)
    p, r = pay(db, w, T0, 1000)
    fake = FakeQpay(monkeypatch)
    cand = _cand(db, p.id)
    # Сонголтын ДАРАА өөр зам (retry/баримт холбох) SENT болгосон — түгжээний дотор дахин шалгана
    rec = db.get(M.VatReceipt, r.id)
    rec.status, rec.ebarimt_id = "SENT", ddtd(5, "030101065006")
    db.commit()
    res = asyncio.run(QR.resolve_one(db, cand, action="CREATE", username="t"))
    assert res == {**res, "outcome": "SKIPPED", "reason": "HAS_DDTD"} and fake.calls == []
    # Өөр мөрөнд идэвхтэй баримт бий → HAS_RECEIPT
    p2, r2 = pay(db, w, T0, 1000)
    cand2 = _cand(db, p2.id)
    db.add(M.VatReceipt(payment_id=p2.id, amount=p2.amount, vat_amount=0, provider="POS-TERMINAL",
                        status="EXTERNAL"))
    db.commit()
    assert asyncio.run(QR.resolve_one(db, cand2, action="CREATE", username="t"))["reason"] == "HAS_RECEIPT"
    # Өөр төлбөр ИЖИЛ QPay payment_id-тэй (нэг QPay гүйлгээ давхар бүртгэгдсэн) → сонгохгүй, түгжээнд ч алгасна
    p4, _r4 = pay(db, w, T0, 1000)
    cand4 = _cand(db, p4.id)
    twin, _ = pay(db, w, T0, 1000, rstatus="SENT", jstatus="DONE", ebarimt_id=ddtd(6, "030101065006"))
    twin.provider_payment_id = p4.provider_payment_id
    db.commit()
    assert QR.select_candidates(db, site_ids=None, payment_ids=[p4.id])["skipped"] == {"QPAY_ID_SHARED": 1}
    assert asyncio.run(QR.resolve_one(db, cand4, action="CREATE", username="t"))["reason"] == "QPAY_ID_SHARED"
    # Дансны тохиргоо өөрчлөгдсөн (outbox-ын fingerprint) → алгасна
    p3, r3 = pay(db, w, T0, 1000)
    cand3 = _cand(db, p3.id)
    j = db.query(M.FinancialJob).filter_by(receipt_id=r3.id).one()
    j.payload = {**j.payload, "account_fingerprint": "other"}
    db.commit()
    assert asyncio.run(QR.resolve_one(db, cand3, action="CREATE", username="t"))["reason"] == "ACCOUNT_CHANGED"
    assert fake.calls == [] and _rec(db, r3.id).status == "REVIEW"


# ───────────────────────── ТЕГ нотолгоо ─────────────────────────

def teg_world(db, w):
    """EP: 8 SENT (ТЕГ мөр +2с, T0−1ц…T0+1ц) · A: REVIEW 3000 — ТЕГ-т эзэнгүй мөр +10с (баримт БИЙ) ·
    B: REVIEW 1500 — мөр алга · C: REVIEW 2000 — ойролцоо эзэнгүй мөр бий, гэвч тэр нь SENT S-ийн
    90с зөрсөн мөр (өдрийн тоо тэнцүү) · D: файлын хүрээнээс өмнө · M: Моннис (файл EP-ийнх)."""
    s, rows = {}, []
    for i, m in enumerate((-60, 0, 2, 4, 6, 8, 10, 60)):         # файл T0−1ц … T0+1ц-ийг хамарна
        p, _r = pay(db, w, T0 + timedelta(minutes=m), 1000, rstatus="SENT", jstatus="DONE",
                    ebarimt_id=ddtd(100 + i, "030101065006"))
        rows.append((ddtd(100 + i), p.paid_at + timedelta(seconds=2), 1000))
    s["A"] = pay(db, w, T0 + timedelta(minutes=3), 3000, site=w.site2)
    rows.append((ddtd(200), T0 + timedelta(minutes=3, seconds=10), 3000))
    s["B"] = pay(db, w, T0 + timedelta(minutes=4), 1500)
    s["C"] = pay(db, w, T0 + timedelta(minutes=5), 2000)
    sp, _ = pay(db, w, T0 + timedelta(minutes=5, seconds=30), 2000, rstatus="SENT", jstatus="DONE",
                ebarimt_id=ddtd(300, "030101065006"))
    rows.append((ddtd(301), sp.paid_at + timedelta(seconds=90), 2000))
    s["D"] = pay(db, w, T0 - timedelta(hours=3), 1000)
    s["M"] = pay(db, w, T0 + timedelta(minutes=6), 5000, site=w.mon_site)
    return s, rows


def test_teg_evidence_verdicts(db):
    w = world(db)
    s, rows = teg_world(db, w)
    tax = [{"ddtd": d, "dt": dt, "amount": a, "src": "QPayPos"} for d, dt, a in rows]
    tax.append({"ddtd": "015200020090000097600000110045952", "dt": T0, "amount": 999.0, "src": "Онлайм"})
    cands = QR.select_candidates(db, site_ids=None)["candidates"]
    ev = QR.teg_evidence(db, [("teg.xlsx", tax)], cands)
    assert ev["files"][0]["usable"] and ev["files"][0]["tenant_id"] == w.ep.id
    v = {pid: ev["verdicts"][r.id] for pid, (p, r) in ((k, s[k]) for k in s)}
    assert v["A"]["teg"] == "IN_TEG" and v["A"]["teg_ddtd"] == ddtd(200) and v["A"]["surplus"] == 1
    assert v["B"]["teg"] == "NOT_IN_TEG"
    assert v["C"]["teg"] == "AMBIGUOUS" and v["C"]["near"] == 1 and v["C"]["surplus"] == 0
    assert v["D"]["teg"] == "OUTSIDE" and v["M"]["teg"] == "NOT_COVERED"
    assert [QR.plan_action(v[k]["teg"]) for k in "ABCDM"] == ["ATTACH_TEG", "CREATE", "SKIP", "SKIP", "SKIP"]
    assert QR.plan_action("AMBIGUOUS", include_ambiguous=True) == "CREATE"
    # Өөр түрээслэгчийн (манай баримттай таардаггүй) файл — нотолгоо БИШ
    alien = [{"ddtd": ddtd(900 + i), "dt": T0 + timedelta(minutes=i, seconds=7), "amount": 7777.0, "src": "Q"}
             for i in range(5)]
    ev2 = QR.teg_evidence(db, [("other.xlsx", alien)], cands)
    assert not ev2["files"][0]["usable"] and {x["teg"] for x in ev2["verdicts"].values()} == {"NOT_COVERED"}


# ───────────────────────── CLI ─────────────────────────

def test_cli_dry_run_is_read_only_and_offline(db, engine, tmp_path, monkeypatch, capsys):
    cli = _tool()
    monkeypatch.setattr(cli, "SessionLocal", sessionmaker(bind=engine, autoflush=False))
    w = world(db)
    s, rows = teg_world(db, w)
    fake = FakeQpay(monkeypatch)
    f = _xlsx(str(tmp_path / "teg.xlsx"), rows)
    before = sorted((r.id, r.status, r.ebarimt_id) for r in db.query(M.VatReceipt).all())
    assert cli.main(["--from", "2026-10-01", "--tenant", "EASYPARKING", "--file", f,
                     "--out", str(tmp_path / "r.csv")]) == 0
    out = capsys.readouterr().out
    assert "DRY-RUN — юу ч өөрчлөгдсөнгүй, QPay руу хандсангүй" in out
    assert "СОНГОГДСОН: 4 төлбөр · 7,500₮" in out                   # A, B, C, D (Моннис — өөр түрээслэгч)
    assert "НӨХӨХ: 2 төлбөр · 4,500₮ (ATTACH_TEG 1 · CREATE 1)" in out
    assert "IN_TEG" in out and "AMBIGUOUS" in out and "OUTSIDE" in out
    assert fake.calls == []
    db.expire_all()
    assert sorted((r.id, r.status, r.ebarimt_id) for r in db.query(M.VatReceipt).all()) == before
    assert db.query(M.AuditLog).count() == 0
    assert s["A"][0].id in (tmp_path / "r.csv").read_text(encoding="utf-8-sig")


def test_cli_guards_refuse(db, engine, tmp_path, monkeypatch, capsys):
    cli = _tool()
    monkeypatch.setattr(cli, "SessionLocal", sessionmaker(bind=engine, autoflush=False))
    w = world(db)
    p, r = pay(db, w, T0, 1000)
    fake = FakeQpay(monkeypatch)
    base = ["--from", "2026-10-01", "--tenant", "EASYPARKING", "--delay", "0"]
    ok = base + ["--apply", "--yes", "--user", "t", "--i-understand-not-idempotent"]
    monkeypatch.setattr(settings, "database_url", "postgresql+psycopg2://u:p@localhost/audit1014")
    assert cli.main(ok) == 3                                                 # продын ХУУЛБАР
    monkeypatch.setattr(settings, "database_url", "postgresql+psycopg2://u:p@localhost/t_parking")
    assert cli.main(ok) == 3
    monkeypatch.setattr(settings, "database_url", "postgresql+psycopg2://u:p@localhost/parking")
    monkeypatch.setenv("PARKING_DEPLOY_ROLE", "staging")
    assert cli.main(ok) == 3                                                 # ТЕСТ сервер (role)
    monkeypatch.delenv("PARKING_DEPLOY_ROLE")
    monkeypatch.setattr(settings, "public_base_url", "https://test.easy-parking.mn")
    assert cli.main(ok) == 3                                                 # ТЕСТ сервер (хаяг)
    monkeypatch.setattr(settings, "public_base_url", "https://site.easy-parking.mn")
    monkeypatch.setattr(settings, "qpay_mock", True)
    assert cli.main(ok) == 3                                                 # QPay MOCK
    monkeypatch.setattr(settings, "qpay_mock", False)
    monkeypatch.setattr(settings, "qpay_password", "")
    assert cli.main(ok) == 3                                                 # QPay тохируулаагүй
    monkeypatch.setattr(settings, "qpay_password", "secret")
    assert cli.main(base + ["--apply", "--yes", "--user", "t"]) == 6         # идемпотент биш — ил зөвшөөрөл
    assert cli.main(base + ["--apply", "--user", "t", "--i-understand-not-idempotent"]) == 4   # TTY биш
    assert cli.main(ok + ["--max", "0"]) == 2
    out = capsys.readouterr().out
    assert "продын ХУУЛБАР" in out and "staging" in out and "MOCK" in out
    assert "--i-understand-not-idempotent нэмж" in out
    assert fake.calls == [] and _rec(db, r.id).status == "REVIEW" and db.query(M.AuditLog).count() == 0
    # Бүх хамгаалалт давсан — туршилт --limit 1, дараа нь бүгд, дахин ажиллуулахад юу ч үгүй
    p2, r2 = pay(db, w, T0 + timedelta(minutes=1), 2000, tin="5059352")
    assert cli.main(ok + ["--limit", "1"]) == 0
    assert [c["payment_id"] for c in fake.calls] == [p.provider_payment_id]
    assert cli.main(ok) == 0
    assert [c["receiver"] for c in fake.calls] == [None, "5059352"]
    assert cli.main(ok) == 0 and len(fake.calls) == 2
    assert _rec(db, r.id).status == _rec(db, r2.id).status == "SENT"
    summ = _actions(db, QR.SUMMARY_ACTION)
    assert len(summ) == 2 and all(x.detail["result"] == {"sent": 1, "CREATE:SENT": 1} for x in summ)
    assert len(_actions(db, QR.AUDIT_ACTION)) == 2


def test_cli_apply_attach_teg_without_qpay_and_error_exit(db, engine, tmp_path, monkeypatch, capsys):
    cli = _tool()
    monkeypatch.setattr(cli, "SessionLocal", sessionmaker(bind=engine, autoflush=False))
    w = world(db)
    s, rows = teg_world(db, w)
    fake = FakeQpay(monkeypatch)
    f = _xlsx(str(tmp_path / "teg.xlsx"), rows)
    base = ["--from", "2026-10-01", "--tenant", "EASYPARKING", "--file", f, "--delay", "0",
            "--apply", "--yes", "--user", "temuujin"]
    # Зөвхөн ATTACH (QPay-гүй) — идемпотентийн зөвшөөрөлгүй ч болно
    assert cli.main(base + ["--payment", s["A"][0].id]) == 0
    rec = _rec(db, s["A"][1].id)
    assert (rec.status, rec.ebarimt_id, rec.provider) == ("SENT", ddtd(200), "QPAY")
    assert "teg:qpay-review-resolve" in rec.raw and fake.calls == []
    assert _job(db, s["A"][1].id).status == "DONE"
    # B (CREATE) QPay алдаа → REVIEW хэвээр, exit 1; C (AMBIGUOUS), D (OUTSIDE) хөндөгдөхгүй
    fake.fail = http_error(500, {"message": "Internal"})
    assert cli.main(base + ["--i-understand-not-idempotent"]) == 1
    assert [c["payment_id"] for c in fake.calls] == [s["B"][0].provider_payment_id]
    for k in "BCD":
        assert _rec(db, s[k][1].id).status == "REVIEW"
    out = capsys.readouterr().out
    assert "АЛДАА (REVIEW хэвээр): HTTP 500: Internal" in out
