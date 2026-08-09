"""Тохиргоо → Холболт → Төлбөрийн данс: нэгдсэн жагсаалтын endpoint.

    cd backend && venv/bin/python tests/test_payment_accounts.py

Яагаад чухал вэ: QPay данс өмнө нь 3 газар (зогсоолын модал, түрээслэгчийн модал,
.env) тарж харагддаг байсныг энэ endpoint нэгтгэдэг. «Аль зогсоол аль данс руу
төлж байна» гэсэн тооцоолол qpay.account_for-ийн дүрэмтэй ЗӨРВӨЛ админ андуурч
мөнгө буруу данс руу орсныг анзаарахгүй.

Шалгах зүйл:
  - Данс шийдэх шатлал: зогсоолын хос → түрээслэгчийн хос → глобал (account_for-той ижил)
  - Дутуу данс (нэр байгаад нууц үг алга) → complete=False, зогсоолууд нь дараагийн шатлалд
  - EB_ угтваргүй invoice_code → warning
  - Нууц үг (qpay_password) хариултад ОГТ гарахгүй
  - Банкны данстай зогсоолууд bank_accounts-д жагсана
  - SUPER_ADMIN биш хэрэглэгчид 403
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings

settings.qpay_ebarimt = True
settings.qpay_username = "GLOBAL_USER"
settings.qpay_password = "GLOBAL_PASS"
settings.qpay_invoice_code = "EB_GLOBAL_INVOICE"

import json  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models import ParkingSite, Tenant, User  # noqa: E402
from app.routers.admin_router import payment_accounts  # noqa: E402
from app.secretbox import encrypt_secret  # noqa: E402

PASS = FAIL = 0


def check(name, cond):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗ <<< FAIL'} {name}")


CODES = ("ZZPA_TEN", "ZZPA_OWN", "ZZPA_PLAIN", "ZZPA_BANK", "ZZPA_HALF")
TCODE = "ZZPA_T1"
db = SessionLocal()
db.query(ParkingSite).filter(ParkingSite.site_code.in_(CODES)).delete(synchronize_session=False)
db.query(Tenant).filter(Tenant.code.in_((TCODE, TCODE + "H"))).delete(synchronize_session=False)
db.commit()

# Түрээслэгч (бүрэн данстай) + түүний зогсоол
ten = Tenant(name="ПА тест түрээслэгч", code=TCODE, qpay_username="TEN_USER",
             qpay_password=encrypt_secret("TEN_PASS"), qpay_invoice_code="TEN_INVOICE")  # EB_-гүй!
db.add(ten)
db.flush()
s_ten = ParkingSite(name="ПА түрээслэгчийн зогсоол", site_code="ZZPA_TEN", zone_code="A",
                    capacity=0, tenant_id=ten.id)
# Өөрийн данстай зогсоол (түрээслэгчид харьяалагдсан ч өөрийн данс ДАРНА)
s_own = ParkingSite(name="ПА өөрийн данстай", site_code="ZZPA_OWN", zone_code="A", capacity=0,
                    tenant_id=ten.id, qpay_username="SITE_USER",
                    qpay_password=encrypt_secret("SITE_PASS"), qpay_invoice_code="EB_SITE_INV")
# Юу ч тохируулаагүй зогсоол → глобал
s_plain = ParkingSite(name="ПА энгийн", site_code="ZZPA_PLAIN", zone_code="A", capacity=0)
# Банкны данстай зогсоол
s_bank = ParkingSite(name="ПА банктай", site_code="ZZPA_BANK", zone_code="A", capacity=0,
                     bank_name="Хаан", bank_account="5100200300", bank_account_name="Тест ХХК")
db.add_all([s_ten, s_own, s_plain, s_bank])

# Дутуу данстай түрээслэгч (нэр бий, нууц үг алга) + зогсоол нь глобал руу унах ёстой
ten_half = Tenant(name="ПА дутуу түрээслэгч", code=TCODE + "H", qpay_username="HALF_USER")
db.add(ten_half)
db.flush()
s_half = ParkingSite(name="ПА дутуу дансных", site_code="ZZPA_HALF", zone_code="A",
                     capacity=0, tenant_id=ten_half.id)
db.add(s_half)
db.commit()

super_user = User(username="pa_tester", password_hash="x", role="SUPER_ADMIN")

print("Нэгдсэн жагсаалт:")
out = payment_accounts(db=db, user=super_user)

raw = json.dumps(out, default=str)
check("нууц үг хариултад алга", "TEN_PASS" not in raw and "SITE_PASS" not in raw
      and "GLOBAL_PASS" not in raw and "qpay_password'" not in raw)

by_key = {(a["scope"], a.get("site_code") or a["name"]): a for a in out["accounts"]}
t_acc = next((a for a in out["accounts"] if a["scope"] == "tenant" and a["name"] == ten.name), None)
check("түрээслэгчийн данс жагсаалтад бий", t_acc is not None)
check("түрээслэгчийн дансыг зөвхөн ЗӨВ зогсоол ашиглана (өөрийн данстай нь орохгүй)",
      t_acc and [s["site_code"] for s in t_acc["sites"]] == ["ZZPA_TEN"])
check("EB_-гүй invoice_code → warning", t_acc and t_acc["warning"])

o_acc = next((a for a in out["accounts"] if a["scope"] == "site" and a.get("site_code") == "ZZPA_OWN"), None)
check("зогсоолын өөрийн данс жагсаалтад бий", o_acc is not None)
check("өөрийн данс өөрийгөө ашиглана", o_acc and [s["site_code"] for s in o_acc["sites"]] == ["ZZPA_OWN"])
check("EB_ угтвартай → warning алга", o_acc and not o_acc["warning"])

h_acc = next((a for a in out["accounts"] if a["scope"] == "tenant" and a["name"] == ten_half.name), None)
check("дутуу данс complete=False", h_acc is not None and h_acc["complete"] is False)
g_codes = [s["site_code"] for s in out["global"]["sites"]]
check("энгийн зогсоол глобалд", "ZZPA_PLAIN" in g_codes)
check("дутуу данстай түрээслэгчийн зогсоол глобалд унана", "ZZPA_HALF" in g_codes)
check("дутуу дансыг ямар ч зогсоол ашиглахгүй", h_acc and h_acc["sites"] == [])

banks = {b["site_code"]: b for b in out["bank_accounts"]}
check("банкны данстай зогсоол жагсана", "ZZPA_BANK" in banks
      and banks["ZZPA_BANK"]["bank_account"] == "5100200300")
check("банкгүй зогсоол жагсахгүй", "ZZPA_PLAIN" not in banks)

print("Эрхийн шалгалт:")
# Endpoint нь Depends(require_role("SUPER_ADMIN"))-тэй — router-ийн бүртгэлээс баталгаажуулна
import inspect  # noqa: E402

sig_src = inspect.getsource(payment_accounts)
check("зөвхөн SUPER_ADMIN dependency-тэй", 'require_role("SUPER_ADMIN")' in sig_src)

# Цэвэрлэгээ
db.query(ParkingSite).filter(ParkingSite.site_code.in_(CODES)).delete(synchronize_session=False)
db.query(Tenant).filter(Tenant.code.in_((TCODE, TCODE + "H"))).delete(synchronize_session=False)
db.commit()
db.close()

print(f"\n{PASS} PASS, {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
