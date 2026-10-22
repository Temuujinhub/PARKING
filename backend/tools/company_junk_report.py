"""Бүртгэлтэй машины «Байгууллага» талбарын ХОГ нэрсийн тайлан (харилцагчийн №52).

Excel импорт байгууллагын нэрийг хуудасны нэр / гарчгийн мөрөөс авдаг тул
«Улсын дугаар», «дугаар», «Байгууллага 1» (загвар файлын хуудас), «data»,
«эзэмшигч», «Sheet1» гэх мэт байгууллага БИШ утга орсон (прод 10-14: ~2,670).
Таних дүрэм нь app/services/company_names.is_junk_company — company-search
санал болгохгүй, гараар шинээр оруулахыг хориглодогтой ИЖИЛ.

Анхдагчаар ЗӨВХӨН ХАРУУЛНА (dry-run): утга × зогсоол × төрөл × идэвхтэй тоо.
--apply нь сонгосон хог утгыг ХООСОН ('') болгоно — тайланд «(байгууллагагүй)»
бүлэгт орно, гараар засахад гэрээт машинд жинхэнэ нэр нэхнэ. Өөр ямар ч талбарт
(дугаар, хугацаа, төрөл, идэвхтэй) ХҮРЭХГҮЙ; утга бүрт AuditLog мөр үлдээнэ.
Зөвхөн --value-ээр нэрлэсэн утгыг өөрчилнө (бүгдийг нэг дор биш).

    cd /root/PARKING/backend
    venv/bin/python tools/company_junk_report.py
    venv/bin/python tools/company_junk_report.py --value "Байгууллага 1" --value Sheet1 --apply
"""
import argparse
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import func  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models import AuditLog, ParkingSite, RegisteredDriver as D  # noqa: E402
from app.services.company_names import is_junk_company  # noqa: E402

TOOL = "company_junk_report"


def junk_rows(db):
    """[(company, site_name, contract_type, is_active, count)] — зөвхөн хог утгууд."""
    rows = (db.query(D.company, ParkingSite.name, D.contract_type, D.is_active, func.count(D.id))
            .outerjoin(ParkingSite, ParkingSite.id == D.site_id)
            .group_by(D.company, ParkingSite.name, D.contract_type, D.is_active).all())
    return sorted((r for r in rows if is_junk_company(r[0])), key=lambda r: (r[0], -r[4]))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--value", action="append", default=[],
                    help="--apply үед хоослох хог утга (давтаж болно, ЯГ таарна)")
    ap.add_argument("--apply", action="store_true", help="бичих (анхдагч: зөвхөн харуулах)")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        rows = junk_rows(db)
        per_value = defaultdict(int)
        print(f"{'Байгууллага (хог)':<22} {'Зогсоол':<26} {'Төрөл':<9} {'Идэвх':<6} {'Тоо':>6}")
        for comp, site, ct, active, n in rows:
            per_value[comp] += n
            print(f"{comp[:22]:<22} {(site or '— бүх зогсоол')[:26]:<26} {ct:<9} "
                  f"{'тийм' if active else 'үгүй':<6} {n:>6}")
        print("\nУтгаар нийт:")
        for comp, n in sorted(per_value.items(), key=lambda kv: -kv[1]):
            print(f"  {comp!r:<26} {n:>6}")
        print(f"  {'НИЙТ':<26} {sum(per_value.values()):>6}")

        if not args.apply:
            print("\n(dry-run) Хоослохын тулд: --value <утга> ... --apply")
            return
        targets = [v for v in args.value if v in per_value]
        skipped = [v for v in args.value if v not in per_value]
        if skipped:
            print(f"\nАЛГАСАВ (хог биш эсвэл олдсонгүй): {skipped}")
        if not targets:
            print("Хоослох утга алга — --value-ээр жагсаалтаас нэрлэнэ үү.")
            return
        for v in targets:
            n = (db.query(D).filter(D.company == v)
                 .update({D.company: ""}, synchronize_session=False))
            db.add(AuditLog(username=f"system({TOOL})", action="DRIVER_COMPANY_CLEAR",
                            entity="driver", entity_id="-",
                            detail={"company_before": v, "rows": n, "tool": TOOL}))
            print(f"  «{v}» → '' : {n} бүртгэл")
        db.commit()
        print("Хадгалагдлаа.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
