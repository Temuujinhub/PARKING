"""Өрийн (нөхөн төлбөрийн) аудит — «энэ өр ЖОЛООЧИЙН буруу юу, СИСТЕМИЙН үү?»

Асуулт: бид маш их өр бүртгэж байна, энэ нь системийн дутагдлаас болж байна уу?
Энэ хэрэгсэл өрийг ҮҮССЭН ШАЛТГААНААР нь хоёр ангилдаг:

  ЖОЛООЧИЙН БУРУУ (бодит авлага, нэхэмжлэх үндэслэлтэй)
    unpaid_exit   — гарах уншилт БАЙГАА, төлбөр төлөлгүй хаалт давсан

  СИСТЕМИЙН ГАРАЛТАЙ (машин үнэндээ хэдийнэ гарсан; гарах уншилт алдагдсанаас
  session нээлттэй үлдэж, дараа нь ямар нэг цэвэрлэгээ өр болгосон)
    auto_close      — N цаг болоод авто хаагдсан (гарсныг нь мэдээгүй)
    admin_remove    — админ гацсан машиныг бүртгэлээс хассан
    camera_sync     — камерын логоос нөхөж бүртгээд шууд хаасан
    camera_backfill — гараар нөхөн бүртгэлт
    reconcile / shift_close — тооцоо тулгах, ээлж хаахад үлдсэнийг хаах

Ажиллуулах:
    cd /root/PARKING/backend
    venv/bin/python tools/debt_audit.py                # бүх хугацаа
    venv/bin/python tools/debt_audit.py --days 7       # сүүлийн 7 хоног
    venv/bin/python tools/debt_audit.py --days 7 --by-site
"""
import argparse
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import func

from app.database import SessionLocal
from app.models import Compensation, ParkingSite

# Шалтгаан → (ангилал, хүн уншихуйц тайлбар)
DRIVER = "ЖОЛООЧ"
SYSTEM = "СИСТЕМ"
OTHER = "БУСАД"

KINDS = {
    "unpaid_exit":     (DRIVER, "төлөлгүй хаалт давсан (бодит авлага)"),
    "auto_close":      (SYSTEM, "авто хаалт — гарсныг нь мэдээгүй"),
    "admin_remove":    (SYSTEM, "админ гацсан машиныг хассан"),
    "camera_sync":     (SYSTEM, "камерын логоос авто нөхөлт"),
    "camera_backfill": (SYSTEM, "камерын логоос гар нөхөлт"),
    "reconcile":       (SYSTEM, "тооцоо тулгалт"),
    "shift_close":     (SYSTEM, "ээлж хаахад үлдсэнийг хаасан"),
}


def classify(reason: str) -> tuple[str, str]:
    return KINDS.get((reason or "").strip(), (OTHER, "гараар/тодорхойгүй шалтгаан"))


def money(v) -> str:
    return f"{float(v or 0):,.0f}₮"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=0, help="сүүлийн N хоног (0 = бүх хугацаа)")
    ap.add_argument("--by-site", action="store_true", help="зогсоолоор задлан харуулах")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        q = db.query(Compensation)
        since = None
        if args.days:
            since = datetime.utcnow() - timedelta(days=args.days)
            q = q.filter(Compensation.created_at >= since)

        rows = (q.with_entities(Compensation.reason,
                                func.count(Compensation.id),
                                func.coalesce(func.sum(Compensation.amount), 0),
                                func.count(Compensation.id).filter(Compensation.status == "PAID"),
                                func.coalesce(func.sum(Compensation.amount)
                                              .filter(Compensation.status == "PAID"), 0))
                .group_by(Compensation.reason).all())
        if not rows:
            print("Өрийн бичлэг олдсонгүй.")
            return

        period = f"сүүлийн {args.days} хоног" if args.days else "бүх хугацаа"
        print(f"\n═══ ӨРИЙН АУДИТ ({period}) ═══\n")
        print(f"{'Шалгаан':<18}{'Ангилал':<9}{'Тоо':>7}{'Дүн':>16}{'Цугл.':>7}{'Цугл.дүн':>14}  Тайлбар")
        print("─" * 106)

        totals = {DRIVER: [0, 0.0, 0, 0.0], SYSTEM: [0, 0.0, 0, 0.0], OTHER: [0, 0.0, 0, 0.0]}
        for reason, cnt, amt, paid_cnt, paid_amt in sorted(rows, key=lambda r: -float(r[2])):
            kind, note = classify(reason)
            t = totals[kind]
            t[0] += cnt
            t[1] += float(amt)
            t[2] += paid_cnt
            t[3] += float(paid_amt)
            print(f"{(reason or '—')[:17]:<18}{kind:<9}{cnt:>7}{money(amt):>16}"
                  f"{paid_cnt:>7}{money(paid_amt):>14}  {note}")

        grand = sum(t[1] for t in totals.values()) or 1.0
        print("\n─── ДҮГНЭЛТ " + "─" * 60)
        for kind in (DRIVER, SYSTEM, OTHER):
            cnt, amt, paid_cnt, paid_amt = totals[kind]
            if not cnt:
                continue
            share = 100.0 * amt / grand
            collect = 100.0 * paid_amt / amt if amt else 0.0
            print(f"  {kind:<8} {cnt:>6} ш  {money(amt):>15}  "
                  f"нийт өрийн {share:5.1f}%  ·  цуглуулалт {collect:4.1f}%")
        sys_share = 100.0 * totals[SYSTEM][1] / grand
        print()
        if sys_share >= 50:
            print(f"  ⚠ Бүртгэсэн өрийн {sys_share:.0f}% нь СИСТЕМИЙН гаралтай "
                  f"(гарах уншилт алдагдсанаас үүссэн), жолоочийн бодит авлага БИШ.")
            print("    → Нэхэмжлэхийн өмнө шүүх ёстой. Үндсэн шалтгаан нь гарцын "
                  "камерын уншилт — cam_status.py / exit_diag.py-аар зогсоолоор шалгана.")
        else:
            print(f"  Системийн гаралтай өр {sys_share:.0f}% — жолоочийн авлага давамгайлж байна.")

        if args.by_site:
            print("\n─── ЗОГСООЛООР " + "─" * 57)
            sq = (db.query(ParkingSite.name, Compensation.reason,
                           func.count(Compensation.id),
                           func.coalesce(func.sum(Compensation.amount), 0))
                  .join(Compensation, Compensation.site_id == ParkingSite.id))
            if since:
                sq = sq.filter(Compensation.created_at >= since)
            per_site: dict[str, list[float]] = {}
            for name, reason, cnt, amt in sq.group_by(ParkingSite.name, Compensation.reason).all():
                kind, _ = classify(reason)
                cur = per_site.setdefault(name, [0.0, 0.0])  # [систем, жолооч]
                cur[0 if kind != DRIVER else 1] += float(amt)
            print(f"{'Зогсоол':<20}{'Системийн':>16}{'Жолоочийн':>16}{'Системийн %':>13}")
            for name, (sys_amt, drv_amt) in sorted(per_site.items(), key=lambda kv: -kv[1][0]):
                tot = sys_amt + drv_amt or 1.0
                print(f"{name[:19]:<20}{money(sys_amt):>16}{money(drv_amt):>16}"
                      f"{100.0 * sys_amt / tot:>12.0f}%")
        print()
    finally:
        db.close()


if __name__ == "__main__":
    main()
