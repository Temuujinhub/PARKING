"""Камерын логтой тулгаж «зогсоолд гацсан» бүртгэлийг ЖИНХЭНЭ гарсан цагаар хаах.

Асуудал: гарах event амьд урсгалаар алдагдвал бүртгэл ОРОЙ хүртэл нээлттэй үлдэнэ.
Камерын дотоод лог тэр машин хэдэн цагийн өмнө гарсныг мэдэж байдаг ч бид уншдаггүй
байсан. Үр дагавар:
  • зогсоолын багтаамжаас олон машин «дотор» харагдана (Кэй Эйч: 91 байрлалд 118)
  • 24-72 цаг «зогссон» машин жагсаалтыг дүүргэнэ
  • гарах ЗУРАГТАЙ атлаа «Зогсож байна» төлөвтэй мөр гарна
  • эцэст нь 12 цагийн авто хаалт ХУУРАМЧ хугацаагаар хааж, төлбөрийг хөөрөгдөнө

`camera_sync` (2026-08-12-нээс) үүнийг урсгалын дунд хийдэг болсон ч зөвхөн
lookback_hours (анхдагч 8ц) цонхыг хардаг. ХУРИМТЛАГДСАН үлдэгдэлд энэ хэрэгсэл.

Ажиллуулах (эхлээд ЗААВАЛ dry-run):
    cd /root/PARKING/backend
    venv/bin/python tools/exit_reconcile.py --hours 72              # зөвхөн харуулна
    venv/bin/python tools/exit_reconcile.py --hours 72 --site KH
    venv/bin/python tools/exit_reconcile.py --hours 72 --apply      # ҮНЭХЭЭР хаана

Хамгаалалт:
  • ЗӨВХӨН OPEN/PAID бүртгэлд хүрнэ. AWAITING_PAYMENT-д ХҮРЭХГҮЙ — тэр машиныг
    систем гарцад хараад төлбөр хүлээж байгаа, auto_close-ийн дүрэм шийднэ.
  • Гарах уншилт нь орсон цагаас ХОЙШ байх ёстой.
  • Өр анхдагчаар үүсгэхгүй (жолооч төлөх боломж олгогдоогүй) — `--debt` өгвөл үүснэ.
  • Бүх үйлдэл AuditLog-д (EXIT_RECONCILE).
"""
import argparse
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal
from app.models import AuditLog, ParkingSession, ParkingSite
from app.services.camera_records import site_camera_events
from app.session_logic import close_session_forced, is_valid_plate, normalize_plate

TZ = timedelta(hours=8)


def L(dt):
    return (dt + TZ).strftime("%m-%d %H:%M") if dt else "—"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=72, help="камерын логийн цонх (цаг)")
    ap.add_argument("--site", help="зогсоолын код (жишээ: KH)")
    ap.add_argument("--apply", action="store_true", help="ҮНЭХЭЭР хаана")
    ap.add_argument("--debt", action="store_true", help="хаахдаа өр үүсгэх")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        q = db.query(ParkingSite).filter(ParkingSite.is_active.is_(True))
        if args.site:
            q = q.filter(ParkingSite.site_code == args.site)
        sites = q.all()
        if not sites:
            print("Зогсоол олдсонгүй.")
            return

        mode = "ХААХ" if args.apply else "DRY-RUN (юу ч өөрчлөгдөхгүй)"
        print(f"\n═══ ГАРАХ УНШИЛТААР ТУЛГАХ · {mode} · сүүлийн {args.hours}ц ═══")

        grand_n, grand_fee = 0, 0.0
        for site in sites:
            open_rows = (db.query(ParkingSession)
                         .filter(ParkingSession.site_id == site.id,
                                 ParkingSession.status.in_(("OPEN", "PAID")))
                         .all())
            if not open_rows:
                continue
            try:
                cam = site_camera_events(db, site.id, hours=args.hours)
            except Exception as e:  # noqa: BLE001
                print(f"\n── {site.name}: камерын лог уншигдсангүй — {e}")
                continue
            broken = [c for c in cam["cameras"] if c.get("error")]
            exits: dict[str, list] = {}
            for e in cam["events"]:
                if e["lane_dir"] == "exit" and e["plate"]:
                    exits.setdefault(normalize_plate(e["plate"]), []).append(e["time"])
            for v in exits.values():
                v.sort()

            hits = []
            for s in open_rows:
                if not is_valid_plate(s.plate_number):
                    continue
                t = next((x for x in exits.get(s.plate_number, []) if x > s.entry_time), None)
                if t:
                    hits.append((s, t))

            print(f"\n── {site.name} ({site.site_code})  ·  нээлттэй {len(open_rows)}"
                  f"  ·  логоос гарсан нь тогтоогдсон {len(hits)}"
                  + (f"  ·  ⚠ {len(broken)} камер уншигдсангүй" if broken else ""))
            if not hits:
                continue
            hits.sort(key=lambda h: h[0].entry_time)
            for s, t in hits[:15]:
                hrs = (t - s.entry_time).total_seconds() / 3600
                print(f"   {s.plate_number:<10} орсон {L(s.entry_time)}  →  "
                      f"гарсан {L(t)}  ({hrs:.1f}ц)")
            if len(hits) > 15:
                print(f"   … бас {len(hits) - 15}")

            if not args.apply:
                grand_n += len(hits)
                continue
            for s, t in hits:
                try:
                    s.exit_time = t
                    s.exit_confirmed = True
                    if s.status == "OPEN":
                        s.status = "AWAITING_PAYMENT"
                    close_session_forced(db, s, "exit_reconcile", "system",
                                         create_comp=args.debt)
                    db.add(AuditLog(username="system", action="EXIT_RECONCILE",
                                    entity="session", entity_id=s.id,
                                    detail={"plate": s.plate_number,
                                            "exit": t.isoformat(),
                                            "entry": s.entry_time.isoformat()}))
                    db.commit()
                    grand_n += 1
                    grand_fee += float(s.total_fee or 0)
                except Exception as e:  # noqa: BLE001
                    db.rollback()
                    print(f"   ! {s.plate_number}: {e}")

        print("\n" + "─" * 60)
        if args.apply:
            print(f"✅ {grand_n} бүртгэл ЖИНХЭНЭ гарсан цагаар хаагдлаа "
                  f"(нийт {grand_fee:,.0f}₮ бодогдов)")
        else:
            print(f"{grand_n} бүртгэлийг хаах боломжтой. Үнэхээр хаах бол `--apply`.")
        print()
    finally:
        db.close()


if __name__ == "__main__":
    main()
