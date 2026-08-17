"""Нэг зогсоолын орлого ЯАГААД унасныг эх сурвалжаар нь ялгана.

Зогсоолын орлого унахад дөрвөн тэс өөр шалтгаан байж болно. Эдгээрийг
ялгахгүйгээр «камер эвдэрсэн» гэж дүгнэвэл буруу зам руу орно:

  1. МАШИН цөөрсөн        — бодит ертөнц (баяр, засвар, түрээслэгч гарсан)
  2. КАМЕР уншихаа больсон — орох/гарах уншилт байхгүй → төлбөр тооцогдохгүй
  3. ТАРИФ өөрчлөгдсөн     — үнэгүй болсон/хугацаа сунгасан → 0₮ гарц олширно
  4. ЦУГЛУУЛАЛТ зогссон    — гарц төлбөртэй ч хэн ч авахгүй (кассир алга)

Тухайн зогсоолын ӨНӨӨДРИЙГ лавлагаа өдөртэй (default: өмнөх ижил гараг)
тулгаж дөрвүүлэнг зэрэг харуулна.

Ажиллуулах (production сервер дээр, backend хавтаст):
    venv/bin/python tools/site_drop_diag.py --site RASH
    venv/bin/python tools/site_drop_diag.py --site EREL --ref 2026-08-10
    venv/bin/python tools/site_drop_diag.py --all       # бүх зогсоол товчоор

Зөвхөн DB УНШИНА — камер руу хандахгүй.
"""
import argparse
import os
import sys
from collections import Counter
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import func

from app.database import SessionLocal
from app.models import (Device, LprEvent, ParkingSession, ParkingSite, Payment,
                        TariffTemplate, User)

TZ = timedelta(hours=8)


def day_bounds(d: datetime.date):
    """УБ-ын өдрийн эхлэл/төгсгөлийг серверийн UTC-д хөрвүүлнэ."""
    start_local = datetime(d.year, d.month, d.day)
    return start_local - TZ, start_local + timedelta(days=1) - TZ


def window(db, site, a, b, cut_hour=None):
    """Нэг өдрийн хэмжүүрүүд. cut_hour өгвөл тэр цаг ХҮРТЭЛ (шударга харьцуулалт)."""
    if cut_hour is not None:
        b = min(b, a + timedelta(hours=cut_hour + 1))
    ent = (db.query(func.count(ParkingSession.id))
           .filter(ParkingSession.site_id == site.id,
                   ParkingSession.entry_time >= a, ParkingSession.entry_time < b).scalar())
    exits = (db.query(ParkingSession)
             .filter(ParkingSession.site_id == site.id,
                     ParkingSession.exit_time >= a, ParkingSession.exit_time < b,
                     ParkingSession.status.in_(["CLOSED", "FREE", "MANUAL_CLOSED"])).all())
    billed = sum(1 for s in exits if float(s.total_fee or 0) > 0)
    free = len(exits) - billed
    reads = (db.query(func.count(LprEvent.id))
             .filter(LprEvent.site_id == site.id, LprEvent.accepted.is_(True),
                     LprEvent.created_at >= a, LprEvent.created_at < b).scalar())
    rev = (db.query(func.coalesce(func.sum(Payment.amount), 0))
           .join(ParkingSession, ParkingSession.id == Payment.session_id)
           .filter(ParkingSession.site_id == site.id, Payment.status == "PAID",
                   Payment.paid_at >= a, Payment.paid_at < b).scalar())
    prov = Counter()
    for p, amt in (db.query(Payment.provider, Payment.amount)
                   .join(ParkingSession, ParkingSession.id == Payment.session_id)
                   .filter(ParkingSession.site_id == site.id, Payment.status == "PAID",
                           Payment.paid_at >= a, Payment.paid_at < b).all()):
        prov[p or "?"] += float(amt or 0)
    who = Counter()
    for uname, amt in (db.query(User.username, Payment.amount)
                       .join(Payment, Payment.cashier_id == User.id)
                       .join(ParkingSession, ParkingSession.id == Payment.session_id)
                       .filter(ParkingSession.site_id == site.id, Payment.status == "PAID",
                               Payment.paid_at >= a, Payment.paid_at < b).all()):
        who[uname] += float(amt or 0)
    return {"ent": ent or 0, "exits": len(exits), "billed": billed, "free": free,
            "reads": reads or 0, "rev": float(rev or 0), "prov": prov, "who": who}


def report(db, site, today, ref, cut_hour):
    a1, b1 = day_bounds(today)
    a0, b0 = day_bounds(ref)
    cur = window(db, site, a1, b1, cut_hour)
    old = window(db, site, a0, b0, cut_hour)

    def line(name, k, fmt="{:,.0f}"):
        c, o = cur[k], old[k]
        pct = f"{int(c * 100 // o)}%" if o else "—"
        return f"   {name:26}{fmt.format(o):>12}{fmt.format(c):>12}{pct:>8}"

    print(f"\n══ {site.name} ({site.site_code}) — {ref} vs {today} "
          f"(хоёулаа {cut_hour:02d}ц хүртэл) ══")
    print(f"   {'үзүүлэлт':26}{str(ref):>12}{str(today):>12}{'хувь':>8}")
    print(line("Орсон машин", "ent"))
    print(line("Хүлээн авсан уншилт", "reads"))
    print(line("Гарсан", "exits"))
    print(line("  үүнээс ТӨЛБӨРТЭЙ", "billed"))
    print(line("  үүнээс 0₮/үнэгүй", "free"))
    print(line("ОРЛОГО ₮", "rev"))

    print("\n   Шалтгааны дүгнэлт:")
    def pc(k):
        return (cur[k] * 100 // old[k]) if old[k] else 100
    if pc("ent") < 60:
        print("     → МАШИН цөөрсөн: орох урсгал өөрөө буурсан (бодит ертөнц/хаалт)")
    if pc("reads") < 60 and pc("ent") >= 60:
        print("     → КАМЕР: машин ирсээр байхад уншилт унасан (камер/стрим шалга)")
    if old["billed"] and pc("billed") < 50 and pc("exits") >= 70:
        print("     → ТАРИФ: гарц хэвийн ч ТӨЛБӨРТЭЙ гарц эрс цөөрсөн — тариф/")
        print("       үнэгүй хугацаа/гэрээт жагсаалт өөрчлөгдсөн эсэхийг шалга")
    if old["rev"] and pc("rev") < 50 and pc("billed") >= 70:
        print("     → ЦУГЛУУЛАЛТ: төлбөртэй гарц хэвийн ч мөнгө цугларахгүй —")
        print("       кассир алга эсвэл төлбөрийн суваг тасарсан")
    if pc("ent") >= 80 and pc("reads") >= 80 and pc("billed") >= 80 and pc("rev") >= 80:
        print("     → Бүх үзүүлэлт хэвийн — уналт алга")

    for label, key in (("Лавлагаа", "old"), ("Өнөөдөр", "cur")):
        w = old if key == "old" else cur
        prov = ", ".join(f"{p}={v / 1000:,.0f}к" for p, v in w["prov"].most_common()) or "—"
        who = ", ".join(f"{u}={v / 1000:,.0f}к" for u, v in w["who"].most_common(4)) or "—"
        print(f"\n   {label} хэрэгсэл: {prov}")
        print(f"   {label} кассир:   {who}")

    # Тарифын тохиргоо
    tpl = db.get(TariffTemplate, site.tariff_template_id) if site.tariff_template_id else None
    if tpl:
        tiers = ", ".join(f"{t.upto_minutes}м={float(t.price):,.0f}₮"
                          for t in sorted(tpl.tiers, key=lambda x: x.upto_minutes)[:4])
        print(f"\n   Тариф «{tpl.name}»: үнэгүй {tpl.free_minutes}м, "
              f"grace {tpl.grace_minutes}м, цаг тутам {float(tpl.extra_hour_price):,.0f}₮")
        print(f"      шатлал: {tiers or '—'}")
    else:
        print("\n   ⚠ ТАРИФ ХОЛБОГДООГҮЙ — бүх зогсолт 0₮ болно!")
    if getattr(site, "no_charge", False):
        print("   ⚠ Зогсоол «ТӨЛБӨРГҮЙ» (no_charge) горимд байна!")

    # Камерын сүүлийн уншилт
    cams = (db.query(Device).filter(Device.site_id == site.id,
                                    Device.device_type == "camera",
                                    Device.status == "active").all())
    if cams:
        now = datetime.utcnow()
        last = dict(db.query(LprEvent.device_id, func.max(LprEvent.created_at))
                    .filter(LprEvent.device_id.in_([c.id for c in cams]),
                            LprEvent.accepted.is_(True))
                    .group_by(LprEvent.device_id).all())
        print("\n   Камер (сүүлийн ХҮЛЭЭН АВСАН уншилт):")
        for c in sorted(cams, key=lambda x: (bool(x.nested_inner), x.lane_dir or "")):
            t = last.get(c.id)
            age = f"{(now - t).total_seconds() / 60:.0f} мин өмнө" if t else "ХЭЗЭЭ Ч ҮГҮЙ"
            warn = "  ⚠" if (t is None or (now - t).total_seconds() > 3600) else ""
            mark = "🔵" if c.nested_inner else "  "
            print(f"   {mark} {(c.name or '?')[:16]:18}{(c.lane_dir or '?'):6}{age:>18}{warn}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", help="зогсоолын код эсвэл нэрний эхлэл")
    ap.add_argument("--all", action="store_true", help="бүх зогсоолыг дараалан")
    ap.add_argument("--ref", help="лавлагаа өдөр YYYY-MM-DD (default: өмнөх ижил гараг)")
    args = ap.parse_args()
    if not args.site and not args.all:
        sys.exit("--site эсвэл --all өгнө үү")

    db = SessionLocal()
    try:
        now_local = datetime.utcnow() + TZ
        today = now_local.date()
        ref = (datetime.strptime(args.ref, "%Y-%m-%d").date() if args.ref
               else today - timedelta(days=7))
        cut = now_local.hour

        sites = db.query(ParkingSite).filter(ParkingSite.is_active.is_(True)).all()
        if args.site:
            sites = [s for s in sites if s.site_code == args.site
                     or (s.name or "").lower().startswith(args.site.lower())]
            if not sites:
                sys.exit(f"«{args.site}» олдсонгүй")
        for s in sites:
            report(db, s, today, ref, cut)
    finally:
        db.close()


if __name__ == "__main__":
    main()
