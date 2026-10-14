"""Хяналт 2 (rev3) — бие даасан хянагчийн 5 сорил (A, A2, B, C, D) ЗАССАН зан төлөвийг шалгана.

A/A2: UI-ийн «Систем баримт гаргах» (no_terminal_receipt) ба «давхардлыг зөвшөөрөх»
(allow_duplicate) дахин гаргалт REISSUED_NO_TERMINAL тэмдэгтэй — дараагийн --pos-app ДАХИН
цуцлахгүй. B: 10-14 04:48 UTC-ээс хойшхи төлбөрийг нэрээр биш source тамгаар таньна — POS аппын
хэрэглэгчийн ВЕБ кассын бэлэн (цорын ганц msgbill баримт) цуцлагдахгүй. C: терминалын кассын
цагийн бүсээр хамрах хүрээ — файлын эхний өдрийн УБ 00:00–08:00 «терминалын мөргүй» биш OUTSIDE.
D: терминалын цаг системтэйгээр зөрвөл (мөр 15с ӨМНӨ) бүгд AMBIGUOUS — дахин гаргахгүй.
"""
import asyncio
from datetime import datetime, timedelta

from app import models as M
from app.routers import payments_router as PR
from app.services import financial_jobs as J, vat_duplicates as VD
from app.services.receipts import REISSUED_NO_TERMINAL

from test_vat_reissue_missing import (EXT, MSG, ONT, T0, ddtd, dup, env, engine, db,  # noqa: F401
                                      payment, sent, world)


def _recs(db, pid):
    db.expire_all()
    return db.query(M.VatReceipt).filter_by(payment_id=pid).all()


def _sel(db, w, sites=None, frm="2026-09-01", to="2026-09-30"):
    sites = sites or [w.site]
    scope = VD.pos_app_scope(db, sites)
    lo, hi = VD.ub_window(frm, to)
    return VD.select_pos_app(db, sites=sites, lo=lo, hi=hi, scope=scope)


def _admin(db):
    u = M.User(username="admin", password_hash="x", role="SUPER_ADMIN")
    db.add(u)
    db.commit()
    return u


def test_A_ui_reissue_is_marked_and_never_recancelled(db):
    w = world(db)
    admin = _admin(db)
    p = payment(db, w, T0, 2000, receipts=[EXT])
    db.commit()
    res = asyncio.run(PR.retry_ebarimt_endpoint(p.id, {"reissue_external": True,
                                                       "no_terminal_receipt": True}, db, admin))
    assert res.get("pending")
    [r] = [x for x in _recs(db, p.id) if x.status != "CANCELLED"]
    assert r.provider == "MSGBILL" and r.raw["reissue"]["reason"] == REISSUED_NO_TERMINAL
    assert r.raw["reissue"]["by"] == "admin" and r.raw["reissue"]["source"] == "ui"
    r.status, r.ebarimt_id, r.provider_ref = "SENT", ddtd(51, MSG), "rcp_51"   # worker delivered
    db.commit()
    sel = _sel(db, w)
    assert p.id not in {x["payment_id"] for x in sel["selected"]}
    assert sel["excluded"]["REISSUED"] == 1


def test_A2_ui_allow_duplicate_reissue_is_marked_and_never_recancelled(db):
    w = world(db)
    admin = _admin(db)
    p = payment(db, w, T0, 2000, receipts=[dup(61, rule=VD.POS_APP_RULE)])
    db.commit()
    res = asyncio.run(PR.retry_ebarimt_endpoint(p.id, {"allow_duplicate": True,
                                                       "no_terminal_receipt": True}, db, admin))
    assert res.get("pending")
    new = [x for x in _recs(db, p.id) if x.status != "CANCELLED"]
    assert len(new) == 1 and new[0].raw["reissue"]["reason"] == REISSUED_NO_TERMINAL
    new[0].status, new[0].ebarimt_id, new[0].provider_ref = "SENT", ddtd(62, MSG), "rcp_62"
    db.commit()
    assert p.id not in {x["payment_id"] for x in _sel(db, w)["selected"]}
    # «Дахин үүсгэх» дүрмээр EXTERNAL болсон мөр (терминалын бүртгэл) — тэмдэглэхгүй
    q = payment(db, w, T0 + timedelta(minutes=5), 2100, receipts=[dup(63, rule=VD.POS_APP_RULE)])
    db.commit()
    out = asyncio.run(PR.retry_ebarimt_endpoint(q.id, {"allow_duplicate": True}, db, admin))
    assert out.get("created") and out.get("external")
    [e] = [x for x in _recs(db, q.id) if x.status != "CANCELLED"]
    assert e.status == "EXTERNAL" and "reissue" not in (e.raw or {})


def test_B_post_deploy_browser_cash_of_pos_user_is_not_selected(db):
    """Тамга найдвартай болсноос хойш (10-14 04:48 UTC) нэрээр таахгүй: POS аппын хэрэглэгчийн
    веб кассын бэлэн (source NULL, msgbill цорын ганц баримт) ЦУЦЛАГДАХГҮЙ; түүнээс өмнөх
    (тамгагүй түүхэн) бэлэн — нэрээр (хуучин дүрэм)."""
    w = world(db)
    after = payment(db, w, datetime(2026, 10, 14, 6, 0), 1500, method="CASH", user=w.pos,
                    receipts=[sent(71)])
    after.source = None                                   # веб касс (хөтөч) — продын хэлбэр
    stamped = payment(db, w, datetime(2026, 10, 14, 6, 5), 1600, method="CASH", user=w.pos,
                      receipts=[sent(72)])                # POS апп (толгойтой) — POSAPP
    before = payment(db, w, T0, 1700, method="CASH", user=w.pos, receipts=[sent(73)])
    before.source = None                                  # 9-р сар — тамгагүй түүх
    db.commit()
    assert not J.terminal_issues_receipt(db, after)       # runtime: систем гаргана (зөв)
    sel = _sel(db, w, frm="2026-10-14", to="2026-10-15")
    ids = {x["payment_id"] for x in sel["selected"]}
    assert after.id not in ids and stamped.id in ids and sel["excluded"]["NOT_POS_APP"] == 1
    [row] = [x for x in _sel(db, w)["selected"] if x["payment_id"] == before.id]
    assert row["via"] == "user=pos.k"
    # Тулгалтын POS аппын эрэмбэ (payment_probe.pos_app) ч мөн дүрмээр
    probe = {p["id"]: p for p in VD.load_probe(db, datetime(2026, 9, 1), datetime(2026, 10, 15), None)}
    assert probe[after.id]["pos_app"] is False and probe[before.id]["pos_app"] is True


def _web(db, w, at, amount, seq):
    return payment(db, w, at, amount, method="CASH", user=w.web, receipts=[sent(seq)])


def test_C_file_start_display_date_skew_is_outside_not_missing(db):
    w = world(db)
    D = datetime(2026, 9, 21)                             # экспортын харагдах огноо
    ub = timedelta(hours=8)
    rows = []
    for i, ub_t in enumerate([D + timedelta(minutes=30), D + timedelta(hours=11),
                              D + timedelta(hours=12), D + timedelta(hours=13),
                              D + timedelta(hours=14), D + timedelta(hours=20)]):
        p = _web(db, w, ub_t - ub, 1100 + i, 800 + i)
        rows.append({"dt": ub_t + timedelta(seconds=3), "amount": float(p.amount),
                     "ddtd": ddtd(800 + i, MSG), "src": "msgbill"})
    night = payment(db, w, D + timedelta(hours=2) - ub, 3000, receipts=[EXT])
    day = []
    for i in range(5):
        p = payment(db, w, D + timedelta(hours=3, minutes=10 * i), 2000 + i, receipts=[EXT])
        day.append(p)
        rows.append({"dt": p.paid_at + timedelta(seconds=2), "amount": float(p.amount),
                     "ddtd": ddtd(300 + i, ONT), "src": "Ontime"})
    db.commit()
    assert all(r["dt"].date() == D.date() for r in rows)
    ev = VD.terminal_evidence(db, [("teg_D.xlsx", rows)], sites=VD.tenant_sites(db, [w.ep]),
                              tol=60, shifts=[0.0, -8.0])
    sel = VD.select_reissue(db, sites=[w.site], lo=D - timedelta(days=2), hi=D + timedelta(days=2))
    split = VD.evidence_split(sel["candidates"], ev, {w.site.id: w.ep.id})
    assert {p.id for p in day} == {r["payment_id"] for r in split["with_terminal"]}
    assert night.id in {r["payment_id"] for r in split["outside"]}           # ЗАССАН: OUTSIDE
    assert not split["missing"] and not split["ambiguous"]
    # UI-ийн «ТЕГ-т баримт алга» ч мөн терминалын кассын хүрээгээр
    a = VD.reconcile_db(db, rows, shifts=[0.0, -8.0], tol=60, ours_sites=[w.site.id],
                        probe_sites=[w.site.id], scope_ids={w.site.id})
    assert night.id not in {m["payment_id"] for m in a["terminal"]["missing"]}


def test_D_systematic_offset_is_ambiguous_not_reissued(db):
    w = world(db)
    rows = []
    for i in range(4):
        p = _web(db, w, T0 + timedelta(minutes=5 * i), 1100 + i, 900 + i)
        rows.append({"dt": p.paid_at + timedelta(hours=8, seconds=3), "amount": float(p.amount),
                     "ddtd": ddtd(900 + i, MSG), "src": "msgbill"})
    sales = []
    for i in range(6):
        p = payment(db, w, T0 + timedelta(minutes=2 + 3 * i), 2000 + 100 * i, receipts=[EXT])
        sales.append(p)
        rows.append({"dt": p.paid_at - timedelta(seconds=15), "amount": float(p.amount),
                     "ddtd": ddtd(310 + i, ONT), "src": "Ontime"})
    db.commit()
    ev = VD.terminal_evidence(db, [("teg.xlsx", rows)], sites=VD.tenant_sites(db, [w.ep]),
                              tol=60, shifts=[0.0, -8.0])
    st = ev["files"][0]
    sel = VD.select_reissue(db, sites=[w.site], lo=T0 - timedelta(days=1), hi=T0 + timedelta(days=1))
    split = VD.evidence_split(sel["candidates"], ev, {w.site.id: w.ep.id})
    assert st["usable"] and st["terminal_rows"] == 6 and st["terminal_matched"] == 0
    assert st["terminal_unpaired"] == 6 and len(ev["unpaired"]) == 6
    covered = {r["payment_id"] for r in split["covered"]}
    assert covered and covered <= {p.id for p in sales}
    assert not split["missing"]                                              # ЗАССАН: дахин гаргахгүй
    assert {r["payment_id"] for r in split["ambiguous"]} == covered


# ───────────── хяналт 2-ын MINOR засварууд ─────────────

def test_I_failed_reissued_receipt_is_retryable(db):
    """REISSUED баримт FAILED (суваг тохируулаагүй — BLOCKED, attempts 0) болбол давхардлын
    хамгаалалт (duplicate_guarded) хаахгүй, дүрмээр EXTERNAL болгохгүй — бөөн/ганц нөхөлт
    msgbill-ээр дахин илгээнэ."""
    w = world(db)
    p = payment(db, w, T0, 2000, receipts=[dup(81, rule=VD.POS_APP_RULE)])
    db.flush()
    rec = M.VatReceipt(payment_id=p.id, amount=p.amount, vat_amount=p.vat_amount, provider="MSGBILL",
                       status="FAILED", receipt_url="ValueError: Баримтын сувгийн тохиргоог шалгана уу",
                       raw={"reissue": {"reason": REISSUED_NO_TERMINAL, "by": "t"}})
    db.add(rec)
    db.flush()
    db.add(M.FinancialJob(job_key=f"receipt:{rec.id}", kind="RECEIPT", payment_id=p.id, receipt_id=rec.id,
                          status="BLOCKED", attempts=0, payload={"provider": "MSGBILL"}))
    db.commit()
    recs = _recs(db, p.id)
    assert J.duplicate_guarded(recs) is False
    out = J.retry_payment_receipts(db, db.get(M.Payment, p.id))       # бөөн нөхөлтийн дуудлага
    assert out.get("pending") and not out.get("duplicate_of_pos") and not out.get("external")
    db.expire_all()
    rec = db.get(M.VatReceipt, rec.id)
    job = db.query(M.FinancialJob).filter_by(receipt_id=rec.id).one()
    assert (rec.status, rec.provider, job.status) == ("PENDING", "MSGBILL", "PENDING")
    # Тэмдэггүй FAILED — хамгаалалт хэвээр
    q = payment(db, w, T0 + timedelta(minutes=3), 2100, receipts=[dup(82), ("MSGBILL", "FAILED", {})])
    db.commit()
    assert J.duplicate_guarded(_recs(db, q.id)) is True


def test_K_terminal_register_not_dropped_as_qpay_on_few_hits(db):
    """Цөөн (1–2) Ontime мөр санамсаргүйгээр QR төлбөртэй таарсан ч «QPay касс» гэж хасахгүй."""
    w = world(db)
    rows = []
    for i in range(4):
        p = _web(db, w, T0 + timedelta(minutes=10 * i), 1100 + i, 950 + i)
        rows.append({"dt": p.paid_at + timedelta(hours=8, seconds=3), "amount": float(p.amount),
                     "ddtd": ddtd(950 + i, MSG), "src": "msgbill"})
    sale = payment(db, w, T0 + timedelta(minutes=12), 2500, receipts=[EXT])
    payment(db, w, T0 + timedelta(minutes=12, seconds=2), 2500, method="QR", user=w.web)   # QR ойрхон
    rows.append({"dt": sale.paid_at + timedelta(seconds=3), "amount": 2500.0, "ddtd": ddtd(390, ONT),
                 "src": "Ontime"})
    db.commit()
    ev = VD.terminal_evidence(db, [("teg.xlsx", rows)], sites=VD.tenant_sites(db, [w.ep]),
                              tol=60, shifts=[0.0, -8.0])
    st = ev["files"][0]
    assert st["qr_registers"] == [] and ONT in st["terminal_registers"]
    assert sale.id in ev["terminal_ids"]


def test_L_pos_user_override_reaches_evidence_pool(db):
    """--pos-user (тамгагүй түүхэн бэлэн, картын түүхгүй хэрэглэгч) — тулгалтын POS аппын санд ч."""
    w = world(db)
    w.web.role = "OPERATOR"
    rows = []
    for i in range(4):
        p = _web(db, w, T0 + timedelta(minutes=10 * i), 1100 + i, 960 + i)
        rows.append({"dt": p.paid_at + timedelta(hours=8, seconds=3), "amount": float(p.amount),
                     "ddtd": ddtd(960 + i, MSG), "src": "msgbill"})
    cash = payment(db, w, T0 + timedelta(minutes=12), 2600, method="CASH", user=w.web, receipts=[sent(970)])
    cash.source = None
    rows.append({"dt": cash.paid_at + timedelta(hours=8, seconds=2), "amount": 2600.0,
                 "ddtd": ddtd(970, MSG), "src": "msgbill"})
    rows.append({"dt": cash.paid_at + timedelta(seconds=4), "amount": 2600.0, "ddtd": ddtd(391, ONT),
                 "src": "Ontime"})
    db.commit()
    kw = dict(sites=VD.tenant_sites(db, [w.ep]), tol=60, shifts=[0.0, -8.0])
    assert cash.id not in VD.terminal_evidence(db, [("t.xlsx", rows)], **kw)["terminal_ids"]
    assert cash.id in VD.terminal_evidence(db, [("t.xlsx", rows)], pos_users={"sogii"}, **kw)["terminal_ids"]


def test_H_all_global_converts_per_site_on_rows(db, engine, monkeypatch, capsys):
    import importlib.util
    import os
    from sqlalchemy.orm import sessionmaker
    from app.services import app_settings as A
    path = os.path.join(os.path.dirname(__file__), "..", "..", "tools", "pos_terminal_setting.py")
    spec = importlib.util.spec_from_file_location("pos_terminal_setting", path)
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    monkeypatch.setattr(tool, "SessionLocal", sessionmaker(bind=engine, autoflush=False))
    w = world(db)
    off = M.ParkingSite(name="Off", site_code="OFFSITE", tenant_id=w.ep.id)
    card_only = M.ParkingSite(name="Card", site_code="CARDONLY", tenant_id=w.ep.id)
    db.add_all([off, card_only])
    db.flush()
    for sid, vals in ((w.site.id, {"pos_terminal_issues": True, "pos_terminal_methods": "CARD,CASH",
                                   "pos_terminal_users": "pos.khangarid"}),
                      (w.mon_site.id, {"pos_terminal_issues": True, "pos_terminal_users": "Building"}),
                      (card_only.id, {"pos_terminal_issues": True, "pos_terminal_methods": "CARD"}),
                      (off.id, {"pos_terminal_issues": False})):
        A.set_site_rules(db, A.EBARIMT_KEY, sid, vals, "t")
    db.commit()
    A.invalidate_cache()
    with __import__("pytest").raises(SystemExit):
        tool.main(["--all", "--set", "off"])
    assert tool.main(["--all", "--set", "global"]) == 0                     # dry-run
    out = capsys.readouterr().out
    assert "Өөрчлөх зогсоол: 3" in out and "«—» → «pos.khangarid,Building»" in out   # site_code-оор
    A.invalidate_cache()
    assert A.get_site_overrides(db, A.EBARIMT_KEY)[w.site.id]["pos_terminal_issues"] is True
    assert tool.main(["--all", "--set", "global", "--apply", "--user", "temuujin"]) == 0
    db.expire_all()
    A.invalidate_cache()
    ov = A.get_site_overrides(db, A.EBARIMT_KEY)
    assert w.site.id not in ov and w.mon_site.id not in ov
    assert ov[card_only.id] == {"pos_terminal_methods": "CARD"}            # ялгаатай арга үлдэнэ
    assert ov[off.id] == {"pos_terminal_issues": False}                    # тодорхой OFF хөндөгдөхгүй
    assert A.get_rules(db, A.EBARIMT_KEY)["pos_terminal_users"] == "pos.khangarid,Building"
    assert A.pos_app_users(db) == {"pos.khangarid", "building"}
    # Ерөнхийг унтраавал хуучин ON зогсоолууд ч унтарна (өвлөнө)
    A.set_rules(db, A.EBARIMT_KEY, {"pos_terminal_issues": False}, "t")
    db.commit()
    A.invalidate_cache()
    assert A.pos_terminal_receipt_methods(db, w.site.id) == set()
    logs = db.query(M.AuditLog).filter_by(action="UPDATE", entity="payment_rules").all()
    assert len(logs) == 4 and {x.detail["_set"] for x in logs} == {"all-global"}
    assert tool.main(["--all", "--set", "global", "--apply", "--user", "temuujin"]) == 0   # идемпотент
    assert db.query(M.AuditLog).filter_by(action="UPDATE").count() == 4


def test_D_cli_ambiguous_not_reissued_without_flag(db, engine, tmp_path, monkeypatch, capsys):
    import importlib.util
    import os
    from sqlalchemy.orm import sessionmaker
    from test_vat_reissue_missing import _xlsx
    path = os.path.join(os.path.dirname(__file__), "..", "..", "tools", "vat_reissue_missing.py")
    spec = importlib.util.spec_from_file_location("vat_reissue_missing", path)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    monkeypatch.setattr(cli, "SessionLocal", sessionmaker(bind=engine, autoflush=False))
    w = world(db)
    rows = []
    for i in range(6):
        p = _web(db, w, T0 + timedelta(minutes=10 * i), 1100 + i, 980 + i)
        rows.append({"dt": p.paid_at + timedelta(hours=8, seconds=3), "amount": float(p.amount),
                     "ddtd": ddtd(980 + i, MSG), "src": "msgbill"})
    skew = payment(db, w, T0 + timedelta(minutes=12), 2700, receipts=[EXT])   # терминал 15с ӨМНӨ
    rows.append({"dt": skew.paid_at - timedelta(seconds=15), "amount": 2700.0, "ddtd": ddtd(392, ONT),
                 "src": "Ontime"})
    lost = payment(db, w, T0 + timedelta(minutes=22), 2800, receipts=[EXT])   # жинхэнэ «алга»
    ok = payment(db, w, T0 + timedelta(minutes=32), 2900, receipts=[EXT])
    rows.append({"dt": ok.paid_at + timedelta(seconds=3), "amount": 2900.0, "ddtd": ddtd(393, ONT),
                 "src": "Ontime"})
    db.commit()
    f = _xlsx(str(tmp_path / "teg.xlsx"), rows)
    base = ["--file", f, "--tenant", "EASYPARKING", "--out", str(tmp_path / "r.csv"), "--delay", "0"]
    assert cli.main(base) == 0
    text = capsys.readouterr().out
    assert "ХОСГҮЙ терминалын мөр 1" in text and "ТОДОРХОЙГҮЙ" in text
    assert "ДАХИН ГАРГАХ (ТЕГ-т терминалын мөр алга): 1 төлбөр · 2,800₮" in text
    assert "(хосгүй 1)" in text
    all_csv = (tmp_path / "r_all.csv").read_text(encoding="utf-8-sig")
    assert "AMBIGUOUS" in all_csv and skew.id in all_csv
    assert cli.main(base + ["--apply", "--yes", "--user", "t"]) == 0
    assert db.query(M.FinancialJob).filter_by(payment_id=lost.id).count() == 1
    assert db.query(M.FinancialJob).filter_by(payment_id=skew.id).count() == 0
    assert cli.main(base + ["--apply", "--yes", "--user", "t", "--include-ambiguous"]) == 0
    assert db.query(M.FinancialJob).filter_by(payment_id=skew.id).count() == 1
    assert db.query(M.FinancialJob).filter_by(payment_id=ok.id).count() == 0
