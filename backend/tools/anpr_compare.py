"""ANPR системийн логтой тулгаж, МАНАЙ систем хэдэн уншилт алдсаныг ХЭМЖИНЭ.

172.16.100.20 дээрх ANPR систем нь ЯГ ТЭР камеруудаас уншилт авдаг бөгөөд өдрийн
логоо CSV-ээр гаргадаг. Тэр нь бидний хувьд ЛАВЛАГАА: тэдэнд байгаад бидэнд
байхгүй уншилт = манай алдсан машин. Одоог хүртэл бид алдагдлаа зөвхөн шууд бус
шинжээр (ghost дугаар, no_session гарц) таамаглаж ирсэн.

CSV формат (Лог хуудасны «CSV татах»):
    date,time,tag,detail
    "2026-08-21","13:49:32","[ANPR_EVENT]","plate=5766ХЭН  conf=94%  dir=enter  lot=""ҮЦХ-Баруун""  cam=58"

Ажиллуулах (production сервер дээр, backend хавтаст):
    venv/bin/python tools/anpr_compare.py /зам/log_2026-08-21.csv
    venv/bin/python tools/anpr_compare.py log.csv --window 10     # тохирох цонх (мин)
    venv/bin/python tools/anpr_compare.py log.csv --site RASH     # зөвхөн нэг зогсоол
    venv/bin/python tools/anpr_compare.py log.csv --list-missing  # алдсан уншилтуудыг жагсаах
"""
import argparse
import csv
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal  # noqa: E402
from app.models import LprEvent, ParkingSite  # noqa: E402
from app.session_logic import normalize_plate, plates_ocr_similar  # noqa: E402

TZ = timedelta(hours=8)          # Улаанбаатар — ЗӨВХӨН харуулахад

# CSV-ийн цаг UTC уу, орон нутгийн уу гэдэг нь ANPR системийн хувилбараас
# хамаарч ӨӨР байдаг (лог хуудас нь UTC, event хүснэгт нь УБ цагаар харуулдаг).
# Тааруулж таамаглавал БҮХ уншилт «алдагдсан» гэж худал гарна — 2026-08-21-нд
# яг ингэж 100% алдагдал харуулж байсан. Тиймээс ТААМАГЛАХГҮЙ: боломжит
# шилжилт бүрээр тулгаад ХАМГИЙН ОЛОН тохирлыг өгснийг нь сонгоно.
OFFSETS = [timedelta(0), timedelta(hours=-8), timedelta(hours=8)]

# ANPR системийн зогсоолын нэр → манай зогсоолын код.
# Тэдэнд байгаа атал бидэнд байхгүй зогсоол (Их монгол, Маргад, 100 айл…) энд
# ОРОХГҮЙ — тэдгээр нь одоогоор манай тооцоонд хамаарахгүй.
LOT_MAP = {
    "MonnisBuilding": "MONNIS",
    "Раш булаг": "RASH",
    "Раш булаг шороо": "RASH",     # доторх (nested) талбай — ижил зогсоол
    # «СТӨ» нь Соёлын төв (2026-08-21 баталсан). Нэрээр таавал «Спортын төв
    # ордон» руу буруу очих эрсдэлтэй тул ЗААВАЛ энд бичсэн байх ёстой.
    "СТӨ": "STO",
    "Хан гарьд": "HANGARD",
    "kh": "KH",
    "Ялалт": "YALALT",
    "Туушин": "TUUSHIN",
    "Номадс": "NOMADS",
    "Спорт": "SPORT",
    "Эрэл": "EREL",
}

ROW = re.compile(r'plate=(\S+)\s+conf=(\d+)%\s+dir=(\w+)\s+lot="([^"]*)"\s+cam=(\d+)')


def read_csv(path: str) -> list[dict]:
    """CSV-ээс ANPR_EVENT мөрүүдийг задална (цагийг UTC болгоно)."""
    out = []
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if (r.get("tag") or "").strip() != "[ANPR_EVENT]":
                continue
            m = ROW.match((r.get("detail") or "").strip())
            if not m:
                continue
            plate, conf, direction, lot, cam = m.groups()
            try:
                at = datetime.strptime(f"{r['date']} {r['time']}", "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
            out.append({"at": at, "plate": normalize_plate(plate), "raw_plate": plate,
                        "conf": int(conf), "dir": direction, "lot": lot.strip(), "cam": cam})
    return out


def _norm(s: str) -> str:
    """Нэрийг тулгахад бэлдэнэ: жижиг үсэг, зай/зураас/цэг хасна."""
    return re.sub(r"[\s\-_.]", "", (s or "").lower())


def resolve_sites(db, lots: set[str], overrides: dict, how: dict) -> dict:
    """Тэдний зогсоолын нэр → манай ParkingSite.

    Дараалал: --map (гараар) → LOT_MAP → site_code → нэрийн ойролцоо тохирол.
    Тохироогүйг дуудагч талд «манай системд байхгүй» гэж жагсаана."""
    all_sites = db.query(ParkingSite).all()
    by_code = {(s.site_code or "").upper(): s for s in all_sites}
    out = {}
    for lot in lots:
        code = (overrides.get(lot) or LOT_MAP.get(lot) or "").upper()
        site = by_code.get(code)
        by_code_hit = site is not None
        if site is None:
            # Нэрийн ойролцоо тохирол — аль нэг нь нөгөөгөө агуулж байвал
            k = _norm(lot)
            if len(k) >= 3:
                for s in all_sites:
                    n = _norm(s.name)
                    if k in n or (len(n) >= 3 and n in k):
                        site = s
                        break
        if site is not None:
            out[lot] = site
            # ҮНЭН ЯЛГАА: кодоор олдсон уу, эсвэл нэрээр ТААСАН уу.
            # Жагсаалтад байгаа ч код нь энэ DB-д байхгүй бол мөн л таалт.
            how[lot] = (("гараар (--map)" if lot in overrides else "жагсаалтаас")
                        if by_code_hit else "НЭРЭЭР ТААСАН")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv_path")
    ap.add_argument("--window", type=float, default=5.0,
                    help="тохирох цонх, минут (default 5) — камерын цагийн зөрүүг тэвчинэ")
    ap.add_argument("--site", help="зөвхөн энэ зогсоол (site_code)")
    ap.add_argument("--list-missing", action="store_true", help="алдсан уншилтуудыг жагсаах")
    ap.add_argument("--map", action="append", default=[], metavar="ЛОТ=КОД",
                    help="зогсоолын зураглалыг гараар (ж: --map \"Хан гарьд=HANGARYD\")")
    a = ap.parse_args()

    rows = read_csv(a.csv_path)
    if not rows:
        print("CSV-ээс ANPR_EVENT мөр олдсонгүй")
        return
    day = (min(r["at"] for r in rows) + TZ).date()
    print(f"══ ANPR лог vs манай систем · {day} ══")
    print(f"   CSV: {len(rows):,} уншилт, {len({r['lot'] for r in rows})} зогсоол\n")

    db = SessionLocal()
    ov = {}
    for item in a.map:
        lot, _, code = item.partition("=")
        if code:
            ov[lot.strip()] = code.strip()
    how: dict = {}
    sites = resolve_sites(db, {r["lot"] for r in rows}, ov, how)
    unmapped = sorted({r["lot"] for r in rows} - set(sites))

    # Манай уншилтууд — тухайн өдрийн (UTC мужаар, цонхны зайтай)
    lo = min(r["at"] for r in rows) - timedelta(minutes=a.window)
    hi = max(r["at"] for r in rows) + timedelta(minutes=a.window)
    ours = defaultdict(list)     # site_id → [(at, plate)]
    for at, plate, sid in (db.query(LprEvent.created_at, LprEvent.plate_number, LprEvent.site_id)
                           .filter(LprEvent.created_at >= lo, LprEvent.created_at <= hi).all()):
        ours[sid].append((at, plate))

    win = timedelta(minutes=a.window)

    def count_matches(shift: timedelta) -> int:
        """Тухайн цагийн шилжилтэд хэдэн уншилт тохирохыг тоолно."""
        n = 0
        for lot, site in sites.items():
            by_plate = defaultdict(list)
            for at, p in ours.get(site.id, []):
                by_plate[p].append(at)
            for r in rows:
                if r["lot"] != lot:
                    continue
                t0 = r["at"] + shift
                if any(abs((t - t0).total_seconds()) <= win.total_seconds()
                       for t in by_plate.get(r["plate"], ())):
                    n += 1
        return n

    # Шилжилтийг ӨГӨГДЛӨӨР сонгоно (таамаглахгүй)
    scored = [(count_matches(o), o) for o in OFFSETS]
    best_n, shift = max(scored, key=lambda x: x[0])
    for n, o in sorted(scored, key=lambda x: -x[0]):
        h = int(o.total_seconds() // 3600)
        print(f"   цагийн шилжилт {h:+3d}ц → {n:5} тохирол"
              + ("   ← сонгов" if o == shift else ""))
    if best_n == 0:
        print("\n   ⚠ Ямар ч шилжилтэд тохирол олдсонгүй — зогсоолын зураглал "
              "буруу байж болзошгүй (--map-аар зааж өгнө үү).")
    print()
    for r in rows:
        r["at"] += shift

    print(f"{'Зогсоол':<22}{'ANPR':>7}{'Манай':>8}{'Тохирсон':>10}{'АЛДСАН':>9}{'алдагдал':>10}")
    print("─" * 66)
    totals = [0, 0, 0]
    missing_all, zero_sites = [], []
    by_site = defaultdict(list)
    for lot, site in sites.items():
        by_site[site.id].append(lot)
    for sid, lots_ in sorted(by_site.items(), key=lambda kv: sites[kv[1][0]].name):
        site = sites[lots_[0]]
        if a.site and site.site_code != a.site.upper():
            continue
        theirs = [r for r in rows if r["lot"] in lots_]
        mine = ours.get(site.id, [])
        by_plate = defaultdict(list)
        for at, p in mine:
            by_plate[p].append(at)
        matched, missing = 0, []
        for r in theirs:
            cand = by_plate.get(r["plate"])
            hit = cand and any(abs((t - r["at"]).total_seconds()) <= win.total_seconds()
                               for t in cand)
            if not hit:      # OCR-ойролцоо уншилтыг ч тохируулж үзнэ
                hit = any(plates_ocr_similar(r["plate"], p)
                          and any(abs((t - r["at"]).total_seconds()) <= win.total_seconds()
                                  for t in ts)
                          for p, ts in by_plate.items())
            if hit:
                matched += 1
            else:
                missing.append(r)
        loss = 100 * len(missing) / len(theirs) if theirs else 0
        mark = "  ⚠" if loss >= 20 else ""
        print(f"{site.name[:21]:<22}{len(theirs):>7}{len(mine):>8}{matched:>10}"
              f"{len(missing):>9}{loss:>9.0f}%{mark}")
        if len(theirs) >= 50 and not mine:
            zero_sites.append((site.name, lots_, len(theirs)))
        totals[0] += len(theirs)
        totals[1] += len(mine)
        totals[2] += matched
        missing_all += [(site.name, r) for r in missing]

    print("─" * 66)
    lost = totals[0] - totals[2]
    pct = 100 * lost / totals[0] if totals[0] else 0
    print(f"{'НИЙТ':<22}{totals[0]:>7}{totals[1]:>8}{totals[2]:>10}{lost:>9}{pct:>9.0f}%")

    if zero_sites:
        print("\n   ⛔ ANPR-т уншилт бий атал МАНАЙД ТЭГ — зураглал буруу эсвэл "
              "бүтэн өдрийн тасалдал:")
        for name, lots_, n in zero_sites:
            print(f"      «{'», «'.join(lots_)}» → «{name}» — тэдэнд {n}, манайд 0")
        print("      Зураглалыг шалгах: --map \"<тэдний нэр>=<манай код>\"")

    guessed = [(lot, sites[lot].name) for lot, h in how.items() if h == "НЭРЭЭР ТААСАН"]
    if guessed:
        print("\n   ⚠ НЭРЭЭР таасан зураглал — баталгаажуулна уу:")
        for lot, name in sorted(guessed):
            print(f"      «{lot}» → «{name}»")
        print("      Буруу бол: --map \"<тэдний нэр>=<манай код>\" эсвэл LOT_MAP-д бичнэ.")

    if unmapped:
        print(f"\n   ⓘ Манай системд БАЙХГҮЙ {len(unmapped)} зогсоол (тооцоонд ороогүй):")
        print("     " + ", ".join(unmapped))

    if missing_all:
        print(f"\n   Алдсан уншилтын ЦАГИЙН тархалт (УБ цагаар):")
        by_hour = defaultdict(int)
        for _s, r in missing_all:
            by_hour[(r["at"] + TZ).hour] += 1
        for h in sorted(by_hour):
            print(f"     {h:02d}:00  {'█' * min(60, by_hour[h])} {by_hour[h]}")
    if a.list_missing:
        print(f"\n   Алдсан уншилтууд ({len(missing_all)}):")
        for s, r in missing_all[:400]:
            print(f"     {(r['at'] + TZ):%H:%M:%S}  {s:<18} {r['raw_plate']:<10} "
                  f"{r['dir']:<6} cam={r['cam']} conf={r['conf']}%")
        if len(missing_all) > 400:
            print(f"     … нийт {len(missing_all)}")


if __name__ == "__main__":
    main()
