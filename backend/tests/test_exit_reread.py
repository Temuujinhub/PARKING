"""Гарсан машины ДАВТАН уншилт хий дүн/сэргээлт үүсгэхгүй.

    cd backend && venv/bin/python tests/test_exit_reread.py

Хангарьд 2026-09-23 (2942УКН, NVR бичлэгээр батлагдсан):
  08:54 орсон → 08:55 гарсан (0.8 мин, FREE)          ← үнэхээр гарсан
  10:53 ДАХИН орсон → 11:02:14 гарсан (FREE)
  11:02:49 гарах камер «2942УНН» гэж дахин уншив (dedup 30с-ээс хойш)
  → сешн олдсонгүй → auto_reopen_for_exit 08:54-ийн зогсолтыг «хуурамч
    гарц» гэж сэргээж 5,000₮ нэхэв (AWAITING → 2ц дараа өр).

Шалгах зүйл:
  1. Яг тэр дараалал → «reread», 08:54-ийн сешн FREE хэвээр, шинэ нэхэмжлэл алга
  2. exit_reread_seconds=0 (унтраасан) үед ч 08:54 сэргээгдэхгүй (дараагийн
     зогсолт бий = өмнөх гарц бодит)
  3. Жинхэнэ «хуурамч гарц» (дараагийн зогсолтгүй, орой дахин гарцад) →
     сэргээнэ (хуучин хамгаалалт хэвээр)
  4. Орох уншилтгүй машины 2000₮ суурь хураамж → 40с дараах давтан
     уншилт ХОЁР ДАХЬ суурь хураамж үүсгэхгүй
  5. Цонхноос гадуурх (15 мин) уншилт давтан гэж тооцогдохгүй
  6. plates_reread_similar: ≤2 тэмдэгт = True, 3 = False
  7. Гарцад ТӨЛСНИЙ дараа (сешн CLOSED, эхний уншилт 3 мин өмнө) ЯГ ИЖИЛ
     дугаар 14с дараа дахин уншигдав → 2000₮ үүсэхгүй, хаалт дахин нээгдэнэ
     (14 хоногт 299 кейс, Соёлын төв 171)
  8. 2 тэмдэгтийн зөрүү 120с дараа → давтан гэж тооцохгүй (60с-ээс хойш)
"""
import asyncio
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
from app.models import (  # noqa: E402
    AuditLog, BarrierCommand, Compensation, Device, LprEvent, ParkingSession, ParkingSite,
    Payment, TariffTemplate, TariffTier,
)
from app.services import app_settings as A  # noqa: E402
from app.services.device_auto import ensure_lane_barriers  # noqa: E402
from app.session_logic import (  # noqa: E402
    _barrier_rules, _exit_rules, handle_exit, plates_reread_similar,
)

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗ <<< FAIL'} {name}{'' if cond else f'  [{extra}]'}")


P, P_MIS = "2942ХКН", "2942ХНН"          # зөв / камерын буруу давтан уншилт (1 зөрүү)
Q = "7788ХФЕ"                            # жинхэнэ хуурамч гарц
R, R_MIS = "5151ХЗЯ", "5151ХЭЮ"          # орох уншилтгүй машин / 2 зөрүүтэй давтан уншилт
T, T_MIS = "3131ХГБ", "3131ХГВ"          # цонхноос гадуурх
U = "6464ХДЖ"                            # гарцад төлсний дараах давтан уншилт
W, W2 = "8282ХЛМ", "8282ХСТ"             # 2 зөрүү, 120с
F = "4545ХЁШ"                            # fee_locked
K, K2 = "7685ХЕХ", "7625ХЕХ"             # 1 тэмдэгт — өөр машин
E = "9191ХМЯ"                            # орох уншилттай, сешнгүй
PLATES = [P, P_MIS, Q, R, R_MIS, T, T_MIS, U, W, W2, F, K, K2, E, "3737ХЖЗ", "3737ХЖЭ"]
db = SessionLocal()
made: list = []


def raw(p):
    return {"Picture": {"Plate": {"PlateNumber": p}}}


def sessions(site_id, plates):
    db.expire_all()
    return (db.query(ParkingSession)
            .filter(ParkingSession.site_id == site_id, ParkingSession.plate_number.in_(plates))
            .order_by(ParkingSession.entry_time).all())


def reopen_logs(ids):
    return db.query(AuditLog).filter(AuditLog.action == "AUTO_REOPEN",
                                     AuditLog.entity_id.in_(ids)).count()


site = None
try:
    tpl = TariffTemplate(id=str(uuid.uuid4()), name="ZZ-reread", free_minutes=5,
                         grace_minutes=15, extra_hour_price=1000, daily_cap=25000)
    db.add(tpl); db.flush(); made.append(tpl)
    tier = TariffTier(id=str(uuid.uuid4()), template_id=tpl.id, upto_minutes=60, price=1000)
    db.add(tier); db.flush(); made.append(tier)
    site = ParkingSite(id=str(uuid.uuid4()), name="ZZ-reread", site_code=f"ZR{uuid.uuid4().hex[:7]}",
                       tariff_template_id=tpl.id, capacity=100)
    db.add(site); db.flush(); made.append(site)
    cam_in = Device(id=str(uuid.uuid4()), site_id=site.id, name="ZZ орох", device_type="camera",
                    ip_address="10.255.9.1", lane_no=1, lane_dir="entry", status="active",
                    auto_open=True, device_key=f"zz-{uuid.uuid4().hex[:10]}")
    cam_out = Device(id=str(uuid.uuid4()), site_id=site.id, name="ZZ гарах", device_type="camera",
                     ip_address="10.255.9.2", lane_no=1, lane_dir="exit", status="active",
                     auto_open=True, device_key=f"zz-{uuid.uuid4().hex[:10]}")
    db.add_all([cam_in, cam_out]); db.flush(); made += [cam_in, cam_out]
    db.commit()
    ensure_lane_barriers(db)
    db.commit()

    dedup = int(_barrier_rules(db, site.id)["dedup_seconds"])
    xr = _exit_rules(db, site.id)
    fake_min = int(xr["fake_exit_minutes"])
    window = int(xr.get("exit_reread_seconds") or 0)
    print(f"(дүрэм: dedup {dedup}с · fake_exit {fake_min}м · exit_reread {window}с · "
          f"no_session_fee {xr.get('no_session_fee')})")
    gap = dedup + 5                      # dedup цонхноос ЯГ ХОЙШ (35с)

    print("\n6. plates_reread_similar")
    check("1 зөрүү → True", plates_reread_similar(P, P_MIS))
    check("2 зөрүү → True", plates_reread_similar(R, R_MIS))
    check("3 зөрүү → False", not plates_reread_similar("1234ХАМ", "1239ХЭЦ"))
    check("хоосон → False", not plates_reread_similar("", P))

    def seed_2942(entry_a_ago_min):
        now = datetime.utcnow()
        a = ParkingSession(id=str(uuid.uuid4()), site_id=site.id, plate_number=P,
                           entry_time=now - timedelta(minutes=entry_a_ago_min),
                           exit_time=now - timedelta(minutes=entry_a_ago_min - 0.8),
                           entry_device_id=cam_in.id, exit_device_id=cam_out.id,
                           exit_confirmed=True, status="FREE", total_fee=0, base_fee=0,
                           vat_amount=0, duration_minutes=0)
        b = ParkingSession(id=str(uuid.uuid4()), site_id=site.id, plate_number=P,
                           entry_time=now - timedelta(minutes=10),
                           exit_time=now - timedelta(seconds=gap),
                           entry_device_id=cam_in.id, exit_device_id=cam_out.id,
                           exit_confirmed=True, status="FREE", total_fee=0, base_fee=0,
                           vat_amount=0, duration_minutes=9)
        ev = LprEvent(site_id=site.id, device_id=cam_out.id, plate_number=P, lane_dir="exit",
                      confidence=90, accepted=True, raw={},
                      created_at=now - timedelta(seconds=gap))
        db.add_all([a, b, ev]); db.commit()
        return a.id, b.id

    print("\n1. 2942УКН дараалал → давтан уншилт, 08:54 сэргээгдэхгүй")
    if window <= 0:
        check("exit_reread_seconds анхдагч > 0", False, window)
    a_id, b_id = seed_2942(128)
    r = asyncio.run(handle_exit(db, cam_out, P_MIS, 78, raw(P_MIS)))
    check("action = reread", r.get("action") == "reread", r)
    live = [s for s in sessions(site.id, [P, P_MIS]) if s.status in ("OPEN", "AWAITING_PAYMENT", "PAID")]
    check("идэвхтэй/нэхэмжлэлтэй сешн үүсээгүй", not live, [(s.plate_number, s.status) for s in live])
    check("08:54-ийн сешн FREE хэвээр", db.get(ParkingSession, a_id).status == "FREE")
    check("AUTO_REOPEN бичигдээгүй", reopen_logs([a_id, b_id]) == 0)
    check("EXIT_REREAD аудит", db.query(AuditLog).filter(AuditLog.action == "EXIT_REREAD",
                                                         AuditLog.entity_id == b_id).count() == 1)

    print("\n2. exit_reread унтраалттай ч дараагийн зогсолттой бол сэргээхгүй")
    db.query(ParkingSession).filter(ParkingSession.site_id == site.id,
                                    ParkingSession.plate_number.in_([P, P_MIS])).delete(synchronize_session=False)
    db.query(LprEvent).filter(LprEvent.site_id == site.id,
                              LprEvent.plate_number.in_([P, P_MIS])).delete(synchronize_session=False)
    db.commit()
    a_id, b_id = seed_2942(128)
    orig = A.get_rules
    A.get_rules = lambda _db, group, site_id=None: (
        {**orig(_db, group, site_id), "exit_reread_seconds": 0} if group == A.EXITRULES_KEY
        else orig(_db, group, site_id))
    try:
        r = asyncio.run(handle_exit(db, cam_out, P_MIS, 78, raw(P_MIS)))
    finally:
        A.get_rules = orig
    check("reread биш (унтраалттай)", r.get("action") != "reread", r)
    check("08:54-ийн сешн FREE хэвээр (сэргээгдээгүй)", db.get(ParkingSession, a_id).status == "FREE",
          db.get(ParkingSession, a_id).status)
    check("AUTO_REOPEN бичигдээгүй", reopen_logs([a_id, b_id]) == 0)

    print("\n3. Жинхэнэ хуурамч гарц (дараагийн зогсолтгүй) → сэргээнэ")
    now = datetime.utcnow()
    q = ParkingSession(id=str(uuid.uuid4()), site_id=site.id, plate_number=Q,
                       entry_time=now - timedelta(hours=5),
                       exit_time=now - timedelta(hours=5) + timedelta(minutes=max(fake_min - 1, 0) or 0.5),
                       entry_device_id=cam_in.id, exit_device_id=cam_out.id, exit_confirmed=True,
                       status="FREE", total_fee=0, base_fee=0, vat_amount=0, duration_minutes=0)
    q.payment_wait_started_at = now - timedelta(hours=5)      # хуучин хүлээлтийн тамга
    q.payment_quote_until = now - timedelta(hours=5)
    db.add(q); db.commit()
    r = asyncio.run(handle_exit(db, cam_out, Q, 90, raw(Q)))
    qq = db.get(ParkingSession, q.id); db.refresh(qq)
    check("сэргээж төлбөр нэхэв", qq.status == "AWAITING_PAYMENT" and float(qq.total_fee or 0) > 0,
          f"{r} {qq.status} {qq.total_fee}")
    check("AUTO_REOPEN short_fake_exit", reopen_logs([q.id]) == 1)
    check("9. payment_wait_started_at шинэчлэгдэв", qq.payment_wait_started_at is not None
          and qq.payment_wait_started_at > datetime.utcnow() - timedelta(minutes=1),
          qq.payment_wait_started_at)

    print("\n4. Орох уншилтгүй машин: 2000₮ → 40с дараах давтан уншилт 2 дахь нэхэмжлэл үүсгэхгүй")
    r1 = asyncio.run(handle_exit(db, cam_out, R, 90, raw(R)))
    flat = r1.get("action") == "no_session_fee"
    if not flat:
        print(f"  (энэ DB-д no_session_fee унтраалттай: {r1.get('action')} — 4-р кейсийг алгасав)")
    else:
        # эхний уншилтыг dedup цонхноос хойш шилжүүлнэ
        db.query(LprEvent).filter(LprEvent.site_id == site.id, LprEvent.plate_number == R).update(
            {"created_at": datetime.utcnow() - timedelta(seconds=gap)}, synchronize_session=False)
        db.query(ParkingSession).filter(ParkingSession.site_id == site.id,
                                        ParkingSession.plate_number == R).update(
            {"updated_at": datetime.utcnow() - timedelta(seconds=gap)}, synchronize_session=False)
        db.commit()
        r2 = asyncio.run(handle_exit(db, cam_out, R_MIS, 70, raw(R_MIS)))
        check("2 дахь уншилт reread", r2.get("action") == "reread", r2)
        n = [s for s in sessions(site.id, [R, R_MIS]) if s.status == "AWAITING_PAYMENT"]
        check("суурь хураамжийн сешн ЯГ НЭГ", len(n) == 1, [(s.plate_number, s.total_fee) for s in n])

    print("\n5. Цонхноос гадуур (15 мин) → давтан гэж тооцохгүй")
    now = datetime.utcnow()
    t = ParkingSession(id=str(uuid.uuid4()), site_id=site.id, plate_number=T,
                       entry_time=now - timedelta(minutes=40), exit_time=now - timedelta(minutes=15),
                       entry_device_id=cam_in.id, exit_device_id=cam_out.id, exit_confirmed=True,
                       status="CLOSED", total_fee=1000, base_fee=909, vat_amount=91, duration_minutes=25)
    db.add(t)
    db.add(LprEvent(site_id=site.id, device_id=cam_out.id, plate_number=T, lane_dir="exit",
                    confidence=90, accepted=True, raw={}, created_at=now - timedelta(minutes=15)))
    db.commit()
    r = asyncio.run(handle_exit(db, cam_out, T_MIS, 80, raw(T_MIS)))
    check("reread биш", r.get("action") != "reread", r)

    print("\n7. Гарцад төлсний дараах ЯГ ИЖИЛ дугаарын давтан уншилт → 2000₮ үүсэхгүй")
    now = datetime.utcnow()
    u = ParkingSession(id=str(uuid.uuid4()), site_id=site.id, plate_number=U,
                       entry_time=now - timedelta(hours=2), exit_time=now - timedelta(seconds=14),
                       paid_at=now - timedelta(seconds=14),
                       entry_device_id=cam_in.id, exit_device_id=cam_out.id, exit_confirmed=True,
                       status="CLOSED", total_fee=2000, base_fee=1818, vat_amount=182,
                       duration_minutes=120)
    db.add(u)
    db.add(LprEvent(site_id=site.id, device_id=cam_out.id, plate_number=U, lane_dir="exit",
                    confidence=90, accepted=True, raw={}, created_at=now - timedelta(minutes=3)))
    db.commit()
    r = asyncio.run(handle_exit(db, cam_out, U, 92, raw(U)))
    check("action = reread", r.get("action") == "reread", r)
    n = [x for x in sessions(site.id, [U]) if x.status in ("OPEN", "AWAITING_PAYMENT")]
    check("суурь хураамжийн сешн үүсээгүй", not n, [(x.status, x.total_fee) for x in n])
    check("хаалт дахин нээгдэв (эрхтэй)", r.get("barrier_opened") is True, r)

    print("\n8. 2 тэмдэгтийн зөрүү 120с дараа → давтан биш")
    now = datetime.utcnow()
    w = ParkingSession(id=str(uuid.uuid4()), site_id=site.id, plate_number=W,
                       entry_time=now - timedelta(minutes=30), exit_time=now - timedelta(seconds=120),
                       entry_device_id=cam_in.id, exit_device_id=cam_out.id, exit_confirmed=True,
                       status="FREE", total_fee=0, base_fee=0, vat_amount=0, duration_minutes=28)
    db.add(w); db.commit()
    check("≤2 зөрүү (урьдчилсан нөхцөл)", plates_reread_similar(W, W2))
    r = asyncio.run(handle_exit(db, cam_out, W2, 80, raw(W2)))
    check("reread биш", r.get("action") != "reread", r)

    def short_closed(plate, **kw):
        now = datetime.utcnow()
        x = ParkingSession(id=str(uuid.uuid4()), site_id=site.id, plate_number=plate,
                           entry_time=now - timedelta(hours=4), exit_time=now - timedelta(hours=4) + timedelta(seconds=40),
                           entry_device_id=cam_in.id, exit_device_id=cam_out.id, exit_confirmed=True,
                           status="FREE", total_fee=0, base_fee=0, vat_amount=0, duration_minutes=0, **kw)
        db.add(x); db.commit()
        return x.id

    print("\n10. Суурь хураамжийн (fee_locked) сешн сэргээгдэхгүй")
    fid = short_closed(F, fee_locked=True, note="Орох уншилтгүй — суурь хураамж")
    asyncio.run(handle_exit(db, cam_out, F, 90, raw(F)))
    check("сэргээгээгүй", reopen_logs([fid]) == 0 and db.get(ParkingSession, fid).status == "FREE")

    print("\n11. 1 тэмдэгтээр өөр дугаар богино зогсолтыг сэргээхгүй")
    kid = short_closed(K)
    asyncio.run(handle_exit(db, cam_out, K2, 90, raw(K2)))
    check("сэргээгээгүй", reopen_logs([kid]) == 0 and db.get(ParkingSession, kid).status == "FREE")

    print("\n12. Орох камер гарцаас хойш дахин уншсан (сешнгүй) → сэргээхгүй")
    eid = short_closed(E)
    db.add(LprEvent(site_id=site.id, device_id=cam_in.id, plate_number=E, lane_dir="entry",
                    confidence=90, accepted=True, raw={},
                    created_at=datetime.utcnow() - timedelta(hours=2)))
    db.commit()
    asyncio.run(handle_exit(db, cam_out, E, 90, raw(E)))
    check("сэргээгээгүй", reopen_logs([eid]) == 0 and db.get(ParkingSession, eid).status == "FREE")

    print("\n13. Гэрээт машин давтан уншилтын замд орохгүй (ердийн «гэрээт гарц»)")
    from app.models import RegisteredDriver
    G, G2 = "3737ХЖЗ", "3737ХЖЭ"
    now = datetime.utcnow()
    db.add(ParkingSession(id=str(uuid.uuid4()), site_id=site.id, plate_number=G2,
                          entry_time=now - timedelta(minutes=20), exit_time=now - timedelta(seconds=40),
                          entry_device_id=cam_in.id, exit_device_id=cam_out.id, exit_confirmed=True,
                          status="FREE", total_fee=0, base_fee=0, vat_amount=0, duration_minutes=19))
    drv = RegisteredDriver(id=str(uuid.uuid4()), plate_number=G, full_name="ТЕСТ гэрээт",
                           site_id=site.id, valid_from=now - timedelta(days=1),
                           valid_to=now + timedelta(days=30))
    db.add(drv); db.commit(); made.append(drv)
    r = asyncio.run(handle_exit(db, cam_out, G, 90, raw(G)))
    check("registered_exit", r.get("action") == "registered_exit", r)

finally:
    db.rollback()
    if site is not None:
        sess_ids = [x[0] for x in db.query(ParkingSession.id)
                    .filter(ParkingSession.site_id == site.id).all()]
        site_devs = [x[0] for x in db.query(Device.id).filter(Device.site_id == site.id).all()]
        if sess_ids:
            db.query(Payment).filter(Payment.session_id.in_(sess_ids)).delete(synchronize_session=False)
            db.query(BarrierCommand).filter(BarrierCommand.session_id.in_(sess_ids)).delete(synchronize_session=False)
            db.query(Compensation).filter(Compensation.session_id.in_(sess_ids)).delete(synchronize_session=False)
            db.query(AuditLog).filter(AuditLog.entity_id.in_(sess_ids)).delete(synchronize_session=False)
        if site_devs:
            db.query(BarrierCommand).filter(BarrierCommand.device_id.in_(site_devs)).delete(synchronize_session=False)
            db.query(LprEvent).filter(LprEvent.device_id.in_(site_devs)).delete(synchronize_session=False)
        db.query(ParkingSession).filter(ParkingSession.site_id == site.id).delete(synchronize_session=False)
        db.query(Device).filter(Device.site_id == site.id).delete(synchronize_session=False)
        db.commit()
    for obj in reversed(made):
        if isinstance(obj, Device):
            continue
        try:
            db.delete(obj); db.commit()
        except Exception as e:  # noqa: BLE001
            db.rollback(); print(f"  (цэвэрлэгээ: {e})")
    db.close()

print(f"\n{PASS} ✓  {FAIL} ✗")
sys.exit(1 if FAIL else 0)
