"""Орлогын ХЭМЖЭЭ биш, ЭХ СУРВАЛЖ — «огцом унасан» уу, «нэг өдөр өндөр байсан» уу.

Хяналтын самбарын өдрийн багана нэг тоо харуулдаг тул нэг өндөр өдөр бүхэлдээ
«хэвийн түвшин» мэт харагдаж, дараагийн өдрүүд «унасан» болж хардагддаг. Гэвч
нэг өдрийн орлого гурван тэс өөр зүйлээс бүрдэнэ:

  1. ШИНЭ    — тэр өдөр орж гарсан машины төлбөр (жинхэнэ өдрийн хүчин чадал)
  2. ӨР      — хуучин session/нөхөн төлбөр тэр өдөр цугларсан (нэг удаагийн)
  3. БӨӨН    — нэг кассир богино хугацаанд олон төлбөр бүртгэсэн (цэвэрлэгээ/
               тулгалт хийсэн бол тэр өдөр хиймэл өндөр болно)

Мөн зогсоол бүрийн ЦУГЛУУЛАЛТЫН ЦОНХ (эхний–сүүлийн төлбөрийн цаг, тоо) —
«кассир хэдээс хэд хүртэл ажилласан» гэдгийг ТАААМАГЛАХГҮЙ, шууд хэмжинэ.
2026-08-17-ны шинжилгээ уналтыг ажилтан нэвтрээгүйтэй холбосон тул энэ цонх нь
тэр дүгнэлтийг өдөр бүр давтан шалгах хэмжүүр болно.

Ажиллуулах (production сервер дээр, backend хавтаст):
    venv/bin/python tools/revenue_source_audit.py --days 8
    venv/bin/python tools/revenue_source_audit.py --days 14 --site RASH

Зөвхөн DB УНШИНА. Хосолж ажиллуулах: tools/revenue_pace.py (хурд/кассир/суваг),
tools/site_drop_diag.py (нэг зогсоолын дөрвөн шалтгаан).
"""
import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal
from app.models import Compensation, ParkingSession, ParkingSite, Payment, User

TZ = timedelta(hours=8)
WD = ["Да", "Мя", "Лх", "Пү", "Ба", "Бя", "Ня"]
# Нэг кассир ийм хугацаанд ийм олон төлбөр бүртгэсэн бол «бөөн» гэж тэмдэглэнэ.
BULK_WINDOW_MIN = 30
BULK_COUNT = 10


def k(v):
    return f"{v / 1000:,.0f}"


def spike_days(days, day_tot, day_debt, factor=1.5):
    """Дууссан өдрүүдийн МЕДИАНААС factor дахин өндөр өдрүүд + өндөрлөлтийн
    хэдэн хувь нь ӨР цуглуулалт байсныг буцаана.

    Медиан ашигласан шалтгаан: дундаж нь тэр ганц өндөр өдрөө өөртөө шингээж
    «түвшин» гэдгийг гажуудуулна.  Буцаалт: (median, [(өдөр, илүү₮, өр%)])"""
    base = list(days)
    if not base:
        return 0.0, []
    vals = sorted(day_tot[d] for d in base)
    med = vals[len(vals) // 2]
    out = []
    for d in base:
        if day_tot[d] > med * factor:
            extra = day_tot[d] - med
            out.append((d, extra, (day_debt[d] / extra * 100) if extra > 0 else 0.0))
    return med, out


def find_bulks(by_cash, window_min=BULK_WINDOW_MIN, count=BULK_COUNT):
    """Нэг кассир `window_min` дотор `count`+ төлбөр бүртгэсэн тохиолдлууд.

    Гүйдэг цонх: (өдөр, кассир) -> [(эхлэл, төгсгөл, тоо)] — өдөрт нэг удаа."""
    out = []
    for (d, uname), times in by_cash.items():
        ts = sorted(times)
        i = 0
        for j in range(len(ts)):
            while ts[j] - ts[i] > timedelta(minutes=window_min):
                i += 1
            if j - i + 1 >= count:
                out.append((d, uname, ts[i], ts[j], j - i + 1))
                break
    return sorted(out)



def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=8)
    ap.add_argument("--site", help="зөвхөн нэг зогсоол (код эсвэл нэрний эхлэл)")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        now_local = datetime.utcnow() + TZ
        start_local = (now_local - timedelta(days=args.days - 1)).replace(
            hour=0, minute=0, second=0, microsecond=0)
        since = start_local - TZ

        site = None
        if args.site:
            site = (db.query(ParkingSite).filter(ParkingSite.site_code == args.site).first()
                    or db.query(ParkingSite)
                    .filter(ParkingSite.name.ilike(f"{args.site}%")).first())
            if not site:
                sys.exit(f"«{args.site}» олдсонгүй")

        q = (db.query(Payment.id, Payment.paid_at, Payment.amount, Payment.cashier_id,
                      ParkingSession.site_id, ParkingSession.entry_time, ParkingSite.name)
             .join(ParkingSession, ParkingSession.id == Payment.session_id)
             .join(ParkingSite, ParkingSite.id == ParkingSession.site_id)
             .filter(Payment.status == "PAID", Payment.paid_at >= since))
        if site:
            q = q.filter(ParkingSession.site_id == site.id)
        pays = q.all()
        if not pays:
            sys.exit("Энэ мужид төлөгдсөн төлбөр алга.")

        # Нэг төлбөрт хавсарсан ӨРийн дүн (Compensation.payment_id) — үлдсэн нь ШИНЭ
        debt_of: dict[str, float] = defaultdict(float)
        pids = [p.id for p in pays]
        for i in range(0, len(pids), 1000):
            for pid, amt in (db.query(Compensation.payment_id, Compensation.amount)
                             .filter(Compensation.payment_id.in_(pids[i:i + 1000]),
                                     Compensation.status != "CANCELLED").all()):
                debt_of[pid] += float(amt or 0)

        names = {u.id: u.username for u in db.query(User).all()}

        day_tot: dict = defaultdict(float)
        day_debt: dict = defaultdict(float)
        day_late: dict = defaultdict(float)   # хуучин өдрийн session-ий төлбөр
        day_cnt: dict = defaultdict(int)
        day_top: dict = defaultdict(list)
        # (өдөр, зогсоол) -> [эхний цаг, сүүлийн цаг, тоо, дүн]
        win: dict = defaultdict(lambda: [None, None, 0, 0.0])
        # (өдөр, кассир) -> төлбөрийн цагууд (бөөн илрүүлэхэд)
        by_cash: dict = defaultdict(list)

        for pid, at, amount, cashier_id, sid, entry_time, sname in pays:
            loc = at + TZ
            d = loc.strftime("%m-%d")
            amt = float(amount or 0)
            dbt = min(debt_of.get(pid, 0.0), amt)
            day_tot[d] += amt
            day_debt[d] += dbt
            day_cnt[d] += 1
            day_top[d].append((amt, sname))
            if entry_time and (entry_time + TZ).date() != loc.date():
                day_late[d] += amt - dbt
            w = win[(d, sname)]
            w[0] = loc if w[0] is None or loc < w[0] else w[0]
            w[1] = loc if w[1] is None or loc > w[1] else w[1]
            w[2] += 1
            w[3] += amt
            by_cash[(d, names.get(cashier_id, "(кассиргүй)"))].append(loc)

        days = sorted(day_tot)
        title = site.name if site else "БҮХ ЗОГСООЛ"

        def wd(d):
            return WD[datetime.strptime(f"{now_local.year}-{d}", "%Y-%m-%d").weekday()]

        # ── 1. Өдөр бүрийн орлогыг эх сурвалжаар нь задлах ───────────────────
        print(f"══ {title} — орлогын ЭХ СУРВАЛЖ (мян.₮, УБ цаг) ══\n")
        print(f"   {'өдөр':9}{'нийт':>9}{'шинэ':>9}{'ӨР':>9}{'хойшилсон':>11}"
              f"{'тоо':>6}{'дундаж₮':>9}   хамгийн том 3 төлбөр")
        for d in days:
            tot, dbt = day_tot[d], day_debt[d]
            late = day_late[d]
            top = sorted(day_top[d], reverse=True)[:3]
            tops = ", ".join(f"{k(a)}к·{n[:10]}" for a, n in top)
            avg = tot / day_cnt[d] if day_cnt[d] else 0
            mark = " ← ӨНӨӨДӨР (дуусаагүй)" if d == days[-1] else ""
            print(f"   {d} {wd(d)}  {k(tot):>8}{k(tot - dbt):>9}{k(dbt):>9}{k(late):>11}"
                  f"{day_cnt[d]:>6}{avg:>9,.0f}   {tops}{mark}")

        med, spikes = spike_days(days[:-1], day_tot, day_debt)
        if len(days) >= 4:
            print(f"\n   Дундаж (медиан, дууссан өдрүүд): {k(med)}к₮")
            for d, extra, dshare in spikes:
                print(f"   {d} {wd(d)} нь медианаас {k(extra)}к₮ өндөр — "
                      f"үүний {dshare:.0f}% нь ӨР цуглуулалт")
                if dshare >= 50:
                    print("      → Тэр өдөр ХУУЧИН АВЛАГА цугларсан = нэг удаагийн. "
                          "Дараагийн өдрүүд «унасан» биш, ХЭВИЙН түвшиндээ буцсан.")
                else:
                    print("      → Өндөрлөлт нь ШИНЭ зогсолтоос — тэр өдрийн хүчин чадал "
                          "бодит байсан. Дараах уналт ЖИНХЭНЭ (кассир/камер/тариф шалга).")
            if not spikes:
                print("   Медианаас эрс өндөр өдөр алга — түвшин тогтвортой, "
                      "уналт нь ЗОГСООЛ тутмын асуудал (доорх хүснэгт).")

        # ── 2. Бөөн бүртгэл (нэг кассир богино хугацаанд олон төлбөр) ────────
        bulks = find_bulks(by_cash)
        if bulks:
            print(f"\n══ БӨӨН бүртгэл ({BULK_WINDOW_MIN} мин дотор {BULK_COUNT}+ төлбөр) ══")
            for d, uname, a, b, n in bulks:
                print(f"   {d} {uname[:14]:15} {a:%H:%M}–{b:%H:%M}  {n}+ төлбөр")
            print("   → Ийм өдрийн дүнг «өдрийн хэвийн хүчин чадал» гэж үзэж болохгүй.")
        else:
            print(f"\n   Бөөн бүртгэл илрээгүй ({BULK_WINDOW_MIN} мин / {BULK_COUNT}+).")

        # ── 3. Зогсоол бүрийн ЦУГЛУУЛАЛТЫН ЦОНХ ──────────────────────────────
        # «Ажилтан нэвтрээгүй» гэдгийг төлбөрийн байхгүйгээс ТААМАГЛАХ биш,
        # ажилласан цагийн мужаар нь шууд харна: цонх богиноссон/алга болсон
        # зогсоол = ажилтны асуудал; цонх хэвийн ч дүн бага = тариф/урсгал.
        sites = sorted({s for (_, s) in win})
        print("\n══ ЦУГЛУУЛАЛТЫН ЦОНХ — зогсоол × өдөр (эхний–сүүлийн төлбөр · тоо) ══")
        for sname in sites:
            print(f"   {sname[:18]:19}", end="")
            for d in days:
                w = win.get((d, sname))
                cell = f"{w[0]:%H}-{w[1]:%H}·{w[2]}" if w else "—"
                print(f"{cell:>12}", end="")
            print()
        print("\n══ ЦУГЛУУЛАЛТЫН ДҮН — зогсоол × өдөр (мян.₮) ══")
        for sname in sorted(sites, key=lambda s: -sum(win.get((d, s), [0, 0, 0, 0])[3]
                                                      for d in days)):
            print(f"   {sname[:18]:19}", end="")
            for d in days:
                w = win.get((d, sname))
                print(f"{(k(w[3]) if w else '—'):>12}", end="")
            print()
            last3 = [win.get((d, sname), [0, 0, 0, 0.0])[3] for d in days[-4:-1]]
            prev3 = [win.get((d, sname), [0, 0, 0, 0.0])[3] for d in days[-7:-4]]
            if sum(prev3) > 300_000 and sum(last3) < sum(prev3) * 0.3:
                print(f"   {'':19}⚠ {sname} — сүүлийн 3 хоног өмнөх 3 хоногийн "
                      f"{sum(last3) * 100 / sum(prev3):.0f}% (site_drop_diag --site-аар гүнзгийрүүл)")
    finally:
        db.close()


if __name__ == "__main__":
    main()
