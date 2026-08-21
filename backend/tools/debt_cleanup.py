"""Системийн гаралтай (phantom) өрийг бөөнөөр цуцлах.

Яагаад: `debt_audit.py`-ийн харуулснаар бүртгэсэн өрийн 99%+ нь машин ҮНЭНДЭЭ
гарсан ч ГАРАХ КАМЕР дугаарыг уншаагүйгээс үүссэн — auto_close / admin_remove /
camera_sync / camera_backfill. Эдгээр нь жолоочийн бодит авлага БИШ:

  • нэхэмжлэх хууль зүйн үндэслэлгүй (машин төлөөгүй гэдэг нотолгоо алга)
  • санхүүгийн тайланг гажуудуулна («48 сая₮ авлага» гэж харагдана)
  • ЖОЛООЧИЙГ БЛОКЛОНО — өртэй машины QR нэхэмжлэл 2+ мөр болж, өмнө нь
    QPay «VAT_AMOUNT_INVALID» өгч QR огт үүсдэггүй байсан
  • хуримтлагдвал АВТОМАТ ХАР ЖАГСААЛТ идэвхжиж машиныг зогсоолд оруулахгүй

ЖОЛООЧИЙН БОДИТ АВЛАГА (`unpaid_exit` — гарах уншилт байгаа, төлөлгүй давсан)
анхдагчаар ХӨНДӨГДӨХГҮЙ.

Ажиллуулах (эхлээд ЗААВАЛ dry-run — юу ч өөрчлөгдөхгүй):
    cd /root/PARKING/backend
    venv/bin/python tools/debt_cleanup.py                      # dry-run, бүх систем өр
    venv/bin/python tools/debt_cleanup.py --plate 5523УБО      # ганц машин
    venv/bin/python tools/debt_cleanup.py --site RASH --days 30
    venv/bin/python tools/debt_cleanup.py --apply              # ҮНЭХЭЭР цуцлана

Цуцлахад:
  • compensations.status: PENDING → CANCELLED
  • AuditLog бичигдэнэ (COMPENSATION_BULK_CANCEL) — хэн, хэзээ, яагаад, аль өр
  • Тухайн дугаарт төлөгдөөгүй өр үлдэхгүй бол АВТОМАТ хар жагсаалтын хоригийг
    мөн авна (гараар нэмсэн хоригт хүрэхгүй)

Буцаах боломжгүй тул `--apply`-аас өмнө dry-run-ы дүнг заавал хараарай.
"""
import argparse
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import func

from app.database import SessionLocal
from app.models import AuditLog, BlacklistEntry, Compensation, ParkingSite

# Системийн гаралтай шалтгаанууд (машин гарсныг нь мэдээгүйгээс үүссэн)
SYSTEM_REASONS = ["camera_sync", "auto_close", "admin_remove",
                  "camera_backfill", "reconcile", "shift_close"]
# Жолоочийн бодит авлага — ХЭЗЭЭ Ч автоматаар цуцлахгүй
DRIVER_REASONS = ["unpaid_exit"]

AUTO_BL_MARK = "автомат хориг"   # _auto_blacklist-ийн үлдээдэг тэмдэг


def money(v) -> str:
    return f"{float(v or 0):,.0f}₮"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="ҮНЭХЭЭР цуцлана (үгүй бол зөвхөн харуулна)")
    ap.add_argument("--plate", help="зөвхөн энэ улсын дугаар")
    ap.add_argument("--site", help="зөвхөн энэ зогсоол (site_code)")
    ap.add_argument("--days", type=int, default=0, help="сүүлийн N хоног (0 = бүгд)")
    ap.add_argument("--reasons", default=",".join(SYSTEM_REASONS),
                    help=f"таслалаар (анхдагч: {','.join(SYSTEM_REASONS)})")
    ap.add_argument("--all-reasons", action="store_true",
                    help="ШАЛТГААНААС ҮЛ ХАМААРАН бүх төлөгдөөгүй өрийг цуцлах "
                         "(жолоочийн бодит авлага ч орно)")
    ap.add_argument("--keep-blacklist", action="store_true",
                    help="хар жагсаалтын автомат хоригт хүрэхгүй")
    ap.add_argument("--clear-blacklist", action="store_true",
                    help="хар жагсаалтыг БҮХЭЛД НЬ цэвэрлэх — гараар нэмсэн хоригийг ч "
                         "(өргүй үлдсэн эсэхээс үл хамааран)")
    ap.add_argument("--note", default="системийн гаралтай өр — аудитаар цэвэрлэв",
                    help="аудитын лог дахь тайлбар")
    args = ap.parse_args()

    reasons = [r.strip() for r in args.reasons.split(",") if r.strip()]
    if args.all_reasons:
        reasons = []          # шүүлтгүй = БҮХ шалтгаан (гараар бичсэн тайлбар ч орно)
        print("⚠ --all-reasons: шалтгаанаас ҮЛ ХАМААРАН бүх төлөгдөөгүй өр цуцлагдана "
              "(жолоочийн бодит авлага ч мөн).")
    risky = [r for r in reasons if r in DRIVER_REASONS] or (["БҮГД"] if args.all_reasons else [])
    if risky and args.apply:
        print(f"⚠ АНХААР: {', '.join(risky)} нь ЖОЛООЧИЙН БОДИТ АВЛАГА. "
              f"Үнэхээр цуцлах бол --note-д шалтгаанаа бичээрэй.")

    db = SessionLocal()
    try:
        q = db.query(Compensation).filter(Compensation.status == "PENDING")
        if reasons:
            q = q.filter(Compensation.reason.in_(reasons))
        if args.plate:
            q = q.filter(Compensation.plate_number == args.plate.strip().upper())
        if args.days:
            q = q.filter(Compensation.created_at >= datetime.utcnow() - timedelta(days=args.days))
        if args.site:
            site = db.query(ParkingSite).filter(ParkingSite.site_code == args.site).first()
            if not site:
                print(f"Зогсоол олдсонгүй: {args.site}")
                return
            q = q.filter(Compensation.site_id == site.id)

        comps = q.order_by(Compensation.created_at).all()
        if not comps and not args.clear_blacklist:
            print("Тохирох төлөгдөөгүй өр олдсонгүй — цэвэрлэх зүйл алга.")
            return
        if not comps:
            # Өр алга ч хар жагсаалтыг цэвэрлэх даалгавар үлдсэн
            print("Тохирох төлөгдөөгүй өр алга — зөвхөн хар жагсаалтыг цэвэрлэнэ.\n")
            entries = db.query(BlacklistEntry).filter(BlacklistEntry.is_active.is_(True)).all()
            manual = sum(1 for b in entries if AUTO_BL_MARK not in (b.reason or ""))
            print(f"Хар жагсаалт: БҮХ {len(entries)} хориг чөлөөлөгдөнө "
                  f"(үүнээс {manual} нь ГАРААР нэмсэн)")
            if not args.apply:
                print("\nЭнэ бол зөвхөн ТООЦОО. Үнэхээр цэвэрлэх бол `--apply` нэмнэ үү.\n")
                return
            for b in entries:
                b.is_active = False
            db.add(AuditLog(username="system(debt_cleanup)", action="BLACKLIST_BULK_CLEAR",
                            entity="blacklist", entity_id="-",
                            detail={"note": args.note, "lifted": len(entries),
                                    "manual": manual}))
            db.commit()
            print(f"\n✅ Хар жагсаалтаас чөлөөлөв: {len(entries)} дугаар\n")
            return

        by_reason: dict[str, list[int, float]] = {}
        plates = set()
        total = 0.0
        for c in comps:
            row = by_reason.setdefault(c.reason, [0, 0.0])
            row[0] += 1
            row[1] += float(c.amount)
            total += float(c.amount)
            plates.add(c.plate_number)

        mode = "ЦУЦЛАХ" if args.apply else "DRY-RUN (юу ч өөрчлөгдөхгүй)"
        print(f"\n═══ ӨР ЦЭВЭРЛЭХ · {mode} ═══\n")
        print(f"{'Шалтгаан':<18}{'Тоо':>8}{'Дүн':>16}")
        print("─" * 42)
        for reason, (cnt, amt) in sorted(by_reason.items(), key=lambda kv: -kv[1][1]):
            mark = "  ← ЖОЛООЧИЙН АВЛАГА!" if reason in DRIVER_REASONS else ""
            print(f"{reason[:17]:<18}{cnt:>8}{money(amt):>16}{mark}")
        print("─" * 42)
        print(f"{'НИЙТ':<18}{len(comps):>8}{money(total):>16}   ·  {len(plates)} машин\n")

        # ── Хар жагсаалт: аль дугаарын АВТОМАТ хориг чөлөөлөгдөх вэ ────────────
        lift: list[BlacklistEntry] = []
        if args.clear_blacklist:
            # БҮХ идэвхтэй хориг — гараар нэмсэн ч, өр үлдсэн ч хамаагүй.
            # Зогсоол дамнасан жагсаалт тул site шүүлт хамаарахгүй.
            lift = db.query(BlacklistEntry).filter(BlacklistEntry.is_active.is_(True)).all()
            manual = sum(1 for b in lift if AUTO_BL_MARK not in (b.reason or ""))
            print(f"Хар жагсаалт: БҮХ {len(lift)} хориг чөлөөлөгдөнө "
                  f"(үүнээс {manual} нь ГАРААР нэмсэн)\n")
        elif not args.keep_blacklist:
            doomed = {c.id for c in comps}
            for plate in plates:
                left = (db.query(func.count(Compensation.id))
                        .filter(Compensation.plate_number == plate,
                                Compensation.status == "PENDING",
                                Compensation.id.notin_(doomed)).scalar() or 0)
                if left:
                    continue   # өөр өр үлдэж байна — хориг хэвээр
                for bl in (db.query(BlacklistEntry)
                           .filter(BlacklistEntry.plate_number == plate,
                                   BlacklistEntry.is_active.is_(True)).all()):
                    if AUTO_BL_MARK in (bl.reason or ""):
                        lift.append(bl)   # гараар нэмсэн хоригт хүрэхгүй
            print(f"Хар жагсаалт: {len(lift)} АВТОМАТ хориг чөлөөлөгдөнө "
                  f"(гараар нэмсэнд хүрэхгүй)\n")

        if not args.apply:
            print("Энэ бол зөвхөн ТООЦОО. Үнэхээр цуцлах бол дээрх командад "
                  "`--apply` нэмнэ үү.\n")
            return

        # ── Гүйцэтгэл ─────────────────────────────────────────────────────────
        for c in comps:
            c.status = "CANCELLED"
        for bl in lift:
            bl.is_active = False
        db.add(AuditLog(
            username="system(debt_cleanup)", action="COMPENSATION_BULK_CANCEL",
            entity="compensation", entity_id=comps[0].id,
            detail={"note": args.note, "count": len(comps), "total": round(total, 2),
                    "plates": len(plates), "blacklist_lifted": len(lift),
                    "by_reason": {r: {"count": v[0], "amount": round(v[1], 2)}
                                  for r, v in by_reason.items()},
                    "filters": {"plate": args.plate, "site": args.site,
                                "days": args.days, "reasons": reasons}},
        ))
        db.commit()
        print(f"✅ Цуцлав: {len(comps)} өр · {money(total)} · {len(plates)} машин")
        if lift:
            print(f"✅ Хар жагсаалтаас чөлөөлөв: {len(lift)} дугаар")
        print("   AuditLog: COMPENSATION_BULK_CANCEL\n")
        print("   ЦААШИД ДАХИН ХУРИМТЛАГДАХААС СЭРГИЙЛЭХ:")
        print("   Тохиргоо → Авто цэвэрлэгээ хэсгээс «өр үүсгэх» (create_debt)-ийг")
        print("   унтраана. Үгүй бол долоо хоногт ~4 сая₮ дахин үүснэ.\n")
    finally:
        db.close()


if __name__ == "__main__":
    main()
