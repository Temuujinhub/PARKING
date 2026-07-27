"""Гэрээт машин ОРСНЫ ДАРАА бүртгэгдсэн ч шууд үнэгүй гарах эсэх.

    cd backend && venv/bin/python tests/test_registered_live.py

Бодит алдаа (production, 2026-07-27): 5364УЕН нь Моннисын гэрээт жагсаалтад
байсан атлаа 5000₮ нэхэмжилж байв. Шалтгаан: `is_registered` нь ОРОХ үед
тогтоогдож session дээр хөлддөг байсан — машин 09:43-д орсон, жагсаалт
11:44-д импортлогдсон тул хөлдсөн утга false хэвээр үлдсэн.

Шаардлага: гэрээт жагсаалтад орсон машин ЯМАР Ч ГАР АЖИЛЛАГААГҮЙ шууд гарна.

Шалгах зүйл:
  - Орохдоо бүртгэлгүй байсан ч дараа нь бүртгэгдвэл төлбөр 0 болно
  - Session дээрх тэмдэг өөрөө засагдана (жагсаалтад "Гэрээт" гэж харагдана)
  - Хаалттай/төлөгдсөн session-ий түүхэн дүн ӨӨРЧЛӨГДӨХГҮЙ (санхүү эвдэрхгүй)
  - Бүртгэлгүй машин хэвээрээ төлбөртэй
  - Хугацаа дууссан/идэвхгүй бүртгэл үнэгүй болгохгүй
"""
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal  # noqa: E402
from app.models import (ParkingSession, ParkingSite, RegisteredDriver,  # noqa: E402
                        TariffTemplate, TariffTier)
from app.session_logic import session_fee_info  # noqa: E402

PASS = FAIL = 0


def check(name, cond):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗ <<< FAIL'} {name}")


CODE = "ZZREGLIVE"
PLATE = "9999ЗЗЗ"
db = SessionLocal()

# ── Цэвэрлэгээ + бэлтгэл ──
old = db.query(ParkingSite).filter(ParkingSite.site_code == CODE).first()
if old:
    db.query(ParkingSession).filter(ParkingSession.site_id == old.id).delete()
    db.query(RegisteredDriver).filter(RegisteredDriver.site_id == old.id).delete()
    db.delete(old)
    db.commit()

tpl = TariffTemplate(name="ZZ тест тариф", free_minutes=0, grace_minutes=15)
db.add(tpl)
db.flush()
db.add(TariffTier(template_id=tpl.id, upto_minutes=60, price=1000))
db.add(TariffTier(template_id=tpl.id, upto_minutes=120, price=2000))
site = ParkingSite(name="ZZ тест", site_code=CODE, zone_code="A", capacity=0,
                   tariff_template_id=tpl.id)
db.add(site)
db.commit()

now = datetime.utcnow()
sess = ParkingSession(site_id=site.id, plate_number=PLATE,
                      entry_time=now - timedelta(hours=2), status="OPEN",
                      is_registered=False)   # орохдоо бүртгэлгүй байсан
db.add(sess)
db.commit()

try:
    print("Орохдоо бүртгэлгүй байсан машин:")
    fee = session_fee_info(db, sess)
    check("төлбөртэй (2 цаг → 2000₮)", fee["total_fee"] > 0 and not fee["is_free"])

    print("\nОдоо гэрээт жагсаалтад нэмэгдэв (машин зогсоолд байсаар):")
    drv = RegisteredDriver(plate_number=PLATE, full_name="Тест эзэмшигч",
                           company="ZZ ХХК", contract_type="CONTRACT",
                           site_id=site.id, valid_from=now, valid_to=now + timedelta(days=30),
                           is_active=True)
    db.add(drv)
    db.commit()

    fee = session_fee_info(db, sess)
    check("төлбөр 0 болов", fee["total_fee"] == 0)
    check("is_free=True → хаалт авто нээгдэнэ", fee["is_free"] is True)
    check("шалтгаан 'Бүртгэлтэй жолооч'", fee["reason"] == "Бүртгэлтэй жолооч")
    check("session дээрх тэмдэг өөрөө засагдсан", sess.is_registered is True)

    print("\nХаагдсан session-ий түүх ӨӨРЧЛӨГДӨХГҮЙ (санхүү хамгаалагдана):")
    closed = ParkingSession(site_id=site.id, plate_number=PLATE + "1",
                            entry_time=now - timedelta(hours=3),
                            exit_time=now - timedelta(hours=1),
                            status="CLOSED", is_registered=False)
    db.add(closed)
    db.commit()
    db.add(RegisteredDriver(plate_number=PLATE + "1", contract_type="CONTRACT",
                            site_id=site.id, valid_from=now,
                            valid_to=now + timedelta(days=30), is_active=True))
    db.commit()
    fee_closed = session_fee_info(db, closed)
    check("хаагдсан session төлбөртэй хэвээр", fee_closed["total_fee"] > 0)
    check("тэмдэг нь хөндөгдөөгүй", closed.is_registered is False)

    print("\nБүртгэлгүй машин:")
    other = ParkingSession(site_id=site.id, plate_number="1111ЗЗЗ",
                           entry_time=now - timedelta(hours=2), status="OPEN",
                           is_registered=False)
    db.add(other)
    db.commit()
    check("төлбөртэй хэвээр", session_fee_info(db, other)["total_fee"] > 0)

    print("\nХугацаа дууссан бүртгэл:")
    expired = ParkingSession(site_id=site.id, plate_number="2222ЗЗЗ",
                             entry_time=now - timedelta(hours=2), status="OPEN",
                             is_registered=False)
    db.add(expired)
    db.add(RegisteredDriver(plate_number="2222ЗЗЗ", contract_type="CONTRACT",
                            site_id=site.id, valid_from=now - timedelta(days=60),
                            valid_to=now - timedelta(days=1), is_active=True))
    db.commit()
    check("хугацаа дууссан бол үнэгүй болгохгүй",
          session_fee_info(db, expired)["total_fee"] > 0)

    print("\nИдэвхгүй болгосон бүртгэл:")
    inact = ParkingSession(site_id=site.id, plate_number="3333ЗЗЗ",
                           entry_time=now - timedelta(hours=2), status="OPEN",
                           is_registered=False)
    db.add(inact)
    db.add(RegisteredDriver(plate_number="3333ЗЗЗ", contract_type="CONTRACT",
                            site_id=site.id, valid_from=now,
                            valid_to=now + timedelta(days=30), is_active=False))
    db.commit()
    check("идэвхгүй бүртгэл үнэгүй болгохгүй",
          session_fee_info(db, inact)["total_fee"] > 0)

    print("\nDB-гүй дуудалт (тестийн fake объект) эвдрэхгүй:")
    fee_nodb = session_fee_info(None, sess)
    check("db=None үед ч ажиллана", fee_nodb["is_free"] is True)
finally:
    db.query(ParkingSession).filter(ParkingSession.site_id == site.id).delete()
    db.query(RegisteredDriver).filter(RegisteredDriver.site_id == site.id).delete()
    db.delete(site)
    db.commit()   # зогсоол устсаны ДАРАА л тарифыг устгаж болно (FK)
    db.query(TariffTier).filter(TariffTier.template_id == tpl.id).delete()
    db.query(TariffTemplate).filter(TariffTemplate.id == tpl.id).delete()
    db.commit()
    db.close()

print(f"\n{PASS} PASS, {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
