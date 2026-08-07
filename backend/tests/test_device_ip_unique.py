"""Камерын IP давхардлыг хадгалахгүй + байгаа давхардлыг анхааруулах.

    cd backend && venv/bin/python tests/test_device_ip_unique.py

Шалтгаан (2026-08-07 production): 10.0.111.12/.13 хос камер «Туушин» болон
«Номадс» гэсэн ХОЁР зогсоолд ӨӨР ӨӨР нэвтрэлтээр бүртгэгдсэн байв. Буруу нууц
үгтэй зогсоолын урсгал камерын remainLoginTimes-ыг шавхаж, камер өөрийгөө 300
секунд түгжсэн — тэр хугацаанд нөгөө зогсоолын ХААЛТ ч нээгдэхгүй болсон.
"""
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import HTTPException  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models import Device, ParkingSite, User  # noqa: E402
from app.routers.admin_router import (  # noqa: E402
    _assert_ip_free, _assert_lane_free, list_devices, list_sites)

PASS = FAIL = 0


def check(name, cond):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗ <<< FAIL'} {name}")


def raises(fn) -> str | None:
    """HTTPException шидвэл түүний detail-ыг, эс бол None буцаана."""
    try:
        fn()
    except HTTPException as e:
        return str(e.detail)
    return None


IP = "10.77.77.11"
IP2 = "10.77.77.12"
db = SessionLocal()
made: list = []

try:
    site_a = ParkingSite(id=str(uuid.uuid4()), name="ZZ-Туушин", site_code=f"ZZT{uuid.uuid4().hex[:6]}")
    site_b = ParkingSite(id=str(uuid.uuid4()), name="ZZ-Номадс", site_code=f"ZZN{uuid.uuid4().hex[:6]}")
    db.add_all([site_a, site_b])
    db.flush()
    made += [site_a, site_b]

    cam = Device(id=str(uuid.uuid4()), site_id=site_a.id, name="Орох", device_type="camera",
                 ip_address=IP, lane_no=1, lane_dir="entry", status="active")
    db.add(cam)
    db.flush()
    made.append(cam)

    print("\n1. Шинээр хадгалахыг зогсооно")
    err = raises(lambda: _assert_ip_free(db, IP, "camera"))
    check("өөр зогсоолд ижил IP-тэй камер → татгалзана", err is not None)
    check("алдаанд аль зогсоол/төхөөрөмжтэй мөргөлдсөн нь бичигдэнэ",
          bool(err) and "ZZ-Туушин" in err and "Орох" in err)
    check("алдаанд IP өөрөө бичигдэнэ", bool(err) and IP in err)

    print("\n2. Зөв тохиолдлууд саадгүй өнгөрнө")
    check("өөр IP → OK", raises(lambda: _assert_ip_free(db, IP2, "camera")) is None)
    check("IP хоосон → OK", raises(lambda: _assert_ip_free(db, "", "camera")) is None)
    check("IP None → OK", raises(lambda: _assert_ip_free(db, None, "camera")) is None)
    # all-in-one ITC: хаалт нь камерынхаа релеэр ажилладаг тул IP-г зориуд хуваалцана
    check("ижил IP-тэй ХААЛТ → OK (камерын реле)",
          raises(lambda: _assert_ip_free(db, IP, "barrier")) is None)
    check("өөрийгөө засахад → OK (exclude_id)",
          raises(lambda: _assert_ip_free(db, IP, "camera", exclude_id=cam.id)) is None)

    print("\n3. Устгагдсан бүртгэл замд саад болохгүй")
    cam.status = "deleted"
    db.flush()
    check("устгагдсан камерын IP-г дахин ашиглаж болно",
          raises(lambda: _assert_ip_free(db, IP, "camera")) is None)
    cam.status = "active"
    db.flush()

    print("\n4. Байгаа давхардлыг жагсаалтад анхааруулна")
    dup = Device(id=str(uuid.uuid4()), site_id=site_b.id, name="Орох камер",
                 device_type="camera", ip_address=IP, lane_no=1, lane_dir="entry",
                 status="active")
    db.add(dup)
    db.flush()
    made.append(dup)

    admin = User(id=str(uuid.uuid4()), username="zz-super", password_hash="x",
                 role="SUPER_ADMIN")
    rows = {r["id"]: r for r in list_devices(site_id=None, include_deleted=False,
                                             db=db, user=admin)}
    check("хоёулаа тэмдэглэгдэнэ",
          bool(rows.get(cam.id, {}).get("ip_conflict"))
          and bool(rows.get(dup.id, {}).get("ip_conflict")))
    note = rows.get(cam.id, {}).get("ip_conflict") or ""
    check("анхааруулгад нөгөө зогсоол/төхөөрөмжийн нэр орно",
          "ZZ-Номадс" in note and "Орох камер" in note)

    clean = [r for r in rows.values()
             if r["device_type"] == "camera" and r["id"] not in (cam.id, dup.id)
             and not any(o.ip_address == r["ip_address"] for o in (cam, dup))
             and not r.get("ip_conflict")]
    check("давхардалгүй камерт анхааруулга гарахгүй",
          all(r.get("ip_conflict") is None for r in clean))

    print("\n5. Нэг эгнээнд хоёр дахь төхөөрөмж — хадгалахгүй")
    # cam нь site_a-д 1/entry камер (дээр үүсгэсэн)
    err = raises(lambda: _assert_lane_free(db, site_a.id, "camera", 1, "entry"))
    check("ижил эгнээ+чиглэлд 2 дахь камер → татгалзана", err is not None)
    check("алдаанд байгаа төхөөрөмжийн нэр орно", bool(err) and "Орох" in err)
    check("өөр эгнээ → OK",
          raises(lambda: _assert_lane_free(db, site_a.id, "camera", 2, "entry")) is None)
    check("өөр чиглэл → OK",
          raises(lambda: _assert_lane_free(db, site_a.id, "camera", 1, "exit")) is None)
    # site_a-д 3/exit камер нэмээд, site_b-ийн МӨН 3/exit сул хэвээр эсэхийг шалгана
    # (зогсоол хооронд эгнээ бие даасан байх ёстой)
    cam3 = Device(id=str(uuid.uuid4()), site_id=site_a.id, name="Гарах 3", device_type="camera",
                  ip_address="10.77.77.33", lane_no=3, lane_dir="exit", status="active")
    db.add(cam3)
    db.flush()
    made.append(cam3)
    check("өөр зогсоолын ижил эгнээ → OK (эгнээ зогсоолын дотор бие даасан)",
          raises(lambda: _assert_lane_free(db, site_b.id, "camera", 3, "exit")) is None)
    check("ижил зогсоолын тэр эгнээ → татгалзана",
          raises(lambda: _assert_lane_free(db, site_a.id, "camera", 3, "exit")) is not None)
    check("ижил эгнээний ХААЛТ → OK (камераас өөр төрөл)",
          raises(lambda: _assert_lane_free(db, site_a.id, "barrier", 1, "entry")) is None)
    check("өөрийгөө засахад → OK",
          raises(lambda: _assert_lane_free(db, site_a.id, "camera", 1, "entry",
                                           exclude_id=cam.id)) is None)

    lane_dup = Device(id=str(uuid.uuid4()), site_id=site_a.id, name="Орох 2",
                      device_type="camera", ip_address="10.77.77.99", lane_no=1,
                      lane_dir="entry", status="active")
    db.add(lane_dup)
    db.flush()
    made.append(lane_dup)
    rows = {r["id"]: r for r in list_devices(site_id=None, include_deleted=False,
                                             db=db, user=admin)}
    note = rows.get(lane_dup.id, {}).get("ip_conflict") or ""
    check("эгнээний давхцал жагсаалтад анхааруулга болж гарна", "эгнээний" in note)
    check("анхааруулгад нөгөө төхөөрөмжийн нэр орно", "Орох" in note)

    print("\n6. Зогсоолын ижил нэр — анхааруулна (хадгалахыг зогсоохгүй)")
    twin = ParkingSite(id=str(uuid.uuid4()), name="ZZ-Туушин",
                       site_code=f"ZZX{uuid.uuid4().hex[:6]}")
    db.add(twin)
    db.flush()
    made.append(twin)
    sites = {s["id"]: s for s in list_sites(db=db, user=admin)}
    check("ижил нэртэй хоёр зогсоол хоёулаа тэмдэглэгдэнэ",
          bool(sites.get(site_a.id, {}).get("name_conflict"))
          and bool(sites.get(twin.id, {}).get("name_conflict")))
    check("ялгаатай нэртэй зогсоолд анхааруулга гарахгүй",
          sites.get(site_b.id, {}).get("name_conflict") is None)

finally:
    for obj in reversed(made):
        db.delete(obj)
    db.commit()
    db.close()

print(f"\n{'='*50}\nPASS={PASS} FAIL={FAIL}")
sys.exit(1 if FAIL else 0)
