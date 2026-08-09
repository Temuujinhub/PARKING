#!/usr/bin/env python3
"""Системийг НЭВТРҮҮЛЭХ үеийн хураагдаагүй төлбөрийг цэвэрлэх.

Асуудал: NIC / Кэй Эйч / Моннис зэрэг зогсоолд системийг анх нэвтрүүлэхэд
саатал их гарч, зарим өдөр хаалт онгорхой байсан. Машинууд орж-гарч бүртгэгдэн
төлбөр БОДОГДСОН ч бодитоор ХУРААГДААГҮЙ. Улмаас Тайлан дээр «Үүссэн төлбөр»
хиймэл өндөр гарч, «Цуглуулалт» 17–35% гэж худал доогуур харагдаж байна.

Энэ хэрэгсэл нь тухайн хугацааны ТӨЛБӨР ХУРААГДААГҮЙ сешнүүдийн дүнг тэглэж
(status=FREE), холбогдох нэхэмжлэлийг цуцалж, тэдгээрээс болж автоматаар
хар жагсаалтад орсон дугаарыг чөлөөлнө. Орсон/гарсан ТООЛЛОГО хэвээр үлдэнэ —
зөвхөн мөнгөн дүн тэгширнэ, тиймээс түүх алдагдахгүй.

Хамгаалалтууд:
  • Ямар нэг төлбөр ТӨЛӨГДСӨН сешнийг ХЭЗЭЭ Ч хөндөхгүй (хэсэгчилсэн ч бай)
  • Зогсож БУЙ (OPEN) машиныг хөндөхгүй — амьд зогсолт
  • Сүүлийн --min-age-hours (default 3) цагт өөрчлөгдсөн сешнийг хөндөхгүй —
    яг одоо гарах хаалтад байгаа машин санамсаргүй тэгширэхээс сэргийлнэ
  • Сешн бүрийн ӨМНӨХ дүнг note-д бичиж үлдээнэ (аудит)

Хэрэглээ (default DRY-RUN):
    sudo /root/PARKING/backend/venv/bin/python /root/PARKING/tools/clean_test_period.py \
        --site "NIC" --site "Кэй Эйч" --site "Моннис"
    # тодорхой хугацаагаар:
        ... --from 2026-07-01 --until 2026-08-10
    # бодитоор гүйцэтгэх:
        ... --site "NIC" --site "Кэй Эйч" --site "Моннис" --apply
"""
import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta

os.chdir("/root/PARKING/backend")
sys.path.insert(0, "/root/PARKING/backend")

from app.database import SessionLocal  # noqa: E402
from app.models import (AuditLog, BlacklistEntry, Compensation,  # noqa: E402
                        ParkingSession, ParkingSite, Payment)

# Хөндөхгүй төлөв: зогсож буй машин (OPEN) — амьд зогсолт
SKIP_STATUS = {"OPEN"}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--site", action="append", default=[],
                    help="Зогсоолын нэрийн хэсэг (олон удаа өгч болно)")
    ap.add_argument("--from", dest="date_from", default=None, help="YYYY-MM-DD (орсон цагаар)")
    ap.add_argument("--until", default=None, help="YYYY-MM-DD (орсон цагаар, багтахгүй)")
    ap.add_argument("--min-age-hours", type=float, default=3.0,
                    help="Сүүлийн энэ хугацаанд хөдөлсөн сешнийг хөндөхгүй (default 3)")
    ap.add_argument("--keep-blacklist", action="store_true",
                    help="Автомат хоригийг хэвээр үлдээх")
    ap.add_argument("--apply", action="store_true", help="Бодитоор цэвэрлэх")
    args = ap.parse_args()

    if not args.site:
        print("--site заавал (ж: --site NIC --site \"Кэй Эйч\" --site Моннис)")
        sys.exit(1)

    db = SessionLocal()
    try:
        sites = {s.id: s.name for s in db.query(ParkingSite).all()}
        wanted = {sid for sid, name in sites.items()
                  if any(p.strip().lower() in (name or "").lower() for p in args.site)}
        if not wanted:
            print("Өгсөн нэрэнд тохирох зогсоол олдсонгүй. Байгаа зогсоолууд:")
            for name in sorted(sites.values()):
                print(f"  • {name}")
            sys.exit(1)
        print("Зогсоол: " + ", ".join(sorted(sites[s] for s in wanted)))

        # Төлбөр ТӨЛӨГДСӨН сешнүүд — эдгээрийг хэзээ ч хөндөхгүй
        paid_ids = {sid for (sid,) in db.query(Payment.session_id)
                    .filter(Payment.status == "PAID",
                            Payment.session_id.isnot(None)).distinct().all()}

        q = (db.query(ParkingSession)
             .filter(ParkingSession.site_id.in_(wanted),
                     ParkingSession.total_fee.isnot(None),
                     ParkingSession.total_fee > 0))
        if args.date_from:
            q = q.filter(ParkingSession.entry_time >= datetime.fromisoformat(args.date_from))
        if args.until:
            q = q.filter(ParkingSession.entry_time < datetime.fromisoformat(args.until))

        cutoff = datetime.utcnow() - timedelta(hours=args.min_age_hours)
        picked, skipped_paid, skipped_live = [], 0, 0
        for s in q.all():
            if s.id in paid_ids:
                skipped_paid += 1
                continue
            if s.status in SKIP_STATUS or (s.updated_at and s.updated_at > cutoff):
                skipped_live += 1
                continue
            picked.append(s)

        if not picked:
            print(f"Цэвэрлэх сешн олдсонгүй (төлөгдсөн {skipped_paid}, "
                  f"амьд/шинэ {skipped_live} хөндөгдөөгүй).")
            return

        # Зогсоол × сараар хураангуй — Тайлангийн «Үүссэн» баганатай тулгах боломжтой
        by_key: dict[tuple, list] = defaultdict(list)
        for s in picked:
            by_key[(sites.get(s.site_id, "?"), s.entry_time.strftime("%Y-%m"))].append(s)
        print()
        for (site_name, ym) in sorted(by_key):
            group = by_key[(site_name, ym)]
            amt = sum(float(s.total_fee or 0) for s in group)
            print(f"── {site_name} · {ym}: {len(group)} сешн · {amt:,.0f}₮ тэгширнэ")

        total_amt = sum(float(s.total_fee or 0) for s in picked)
        plates = {s.plate_number for s in picked}
        sess_ids = {s.id for s in picked}
        print(f"\nНИЙТ: {len(picked)} сешн · {total_amt:,.0f}₮ · {len(plates)} дугаар")
        print(f"Хөндөгдөхгүй: төлбөр төлсөн {skipped_paid}, амьд/сүүлийн "
              f"{args.min_age_hours:g}ц дотор {skipped_live}")

        comps = (db.query(Compensation)
                 .filter(Compensation.session_id.in_(sess_ids),
                         Compensation.status == "PENDING").all())
        comp_amt = sum(float(c.amount) for c in comps)
        print(f"Цуцлагдах нэхэмжлэл: {len(comps)} · {comp_amt:,.0f}₮")

        bl = []
        if not args.keep_blacklist and plates:
            bl = (db.query(BlacklistEntry)
                  .filter(BlacklistEntry.plate_number.in_(plates),
                          BlacklistEntry.is_active.is_(True),
                          BlacklistEntry.reason.ilike("%автомат хориг%")).all())
            print(f"Тайлагдах автомат хориг: {len(bl)}")

        if not args.apply:
            print("\nЭнэ бол DRY-RUN — юу ч өөрчлөгдөөгүй. Бодитоор хийхдээ --apply нэмнэ.")
            return

        stamp = datetime.utcnow().strftime("%Y-%m-%d")
        for s in picked:
            was = float(s.total_fee or 0)
            s.base_fee = 0
            s.vat_amount = 0
            s.total_fee = 0
            s.status = "FREE"
            mark = (f"[{stamp}] нэвтрүүлэлтийн үеийн цэвэрлэгээ — "
                    f"төлбөр хураагдаагүй, өмнөх дүн {was:,.0f}₮")
            s.note = f"{s.note}\n{mark}" if s.note else mark
        for c in comps:
            c.status = "CANCELLED"
        for b in bl:
            b.is_active = False

        db.add(AuditLog(username="system", action="TEST_PERIOD_CLEANUP", entity="session",
                        entity_id=None,
                        detail={"sessions": len(picked), "amount": round(total_amt, 2),
                                "plates": len(plates), "compensations": len(comps),
                                "compensation_amount": round(comp_amt, 2),
                                "blacklist_cleared": len(bl),
                                "sites": args.site,
                                "date_from": args.date_from, "until": args.until}))
        db.commit()
        print(f"\n✅ {len(picked)} сешн тэгширлээ ({total_amt:,.0f}₮), "
              f"{len(comps)} нэхэмжлэл цуцлагдлаа, {len(bl)} хориг тайлагдлаа.")
        print("Тайлан → Сараар/Зогсоолоор дээр «Үүссэн» буурч, «Цуглуулалт» "
              "бодит утга руугаа (100%-д ойр) хүрэх ёстой.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
