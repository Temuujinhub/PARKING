#!/usr/bin/env python3
"""Камерын логоос НӨХӨЖ бүртгэсэн сешн/өрийг буцаах (эрсдэлтэйг нь сонгож).

Асуудал: 48 цагийн камерын логоор нөхөж бүртгэхэд тэр мужид АЛЬ ХЭДИЙН
шийдэгдсэн машинууд («өмнө нь өр үүсгээд цуцалсан», «өчигдөр төлөгдсөн»,
«тест цэвэрлэгээгээр устгасан») дахин «системд байхгүй» гэж танигдаж
ДАХИН өр болсон байж болзошгүй. Ийм өр нь давхардал — үйлчлүүлэгчээс
хоёр удаа нэхэх эрсдэлтэй.

Энэ хэрэгсэл нь CAMERA_BACKFILL-ээр үүссэн бүртгэлүүдийг олж, сонгосон
хугацаанаас ӨМНӨХИЙГ нь бүрэн буцаана: сешнийг устгаж, өрийг цуцална.
(Эдгээр сешн нь бидний зохиомлоор үүсгэсэн бөгөөд төлбөр хийгдээгүй тул
устгахад бодит түүх алдагдахгүй. Төлбөртэй бол ХЭЗЭЭ Ч хөндөхгүй.)

    # юу буцаахыг харах (өнөөдрөөс өмнөх бүгд):
    sudo /root/PARKING/backend/venv/bin/python /root/PARKING/tools/undo_camera_backfill.py
    # өнөөдрийнхийг үлдээж бусдыг буцаах:
    sudo ... undo_camera_backfill.py --before 2026-08-10 --apply
    # бүгдийг буцаах:
    sudo ... undo_camera_backfill.py --all --apply
"""
import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta

os.chdir("/root/PARKING/backend")
sys.path.insert(0, "/root/PARKING/backend")

from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import (AuditLog, Compensation, ParkingSession,  # noqa: E402
                        ParkingSite, Payment)

MARK = "камерын логоос нөхөж"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--site", action="append", default=[], help="Зогсоолын нэрийн хэсэг")
    ap.add_argument("--before", default=None,
                    help="Энэ огнооноос ӨМНӨ ОРСОН бүртгэлийг буцаана (YYYY-MM-DD). "
                         "Өгөхгүй бол ӨНӨӨДРИЙН эхлэл (локал) автоматаар.")
    ap.add_argument("--all", action="store_true", help="Хугацаа үл харгалзан бүгдийг")
    ap.add_argument("--apply", action="store_true", help="Бодитоор буцаах")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        sites = {s.id: s.name for s in db.query(ParkingSite).all()}
        wanted = set(sites)
        if args.site:
            wanted = {sid for sid, name in sites.items()
                      if any(p.strip().lower() in (name or "").lower() for p in args.site)}
            if not wanted:
                print("Зогсоол олдсонгүй. Байгаа:", ", ".join(sorted(sites.values())))
                sys.exit(1)

        q = (db.query(ParkingSession)
             .filter(ParkingSession.site_id.in_(wanted),
                     ParkingSession.note.ilike(f"%{MARK}%")))
        cutoff = None
        if not args.all:
            if args.before:
                cutoff = datetime.fromisoformat(args.before)
            else:
                # Өнөөдрийн локал 00:00-г UTC болгож — өнөөдрийнхийг ҮЛДЭЭНЭ
                tz = timedelta(hours=settings.tz_offset_hours)
                cutoff = ((datetime.utcnow() + tz).replace(hour=0, minute=0, second=0,
                                                           microsecond=0) - tz)
            q = q.filter(ParkingSession.entry_time < cutoff)
            print(f"Хамрах хүрээ: {cutoff:%Y-%m-%d %H:%M} UTC-ээс ӨМНӨ орсон бүртгэлүүд")
        else:
            print("Хамрах хүрээ: БҮГД (хугацаа үл харгалзан)")

        rows = q.all()
        if not rows:
            print("Буцаах бүртгэл олдсонгүй.")
            return

        ids = [s.id for s in rows]
        paid_ids = {sid for (sid,) in db.query(Payment.session_id)
                    .filter(Payment.session_id.in_(ids),
                            Payment.status == "PAID").distinct().all()}
        comps = (db.query(Compensation)
                 .filter(Compensation.session_id.in_(ids)).all())
        comp_by_sess = defaultdict(list)
        for c in comps:
            comp_by_sess[c.session_id].append(c)

        undo, protected = [], []
        for s in rows:
            if s.id in paid_ids or any(c.status == "PAID" for c in comp_by_sess.get(s.id, [])):
                protected.append(s)     # мөнгө орсон — хэзээ ч хөндөхгүй
            else:
                undo.append(s)

        by_site = defaultdict(lambda: [0, 0.0])
        for s in undo:
            b = by_site[sites.get(s.site_id, "?")]
            b[0] += 1
            b[1] += sum(float(c.amount) for c in comp_by_sess.get(s.id, [])
                        if c.status == "PENDING")
        print("\n── Буцаах бүртгэлүүд ──")
        for name in sorted(by_site):
            cnt, amt = by_site[name]
            print(f"  {name:22} {cnt:>5} сешн · өр {amt:>12,.0f}₮")
        debt_total = sum(a for _c, a in by_site.values())
        print(f"\nНИЙТ: {len(undo)} сешн устгана · {debt_total:,.0f}₮ өр цуцлагдана")
        if protected:
            print(f"ХАМГААЛАГДСАН: {len(protected)} сешн (төлбөр хийгдсэн — хөндөхгүй)")

        print("\n  Жишээ (эхний 8):")
        for s in sorted(undo, key=lambda x: x.entry_time)[:8]:
            due = sum(float(c.amount) for c in comp_by_sess.get(s.id, [])
                      if c.status == "PENDING")
            print(f"    {s.plate_number:10} орсон {s.entry_time:%m-%d %H:%M}  "
                  f"{s.status:16} өр {due:>8,.0f}₮")

        if not args.apply:
            print("\nЭнэ бол DRY-RUN — юу ч өөрчлөгдөөгүй. Бодитоор хийхдээ --apply нэмнэ.")
            return

        for s in undo:
            for c in comp_by_sess.get(s.id, []):
                if c.status == "PENDING":
                    c.status = "CANCELLED"
                c.session_id = None      # FK-г салгаж сешнийг устгах боломжтой болгоно
            db.delete(s)
        db.add(AuditLog(username="system", action="CAMERA_BACKFILL_UNDO", entity="session",
                        entity_id=None,
                        detail={"sessions": len(undo), "debt": round(debt_total, 2),
                                "protected": len(protected),
                                "cutoff": cutoff.isoformat() if cutoff else "all",
                                "sites": args.site or "бүгд"}))
        db.commit()
        print(f"\n✅ {len(undo)} бүртгэл устгаж, {debt_total:,.0f}₮ өр цуцаллаа.")
        print("Дараагийн нөхөлт нь watermark-тай (давхардахгүй) автомат sync-ээр хийгдэнэ.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
