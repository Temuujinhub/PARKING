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

ХОЁР ҮЕ ШАТ (дараалал нь ЧУХАЛ — эсрэгээр нь хийвэл орлого устана):
  1. Камерын логт ГАРАХ УНШИЛТ нь БАЙГАА бүртгэл → ЖИНХЭНЭ гарсан цагаар хаана,
     төлбөр нь тэр хугацаагаар зөв бодогдоно (орлого хадгалагдана).
  2. `--free-rest N` өгвөл: логт гарах баримт нь ОЛДООГҮЙ, зөвхөн орох уншилттай,
     N цагаас хуучин бүртгэл → ҮНЭГҮЙ хаана (0₮, хугацаа NULL, өргүй). Хэзээ
     гарсныг нь мэдэхгүй тул хуурамч дүн бичихгүй.

Ажиллуулах (эхлээд ЗААВАЛ dry-run):
    cd /root/PARKING/backend
    venv/bin/python tools/exit_reconcile.py --hours 72                    # харуулна
    venv/bin/python tools/exit_reconcile.py --hours 72 --free-rest 24     # 2 үе шат
    venv/bin/python tools/exit_reconcile.py --hours 72 --site KH --apply  # нэг зогсоол
    venv/bin/python tools/exit_reconcile.py --hours 72 --free-rest 24 --apply

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
    ap.add_argument("--free-rest", type=int, metavar="ЦАГ",
                    help="логоос гарсан нь ОЛДООГҮЙ, зөвхөн орох уншилттай "
                         "бүртгэлээс N цагаас хуучныг ҮНЭГҮЙ хаах (0₮, өргүй)")
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

        grand_n, grand_fee, grand_free = 0, 0.0, 0
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
            hits.sort(key=lambda h: h[0].entry_time)
            for s, t in hits[:15]:
                hrs = (t - s.entry_time).total_seconds() / 3600
                print(f"   {s.plate_number:<10} орсон {L(s.entry_time)}  →  "
                      f"гарсан {L(t)}  ({hrs:.1f}ц)")
            if len(hits) > 15:
                print(f"   … бас {len(hits) - 15}")

            # ── 2-р үе шат: логоос ОЛДООГҮЙ, зөвхөн орох уншилттай хуучин
            # бүртгэлүүд. Гарах баримт хаана ч байхгүй тул хугацаа нь ТОДОРХОЙГҮЙ —
            # хуурамч дүн бичихгүй, ҮНЭГҮЙ хаана (auto_close-ийн entry_only дүрэмтэй
            # ижил зарчим, зөвхөн босгыг нь энд гараар өгнө).
            free_rows = []
            if args.free_rest:
                hit_ids = {s.id for s, _ in hits}
                cutoff = datetime.utcnow() - timedelta(hours=args.free_rest)
                free_rows = [s for s in open_rows
                             if s.id not in hit_ids and s.status == "OPEN"
                             and s.exit_device_id is None and s.entry_time < cutoff]
                if free_rows:
                    print(f"   + логт олдоогүй, {args.free_rest}ц-аас хуучин: "
                          f"{len(free_rows)} → ҮНЭГҮЙ хаана")

            if not args.apply:
                grand_n += len(hits)
                grand_free += len(free_rows)
                continue
            for s in free_rows:
                try:
                    s.status = "FREE"
                    s.exit_time = datetime.utcnow()
                    s.duration_minutes = None      # хэзээ гарсныг МЭДЭХГҮЙ
                    s.base_fee, s.vat_amount, s.total_fee = 0, 0, 0
                    s.note = (f"{s.note + ' | ' if s.note else ''}"
                              f"exit_reconcile: гарах баримт олдсонгүй — "
                              f"{args.free_rest}ц дараа үнэгүй хаав")[:1000]
                    db.add(AuditLog(username="system", action="EXIT_RECONCILE_FREE",
                                    entity="session", entity_id=s.id,
                                    detail={"plate": s.plate_number,
                                            "entry": s.entry_time.isoformat(),
                                            "hours": args.free_rest}))
                    db.commit()
                    grand_free += 1
                except Exception as e:  # noqa: BLE001
                    db.rollback()
                    print(f"   ! {s.plate_number}: {e}")
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
            if grand_free:
                print(f"✅ {grand_free} бүртгэл гарах баримтгүй тул ҮНЭГҮЙ хаагдлаа "
                      f"(0₮, өргүй)")
        else:
            print(f"{grand_n} бүртгэлийг ЖИНХЭНЭ гарсан цагаар хаах боломжтой.")
            if grand_free:
                print(f"{grand_free} бүртгэлийг ҮНЭГҮЙ хаах боломжтой (гарах баримтгүй).")
            print("Үнэхээр хаах бол `--apply` нэмнэ үү.")
        print()
    finally:
        db.close()


if __name__ == "__main__":
    main()
