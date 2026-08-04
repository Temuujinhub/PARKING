"""Гарах оношилгоо — нэг машин (эсвэл сүүлийн N гаралт) гарахад ЮУ БОЛСНЫГ
цагийн дарааллаар харуулна: орох/гарах уншилт, OCR тохирол, хаалт нээсэн эсэх,
төлбөр, session-ий эцсийн төлөв. Дутуу/буруу уншигдсан дугаар гарахад систем
хэрхэн шийдсэнийг мөшгих зорилготой.

Ажиллуулах (production сервер дээр, backend хавтаст):
    cd /root/PARKING/backend
    venv/bin/python tools/exit_diag.py 4132            # тухайн дугаараар (хэсэгчилсэн ч болно)
    venv/bin/python tools/exit_diag.py --site NIC      # тухайн зогсоолын сүүлийн 20 гаралт
    venv/bin/python tools/exit_diag.py --recent 30     # бүх зогсоолын сүүлийн 30 гаралт
"""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal
from app.models import (AuditLog, BarrierCommand, Device, LprEvent, ParkingSession,
                        ParkingSite, Payment)

TZ = timedelta(hours=8)  # УБ-ын цаг


def L(dt):
    return (dt + TZ).strftime("%m-%d %H:%M:%S") if dt else "—"


def action_summary(db, s: ParkingSession) -> str:
    """Session-ий эцсийн төлөвөөс «ямар үйлдэл хийгдсэн»-ийг товч монголоор."""
    st = s.status
    if st in ("OPEN", "AWAITING_PAYMENT"):
        return "🔵 ЗОГСООЛД БАЙГАА (гараагүй эсвэл гарах уншилт session-тэй тохироогүй)"
    if st == "FREE":
        return "🟢 ҮНЭГҮЙ ГАРСАН (grace/үнэгүй хугацаанд багтсан — төлбөргүй хаалт нээв)"
    if st == "PAID":
        return "🟢 ТӨЛӨӨД ГАРСАН"
    if st == "CLOSED":
        return "🟢 ГАРСАН (хаалт нээгдэв)"
    if st == "MANUAL_CLOSED":
        return "🟠 ГАРААР/АВТО ХААСАН (оператор эсвэл шөнийн авто хаалт — өр үүссэн байж болно)"
    return f"төлөв: {st}"


def show_session(db, s: ParkingSession):
    site = db.get(ParkingSite, s.site_id)
    dur = s.duration_minutes
    if dur is None and s.exit_time:
        dur = (s.exit_time - s.entry_time).total_seconds() / 60
    print("=" * 72)
    print(f"🚗 {s.plate_number}  ·  {site.name if site else '?'}  ·  session {s.id[:8]}")
    print(f"   {action_summary(db, s)}")
    print(f"   Орсон:  {L(s.entry_time)}    Гарсан: {L(s.exit_time)}"
          f"    Хугацаа: {int(dur) if dur else 0}м")
    if s.total_fee is not None:
        print(f"   Төлбөр: {float(s.total_fee):,.0f}₮   Төлсөн: {L(s.paid_at)}")
    if s.vehicle_color or s.vehicle_type:
        print(f"   Машин: {' · '.join(x for x in (s.vehicle_color, s.vehicle_type) if x)}")
    if s.note:
        print(f"   Тэмдэглэл: {s.note}")

    # 1) Уншилтууд (орох/гарах, дутуу/буруу таних харагдана)
    win_lo = s.entry_time - timedelta(minutes=5)
    win_hi = (s.exit_time or datetime.now(timezone.utc).replace(tzinfo=None)) + timedelta(minutes=10)
    evs = (db.query(LprEvent).filter(LprEvent.site_id == s.site_id,
                                     LprEvent.created_at >= win_lo,
                                     LprEvent.created_at <= win_hi)
           .order_by(LprEvent.created_at).all())
    # Session-ий дугаартай ойролцоо (эхний 4 тэмдэгт таарсан) уншилтуудыг л
    key = s.plate_number[:4]
    rel = [e for e in evs if e.plate_number[:4] == key or e.plate_number == "?"]
    if rel:
        print("   ── Камерын уншилтууд ──")
        for e in rel:
            flag = "" if e.accepted else f"  ✗ {e.reject_reason or ''}"
            mark = "" if e.plate_number == s.plate_number else "  ⚠ дугаар өөр уншсан"
            print(f"     {L(e.created_at)}  {e.lane_dir:5}  «{e.plate_number}»"
                  f"  conf={int(e.confidence or 0)}{mark}{flag}")
    # ХУУРМАГ ШАЛГАЛТ: session-ий дугаараар уншилт ОГТ олдоогүй бол (phantom
    # эсэх), орох камер тэр агшинд ЮУ уншсаныг харуул — жинхэнэ (буруу уншигдсан)
    # машиныг илрүүлнэ. entry_time ±3 мин цонхонд бүх дугаарыг харуулна.
    has_own = any(e.plate_number == s.plate_number for e in evs)
    if not has_own and s.entry_device_id:
        near = (db.query(LprEvent)
                .filter(LprEvent.device_id == s.entry_device_id,
                        LprEvent.created_at >= s.entry_time - timedelta(minutes=3),
                        LprEvent.created_at <= s.entry_time + timedelta(minutes=3))
                .order_by(LprEvent.created_at).all())
        print("   ⚠ ЭНЭ ДУГААРААР УНШИЛТ АЛГА (phantom магадлалтай). Орох камер "
              f"{L(s.entry_time)}-ийн орчимд уншсан нь:")
        if near:
            for e in near:
                print(f"     {L(e.created_at)}  {e.lane_dir:5}  «{e.plate_number}»  conf={int(e.confidence or 0)}")
        else:
            print("     (тэр камер тэр агшинд юу ч уншаагүй — гараар/симуляц/автозасвараар үүссэн байж болзошгүй)")

    # 2) OCR тохирлын аудит (гарах камер өөр уншаад session-д тохсон эсэх)
    audits = (db.query(AuditLog).filter(AuditLog.entity_id == s.id,
                                        AuditLog.action.in_(
                                            ["EXIT_OCR_MATCH", "EXIT_OCR_MATCH",
                                             "MANUAL_EXIT", "AUTO_CLOSE", "ADMIN_REMOVE"]))
              .order_by(AuditLog.created_at).all())
    for a in audits:
        d = a.detail or {}
        if a.action == "EXIT_OCR_MATCH":
            print(f"   🔗 OCR ТОХИРОЛ: гарах камер «{d.get('read_plate')}» уншсаныг "
                  f"session «{d.get('matched_plate')}»-д тохов ({L(a.created_at)})")
        else:
            print(f"   📝 {a.action} ({a.username}) {L(a.created_at)}  {d}")

    # 3) Хаалтны командууд (нээгдсэн эсэх, амжилттай эсэх)
    cmds = (db.query(BarrierCommand).filter(BarrierCommand.session_id == s.id)
            .order_by(BarrierCommand.created_at).all())
    if cmds:
        print("   ── Хаалтны командууд ──")
        for c in cmds:
            icon = "✅" if c.status == "SUCCESS" else ("❌" if c.status == "FAILED" else "⏳")
            print(f"     {L(c.created_at)}  {c.command}/{c.command_source}  {icon} {c.status}"
                  f"  {int(c.duration_ms or 0)}мс  {c.response_text[:60]}")

    # 4) Төлбөр
    pays = db.query(Payment).filter(Payment.session_id == s.id).all()
    for p in pays:
        print(f"   💰 {p.provider}/{p.payment_method}  {float(p.amount):,.0f}₮  {p.status}  {L(p.paid_at)}")


def main():
    db = SessionLocal()
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return

    if args[0] == "--site" and len(args) > 1:
        site = db.query(ParkingSite).filter(ParkingSite.site_code == args[1].upper()).first()
        if not site:
            print(f"Зогсоол '{args[1]}' олдсонгүй")
            return
        rows = (db.query(ParkingSession).filter(ParkingSession.site_id == site.id,
                                                ParkingSession.exit_time.isnot(None))
                .order_by(ParkingSession.exit_time.desc()).limit(20).all())
    elif args[0] == "--recent":
        n = int(args[1]) if len(args) > 1 else 30
        rows = (db.query(ParkingSession).filter(ParkingSession.exit_time.isnot(None))
                .order_by(ParkingSession.exit_time.desc()).limit(n).all())
    else:
        # Дугаараар (хэсэгчилсэн ч болно — эхний 4 тэмдэгтээр)
        plate = args[0].upper().replace(" ", "")
        rows = (db.query(ParkingSession)
                .filter(ParkingSession.plate_number.ilike(f"%{plate}%"))
                .order_by(ParkingSession.entry_time.desc()).limit(20).all())
        if not rows:
            print(f"«{plate}» дугаартай session олдсонгүй. Сүүлийн 10 гаралтыг харуулъя:")
            rows = (db.query(ParkingSession).filter(ParkingSession.exit_time.isnot(None))
                    .order_by(ParkingSession.exit_time.desc()).limit(10).all())

    if not rows:
        print("Илэрц алга.")
        return
    for s in rows:
        show_session(db, s)
    print("=" * 72)
    print(f"Нийт {len(rows)} session. Тэмдэглэгээ: 🟢 амжилттай гарсан · "
          "🔵 зогсоолд байгаа · 🟠 гараар/авто хаасан · ⚠ дугаар өөр уншсан")


if __name__ == "__main__":
    main()
