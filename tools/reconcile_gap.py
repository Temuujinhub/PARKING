#!/usr/bin/env python3
"""Тайлангийн ЗӨРҮҮГ сешн бүрээр задалж, шаардвал авлага болгож нөхөх.

Тэнцэл байх ёстой:   Үүссэн = Хураасан + Хүлээгдэж буй + Өр болсон

Зөрүү үлдвэл тэр нь «хураагдаагүй, өр ч болоогүй» дүн — ихэвчлэн цэвэрлэгээний
үед нэхэмжлэл нь цуцлагдсан ч сешний төлбөр нь үлдсэнээс болдог. Энэ хэрэгсэл:

  1. Зөрүүг сешн бүрээр олж, ЯАГААД гарсныг ангилж харуулна
  2. Төлөгдсөн (PAID) төлбөр/нэхэмжлэл хөндөгдсөн эсэхийг АУДИТААР шалгана
  3. --apply өгвөл үлдэгдэл бүрд нөхөн төлбөр (авлага) үүсгэж тэнцэлд оруулна

    sudo /root/PARKING/backend/venv/bin/python /root/PARKING/tools/reconcile_gap.py \
        --site "Моннис" --from 2026-07-01
    # авлага болгож нөхөх:
        ... --site "Моннис" --apply
"""
import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime

os.chdir("/root/PARKING/backend")
sys.path.insert(0, "/root/PARKING/backend")

from sqlalchemy import func  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import (AuditLog, Compensation, ParkingSession,  # noqa: E402
                        ParkingSite, Payment)

# «Хүлээгдэж буй» гэж тайланд тоологддог төлөв — зөрүү гэж давхар тоолохгүй
AWAITING = {"AWAITING_PAYMENT"}
LIVE = {"OPEN", "PAID"}


def _find_overcleaned(db, wanted, sites):
    """Өр нь ТӨЛӨГДСӨН мөртлөө дүн нь тэглэгдсэн сешнүүд → [(session, төлсөн_дүн)].

    Ийм сешнд өөрийн Payment мөр байдаггүй (өр нь өөр сешний төлбөрт
    нийлүүлэгдсэн) тул цэвэрлэгээ «хураагдаагүй» гэж андуурсан."""
    paid_comp = (db.query(Compensation.session_id, func.sum(Compensation.amount))
                 .filter(Compensation.status == "PAID",
                         Compensation.session_id.isnot(None),
                         Compensation.site_id.in_(wanted))
                 .group_by(Compensation.session_id).all())
    out = []
    for sid, amt in paid_comp:
        s = db.get(ParkingSession, sid)
        if s is None:
            continue
        if float(s.total_fee or 0) < float(amt or 0) - 0.5:
            out.append((s, float(amt or 0)))
    return out


def _cashflow_breakdown(db, wanted, sites, args):
    """«Нийт орлого > Үүссэн» яагаад болохыг задалж харуулна.

    Нийт орлого нь мөнгө ОРСОН цагаар (paid_at) тоологддог тул тухайн мужаас
    ӨМНӨ орсон машины төлбөр, хуучин өрийн төлөлт ч энд ордог. Үүссэн нь
    зөвхөн мужид ОРСОН машины дүн. Хоёр өөр зүйл — зөрүү нь алдаа биш."""
    if not (args.date_from or args.until):
        return
    start = datetime.fromisoformat(args.date_from) if args.date_from else datetime(1970, 1, 1)
    end = datetime.fromisoformat(args.until) if args.until else datetime(2999, 1, 1)

    print("\n── «Нийт орлого» задаргаа (мужид орсон мөнгө хаанаас ирсэн бэ) ──")
    for sid in sorted(wanted, key=lambda i: sites.get(i, "")):
        q = (db.query(func.coalesce(func.sum(Payment.amount), 0))
             .select_from(ParkingSession)
             .join(Payment, Payment.session_id == ParkingSession.id)
             .filter(ParkingSession.site_id == sid, Payment.status == "PAID",
                     Payment.paid_at >= start, Payment.paid_at < end))
        total = float(q.scalar() or 0)
        if not total:
            continue
        inside = float(q.filter(ParkingSession.entry_time >= start,
                                ParkingSession.entry_time < end).scalar() or 0)
        before = total - inside
        debt = float(db.query(func.coalesce(func.sum(Compensation.amount), 0))
                     .filter(Compensation.site_id == sid, Compensation.status == "PAID",
                             Compensation.paid_at >= start,
                             Compensation.paid_at < end).scalar() or 0)
        print(f"  {sites.get(sid, '?'):22} нийт {total:>10,.0f}₮ = "
              f"мужид орсон машинаас {inside:>10,.0f}₮ + "
              f"өмнөх хугацааны машинаас {before:>9,.0f}₮"
              + (f" (үүнээс өр {debt:,.0f}₮)" if debt else ""))
    print("  → «Нийт орлого» кассын мөнгөн урсгал тул «Үүссэн»-ээс их байж болно.")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--site", action="append", default=[], help="Зогсоолын нэрийн хэсэг")
    ap.add_argument("--from", dest="date_from", default=None, help="YYYY-MM-DD")
    ap.add_argument("--until", default=None, help="YYYY-MM-DD")
    ap.add_argument("--min-gap", type=float, default=1.0, help="Үүнээс бага зөрүүг үл тоох")
    ap.add_argument("--apply", action="store_true", help="Үлдэгдлийг авлага болгож бүртгэх")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        sites = {s.id: s.name for s in db.query(ParkingSite).all()}
        wanted = set(sites)
        if args.site:
            wanted = {sid for sid, name in sites.items()
                      if any(p.strip().lower() in (name or "").lower() for p in args.site)}
            if not wanted:
                print("Зогсоол олдсонгүй. Байгаа:", ", ".join(sorted(sites.values())))
                sys.exit(1)

        # ── 1. Төлөгдсөн зүйл хөндөгдсөн эсэхийг эхлээд БАТАЛНА ──
        print("── Аюулгүй байдлын шалгалт: төлөгдсөн мөнгө хөндөгдсөн үү? ──")
        paid_pay = db.query(func.count(Payment.id)).filter(Payment.status == "PAID").scalar()
        paid_comp = (db.query(func.count(Compensation.id))
                     .filter(Compensation.status == "PAID").scalar())
        cancelled = (db.query(func.count(Compensation.id))
                     .filter(Compensation.status == "CANCELLED").scalar())
        print(f"  Төлөгдсөн төлбөр   : {paid_pay:,} бичилт (цэвэрлэгээ ХЭЗЭЭ Ч хөндөөгүй)")
        print(f"  Төлөгдсөн нэхэмжлэл: {paid_comp:,} (цэвэрлэгээ зөвхөн PENDING-д хүрсэн)")
        print(f"  Цуцлагдсан нэхэмжлэл: {cancelled:,}")
        for act in ("TEST_DEBT_CLEANUP", "TEST_PERIOD_CLEANUP", "PHANTOM_DURATION_CLEANUP"):
            for a in (db.query(AuditLog).filter(AuditLog.action == act)
                      .order_by(AuditLog.created_at).all()):
                print(f"  {a.created_at:%Y-%m-%d %H:%M} {act}: {a.detail}")

        # ── 2. Сешн бүрийн зөрүү ──
        q = db.query(ParkingSession).filter(ParkingSession.site_id.in_(wanted),
                                            ParkingSession.total_fee.isnot(None),
                                            ParkingSession.total_fee > 0)
        if args.date_from:
            q = q.filter(ParkingSession.entry_time >= datetime.fromisoformat(args.date_from))
        if args.until:
            q = q.filter(ParkingSession.entry_time < datetime.fromisoformat(args.until))
        rows = q.all()
        ids = [s.id for s in rows]
        if not ids:
            print("\nСешн олдсонгүй.")
            return

        def _sum_map(query):
            out = defaultdict(float)
            for chunk in [ids[i:i + 900] for i in range(0, len(ids), 900)]:
                for sid, amt in query(chunk):
                    out[sid] += float(amt or 0)
            return out

        paid_by = _sum_map(lambda c: db.query(Payment.session_id, func.sum(Payment.amount))
                           .filter(Payment.session_id.in_(c), Payment.status == "PAID")
                           .group_by(Payment.session_id).all())
        # Төлбөрт нийлүүлэгдсэн ӨӨР сешний өр — энэ сешний хураамж биш
        other_debt = _sum_map(
            lambda c: db.query(Payment.session_id, func.sum(Compensation.amount))
            .select_from(Payment).join(Compensation, Compensation.payment_id == Payment.id)
            .filter(Payment.session_id.in_(c), Payment.status == "PAID",
                    Compensation.status == "PAID",
                    Compensation.session_id != Payment.session_id)
            .group_by(Payment.session_id).all())
        pending_by = _sum_map(
            lambda c: db.query(Compensation.session_id, func.sum(Compensation.amount))
            .filter(Compensation.session_id.in_(c), Compensation.status == "PENDING")
            .group_by(Compensation.session_id).all())
        # Цуцлагдсан нэхэмжлэлтэй сешнүүд — зөрүүний шалтгааныг таних
        had_cancelled = {sid for (sid,) in db.query(Compensation.session_id)
                         .filter(Compensation.session_id.in_(ids),
                                 Compensation.status == "CANCELLED").distinct().all()}

        gaps = []
        for s in rows:
            if s.status in LIVE or s.status in AWAITING:
                continue     # амьд/хүлээгдэж буй — тайланд өөр баганаар тоологдоно
            own_paid = max(0.0, paid_by.get(s.id, 0.0) - other_debt.get(s.id, 0.0))
            gap = float(s.total_fee or 0) - own_paid - pending_by.get(s.id, 0.0)
            if gap >= args.min_gap:
                why = ("цуцлагдсан нэхэмжлэлийн үлдэгдэл" if s.id in had_cancelled
                       else ("хэсэгчлэн төлөгдсөн" if own_paid > 0
                             else "төлөгдөөгүй, өр ч болоогүй"))
                gaps.append((s, gap, own_paid, why))

        # ── 2.5 ЦЭВЭРЛЭГЭЭ ИЛҮҮ УСТГАСАН ЭСЭХ ──
        # clean_test_period нь «төлбөр төлөгдсөн» сешнийг ӨӨРИЙНХ нь Payment
        # мөрөөр л таньдаг байв. Гэтэл машины ӨРийг дараагийн ирэлтэд нь ӨӨР
        # сешний төлбөрт нийлүүлж авдаг (include_debts) тул тэр сешн дээр
        # Payment мөр байдаггүй → цэвэрлэгээ түүнийг «хураагдаагүй» гэж үзэж
        # дүнг нь тэглэсэн. Мөнгө нь авагдсан хэрнээ «үүссэн» нь 0 болсон учир
        # Нийт орлого > Үүссэн болж байсан. Эдгээрийг сэргээнэ.
        over = _find_overcleaned(db, wanted, sites)
        if over:
            total_restore = sum(amt - float(s.total_fee or 0) for s, amt in over)
            print(f"\n── ⚠ Цэвэрлэгээ ИЛҮҮ устгасан: {len(over)} сешн · "
                  f"{total_restore:,.0f}₮ сэргээх ──")
            print("  (өр нь ТӨЛӨГДСӨН мөртлөө сешний дүн тэглэгдсэн — мөнгө нь касст "
                  "орсон ч тайланд «үүссэн» гэж тоологдохгүй байна)")
            for s, amt in sorted(over, key=lambda x: -(x[1] - float(x[0].total_fee or 0)))[:8]:
                print(f"    {s.plate_number:10} {s.entry_time:%m-%d %H:%M} "
                      f"одоо {float(s.total_fee or 0):>7,.0f}₮ → төлөгдсөн өр {amt:>7,.0f}₮")
            if args.apply:
                for s, amt in over:
                    vat = round(amt * 0.1 / 1.1)
                    s.total_fee = amt
                    s.vat_amount = vat
                    s.base_fee = amt - vat
                    s.status = "CLOSED"      # өр нь төлөгдсөн тул хаагдсан
                    mark = f"[сэргээв] төлөгдсөн өр {amt:,.0f}₮-ийн дүнг буцаав"
                    s.note = f"{s.note}\n{mark}" if s.note else mark
                db.add(AuditLog(username="system", action="OVERCLEAN_RESTORE",
                                entity="session", entity_id=None,
                                detail={"sessions": len(over),
                                        "amount": round(total_restore, 2)}))
                db.commit()
                print(f"  ✅ {len(over)} сешний дүн сэргэв ({total_restore:,.0f}₮)")
            else:
                print("  --apply өгвөл эдгээрийн дүнг сэргээнэ.")

        _cashflow_breakdown(db, wanted, sites, args)

        if not gaps:
            print("\n✅ Зөрүү алга — Үүссэн = Хураасан + Хүлээгдэж буй + Өр болсон тэнцэж байна.")
            return

        by_site = defaultdict(lambda: [0, 0.0])
        by_why = defaultdict(lambda: [0, 0.0])
        for s, gap, _own, why in gaps:
            b = by_site[sites.get(s.site_id, "?")]
            b[0] += 1
            b[1] += gap
            w = by_why[why]
            w[0] += 1
            w[1] += gap

        print(f"\n── Зөрүү: {len(gaps)} сешн ──")
        for name in sorted(by_site):
            cnt, amt = by_site[name]
            print(f"  {name:22} {cnt:>5} сешн · {amt:>10,.0f}₮")
        print("\n  Шалтгаанаар:")
        for why in sorted(by_why, key=lambda w: -by_why[w][1]):
            cnt, amt = by_why[why]
            print(f"    {why:36} {cnt:>5} сешн · {amt:>10,.0f}₮")

        print("\n  Хамгийн том 10:")
        for s, gap, own, why in sorted(gaps, key=lambda g: -g[1])[:10]:
            print(f"    {s.plate_number:10} {s.entry_time:%m-%d %H:%M} "
                  f"үүссэн {float(s.total_fee or 0):>8,.0f}₮ хураасан {own:>8,.0f}₮ "
                  f"→ зөрүү {gap:>8,.0f}₮ ({why})")

        total_gap = sum(g for _s, g, _o, _w in gaps)
        print(f"\nНИЙТ ЗӨРҮҮ: {total_gap:,.0f}₮")

        if not args.apply:
            print("\nЭнэ бол DRY-RUN. --apply өгвөл эдгээр үлдэгдлийг НӨХӨН ТӨЛБӨР")
            print("(авлага) болгож бүртгэнэ → тайлангийн тэнцэл сэргэж, мөнгө нь")
            print("«Өр болсон» баганад харагдана (устгагдахгүй, нэхэгдэх боломжтой).")
            return

        for s, gap, _own, why in gaps:
            db.add(Compensation(session_id=s.id, site_id=s.site_id,
                                plate_number=s.plate_number, amount=gap,
                                reason="reconcile", created_by="system"))
        db.add(AuditLog(username="system", action="GAP_RECONCILE", entity="compensation",
                        entity_id=None,
                        detail={"sessions": len(gaps), "amount": round(total_gap, 2),
                                "sites": args.site or "бүгд"}))
        db.commit()
        print(f"\n✅ {len(gaps)} авлага үүсгэв ({total_gap:,.0f}₮). Тайлангийн «Өр болсон» "
              f"багана энэ дүнгээр нэмэгдэж, тэнцэл сэргэнэ.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
