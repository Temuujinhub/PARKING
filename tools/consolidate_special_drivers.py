#!/usr/bin/env python3
"""ХБИ (хөгжлийн бэрхшээлтэй иргэд)-ийн машиныг «Тусгай хэрэгцээт» болгож нэгтгэх.

Асуудал: ХБИ-ийн машид зогсоол бүрд ДАВХАРДАЖ бүртгэгдсэн (нэг дугаар 7-8 мөр).
Шийдэл: дугаар бүрд ТҮРЭЭСЛЭГЧ ТУС БҮРД ганц бүртгэл үлдээнэ —
contract_type=SPECIAL, site_id=NULL (= тухайн түрээслэгчийн БҮХ зогсоол,
find_registered-ийн all_sites_cond). Түрээслэгчийн тусгаарлалт хадгалагдана:
өөр түрээслэгчийн зогсоолд энэ бүртгэл ҮЙЛЧЛЭХГҮЙ — тэнд эрх өгөх бол тухайн
түрээслэгч дээр тусдаа мөр үүснэ (энэ tool одоо байгаа давхардлаас нь өөрөө
гаргаж ирнэ).

Хэрэглээ (эхлээд заавал dry-run — юу өөрчлөгдөхийг харна):
    sudo /root/PARKING/backend/venv/bin/python /root/PARKING/tools/consolidate_special_drivers.py
    # бодитоор гүйцэтгэх:
    sudo /root/PARKING/backend/venv/bin/python /root/PARKING/tools/consolidate_special_drivers.py --apply
    # өөр эзэмшигчийн нэрээр:  --owner "ХБИ"   (default)
    # эзэмшигч биш байгууллагаар: --company "дугаар"

Юу хийдэг:
  1. full_name = ХБИ (эсвэл --company таарсан) бүх бүртгэлийг (дугаар, түрээслэгч)
     хосоор бүлэглэнэ — мөрийн түрээслэгч = зогсоолынх нь tenant_id (зогсоолгүй
     мөрд өөрийнх нь tenant_id)
  2. Хос бүрд: хамгийн урт хугацаатай мөрийг SPECIAL/site NULL болгож үлдээнэ
     (valid_from=хамгийн эрт, valid_to=хамгийн орой, is_active=True)
  3. Бусад давхардлыг УСТГАНА
Аудитын логт CONSOLIDATE_SPECIAL гэж бичнэ.
"""
import argparse
import os
import sys
from collections import defaultdict

os.chdir("/root/PARKING/backend")
sys.path.insert(0, "/root/PARKING/backend")

from app.database import SessionLocal  # noqa: E402
from app.models import AuditLog, ParkingSite, RegisteredDriver  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--owner", default="ХБИ", help="Эзэмшигчийн нэр (default: ХБИ)")
    ap.add_argument("--company", default=None,
                    help="Нэрийн оронд байгууллагаар сонгох (ж: дугаар)")
    ap.add_argument("--apply", action="store_true",
                    help="Бодитоор өөрчлөх (өгөхгүй бол зөвхөн харуулна)")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        q = db.query(RegisteredDriver)
        if args.company:
            q = q.filter(RegisteredDriver.company.ilike(args.company.strip()))
            label = f"company≈{args.company!r}"
        else:
            q = q.filter(RegisteredDriver.full_name.ilike(args.owner.strip()))
            label = f"эзэмшигч≈{args.owner!r}"
        rows = q.order_by(RegisteredDriver.plate_number).all()
        if not rows:
            print(f"{label} бүртгэл олдсонгүй.")
            return

        site_tenant = dict(db.query(ParkingSite.id, ParkingSite.tenant_id).all())
        tenant_names = {}
        try:
            from app.models import Tenant
            tenant_names = dict(db.query(Tenant.id, Tenant.name).all())
        except Exception:  # noqa: BLE001 — Tenant model нэр өөр байвал id-гаар харуулна
            pass

        def row_tenant(r):
            """Мөрийн харьяа түрээслэгч: зогсоолынх нь tenant, зогсоолгүй бол өөрийнх."""
            return site_tenant.get(r.site_id) if r.site_id else r.tenant_id

        by_key: dict[tuple, list] = defaultdict(list)
        for r in rows:
            by_key[(r.plate_number, row_tenant(r))].append(r)

        total_del = 0
        already = 0
        plates = set()
        for (plate, tenant), group in sorted(by_key.items(), key=lambda kv: (kv[0][0], str(kv[0][1]))):
            plates.add(plate)
            keep = max(group, key=lambda r: (r.valid_to or r.created_at, r.created_at))
            dupes = [r for r in group if r.id != keep.id]
            changed = (keep.contract_type != "SPECIAL" or keep.site_id is not None
                       or keep.tenant_id != tenant or not keep.is_active)
            if not dupes and not changed:
                already += 1
                continue
            vf = min((r.valid_from for r in group if r.valid_from), default=keep.valid_from)
            vt = max((r.valid_to for r in group if r.valid_to), default=keep.valid_to)
            tname = tenant_names.get(tenant, tenant) if tenant else "түрээслэгчгүй зогсоолууд"
            print(f"  {plate} [{tname}]: {len(group)} мөр → 1 SPECIAL "
                  f"(түрээслэгчийн бүх зогсоол), {vf:%Y-%m-%d}–{vt:%Y-%m-%d}, "
                  f"устгах {len(dupes)}")
            total_del += len(dupes)
            if args.apply:
                keep.contract_type = "SPECIAL"
                keep.site_id = None
                keep.tenant_id = tenant
                keep.is_active = True
                keep.valid_from, keep.valid_to = vf, vt
                if not keep.full_name:
                    keep.full_name = args.owner
                for r in dupes:
                    db.delete(r)

        keep_cnt = len(by_key)
        print(f"\nНийт: {len(plates)} дугаар · {len(rows)} мөр байсан → {keep_cnt} мөр "
              f"үлдэнэ (дугаар×түрээслэгч тус бүрд 1; устгах {total_del}, "
              f"аль хэдийн зөв {already}).")
        if not args.apply:
            print("Энэ бол DRY-RUN — юу ч өөрчлөгдөөгүй. Бодитоор хийхдээ --apply нэмнэ.")
            return

        db.add(AuditLog(username="system", action="CONSOLIDATE_SPECIAL", entity="driver",
                        entity_id=None,
                        detail={"filter": label, "rows_before": len(rows),
                                "rows_after": keep_cnt, "plates": len(plates),
                                "deleted": total_del}))
        db.commit()
        print("✅ Гүйцэтгэлээ. Шалгах: Бүртгэлтэй машин хуудсанд төрөл="
              "«Тусгай хэрэгцээт», зогсоол=«Бүх зогсоол» болсон байна.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
