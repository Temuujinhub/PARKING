"""Гарсан машинууд ЯАГААД бага төлсныг задлана — тодорхой хугацааны гарц бүрээр.

«72 машин гарсан хэрнээ мөнгө бага» гэдэгт олон шалтгаан байж болно:
  • шөнийн зогсолт — daily_cap-д хүрсэн (7 цаг ч, 12 цаг ч ижил дээд дүн)
  • үнэгүй хугацаанд (эхний 30 мин) багтсан
  • гэрээт/бүртгэлтэй машин — төлбөр авдаггүй
  • гарах уншилтгүй → албадан хаагдаж 0₮ (алдагдал — ЭНЭ Л АНХААРАХ ЗҮЙЛ)
  • QR төлбөр PENDING-д гацсан (жолооч төлсөн ч webhook ирээгүй)

Хэрэгсэл гарц бүрийг ЯАЖ ТӨЛӨГДСӨН, дүн, хугацаа, гарах уншилттай эсэхээр
нь ангилж, «бодит үнэгүй» vs «алдагдсан» хоёрыг ялгана.

Ажиллуулах (production сервер дээр, backend хавтаст):
    venv/bin/python tools/exit_settlement.py --hours 6
    venv/bin/python tools/exit_settlement.py --hours 12 --site RASH --list 30
    venv/bin/python tools/exit_settlement.py --today          # өнөөдөр (УБ) гарсан бүгд

Зөвхөн DB УНШИНА.
"""
import argparse
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models import AuditLog, ParkingSession, ParkingSite, Payment
from app.database import SessionLocal

TZ = timedelta(hours=8)  # УБ-ын цаг
_PROVIDER = {"QPAY": "QPay QR", "POS": "Карт", "CASH": "Бэлэн", "TRANSFER": "Дансаар"}


def L(dt):
    return (dt + TZ).strftime("%m-%d %H:%M") if dt else "—"


def classify(s, pays: list, closed_action: str | None) -> str:
    """Гарц бүрийн ТӨЛБӨРИЙН эцсийн ангилал (хүн ойлгох)."""
    if pays:
        methods = {_PROVIDER.get(p, p) for p in pays}
        return "Төлсөн: " + ", ".join(sorted(methods))
    if s.status == "FREE":
        if s.is_registered:
            return "Үнэгүй: гэрээт машин"
        return "Үнэгүй: хугацаанд багтсан/хөнгөлөлт"
    if s.status == "CLOSED" and float(s.total_fee or 0) == 0:
        return "Үнэгүй: 0₮-өөр хаагдсан"
    if s.status == "MANUAL_CLOSED":
        if closed_action in ("CAMERA_SYNC", "CAMERA_SYNC_EXIT"):
            return "АЛДАГДСАН: логоос нөхөж хаасан (гарах уншилтгүй)"
        if s.exit_confirmed:
            return "АЛДАГДСАН: гарсан ч төлөгдөөгүй (өр үүссэн байж болно)"
        return "АЛДАГДСАН: албадан хаасан, төлбөргүй"
    return f"Бусад: {s.status}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=6)
    ap.add_argument("--today", action="store_true", help="өнөөдөр (УБ цаг) гарсан бүгд")
    ap.add_argument("--site", help="зөвхөн нэг зогсоол (код эсвэл нэрний эхлэл)")
    ap.add_argument("--list", type=int, default=0)
    args = ap.parse_args()

    db = SessionLocal()
    try:
        if args.today:
            local_midnight = (datetime.utcnow() + TZ).replace(hour=0, minute=0, second=0,
                                                              microsecond=0)
            since = local_midnight - TZ
            span = "өнөөдөр (УБ)"
        else:
            since = datetime.utcnow() - timedelta(hours=args.hours)
            span = f"сүүлийн {args.hours:g} цаг"

        q = (db.query(ParkingSession)
             .filter(ParkingSession.exit_time >= since,
                     ParkingSession.status.in_(["CLOSED", "FREE", "MANUAL_CLOSED"])))
        site = None
        if args.site:
            site = (db.query(ParkingSite).filter(ParkingSite.site_code == args.site).first()
                    or db.query(ParkingSite)
                    .filter(ParkingSite.name.ilike(f"{args.site}%")).first())
            if not site:
                sys.exit(f"«{args.site}» олдсонгүй")
            q = q.filter(ParkingSession.site_id == site.id)
        rows = q.order_by(ParkingSession.exit_time.desc()).all()

        title = site.name if site else "БҮХ ЗОГСООЛ"
        print(f"══ {title} — {span} ГАРСАН {len(rows)} машин ══\n")
        if not rows:
            return

        ids = [s.id for s in rows]
        pays: dict = defaultdict(list)
        collected = 0.0
        for sid, provider, amount in (db.query(Payment.session_id, Payment.provider,
                                               Payment.amount)
                                      .filter(Payment.session_id.in_(ids),
                                              Payment.status == "PAID").all()):
            pays[sid].append(provider)
            collected += float(amount or 0)
        close_act: dict = {}
        for eid, action in (db.query(AuditLog.entity_id, AuditLog.action)
                            .filter(AuditLog.entity == "session",
                                    AuditLog.entity_id.in_(ids),
                                    AuditLog.action.in_(
                                        ["CAMERA_SYNC", "CAMERA_SYNC_EXIT", "MANUAL_EXIT",
                                         "AUTO_CLOSE", "REENTRY_CLOSE"]))
                            .order_by(AuditLog.created_at).all()):
            close_act[eid] = action

        kinds = Counter()
        kind_amt: dict = defaultdict(float)
        samples: dict = defaultdict(list)
        for s in rows:
            k = classify(s, pays.get(s.id, []), close_act.get(s.id))
            kinds[k] += 1
            kind_amt[k] += float(s.total_fee or 0)
            samples[k].append(s)

        print(f"Нийт цугласан төлбөр: {collected:,.0f}₮   ·   "
              f"дундаж {collected / len(rows):,.0f}₮/машин\n")
        print(f"{'ангилал':52}{'тоо':>5}{'нийт дүн':>12}")
        lost_n = 0
        for k, n in kinds.most_common():
            flag = "  ⚠" if k.startswith("АЛДАГДСАН") else ""
            if k.startswith("АЛДАГДСАН"):
                lost_n += n
            print(f"{k[:50]:52}{n:5}{kind_amt[k]:12,.0f}{flag}")

        free_n = sum(n for k, n in kinds.items() if k.startswith("Үнэгүй"))
        paid_n = sum(n for k, n in kinds.items() if k.startswith("Төлсөн"))
        print(f"\n   Төлсөн {paid_n}  ·  үнэгүй {free_n}  ·  АЛДАГДСАН {lost_n}"
              f"  ({lost_n * 100 // len(rows)}%)")
        print("   «Үнэгүй» = гэрээт/хугацаанд багтсан (ХЭВИЙН). "
              "«АЛДАГДСАН» = гарц дээрээ төлөх ёстой байсан ч чадаагүй.")

        # Хугацааны хуваарилалт — шөнийн урт зогсолт daily_cap-д хүрсэн эсэх
        durs = [s.duration_minutes for s in rows if s.duration_minutes]
        if durs:
            durs.sort()
            over4h = sum(1 for d in durs if d >= 240)
            print(f"\n   Зогсолтын хугацаа: дундаж {sum(durs) // len(durs)}м, "
                  f"дундах {durs[len(durs) // 2]}м, 4ц+ зогссон {over4h} машин "
                  f"({over4h * 100 // len(rows)}%)")
            print("   (шөнийн урт зогсолт daily_cap-д хүрч, цагийн бус тогтмол дүн төлдөг)")

        if args.list:
            print(f"\nЖишээ ({min(args.list, len(rows))} мөр, шинэ нь эхэнд):")
            shown = 0
            for s in rows:
                if shown >= args.list:
                    break
                k = classify(s, pays.get(s.id, []), close_act.get(s.id))
                dur = s.duration_minutes
                print(f"   {s.plate_number:10} {L(s.entry_time)}→{L(s.exit_time)}"
                      f"  {str(dur) + 'м' if dur is not None else '—':>7}"
                      f"  {float(s.total_fee or 0):>7,.0f}₮  {k}")
                shown += 1
    finally:
        db.close()


if __name__ == "__main__":
    main()
