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
төлбөр «ДАВХАРДАЛ ЦУЦЛАГДСАН» гэж гарна). Цуцалсны дараа терминалын баримтыг бүртгэнэ
(ТЕГ-ийн Ontime ДДТД-тэй ONTIME/SENT мөр) — «Дахин үүсгэх» давхардлыг сэргээхгүй.

──── --pos-app: ДҮРМЭЭР (ТЕГ файлгүй) — 2026-10-13 эзний шийдвэр ────
ИйзиПаркинг ХХК-ийн POS терминалтай БҮХ зогсоолд PAX терминал POS аппын КАРТ, БЭЛЭН
борлуулалт бүрд Ontime e-Barimt өөрөө хэвлэдэг → тэдгээрийн манай msgbill баримтыг
цуцална (--file-тай хамт өгөхгүй). Сонголт: PAID, CARD/CASH, --tenant-ийн зогсоол
(--site-аар нарийсгана; тест KH, SITE10 зөвхөн нэрлэсэн үед), төлсөн огноо --from/--to
(УБ, анхдагч 2026-08-19 → одоо); POS аппынх = provider POS, эсвэл source POSAPP, эсвэл
кассчин нь зогсоолын «POS аппын хэрэглэгчид» (pos_terminal_users) тохиргоонд, тохиргоо
хоосон бол тэр зогсоолд POS-оор ≥20 карт авсан хэрэглэгч (--pos-user дарна); идэвхтэй
msgbill баримттай (provider_ref), өөр сувгийн тэмдэггүй SENT баримтгүй; ААН/ТТД-тэй
баримтыг (--include-company-гүй бол) ХӨНДӨХГҮЙ. Цуцалсны дараа ДДТД-гүй EXTERNAL
(«POS терминал гаргасан») мөр бүртгэнэ. Ontime баримт хэвлэгдээгүй борлуулалт байвал
e-Barimt-гүй үлдэнэ.

    venv/bin/python tools/vat_cancel_duplicates.py --pos-app --tenant EASYPARKING \
        --out /root/posapp.csv                                                         # харах
    venv/bin/python tools/vat_cancel_duplicates.py --pos-app --tenant EASYPARKING \
        --apply --limit 1 --user temuujin --note "Эзний шийдвэр 10-13: POS терминал Ontime"  # туршилт
"""
import argparse
import asyncio
import csv
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
from app.models import AuditLog, ParkingSite  # noqa: E402
from app.routers import vat_recon as VR  # noqa: E402
from app.services import vat_duplicates as VD  # noqa: E402

TOOL = "vat_cancel_duplicates"
DUP_FIELDS = ["payment_id", "site_code", "site_name", "plate", "method", "amount", "paid_at_ub",
              "paid_at", "our_ddtd", "our_provider_ref", "our_status", "teg_ddtd", "teg_register",
              "teg_vendor", "teg_time_ub", "gap_s", "ours_in_file", "file"]
LEFT_FIELDS = ["file", "dt", "dt_utc", "amount", "ddtd", "register", "foreign", "src", "verdict",
               "note", "payment_id", "site_name", "plate", "method", "our_ddtd", "gap_s"]
POSAPP_FIELDS = ["payment_id", "site_code", "site_name", "plate", "method", "amount", "paid_at_ub",
                 "paid_at", "month", "cashier", "via", "our_ddtd", "our_provider_ref", "our_status",
                 "receiver_type", "customer_tin", "kind"]
POSAPP_NOTE = ("Эзний шийдвэр 2026-10-13: POS терминал (PAX/Ontime) карт/бэлэн борлуулалт бүрд "
               "e-Barimt хэвлэдэг — msgbill дэх давхардлыг цуцлав")


def _read_only(db):
    """Dry-run: Postgres-ийн READ ONLY транзакц — алдаатай код ч юу ч бичиж чадахгүй."""
    if db.bind.dialect.name == "postgresql":
        db.execute(text("SET TRANSACTION READ ONLY"))


def resolve_tenant(db, value):
    if not value:
        return None
    try:
        return VD.resolve_tenant(db, value)
    except ValueError as e:
        raise SystemExit(str(e))


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


async def apply_cancel(db, dups, *, note, username, delay=0.5, log=print, source="cli",
                       mark_extra=None, guard=None, allow_company=False):
    """Жагсаалтын төлбөр бүрийг ДАРААЛАН цуцална (cancel_one: түгжээ, дүрэм дахин
    шалгах, идемпотент, AuditLog, commit, терминалын мөр). Квот/эрхийн алдаа давтагдвал
    зогсоно. --file ба --pos-app хоёулаа ЭНЭ НЭГ зам."""
    res_rows, cnt, auth_streak, stopped = [], Counter(), 0, None
    for i, d in enumerate(dups, 1):
        try:
            res = await VD.cancel_one(db, d["payment_id"], note=note, username=username,
                                      teg={"teg_ddtd": d.get("teg_ddtd"),
                                           "teg_register": d.get("teg_register")},
                                      source=source, mark_extra=mark_extra, guard=guard,
                                      allow_company=allow_company)
        except Exception as e:  # noqa: BLE001 — нэг төлбөрийн алдаа бусдыг зогсоохгүй
            db.rollback()
            res = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        if res.get("skipped"):
            outcome = "SKIPPED (аль хэдийн цуцлагдсан" + (
                ", терминалын мөр нөхөв)" if res.get("terminal_receipt") else ")")
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
        term = res.get("terminal_receipt") or {}
        log(f"  [{i}/{len(dups)}] {d['paid_at_ub']} {d['site_code']} {d['plate'] or '—'} "
            f"{d['method']} {d['amount']:,.0f}₮ {d['our_ddtd']} → {outcome}"
            + (f" (түлхүүр: {scopes})" if scopes else "")
            + (f" · терминал {term.get('provider')}/{term.get('status')}" if term else ""))
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


def _breakdown(rows):
    """{(зогсоол, сар, арга): [тоо, ₮]} + зогсоолын нийт — YES асуулт ба dry-run-д."""
    cells: dict = {}
    for r in rows:
        k = (r["site_code"] or r["site_name"] or "?", r["month"], r["method"])
        c = cells.setdefault(k, [0, 0.0])
        c[0] += 1
        c[1] += r["amount"]
    return dict(sorted(cells.items()))


def print_pos_app_summary(sel, log=print):
    rows = sel["selected"]
    total = sum(r["amount"] for r in rows)
    log(f"\nЦУЦЛАХ (POS аппын дүрэм): {len(rows)} төлбөр · {total:,.0f}₮")
    by_site: dict = {}
    for (site, month, method), (n, amt) in _breakdown(rows).items():
        by_site.setdefault(site, []).append(f"{month} {method} {n} ({amt:,.0f}₮)")
    for site, parts in by_site.items():
        n = sum(1 for r in rows if (r["site_code"] or r["site_name"]) == site)
        amt = sum(r["amount"] for r in rows if (r["site_code"] or r["site_name"]) == site)
        log(f"  {site}: {n} ({amt:,.0f}₮) — " + " · ".join(parts))
    by_m = Counter(r["method"] for r in rows)
    log("  Аргаар: " + " · ".join(f"{k} {v}" for k, v in sorted(by_m.items())))
    comp = sel["company"]
    if comp:
        cs = Counter(r["site_code"] for r in comp)
        log(f"\nХАСАГДСАН — ААН/ТТД-тэй баримт: {len(comp)} ({sum(r['amount'] for r in comp):,.0f}₮) · "
            + " · ".join(f"{k} {v}" for k, v in sorted(cs.items()))
            + " — терминал иргэний баримт хэвлэдэг тул байгууллагын баримтыг ХӨНДӨХГҮЙ"
              " (--include-company-оор л)")
    ex = {k: v for k, v in sel["excluded"].items() if k not in ("COMPANY",)}
    if ex:
        log("Бусад хасагдсан: " + " · ".join(f"{VD.POS_APP_EXCLUSION_MN.get(k, k)} {v}"
                                              for k, v in sorted(ex.items(), key=lambda kv: -kv[1])))
    if sel["complete"]:
        log(f"Өмнө цуцлагдсан, терминалын мөр дутуу (msgbill дуудахгүй, нөхнө): {len(sel['complete'])}")
    log("\n⚠ АНХААР: эдгээр борлуулалтад Ontime (POS терминал) баримт ҮНЭХЭЭР хэвлэгдсэн гэж үзэж "
        "манай msgbill баримтыг ЦУЦАЛНА. Терминал баримт хэвлээгүй (унтарсан, цаасгүй, Ontime "
        "салгасан) борлуулалт байвал цуцалсны дараа тэр нь e-Barimt-ГҮЙ үлдэнэ — ТЕГ экспортоор "
        "(Ибаримт → ТЕГ тулгалт → terminal_missing) шалгаж, «Систем баримт гаргах»-аар нөхнө.")
    return total


def main_pos_app(args):
    """--pos-app: ТЕГ файлгүй, эзний шийдвэрийн дүрмээр сонгож цуцлах."""
    if not args.tenant:
        raise SystemExit("--pos-app үед --tenant заавал (ж: EASYPARKING — ИйзиПаркинг ХХК)")
    username = args.user or f"cli:{os.getenv('SUDO_USER') or getpass.getuser()}"
    out = args.out or f"vat_posapp_{datetime.now():%Y%m%d_%H%M%S}.csv"
    stem = out[:-4] if out.lower().endswith(".csv") else out
    note = args.note if args.note != NOTE_DEFAULT else POSAPP_NOTE
    db = SessionLocal()
    _read_only(db)
    try:
        tenant = resolve_tenant(db, args.tenant)
        try:
            sites = VD.pos_app_sites(db, tenant, args.site)
        except ValueError as e:
            raise SystemExit(str(e))
        site_users = VD.pos_users_for_sites(db, sites, override=VD.parse_pos_user_override(args.pos_user),
                                            min_card=args.min_card)
        lo, hi = VD.ub_window(args.date_from, args.date_to)
        loc = timedelta(hours=settings.tz_offset_hours)
        print(f"{TOOL} --pos-app: {'APPLY' if args.apply else 'DRY-RUN'} · DB "
              f"{settings.database_url.rsplit('/', 1)[-1]} · түрээслэгч {tenant.code} ({tenant.name})"
              f" · төлсөн {(lo + loc):%Y-%m-%d} → {(hi + loc):%Y-%m-%d %H:%M} (УБ)"
              + (" · ААН/ТТД-тэй ОРНО" if args.include_company else ""))
        if tenant.code not in VD.POS_APP_TENANTS:
            print(f"⚠ «{tenant.code}» эзний шийдвэрт (ИйзиПаркинг ХХК) хамаарахгүй — --apply татгалзана")
        print("POS аппын хэрэглэгчид (зогсоол бүрээр):")
        for s_ in sites:
            info = site_users[s_.id]
            if not info["users"] and not info["derived"]:
                continue
            der = ", ".join(f"{u} {n}" for u, n in sorted(info["derived"].items(), key=lambda kv: -kv[1]))
            print(f"  {s_.site_code:<12} [{info['source']}] {', '.join(sorted(info['users'])) or '—'}"
                  + (f"  (POS карт: {der})" if der else "")
                  + ("" if info["methods"] == set(VD.POS_APP_METHODS)
                     else f"  · тохиргоо: зөвхөн {','.join(sorted(info['methods']))}"))
        low = {s_.site_code: info["below"] for s_ in sites
               for info in [site_users[s_.id]] if info["below"]}
        if low:
            print("  ⚠ босгоос доош POS карттай хэрэглэгч (POS аппынх гэж ТООЦООГҮЙ — "
                  "хэрэгтэй бол --pos-user): " + " · ".join(
                      f"{k}: " + ", ".join(f"{u} {n}" for u, n in v.items()) for k, v in sorted(low.items())))
        skipped_test = sorted(c for c in VD.POS_APP_TEST_SITES
                              if c not in {(x.site_code or '').upper() for x in sites})
        if skipped_test:
            print(f"  (тест зогсоол алгасав: {', '.join(skipped_test)} — --site-аар нэрлэвэл орно)")
        t0 = time.monotonic()
        sel = VD.select_pos_app(db, sites=sites, lo=lo, hi=hi, site_users=site_users,
                                include_company=args.include_company)
        total = print_pos_app_summary(sel)
        rows = [{**r, "kind": "cancel"} for r in sel["selected"]]
        no_ontime = []
        for f in args.evidence or []:
            # НЭМЭЛТ нотолгоо: ТЕГ экспортын хугацаанд Ontime мөргүй төлбөрийг ЦУЦЛАХГҮЙ
            tax, _diag = VR.parse_tax_export(os.path.basename(f), open(f, "rb").read())
            shifts = [0.0, -float(settings.tz_offset_hours)]
            ev = VD.pos_app_evidence(db, tax, rows, tol=args.tol, shifts=shifts,
                                     site_ids={s_.id for s_ in sites})
            pct = (len(ev["with_ontime"]) / ev["inside"]) if ev["inside"] else 0
            print(f"\nНОТОЛГОО {os.path.basename(f)}: хугацаанд {ev['inside']} төлбөр · Ontime мөртэй "
                  f"{len(ev['with_ontime'])} ({pct:.1%}) · Ontime мөргүй {len(ev['missing'])} — ЦУЦЛАХГҮЙ"
                  + (f" · өөр кассууд {', '.join(ev['foreign_registers'])}" if ev["foreign_registers"] else
                     " · ⚠ файлд өөр систем (Ontime) касс АЛГА"))
            for r in ev["missing"][:20]:
                print(f"    баримтгүй үлдэх байсан: {r['paid_at_ub']} {r['site_code']} {r['plate'] or '—'} "
                      f"{r['method']} {r['amount']:,.0f}₮ ({r['cashier']})")
            no_ontime += ev["missing"]
        if no_ontime:
            drop = {r["payment_id"] for r in no_ontime}
            rows = [r for r in rows if r["payment_id"] not in drop]
            write_csv(f"{stem}_no_ontime.csv", no_ontime, POSAPP_FIELDS)
            print(f"Ontime нотолгоогүй {len(drop)} төлбөрийг жагсаалтаас хасав → {stem}_no_ontime.csv"
                  f" · цуцлах {len(rows)} ({sum(r['amount'] for r in rows):,.0f}₮)")
        # Цуцлах мөр ЭХЭНДЭЭ (--limit 1 туршилт msgbill цуцлалтыг шалгана), дараа нь
        # өмнө цуцлагдсан боловч терминалын мөр дутуу (msgbill дуудахгүй) нөхөлт
        work = rows + [{**r, "kind": "complete_terminal"} for r in sel["complete"]]
        write_csv(out, work, POSAPP_FIELDS)
        write_csv(f"{stem}_company.csv", sel["company"], POSAPP_FIELDS)
        print(f"\nCSV: {out} ({len(work)}) · {stem}_company.csv ({len(sel['company'])})"
              f" · {time.monotonic() - t0:.1f}с")
        sites_used = [s_ for s_ in sites if any(r["site_id"] == s_.id for r in work)]
        if not args.apply:
            probs = VD.cancel_ready(db, sites_used)
            if probs:
                print("\n⚠ --apply ОДООГООР ажиллахгүй: " + "; ".join(probs))
            print("\nDRY-RUN — юу ч өөрчлөгдсөнгүй. Цуцлахын тулд ижил командыг --apply --user <нэр> "
                  "(--max <тоо>) -тэй ажиллуулна.")
            return 0
        if tenant.code not in VD.POS_APP_TENANTS:
            print(f"\nЗОГСОВ: --pos-app зөвхөн {', '.join(sorted(VD.POS_APP_TENANTS))} (эзний шийдвэр)")
            return 6
        if args.limit is not None:
            work = work[:max(0, args.limit)]
        # --max нь ЭНЭ УДАА бодитоор цуцлах тоонд (--limit 1 туршилт 8,000+ сонголттой ч болно)
        if len(work) > args.max:
            print(f"\nЗОГСОВ: {len(work)} төлбөр > --max {args.max}. Тоог шалгаад --max-ийг ил тодоор өсгөнө үү.")
            return 2
        if not work:
            print("\nЦуцлах төлбөр алга.")
            return 0
        probs = VD.cancel_ready(db, sites_used)
        if probs:
            print("\nЗОГСОВ — цуцлах боломжгүй: " + "; ".join(probs))
            return 3
        scopes = VD.account_scopes(sites_used)
    finally:
        db.rollback()
    amount = sum(r["amount"] for r in work)
    cells = _breakdown(work)
    print(f"\nЦУЦЛАХ ГЭЖ БАЙНА (POS аппын дүрэм): DB {VD.db_name()} · сервер {settings.public_base_url}"
          f" · түрээслэгч {tenant.code} · {len(work)} төлбөр ({amount:,.0f}₮)")
    for (site, month, method), (n, amt) in cells.items():
        print(f"  {site:<12} {month} {method:<4} {n:6d}  {amt:12,.0f}₮")
    print("  msgbill түлхүүр (оролдох дараалал): "
          + " · ".join(f"{k}: {' → '.join(v) or '—'}" for k, v in scopes.items()))
    print("  Ontime баримт хэвлэгдээгүй борлуулалт байвал e-Barimt-ГҮЙ үлдэнэ.")
    if not args.yes:
        if not sys.stdin.isatty():
            print("\nЗОГСОВ: баталгаажуулах терминал алга — --yes өгнө үү.")
            return 4
        ans = input(f"\n{len(work)} төлбөрийн манай msgbill баримтыг ({amount:,.0f}₮) ТЕГ-т ЦУЦЛАХ уу? "
                    "Мөнгө буцаахгүй. Зөвшөөрвөл YES гэж бичнэ үү: ").strip()
        if ans != "YES":
            print("Цуцалсангүй.")
            return 5
    print(f"\nЦуцалж байна ({len(work)}, {amount:,.0f}₮) — хэрэглэгч {username}, шалтгаан «{note}»")
    guard = VD.pos_app_guard(site_users, {s_.id for s_ in sites}, args.include_company)
    res_rows, cnt, stopped = asyncio.run(apply_cancel(
        db, work, note=note, username=username, delay=args.delay, source="cli-pos-app",
        mark_extra={"rule": VD.POS_APP_RULE}, guard=guard, allow_company=bool(args.include_company)))
    try:
        db.add(AuditLog(username=username, action="EBARIMT_CANCEL_BULK_SUMMARY", entity="vat",
                        entity_id="DUPLICATE_OF_POS_TERMINAL",
                        detail={"tool": TOOL, "mode": "pos-app", "rule": VD.POS_APP_RULE,
                                "tenant": tenant.code, "sites": [s_.site_code for s_ in sites],
                                "from": args.date_from, "to": args.date_to,
                                "include_company": bool(args.include_company),
                                "evidence": [os.path.basename(f) for f in args.evidence or []],
                                "requested": len(work), "amount": amount, "note": note,
                                "result": dict(cnt), "stopped": stopped}))
        db.commit()
    finally:
        db.close()
    write_csv(f"{stem}_applied.csv", res_rows, POSAPP_FIELDS + ["outcome", "error", "key_scope"])
    print(f"\nДҮН: {dict(cnt)}" + (f"\nЗОГССОН: {stopped}" if stopped else "")
          + f"\nCSV: {stem}_applied.csv\nДахин ажиллуулахад цуцлагдсан нь алгасагдана (идемпотент).")
    return 0 if not cnt.get("failed") and not stopped else 1


NOTE_DEFAULT = ("ТЕГ тулгалт: POS терминал (касс) дээр баримт хэвлэгдсэн тул msgbill дэх давхардлыг "
                "цуцлав")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--file", action="append", help="ТЕГ экспорт (.xlsx/.csv); олон удаа өгч болно")
    mode.add_argument("--pos-app", action="store_true",
                      help="ТЕГ файлгүй, ДҮРМЭЭР: POS аппын карт/бэлэн борлуулалтын msgbill баримт "
                           "(2026-10-13 эзний шийдвэр; --tenant заавал)")
    ap.add_argument("--tenant", help="түрээслэгч (id эсвэл code) — тэр ТТД-ийн экспорт бол")
    ap.add_argument("--site", action="append", help="зөвхөн энэ зогсоол(ууд)-ын давхардал (site_code)")
    ap.add_argument("--from", dest="date_from", help="төлсөн огноо (УБ) ≥ YYYY-MM-DD")
    ap.add_argument("--to", dest="date_to", help="төлсөн огноо (УБ) ≤ YYYY-MM-DD")
    ap.add_argument("--tol", type=int, default=VR.DEFAULT_TOL, help="цагийн зөрүү (сек), анхдагч 60")
    ap.add_argument("--tz-shift", type=float, default=None,
                    help="файлын цагт нэмэх цаг (анхдагч: касс бүрд 0/−8-аас автоматаар)")
    ap.add_argument("--apply", action="store_true", help="БОДИТООР цуцлах (анхдагч dry-run)")
    ap.add_argument("--note", default=NOTE_DEFAULT, help="цуцлах шалтгаан")
    ap.add_argument("--user", default=None, help="AuditLog-д бичих хэрэглэгч (анхдагч cli:<OS хэрэглэгч>)")
    ap.add_argument("--limit", type=int, default=None, help="эхний N-ийг л цуцлах (туршилтад)")
    ap.add_argument("--max", type=int, default=2000, help="үүнээс олон бол зогсоно (анхдагч 2000)")
    ap.add_argument("--out", default=None, help="CSV тайлангийн зам")
    ap.add_argument("--yes", action="store_true", help="баталгаажуулалтын асуултыг алгасах")
    ap.add_argument("--delay", type=float, default=0.5, help="цуцлалт хоорондын завсар (сек)")
    ap.add_argument("--include-company", action="store_true",
                    help="--pos-app: ААН/ТТД-тэй баримтыг ч цуцлах (анхдагч ХӨНДӨХГҮЙ)")
    ap.add_argument("--pos-user", action="append",
                    help="--pos-app: POS аппын хэрэглэгч(ид) — «pos.a,pos.b» (бүх зогсоолд) эсвэл "
                         "«HANGARID=pos.khangarid»; тохиргоо/≥20 картын таамгийг дарна")
    ap.add_argument("--min-card", type=int, default=VD.POS_APP_MIN_CARD,
                    help="--pos-app: тохиргоо хоосон зогсоолд POS-оор ≥N карт авсан хэрэглэгч (20)")
    ap.add_argument("--evidence", action="append",
                    help="--pos-app: НЭМЭЛТ нотолгоо — ТЕГ экспорт (олон удаа); түүний хугацаанд "
                         "Ontime мөргүй төлбөрийг цуцлахгүй (терминал хэвлээгүй нь батлагдсан)")
    args = ap.parse_args(argv)
    if args.pos_app:
        return main_pos_app(args)
    if args.include_company or args.pos_user or args.evidence:
        ap.error("--include-company/--pos-user/--evidence зөвхөн --pos-app-тай")

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
        if args.limit is not None:
            dups = dups[:max(0, args.limit)]
        # --max нь ЭНЭ УДАА бодитоор цуцлах тоонд (--limit-ийн дараа)
        if len(dups) > args.max:
            print(f"\nЗОГСОВ: {len(dups)} давхардал > --max {args.max}. Тоог шалгаад --max-ийг ил тодоор өсгөнө үү.")
            return 2
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
