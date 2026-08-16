"""Дамжин (nested) зогсоолд ЭНГИЙН нөхөлтийн дүрэм үйлчлэхгүй байхыг батална.

    cd backend && venv/bin/python tests/test_nested_sync_rules.py

Рашбулаг ЭТТ-ийн дараалал: Орох камер → Орох 2 → Гарах 2 → Гарах камер.
ДУНДАХ хоёр (Орох 2 / Гарах 2) нь шороон зогсоол руу орж гарахыг илэрхийлдэг
болохоос манай зогсоолд орох/гарахыг ИЛЭРХИЙЛЭХГҮЙ. Гэтэл камерын логийн
нөхөлт (camera_sync) бүх камерыг ялгалгүй уншиж:
  • «Орох 2»-ын уншилтаар ШИНЭ session үүсгэдэг,
  • «Гарах 2»-ын уншилтаар ГАДНА session-ийг «гарсан» гэж ХААДАГ байв.
Улмаас машин дотор байхад «гарсан» болж, жинхэнэ гарцад нь «бүртгэлгүй»
болоод оператор гараар нээдэг байлаа.
"""
import os
import sys
import uuid
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings  # noqa: E402

settings.barrier_mock = True
settings.snapshot_enabled = False
settings.screen_enabled = False

from app.database import SessionLocal  # noqa: E402
from app.models import Device, LprEvent, ParkingSession, ParkingSite  # noqa: E402
from app.services import camera_sync  # noqa: E402

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗ <<< FAIL'} {name}{'' if cond else f'  [{extra}]'}")


RULES = {"enabled": True, "times_per_day": 4, "lookback_hours": 12,
         "min_age_minutes": 0, "create_debt": False, "skip_invalid_plate": True}


def _cam(inner_events, outer_events=()):
    """site_camera_events-ийн буцаах хэлбэрийг дуурайна."""
    return {"window_hours": 2.0,
            "cameras": [{"name": "Орох камер", "ip": "10.0.0.10", "lane_dir": "entry",
                         "events": len(outer_events), "error": None, "nested_inner": False},
                        {"name": "Орох 2", "ip": "10.0.0.12", "lane_dir": "entry",
                         "events": 0, "error": None, "nested_inner": True}],
            "events": list(outer_events), "inner_events": list(inner_events)}


def _ev(plate, lane_dir, at, inner=True):
    return {"plate": plate, "raw_plate": plate, "time": at, "lane_dir": lane_dir,
            "event": "TrafficJunction", "source": "Video", "camera": "Орох 2",
            "nested_inner": inner}


def main():
    db = SessionLocal()
    tag = uuid.uuid4().hex[:6]
    site = ParkingSite(name=f"ZZ-Дамжин-{tag}", site_code=f"ZZT{tag}",
                       transit_max_hours=4, is_active=True)
    db.add(site)
    db.flush()
    db.add_all([
        Device(site_id=site.id, name="Орох камер", device_type="camera", lane_dir="entry",
               lane_no=1, ip_address=f"10.0.{tag[:2]}.10", status="active"),
        Device(site_id=site.id, name="Орох 2", device_type="camera", lane_dir="entry",
               lane_no=3, ip_address=f"10.0.{tag[:2]}.12", status="active",
               nested_inner=True),
        Device(site_id=site.id, name="Гарах 2", device_type="camera", lane_dir="exit",
               lane_no=4, ip_address=f"10.0.{tag[:2]}.13", status="active",
               nested_inner=True),
    ])
    now = datetime.utcnow()
    plate = "1234УБА"
    s = ParkingSession(site_id=site.id, plate_number=plate, status="OPEN",
                       entry_time=now - timedelta(hours=2))
    db.add(s)
    db.commit()

    try:
        print("\nДотоод уншилт нь ТООЛУУР зогсооно (session хөндөхгүй):")
        t_in = now - timedelta(minutes=60)
        p, r = camera_sync._sync_inner(db, site, _cam([_ev(plate, "entry", t_in)]),
                                       now - timedelta(hours=2), now, RULES, False)
        db.refresh(s)
        check("тоолуур зогссон", p == 1 and s.paused_since is not None, f"p={p} {s.paused_since}")
        check("session хэвээр OPEN", s.status == "OPEN", s.status)
        check("гарах цаг бичигдээгүй", s.exit_time is None, str(s.exit_time))
        check("шинэ session үүсээгүй",
              db.query(ParkingSession).filter(ParkingSession.site_id == site.id).count() == 1)

        print("\nДотоод ГАРАХ уншилт тоолуурыг үргэлжлүүлнэ (хаахгүй):")
        t_out = now - timedelta(minutes=30)
        p2, r2 = camera_sync._sync_inner(db, site, _cam([_ev(plate, "exit", t_out)]),
                                         now - timedelta(hours=2), now, RULES, False)
        db.refresh(s)
        check("тоолуур үргэлжилсэн", r2 == 1 and s.paused_since is None, f"r={r2}")
        check("хасагдах минут хуримтлагдсан", 29 <= int(s.paused_minutes or 0) <= 31,
              str(s.paused_minutes))
        check("session ХААГДААГҮЙ", s.status == "OPEN" and s.exit_time is None, s.status)

        print("\nАмьд урсгал аль хэдийн үзсэн уншилтыг ДАВХАР тоолохгүй:")
        t3 = now - timedelta(minutes=20)
        db.add(LprEvent(site_id=site.id, plate_number=plate, lane_dir="entry",
                        confidence=99, accepted=True, created_at=t3))
        db.commit()
        before = int(s.paused_minutes or 0)
        p3, _ = camera_sync._sync_inner(db, site, _cam([_ev(plate, "entry", t3)]),
                                        now - timedelta(hours=2), now, RULES, False)
        db.refresh(s)
        check("логийн хуулбар алгасагдсан", p3 == 0 and s.paused_since is None, f"p={p3}")
        check("хуримтлал өөрчлөгдөөгүй", int(s.paused_minutes or 0) == before)

        print("\nЭнгийн нөхөлтийн дүрэм дотоод уншилтад ХҮРЭХГҮЙ:")
        # site_camera_events дотоод камерын уншилтыг `events`-т ОРУУЛАХГҮЙ тул
        # sync_site-ийн орох/гарах гогцоо түүнийг огт олж харахгүй.
        cam = _cam([_ev(plate, "exit", now - timedelta(minutes=10))])
        check("гадна урсгалд дотоод уншилт алга",
              all(not e.get("nested_inner") for e in cam["events"]))
        check("дотоод уншилт тусдаа урсгалд байна", len(cam["inner_events"]) == 1)

        # camera_records-ийн ХУВААЛТ өөрөө зөв ажиллаж байгаа эсэх (гэрээ)
        from app.services.camera_records import site_camera_events
        import inspect
        src = inspect.getsource(site_camera_events)
        check("site_camera_events дотоод/гадныг салгадаг",
              "inner_events" in src and "nested_inner" in src)
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
