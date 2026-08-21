#!/usr/bin/env python3
"""camera_sync-ийн 8 ЦАГИЙН ШИЛЖИЛТЭЭР үүссэн ДАВХАР бүртгэлийг цэвэрлэх.

ШАЛТГААН (2026-08-22, commit db0210b-д зассан): Dahua RecordFinder-ийн `Time`
талбар нь төхөөрөмжийн ЛОКАЛ цагийн epoch боловч манай код түүнийг жинхэнэ UTC
гэж уншдаг байв. Улмаар `camera_sync` нөхөж үүсгэсэн бүртгэлийн `entry_time`
бодит цагаас 8 цагаар ХОЙШ бичигдэж, camsync-ийн давхардлын шалгалтууд
(LprEvent ±90с, entry_time ±1ц) 8 цагийн зайнаас хайдаг болсноор давхардлыг
барихаа больсон.

Үр дүн (08-18-аас, 10 зогсоол): нөхөлтөөр үүссэн 1,949 бүртгэлийн 1,347 (69%)
нь амьд бүртгэлийн ЯГ −8 цагийн хуулбар байв. Тэдгээр нь мөнгө хураадаггүй
(1,949-өөс ердөө 2 нь төлөгдсөн) мөртөө БОДОГДСОН дүнгийн 31%-ийг эзэлдэг тул
«цуглуулалтын хувь» хиймлээр доогуур харагдана.

ЮУ ХИЙДЭГ:
  • нээлттэй хуулбар  → ҮНЭГҮЙ хаана (төлбөр 0, өр үүсгэхгүй)
  • хаагдсан хуулбар  → бодогдсон төлбөрийг 0 болгож, хугацааг арилгана
  • аль алинд нь тэмдэглэгээ үлдээж, AuditLog бичнэ

ХАМГААЛАЛТ:
  • ТӨЛӨГДСӨН бүртгэлд ХЭЗЭЭ Ч хүрэхгүй (paid_at эсвэл Payment мөр байвал алгасна)
  • зөвхөн «авто sync» тэмдэглэгээтэй бүртгэл
  • амьд хосыг нь ЗААВАЛ олсон байх ёстой (яг тэр дугаар, тэр зогсоол,
    entry_time нь ЯГ −offset ± tolerance)
  • аль хэдийн цэвэрлэсэн бүртгэлийг дахин хөндөхгүй (идемпотент)

    sudo /root/PARKING/backend/venv/bin/python /root/PARKING/tools/clean_camsync_dup.py
    sudo ... clean_camsync_dup.py --days 30 --apply
    sudo ... clean_camsync_dup.py --site "Хангарьд" --apply
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
from app.models import (AuditLog, ParkingSession, ParkingSite,  # noqa: E402
                        Payment)

SYNC_NOTE = "авто sync"
DONE_MARK = "camsync-давхардал цэвэрлэв"
ACTIVE = ("OPEN", "AWAITING_PAYMENT", "PAID")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30, help="хэдэн хоног ухрах")
    ap.add_argument("--site", action="append", help="зөвхөн энэ зогсоол (хэсэгчилсэн нэр)")
    ap.add_argument("--tolerance-min", type=int, default=15,
                    help="амьд хостой тааруулах цагийн тэвчээр (мин)")
    ap.add_argument("--apply", action="store_true", help="бодитоор бичнэ (default: dry-run)")
    a = ap.parse_args()

    off = timedelta(hours=int(settings.camera_tz_offset_hours))
    tol = timedelta(minutes=a.tolerance_min)
    since = datetime.utcnow() - timedelta(days=a.days)

    db = SessionLocal()
    try:
        sites = db.query(ParkingSite).all()
        if a.site:
            sites = [s for s in sites
                     if any(x.lower() in (s.name or "").lower() for x in a.site)]
        stats = defaultdict(lambda: {"dup": 0, "open": 0, "closed": 0,
                                     "fee": 0.0, "paid_skip": 0, "nomatch": 0})
        touched = []

        for site in sites:
            rows = (db.query(ParkingSession)
                    .filter(ParkingSession.site_id == site.id,
                            ParkingSession.entry_time >= since)
                    .all())
            sync = [s for s in rows
                    if SYNC_NOTE in (s.note or "") and DONE_MARK not in (s.note or "")]
            if not sync:
                continue
            live = [s for s in rows if SYNC_NOTE not in (s.note or "")]
            by_plate = defaultdict(list)
            for s in live:
                by_plate[s.plate_number].append(s)

            for s in sync:
                target = s.entry_time - off
                pair = next((l for l in by_plate.get(s.plate_number, [])
                             if abs(l.entry_time - target) <= tol), None)
                if pair is None:
                    stats[site.name]["nomatch"] += 1
                    continue
                # ТӨЛӨГДСӨНД хүрэхгүй
                if s.paid_at or db.query(Payment.id).filter(
                        Payment.session_id == s.id,
                        Payment.status == "PAID").first():
                    stats[site.name]["paid_skip"] += 1
                    continue

                st = stats[site.name]
                st["dup"] += 1
                st["fee"] += float(s.total_fee or 0)
                was_open = s.status in ACTIVE
                st["open" if was_open else "closed"] += 1
                touched.append((site.name, s.plate_number, s.entry_time,
                                pair.entry_time, s.status, float(s.total_fee or 0)))
                if not a.apply:
                    continue
                if was_open:
                    s.status = "FREE"
                    s.exit_time = s.exit_time or datetime.utcnow()
                    s.exit_confirmed = False
                s.base_fee = s.vat_amount = s.total_fee = 0
                s.duration_minutes = None
                s.note = f"{s.note} | {DONE_MARK} (амьд хос: {pair.id[:8]})"[:1000]

        # ── Тайлан ────────────────────────────────────────────────────────
        print(f"{'ЗОГСООЛ':<20} {'ДАВХАР':>7} {'нээлттэй':>9} {'хаагдсан':>9} "
              f"{'бодогдсон₮':>12} {'төлсөн→алгас':>13} {'хосгүй':>7}")
        print("─" * 82)
        tot = defaultdict(float)
        for name, v in sorted(stats.items(), key=lambda kv: -kv[1]["dup"]):
            if not (v["dup"] or v["nomatch"]):
                continue
            print(f"{name[:20]:<20} {v['dup']:>7} {v['open']:>9} {v['closed']:>9} "
                  f"{v['fee']:>12,.0f} {v['paid_skip']:>13} {v['nomatch']:>7}")
            for k in ("dup", "open", "closed", "fee", "paid_skip", "nomatch"):
                tot[k] += v[k]
        print("─" * 82)
        print(f"{'НИЙТ':<20} {int(tot['dup']):>7} {int(tot['open']):>9} "
              f"{int(tot['closed']):>9} {tot['fee']:>12,.0f} "
              f"{int(tot['paid_skip']):>13} {int(tot['nomatch']):>7}")

        if touched[:8]:
            print("\nЖишээ (хамгийн эхний 8):")
            for n, p, se, le, stt, fee in touched[:8]:
                print(f"  {n[:14]:<14} {p:<9} sync {se:%m-%d %H:%M} ↔ амьд {le:%m-%d %H:%M} "
                      f"| {stt:<14} {fee:>8,.0f}₮")

        if a.apply and tot["dup"]:
            db.add(AuditLog(username="tools/clean_camsync_dup.py", action="CAMSYNC_DUP_CLEAN",
                            entity="session", entity_id="-",
                            detail={"count": int(tot["dup"]), "opened": int(tot["open"]),
                                    "closed": int(tot["closed"]), "fee_zeroed": tot["fee"],
                                    "offset_hours": int(settings.camera_tz_offset_hours),
                                    "days": a.days}))
            db.commit()
            print(f"\n✓ {int(tot['dup'])} давхар бүртгэл цэвэрлэгдлээ "
                  f"({tot['fee']:,.0f}₮ хиймэл бодолт арилав)")
        elif tot["dup"]:
            print(f"\n(dry-run — юу ч бичээгүй. Бодитоор хийхдээ --apply нэмнэ үү)")
        else:
            print("\nЦэвэрлэх давхардал олдсонгүй.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
