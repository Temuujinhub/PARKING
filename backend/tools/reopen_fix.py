"""Буруу АВТО СЭРГЭЭЛТИЙГ буцаах (reopen fix).

Асуудал (Маршил 2026-09-13, 53 сешн · 2.6 сая₮): гарах камерт уншигдаад
төлөлгүй гарсан (AWAITING_PAYMENT → авто хаалт `unpaid_exit`, 1,000–1,500₮ өр)
машин 1–2 хоногийн дараа дахин ирэхэд орох уншилт нь burst нэгтгэлд залгигдаж
сешнгүй үлдэв → гарцад `auto_reopen_for_exit` ХУУЧИН сешнийг сэргээж (хуучин
өрийг цуцлаад) орсноос хойшхи 2 хоногийн дүн 50,000₮ нэхэж, дараа нь тэр дүнгээр
шинэ өр үүсгэв. Код талын засвар (plates_burst_similar, exit_device_id-тай
хаагдсаныг сэргээхгүй, reopen_max_hours 12ц) ИРЭЭДҮЙН тохиолдлыг хаана; энэ
хэрэгсэл ӨНГӨРСӨН буруу сэргээлтийг буцаана:

  Шалгуур: AUTO_REOPEN хийгдсэн сешн, ӨМНӨ нь AUTO_CLOSE(reason=unpaid_exit)-оор
  хаагдсан (= гарах камерт уншигдсан, машин үнэхээр гарсан), сэргээснээс хойш
  ТӨЛӨГДӨӨГҮЙ.

  Засвар:
    • сешнийг АНХНЫ гарах уншилтын цагаар (AUTO_CLOSE-оос өмнөх сүүлийн гарах
      LprEvent; олдохгүй бол AUTO_CLOSE-ийн цаг) дахин хаана: exit_time, дүн тэр
      үеийнхээр, exit_confirmed=true, status MANUAL_CLOSED (төлөгдөөгүй)
    • сэргээлтийн ДАРАА үүссэн PENDING өр (50,000 г.м.) → CANCELLED
    • сэргээлтээр цуцлагдсан АНХНЫ өр (1,000 г.м., cancelled_by NULL) → PENDING
      буцаана (--no-restore-debts бол буцаахгүй)
    • тэр сешний PENDING QPay нэхэмжлэл → CANCELLED
    • AuditLog REOPEN_FIX

Анхдагчаар ЗӨВХӨН ХАРУУЛНА (dry-run). Бичихийн тулд --apply.

    cd /root/PARKING/backend
    venv/bin/python tools/reopen_fix.py --since 2026-08-01
    venv/bin/python tools/reopen_fix.py --since 2026-08-01 --apply
    venv/bin/python tools/reopen_fix.py --site Маршил --apply
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
from app.session_logic import session_fee_info  # noqa: E402

TZ = timedelta(hours=settings.tz_offset_hours)
TOOL = "reopen_fix"


def _ub(dt):
    return (dt + TZ).strftime("%m-%d %H:%M") if dt else "—"


def find_candidates(db, since: datetime, site_like: str | None):
    reopens = (db.query(AuditLog).filter(AuditLog.action == "AUTO_REOPEN",
                                         AuditLog.created_at >= since)
               .order_by(AuditLog.created_at).all())
    out, seen = [], set()
    for r in reopens:
        if r.entity_id in seen:
            continue
        s = db.get(ParkingSession, r.entity_id)
        if not s or s.paid_at is not None:
            continue
        if site_like and not (s.site and site_like.lower() in s.site.name.lower()):
            continue
        close = (db.query(AuditLog)
                 .filter(AuditLog.entity_id == s.id, AuditLog.action == "AUTO_CLOSE",
                         AuditLog.created_at < r.created_at)
                 .order_by(AuditLog.created_at.desc()).first())
        if not close or (close.detail or {}).get("reason") != "unpaid_exit":
            continue        # гарах уншилтгүй хаагдсан (OPEN→stale) сэргээлт — бодит байж болно
        seen.add(r.entity_id)
        out.append((s, r, close))
    return out


def fix_one(db, s: ParkingSession, reopen: AuditLog, close: AuditLog, dry: bool,
            restore_debts: bool) -> dict:
    # Анхны гарах уншилт: AUTO_CLOSE-оос өмнөх сүүлийн гарах LprEvent (энэ дугаар, энэ зогсоол)
    ev = (db.query(LprEvent)
          .filter(LprEvent.site_id == s.site_id, LprEvent.lane_dir == "exit",
                  LprEvent.plate_number == s.plate_number,
                  LprEvent.created_at > s.entry_time, LprEvent.created_at < close.created_at)
          .order_by(LprEvent.created_at.desc()).first())
    at = ev.created_at if ev else close.created_at
    # Дүн: авто хаалт тухайн үед бодсон өрийн дүн (AUTO_CLOSE аудит detail.debt) —
    # яг тэр үеийн тооцоо. Байхгүй бол гарах уншилтын цагаар дахин бодно.
    orig_debt = float((close.detail or {}).get("debt") or 0)
    if orig_debt > 0:
        r = settings.vat_rate
        vat = round(orig_debt * r / (1 + r))
        fee = {"total_fee": orig_debt, "vat_amount": vat, "base_fee": orig_debt - vat,
               "duration_minutes": max(0, int((at - s.entry_time).total_seconds() // 60)),
               "is_free": False}
    else:
        fee = session_fee_info(db, s, at=at)
    new_debts = (db.query(Compensation)
                 .filter(Compensation.session_id == s.id, Compensation.status == "PENDING",
                         Compensation.created_at >= reopen.created_at).all())
    old_debts = (db.query(Compensation)
                 .filter(Compensation.session_id == s.id, Compensation.status == "CANCELLED",
                         Compensation.cancelled_by.is_(None),
                         Compensation.created_at < reopen.created_at).all())
    pend_pay = (db.query(Payment).filter(Payment.session_id == s.id,
                                         Payment.status == "PENDING").all())
    info = {"plate": s.plate_number, "site": s.site.name if s.site else "?",
            "entry": _ub(s.entry_time), "reopened": _ub(reopen.created_at),
            "status_was": s.status, "fee_was": float(s.total_fee or 0),
            "exit_new": _ub(at), "exit_src": "гарах уншилт" if ev else "авто хаалтын цаг",
            "fee_new": fee["total_fee"],
            "cancel_new_debts": [float(c.amount) for c in new_debts],
            "restore_old_debts": [float(c.amount) for c in old_debts] if restore_debts else [],
            "cancel_invoices": [float(p.amount) for p in pend_pay]}
    if dry:
        return info
    from app.services.nested import close_open_pause
    close_open_pause(db, s, at)
    s.exit_time = at
    s.exit_confirmed = True
    s.exit_device_id = ev.device_id if ev else s.exit_device_id
    s.duration_minutes = fee["duration_minutes"]
    s.base_fee, s.vat_amount, s.total_fee = fee["base_fee"], fee["vat_amount"], fee["total_fee"]
    s.status = "FREE" if fee["is_free"] else "MANUAL_CLOSED"
    s.exit_deadline = None
    s.note = (f"{s.note + ' | ' if s.note else ''}{TOOL}: буруу авто сэргээлтийг буцаав — "
              f"анхны гарц {_ub(at)} ({info['exit_src']}), дүн {fee['total_fee']:.0f}₮ "
              f"(сэргээлтийн дүн {info['fee_was']:.0f}₮)")[:1000]
    now = datetime.utcnow()
    for c in new_debts:
        c.status = "CANCELLED"
        c.cancelled_at = now
        c.cancelled_by = TOOL
        c.cancel_reason = "Системийн алдаа — буруу авто сэргээлтийн хий дүнгээр үүссэн өр"
    if restore_debts:
        for c in old_debts:
            c.status = "PENDING"
    for p in pend_pay:
        p.status = "CANCELLED"
    db.add(AuditLog(username=TOOL, action="REOPEN_FIX", entity="session", entity_id=s.id,
                    detail={k: v for k, v in info.items() if k != "site"}))
    return info


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", default=(datetime.utcnow() - timedelta(days=60)).strftime("%Y-%m-%d"),
                    help="энэ огнооноос хойшхи AUTO_REOPEN (анхдагч: 60 хоног)")
    ap.add_argument("--site", default=None, help="зогсоолын нэрийн хэсэг")
    ap.add_argument("--no-restore-debts", action="store_true",
                    help="сэргээлтээр цуцлагдсан анхны өрийг PENDING болгож БУЦААХГҮЙ")
    ap.add_argument("--apply", action="store_true", help="бодитоор бичих (анхдагч: зөвхөн харуулна)")
    a = ap.parse_args()
    since = datetime.fromisoformat(a.since) - TZ
    db = SessionLocal()
    try:
        cands = find_candidates(db, since, a.site)
        print(f"{'ЗАСНА' if a.apply else 'DRY-RUN'} — {a.since}-аас хойшхи буруу сэргээлт: {len(cands)} сешн")
        t_was = t_new = t_cancel = t_restore = 0.0
        for s, r, c in cands:
            info = fix_one(db, s, r, c, dry=not a.apply, restore_debts=not a.no_restore_debts)
            t_was += info["fee_was"]; t_new += info["fee_new"]
            t_cancel += sum(info["cancel_new_debts"]); t_restore += sum(info["restore_old_debts"])
            extra = ""
            if info["cancel_new_debts"]:
                extra += f"  ✂ өр {', '.join(f'{x:.0f}' for x in info['cancel_new_debts'])}"
            if info["restore_old_debts"]:
                extra += f"  ↺ анхны өр {', '.join(f'{x:.0f}' for x in info['restore_old_debts'])}"
            if info["cancel_invoices"]:
                extra += f"  ✂ нэхэмжлэл {', '.join(f'{x:.0f}' for x in info['cancel_invoices'])}"
            print(f"  {info['plate']:<9} {info['site'][:14]:<14} орсон {info['entry']} · сэргээсэн "
                  f"{info['reopened']} · гарц → {info['exit_new']} ({info['exit_src']}) · дүн "
                  f"{info['fee_was']:>7.0f} → {info['fee_new']:>6.0f}₮ [{info['status_was']}]{extra}")
        print(f"\nДүн: {t_was:,.0f}₮ → {t_new:,.0f}₮ · цуцлах өр {t_cancel:,.0f}₮ · буцаах анхны өр {t_restore:,.0f}₮")
        if a.apply:
            db.commit()
            print("✓ Хадгалав (AuditLog: REOPEN_FIX).")
        elif cands:
            print("Бичихийн тулд --apply нэмнэ.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
