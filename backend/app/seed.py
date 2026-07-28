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

        # Хэрэглэгчид — нууц үгийг САНАМСАРГҮЙ үүсгэж НЭГ УДАА хэвлэнэ.
        #
        # Өмнө нь энд "Temuujin@2026" гэх мэт нууц үг ШУУД бичигдсэн байсан. Repo
        # нээлттэй болсноор тэдгээр нь олон нийтэд задарч, суулгасан бүр систем
        # SUPER_ADMIN эрхээрээ нээлттэй үлдэж байв (2026-07-28-нд илрүүлсэн).
        # Одоо кодод ямар ч нууц үг байхгүй — суулгагч гарсан утгыг тэмдэглэж авна.
        import secrets as _secrets

        def _pw() -> str:
            return _secrets.token_urlsafe(15)  # ~20 тэмдэгт, CSPRNG

        seeded = [
            ("temuujin", "Тэмүүжин (Super Admin)", "SUPER_ADMIN"),
            ("admin", "Системийн админ", "ADMIN"),
            ("sanhuu", "Санхүүгийн ажилтан", "FINANCE"),
            ("operator", "Зогсоолын оператор", "OPERATOR"),
        ]
        users, creds = [], []
        for username, full_name, role in seeded:
            pw = _pw()
            users.append(User(username=username, password_hash=hash_password(pw),
                              full_name=full_name, role=role))
            creds.append((username, role, pw))
        db.add_all(users)

        print("\n" + "=" * 66)
        print("АНХНЫ НУУЦ ҮГ — ЭНЭ МЭДЭЭЛЭЛ ДАХИН ХАРАГДАХГҮЙ, ОДОО ТЭМДЭГЛЭЖ АВНА УУ")
        print("=" * 66)
        for username, role, pw in creds:
            print(f"  {username:<10} {role:<12} {pw}")
        print("=" * 66)
        print("Нэвтэрсний дараа UI-аас нууц үгээ солино уу.")
        print("Дараа солих:  sudo venv/bin/python ../tools/set_password.py <хэрэглэгч>\n")

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
        print("Seed амжилттай. Нэвтрэх мэдээллийг ДЭЭР хэвлэсэн (дахин харагдахгүй).")
    finally:
        db.close()


if __name__ == "__main__":
    run()
