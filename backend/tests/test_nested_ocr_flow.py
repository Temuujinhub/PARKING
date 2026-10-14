"""Доторх зогсоол — OCR-ойролцоо тохирлын БҮТЭН live урсгал (Postgres, 2026-10-14).

    cd backend && PARKING_DATABASE_URL=.../t_nested venv/bin/python tests/test_nested_ocr_flow.py

audit1014-ийн нотолгоог хиймэл дугаараар (98xxЖЖА) давтана:
  a. 7969УАА хэв маяг — гадна «98x1ЖЖА», доторх орох «98x1ЖЖ» (тайрагдсан, формат буруу):
     гадна тоолуур зогсож, доторх сешн гадна дугаараар засагдаж, хаалт ШУУД нээгдэнэ;
     8ц дараах доторх гарах уншилт ердийн гарцаар таарч гадна гарц 0₮.
  b. 0450УЕМ/0450УБ — Б/Е андуурал + тайралт, мөн ижил дүн.
  c. 6238ӨМҮ хэв маяг — гарцад төлбөр хүлээж (AWAITING_PAYMENT) байгаад дотогш буцав:
     гадна сешн OPEN болж зогсоно, дараагийн гадна гарц дотор өнгөрүүлснийг хасна,
     «орох уншилтгүй» суурь хураамжийн сешн үүсэхгүй.
  d. Уралдаан — тэр мөрийг төлбөр (өөр транзакц) ТҮГЖСЭН үед доторх орох уншилт:
     буцаалт хийгдэхгүй, алдаагүй, доторх сешн/хаалт хэвийн (skip_locked).
  e. Доторх гарах уншилтын нөхөлт — доторх сешнгүй (хувилбар нь өөр), гадна сешн ЗОГССОН:
     батлагдсан гарц, ДОТООД гарах хаалт нээгдэнэ; гадна хаалтад команд 0; давтан уншилт
     дахин тооцохгүй; үл мэдэгдэх машин review, хаалт 0.
  f. 100 айлын 24ц: эцэг дээр inner_unverified_cap_hours=24 — доторх гарах уншилтгүй
     30ц зогсолтоос 1,440 мин хасна (тест дуусахад тохиргоог буцаана).
  g. Хяналт F1 (шүүмжлэгчийн zz_rev3_skiplock_probe): OPEN гадна мөрийг өөр транзакц
     0.3с түгжсэн үед доторх орох уншилт — зогсолт АЛДАГДАХГҮЙ (e7c5a8a шиг хүлээнэ;
     өмнөх засвар skip_locked-оор чимээгүй алгасаж 0₮ → 3,000₮ болгож байв).
"""
import asyncio
import os
import sys
import threading
import uuid
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings  # noqa: E402

settings.barrier_mock = True
settings.snapshot_enabled = False
settings.screen_enabled = False
settings.transit_max_hours = 4

from sqlalchemy import text  # noqa: E402

from app.database import SessionLocal, engine  # noqa: E402

_DBNAME = engine.url.database or ""
if _DBNAME == "parking" or _DBNAME.startswith("audit"):
    print(f"ТАТГАЛЗАВ: «{_DBNAME}» дээр ажиллуулахгүй — тусдаа тестийн DB ашиглана уу.")
    sys.exit(2)

import app.services.app_settings as A  # noqa: E402
from app.models import (  # noqa: E402
    AuditLog, BarrierCommand, Device, LprEvent, ParkingSession, ParkingSite,
    TariffTemplate, TariffTier,
)
from app.services.nested import close_open_pause, unverified_cap_minutes  # noqa: E402
from app.session_logic import handle_entry, handle_exit, session_fee_info  # noqa: E402

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗ <<< FAIL'} {name}{'' if cond else f'  [{extra}]'}")


db = SessionLocal()
made: list = []
site_ids: list = []
overlay_sites: list = []


def run(coro):
    return asyncio.run(coro)


def raw(p):
    return {"Picture": {"Plate": {"PlateNumber": p}}}


def mk_pair():
    tpl = TariffTemplate(id=str(uuid.uuid4()), name="ZZ-ocr-flow", free_minutes=30,
                         grace_minutes=15, extra_hour_price=1000, daily_cap=25000)
    db.add(tpl)
    db.flush()
    made.append(tpl)
    tier = TariffTier(id=str(uuid.uuid4()), template_id=tpl.id, upto_minutes=60, price=1000)
    db.add(tier)
    db.flush()
    made.append(tier)
    outer = ParkingSite(id=str(uuid.uuid4()), name="ZZ-гадна", site_code=f"ZO{uuid.uuid4().hex[:7]}",
                        tariff_template_id=tpl.id, capacity=100, transit_max_hours=0)
    db.add(outer)
    db.flush()
    inner = ParkingSite(id=str(uuid.uuid4()), name="ZZ-дотор", site_code=f"ZI{uuid.uuid4().hex[:7]}",
                        parent_site_id=outer.id, no_charge=True, capacity=20,
                        tariff_template_id=tpl.id)
    db.add(inner)
    db.flush()
    made.extend([outer, inner])
    site_ids.extend([outer.id, inner.id])
    devs = {}
    for site, tag in ((outer, "o"), (inner, "i")):
        for lane_dir, lane in (("entry", 1), ("exit", 2)):
            ip = f"10.78.{uuid.uuid4().int % 250}.{uuid.uuid4().int % 250}"
            cam = Device(id=str(uuid.uuid4()), site_id=site.id, name=f"ZZ {tag}-{lane_dir}",
                         device_type="camera", ip_address=ip, lane_no=lane, lane_dir=lane_dir,
                         status="active", auto_open=True, device_key=f"zz-{uuid.uuid4().hex[:10]}")
            bar = Device(id=str(uuid.uuid4()), site_id=site.id, name=f"ZZ {tag}-{lane_dir} хаалт",
                         device_type="barrier", ip_address="", lane_no=lane, lane_dir=lane_dir,
                         status="active", auto_open=True, device_key=f"zz-{uuid.uuid4().hex[:10]}")
            db.add_all([cam, bar])
            db.flush()
            made.extend([cam, bar])
            devs[tag + lane_dir] = cam
            devs[tag + lane_dir + "_bar"] = bar
    db.commit()
    return outer, inner, devs


def shift(hours, sites):
    """Тестийн зогсоолуудын БҮХ цагийг ухрааж «цаг өнгөрснийг» дуурайна."""
    args = {"h": float(hours) * 3600, "sids": [str(s.id) for s in sites]}
    db.execute(text(
        "update parking_sessions set entry_time=entry_time-make_interval(secs=>:h), "
        "exit_time=exit_time-make_interval(secs=>:h), paused_since=paused_since-make_interval(secs=>:h), "
        "updated_at=updated_at-make_interval(secs=>:h), "
        "last_exit_seen_at=last_exit_seen_at-make_interval(secs=>:h), "
        "payment_wait_started_at=payment_wait_started_at-make_interval(secs=>:h), "
        "payment_quote_until=payment_quote_until-make_interval(secs=>:h) "
        "where site_id::text = any(:sids)"), args)
    db.execute(text("update lpr_events set created_at=created_at-make_interval(secs=>:h) "
                    "where site_id::text = any(:sids)"), args)
    db.execute(text("update barrier_commands set created_at=created_at-make_interval(secs=>:h) "
                    "where device_id in (select id from devices where site_id::text = any(:sids))"),
               args)
    db.commit()
    db.expire_all()


def sessions(site):
    db.expire_all()
    return (db.query(ParkingSession).filter_by(site_id=site.id)
            .order_by(ParkingSession.entry_time).all())


def cmds(dev):
    return db.query(BarrierCommand).filter_by(device_id=dev.id, command="open").count()


try:
    print("\na. 7969УАА хэв маяг — тайрагдсан доторх уншилт, 8ц дотор → 0₮")
    outer, inner, d = mk_pair()
    P = "9811ЖЖА"
    run(handle_entry(db, d["oentry"], P, 94, raw(P)))
    shift(23 / 3600, [outer, inner])
    r = run(handle_entry(db, d["ientry"], "9811ЖЖ", 66, raw("9811ЖЖ")))
    po, = sessions(outer)
    ki, = sessions(inner)
    check("гадна тоолуур зогсов", po.paused_since is not None, po.paused_since)
    check("доторх сешн гадна дугаараар засагдав", ki.plate_number == P, ki.plate_number)
    check("hold-д баригдалгүй доторх ОРОХ хаалт нээгдэв",
          r.get("barrier_opened") and not r.get("held") and cmds(d["ientry_bar"]) == 1, r)
    check("гадна хаалтуудад нэмэлт команд алга",
          cmds(d["oexit_bar"]) == 0, cmds(d["oexit_bar"]))
    shift(8, [outer, inner])
    r = run(handle_exit(db, d["iexit"], P, 90, raw(P)))
    po, = sessions(outer)
    check("доторх гарах уншилт засагдсан сешнтэй таарав", r.get("action") == "exit_completed", r)
    check("гадна тоолуур ~480 мин хасалттай үргэлжилэв",
          po.paused_since is None and 479 <= int(po.paused_minutes or 0) <= 481, po.paused_minutes)
    shift(5 / 60, [outer, inner])
    r = run(handle_exit(db, d["oexit"], P, 90, raw(P)))
    po, = sessions(outer)
    check("гадна гарц 0₮ (өмнө нь 11,000₮)", po.status == "FREE" and float(po.total_fee or 0) == 0,
          (r, po.status, po.total_fee))

    print("\nb. 0450УЕМ/0450УБ — Б/Е андуурал + тайралт")
    outer, inner, d = mk_pair()
    P = "9812УЕМ"
    run(handle_entry(db, d["oentry"], P, 94, raw(P)))
    shift(17 / 3600, [outer, inner])
    run(handle_entry(db, d["ientry"], "9812УБ", 66, raw("9812УБ")))
    po, = sessions(outer)
    check("гадна тоолуур зогсов", po.paused_since is not None)
    a = db.query(AuditLog).filter_by(action="NESTED_OCR_MATCH", entity_id=po.id).one_or_none()
    check("NESTED_OCR_MATCH аудит", a is not None and a.detail.get("read_plate") == "9812УБ",
          a.detail if a else None)

    print("\nc. 6238ӨМҮ хэв маяг — гарцад татгалзсан машин дотогш буцав")
    outer, inner, d = mk_pair()
    P = "9813ЖЖА"
    run(handle_entry(db, d["oentry"], P, 94, raw(P)))
    shift(22, [outer, inner])
    r = run(handle_exit(db, d["oexit"], P, 90, raw(P)))
    po, = sessions(outer)
    check("гадна гарцад төлбөр хүлээв", po.status == "AWAITING_PAYMENT" and r.get("amount_due", 0) > 0, r)
    shift(32 / 3600, [outer, inner])
    run(handle_entry(db, d["ientry"], P, 82, raw(P)))
    po, = sessions(outer)
    check("гадна сешн OPEN болж тоолуур зогсов",
          po.status == "OPEN" and po.paused_since is not None and po.payment_wait_started_at is None
          and po.exit_device_id is None, (po.status, po.paused_since))
    check("NESTED_EXIT_REVERT аудит",
          db.query(AuditLog).filter_by(action="NESTED_EXIT_REVERT", entity_id=po.id).count() == 1)
    fee_out = session_fee_info(db, po)
    shift(3, [outer, inner])
    r = run(handle_exit(db, d["oexit"], P, 90, raw(P)))
    po = sessions(outer)
    check("дараагийн гадна гарц ердийн сешнээр (суурь хураамжийн шинэ сешн алга)",
          len(po) == 1 and r.get("action") == "awaiting_payment", (r, len(po)))
    po = po[0]
    fee_now = session_fee_info(db, po)
    check("дотор өнгөрүүлсэн 3ц хасагдав (дүн өсөөгүй)",
          int(fee_now["paused_minutes"]) >= 179 and fee_now["total_fee"] == fee_out["total_fee"],
          (fee_now, fee_out))

    print("\nd. Уралдаан — төлбөр мөрийг түгжсэн үед доторх орох уншилт")
    outer, inner, d = mk_pair()
    P = "9814ЖЖА"
    run(handle_entry(db, d["oentry"], P, 94, raw(P)))
    shift(5, [outer, inner])
    run(handle_exit(db, d["oexit"], P, 90, raw(P)))
    po, = sessions(outer)
    locked, release = threading.Event(), threading.Event()

    def hold_lock(pid):
        s2 = SessionLocal()
        try:
            s2.execute(text("select id from parking_sessions where id=:i for update"), {"i": pid})
            locked.set()
            release.wait(30)
            s2.rollback()
        finally:
            s2.close()
    th = threading.Thread(target=hold_lock, args=(po.id,))
    th.start()
    locked.wait(10)
    try:
        r = run(handle_entry(db, d["ientry"], P, 82, raw(P)))
    finally:
        release.set()
        th.join(30)
    po, = sessions(outer)
    ki = sessions(inner)
    check("түгжээтэй үед буцаалт хийгдсэнгүй (AWAITING хэвээр)", po.status == "AWAITING_PAYMENT",
          po.status)
    check("доторх сешн үүсэж, хаалт нээгдэв (алдаагүй)",
          len(ki) == 1 and r.get("barrier_opened"), (r, len(ki)))

    print("\ne. Доторх гарах уншилтын нөхөлт")
    outer, inner, d = mk_pair()
    P = "9815ЖЖА"
    po = ParkingSession(id=str(uuid.uuid4()), site_id=outer.id, plate_number=P,
                        entry_time=datetime.utcnow() - timedelta(hours=6, minutes=1),
                        paused_since=datetime.utcnow() - timedelta(hours=6), status="OPEN",
                        paused_minutes=0)
    junk = ParkingSession(id=str(uuid.uuid4()), site_id=inner.id, plate_number="9815ЖЖ",
                          entry_time=datetime.utcnow() - timedelta(hours=6), status="OPEN")
    db.add_all([po, junk])
    db.commit()
    r = run(handle_exit(db, d["iexit"], P, 90, raw(P)))
    db.expire_all()
    check("батлагдсан доторх гарц", r.get("action") == "inner_exit_fallback"
          and po.paused_since is None and 359 <= int(po.paused_minutes or 0) <= 360, r)
    check("ДОТООД гарах хаалт нээгдэв, гадна хаалтад команд 0",
          r.get("barrier_opened") and cmds(d["iexit_bar"]) == 1 and cmds(d["oexit_bar"]) == 0
          and cmds(d["oentry_bar"]) == 0, (cmds(d["iexit_bar"]), cmds(d["oexit_bar"])))
    check("хувилбар доторх сешн хаагдав", junk.status == "FREE" and junk.exit_confirmed, junk.status)
    pm = po.paused_minutes
    r2 = run(handle_exit(db, d["iexit"], P, 90, raw(P)))
    db.expire_all()
    check("давтан уншилт дахин тооцсонгүй", po.paused_minutes == pm and r2.get("action") != "inner_exit_fallback",
          (r2, po.paused_minutes))
    r3 = run(handle_exit(db, d["iexit"], "9816ЖЖК", 90, raw("9816ЖЖК")))
    check("үл мэдэгдэх машин review, хаалт нээгдээгүй",
          r3.get("action") == "review_required" and cmds(d["iexit_bar"]) == 1, r3)

    print("\nf. 100 айлын 24ц — гарах уншилт алдагдсан зогсолт")
    outer, inner, d = mk_pair()
    check("анхдагч 240 мин", unverified_cap_minutes(db, outer.id) == 240)
    A.set_site_rules(db, A.EXITRULES_KEY, outer.id, {"inner_unverified_cap_hours": 24}, "test")
    overlay_sites.append(outer.id)
    db.commit()
    check("эцэг зогсоол дээр 24 → 1440 мин", unverified_cap_minutes(db, outer.id) == 1440)
    P = "9817ЖЖА"
    po = ParkingSession(id=str(uuid.uuid4()), site_id=outer.id, plate_number=P,
                        entry_time=datetime.utcnow() - timedelta(hours=31),
                        paused_since=datetime.utcnow() - timedelta(hours=30), status="OPEN",
                        paused_minutes=0)
    db.add(po)
    db.commit()
    check("доторх гарах уншилтгүй 30ц → 1440 мин хасагдав",
          close_open_pause(db, po, datetime.utcnow()) == 1440, po.paused_minutes)
    db.rollback()

    print("\ng. OPEN гадна мөр түр түгжээтэй үед доторх орох уншилт (хяналт F1)")
    outer, inner, d = mk_pair()
    P = "9861ЖЖА"
    run(handle_entry(db, d["oentry"], P, 94, raw(P)))
    shift(0.01, [outer, inner])
    po, = sessions(outer)
    locked, release = threading.Event(), threading.Event()

    def hold_short(pid, secs):
        s2 = SessionLocal()
        try:
            s2.execute(text("select id from parking_sessions where id=:i for update"), {"i": pid})
            locked.set()
            release.wait(secs)
            s2.rollback()
        finally:
            s2.close()
    th = threading.Thread(target=hold_short, args=(po.id, 0.3))
    th.start()
    locked.wait(10)
    try:
        r = run(handle_entry(db, d["ientry"], P, 82, raw(P)))
    finally:
        th.join(30)
    po, = sessions(outer)
    check("зогсолт түр түгжээг даван хадгалагдав", po.paused_since is not None,
          (r, po.paused_since))
    check("доторх орох хаалт нээгдэв", r.get("barrier_opened"), r)
    shift(3, [outer, inner])
    run(handle_exit(db, d["iexit"], P, 90, raw(P)))
    po, = sessions(outer)
    check("3ц дараах доторх гарц ~180 мин хасав", 179 <= int(po.paused_minutes or 0) <= 181,
          po.paused_minutes)
    check("гадна дүн 0₮ (e7c5a8a-тай ижил)", session_fee_info(db, po)["total_fee"] == 0,
          session_fee_info(db, po))
finally:
    db.rollback()
    for sid in overlay_sites:
        A.set_site_rules(db, A.EXITRULES_KEY, sid, {"inner_unverified_cap_hours": None}, "test")
    db.commit()
    sess_ids = [r[0] for r in db.query(ParkingSession.id).filter(
        ParkingSession.site_id.in_(site_ids)).all()]
    if sess_ids:
        db.query(BarrierCommand).filter(BarrierCommand.session_id.in_(sess_ids)).delete(
            synchronize_session=False)
        db.query(AuditLog).filter(AuditLog.entity_id.in_(sess_ids)).delete(
            synchronize_session=False)
    db.query(LprEvent).filter(LprEvent.site_id.in_(site_ids)).delete(synchronize_session=False)
    if sess_ids:
        from app.models import Compensation, Payment
        db.query(Compensation).filter(Compensation.session_id.in_(sess_ids)).delete(
            synchronize_session=False)
        db.query(Payment).filter(Payment.session_id.in_(sess_ids)).delete(
            synchronize_session=False)
    db.query(ParkingSession).filter(ParkingSession.id.in_(sess_ids)).delete(
        synchronize_session=False)
    db.commit()
    dev_ids = [x.id for x in made if isinstance(x, Device)]
    if dev_ids:
        db.query(BarrierCommand).filter(BarrierCommand.device_id.in_(dev_ids)).delete(
            synchronize_session=False)
        db.commit()
    for obj in reversed(made):
        try:
            db.delete(obj)
            db.commit()
        except Exception as e:  # noqa: BLE001
            db.rollback()
            print("  цэвэрлэгээ:", type(obj).__name__, e)
    left = db.query(ParkingSession.id).filter(ParkingSession.site_id.in_(site_ids)).count()
    print(f"\n  цэвэрлэгээ: тестийн зогсоолын сешн үлдсэн {left}")
    db.close()

print("\n" + "=" * 54)
print(f"PASS={PASS} FAIL={FAIL}")
sys.exit(1 if FAIL else 0)
