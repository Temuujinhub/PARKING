"""Терминал (Ontime) баримт ХЭВЛЭЭГҮЙ POS аппын борлуулалтад манай msgbill баримтыг ДАХИН гаргах.

Яагаад (2026-10-14): эзний шийдвэрээр POS аппын карт/бэлэн борлуулалтад систем баримт
гаргахгүй (EXTERNAL — «терминал гаргасан», ДДТД-гүй), түүхэн msgbill баримтыг «POS-ийн
давхардал» гэж цуцалсан (DUPLICATE_OF_POS_TERMINAL — `vat_cancel_duplicates --pos-app`).
Терминал тэр борлуулалтад баримт ХЭВЛЭЭГҮЙ бол (унтарсан, цаасгүй, Ontime салгасан —
08-25..09-19-д «Ontime салгасан» гэсэн тэмдэглэлтэй; нягтлангийн 9-р сарын ~13 сая₮
давхардал vs манай POS аппын msgbill ~18 сая₮) тэр борлуулалт ОДОО e-Barimt-ГҮЙ.

ТЕГ экспорт(ууд)-ын хамрах хүрээнд (хугацаа × түрээслэгч; `--pos-app --evidence`-тэй НЭГ
тулгалт: vat_duplicates.terminal_evidence — касс бүрийн цагийн бүс (msgbill УБ, Ontime/QPay
UTC), нэг-нэгээр, ±tol; файлын түрээслэгчийг манай кассын мөрөөр батална) байгаа:
  • EXTERNAL мөртэй, эсвэл манай баримт нь POS-ийн давхардал гэж цуцлагдсан,
  • өөр идэвхтэй баримтгүй (системийн SENT/PENDING/REVIEW/FAILED, ДДТД-тэй терминалын),
  • цуцлалт/терминалын мөр нь ТЕГ файлын Ontime мөрөөр хийгдээгүй (--file горимын
    teg_ddtd = терминал хэвлэсний нотолгоо — ХӨНДӨХГҮЙ)
төлбөрүүдээс ТЕГ-т терминалын мөр ТААРААГҮЙ-г олно.

АНХДАГЧ = DRY-RUN (READ ONLY): зогсоол × өдрөөр тоо/₮, CSV. --apply үед дараалан:
financial_jobs.retry_payment_receipts (reissue_external + allow_duplicate +
no_terminal_receipt — UI-ийн «Систем баримт гаргах»-тай нэг зам) → шинэ PENDING msgbill
ажил (outbox илгээнэ); шинэ баримтын raw.reissue = REISSUED_NO_TERMINAL_RECEIPT (дараагийн
`--pos-app` цуцлалт ХӨНДӨХГҮЙ); AuditLog EBARIMT_REISSUE_NO_TERMINAL (төлбөр бүрд) +
EBARIMT_REISSUE_SUMMARY. Идемпотент: дахин ажиллуулахад аль хэдийн гаргасныг (идэвхтэй
системийн баримттай) алгасна.

    cd /root/PARKING/backend
    venv/bin/python tools/vat_reissue_missing.py --file /root/teg_sep.xlsx --tenant EASYPARKING  # харах
    venv/bin/python tools/vat_reissue_missing.py --file a.xlsx --file b.xlsx --tenant ALL \\
        --out /root/reissue.csv                                                                # олон файл
    venv/bin/python tools/vat_reissue_missing.py --file /root/teg_sep.xlsx --tenant EASYPARKING \\
        --apply --limit 1 --user temuujin                                                      # туршилт

Хамгаалалт: --apply зөвхөн ажиллаж буй «parking» DB + прод сервер дээр (продын ХУУЛБАР
audit*/t_*, ТЕСТ сервер дээр татгалзана); msgbill MOCK/тест/түлхүүргүй үед ажиллахгүй;
--max (2000)-аас олон бол зогсоно; YES-ийн өмнө DB, сервер, түрээслэгч, тоог хэвлэнэ; TTY
дээр «YES» (том үсгээр) гэж баталгаажуулна (--yes-ээр алгасна); терминалын (Ontime) мөр
ОГТ алга файл (шүүж татсан байж болзошгүй) байвал --accept-no-terminal-file-гүйгээр
татгалзана. Мөнгө хөдлөхгүй — зөвхөн татварын баримт.
"""
import argparse
import getpass
import os
import sys
import time
from collections import Counter
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import AuditLog  # noqa: E402
from app.routers import vat_recon as VR  # noqa: E402
from app.services import vat_duplicates as VD  # noqa: E402
from app.services.receipts import REISSUED_NO_TERMINAL  # noqa: E402

TOOL = "vat_reissue_missing"
FIELDS = ["payment_id", "verdict", "tenant", "site_code", "site_name", "plate", "method", "amount",
          "paid_at_ub", "paid_at", "day", "cashier", "state", "cancel_rule", "cancelled_ddtd",
          "receiver_type", "customer_tin"]
VERDICT_MN = {"REISSUE": "ТЕГ-т терминалын мөр алга — ДАХИН ГАРГАНА",
              "TERMINAL_OK": "терминалын мөр ТЕГ-т бий — хөндөхгүй",
              "OUTSIDE": "ТЕГ файлын хүрээнээс гадуур — дүгнэхгүй"}


def _read_only(db):
    if db.bind.dialect.name == "postgresql":
        db.execute(text("SET TRANSACTION READ ONLY"))


def write_csv(path, rows, fields):
    import csv
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def print_by_site_day(rows, log=print):
    cells: dict = {}
    for r in rows:
        k = (r["tenant"] or "?", r["site_code"] or r["site_name"] or "?")
        c = cells.setdefault(k, {})
        d = c.setdefault(r["day"], [0, 0.0, Counter()])
        d[0] += 1
        d[1] += r["amount"]
        d[2][r["method"]] += 1
    for (ten, site), days in sorted(cells.items()):
        n = sum(v[0] for v in days.values())
        amt = sum(v[1] for v in days.values())
        log(f"  {ten:<11} {site:<14} {n:5d} ({amt:,.0f}₮)")
        for day, (dn, damt, dm) in sorted(days.items()):
            log(f"      {day}  {dn:4d}  {damt:10,.0f}₮  ({' · '.join(f'{k} {v}' for k, v in sorted(dm.items()))})")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", action="append", required=True,
                    help="ТЕГ экспорт (.xlsx/.csv); олон удаа, урт хугацаа болно")
    ap.add_argument("--tenant", required=True, help="түрээслэгч (code/нэр/id) эсвэл ALL")
    ap.add_argument("--site", action="append", help="зөвхөн энэ зогсоол(ууд) (site_code)")
    ap.add_argument("--tol", type=int, default=VR.DEFAULT_TOL, help="цагийн зөрүү (сек), анхдагч 60")
    ap.add_argument("--apply", action="store_true", help="БОДИТООР дахин гаргах (анхдагч dry-run)")
    ap.add_argument("--user", default=None, help="AuditLog-д (анхдагч cli:<OS хэрэглэгч>)")
    ap.add_argument("--limit", type=int, default=None, help="эхний N-ийг л (туршилтад)")
    ap.add_argument("--max", type=int, default=2000, help="үүнээс олон бол зогсоно (анхдагч 2000)")
    ap.add_argument("--out", default=None, help="CSV тайлангийн зам")
    ap.add_argument("--yes", action="store_true", help="баталгаажуулалтын асуултыг алгасах")
    ap.add_argument("--delay", type=float, default=0.2, help="төлбөр хоорондын завсар (сек)")
    ap.add_argument("--accept-no-terminal-file", action="store_true",
                    help="терминалын (Ontime) мөр ОГТ алга файлыг нотолгоо болгож --apply хийх")
    args = ap.parse_args(argv)
    for f in args.file:
        if not os.path.isfile(f):
            raise SystemExit(f"Файл олдсонгүй: {f}")
    username = args.user or f"cli:{os.getenv('SUDO_USER') or getpass.getuser()}"
    out = args.out or f"vat_reissue_{datetime.now():%Y%m%d_%H%M%S}.csv"
    stem = out[:-4] if out.lower().endswith(".csv") else out
    taxes = []
    for f in args.file:
        tax, _diag = VR.parse_tax_export(os.path.basename(f), open(f, "rb").read())
        taxes.append((os.path.basename(f), tax))
    files = [n for n, _t in taxes]

    db = SessionLocal()
    _read_only(db)
    try:
        try:
            tenants = VD.resolve_tenants(db, args.tenant)
            sites = VD.pos_app_sites(db, tenants, args.site)
        except ValueError as e:
            raise SystemExit(str(e))
        tcode = {t.id: t.code for t in tenants}
        print(f"{TOOL}: {'APPLY' if args.apply else 'DRY-RUN'} · DB {VD.db_name()} · түрээслэгч "
              f"{VD.tenant_label(tenants)} · {len(sites)} зогсоол · файл {len(files)} · tol ±{args.tol}с")
        t0 = time.monotonic()
        ev_sites = VD.tenant_sites(db, tenants)
        ev = VD.terminal_evidence(db, taxes, sites=ev_sites, tol=args.tol,
                                  shifts=[0.0, -float(settings.tz_offset_hours)])
        if not ev["coverage"]:
            VD.print_evidence(ev, tenants=tcode)
            print("\nЗОГСОВ: ашиглах боломжтой ТЕГ файл алга (дээрх тайлбарыг үзнэ үү).")
            return 7
        lo = min(c[0] for c in ev["coverage"])
        hi = max(c[1] for c in ev["coverage"])
        sel = VD.select_reissue(db, sites=sites, lo=lo, hi=hi + timedelta(seconds=1))
        split = VD.evidence_split(sel["candidates"], ev, {x.id: x.tenant_id for x in ev_sites})
        VD.print_evidence(ev, split, tenants=tcode)
        no_term = [st["file"] for st in ev["files"] if st["usable"] and not st["terminal_registers"]]
        for r in split["missing"]:
            r["verdict"] = "REISSUE"
        for r in split["with_terminal"]:
            r["verdict"] = "TERMINAL_OK"
        for r in split["outside"]:
            r["verdict"] = "OUTSIDE"
        miss = split["missing"]
        total = sum(r["amount"] for r in miss)
        print(f"\nНЭР ДЭВШИГЧ (EXTERNAL / POS-ийн давхардал гэж цуцалсан, өөр баримтгүй): "
              f"{len(sel['candidates'])} · хасагдсан: "
              + (" · ".join(f"{VD.REISSUE_REASON_MN.get(k, k)} {v}" for k, v in sel["skipped"].most_common())
                 or "—"))
        print(f"  {VERDICT_MN['TERMINAL_OK']}: {len(split['with_terminal'])}"
              f" · {VERDICT_MN['OUTSIDE']}: {len(split['outside'])}")
        print(f"\nДАХИН ГАРГАХ (ТЕГ-т терминалын мөр алга): {len(miss)} төлбөр · {total:,.0f}₮")
        print_by_site_day(miss)
        allrows = miss + split["with_terminal"] + split["outside"]
        write_csv(out, miss, FIELDS)
        write_csv(f"{stem}_all.csv", allrows, FIELDS)
        print(f"\nCSV: {out} ({len(miss)}) · {stem}_all.csv ({len(allrows)}) · "
              f"{time.monotonic() - t0:.1f}с")
        sites_used = [s_ for s_ in sites if any(r["site_id"] == s_.id for r in miss)]
        if not args.apply:
            probs = VD.issue_ready(db, sites_used)
            if no_term:
                probs.append(f"терминалын мөргүй файл ({', '.join(no_term)}) — --accept-no-terminal-file шаардлагатай")
            if probs:
                print("\n⚠ --apply ОДООГООР ажиллахгүй: " + "; ".join(probs))
            print("\nDRY-RUN — юу ч өөрчлөгдсөнгүй. Дахин гаргахын тулд ижил командыг --apply --user <нэр> "
                  "(--max <тоо>) -тэй ажиллуулна.")
            return 0
        if no_term and not args.accept_no_terminal_file:
            print(f"\nЗОГСОВ: {', '.join(no_term)} файлд терминалын (Ontime) мөр ОГТ алга — экспортыг шүүж "
                  "татсан бол бүх борлуулалт «баримтгүй» гэж буруу дүгнэгдэнэ. Файлаа шалгаад "
                  "--accept-no-terminal-file-ээр баталгаажуулна уу.")
            return 8
        work = miss if args.limit is None else miss[:max(0, args.limit)]
        if len(work) > args.max:
            print(f"\nЗОГСОВ: {len(work)} төлбөр > --max {args.max}. Тоог шалгаад --max-ийг ил тодоор өсгөнө үү.")
            return 2
        if not work:
            print("\nДахин гаргах төлбөр алга.")
            return 0
        probs = VD.issue_ready(db, sites_used)
        if probs:
            print("\nЗОГСОВ — баримт гаргах боломжгүй: " + "; ".join(probs))
            return 3
    finally:
        db.rollback()
    amount = sum(r["amount"] for r in work)
    print(f"\nДАХИН ГАРГАХ ГЭЖ БАЙНА: DB {VD.db_name()} · сервер {settings.public_base_url} · түрээслэгч "
          f"{VD.tenant_label(tenants)} · {len(work)} төлбөр ({amount:,.0f}₮) · файл {', '.join(files)}")
    if not args.yes:
        if not sys.stdin.isatty():
            print("\nЗОГСОВ: баталгаажуулах терминал алга — --yes өгнө үү.")
            return 4
        ans = input(f"\n{len(work)} борлуулалтад манай msgbill баримтыг ({amount:,.0f}₮) ДАХИН ГАРГАХ уу? "
                    "Терминал баримт хэвлээгүй нь ТЕГ экспортоор батлагдсан. Зөвшөөрвөл YES гэж бичнэ үү: ").strip()
        if ans != "YES":
            print("Дахин гаргасангүй.")
            return 5
    print(f"\nДахин гаргаж байна ({len(work)}, {amount:,.0f}₮) — хэрэглэгч {username}")
    cnt, res_rows = Counter(), []
    for i, r in enumerate(work, 1):
        try:
            res = VD.reissue_one(db, r["payment_id"], username=username, files=files, source="cli")
        except Exception as e:  # noqa: BLE001 — нэг төлбөрийн алдаа бусдыг зогсоохгүй
            db.rollback()
            res = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        if res.get("skipped"):
            outcome = f"SKIPPED ({VD.REISSUE_REASON_MN.get(res['skipped'], res['skipped'])})"
            cnt["skipped"] += 1
        elif res.get("ok"):
            outcome = "REISSUED (msgbill ажил хадгалагдсан)"
            cnt["reissued"] += 1
        else:
            outcome = f"АЛДАА: {res.get('error')}"
            cnt["failed"] += 1
        print(f"  [{i}/{len(work)}] {r['paid_at_ub']} {r['site_code']} {r['plate'] or '—'} {r['method']} "
              f"{r['amount']:,.0f}₮ → {outcome}")
        res_rows.append({**r, "outcome": outcome, "error": res.get("error") or ""})
        if args.delay and not res.get("skipped"):
            time.sleep(args.delay)
    try:
        db.add(AuditLog(username=username, action="EBARIMT_REISSUE_SUMMARY", entity="vat",
                        entity_id=REISSUED_NO_TERMINAL,
                        detail={"tool": TOOL, "tenants": [t.code for t in tenants],
                                "sites": [s_.site_code for s_ in sites], "files": files,
                                "requested": len(work), "amount": amount, "result": dict(cnt)}))
        db.commit()
    finally:
        db.close()
    write_csv(f"{stem}_applied.csv", res_rows, FIELDS + ["outcome", "error"])
    print(f"\nДҮН: {dict(cnt)}\nCSV: {stem}_applied.csv\nШинэ баримтыг financial worker msgbill-ээр "
          "илгээнэ (Ибаримт хуудсанд PENDING → SENT). Дахин ажиллуулахад гаргасан нь алгасагдана.")
    return 0 if not cnt.get("failed") else 1


if __name__ == "__main__":
    sys.exit(main())
