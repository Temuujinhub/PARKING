#!/usr/bin/env python3
"""«Бүртгэлгүй гарах» (no_session) яагаад болдгийг ШАЛТГААНААР нь ангилах.

ЯАГААД: 2026-08-13-нд нэг өдөрт 473 удаа `lpr exit … → no_session` гарсан.
`match_open_session` нь 3 шаттай (яг таарах → OCR ойролцоо → орох дутуу дэд
мөр), дараа нь `auto_reopen_for_exit` ажилладаг. Тэгэхээр «логик байхгүй»
гэсэн асуудал БИШ — аль шатанд, ямар шалтгаанаар унаж байгааг мэдэх хэрэгтэй.

Энэ хэрэгсэл гарах уншилт бүрийг ДАХИН тоглуулж (session-д хүрэхгүй, зөвхөн
уншина) дараах ангилалд хуваана:

  ✓ тохирсон           — session олдсон (яг/OCR/дэд мөр). Эдгээр нь ХЭВИЙН
  1. орох уншилт АЛГА   — тэр өдөр орох камер энэ машиныг огт уншаагүй
  2. session ХААГДСАН   — орох бүртгэл байсан ч гарахаас ӨМНӨ хаагдсан
  3. олон нэр дэвшигч   — OCR ойролцоо 2+ олдсон тул АЮУЛГҮЙН үүднээс татгалзсан
  4. хог уншилт         — дугаарын формат буруу (жишээ «УО4764УНХ», «9265УР»)
  5. давхар уншилт      — тэр машин саяхан ГАРСАН (session хаагдсан нь зөв)

Эхний 3 нь ЗАСАХ боломжтой; 4, 5 нь камерын/бодит байдлын асуудал.

Ажиллуулах:
    cd /root/PARKING/backend && venv/bin/python tools/exit_nosession_diag.py
    venv/bin/python tools/exit_nosession_diag.py --days 2 --site EREL
"""
import os
import sys
from collections import Counter
from datetime import datetime, timedelta

os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.getcwd())

from app.database import SessionLocal            # noqa: E402
from app.models import Device, LprEvent, ParkingSession, ParkingSite  # noqa: E402
from app.session_logic import (is_valid_plate, plates_ocr_similar)  # noqa: E402


def classify(db, ev, site_id, opens_cache):
    """Нэг гарах уншилтыг ангилна → (код, тайлбар)."""
    plate = ev.plate_number
    t = ev.created_at

    # Тухайн агшинд НЭЭЛТТЭЙ байсан session-үүд (яг тэр үеийн байдлаар)
    opens = [s for s in opens_cache
             if s.entry_time <= t and (s.exit_time is None or s.exit_time > t)]
    if any(s.plate_number == plate for s in opens):
        return "ok_exact", ""
    close = [s for s in opens if plates_ocr_similar(plate, s.plate_number)]
    if len(close) == 1:
        return "ok_fuzzy", close[0].plate_number
    if len(close) > 1:
        return "many", ",".join(s.plate_number for s in close[:3])
    if is_valid_plate(plate):
        partial = [s for s in opens
                   if not is_valid_plate(s.plate_number) and len(s.plate_number) >= 3
                   and (plate.startswith(s.plate_number)
                        or plate.endswith(s.plate_number)
                        or s.plate_number in plate)]
        if len(partial) == 1:
            return "ok_partial", partial[0].plate_number
    else:
        return "junk", plate

    # Session огт нээлттэй байгаагүй — орох уншилт байсан уу?
    entry = (db.query(LprEvent)
             .filter(LprEvent.site_id == site_id, LprEvent.lane_dir == "entry",
                     LprEvent.plate_number == plate,
                     LprEvent.created_at >= t - timedelta(hours=48),
                     LprEvent.created_at < t)
             .order_by(LprEvent.created_at.desc()).first())
    if entry is None:
        return "no_entry", ""

    # Орох уншилт байсан → session нь гарахаас ӨМНӨ хаагдсан уу?
    closed = (db.query(ParkingSession)
              .filter(ParkingSession.site_id == site_id,
                      ParkingSession.plate_number == plate,
                      ParkingSession.entry_time >= t - timedelta(hours=48),
                      ParkingSession.entry_time < t)
              .order_by(ParkingSession.entry_time.desc()).first())
    if closed is None:
        return "entry_no_session", ""
    if closed.exit_time is not None and closed.exit_time <= t:
        gap = (t - closed.exit_time).total_seconds()
        if gap < 300:
            return "recent_exit", f"{gap:.0f}с өмнө гарсан"
        return "closed_early", (f"{closed.status}, "
                                f"{gap / 3600:.1f}ц өмнө хаагдсан")
    return "other", closed.status


LABELS = {
    "ok_exact":         "✓ яг таарсан",
    "ok_fuzzy":         "✓ OCR ойролцоогоор тохсон",
    "ok_partial":       "✓ орох дутуу → дэд мөрөөр тохсон",
    "many":             "3. олон нэр дэвшигч — АЮУЛГҮЙН үүднээс татгалзсан",
    "junk":             "4. хог уншилт (формат буруу)",
    "no_entry":         "1. орох уншилт АЛГА",
    "entry_no_session": "1б. орох уншилт бий ч session үүсээгүй",
    "closed_early":     "2. session ГАРАХААС ӨМНӨ хаагдсан",
    "recent_exit":      "5. саяхан гарсан (давхар уншилт)",
    "other":            "— бусад",
}


def main(days: int, site_code: str | None):
    db = SessionLocal()
    try:
        since = datetime.utcnow() - timedelta(days=days)
        sites = db.query(ParkingSite).all()
        if site_code:
            sites = [s for s in sites if s.site_code == site_code]
        print(f"=== Бүртгэлгүй гарах (no_session) шинжилгээ — сүүлийн {days} хоног ===\n")
        grand = Counter()
        for site in sites:
            # Дотоод (nested) хаалтыг оруулахгүй — тэдгээр нь session нээдэггүй
            inner = {d.id for d in db.query(Device).filter(
                Device.site_id == site.id, Device.nested_inner.is_(True)).all()}
            evs = (db.query(LprEvent)
                   .filter(LprEvent.site_id == site.id, LprEvent.lane_dir == "exit",
                           LprEvent.accepted.is_(True),
                           LprEvent.created_at >= since)
                   .order_by(LprEvent.created_at).all())
            evs = [e for e in evs if e.device_id not in inner]
            if not evs:
                continue
            opens_cache = (db.query(ParkingSession)
                           .filter(ParkingSession.site_id == site.id,
                                   ParkingSession.entry_time >= since - timedelta(hours=48))
                           .all())
            cnt, samples = Counter(), {}
            for ev in evs:
                code, detail = classify(db, ev, site.id, opens_cache)
                cnt[code] += 1
                grand[code] += 1
                if code not in samples and not code.startswith("ok"):
                    samples[code] = f"{ev.plate_number} {ev.created_at:%m-%d %H:%M} {detail}"
            ok = sum(v for k, v in cnt.items() if k.startswith("ok"))
            print(f"── {site.name} ({site.site_code}) — гарах уншилт {len(evs)}, "
                  f"тохирсон {ok} ({ok * 100 // max(1, len(evs))}%)")
            for code, n in cnt.most_common():
                if code.startswith("ok"):
                    continue
                print(f"   {n:>5}  {LABELS.get(code, code):<48} {samples.get(code, '')}")
            print()

        total = sum(grand.values())
        ok = sum(v for k, v in grand.items() if k.startswith("ok"))
        print(f"═══ НИЙТ гарах уншилт {total}, тохирсон {ok} "
              f"({ok * 100 // max(1, total)}%), тохироогүй {total - ok}")
        for code, n in grand.most_common():
            print(f"   {n:>6}  ({n * 100 / max(1, total):4.1f}%)  {LABELS.get(code, code)}")
        print("\nЗАСАХ БОЛОМЖТОЙ нь 1, 1б, 2, 3 — эдгээрийн хэмжээгээр дараагийн")
        print("ажлыг эрэмбэлнэ. 4 (хог) ба 5 (давхар) нь кодоор засагдахгүй.")
    finally:
        db.close()


if __name__ == "__main__":
    _days, _site = 1, None
    for i, a in enumerate(sys.argv[1:]):
        if a == "--days" and i + 2 <= len(sys.argv[1:]):
            _days = int(sys.argv[i + 2])
        elif a == "--site" and i + 2 <= len(sys.argv[1:]):
            _site = sys.argv[i + 2]
    main(_days, _site)
