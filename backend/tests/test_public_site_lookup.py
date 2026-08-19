"""QR-аас ирэх зогсоолын кодын хайлт — том/жижиг үсэг, хоосон зайд тэсвэртэй эсэх.

    cd backend && venv/bin/python tests/test_public_site_lookup.py

Яагаад чухал вэ: талбайд хэвлэгдчихсэн QR кодыг дахин хэвлэх боломжгүй. Хэрэв
DB дэх site_code нь хэвлэгдсэн кодоос зөвхөн үсгийн хэмжээгээрээ зөрвөл жолооч
төлбөрөө төлж чадахгүй үлдэнэ. Тиймээс хайлт нь тэсвэртэй байх ёстой.

Шалгах зүйл:
  - Яг таарах код олдоно
  - Жижиг/холимог үсгээр бичсэн код мөн олдоно
  - Урд хойд хоосон зай саад болохгүй
  - Бүртгэлгүй код 404 өгнө (мөн логт бичигдэнэ — хэвлэгдсэн кодыг олоход хэрэгтэй)
  - Идэвхгүй зогсоол /site дээр 404 (гэхдээ QR зураг нь татагдана)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings

settings.barrier_mock = True
settings.qpay_mock = True
settings.ebarimt_mock = True
settings.ebarimt_mock_receipts = True  # тестэд MOCK баримт (SENT) хэрэгтэй

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import ParkingSite  # noqa: E402

PASS = FAIL = 0


def check(name, cond):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗ <<< FAIL'} {name}")


CODE = "ZZTESTQR"
client = TestClient(app)
db = SessionLocal()

# Өмнөх ажиллалтын үлдэгдэл байвал цэвэрлэнэ
db.query(ParkingSite).filter(ParkingSite.site_code == CODE).delete()
db.commit()

site = ParkingSite(site_code=CODE, name="QR тест зогсоол", zone_code="A", capacity=0)
db.add(site)
db.commit()

try:
    print("Кодын хайлт (/api/public/site):")
    r = client.get(f"/api/public/site/{CODE}")
    check("яг таарах код → 200", r.status_code == 200 and r.json()["site_code"] == CODE)

    r = client.get(f"/api/public/site/{CODE.lower()}")
    check("жижиг үсгээр → 200", r.status_code == 200 and r.json()["site_code"] == CODE)

    r = client.get(f"/api/public/site/{CODE[:4].lower() + CODE[4:]}")
    check("холимог үсгээр → 200", r.status_code == 200)

    r = client.get(f"/api/public/site/  {CODE}  ")
    check("урд хойд хоосон зайтай → 200", r.status_code == 200)

    r = client.get("/api/public/site/BAIHGUI-KOD")
    check("бүртгэлгүй код → 404", r.status_code == 404)

    print("UUID-аар хайх (/checkout/<uuid> хэлбэрийн хэвлэгдсэн QR):")
    r = client.get(f"/api/public/site/{site.id}")
    check("зогсоолын id-гаар → 200", r.status_code == 200 and r.json()["site_code"] == CODE)

    r = client.get(f"/api/public/site/{str(site.id).upper()}")
    check("id том үсгээр → 200", r.status_code == 200)

    r = client.get("/api/public/site/00000000-0000-0000-0000-000000000000")
    check("бүртгэлгүй UUID → 404 (DB алдаа биш)", r.status_code == 404)

    r = client.get("/api/public/site/zzz-biш-uuid-биш-код")
    check("UUID мэт боловч буруу утга → 404", r.status_code == 404)

    print("Бусад public endpoint мөн тэсвэртэй эсэх:")
    r = client.get(f"/api/public/recent-exits/{CODE.lower()}")
    check("recent-exits жижиг үсгээр → 200", r.status_code == 200)

    r = client.get(f"/api/public/search?site={CODE.lower()}&q=12")
    check("search жижиг үсгээр → 200", r.status_code == 200)

    r = client.get(f"/api/public/qr/{CODE.lower()}.png")
    check("QR зураг жижиг үсгээр → 200 PNG",
          r.status_code == 200 and r.headers["content-type"] == "image/png")
    check("QR файлын нэр DB дэх ЖИНХЭНЭ кодоор өгөгдөнө",
          CODE in r.headers.get("content-disposition", ""))

    print("Хэвлэгдсэн самбарын QR линк (qr_url):")
    PRINTED = f"https://app.easy-parking.mn/checkout/{site.id}"
    r = client.get(f"/api/public/qr/{CODE}.png")
    default_png = r.content
    site.qr_url = PRINTED
    db.commit()

    from app.serializers import site_pay_url
    check("qr_url бөглөвөл pay_url нь ЯГ тэр линк болно", site_pay_url(site) == PRINTED)

    r = client.get(f"/api/public/qr/{CODE}.png")
    check("QR зураг өөрчлөгдсөн (өөр линк кодлогдсон)", r.content != default_png)

    site.qr_url = None
    db.commit()
    r = client.get(f"/api/public/qr/{CODE}.png")
    check("qr_url цэвэрлэвэл стандарт /pay?site= рүү буцна", r.content == default_png)
    check("стандарт pay_url хэлбэр зөв", site_pay_url(site).endswith(f"/pay?site={CODE}"))

    print("Идэвхгүй зогсоол:")
    site.is_active = False
    db.commit()
    r = client.get(f"/api/public/site/{CODE}")
    check("идэвхгүй → /site 404", r.status_code == 404)
    r = client.get(f"/api/public/qr/{CODE}.png")
    check("идэвхгүй ч QR зураг татагдана (урьдчилан хэвлэх)", r.status_code == 200)
finally:
    db.query(ParkingSite).filter(ParkingSite.site_code == CODE).delete()
    db.commit()
    db.close()

print(f"\n{PASS} PASS, {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
