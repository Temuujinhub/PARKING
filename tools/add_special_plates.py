#!/usr/bin/env python3
"""Онцгой дугаарын машиныг (ДК/дипломат, түргэн, гал команд г.м) SPECIAL-аар бүртгэх.

Түрээслэгчийн БҮХ зогсоолд үнэгүй нэвтэрдэг «Тусгай хэрэгцээт» бүртгэл үүсгэнэ
(contract_type=SPECIAL, site_id=NULL, tenant_id=түрээслэгч). Идемпотент:
(дугаар × түрээслэгч)-ийн SPECIAL бүртгэл аль хэдийн байвал хугацааг нь л сунгана.

Хэрэглээ (эхлээд dry-run, дараа нь --apply):
    # Дугааруудыг шууд өгөх:
    sudo /root/PARKING/backend/venv/bin/python /root/PARKING/tools/add_special_plates.py \
        --tenant Ийзи --name "Дипломат корпус" --plates 1404ДК,1416ДК,9942ДК --apply

    # Файлаас (мөр бүрд нэг дугаар; таслалаар "дугаар,нэр" ч болно):
    sudo /root/PARKING/backend/venv/bin/python /root/PARKING/tools/add_special_plates.py \
        --tenant Ийзи --name "Түргэн тусламж" --file /tmp/turgen.txt --apply

Сонголтууд:
    --tenant <нэрийн хэсэг>   Түрээслэгч (нэрээр хайна; олон таарвал жагсааж зогсоно)
    --name  <эзэмшигч>        full_name талбар (ж: "Дипломат корпус", "Гал команд")
    --company <байгууллага>   company талбар (default: --name-тэй ижил)
    --years N                 Хүчинтэй жил (default 10)
"""
import argparse
import os
import sys
from datetime import datetime, timedelta

os.chdir("/root/PARKING/backend")
sys.path.insert(0, "/root/PARKING/backend")

from app.database import SessionLocal  # noqa: E402
from app.models import AuditLog, RegisteredDriver, Tenant  # noqa: E402


def read_plates(args) -> list[tuple[str, str | None]]:
    """(дугаар, нэр|None) жагсаалт — CLI --plates эсвэл --file-ээс."""
    items: list[tuple[str, str | None]] = []
    if args.plates:
        for p in args.plates.split(","):
            p = p.strip()
            if p:
                items.append((p.upper().replace(" ", ""), None))
    if args.file:
        for line in open(args.file, encoding="utf-8-sig"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [x.strip() for x in line.split(",")]
            plate = parts[0].upper().replace(" ", "")
            name = parts[1] if len(parts) > 1 and parts[1] else None
            if plate:
                items.append((plate, name))
    # давхардлыг арилгана (сүүлийн нэр нь үлдэнэ)
    dedup: dict[str, str | None] = {}
    for plate, name in items:
        dedup[plate] = name or dedup.get(plate)
    return sorted(dedup.items())


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tenant", required=True, help="Түрээслэгчийн нэрийн хэсэг (ж: Ийзи)")
    ap.add_argument("--name", required=True, help='Эзэмшигчийн нэр (ж: "Дипломат корпус")')
    ap.add_argument("--company", default=None, help="Байгууллага (default: --name)")
    ap.add_argument("--plates", default=None, help="Таслалаар тусгаарласан дугаарууд")
    ap.add_argument("--file", default=None, help="Файл: мөр бүрд дугаар[,нэр]")
    ap.add_argument("--years", type=int, default=10, help="Хүчинтэй жил (default 10)")
    ap.add_argument("--apply", action="store_true", help="Бодитоор бүртгэх")
    args = ap.parse_args()

    plates = read_plates(args)
    if not plates:
        print("Дугаар өгөөгүй байна — --plates эсвэл --file ашиглана уу.")
        sys.exit(1)

    db = SessionLocal()
    try:
        tenants = (db.query(Tenant).filter(Tenant.name.ilike(f"%{args.tenant.strip()}%"))
                   .order_by(Tenant.name).all())
        if len(tenants) != 1:
            print(f"--tenant {args.tenant!r} гэхэд {len(tenants)} түрээслэгч таарлаа:")
            for t in db.query(Tenant).order_by(Tenant.name).all():
                print(f"  • {t.name}")
            print("Нэрийг ганц таарахаар тодруулна уу.")
            sys.exit(1)
        tenant = tenants[0]
        company = args.company or args.name
        now = datetime.utcnow()
        until = now + timedelta(days=365 * args.years)

        created = updated = 0
        for plate, name in plates:
            existing = (db.query(RegisteredDriver)
                        .filter(RegisteredDriver.plate_number == plate,
                                RegisteredDriver.tenant_id == tenant.id,
                                RegisteredDriver.site_id.is_(None),
                                RegisteredDriver.contract_type == "SPECIAL")
                        .first())
            if existing:
                print(f"  {plate}: байна — хугацааг {until:%Y-%m-%d} болтол сунгана")
                updated += 1
                if args.apply:
                    existing.is_active = True
                    existing.valid_to = max(existing.valid_to or until, until)
                    if name:
                        existing.full_name = name
                continue
            print(f"  {plate}: ШИНЭЭР бүртгэнэ ({name or args.name}, {until:%Y-%m-%d} хүртэл)")
            created += 1
            if args.apply:
                db.add(RegisteredDriver(
                    plate_number=plate, full_name=name or args.name, company=company,
                    contract_type="SPECIAL", site_id=None, tenant_id=tenant.id,
                    monthly_fee=0, valid_from=now, valid_to=until, is_active=True,
                    note="онцгой дугаар (add_special_plates)"))

        print(f"\nТүрээслэгч: {tenant.name} · шинэ {created}, сунгах {updated}, нийт {len(plates)}")
        if not args.apply:
            print("Энэ бол DRY-RUN — юу ч өөрчлөгдөөгүй. Бодитоор хийхдээ --apply нэмнэ.")
            return
        db.add(AuditLog(username="system", action="ADD_SPECIAL_PLATES", entity="driver",
                        entity_id=None,
                        detail={"tenant": tenant.name, "name": args.name,
                                "created": created, "updated": updated}))
        db.commit()
        print("✅ Бүртгэгдлээ — эдгээр дугаар түрээслэгчийн бүх зогсоолд үнэгүй нэвтэрнэ.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
