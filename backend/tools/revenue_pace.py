"""Орлогын ХУРД — өнөөдрийн орлого «буурсан» уу, «өдөр дуусаагүй» юу.

Хяналтын самбарын өдрийн нийлбэр нь дуусаагүй өдрийг дууссан өдрүүдтэй
харьцуулдаг тул үргэлж «унасан» харагддаг. Зөв харьцуулалт нь ЦАГ ТУТМЫН
ХУРИМТЛАЛ: өнөөдөр 16:00 үеийн хуримтлалыг өмнөх өдрүүдийн ЯГ 16:00 үеийн
хуримтлалтай тулгах. Гараг чухал — Даваа-г өмнөх Даваатай харьцуулах нь зөв.

Гурван зүйлийг зэрэг гаргана:
  1. Өдөр бүрийн цаг тутмын ХУРИМТЛАЛ (мян.₮) — өнөөдрийн хурд аль өдрийнхтэй
     ойролцоо явааг шууд харна
  2. Өдөр бүрийн төлбөрийн ХЭРЭГСЛИЙН задаргаа — нэг суваг (QPay/карт/бэлэн)
     унасан бол тэр нь орлогын уналтын шалтгаан
  3. Өдөр бүрийн ГАРЦЫН цуглуулалт — гарсан машин бүрээс мөнгө авч чадаж
     байна уу (төлсөн / үнэгүй / төлбөргүй алдагдсан)

Ажиллуулах (production сервер дээр, backend хавтаст):
    venv/bin/python tools/revenue_pace.py --days 8
    venv/bin/python tools/revenue_pace.py --days 8 --site HANGARID

Зөвхөн DB УНШИНА.
"""
import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal
from app.models import ParkingSession, ParkingSite, Payment, User

TZ = timedelta(hours=8)
_PROV = {"QPAY": "QPay", "POS": "Карт", "CASH": "Бэлэн", "TRANSFER": "Данс"}


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

        pq = (db.query(Payment.paid_at, Payment.amount, Payment.provider)
              .filter(Payment.status == "PAID", Payment.paid_at >= since))
        if site:
            pq = pq.join(ParkingSession, ParkingSession.id == Payment.session_id) \
                   .filter(ParkingSession.site_id == site.id)
        pays = pq.all()

        # ── 1. Цаг тутмын хуримтлал ──────────────────────────────────────────
        per: dict = defaultdict(lambda: defaultdict(float))   # day -> hour -> ₮
        prov: dict = defaultdict(lambda: defaultdict(float))  # day -> provider -> ₮
        for at, amt, provider in pays:
            loc = at + TZ
            d = loc.strftime("%m-%d")
            per[d][loc.hour] += float(amt or 0)
            prov[d][_PROV.get(provider, provider or "?")] += float(amt or 0)

        days = sorted(per)
        weekday_mn = ["Да", "Мя", "Лх", "Пү", "Ба", "Бя", "Ня"]

        def wd(d):
            dt = datetime.strptime(f"{now_local.year}-{d}", "%Y-%m-%d")
            return weekday_mn[dt.weekday()]

        title = site.name if site else "БҮХ ЗОГСООЛ"
        cur_h = now_local.hour
        print(f"══ {title} — орлогын цаг тутмын ХУРИМТЛАЛ (мян.₮, УБ цаг) ══\n")
        hdr = "   цаг  " + "".join(f"{d} {wd(d)}".rjust(11) for d in days)
        print(hdr)
        cum = {d: 0.0 for d in days}
        for h in range(24):
            cells = []
            for d in days:
                cum[d] += per[d].get(h, 0.0)
                is_today = d == days[-1]
                if is_today and h > cur_h:
                    cells.append("—".rjust(11))
                else:
                    cells.append(f"{cum[d] / 1000:,.0f}".rjust(11))
            mark = "  ← ОДОО" if h == cur_h else ""
            print(f"   {h:02d}ц " + "".join(cells) + mark)

        today = days[-1]
        same_wd = [d for d in days[:-1] if wd(d) == wd(today)]
        today_cum = sum(v for h, v in per[today].items() if h <= cur_h)
        print(f"\n   Өнөөдөр ({today} {wd(today)}) {cur_h:02d}ц хүртэлх: "
              f"{today_cum:,.0f}₮")
        for d in same_wd:
            ref = sum(v for h, v in per[d].items() if h <= cur_h)
            pct = int(today_cum * 100 // ref) if ref else 0
            print(f"   Өмнөх {wd(d)} ({d}) мөн үед: {ref:,.0f}₮ → өнөөдөр {pct}%")
        for d in days[-4:-1]:
            ref = sum(v for h, v in per[d].items() if h <= cur_h)
            pct = int(today_cum * 100 // ref) if ref else 0
            print(f"   {d} {wd(d)} мөн үед: {ref:,.0f}₮ → өнөөдөр {pct}%")

        # ── 2. Хэрэгслийн задаргаа ───────────────────────────────────────────
        provs = sorted({p for d in prov for p in prov[d]})
        print(f"\n══ Төлбөрийн хэрэгслээр (бүтэн өдөр, мян.₮) ══")
        print("   өдөр   " + "".join(p.rjust(9) for p in provs) + "    нийт".rjust(10))
        for d in days:
            tot = sum(prov[d].values())
            print(f"   {d} " + "".join(f"{prov[d].get(p, 0) / 1000:,.0f}".rjust(9)
                                       for p in provs) + f"{tot / 1000:,.0f}".rjust(10))

        # ── 2б. Хэрэгслээр — МӨН ЦАГ хүртэл (шударга харьцуулалт) ────────────
        # Дээрх хүснэгт дууссан өдрийг дуусаагүйтэй харьцуулдаг тул суваг унасан
        # эсэхийг өнөөдрийн байдлаар хэлж чадахгүй. Энэ нь өдөр бүрийн яг
        # ОДООГИЙН ЦАГ хүртэлх дүн — бэлэн суваг ӨНӨӨДӨР ч унасан хэвээр үү
        # гэдгийг шууд харна.
        prov_cut: dict = defaultdict(lambda: defaultdict(float))
        for at, amt, provider in pays:
            loc = at + TZ
            if loc.hour <= cur_h:
                prov_cut[loc.strftime("%m-%d")][_PROV.get(provider, provider or "?")] +=                     float(amt or 0)
        print(f"\n══ Хэрэгслээр — өдөр бүрийн {cur_h:02d}ц ХҮРТЭЛ (мян.₮) ══")
        print("   өдөр   " + "".join(p2.rjust(9) for p2 in provs) + "    нийт".rjust(10))
        for d in days:
            tot = sum(prov_cut[d].values())
            print(f"   {d} " + "".join(f"{prov_cut[d].get(p2, 0) / 1000:,.0f}".rjust(9)
                                       for p2 in provs) + f"{tot / 1000:,.0f}".rjust(10))

        # ── 2в. Бэлэн/Карт/Данс — КАССИР тутам өдрөөр ────────────────────────
        # Суваг унасан бол ХЭН бичихээ больсоныг нэрээр нь заана (ажлаа хийгээгүй
        # юу, эрх нь хаагдсан уу, огт нэвтрээгүй юу — эндээс мөрдөнө).
        cq = (db.query(Payment.paid_at, Payment.amount, User.username)
              .outerjoin(User, User.id == Payment.cashier_id)
              .filter(Payment.status == "PAID", Payment.paid_at >= since,
                      Payment.provider.in_(["CASH", "POS", "TRANSFER"])))
        if site:
            cq = cq.join(ParkingSession, ParkingSession.id == Payment.session_id) \
                   .filter(ParkingSession.site_id == site.id)
        by_cashier: dict = defaultdict(lambda: defaultdict(float))
        for at, amt, uname in cq.all():
            by_cashier[uname or "(кассиргүй)"][(at + TZ).strftime("%m-%d")] += float(amt or 0)
        if by_cashier:
            print(f"\n══ Бэлэн+Карт+Данс — кассир тутам өдрөөр (мян.₮) ══")
            print("   кассир          " + "".join(d.rjust(8) for d in days))
            for uname in sorted(by_cashier,
                                key=lambda u: -sum(by_cashier[u].values())):
                cells = "".join(f"{by_cashier[uname].get(d, 0) / 1000:,.0f}".rjust(8)
                                for d in days)
                print(f"   {uname[:15]:16}{cells}")

        # ── 2г. ЗОГСООЛ тутам өдрөөр — аль зогсоол «харанхуйлсныг» заана ─────
        # Кассир ажиллахаа больсон бол тэр зогсоолын орлого унана. Энэ хүснэгт
        # ажилтны асуудлыг ЗОГСООЛЫН газрын зурагт буулгана.
        sq2 = (db.query(Payment.paid_at, Payment.amount, ParkingSite.name)
               .join(ParkingSession, ParkingSession.id == Payment.session_id)
               .join(ParkingSite, ParkingSite.id == ParkingSession.site_id)
               .filter(Payment.status == "PAID", Payment.paid_at >= since))
        if site:
            sq2 = sq2.filter(ParkingSession.site_id == site.id)
        by_site: dict = defaultdict(lambda: defaultdict(float))
        for at, amt, sname in sq2.all():
            by_site[sname][(at + TZ).strftime("%m-%d")] += float(amt or 0)
        if by_site:
            print(f"\n══ ЗОГСООЛ тутам өдрөөр (мян.₮) — аль нь харанхуйлав ══")
            print("   зогсоол         " + "".join(d.rjust(8) for d in days))
            for sname in sorted(by_site, key=lambda n: -sum(by_site[n].values())):
                row = by_site[sname]
                cells = "".join(f"{row.get(d, 0) / 1000:,.0f}".rjust(8) for d in days)
                # Сүүлийн 3 хоног өмнөх 3 хоногийн 25%-аас бага бол анхааруулна
                prev = sum(row.get(d, 0) for d in days[-6:-3]) or 0
                last = sum(row.get(d, 0) for d in days[-3:]) or 0
                flag = "  ⚠ УНАСАН" if prev > 100_000 and last < prev * 0.25 else ""
                print(f"   {sname[:15]:16}{cells}{flag}")

        # ── 2д. КАССИР × ЗОГСООЛ — хэн хаана ажилладаг байсан бэ ────────────
        # Бизнесийн загвар (2026-08-17): онлайн оператор «Дансаар» аваад алсаас
        # хаалт нээнэ; БЭЛЭН мөнгийг ЗӨВХӨН газар дээрх ажилтан POS дээр авна.
        # Тиймээс газар дээрх кассир алга болвол тэр зогсоолын бэлэн орлого
        # ТЭГ болно — онлайн оператор түүнийг орлож чадах эсэхийг эндээс харна.
        cs = (db.query(User.username, ParkingSite.name, Payment.provider,
                       Payment.amount, Payment.paid_at)
              .join(Payment, Payment.cashier_id == User.id)
              .join(ParkingSession, ParkingSession.id == Payment.session_id)
              .join(ParkingSite, ParkingSite.id == ParkingSession.site_id)
              .filter(Payment.status == "PAID", Payment.paid_at >= since))
        if site:
            cs = cs.filter(ParkingSession.site_id == site.id)
        pair: dict = defaultdict(lambda: defaultdict(float))
        pair_last: dict = defaultdict(str)
        for uname, sname, provider, amt, at in cs.all():
            key = (uname, sname)
            pair[key][_PROV.get(provider, provider or "?")] += float(amt or 0)
            d = (at + TZ).strftime("%m-%d")
            if d > pair_last[key]:
                pair_last[key] = d
        if pair:
            print(f"\n══ КАССИР × ЗОГСООЛ ({args.days} хоног, мян.₮) ══")
            print(f"   {'кассир':14}{'зогсоол':17}"
                  + "".join(p2.rjust(8) for p2 in provs) + f"{'сүүлд':>8}")
            for (uname, sname) in sorted(pair, key=lambda k: -sum(pair[k].values())):
                cells = "".join(f"{pair[(uname, sname)].get(p2, 0) / 1000:,.0f}".rjust(8)
                                for p2 in provs)
                last = pair_last[(uname, sname)]
                flag = "  ⚠ ЗОГССОН" if last < days[-1] and last < days[-2] else ""
                print(f"   {uname[:12]:14}{sname[:15]:17}{cells}{last:>8}{flag}")

        # ── 3. Гарцын цуглуулалт ─────────────────────────────────────────────
        eq = (db.query(ParkingSession)
              .filter(ParkingSession.exit_time >= since,
                      ParkingSession.status.in_(["CLOSED", "FREE", "MANUAL_CLOSED"])))
        if site:
            eq = eq.filter(ParkingSession.site_id == site.id)
        exits = eq.all()
        paid_ids = {sid for (sid,) in db.query(Payment.session_id)
                    .filter(Payment.status == "PAID",
                            Payment.session_id.in_([s.id for s in exits])).all()} \
            if exits else set()
        ex: dict = defaultdict(lambda: [0, 0, 0, 0])  # d -> [гарсан, төлсөн, үнэгүй, алдагдсан]
        for s in exits:
            d = (s.exit_time + TZ).strftime("%m-%d")
            row = ex[d]
            row[0] += 1
            if s.id in paid_ids:
                row[1] += 1
            elif float(s.total_fee or 0) == 0:
                row[2] += 1
            else:
                row[3] += 1
        print(f"\n══ Гарцын цуглуулалт (гарсан өдрөөр) ══")
        print(f"   {'өдөр':8}{'гарсан':>8}{'төлсөн':>8}{'үнэгүй':>8}"
              f"{'ТӨЛБӨРГҮЙ':>11}{'цуглуулалт%':>13}")
        for d in sorted(ex):
            g, p, f, l = ex[d]
            billable = p + l
            rate = p * 100 // billable if billable else 100
            flag = "  ⚠" if billable and rate < 70 else ""
            print(f"   {d:8}{g:8}{p:8}{f:8}{l:11}{rate:12}%{flag}")
        print("   цуглуулалт% = төлсөн / (төлсөн + төлбөргүй). «үнэгүй» = гэрээт/"
              "хугацаанд багтсан/0₮ хаалт — хуваарьт орохгүй")
    finally:
        db.close()


if __name__ == "__main__":
    main()
