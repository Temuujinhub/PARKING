"""Cooldown/in-flight алгасалт SKIPPED мөр үлдээдэг болов (аудит A10).

Өмнө нь ensure_entry_barrier / ensure_exit_barrier_if_cleared / ensure_inner_barrier
нар cooldown буюу in-flight үед True буцаагаад ЯМАР Ч ул мөр үлдээдэггүй байсан
тул «уншилт бий, команд алга» байдал оношлогдохгүй байв. SKIPPED мөр нь:
  • cooldown-ы SUCCESS хайлтад тооцогдохгүй (зан төлөв өөрчлөгдөөгүй)
  • шалтгаан нь response_text-д бичигдэнэ

Амьд Postgres шаардана (тестийн өгөгдөл өөрөө цэвэрлэгдэнэ).

    cd backend && venv/bin/python tests/test_barrier_skipped.py
"""
import asyncio
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings  # noqa: E402

settings.barrier_mock = True
settings.snapshot_enabled = False
settings.screen_enabled = False

from app.database import SessionLocal  # noqa: E402
from app.models import BarrierCommand, Device, ParkingSite  # noqa: E402
from app.session_logic import ensure_entry_barrier  # noqa: E402

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗ <<< FAIL'} {name}{'' if cond else f'  [{extra}]'}")


def main():
    db = SessionLocal()
    tag = uuid.uuid4().hex[:6]
    site = ParkingSite(name=f"ZZ-Skip-{tag}", site_code=f"ZZS{tag}", is_active=True)
    db.add(site)
    db.flush()
    cam = Device(site_id=site.id, name="Орох камер", device_type="camera",
                 lane_dir="entry", lane_no=1, ip_address=f"10.7.{int(tag[:2], 16) % 250}.10",
                 status="active", auto_open=True)
    bar = Device(site_id=site.id, name="Орох хаалт", device_type="barrier",
                 lane_dir="entry", lane_no=1, status="active")
    db.add_all([cam, bar])
    db.commit()
    site_id, bar_id = site.id, bar.id

    try:
        print("Cooldown-ы алгасалт SKIPPED мөртэй:")
        ok1 = asyncio.run(ensure_entry_barrier(db, cam, "7777УБК"))
        db.commit()
        check("эхний нээлт SUCCESS", ok1)
        ok2 = asyncio.run(ensure_entry_barrier(db, cam, "7777УБК"))
        db.commit()
        check("cooldown дотор True буцаасаар байна (зан төлөв хэвээр)", ok2)
        cmds = (db.query(BarrierCommand).filter(BarrierCommand.device_id == bar_id)
                .order_by(BarrierCommand.created_at).all())
        succ = [c for c in cmds if c.status == "SUCCESS"]
        skip = [c for c in cmds if c.status == "SKIPPED"]
        check("SUCCESS команд ганц л удаа (давхардаагүй)", len(succ) == 1, str(len(succ)))
        check("SKIPPED мөр үлдсэн", len(skip) == 1, str([c.status for c in cmds]))
        check("шалтгаан нь бичигдсэн",
              bool(skip) and "давт" in (skip[0].response_text or ""),
              skip[0].response_text if skip else "—")
        check("SKIPPED нь дугаараа агуулна",
              bool(skip) and "7777УБК" in (skip[0].response_text or ""),
              skip[0].response_text if skip else "—")

        # SKIPPED мөр cooldown-ы SUCCESS хайлтыг өдөөхгүй гэдгийг зан төлөвөөр
        # батлах: cooldown өнгөрмөгц ДАХИН SUCCESS команд явна
        from datetime import datetime, timedelta
        for c in succ:   # cooldown-оос гаргачихъя (хүлээхгүйн тулд цагийг ухраана)
            c.created_at = datetime.utcnow() - timedelta(
                seconds=settings.barrier_reopen_cooldown_sec + 1)
        db.commit()
        ok3 = asyncio.run(ensure_entry_barrier(db, cam, "7777УБК"))
        db.commit()
        succ2 = (db.query(BarrierCommand)
                 .filter(BarrierCommand.device_id == bar_id,
                         BarrierCommand.status == "SUCCESS").count())
        check("cooldown дуусмагц шинэ SUCCESS команд явсан (SKIPPED саад болоогүй)",
              ok3 and succ2 == 2, f"ok={ok3} succ={succ2}")
    finally:
        db.rollback()
        db.query(BarrierCommand).filter(BarrierCommand.device_id == bar_id) \
            .delete(synchronize_session=False)
        db.query(Device).filter(Device.site_id == site_id).delete(synchronize_session=False)
        db.query(ParkingSite).filter(ParkingSite.id == site_id).delete(synchronize_session=False)
        db.commit()
        db.close()

    print(f"\n{PASS} ✓ / {FAIL} ✗")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
