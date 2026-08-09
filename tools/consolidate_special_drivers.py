#!/usr/bin/env python3
"""ХБИ (хөгжлийн бэрхшээлтэй иргэд)-ийн машиныг «Тусгай хэрэгцээт» болгож нэгтгэх.

Асуудал: ХБИ-ийн машид зогсоол бүрд ДАВХАРДАЖ бүртгэгдсэн (нэг дугаар 7-8 мөр).
Шийдэл: дугаар бүрд ГАНЦ бүртгэл үлдээнэ — contract_type=SPECIAL, site_id=NULL,
tenant_id=NULL. Ийм бүртгэл систем даяар (бүх зогсоол, түрээслэгч үл харгалзан)
үнэгүй нэвтрүүлдэг (session_logic.find_registered-ийн special_cond).

Хэрэглээ (эхлээд заавал dry-run — юу өөрчлөгдөхийг харна):
    sudo /root/PARKING/backend/venv/bin/python /root/PARKING/tools/consolidate_special_drivers.py
    # бодитоор гүйцэтгэх:
    sudo /root/PARKING/backend/venv/bin/python /root/PARKING/tools/consolidate_special_drivers.py --apply
    # өөр эзэмшигчийн нэрээр:  --owner "ХБИ"   (default)
    # эзэмшигч биш байгууллагаар: --company "дугаар"

Юу хийдэг:
  1. full_name = ХБИ (эсвэл --company таарсан) бүх бүртгэлийг дугаараар бүлэглэнэ
  2. Дугаар бүрд: хамгийн УРТ хугацаатай мөрийг SPECIAL/NULL/NULL болгож үлдээнэ
     (valid_from=хамгийн эрт, valid_to=хамгийн орой, is_active=True)
  3. Бусад давхардлыг УСТГАНА
Аудитын логт CONSOLIDATE_SPECIAL гэж бичнэ. Устгахын өмнө тоог харуулна.
"""
import argparse
import os
import sys
from collections import defaultdict

os.chdir("/root/PARKING/backend")
sys.path.insert(0, "/root/PARKING/backend")

from app.database import SessionLocal  # noqa: E402
from app.models import AuditLog, RegisteredDriver  # noqa: E402


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

        by_plate: dict[str, list] = defaultdict(list)
        for r in rows:
            by_plate[r.plate_number].append(r)

        total_del = 0
        already = 0
        for plate, group in sorted(by_plate.items()):
            # SPECIAL/NULL/NULL болгож үлдээх мөр: хамгийн оройтож дуусах нь
            keep = max(group, key=lambda r: (r.valid_to or r.created_at, r.created_at))
            dupes = [r for r in group if r.id != keep.id]
            changed = (keep.contract_type != "SPECIAL" or keep.site_id is not None
                       or keep.tenant_id is not None or not keep.is_active)
            if not dupes and not changed:
                already += 1
                continue
            vf = min((r.valid_from for r in group if r.valid_from), default=keep.valid_from)
            vt = max((r.valid_to for r in group if r.valid_to), default=keep.valid_to)
            print(f"  {plate}: {len(group)} мөр → 1 SPECIAL (бүх зогсоол), "
                  f"{vf:%Y-%m-%d}–{vt:%Y-%m-%d}, устгах {len(dupes)}")
            total_del += len(dupes)
            if args.apply:
                keep.contract_type = "SPECIAL"
                keep.site_id = None
                keep.tenant_id = None
                keep.is_active = True
                keep.valid_from, keep.valid_to = vf, vt
                if not keep.full_name:
                    keep.full_name = args.owner
                for r in dupes:
                    db.delete(r)

        print(f"\nНийт: {len(by_plate)} дугаар, {len(rows)} мөр байсан → "
              f"{len(by_plate)} мөр үлдэнэ (устгах {total_del}, аль хэдийн зөв {already}).")
        if not args.apply:
            print("Энэ бол DRY-RUN — юу ч өөрчлөгдөөгүй. Бодитоор хийхдээ --apply нэмнэ.")
            return

        db.add(AuditLog(username="system", action="CONSOLIDATE_SPECIAL", entity="driver",
                        entity_id=None,
                        detail={"filter": label, "rows_before": len(rows),
                                "plates": len(by_plate), "deleted": total_del}))
        db.commit()
        print("✅ Гүйцэтгэлээ. Шалгах: Бүртгэлтэй машин хуудсанд төрөл="
              "«Тусгай хэрэгцээт», зогсоол=«Бүх зогсоол» болсон байна.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
