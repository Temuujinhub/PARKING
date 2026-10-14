"""Тусдаа site-тай (parent/child) доторх зогсоол — сэргээлт, camera_sync, хязгаарын дүрэм.

    cd backend && venv/bin/python tests/test_nested_reopen_pause.py

Зөвхөн ТЕСТИЙН өгөгдлийн сан дээр ажиллуулна (`parking`, `audit*` нэртэй бол
шууд татгалзана) — өөрийн мөрүүдийг үүсгээд төгсгөлд нь цэвэрлэнэ.

2026-10-13 аудит (audit1013, 100 айл / Хангарьд) — доторх хугацаа гадна
төлбөрөөс хасагдаагүй эсвэл хэт хасагдсан хэв маягууд:

  a. Доторх ГАРАХ камер дөнгөж орж буй машиныг уншиж (15с) хүүхэд сешн
     хаагдана → жинхэнэ гарахад сешн сэргэсэн ч эцгийн тоолуур сэргээгүй
     (8507УБР: 7ц дотор → 25,000₮; зөв дүн 0₮).
  b. Гадна ГАРАХ камер орж буй машиныг уншиж эцэг сешн хаагдана → дотор 11ц
     байгаад гарахад эцэг сешн сэргэсэн ч доторх хугацаа хасагдаагүй (4216УБО).
  d. camera_sync логоос нөхсөн доторх сешн эцгийн тоолуурыг огт зогсоогоогүй
     (5508УАТ, 8333УАО, 3016УНЕ).
  e. camera_sync доторх сешнийг логийн гарах уншилтаар хаахад эцгийн тоолуур
     ТЭР цагаас үргэлжилнэ (дотор 30 мин, гадаа 7ц → төлбөртэй). Харин
     «хуурамч гарц»-ын босгоос богино бол энэ нь доторх ГАРАХ камерын
     cross-read (1937УАЕ 10-09: орсноос 51с, машин 7ц ДОТОР байсан) — эцгийн
     зогсолт нээлттэй үлдэж прод-оос илүү нэхэхгүй (7224УБС: жинхэнэ гарц
     live ирвэл 0₮).
  f. Хуучин (гарах уншилтгүй) доторх сешн дахин ашиглагдахад зогсолт өчигдрийн
     цагаас эхэлж бүх гадна хугацаа хасагдана (8810УКР).
  g. Хар жагсаалтаар хориглогдсон (хаалт нээгдээгүй) машины эцгийн тоолуур зогсдог.
  h. Цуврал уншилтын autocorrect замд (junk → зөв дугаар) хаалт нээгдсэн ч
     эцгийн тоолуур зогсдоггүй.
  i. Хязгаарын дүрэм: 0 = хязгааргүй нь зөвхөн доторх ГАРАХ уншилтаар
     БАТЛАГДСАН зогсолтод; батлагдаагүйд глобал хязгаар (НЭГ site загварт хэвээр).
  j–m. Төлбөрийн дүрмийн #9 анхааруулга (өвлөсөн 0), админы гараар сэргээх ба
     камерын логоос нөхөх, жолоочийн «Хамрах хүрээ» эцэг зогсоолд.
  n. Эцэг сэргэхэд ӨМНӨХ айлчлалын (phantom) хүүхэд сешн гадаа зогссон
     хугацааг «дотор» гэж хасахгүй; дахин ашиглагдсан бол ОРОХ уншилтаас.
  o. «Зөвхөн бүртгэлтэй» доторх зогсоолд татгалзсан машины хүүхэд сешн эцэг
     сэргэхэд хасалт өгөхгүй (Рашбулаг шороо: 19 сешн, хаалтны команд 0).
  p. Гараар сэргээсэн хүүхэд сешнийг нотолгоогүй албадан хаахад эцгийн
     зогсолт «батлагдсан» (хязгааргүй) болж барагдахгүй.

Дугаарууд нь ХИЙМЭЛ (99xxЖЖА) — бодит харилцагчийн мөрөнд хүрэхгүй; цаг
шилжүүлэх ба цэвэрлэгээ зөвхөн тестийн зогсоолуудаар хязгаарлагдана.
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
settings.transit_max_hours = 4          # глобал default = 240 мин

from sqlalchemy import text  # noqa: E402

from app.database import SessionLocal, engine  # noqa: E402

_DBNAME = engine.url.database or ""
if _DBNAME == "parking" or _DBNAME.startswith("audit"):
    print(f"ТАТГАЛЗАВ: «{_DBNAME}» өгөгдлийн сан дээр ажиллуулахгүй — тусдаа тестийн DB "
          "(PARKING_DATABASE_URL=.../t_xxx) ашиглана уу.")
    sys.exit(2)

import app.services.app_settings as app_settings  # noqa: E402
import app.services.camera_sync as camera_sync  # noqa: E402
from app.models import (  # noqa: E402
    AuditLog, BarrierCommand, BlacklistEntry, Compensation, Device, LprEvent,
    ParkingSession, ParkingSite, RegisteredDriver, TariffTemplate, TariffTier,
)
from app.services.app_settings import CAMSYNC_STATE, get_camsync_rules, get_state, set_state  # noqa: E402
from app.services.nested import (  # noqa: E402
    close_open_pause, credit_backfilled_child, effective_paused_minutes,
    on_inner_entry, on_inner_exit, pause_cap_minutes, unverified_cap_minutes,
)
from app.services.payment_rules import check_conflicts  # noqa: E402
from app.session_logic import handle_entry, handle_exit, session_fee_info  # noqa: E402

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗ <<< FAIL'} {name}{'' if cond else f'  [{extra}]'}")


# Дугаарууд ХИЙМЭЛ (99xxЖЖА — бодит харилцагчийн мөрөнд хүрэхгүй). Цаг шилжүүлэх
# ба цэвэрлэгээ тестийн зогсоолоор (`site_ids`) шүүгдэнэ — DB даяар дугаараар биш.
db = SessionLocal()
made: list = []
site_ids: list = []


def run(coro):
    return asyncio.run(coro)


def raw(p):
    return {"Picture": {"Plate": {"PlateNumber": p}}}


def mk_tariff():
    tpl = TariffTemplate(id=str(uuid.uuid4()), name="ZZ-nested-reopen", free_minutes=30,
                         grace_minutes=15, extra_hour_price=1000)
    db.add(tpl)
    db.flush()
    made.append(tpl)
    tier = TariffTier(id=str(uuid.uuid4()), template_id=tpl.id, upto_minutes=60, price=1000)
    db.add(tier)
    db.flush()
    made.append(tier)
    return tpl


def mk_pair(tpl, parent_cap=0, child_cap=None, **child_kw):
    """Гадна (эцэг) + доторх (хүүхэд) зогсоол, тус бүр орох/гарах камер + хаалт."""
    outer = ParkingSite(id=str(uuid.uuid4()), name="ZZ-гадна", site_code=f"ZO{uuid.uuid4().hex[:7]}",
                        tariff_template_id=tpl.id, capacity=100, transit_max_hours=parent_cap)
    db.add(outer)
    db.flush()
    inner = ParkingSite(id=str(uuid.uuid4()), name="ZZ-дотор", site_code=f"ZI{uuid.uuid4().hex[:7]}",
                        parent_site_id=outer.id, no_charge=True, capacity=20,
                        tariff_template_id=tpl.id, transit_max_hours=child_cap, **child_kw)
    db.add(inner)
    db.flush()
    made.extend([outer, inner])          # урвуугаар устгана: inner → outer (FK)
    site_ids.extend([outer.id, inner.id])
    devs = {}
    for site, tag in ((outer, "o"), (inner, "i")):
        for lane_dir, lane in (("entry", 1), ("exit", 2)):
            ip = f"10.77.{uuid.uuid4().int % 250}.{uuid.uuid4().int % 250}"
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
    db.commit()
    return outer, inner, devs


def shift(plate, hours):
    """Тухайн дугаарын ТЕСТИЙН зогсоол дээрх бүх цагийг `hours` цагаар ухрааж
    «цаг өнгөрснийг» дуурайна."""
    secs = float(hours) * 3600
    args = {"h": secs, "p": plate, "sids": [str(x) for x in site_ids]}
    db.execute(text(
        "update parking_sessions set entry_time=entry_time-make_interval(secs=>:h), "
        "exit_time=exit_time-make_interval(secs=>:h), paused_since=paused_since-make_interval(secs=>:h), "
        "updated_at=updated_at-make_interval(secs=>:h), "
        "last_exit_seen_at=last_exit_seen_at-make_interval(secs=>:h), "
        "payment_wait_started_at=payment_wait_started_at-make_interval(secs=>:h) "
        "where plate_number=:p and site_id::text = any(:sids)"), args)
    db.execute(text("update lpr_events set created_at=created_at-make_interval(secs=>:h) "
                    "where plate_number=:p and site_id::text = any(:sids)"), args)
    db.commit()
    db.expire_all()


def sess(site, plate):
    db.expire_all()
    return (db.query(ParkingSession).filter_by(site_id=site.id, plate_number=plate)
            .order_by(ParkingSession.entry_time.desc()).first())


def camsync(site, events, exits=()):
    """camera_sync.sync_site-ийг камерын хиймэл логоор ажиллуулна (сүлжээгүй)."""
    cam = {"cameras": [{"ip": "10.0.0.1", "lane_dir": "entry", "error": None},
                       {"ip": "10.0.0.2", "lane_dir": "exit", "error": None}],
           "events": ([{"lane_dir": "entry", "plate": p, "time": t} for p, t in events]
                      + [{"lane_dir": "exit", "plate": p, "time": t} for p, t in exits]),
           "inner_events": []}
    orig = camera_sync.site_camera_events
    camera_sync.site_camera_events = lambda _db, _sid, hours=None: cam
    try:
        rules = {**get_camsync_rules(db), "min_age_minutes": 1, "lookback_hours": 48,
                 "create_debt_log_exit": False, "skip_invalid_plate": True}
        return camera_sync.sync_site(db, site, rules)
    finally:
        camera_sync.site_camera_events = orig


try:
    tpl = mk_tariff()

    print("\na. Доторх ГАРАХ камер орж буй машиныг уншив (15с); жинхэнэ гарц 7ц дараа (8507УБР)")
    outer, inner, d = mk_pair(tpl)
    P = "9901ЖЖА"
    run(handle_entry(db, d["oentry"], P, 0.95, raw(P)))
    run(handle_entry(db, d["ientry"], P, 0.95, raw(P)))
    r1 = run(handle_exit(db, d["iexit"], P, 0.95, raw(P)))
    check("хуурамч гарц хүүхэд сешнийг хаалаа, эцгийн тоолуур үргэлжлэв",
          sess(outer, P).paused_since is None, r1.get("action"))
    shift(P, 7)
    r2 = run(handle_exit(db, d["iexit"], P, 0.95, raw(P)))
    po = sess(outer, P)
    check("жинхэнэ доторх гарц: хүүхэд АВТО сэргэж хаагдав", r2.get("action") == "exit_completed", r2)
    check("эцэгт ~420 мин хасалт (эцгийн 0 = батлагдсан бол хязгааргүй)",
          po.paused_minutes >= 418, po.paused_minutes)
    r3 = run(handle_exit(db, d["oexit"], P, 0.95, raw(P)))
    po = sess(outer, P)
    check("гадна гарц үнэгүй (дотор хугацаа хасагдсан)",
          po.status == "FREE" and float(po.total_fee or 0) == 0,
          (r3.get("action"), po.status, po.total_fee, po.paused_minutes))

    print("\nb. Гадна ГАРАХ камер орж буй машиныг уншив (14с); дотор 11ц (4216УБО)")
    outer, inner, d = mk_pair(tpl)
    P = "9902ЖЖА"
    run(handle_entry(db, d["oentry"], P, 0.95, raw(P)))
    run(handle_exit(db, d["oexit"], P, 0.95, raw(P)))
    check("хуурамч гарц эцэг сешнийг FREE хаалаа", sess(outer, P).status == "FREE")
    run(handle_entry(db, d["ientry"], P, 0.95, raw(P)))
    check("доторх орох: идэвхтэй эцэг алга → юу ч зогсоогоогүй",
          not sess(outer, P).paused_minutes and sess(outer, P).paused_since is None)
    shift(P, 11)
    run(handle_exit(db, d["iexit"], P, 0.95, raw(P)))
    shift(P, 1 / 60)      # доторх гарцаас 1 мин дараа гадна гарц
    r = run(handle_exit(db, d["oexit"], P, 0.95, raw(P)))
    po = sess(outer, P)
    check("жинхэнэ гадна гарц: эцэг АВТО сэргэв",
          db.query(AuditLog).filter_by(entity_id=po.id, action="AUTO_REOPEN").count() == 1)
    check("хаалттай үеийн доторх ~660 мин дахин хасагдав", po.paused_minutes >= 655,
          po.paused_minutes)
    check("сэргэсэн эцэг үнэгүй", po.status == "FREE" and float(po.total_fee or 0) == 0,
          (r.get("action"), po.status, po.total_fee))

    print("\nb2. Жинхэнэ эргэлт (дотогш ороогүй) — сэргэхэд хасалт нэмэгдэхгүй")
    outer, inner, d = mk_pair(tpl)
    P = "9903ЖЖА"
    run(handle_entry(db, d["oentry"], P, 0.95, raw(P)))
    run(handle_exit(db, d["oexit"], P, 0.95, raw(P)))
    shift(P, 3)
    r = run(handle_exit(db, d["oexit"], P, 0.95, raw(P)))
    po = sess(outer, P)
    check("сэргэсэн эцэг энгийнээр төлбөртэй (доторх хасалтгүй)",
          not po.paused_minutes and r.get("action") == "awaiting_payment",
          (r.get("action"), po.paused_minutes, po.total_fee))

    print("\nd. camera_sync логоос нөхсөн доторх сешн эцгийн тоолуурыг зогсооно (5508УАТ)")
    outer, inner, d = mk_pair(tpl)
    P = "9904ЖЖА"
    run(handle_entry(db, d["oentry"], P, 0.95, raw(P)))
    shift(P, 9)
    po = sess(outer, P)
    t_in = po.entry_time + timedelta(minutes=2)
    res = camsync(inner, [(P, t_in)])
    po = sess(outer, P)
    check("нөхсөн доторх сешн үүсэв (гарах уншилтгүй → OPEN)",
          res.get("created") == 1 and sess(inner, P) is not None
          and sess(inner, P).status == "OPEN", res)
    check("эцгийн тоолуур логийн орох цагаас зогсов",
          po.paused_since is not None and abs((po.paused_since - t_in).total_seconds()) < 1,
          po.paused_since)
    # Гарах уншилттай нөхөлт — минутыг шууд нэмнэ (батлагдсан → эцгийн 0 = хязгааргүй)
    outer2, inner2, d2 = mk_pair(tpl)
    P2 = "9905ЖЖА"
    run(handle_entry(db, d2["oentry"], P2, 0.95, raw(P2)))
    shift(P2, 10)
    po2 = sess(outer2, P2)
    res2 = camsync(inner2, [(P2, po2.entry_time + timedelta(minutes=1))],
                   [(P2, po2.entry_time + timedelta(minutes=544))])
    po2 = sess(outer2, P2)
    check("гарах уншилттай нөхсөн доторх сешн → 543 мин хасалт", po2.paused_minutes == 543,
          (res2, po2.paused_minutes, po2.paused_since))
    check("давхар тоолоогүй (paused_since хоосон)", po2.paused_since is None, po2.paused_since)
    check("эцгийн тоолуур аль хэдийн зогссон бол дахин нэмэхгүй",
          credit_backfilled_child(db, inner, P, t_in, t_in + timedelta(hours=1)) == 0)
    db.rollback()

    print("\ne. camera_sync доторх сешнийг логийн гарах уншилтаар хаав — дотор 30 мин, гадаа 7ц")
    outer, inner, d = mk_pair(tpl, parent_cap=0)
    P = "9906ЖЖА"
    run(handle_entry(db, d["oentry"], P, 0.95, raw(P)))
    run(handle_entry(db, d["ientry"], P, 0.95, raw(P)))
    shift(P, 7.5)
    po, pi = sess(outer, P), sess(inner, P)
    check("доторх орохоор эцгийн тоолуур зогссон", po.paused_since is not None)
    res = camsync(inner, [(P, pi.entry_time)], [(P, pi.entry_time + timedelta(minutes=30))])
    po, pi = sess(outer, P), sess(inner, P)
    check("логийн гарах уншилтаар доторх сешн хаагдав", pi.status != "OPEN" and pi.exit_confirmed,
          (res, pi.status, pi.exit_confirmed))
    check("эцгийн тоолуур ТЭР цагаас үргэлжлэв (30 мин хасалт)",
          po.paused_since is None and 29 <= (po.paused_minutes or 0) <= 30,
          (po.paused_since, po.paused_minutes))
    fee = session_fee_info(db, po)
    check("гадаа 7ц → төлбөртэй (эцэг 0 байсан ч)", fee["total_fee"] > 0 and not fee["is_free"], fee)

    print("\ne2. Доторх ГАРАХ камерын cross-read (51с) camera_sync-ээр; машин 7ц ДОТОР (1937УАЕ хэв маяг)")
    outer, inner, d = mk_pair(tpl, parent_cap=0)
    P = "9907ЖЖА"
    run(handle_entry(db, d["oentry"], P, 0.95, raw(P)))
    run(handle_entry(db, d["ientry"], P, 0.95, raw(P)))
    shift(P, 7 + 3 / 60)
    po, pi = sess(outer, P), sess(inner, P)
    t_pause = po.paused_since
    # Логт: орсноос 51с-д cross-read, жинхэнэ гарц 7ц дараа (live татгалзсан)
    res = camsync(inner, [(P, pi.entry_time)], [(P, pi.entry_time + timedelta(seconds=51)),
                                                (P, pi.entry_time + timedelta(hours=7, seconds=10))])
    po, pi = sess(outer, P), sess(inner, P)
    check("cross-read доторх сешнийг хаасан (camera_sync)", pi.status != "OPEN" and pi.exit_confirmed,
          (res, pi.status))
    check("богино (≤ fake_exit_minutes) гарц эцгийн тоолуурыг ҮРГЭЛЖЛҮҮЛЭЭГҮЙ",
          po.paused_since == t_pause and not po.paused_minutes, (po.paused_since, po.paused_minutes))
    r = run(handle_exit(db, d["oexit"], P, 0.95, raw(P)))
    po = sess(outer, P)
    fee = session_fee_info(db, po, at=po.exit_time)
    # Төлбөр хүлээж буй (AWAITING) сешний зогсолт хаагдах үед барагдана —
    # нэхэмжлэлийн дүн effective_paused_minutes-ээр ИЖИЛ хязгаартай.
    check("гадна гарц: батлагдаагүй хязгаар 240 мин хасагдав (прод-той ижил, 1 мин БИШ)",
          fee["paused_minutes"] == 240, (r.get("action"), po.status, fee["paused_minutes"]))
    check("нэхэмжилсэн дүн = 7ц03м − 4ц (прод-оос ИЛҮҮГҮЙ)",
          float(po.total_fee or 0) == fee["total_fee"] > 0, (po.total_fee, fee["total_fee"]))

    print("\ne3. Мөн cross-read, гэхдээ жинхэнэ доторх гарц LIVE ирэв (7224УБС хэв маяг) → 0₮")
    outer, inner, d = mk_pair(tpl, parent_cap=0)
    P = "9908ЖЖА"
    run(handle_entry(db, d["oentry"], P, 0.95, raw(P)))
    run(handle_entry(db, d["ientry"], P, 0.95, raw(P)))
    shift(P, 7 + 3 / 60)
    pi = sess(inner, P)
    camsync(inner, [(P, pi.entry_time)], [(P, pi.entry_time + timedelta(seconds=44))])
    r = run(handle_exit(db, d["iexit"], P, 0.95, raw(P)))
    po = sess(outer, P)
    check("доторх сешн АВТО сэргэж жинхэнэ гарцаар хаагдав, эцэгт ~423 мин",
          r.get("action") == "exit_completed" and po.paused_minutes >= 420
          and po.paused_since is None, (r.get("action"), po.paused_minutes))
    shift(P, 1 / 60)
    run(handle_exit(db, d["oexit"], P, 0.95, raw(P)))
    po = sess(outer, P)
    check("гадна гарц үнэгүй", po.status == "FREE" and float(po.total_fee or 0) == 0,
          (po.status, po.total_fee))

    print("\nf. Хуучин доторх сешн дахин ашиглагдав — зогсолт ОДООГООС (8810УКР)")
    outer, inner, d = mk_pair(tpl, parent_cap=0)
    P = "9909ЖЖА"
    now = datetime.utcnow()
    stale = ParkingSession(id=str(uuid.uuid4()), site_id=inner.id, plate_number=P,
                           entry_time=now - timedelta(hours=30), status="OPEN", paused_minutes=0)
    po = ParkingSession(id=str(uuid.uuid4()), site_id=outer.id, plate_number=P,
                        entry_time=now - timedelta(hours=6), status="OPEN", paused_minutes=0)
    db.add_all([stale, po])
    db.commit()
    r = run(handle_entry(db, d["ientry"], P, 0.95, raw(P)))
    po = sess(outer, P)
    check("хуучин доторх сешн дахин ашиглагдав", r.get("session_id") == stale.id, r)
    check("зогсолт гадна сешн эхлэхээс ӨМНӨ биш, одооноос",
          po.paused_since is not None and po.paused_since >= now - timedelta(seconds=5),
          (po.paused_since, po.entry_time))
    fee = session_fee_info(db, po, at=datetime.utcnow() + timedelta(minutes=10))
    check("6ц10м гадна зогсолт үнэгүй БОЛООГҮЙ (≤10 мин хасалт)",
          fee["paused_minutes"] <= 11 and fee["total_fee"] > 0, fee)
    # Тусдаа шалгалт: on_inner_entry өөрөө ч гадна сешний эхлэлээр тасална
    po.paused_since = None
    db.commit()
    on_inner_entry(db, inner, P, now - timedelta(hours=30))
    check("on_inner_entry: paused_since ≥ гадна entry_time", po.paused_since == po.entry_time,
          (po.paused_since, po.entry_time))
    db.rollback()

    print("\ng. Хар жагсаалтаар хориглосон (бүртгэлтэй) машин — эцгийн тоолуур зогсохгүй")
    outer, inner, d = mk_pair(tpl)
    P = "9910ЖЖА"
    drv = RegisteredDriver(id=str(uuid.uuid4()), plate_number=P, site_id=outer.id,
                           contract_type="TRANSIT", access_scope="site", is_active=True,
                           valid_from=now - timedelta(days=1), valid_to=now + timedelta(days=30))
    bl = BlacklistEntry(id=str(uuid.uuid4()), plate_number=P, reason="ZZ тест", is_active=True)
    db.add_all([drv, bl])
    db.commit()
    made.extend([drv, bl])
    po = ParkingSession(id=str(uuid.uuid4()), site_id=outer.id, plate_number=P,
                        entry_time=now - timedelta(minutes=20), status="OPEN", paused_minutes=0)
    db.add(po)
    db.commit()
    _orig_bl = app_settings.get_blacklist_rules
    app_settings.get_blacklist_rules = lambda _db, _sid=None: {**_orig_bl(_db, _sid), "block_entry": True}
    try:
        r = run(handle_entry(db, d["ientry"], P, 0.95, raw(P)))
    finally:
        app_settings.get_blacklist_rules = _orig_bl
    po = sess(outer, P)
    check("хаалт нээгдээгүй", not r.get("barrier_opened"), r)
    check("эцгийн тоолуур ЯВСААР (хасалтгүй)", po.paused_since is None, po.paused_since)

    print("\nh. Цуврал уншилт: junk → зөв дугаар (autocorrect) — эцгийн тоолуур зогсоно")
    outer, inner, d = mk_pair(tpl)
    P = "9911ЖЖА"
    # Гадна орсноос 15 мин (> OCR-ойролцоо тохирлын 10 мин цонх) — эхний junk уншилт
    # гадна сешнтэй тохирохгүй тул autocorrect зам өөрөө шалгагдана (2026-10-14)
    po = ParkingSession(id=str(uuid.uuid4()), site_id=outer.id, plate_number=P,
                        entry_time=datetime.utcnow() - timedelta(minutes=15), status="OPEN",
                        paused_minutes=0)
    db.add(po)
    db.commit()
    r1 = run(handle_entry(db, d["ientry"], "9911ЖЖ", 0.6, raw("9911ЖЖ")))
    r2 = run(handle_entry(db, d["ientry"], P, 0.95, raw(P)))
    po = sess(outer, P)
    check("2 дахь уншилт autocorrect", r2.get("action") == "plate_autocorrect", (r1, r2))
    check("autocorrect замд эцгийн тоолуур зогсов", po.paused_since is not None, po.paused_since)

    print("\nh2. Junk уншилт гадна орсноос 10 мин дотор — OCR-ойролцоо ганц гадна сешн (2026-10-14)")
    outer, inner, d = mk_pair(tpl)
    P = "9941ЖЖА"
    po = ParkingSession(id=str(uuid.uuid4()), site_id=outer.id, plate_number=P,
                        entry_time=datetime.utcnow() - timedelta(minutes=1), status="OPEN",
                        paused_minutes=0)
    db.add(po)
    db.commit()
    r1 = run(handle_entry(db, d["ientry"], "9941ЖЖ", 0.6, raw("9941ЖЖ")))
    po = sess(outer, P)
    ki = db.get(ParkingSession, r1.get("session_id"))
    check("эхний junk уншилтаар эцгийн тоолуур зогсов", po.paused_since is not None, r1)
    check("доторх сешний дугаар гадныхаар засагдав", ki is not None and ki.plate_number == P,
          ki.plate_number if ki else None)
    check("hold-д баригдалгүй хаалт нээгдэв", r1.get("barrier_opened") and not r1.get("held"), r1)
    r2 = run(handle_entry(db, d["ientry"], P, 0.95, raw(P)))
    check("2 дахь (зөв) уншилт шинэ сешн үүсгэсэнгүй", r2.get("action") in ("burst_dedup", "dedup"), r2)

    print("\ni. Хязгаарын дүрэм — батлагдсан (0 = хязгааргүй) / батлагдаагүй (глобал)")
    outer, inner, d = mk_pair(tpl, parent_cap=0)
    t6 = datetime.utcnow() - timedelta(hours=6)
    s_v = ParkingSession(id=str(uuid.uuid4()), site_id=outer.id, plate_number="9912ЖЖА",
                         entry_time=t6 - timedelta(minutes=5), status="OPEN", paused_minutes=0,
                         paused_since=t6)
    s_u = ParkingSession(id=str(uuid.uuid4()), site_id=outer.id, plate_number="9913ЖЖА",
                         entry_time=t6 - timedelta(minutes=5), status="OPEN", paused_minutes=0,
                         paused_since=t6)
    db.add_all([s_v, s_u])
    db.flush()
    at = t6 + timedelta(hours=6)
    check("тохируулсан хязгаар (эцгээс өвлөсөн) 0", pause_cap_minutes(db, outer.id) == 0)
    check("батлагдаагүй хязгаар = глобал 240", unverified_cap_minutes(db, outer.id) == 240,
          unverified_cap_minutes(db, outer.id))
    shown = effective_paused_minutes(db, s_u, at)
    check("дотор байх үеийн урьдчилсан хасалт 240", shown == 240, shown)
    close_open_pause(db, s_u, at)
    check("гадна гарц (доторх гарах уншилтгүй) 240 — урьдчилсантай ИЖИЛ",
          s_u.paused_minutes == shown, (s_u.paused_minutes, shown))
    on_inner_exit(db, inner, "9912ЖЖА", at)
    check("доторх ГАРАХ уншилттай → бүтэн 360 (хязгааргүй)", s_v.paused_minutes == 360,
          s_v.paused_minutes)
    _g = settings.transit_max_hours
    settings.transit_max_hours = 0
    try:
        check("глобал ч 0 бол батлагдаагүй нь ч хязгааргүй",
              unverified_cap_minutes(db, outer.id) == 0)
    finally:
        settings.transit_max_hours = _g
    single = ParkingSite(id=str(uuid.uuid4()), name="ZZ-нэг-site", site_code=f"ZS{uuid.uuid4().hex[:7]}",
                         capacity=10, transit_max_hours=0, tariff_template_id=tpl.id)
    db.add(single)
    db.flush()
    s_s = ParkingSession(id=str(uuid.uuid4()), site_id=single.id, plate_number="9914ЖЖА",
                         entry_time=t6 - timedelta(minutes=5), status="OPEN", paused_minutes=0,
                         paused_since=t6)
    db.add(s_s)
    db.flush()
    check("НЭГ site загвар (Рашбулаг ЭТТ) 0 = хязгааргүй хэвээр",
          unverified_cap_minutes(db, single.id) == 0
          and effective_paused_minutes(db, s_s, at) == 360,
          effective_paused_minutes(db, s_s, at))
    db.rollback()

    print("\nj. Төлбөрийн дүрмийн шалгалт #9 — өвлөсөн 0-ийг анхааруулна")
    outer, inner, d = mk_pair(tpl, parent_cap=0)
    t_child = [c["title"] for c in check_conflicts(db, inner)]
    t_parent = [c["title"] for c in check_conflicts(db, outer)]
    check("хүүхэд (хоосон, эцгээс 0 өвлөсөн) → анхааруулга",
          "Доторх зогсоолын хасалт хязгааргүй" in t_child, t_child)
    check("эцэг (0, хүүхэд өвлөж байна) → анхааруулга",
          "Доторх зогсоолууд хязгааргүй хасалт өвлөж байна" in t_parent, t_parent)
    outer3, inner3, _ = mk_pair(tpl, parent_cap=None)
    t3 = [c["title"] for c in check_conflicts(db, inner3)]
    check("хоёулаа хоосон (глобал 4ц) → анхааруулгагүй",
          not any("хязгааргүй" in t for t in t3), t3)

    print("\nk. Админ гараар сэргээх (REOPEN) — хүүхэд → эцгийн тоолуур, эцэг → хүүхдийн хугацаа")
    from types import SimpleNamespace
    from app.routers.sessions_router import register_from_camera, reopen_session
    admin = SimpleNamespace(role="SUPER_ADMIN", username="zz-test", site_ids=[], site_id=None)
    outer, inner, d = mk_pair(tpl)
    P = "9915ЖЖА"
    run(handle_entry(db, d["oentry"], P, 0.95, raw(P)))
    run(handle_entry(db, d["ientry"], P, 0.95, raw(P)))
    run(handle_exit(db, d["iexit"], P, 0.95, raw(P)))
    pi = sess(inner, P)
    fake_exit = pi.exit_time
    check("хуурамч гарцаар хүүхэд хаагдаж эцэг үргэлжлэв",
          pi.status in ("FREE", "CLOSED") and sess(outer, P).paused_since is None,
          (pi.status, sess(outer, P).paused_since))
    run(reopen_session(pi.id, db, admin))
    po = sess(outer, P)
    check("хүүхэд гараар сэргэхэд эцгийн тоолуур «гарц»-аас дахин зогсов",
          po.paused_since == fake_exit, (po.paused_since, fake_exit))
    outer2, inner2, d2 = mk_pair(tpl)
    P2 = "9916ЖЖА"
    run(handle_entry(db, d2["oentry"], P2, 0.95, raw(P2)))
    run(handle_exit(db, d2["oexit"], P2, 0.95, raw(P2)))       # хуурамч гадна гарц
    run(handle_entry(db, d2["ientry"], P2, 0.95, raw(P2)))
    shift(P2, 2)
    run(handle_exit(db, d2["iexit"], P2, 0.95, raw(P2)))       # жинхэнэ доторх гарц
    po2 = sess(outer2, P2)
    check("эцэг хуурамч гарцаар хаагдсан, хасалтгүй", po2.status == "FREE"
          and not po2.paused_minutes, (po2.status, po2.paused_minutes))
    run(reopen_session(po2.id, db, admin))
    po2 = sess(outer2, P2)
    check("эцэг гараар сэргэхэд хаалттай үеийн доторх ~120 мин хасагдав",
          po2.status == "OPEN" and 118 <= (po2.paused_minutes or 0) <= 121,
          (po2.status, po2.paused_minutes))

    print("\nl. Админ камерын логоос нөхөж бүртгэх (доторх зогсоол) — эцгийн тоолуур")
    outer, inner, d = mk_pair(tpl)
    P = "9917ЖЖА"
    run(handle_entry(db, d["oentry"], P, 0.95, raw(P)))
    shift(P, 8)
    po = sess(outer, P)
    t_a, t_b = po.entry_time + timedelta(minutes=3), po.entry_time + timedelta(minutes=303)
    res = register_from_camera({"site_id": inner.id, "create_debt": False,
                                "cars": [{"plate": P, "at": t_a.isoformat(),
                                          "exit_at": t_b.isoformat()}]}, db, admin)
    po = sess(outer, P)
    check("нөхсөн доторх сешн → эцэгт 300 мин хасалт", res.get("created") == 1
          and po.paused_minutes == 300 and po.paused_since is None,
          (res, po.paused_minutes, po.paused_since))

    print("\nm. Жолоочийн «Хамрах хүрээ» — дотроо зогсоолтой эцэг зогсоолд харагдана")
    from app.routers.driver_directory_router import driver_options
    outer, inner, d = mk_pair(tpl)
    opts = {o["id"]: o for o in driver_options(db, SimpleNamespace(role="SUPER_ADMIN"))}
    check("эцэг зогсоол has_inner_lanes=true", opts.get(outer.id, {}).get("has_inner_lanes") is True,
          opts.get(outer.id))
    check("доторх (хүүхэд, камергүй) зогсоолд хэвээр false",
          opts.get(inner.id, {}).get("has_inner_lanes") is False, opts.get(inner.id))

    print("\nn. Эцэг сэргэхэд ӨМНӨХ айлчлалын нээлттэй хүүхэд сешн — гадаа 3ц хасагдахгүй")
    outer, inner, d = mk_pair(tpl, parent_cap=None)          # Хангарьд шиг (глобал 4ц)
    P = "9918ЖЖА"
    now = datetime.utcnow()
    db.add(ParkingSession(id=str(uuid.uuid4()), site_id=inner.id, plate_number=P,
                          entry_time=now - timedelta(hours=20), status="OPEN", paused_minutes=0))
    db.commit()
    run(handle_entry(db, d["oentry"], P, 0.95, raw(P)))
    run(handle_exit(db, d["oexit"], P, 0.95, raw(P)))          # хуурамч гадна гарц
    check("хуурамч гарц эцгийг FREE хаалаа", sess(outer, P).status == "FREE")
    shift(P, 3)                                                 # машин 3ц ГАДАА
    r = run(handle_exit(db, d["oexit"], P, 0.95, raw(P)))
    po = sess(outer, P)
    check("эцэг АВТО сэргэв",
          db.query(AuditLog).filter_by(entity_id=po.id, action="AUTO_REOPEN").count() == 1)
    check("phantom хүүхэд сешнээр хасалт өгөөгүй — 3ц төлбөртэй",
          not po.paused_minutes and po.paused_since is None
          and r.get("action") == "awaiting_payment", (r.get("action"), po.paused_minutes))

    print("\nn2. Мөн phantom сешн, гадаа 3ц → дотогш орж (дахин ашиглав) 1ц → гарц")
    outer, inner, d = mk_pair(tpl, parent_cap=0)              # 100 айл шиг (батлагдсан хязгааргүй)
    P = "9919ЖЖА"
    now = datetime.utcnow()
    stale2 = ParkingSession(id=str(uuid.uuid4()), site_id=inner.id, plate_number=P,
                            entry_time=now - timedelta(hours=20), status="OPEN", paused_minutes=0)
    db.add(stale2)
    db.commit()
    stale2_id = stale2.id
    run(handle_entry(db, d["oentry"], P, 0.95, raw(P)))
    run(handle_exit(db, d["oexit"], P, 0.95, raw(P)))          # хуурамч гадна гарц
    shift(P, 3)
    r_in = run(handle_entry(db, d["ientry"], P, 0.95, raw(P)))
    check("хуучин доторх сешн дахин ашиглагдав", r_in.get("session_id") == stale2_id, r_in)
    shift(P, 1)
    run(handle_exit(db, d["iexit"], P, 0.95, raw(P)))          # доторх ГАРАХ уншилт
    shift(P, 1 / 60)
    run(handle_exit(db, d["oexit"], P, 0.95, raw(P)))          # эцэг сэргэнэ
    po = sess(outer, P)
    check("зөвхөн доторх 1ц (ОРОХ уншилтаас) хасагдав, гадаа 3ц биш",
          59 <= (po.paused_minutes or 0) <= 61, po.paused_minutes)

    print("\no. «Зөвхөн бүртгэлтэй» доторх зогсоол: татгалзсан машины хүүхэд сешн (Рашбулаг шороо)")
    outer, inner, d = mk_pair(tpl, parent_cap=None, registered_only=True)
    P = "9920ЖЖА"
    run(handle_entry(db, d["oentry"], P, 0.95, raw(P)))
    run(handle_exit(db, d["oexit"], P, 0.95, raw(P)))          # хуурамч гадна гарц
    r_in = run(handle_entry(db, d["ientry"], P, 0.95, raw(P)))  # бүртгэлгүй → татгалзана
    pi = sess(inner, P)
    check("доторх хаалт нээгдээгүй ч сешн үүссэн (phantom)",
          not r_in.get("barrier_opened") and pi is not None and pi.status == "OPEN",
          (r_in, pi and pi.status))
    shift(P, 3)                                                 # машин 3ц ГАДАА
    r = run(handle_exit(db, d["oexit"], P, 0.95, raw(P)))
    po = sess(outer, P)
    check("эцэг АВТО сэргэв",
          db.query(AuditLog).filter_by(entity_id=po.id, action="AUTO_REOPEN").count() == 1)
    check("татгалзсан машинд доторх хасалт өгөөгүй — 3ц төлбөртэй",
          not po.paused_minutes and po.paused_since is None
          and r.get("action") == "awaiting_payment", (r.get("action"), po.paused_minutes))
    now = datetime.utcnow()
    po2 = ParkingSession(id=str(uuid.uuid4()), site_id=outer.id, plate_number="9922ЖЖА",
                         entry_time=now - timedelta(hours=1), status="OPEN", paused_minutes=0)
    db.add(po2)
    db.flush()
    got = credit_backfilled_child(db, inner, "9922ЖЖА", now - timedelta(minutes=30), None)
    check("camera_sync нөхөлт: татгалзах машинд эцгийн тоолуур зогсохгүй",
          got == 0 and po2.paused_since is None, (got, po2.paused_since))
    db.rollback()

    print("\np. Гараар сэргээсэн хүүхэд сешнийг НОТОЛГООГҮЙ албадан хаав — эцэг «батлагдсан» биш")
    from app.session_logic import close_session_forced
    outer, inner, d = mk_pair(tpl, parent_cap=0)
    P = "9921ЖЖА"
    run(handle_entry(db, d["oentry"], P, 0.95, raw(P)))
    run(handle_entry(db, d["ientry"], P, 0.95, raw(P)))
    run(handle_exit(db, d["iexit"], P, 0.95, raw(P)))          # cross-read → хүүхэд хаагдав
    pi = sess(inner, P)
    run(reopen_session(pi.id, db, admin))                      # эцэг дахин зогсоно
    shift(P, 6)
    pi = sess(inner, P)
    check("сэргээсэн хүүхэд: exit_confirmed хэвээр, exit_time хоосон",
          pi.status == "OPEN" and pi.exit_confirmed and pi.exit_time is None,
          (pi.status, pi.exit_confirmed, pi.exit_time))
    close_session_forced(db, pi, "zz_remove", "zz-test", create_comp=True)
    db.commit()
    po = sess(outer, P)
    check("нотолгоогүй хаалт эцгийн зогсолтыг 6ц «батлагдсан» болгож барагдуулаагүй",
          po.paused_since is not None and not po.paused_minutes,
          (po.paused_since, po.paused_minutes))
    run(handle_exit(db, d["oexit"], P, 0.95, raw(P)))
    po = sess(outer, P)
    fee = session_fee_info(db, po, at=po.exit_time)
    check("гадна гарц: батлагдаагүй хязгаар 240 мин (6ц биш)", fee["paused_minutes"] == 240,
          (po.status, fee["paused_minutes"]))

finally:
    db.rollback()
    sess_ids = [r[0] for r in db.query(ParkingSession.id).filter(
        ParkingSession.site_id.in_(site_ids)).all()]
    if sess_ids:
        db.query(Compensation).filter(Compensation.session_id.in_(sess_ids)).delete(
            synchronize_session=False)
        db.query(BarrierCommand).filter(BarrierCommand.session_id.in_(sess_ids)).delete(
            synchronize_session=False)
        db.query(AuditLog).filter(AuditLog.entity_id.in_(sess_ids)).delete(
            synchronize_session=False)
    db.query(LprEvent).filter(LprEvent.site_id.in_(site_ids)).delete(synchronize_session=False)
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
            print(f"  [cleanup] {type(obj).__name__} устгаж чадсангүй: {str(e)[:80]}")
    try:   # camera_sync-ийн watermark-аас тестийн зогсоолуудыг хасна
        st = get_state(db, CAMSYNC_STATE)
        if any(k in st for k in site_ids):
            set_state(db, CAMSYNC_STATE, {k: v for k, v in st.items() if k not in site_ids})
            db.commit()
    except Exception as e:  # noqa: BLE001
        db.rollback()
        print(f"  [cleanup] camsync_state: {str(e)[:80]}")
    db.close()

print(f"\n{'=' * 54}\nPASS={PASS} FAIL={FAIL}")
sys.exit(1 if FAIL else 0)
