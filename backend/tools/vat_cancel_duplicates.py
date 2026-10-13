"""ТЕГ экспортоор POS терминалын баримттай ДАВХАРДСАН манай msgbill баримтыг олох/цуцлах.

Асуудал (2026-08-24-нд батлагдсан, 08-24-ний ТЕГ файл дээр 63 = 167,500₮): Хангарьдын
касс дээр БЭЛЭН/КАРТ төлбөрт Ontime POS терминал (ТЕГ ДДТД …10002990) баримт
хэвлэсэн атал манай систем msgbill-ээр (…10045952) ДАХИН баримт гаргасан → нэг
борлуулалт ТЕГ-т 2 баримттай. Бид зөвхөн ӨӨРИЙН (msgbill) баримтаа цуцалж чадна.

Тулгалт нь UI-ийн «ТЕГ тулгалт»-тай ЯГ НЭГ код (app/services/vat_duplicates.py →
app/routers/vat_recon.py): касс бүрийн өөрийн цагийн бүс, манай баримт зөвхөн өөрийн
кассын мөртэй, хатуу нэг-нэгээр, ±60с. Цуцлах дүрэм (vat_recon.cancel_block_reason):
QR биш (бэлэн/карт/данс/хэтэвч) төлбөр, идэвхтэй баримт бүгд msgbill + provider_ref.

АНХДАГЧ = DRY-RUN (READ ONLY транзакц — юу ч бичихгүй): хураангуй хэвлэж, бүтэн
CSV тайлан бичнэ. --apply үед ЗӨВХӨН тэр жагсаалтыг дараалан цуцална (идемпотент —
дахин ажиллуулахад цуцлагдсаныг алгасна, CANCEL_PENDING-ийг GET-ээр эцэслэнэ).

    cd /root/PARKING/backend
    venv/bin/python tools/vat_cancel_duplicates.py --file /root/teg_0824.xlsx \\
        --tenant EASYPARKING                                                              # харах
    venv/bin/python tools/vat_cancel_duplicates.py --file a.xlsx --file b.xlsx \\
        --tenant EASYPARKING --site HANGARID --out /root/dup_report.csv                  # шүүх
    venv/bin/python tools/vat_cancel_duplicates.py --file /root/teg_0824.xlsx \\
        --tenant EASYPARKING --site HANGARID \\
        --apply --user temuujin --note "ТЕГ тулгалт 08-24: Ontime POS давхардал"        # цуцлах

Хамгаалалт: --apply зөвхөн ажиллаж буй «parking» DB + прод сервер дээр (продын ХУУЛБАР
audit*/t_* эсвэл ТЕСТ сервер дээр татгалзана), --tenant заавал; msgbill MOCK/тест
түлхүүр/түлхүүргүй үед ажиллахгүй; --max (2000)-аас олон бол зогсоно; YES-ийн өмнө DB,
сервер, түрээслэгч, msgbill түлхүүрийн хүрээг хэвлэнэ; TTY дээр «YES» гэж баталгаажуулна
(--yes-ээр алгасна); квот/эрхийн алдаа давтагдвал зогсоно. Төлбөр бүрд AuditLog
EBARIMT_CANCEL_BULK + commit. Мөнгө буцаахгүй — зөвхөн татварын баримт.

Дахин ажиллуулах (--limit 1 → бүгд) ИЖИЛ файлаар ч аюулгүй: цуцалсан баримтын хуучин мөр
өөрийн цуцлагдсан баримттайгаа холбогдож, Ontime мөр өөр төлбөр рүү шилжихгүй (тэр
төлбөр «ДАВХАРДАЛ ЦУЦЛАГДСАН» гэж гарна).
"""
import argparse
import asyncio
import csv
import getpass
import os
import sys
import time
from collections import Counter
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import or_, text  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import AuditLog, ParkingSite, Tenant  # noqa: E402
from app.routers import vat_recon as VR  # noqa: E402
from app.services import vat_duplicates as VD  # noqa: E402

TOOL = "vat_cancel_duplicates"
DUP_FIELDS = ["payment_id", "site_code", "site_name", "plate", "method", "amount", "paid_at_ub",
              "paid_at", "our_ddtd", "our_provider_ref", "our_status", "teg_ddtd", "teg_register",
              "teg_vendor", "teg_time_ub", "gap_s", "ours_in_file", "file"]
LEFT_FIELDS = ["file", "dt", "dt_utc", "amount", "ddtd", "register", "foreign", "src", "verdict",
               "note", "payment_id", "site_name", "plate", "method", "our_ddtd", "gap_s"]


def _read_only(db):
    """Dry-run: Postgres-ийн READ ONLY транзакц — алдаатай код ч юу ч бичиж чадахгүй."""
    if db.bind.dialect.name == "postgresql":
        db.execute(text("SET TRANSACTION READ ONLY"))


def resolve_tenant(db, value):
    if not value:
        return None
    conds = [Tenant.code.ilike(value)]
    if len(value) == 36:                       # UUID
        conds.append(Tenant.id == value)
    t = db.query(Tenant).filter(or_(*conds)).first()
    if t is None:
        raise SystemExit(f"Түрээслэгч олдсонгүй: {value}")
    return t


def resolve_sites(db, codes):
    if not codes:
        return None
    out = {}
    for c in codes:
        s = db.query(ParkingSite).filter(ParkingSite.site_code.ilike(c)).first()
        if s is None:
            raise SystemExit(f"Зогсоол олдсонгүй (site_code): {c}")
        out[s.id] = s
    return out


def analyze_files(db, files, *, tol, shifts, tenant_sites, log=print):
    """Файл бүрийг ТУСАД НЬ тулгана (өөр өдрийн файлын цонх холилдохгүй),
    давхардлыг төлбөрөөр нь нэгтгэнэ. Буцаана: (давхардал, үлдсэн мөрүүд, статистик)."""
    dups, left_rows, stats, seen = [], [], [], set()
    for f in files:
        raw = open(f, "rb").read()
        tax, diag = VR.parse_tax_export(os.path.basename(f), raw)
        a = VD.reconcile_db(db, tax, shifts=shifts, tol=tol, ours_sites=tenant_sites,
                            probe_sites=None,
                            scope_ids=set(tenant_sites) if tenant_sites is not None else None)
        r = a["r"]
        verdicts = Counter(x["verdict"] for x in a["explained"])
        log(f"\n== {f}")
        log(f"   ТЕГ мөр {len(tax)} (багана {diag['columns']}) · касс: "
            + ", ".join(f"…{k}={v} ({'+' if r['group_shifts'].get(k, 0) > 0 else ''}"
                        f"{r['group_shifts'].get(k, 0):g}ц{', өөр систем' if k in r['foreign_registers'] else ''})"
                        for k, v in sorted(VR.pos_groups([t['ddtd'] for t in tax], r.get('tail')).items())))
        log(f"   манай баримттай таарсан {r['matched']} · манайд бий/ТЕГ-д алга {len(r['unmatched_ours'])}"
            f" · ТЕГ-д бий/манайд алга {len(a['left'])}"
            + (f" · манай ЦУЦЛАГДСАН баримтын мөр файлд {r['cancelled_in_file']}"
               if r.get("cancelled_in_file") else ""))
        for k, st in sorted(r.get("group_stats", {}).items()):
            if st["foreign"]:
                rate = st["matched"] / max(st["rows"], 1)
                rs = a.get("register_sites", {}).get(k)
                where = ("" if rs is None else
                         f" · зогсоол «{rs['site_name']}» ({rs['share']:.0%} / {rs['hits']})" if rs["site_id"]
                         else f" · ⚠ зогсоол ТОДОРХОЙГҮЙ ({rs['sites']} зогсоолд тарсан) — цуцлахгүй")
                log(f"   өөр системийн касс …{k}: {st['matched']}/{st['rows']} мөр манай төлбөртэй таарсан"
                    + where
                    + ("" if rate >= 0.5 else
                       "  ⚠ ХАГАСААС БАГА — энэ касс манай зогсоолынх биш байж магадгүй, "
                       "давхардлыг CSV-ээс гараар шалгана уу"))
        for v, n in verdicts.most_common():
            log(f"     {n:5d}  {v:26s} {VR.VERDICTS.get(v, '').split(' — ')[0]}")
        for x in a["explained"]:
            left_rows.append({**{k: x.get(k) for k in LEFT_FIELDS}, "file": os.path.basename(f)})
        for d in a["duplicates"]:
            if d["payment_id"] in seen:
                continue
            seen.add(d["payment_id"])
            dups.append({**d, "file": os.path.basename(f)})
        stats.append({"file": f, "rows": len(tax), "matched": r["matched"],
                      "verdicts": dict(verdicts), "duplicates": len(a["duplicates"])})
    dups.sort(key=lambda d: d["paid_at"])
    return dups, left_rows, stats


def filter_dups(dups, *, sites=None, date_from=None, date_to=None):
    out = []
    for d in dups:
        if sites is not None and d["site_id"] not in sites:
            continue
        day = d["paid_at_ub"][:10]
        if date_from and day < date_from:
            continue
        if date_to and day > date_to:
            continue
        out.append(d)
    return out


def print_summary(dups, log=print):
    s = VR.duplicate_summary(dups)
    log(f"\nДАВХАРДАЛ (манай msgbill баримтыг цуцлах): {s['count']} төлбөр · {s['amount']:,.0f}₮"
        f" · манай баримт ч ТЕГ файлд таарсан: {s['ours_in_file']}")
    for title, key in (("Зогсоолоор", "by_site"), ("Аргаар", "by_method"),
                       ("Өдрөөр (УБ)", "by_day"), ("ТЕГ кассаар", "by_register")):
        log(f"  {title}: " + " · ".join(f"{k} {v['count']} ({v['amount']:,.0f}₮)"
                                         for k, v in s[key].items()) if s[key] else f"  {title}: —")
    return s


def write_csv(path, rows, fields):
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


async def apply_cancel(db, dups, *, note, username, delay=0.5, log=print):
    """Жагсаалтын төлбөр бүрийг ДАРААЛАН цуцална (cancel_one: түгжээ, дүрэм дахин
    шалгах, идемпотент, AuditLog, commit). Квот/эрхийн алдаа давтагдвал зогсоно."""
    res_rows, cnt, auth_streak, stopped = [], Counter(), 0, None
    for i, d in enumerate(dups, 1):
        try:
            res = await VD.cancel_one(db, d["payment_id"], note=note, username=username,
                                      teg={"teg_ddtd": d["teg_ddtd"], "teg_register": d["teg_register"]},
                                      source="cli")
        except Exception as e:  # noqa: BLE001 — нэг төлбөрийн алдаа бусдыг зогсоохгүй
            db.rollback()
            res = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        if res.get("skipped"):
            outcome = "SKIPPED (аль хэдийн цуцлагдсан)"
            cnt["skipped"] += 1
        elif res.get("ok"):
            outcome = "CANCELLED"
            cnt["cancelled"] += 1
        elif res.get("pending"):
            outcome = "CANCEL_PENDING (msgbill ТЕГ-т дахин оролдоно)"
            cnt["pending"] += 1
        else:
            outcome = f"АЛДАА: {res.get('error')}"
            cnt["failed"] += 1
        scopes = ",".join(sorted({v for v in (res.get("key_scopes") or {}).values() if v}))
        if scopes:
            cnt[f"key:{scopes}"] += 1
        log(f"  [{i}/{len(dups)}] {d['paid_at_ub']} {d['site_code']} {d['plate'] or '—'} "
            f"{d['method']} {d['amount']:,.0f}₮ {d['our_ddtd']} → {outcome}"
            + (f" (түлхүүр: {scopes})" if scopes else ""))
        res_rows.append({**d, "outcome": outcome, "error": res.get("error") or "",
                         "key_scope": scopes})
        if not res.get("ok") and not res.get("pending") and not res.get("skipped"):
            auth_streak = auth_streak + 1 if res.get("auth_error") else 0
            if res.get("quota"):
                stopped = "msgbill квот/хязгаарын алдаа — зогслоо, дараа дахин ажиллуулна уу"
                break
            if auth_streak >= 3:
                stopped = "msgbill түлхүүрийн (эрхийн) алдаа 3 удаа дараалсан — зогслоо"
                break
        else:
            auth_streak = 0
        if not res.get("skipped") and delay:
            await asyncio.sleep(delay)
    return res_rows, cnt, stopped


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", action="append", required=True, help="ТЕГ экспорт (.xlsx/.csv); олон удаа өгч болно")
    ap.add_argument("--tenant", help="түрээслэгч (id эсвэл code) — тэр ТТД-ийн экспорт бол")
    ap.add_argument("--site", action="append", help="зөвхөн энэ зогсоол(ууд)-ын давхардал (site_code)")
    ap.add_argument("--from", dest="date_from", help="төлсөн огноо (УБ) ≥ YYYY-MM-DD")
    ap.add_argument("--to", dest="date_to", help="төлсөн огноо (УБ) ≤ YYYY-MM-DD")
    ap.add_argument("--tol", type=int, default=VR.DEFAULT_TOL, help="цагийн зөрүү (сек), анхдагч 60")
    ap.add_argument("--tz-shift", type=float, default=None,
                    help="файлын цагт нэмэх цаг (анхдагч: касс бүрд 0/−8-аас автоматаар)")
    ap.add_argument("--apply", action="store_true", help="БОДИТООР цуцлах (анхдагч dry-run)")
    ap.add_argument("--note", default="ТЕГ тулгалт: POS терминал (касс) дээр баримт хэвлэгдсэн тул "
                                      "msgbill дэх давхардлыг цуцлав", help="цуцлах шалтгаан")
    ap.add_argument("--user", default=None, help="AuditLog-д бичих хэрэглэгч (анхдагч cli:<OS хэрэглэгч>)")
    ap.add_argument("--limit", type=int, default=None, help="эхний N-ийг л цуцлах (туршилтад)")
    ap.add_argument("--max", type=int, default=2000, help="үүнээс олон бол зогсоно (анхдагч 2000)")
    ap.add_argument("--out", default=None, help="CSV тайлангийн зам")
    ap.add_argument("--yes", action="store_true", help="баталгаажуулалтын асуултыг алгасах")
    ap.add_argument("--delay", type=float, default=0.5, help="цуцлалт хоорондын завсар (сек)")
    args = ap.parse_args(argv)

    for f in args.file:
        if not os.path.isfile(f):
            raise SystemExit(f"Файл олдсонгүй: {f}")
    if args.apply and not args.tenant:
        # ТЕГ экспорт ТТД-ээр гардаг — түрээслэгчгүй бол бусад ТТД-ийн төлбөр ч нэр
        # дэвшигч болно (2026-10-13 хяналт)
        raise SystemExit("--apply үед --tenant заавал (ТЕГ файлын ТТД-ийн түрээслэгч, ж: EASYPARKING)")
    shifts = [0.0, -float(settings.tz_offset_hours)] if args.tz_shift is None else [args.tz_shift]
    username = args.user or f"cli:{os.getenv('SUDO_USER') or getpass.getuser()}"
    out = args.out or f"vat_duplicates_{datetime.now():%Y%m%d_%H%M%S}.csv"
    stem = out[:-4] if out.lower().endswith(".csv") else out

    db = SessionLocal()
    _read_only(db)
    try:
        tenant = resolve_tenant(db, args.tenant)
        tenant_sites = ([s.id for s in db.query(ParkingSite).filter(ParkingSite.tenant_id == tenant.id)]
                        if tenant else None)
        if tenant is not None and not tenant_sites:
            raise SystemExit(f"«{tenant.name}»-д зогсоол оноогоогүй")
        only = resolve_sites(db, args.site)
        print(f"{TOOL}: {'APPLY' if args.apply else 'DRY-RUN'} · DB {settings.database_url.rsplit('/', 1)[-1]}"
              f" · tol ±{args.tol}с · шилжилт {shifts}"
              + (f" · түрээслэгч {tenant.code}" if tenant else "")
              + (f" · зогсоол {','.join(s.site_code for s in only.values())}" if only else ""))
        t0 = time.monotonic()
        dups, left_rows, _stats = analyze_files(db, args.file, tol=args.tol, shifts=shifts,
                                                tenant_sites=tenant_sites)
        dups = filter_dups(dups, sites=set(only) if only else None,
                           date_from=args.date_from, date_to=args.date_to)
        print_summary(dups)
        write_csv(out, dups, DUP_FIELDS)
        write_csv(f"{stem}_unmatched.csv", left_rows, LEFT_FIELDS)
        print(f"\nCSV: {out} ({len(dups)} давхардал) · {stem}_unmatched.csv ({len(left_rows)} ТЕГ мөр)"
              f" · {time.monotonic() - t0:.1f}с")
        sites = list({d["site_id"]: db.get(ParkingSite, d["site_id"]) for d in dups if d["site_id"]}.values())
        if not args.apply:
            probs = VD.cancel_ready(db, sites)
            if probs:
                print("\n⚠ --apply ОДООГООР ажиллахгүй: " + "; ".join(probs))
            print("\nDRY-RUN — юу ч өөрчлөгдсөнгүй. Цуцлахын тулд ижил командыг --apply --user <нэр> -тэй ажиллуулна.")
            return 0
        if len(dups) > args.max:
            print(f"\nЗОГСОВ: {len(dups)} давхардал > --max {args.max}. Тоог шалгаад --max-ийг ил тодоор өсгөнө үү.")
            return 2
        if args.limit is not None:
            dups = dups[:max(0, args.limit)]
        if not dups:
            print("\nЦуцлах давхардал алга.")
            return 0
        probs = VD.cancel_ready(db, sites)
        if probs:
            print("\nЗОГСОВ — цуцлах боломжгүй: " + "; ".join(probs))
            return 3
        scopes = VD.account_scopes(sites)
    finally:
        db.rollback()          # dry-run/шинжилгээний READ ONLY транзакцыг хаана

    total = sum(d["amount"] for d in dups)
    by_site = VR.duplicate_summary(dups)["by_site"]
    print(f"\nЦУЦЛАХ ГЭЖ БАЙНА: DB {VD.db_name()} · сервер {settings.public_base_url} · түрээслэгч "
          f"{tenant.code} · " + " · ".join(f"{k} {v['count']} ({v['amount']:,.0f}₮)" for k, v in by_site.items())
          + "\n  msgbill түлхүүр (оролдох дараалал): "
          + " · ".join(f"{k}: {' → '.join(v) or '—'}" for k, v in scopes.items()))
    if not args.yes:
        if not sys.stdin.isatty():
            print("\nЗОГСОВ: баталгаажуулах терминал алга — --yes өгнө үү.")
            return 4
        ans = input(f"\n{len(dups)} төлбөрийн манай msgbill баримтыг ({total:,.0f}₮) ТЕГ-т ЦУЦЛАХ уу? "
                    "Мөнгө буцаахгүй. Зөвшөөрвөл YES гэж бичнэ үү: ").strip()
        if ans != "YES":
            print("Цуцалсангүй.")
            return 5
    print(f"\nЦуцалж байна ({len(dups)}, {total:,.0f}₮) — хэрэглэгч {username}, шалтгаан «{args.note}»")
    res_rows, cnt, stopped = asyncio.run(apply_cancel(db, dups, note=args.note, username=username,
                                                      delay=args.delay))
    try:
        db.add(AuditLog(username=username, action="EBARIMT_CANCEL_BULK_SUMMARY", entity="vat",
                        entity_id="DUPLICATE_OF_POS_TERMINAL",
                        detail={"tool": TOOL, "files": [os.path.basename(f) for f in args.file],
                                "requested": len(dups), "amount": total, "note": args.note,
                                "result": dict(cnt), "stopped": stopped}))
        db.commit()
    finally:
        db.close()
    write_csv(f"{stem}_applied.csv", res_rows, DUP_FIELDS + ["outcome", "error", "key_scope"])
    print(f"\nДҮН: {dict(cnt)}" + (f"\nЗОГССОН: {stopped}" if stopped else "")
          + f"\nCSV: {stem}_applied.csv\nДахин ажиллуулахад цуцлагдсан нь алгасагдана (идемпотент).")
    return 0 if not cnt.get("failed") and not stopped else 1


if __name__ == "__main__":
    sys.exit(main())
