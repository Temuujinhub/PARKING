#!/usr/bin/env python3
"""Үндэслэлгүй өрийг (нөхөн төлбөр) буцааж цэвэрлэх — 2 нөхцөлөөр.

Нөхөж бүртгэх/аудитын цэвэрлэгээний үед БҮХ гацсан машиныг өр болгосноос
үндэслэлгүй өр асар их үүссэн (2026-08-10: нийт 25 сая₮). Эдгээрээс:

  1. БУРУУ ТАНИГДСАН дугаар (junk) — «726ДДЦ», «К319УБ», «5557КК» гэх мэт
     формат буруу уншилт. Ийм машин байхгүй тул өр нэхэх хүн ч байхгүй.

  2. ЗӨВХӨН ОРОХ талд уншигдсан — гарах камерт огт уншигдаагүй тул машин
     ХЭЗЭЭ гарсныг мэдэхгүй. Төлбөр нь таамаг (ихэвчлэн өдрийн дээд хязгаар)
     тул өр болгох үндэслэлгүй.

Хийх үйлдэл: нэхэмжлэлийг ЦУЦЛАХ (CANCELLED — устгахгүй, түүх үлдэнэ) +
сешний дүнг тэглэж «үнэгүй» болгоно. Ингэснээр тайлангийн тэнцэл хэвээр
хадгалагдана (Үүссэн ч хамт буурна).

    sudo /root/PARKING/backend/venv/bin/python /root/PARKING/tools/clean_bad_debts.py
    sudo ... clean_bad_debts.py --site "Кэй Эйч" --apply
    sudo ... clean_bad_debts.py --junk-only --apply      # зөвхөн буруу дугаар
    sudo ... clean_bad_debts.py --entry-only-only --apply # зөвхөн орох талынх
"""
import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime

os.chdir("/root/PARKING/backend")
sys.path.insert(0, "/root/PARKING/backend")

from app.database import SessionLocal  # noqa: E402
from app.models import (AuditLog, Compensation, LprEvent,  # noqa: E402
                        ParkingSession, ParkingSite)
from app.session_logic import is_valid_plate  # noqa: E402

# Камерын логоос нөхөж бүртгэсэн сешн — эдгээрт ГАРАХ уншилтын нотолгоо бий
# (камерын дотоод логоос), тиймээс exit_device_id хоосон ч «зөвхөн орох» БИШ
BACKFILL_MARK = "камерын логоос нөхөж"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--site", action="append", default=[], help="Зогсоолын нэрийн хэсэг")
    ap.add_argument("--junk-only", action="store_true", help="Зөвхөн буруу дугаар")
    ap.add_argument("--entry-only-only", action="store_true", help="Зөвхөн орох талынх")
    ap.add_argument("--apply", action="store_true", help="Бодитоор цуцлах")
    args = ap.parse_args()
    do_junk = not args.entry_only_only
    do_entry = not args.junk_only

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

        comps = (db.query(Compensation)
                 .filter(Compensation.status == "PENDING",
                         Compensation.site_id.in_(wanted)).all())
        if not comps:
            print("Төлөгдөөгүй өр алга.")
            return
        total_all = sum(float(c.amount) for c in comps)
        print(f"Төлөгдөөгүй өр: {len(comps)} · {total_all:,.0f}₮")

        # Гарах камерт уншигдсан эсэх — серверийн LPR логоос (дугаар × зогсоол)
        exit_seen = {(p, s) for p, s in
                     db.query(LprEvent.plate_number, LprEvent.site_id)
                     .filter(LprEvent.lane_dir == "exit",
                             LprEvent.site_id.in_(wanted)).distinct().all()}

        picked = []
        for c in comps:
            s = db.get(ParkingSession, c.session_id) if c.session_id else None
            reasons = []
            if do_junk and not is_valid_plate(c.plate_number):
                reasons.append("буруу дугаар")
            if do_entry and s is not None:
                backfilled = BACKFILL_MARK in (s.note or "")
                has_exit = (s.exit_device_id is not None
                            or (c.plate_number, s.site_id) in exit_seen
                            or backfilled)
                if not has_exit:
                    reasons.append("зөвхөн орох уншилт")
            if reasons:
                picked.append((c, s, ", ".join(reasons)))

        if not picked:
            print("Шалгуурт нийцэх өр олдсонгүй.")
            return

        by_site = defaultdict(lambda: [0, 0.0])
        by_reason = defaultdict(lambda: [0, 0.0])
        for c, _s, why in picked:
            b = by_site[sites.get(c.site_id, "?")]
            b[0] += 1
            b[1] += float(c.amount)
            r = by_reason[why]
            r[0] += 1
            r[1] += float(c.amount)

        print("\n── Цуцлагдах өр ──")
        for name in sorted(by_site):
            cnt, amt = by_site[name]
            print(f"  {name:22} {cnt:>5} өр · {amt:>12,.0f}₮")
        print("\n  Шалтгаанаар:")
        for why in sorted(by_reason, key=lambda w: -by_reason[w][1]):
            cnt, amt = by_reason[why]
            print(f"    {why:26} {cnt:>5} өр · {amt:>12,.0f}₮")

        print("\n  Хамгийн том 10:")
        for c, s, why in sorted(picked, key=lambda x: -float(x[0].amount))[:10]:
            ent = f"{s.entry_time:%m-%d %H:%M}" if s and s.entry_time else "—"
            print(f"    {c.plate_number:10} {float(c.amount):>10,.0f}₮  орсон {ent}  ({why})")

        total = sum(float(c.amount) for c, _s, _w in picked)
        keep = len(comps) - len(picked)
        print(f"\nНИЙТ ЦУЦЛАХ: {len(picked)} өр · {total:,.0f}₮")
        print(f"ХЭВЭЭР ҮЛДЭХ: {keep} өр · {total_all - total:,.0f}₮ "
              f"(гарах уншилттай, зөв дугаартай — бодит өр)")

        if not args.apply:
            print("\nЭнэ бол DRY-RUN — юу ч өөрчлөгдөөгүй. Бодитоор хийхдээ --apply нэмнэ.")
            return

        stamp = datetime.utcnow().strftime("%Y-%m-%d")
        zeroed = 0
        for c, s, why in picked:
            c.status = "CANCELLED"
            # Сешний дүнг ч тэглэнэ — эс бол «үүссэн» хэвээр үлдэж тайлангийн
            # тэнцэл дахин зөрнө (Үүссэн = Хураасан + Хүлээгдэж буй + Өр болсон)
            if s is not None and float(s.total_fee or 0) > 0:
                s.base_fee, s.vat_amount, s.total_fee = 0, 0, 0
                if s.status in ("MANUAL_CLOSED", "CLOSED", "AWAITING_PAYMENT"):
                    s.status = "FREE"
                mark = f"[{stamp}] өр цуцлав ({why}) — үндэслэлгүй тул үнэгүй болгов"
                s.note = f"{s.note}\n{mark}" if s.note else mark
                zeroed += 1
        db.add(AuditLog(username="system", action="BAD_DEBT_CLEANUP", entity="compensation",
                        entity_id=None,
                        detail={"cancelled": len(picked), "amount": round(total, 2),
                                "sessions_zeroed": zeroed,
                                "sites": args.site or "бүгд",
                                "junk": do_junk, "entry_only": do_entry}))
        db.commit()
        print(f"\n✅ {len(picked)} өр цуцлагдлаа ({total:,.0f}₮), "
              f"{zeroed} сешний дүн тэгширлээ.")
        print("Тайлан → «Өр болсон» болон «Үүссэн» хамт буурч, тэнцэл хэвээр байна.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
