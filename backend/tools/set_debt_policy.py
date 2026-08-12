"""Өр үүсгэх бодлогыг (create_debt) нэг командаар унтраах/асаах.

Яагаад хэрэгтэй вэ: `app_settings.py` дахь анхдагч утга нь DB-д тухайн мөр
БАЙХГҮЙ үед л үйлчилдэг. Ажиллаж буй сервер дээр админ нэг ч удаа Тохиргоог
хадгалсан бол DB-д мөр үүссэн байх ба кодын шинэ анхдагч НӨЛӨӨЛӨХГҮЙ.
Тиймээс deploy хийсний дараа энэ хэрэгслээр DB-ийн утгыг шууд солино.

Юуг хөнддөг вэ:
  • autoclose.create_debt  — N цагийн авто хаалт өр үүсгэх эсэх
  • camsync.create_debt    — камерын логоос нөхөж бүртгэхэд өр үүсгэх эсэх

Юуг ХӨНДӨХГҮЙ вэ: `unpaid_exit` (гарах уншилт байгаа, төлөлгүй давсан) —
жолоочийн бодит авлага үргэлжлэн бүртгэгдэнэ.

Ажиллуулах:
    cd /root/PARKING/backend
    venv/bin/python tools/set_debt_policy.py            # одоогийн байдлыг харах
    venv/bin/python tools/set_debt_policy.py --off      # өр үүсгэхийг УНТРААХ
    venv/bin/python tools/set_debt_policy.py --on       # буцааж АСААХ
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal
from app.services.app_settings import (AUTOCLOSE_KEY, CAMSYNC_KEY,
                                       get_autoclose_rules, get_camsync_rules, set_rules)

TARGETS = [
    (AUTOCLOSE_KEY, get_autoclose_rules, "Авто хаалт (N цаг хөдөлгөөнгүй)"),
    (CAMSYNC_KEY, get_camsync_rules, "Камерын лог нөхөлт"),
]


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--off", action="store_true", help="өр үүсгэхийг унтраах")
    g.add_argument("--on", action="store_true", help="өр үүсгэхийг асаах")
    ap.add_argument("--by", default="system(set_debt_policy)", help="хэн өөрчилсөн")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        from app.models import AppSetting
        print("\n═══ ӨР ҮҮСГЭХ БОДЛОГО ═══\n")
        for key, getter, label in TARGETS:
            rules = dict(getter(db))
            cur = bool(rules.get("create_debt"))
            # Утга DB-ээс ирсэн үү, кодын анхдагчаас уу — ЭНЭ НЬ ЧУХАЛ: DB-д мөр
            # байвал кодын шинэ анхдагч НӨЛӨӨЛӨХГҮЙ, deploy хийгээд ч хуучнаараа
            # үлдэнэ. Тиймээс эх сурвалжийг нь ил харуулна.
            row = db.get(AppSetting, key)
            src = "DB" if row and "create_debt" in (row.value or {}) else "код-анхдагч"
            if args.off or args.on:
                want = bool(args.on)
                if cur == want and src == "DB":
                    print(f"  {label:<34} create_debt={cur} [{src}] (өөрчлөлтгүй)")
                    continue
                rules["create_debt"] = want
                set_rules(db, key, rules, args.by)
                print(f"  {label:<34} create_debt: {cur} [{src}] → {want} [DB]  ✅")
            else:
                print(f"  {label:<34} create_debt={cur}  [{src}]")
        if args.off or args.on:
            db.commit()
            print("\nХадгалав. Backend-ийг дахин асаах шаардлагагүй "
                  "(тохиргоо 30 секунд тутам шинэчлэгддэг).")
        else:
            print("\nӨөрчлөх бол: --off (унтраах) эсвэл --on (асаах)")
        print("\nЖИЧ: «Бүртгэлээс хасах» цонхны чагт (admin_remove) нь кодоор\n"
              "анхдагч УНТРААЛТТАЙ болсон — админ хэрэгтэй үедээ гараар чагтална.\n"
              "unpaid_exit (бодит авлага) үргэлжлэн бүртгэгдэнэ.\n")
    finally:
        db.close()


if __name__ == "__main__":
    main()
