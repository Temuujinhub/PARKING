"""Өрийн бодлого: БАРИМТТАЙ гарц vs ТААМАГ гарцыг салгасныг батална.

    cd backend && venv/bin/python tests/test_debt_policy_split.py

Хоёр тохиолдол НОТОЛГООНЫ хувьд огт өөр:
  • Гарах уншилт ОГТ БАЙХГҮЙ  → хэзээ гарсныг мэдэхгүй → өр нэхэх үндэслэлгүй
  • Логоор ГАРСАН нь тогтоогдсон → камерын бичлэг гэрчилнэ → ЖИНХЭНЭ авлага

2026-08-12-нд хоёуланг НЭГ шилжүүлэгчээр унтраасан нь баримттай авлагыг ч
алдуулж байв: 2026-08-17 хэмжилтээр Эрэл-13 дээр ГАНЦ ӨДӨРТ логоор гарсан нь
тогтоогдсон 112 машинд 334,000₮ бодогдоод бүртгэгдэлгүй өнгөрсөн.
"""
import os
import sys
import uuid
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings  # noqa: E402

settings.barrier_mock = True
settings.snapshot_enabled = False
settings.screen_enabled = False

from app.database import SessionLocal  # noqa: E402
from app.models import (Compensation, ParkingSession, ParkingSite,  # noqa: E402
                        TariffTemplate, TariffTier)
from app.services.app_settings import CAMSYNC_KEY, DEFAULTS  # noqa: E402
from app.session_logic import close_session_forced  # noqa: E402

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗ <<< FAIL'} {name}{'' if cond else f'  [{extra}]'}")


def main():
    print("\nТохиргоо ХОЁР тусдаа шилжүүлэгчтэй:")
    r = DEFAULTS[CAMSYNC_KEY]
    check("«create_debt_log_exit» түлхүүр бий", "create_debt_log_exit" in r)
    check("«create_debt» (таамаг) хэвээр бий", "create_debt" in r)
    check("БАРИМТТАЙ гарц анхдагчаар ӨР ҮҮСГЭНЭ",
          r.get("create_debt_log_exit") is True, str(r.get("create_debt_log_exit")))
    check("ТААМАГ гарц анхдагчаар өр үүсгэхгүй (хуурамч өр эргэж ирэхгүй)",
          r.get("create_debt") is False, str(r.get("create_debt")))

    # camera_sync-ийн хоёр дуудалт ЗӨВХӨН log_exit түлхүүрийг ашиглана
    import inspect

    from app.services import camera_sync
    src = inspect.getsource(camera_sync)
    check("camera_sync нь log_exit түлхүүрийг ашигладаг",
          src.count('rules["create_debt_log_exit"]') == 2,
          str(src.count('rules["create_debt_log_exit"]')))
    check("camera_sync хуучин түлхүүрээр өр үүсгэхээ больсон",
          'create_comp=rules["create_debt"]' not in src)

    db = SessionLocal()
    tag = uuid.uuid4().hex[:6]
    tpl = TariffTemplate(name=f"ZZ-D-{tag}", free_minutes=30, grace_minutes=15,
                         extra_hour_price=1000)
    db.add(tpl)
    db.flush()
    db.add(TariffTier(template_id=tpl.id, upto_minutes=60, price=1000))
    site = ParkingSite(name=f"ZZ-Өр-{tag}", site_code=f"ZZD{tag}", is_active=True,
                       tariff_template_id=tpl.id)
    db.add(site)
    db.commit()
    now = datetime.utcnow()
    made = []

    def mk(plate, minutes, **kw):
        s = ParkingSession(site_id=site.id, plate_number=plate, status="OPEN",
                           entry_time=now - timedelta(minutes=minutes), **kw)
        db.add(s)
        db.commit()
        made.append(s)
        return s

    try:
        print("\nЛОГООР ГАРСАН нь тогтоогдсон (баримттай) → ӨР ҮҮСНЭ:")
        s1 = mk("1111УБА", 180)
        s1.exit_time = now - timedelta(minutes=30)   # логийн гарах цаг
        s1.exit_confirmed = True
        s1.status = "AWAITING_PAYMENT"
        due = close_session_forced(db, s1, "camera_sync_exit", "system", create_comp=True)
        db.commit()
        comp = (db.query(Compensation)
                .filter(Compensation.plate_number == "1111УБА").first())
        check("өр үүссэн", comp is not None and due > 0, f"due={due}")
        check("өрийн дүн бодит төлбөртэй тэнцүү",
              comp is not None and abs(float(comp.amount) - due) < 1, str(due))
        check("төлбөр ГАРСАН ЦАГААР бодогдсон (одоогоор биш)",
              s1.duration_minutes is not None and 145 <= s1.duration_minutes <= 155,
              str(s1.duration_minutes))

        print("\nГарах уншилтгүй (таамаг) → ӨР ҮҮСГЭХГҮЙ, дүн ч бичихгүй:")
        s2 = mk("2222УБА", 600)                      # 10 цаг, гарах уншилт алга
        due2 = close_session_forced(db, s2, "auto_close", "system", create_comp=False)
        db.commit()
        comp2 = (db.query(Compensation)
                 .filter(Compensation.plate_number == "2222УБА").first())
        check("өр үүсээгүй", comp2 is None and due2 == 0, f"due={due2}")
        check("хуурамч дүн бичээгүй (0₮)", float(s2.total_fee or 0) == 0,
              str(s2.total_fee))
        check("хугацааг ТААМАГЛААГҮЙ (NULL)", s2.duration_minutes is None,
              str(s2.duration_minutes))
    finally:
        ids = [x.id for x in made]
        db.query(Compensation).filter(
            Compensation.plate_number.in_(["1111УБА", "2222УБА"]),
            Compensation.site_id == site.id).delete(synchronize_session=False)
        db.query(ParkingSession).filter(ParkingSession.id.in_(ids)).delete(
            synchronize_session=False)
        db.query(ParkingSite).filter(ParkingSite.id == site.id).delete()
        db.query(TariffTier).filter(TariffTier.template_id == tpl.id).delete()
        db.query(TariffTemplate).filter(TariffTemplate.id == tpl.id).delete()
        db.commit()
        db.close()

    print(f"\n{PASS} PASS, {FAIL} FAIL")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
