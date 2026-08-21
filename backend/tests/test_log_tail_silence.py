"""log_tail нь ӨӨРИЙНХӨӨ оруулсан уншилтаар «камер амьд» гэж хуурагдахгүй.

Яагаад: `_silent_devices` нь log_tail-ийн оруулсан уншилтыг ч тооцдог байсан тул
нэг уншилт оруулмагц камер 180 секунд «амьд» харагдаж, дараагийн таталт 200
секундын дараа болдог байв. Үр дүнд нь бодит машины уншилт 200с хоцорч, хаалт нь
машиныг явсны ДАРАА нээгддэг («машин байхгүй атал нээгдэж байна» гэсэн гомдол,
2026-08-21 Рашбулаг ЭТТ — хаалтны лог яг 200 секундын алхамтай байв).

Амьд Postgres шаардана. Бүх өөрчлөлт rollback хийгддэг — DB-д юу ч үлдэхгүй.

    cd backend && venv/bin/python tests/test_log_tail_silence.py
"""
import asyncio
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import Device, LprEvent, ParkingSite  # noqa: E402
from app.services.log_tail import _silent_devices, may_open  # noqa: E402

PASS = FAIL = 0


def check(name, cond):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗ <<< FAIL'} {name}")


def silent_ids(db, site_id):
    return {d.id for d in asyncio.run(_silent_devices(db, site_id))}


print("may_open — хуучин уншилтад хаалт нээхгүй (эзэнгүй онгорхой хаалтаас сэргийлнэ):")
check("хэвийн мөчлөг (20с) → нээнэ", may_open(20.0, 20.0))
check("чимээгүй камер олноос мөчлөг удаашрав (80с) → нээсээр байна",
      may_open(80.0, 20.0))
check(f"босго ({settings.log_tail_open_max_lag_sec:.0f}с) хүртэл нээнэ",
      may_open(settings.log_tail_open_max_lag_sec, 20.0))
check("сервис саяхан асcан (3 мин) → НЭЭХГҮЙ", not may_open(180.0, 20.0))
check("камер 5 мин хүрээгүй байсан → НЭЭХГҮЙ", not may_open(300.0, 20.0))
check("анхны таталт (gap = monotonic бүхэл) → НЭЭХГҮЙ", not may_open(1_000_000.0, 20.0))
check("мөчлөг маш удаан тохируулсан ч 3 мөчлөгөөс доош буухгүй",
      may_open(150.0, 50.0))
print()

db = SessionLocal()
try:
    now = datetime.utcnow()
    old = now - timedelta(seconds=settings.log_tail_silence_sec + 60)

    site = ParkingSite(name="ZZ-log_tail тест", site_code=f"ZZLT{int(now.timestamp()) % 100000}",
                       capacity=10, is_active=True)
    db.add(site)
    db.flush()
    cam = Device(site_id=site.id, name="Тест камер", device_type="camera",
                 ip_address="10.255.255.1", lane_dir="entry", status="active")
    db.add(cam)
    db.flush()

    print("Уншилт огт байхгүй камер:")
    check("чимээгүй гэж тооцогдоно", cam.id in silent_ids(db, site.id))

    print("\nСТРИМЭЭР саяхан уншилт ирсэн камер:")
    stream_ev = LprEvent(site_id=site.id, device_id=cam.id, plate_number="1111ААА",
                         lane_dir="entry", confidence=99, accepted=True,
                         raw={"TrafficCar": {"PlateNumber": "1111ААА"}}, created_at=now)
    db.add(stream_ev)
    db.flush()
    check("чимээгүй БИШ — стрим ажиллаж байна", cam.id not in silent_ids(db, site.id))

    print("\nСтрим хуучирсан, харин log_tail САЯ уншилт оруулсан:")
    stream_ev.created_at = old
    db.add(LprEvent(site_id=site.id, device_id=cam.id, plate_number="2222ББВ",
                    lane_dir="entry", confidence=100, accepted=True,
                    raw={"log_tail": True, "camera_time": now.isoformat(),
                         "TrafficCar": {"PlateNumber": "2222ББВ"}}, created_at=now))
    db.flush()
    check("ЧИМЭЭГҮЙ хэвээр — өөрийн оруулсан уншилт стримийг орлохгүй",
          cam.id in silent_ids(db, site.id))

    print("\nСтрим сэргэвэл (шинэ стрим уншилт):")
    db.add(LprEvent(site_id=site.id, device_id=cam.id, plate_number="3333ВГД",
                    lane_dir="entry", confidence=99, accepted=True,
                    raw={"TrafficCar": {"PlateNumber": "3333ВГД"}}, created_at=now))
    db.flush()
    check("чимээгүй БИШ болно", cam.id not in silent_ids(db, site.id))

    print("\nТатгалзсан (accepted=False) уншилт:")
    for e in db.query(LprEvent).filter(LprEvent.device_id == cam.id).all():
        e.accepted = False
    db.flush()
    check("татгалзсан уншилт стрим амьд гэдгийг нотлохгүй",
          cam.id in silent_ids(db, site.id))
finally:
    db.rollback()   # тестийн мөрүүд DB-д ҮЛДЭХГҮЙ
    db.close()

print(f"\n{PASS} PASS, {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
