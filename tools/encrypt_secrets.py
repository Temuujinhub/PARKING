#!/usr/bin/env python
"""DB-д ил хадгалагдсан нууц утгуудыг шифрлэх нэг удаагийн шилжилт.

    cd /root/PARKING/backend && venv/bin/python ../tools/encrypt_secrets.py

Юу хийдэг: parking_sites.qpay_password, devices.password-ийн "enc:" угтваргүй
(plaintext) утгуудыг PARKING_SECRET_ENC_KEY-ээр шифрлэж дарж бичнэ. Идемпотент —
дахин ажиллуулахад шифрлэгдсэн мөрийг алгасна. Нууц утгыг хэвлэхгүй, зөвхөн тоо.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))

from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import Device, ParkingSite  # noqa: E402
from app.secretbox import encrypt_secret, is_encrypted  # noqa: E402


def main() -> int:
    if not settings.secret_enc_key:
        print("PARKING_SECRET_ENC_KEY тохируулаагүй — юу ч хийсэнгүй.", file=sys.stderr)
        return 1
    db = SessionLocal()
    try:
        sites = devices = 0
        for site in db.query(ParkingSite).filter(ParkingSite.qpay_password.isnot(None)).all():
            if site.qpay_password and not is_encrypted(site.qpay_password):
                site.qpay_password = encrypt_secret(site.qpay_password)
                sites += 1
        for dev in db.query(Device).filter(Device.password.isnot(None)).all():
            if dev.password and not is_encrypted(dev.password):
                dev.password = encrypt_secret(dev.password)
                devices += 1
        db.commit()
        print(f"Шифрлэгдлээ: зогсоолын QPay нууц үг {sites}, төхөөрөмжийн нууц үг {devices}.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
