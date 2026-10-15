"""Терминал (Ontime) баримт ХЭВЛЭЭГҮЙ POS аппын борлуулалтыг ДАХИН гаргах (2026-10-14).

`tools/vat_reissue_missing.py` + `vat_duplicates.terminal_evidence/evidence_split/
select_reissue/reissue_one`. ТЕГ экспортын бодит хэлбэрүүд: (а) Ontime/QPay мөр UTC, msgbill
мөр УБ локал (08-25-ны экспорт), (б) бүгд УБ (08-24 «ebarimt-tulgalt»). msgbill/QPay-ийн касс
түрээслэгчдэд НИЙТЛЭГ (…10045952/…10014863) тул файлын түрээслэгчийг манай кассын мөрөөр батална.
Сүлжээ огт дуудахгүй (дахин гаргалт = outbox ажил). DB нь санах ойн SQLite.
"""
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
from app.services import app_settings as A, msgbill, pos_app as PA, vat_duplicates as VD
from app.services.receipts import DUPLICATE_OF_POS, REISSUED_NO_TERMINAL

T0 = datetime(2026, 9, 20, 3, 0, 0)            # UTC (УБ 11:00)
MSG, QPY, ONT = "10045952", "10014863", "10002990"


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setattr(settings, "msgbill_api_key", "bsk_live_GLOBAL")
    monkeypatch.setattr(settings, "msgbill_base_url", "https://msgbill.mn/api/v1")
    monkeypatch.setattr(settings, "ebarimt_mock_receipts", False)
    monkeypatch.setattr(settings, "qpay_ebarimt", False)
    monkeypatch.setattr(settings, "secret_enc_key", "")
    monkeypatch.setattr(settings, "database_url", "postgresql+psycopg2://u:p@localhost/parking")
    monkeypatch.setattr(settings, "public_base_url", "https://easy-parking.mn")
    monkeypatch.delenv("PARKING_DEPLOY_ROLE", raising=False)
    # Түүхэн цуцлалт/нөхөлтийн хэрэгслүүд 10-14-ний тохиргоогоор (ерөнхий «терминал баримт
    # гаргадаг» АСААЛТТАЙ) ажиллаж байсныг барина — кодын анхдагч 2026-10-15-аас УНТРААЛТТАЙ
    # (унтраалттай үед --pos-app юу ч сонгохгүй: SITE_OFF — test_pos_inline_receipt).
    monkeypatch.setitem(A.DEFAULTS[A.EBARIMT_KEY], "pos_terminal_issues", True)

    async def no_net(*a, **k):
        raise AssertionError("Network forbidden")
    import httpx
    monkeypatch.setattr(httpx.AsyncClient, "send", no_net)
    msgbill.invalidate_cache()
    A.invalidate_cache()
    PA.invalidate_cache()
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


class W:
    n = 0


def ddtd(seq, reg):
    return "0152000200900009726%06d" % (seq % 1000000) + reg


def world(db):
    w = W()
    w.ep = M.Tenant(name="ИйзиПаркинг ХХК", code="EASYPARKING", msgbill_api_key="bsk_live_EP")
    w.mon = M.Tenant(name="Моннис пропэрти", code="MONNIS", msgbill_api_key="bsk_live_MON")
    db.add_all([w.ep, w.mon])
    db.flush()
    w.site = M.ParkingSite(name="Хангарьд", site_code="HANGARID", tenant_id=w.ep.id)
    w.mon_site = M.ParkingSite(name="Моннис", site_code="MONNIS", tenant_id=w.mon.id)
    db.add_all([w.site, w.mon_site])
    w.pos = M.User(username="pos.k", password_hash="x", role="POS")
    w.web = M.User(username="sogii", password_hash="x", role="OPERATOR")
    db.add_all([w.pos, w.web])
    db.flush()
    return w


def payment(db, w, at, amount, *, method="CARD", user=None, site=None, receipts=()):
    """receipts: [(provider, status, {ebarimt_id, provider_ref, raw, ddtd_note})]."""
    W.n += 1
    p = M.Payment(site_id=(site or w.site).id, provider={"CARD": "POS", "QR": "QPAY"}.get(method, method),
                  payment_method=method, source="POSAPP" if method in ("CARD", "CASH") and user is w.pos else None,
                  amount=Decimal(amount), vat_amount=Decimal("0"), status="PAID",
                  sender_invoice_no=f"r{W.n}", paid_at=at, cashier_id=(user or w.pos).id,
                  raw_payload={"plate_number": f"{W.n:04d}УБА"})
    db.add(p)
    db.flush()
    for prov, st, extra in receipts:
        db.add(M.VatReceipt(payment_id=p.id, amount=p.amount, vat_amount=p.vat_amount, provider=prov,
                            status=st, **extra))
    return p


EXT = ("POS-TERMINAL", "EXTERNAL", {"receipt_url": "Баримтыг POS терминал (Ontime) гаргасан"})


def dup(seq, **cancel):
    return ("MSGBILL", "CANCELLED", {"ebarimt_id": ddtd(seq, MSG), "provider_ref": f"rcp_{seq}",
                                     "ddtd_note": f"{DUPLICATE_OF_POS}: x",
                                     "raw": {"cancel": {"reason": DUPLICATE_OF_POS, **cancel}}})


def sent(seq, prov="MSGBILL", reg=MSG):
    return (prov, "SENT", {"ebarimt_id": ddtd(seq, reg), "provider_ref": f"rcp_{seq}"})


def scenario(db, w):
    """Хангарьд: 4 веб кассын msgbill SENT (манай касс), 6 нэр дэвшигч, QR + QPay."""
    s = {}
    for i, m in enumerate((0, 5, 10, 90)):         # web3 — файлын хамрах хугацааг 90 мин хүртэл
        s[f"web{i}"] = payment(db, w, T0 + timedelta(minutes=m), 1100 + i, method="CASH",
                               user=w.web, receipts=[sent(900 + i)])
    s["ext_ok"] = payment(db, w, T0 + timedelta(minutes=20), 2000, receipts=[EXT])
    s["ext_miss"] = payment(db, w, T0 + timedelta(minutes=30), 3000, receipts=[EXT])
    s["dup_ok"] = payment(db, w, T0 + timedelta(minutes=40), 4000, method="CASH",
                          receipts=[dup(1, rule=VD.POS_APP_RULE), EXT])
    s["dup_miss"] = payment(db, w, T0 + timedelta(minutes=50), 5000, receipts=[dup(2, rule=VD.POS_APP_RULE)])
    s["dup_teg"] = payment(db, w, T0 + timedelta(minutes=60), 6000,
                           receipts=[dup(3, teg_ddtd=ddtd(77, ONT)), EXT])
    s["outside"] = payment(db, w, T0 + timedelta(days=3), 7000, receipts=[EXT])
    s["plain_cancel"] = payment(db, w, T0 + timedelta(minutes=70), 8000,
                                receipts=[("MSGBILL", "CANCELLED", {"ebarimt_id": ddtd(4, MSG),
                                                                    "provider_ref": "rcp_4"})])
    # QR төлбөрүүд (манайд QPAY баримтгүй — QPay касс «өөр систем» болно) + ext_miss-тэй ижил
    # дүн/цагийн QR (QPay мөр ext_miss руу хосолбол худал «терминалтай» болох байсан)
    for i in range(4):
        s[f"qr{i}"] = payment(db, w, T0 + timedelta(minutes=3 + 7 * i), 900 + 10 * i, method="QR", user=w.web)
    s["qr_x"] = payment(db, w, T0 + timedelta(minutes=30, seconds=12), 3000, method="QR", user=w.web)
    db.commit()
    return s


def export(s, *, ontime_utc=True, terminal=("ext_ok", "dup_ok"), own=True):
    """ТЕГ экспорт мөрүүд (parse_tax_export-ийн гаралттай ижил dict). msgbill — УБ локал."""
    ub = timedelta(hours=8)
    rows = []
    if own:
        for i in range(4):
            p = s[f"web{i}"]
            rows.append({"dt": p.paid_at + ub + timedelta(seconds=3), "amount": float(p.amount),
                         "ddtd": ddtd(900 + i, MSG), "src": "msgbill"})
    for i, k in enumerate([f"qr{i}" for i in range(4)] + ["qr_x"]):
        p = s[k]
        rows.append({"dt": p.paid_at + timedelta(seconds=-2 if k == "qr_x" else 4), "amount": float(p.amount),
                     "ddtd": ddtd(500 + i, QPY), "src": "QPay"})
    for i, k in enumerate(terminal):
        p = s[k]
        rows.append({"dt": p.paid_at + (timedelta(0) if ontime_utc else ub) + timedelta(seconds=20),
                     "amount": float(p.amount), "ddtd": ddtd(300 + i, ONT), "src": "Ontime"})
    return rows


def evidence(db, w, rows, sites=None):
    sites = sites or VD.tenant_sites(db, [w.ep])
    ev = VD.terminal_evidence(db, [("teg.xlsx", rows)], sites=sites, tol=60, shifts=[0.0, -8.0])
    return ev


# ───────────────────────── нэр дэвшигчийн дүрэм ─────────────────────────

def test_reissue_reason_rules(db):
    w = world(db)
    s = scenario(db, w)
    recs = lambda p: db.query(M.VatReceipt).filter_by(payment_id=p.id).all()   # noqa: E731
    r = {k: VD.reissue_reason(p, recs(p)) for k, p in s.items()}
    assert r["ext_ok"] is None and r["ext_miss"] is None and r["dup_ok"] is None and r["dup_miss"] is None
    assert r["dup_teg"] == "TEG_CONFIRMED"                 # --file цуцлалт = терминал хэвлэсний нотолгоо
    assert r["plain_cancel"] == "NOT_TERMINAL"             # давхардлын бус цуцлалт — хөндөхгүй
    assert r["web0"] == "NOT_TERMINAL" and r["qr0"] == "METHOD"
    # ДДТД-тэй терминалын мөр (ТЕГ-ээр батлагдсан) эсвэл системийн PENDING мөртэй бол — баримттай
    p = payment(db, w, T0, 100, receipts=[dup(9), ("ONTIME", "SENT", {"ebarimt_id": ddtd(9, ONT)})])
    q = payment(db, w, T0, 100, receipts=[EXT, ("MSGBILL", "PENDING", {})])
    x = payment(db, w, T0, 100, receipts=[("POS-TERMINAL", "EXTERNAL",
                                           {"raw": {"teg": {"teg_ddtd": ddtd(8, MSG), "untrusted": True}}})])
    db.commit()
    assert VD.reissue_reason(p, recs(p)) == "HAS_RECEIPT"
    assert VD.reissue_reason(q, recs(q)) == "HAS_RECEIPT"
    assert VD.reissue_reason(x, recs(x)) == "TEG_CONFIRMED"


@pytest.mark.parametrize("ontime_utc", [True, False])
def test_matcher_real_export_shapes(db, ontime_utc):
    """(а) Ontime UTC + msgbill УБ, (б) бүгд УБ — касс бүрийн шилжилтийг тааралтаар сонгоно.
    QPay касс (манайд QPAY баримтгүй → «өөр систем») QR-тэй таардаг тул ТЕРМИНАЛ гэж үзэхгүй —
    эс бөгөөс QPay мөр ext_miss-тэй (POS апп түрүүлж) хосолж худал «терминалтай» болох байв."""
    w = world(db)
    s = scenario(db, w)
    ev = evidence(db, w, export(s, ontime_utc=ontime_utc))
    [st] = ev["files"]
    assert st["usable"] and st["tenants"] == {w.ep.id} and st["own_registers"] == [MSG]
    assert st["qr_registers"] == [QPY] and list(st["terminal_registers"]) == [ONT]
    assert st["terminal_registers"][ONT]["shifts"] == {0 if ontime_utc else -8}
    sel = VD.select_reissue(db, sites=[w.site], lo=T0 - timedelta(days=1), hi=T0 + timedelta(days=5))
    assert sel["skipped"]["TEG_CONFIRMED"] == 1 and sel["skipped"]["NOT_TERMINAL"] == 1
    split = VD.evidence_split(sel["candidates"], ev, {w.site.id: w.ep.id, w.mon_site.id: w.mon.id})
    ids = {k: {r["payment_id"] for r in v} for k, v in split.items()}
    assert ids["with_terminal"] == {s["ext_ok"].id, s["dup_ok"].id}
    assert ids["missing"] == {s["ext_miss"].id, s["dup_miss"].id}
    assert ids["outside"] == {s["outside"].id}


def test_long_file_is_chunked_with_same_result(db):
    """Урт (олон хоногийн) экспортыг хоногоор хувааж тулгана (санах ой) — хил дээрх
    (УБ шөнө дунд / UTC шөнө дунд) борлуулалтын Ontime мөр хөрш хэсэгт таарсан ч НЭГДЭЛ
    нь бүхэлд нь тулгасантай ИЖИЛ."""
    w = world(db)
    s = scenario(db, w)
    rows = export(s)
    edge = {}
    for d in range(1, 4):                                  # 3 хоног, хоног бүрд манай касс + Ontime
        base = T0 + timedelta(days=d)
        for i, m in enumerate((-190, -175, 0, 15, 400, 415)):
            p = payment(db, w, base + timedelta(minutes=m), 1200 + 10 * i + d, method="CASH",
                        user=w.web, receipts=[sent(1000 + 10 * d + i)])
            rows.append({"dt": p.paid_at + timedelta(hours=8, seconds=3), "amount": float(p.amount),
                         "ddtd": ddtd(1000 + 10 * d + i, MSG), "src": "msgbill"})
        # UTC шөнө дундын хил: 23:59:50-д төлсөн, Ontime мөр (UTC) 00:00:12 — дараагийн хоногт
        k = f"edge{d}"
        edge[k] = payment(db, w, datetime(2026, 9, 20 + d, 23, 59, 50), 3333 + d, receipts=[EXT])
        rows.append({"dt": datetime(2026, 9, 21 + d, 0, 0, 12), "amount": 3333.0 + d,
                     "ddtd": ddtd(600 + d, ONT), "src": "Ontime"})
    db.commit()
    whole = VD.terminal_evidence(db, [("m.xlsx", rows)], sites=[w.site, w.mon_site], tol=60,
                                 shifts=[0.0, -8.0], chunk_days=31)
    parts = VD.terminal_evidence(db, [("m.xlsx", rows)], sites=[w.site, w.mon_site], tol=60,
                                 shifts=[0.0, -8.0], chunk_days=1)
    assert parts["files"][0]["chunks"] > 3 and whole["files"][0]["chunks"] == 1
    assert parts["terminal_ids"] == whole["terminal_ids"]
    assert {p.id for p in edge.values()} <= parts["terminal_ids"]
    sel = VD.select_reissue(db, sites=[w.site], lo=T0 - timedelta(days=1), hi=T0 + timedelta(days=6))
    ten = {w.site.id: w.ep.id, w.mon_site.id: w.mon.id}
    a, b = VD.evidence_split(sel["candidates"], whole, ten), VD.evidence_split(sel["candidates"], parts, ten)
    assert {k: {r["payment_id"] for r in v} for k, v in a.items()} == \
        {k: {r["payment_id"] for r in v} for k, v in b.items()}


def test_terminal_row_never_precedes_its_sale(db):
    """Бодит ТЕГ экспорт: Ontime мөр төлбөрөөс 0.4–26с ХОЙНО. Ижил дүнтэй хоёр POS аппын
    борлуулалтаас мөрөөс 15с ХОЙНО төлсөнд (боломжгүй) биш, 40с ӨМНӨ төлсөнд оноогдоно —
    эс бөгөөс жинхэнэ борлуулалт «терминалын баримтгүй» → ДАХИН гаргагдаж давхардана."""
    w = world(db)
    s = scenario(db, w)
    t = T0 + timedelta(minutes=80)
    before = payment(db, w, t - timedelta(seconds=40), 4400, receipts=[EXT])     # жинхэнэ
    after = payment(db, w, t + timedelta(seconds=15), 4400, receipts=[EXT])      # терминал хэвлээгүй
    db.commit()
    rows = export(s) + [{"dt": t, "amount": 4400.0, "ddtd": ddtd(399, ONT), "src": "Ontime"}]
    ev = evidence(db, w, rows)
    assert before.id in ev["terminal_ids"] and after.id not in ev["terminal_ids"]


def test_file_without_our_register_or_other_tenant_is_not_evidence(db):
    """Манай кассын мөргүй (зөвхөн Ontime/QPay шүүж татсан) файл, эсвэл өөр түрээслэгчийн
    файл НОТОЛГОО БИШ — ямар ч төлбөрийг «терминалын баримтгүй» гэж дүгнэхгүй."""
    w = world(db)
    s = scenario(db, w)
    ev = evidence(db, w, export(s, own=False))
    assert ev["coverage"] == [] and not ev["files"][0]["usable"]
    # Моннисын зогсоолоор (өөр ТТД) — EP-ийн файлын манай кассын мөр Моннисын баримттай таарахгүй
    payment(db, w, T0 + timedelta(minutes=5), 1101, method="CASH", user=w.web, site=w.mon_site,
            receipts=[sent(990)])
    db.commit()
    ev = evidence(db, w, export(s), sites=[w.mon_site])
    assert ev["coverage"] == [] and "батлагдсангүй" in ev["files"][0]["note"]


def test_tenant_all_other_tenant_cannot_steal_terminal_rows(db):
    """--tenant ALL: msgbill/QPay-ийн касс түрээслэгчдэд нийтлэг — Моннисын ижил дүн, ойрхон
    цагтай POS аппын төлбөр EP-ийн файлын Ontime мөрийг «булааж» EP-ийн жинхэнэ борлуулалт
    «терминалын баримтгүй» → ДАХИН гаргагдах ёсгүй. Файлын түрээслэгчийг тогтоосны дараа
    терминалын мөрийг зөвхөн тэр түрээслэгчийн төлбөртэй хосолно."""
    w = world(db)
    s = scenario(db, w)
    for k in ("ext_ok", "dup_ok"):                 # Ontime мөрөөс 2с зайтай (EP-ийнх 20с)
        p = s[k]
        payment(db, w, p.paid_at + timedelta(seconds=18), int(p.amount), method=p.payment_method,
                site=w.mon_site, receipts=[sent(700 + len(k), MSG)])
    db.commit()
    rows = export(s)
    ev = VD.terminal_evidence(db, [("teg.xlsx", rows)], sites=VD.tenant_sites(db, [w.ep, w.mon]),
                              tol=60, shifts=[0.0, -8.0])
    assert ev["files"][0]["tenants"] == {w.ep.id}
    sel = VD.select_reissue(db, sites=[w.site, w.mon_site], lo=T0 - timedelta(days=1),
                            hi=T0 + timedelta(days=5))
    split = VD.evidence_split(sel["candidates"], ev, {w.site.id: w.ep.id, w.mon_site.id: w.mon.id})
    assert {r["payment_id"] for r in split["with_terminal"]} == {s["ext_ok"].id, s["dup_ok"].id}
    assert {r["payment_id"] for r in split["missing"]} == {s["ext_miss"].id, s["dup_miss"].id}


# ───────────────────────── CLI ─────────────────────────

def _tool(name):
    path = os.path.join(os.path.dirname(__file__), "..", "..", "tools", name)
    spec = importlib.util.spec_from_file_location(name[:-3], path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _xlsx(path, rows):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Пос дугаар", "ДДТД", "Огноо", "Нийт дүн", "Систем нийлүүлэгч"])
    for r in rows:
        ws.append(["1", r["ddtd"], r["dt"].strftime("%Y-%m-%d %H:%M:%S"), r["amount"], r["src"]])
    wb.save(path)
    return path


def _recs(db, pid):
    db.expire_all()
    return db.query(M.VatReceipt).filter_by(payment_id=pid).all()


def test_cli_dry_run_apply_idempotent_and_not_recancelled(db, engine, tmp_path, monkeypatch, capsys):
    cli = _tool("vat_reissue_missing.py")
    monkeypatch.setattr(cli, "SessionLocal", sessionmaker(bind=engine, autoflush=False))
    w = world(db)
    s = scenario(db, w)
    f = _xlsx(str(tmp_path / "teg.xlsx"), export(s))
    out = str(tmp_path / "r.csv")
    base = ["--file", f, "--tenant", "EASYPARKING", "--out", out, "--delay", "0"]
    assert cli.main(base) == 0
    text = capsys.readouterr().out
    assert "ДАХИН ГАРГАХ (ТЕГ-т терминалын мөр алга): 2 төлбөр · 8,000₮" in text
    assert "EASYPARKING HANGARID" in text and "2026-09-20     2" in text
    assert "QPay гэж хассан касс: 10014863" in text
    csv_text = (tmp_path / "r.csv").read_text(encoding="utf-8-sig")
    assert s["ext_miss"].id in csv_text and s["dup_miss"].id in csv_text and s["ext_ok"].id not in csv_text
    assert db.query(M.FinancialJob).count() == 0 and db.query(M.AuditLog).count() == 0
    # Хамгаалалт: хуулбар DB, --max, YES-гүй TTY биш
    monkeypatch.setattr(settings, "database_url", "postgresql+psycopg2://u:p@localhost/audit1014")
    assert cli.main(base + ["--apply", "--yes", "--user", "t"]) == 3
    monkeypatch.setattr(settings, "database_url", "postgresql+psycopg2://u:p@localhost/parking")
    assert cli.main(base + ["--apply", "--yes", "--user", "t", "--max", "1"]) == 2
    assert cli.main(base + ["--apply", "--user", "t"]) == 4                  # stdin TTY биш
    assert db.query(M.FinancialJob).count() == 0
    # Бодит (туршилт --limit 1 → бүгд)
    assert cli.main(base + ["--apply", "--yes", "--user", "temuujin", "--limit", "1"]) == 0
    assert cli.main(base + ["--apply", "--yes", "--user", "temuujin"]) == 0
    for k in ("ext_miss", "dup_miss"):
        live = [r for r in _recs(db, s[k].id) if r.status != "CANCELLED"]
        assert [(r.provider, r.status) for r in live] == [("MSGBILL", "PENDING")]
        assert live[0].raw["reissue"]["reason"] == REISSUED_NO_TERMINAL
        assert live[0].raw["reissue"]["files"] == ["teg.xlsx"]
        job = db.query(M.FinancialJob).filter_by(receipt_id=live[0].id).one()
        assert job.status == "PENDING" and job.payload["provider"] == "MSGBILL"
    ext_job = db.query(M.FinancialJob).filter_by(payment_id=s["ext_miss"].id).one()
    assert ext_job.payload["idempotency_key"].startswith("external-")
    dup_job = db.query(M.FinancialJob).filter_by(payment_id=s["dup_miss"].id).one()
    assert dup_job.payload["idempotency_key"].startswith("replace-")
    for k in ("ext_ok", "dup_ok", "dup_teg", "outside", "plain_cancel"):          # хөндөгдөөгүй
        assert not db.query(M.FinancialJob).filter_by(payment_id=s[k].id).count()
    logs = db.query(M.AuditLog).filter_by(action="EBARIMT_REISSUE_NO_TERMINAL").all()
    assert len(logs) == 2 and all(x.username == "temuujin" and x.detail["ok"] for x in logs)
    assert db.query(M.AuditLog).filter_by(action="EBARIMT_REISSUE_SUMMARY").count() == 2
    # Дахин ажиллуулахад — нэр дэвшигч алга (идемпотент)
    capsys.readouterr()
    assert cli.main(base + ["--apply", "--yes", "--user", "temuujin"]) == 0
    assert "Дахин гаргах төлбөр алга" in capsys.readouterr().out
    assert db.query(M.FinancialJob).count() == 2
    # Дахин гаргасан баримт SENT болсны дараа --pos-app ДАХИН цуцлахгүй (REISSUED)
    for k in ("ext_miss", "dup_miss"):
        r = [x for x in _recs(db, s[k].id) if x.status == "PENDING"][0]
        r.status, r.ebarimt_id, r.provider_ref = "SENT", ddtd(400 + len(k), MSG), f"rcp_re_{k}"
    db.commit()
    scope = VD.pos_app_scope(db, [w.site])
    lo, hi = VD.ub_window("2026-09-01", "2026-09-30")
    sel = VD.select_pos_app(db, sites=[w.site], lo=lo, hi=hi, scope=scope)
    assert not {s["ext_miss"].id, s["dup_miss"].id} & {r["payment_id"] for r in sel["selected"]}
    assert sel["excluded"]["REISSUED"] == 2


def test_cli_guards_no_terminal_file_and_wrong_tenant(db, engine, tmp_path, monkeypatch, capsys):
    cli = _tool("vat_reissue_missing.py")
    monkeypatch.setattr(cli, "SessionLocal", sessionmaker(bind=engine, autoflush=False))
    w = world(db)
    s = scenario(db, w)
    f = _xlsx(str(tmp_path / "noterm.xlsx"), export(s, terminal=()))
    base = ["--file", f, "--tenant", "EASYPARKING", "--out", str(tmp_path / "x.csv"), "--delay", "0"]
    assert cli.main(base) == 0
    text = capsys.readouterr().out
    assert "ТЕРМИНАЛЫН (Ontime) МӨР ОГТ АЛГА" in text
    assert "ДАХИН ГАРГАХ (ТЕГ-т терминалын мөр алга): 4 төлбөр" in text
    assert cli.main(base + ["--apply", "--yes", "--user", "t"]) == 8              # нэмэлт баталгаа шаардана
    assert db.query(M.FinancialJob).count() == 0
    assert cli.main(base + ["--apply", "--yes", "--user", "t", "--accept-no-terminal-file"]) == 0
    assert db.query(M.FinancialJob).count() == 4
    # Өөр түрээслэгч (Моннис) — EP-ийн файл нотолгоо биш → зогсоно
    f2 = _xlsx(str(tmp_path / "ep.xlsx"), export(s))
    assert cli.main(["--file", f2, "--tenant", "MONNIS", "--out", str(tmp_path / "m.csv")]) == 7
    # --tenant ALL — EP-ийн файл зөвхөн EP-ийг хамарна
    capsys.readouterr()
    assert cli.main(["--file", f2, "--tenant", "ALL", "--out", str(tmp_path / "a.csv")]) == 0
    assert "түрээслэгч EASYPARKING" in capsys.readouterr().out


def test_ui_terminal_missing_not_fooled_by_shared_ontime_register(db):
    """UI-ийн «POS терминал гаргасан ч ТЕГ-т баримт алга» (reconcile_db → terminal) нь CLI-тэй нэг
    хосолт: олон зогсоол НЭГ Ontime кассаар (…10002990) ажиллах үед олонхын зогсоолын QR
    төлбөр нөгөө зогсоолын POS аппын борлуулалтаас Ontime мөрийг булааж «алга» гэж худал
    харуулахгүй (хуучин vat_recon.terminal_check-ийн «кассын зогсоол» эрэмбэ булаадаг)."""
    from app.routers import vat_recon as VR
    w = world(db)
    s = scenario(db, w)
    yl = M.ParkingSite(name="ялалт", site_code="YLALT1", tenant_id=w.ep.id)
    db.add(yl)
    db.flush()
    t = T0 + timedelta(minutes=45)
    rows = export(s, terminal=())
    for i in range(3):                                   # олонх — Ялалтын POS аппын борлуулалт
        p = payment(db, w, t + timedelta(minutes=2 + i), 1700 + i, site=yl, receipts=[EXT])
        rows.append({"dt": p.paid_at + timedelta(seconds=3), "amount": float(p.amount),
                     "ddtd": ddtd(310 + i, ONT), "src": "Ontime"})
    h = payment(db, w, t, 2500, receipts=[EXT])           # Хангарьд — терминал хэвлэсэн
    rows.append({"dt": t + timedelta(seconds=4), "amount": 2500.0, "ddtd": ddtd(320, ONT), "src": "Ontime"})
    payment(db, w, t + timedelta(seconds=2), 2500, method="QR", user=w.web, site=yl)   # Ялалтын QR
    db.commit()
    scope = {w.site.id, yl.id}
    a = VD.reconcile_db(db, rows, shifts=[0.0, -8.0], tol=60, ours_sites=list(scope),
                        probe_sites=list(scope), scope_ids=scope)
    missing = {m["payment_id"] for m in a["terminal"]["missing"]}
    assert h.id not in missing
    assert {s["ext_miss"].id, s["dup_ok"].id} <= missing            # Ontime мөргүй — жинхэнэ «алга»
    # explain-ийн хос ч (POS апп түрүүлж) Ялалтын QR биш Хангарьдын борлуулалт руу
    [row] = [x for x in a["explained"] if x["foreign"] and x["amount"] == 2500.0]
    assert row["payment_id"] == h.id
    assert h.id not in {m["payment_id"] for m in VR.terminal_check(
        rows, a["explained"], a["probe"], 60, a["r"]["shift"], scope)["missing"]}


def test_file_mode_never_cancels_web_cash_of_majority_site(db):
    """--file/UI-ийн давхардлын илрүүлэлт: нэг Ontime кассаар олон зогсоол ажиллах үед олонхын
    зогсоолын ижил дүнтэй ВЕБ КАССЫН бэлэн (msgbill-ийн цорын ганц баримт) нөгөө зогсоолын POS
    аппын борлуулалтаас Ontime мөрийг булааж «DUPLICATE» болохгүй (2026-10-14: POS апп түрүүнд).
    2026-10-15: кассын зогсоолыг ЗОГСООЛ × ӨДРӨӨР тогтооно — Хангарьдын POS аппын борлуулалт ч
    тэр өдөр терминалаар хэвлэсэн тул түүний ЖИНХЭНЭ давхардал (Ontime + манай msgbill хоёулаа
    ТЕГ-т) «өөр зогсоол» биш DUPLICATE (өмнө нь олонх = Ялалт → цуцлагдахгүй байв)."""
    w = world(db)
    s = scenario(db, w)
    yl = M.ParkingSite(name="ялалт", site_code="YLALT1", tenant_id=w.ep.id)
    db.add(yl)
    db.flush()
    t = T0 + timedelta(minutes=45)
    rows = export(s, terminal=())
    for i in range(3):                                   # олонх — Ялалтын POS аппын борлуулалт
        p = payment(db, w, t + timedelta(minutes=2 + i), 1700 + i, site=yl, receipts=[sent(330 + i)])
        rows.append({"dt": p.paid_at + timedelta(seconds=3), "amount": float(p.amount),
                     "ddtd": ddtd(310 + i, ONT), "src": "Ontime"})
        rows.append({"dt": p.paid_at + timedelta(hours=8, seconds=2), "amount": float(p.amount),
                     "ddtd": ddtd(330 + i, MSG), "src": "msgbill"})
    h = payment(db, w, t, 2500, receipts=[sent(340)])                   # Хангарьд POS апп (давхар)
    rows.append({"dt": t + timedelta(seconds=4), "amount": 2500.0, "ddtd": ddtd(320, ONT), "src": "Ontime"})
    rows.append({"dt": t + timedelta(hours=8, seconds=2), "amount": 2500.0, "ddtd": ddtd(340, MSG),
                 "src": "msgbill"})
    web = payment(db, w, t + timedelta(seconds=2), 2500, method="CASH", user=w.web, site=yl,
                  receipts=[sent(341)])                                 # Ялалтын веб касс — ганц баримт
    rows.append({"dt": web.paid_at + timedelta(hours=8, seconds=2), "amount": 2500.0,
                 "ddtd": ddtd(341, MSG), "src": "msgbill"})
    db.commit()
    scope = {w.site.id, yl.id}
    a = VD.reconcile_db(db, rows, shifts=[0.0, -8.0], tol=60, ours_sites=list(scope),
                        probe_sites=list(scope), scope_ids=scope)
    dup_ids = {d["payment_id"] for d in a["duplicates"]}
    assert web.id not in dup_ids
    [row] = [x for x in a["explained"] if x["foreign"] and x["amount"] == 2500.0]
    assert row["payment_id"] == h.id and row["verdict"] == "DUPLICATE"
    assert h.id in dup_ids and {p["site_id"] for p in a["probe"] if p["id"] in dup_ids} == {w.site.id, yl.id}
    rs = a["register_sites"][ONT]
    assert rs["sites"] == 2 and rs["allow"] == sorted([f"{w.site.id}|2026-09-20", f"{yl.id}|2026-09-20"])


# ───────── 2026-10-15: прод дахин гаргалт ДАВХАРДАЛГҮЙ (ТЕГ-ийн 9/10-р сарын бодит экспорт) ─────────

def _ub(p, s=3):
    """Манай msgbill мөр — ТЕГ экспортод УБ локал цагаар."""
    return p.paid_at + timedelta(hours=8, seconds=s)


def own_world(db, w, s):
    """Терминалын мөргүй нэр дэвшигчид — МАНАЙ баримт ТЕГ экспортод бий/алга:
    dup_miss — цуцалсан баримт (…000002) экспортод ҮЛДСЭН (ДДТД-ээр);
    twin — манайх (…000020) экспортод алга, msgbill-ийн ДАВХАР баримт (…000021, манайд бүртгэлгүй) бий;
    old — 09-07-ноос өмнөх msgbill-ийн БАГЦЫН дугаар (0291…) — ТЕГ-т өөр ДДТД (цаг+дүнгээр);
    gone — цуцалсан баримт экспортод алга, давхар ч алга → ДАХИН ГАРГАНА;
    web/b — веб кассын (идэвхтэй) баримтын мөр b-ийн төлбөрт ЦАГААР ойрхон (5с vs 25с), дугаар нь b-ийн
    цуцалсан баримтаас 1-ээр зөрүүтэй ч ДДТД нь веб кассынх → ДДТД түрүүнд: b ДАХИН ГАРГАНА."""
    x = {"rows": []}
    x["twin"] = payment(db, w, T0 + timedelta(minutes=55), 5500, receipts=[dup(20)])
    x["rows"].append({"dt": _ub(x["twin"], 2), "amount": 5500.0, "ddtd": ddtd(21, MSG), "src": "msgbill"})
    x["old"] = payment(db, w, T0 + timedelta(minutes=65), 6500, receipts=[(
        "MSGBILL", "CANCELLED", {"ebarimt_id": "02910024410600109722%05d" % 7 + MSG, "provider_ref": "rcp_old",
                                 "ddtd_note": f"{DUPLICATE_OF_POS}: x",
                                 "raw": {"cancel": {"reason": DUPLICATE_OF_POS}}})])
    x["rows"].append({"dt": _ub(x["old"], 2), "amount": 6500.0, "ddtd": ddtd(30, MSG), "src": "msgbill"})
    x["gone"] = payment(db, w, T0 + timedelta(minutes=75), 7300, receipts=[dup(50)])
    x["web"] = payment(db, w, T0 + timedelta(minutes=80), 7700, method="CASH", user=w.web, receipts=[sent(40)])
    x["b"] = payment(db, w, x["web"].paid_at + timedelta(seconds=20), 7700, receipts=[dup(41)])
    x["rows"].append({"dt": _ub(x["web"], 25), "amount": 7700.0, "ddtd": ddtd(40, MSG), "src": "msgbill"})
    x["rows"].append({"dt": _ub(s["dup_miss"]), "amount": 5000.0, "ddtd": ddtd(2, MSG), "src": "msgbill"})
    db.commit()
    return x


def test_own_receipt_still_in_teg_is_never_reissued(db):
    """10-15-ны экспортод манай 1,680 борлуулалтын (6.71 сая₮) баримт — идэвхтэй, эсвэл 10-14-нд
    цуцалсан ч экспортод ҮЛДСЭН, эсвэл msgbill-ийн давхар баримт — бий байхад vat_reissue_missing
    тэдгээрийг «терминалын мөргүй» → ДАХИН гаргах байв (ТЕГ-т 2 баримт). ДДТД-ээр түрүүлж, 0291…
    багцын дугаартайг цаг+дүнгээр, давхар баримтыг ДДТД-ийн дарааллаар таньж OWN_IN_TEG болгоно."""
    w = world(db)
    s = scenario(db, w)
    x = own_world(db, w, s)
    ev = evidence(db, w, export(s) + x["rows"])
    own = ev["own_present"]
    assert own[s["dup_miss"].id] == "DDTD" and own[x["web"].id] == "DDTD"
    assert own[x["twin"].id] == "TWIN" and own[x["old"].id] == "TIME"
    assert x["b"].id not in own and x["gone"].id not in own and s["ext_miss"].id not in own
    sel = VD.select_reissue(db, sites=[w.site], lo=T0 - timedelta(days=1), hi=T0 + timedelta(days=5))
    split = VD.evidence_split(sel["candidates"], ev, {w.site.id: w.ep.id})
    ids = {k: {r["payment_id"] for r in v} for k, v in split.items()}
    assert ids["own_in_teg"] == {s["dup_miss"].id, x["twin"].id, x["old"].id}
    assert ids["missing"] == {s["ext_miss"].id, x["gone"].id, x["b"].id}
    assert ids["with_terminal"] == {s["ext_ok"].id, s["dup_ok"].id} and not ids["ambiguous"]
    assert {r["own_teg"] for r in split["own_in_teg"]} == {"DDTD", "TWIN", "TIME"}


def test_twin_goes_to_receipt_created_nearest_when_numbers_interleave(db):
    """3HOSPITAL 09-25: ижил дүнтэй баримт дараалж гарахад манайх …23, 25, 27, давхар нь …24, 26 —
    давхар мөр (…26) дугаараар 25, 27 хоёуланд 1 зөрүүтэй. Баримт ҮҮССЭН цагаар хамгийн ойрд оноохгүй
    бол 27-ийн борлуулалт (баримт нь ТЕГ-т алга, давхар нь бий) ДАХИН гаргагдаж давхардана."""
    w = world(db)
    s = scenario(db, w)
    a = payment(db, w, T0 + timedelta(minutes=75), 1000, receipts=[dup(25)])
    b = payment(db, w, T0 + timedelta(minutes=77), 1000, receipts=[dup(27)])
    db.flush()
    for p in (a, b):
        for r in db.query(M.VatReceipt).filter_by(payment_id=p.id):
            r.created_at = p.paid_at
    db.commit()
    rows = export(s) + [{"dt": _ub(a, 1), "amount": 1000.0, "ddtd": ddtd(25, MSG), "src": "msgbill"},
                        {"dt": _ub(b, 2), "amount": 1000.0, "ddtd": ddtd(26, MSG), "src": "msgbill"}]
    ev = evidence(db, w, rows)
    assert ev["own_present"][a.id] == "DDTD" and ev["own_present"][b.id] == "TWIN"


def test_site_day_terminal_usage_gates_ambiguous_pairing_and_file_duplicates(db):
    """Нэг Ontime касс 5 зогсоолд: 3HOSPITAL огт терминалгүй (≤2% санамсаргүй хос) ч бусдын ХОСГҮЙ
    мөрөөс болж 517 борлуулалт нь ТОДОРХОЙГҮЙ болж байв. Одоо:
    (а) хосгүй мөр зөвхөн ТЭР ӨДӨР терминалаар хэвлэсэн зогсоолд ТОДОРХОЙГҮЙ (Хангарьд h_amb), 3HOSPITAL-д
        терминалын мөргүй (near_miss_ignored);
    (б) мөр 3HOSPITAL-ийн y-д (2с) илүү ойр ч тэр өдөр терминалтай Хангарьдын x-тэй (8с) хосолно; y нь
        маргаантай → ТОДОРХОЙГҮЙ (дахин гаргахгүй);
    (в) --file/UI: 3HOSPITAL-ийн z (манай msgbill + Ontime мөр) санамсаргүй тааралт — «тэр өдөр
        терминалгүй» → цуцлахгүй."""
    from app.routers import vat_recon as VR
    w = world(db)
    s = scenario(db, w)
    hos = M.ParkingSite(name="3-р эмнэлэг", site_code="3HOSPITAL", tenant_id=w.ep.id)
    db.add(hos)
    db.flush()
    # 3HOSPITAL-ийн өдрийн ~300 борлуулалтын жижиг хувилбар — санамсаргүй 2 хос нь <10%
    hs = [payment(db, w, T0 + timedelta(minutes=5 + 2 * i), 1000, site=hos, receipts=[EXT]) for i in range(40)]
    rows = export(s)
    h_amb = payment(db, w, T0 + timedelta(minutes=33), 1000, receipts=[EXT])
    rows.append({"dt": h_amb.paid_at - timedelta(seconds=30), "amount": 1000.0, "ddtd": ddtd(350, ONT),
                 "src": "Ontime"})
    x = payment(db, w, T0 + timedelta(minutes=61), 2000, receipts=[EXT])
    y = payment(db, w, x.paid_at + timedelta(seconds=6), 2000, site=hos, receipts=[EXT])
    rows.append({"dt": x.paid_at + timedelta(seconds=8), "amount": 2000.0, "ddtd": ddtd(351, ONT), "src": "Ontime"})
    z = payment(db, w, T0 + timedelta(minutes=70), 2500, method="CASH", site=hos, receipts=[sent(360)])
    rows.append({"dt": z.paid_at + timedelta(seconds=3), "amount": 2500.0, "ddtd": ddtd(352, ONT), "src": "Ontime"})
    rows.append({"dt": _ub(z, 2), "amount": 2500.0, "ddtd": ddtd(360, MSG), "src": "msgbill"})
    db.commit()
    sites = VD.tenant_sites(db, [w.ep])
    ev = VD.terminal_evidence(db, [("teg.xlsx", rows)], sites=sites, tol=60, shifts=[0.0, -8.0])
    day = "2026-09-20"
    assert (w.site.id, day) in ev["terminal_days"] and (hos.id, day) not in ev["near_days"]
    assert x.id in ev["terminal_ids"] and y.id not in ev["terminal_ids"] and y.id in ev["contested"]
    sel = VD.select_reissue(db, sites=sites, lo=T0 - timedelta(days=1), hi=T0 + timedelta(days=1))
    split = VD.evidence_split(sel["candidates"], ev, {p.id: w.ep.id for p in sites})
    ids = {k: {r["payment_id"] for r in v} for k, v in split.items()}
    assert ids["ambiguous"] == {h_amb.id, y.id}
    assert {p.id for p in hs} <= ids["missing"] and x.id in ids["with_terminal"]
    ignored = {r["payment_id"] for r in split["missing"] if r.get("near_miss_ignored")}
    assert ignored and ignored <= {p.id for p in hs}
    # (в) --file/UI-ийн давхардал: 3HOSPITAL тэр өдөр терминалгүй → санамсаргүй тааралт цуцлагдахгүй
    ids_ = [p.id for p in sites]
    a = VD.reconcile_db(db, rows, shifts=[0.0, -8.0], tol=60, ours_sites=ids_, probe_sites=ids_,
                        scope_ids=set(ids_))
    [row] = [r for r in a["explained"] if r["foreign"] and r["amount"] == 2500.0]
    assert row["payment_id"] == z.id and row["verdict"] == "DUPLICATE_NOT_CANCELLABLE"
    assert "тэр өдөр терминалаар" in row["note"] and z.id not in {d["payment_id"] for d in a["duplicates"]}
    rs = a["register_sites"][ONT]
    assert rs["allow"] == [f"{w.site.id}|{day}"] and VR.register_allows(rs, {"site_id": hos.id,
                                                                              "paid_at": z.paid_at}) is False


def test_chunk_with_low_own_share_is_covered_by_file_level_share(db):
    """9-р сарын файлын 09-01..02 хэсэг: QPay олноор нь дахин илгээсэн мөрөөс болж хэсгийн манай
    кассын хувь 45% (<50%) → тэр 2 хоног нотолгоонд ОГТ орохгүй (хүрээнээс гадуур) байв. Түрээслэгчийн
    батламжийг ФАЙЛЫН нийт мөрөөр (58%) — тэр хоног хамрагдана."""
    w = world(db)
    rows = []
    for i in range(2):
        p = payment(db, w, T0 + timedelta(minutes=10 * i), 1100 + i, method="CASH", user=w.web, receipts=[sent(800 + i)])
        rows.append({"dt": _ub(p), "amount": float(p.amount), "ddtd": ddtd(800 + i, MSG), "src": "msgbill"})
        q = payment(db, w, T0 + timedelta(minutes=5 + 10 * i), 900 + i, method="QR", user=w.web,
                    receipts=[sent(820 + i, prov="QPAY", reg=QPY)])
        rows.append({"dt": q.paid_at + timedelta(seconds=4), "amount": float(q.amount), "ddtd": ddtd(820 + i, QPY),
                     "src": "QPay"})
    for i in range(10):                                  # QPay-ийн бөөн дахин илгээлт — манайд таарахгүй
        rows.append({"dt": T0 + timedelta(minutes=30 + i), "amount": 777.0 + i, "ddtd": ddtd(840 + i, QPY),
                     "src": "QPay"})
    e1 = payment(db, w, T0 + timedelta(minutes=20), 2000, receipts=[EXT])
    rows.append({"dt": e1.paid_at + timedelta(seconds=5), "amount": 2000.0, "ddtd": ddtd(860, ONT), "src": "Ontime"})
    e2 = payment(db, w, T0 + timedelta(minutes=40), 3000, receipts=[EXT])
    for i in range(10):
        p = payment(db, w, T0 + timedelta(days=1, minutes=10 * i), 1200 + i, method="CASH", user=w.web,
                    receipts=[sent(870 + i)])
        rows.append({"dt": _ub(p), "amount": float(p.amount), "ddtd": ddtd(870 + i, MSG), "src": "msgbill"})
    e3 = payment(db, w, T0 + timedelta(days=1, minutes=45), 2200, receipts=[EXT])
    rows.append({"dt": e3.paid_at + timedelta(seconds=5), "amount": 2200.0, "ddtd": ddtd(890, ONT), "src": "Ontime"})
    db.commit()
    ev = VD.terminal_evidence(db, [("sep.xlsx", rows)], sites=VD.tenant_sites(db, [w.ep]), tol=60,
                              shifts=[0.0, -8.0], chunk_days=1)
    [st] = ev["files"]
    assert st["chunks"] == 2 and st["low_share_chunks"] == 1 and st["unusable_chunks"] == 0
    assert "ФАЙЛЫН түвшинд 58%" in st["note"] and st["usable"]
    sel = VD.select_reissue(db, sites=[w.site], lo=T0 - timedelta(days=1), hi=T0 + timedelta(days=3))
    split = VD.evidence_split(sel["candidates"], ev, {w.site.id: w.ep.id})
    ids = {k: {r["payment_id"] for r in v} for k, v in split.items()}
    assert {e1.id, e3.id} <= ids["with_terminal"] and e2.id in ids["missing"] and not ids["outside"]
    # Файлын нийт хувь ч бага бол (14/44 — дахин илгээлт давамгайлсан) — НОТОЛГОО БИШ
    more = [{"dt": T0 + timedelta(minutes=45, seconds=30 * i), "amount": 555.0 + i, "ddtd": ddtd(900 + i, QPY),
             "src": "QPay"} for i in range(20)]
    ev2 = VD.terminal_evidence(db, [("sep.xlsx", rows + more)], sites=VD.tenant_sites(db, [w.ep]), tol=60,
                               shifts=[0.0, -8.0], chunk_days=1)
    assert ev2["coverage"] == [] and "батлагдсангүй" in ev2["files"][0]["note"]


def test_cli_own_in_teg_flag_and_max_hint(db, engine, tmp_path, monkeypatch, capsys):
    cli = _tool("vat_reissue_missing.py")
    monkeypatch.setattr(cli, "SessionLocal", sessionmaker(bind=engine, autoflush=False))
    w = world(db)
    s = scenario(db, w)
    x = own_world(db, w, s)
    f = _xlsx(str(tmp_path / "teg.xlsx"), export(s) + x["rows"])
    out = str(tmp_path / "r.csv")
    base = ["--file", f, "--tenant", "EASYPARKING", "--out", out, "--delay", "0"]
    assert cli.main(base) == 0
    text = capsys.readouterr().out
    assert "ДАХИН ГАРГАХ (ТЕГ-т терминалын мөр алга): 3 төлбөр" in text
    assert "МАНАЙ баримт ТЕГ-т бий 3" in text and "--include-own-in-teg-гүйгээр гаргахгүй: 3" in text
    assert "ТЕРМИНАЛ АШИГЛАСАН ӨДӨР" in text and "HANGARID: хосолсон 2/" in text
    import csv as _csv
    allrows = list(_csv.DictReader(open(str(tmp_path / "r_all.csv"), encoding="utf-8-sig")))
    own = {r["payment_id"]: r["own_teg"] for r in allrows if r["verdict"] == "OWN_IN_TEG"}
    assert own == {s["dup_miss"].id: "DDTD", x["twin"].id: "TWIN", x["old"].id: "TIME"}
    # --max: dry-run сануулна, --apply нь яг ямар тугаар үргэлжлүүлэхийг заагаад зогсоно
    assert cli.main(base + ["--max", "2"]) == 0
    assert "--max: 3 төлбөр > --max 2" in capsys.readouterr().out
    assert cli.main(base + ["--apply", "--yes", "--user", "t", "--max", "2"]) == 2
    text = capsys.readouterr().out
    assert "ЗОГСОВ: 3 төлбөр > --max 2" in text and "--apply-д --max 3 нэмнэ" in text
    assert db.query(M.FinancialJob).count() == 0
    assert cli.main(base + ["--include-own-in-teg"]) == 0
    assert "+ МАНАЙ баримт ТЕГ-т бий): 6 төлбөр" in capsys.readouterr().out
    # Бодит: зөвхөн ТЕГ-т ямар ч баримтгүй 3 — манай баримт ТЕГ-т байгааг ХӨНДӨХГҮЙ
    assert cli.main(base + ["--apply", "--yes", "--user", "temuujin"]) == 0
    jobs = {j.payment_id for j in db.query(M.FinancialJob).all()}
    assert jobs == {s["ext_miss"].id, x["gone"].id, x["b"].id}
    summ = db.query(M.AuditLog).filter_by(action="EBARIMT_REISSUE_SUMMARY").one()
    assert summ.detail["include_own_in_teg"] is False and summ.detail["requested"] == 3


def test_file_duplicate_requires_our_ddtd_not_just_time_match(db):
    """--file/UI: «манай баримт ч ТЕГ файлд бий» (давхардлын шууд нотолгоо) нь ДДТД-ээр. audit1014 ×
    10-15-ны экспорт: 41 «давхардлын» 25-д манай баримт экспортод огт алга, зөвхөн цаг+дүнгээр өөр
    (давхар/бусдын) msgbill мөртэй таарсан байв — цуцалбал тэр борлуулалтын ЦОРЫН ГАНЦ баримт болно."""
    w = world(db)
    s = scenario(db, w)
    rows = export(s)
    real = payment(db, w, T0 + timedelta(minutes=72), 2700, method="CASH", receipts=[sent(370)])
    fake = payment(db, w, T0 + timedelta(minutes=76), 2900, method="CASH", receipts=[sent(380)])
    for p, msg in ((real, ddtd(370, MSG)), (fake, ddtd(381, MSG))):      # fake: өөр ДДТД (цагаар таарна)
        rows.append({"dt": p.paid_at + timedelta(seconds=4), "amount": float(p.amount),
                     "ddtd": ddtd(390 + int(p.amount) // 100, ONT), "src": "Ontime"})
        rows.append({"dt": _ub(p, 2), "amount": float(p.amount), "ddtd": msg, "src": "msgbill"})
    db.commit()
    a = VD.reconcile_db(db, rows, shifts=[0.0, -8.0], tol=60, ours_sites=[w.site.id],
                        probe_sites=[w.site.id], scope_ids={w.site.id})
    assert real.id in a["matched_pay_ids"] and fake.id not in a["matched_pay_ids"]
    v = {r["payment_id"]: r for r in a["explained"] if r["foreign"]}
    assert v[real.id]["verdict"] == "DUPLICATE"
    assert v[fake.id]["verdict"] == "DUPLICATE_NOT_CANCELLABLE" and "олдсонгүй" in v[fake.id]["note"]
    assert {d["payment_id"] for d in a["duplicates"]} == {real.id}
