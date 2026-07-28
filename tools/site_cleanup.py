#!/usr/bin/env python
"""Нэг удаагийн цэвэрлэгээ — гацсан session/өрийг тухайн зогсоол дээр цэгцэлнэ.

    # Эхлээд ХУУРАЙ гүйлт (юу ч өөрчлөхгүй, юу хийхээ харуулна):
    sudo /root/PARKING/backend/venv/bin/python /root/PARKING/tools/site_cleanup.py --site MONNIS --since 2026-07-27

    # Бодитоор хэрэгжүүлэх:
    sudo ... site_cleanup.py --site MONNIS --since 2026-07-27 --apply

Юу хийдэг (бүгд ЗӨВХӨН заасан зогсоол дээр):
  1. --since (локал огноо)-оос хойш үүссэн, ЗӨВХӨН ОРОХ талд уншигдсан
     (гарах камерт огт харагдаагүй) идэвхтэй session-үүдийг ҮНЭГҮЙ (FREE) хаана —
     өр үүсгэхгүй. Эдгээр нь ихэвчлэн гараар оруулсан/уншилт алдагдсан phantom.
  2. --since-ээс хойш үүссэн БҮХ PENDING өрийг CANCELLED болгоно.
  3. Огноо харгалзахгүй: ИДЭВХТЭЙ гэрээт (RegisteredDriver) машины бүх PENDING
     өрийг CANCELLED болгоно — гэрээт машинаас төлбөр авдаггүй тул өр нь
     уншилт алдагдсанаас үүссэн артефакт.
Бүх өөрчлөлт AuditLog-д бичигдэнэ (username=cleanup).
"""
import argparse
import os
import sys
from datetime import datetime, timedelta

BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
sys.path.insert(0, BACKEND)
os.chdir(BACKEND)

from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import (AuditLog, Compensation, ParkingSession, ParkingSite,  # noqa: E402
                        RegisteredDriver)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", required=True, help="зогсоолын код (ж: MONNIS)")
    ap.add_argument("--since", required=True, help="локал огноо YYYY-MM-DD (энэ өдрийн 00:00-оос)")
    ap.add_argument("--apply", action="store_true", help="бодитоор өөрчлөх (үгүй бол зөвхөн харуулна)")
    args = ap.parse_args()

    local_midnight = datetime.strptime(args.since, "%Y-%m-%d")
    since_utc = local_midnight - timedelta(hours=settings.tz_offset_hours)
    now = datetime.utcnow()
    mode = "ХЭРЭГЖҮҮЛЖ БАЙНА" if args.apply else "ХУУРАЙ ГҮЙЛТ (юу ч өөрчлөхгүй)"

    db = SessionLocal()
    try:
        site = db.query(ParkingSite).filter(ParkingSite.site_code == args.site).first()
        if not site:
            print(f"✗ {args.site} кодтой зогсоол олдсонгүй")
            return 1
        print(f"═══ {site.name} ({args.site}) — {mode} ═══")
        print(f"Локал {args.since} 00:00 = UTC {since_utc} -оос хойшхи бүртгэлүүд\n")

        # 1. Зөвхөн орох талд уншигдсан идэвхтэй session-үүд → FREE
        sessions = (db.query(ParkingSession)
                    .filter(ParkingSession.site_id == site.id,
                            ParkingSession.status.in_(["OPEN", "AWAITING_PAYMENT"]),
                            ParkingSession.entry_time >= since_utc,
                            ParkingSession.exit_device_id.is_(None))
                    .order_by(ParkingSession.entry_time).all())
        print(f"1) Зөвхөн орох талд уншигдсан идэвхтэй session: {len(sessions)}")
        for s in sessions:
            local_in = s.entry_time + timedelta(hours=settings.tz_offset_hours)
            print(f"   {s.plate_number:<10} орсон {local_in:%m/%d %H:%M} ({s.status}) → FREE")
            if args.apply:
                s.status = "FREE"
                s.exit_time = now
                s.total_fee = s.total_fee or 0
                s.note = f"{s.note + ' | ' if s.note else ''}cleanup: зөвхөн орох уншилттай тул үнэгүй хаав"[:1000]
                db.add(AuditLog(username="cleanup", action="SESSION_FREE_CLOSE",
                                entity="session", entity_id=s.id,
                                detail={"plate": s.plate_number, "reason": "entry-only session"}))

        # 2. --since-ээс хойш үүссэн PENDING өрүүд → CANCELLED
        comps = (db.query(Compensation)
                 .filter(Compensation.site_id == site.id,
                         Compensation.status == "PENDING",
                         Compensation.created_at >= since_utc).all())
        print(f"\n2) {args.since}-оос хойш үүссэн PENDING өр: {len(comps)}")
        for c in comps:
            print(f"   {c.plate_number:<10} {float(c.amount):>8.0f}₮  ({c.reason}) → CANCELLED")
            if args.apply:
                c.status = "CANCELLED"
                db.add(AuditLog(username="cleanup", action="COMP_CANCEL", entity="compensation",
                                entity_id=c.id, detail={"plate": c.plate_number,
                                                        "amount": float(c.amount),
                                                        "reason": "bulk cleanup"}))

        # 3. Идэвхтэй гэрээт машины PENDING өр (огноо хамаарахгүй) → CANCELLED
        reg_plates = {r.plate_number for r in db.query(RegisteredDriver)
                      .filter(RegisteredDriver.is_active.is_(True),
                              RegisteredDriver.valid_from <= now,
                              RegisteredDriver.valid_to >= now,
                              (RegisteredDriver.site_id == site.id) |
                              (RegisteredDriver.site_id.is_(None))).all()}
        reg_comps = (db.query(Compensation)
                     .filter(Compensation.site_id == site.id,
                             Compensation.status == "PENDING",
                             Compensation.plate_number.in_(reg_plates)).all()) if reg_plates else []
        print(f"\n3) Гэрээт машины PENDING өр: {len(reg_comps)} (идэвхтэй гэрээт {len(reg_plates)} дугаар)")
        for c in reg_comps:
            print(f"   {c.plate_number:<10} {float(c.amount):>8.0f}₮  ({c.reason}) → CANCELLED")
            if args.apply:
                c.status = "CANCELLED"
                db.add(AuditLog(username="cleanup", action="COMP_CANCEL", entity="compensation",
                                entity_id=c.id, detail={"plate": c.plate_number,
                                                        "amount": float(c.amount),
                                                        "reason": "registered driver"}))

        if args.apply:
            db.commit()
            print(f"\n✓ Хэрэгжүүллээ: {len(sessions)} session FREE, "
                  f"{len(comps) + len(reg_comps)} өр CANCELLED (AuditLog-д бүртгэв)")
        else:
            print("\n(юу ч өөрчлөөгүй — бодитоор хийхдээ --apply нэмнэ)")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
