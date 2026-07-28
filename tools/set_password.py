#!/usr/bin/env python
"""Хэрэглэгчийн нууц үгийг солих (яаралтай нөхцөлд, UI-гүйгээр).

    cd /root/PARKING/backend
    sudo venv/bin/python ../tools/set_password.py temuujin
    sudo venv/bin/python ../tools/set_password.py --audit        # эрсдэлтэйг жагсаана

Яагаад: app/seed.py дотор анхны нууц үгүүд ил бичигдсэн бөгөөд repo нь нээлттэй
байсан тул тэдгээр нь олон нийтэд задарсан. Энэ хэрэгсэл нь тэдгээрийг хурдан
солиход зориулагдсан. Нууц үг терминалд ХЭВЛЭГДЭХГҮЙ (getpass-аар асууна).
"""
import argparse
import getpass
import os
import sys

BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
sys.path.insert(0, BACKEND)
# config.py-ийн env_file нь ".env" (CWD-д харьцангуй) тул backend хавтас руу шилжинэ —
# эс бол DATABASE_URL нь кодын default утга болж «password authentication failed» өгнө
# (add_site.py-тай ижил хэв маяг).
os.chdir(BACKEND)

from app.auth import hash_password, verify_password  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import User  # noqa: E402

# seed.py-д ил бичигдэж байсан анхны нууц үгүүд
LEAKED = ["Temuujin@2026", "Admin@2026", "Operator@2026", "Cashier@2026",
          "Manager@2026", "Finance@2026"]


def audit(db) -> int:
    risky = []
    for u in db.query(User).all():
        for pw in LEAKED:
            try:
                if verify_password(pw, u.password_hash):
                    risky.append(u)
                    break
            except Exception:  # noqa: BLE001
                pass
    if not risky:
        print("OK: задарсан анхны нууц үг ашиглаж буй хэрэглэгч алга.")
        return 0
    print("АНХААР: доорх хэрэглэгчид задарсан нууц үгтэй хэвээр байна —")
    for u in risky:
        print(f"   • {u.username}  ({u.role}, идэвхтэй={u.is_active})")
    print("\nСолих:  sudo venv/bin/python ../tools/set_password.py <хэрэглэгч>")
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Хэрэглэгчийн нууц үг солих")
    ap.add_argument("username", nargs="?", help="нэвтрэх нэр")
    ap.add_argument("--audit", action="store_true", help="эрсдэлтэй хэрэглэгчийг жагсаах")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        if args.audit or not args.username:
            return audit(db)
        user = db.query(User).filter(User.username == args.username).first()
        if not user:
            print(f"Хэрэглэгч олдсонгүй: {args.username}", file=sys.stderr)
            return 1
        pw1 = getpass.getpass(f"«{user.username}» ({user.role}) шинэ нууц үг: ")
        if len(pw1) < 10:
            print("Нууц үг хэт богино (10+ тэмдэгт).", file=sys.stderr)
            return 1
        if pw1 in LEAKED:
            print("Энэ бол задарсан нууц үгүүдийн нэг — өөрийг сонгоно уу.", file=sys.stderr)
            return 1
        if pw1 != getpass.getpass("Дахин оруулна уу: "):
            print("Таарахгүй байна.", file=sys.stderr)
            return 1
        from datetime import datetime
        user.password_hash = hash_password(pw1)
        # Энэ хугацаанаас ӨМНӨ олгогдсон бүх токен ШУУД хүчингүй болно —
        # задарсан нууц үгээр нэвтэрсэн хэн нэгэн байсан ч тэр даруй тасарна.
        user.password_changed_at = datetime.utcnow()
        db.commit()
        print(f"«{user.username}»-ийн нууц үг солигдлоо.")
        print("Энэ хэрэглэгчийн БҮХ идэвхтэй токен ШУУД хүчингүй боллоо "
              "(нэвтэрсэн байсан хэн ч тасарна).")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
