"""Бүх зогсоолын эрүүл мэндийн нэг хуудас — аль нь хэвийн, аль нь биш.

Гурван зүйлийг зогсоол тутамд ЗЭРЭГ харуулна:
  1. Зогсолт АМЬДААР бүртгэгдсэн үү, эсвэл камерын логоос НӨХӨГДСӨН үү
     (нөхөгдсөн = тэр агшинд хаалт нээгдээгүй, төлбөр нэхэгдээгүй)
  2. LPR уншилт хүлээн авсан / гологдсон
  3. ЗУРГИЙН хувь — өдөр ба цагийн мужаар (шөнө/өглөө/үдээс хойш/орой)

Ажиллуулах (production сервер дээр, backend хавтаст):
    venv/bin/python tools/site_health.py --days 3
    venv/bin/python tools/site_health.py --days 3 --site RASH

Зөвхөн DB УНШИНА — камер руу огт хандахгүй тул аль ч үед аюулгүй.
"""
import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import func

from app.database import SessionLocal
from app.models import AuditLog, Device, LprEvent, ParkingSession, ParkingSite

TZ = timedelta(hours=8)  # УБ-ын цаг

# Цагийн муж — цаг тутмаар харахад хэт нарийн, өдрөөр харахад хэт бүдүүн
BUCKETS = [("шөнө 00-06", 0, 6), ("өглөө 06-12", 6, 12),
           ("үдээс хойш 12-18", 12, 18), ("орой 18-24", 18, 24)]


def bucket_of(dt: datetime) -> str:
    h = (dt + TZ).hour
    for name, a, b in BUCKETS:
        if a <= h < b:
            return name
    return BUCKETS[-1][0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--site", help="зөвхөн нэг зогсоол (код эсвэл нэрний эхлэл)")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        since = datetime.utcnow() - timedelta(days=args.days)
        sites = db.query(ParkingSite).filter(ParkingSite.is_active.is_(True)).all()
        if args.site:
            sites = [s for s in sites if s.site_code == args.site
                     or (s.name or "").lower().startswith(args.site.lower())]
            if not sites:
                sys.exit(f"«{args.site}» олдсонгүй")

        synced = {eid for (eid,) in db.query(AuditLog.entity_id)
                  .filter(AuditLog.entity == "session", AuditLog.action == "CAMERA_SYNC",
                          AuditLog.created_at >= since).all()}

        print(f"══ Зогсоолын байдал — сүүлийн {args.days} хоног ══\n")
        print(f"{'зогсоол':22}{'зогсолт':>9}{'амьд':>7}{'нөхсөн':>8}{'нөхөлт%':>9}"
              f"{'уншилт':>8}{'голог%':>8}{'зурагтай%':>11}")
        rows = []
        for site in sites:
            sess = (db.query(ParkingSession)
                    .filter(ParkingSession.site_id == site.id,
                            ParkingSession.entry_time >= since).all())
            if not sess:
                continue
            back = sum(1 for s in sess
                       if s.id in synced or "логоос нөхөж" in (s.note or ""))
            live = len(sess) - back
            ok = bad = 0
            for accepted, n in (db.query(LprEvent.accepted, func.count())
                                .filter(LprEvent.site_id == site.id,
                                        LprEvent.created_at >= since)
                                .group_by(LprEvent.accepted).all()):
                if accepted:
                    ok += n
                else:
                    bad += n
            withpic = sum(1 for s in sess if s.entry_snapshot or s.exit_snapshot)
            rows.append((site, sess, live, back, ok, bad, withpic))
            print(f"{(site.name or '?')[:20]:22}{len(sess):9}{live:7}{back:8}"
                  f"{back * 100 // len(sess):8}%{ok + bad:8}"
                  f"{(bad * 100 // (ok + bad) if ok + bad else 0):7}%"
                  f"{withpic * 100 // len(sess):10}%")

        if not rows:
            print("   Тухайн хугацаанд зогсолт олдсонгүй.")
            return
        tot = sum(len(r[1]) for r in rows)
        tb = sum(r[3] for r in rows)
        print(f"\n{'НИЙТ':22}{tot:9}{sum(r[2] for r in rows):7}{tb:8}"
              f"{tb * 100 // tot:8}%")
        print("\n   нөхөлт% = машин орох/гарах агшинд систем МЭДЭЭГҮЙ байсан хувь")
        print("            (хаалт гараар нээгдсэн, төлбөр нэхэгдээгүй). 0% = хэвийн")
        print("   голог%  = event ирсэн ч дугаар танигдаагүй/итгэлцүүр багатай")

        # ── ЗУРГИЙН ХАМРАЛТ: өдөр × цагийн муж ──────────────────────────────
        print(f"\n══ Зургийн хамралт (өдөр × цагийн муж) ══")
        for site, sess, *_ in rows:
            grid: dict = defaultdict(lambda: [0, 0])
            for s in sess:
                d = str((s.entry_time + TZ).date())
                cell = grid[(d, bucket_of(s.entry_time))]
                cell[0] += 1
                if s.entry_snapshot or s.exit_snapshot:
                    cell[1] += 1
            if not grid:
                continue
            days = sorted({d for d, _b in grid})
            print(f"\n   {site.name}")
            print(f"   {'муж':20}" + "".join(f"{d[5:]:>14}" for d in days))
            for name, _a, _b in BUCKETS:
                cells = []
                for d in days:
                    n, pic = grid.get((d, name), [0, 0])
                    cells.append((f"{pic}/{n} {pic * 100 // n}%" if n else "—").rjust(14))
                print(f"   {name:20}" + "".join(cells))

        # Камерын сүүлийн амьд уншилт — «одоо ажиллаж байна уу» гэдгийн шууд хариу
        print(f"\n══ Камер бүрийн сүүлийн ХҮЛЭЭН АВСАН уншилт ══")
        now = datetime.utcnow()
        for site, *_ in rows:
            devs = (db.query(Device)
                    .filter(Device.site_id == site.id, Device.device_type == "camera",
                            Device.status != "deleted").all())
            if not devs:
                continue
            last = dict(db.query(LprEvent.device_id, func.max(LprEvent.created_at))
                        .filter(LprEvent.device_id.in_([d.id for d in devs]),
                                LprEvent.accepted.is_(True))
                        .group_by(LprEvent.device_id).all())
            print(f"\n   {site.name}")
            for d in sorted(devs, key=lambda x: (bool(x.nested_inner), x.lane_dir or "")):
                t = last.get(d.id)
                age = f"{(now - t).total_seconds() / 60:.0f} мин өмнө" if t else "ХЭЗЭЭ Ч ҮГҮЙ"
                mark = "🔵" if d.nested_inner else "  "
                warn = "  ⚠" if (t is None or (now - t).total_seconds() > 3600) else ""
                print(f"   {mark} {(d.name or '?')[:16]:18}{(d.lane_dir or '?'):6}{age:>18}{warn}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
