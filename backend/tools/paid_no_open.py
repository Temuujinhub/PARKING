"""«Төлбөр төлсөн боловч хаалт нээгдэхгүй» гомдлын оношилгоо.

Гомдол бүрийн цаана 6 боломжит шалтгаан бий — энэ хэрэгсэл аль нь болохыг
цагийн дарааллаар шалгаж, ДҮГНЭЛТ хэвлэнэ:

  1. Төлбөр үнэндээ PAID болоогүй (QPay webhook ирээгүй / PENDING / REVIEW)
  2. Төлөх үед машин гарцын камерт уншигдаагүй байсан → систем гарах уншилт
     хүлээдэг; гарах камер дугаарыг уншаагүй/буруу уншсан бол хаалт нээгдэхгүй
  3. Хаалтны команд явсан ч FAILED (камерын RPC сесс, сүлжээ)
  4. Grace хугацаа хэтэрсэн → гарахад ҮЛДЭГДЭЛ нэхэж «нээгдэхгүй» мэт харагдана
  5. Гарцын хаалт/камер төхөөрөмж идэвхгүй (status != active)
  6. BARRIER_MOCK=true — команд «амжилттай» ч бодит хаалт хөдөлдөггүй

Ажиллуулах (production сервер дээр, backend хавтаст):
    cd /root/PARKING/backend
    venv/bin/python tools/paid_no_open.py 1234УБА      # тухайн дугаарын сүүлийн төлбөрүүд
    venv/bin/python tools/paid_no_open.py --scan 24    # сүүлийн 24 цагийн сэжигтэй бүх кейс
    venv/bin/python tools/paid_no_open.py --scan 24 --site NIC   # нэг зогсоолоор
"""
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings
from app.database import SessionLocal
from app.models import (BarrierCommand, Device, LprEvent, ParkingSession,
                        ParkingSite, Payment)

TZ = timedelta(hours=8)  # УБ-ын цаг


def L(dt):
    return (dt + TZ).strftime("%m-%d %H:%M:%S") if dt else "—"


def exit_devices(db, site_id):
    return (db.query(Device)
            .filter(Device.site_id == site_id,
                    Device.lane_dir.in_(["exit", "both"]))
            .order_by(Device.device_type, Device.lane_no).all())


def diagnose(db, s: ParkingSession, verbose=True):
    """Нэг session-ий төлбөр→хаалт урсгалыг мөшгөж (шалтгаан, тайлбар) буцаана."""
    site = db.get(ParkingSite, s.site_id)
    pays = (db.query(Payment).filter(Payment.session_id == s.id)
            .order_by(Payment.created_at).all())
    causes = []

    if verbose:
        print("=" * 72)
        print(f"🚗 {s.plate_number}  ·  {site.name if site else '?'}  ·  "
              f"session {s.id[:8]}  ·  төлөв: {s.status}")
        print(f"   Орсон: {L(s.entry_time)}   Төлсөн: {L(s.paid_at)}   "
              f"Гарсан: {L(s.exit_time)}   Grace дуусах: {L(s.exit_deadline)}")

    # ── 1. Төлбөрийн бүртгэл ─────────────────────────────────────────────
    paid = [p for p in pays if p.status == "PAID"]
    for p in pays:
        if verbose:
            print(f"   💰 {L(p.paid_at or p.created_at)}  {p.provider}/{p.payment_method}"
                  f"  {float(p.amount):,.0f}₮  {p.status}"
                  f"  ({p.sender_invoice_no})")
        if p.status == "PENDING":
            causes.append("Төлбөр PENDING — QPay webhook ирээгүй эсвэл жолооч төлж "
                          "дуусгаагүй. Кассаас «шалгах» дарж qpay/check хийлгэх.")
        elif p.status == "REVIEW":
            causes.append("Төлбөр REVIEW — ДУТУУ төлөгдсөн тул систем санаатайгаар "
                          "гаргаагүй. Оператор дүнг шалгаж шийднэ.")
    if not pays:
        causes.append("Энэ session-д төлбөрийн бүртгэл ОГТ алга — жолооч өөр "
                      "session/дугаар руу төлсөн байж болзошгүй (буруу уншигдсан "
                      "дугаарын session-г хайх).")
    if not paid:
        return causes

    paid_at = max(p.paid_at for p in paid if p.paid_at) if any(p.paid_at for p in paid) else None

    # ── 2. Төлсний ДАРААХ хаалтны командууд ──────────────────────────────
    cmds = (db.query(BarrierCommand)
            .filter(BarrierCommand.session_id == s.id, BarrierCommand.command == "open")
            .order_by(BarrierCommand.created_at).all())
    after = [c for c in cmds if paid_at and c.created_at >= paid_at - timedelta(seconds=5)]
    if verbose:
        for c in cmds:
            icon = "✅" if c.status == "SUCCESS" else ("❌" if c.status == "FAILED" else "⏳")
            print(f"   🚧 {L(c.created_at)}  open/{c.command_source}  {icon} {c.status}"
                  f"  {int(c.duration_ms or 0)}мс  {(c.response_text or '')[:60]}")

    ok = [c for c in after if c.status == "SUCCESS"]
    failed = [c for c in after if c.status == "FAILED"]
    if ok:
        if settings.barrier_mock:
            causes.append("Команд SUCCESS боловч BARRIER_MOCK=true — бодит хаалтад "
                          "команд ЯВААГҮЙ. .env-д BARRIER_MOCK=false болгоно уу!")
        else:
            slow = [c for c in ok if (c.duration_ms or 0) > 5000]
            if slow:
                causes.append(f"Хаалт нээгдсэн ч УДААН ({max(c.duration_ms for c in slow)}мс) "
                              "— жолоочид «нээгдэхгүй» мэт санагдсан байж болно.")
            elif not causes:
                causes.append("Хаалт хэвийн нээгдсэн ✅ — гомдол өөр удаагийн ирэлт "
                              "эсвэл өөр дугаарын тухай байж болзошгүй.")
    elif failed:
        causes.append(f"Хаалтны команд FAILED: «{(failed[-1].response_text or '')[:80]}» "
                      "— камерын RPC сесс/сүлжээ. tools/camera_sessions.py-аар шалгах.")
    else:
        # Төлсний дараа команд ОГТ үүсээгүй — яагаад?
        # mark_paid_and_open нь exit_device_id байвал шууд нээдэг, үгүй бол
        # гарах камерын ДАРААГИЙН уншилтыг хүлээдэг.
        evs = (db.query(LprEvent)
               .filter(LprEvent.site_id == s.site_id, LprEvent.lane_dir == "exit",
                       LprEvent.created_at >= (paid_at or s.entry_time),
                       LprEvent.created_at <= (paid_at or s.entry_time) + timedelta(hours=2))
               .order_by(LprEvent.created_at).all())
        own = [e for e in evs if e.plate_number == s.plate_number]
        near = [e for e in evs if e.plate_number != s.plate_number
                and e.plate_number[:4] == s.plate_number[:4]]
        if verbose and (own or near):
            print("   ── Төлснөөс хойшхи гарах уншилтууд ──")
            for e in (own + near)[:8]:
                mark = "" if e.plate_number == s.plate_number else "  ⚠ өөр уншсан"
                flag = "" if e.accepted else f"  ✗ {e.reject_reason or ''}"
                print(f"     {L(e.created_at)}  «{e.plate_number}»  "
                      f"conf={int(e.confidence or 0)}{mark}{flag}")
        if not s.exit_device_id and not own and not near:
            causes.append("Төлсний дараа гарах камерт энэ дугаар ОГТ уншигдаагүй — "
                          "камер дугаар уншаагүй (бохир дугаар/өнцөг) эсвэл камер "
                          "гацсан. tools/camera_snapshot_health.py-аар шалгах.")
        elif near and not own:
            causes.append(f"Гарах камер дугаарыг БУРУУ уншсан («{near[-1].plate_number}») "
                          "тул төлсөн session-тэй тохироогүй.")
        elif own:
            rejected = [e for e in own if not e.accepted]
            if rejected:
                causes.append(f"Гарах уншилт байсан ч ТАТГАЛЗСАН: "
                              f"{rejected[-1].reject_reason or '?'}")
            else:
                causes.append("Гарах уншилт байсан ч хаалтны команд үүсээгүй — "
                              "лог шалгах шаардлагатай (journalctl -u parking).")

    # ── 3. Grace хэтэрсэн эсэх ───────────────────────────────────────────
    if paid_at and s.exit_deadline and s.status not in ("CLOSED", "FREE"):
        late = (db.query(LprEvent)
                .filter(LprEvent.site_id == s.site_id, LprEvent.lane_dir == "exit",
                        LprEvent.plate_number == s.plate_number,
                        LprEvent.created_at > s.exit_deadline).first())
        if late:
            causes.append(f"Grace ({L(s.exit_deadline)}) ХЭТЭРСНИЙ дараа гарах гэсэн — "
                          "систем үлдэгдэл нэхсэн тул «нээгдээгүй» гэж ойлгогдсон.")

    # ── 4. Гарцын төхөөрөмжийн төлөв ─────────────────────────────────────
    devs = exit_devices(db, s.site_id)
    bad = [d for d in devs if d.status != "active"]
    if bad:
        causes.append("Гарцын идэвхгүй төхөөрөмж: " +
                      ", ".join(f"{d.name or d.device_type}({d.status})" for d in bad))
    if not any(d.device_type == "barrier" for d in devs):
        causes.append("Энэ зогсоолд ГАРЦЫН хаалт төхөөрөмж бүртгэлгүй байна!")

    return causes


def scan(db, hours: int, site_code: str | None):
    """Сүүлийн N цагт: төлсөн ч хаалт нээгдээгүй магадлалтай бүх session."""
    since = datetime.utcnow() - timedelta(hours=hours)
    q = (db.query(Payment).filter(Payment.created_at >= since)
         .order_by(Payment.created_at))
    site = None
    if site_code:
        site = (db.query(ParkingSite)
                .filter(ParkingSite.site_code == site_code.upper()).first())
        if not site:
            print(f"Зогсоол '{site_code}' олдсонгүй")
            return
    suspicious, pending = [], []
    for p in q.all():
        s = db.get(ParkingSession, p.session_id)
        if not s or (site and s.site_id != site.id):
            continue
        if p.status in ("PENDING", "REVIEW"):
            if p.created_at < datetime.utcnow() - timedelta(minutes=5):
                pending.append((p, s))
            continue
        if p.status != "PAID":
            continue
        # Төлсөн атлаа хаалт нээгдээгүй: SUCCESS «open» команд алга + гараагүй
        ok = (db.query(BarrierCommand)
              .filter(BarrierCommand.session_id == s.id,
                      BarrierCommand.command == "open",
                      BarrierCommand.status == "SUCCESS",
                      BarrierCommand.created_at >= (p.paid_at or p.created_at) - timedelta(seconds=5))
              .first())
        if not ok and s.status not in ("CLOSED", "FREE"):
            suspicious.append((p, s))

    if settings.barrier_mock:
        print("⚠⚠ АНХААР: BARRIER_MOCK=true — ямар ч команд бодит хаалтад очихгүй!\n")
    print(f"══ Сүүлийн {hours} цаг: төлсөн-ч-гараагүй {len(suspicious)}, "
          f"гацсан төлбөр {len(pending)} ══")
    for p, s in pending:
        print(f"\n⏳ {s.plate_number}  {float(p.amount):,.0f}₮  {p.status}  "
              f"{L(p.created_at)}  ({p.provider})")
    for p, s in suspicious:
        print()
        for c in diagnose(db, s):
            print(f"   👉 {c}")


def main():
    db = SessionLocal()
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return
    if args[0] == "--scan":
        hours = int(args[1]) if len(args) > 1 and args[1].isdigit() else 24
        site_code = None
        if "--site" in args:
            site_code = args[args.index("--site") + 1]
        scan(db, hours, site_code)
        return

    plate = args[0].upper().replace(" ", "")
    rows = (db.query(ParkingSession)
            .filter(ParkingSession.plate_number.ilike(f"%{plate}%"))
            .order_by(ParkingSession.entry_time.desc()).limit(5).all())
    if not rows:
        print(f"«{plate}» дугаартай session олдсонгүй.")
        return
    if settings.barrier_mock:
        print("⚠⚠ АНХААР: BARRIER_MOCK=true — команд бодит хаалтад очихгүй!\n")
    for s in rows:
        causes = diagnose(db, s)
        print("   ── ДҮГНЭЛТ ──")
        for c in (causes or ["Асуудал илрээгүй — лог/камерыг гараар шалгах."]):
            print(f"   👉 {c}")
    print("=" * 72)


if __name__ == "__main__":
    main()
