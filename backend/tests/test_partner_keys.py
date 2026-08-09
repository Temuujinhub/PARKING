"""Гадаад API-ийн партнер түлхүүр — DB удирдлага (Тохиргоо → Холболт → Гадаад API).

    cd backend && venv/bin/python tests/test_partner_keys.py

Яагаад чухал вэ: түлхүүр нь хаалт нээх/төлбөр батлах эрх олгодог тул (1) DB-д зөвхөн
hash хадгалагдах, (2) хаасан түлхүүр тэр дороо унтрах, (3) «зөвхөн лавлах» түлхүүр
төлбөр батлахгүй байх, (4) зогсоолын хязгаар мөрдөгдөх нь мөнгөний аюулгүй байдал.

Шалгах зүйл:
  - Үүсгэхэд түлхүүр НЭГ удаа ил гарч, DB-д sha256 hash + prefix л үлдэнэ
  - require_partner DB түлхүүрийг таньж нэр/эрх/хязгаарыг өгнө
  - .env-ийн хуучин түлхүүр fallback-аар ажилласаар (тасрахгүй)
  - Буруу түлхүүр → 401; хаагдсан түлхүүр → 401
  - read-only түлхүүрээр intent/confirm → 403
  - Зогсоолын хязгаартай түлхүүр өөр зогсоолын session-д хандахгүй
  - Идэвхтэй ижил нэр давхардуулахгүй; буруу нэр татгалзана
"""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings

settings.partner_keys = "envwallet:ENV_KEY_999"

import hashlib  # noqa: E402

from fastapi import HTTPException  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models import ParkingSite, PartnerKey, User  # noqa: E402
from app.routers.admin_router import (create_partner_key, list_partner_keys,  # noqa: E402
                                      revoke_partner_key)
from app.routers.integration_router import (_check_site_scope, _require_pay,  # noqa: E402
                                            require_partner)

PASS = FAIL = 0


def check(name, cond):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗ <<< FAIL'} {name}")


def expect_http(fn, code):
    try:
        fn()
        return False
    except HTTPException as e:
        return e.status_code == code


REQ = SimpleNamespace(client=SimpleNamespace(host="10.9.9.9"))
CODES = ("ZZPK_A", "ZZPK_B")
db = SessionLocal()
db.query(PartnerKey).filter(PartnerKey.name.in_(("pk-test", "pk-ro", "pk-scoped"))).delete(
    synchronize_session=False)
db.query(ParkingSite).filter(ParkingSite.site_code.in_(CODES)).delete(synchronize_session=False)
db.commit()
site_a = ParkingSite(name="ПК тест А", site_code="ZZPK_A", zone_code="A", capacity=0)
site_b = ParkingSite(name="ПК тест Б", site_code="ZZPK_B", zone_code="A", capacity=0)
db.add_all([site_a, site_b])
db.commit()
su = User(username="pk_tester", password_hash="x", role="SUPER_ADMIN")

print("Түлхүүр үүсгэх:")
r = create_partner_key({"name": "pk-test", "can_pay": True}, db=db, user=su)
check("түлхүүр ил буцаана (pk_ угтвартай)", r["key"].startswith("pk_") and len(r["key"]) > 20)
row = db.get(PartnerKey, r["id"])
check("DB-д hash л хадгална (түлхүүр байхгүй)",
      row.key_hash == hashlib.sha256(r["key"].encode()).hexdigest()
      and r["key"] not in (row.key_hash, row.key_prefix))
check("prefix танигдахуйц", row.key_prefix == r["key"][:10])
check("идэвхтэй ижил нэр давхардуулахгүй",
      expect_http(lambda: create_partner_key({"name": "pk-test"}, db=db, user=su), 400))
check("буруу нэр татгалзана",
      expect_http(lambda: create_partner_key({"name": "муу нэр!"}, db=db, user=su), 400))

print("Нэвтрэлт:")
auth = require_partner(REQ, x_api_key=r["key"], db=db)
check("DB түлхүүр танигдана, нэр нь string шиг ажиллана",
      auth == "pk-test" and f"partner:{auth}" == "partner:pk-test")
check("төлбөрийн эрхтэй", auth.can_pay())
db.refresh(row)
check("last_used_at бичигдэнэ", row.last_used_at is not None)
check(".env fallback хэвээр", require_partner(REQ, x_api_key="ENV_KEY_999", db=db) == "ENVWALLET")
check("буруу түлхүүр → 401",
      expect_http(lambda: require_partner(REQ, x_api_key="wrong", db=db), 401))

print("Эрх/хязгаар:")
ro = create_partner_key({"name": "pk-ro", "can_pay": False}, db=db, user=su)
ro_auth = require_partner(REQ, x_api_key=ro["key"], db=db)
check("read-only түлхүүр төлбөрийн эрхгүй → 403",
      not ro_auth.can_pay() and expect_http(lambda: _require_pay(ro_auth), 403))
sc = create_partner_key({"name": "pk-scoped", "site_id": site_a.id}, db=db, user=su)
sc_auth = require_partner(REQ, x_api_key=sc["key"], db=db)
check("өөрийн зогсоолдоо хандана", _check_site_scope(sc_auth, site_a.id) is None)
check("өөр зогсоолд → 403", expect_http(lambda: _check_site_scope(sc_auth, site_b.id), 403))

print("Хаах:")
revoke_partner_key(r["id"], db=db, user=su)
check("хаасан түлхүүр тэр дороо 401",
      expect_http(lambda: require_partner(REQ, x_api_key=r["key"], db=db), 401))
check("хаасны дараа ижил нэрээр шинэ түлхүүр үүсгэж болно",
      create_partner_key({"name": "pk-test"}, db=db, user=su)["name"] == "pk-test")

print("Жагсаалт:")
out = list_partner_keys(db=db, user=su)
import json  # noqa: E402

raw = json.dumps(out, default=str)
check("жагсаалтад түлхүүр ил гарахгүй",
      r["key"] not in raw and ro["key"] not in raw and sc["key"] not in raw
      and "ENV_KEY_999" not in raw)
check(".env партнер нэрээр жагсана", "ENVWALLET" in out["env_partners"])
names = {k["name"] for k in out["keys"]}
check("DB түлхүүрүүд жагсана", {"pk-test", "pk-ro", "pk-scoped"} <= names)

# Цэвэрлэгээ
db.query(PartnerKey).filter(PartnerKey.name.in_(("pk-test", "pk-ro", "pk-scoped"))).delete(
    synchronize_session=False)
db.query(ParkingSite).filter(ParkingSite.site_code.in_(CODES)).delete(synchronize_session=False)
db.commit()
db.close()

print(f"\n{PASS} PASS, {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
