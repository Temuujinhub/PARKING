"""«Гараар хаасан» гэж харагдаж буй бүртгэлүүд ҮНЭНДЭЭ юугаар хаагдсаныг задална.

Яагаад хэрэгтэй вэ: `MANUAL_CLOSED` төлөв нь Түүх дээр «Гараар хаасан» гэж
харагддаг ч кодод 6 өөр зам ийм төлөв бичдэг (оператор гараар, авто цэвэрлэгээ,
камерын логийн sync, ээлж/шөнийн хаалт, төлбөргүй гарсан машин дахин орж ирэх).
Тэдгээрийн заримд session-ий түвшний AuditLog бичигддэггүй тул «Хаасан» багана
хоосон, заримд «Систем» гэж гарч, «гараар хаасан хэрнээ систем» мэт харагддаг.

Ажиллуулах (production сервер дээр, backend хавтаст):
    venv/bin/python tools/close_reason_diag.py --site RASH --days 3
    venv/bin/python tools/close_reason_diag.py --days 1          # бүх зогсоол
    venv/bin/python tools/close_reason_diag.py --site RASH --days 3 --list 30
"""
import argparse
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal
from app.models import AuditLog, ParkingSession, ParkingSite

TZ = timedelta(hours=8)  # УБ-ын цаг

# session-ийн түвшинд бичигддэг хаалтын AuditLog үйлдлүүд → хүн ойлгох тайлбар.
# Түүх хуудас (sessions_router._CLOSE_ACTIONS) эдгээрийн ЗӨВХӨН эхний 5-ыг мэддэг —
# бусад нь UI дээр «-» болж, хэн хаасан нь мэдэгдэхгүй.
ACTION_LABEL = {
    "MANUAL_EXIT": "оператор гараар гаргасан",
    "ADMIN_REMOVE": "админ зогсоолоос хассан",
    "AUTO_CLOSE": "авто цэвэрлэгээ (хугацаа хэтэрсэн)",
    "AUTO_FREE_CLOSE": "авто: зөвхөн орох уншилттай",
    "AUTO_JUNK_CLOSE": "авто: формат буруу дугаар",
    "CAMERA_SYNC": "камерын логоос нөхөж бүртгэсэн",
    "CAMERA_SYNC_EXIT": "камерын логийн ГАРАХ уншилтаар хаасан",
}
UI_KNOWN = {"MANUAL_EXIT", "ADMIN_REMOVE", "AUTO_CLOSE", "AUTO_FREE_CLOSE", "AUTO_JUNK_CLOSE"}


def L(dt):
    return (dt + TZ).strftime("%m-%d %H:%M") if dt else "—"


def classify(s: ParkingSession, logs: list) -> tuple[str, str]:
    """(ангилал, хэн) — session-ий хаалтын жинхэнэ эх сурвалж."""
    if logs:
        action, username = logs[-1][0], logs[-1][1]
        return ACTION_LABEL.get(action, action), username
    # AuditLog огт байхгүй — note болон бусад шинжээр таана
    note = (s.note or "").lower()
    if "ээлж" in note or "shift" in note:
        return "ээлж хаах (close_cars)", "?"
    if "шөн" in note or "night" in note:
        return "шөнийн хаалт", "?"
    if s.exit_confirmed:
        return "БҮРТГЭЛГҮЙ: гарах уншилттай ч ямар ч AuditLog алга", "—"
    return "БҮРТГЭЛГҮЙ: ээлж/шөнийн хаалт эсвэл дахин орж ирэхэд хаагдсан", "—"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", help="зогсоолын код (ж: RASH). Өгөхгүй бол бүгд")
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--list", type=int, default=0, help="хэдэн жишээ мөр хэвлэх")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        since = datetime.utcnow() - timedelta(days=args.days)
        q = (db.query(ParkingSession)
             .filter(ParkingSession.status == "MANUAL_CLOSED",
                     ParkingSession.exit_time >= since))
        site = None
        if args.site:
            site = (db.query(ParkingSite)
                    .filter(ParkingSite.site_code == args.site).first())
            if not site:
                sys.exit(f"«{args.site}» зогсоол олдсонгүй")
            q = q.filter(ParkingSession.site_id == site.id)
        rows = q.order_by(ParkingSession.exit_time.desc()).all()

        title = site.name if site else "БҮХ ЗОГСООЛ"
        print(f"══ {title} — сүүлийн {args.days} хоногийн «Гараар хаасан» "
              f"({len(rows)} бүртгэл) ══\n")
        if not rows:
            return

        ids = [s.id for s in rows]
        logs = defaultdict(list)
        for eid, action, username, at in (
                db.query(AuditLog.entity_id, AuditLog.action, AuditLog.username,
                         AuditLog.created_at)
                .filter(AuditLog.entity == "session", AuditLog.entity_id.in_(ids))
                .order_by(AuditLog.created_at).all()):
            logs[eid].append((action, username, at))

        kinds, hidden, samples = Counter(), Counter(), defaultdict(list)
        free_but_manual = 0
        for s in rows:
            kind, who = classify(s, logs.get(s.id, []))
            kinds[kind] += 1
            samples[kind].append((s, who))
            # UI-д «Хаасан» багана хоосон үлдэх (эсвэл «Систем» гэж гарах) эсэх
            acts = {a for a, _u, _t in logs.get(s.id, [])}
            if not acts & UI_KNOWN:
                hidden[kind] += 1
            if not float(s.total_fee or 0):
                free_but_manual += 1

        print("Хаалтын эх сурвалж:")
        for kind, n in kinds.most_common():
            h = hidden[kind]
            flag = f"   ← UI дээр «Хаасан» багана ХООСОН ({h})" if h else ""
            print(f"   {n:5}  {kind}{flag}")

        print(f"\nТөлбөр 0₮ мөртөө «Гараар хаасан» гэж бичигдсэн: {free_but_manual}"
              f" / {len(rows)}  (эдгээр нь «Үнэгүй гарсан» байх ёстой)")

        if args.list:
            print(f"\nЖишээ ({min(args.list, len(rows))} мөр):")
            shown = 0
            for kind, items in samples.items():
                for s, who in items:
                    if shown >= args.list:
                        break
                    dur = s.duration_minutes
                    print(f"   {s.plate_number:10} {L(s.entry_time)} → {L(s.exit_time)}"
                          f"  {str(dur) + 'м' if dur is not None else '—':>6}"
                          f"  {float(s.total_fee or 0):>7,.0f}₮  "
                          f"{'дотор ' + str(int(s.paused_minutes or 0)) + 'м' if s.paused_minutes else '':10}"
                          f"  {kind} [{who}]")
                    shown += 1
    finally:
        db.close()


if __name__ == "__main__":
    main()
