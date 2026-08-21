#!/usr/bin/env python3
"""Төлөгдөөгүй хаагдсан бүртгэлийн бодогдсон дүнг ТЭГЛЭХ.

ХЭЗЭЭ ХЭРЭГЛЭХ: системийн гэмтлээс (камер гарах уншилтаа алдсан, цагийн
тохиргоо буруу байсан г.м) болж жолоочид ЗӨВ мэдэгдэлгүйгээр төлбөр
хуримтлагдсан үед. Тэр дүн нь цуглуулагдахгүй мөртөө тайланд «авлага» мэт
харагдаж, цуглуулалтын хувийг гажуудуулна.

ЖИШЭЭ (2026-08-21 Рашбулаг ЭТТ): өглөө 07-08:30-д орж орой 18-20 цагт гарсан
11 ажилчны машин, тус бүр 17,000₮ = 144,000₮. Тэдгээрийн гарах уншилт
алдагдсан тул `MANUAL_CLOSED` болж төлбөр үлдсэн. Жишээ нь `6201УБЦ` нь
08-10..08-14-нд ӨДӨР БҮР бэлнээр 21-27 мянга төлж байсан УЛАМЖЛАЛТ үйлчлүүлэгч
— 08-17-оос хойш гарах уншилт нь алдагдаж эхэлснээр төлбөр нь нэхэгдэхээ болив.

ЮУ ХИЙДЭГ: bodogdson дүнг 0, хугацааг арилгаж, PENDING нэхэмжлэлийг ЦУЦАЛНА.
Төлбөрийн (Payment) мөрийг ХЭЗЭЭ Ч хөндөхгүй — бодит мөнгө хэвээр.

⚠ ЭНЭ НЬ ШИНЖ ТЭМДГИЙГ АРИЛГАНА, ШАЛТГААНЫГ БИШ. Гарах уншилтыг сэргээхгүй
бол маргааш дахин ижил дүн хуримтлагдана.

    sudo /root/PARKING/backend/venv/bin/python /root/PARKING/tools/zero_unpaid_closed.py \
        --site "Рашбулаг" --from 2026-08-15
    sudo ... zero_unpaid_closed.py --site "Рашбулаг" --from 2026-08-15 --apply
"""
import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta

os.chdir("/root/PARKING/backend")
sys.path.insert(0, "/root/PARKING/backend")

from app.database import SessionLocal  # noqa: E402
from app.models import (AuditLog, Compensation, ParkingSession,  # noqa: E402
                        ParkingSite, Payment)

MARK = "системийн гэмтлийн улмаас дүн тэгсгэв"
# Хаагдсан ч төлбөр үлдсэн төлөвүүд
TARGET = ("MANUAL_CLOSED", "AWAITING_PAYMENT")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", action="append", required=True)
    ap.add_argument("--from", dest="dfrom", required=True)
    ap.add_argument("--to", dest="dto", default=None)
    ap.add_argument("--min-fee", type=float, default=1.0, help="үүнээс дээш дүнтэйг л")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    start = datetime.fromisoformat(a.dfrom)
    end = (datetime.fromisoformat(a.dto) if a.dto
           else datetime.utcnow() + timedelta(days=1))
    # ⚠ ХООСОН --site нь БҮХ зогсоолыг тааруулна (bash-ийн хоосон хувьсагч г.м).
    # Тест дээр яг ийм байдлаар 14 бүртгэл санамсаргүй зассан тул хатуу шалгана.
    pats = [x.strip() for x in a.site if x and x.strip()]
    if not pats:
        print("✗ --site хоосон байна. Зогсоолын нэрийг тодорхой бичнэ үү.")
        return 2

    db = SessionLocal()
    try:
        all_sites = db.query(ParkingSite).all()
        sites = [s for s in all_sites
                 if any(x.lower() in (s.name or "").lower() for x in pats)]
        if not sites:
            print(f"✗ «{', '.join(pats)}» -д тохирох зогсоол олдсонгүй")
            return 1
        if len(sites) == len(all_sites) and len(all_sites) > 1:
            print(f"✗ Шүүлтүүр БҮХ {len(all_sites)} зогсоолыг тааруулж байна — "
                  f"санамсаргүй байж магадгүй. Нэг бүрчлэн нэрлэнэ үү.")
            return 2
        print(f"Зогсоол: {', '.join(s.name for s in sites)}\n")

        stats = defaultdict(lambda: {"n": 0, "fee": 0.0, "comp": 0, "paid_skip": 0})
        rows_out = []
        for site in sites:
            rows = (db.query(ParkingSession)
                    .filter(ParkingSession.site_id == site.id,
                            ParkingSession.entry_time >= start,
                            ParkingSession.entry_time < end,
                            ParkingSession.status.in_(TARGET))
                    .order_by(ParkingSession.entry_time)
                    .all())
            for s in rows:
                fee = float(s.total_fee or 0)
                if fee < a.min_fee or MARK in (s.note or ""):
                    continue
                if s.paid_at or db.query(Payment.id).filter(
                        Payment.session_id == s.id, Payment.status == "PAID").first():
                    stats[site.name]["paid_skip"] += 1
                    continue
                comps = (db.query(Compensation)
                         .filter(Compensation.session_id == s.id,
                                 Compensation.status == "PENDING").all())
                st = stats[site.name]
                st["n"] += 1
                st["fee"] += fee
                st["comp"] += len(comps)
                rows_out.append((site.name, s.plate_number, s.entry_time,
                                 s.exit_time, s.status, fee, len(comps)))
                if not a.apply:
                    continue
                for c in comps:
                    c.status = "CANCELLED"
                s.base_fee = s.vat_amount = s.total_fee = 0
                s.duration_minutes = None
                s.note = f"{(s.note + ' | ') if s.note else ''}{MARK}"[:1000]

        print(f"{'ЗОГСООЛ':<20} {'БҮРТГЭЛ':>8} {'ДҮН₮':>12} {'нэхэмжлэл':>10} {'төлсөн→алгас':>13}")
        print("─" * 68)
        tot = defaultdict(float)
        for name, v in stats.items():
            print(f"{name[:20]:<20} {v['n']:>8} {v['fee']:>12,.0f} {v['comp']:>10} {v['paid_skip']:>13}")
            for k in v:
                tot[k] += v[k]
        print("─" * 68)
        print(f"{'НИЙТ':<20} {int(tot['n']):>8} {tot['fee']:>12,.0f} "
              f"{int(tot['comp']):>10} {int(tot['paid_skip']):>13}")

        if rows_out:
            print(f"\nЖагсаалт ({min(len(rows_out), 20)}/{len(rows_out)}):")
            for n, p, se, xe, stt, fee, nc in rows_out[:20]:
                x = f"{xe:%m-%d %H:%M}" if xe else "—"
                print(f"  {p:<9} {se:%m-%d %H:%M} → {x}  {stt:<16} {fee:>8,.0f}₮"
                      + (f"  нэхэмжлэл×{nc}" if nc else ""))

        if a.apply and tot["n"]:
            db.add(AuditLog(username="tools/zero_unpaid_closed.py",
                            action="ZERO_UNPAID_CLOSED", entity="session", entity_id="-",
                            detail={"count": int(tot["n"]), "fee_zeroed": tot["fee"],
                                    "comps_cancelled": int(tot["comp"]),
                                    "sites": [s.name for s in sites],
                                    "from": a.dfrom, "to": a.dto}))
            db.commit()
            print(f"\n✓ {int(tot['n'])} бүртгэлийн {tot['fee']:,.0f}₮ тэгсгэв")
            print("⚠ САНУУЛГА: энэ нь ШИНЖ ТЭМДГИЙГ арилгасан. Гарах уншилтыг "
                  "сэргээхгүй бол маргааш дахин ижил дүн хуримтлагдана.")
        elif tot["n"]:
            print("\n(dry-run — юу ч бичээгүй. Бодитоор хийхдээ --apply нэмнэ үү)")
        else:
            print("\nТэгсгэх бүртгэл олдсонгүй.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
