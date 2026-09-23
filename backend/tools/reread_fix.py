"""Гарсан машины ДАВТАН уншилтаас үүссэн хий нэхэмжлэл/өрийг буцаах.

Асуудал (2026-09-23 аудит, продын backup 14 хоног):
  А. «Орох уншилтгүй — суурь хураамж» (2000₮) сешн тэр машины ӨӨРИЙН сешн
     гарцад хаагдсанаас (ихэвчлэн гарцад QR төлсний) хэдхэн секундын дараа
     камер дахин уншихад үүсч, 2 цагийн дараа `unpaid_exit` өр болдог
     (488 кейс, Соёлын төв 171).
  Б. `auto_reopen_for_exit` үнэхээр гарсан богино зогсолтыг «хуурамч гарц»
     гэж сэргээсэн — машин дараа нь ДАХИН орсон эсвэл сэргээлтийг өдөөсөн
     уншилт нь дөнгөж гарсан машины давтан уншилт байсан (Хангарьд 2942УКН
     08:54 → 5,000₮).
Код талын засвар (recent_exit_reread + сэргээлтийн «дараагийн зогсолт»
хамгаалалт) ИРЭЭДҮЙН тохиолдлыг хаана; энэ хэрэгсэл ӨНГӨРСНИЙГ засна.

Шалгуур нь кодынхтой ИЖИЛ (session_logic.plates_ocr_similar — 300с,
plates_reread_similar ≤2 зөрүү — 60с).

Засвар:
  А: сешн → FREE, дүн 0, exit_time = үүссэн цаг; PENDING өр → CANCELLED;
     PENDING нэхэмжлэл → CANCELLED.
  Б: (зөвхөн «хуурамч гарц» гэж сэргээгдсэн богино зогсолт) сешн → анхны
     гарцын цагаар хаагдсан төлөвт (ихэвчлэн FREE 0₮); сэргээлтийн дараах
     PENDING өр → CANCELLED; нэхэмжлэл → CANCELLED.
  ТӨЛӨГДСӨН (paid_at/PAID өр) кейсэд ХҮРЭХГҮЙ — «буцаан олголт» жагсаалтад
  (зөвхөн тухайн сешнд ногдох дүнгээр) гаргана, санхүү шийднэ. Амьд сешн
  (OPEN/AWAITING/PAID, сүүлийн 3 цагт өөрчлөгдсөн), шийдэгдээгүй төлбөртэй
  (PENDING/CREATING/UNKNOWN/REVIEW) болон сэргээлт хуучин өрийг цуцалсан
  сешнийг АЛГАСНА. Мөрийг түгжиж дахин шалгаад сешн бүрээр commit хийнэ.
  Төлбөрийн (Payment) мөрөнд ХЭЗЭЭ Ч хүрэхгүй.

Анхдагчаар ЗӨВХӨН ХАРУУЛНА (dry-run). Бичихийн тулд --apply.

    cd /root/PARKING/backend
    venv/bin/python tools/reread_fix.py --since 2026-09-01
    venv/bin/python tools/reread_fix.py --since 2026-09-01 --site Хангарьд --apply
"""
import argparse
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import (  # noqa: E402
    AuditLog, Compensation, LprEvent, ParkingSession, ParkingSite, Payment,
)
from app.session_logic import (  # noqa: E402
    _ocr_canon, plates_ocr_similar, plates_reread_similar, session_fee_info,
)

TZ = timedelta(hours=settings.tz_offset_hours)
TOOL = "reread_fix"
FLAT_NOTE = "Орох уншилтгүй — суурь хураамж"
WINDOW, NEAR = 300, 60


def _ub(dt):
    return (dt + TZ).strftime("%m-%d %H:%M:%S") if dt else "—"


def _prior_exit(db, site_id, plate, at, exclude_id):
    """`at`-аас өмнө WINDOW дотор гарцад хаагдсан ижил/ойролцоо машины сешн."""
    rows = (db.query(ParkingSession)
            .filter(ParkingSession.site_id == site_id, ParkingSession.id != exclude_id,
                    ParkingSession.exit_device_id.isnot(None),
                    ParkingSession.status.in_(["CLOSED", "FREE", "MANUAL_CLOSED"]),
                    ParkingSession.exit_time <= at,
                    ParkingSession.exit_time >= at - timedelta(seconds=WINDOW))
            .order_by(ParkingSession.exit_time.desc()).all())
    for o in rows:
        if plates_ocr_similar(plate, o.plate_number):
            return o
    for o in rows:
        if o.exit_time >= at - timedelta(seconds=NEAR) and plates_reread_similar(plate, o.plate_number):
            return o
    return None


UNDECIDED = ("PENDING", "CREATING", "UNKNOWN", "REVIEW")
LIVE = ("OPEN", "AWAITING_PAYMENT", "PAID")


def _money(db, s, since=None):
    q = db.query(Compensation).filter(Compensation.session_id == s.id)
    if since is not None:
        q = q.filter(Compensation.created_at >= since)
    debts = q.all()
    pays = db.query(Payment).filter(Payment.session_id == s.id).all()
    return debts, pays


def _skip_reason(db, s, pays, now):
    """--apply-д ХҮРЭХГҮЙ шалтгаан (None = засаж болно)."""
    if s.status in LIVE:
        return f"амьд сешн ({s.status})"
    if s.updated_at and s.updated_at > now - timedelta(hours=3):
        return "сүүлийн 3 цагт өөрчлөгдсөн"
    if any(p.status in UNDECIDED for p in pays):
        return "шийдэгдээгүй төлбөр (" + ",".join(sorted({p.status for p in pays
                                                         if p.status in UNDECIDED})) + ")"
    return None


def _refund(s, debts, pays):
    """Энэ сешнд ногдох төлөгдсөн дүн — нэг QR-т багтсан ӨӨР өрийг хасна."""
    paid = sum(float(p.amount) for p in pays if p.status == "PAID")
    paid += sum(float(c.amount) for c in debts if c.status == "PAID")
    own = min(paid, float(s.total_fee or 0) or paid)
    return own, paid > own + 0.5


def find_flat(db, since, site_like):
    q = (db.query(ParkingSession)
         .filter(ParkingSession.note.like(f"{FLAT_NOTE}%"), ParkingSession.entry_time >= since))
    out = []
    for s in q.order_by(ParkingSession.entry_time).all():
        if site_like and not (s.site and site_like.lower() in s.site.name.lower()):
            continue
        if TOOL in (s.note or ""):
            continue
        prior = _prior_exit(db, s.site_id, s.plate_number, s.entry_time, s.id)
        if prior is not None:
            out.append((s, prior))
    return out


def find_reopen(db, since, site_like):
    out, seen = [], set()
    for r in (db.query(AuditLog).filter(AuditLog.action == "AUTO_REOPEN",
                                        AuditLog.created_at >= since)
              .order_by(AuditLog.created_at).all()):
        if r.entity_id in seen:
            continue
        s = db.get(ParkingSession, r.entity_id)
        if not s or (site_like and not (s.site and site_like.lower() in s.site.name.lower())):
            continue
        if TOOL in (s.note or ""):
            continue
        d = r.detail or {}
        # Зөвхөн «хуурамч гарц» (богино зогсолт) сэргээлт — анхны гарц, дүн (0)
        # нь тодорхой. Бусад (авто хаалтаас) сэргээлтийн анхны төлөв нарийн тул
        # энэ хэрэгсэл хүрэхгүй (reopen_fix.py / санхүү).
        if not d.get("short_fake_exit") or d.get("prev_minutes") is None:
            continue
        if getattr(s, "fee_locked", False):
            continue        # суурь хураамжийн сешн — А хэсэг/санхүү
        read = d.get("read_plate") or s.plate_number
        # Сэргээлтээс ӨМНӨ, энэ сешнээс ХОЙШ ЯГ ИЖИЛ дугаар дахин орсон (= өмнөх
        # гарц бодит). Кодын хамгаалалттай ижил: OCR жигдэлсэн яг тохирол.
        keys = {_ocr_canon(s.plate_number), _ocr_canon(read)}
        later = [o for o in (db.query(ParkingSession)
                             .filter(ParkingSession.site_id == s.site_id, ParkingSession.id != s.id,
                                     ParkingSession.entry_time > s.entry_time,
                                     ParkingSession.entry_time < r.created_at).all())
                 if _ocr_canon(o.plate_number) in keys]
        if not s.entry_time:
            continue
        orig_exit = s.entry_time + timedelta(minutes=float(d["prev_minutes"]))
        # Сешн үүсээгүй ч ОРОХ камер гарцаас хойш (60с+) дахин уншсан
        from app.session_logic import _inner_lane_devices
        reads = [e for e in (db.query(LprEvent)
                             .filter(LprEvent.site_id == s.site_id, LprEvent.lane_dir == "entry",
                                     LprEvent.accepted.is_(True),
                                     LprEvent.device_id.notin_(_inner_lane_devices(s.site_id)),
                                     LprEvent.created_at > orig_exit + timedelta(seconds=60),
                                     LprEvent.created_at < r.created_at).all())
                 if _ocr_canon(e.plate_number) in keys]
        prior = _prior_exit(db, s.site_id, read, r.created_at, s.id)
        why = []
        if later:
            why.append(f"дараа нь дахин орсон {later[0].plate_number} {_ub(later[0].entry_time)}")
        elif reads:
            why.append(f"орох камер дахин уншсан {reads[0].plate_number} {_ub(reads[0].created_at)}")
        if prior is not None:
            why.append(f"давтан уншилт: {prior.plate_number} {_ub(prior.exit_time)}-д гарсан")
        if not why:
            continue
        seen.add(s.id)
        out.append((s, r, orig_exit, "; ".join(why)))
    return out


def _cancel(db, pend, now, why):
    """Зөвхөн PENDING өрийг цуцална (мөрийг түгжиж дахин шалгана). Төлбөрт
    (Payment) ХЭЗЭЭ Ч хүрэхгүй — шийдэгдээгүй төлбөртэй сешнийг алгасдаг.
    ӨӨР сешний шийдэгдээгүй нэхэмжлэлд (нэг QR-т нийлсэн) холбогдсон өрийг
    цуцлахгүй — тэр нэхэмжлэл төлөгдвөл өрийг барагдуулах ёстой."""
    for c in pend:
        if c.payment_id:
            p = db.get(Payment, c.payment_id)
            if p is not None and p.status in UNDECIDED + ("PAID",):
                continue
        c = (db.query(Compensation).enable_eagerloads(False).filter(Compensation.id == c.id)
             .populate_existing().with_for_update().one())
        if c.status == "PENDING":
            c.status = "CANCELLED"
            c.cancelled_at = now
            c.cancelled_by = TOOL
            c.cancel_reason = why


def _lock(db, sid):
    from sqlalchemy.exc import OperationalError
    try:
        return (db.query(ParkingSession).enable_eagerloads(False)
                .filter(ParkingSession.id == sid).populate_existing()
                .with_for_update(nowait=True).first())
    except OperationalError:
        db.rollback()
        return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", default=(datetime.utcnow() + TZ - timedelta(days=30)).strftime("%Y-%m-%d"),
                    help="УБ огноо (анхдагч: 30 хоног)")
    ap.add_argument("--site", default=None, help="зогсоолын нэрийн хэсэг")
    ap.add_argument("--only", choices=["flat", "reopen"], default=None)
    ap.add_argument("--apply", action="store_true", help="бодитоор бичих (анхдагч: зөвхөн харуулна)")
    a = ap.parse_args()
    since = datetime.fromisoformat(a.since) - TZ
    now = datetime.utcnow()
    db = SessionLocal()
    refunds, skipped = [], []
    try:
        tot = {"flat": 0, "flat_debt": 0.0, "reopen": 0, "reopen_fee": 0.0, "reopen_debt": 0.0}
        if a.only in (None, "flat"):
            rows = find_flat(db, since, a.site)
            print(f"\nА. Давтан уншилтын 2000₮ суурь хураамж: {len(rows)} сешн")
            for s, prior in rows:
                debts, pays = _money(db, s)
                site = s.site.name if s.site else "?"
                gap = (s.entry_time - prior.exit_time).total_seconds()
                own, bundled = _refund(s, debts, pays)
                if own > 0 or s.paid_at:
                    refunds.append((site, s.plate_number, _ub(s.entry_time), own,
                                    "суурь хураамж (давтан уншилт)"
                                    + (" · нэг QR-т өөр өр багтсан" if bundled else "")))
                    continue
                why_skip = _skip_reason(db, s, pays, now)
                if why_skip:
                    skipped.append((site, s.plate_number, _ub(s.entry_time), why_skip))
                    continue
                pend = [c for c in debts if c.status == "PENDING"]
                tot["flat"] += 1
                tot["flat_debt"] += float(sum(c.amount for c in pend))
                print(f"  {s.plate_number:<9} {site[:14]:<14} {_ub(s.entry_time)} [{s.status}] "
                      f"← {prior.plate_number} {gap:.0f}с өмнө гарсан"
                      f"{'  ✂ өр ' + ', '.join(f'{float(c.amount):.0f}' for c in pend) if pend else ''}")
                if a.apply:
                    s = _lock(db, s.id)
                    if (s is None or s.paid_at
                            or _skip_reason(db, s, _money(db, s)[1], datetime.utcnow())
                            or any(p.status == "PAID" for p in _money(db, s)[1])):
                        db.rollback(); continue
                    debts, pays = _money(db, s)
                    if any(c.status == "PAID" for c in debts):
                        db.rollback(); continue
                    pend = [c for c in debts if c.status == "PENDING"]
                    _cancel(db, pend, now,
                            "Системийн алдаа — гарсан машины давтан уншилтаар үүссэн суурь хураамж")
                    s.status = "FREE"
                    s.exit_time = s.exit_time or s.entry_time
                    s.duration_minutes = 0
                    s.base_fee = s.vat_amount = s.total_fee = 0
                    s.exit_deadline = None
                    s.note = (f"{s.note} | {TOOL}: {prior.plate_number} {gap:.0f}с өмнө гарсны "
                              f"давтан уншилт — суурь хураамж цуцлав")[:1000]
                    db.add(AuditLog(username=TOOL, action="REREAD_FIX", entity="session",
                                    entity_id=s.id, detail={"kind": "flat", "prior_session": prior.id,
                                                            "gap_sec": round(gap),
                                                            "cancelled_debts": [float(c.amount) for c in pend]}))
                    db.commit()
        if a.only in (None, "reopen"):
            rows = find_reopen(db, since, a.site)
            print(f"\nБ. Буруу авто сэргээлт (машин үнэхээр гарсан): {len(rows)} сешн")
            for s, r, orig_exit, why in rows:
                debts, pays = _money(db, s, since=r.created_at)
                site = s.site.name if s.site else "?"
                own, bundled = _refund(s, debts, pays)
                if own > 0 or s.paid_at:
                    refunds.append((site, s.plate_number, _ub(r.created_at), own,
                                    f"буруу сэргээлт ({why})"
                                    + (" · нэг QR-т өөр өр багтсан" if bundled else "")))
                    continue
                if int((r.detail or {}).get("canceled_debt") or 0) > 0:
                    skipped.append((site, s.plate_number, _ub(r.created_at),
                                    "сэргээлт хуучин өрийг цуцалсан — reopen_fix.py/гараар"))
                    continue
                why_skip = _skip_reason(db, s, pays, now)
                if why_skip:
                    skipped.append((site, s.plate_number, _ub(r.created_at), why_skip))
                    continue
                pend = [c for c in debts if c.status == "PENDING"]
                fee = session_fee_info(db, s, at=orig_exit)
                tot["reopen"] += 1
                tot["reopen_fee"] += float(s.total_fee or 0)
                tot["reopen_debt"] += float(sum(c.amount for c in pend))
                print(f"  {s.plate_number:<9} {site[:14]:<14} орсон {_ub(s.entry_time)} · сэргээсэн "
                      f"{_ub(r.created_at)} · [{s.status} {float(s.total_fee or 0):.0f}₮] → гарц "
                      f"{_ub(orig_exit)} {fee['total_fee']:.0f}₮ · {why}"
                      f"{'  ✂ өр ' + ', '.join(f'{float(c.amount):.0f}' for c in pend) if pend else ''}")
                if a.apply:
                    s = _lock(db, s.id)
                    if (s is None or s.paid_at
                            or _skip_reason(db, s, _money(db, s)[1], datetime.utcnow())
                            or any(p.status == "PAID" for p in _money(db, s)[1])):
                        db.rollback(); continue
                    debts, pays = _money(db, s, since=r.created_at)
                    if any(c.status == "PAID" for c in debts):
                        db.rollback(); continue
                    pend = [c for c in debts if c.status == "PENDING"]
                    ev = (db.query(LprEvent)
                          .filter(LprEvent.site_id == s.site_id, LprEvent.lane_dir == "exit",
                                  LprEvent.created_at.between(orig_exit - timedelta(minutes=2),
                                                              orig_exit + timedelta(minutes=2)))
                          .all())
                    ev = next((e for e in ev if plates_reread_similar(e.plate_number, s.plate_number)), None)
                    _cancel(db, pend, now,
                            "Системийн алдаа — үнэхээр гарсан машины зогсолтыг буруу сэргээсэн")
                    was = f"{s.status} {float(s.total_fee or 0):.0f}₮"
                    s.exit_time = orig_exit
                    s.exit_confirmed = True
                    s.exit_device_id = ev.device_id if ev else s.exit_device_id
                    s.duration_minutes = fee["duration_minutes"]
                    s.base_fee, s.vat_amount, s.total_fee = (fee["base_fee"], fee["vat_amount"],
                                                             fee["total_fee"])
                    s.status = "FREE" if fee["is_free"] else "MANUAL_CLOSED"
                    s.exit_deadline = None
                    s.note = (f"{s.note + ' | ' if s.note else ''}{TOOL}: буруу авто сэргээлтийг "
                              f"буцаав ({why}); гарц {_ub(orig_exit)}, өмнө нь {was}")[:1000]
                    db.add(AuditLog(username=TOOL, action="REREAD_FIX", entity="session",
                                    entity_id=s.id, detail={"kind": "reopen", "reopen_audit": r.id,
                                                            "why": why, "was": was,
                                                            "cancelled_debts": [float(c.amount) for c in pend]}))
                    db.commit()
        print(f"\nДүн: А {tot['flat']} сешн, цуцлах өр {tot['flat_debt']:,.0f}₮ · "
              f"Б {tot['reopen']} сешн, нэхэмжилсэн {tot['reopen_fee']:,.0f}₮, цуцлах өр {tot['reopen_debt']:,.0f}₮")
        if skipped:
            print(f"\n⏭ АЛГАССАН ({len(skipped)}) — амьд/шийдэгдээгүй төлбөртэй, дараа дахин ажиллуулна:")
            for site, plate, t, why in skipped:
                print(f"  {plate:<9} {site[:14]:<14} {t}  {why}")
        if refunds:
            print(f"\n⚠ ТӨЛӨГДСӨН — хүрээгүй, буцаан олголтыг санхүү шийднэ ({len(refunds)}, "
                  f"{sum(x[3] for x in refunds):,.0f}₮, зөвхөн энэ сешнд ногдох хэсэг):")
            for site, plate, t, amt, why in refunds:
                print(f"  {plate:<9} {site[:14]:<14} {t}  {amt:,.0f}₮  {why}")
        if a.apply:
            print("\n✓ Хадгалав (сешн бүрээр commit, AuditLog: REREAD_FIX).")
        else:
            db.rollback()
            print("\n(DRY-RUN — бичихийн тулд --apply)")
    finally:
        db.close()


if __name__ == "__main__":
    main()
