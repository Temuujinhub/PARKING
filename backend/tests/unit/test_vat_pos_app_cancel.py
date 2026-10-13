"""POS аппын ДҮРМЭЭР (ТЕГ файлгүй) msgbill давхардлыг цуцлах — 2026-10-13 эзний шийдвэр.

ИйзиПаркинг ХХК-ийн POS терминалтай бүх зогсоолд PAX терминал POS аппын карт/бэлэн
борлуулалт бүрд Ontime e-Barimt хэвлэдэг → тэдгээрийн манай msgbill баримтыг цуцална.
Сонголтын дүрэм (`vat_duplicates.select_pos_app` / `pos_app_exclusion`), CLI
(`tools/vat_cancel_duplicates.py --pos-app`) ба тохиргооны хэрэгсэл
(`tools/pos_terminal_setting.py`)-ийг шалгана. msgbill HTTP хуурамч — гадагш хүсэлт 0.
DB нь санах ойн SQLite.
"""
import asyncio
import importlib.util
import os
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import models as M
from app.config import settings
from app.database import Base
from app.services import app_settings as A, financial_jobs as J, msgbill, vat_duplicates as VD
from app.services.receipts import DUPLICATE_OF_POS, is_duplicate_cancel

BASE = datetime(2026, 9, 10, 4, 0, 0)          # UTC (УБ 12:00)


class FakeResp:
    def __init__(self, status, j=None):
        self.status_code, self._j = status, j
        self.content = b"x" if j is not None else b""
        self.text = str(j)

    def json(self):
        return self._j


class FakeHTTP:
    script: list = []
    calls: list = []

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None, headers=None):
        FakeHTTP.calls.append(("POST", url))
        return FakeResp(*(FakeHTTP.script.pop(0) if FakeHTTP.script else (200, {"state": "CANCELLED"})))

    async def get(self, url, headers=None):
        FakeHTTP.calls.append(("GET", url))
        return FakeResp(*(FakeHTTP.script.pop(0) if FakeHTTP.script else (200, {"state": "CANCELLED"})))


@pytest.fixture(autouse=True)
def fake_http(monkeypatch):
    monkeypatch.setattr(msgbill.httpx, "AsyncClient", FakeHTTP)
    monkeypatch.setattr(settings, "msgbill_api_key", "bsk_live_GLOBAL")
    monkeypatch.setattr(settings, "msgbill_base_url", "https://msgbill.mn/api/v1")
    monkeypatch.setattr(settings, "ebarimt_mock_receipts", False)
    monkeypatch.setattr(settings, "secret_enc_key", "")
    monkeypatch.setattr(settings, "database_url", "postgresql+psycopg2://u:p@localhost/parking")
    monkeypatch.setattr(settings, "public_base_url", "https://easy-parking.mn")
    monkeypatch.delenv("PARKING_DEPLOY_ROLE", raising=False)
    msgbill.invalidate_cache()
    A.invalidate_cache()
    FakeHTTP.script, FakeHTTP.calls = [], []
    yield
    msgbill.invalidate_cache()
    A.invalidate_cache()


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


class World:
    pass


def _ddtd(i, reg="10045952"):
    return "0291002441060010972600%03d" % (i % 1000) + reg


def build(db):
    """ИйзиПаркинг: HANGARID (pos.k ≥20 POS карт, веб касс sogii), YLALT1 (pos.y 19 карт —
    босгоос доош), KH (тест), Моннис: MONNIS (pos.m 30 карт)."""
    w = World()
    w.ep = M.Tenant(name="ИйзиПаркинг ХХК", code="EASYPARKING", msgbill_api_key="bsk_live_EP")
    w.mon = M.Tenant(name="Моннис пропэрти", code="MONNIS", msgbill_api_key="bsk_live_MON")
    db.add_all([w.ep, w.mon])
    db.flush()
    w.sites = {}
    for code, ten in (("HANGARID", w.ep), ("YLALT1", w.ep), ("KH", w.ep), ("MONNIS", w.mon)):
        s = M.ParkingSite(name=code.title(), site_code=code, tenant_id=ten.id)
        db.add(s)
        db.flush()
        w.sites[code] = s
    w.users = {}
    for u in ("pos.k", "pos.y", "pos.m", "sogii", "operator"):
        x = M.User(username=u, password_hash="x", role="OPERATOR")
        db.add(x)
        db.flush()
        w.users[u] = x
    w.n = 0
    db.commit()
    return w


def pay(db, w, site, user, *, method="CARD", provider=None, source=None, at=None, amount=1000,
        rec="SENT", rprov="MSGBILL", ref=True, tin=None, rtype=None, mark=False, ddtd=None):
    w.n += 1
    p = M.Payment(site_id=w.sites[site].id, provider=provider or ("POS" if method == "CARD" else method),
                  payment_method=method, source=source, amount=Decimal(amount),
                  vat_amount=Decimal("90.91"), status="PAID", sender_invoice_no=f"i{w.n}",
                  paid_at=at or BASE + timedelta(minutes=w.n), cashier_id=w.users[user].id,
                  customer_tin=tin, ebarimt_receiver_type=rtype, raw_payload={"plate_number": f"{w.n}УБА"})
    db.add(p)
    db.flush()
    if rec:
        r = M.VatReceipt(payment_id=p.id, amount=p.amount, vat_amount=p.vat_amount, status=rec,
                         provider=rprov, provider_ref=f"rcp-{w.n}" if ref else None,
                         ebarimt_id=(ddtd or _ddtd(w.n)) if rec in ("SENT", "CANCEL_PENDING", "CANCELLED")
                         else None)
        if mark:
            r.ddtd_note = f"{DUPLICATE_OF_POS}: өмнө цуцалсан"
        db.add(r)
    return p


def bulk_cards(db, w, site, user, n):
    """Хэрэглэгчийн POS картын түүх (баримтгүй — зөвхөн босгонд)."""
    for _ in range(n):
        pay(db, w, site, user, rec=None)


def select(db, w, codes=None, include_company=False, override=None):
    sites = VD.pos_app_sites(db, w.ep, codes)
    su = VD.pos_users_for_sites(db, sites, override=override)
    lo, hi = VD.ub_window(None, "2026-10-13")
    return VD.select_pos_app(db, sites=sites, lo=lo, hi=hi, site_users=su,
                             include_company=include_company), su


# ───────────────────────── сонголтын дүрэм ─────────────────────────────

def test_selection_rule(db):
    w = build(db)
    bulk_cards(db, w, "HANGARID", "pos.k", 20)
    bulk_cards(db, w, "YLALT1", "pos.y", 18)
    bulk_cards(db, w, "MONNIS", "pos.m", 30)
    ok_card = pay(db, w, "HANGARID", "pos.k")                                   # provider POS
    ok_cash = pay(db, w, "HANGARID", "pos.k", method="CASH")                    # хэрэглэгчээр
    ok_src = pay(db, w, "HANGARID", "sogii", method="CASH", source="POSAPP")    # source POSAPP
    web = pay(db, w, "HANGARID", "sogii", method="CASH")                        # веб касс
    comp = pay(db, w, "HANGARID", "pos.k", tin="5317878", rtype="COMPANY")      # ААН
    cit_reg = pay(db, w, "HANGARID", "pos.k", method="CASH", tin="УБ99112233", rtype="CITIZEN")
    qr = pay(db, w, "HANGARID", "pos.k", method="QR", provider="QPAY")
    qpay_sent = pay(db, w, "HANGARID", "pos.k", rprov="QPAY", ref=False)        # өөр сувгийн SENT
    legacy = pay(db, w, "HANGARID", "pos.k", rprov=None, ref=False)             # хуучин PosAPI
    cancelled = pay(db, w, "HANGARID", "pos.k", rec="CANCELLED")                # энгийн цуцлалт
    failed = pay(db, w, "HANGARID", "pos.k", rec="FAILED", ref=False)
    noref = pay(db, w, "HANGARID", "pos.k", ref=False)                          # provider_ref алга
    mock = pay(db, w, "HANGARID", "pos.k", ddtd="9" * 40)                      # MOCK 40 оронтой ДДТД
    pending = pay(db, w, "HANGARID", "pos.k", rec="CANCEL_PENDING", mark=True)  # дундуур тасарсан
    done = pay(db, w, "HANGARID", "pos.k", rec="CANCELLED", mark=True)          # терминалын мөр дутуу
    before = pay(db, w, "HANGARID", "pos.k", at=datetime(2026, 8, 18, 15, 0))   # go-live-аас өмнө (УБ 23:00)
    y_cash = pay(db, w, "YLALT1", "pos.y", method="CASH")                       # pos.y 18+1 < 20 карт
    y_card = pay(db, w, "YLALT1", "pos.y")                                      # provider POS → POS апп
    kh = pay(db, w, "KH", "operator")                                           # тест зогсоол
    mon = pay(db, w, "MONNIS", "pos.m")                                         # Моннис
    db.commit()
    sel, su = select(db, w)
    ids = {r["payment_id"] for r in sel["selected"]}
    assert ids == {ok_card.id, ok_cash.id, ok_src.id, pending.id, y_card.id}
    assert su[w.sites["HANGARID"].id]["users"] == {"pos.k"}
    assert su[w.sites["YLALT1"].id]["users"] == set()                          # 19 карт < 20
    assert {r["payment_id"] for r in sel["company"]} == {comp.id, cit_reg.id}
    assert {r["payment_id"] for r in sel["complete"]} == {done.id}
    ex = sel["excluded"]
    assert ex["NOT_POS_APP"] == 2 and ex["COMPANY"] == 2                        # sogii web + pos.y бэлэн
    assert ex["OTHER_ISSUER_SENT"] == 2                                         # QPAY + хуучин PosAPI SENT
    # NO_MSGBILL: баримтгүй POS картын түүх 20+18 + cancelled/failed/done/noref; RULE: MOCK ДДТД
    assert ex["NO_MSGBILL"] == 20 + 18 + 4 and ex["RULE"] == 1
    assert not {qr.id, before.id, kh.id, mon.id, web.id, legacy.id, qpay_sent.id, cancelled.id,
                failed.id, noref.id, mock.id} & ids
    via = {r["payment_id"]: r["via"] for r in sel["selected"]}
    assert via[ok_card.id] == "provider=POS" and via[ok_cash.id] == "user=pos.k"
    assert via[ok_src.id] == "source=POSAPP"
    # ААН-ийг ил тодоор оруулбал
    sel2, _ = select(db, w, include_company=True)
    assert {comp.id, cit_reg.id} <= {r["payment_id"] for r in sel2["selected"]}
    # Тест зогсоолыг нэрлэвэл орно; өөр түрээслэгчийн зогсоол — алдаа
    sel3, _ = select(db, w, codes=["KH"])
    assert {r["payment_id"] for r in sel3["selected"]} == {kh.id}
    with pytest.raises(ValueError):
        VD.pos_app_sites(db, w.ep, ["MONNIS"])


def test_users_setting_and_override_take_precedence(db):
    w = build(db)
    bulk_cards(db, w, "HANGARID", "pos.k", 25)
    cash_k = pay(db, w, "HANGARID", "pos.k", method="CASH")
    cash_s = pay(db, w, "HANGARID", "sogii", method="CASH")
    db.commit()
    hid = w.sites["HANGARID"].id
    # Тохиргоо (pos_terminal_users) таамгийг дарна
    A.set_site_rules(db, A.EBARIMT_KEY, hid, {"pos_terminal_users": "sogii"}, "t")
    db.commit()
    A.invalidate_cache()
    sel, su = select(db, w)
    assert su[hid]["source"] == "тохиргоо" and su[hid]["users"] == {"sogii"}
    assert {r["payment_id"] for r in sel["selected"]} == {cash_s.id}
    # --pos-user бүгдийг дарна (зогсоолоор эсвэл бүх зогсоолд)
    for ov in (["HANGARID=pos.k"], ["pos.k"]):
        sel, su = select(db, w, override=VD.parse_pos_user_override(ov))
        assert su[hid]["source"] == "--pos-user"
        assert {r["payment_id"] for r in sel["selected"]} == {cash_k.id}


def test_derive_threshold_and_window(db):
    w = build(db)
    bulk_cards(db, w, "HANGARID", "pos.k", 19)
    pay(db, w, "HANGARID", "pos.k", rec=None, at=datetime(2026, 8, 1))      # go-live-аас өмнө — тоолохгүй
    db.commit()
    hid = w.sites["HANGARID"].id
    assert VD.derive_pos_users(db, [hid]) == {hid: {}}
    pay(db, w, "HANGARID", "pos.k", rec=None)
    db.commit()
    assert VD.derive_pos_users(db, [hid]) == {hid: {"pos.k": 20}}
    lo, hi = VD.ub_window("2026-09-01", "2026-09-30")
    assert lo == datetime(2026, 8, 31, 16, 0) and hi == datetime(2026, 9, 30, 16, 0)


def test_terminal_setting_does_not_suppress_company_receipt(db):
    """Тохиргоо асаалттай зогсоолд ч ААН/регистртэй баримтыг систем гаргасаар — терминал
    иргэний баримт хэвлэдэг (цуцлалтын дүрэмтэй НЭГ бодлого)."""
    w = build(db)
    hid = w.sites["HANGARID"].id
    A.set_site_rules(db, A.EBARIMT_KEY, hid, {"pos_terminal_issues": True}, "t")
    db.commit()
    A.invalidate_cache()
    card = pay(db, w, "HANGARID", "pos.k", rec=None)
    comp = pay(db, w, "HANGARID", "pos.k", rec=None, tin="5317878", rtype="COMPANY")
    db.commit()
    assert J.terminal_issues_receipt(db, card) is True
    assert J.terminal_issues_receipt(db, comp) is False


# ───────────────────────── CLI --pos-app ─────────────────────────────

def _tool(name):
    path = os.path.join(os.path.dirname(__file__), "..", "..", "tools", name)
    spec = importlib.util.spec_from_file_location(name[:-3], path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _recs(db, pid):
    db.expire_all()
    return db.query(M.VatReceipt).filter_by(payment_id=pid).all()


def test_cli_pos_app_dry_run_apply_resume(db, engine, tmp_path, monkeypatch, capsys):
    cli = _tool("vat_cancel_duplicates.py")
    monkeypatch.setattr(cli, "SessionLocal", sessionmaker(bind=engine, autoflush=False))
    w = build(db)
    bulk_cards(db, w, "HANGARID", "pos.k", 20)
    a = pay(db, w, "HANGARID", "pos.k")
    b = pay(db, w, "HANGARID", "pos.k", method="CASH")
    c = pay(db, w, "HANGARID", "pos.k", method="CASH")
    comp = pay(db, w, "HANGARID", "pos.k", tin="5317878", rtype="COMPANY")
    qr = pay(db, w, "HANGARID", "pos.k", method="QR", provider="QPAY")
    mon = pay(db, w, "MONNIS", "pos.m")
    db.commit()
    out = str(tmp_path / "p.csv")
    base = ["--pos-app", "--out", out, "--to", "2026-10-13", "--delay", "0"]
    with pytest.raises(SystemExit):                                       # --file-тай хамт болохгүй
        cli.main(base + ["--file", out])
    with pytest.raises(SystemExit) as e:                                  # --tenant заавал
        cli.main(base)
    assert "--tenant" in str(e.value)
    assert cli.main(base + ["--tenant", "EASYPARKING"]) == 0              # dry-run
    text = capsys.readouterr().out
    assert "ЦУЦЛАХ (POS аппын дүрэм): 3 төлбөр" in text and "e-Barimt-ГҮЙ" in text
    assert "ААН/ТТД-тэй баримт: 1" in text and "HANGARID" in text and "2026-09" in text
    assert FakeHTTP.calls == [] and db.query(M.AuditLog).count() == 0
    # Моннис түрээслэгч — эзний шийдвэрт хамаарахгүй
    apply = ["--apply", "--yes", "--user", "temuujin", "--note", "эзний шийдвэр"]
    assert cli.main(base + ["--tenant", "MONNIS"] + apply) == 6 and FakeHTTP.calls == []
    # Хуулбар DB дээр татгалзана
    monkeypatch.setattr(settings, "database_url", "postgresql+psycopg2://u:p@localhost/audit1013")
    assert cli.main(base + ["--tenant", "EASYPARKING"] + apply) == 3 and FakeHTTP.calls == []
    monkeypatch.setattr(settings, "database_url", "postgresql+psycopg2://u:p@localhost/parking")
    # --max нь бодит цуцлах тоонд (--limit-ийн дараа)
    assert cli.main(base + ["--tenant", "EASYPARKING", "--max", "2"] + apply) == 2
    assert cli.main(base + ["--tenant", "EASYPARKING", "--max", "2", "--limit", "1"] + apply) == 0
    assert len(FakeHTTP.calls) == 1
    rs = _recs(db, a.id)
    msg = [r for r in rs if r.provider == "MSGBILL"]
    assert [r.status for r in msg] == ["CANCELLED"] and is_duplicate_cancel(msg[0])
    assert msg[0].raw["cancel"]["rule"] == VD.POS_APP_RULE
    [term] = [r for r in rs if r.provider != "MSGBILL"]
    assert (term.status, term.provider, term.ebarimt_id) == ("EXTERNAL", "POS-TERMINAL", None)
    # Үлдсэнийг — эхнийх нь сонголтоос гарсан (msgbill цуцлагдсан), давхар дуудахгүй
    FakeHTTP.calls = []
    assert cli.main(base + ["--tenant", "EASYPARKING"] + apply) == 0
    assert len(FakeHTTP.calls) == 2
    for p in (a, b, c):
        st = sorted((r.provider, r.status) for r in _recs(db, p.id))
        assert st == [("MSGBILL", "CANCELLED"), ("POS-TERMINAL", "EXTERNAL")]
    for p in (comp, qr, mon):                                               # хөндөгдөөгүй
        assert [r.status for r in _recs(db, p.id)] == ["SENT"]
    logs = db.query(M.AuditLog).filter_by(action="EBARIMT_CANCEL_BULK").all()
    assert len(logs) == 3 and all(x.username == "temuujin" and x.detail["note"] == "эзний шийдвэр"
                                  and x.detail["rule"] == VD.POS_APP_RULE for x in logs)
    assert db.query(M.AuditLog).filter_by(action="EBARIMT_CANCEL_BULK_SUMMARY").count() == 2
    # Дахин — юу ч хийхгүй; «Дахин үүсгэх» давхардлыг сэргээхгүй
    FakeHTTP.calls = []
    assert cli.main(base + ["--tenant", "EASYPARKING"] + apply) == 0 and FakeHTTP.calls == []
    res = J.retry_payment_receipts(db, db.get(M.Payment, b.id))
    assert res.get("duplicate_of_pos") and len(_recs(db, b.id)) == 2


def test_guard_rechecks_after_selection(db):
    """Сонголтоос хойш ААН болсон / веб кассынх болсон төлбөрийг цуцлахгүй (түгжээний дараа)."""
    w = build(db)
    bulk_cards(db, w, "HANGARID", "pos.k", 20)
    p = pay(db, w, "HANGARID", "pos.k", method="CASH")
    db.commit()
    sel, su = select(db, w)
    assert [r["payment_id"] for r in sel["selected"]] == [p.id]
    p.customer_tin, p.ebarimt_receiver_type = "5317878", "COMPANY"
    db.commit()
    guard = VD.pos_app_guard(su, {s.id for s in VD.pos_app_sites(db, w.ep)})
    out = asyncio.run(VD.cancel_one(db, p.id, note="x", username="t", guard=guard,
                                    mark_extra={"rule": VD.POS_APP_RULE}))
    assert out["ok"] is False and "ААН" in out["error"] and FakeHTTP.calls == []
    assert [r.status for r in _recs(db, p.id)] == ["SENT"]


def test_pos_app_external_then_teg_file_upgrades_terminal_row(db):
    """POS аппын дүрмээр цуцалж EXTERNAL бүртгэсний ДАРАА 08-24-ний ТЕГ файлаар ажиллуулбал
    шинэ мөр нэмэхгүй — EXTERNAL мөр Ontime ДДТД-тэй ONTIME/SENT болно; msgbill дуудахгүй."""
    w = build(db)
    p = pay(db, w, "HANGARID", "pos.k")
    db.commit()
    out = asyncio.run(VD.cancel_one(db, p.id, note="x", username="t", source="cli-pos-app"))
    assert out["terminal_receipt"]["status"] == "EXTERNAL" and len(FakeHTTP.calls) == 1
    teg = "015200020090000097260000110002990"
    out = asyncio.run(VD.cancel_one(db, p.id, note="x", username="t", teg={"teg_ddtd": teg}))
    assert out["skipped"] == "already_cancelled" and len(FakeHTTP.calls) == 1
    st = sorted((r.provider, r.status, r.ebarimt_id) for r in _recs(db, p.id))
    assert st == [("MSGBILL", "CANCELLED", _ddtd(1)), ("ONTIME", "SENT", teg)]


# ───────────────────────── тохиргооны хэрэгсэл ─────────────────────────────

def test_setting_tool_dry_run_apply_and_guards(db, engine, monkeypatch, capsys):
    tool = _tool("pos_terminal_setting.py")
    monkeypatch.setattr(tool, "SessionLocal", sessionmaker(bind=engine, autoflush=False))
    w = build(db)
    bulk_cards(db, w, "HANGARID", "pos.k", 21)
    bulk_cards(db, w, "YLALT1", "pos.y", 5)                 # терминалгүй гэж үзнэ (< 20)
    bulk_cards(db, w, "KH", "operator", 30)                 # тест зогсоол — нэрлээгүй
    db.commit()
    hid = w.sites["HANGARID"].id
    assert tool.main(["--tenant", "EASYPARKING"]) == 0       # dry-run
    text = capsys.readouterr().out
    assert "ДАРАА: асаалт=ON · аргууд=CARD,CASH · хэрэглэгчид=pos.k" in text
    assert "Өөрчлөх зогсоол: 1" in text
    A.invalidate_cache()
    assert A.pos_terminal_receipt_methods(db, hid) == set() and db.query(M.AuditLog).count() == 0
    assert tool.main(["--tenant", "MONNIS", "--apply"]) == 6
    monkeypatch.setenv("PARKING_DEPLOY_ROLE", "staging")
    assert tool.main(["--tenant", "EASYPARKING", "--apply"]) == 3
    monkeypatch.delenv("PARKING_DEPLOY_ROLE")
    assert tool.main(["--tenant", "EASYPARKING", "--apply", "--user", "temuujin"]) == 0
    db.expire_all()
    A.invalidate_cache()
    assert A.pos_terminal_receipt_methods(db, hid) == {"CARD", "CASH"}
    assert A.pos_app_users(db, hid) == {"pos.k"}
    for code in ("YLALT1", "KH"):
        assert A.pos_terminal_receipt_methods(db, w.sites[code].id) == set()
    [log] = db.query(M.AuditLog).filter_by(action="UPDATE", entity="payment_rules").all()
    assert log.entity_id == hid and log.username == "temuujin"
    assert log.detail["ebarimt_rules"] == {"pos_terminal_issues": True, "pos_terminal_methods": "CARD,CASH",
                                           "pos_terminal_users": "pos.k"}
    # Дахин — өөрчлөлтгүй (идемпотент), шинэ аудит үгүй
    assert tool.main(["--tenant", "EASYPARKING", "--apply", "--user", "temuujin"]) == 0
    assert db.query(M.AuditLog).filter_by(action="UPDATE").count() == 1
    # Унтраах
    assert tool.main(["--tenant", "EASYPARKING", "--site", "HANGARID", "--off", "--apply"]) == 0
    db.expire_all()
    A.invalidate_cache()
    assert A.pos_terminal_receipt_methods(db, hid) == set()


def test_evidence_file_excludes_sales_without_ontime_row(db, engine, tmp_path, monkeypatch, capsys):
    """--evidence: ТЕГ экспортын хугацаанд Ontime (…10002990) мөргүй POS аппын борлуулалтыг
    цуцлахгүй — терминал баримт хэвлээгүй нь батлагдсан, манай msgbill нь цорын ганц баримт
    (08-24: 70-аас 7 нь Ontime мөргүй байв)."""
    import openpyxl
    cli = _tool("vat_cancel_duplicates.py")
    monkeypatch.setattr(cli, "SessionLocal", sessionmaker(bind=engine, autoflush=False))
    w = build(db)
    bulk_cards(db, w, "HANGARID", "pos.k", 20)
    t0 = datetime(2026, 9, 20, 3, 0, 0)
    has = pay(db, w, "HANGARID", "pos.k", method="CASH", at=t0, amount=1000)
    lacks = pay(db, w, "HANGARID", "pos.k", at=t0 + timedelta(minutes=30), amount=2000)
    outside = pay(db, w, "HANGARID", "pos.k", at=t0 + timedelta(days=2), amount=3000)
    db.commit()
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Пос дугаар", "ДДТД", "Огноо", "Нийт дүн", "Систем нийлүүлэгч"])
    ws.append(["1", "015200020090000097260000110002990", (t0 + timedelta(seconds=5)).strftime("%Y-%m-%d %H:%M:%S"),
               1000, "Ontime"])
    for i, p in enumerate((has, lacks)):                       # манай msgbill мөр (УБ локал)
        ws.append(["2", "01520002009000109726000%02d10045952" % i,
                   (p.paid_at + timedelta(hours=8, seconds=1)).strftime("%Y-%m-%d %H:%M:%S"),
                   float(p.amount), "Онлайм нетворк"])
    f = str(tmp_path / "teg.xlsx")
    wb.save(f)
    out = str(tmp_path / "p.csv")
    base = ["--pos-app", "--tenant", "EASYPARKING", "--to", "2026-10-13", "--out", out,
            "--evidence", f, "--delay", "0"]
    assert cli.main(base) == 0
    text = capsys.readouterr().out
    assert "хугацаанд 2 төлбөр · Ontime мөртэй 1 (50.0%) · Ontime мөргүй 1" in text
    assert (tmp_path / "p_no_ontime.csv").read_text(encoding="utf-8-sig").count(lacks.id) == 1
    assert cli.main(base + ["--apply", "--yes", "--user", "t"]) == 0
    assert len(FakeHTTP.calls) == 2                                   # has + outside
    assert [r.status for r in _recs(db, lacks.id)] == ["SENT"]        # нотолгоогүй — хөндөөгүй
    for p in (has, outside):
        assert sorted(r.status for r in _recs(db, p.id)) == ["CANCELLED", "EXTERNAL"]
