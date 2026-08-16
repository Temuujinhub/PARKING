"""Хаалтын ТӨЛӨВ ба ШАЛТГААНЫ шошго — юуг хэзээ бичихийг батална.

    cd backend && venv/bin/python tests/test_close_labels.py

Зарчим: ТӨЛӨВ нь ЮУ болсныг хэлнэ, `closed_by` нь ХЭН хийсэнийг.
  CLOSED        — төлбөр барагдсан
  FREE          — төлбөр 0₮ (үнэгүй хугацаа / гэрээт / хөнгөлөлт)
  MANUAL_CLOSED — «Гарах уншилтгүй», төлбөр үлдсэн

Урьд нь албадан хаалтын БҮХ зам 0₮ зогсолтыг ч `MANUAL_CLOSED` гэж бичээд
Түүх дээр «Гараар хаасан» гэж харагддаг байсан (2026-08-16 Рашбулаг ЭТТ:
286 мөрийн 138 нь 0₮, 252 нь «хэн хаасан» нь хоосон).
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
from app.models import (AuditLog, ParkingSession, ParkingSite, Payment,  # noqa: E402
                        TariffTemplate, TariffTier)
from app.routers.sessions_router import _CLOSE_LABEL, _attach_close_info  # noqa: E402
from app.session_logic import close_session_forced  # noqa: E402

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗ <<< FAIL'} {name}{'' if cond else f'  [{extra}]'}")


def main():
    db = SessionLocal()
    tag = uuid.uuid4().hex[:6]
    tpl = TariffTemplate(name=f"ZZ-Tar-{tag}", free_minutes=30, grace_minutes=15,
                         extra_hour_price=1000)
    db.add(tpl)
    db.flush()
    db.add(TariffTier(template_id=tpl.id, upto_minutes=60, price=1000))
    site = ParkingSite(name=f"ZZ-Шошго-{tag}", site_code=f"ZZC{tag}", is_active=True,
                       tariff_template_id=tpl.id)
    db.add(site)
    db.commit()
    now = datetime.utcnow()
    made = []

    def mk(minutes, **kw):
        s = ParkingSession(site_id=site.id, plate_number=f"{len(made):04d}УБА",
                           status="OPEN", entry_time=now - timedelta(minutes=minutes), **kw)
        db.add(s)
        db.commit()
        made.append(s)
        return s

    try:
        print("\nҮНЭГҮЙ хугацаанд багтсаныг албадан хаахад → FREE:")
        s = mk(10)                       # 30 мин үнэгүйд багтсан
        s.exit_time = now                # гарах уншилт бий гэж үзье
        s.status = "AWAITING_PAYMENT"
        close_session_forced(db, s, "camera_sync_exit", "system", create_comp=False)
        db.commit()
        check("төлбөр 0₮", float(s.total_fee or 0) == 0, str(s.total_fee))
        check("төлөв FREE (Гараар хаасан БИШ)", s.status == "FREE", s.status)

        print("\nТӨЛБӨРТЭЙ мөртөө төлөгдөөгүйг албадан хаахад → MANUAL_CLOSED:")
        s2 = mk(120)
        s2.exit_time = now
        s2.status = "AWAITING_PAYMENT"
        close_session_forced(db, s2, "camera_sync_exit", "system", create_comp=False)
        db.commit()
        check("төлбөр бодогдсон", float(s2.total_fee or 0) > 0, str(s2.total_fee))
        check("төлөв MANUAL_CLOSED", s2.status == "MANUAL_CLOSED", s2.status)

        print("\nТӨЛСӨН бол → CLOSED:")
        s3 = mk(120, paid_at=now - timedelta(minutes=5))
        s3.exit_time = now
        s3.status = "PAID"
        close_session_forced(db, s3, "auto_close", "system", create_comp=False)
        db.commit()
        check("төлөв CLOSED", s3.status == "CLOSED", s3.status)

        print("\nШалтгаан бүрд НЭГ товч шошиг:")
        want = ["ADMIN_REMOVE", "MANUAL_EXIT", "AUTO_CLOSE", "AUTO_FREE_CLOSE",
                "AUTO_JUNK_CLOSE", "CAMERA_SYNC", "CAMERA_SYNC_EXIT",
                "SHIFT_CLOSE_CAR", "NIGHT_CLOSE_CAR", "REENTRY_CLOSE"]
        for a in want:
            check(f"«{a}» шошготой", a in _CLOSE_LABEL, "шошиг алга")
        check("шошиг бүр товч (≤26 тэмдэгт)",
              all(len(v) <= 26 for v in _CLOSE_LABEL.values()),
              str([v for v in _CLOSE_LABEL.values() if len(v) > 26]))

        print("\nОператорын гаргалт ТӨЛБӨРТЭЙ/ҮНЭГҮЙ гэж хуваагдана:")
        s4, s5 = mk(60), mk(60)
        for x in (s4, s5):
            db.add(AuditLog(username="khuslen", action="MANUAL_EXIT", entity="session",
                            entity_id=x.id, detail={}))
        db.add(Payment(session_id=s4.id, amount=1000, status="PAID", provider="CASH",
                       payment_method="CASH", paid_at=now,
                       sender_invoice_no=f"ZZ{tag}"))
        db.commit()
        out = _attach_close_info(db, [{"id": s4.id, "plate_number": s4.plate_number},
                                      {"id": s5.id, "plate_number": s5.plate_number}])
        check("төлбөр авсан нь ялгагдсан",
              out[0]["closed_by"]["label"] == "Оператор төлбөртэй гаргасан",
              out[0]["closed_by"]["label"])
        check("үнэгүй гаргасан нь ялгагдсан",
              out[1]["closed_by"]["label"] == "Оператор ҮНЭГҮЙ гаргасан",
              out[1]["closed_by"]["label"])

        print("\nНөхөлтөөр хаагдсан мөр «хэн хаасан» нь ХООСОН үлдэхгүй:")
        s6 = mk(60)
        db.add(AuditLog(username="system", action="CAMERA_SYNC_EXIT", entity="session",
                        entity_id=s6.id, detail={}))
        db.commit()
        out2 = _attach_close_info(db, [{"id": s6.id, "plate_number": s6.plate_number}])
        check("шошиг гарч ирсэн", (out2[0]["closed_by"] or {}).get("label")
              == "Логийн гарах уншилт", str(out2[0]["closed_by"]))
        check("«Систем» гэж тэмдэглэгдсэн", (out2[0]["closed_by"] or {}).get("auto") is True)
    finally:
        ids = [x.id for x in made]
        db.query(Payment).filter(Payment.session_id.in_(ids)).delete(synchronize_session=False)
        db.query(AuditLog).filter(AuditLog.entity == "session",
                                  AuditLog.entity_id.in_(ids)).delete(synchronize_session=False)
        db.query(ParkingSession).filter(ParkingSession.site_id == site.id).delete()
        db.query(ParkingSite).filter(ParkingSite.id == site.id).delete()
        db.query(TariffTier).filter(TariffTier.template_id == tpl.id).delete()
        db.query(TariffTemplate).filter(TariffTemplate.id == tpl.id).delete()
        db.commit()
        db.close()

    print(f"\n{PASS} PASS, {FAIL} FAIL")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
