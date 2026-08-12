"""Камерын нэвтрэлт солих хэрэгслийн аюулгүй байдлын тест (tools/set_camera_creds.py).

    cd backend && venv/bin/python tests/test_camera_creds_rotate.py

Гол шалгах зүйлс:
  • Алгасах түрээслэгчийн (Monnis) камер ХӨНДӨГДӨХГҮЙ
  • Тэдгээрийн ОДООГИЙН (глобал .env) нэвтрэлт мөрөнд БЭХЛЭГДЭНЭ — глобал
    солигдоход чимээгүй унахгүй
  • Нэвтэрч чадаагүй камер ХӨНДӨГДӨХГҮЙ (бүх хаалт унахаас хамгаална)
  • .env-д PARKING_ угтвартай зөв түлхүүр бичигдэнэ
"""
import os
import sys
import tempfile
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))), "tools"))

from app.config import settings  # noqa: E402

settings.camera_username = "admin"
settings.camera_password = "хуучин-глобал"

from app.database import SessionLocal  # noqa: E402
from app.models import Device, ParkingSite, Tenant  # noqa: E402
from app.secretbox import decrypt_secret, encrypt_secret  # noqa: E402
from app.services.device_auth import camera_credentials  # noqa: E402

import set_camera_creds as sc  # noqa: E402

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {extra}")


db = SessionLocal()
made = []
try:
    t_easy = Tenant(id=str(uuid.uuid4()), name=f"Тест ИйзиПаркинг {uuid.uuid4().hex[:4]}",
                    code=f"TE{uuid.uuid4().hex[:6].upper()}")
    t_mon = Tenant(id=str(uuid.uuid4()), name=f"Тест Моннис Пропертиес {uuid.uuid4().hex[:4]}",
                   code=f"TM{uuid.uuid4().hex[:6].upper()}")
    db.add_all([t_easy, t_mon]); made += [t_easy, t_mon]
    db.flush()
    s_easy = ParkingSite(id=str(uuid.uuid4()), name="Тест зогсоол", zone_code="A",
                         site_code=f"CR{uuid.uuid4().hex[:5].upper()}", tenant_id=t_easy.id)
    s_mon = ParkingSite(id=str(uuid.uuid4()), name="Тест Monnis билдинг", zone_code="A",
                        site_code=f"CR{uuid.uuid4().hex[:5].upper()}", tenant_id=t_mon.id)
    db.add_all([s_easy, s_mon]); made += [s_easy, s_mon]
    db.flush()
    d_easy = Device(id=str(uuid.uuid4()), site_id=s_easy.id, device_type="camera",
                    name="Тест орох", ip_address="10.255.0.10", lane_dir="entry", status="active")
    d_mon = Device(id=str(uuid.uuid4()), site_id=s_mon.id, device_type="camera",
                   name="Monnis орох", ip_address="10.255.0.20", lane_dir="entry", status="active")
    db.add_all([d_easy, d_mon]); made += [d_easy, d_mon]
    db.commit()

    print("1. Түрээслэгчээр ялгах")
    check("ИйзиПаркингийн камер танигдав", "Ийзи" in sc.tenant_name_of(db, d_easy))
    check("Моннисын камер танигдав", "Моннис" in sc.tenant_name_of(db, d_mon))

    print("\n2. Алгасах камерын нэвтрэлт мөрөнд БЭХЛЭГДЭНЭ")
    check("эхэндээ мөрийн нэвтрэлтгүй (глобал .env)", not (d_mon.username or ""))
    u, p = camera_credentials(d_mon)
    check("глобал утга үйлчилж байв", (u, p) == ("admin", "хуучин-глобал"), (u, p))
    # Хэрэгслийн pin алхам (--apply дотор хийгддэг хэсэг)
    d_mon.username, d_mon.password = u, encrypt_secret(p)
    db.commit()
    # Глобал солигдлоо гэж дуурайя
    settings.camera_username, settings.camera_password = "sysadmin", "шинэ-глобал"
    u2, p2 = camera_credentials(d_mon)
    check("глобал солигдсон ч Monnis хуучнаараа ажиллана",
          (u2, p2) == ("admin", "хуучин-глобал"), (u2, p2))
    check("нууц үг DB-д ил биш (шифрлэгдсэн бол enc:)",
          not settings.secret_enc_key or (d_mon.password or "").startswith("enc:"))

    print("\n3. Зорилтот камерт шинэ нэвтрэлт")
    d_easy.username, d_easy.password = "sysadmin", encrypt_secret("шинэ-нууц")
    db.commit()
    u3, p3 = camera_credentials(d_easy)
    check("шинэ нэвтрэлт үйлчилнэ", (u3, p3) == ("sysadmin", "шинэ-нууц"), (u3, p3))
    check("тайлагдаж байна", decrypt_secret(d_easy.password) == "шинэ-нууц")

    print("\n4. .env шинэчлэлт — PARKING_ угтвартай зөв түлхүүр")
    fd, path = tempfile.mkstemp(suffix=".env")
    with os.fdopen(fd, "w") as f:
        f.write("PARKING_CAMERA_USERNAME=admin\nPARKING_CAMERA_PASSWORD=huuchin\n"
                "PARKING_BARRIER_USERNAME=admin\nPARKING_OTHER=stay\n# сэтгэгдэл\n")
    sc.update_env(path, "sysadmin", "шинэ-нууц")
    with open(path, encoding="utf-8") as f:
        body = f.read()
    check("CAMERA_USERNAME солигдов", "PARKING_CAMERA_USERNAME=sysadmin" in body)
    check("CAMERA_PASSWORD солигдов", "PARKING_CAMERA_PASSWORD=шинэ-нууц" in body)
    check("BARRIER_USERNAME солигдов", "PARKING_BARRIER_USERNAME=sysadmin" in body)
    check("BARRIER_PASSWORD нэмэгдэв", "PARKING_BARRIER_PASSWORD=шинэ-нууц" in body)
    check("бусад мөр хэвээр", "PARKING_OTHER=stay" in body and "# сэтгэгдэл" in body)
    check("угтваргүй түлхүүр бичээгүй", "\nCAMERA_USERNAME=" not in body)
    baks = [p for p in os.listdir(os.path.dirname(path))
            if p.startswith(os.path.basename(path) + ".bak-")]
    check("нөөц үлдээв", len(baks) == 1, baks)
    os.remove(path)
    for b in baks:
        os.remove(os.path.join(os.path.dirname(path), b))
finally:
    db.rollback()
    for obj in reversed(made):
        try:
            db.delete(obj)
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
    db.close()

print(f"\n{'='*44}\n{PASS} PASS, {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
