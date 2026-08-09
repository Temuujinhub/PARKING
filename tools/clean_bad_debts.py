#!/usr/bin/env python3
"""Үндэслэлгүй өрийг (нөхөн төлбөр) буцааж цэвэрлэх — 3 нөхцөлөөр.

Нөхөж бүртгэх/аудитын цэвэрлэгээний үед БҮХ гацсан машиныг өр болгосноос
үндэслэлгүй өр асар их үүссэн (2026-08-10: нийт 25 сая₮). Эдгээрээс:

  1. БУРУУ ТАНИГДСАН дугаар (junk) — «726ДДЦ», «К319УБ», «5557КК» гэх мэт
     формат буруу уншилт. Ийм машин байхгүй тул өр нэхэх хүн ч байхгүй.

  2. ЦАГИЙН ЗААГ (--entry-before) — нэвтрүүлэлт/тохируулгын үеийн бүх өрийг
     нэг мөсөн болиулах. Тэр цагаас ӨМНӨ орсон машины өр бүхэлдээ цуцлагдана.

  3. ЗӨВХӨН ОРОХ талд уншигдсан — гарах камерт огт уншигдаагүй тул машин
     ХЭЗЭЭ гарсныг мэдэхгүй. Төлбөр нь таамаг (ихэвчлэн өдрийн дээд хязгаар)
     тул өр болгох үндэслэлгүй.

Хийх үйлдэл: нэхэмжлэлийг ЦУЦЛАХ (CANCELLED — устгахгүй, түүх үлдэнэ) +
сешний дүнг тэглэж «үнэгүй» болгоно. Ингэснээр тайлангийн тэнцэл хэвээр
хадгалагдана (Үүссэн ч хамт буурна).

    sudo /root/PARKING/backend/venv/bin/python /root/PARKING/tools/clean_bad_debts.py
    sudo ... clean_bad_debts.py --site "Кэй Эйч" --apply
    sudo ... clean_bad_debts.py --junk-only --apply      # зөвхөн буруу дугаар
    sudo ... clean_bad_debts.py --entry-only-only --apply # зөвхөн орох талынх
    # бүх зогсоол, өнөөдөр 09:00-аас өмнөх БҮГД + junk + зөвхөн орох:
    sudo ... clean_bad_debts.py --entry-before "2026-08-10 09:00" --apply
"""
import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta

os.chdir("/root/PARKING/backend")
sys.path.insert(0, "/root/PARKING/backend")

from sqlalchemy import func  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import (AuditLog, Compensation, LprEvent,  # noqa: E402
                        ParkingSession, ParkingSite, Payment)
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
    ap.add_argument("--entry-before", default=None,
                    help="Энэ ЛОКАЛ цагаас өмнө орсон бүх машины өрийг цуцлах "
                         "(ж: «2026-08-10 09:00» эсвэл «2026-08-10»). Нэвтрүүлэлтийн "
                         "үеийн бүх өрийг нэг мөсөн болиулахад.")
    ap.add_argument("--apply", action="store_true", help="Бодитоор цуцлах")
    args = ap.parse_args()
    do_junk = not args.entry_only_only
    do_entry = not args.junk_only

    # Локал цагийг UTC болгоно (DB нь UTC хадгалдаг)
    before_utc = None
    if args.entry_before:
        from app.config import settings as _cfg
        raw = args.entry_before.strip()
        if len(raw) == 10:            # зөвхөн огноо → тухайн өдрийн 00:00
            raw += " 00:00"
        before_utc = (datetime.fromisoformat(raw.replace(" ", "T"))
                      - timedelta(hours=_cfg.tz_offset_hours))
        print(f"Цагийн зааг: {args.entry_before} (локал) = {before_utc:%Y-%m-%d %H:%M} UTC")

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
            # (1) Цагийн зааг — тэр үеэс өмнө ОРСОН машины өр бүхэлдээ
            if before_utc is not None and s is not None and s.entry_time \
                    and s.entry_time < before_utc:
                reasons.append("заагаас өмнө орсон")
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
        # Сешн бүрд ХУРААГДСАН дүн — үүнээс доош тэглэж БОЛОХГҮЙ (эс бол
        # хураасан нь үүссэнээс давж цуглуулалт 100%+ гардаг: Рашбулаг 107%)
        sess_ids = [c.session_id for c, _s, _w in picked if c.session_id]
        collected: dict[str, float] = defaultdict(float)
        for chunk in [sess_ids[i:i + 900] for i in range(0, len(sess_ids), 900)]:
            for sid, amt in (db.query(Payment.session_id, func.sum(Payment.amount))
                             .filter(Payment.session_id.in_(chunk),
                                     Payment.status == "PAID")
                             .group_by(Payment.session_id).all()):
                collected[sid] += float(amt or 0)
            for sid, amt in (db.query(Compensation.session_id, func.sum(Compensation.amount))
                             .filter(Compensation.session_id.in_(chunk),
                                     Compensation.status == "PAID")
                             .group_by(Compensation.session_id).all()):
                collected[sid] += float(amt or 0)

        zeroed = 0
        for c, s, why in picked:
            c.status = "CANCELLED"
            # Сешний дүнг хураасан дүн хүртэл БУУЛГАНА (тэглэхгүй) — эс бол
            # тайлангийн тэнцэл зөрнө (Үүссэн = Хураасан + Хүлээгдэж буй + Өр)
            keep = collected.get(c.session_id, 0.0)
            if s is not None and float(s.total_fee or 0) > keep:
                vat = round(keep * 0.1 / 1.1)
                s.total_fee, s.vat_amount, s.base_fee = keep, vat, keep - vat
                if keep <= 0 and s.status in ("MANUAL_CLOSED", "CLOSED", "AWAITING_PAYMENT"):
                    s.status = "FREE"
                mark = f"[{stamp}] өр цуцлав ({why}) — үндэслэлгүй тул үнэгүй болгов"
                s.note = f"{s.note}\n{mark}" if s.note else mark
                zeroed += 1
        db.add(AuditLog(username="system", action="BAD_DEBT_CLEANUP", entity="compensation",
                        entity_id=None,
                        detail={"cancelled": len(picked), "amount": round(total, 2),
                                "sessions_zeroed": zeroed,
                                "sites": args.site or "бүгд",
                                "junk": do_junk, "entry_only": do_entry,
                                "entry_before": args.entry_before}))
        db.commit()
        print(f"\n✅ {len(picked)} өр цуцлагдлаа ({total:,.0f}₮), "
              f"{zeroed} сешний дүн тэгширлээ.")
        print("Тайлан → «Өр болсон» болон «Үүссэн» хамт буурч, тэнцэл хэвээр байна.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
