"""Рашбулаг ЭТТ — давхар (дамжин) зогсоолын тохиргоог хийнэ.

Эхлээд ХУУРАЙ ажиллуулна (юу ч өөрчлөгдөхгүй, зөвхөн харуулна):
    sudo /root/PARKING/backend/venv/bin/python ~/setup_nested.py
Бодитоор бичих:
    sudo /root/PARKING/backend/venv/bin/python ~/setup_nested.py --apply

Хийх зүйл:
  10.0.106.10  гадна орох   эгнээ 1 / Орох   (хэвээр, зөвхөн шалгана)
  10.0.106.11  гадна гарах  эгнээ 2 / Гарах  (хэвээр, зөвхөн шалгана)
  10.0.106.12  дотор орох   эгнээ 3 / Орох   + ДОТООД тэмдэг
  10.0.106.13  дотор гарах  эгнээ 4 / Гарах  + ДОТООД тэмдэг
Дараа нь доторх хаалтуудыг үүсгэж, зогсоолын «дамжин зогсох дээд хугацаа»-г тавина.
"""
import os
import sys

os.chdir("/root/PARKING/backend")
sys.path.insert(0, "/root/PARKING/backend")

from app.database import SessionLocal                    # noqa: E402
from app.models import Device, ParkingSite               # noqa: E402
from app.services.barrier import _resolve_device         # noqa: E402
from app.services.device_auto import ensure_lane_barriers  # noqa: E402
from app.session_logic import _find_barrier              # noqa: E402

APPLY = "--apply" in sys.argv
SITE_CODE = os.environ.get("SITE_CODE", "RASH")
TRANSIT_MAX_HOURS = int(os.environ.get("TRANSIT_MAX_HOURS", "12"))

# ip -> (эгнээ, чиглэл, дотоод_эсэх, тайлбар)
PLAN = {
    "10.0.106.10": (1, "entry", False, "гадна орох"),
    "10.0.106.11": (2, "exit",  False, "гадна гарах"),
    "10.0.106.12": (3, "entry", True,  "ДОТОР орох"),
    "10.0.106.13": (4, "exit",  True,  "ДОТОР гарах"),
}

db = SessionLocal()
try:
    site = db.query(ParkingSite).filter(ParkingSite.site_code == SITE_CODE).first()
    if site is None:
        sys.exit(f"«{SITE_CODE}» кодтой зогсоол олдсонгүй. SITE_CODE=... гэж зааж өгнө үү.")
    print(f"\nЗогсоол: {site.name} ({site.site_code})")
    print(f"Горим:   {'БОДИТ БИЧИЛТ (--apply)' if APPLY else 'ХУУРАЙ — юу ч өөрчлөгдөхгүй'}\n")

    cams = {c.ip_address: c for c in db.query(Device).filter(
        Device.site_id == site.id, Device.device_type == "camera",
        Device.status != "deleted").all() if c.ip_address}

    missing = [ip for ip in PLAN if ip not in cams]
    if missing:
        sys.exit(f"Энэ зогсоолд олдсонгүй: {', '.join(missing)}\n"
                 f"Байгаа камерууд: {', '.join(sorted(cams)) or '(алга)'}")
    extra = [ip for ip in cams if ip not in PLAN]
    if extra:
        print(f"  ℹ Төлөвлөгөөнд ороогүй камер: {', '.join(sorted(extra))} — хөндөхгүй\n")

    print(f"{'КАМЕР':<14} {'IP':<14} {'ОДОО':<18} {'БОЛОХ':<18} ӨӨРЧЛӨЛТ")
    print("─" * 88)
    changes = 0
    for ip, (lane, ldir, inner, label) in PLAN.items():
        c = cams[ip]
        now = f"{c.lane_no}/{c.lane_dir}{' дотоод' if c.nested_inner else ''}"
        new = f"{lane}/{ldir}{' дотоод' if inner else ''}"
        diff = (c.lane_no != lane or c.lane_dir != ldir or bool(c.nested_inner) != inner)
        print(f"{c.name[:13]:<14} {ip:<14} {now:<18} {new:<18} "
              f"{('ЗАСНА — ' + label) if diff else 'хэвээр'}")
        if diff:
            changes += 1
            c.lane_no, c.lane_dir, c.nested_inner = lane, ldir, inner

    if site.transit_max_hours != TRANSIT_MAX_HOURS:
        print(f"\nДамжин зогсох дээд хугацаа: {site.transit_max_hours} → {TRANSIT_MAX_HOURS} цаг")
        site.transit_max_hours = TRANSIT_MAX_HOURS
        changes += 1

    if not APPLY:
        db.rollback()
        print(f"\n{changes} өөрчлөлт хийгдэх байсан. Бодитоор бичихийн тулд --apply нэмнэ үү.")
        sys.exit(0)

    db.flush()
    res = ensure_lane_barriers(db)
    db.commit()
    print(f"\n✓ Хадгаллаа. Хаалт: {res['created']} шинээр, {res['restored']} сэргээв.")

    print("\n─── ШАЛГАЛТ: хаалт бүр аль камер руу команд явуулах вэ ───")
    ok = True
    for b in db.query(Device).filter(Device.site_id == site.id,
                                     Device.device_type == "barrier",
                                     Device.status == "active").order_by(Device.lane_no).all():
        bip, tgt = _resolve_device(db, b)
        tag = "ДОТООД" if b.nested_inner else "гадна "
        print(f"  [{tag}] {b.name:<26} эгнээ {b.lane_no}/{b.lane_dir:<6} → "
              f"{(tgt.name if tgt else '?'):<14} {bip or 'IP ОЛДСОНГҮЙ'}")
        if not bip:
            ok = False

    print("\n─── ШАЛГАЛТ: камер бүр аль хаалтаа нээх вэ ───")
    for ip, (_l, _d, inner, label) in PLAN.items():
        c = cams[ip]
        b = _find_barrier(db, site.id, c)
        good = b is not None and bool(b.nested_inner) == inner
        ok = ok and good
        print(f"  {'✓' if good else '✗'} {label:<12} {ip} → {b.name if b else 'ХААЛТ ОЛДСОНГҮЙ'}")

    print("\n" + ("✓ Тохиргоо БҮРЭН ЗӨВ." if ok else
                  "✗ Дутуу зүйл байна — дээрх ✗ мөрүүдийг шалгана уу."))
finally:
    db.close()
