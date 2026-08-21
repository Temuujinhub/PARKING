#!/usr/bin/env python3
"""camera_sync-ийн ЦАГИЙН ЗӨРҮҮГ бүх түүхээр задлах аудит.

Хариулах асуултууд:
  1. Давхардал ХЭЗЭЭНЭЭС эхэлсэн бэ (сараар/өдрөөр)?
  2. Зөвхөн 8 цагийн зөрүү юу, өөр зөрүү бий юу? → ЗӨРҮҮНИЙ ГИСТОГРАМ
     (энэ нь «өөр алдаа гаргасан уу» гэдгийг ШАЛГАХ АРГА: хэрэв зөвхөн
      8 цагийн бөөгнөрөл харагдвал бусад зөрүү байхгүй гэсэн үг)
  3. Санхүүд хэдэн төгрөгийн хиймэл бодолт орсон бэ?
  4. log_tail хэдэн уншилт оруулсан бэ (зогсоол/өдрөөр)?

«ХОС» гэж юуг үзэх вэ: нөхөлтөөр үүссэн бүртгэлтэй ЯГ ИЖИЛ дугаартай,
ЯГ ИЖИЛ зогсоол дээрх АМЬД (нөхөлт биш) бүртгэл. Амьд бүртгэл нь камерын
шууд урсгалаас үүсдэг тул бодит машиныг төлөөлнө. Зөрүүг
`нөхөлт.entry_time − амьд.entry_time` гэж тооцно.

    sudo /root/PARKING/backend/venv/bin/python /root/PARKING/tools/camsync_offset_audit.py
    sudo ... camsync_offset_audit.py --from 2026-07-01
    sudo ... camsync_offset_audit.py --from 2026-07-01 --site "Хангарьд"
"""
import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta

os.chdir("/root/PARKING/backend")
sys.path.insert(0, "/root/PARKING/backend")

from sqlalchemy import func, or_  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import (LprEvent, ParkingSession, ParkingSite,  # noqa: E402
                        Payment)

SYNC_NOTE = "авто sync"
PAIR_WINDOW_H = 26          # хос хайх дээд муж (±)
BUCKET_MIN = 15             # гистограмын алхам


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="dfrom", default="2026-07-01")
    ap.add_argument("--to", dest="dto", default=None)
    ap.add_argument("--site", action="append")
    ap.add_argument("--lpr-day", default=None, help="log_tail тоог энэ өдрөөр (YYYY-MM-DD)")
    a = ap.parse_args()

    start = datetime.fromisoformat(a.dfrom)
    end = datetime.fromisoformat(a.dto) if a.dto else datetime.utcnow() + timedelta(days=1)
    db = SessionLocal()
    try:
        sites = db.query(ParkingSite).all()
        if a.site:
            sites = [s for s in sites
                     if any(x.lower() in (s.name or "").lower() for x in a.site)]

        by_month = defaultdict(lambda: {"sync": 0, "paired": 0, "live": 0})
        hist = defaultdict(int)
        per_site = defaultdict(lambda: {"sync": 0, "paired": 0, "fee": 0.0,
                                        "unpaid_fee": 0.0, "paid": 0, "first": None})
        first_sync = None

        for site in sites:
            rows = (db.query(ParkingSession.id, ParkingSession.plate_number,
                             ParkingSession.entry_time, ParkingSession.note,
                             ParkingSession.total_fee, ParkingSession.paid_at)
                    .filter(ParkingSession.site_id == site.id,
                            ParkingSession.entry_time >= start,
                            ParkingSession.entry_time < end)
                    .all())
            live_by_plate = defaultdict(list)
            sync_rows = []
            for r in rows:
                if SYNC_NOTE in (r.note or ""):
                    sync_rows.append(r)
                else:
                    live_by_plate[r.plate_number].append(r.entry_time)
            for lst in live_by_plate.values():
                lst.sort()

            paid_ids = {x[0] for x in db.query(Payment.session_id)
                        .filter(Payment.session_id.in_([r.id for r in sync_rows]),
                                Payment.status == "PAID").all()} if sync_rows else set()

            for r in sync_rows:
                m = r.entry_time.strftime("%Y-%m")
                by_month[m]["sync"] += 1
                st = per_site[site.name]
                st["sync"] += 1
                fee = float(r.total_fee or 0)
                st["fee"] += fee
                if r.id in paid_ids or r.paid_at:
                    st["paid"] += 1
                else:
                    st["unpaid_fee"] += fee
                if st["first"] is None or r.entry_time < st["first"]:
                    st["first"] = r.entry_time
                if first_sync is None or r.entry_time < first_sync:
                    first_sync = r.entry_time
                # ХАМГИЙН ОЙРЫН амьд хос
                cand = live_by_plate.get(r.plate_number) or []
                best = None
                for lt in cand:
                    d = (r.entry_time - lt).total_seconds()
                    if abs(d) <= PAIR_WINDOW_H * 3600:
                        if best is None or abs(d) < abs(best):
                            best = d
                if best is None:
                    hist["хосгүй"] += 1
                    continue
                by_month[m]["paired"] += 1
                st["paired"] += 1
                hist[int(round(best / 60 / BUCKET_MIN)) * BUCKET_MIN] += 1

            for r in rows:
                if SYNC_NOTE not in (r.note or ""):
                    by_month[r.entry_time.strftime("%Y-%m")]["live"] += 1

        # ── 1. Хугацааны хамрах хүрээ ───────────────────────────────────
        print("═" * 78)
        print(f"ХАМРАХ ХҮРЭЭ: {a.dfrom} → {end:%Y-%m-%d}   "
              f"хамгийн ЭРТ нөхөлт: {first_sync or '—'}")
        print("═" * 78)
        print(f"\n{'САР':<9} {'амьд':>8} {'нөхөлт':>8} {'хос олдсон':>11} {'нөхөлтийн %':>12}")
        print("─" * 52)
        for m in sorted(by_month):
            v = by_month[m]
            tot = v["live"] + v["sync"]
            print(f"{m:<9} {v['live']:>8} {v['sync']:>8} {v['paired']:>11} "
                  f"{(100*v['sync']/tot if tot else 0):>11.0f}%")

        # ── 2. ЗӨРҮҮНИЙ ГИСТОГРАМ ───────────────────────────────────────
        print(f"\n{'='*78}\nЗӨРҮҮНИЙ ГИСТОГРАМ (нөхөлт − амьд, минутаар)")
        print("Энэ нь «8 цагаас өөр алдаа бий юу» гэдгийг шалгах АРГА.\n")
        rows_h = sorted((k for k in hist if k != "хосгүй"), key=lambda x: x)
        mx = max((hist[k] for k in rows_h), default=1)
        for k in rows_h:
            n = hist[k]
            if n < 2:
                continue
            bar = "█" * max(1, int(40 * n / mx))
            print(f"  {k/60:>+7.2f}ц {n:>6} {bar}")
        print(f"  {'хосгүй':>9} {hist['хосгүй']:>6}")

        # ── 3. Санхүү ────────────────────────────────────────────────────
        print(f"\n{'='*78}\n{'ЗОГСООЛ':<20} {'нөхөлт':>7} {'хос':>6} "
              f"{'бодогдсон₮':>12} {'ТӨЛӨГДӨӨГҮЙ₮':>14} {'төлсөн':>7} {'эхэлсэн':>12}")
        print("─" * 82)
        t = defaultdict(float)
        for name, v in sorted(per_site.items(), key=lambda kv: -kv[1]["sync"]):
            if not v["sync"]:
                continue
            print(f"{name[:20]:<20} {v['sync']:>7} {v['paired']:>6} {v['fee']:>12,.0f} "
                  f"{v['unpaid_fee']:>14,.0f} {v['paid']:>7} {v['first']:%m-%d %H:%M}")
            for k in ("sync", "paired", "fee", "unpaid_fee", "paid"):
                t[k] += v[k]
        print("─" * 82)
        print(f"{'НИЙТ':<20} {int(t['sync']):>7} {int(t['paired']):>6} {t['fee']:>12,.0f} "
              f"{t['unpaid_fee']:>14,.0f} {int(t['paid']):>7}")

        # ── 4. log_tail-ийн оруулсан уншилт ─────────────────────────────
        day = a.lpr_day
        inj = LprEvent.raw["log_tail"].as_string()
        q = (db.query(ParkingSite.name, func.date(LprEvent.created_at), func.count())
             .join(ParkingSite, LprEvent.site_id == ParkingSite.id)
             .filter(inj == "true", LprEvent.created_at >= start)
             .group_by(ParkingSite.name, func.date(LprEvent.created_at)))
        if day:
            q = q.filter(func.date(LprEvent.created_at) == day)
        rows_l = q.all()
        print(f"\n{'='*78}\nlog_tail-ЭЭР ОРУУЛСАН УНШИЛТ"
              + (f" ({day})" if day else " (өдрөөр)"))
        if not rows_l:
            print("  (алга)")
        else:
            per = defaultdict(dict)
            for nm, d, c in rows_l:
                per[nm][str(d)] = c
            days = sorted({d for v in per.values() for d in v})[-8:]
            print(f"  {'ЗОГСООЛ':<20}" + "".join(f"{d[5:]:>8}" for d in days) + f"{'НИЙТ':>8}")
            for nm in sorted(per, key=lambda n: -sum(per[n].values())):
                line = "".join(f"{per[nm].get(d, 0):>8}" for d in days)
                print(f"  {nm[:20]:<20}{line}{sum(per[nm].values()):>8}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
