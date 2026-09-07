"""POS bootstrap + recent-exits эгнээний шүүлт — 2 орох + 2 гарах хаалттай зогсоол.

    cd backend && venv/bin/python tests/test_pos_bootstrap.py

Амьд Postgres шаардана (түр зогсоол/төхөөрөмж/session үүсгээд төгсгөлд устгана).
"""
import os
import sys
import uuid
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings  # noqa: E402

settings.barrier_mock = True
settings.snapshot_enabled = False
settings.screen_enabled = False

from app.database import SessionLocal  # noqa: E402
from app.models import Device, ParkingSession, ParkingSite, User  # noqa: E402
from app.routers import payments_router as pr  # noqa: E402
from app.routers import sessions_router as sr  # noqa: E402

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗ <<< FAIL'} {name}{'' if cond else f'  [{extra}]'}")


def main():
    db = SessionLocal()
    tag = uuid.uuid4().hex[:6]
    site = ParkingSite(name=f"TEST-POS-{tag}", site_code=f"tpos{tag}", zone_code="T")
    other = ParkingSite(name=f"TEST-POS2-{tag}", site_code=f"tpos2{tag}", zone_code="T")
    db.add_all([site, other]); db.flush()
    devs = []
    for no in (1, 2):
        for d in ("entry", "exit"):
            devs.append(Device(site_id=site.id, name=f"Кам {d}{no}", device_type="camera",
                               lane_no=no, lane_dir=d, device_key=f"k-{tag}-{d}{no}", ip_address="10.0.0.1"))
            devs.append(Device(site_id=site.id, name=f"Хаалт {d}{no}", device_type="barrier",
                               lane_no=no, lane_dir=d, ip_address="10.0.0.1"))
    devs.append(Device(site_id=other.id, name="Өөр хаалт", device_type="barrier", lane_no=1, lane_dir="exit"))
    term = Device(site_id=site.id, name="PAX", device_type="pax_terminal", device_key=f"PAX-{tag}", lane_no=1, lane_dir="exit")
    devs.append(term)
    db.add_all(devs); db.flush()
    cam_exit1 = next(d for d in devs if d.device_type == "camera" and d.lane_dir == "exit" and d.lane_no == 1)
    cam_exit2 = next(d for d in devs if d.device_type == "camera" and d.lane_dir == "exit" and d.lane_no == 2)
    bar_exit2 = next(d for d in devs if d.device_type == "barrier" and d.lane_dir == "exit" and d.lane_no == 2)
    now = datetime.utcnow()
    s1 = ParkingSession(site_id=site.id, plate_number=f"{tag[:4].upper()}ААА", entry_time=now, exit_time=now,
                        status="AWAITING_PAYMENT", exit_device_id=cam_exit1.id, total_fee=1000)
    s2 = ParkingSession(site_id=site.id, plate_number=f"{tag[:4].upper()}БББ", entry_time=now, exit_time=now,
                        status="AWAITING_PAYMENT", exit_device_id=cam_exit2.id, total_fee=1000)
    db.add_all([s1, s2]); db.commit()
    # OPERATOR: зөвхөн энэ зогсоол, free_exit эрхгүй → can_open false
    op = User(username=f"pos-{tag}", role="OPERATOR", password_hash="x", full_name="POS", site_ids=[site.id])
    op_fe = User(username=f"posfe-{tag}", role="OPERATOR", password_hash="x", full_name="POS",
                 site_ids=[site.id], permissions=["cashier", "check", "free_exit"])

    try:
        print("\nBootstrap:")
        r = pr.pos_bootstrap(terminal_id=term.device_key, db=db, user=op)
        check("зөвхөн өөрийн зогсоол", [s["id"] for s in r["sites"]] == [site.id], str([s["name"] for s in r["sites"]]))
        check("terminal → site", r["terminal"] and r["terminal"]["site_id"] == site.id, str(r["terminal"]))
        check("permissions бий", "cashier" in r["permissions"])
        st = r["sites"][0]
        check("4 эгнээ (2 орох + 2 гарах)", [(l["lane_dir"], l["lane_no"]) for l in st["lanes"]]
              == [("entry", 1), ("entry", 2), ("exit", 1), ("exit", 2)], str(st["lanes"]))
        check("эгнээ бүр хаалт+камертай", all(l["barrier_id"] and l["camera_id"] for l in st["lanes"]))
        ex2 = st["lanes"][3]
        check("гарах-2 эгнээ = Хаалт exit2 + Кам exit2",
              ex2["barrier_id"] == bar_exit2.id and ex2["camera_id"] == cam_exit2.id, str(ex2))
        check("free_exit-гүй → can_open=false", all(l["can_open"] is False for l in st["lanes"]))
        check("4 хаалт, 4 камер тусдаа жагсаалтад", len(st["barriers"]) == 4 and len(st["cameras"]) == 4)
        secret = {"device_key", "ip_address", "password", "username"}
        leak = [k for row in st["barriers"] + st["cameras"] for k in row if k in secret]
        check("нууц талбар (device_key/ip/нууц үг) задраагүй", not leak, str(leak))
        r2 = pr.pos_bootstrap(terminal_id=None, db=db, user=op_fe)
        check("free_exit → can_open=true", all(l["can_open"] for l in r2["sites"][0]["lanes"]))
        check("terminal_id өгөхгүй → terminal=null", r2["terminal"] is None)

        print("\nrecent-exits эгнээгээр:")
        rows = sr.recent_exits(site_id=site.id, minutes=30, db=db, user=op)
        mine = [x for x in rows if x["id"] in (s1.id, s2.id)]
        check("шүүлтгүй → 2 машин", len(mine) == 2, str(len(mine)))
        check("мөрөнд exit_lane_no/exit_device_name", all(x["exit_lane_no"] in (1, 2) and x["exit_device_name"] for x in mine), str(mine[0].keys()))
        rows = sr.recent_exits(site_id=site.id, minutes=30, lane_no=2, db=db, user=op)
        check("lane_no=2 → зөвхөн 2-р эгнээний машин", [x["id"] for x in rows if x["id"] in (s1.id, s2.id)] == [s2.id])
        rows = sr.recent_exits(site_id=site.id, minutes=30, device_id=bar_exit2.id, db=db, user=op)
        check("device_id=хаалт exit2 → ижил эгнээний камерын машин", [x["id"] for x in rows if x["id"] in (s1.id, s2.id)] == [s2.id])
        rows = sr.recent_exits(site_id=site.id, minutes=30, device_id=cam_exit1.id, db=db, user=op)
        check("device_id=камер exit1 → 1-р эгнээ", [x["id"] for x in rows if x["id"] in (s1.id, s2.id)] == [s1.id])
    finally:
        db.rollback()
        db.query(ParkingSession).filter(ParkingSession.id.in_([s1.id, s2.id])).delete(synchronize_session=False)
        db.query(Device).filter(Device.id.in_([d.id for d in devs])).delete(synchronize_session=False)
        db.query(ParkingSite).filter(ParkingSite.id.in_([site.id, other.id])).delete(synchronize_session=False)
        db.commit(); db.close()
    print(f"\n{PASS} ✓  {FAIL} ✗")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
