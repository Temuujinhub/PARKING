"""log_tail нэг мөчлөгт хуримтлагдсан уншилтуудыг ХАЯХГҮЙ — дараалан боловсруулна.

2026-08-29 аудит (A4): өмнө нь мөчлөг тутам зөвхөн ХАМГИЙН СҮҮЛИЙН уншилтыг
боловсруулж, өмнөхийг нь _seen-д тэмдэглээд хаядаг байв — 20 секундэд 2+ машин
ирвэл эхнийх нь session ч, төлбөр ч үгүй алга болно. Одоо:
  • бүх шинэхэн уншилт боловсруулагдана (session бүрдээ үүснэ)
  • ХААЛТ зөвхөн хамгийн сүүлийн уншилтад нээгдэнэ (хуучин дүрэм хэвээр)
  • burst_merge=False — өөр машинуудыг «нэг машин» гэж нийлүүлэхгүй
  • _MAX_PER_CYCLE-ээс их бол илүүдэл нь ХАЯГДАХГҮЙ, дараагийн мөчлөгт үлдэнэ

Амьд Postgres шаардана (тестийн өгөгдөл ZZ- угтвартай, өөрөө цэвэрлэнэ).

    cd backend && venv/bin/python tests/test_log_tail_queue.py
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
from app.models import (AuditLog, BarrierCommand, Device, LprEvent,  # noqa: E402
                        ParkingSession, ParkingSite)
from app.services import log_tail  # noqa: E402
from app.services.camera_records import to_camera_epoch  # noqa: E402

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗ <<< FAIL'} {name}{'' if cond else f'  [{extra}]'}")


def _rec(plate, at_utc):
    return {"PlateNumber": plate,
            "Time": to_camera_epoch(at_utc.replace(tzinfo=timezone.utc)),
            "Event": 34, "event_name": "TrafficTollGate"}


def main():
    db = SessionLocal()
    tag = uuid.uuid4().hex[:6]
    site = ParkingSite(name=f"ZZ-Дараалал-{tag}", site_code=f"ZZQ{tag}", is_active=True)
    db.add(site)
    db.flush()
    cam = Device(site_id=site.id, name="Чимээгүй орох", device_type="camera",
                 lane_dir="entry", lane_no=1, ip_address=f"10.8.{int(tag[:2], 16) % 250}.10",
                 status="active")
    bar = Device(site_id=site.id, name="Орох хаалт", device_type="barrier",
                 lane_dir="entry", lane_no=1, status="active")
    db.add_all([cam, bar])
    db.commit()
    site_id, cam_id, bar_id = site.id, cam.id, bar.id

    batch: list = []

    async def fake_fetch(ip, u, p, start, end, plate=None, client=None):
        return list(batch)

    log_tail.fetch_snap_events = fake_fetch
    log_tail._seen.clear()
    log_tail._last_pull.clear()

    try:
        # Анхны мөчлөг — зөвхөн тэмдэглэнэ (хуучин лог асгарахгүй)
        asyncio.run(log_tail.run_once(site_id))

        # ── Нэг мөчлөгт 3 ӨӨР машин ────────────────────────────────────────
        now = datetime.utcnow()
        batch = [_rec("1111УБА", now - timedelta(seconds=50)),
                 _rec("2222УБН", now - timedelta(seconds=30)),
                 _rec("3333УБХ", now - timedelta(seconds=10))]
        res = asyncio.run(log_tail.run_once(site_id))
        print("\nНэг мөчлөгт 3 машин — бүгд бүртгэгдэнэ:")
        check("3 уншилт бүгд сэргэсэн (өмнө нь 1 л байсан)",
              res["recovered"] == 3, str(res))
        plates = {"1111УБА", "2222УБН", "3333УБХ"}
        sess = {s.plate_number for s in db.query(ParkingSession)
                .filter(ParkingSession.site_id == site_id).all()}
        check("3 машин бүрдээ ТУСДАА session-тэй", plates <= sess, str(sess))
        auto = db.query(AuditLog).filter(AuditLog.action == "PLATE_AUTOCORRECT",
                                         AuditLog.entity == "session").all()
        merged = [a for a in auto if a.detail and a.detail.get("new") in plates]
        check("burst нэгтгэл ажиллаагүй (дугаар дараагүй)", not merged,
              str([a.detail for a in merged]))
        opens = (db.query(BarrierCommand)
                 .filter(BarrierCommand.device_id == bar_id,
                         BarrierCommand.status == "SUCCESS").count())
        check("хаалт зөвхөн ХАМГИЙН СҮҮЛИЙН уншилтад нээгдсэн", opens == 1, str(opens))
        ev = {e.plate_number: e.raw for e in db.query(LprEvent)
              .filter(LprEvent.site_id == site_id).all()}
        check("сүүлийн уншилтад opened=true", ev.get("3333УБХ", {}).get("opened") is True,
              str(ev.get("3333УБХ")))
        check("өмнөх уншилтад opened=false", ev.get("1111УБА", {}).get("opened") is False,
              str(ev.get("1111УБА")))

        # ── Runaway хамгаалалт: илүүдэл ХАЯГДАХГҮЙ ─────────────────────────
        now = datetime.utcnow()
        batch = [_rec(f"{4000 + i}УНА", now - timedelta(seconds=120 - i)) for i in range(14)]
        res2 = asyncio.run(log_tail.run_once(site_id))
        res3 = asyncio.run(log_tail.run_once(site_id))
        print("\n_MAX_PER_CYCLE-ээс их (14) — хоёр мөчлөгт бүгд гүйцнэ:")
        check(f"1-р мөчлөгт {log_tail._MAX_PER_CYCLE}",
              res2["recovered"] == log_tail._MAX_PER_CYCLE, str(res2))
        check("2-р мөчлөгт үлдсэн 4 (хаягдаагүй!)", res3["recovered"] == 4, str(res3))
    finally:
        db.rollback()
        # тестийн өгөгдлөө цэвэрлэнэ
        sq = db.query(ParkingSession.id).filter(ParkingSession.site_id == site_id)
        db.query(BarrierCommand).filter(
            (BarrierCommand.device_id.in_((cam_id, bar_id)))
            | (BarrierCommand.session_id.in_(sq.subquery()))).delete(synchronize_session=False)
        db.query(LprEvent).filter(LprEvent.site_id == site_id).delete(synchronize_session=False)
        db.query(ParkingSession).filter(ParkingSession.site_id == site_id) \
            .delete(synchronize_session=False)
        db.query(Device).filter(Device.site_id == site_id).delete(synchronize_session=False)
        db.query(ParkingSite).filter(ParkingSite.id == site_id).delete(synchronize_session=False)
        db.commit()
        db.close()

    print(f"\n{PASS} ✓ / {FAIL} ✗")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
