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

--file (2026-10-15): сарын экспортыг файлын өөрийн огноогоор 2 хоногоор хувааж (хөрш хоногийн
мөрийг нөөц болгон) тулгана — бүтэн сарын файлыг нэг дор тулгахад 2GB сервер дээр ~500MB → OOM.
10-14-өөс нэг Ontime касс (…10002990) 5 зогсоолд үйлчилдэг тул «олонхын нэг зогсоол» олдохгүй →
бүх мөр «зогсоол тодорхойгүй — цуцлахгүй» болж байв: одоо кассын зогсоолыг ЗОГСООЛ × ӨДРӨӨР
тогтооно (тэр өдрийн POS аппын борлуулалтын ≥10% нь Ontime мөртэй) — тэдгээрийн жинхэнэ
давхардал (Ontime + манай msgbill хоёулаа ТЕГ-т) цуцлагдана, бусад нь «тэр өдөр терминалгүй».
«Манай баримт ч файлд» нь ДДТД-ээр (0291… багцын дугаартайг л цаг+дүнгээр) — цаг+дүнгээр өөр мөртэй
таарсан ч ДДТД нь экспортод алга бол цуцлахгүй (audit1014 × 10-15: 41-ийн 25 нь ийм байв).

──── --pos-app: ДҮРМЭЭР (ТЕГ файлгүй) — 2026-10-13; 10-14 эзний ЭЦСИЙН шийдвэр ────
PAX терминал (Ontime) POS аппын КАРТ, БЭЛЭН борлуулалт бүрд e-Barimt өөрөө хэвлэдэг — БҮХ
зогсоол, БҮХ түрээслэгч (ИйзиПаркинг, Моннис пропэрти/Monnis Building, Моннисмоторс…) →
тэдгээрийн манай msgbill баримтыг цуцална (--file-тай хамт өгөхгүй). Сонголт: PAID,
CARD/CASH, --tenant-ийн (эсвэл ALL — бүх түрээслэгч) зогсоол (--site-аар нарийсгана; тест
KH, SITE10 зөвхөн нэрлэсэн үед), төлсөн огноо --from/--to (УБ, анхдагч 2026-08-19 → одоо);
POS аппынх = source POSAPP, эсвэл provider POS (/pos/confirm), эсвэл кассчин нь POS аппын
хэрэглэгч (СИСТЕМ ДАЯАР: role POS + тохиргооны жагсаалт + POS аппаар ≥20 карт; --pos-user
дарна); идэвхтэй msgbill баримттай (provider_ref), өөр сувгийн тэмдэггүй SENT баримтгүй;
ААН/ТТД-тэй баримтыг (--include-company-гүй бол) ХӨНДӨХГҮЙ; зогсоолд «POS терминал
e-Barimt гаргадаг» УНТРААСАН бол (терминал нь хэвлэдэггүй) тэр зогсоолыг ХӨНДӨХГҮЙ;
терминал хэвлээгүй нь батлагдаж ДАХИН гаргасан баримтыг ХӨНДӨХГҮЙ. Цуцалсны дараа
ДДТД-гүй EXTERNAL («POS терминал гаргасан») мөр бүртгэнэ.

--evidence (ТЕГ экспорт, олон удаа, урт хугацаа ч болно): файлын хамрах хүрээнд (хугацаа ×
файлын ТТД-ийн түрээслэгч, терминалын кассын цагийн бүсээр) байгаа борлуулалтыг ЗӨВХӨН
терминалын (манайх биш, QPay биш) мөр таарвал цуцална (CSV basis=TEG); терминалын мөргүй
(манай баримт нь ТЕГ-т бий — цорын ганц баримт — ч), ТОДОРХОЙГҮЙ (ижил дүнтэй хосгүй терминалын мөр
ойрхон) борлуулалтыг цуцлахгүй; хүрээнээс
гадуурхыг АНХДАГЧААР ХАСНА (түрээслэгч × сараар хэвлэнэ) — дүрмээр цуцлах бол
--outside-by-rule (basis=RULE). Хамралтын статистикийг хэвлэнэ. Ontime баримт хэвлэгдээгүй ч
цуцлагдсан борлуулалтыг tools/vat_reissue_missing.py нөхнө.

    venv/bin/python tools/vat_cancel_duplicates.py --pos-app --tenant ALL \
        --from 2026-08-25 --to 2026-09-19 --out /root/posapp.csv                           # харах
    venv/bin/python tools/vat_cancel_duplicates.py --pos-app --tenant MONNIS \
        --evidence /root/teg_monnis_sep.xlsx --out /root/posapp_mon.csv                    # нотолгоотой
    venv/bin/python tools/vat_cancel_duplicates.py --pos-app --tenant ALL \
        --apply --limit 1 --user temuujin --note "Эзний шийдвэр 10-14: POS терминал Ontime"  # туршилт
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
POSAPP_FIELDS = ["payment_id", "tenant", "site_code", "site_name", "plate", "method", "amount",
                 "paid_at_ub", "paid_at", "month", "cashier", "via", "our_ddtd", "our_provider_ref",
                 "our_status", "receiver_type", "customer_tin", "kind"]
POSAPP_NOTE = ("Эзний шийдвэр 2026-10-14: POS терминал (PAX/Ontime) карт/бэлэн борлуулалт бүрд "
               "e-Barimt хэвлэдэг (бүх зогсоол) — msgbill дэх давхардлыг цуцлав")


def _read_only(db):
    """Dry-run: Postgres-ийн READ ONLY транзакц — алдаатай код ч юу ч бичиж чадахгүй."""
    if db.bind.dialect.name == "postgresql":
        db.execute(text("SET TRANSACTION READ ONLY"))


def resolve_tenant(db, value):
    if not value or value.strip().upper() == VD.ALL_TENANTS:
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


def _register_where(chunks_rs: list) -> str:
    """Өөр системийн кассын зогсоол (хэсэг бүрийн register_sites-ийн нэгдэл) — хэвлэхэд."""
    allow = [rs for rs in chunks_rs if rs.get("allow") is not None]
    if allow:
        days = {x for rs in allow for x in rs["allow"]}
        names = sorted({n.strip() for rs in allow for n in (rs.get("site_name") or "").split(",") if n.strip()})
        return (f" · зогсоол × өдрөөр (тэр өдөр терминалаар хэвлэсэн): {', '.join(names) or '—'}"
                f" ({len(days)} зогсоол-өдөр)" if days else
                " · ⚠ POS аппын борлуулалттай хосолсон ч аль ч зогсоол тэр өдөр терминал ашиглаагүй — цуцлахгүй")
    named = [rs for rs in chunks_rs if rs.get("site_id")]
    if named:
        names = Counter(rs["site_name"] for rs in named)
        return " · зогсоол " + ", ".join(f"«{k}»" for k, _v in names.most_common())
    if chunks_rs:
        return f" · ⚠ зогсоол ТОДОРХОЙГҮЙ ({max(rs['sites'] for rs in chunks_rs)} зогсоолд тарсан) — цуцлахгүй"
    return ""


def analyze_files(db, files, *, tol, shifts, tenant_sites, log=print, chunk_days=VD.EVIDENCE_CHUNK_DAYS):
    """Файл бүрийг ТУСАД НЬ тулгана (өөр өдрийн файлын цонх холилдохгүй), давхардлыг төлбөрөөр нь
    нэгтгэнэ. Буцаана: (давхардал, үлдсэн мөрүүд, статистик).

    Файлыг өөрийн огноогоор chunk_days хоногоор хувааж тулгана (2026-10-15: 9-р сарын бүтэн
    файлыг нэг дор тулгахад 2GB сервер дээр ~500MB санах ой → OOM-д алагдаж байв) — terminal_evidence-
    тэй нэг арга: хэсэг бүрт хөрш хоногийн мөрийг (касс бүрийн цагийн бүсийн зөрүү + tol) НӨӨЦ болгон
    нэмж тулгаад ЗӨВХӨН үндсэн мөрүүдийн дүгнэлт/давхардлыг авна (хил дээрх борлуулалтын мөр нөгөө
    хэсэгт байсан ч зөв хосолно)."""
    dups, left_rows, stats, seen = [], [], [], set()
    margin = timedelta(hours=max(abs(x) for x in shifts) + 1, seconds=tol + 5)
    for f in files:
        raw = open(f, "rb").read()
        tax, diag = VR.parse_tax_export(os.path.basename(f), raw)
        del raw
        name = os.path.basename(f)
        verdicts, shifts_seen, reg_rs = Counter(), {}, {}
        foreign, matched, in_file, chunks = set(), 0, 0, 0
        un_ours, matched_pids, foreign_hits = {}, set(), Counter()
        tail = VR.register_tail_len([t["ddtd"] for t in tax])
        for _first, _last, _d0, _d1, core, part in VD._file_chunks(tax, chunk_days, margin):
            chunks += 1
            a = VD.reconcile_db(db, part, shifts=shifts, tol=tol, ours_sites=tenant_sites,
                                probe_sites=None,
                                scope_ids=set(tenant_sites) if tenant_sites is not None else None)
            r = a["r"]
            core_ids = {id(t) for t in core}
            matched += sum(1 for t in core if t.get("ours") and not t.get("ours_cancelled"))
            in_file += sum(1 for t in core if t.get("ours_cancelled"))
            matched_pids |= {t["ours"][1].id for t in part if t.get("ours")}
            for u in r["unmatched_ours"]:
                un_ours.setdefault(u.get("payment_id"), u)
            for k, v in r["group_shifts"].items():
                shifts_seen.setdefault(k, set()).add(v)
            foreign |= set(r["foreign_registers"])
            for k, rs in (a.get("register_sites") or {}).items():
                reg_rs.setdefault(k, []).append(rs)
            dup_pids = set()
            for t, x in zip(a["left"], a["explained"]):
                if id(t) not in core_ids:
                    continue                  # нөөц мөр — өөрийн (хөрш) хэсэгт дүгнэгдэнэ
                verdicts[x["verdict"]] += 1
                if x.get("foreign") and x.get("payment_id"):
                    foreign_hits[x.get("register") or ""] += 1
                if x["verdict"] == "DUPLICATE":
                    dup_pids.add(x["payment_id"])
                left_rows.append({**{k: x.get(k) for k in LEFT_FIELDS}, "file": name})
            for d in a["duplicates"]:
                if d["payment_id"] in dup_pids and d["payment_id"] not in seen:
                    seen.add(d["payment_id"])
                    dups.append({**d, "file": name})
            for t in part:
                t.pop("ours", None)           # ORM мөрийн лавлагааг хэсэг бүрийн дараа чөлөөлнө
            del a, r
        unmatched = [u for pid, u in un_ours.items() if pid not in matched_pids]
        groups = VR.pos_groups([t["ddtd"] for t in tax], tail)
        log(f"\n== {f}")
        log(f"   ТЕГ мөр {len(tax)} (багана {diag['columns']}) · {chunks} хэсэг · касс: "
            + ", ".join(f"…{k}={v} ({'/'.join(f'{x:+g}' for x in sorted(shifts_seen.get(k, {0})))}ц"
                        f"{', өөр систем' if k in foreign else ''})" for k, v in sorted(groups.items())))
        left_n = sum(verdicts.values())
        log(f"   манай баримттай таарсан {matched} · манайд бий/ТЕГ-д алга {len(unmatched)}"
            f" · ТЕГ-д бий/манайд алга {left_n}"
            + (f" · манай ЦУЦЛАГДСАН баримтын мөр файлд {in_file}" if in_file else ""))
        for k in sorted(foreign):
            rows_k = groups.get(k, 0)
            rate = foreign_hits[k] / max(rows_k, 1)
            log(f"   өөр системийн касс …{k}: {foreign_hits[k]}/{rows_k} мөр манай төлбөртэй таарсан"
                + _register_where(reg_rs.get(k, []))
                + ("" if rate >= 0.5 else
                   "  ⚠ ХАГАСААС БАГА — энэ касс манай зогсоолынх биш байж магадгүй, "
                   "давхардлыг CSV-ээс гараар шалгана уу"))
        for v, n in verdicts.most_common():
            log(f"     {n:5d}  {v:26s} {VR.VERDICTS.get(v, '').split(' — ')[0]}")
        stats.append({"file": f, "rows": len(tax), "matched": matched, "chunks": chunks,
                      "verdicts": dict(verdicts),
                      "duplicates": sum(1 for d in dups if d["file"] == name)})
        del tax
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


def max_hint(n: int, cap: int) -> str:
    """--max хамгаалалтын тайлбар — dry-run-д сануулж, --apply-д яг ямар тугаар үргэлжлүүлэхийг заана."""
    return (f"\n⚠ --max: {n} төлбөр > --max {cap} (анхдагч хамгаалалт — санамсаргүй бөөн цуцлалтаас). "
            f"Тоо (дээрх задаргаа) зөв бол --apply-д --max {n} нэмнэ үү, эсвэл --limit-ээр хэсэгчлэн "
            "(--limit 1 → --limit 500 → бүгд; дахин ажиллуулахад цуцлагдсан нь алгасагдана).")


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
    """{(түрээслэгч, зогсоол, сар, арга): [тоо, ₮]} — YES асуулт ба dry-run-д."""
    cells: dict = {}
    for r in rows:
        k = (r.get("tenant") or "?", r["site_code"] or r["site_name"] or "?", r["month"], r["method"])
        c = cells.setdefault(k, [0, 0.0])
        c[0] += 1
        c[1] += r["amount"]
    return dict(sorted(cells.items()))


def print_pos_app_summary(sel, log=print):
    rows = sel["selected"]
    total = sum(r["amount"] for r in rows)
    log(f"\nЦУЦЛАХ (POS аппын дүрэм): {len(rows)} төлбөр · {total:,.0f}₮")
    print_cells(rows, log)
    comp = sel["company"]
    if comp:
        cs = Counter(r["site_code"] for r in comp)
        log(f"\nХАСАГДСАН — ААН/ТТД-тэй баримт: {len(comp)} ({sum(r['amount'] for r in comp):,.0f}₮) · "
            + " · ".join(f"{k} {v}" for k, v in sorted(cs.items()))
            + " — терминал иргэний баримт хэвлэдэг тул байгууллагын баримтыг ХӨНДӨХГҮЙ"
              " (--include-company-оор л)")
    if sel.get("site_off"):
        log("ХАСАГДСАН — зогсоолд «POS терминал e-Barimt гаргадаг» УНТРААЛТТАЙ/арга хасагдсан: "
            + " · ".join(f"{k} {v}" for k, v in sorted(sel["site_off"].items())))
    ex = {k: v for k, v in sel["excluded"].items() if k not in ("COMPANY",)}
    if ex:
        log("Бусад хасагдсан: " + " · ".join(f"{VD.POS_APP_EXCLUSION_MN.get(k, k)} {v}"
                                              for k, v in sorted(ex.items(), key=lambda kv: -kv[1])))
    if sel["complete"]:
        log(f"Өмнө цуцлагдсан, терминалын мөр дутуу (msgbill дуудахгүй, нөхнө): {len(sel['complete'])}")
    log("\n⚠ АНХААР: эдгээр борлуулалтад Ontime (POS терминал) баримт ҮНЭХЭЭР хэвлэгдсэн гэж үзэж "
        "манай msgbill баримтыг ЦУЦАЛНА. Терминал баримт хэвлээгүй (унтарсан, цаасгүй, Ontime "
        "салгасан) борлуулалт байвал цуцалсны дараа тэр нь e-Barimt-ГҮЙ үлдэнэ — ТЕГ экспортоор "
        "(--evidence, эсвэл дараа нь tools/vat_reissue_missing.py) шалгаж нөхнө.")
    return total


def print_cells(rows, log=print):
    """Түрээслэгч × зогсоол × сар × арга — тоо ба ₮ (dry-run, YES асуулт)."""
    by_site: dict = {}
    for (ten, site, month, method), (n, amt) in _breakdown(rows).items():
        by_site.setdefault((ten, site), []).append(f"{month} {method} {n} ({amt:,.0f}₮)")
    for (ten, site), parts in by_site.items():
        sel = [r for r in rows if (r.get("tenant") or "?") == ten
               and (r["site_code"] or r["site_name"] or "?") == site]
        log(f"  {ten:<11} {site:<14} {len(sel):5d} ({sum(r['amount'] for r in sel):,.0f}₮) — "
            + " · ".join(parts))
    by_t = Counter()
    amt_t: dict = {}
    for r in rows:
        by_t[r.get("tenant") or "?"] += 1
        amt_t[r.get("tenant") or "?"] = amt_t.get(r.get("tenant") or "?", 0) + r["amount"]
    if len(by_t) > 1:
        log("  Түрээслэгчээр: " + " · ".join(f"{k} {v} ({amt_t[k]:,.0f}₮)" for k, v in sorted(by_t.items())))
    by_m = Counter(r["method"] for r in rows)
    log("  Аргаар: " + " · ".join(f"{k} {v}" for k, v in sorted(by_m.items())))


def print_pos_users(info, log=print):
    """POS аппын хэрэглэгчид — систем даяар (2026-10-14: зогсоолоор БИШ)."""
    log(f"POS аппын хэрэглэгчид [{info['source']}]: {', '.join(sorted(info['users'])) or '—'}")
    der = ", ".join(f"{u} {n}" for u, n in sorted(info["derived"].items(), key=lambda kv: -kv[1]))
    log(f"  POS аппын карт (≥босго): {der or '—'}")
    if info["roles"]:
        log(f"  role POS: {', '.join(sorted(info['roles']))}")
    if info["configured"]:
        log(f"  тохиргооны жагсаалт: {', '.join(sorted(info['configured']))}")
    if info["below"]:
        log("  ⚠ босгоос доош POS карттай (POS аппынх гэж ТООЦООГҮЙ — хэрэгтэй бол --pos-user): "
            + ", ".join(f"{u} {n}" for u, n in sorted(info["below"].items(), key=lambda kv: -kv[1])))


def load_evidence(files):
    out = []
    for f in files or []:
        if not os.path.isfile(f):
            raise SystemExit(f"Файл олдсонгүй: {f}")
        tax, _diag = VR.parse_tax_export(os.path.basename(f), open(f, "rb").read())
        out.append((os.path.basename(f), tax))
    return out


def main_pos_app(args):
    """--pos-app: ТЕГ файлгүй, эзний шийдвэрийн дүрмээр сонгож цуцлах."""
    if not args.tenant:
        raise SystemExit("--pos-app үед --tenant заавал (ж: EASYPARKING, MONNIS эсвэл ALL — бүх түрээслэгч)")
    username = args.user or f"cli:{os.getenv('SUDO_USER') or getpass.getuser()}"
    out = args.out or f"vat_posapp_{datetime.now():%Y%m%d_%H%M%S}.csv"
    stem = out[:-4] if out.lower().endswith(".csv") else out
    note = args.note if args.note != NOTE_DEFAULT else POSAPP_NOTE
    taxes = load_evidence(args.evidence)
    db = SessionLocal()
    _read_only(db)
    try:
        try:
            tenants = VD.resolve_tenants(db, args.tenant)
            sites = VD.pos_app_sites(db, tenants, args.site)
        except ValueError as e:
            raise SystemExit(str(e))
        if not tenants:
            raise SystemExit("Түрээслэгч алга")
        tcode = {t.id: t.code for t in tenants}
        scope = VD.pos_app_scope(db, sites, override=VD.parse_pos_user_override(args.pos_user),
                                 min_card=args.min_card)
        lo, hi = VD.ub_window(args.date_from, args.date_to)
        loc = timedelta(hours=settings.tz_offset_hours)
        print(f"{TOOL} --pos-app: {'APPLY' if args.apply else 'DRY-RUN'} · DB "
              f"{settings.database_url.rsplit('/', 1)[-1]} · түрээслэгч {VD.tenant_label(tenants)}"
              f" · {len(sites)} зогсоол · төлсөн {(lo + loc):%Y-%m-%d} → {(hi + loc):%Y-%m-%d %H:%M} (УБ)"
              + (" · ААН/ТТД-тэй ОРНО" if args.include_company else ""))
        print_pos_users(scope)
        off = sorted(s_.site_code for s_ in sites if not scope["methods"].get(s_.id))
        part = sorted(f"{s_.site_code}={','.join(sorted(scope['methods'][s_.id]))}" for s_ in sites
                      if scope["methods"].get(s_.id) and scope["methods"][s_.id] != set(VD.POS_APP_METHODS))
        if off or part:
            print("  Зогсоолын тохиргоо: " + (f"УНТРААЛТТАЙ (хөндөхгүй): {', '.join(off)}" if off else "")
                  + (" · " if off and part else "") + (f"зөвхөн: {', '.join(part)}" if part else ""))
        skipped_test = sorted(c for c in VD.POS_APP_TEST_SITES
                              if c not in {(x.site_code or '').upper() for x in sites})
        if skipped_test:
            print(f"  (тест зогсоол алгасав: {', '.join(skipped_test)} — --site-аар нэрлэвэл орно)")
        t0 = time.monotonic()
        sel = VD.select_pos_app(db, sites=sites, lo=lo, hi=hi, scope=scope,
                                include_company=args.include_company)
        total = print_pos_app_summary(sel)
        rows = [{**r, "kind": "cancel", "basis": "RULE"} for r in sel["selected"]]
        if taxes:
            # НЭМЭЛТ нотолгоо: ТЕГ экспортын хамрах хүрээнд (хугацаа × файлын ТТД-ийн түрээслэгч)
            # ЗӨВХӨН терминалын мөртэй төлбөрийг цуцална (basis=TEG); хүрээнээс гадуурхыг
            # --outside-by-rule-гүйгээр ХАСНА (хяналт 2, E: EasyParking-ийн файл + --tenant ALL
            # нь Моннисын бүх борлуулалтыг чимээгүй дүрмээр цуцлах байв).
            # Тулгалт нь сонгосон түрээслэгч(ид)-ийн БҮХ зогсоолоор (ТЕГ файл ТТД-ийн бүх борлуулалт)
            ev_sites = VD.tenant_sites(db, tenants)
            shifts = [0.0, -float(settings.tz_offset_hours)]
            ev = VD.terminal_evidence(db, taxes, sites=ev_sites, tol=args.tol, shifts=shifts,
                                      pos_users=scope["users"])
            split = VD.evidence_split(rows, ev, {x.id: x.tenant_id for x in ev_sites})
            VD.print_evidence(ev, split, tenants=tcode, site_names={x.id: x.site_code for x in ev_sites})
            for r in (split["missing"] + split["own_in_teg"])[:20]:
                print(f"    терминалын мөргүй (ЦУЦЛАХГҮЙ): {r['paid_at_ub']} {r['site_code']} "
                      f"{r['plate'] or '—'} {r['method']} {r['amount']:,.0f}₮ ({r['cashier']})")
            for r in split["with_terminal"]:
                r["basis"] = "TEG"
            if split["covered"]:
                print("  Хүрээнд — терминалын мөртэй (цуцална, basis=TEG):")
                print_cells(split["with_terminal"])
            # Терминалын мөргүй борлуулалтын манай баримт ТЕГ-т байгаа (own_in_teg) нь тэр борлуулалтын
            # ЦОРЫН ГАНЦ баримт — терминалын мөргүйтэй адил ЦУЦЛАХГҮЙ (2026-10-15: шинэ ангилал)
            keep_out = split["missing"] + split["own_in_teg"] + split["ambiguous"]
            drop = {r["payment_id"] for r in keep_out}
            if drop:
                write_csv(f"{stem}_no_ontime.csv", keep_out, POSAPP_FIELDS + ["basis", "own_teg"])
                print(f"Терминалын мөргүй {len(split['missing']) + len(split['own_in_teg'])} (үүнээс манай "
                      f"баримт ТЕГ-т бий {len(split['own_in_teg'])}) + ТОДОРХОЙГҮЙ {len(split['ambiguous'])} "
                      f"төлбөрийг хасав (ЦУЦЛАХГҮЙ) → {stem}_no_ontime.csv")
            outside = split["outside"]
            if outside:
                cnt: dict = {}
                for r in outside:
                    c = cnt.setdefault((r.get("tenant") or "?", r["month"]), [0, 0.0])
                    c[0] += 1
                    c[1] += r["amount"]
                print(("Хүрээнээс гадуур (ТЕГ файл хамраагүй) — ДҮРМЭЭР цуцална (--outside-by-rule): "
                       if args.outside_by_rule else
                       "Хүрээнээс гадуур (ТЕГ файл хамраагүй) — ХАСАВ (нотолгоогүй; дүрмээр цуцлах бол "
                       "--outside-by-rule): ")
                      + " · ".join(f"{t} {m} {n} ({a:,.0f}₮)" for (t, m), (n, a) in sorted(cnt.items())))
                if not args.outside_by_rule:
                    drop |= {r["payment_id"] for r in outside}
                    write_csv(f"{stem}_outside.csv", outside, POSAPP_FIELDS + ["basis"])
            rows = [r for r in rows if r["payment_id"] not in drop]
            by_basis = Counter(r["basis"] for r in rows)
            print(f"ЦУЦЛАХ ЖАГСААЛТ: {len(rows)} ({sum(r['amount'] for r in rows):,.0f}₮) · "
                  + " · ".join(f"basis={k} {v}" for k, v in sorted(by_basis.items())))
        # Цуцлах мөр ЭХЭНДЭЭ (--limit 1 туршилт msgbill цуцлалтыг шалгана), дараа нь
        # өмнө цуцлагдсан боловч терминалын мөр дутуу (msgbill дуудахгүй) нөхөлт
        work = rows + [{**r, "kind": "complete_terminal", "basis": "COMPLETE"} for r in sel["complete"]]
        write_csv(out, work, POSAPP_FIELDS + ["basis"])
        write_csv(f"{stem}_company.csv", sel["company"], POSAPP_FIELDS)
        print(f"\nCSV: {out} ({len(work)}) · {stem}_company.csv ({len(sel['company'])})"
              f" · {time.monotonic() - t0:.1f}с")
        sites_used = [s_ for s_ in sites if any(r["site_id"] == s_.id for r in work)]
        if not args.apply:
            probs = VD.cancel_ready(db, sites_used)
            if len(work) > args.max:
                print(max_hint(len(work), args.max))
            if probs:
                print("\n⚠ --apply ОДООГООР ажиллахгүй: " + "; ".join(probs))
            print("\nDRY-RUN — юу ч өөрчлөгдсөнгүй. Цуцлахын тулд ижил командыг --apply --user <нэр> "
                  "(--max <тоо>) -тэй ажиллуулна.")
            return 0
        if args.limit is not None:
            work = work[:max(0, args.limit)]
        # --max нь ЭНЭ УДАА бодитоор цуцлах тоонд (--limit 1 туршилт 8,000+ сонголттой ч болно)
        if len(work) > args.max:
            print("\nЗОГСОВ:" + max_hint(len(work), args.max).split(":", 1)[1])
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
    print(f"\nЦУЦЛАХ ГЭЖ БАЙНА (POS аппын дүрэм): DB {VD.db_name()} · сервер {settings.public_base_url}"
          f" · түрээслэгч {VD.tenant_label(tenants)} · {len(work)} төлбөр ({amount:,.0f}₮)")
    for (ten, site, month, method), (n, amt) in _breakdown(work).items():
        print(f"  {ten:<11} {site:<14} {month} {method:<4} {n:6d}  {amt:12,.0f}₮")
    print("  msgbill түлхүүр (оролдох дараалал): "
          + " · ".join(f"{k}: {' → '.join(v) or '—'}" for k, v in scopes.items()))
    print("  Ontime баримт хэвлэгдээгүй борлуулалт байвал e-Barimt-ГҮЙ үлдэнэ (vat_reissue_missing.py нөхнө).")
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
    guard = VD.pos_app_guard(scope, {s_.id for s_ in sites}, args.include_company)
    res_rows, cnt, stopped = asyncio.run(apply_cancel(
        db, work, note=note, username=username, delay=args.delay, source="cli-pos-app",
        mark_extra={"rule": VD.POS_APP_RULE}, guard=guard, allow_company=bool(args.include_company)))
    try:
        db.add(AuditLog(username=username, action="EBARIMT_CANCEL_BULK_SUMMARY", entity="vat",
                        entity_id="DUPLICATE_OF_POS_TERMINAL",
                        detail={"tool": TOOL, "mode": "pos-app", "rule": VD.POS_APP_RULE,
                                "tenants": [t.code for t in tenants],
                                "sites": [s_.site_code for s_ in sites],
                                "from": args.date_from, "to": args.date_to,
                                "include_company": bool(args.include_company),
                                "pos_users": sorted(scope["users"]),
                                "evidence": [n for n, _t in taxes],
                                "outside_by_rule": bool(args.outside_by_rule),
                                "basis": dict(Counter(r.get("basis") for r in work)),
                                "requested": len(work), "amount": amount, "note": note,
                                "result": dict(cnt), "stopped": stopped}))
        db.commit()
    finally:
        db.close()
    write_csv(f"{stem}_applied.csv", res_rows, POSAPP_FIELDS + ["basis", "outcome", "error", "key_scope"])
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
                           "(2026-10-14 эзний шийдвэр — бүх түрээслэгч; --tenant заавал, ALL болно)")
    ap.add_argument("--tenant", help="түрээслэгч (id эсвэл code) — тэр ТТД-ийн экспорт бол; "
                                     "--pos-app үед ALL = бүх түрээслэгч")
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
                    help="--pos-app: POS аппын хэрэглэгч(ид) «pos.a,pos.b» (систем даяар) — "
                         "role POS/тохиргоо/≥20 картын таамгийг БҮХЭЛД нь дарна")
    ap.add_argument("--min-card", type=int, default=VD.POS_APP_MIN_CARD,
                    help="--pos-app: POS аппаар ≥N карт авсан хэрэглэгч = POS апп (20)")
    ap.add_argument("--evidence", action="append",
                    help="--pos-app: НЭМЭЛТ нотолгоо — ТЕГ экспорт (олон удаа, урт хугацаа болно); "
                         "хамрах хүрээнд нь ЗӨВХӨН терминалын мөртэйг цуцална, хүрээнээс гадуурхыг хасна")
    ap.add_argument("--outside-by-rule", action="store_true",
                    help="--pos-app --evidence: ТЕГ файлын хүрээнээс гадуурх борлуулалтыг ДҮРМЭЭР цуцлах "
                         "(анхдагч — хасна)")
    args = ap.parse_args(argv)
    if args.pos_app:
        return main_pos_app(args)
    if args.include_company or args.pos_user or args.evidence or args.outside_by_rule:
        ap.error("--include-company/--pos-user/--evidence/--outside-by-rule зөвхөн --pos-app-тай")

    for f in args.file:
        if not os.path.isfile(f):
            raise SystemExit(f"Файл олдсонгүй: {f}")
    if args.apply and (not args.tenant or args.tenant.strip().upper() == VD.ALL_TENANTS):
        # ТЕГ экспорт ТТД-ээр гардаг — түрээслэгчгүй (ALL) бол бусад ТТД-ийн төлбөр ч нэр
        # дэвшигч болно (2026-10-13 хяналт)
        raise SystemExit("--file --apply үед --tenant заавал, ALL биш (ТЕГ файлын ТТД-ийн түрээслэгч, "
                         "ж: EASYPARKING)")
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
            if len(dups) > args.max:
                print(max_hint(len(dups), args.max))
            if probs:
                print("\n⚠ --apply ОДООГООР ажиллахгүй: " + "; ".join(probs))
            print("\nDRY-RUN — юу ч өөрчлөгдсөнгүй. Цуцлахын тулд ижил командыг --apply --user <нэр> -тэй ажиллуулна.")
            return 0
        if args.limit is not None:
            dups = dups[:max(0, args.limit)]
        # --max нь ЭНЭ УДАА бодитоор цуцлах тоонд (--limit-ийн дараа)
        if len(dups) > args.max:
            print("\nЗОГСОВ:" + max_hint(len(dups), args.max).split(":", 1)[1])
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
