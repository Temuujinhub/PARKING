"""Backfill-ээр орсон машины event СЕРВЭРТ ИРСЭН үү — алдагдлыг ЛОКАЛЧИЛНА.

Асуудал: зогсолтын ~40% нь амьд биш, camera_sync-ийн backfill-ээр ордог. Гэхдээ
ЯАГААД амьд ороогүй нь хоёр тэс өөр шалтгаантай:

  A. Event СЕРВЭРТ ОГТ ИРЭЭГҮЙ (камер логтоо бичсэн ч eventManager стримээр
     илгээгээгүй) → камер/сүлжээ/холболтын асуудал.
  B. Event ИРСЭН ч session амьд үүсээгүй:
     • дугаар танигдаагүй/итгэлцүүр багатай (rejected) → танилт (голог)
     • эсвэл session өөр дугаараар үүсээд camera_sync давхардуулсан → OCR

Ялгах түлхүүр: АМЬД session үүсэхэд cgi_poller `lpr_events`-д мөр бичдэг, харин
camera_sync backfill БИЧДЭГГҮЙ. Тиймээс backfill session-ий дугаарт entry_time-ийн
орчимд `lpr_event` байгаа эсэхийг шалгавал event ирсэн эсэх тодорхой болно.

Ажиллуулах (production сервер дээр, backend хавтаст):
    venv/bin/python tools/backfill_source_diag.py --hours 12
    venv/bin/python tools/backfill_source_diag.py --since "2026-08-17 01:00"
    venv/bin/python tools/backfill_source_diag.py --hours 12 --site EREL

Зөвхөн DB УНШИНА — камер руу хандахгүй.
"""
import argparse
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import func, or_

from app.database import SessionLocal
from app.models import AuditLog, Device, LprEvent, ParkingSession, ParkingSite

TZ = timedelta(hours=8)
MATCH_MIN = 5   # entry_time-ийн орчим ±энэ минутад lpr_event хайна


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=12)
    ap.add_argument("--since", metavar="'YYYY-MM-DD HH:MM'", help="УБ цагаас хойш")
    ap.add_argument("--site", help="зөвхөн нэг зогсоол (код эсвэл нэрний эхлэл)")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        if args.since:
            since = datetime.strptime(args.since, "%Y-%m-%d %H:%M") - TZ
        else:
            since = datetime.utcnow() - timedelta(hours=args.hours)

        # ── Backfill-ээр үүссэн session-ууд ─────────────────────────────────
        q = db.query(ParkingSession).filter(ParkingSession.entry_time >= since)
        site = None
        if args.site:
            site = (db.query(ParkingSite).filter(ParkingSite.site_code == args.site).first()
                    or db.query(ParkingSite)
                    .filter(ParkingSite.name.ilike(f"{args.site}%")).first())
            if not site:
                sys.exit(f"«{args.site}» олдсонгүй")
            q = q.filter(ParkingSession.site_id == site.id)
        sess = q.all()
        if not sess:
            print("Session олдсонгүй.")
            return

        synced = {eid for (eid,) in db.query(AuditLog.entity_id)
                  .filter(AuditLog.entity == "session", AuditLog.action == "CAMERA_SYNC",
                          AuditLog.created_at >= since).all()}
        backfilled = [s for s in sess
                      if s.id in synced or "логоос нөхөж" in (s.note or "")]
        live = len(sess) - len(backfilled)

        print(f"══ Backfill-ийн эх сурвалж — {len(sess)} session "
              f"({len(sess) - len(backfilled)} амьд, {len(backfilled)} backfill) ══\n")
        if not backfilled:
            print("Backfill session алга — бүгд амьд орсон. ✅")
            return

        # ── Backfill session бүрд entry орчмын lpr_event байна уу ───────────
        plates = {s.plate_number for s in backfilled}
        # Бүх дугаарын lpr_event-ийг цонхонд нэг query-ээр татаад санах ойд тулгана
        floor = min(s.entry_time for s in backfilled) - timedelta(minutes=MATCH_MIN)
        ceil = max(s.entry_time for s in backfilled) + timedelta(minutes=MATCH_MIN)
        ev_by_plate: dict = defaultdict(list)
        for plate, at, acc, reason in (
                db.query(LprEvent.plate_number, LprEvent.created_at,
                         LprEvent.accepted, LprEvent.reject_reason)
                .filter(LprEvent.plate_number.in_(plates),
                        LprEvent.created_at >= floor, LprEvent.created_at <= ceil).all()):
            ev_by_plate[plate].append((at, acc, reason))

        buckets = Counter()
        reasons = Counter()
        per_site: dict = defaultdict(lambda: [0, 0, 0, 0])  # [нийт, ирээгүй, гологдсон, зөрчил]
        site_name = {s.id: s.name for s in db.query(ParkingSite).all()}
        for s in backfilled:
            evs = [(at, acc, r) for at, acc, r in ev_by_plate.get(s.plate_number, [])
                   if abs((at - s.entry_time).total_seconds()) <= MATCH_MIN * 60]
            row = per_site[site_name.get(s.site_id, "?")]
            row[0] += 1
            if not evs:
                buckets["A. Event СЕРВЭРТ ОГТ ИРЭЭГҮЙ (камер/сүлжээ)"] += 1
                row[1] += 1
            elif any(acc for _at, acc, _r in evs):
                buckets["C. Event ИРСЭН + хүлээн авсан (OCR давхардал?)"] += 1
                row[3] += 1
            else:
                buckets["B. Event ИРСЭН ч ГОЛОГДСОН (танилт/итгэлцүүр)"] += 1
                row[2] += 1
                for _at, _acc, r in evs:
                    reasons[r or "?"] += 1

        print("Backfill-ийн ЖИНХЭНЭ шалтгаан:")
        for k, n in buckets.most_common():
            print(f"   {n:5}  {k}  ({n * 100 // len(backfilled)}%)")

        if reasons:
            print("\n   B-гийн гологдсон шалтгаан:")
            for r, n in reasons.most_common(5):
                print(f"      {n:5}  {r}")

        a = buckets.get("A. Event СЕРВЭРТ ОГТ ИРЭЭГҮЙ (камер/сүлжээ)", 0)
        print("\n   ДҮГНЭЛТ:")
        if a > len(backfilled) * 0.5:
            print("     Дийлэнх нь СЕРВЭРТ ИРЭЭГҮЙ → камер eventManager стримээр event")
            print("     илгээхгүй байна (холболт/нөөц/өөр систем). cgi_poller-ийн")
            print("     боловсруулалт БИШ — стрим дамжуулалт.")
        else:
            print("     Дийлэнх нь СЕРВЭРТ ИРСЭН → cgi_poller хүлээж авсан ч session")
            print("     амьд үүсээгүй (танилт эсвэл OCR давхардал). Боловсруулалтын тал.")

        print(f"\n   Зогсоол тутам (backfill: ирээгүй / гологдсон / зөрчил):")
        for name in sorted(per_site, key=lambda n: -per_site[n][0]):
            tot, no_ev, rej, conf = per_site[name]
            print(f"   {name[:20]:22} backfill {tot:4}  ·  ирээгүй {no_ev:4}  "
                  f"гологдсон {rej:4}  зөрчил {conf:4}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
