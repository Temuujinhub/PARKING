"""Зогсоолын орлого АЛЬ ӨДӨР, АЛЬ ШАТАНД тасарсныг олно.

`site_drop_diag.py` нь ЗӨВХӨН хоёр өдрийг (өнөөдөр vs лавлагаа) тулгадаг тул
«хэдийд эвдэрсэн» гэдгийг олж чаддаггүй. Энэ хэрэгсэл нь сүүлийн N хоногийг
дараалуулан тавьж, орлогын хоолойн ДӨРВӨН ШАТЫГ зэрэг хардаг:

    камерын УНШИЛТ → session НЭЭГДЭХ → ГАРЦ бүртгэгдэх → ТӨЛБӨР цуглуулах

Аль шат нь эхлээд унасан бэ — тэр л жинхэнэ шалтгаан. Жишээ нь:
  · уншилт унасан           → камер/стрим (сүлжээ, тоос, эзэн булаалт)
  · уншилт хэвийн, session ↓ → callback/дүрэм (device_key, accepted=false)
  · session хэвийн, гарц ↓   → гарах камер эсвэл гарц баталгаажихгүй байна
  · гарц хэвийн, орлого ↓    → кассир алга / төлбөрийн суваг тасарсан

Мөн өдөр бүрийн хамгийн урт УНШИЛТГҮЙ ЦООРХОЙ-г хэмжинэ (стрим тасралтын
гарын үсэг) ба камер бүрийн өдөр тутмын уншилтыг матрицаар харуулна —
аль ТУХАЙН камер унтарсныг шууд заана.

Ажиллуулах (production сервер дээр, backend хавтаст):
    venv/bin/python tools/site_break_timeline.py --site Эрэл
    venv/bin/python tools/site_break_timeline.py --site EREL --days 14
    venv/bin/python tools/site_break_timeline.py --all --days 10

Зөвхөн DB УНШИНА — камер руу хандахгүй, юу ч бичихгүй.
"""
import argparse
import os
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import func

from app.database import SessionLocal
from app.models import (Device, LprEvent, ParkingSession, ParkingSite, Payment,
                        User)

TZ = timedelta(hours=8)
WD = ["Да", "Мя", "Лх", "Пү", "Ба", "Бя", "Ня"]


def utcnow():
    """Naive UTC — DB-д хадгалагдсан хэлбэртэй ижил."""
    return datetime.now(timezone.utc).replace(tzinfo=None)

# Хоолойн шатууд — ДАРААЛАЛ чухал: эхэлж унасан шат нь шалтгаан.
STAGES = [
    ("reads_in", "УНШИЛТ (орох)", "камер/стрим — LPR уншилт ирэхээ больсон"),
    ("ent", "SESSION нээгдэх", "callback/дүрэм — уншилт ирсэн ч session үүсэхгүй"),
    ("exits", "ГАРЦ бүртгэгдэх", "гарах камер эсвэл гарц баталгаажилт тасарсан"),
    ("billed", "ТӨЛБӨРТЭЙ гарц", "тариф/үнэгүй хугацаа/гэрээт жагсаалт өөрчлөгдсөн"),
    ("rev", "ОРЛОГО цуглуулах", "кассир алга эсвэл төлбөрийн суваг тасарсан"),
]


def day_bounds(d):
    """УБ-ын өдрийн эхлэл/төгсгөлийг серверийн UTC-д хөрвүүлнэ."""
    start_local = datetime(d.year, d.month, d.day)
    return start_local - TZ, start_local + timedelta(days=1) - TZ


def collect(db, site, days, cut_hour):
    """Өдөр тутмын хэмжүүрүүд. Өнөөдөр дуусаагүй тул cut_hour-оор таслана."""
    now_local = utcnow() + TZ
    today = now_local.date()
    rows = []
    cam_daily = defaultdict(dict)   # device_id -> {огноо: тоо}

    for i in range(days - 1, -1, -1):
        d = today - timedelta(days=i)
        a, b = day_bounds(d)
        partial = d == today
        if partial:
            b = min(b, a + timedelta(hours=cut_hour + 1))

        reads = (db.query(LprEvent.lane_dir, LprEvent.created_at, LprEvent.device_id)
                 .filter(LprEvent.site_id == site.id, LprEvent.accepted.is_(True),
                         LprEvent.created_at >= a, LprEvent.created_at < b)
                 .order_by(LprEvent.created_at).all())
        r_in = sum(1 for r in reads if r[0] == "entry")
        r_out = sum(1 for r in reads if r[0] == "exit")
        for _, _, dev in reads:
            if dev:
                cam_daily[dev][d] = cam_daily[dev].get(d, 0) + 1

        # Хамгийн урт уншилтгүй цоорхой (өдрийн эхлэл/төгсгөлийг мөн тооцно)
        marks = [a] + [r[1] for r in reads] + [b]
        gap = max((marks[j + 1] - marks[j]).total_seconds() / 3600
                  for j in range(len(marks) - 1)) if len(marks) > 1 else 0

        ent = (db.query(func.count(ParkingSession.id))
               .filter(ParkingSession.site_id == site.id,
                       ParkingSession.entry_time >= a,
                       ParkingSession.entry_time < b).scalar()) or 0
        fees = [float(f or 0) for (f,) in
                db.query(ParkingSession.total_fee)
                .filter(ParkingSession.site_id == site.id,
                        ParkingSession.exit_time >= a, ParkingSession.exit_time < b,
                        ParkingSession.status.in_(["CLOSED", "FREE", "MANUAL_CLOSED"])).all()]
        billed = sum(1 for f in fees if f > 0)

        rev = (db.query(func.coalesce(func.sum(Payment.amount), 0))
               .join(ParkingSession, ParkingSession.id == Payment.session_id)
               .filter(ParkingSession.site_id == site.id, Payment.status == "PAID",
                       Payment.paid_at >= a, Payment.paid_at < b).scalar())
        cashiers = (db.query(func.count(func.distinct(Payment.cashier_id)))
                    .join(ParkingSession, ParkingSession.id == Payment.session_id)
                    .filter(ParkingSession.site_id == site.id, Payment.status == "PAID",
                            Payment.cashier_id.isnot(None),
                            Payment.paid_at >= a, Payment.paid_at < b).scalar()) or 0

        rows.append({"d": d, "partial": partial, "reads_in": r_in, "reads_out": r_out,
                     "gap": gap, "ent": ent, "exits": len(fees), "billed": billed,
                     "free": len(fees) - billed, "rev": float(rev or 0),
                     "cashiers": cashiers})
    return rows, cam_daily


def baseline(rows, key):
    """Дууссан өдрүүдийн эхний хагасын медиан — «хэвийн» түвшин."""
    done = [r for r in rows if not r["partial"]]
    half = done[: max(1, len(done) // 2)]
    return statistics.median([r[key] for r in half]) if half else 0


def find_break(rows):
    """Орлого эхлээд унасан өдөр, тэр өдөр эхлээд унасан ШАТ-ыг буцаана."""
    base = {k: baseline(rows, k) for k, _, _ in STAGES}
    if not base["rev"]:
        return None, None, base
    for r in rows:
        if r["partial"]:
            continue
        if r["rev"] < base["rev"] * 0.4:
            for key, name, hint in STAGES:
                if base[key] and r[key] < base[key] * 0.5:
                    return r, (key, name, hint), base
            return r, None, base
    return None, None, base


def report(db, site, days, cut_hour):
    rows, cam_daily = collect(db, site, days, cut_hour)
    print(f"\n══ {site.name} ({site.site_code}) — {days} хоногийн ХООЛОЙН ЗУРАГЛАЛ "
          f"(УБ цаг) ══")
    print(f"   {'өдөр':10}{'уншилт о/г':>14}{'цоорхой':>9}{'орсон':>7}{'гарсан':>8}"
          f"{'төлб.':>7}{'0₮':>6}{'орлого₮':>11}{'касс':>6}")
    for r in rows:
        mark = "  ← өнөөдөр (дуусаагүй)" if r["partial"] else ""
        gap = f"{r['gap']:.1f}ц" + ("⚠" if r["gap"] >= 4 else " ")
        reads = f"{r['reads_in']}/{r['reads_out']}"
        day = f"{r['d']} {WD[r['d'].weekday()]}"
        print(f"   {day:10}{reads:>14}{gap:>9}{r['ent']:>7}{r['exits']:>8}"
              f"{r['billed']:>7}{r['free']:>6}{r['rev']:>11,.0f}"
              f"{r['cashiers']:>6}{mark}")

    brk, stage, base = find_break(rows)
    total_act = sum(r["reads_in"] + r["reads_out"] + r["ent"] + r["exits"] for r in rows)
    print("\n   ── ДҮГНЭЛТ ──")
    if not total_act:
        print("   Энэ хугацаанд ЯМАР Ч хөдөлгөөн алга (уншилт ч, session ч байхгүй)")
        print("   → зогсоол идэвхгүй эсвэл огт холбогдоогүй байна")
    elif not base["rev"]:
        print("   Эхний хагасын орлого 0₮ — харьцуулах бааз алга (шинэ/үнэгүй зогсоол?)")
        print("   → --days-ыг нэмж, орлоготой байсан үеийг хамруулж дахин ажиллуул")
    elif not brk:
        print("   Орлого баазын 40%-иас доош унасан өдөр АЛГА — энэ зогсоол хэвийн.")
    else:
        pct = int(brk["rev"] * 100 // base["rev"]) if base["rev"] else 0
        print(f"   ЭВДРЭЛИЙН ӨДӨР: {brk['d']} {WD[brk['d'].weekday()]} — "
              f"орлого {brk['rev']:,.0f}₮ (хэвийн {base['rev']:,.0f}₮-ийн {pct}%)")
        if stage:
            key, name, hint = stage
            spct = int(brk[key] * 100 // base[key]) if base[key] else 0
            print(f"   ЭХЭЛЖ УНАСАН ШАТ: {name} — {brk[key]:,.0f} "
                  f"(хэвийн {base[key]:,.0f}-ийн {spct}%)")
            print(f"   → {hint}")
        else:
            print("   Дээд шатууд хэвийн хэвээр — орлого ЗӨВХӨН цуглуулалтын түвшинд")
            print("   → кассир/төлбөрийн суваг шалга (эсвэл гарц үнэгүй болсон)")
        after = [r for r in rows if r["d"] > brk["d"]]
        if after and all(r["rev"] < base["rev"] * 0.4 for r in after):
            print(f"   ⚠ Тэр өдрөөс хойш {len(after)} хоног сэргээгүй — ИДЭВХТЭЙ эвдрэл.")
        elif after:
            print(f"   Тэр өдрөөс хойш зарим өдөр сэргэсэн — тогтворгүй (тасалдаг).")

    # Камерын өдөр тутмын уншилт — аль ТУХАЙН камер унтарсныг заана
    cams = (db.query(Device).filter(Device.site_id == site.id,
                                    Device.device_type == "camera").all())
    if cams:
        dates = [r["d"] for r in rows]
        print("\n   Камер бүрийн өдөр тутмын уншилт:")
        print(f"   {'камер':20}{'чиг':7}" +
              "".join(f"{d.strftime('%m-%d'):>7}" for d in dates))
        now = utcnow()
        for c in sorted(cams, key=lambda x: (x.lane_dir or "", x.name or "")):
            counts = cam_daily.get(c.id, {})
            cells = "".join(f"{counts.get(d, 0):>7}" for d in dates)
            tag = "🔵" if c.nested_inner else ""
            state = "" if c.status == "active" else f"  [{c.status}]"
            last = (db.query(func.max(LprEvent.created_at))
                    .filter(LprEvent.device_id == c.id,
                            LprEvent.accepted.is_(True)).scalar())
            age = (f"  сүүлд {(now - last).total_seconds() / 3600:.0f}ц өмнө"
                   if last else "  ХЭЗЭЭ Ч УНШААГҮЙ")
            warn = " ⚠" if (last is None or (now - last).total_seconds() > 7200) else ""
            print(f"   {(c.name or '?')[:18]:20}{(c.lane_dir or '?'):7}{cells}"
                  f"{tag}{state}{age}{warn}")

    # Кассирын идэвх — цуглуулалт зогссон эсэхийг хүн тус бүрээр
    a, _ = day_bounds(rows[0]["d"])
    _, b = day_bounds(rows[-1]["d"])
    who = (db.query(User.username, func.max(Payment.paid_at), func.count(Payment.id))
           .join(Payment, Payment.cashier_id == User.id)
           .join(ParkingSession, ParkingSession.id == Payment.session_id)
           .filter(ParkingSession.site_id == site.id, Payment.status == "PAID",
                   Payment.paid_at >= a, Payment.paid_at < b)
           .group_by(User.username).order_by(func.max(Payment.paid_at).desc()).all())
    if who:
        print("\n   Кассир (энэ хугацаанд):")
        for uname, last, n in who[:6]:
            days_ago = (utcnow() - last).total_seconds() / 86400
            warn = "  ⚠ ЗОГССОН" if days_ago >= 1.5 else ""
            print(f"   {uname[:20]:22}{n:>6} төлбөр   сүүлд "
                  f"{(last + TZ).strftime('%m-%d %H:%M')}{warn}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", help="зогсоолын код эсвэл нэрний эхлэл")
    ap.add_argument("--all", action="store_true", help="бүх зогсоолыг дараалан")
    ap.add_argument("--days", type=int, default=8, help="хэдэн хоног (default 8)")
    args = ap.parse_args()
    if not args.site and not args.all:
        sys.exit("--site эсвэл --all өгнө үү")

    db = SessionLocal()
    try:
        cut = (utcnow() + TZ).hour
        sites = db.query(ParkingSite).filter(ParkingSite.is_active.is_(True)).all()
        if args.site:
            sites = [s for s in sites if s.site_code == args.site
                     or (s.name or "").lower().startswith(args.site.lower())]
            if not sites:
                sys.exit(f"«{args.site}» олдсонгүй")
        for s in sites:
            report(db, s, max(2, args.days), cut)
    finally:
        db.close()


if __name__ == "__main__":
    main()
