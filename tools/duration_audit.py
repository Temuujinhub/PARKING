#!/usr/bin/env python3
"""Тайлангийн «Хугацаа» багана яаж бүрдсэнийг задалж харуулах.

Моннис билдинг 38,471 цаг гэх мэт тоо гарч, «1000₮-өөр үржүүлбэл 38 сая» гэсэн
асуулт төрдөг. Гэвч тэр цаг нь БОДИТ зогсолт биш байж болно: машин гарсан ч
гарах камерт уншигдаагүй бол сешн нээлттэй үлдэж, авто хаалт хаах үедээ
`duration = хаасан цаг − орсон цаг` гэж бичдэг — 10 хоног гацсан бүртгэл
240 цаг нэмнэ. Тарифын дээд хязгаар (өдрийн cap) байдаг тул мөнгө нь өсдөггүй,
харин ХУГАЦААНЫ нийлбэр хөөрөгддөг.

Энэ хэрэгсэл нь нийт цагийг: (а) төлөвөөр, (б) үргэлжлэх хугацааны бүлгээр,
(в) хамгийн урт бүртгэлүүдээр задалж, аль хэсэг нь хиймэл болохыг харуулна.

    sudo /root/PARKING/backend/venv/bin/python /root/PARKING/tools/duration_audit.py --site "Моннис"
    sudo ... duration_audit.py --site "Моннис" --from 2026-07-01 --top 20
"""
import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime

os.chdir("/root/PARKING/backend")
sys.path.insert(0, "/root/PARKING/backend")

from app.database import SessionLocal  # noqa: E402
from app.models import ParkingSession, ParkingSite  # noqa: E402

# Үргэлжлэх хугацааны бүлгүүд (цагаар): бодит зогсолт vs хиймэл гацаа
BUCKETS = [(0, 1, "1ц хүртэл"), (1, 4, "1–4ц"), (4, 12, "4–12ц"),
           (12, 24, "12–24ц"), (24, 72, "1–3 хоног"), (72, 10 ** 6, "3 хоногоос дээш")]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--site", action="append", default=[], help="Зогсоолын нэрийн хэсэг")
    ap.add_argument("--from", dest="date_from", default=None, help="YYYY-MM-DD")
    ap.add_argument("--until", default=None, help="YYYY-MM-DD")
    ap.add_argument("--top", type=int, default=10, help="Хамгийн урт N бүртгэл (default 10)")
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

        q = db.query(ParkingSession).filter(ParkingSession.site_id.in_(wanted))
        if args.date_from:
            q = q.filter(ParkingSession.entry_time >= datetime.fromisoformat(args.date_from))
        if args.until:
            q = q.filter(ParkingSession.entry_time < datetime.fromisoformat(args.until))
        rows = q.all()
        if not rows:
            print("Сешн олдсонгүй.")
            return

        total_min = sum(int(s.duration_minutes or 0) for s in rows)
        print("Зогсоол: " + ", ".join(sorted(sites[s] for s in wanted)))
        print(f"Нийт {len(rows)} сешн · {total_min / 60:,.0f} цаг "
              f"(дундаж {total_min / len(rows) / 60:.1f}ц/сешн)\n")

        # (а) Төлөвөөр — FREE/MANUAL_CLOSED голдуу авто хаалтын үр дүн
        print("── Төлөвөөр ──")
        by_status = defaultdict(lambda: [0, 0])   # status → [тоо, минут]
        for s in rows:
            b = by_status[s.status]
            b[0] += 1
            b[1] += int(s.duration_minutes or 0)
        for st, (cnt, mins) in sorted(by_status.items(), key=lambda kv: -kv[1][1]):
            pct = mins / total_min * 100 if total_min else 0
            print(f"  {st:18} {cnt:>6} сешн  {mins / 60:>9,.0f}ц  ({pct:4.1f}% нийт цагийн)")

        # (б) Үргэлжлэх хугацааны бүлгээр — хаана хуримтлагдсаныг харуулна
        print("\n── Үргэлжлэх хугацаагаар ──")
        for lo, hi, label in BUCKETS:
            grp = [s for s in rows if lo * 60 <= int(s.duration_minutes or 0) < hi * 60]
            mins = sum(int(s.duration_minutes or 0) for s in grp)
            pct = mins / total_min * 100 if total_min else 0
            bar = "█" * int(pct / 2)
            print(f"  {label:16} {len(grp):>6} сешн  {mins / 60:>9,.0f}ц  "
                  f"({pct:4.1f}%) {bar}")

        # (в) Хамгийн урт бүртгэлүүд — хиймэл эсэхийг note-оос нь шууд харна
        print(f"\n── Хамгийн урт {args.top} бүртгэл ──")
        longest = sorted(rows, key=lambda s: -(s.duration_minutes or 0))[:args.top]
        for s in longest:
            hours = int(s.duration_minutes or 0) / 60
            note = (s.note or "").replace("\n", " ")[:60]
            print(f"  {s.plate_number:10} {hours:>8,.0f}ц  {s.status:16} "
                  f"{s.entry_time:%m-%d %H:%M}  {note}")

        # Дүгнэлт: 24ц+ бүртгэлийн эзлэх хувь = хиймэл хэсгийн хэмжээ
        stale = [s for s in rows if int(s.duration_minutes or 0) >= 24 * 60]
        stale_min = sum(int(s.duration_minutes or 0) for s in stale)
        real_min = total_min - stale_min
        print(f"\n── Дүгнэлт ──")
        print(f"  24ц+ бүртгэл: {len(stale)} ({len(stale) / len(rows) * 100:.1f}% сешний) "
              f"боловч {stale_min / 60:,.0f}ц буюу нийт цагийн "
              f"{stale_min / total_min * 100 if total_min else 0:.0f}%")
        print(f"  Эдгээрийг хасвал бодит зогсолт: {real_min / 60:,.0f}ц, "
              f"дундаж {real_min / max(1, len(rows) - len(stale)) / 60:.1f}ц/сешн")
        print("\n  Урт бүртгэл нь ихэвчлэн машин гарсан ч гарах камерт уншигдаагүй,")
        print("  авто хаалт хаах үедээ бүх хугацааг бичсэний үр дүн. Мөнгөнд")
        print("  нөлөөлөхгүй (өдрийн дээд хязгаартай) ч ХУГАЦААНЫ нийлбэрийг хөөрөгдөнө.")
        print("  Цэвэрлэх: Тохиргоо → Авто цэвэрлэгээ (босгыг богиносгох) эсвэл")
        print("  Шалгах → Аудит горим.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
