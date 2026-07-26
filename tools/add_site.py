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

BACKEND = "/root/PARKING/backend"
sys.path.insert(0, BACKEND)
# config.py-ийн env_file нь ".env" (CWD-д харьцангуй) тул backend хавтас руу шилжинэ —
# эс бол PUBLIC_BASE_URL/DB тохиргоо .env-ээс уншигдахгүй.
os.chdir(BACKEND)

from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import ParkingSite, TariffTemplate  # noqa: E402


def pay_url(site: ParkingSite) -> str:
    return f"{settings.public_base_url}/pay?site={site.site_code}"


def show(site: ParkingSite, prefix: str = "") -> None:
    cap = "хязгааргүй" if not site.capacity else f"{site.capacity} байр"
    tariff = site.tariff_template.name if site.tariff_template else "— (тариф холбоогүй)"
    state = "идэвхтэй" if site.is_active else "ИДЭВХГҮЙ"
    print(f"{prefix}{site.site_code:<12} {site.name}")
    print(f"{prefix}{'':12} багтаамж: {cap} · бүс: {site.zone_code} · тариф: {tariff} · {state}")
    if site.address:
        print(f"{prefix}{'':12} хаяг: {site.address}")
    print(f"{prefix}{'':12} QR линк: {pay_url(site)}")


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
    p.add_argument("--name", help="Зогсоолын нэр (жолоочид харагдана)")
    p.add_argument("--address", default=None, help="Хаяг / байршлын тайлбар")
    p.add_argument("--capacity", type=int, default=None, help="Багтаамж (0 = дүүргэлтгүй)")
    p.add_argument("--zone", default=None, help="Бүсийн код (default: A)")
    p.add_argument("--tariff", default=None, help="Тарифын загварын нэр (default: эхний идэвхтэй)")
    p.add_argument("--auto-close-hours", type=int, default=None,
                   help="Гацсан машины авто хаалтын босго, цагаар (0 = унтраах)")
    p.add_argument("--inactive", action="store_true", help="Идэвхгүй болгож бүртгэх")
    args = p.parse_args()

    db = SessionLocal()
    try:
        if args.list:
            return list_sites(db)

        if not args.code or not args.name:
            p.error("--code болон --name заавал (эсвэл --list ашиглана уу)")

        code = args.code.strip()
        # Кодыг том/жижиг үсгээс үл хамааран хайна — давхардал үүсгэхээс сэргийлнэ
        existing = next((s for s in db.query(ParkingSite).all()
                         if s.site_code.upper() == code.upper()), None)

        if existing:
            print(f"'{existing.site_code}' код аль хэдийн бүртгэлтэй — шинэчилж байна.\n")
            site = existing
        else:
            site = ParkingSite(site_code=code, name=args.name, zone_code="A", capacity=0)
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
