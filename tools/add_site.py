#!/usr/bin/env python3
"""Зогсоол бүртгэх / засах — вэб UI-гүйгээр, сервер дээр шууд.

Хэвлэгдчихсэн QR кодтой зогсоолыг системд оруулахад зориулагдсан: QR доторх
`/pay?site=<КОД>`-ын КОД-ыг яг тэр хэвээр нь `--code`-оор өгнө.

Ажиллуулах (production сервер дээр):
    sudo /root/PARKING/backend/venv/bin/python /root/PARKING/tools/add_site.py --list

    sudo /root/PARKING/backend/venv/bin/python /root/PARKING/tools/add_site.py \
        --code SPORT --name "Спортын төв ордон" \
        --address "Спортын төв ордны баруун тал" --capacity 0

Онцлог:
  * Идемпотент — ижил `--code`-оор дахин ажиллуулбал алдаа өгөхгүй, зөвхөн шинэчилнэ.
  * `--capacity 0` = дүүргэлтгүй (хязгааргүй) зогсоол.
  * Тариф заагаагүй бол системд байгаа эхний идэвхтэй тарифыг холбоно.
  * Төгсгөлд нь жолоочийн төлбөрийн линк (QR-т кодлогдох ёстой URL)-ийг хэвлэнэ.
"""
import argparse
import os
import sys
import uuid

BACKEND = "/root/PARKING/backend"
sys.path.insert(0, BACKEND)
# config.py-ийн env_file нь ".env" (CWD-д харьцангуй) тул backend хавтас руу шилжинэ —
# эс бол PUBLIC_BASE_URL/DB тохиргоо .env-ээс уншигдахгүй.
os.chdir(BACKEND)

from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import ParkingSite, TariffTemplate  # noqa: E402


from app.serializers import site_pay_url  # noqa: E402


def pay_url(site: ParkingSite) -> str:
    return site_pay_url(site)


def show(site: ParkingSite, prefix: str = "") -> None:
    cap = "хязгааргүй" if not site.capacity else f"{site.capacity} байр"
    tariff = site.tariff_template.name if site.tariff_template else "— (тариф холбоогүй)"
    state = "идэвхтэй" if site.is_active else "ИДЭВХГҮЙ"
    print(f"{prefix}{site.site_code:<12} {site.name}")
    print(f"{prefix}{'':12} багтаамж: {cap} · бүс: {site.zone_code} · тариф: {tariff} · {state}")
    if site.address:
        print(f"{prefix}{'':12} хаяг: {site.address}")
    print(f"{prefix}{'':12} QR линк: {pay_url(site)}"
          f"{'  ← хэвлэгдсэн самбартай ижил' if site.qr_url else ''}")
    print(f"{prefix}{'':12} id-гаар: {settings.public_base_url}/checkout/{site.id}")
    if site.qpay_username and site.qpay_password:
        print(f"{prefix}{'':12} QPay данс: {site.qpay_username} "
              f"(нэхэмжлэх: {site.qpay_invoice_code or '—'}, "
              f"дүүрэг: {site.qpay_district_code or '—'}) — БОДИТ горим")
    elif site.qpay_username:
        print(f"{prefix}{'':12} QPay данс: {site.qpay_username} — НУУЦ ҮГ ДУТУУ, "
              "ерөнхий данс үйлчилнэ!")
    else:
        print(f"{prefix}{'':12} QPay данс: системийн ерөнхий")


def list_sites(db) -> int:
    sites = db.query(ParkingSite).order_by(ParkingSite.created_at).all()
    if not sites:
        print("Бүртгэлтэй зогсоол алга.")
        return 0
    print(f"Бүртгэлтэй {len(sites)} зогсоол:\n")
    for s in sites:
        show(s, prefix="  ")
        print()
    return 0


def pick_tariff(db, name: str | None) -> TariffTemplate | None:
    if name:
        t = db.query(TariffTemplate).filter(TariffTemplate.name == name).first()
        if not t:
            names = [x.name for x in db.query(TariffTemplate).all()]
            print(f"АЛДАА: '{name}' нэртэй тариф олдсонгүй. Байгаа тарифууд: {names}", file=sys.stderr)
            sys.exit(1)
        return t
    return (db.query(TariffTemplate)
            .filter(TariffTemplate.is_active.is_(True))
            .order_by(TariffTemplate.created_at).first())


def main() -> int:
    p = argparse.ArgumentParser(description="Зогсоол бүртгэх / засах")
    p.add_argument("--list", action="store_true", help="Бүртгэлтэй зогсоолуудыг харуулаад гарах")
    p.add_argument("--code", help="Зогсоолын код — QR доторх ?site= утга (ЯГ ижил байх ёстой)")
    p.add_argument("--id", dest="site_id", default=None,
                   help="Зогсоолын UUID — хэвлэгдсэн QR /checkout/<uuid> хэлбэртэй үед "
                        "тэр UUID-г ЯГ ижлээр өгнө (зөвхөн ШИНЭ зогсоолд)")
    p.add_argument("--name", help="Зогсоолын нэр (жолоочид харагдана)")
    p.add_argument("--address", default=None, help="Хаяг / байршлын тайлбар")
    p.add_argument("--capacity", type=int, default=None, help="Багтаамж (0 = дүүргэлтгүй)")
    p.add_argument("--zone", default=None, help="Бүсийн код (default: A)")
    p.add_argument("--tariff", default=None, help="Тарифын загварын нэр (default: эхний идэвхтэй)")
    p.add_argument("--auto-close-hours", type=int, default=None,
                   help="Гацсан машины авто хаалтын босго, цагаар (0 = унтраах)")
    p.add_argument("--qr-url", dest="qr_url", default=None,
                   help="Талбайд ХЭВЛЭГДСЭН самбар дээрх QR линк — систем үүсгэх QR "
                        "яг үүнтэй ижил болно ('' өгвөл цэвэрлэнэ)")
    p.add_argument("--qpay-username", dest="qpay_username", default=None,
                   help="Зогсоолын ӨӨРИЙН QPay нэвтрэх нэр (ж: MONNIS_PROPERTIES)")
    p.add_argument("--qpay-password", dest="qpay_password", default=None,
                   help="Зогсоолын ӨӨРИЙН QPay нууц үг ('' өгвөл цэвэрлэнэ)")
    p.add_argument("--qpay-invoice-code", dest="qpay_invoice_code", default=None,
                   help="Нэхэмжлэхийн код (ж: MONNIS_PROPERTIES_INVOICE)")
    p.add_argument("--qpay-district-code", dest="qpay_district_code", default=None,
                   help="НӨАТ-ын дүүрэг+хороо, 4 орон (ж: 2318)")
    p.add_argument("--qpay-branch-code", dest="qpay_branch_code", default=None,
                   help="Мерчантын салбарын код")
    p.add_argument("--inactive", action="store_true", help="Идэвхгүй болгож бүртгэх")
    args = p.parse_args()

    db = SessionLocal()
    try:
        if args.list:
            return list_sites(db)

        if not args.code or not args.name:
            p.error("--code болон --name заавал (эсвэл --list ашиглана уу)")

        code = args.code.strip()
        site_id = None
        if args.site_id:
            try:
                site_id = str(uuid.UUID(args.site_id.strip()))
            except ValueError:
                print(f"АЛДАА: --id '{args.site_id}' нь зөв UUID биш.", file=sys.stderr)
                return 1

        # Кодыг том/жижиг үсгээс үл хамааран хайна — давхардал үүсгэхээс сэргийлнэ
        existing = next((s for s in db.query(ParkingSite).all()
                         if s.site_code.upper() == code.upper()), None)
        if site_id and not existing:
            existing = db.get(ParkingSite, site_id)

        if existing:
            print(f"'{existing.site_code}' код аль хэдийн бүртгэлтэй — шинэчилж байна.\n")
            if site_id and existing.id != site_id:
                print(f"АНХААР: --id ({site_id}) нь бүртгэлтэй зогсоолын id "
                      f"({existing.id})-ээс өөр байна. id-г ДАРААХ ЗАСВАРЛААГҮЙ — "
                      "хэвлэгдсэн QR ажиллахгүй бол зогсоолыг устгаад шинээр "
                      "--id-тай нь үүсгэнэ үү.", file=sys.stderr)
            site = existing
        else:
            site = ParkingSite(site_code=code, name=args.name, zone_code="A", capacity=0)
            if site_id:
                site.id = site_id
            db.add(site)

        site.name = args.name
        if args.address is not None:
            site.address = args.address
        if args.capacity is not None:
            site.capacity = args.capacity
        if args.zone is not None:
            site.zone_code = args.zone
        if args.auto_close_hours is not None:
            site.auto_close_hours = args.auto_close_hours
        if args.qr_url is not None:
            site.qr_url = args.qr_url.strip() or None
        for f in ("qpay_username", "qpay_password", "qpay_invoice_code",
                  "qpay_district_code", "qpay_branch_code"):
            val = getattr(args, f)
            if val is None:
                continue
            val = val.strip() or None
            # Дүүргийн код = дүүрэг(2) + хороо(2). Буруу утга хадгалагдвал QPay
            # нэхэмжлэл үүсгэхээс татгалзана — жишээ текст ("XXXX") ороход
            # чимээгүй хадгалагдаж байсныг блоклоно.
            if f == "qpay_district_code" and val and not (val.isdigit() and len(val) == 4):
                print(f"АЛДАА: дүүргийн код 4 оронтой ТОО байх ёстой (жишээ 2318), "
                      f"'{val}' биш.\n       Цэвэрлэх бол: --qpay-district-code ''",
                      file=sys.stderr)
                return 1
            if f == "qpay_password":
                from app.secretbox import encrypt_secret
                val = encrypt_secret(val)  # DB-д ил бичихгүй
            setattr(site, f, val)
        site.is_active = not args.inactive

        tariff = pick_tariff(db, args.tariff)
        if tariff and (args.tariff or not site.tariff_template_id):
            site.tariff_template_id = tariff.id

        db.commit()
        db.refresh(site)

        print("Амжилттай хадгаллаа:\n")
        show(site, prefix="  ")
        print("\nДараагийн алхам:")
        print("  * Дээрх QR линкийг утсаараа нээж, зогсоолын нэр зөв гарч байгааг шалгана уу.")
        print("  * Хэвлэгдсэн QR өөр код агуулж байвал --code-оо тэр кодоор дахин ажиллуулна.")
        if not site.tariff_template:
            print("  * АНХААР: тариф холбогдоогүй — Тохиргоо → Тариф хэсгээс сонгоно уу.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
