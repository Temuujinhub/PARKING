"""Зогсоол бүрийн өөрийн QPay мерчант данс + нууц үг API-аар задрахгүй байх.

    cd backend && venv/bin/python tests/test_site_qpay_account.py

Яагаад чухал вэ: түрээслэгч байгууллага бүр өөрийн QPay гэрээтэй байж болно
(ж: Моннис Пропертиес). Төлбөр нь ТЭДНИЙ данс руу орж, e-Barimt нь ТЭДНИЙ
ТТД-ээр үүсэх ёстой. Данс холилдвол мөнгө буруу байгууллага руу очно.

Шалгах зүйл:
  - Данс тохируулаагүй зогсоол → глобал .env-ийн данс
  - Данс тохируулсан зогсоол → өөрийн username/password/invoice_code
  - Өөрийн данстай зогсоол глобал mock=true үед ч БОДИТ горимд ажиллана
  - Хэсэгчилсэн тохиргоо (зөвхөн дүүрэг) → данс глобал, дүүрэг зогсоолынх
  - Токены cache мерчант тус бүрд ТУСДАА (нэг дансны токен нөгөөд ашиглагдахгүй)
  - qpay_password / device.password нь API-ийн хариултад ОГТ гарахгүй
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings

settings.qpay_mock = True
settings.qpay_username = "GLOBAL_USER"
settings.qpay_password = "GLOBAL_PASS"
settings.qpay_invoice_code = "GLOBAL_INVOICE"
settings.qpay_district_code = "2318"
settings.qpay_branch_code = "PARKING"

from app.models import Device, ParkingSite  # noqa: E402
from app.serializers import to_dict  # noqa: E402
from app.services import qpay  # noqa: E402

PASS = FAIL = 0


def check(name, cond):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗ <<< FAIL'} {name}")


print("Данс тохируулаагүй зогсоол:")
plain = ParkingSite(name="Энгийн", site_code="PLAIN")
acc = qpay.account_for(plain)
check("глобал username", acc.username == "GLOBAL_USER")
check("глобал invoice_code", acc.invoice_code == "GLOBAL_INVOICE")
check("глобал mock тохиргоог дагана", acc.mock is True)
check("site=None үед ч глобал данс", qpay.account_for(None).username == "GLOBAL_USER")

print("\nӨӨРИЙН данстай зогсоол (ж: Моннис):")
monnis = ParkingSite(name="Моннис билдинг", site_code="MONNIS",
                     qpay_username="MONNIS_PROPERTIES", qpay_password="SECRET",
                     qpay_invoice_code="MONNIS_PROPERTIES_INVOICE",
                     qpay_district_code="2606")
m = qpay.account_for(monnis)
check("өөрийн username", m.username == "MONNIS_PROPERTIES")
check("өөрийн password", m.password == "SECRET")
check("өөрийн invoice_code", m.invoice_code == "MONNIS_PROPERTIES_INVOICE")
check("өөрийн дүүргийн код", m.district_code == "2606")
check("глобал mock=true байсан ч БОДИТ горим (mock=False)", m.mock is False)
check("өөр мерчант → өөр cache түлхүүр", m.cache_key != acc.cache_key)

print("\nХэсэгчилсэн тохиргоо (зөвхөн дүүрэг солих):")
partial = ParkingSite(name="Хэсэгчилсэн", site_code="PART", qpay_district_code="2419")
pa = qpay.account_for(partial)
check("данс нь глобал хэвээр", pa.username == "GLOBAL_USER")
check("дүүрэг нь зогсоолынх", pa.district_code == "2419")
check("mock глобалыг дагана", pa.mock is True)

print("\nНууц үг зөвхөн хагас бөглөсөн (алдаатай тохиргоо):")
half = ParkingSite(name="Хагас", site_code="HALF", qpay_username="ONLY_USER")
h = qpay.account_for(half)
check("нууц үггүй бол өөрийн данс болохгүй (глобал хэвээр)", h.username == "GLOBAL_USER")
check("бодит горимд шилжихгүй (mock=True)", h.mock is True)

print("\nТокены cache мерчант тус бүрд тусдаа:")
qpay._tokens.clear()
qpay._cache(m)["access"] = "MONNIS_TOKEN"
check("Моннисын токен өөрийн нүдэнд", qpay._cache(m)["access"] == "MONNIS_TOKEN")
check("глобал дансны токен ХООСОН хэвээр", qpay._cache(acc)["access"] is None)
qpay._tokens.clear()

print("\nНууц үг API-аар задрахгүй:")
d = to_dict(monnis)
check("qpay_password утга буцаагдахгүй", "qpay_password" not in d)
check("qpay_password_set=True гэж мэдэгдэнэ", d.get("qpay_password_set") is True)
check("qpay_username нь харагдана (нууц биш)", d.get("qpay_username") == "MONNIS_PROPERTIES")
check("данс тохируулаагүйд qpay_password_set=False",
      to_dict(plain).get("qpay_password_set") is False)

dev = Device(name="Орох камер", device_type="camera", ip_address="10.0.101.10",
             username="admin", password="camsecret")
dd = to_dict(dev)
check("төхөөрөмжийн password буцаагдахгүй", "password" not in dd)
check("password_set=True", dd.get("password_set") is True)
check("username нь харагдана", dd.get("username") == "admin")

print("\nТөхөөрөмжийн нэвтрэлт (уналтын дараалал):")
from app.services.device_auth import barrier_credentials, camera_credentials  # noqa: E402
settings.camera_username, settings.camera_password = "globalcam", "globalpw"
settings.barrier_username, settings.barrier_password = "", ""
check("төхөөрөмжийнх давуу", camera_credentials(dev) == ("admin", "camsecret"))
check("хоосон төхөөрөмж → глобал", camera_credentials(Device()) == ("globalcam", "globalpw"))
check("None → глобал", camera_credentials(None) == ("globalcam", "globalpw"))
check("хаалт мөн төхөөрөмжийнхөөр", barrier_credentials(dev) == ("admin", "camsecret"))
settings.barrier_username, settings.barrier_password = "barr", "barrpw"
check("хаалтын глобал нь камерынхаас давуу",
      barrier_credentials(Device()) == ("barr", "barrpw"))

print(f"\n{PASS} PASS, {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
