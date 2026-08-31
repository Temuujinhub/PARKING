"""Гарах талын давхар уншилт дээр хаалт ДАХИН нээгдэх эсэх.

Гомдол: гэрээт машин гарахад хаалт нээгдэхгүй удаан хүлээдэг. Шалтгаан нь
эхний уншилтын хаалтны команд унасан үед дараагийн уншилтууд lpr_dedup_seconds
(20с) турш «давхар уншилт» гэж шууд буцаж, хаалт огт нээгддэггүй байсан.
"""
import asyncio, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timedelta

from app.config import settings
from app.database import SessionLocal
from app.models import BarrierCommand, Compensation, Device, LprEvent, ParkingSession, ParkingSite, RegisteredDriver
from app.session_logic import handle_entry, handle_exit

PASS = FAIL = 0
def check(name, cond):
    global PASS, FAIL
    PASS, FAIL = (PASS+1, FAIL) if cond else (PASS, FAIL+1)
    print(f"  {'OK ' if cond else 'FAIL <<<'} {name}")

db = SessionLocal()
site = db.query(ParkingSite).filter(ParkingSite.site_code == "SITE01").first()
entry_cam = db.query(Device).filter(Device.site_id==site.id, Device.device_type=="camera", Device.lane_dir=="entry").first()
exit_cam  = db.query(Device).filter(Device.site_id==site.id, Device.device_type=="camera", Device.lane_dir=="exit").first()

PLATE = "8811ТСТ"
# цэвэрлэгээ
db.query(LprEvent).filter(LprEvent.plate_number==PLATE).delete()
db.query(Compensation).filter(Compensation.plate_number==PLATE).delete()
for s in db.query(ParkingSession).filter(ParkingSession.plate_number==PLATE).all():
    db.query(BarrierCommand).filter(BarrierCommand.session_id==s.id).delete()
    db.delete(s)
if not db.query(RegisteredDriver).filter(RegisteredDriver.plate_number==PLATE).first():
    db.add(RegisteredDriver(plate_number=PLATE, full_name="ТЕСТ гэрээт", site_id=site.id,
                            valid_from=datetime.utcnow()-timedelta(days=1),
                            valid_to=datetime.utcnow()+timedelta(days=1), is_active=True))
db.commit()

def barrier_opens_since(t0):
    # SKIPPED = cooldown-ы бүртгэлийн мөр (аудит A10) — илгээгдсэн команд биш
    return (db.query(BarrierCommand)
            .filter(BarrierCommand.command=="open", BarrierCommand.created_at >= t0,
                    BarrierCommand.status != "SKIPPED")
            .count())

async def main():
    print("Гэрээт машин: орох →  1-р гарах уншилт → ДАВХАР уншилт")
    await handle_entry(db, entry_cam, PLATE, 99.0, {"t": 1})
    t0 = datetime.utcnow()
    r1 = await handle_exit(db, exit_cam, PLATE, 99.0, {"t": 2})
    check("1-р гарах уншилт хаалт нээв", r1.get("barrier_opened") is True)
    n1 = barrier_opens_since(t0)

    # Cooldown-ыг тойрч (хаалт унасан нөхцөлийг дуурайх): сүүлийн SUCCESS-ийг FAILED болгоно
    last = (db.query(BarrierCommand).filter(BarrierCommand.command=="open")
            .order_by(BarrierCommand.created_at.desc()).first())
    last.status = "FAILED"
    db.commit()
    print("  (эхний хаалтны команд УНАСАН гэж тэмдэглэв)")

    r2 = await handle_exit(db, exit_cam, PLATE, 99.0, {"t": 3})
    check("давхар уншилт гэж танив", r2.get("action") == "dedup")
    check("ХААЛТ ДАХИН НЭЭГДЭВ (гол засвар)", r2.get("barrier_opened") is True)
    check("шинэ хаалтны команд бичигдэв", barrier_opens_since(t0) > n1)

    # Cooldown ажиллаж байгаа эсэх: одоо сүүлийн команд SUCCESS тул давтахгүй
    n2 = barrier_opens_since(t0)
    r3 = await handle_exit(db, exit_cam, PLATE, 99.0, {"t": 4})
    check("амжилттай нээснийх нь дараа команд ДАВТАХГҮЙ (cooldown)",
          r3.get("barrier_opened") is True and barrier_opens_since(t0) == n2)

    print("\nӨРТЭЙ машин давхар уншилт дээр ГАРАХГҮЙ байх ёстой")
    PLATE2 = "8822ТСТ"
    db.query(LprEvent).filter(LprEvent.plate_number==PLATE2).delete()
    db.query(Compensation).filter(Compensation.plate_number==PLATE2).delete()
    for s in db.query(ParkingSession).filter(ParkingSession.plate_number==PLATE2).all():
        db.query(BarrierCommand).filter(BarrierCommand.session_id==s.id).delete()
        db.delete(s)
    db.commit()
    # Энэ зогсоолын саяхны бүх уншилтыг хуучин болгоно (dedup/burst цонхоос гаргах)
    for ev in db.query(LprEvent).filter(LprEvent.site_id==site.id).all():
        ev.created_at = datetime.utcnow() - timedelta(minutes=10)
    db.commit()
    await handle_entry(db, entry_cam, PLATE2, 99.0, {"t": 5})
    sess = db.query(ParkingSession).filter(ParkingSession.plate_number==PLATE2,
                                           ParkingSession.status=="OPEN").first()
    db.add(Compensation(session_id=sess.id, site_id=site.id, plate_number=PLATE2,
                        amount=5000, reason="тест өр", created_by="test", status="PENDING"))
    db.commit()
    for ev in db.query(LprEvent).filter(LprEvent.site_id==site.id,
                                        LprEvent.lane_dir=="exit").all():
        ev.created_at = datetime.utcnow() - timedelta(minutes=10)
    db.commit()
    await handle_exit(db, exit_cam, PLATE2, 99.0, {"t": 6})
    # cooldown-ыг арилгахын тулд сүүлийн SUCCESS-ийг хуучин болгоно
    for bc in db.query(BarrierCommand).filter(BarrierCommand.command=="open",
                                              BarrierCommand.status=="SUCCESS").all():
        bc.created_at = datetime.utcnow() - timedelta(minutes=10)
    db.commit()
    n3 = barrier_opens_since(t0)
    r4 = await handle_exit(db, exit_cam, PLATE2, 99.0, {"t": 7})
    check("өртэй машин давхар уншилт дээр хаалт нээхгүй",
          r4.get("barrier_opened") is False and barrier_opens_since(t0) == n3)

    # цэвэрлэгээ
    for p in (PLATE, PLATE2):
        db.query(LprEvent).filter(LprEvent.plate_number==p).delete()
        db.query(Compensation).filter(Compensation.plate_number==p).delete()
        for s in db.query(ParkingSession).filter(ParkingSession.plate_number==p).all():
            db.query(BarrierCommand).filter(BarrierCommand.session_id==s.id).delete()
            db.delete(s)
        db.query(RegisteredDriver).filter(RegisteredDriver.plate_number==p).delete()
    db.commit()

asyncio.run(main())
db.close()



# ─── Давхар «нээх» команд таслагдах (2026-07-28 production) ──────────────────
# Нэг машин гарахад камер 2-3 удаа уншдаг: 1-р уншилт auto_exit-ээр нээж,
# 2-р уншилт exit_retry-ээр ДАХИН нээхийг оролддог байв. DB-ийн cooldown нь
# SUCCESS болсныг л хайдаг тул ЯГ ОДОО явж буй командыг олж хардаггүй →
# нэг камерт хоёр RPC зэрэг очиж хоёулаа удаашрана (ганц 87-410мс,
# давхацсан 749-985мс, нэг тохиолдолд хоёул 15с timeout).
print("\nДавхар «нээх» команд таслагдах:")
from app.services import barrier as _B  # noqa: E402

_B._open_inflight.clear()
check("эхлээд явж буй команд алга", not _B.open_in_flight("bar-1"))
_B._open_inflight["bar-1"] = time.monotonic()
check("явж байхад 'in flight' гэж үзнэ", _B.open_in_flight("bar-1"))
check("өөр хаалт хамааралгүй", not _B.open_in_flight("bar-2"))
_B._open_inflight.pop("bar-1", None)
check("дуусмагц дахин нээх боломжтой (УНАСАН ч дахин оролдоно)",
      not _B.open_in_flight("bar-1"))
# Леак хамгаалалт: цэвэрлэгдэлгүй үлдсэн (хугацаа нь илт хэтэрсэн) тэмдэглэгээ
# «нээж байна» гэж худал мэдээлж хаалтыг restart хүртэл гацаадаг байсан —
# одоо өөрөө хүчингүй болно.
_B._open_inflight["bar-1"] = time.monotonic() - 1000
check("гацсан (хуучирсан) тэмдэглэгээ өөрөө цэвэрлэгдэнэ",
      not _B.open_in_flight("bar-1"))
check("цэвэрлэсний дараа dict-ээс арилсан", "bar-1" not in _B._open_inflight)

print(f"\n{PASS} PASS, {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
