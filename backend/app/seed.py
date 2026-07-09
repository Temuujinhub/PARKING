"""Анхны өгөгдөл: super admin, туршилтын зогсоол, тариф, төхөөрөмж.

Ажиллуулах: venv/bin/python -m app.seed
"""
import secrets
from datetime import datetime, timedelta

from .auth import hash_password
from .database import Base, SessionLocal, engine
from .models import (
    Device, Discount, ParkingSite, RegisteredDriver, TariffTemplate, TariffTier, User,
)


def run():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        if db.query(User).count() > 0:
            print("Seed аль хэдийн хийгдсэн байна — алгаслаа.")
            return

        # Хэрэглэгчид
        users = [
            User(username="temuujin", password_hash=hash_password("Temuujin@2026"),
                 full_name="Тэмүүжин (Super Admin)", role="SUPER_ADMIN"),
            User(username="admin", password_hash=hash_password("Admin@2026"),
                 full_name="Системийн админ", role="ADMIN"),
            User(username="sanhuu", password_hash=hash_password("Sanhuu@2026"),
                 full_name="Санхүүгийн ажилтан", role="FINANCE"),
            User(username="operator", password_hash=hash_password("Operator@2026"),
                 full_name="Зогсоолын оператор", role="OPERATOR"),
        ]
        db.add_all(users)

        # Тарифын загвар (easy-park-ийн бүтэцтэй ижил: 60→1000, 120→2000, 180→5000)
        template = TariffTemplate(name="Үндсэн загвар", free_minutes=30, grace_minutes=15,
                                  prepaid_price=0, extra_hour_price=2000, daily_cap=25000)
        db.add(template)
        db.flush()
        db.add_all([
            TariffTier(template_id=template.id, upto_minutes=60, price=1000),
            TariffTier(template_id=template.id, upto_minutes=120, price=2000),
            TariffTier(template_id=template.id, upto_minutes=180, price=5000),
        ])

        # Зогсоол
        site = ParkingSite(name="Төв зогсоол", site_code="SITE01", zone_code="A",
                           address="Улаанбаатар", capacity=120, tariff_template_id=template.id)
        db.add(site)
        db.flush()

        # Төхөөрөмж (Dahua ANPR kit — орох/гарах камер + barrier)
        # device_key нь ХАЛДЛАГААС хамгаалах нууц — таамаглаж болдоггүй санамсаргүй утга
        # (өмнө нь "cam-entry-site01" гэх мэт таамаглаж болох утгатай байсан → аюулгүй биш).
        db.add_all([
            Device(site_id=site.id, name="Орох камер", device_type="camera",
                   model="ITC436-PW9H-IZ / IPMECS-2234-IZ", lane_no=1, lane_dir="entry",
                   auto_open=True, device_key=secrets.token_hex(16)),
            Device(site_id=site.id, name="Гарах камер", device_type="camera",
                   model="ITC436-PW9H-IZ / IPMECS-2234-IZ", lane_no=2, lane_dir="exit",
                   auto_open=False, device_key=secrets.token_hex(16)),
            Device(site_id=site.id, name="Орох хаалт", device_type="barrier",
                   model="DZBL-A / DZE-BL", lane_no=1, lane_dir="entry",
                   device_key=secrets.token_hex(16)),
            Device(site_id=site.id, name="Гарах хаалт", device_type="barrier",
                   model="DZBL-A / DZE-BL", lane_no=2, lane_dir="exit",
                   device_key=secrets.token_hex(16)),
        ])

        # Хөнгөлөлт
        db.add_all([
            Discount(name="Байгууллагын 20%", discount_type="PERCENT", value=20),
            Discount(name="1 цаг үнэгүй купон", discount_type="FREE_MINUTES", value=60),
        ])

        # Гэрээт жолооч (жишээ)
        db.add(RegisteredDriver(plate_number="0028УНИ", full_name="Жишээ гэрээт жолооч",
                                contract_type="MONTHLY", monthly_fee=150000,
                                valid_to=datetime.utcnow() + timedelta(days=365)))
        db.commit()
        print("Seed амжилттай:")
        print("  Super admin: temuujin / Temuujin@2026")
        print("  Admin:       admin / Admin@2026")
        print("  Санхүү:      sanhuu / Sanhuu@2026")
        print("  Оператор:    operator / Operator@2026")
    finally:
        db.close()


if __name__ == "__main__":
    run()
