"""QPay e-Barimt-ын REVIEW баримтыг нөхөх — QR борлуулалт бүрд ЯГ НЭГ e-Barimt (2026-10-15).

Яагаад: 10-р сард EasyParking-ийн ~40 QR төлбөрийн (≈119,500₮; 10-08 11:00–11:10 УБ-д 21)
QPAY баримт REVIEW төлөвт гацсан — outbox-ын ebarimt_v3/create HTTPStatusError буцааж ажил
UNKNOWN болсон (QPay create-ийн дахин илгээлт идемпотент гэж батлагдаагүй тул хорио). «Дахин
үүсгэх» review_required өгдөг; ганц зам нь SUPER_ADMIN-ий ebarimt-diag {create:true} байв.
Энэ хэрэгсэл ЯГ ТЭР код замыг (services/qpay_review.create_and_assign — endpoint-тэй нэг
функц) төлбөр бүрд дараалан, түгжээтэй, аудиттай ажиллуулна.

АНХДАГЧ = DRY-RUN (READ ONLY транзакц, QPay/сүлжээ огт дуудахгүй): төлбөр бүрийн мөр (УБ цаг,
зогсоол, дүн, ТТД, баримт/ажлын төлөв, сүүлийн алдаа, ТЕГ нотолгоо, үйлдэл) + дүн.
  --file <ТЕГ экспорт .xlsx/.csv> (олон удаа): QPay-ийн кассын (…10014863, UTC) мөрөөр
  «баримт аль хэдийн ТЕГ-т бий юу» (services/qpay_review.teg_evidence; задлагч нь
  vat_reissue_missing/vat_duplicates-тэй НЭГ — vat_recon.parse_tax_export):
    • IN_TEG → QPay-г ДУУДАХГҮЙ, ТЕГ-ийн ДДТД-г холбоно (ATTACH_TEG) — ЗӨВХӨН --attach-teg-ээр
      (YES асуултад ДДТД бүрийг жагсаана); аль мөр нь вэ тодорхойгүй бол (IN_TEG_AMBIG) гараар;
    • NOT_IN_TEG → CREATE; AMBIGUOUS (ойролцоо эзэнгүй мөр, (өдөр × дүн)-д эзэнгүй мөр, ижил
      дүнгийн тэнцэл тэр/хожуу өдөр ≥1) → --include-ambiguous-гүйгээр алгасна;
    • файлын хүрээ/түрээслэгчээс гадуур → --include-uncovered-гүйгээр алгасна.
  Файлгүй бол CREATE нь ТЕГ-ийн шалгалтгүй — --apply нь --no-teg-evidence-гүйгээр ТАТГАЛЗАНА.

--apply: tools/vat_reissue_missing.py-тэй ИЖИЛ хамгаалалт — зөвхөн ажиллаж буй «parking» DB +
прод сервер (audit*/t_* хуулбар, ТЕСТ сервер, PARKING_DEPLOY_ROLE=staging → татгалзана);
QPay MOCK/sandbox/тохируулаагүй данс → татгалзана; --max (200)-аас олон бол зогсоно; DB,
сервер, түрээслэгч, тоог хэвлээд TTY дээр «YES» (том үсгээр) (--yes-ээр алгасна). Төлбөр
бүрд: төлбөр/ажил/баримтын мөрийг ТҮГЖИЖ (NOWAIT) дахин шалгана → QPay-г дуудахаас ӨМНӨ
оролдлогыг (баримтын raw.qpay_review_attempts + AuditLog EBARIMT_QPAY_REVIEW_ATTEMPT) commit →
дахин түгжиж шалгана → create_and_assign → ДДТД-г ebarimt-diag-тай ИЖИЛ байдлаар бичиж SENT
(+ ажил DONE, partner-д receipt.ready); QPay алдаа → REVIEW хэвээр, оролдлогод үр дүн (billId-тэй);
--delay (0.5с) завсар; AuditLog EBARIMT_QPAY_REVIEW_RESOLVE (төлбөр бүрд) +
EBARIMT_QPAY_REVIEW_SUMMARY. QPAY-ээс бусад суваг, msgbill баримт, customer_tin-д ХҮРЭХГҮЙ.

ИДЕМПОТЕНТ БИШ БАЙЖ БОЛЗОШГҮЙ: QPay ebarimt_v3/create-д idempotency key алга (цорын ганц
түлхүүр нь QPay payment_id), GET-ээр урьдчилан шалгах зам алга; 2026-08-19-нд нэг QPay
төлбөрт зэрэгцээ 2 дуудлага 2 өөр баримт үүсгэсэн (дэлгэрэнгүйг services/qpay_review-ийн
тайлбараас). Тиймээс CREATE-тэй --apply нь --i-understand-not-idempotent шаардана, ба
QPay-г нэг удаа дуудсан төлбөрийг (timeout/5xx/billId-гүй/ДДТД биш/хадгалагдаагүй — QPay 4xx-ээр
тодорхой татгалзсанаас бусад) дахин ажиллуулахад АЛГАСНА. Дахин оролдох нь ЗӨВХӨН
--retry-attempted + сүүлийн оролдлогоос ≥24ц хойш хамарсан ТЕГ экспорт (--file)-оор (тэр үеийн
эзэнгүй ТЕГ мөр байвал гараар холбоно).

⚠ ebarimt-diag {create:true}-г эдгээр төлбөр дээр ЭНЭ ХЭРЭГСЭЛТЭЙ ЗЭРЭГ ч, ДАРАА нь ч БҮҮ
ашигла — тэр endpoint түгждэггүй, оролдлогын бүртгэлийг шалгадаггүй (давхар баримт гарна).

    cd /root/PARKING/backend
    venv/bin/python tools/qpay_review_resolve.py --from 2026-10-01 --tenant EASYPARKING \\
        --file /root/teg_oct.xlsx                                                       # харах
    venv/bin/python tools/qpay_review_resolve.py --from 2026-10-01 --tenant EASYPARKING \\
        --file /root/teg_oct.xlsx --apply --limit 1 --user temuujin --i-understand-not-idempotent  # туршилт
    venv/bin/python tools/qpay_review_resolve.py --from 2026-10-01 --tenant EASYPARKING \\
        --file /root/teg_oct.xlsx --apply --user temuujin --i-understand-not-idempotent            # бүгд

Дахин ажиллуулахад SENT болсон болон QPay-г дуудсан (үр дүн тодорхойгүй) нь сонголтоос хасагдана.
Мөнгө хөдлөхгүй.
"""
import argparse
import asyncio
import getpass
import os
import sys
from collections import Counter
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import AuditLog  # noqa: E402
from app.routers import vat_recon as VR  # noqa: E402
from app.services import qpay_review as QR  # noqa: E402
from app.services import vat_duplicates as VD  # noqa: E402

TOOL = "qpay_review_resolve"
FIELDS = ["payment_id", "receipt_id", "paid_at_ub", "day", "tenant", "site_code", "plate", "amount",
          "customer_tin", "receiver_type", "receipt_status", "job_status", "job_attempts", "last_error",
          "attempts", "attempt_outcome", "attempt_last_ub", "attempt_bills",
          "teg", "teg_ddtd", "teg_near", "teg_surplus", "account", "action", "note", "skip"]

EPILOG = """АЮУЛГҮЙ БАЙДАЛ:
  • QPay-г дуудахаас ӨМНӨ оролдлого commit хийгдэнэ; timeout/5xx/billId-гүй/ДДТД биш/хадгалагдаагүй
    оролдлоготой төлбөрийг дахин ажиллуулахад АЛГАСНА (QPay 4xx-ээр тодорхой татгалзсанаас бусад).
    Дахин оролдох: --retry-attempted + сүүлийн оролдлогоос ≥24ц хойш хамарсан ТЕГ экспорт (--file).
  • CREATE-тэй --apply: --i-understand-not-idempotent; ТЕГ файлын нотолгоогүй CREATE (файлгүй/
    хүрээнээс гадуур) нь --no-teg-evidence-гүйгээр татгалзана; IN_TEG холболт нь --attach-teg-ээр л.
  • ebarimt-diag {create:true}-г эдгээр төлбөр дээр ЗЭРЭГ ч, ДАРАА нь ч БҮҮ ашигла (түгждэггүй,
    оролдлогын бүртгэлийг шалгадаггүй — давхар баримт гарна)."""


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


def _short(s, n):
    s = str(s or "—").replace("\n", " ")
    return s if len(s) <= n else s[:n - 1] + "…"


def print_table(rows, log=print):
    log(f"  {'#':>3} {'төлсөн (УБ)':<19} {'зогсоол':<14} {'дүн':>9}  {'ТТД/регистр':<11} "
        f"{'баримт':<6} {'ажил/оролдлого':<14} {'сүүлийн алдаа':<18} {'ТЕГ':<12} үйлдэл")
    for i, c in enumerate(rows, 1):
        job = f"{c['job_status'] or '—'}/{c['job_attempts']}"
        log(f"  {i:>3} {c['paid_at_ub']:<19} {_short(c['site_code'], 14):<14} {c['amount']:>8,.0f}₮  "
            f"{_short(c['customer_tin'], 11):<11} {c['receipt_status']:<6} {job:<14} "
            f"{_short(c['last_error'], 18):<18} {c['teg']:<12} {c['action']}"
            + (f"  (ТЕГ {c['teg_ddtd'][-12:]})" if c.get("teg_ddtd") else "")
            + (f"  [QPay оролдлого {c['attempts']}: {c['attempt_outcome']}]" if c.get("attempts") else "")
            + (f"  ← {c['note']}" if c.get("note") else ""))


def print_evidence(ev, tcode, log=print):
    log("\nТЕГ НОТОЛГОО (QPay-ийн касс …%s, цаг UTC):" % QR.QPAY_REGISTER)
    for st in ev["files"]:
        win = st["window"]
        span = (f"{QR.ub(win[0]):%Y-%m-%d %H:%M}…{QR.ub(win[1]):%Y-%m-%d %H:%M} УБ" if win else "—")
        log(f"  {st['file']}: {st['rows']} мөр · QPay {st['qpay_rows']} · {span} · "
            + (f"түрээслэгч {tcode.get(st['tenant_id'], st['tenant_id'])} "
               f"({st['matched']}/{st['qpay_rows'] - st.get('clipped', 0)} = {st['share']:.0%} манай баримттай таарсан)"
               if st["usable"] else f"НОТОЛГОО БИШ — {st['note']}"))
        if st.get("clipped"):
            log(f"    ⚠ DB-ийн сүүлийн PAID төлбөр {QR.ub(ev['db_last_paid']):%Y-%m-%d %H:%M} УБ — түүнээс хойших "
                f"(−tol−60с) {st['clipped']} QPay мөрийг тулгалтад оруулаагүй (хуулбар DB)")
    for tid, t in ev["tenants"].items():
        sur = sorted(t["surplus"].items())
        log(f"  {tcode.get(tid, tid)}: ТЕГ QPay {t['rows']} мөр · манай ДДТД-тэй {t['ours']} · эзэнгүй мөр "
            f"{t['orphans']} · ТЕГ мөргүй манай баримт {t['lonely']} · (УБ өдөр × дүн) тэнцэл зөрсөн {len(sur)}"
            + (":" if sur else " (бүх өдөр тэнцүү)"))
        for i in range(0, len(sur), 6):         # бүтэн жагсаалт (+ = ТЕГ-т илүү мөр, − = ТЕГ мөргүй баримт)
            log("      " + " · ".join(f"{d} {a:,}₮ {v:+d}" for (d, a), v in sur[i:i + 6]))


def summarize(rows, key, log=print, title=""):
    by = {}
    for c in rows:
        k = c[key]
        n, a = by.get(k, (0, 0.0))
        by[k] = (n + 1, a + c["amount"])
    if title:
        log(title)
    for k, (n, a) in sorted(by.items(), key=lambda x: -x[1][0]):
        log(f"    {k:<17} {n:4d}  {a:12,.0f}₮")


def account_state(db, rows):
    """Төлбөр авсан QPay данс (ажлын payload-ийн fingerprint) ↔ одоогийн данс. Сүлжээгүй."""
    from app.models import Payment
    from app.services.financial_jobs import _account, payment_site
    for c in rows:
        try:
            _acc, fp = _account("QPAY", payment_site(db.get(Payment, c["payment_id"])))
        except Exception:  # noqa: BLE001
            c["account"] = "алдаа"
            continue
        c["account"] = "ok" if not c.get("fingerprint") or c["fingerprint"] == fp else "зөрсөн"


def max_hint(n, cap):
    return (f"\n⚠ --max: {n} төлбөр > --max {cap} (анхдагч хамгаалалт). Тоо зөв бол --apply-д "
            f"--max {n} нэмнэ үү, эсвэл --limit-ээр хэсэгчлэн (--limit 1 → --limit 20 → бүгд; "
            "дахин ажиллуулахад SENT болсон болон QPay-г дуудсан нь хасагдана).")


NOT_IDEMPOTENT_MSG = (
    "\nЗОГСОВ: CREATE {n} төлбөрт QPay ebarimt_v3/create-г ДАХИН дуудна. QPay-ийн create "
    "идемпотент гэж КОДООР батлагдаагүй (idempotency key алга, урьдчилан шалгах GET алга; "
    "2026-08-19-нд нэг QPay төлбөрт 2 өөр баримт үүсгэж байсан). ТЕГ экспортоор (--file) ТЕГ-т "
    "БАЙХГҮЙ нь батлагдсан эсэхийг шалгаад --i-understand-not-idempotent нэмж дахин ажиллуулна уу.")

NO_EVIDENCE_MSG = (
    "\nЗОГСОВ: {n} CREATE ТЕГ экспортын нотолгоогүй ({kinds}) — QPay талд баримт аль хэдийн "
    "үүссэн эсэхийг шалгаагүй. Түрээслэгч бүр, төлбөр бүрийн огноог хамарсан ТЕГ экспортыг "
    "--file-ээр өгнө үү; эсвэл нотолгоогүйгээр create хийхийг ИЛ зөвшөөрөх бол --no-teg-evidence.")

ATTEMPT_HINT = (
    "    ↳ ATTEMPTED: өмнө QPay create дуудагдсан, үр дүн ТОДОРХОЙГҮЙ — QPay талд баримт үүссэн байж "
    "болзошгүй. ДАХИН create бүү хий: сүүлийн оролдлогоос ≥24ц хойш хамарсан ТЕГ экспортоор шалга — "
    "ТЕГ-т бий бол «баримт холбох», байхгүй нь батлагдвал --retry-attempted --file <шинэ экспорт>. "
    "ebarimt-diag create-г эдгээр дээр БҮҮ ашигла.")


def evidence_kinds(rows):
    return ", ".join(f"{k} {v}" for k, v in Counter(c["teg"] for c in rows).most_common())


async def apply_all(db, work, *, username, include_failed, delay, retry_attempted=False, log=print):
    cnt, out = Counter(), []
    for i, c in enumerate(work, 1):
        try:
            res = await QR.resolve_one(db, c, action=c["action"], username=username,
                                       include_failed=include_failed, evidence=c.get("evidence"),
                                       retry_attempted=retry_attempted)
        except Exception as e:  # noqa: BLE001 — нэг төлбөрийн алдаа бусдыг зогсоохгүй
            db.rollback()
            res = {"outcome": "FAILED", "error": f"{type(e).__name__}: {str(e)[:200]}",
                   "attempt_outcome": "UNKNOWN" if c["action"] == "CREATE" else None}
        oc, ao = res.get("outcome"), res.get("attempt_outcome")
        if oc == "SENT":
            cnt["sent"] += 1
            msg = f"SENT {res.get('ebarimt_id')}" + (f" · сугалаа {res['lottery']}" if res.get("lottery") else "")
        elif oc == "SKIPPED":
            cnt["skipped"] += 1
            msg = f"АЛГАСАВ ({QR.SKIP_MN.get(res.get('reason'), res.get('reason'))})" + (
                f": {res['error']}" if res.get("error") else "")
        else:
            cnt["failed"] += 1
            msg = f"АЛДАА (REVIEW хэвээр): {res.get('error')}" + (
                f" · QPay billId {res['bill_id']}" if res.get("bill_id") else "")
        if ao and ao not in ("SENT", "NOT_CALLED"):
            cnt["attempt_definite" if ao in QR.DEFINITE else "attempt_uncertain"] += 1
            msg += f" · оролдлого {ao} ({QR.ATTEMPT_MN.get(ao, ao)})"
        cnt[f"{c['action']}:{oc}"] += 1
        log(f"  [{i}/{len(work)}] {c['paid_at_ub']} {c['site_code']} {c['amount']:,.0f}₮ "
            f"{c['action']} → {msg}")
        out.append({**c, "outcome": oc, "ebarimt_id": res.get("ebarimt_id") or "",
                    "bill_id": res.get("bill_id") or "", "attempt_result": ao or "",
                    "error": res.get("error") or ""})
        if delay and c["action"] == "CREATE" and oc != "SKIPPED" and i < len(work):
            await asyncio.sleep(delay)
    return cnt, out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], epilog=EPILOG,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tenant", required=True, help="түрээслэгч (code/нэр/id) эсвэл ALL")
    ap.add_argument("--site", action="append", help="зөвхөн энэ зогсоол(ууд) (site_code)")
    ap.add_argument("--from", dest="date_from", default=None, help="УБ огноо YYYY-MM-DD (paid_at, хамааруулна)")
    ap.add_argument("--to", dest="date_to", default=None, help="УБ огноо YYYY-MM-DD (хамааруулна)")
    ap.add_argument("--payment", action="append", help="зөвхөн энэ төлбөр(үүд) (payment id)")
    ap.add_argument("--include-failed", action="store_true",
                    help="FAILED QPAY баримтыг ч (тохиргоогоор хаагдсан BLOCKED-оос бусад)")
    ap.add_argument("--file", action="append", default=[], help="ТЕГ экспорт (.xlsx/.csv) — нотолгоо")
    ap.add_argument("--tol", type=int, default=QR.DEFAULT_TOL, help="манай баримт ↔ ТЕГ мөр (сек), анхдагч 60")
    ap.add_argument("--teg-before-min", type=float, default=20.0, help="ТЕГ мөр paid_at-аас өмнө (мин)")
    ap.add_argument("--teg-after-min", type=float, default=5.0, help="ТЕГ мөр paid_at-аас хойш (мин)")
    ap.add_argument("--include-ambiguous", action="store_true", help="ТОДОРХОЙГҮЙ-г ч create хийх")
    ap.add_argument("--include-uncovered", action="store_true",
                    help="ТЕГ файлын хүрээнээс гадуурхыг ч create хийх (--apply-д --no-teg-evidence ч шаардана)")
    ap.add_argument("--attach-teg", action="store_true",
                    help="ТЕГ-т БИЙ (IN_TEG) баримтын ТЕГ-ийн ДДТД-г холбох (QPay дуудахгүй; YES асуултад жагсаана)")
    ap.add_argument("--no-teg-evidence", action="store_true",
                    help="ТЕГ файлын нотолгоогүй CREATE-г (файлгүй/хүрээнээс гадуур) ИЛ зөвшөөрөх — "
                         "YES асуулт, аудитад тэмдэглэнэ")
    ap.add_argument("--retry-attempted", action="store_true",
                    help="өмнө QPay create дуудсан, үр дүн тодорхойгүй төлбөрийг ДАХИН оролдох — --file "
                         "заавал, файл сүүлийн оролдлогоос ≥24ц хойш хамарч, ТЕГ-т алга (NOT_IN_TEG) байх ёстой")
    ap.add_argument("--apply", action="store_true", help="БОДИТООР нөхөх (анхдагч dry-run)")
    ap.add_argument("--i-understand-not-idempotent", dest="not_idempotent_ok", action="store_true",
                    help="QPay create идемпотент биш байж болохыг ойлгосон (CREATE-д заавал)")
    ap.add_argument("--user", default=None, help="AuditLog-д (анхдагч cli:<OS хэрэглэгч>)")
    ap.add_argument("--limit", type=int, default=None, help="эхний N-ийг л (туршилтад)")
    ap.add_argument("--max", type=int, default=200, help="үүнээс олон бол зогсоно (анхдагч 200)")
    ap.add_argument("--delay", type=float, default=0.5, help="QPay дуудлага хоорондын завсар (сек)")
    ap.add_argument("--yes", action="store_true", help="баталгаажуулалтын асуултыг алгасах")
    ap.add_argument("--out", default=None, help="CSV тайлангийн зам (өгвөл)")
    args = ap.parse_args(argv)
    for d in (args.date_from, args.date_to):
        if d:
            try:
                datetime.strptime(d, "%Y-%m-%d")
            except ValueError:
                raise SystemExit(f"Огноо буруу: {d} (YYYY-MM-DD)")
    for f in args.file:
        if not os.path.isfile(f):
            raise SystemExit(f"Файл олдсонгүй: {f}")
    if args.retry_attempted and not args.file:
        raise SystemExit("--retry-attempted нь ТЕГ экспорт (--file) шаардана — сүүлийн оролдлогоос ≥24ц "
                         "хойш хамарсан экспортоор QPay талд баримт үүсээгүйг батална.")
    username = args.user or f"cli:{os.getenv('SUDO_USER') or getpass.getuser()}"
    taxes = []
    for f in args.file:
        try:
            tax, _diag = VR.parse_tax_export(os.path.basename(f), open(f, "rb").read())
        except Exception as e:  # noqa: BLE001 — HTTPException(400) тайлбартай
            raise SystemExit(f"ТЕГ файл уншигдсангүй ({f}): {getattr(e, 'detail', e)}")
        taxes.append((os.path.basename(f), tax))
    files = [n for n, _t in taxes]
    lo, hi = QR.ub_window(args.date_from, args.date_to)
    before, after = timedelta(minutes=args.teg_before_min), timedelta(minutes=args.teg_after_min)

    db = SessionLocal()
    _read_only(db)
    try:
        try:
            tenants = VD.resolve_tenants(db, args.tenant)
        except ValueError as e:
            raise SystemExit(str(e))
        sites = VD.tenant_sites(db, tenants)
        if args.site:
            want = {c.upper() for c in args.site}
            sites = [s for s in sites if (s.site_code or "").upper() in want]
            missing = want - {(s.site_code or "").upper() for s in sites}
            if missing:
                raise SystemExit(f"«{VD.tenant_label(tenants)}»-ийн зогсоол биш/олдсонгүй: "
                                 f"{', '.join(sorted(missing))}")
        tcode = {t.id: t.code for t in tenants}
        print(f"{TOOL}: {'APPLY' if args.apply else 'DRY-RUN'} · DB {VD.db_name()} · түрээслэгч "
              f"{VD.tenant_label(tenants)} · {len(sites)} зогсоол · хугацаа (УБ) {args.date_from or '…'}"
              f"…{args.date_to or '…'} · {'REVIEW+FAILED' if args.include_failed else 'REVIEW'} · "
              f"файл {len(files)}" + (" · --retry-attempted" if args.retry_attempted else ""))
        sel = QR.select_candidates(db, site_ids=[s.id for s in sites], lo=lo, hi=hi,
                                   include_failed=args.include_failed, payment_ids=args.payment,
                                   retry_attempted=args.retry_attempted)
        cands = sel["candidates"]
        if taxes:
            ev = QR.teg_evidence(db, taxes, cands, tol=args.tol, before=before, after=after)
            ev_tcode = {**{t.id: t.code for t in VD.resolve_tenants(db, "ALL")}, **tcode}
            print_evidence(ev, ev_tcode)
        else:
            ev = None
        for c in cands:
            info = ev["verdicts"][c["receipt_id"]] if ev else {"teg": "NO_FILE"}
            c["evidence"] = info
            c["teg"] = info["teg"]
            c["teg_ddtd"] = info.get("teg_ddtd") or ""
            c["teg_near"] = info.get("near", "")
            c["teg_surplus"] = info.get("surplus", "")
            c["action"] = QR.plan_action(c["teg"], include_ambiguous=args.include_ambiguous,
                                         include_uncovered=args.include_uncovered,
                                         attach_teg=args.attach_teg, attempted=c.get("attempt_block", False))
            c["note"] = ""
            if c["teg"] == "IN_TEG" and not args.attach_teg:
                c["note"] = "--attach-teg-ээр ТЕГ-ийн ДДТД-г холбоно"
            elif c.get("attempt_block") and c["action"] == "SKIP":
                c["note"] = "өмнөх оролдлого тодорхойгүй — ТЕГ нотолгоо хангалтгүй"
            elif c["action"] == "CREATE" and c["teg"] in QR.NO_EVIDENCE:
                c["note"] = "ТЕГ нотолгоогүй (--no-teg-evidence)"
        account_state(db, cands)
        total = sum(c["amount"] for c in cands)
        print(f"\nСОНГОГДСОН: {len(cands)} төлбөр · {total:,.0f}₮ (QPAY "
              f"{'REVIEW/FAILED' if args.include_failed else 'REVIEW'}, PAID)")
        if cands:
            print_table(cands)
        if sel["skipped"]:
            print("\nХАСАГДСАН (нөхөх боломжгүй/шаардлагагүй): " + " · ".join(
                f"{QR.SKIP_MN.get(k, k)} {v}" for k, v in sel["skipped"].most_common()))
            for r in sel["skipped_rows"][:20]:
                print(f"    {r['paid_at_ub']} {r['site_code']} {r['amount']:,.0f}₮ {r['receipt_status']} → {r['skip']}"
                      + (f" (оролдлого {r['attempts']}: {r['attempt_outcome']}, сүүлийн {r['attempt_last_ub']} УБ"
                         + (f", billId {r['attempt_bills']}" if r.get("attempt_bills") else "") + ")"
                         if r.get("attempts") else ""))
            if sel["skipped"].get("ATTEMPTED"):
                print(ATTEMPT_HINT)
        if cands:
            summarize(cands, "tenant", title="\nТүрээслэгчээр:")
            summarize(cands, "day", title="Өдрөөр (УБ):")
            summarize(cands, "teg", title="ТЕГ нотолгоо:")
            summarize(cands, "action", title="Үйлдэл:")
            for k in sorted({c["teg"] for c in cands}):
                print(f"    {k}: {QR.VERDICT_MN.get(k, k)}")
            for c in cands:
                if c["teg"] in ("AMBIGUOUS", "IN_TEG", "IN_TEG_AMBIG", "ATTEMPT_UNCOVERED"):
                    ev_ = c["evidence"]
                    print(f"    · {c['paid_at_ub']} {c['site_code']} {c['amount']:,.0f}₮ {c['teg']}: эзэнгүй мөр "
                          f"{ev_.get('near')} · өдрийн тэнцэл {ev_.get('surplus') or 0:+d}"
                          + (f" · {ev_['note']}" if ev_.get("note") else "")
                          + "".join(f"\n        ТЕГ {r['ddtd']} {r['dt']} UTC" for r in ev_.get("near_rows", [])))
            n_in = sum(1 for c in cands if c["teg"] == "IN_TEG")
            if n_in and not args.attach_teg:
                print(f"    ↳ IN_TEG {n_in}: ТЕГ-т баримт аль хэдийн бий — --attach-teg-ээр тэр ДДТД-г холбоно "
                      "(QPay дуудахгүй); --attach-teg-гүйгээр хөндөхгүй.")
        comp = [c for c in cands if c["receiver_type"] == "COMPANY"]
        if comp:
            long_ = [c for c in comp if len(c["customer_tin"]) > 7]
            print(f"\nААН (ТТД/регистртэй) {len(comp)}: QPay-д ebarimt_receiver = customer_tin ЯГ "
                  f"байгаагаар (өөрчлөхгүй)" + (f"; ⚠ {len(long_)} нь 7-оос урт — QPay MAX 7 гэж татгалзаж "
                                                "REVIEW хэвээр үлдэнэ («Дугаар засах»)" if long_ else ""))
        work = [c for c in cands if c["action"] != "SKIP"]
        if args.limit is not None:
            work = work[:max(0, args.limit)]
        amount = sum(c["amount"] for c in work)
        acts = Counter(c["action"] for c in work)
        print(f"\nНӨХӨХ: {len(work)} төлбөр · {amount:,.0f}₮ ("
              + " · ".join(f"{k} {v}" for k, v in sorted(acts.items())) + ")" if work else "\nНӨХӨХ: 0")
        if args.out:
            stem = args.out[:-4] if args.out.lower().endswith(".csv") else args.out
            write_csv(args.out, cands, FIELDS)
            write_csv(f"{stem}_skipped.csv", sel["skipped_rows"], FIELDS)
            print(f"CSV: {args.out} ({len(cands)}) · {stem}_skipped.csv ({len(sel['skipped_rows'])})")
        site_ids = {c["site_id"] for c in work}
        sites_used = [s for s in sites if s.id in site_ids]
        probs = VD.server_problems() + QR.account_problems(sites_used)
        changed = [c for c in work if c.get("account") != "ok"]
        no_ev = [c for c in work if c["action"] == "CREATE" and c["teg"] in QR.NO_EVIDENCE]
        attach = [c for c in work if c["action"] == "ATTACH_TEG"]
        retried = [c for c in work if c["action"] == "CREATE" and c.get("attempt_block")]
        n_create = sum(1 for c in work if c["action"] == "CREATE")
        if not args.apply:
            if changed:
                probs.append(f"{len(changed)} төлбөрийн QPay данс ажлын үеийнхээс өөр/уншигдахгүй "
                             "(тохиргоо өөрчлөгдсөн эсвэл .env ачаалагдаагүй) — --apply эдгээрийг алгасна")
            if n_create:
                probs.append("CREATE-д --i-understand-not-idempotent шаардлагатай")
            if no_ev and not args.no_teg_evidence:
                probs.append(f"{len(no_ev)} CREATE ТЕГ нотолгоогүй ({evidence_kinds(no_ev)}) — "
                             "--file (хамарсан ТЕГ экспорт) эсвэл --no-teg-evidence")
            if len(work) > args.max:
                print(max_hint(len(work), args.max))
            if probs:
                print("\n⚠ --apply ОДООГООР ажиллахгүй/анхаар: " + "; ".join(probs))
            need = ["--apply", "--user <нэр>"] + (["--i-understand-not-idempotent"] if n_create else []) + (
                ["--no-teg-evidence"] if no_ev and not args.no_teg_evidence else [])
            print("\nDRY-RUN — юу ч өөрчлөгдсөнгүй, QPay руу хандсангүй. Нөхөхийн тулд ижил командыг "
                  + " ".join(need) + " -тэй ажиллуулна.")
            return 0
        if not work:
            print("\nНөхөх төлбөр алга.")
            return 0
        if len(work) > args.max:
            print("\nЗОГСОВ:" + max_hint(len(work), args.max).split(":", 1)[1])
            return 2
        if probs:
            print("\nЗОГСОВ — нөхөх боломжгүй: " + "; ".join(probs))
            return 3
        if n_create and not args.not_idempotent_ok:
            print(NOT_IDEMPOTENT_MSG.format(n=n_create))
            return 6
        if no_ev and not args.no_teg_evidence:
            print(NO_EVIDENCE_MSG.format(n=len(no_ev), kinds=evidence_kinds(no_ev)))
            return 7
        # YES-ийг хүлээх хооронд DB транзакц нээлттэй үлдээхгүй (idle_in_transaction 5 мин)
        label, tenant_codes = VD.tenant_label(tenants), [t.code for t in tenants]
        site_codes = [s_.site_code for s_ in sites_used]
    finally:
        db.rollback()
    print(f"\nНӨХӨХ ГЭЖ БАЙНА: DB {VD.db_name()} · сервер {settings.public_base_url} · түрээслэгч "
          f"{label} · {len(work)} төлбөр ({amount:,.0f}₮) · "
          + " · ".join(f"{k} {v}" for k, v in sorted(acts.items()))
          + (f" · файл {', '.join(files)}" if files else " · ТЕГ файлгүй"))
    if attach:
        print(f"  ТЕГ-ийн ДДТД ХОЛБОХ ({len(attach)}, QPay дуудахгүй):")
        for c in attach:
            print(f"    · {c['paid_at_ub']} {c['site_code']} {c['amount']:,.0f}₮ → {c['teg_ddtd']} "
                  f"(ТЕГ {c['evidence'].get('teg_dt')} UTC, {c['evidence'].get('teg_file')})")
    if no_ev:
        print(f"  ⚠ --no-teg-evidence: {len(no_ev)} CREATE ТЕГ экспортын нотолгоогүй ({evidence_kinds(no_ev)})")
    if retried:
        print(f"  ⚠ --retry-attempted: {len(retried)} төлбөрт QPay-г ДАХИН дуудна (өмнөх оролдлого тодорхойгүй; "
              "ТЕГ файл ≥24ц хойш хамарсан, ТЕГ-т алга)")
    if not args.yes:
        if not sys.stdin.isatty():
            print("\nЗОГСОВ: баталгаажуулах терминал алга — --yes өгнө үү.")
            return 4
        ans = input(f"\n{len(work)} QR борлуулалтад QPay e-Barimt ({amount:,.0f}₮) НӨХӨХ үү? "
                    + ("[--no-teg-evidence: ТЕГ нотолгоогүй CREATE бий] " if no_ev else "")
                    + "Зөвшөөрвөл YES гэж бичнэ үү: ").strip()
        if ans != "YES":
            print("Нөхсөнгүй.")
            return 5
    print(f"\nНөхөж байна ({len(work)}) — хэрэглэгч {username}, завсар {args.delay}с")
    try:
        cnt, res_rows = asyncio.run(apply_all(db, work, username=username,
                                              include_failed=args.include_failed, delay=args.delay,
                                              retry_attempted=args.retry_attempted))
        db.add(AuditLog(username=username, action=QR.SUMMARY_ACTION, entity="vat", entity_id=TOOL,
                        detail={"tool": TOOL, "tenants": tenant_codes, "sites": site_codes, "files": files,
                                "from": args.date_from, "to": args.date_to,
                                "include_failed": bool(args.include_failed),
                                "include_ambiguous": bool(args.include_ambiguous),
                                "include_uncovered": bool(args.include_uncovered),
                                "attach_teg": bool(args.attach_teg),
                                "attach_ddtd": [c["teg_ddtd"] for c in attach],
                                "no_teg_evidence": bool(args.no_teg_evidence),
                                "no_evidence_creates": len(no_ev),
                                "retry_attempted": bool(args.retry_attempted),
                                "retried": len(retried),
                                "requested": len(work), "amount": amount,
                                "actions": dict(acts), "result": dict(cnt)}))
        try:
            db.commit()
        except Exception as e:  # noqa: BLE001 — DB унасан ч ТОДОРХОЙГҮЙ жагсаалтыг доор хэвлэнэ
            print(f"⚠ SUMMARY аудит хадгалагдсангүй: {e!r}")
            db.rollback()
    finally:
        db.close()
    if args.out:
        stem = args.out[:-4] if args.out.lower().endswith(".csv") else args.out
        write_csv(f"{stem}_applied.csv", res_rows,
                  FIELDS + ["outcome", "ebarimt_id", "bill_id", "attempt_result", "error"])
        print(f"CSV: {stem}_applied.csv")
    print(f"\nДҮН: {dict(cnt)}")
    unsure = [r for r in res_rows if r.get("attempt_result") and r["attempt_result"] not in QR.DEFINITE
              and r["attempt_result"] != "SENT"]
    rejected = [r for r in res_rows if r.get("attempt_result") in ("REJECTED",)]
    if unsure:
        print(f"\n⚠ ТОДОРХОЙГҮЙ {len(unsure)}: QPay-г дуудсан ч үр дүн тодорхойгүй (timeout/5xx/billId-гүй/"
              "ДДТД биш/хадгалагдаагүй) — QPay талд баримт ҮҮССЭН байж болзошгүй. Эдгээр нь оролдлогоор "
              "бүртгэгдсэн тул ДАХИН АЖИЛЛУУЛАХАД АЛГАСАГДАНА; дахин бүү оролд, ebarimt-diag create-г БҮҮ "
              "ашигла. ≥24ц-ийн дараах ТЕГ экспортоор шалгаж, ТЕГ-т бий бол «баримт холбох», байхгүй нь "
              "батлагдвал --retry-attempted --file <шинэ экспорт>.")
        for r in unsure:
            print(f"    · {r['payment_id']} {r['paid_at_ub']} {r['amount']:,.0f}₮ {r['attempt_result']}"
                  + (f" · QPay billId {r['bill_id']}" if r.get("bill_id") else "") + f" · {r['error']}")
    if rejected:
        print(f"\nQPay 4xx-ээр ТОДОРХОЙ татгалзсан {len(rejected)}: баримт үүсээгүй — шалтгааныг (жишээ нь ТТД/"
              "регистр) засаж, дахин dry-run-аар шалгасны дараа л нөхнө.")
    return 0 if not cnt.get("failed") else 1


if __name__ == "__main__":
    sys.exit(main())
