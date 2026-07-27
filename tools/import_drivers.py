#!/usr/bin/env python3
"""Гэрээт машины жагсаалтыг Excel-ээс импортлох — сервер дээр шууд.

Excel-ийн БҮХ хуудсыг уншиж, хуудас бүрийг байгууллага гэж үзнэ.

Ажиллуулах:
    # Юу орохыг УРЬДЧИЛАН харах (DB хөндөхгүй)
    sudo /root/PARKING/backend/venv/bin/python /root/PARKING/tools/import_drivers.py \
        --site MONNIS --file "/root/PARKING/docs/Monnis_property/МБ гадна ... -last.xlsx" --dry-run

    # Бодитоор оруулах
    sudo .../import_drivers.py --site MONNIS --file "<зам>.xlsx"

    # Файлд байхгүй хуучин бүртгэлийг идэвхгүй болгож, жагсаалтыг бүрэн солих
    sudo .../import_drivers.py --site MONNIS --file "<зам>.xlsx" --replace

Идемпотент: ижил файлыг дахин импортлоход давхардал үүсэхгүй (дугаараар upsert).
"""
import argparse
import os
import sys
from collections import Counter

BACKEND = "/root/PARKING/backend"
sys.path.insert(0, BACKEND)
os.chdir(BACKEND)

from app.database import SessionLocal  # noqa: E402
from app.models import ParkingSite  # noqa: E402
from app.services.driver_import import import_rows, parse_workbook  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description="Гэрээт машины Excel импорт")
    p.add_argument("--file", required=True, help="Excel файлын зам (.xlsx)")
    p.add_argument("--site", help="Зогсоолын код (жишээ: MONNIS). Хоосон = бүх зогсоолд")
    p.add_argument("--dry-run", action="store_true", dest="dry_run",
                   help="Зөвхөн харуулна, DB хөндөхгүй")
    p.add_argument("--replace", action="store_true",
                   help="Файлд байхгүй хуучин бүртгэлийг идэвхгүй болгоно")
    p.add_argument("--contract-type", default="CONTRACT",
                   help="MONTHLY | CONTRACT | VIP | STAFF (default CONTRACT)")
    p.add_argument("--valid-days", type=int, default=365, help="Хүчинтэй хугацаа, хоногоор")
    p.add_argument("--monthly-fee", type=float, default=0, help="Сарын төлбөр (шинэ мөрд)")
    args = p.parse_args()

    if not os.path.isfile(args.file):
        print(f"АЛДАА: файл олдсонгүй: {args.file}", file=sys.stderr)
        return 1

    with open(args.file, "rb") as f:
        rows, warnings = parse_workbook(f.read())

    if not rows:
        print("АЛДАА: нэг ч дугаар уншигдсангүй. Файлын бүтцийг шалгана уу.", file=sys.stderr)
        for w in warnings:
            print("  !", w, file=sys.stderr)
        return 1

    print(f"Уншсан: {len(rows)} машин, {len(set(r['company'] for r in rows))} байгууллага\n")
    for company, n in sorted(Counter(r["company"] for r in rows).items()):
        print(f"  {n:4}  {company}")

    if warnings:
        print(f"\nАнхааруулга ({len(warnings)}):")
        for w in warnings:
            print("  !", w)

    db = SessionLocal()
    try:
        site_id = None
        if args.site:
            site = next((s for s in db.query(ParkingSite).all()
                         if s.site_code.upper() == args.site.strip().upper()), None)
            if not site:
                codes = [s.site_code for s in db.query(ParkingSite).all()]
                print(f"\nАЛДАА: '{args.site}' зогсоол олдсонгүй. Байгаа: {codes}", file=sys.stderr)
                return 1
            site_id = site.id
            print(f"\nЗогсоол: {site.name} ({site.site_code})")
        else:
            print("\nЗогсоол: (заагаагүй) — бүх зогсоолд хүчинтэй бүртгэл болно")

        if args.dry_run:
            print("\n--dry-run: DB хөндөөгүй. Жишээ 5 мөр:")
            for r in rows[:5]:
                print(f"  {r['plate']:10} {r['full_name'][:24]:26} {r['company']}")
            return 0

        res = import_rows(db, rows, site_id, contract_type=args.contract_type,
                          valid_days=args.valid_days, monthly_fee=args.monthly_fee,
                          deactivate_missing=args.replace)
        print(f"\nДууслаа: {res['created']} шинэ, {res['updated']} шинэчлэв"
              + (f", {res['deactivated']} идэвхгүй болгов" if res["deactivated"] else ""))
        print("Шалгах: UI → Бүртгэлтэй жолооч")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
