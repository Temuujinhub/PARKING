"""Логийн богино мөчлөгийн НӨӨЦ зам — чимээгүй камерын уншилтыг сэргээнэ.

    cd backend && venv/bin/python tests/test_log_tail.py

Батлах зүйлс:
  • стрим АЖИЛЛАЖ БАЙГАА камерт огт хүрэхгүй (камерын нөөцийг хамгаална)
  • чимээгүй камерын логоос уншилтыг сэргээж, ЖИНХЭНЭ session үүсгэнэ
  • нэг бичлэгийг ХОЁР УДАА боловсруулахгүй (мөчлөг давтагдсан ч)
  • стрим сэргээд ижил уншилтыг бүртгэчихсэн бол давхардуулахгүй
"""
import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings  # noqa: E402

settings.barrier_mock = True
settings.snapshot_enabled = False
settings.screen_enabled = False
settings.log_tail_enabled = True

from app.database import SessionLocal  # noqa: E402
from app.models import Device, LprEvent, ParkingSession, ParkingSite  # noqa: E402
from app.services import log_tail  # noqa: E402

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗ <<< FAIL'} {name}{'' if cond else f'  [{extra}]'}")


def _rec(plate, at_utc, skew_min=0):
    """Камерын бичлэгийн хэлбэр. skew_min — камерын цаг гулссан байдал."""
    t = at_utc + timedelta(minutes=skew_min)
    return {"PlateNumber": plate, "Time": int(t.replace(tzinfo=timezone.utc).timestamp()),
            "Event": 34, "event_name": "TrafficTollGate"}


def main():
    db = SessionLocal()
    tag = uuid.uuid4().hex[:6]
    site = ParkingSite(name=f"ZZ-Нөөц-{tag}", site_code=f"ZZB{tag}", is_active=True)
    db.add(site)
    db.flush()
    quiet = Device(site_id=site.id, name="Чимээгүй орох", device_type="camera",
                   lane_dir="entry", lane_no=1, ip_address=f"10.9.{tag[:2]}.10",
                   status="active")
    loud = Device(site_id=site.id, name="Ажиллаж буй орох", device_type="camera",
                  lane_dir="entry", lane_no=2, ip_address=f"10.9.{tag[:2]}.20",
                  status="active")
    db.add_all([quiet, loud])
    db.commit()
    now = datetime.utcnow()
    # «loud» камер саяхан уншилт хийсэн → чимээгүй БИШ. ГЭХДЭЭ entry burst
    # цонхноос (6с) гадуур байлгана — эс бол «нэг машин» гэж тооцогдоно.
    db.add(LprEvent(site_id=site.id, device_id=loud.id, plate_number="0001УБА",
                    lane_dir="entry", confidence=99, accepted=True,
                    created_at=now - timedelta(seconds=30)))
    db.commit()

    pulled: list = []
    SKEW = 32   # камерын цаг 32 минутаар түрүүлсэн (Рашбулагийн бодит байдал)

    backlog = [_rec(f"{i}111УБА", now - timedelta(minutes=15 - i), SKEW)
               for i in range(10)]        # 15..6 минутын өмнөх хуучин уншилтууд

    async def fake_fetch(ip, u, p, start, end, plate=None, client=None):
        pulled.append(ip)
        return backlog + [_rec("5678УБА", now - timedelta(seconds=30), SKEW)]

    log_tail.fetch_snap_events = fake_fetch
    log_tail._seen.clear()

    try:
        print("\nЗөвхөн ЧИМЭЭГҮЙ камерт хандана:")
        res0 = asyncio.run(log_tail.run_once(site.id))
        check("чимээгүй камер олдсон", res0["silent"] == 1, str(res0))
        check("ажиллаж буй камерт хүрээгүй", loud.ip_address not in pulled, str(pulled))
        check("чимээгүй камераас татсан", quiet.ip_address in pulled, str(pulled))

        print("\nАНХНЫ таталт хуучин логийг БӨӨНӨӨР боловсруулахгүй:")
        # 2026-08-16 прод: анхны мөчлөг 42 хуучин уншилтыг зэрэг өгснөөс
        # handle_entry-ийн 6с burst логик 20 машиныг НЭГ session болгож,
        # дугаарыг нь дараалан дарж бичсэн (plate_autocorrect).
        check("анхны мөчлөгт юу ч боловсруулаагүй", res0["recovered"] == 0, str(res0))
        check("хуучин уншилтаас session үүсээгүй",
              db.query(ParkingSession).filter(ParkingSession.site_id == site.id).count() == 0)

        print("\nДараагийн мөчлөгт ЗӨВХӨН ШИНЭХЭН уншилтыг авна:")
        backlog.append(_rec("5678УБА", now - timedelta(seconds=20), SKEW))
        log_tail._seen[quiet.id].discard(log_tail._key("5678УБА",
                                                      now - timedelta(seconds=30)))
        res = asyncio.run(log_tail.run_once(site.id))
        check("нэг уншилт сэргэсэн", res["recovered"] == 1, str(res))
        s = (db.query(ParkingSession)
             .filter(ParkingSession.site_id == site.id,
                     ParkingSession.plate_number == "5678УБА").first())
        check("session үүссэн", s is not None)
        check("төлөв OPEN", bool(s) and s.status == "OPEN", s.status if s else "—")
        check("камерын 32 мин зөрүү session-д ОРООГҮЙ",
              bool(s) and abs((s.entry_time - now).total_seconds()) < 300,
              str(s.entry_time) if s else "—")

        print("\nМөчлөг давтагдахад ДАВХАР боловсруулахгүй:")
        before = db.query(ParkingSession).filter(ParkingSession.site_id == site.id).count()
        res2 = asyncio.run(log_tail.run_once(site.id))
        after = db.query(ParkingSession).filter(ParkingSession.site_id == site.id).count()
        check("шинэ уншилт боловсруулаагүй", res2["recovered"] == 0, str(res2))
        check("session тоо өөрчлөгдөөгүй", before == after, f"{before}→{after}")

        print("\nСанах ой цэвэрлэгдсэн ч lpr_events давхардлыг барина:")
        log_tail._seen.clear()          # сервис дахин ассан гэж үзье
        res3 = asyncio.run(log_tail.run_once(site.id))
        after3 = db.query(ParkingSession).filter(ParkingSession.site_id == site.id).count()
        check("DB-ийн шалгалт давхардлыг зогсоосон", res3["recovered"] == 0, str(res3))
        check("session тоо хэвээр", after3 == before, f"{before}→{after3}")
    finally:
        db.query(LprEvent).filter(LprEvent.site_id == site.id).delete()
        db.query(ParkingSession).filter(ParkingSession.site_id == site.id).delete()
        db.query(Device).filter(Device.site_id == site.id).delete()
        db.query(ParkingSite).filter(ParkingSite.id == site.id).delete()
        db.commit()
        db.close()

    print(f"\n{PASS} PASS, {FAIL} FAIL")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
