#!/usr/bin/env python3
"""Камерын дотоод event санг (Snapshot Records метадата) унших/CSV болгох.

Камер SD-гүй ч дугаар/цаг/чиглэлийн бүртгэлээ дотоод санд хадгалдаг —
сервер унтарсан үед юу болсныг НӨХӨЖ харах, phantom машины аудит хийхэд.

Хэрэглээ:
    sudo /root/PARKING/backend/venv/bin/python /root/PARKING/tools/camera_event_log.py 10.0.106.10
    # сонголтууд:
    #   --hours 48          сүүлийн N цаг (default 24)
    #   --plate 9786        дугаарын хэсгээр шүүх (камер талдаа *9786*)
    #   --csv /tmp/ev.csv   CSV файлд хадгалах
    #   --user admin --password '...'   (.env/DB-ээс өөр нэвтрэлттэй камерт)
"""
import argparse
import asyncio
import csv
import os
import sys
from datetime import datetime, timedelta, timezone

os.chdir("/root/PARKING/backend")  # config env_file=".env" нь CWD-д харьцангуй
sys.path.insert(0, "/root/PARKING/backend")
from app.config import settings  # noqa: E402
from app.services.camera_records import fetch_snap_events, normalized_plate  # noqa: E402

COLS = ["time_local", "PlateNumber", "event_name", "SnapSource", "Category",
        "VehicleSign", "VehicleColor", "PlateColor", "Lane", "JunctionDirection",
        "RecNo", "time_utc"]


def resolve_creds(ip: str, args) -> tuple:
    if args.user and args.password:
        return args.user, args.password, "CLI"
    try:
        from app.database import SessionLocal
        from app.models import Device
        from app.services.device_auth import camera_credentials
        db = SessionLocal()
        try:
            dev = db.query(Device).filter(Device.ip_address == ip).first()
            if dev is not None and (getattr(dev, "username", None) or "").strip():
                u, p = camera_credentials(dev)
                return u, p, f"DB «{dev.name}»"
        finally:
            db.close()
    except Exception:
        pass
    return settings.camera_username, settings.camera_password, ".env"


async def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("ip")
    ap.add_argument("--hours", type=float, default=24)
    ap.add_argument("--plate", default=None)
    ap.add_argument("--csv", default=None)
    ap.add_argument("--user", default=None)
    ap.add_argument("--password", default=None)
    args = ap.parse_args()

    user, pwd, src = resolve_creds(args.ip, args)
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=args.hours)
    tz = timedelta(hours=settings.camera_tz_offset_hours)
    print(f"=== {args.ip} — event лог, сүүлийн {args.hours:g} цаг "
          f"(нэвтрэлт: {user}/{src}) ===")

    recs = await fetch_snap_events(args.ip, user, pwd, start, end, plate=args.plate)
    for r in recs:
        t = r.get("Time")
        r["time_local"] = ((datetime.fromtimestamp(t, tz=timezone.utc) + tz)
                           .strftime("%Y-%m-%d %H:%M:%S") if isinstance(t, (int, float)) else "")

    print(f"{'Локал цаг':19}  {'Дугаар':12}  {'Event':14}  {'Эх':7}  Марк/Төрөл")
    for r in recs:
        plate = r.get("PlateNumber") or ""
        mark = " ".join(x for x in (r.get("VehicleSign"), r.get("Category")) if x and x != "Unknown")
        print(f"{r['time_local']:19}  {plate:12}  {r['event_name']:14}  "
              f"{(r.get('SnapSource') or ''):7}  {mark}")

    plates = {normalized_plate(r) for r in recs} - {None}
    unread = sum(1 for r in recs if normalized_plate(r) is None)
    print(f"\nНийт {len(recs)} event, {len(plates)} өөр дугаар, "
          f"{unread} нь дугаар уншигдаагүй.")

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=COLS, extrasaction="ignore")
            w.writeheader()
            w.writerows(recs)
        print(f"CSV → {args.csv}")


if __name__ == "__main__":
    asyncio.run(main())
