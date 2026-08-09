#!/usr/bin/env python3
"""Өрөөр цуглуулсан МӨНГИЙГ орлогын бүртгэлд нөхөж оруулах.

Асуудал: `pay_compensation` endpoint 2026-08-09 хүртэл нэхэмжлэлийг зөвхөн
PAID болгодог байсан бөгөөд Payment (төлбөрийн бичилт) ҮҮСГЭДЭГГҮЙ байв.
Тиймээс кассчны өрөөр авсан бэлэн мөнгө:
  • «Нийт орлого», ээлжийн тооцоо, мөнгөн тооцоонд ОГТ харагдахгүй
  • харин «Хураасан» баганад тоологддог (нэхэмжлэл нь PAID тул)
→ Рашбулаг: Хураасан 194,000₮ атал Нийт орлого 177,000₮ (зөрүү 17,000₮).

Энэ хэрэгсэл нь тэдгээр төлөгдсөн нэхэмжлэл бүрд Payment мөр нөхөж үүсгэнэ.
Төлбөрийн хэрэгсэл (бэлэн/карт), кассчин, цагийг АУДИТЫН ЛОГООС сэргээнэ
(COMPENSATION_PAID бичилтэд method хадгалагдсан).

    sudo /root/PARKING/backend/venv/bin/python /root/PARKING/tools/backfill_debt_payments.py
    sudo ... backfill_debt_payments.py --apply
"""
import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime

os.chdir("/root/PARKING/backend")
sys.path.insert(0, "/root/PARKING/backend")

from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import (AuditLog, Compensation, ParkingSite,  # noqa: E402
                        Payment, User)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="Бодитоор бүртгэх")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        sites = {s.id: s.name for s in db.query(ParkingSite).all()}
        # Төлөгдсөн ч төлбөрийн бичилтгүй нэхэмжлэлүүд
        comps = (db.query(Compensation)
                 .filter(Compensation.status == "PAID",
                         Compensation.payment_id.is_(None)).all())
        if not comps:
            print("Бүх төлөгдсөн нэхэмжлэл төлбөрийн бичилттэй — нөхөх зүйл алга.")
            return

        # Аудитын логоос төлбөрийн хэрэгсэл ба кассчныг сэргээнэ
        audit = {}
        for a in (db.query(AuditLog)
                  .filter(AuditLog.action == "COMPENSATION_PAID",
                          AuditLog.entity == "compensation").all()):
            if a.entity_id:
                audit[a.entity_id] = a
        users = {u.username: u.id for u in db.query(User).all()}

        rows, no_session = [], 0
        for c in comps:
            if not c.session_id:
                no_session += 1
                continue
            a = audit.get(c.id)
            method = ((a.detail or {}).get("method") if a and isinstance(a.detail, dict)
                      else None) or "CASH"
            who = (a.username if a else None) or c.paid_by or "system"
            rows.append((c, method, who))

        by_site = defaultdict(lambda: [0, 0.0])
        by_method = defaultdict(lambda: [0, 0.0])
        for c, method, _who in rows:
            b = by_site[sites.get(c.site_id, "?")]
            b[0] += 1
            b[1] += float(c.amount)
            m = by_method[method]
            m[0] += 1
            m[1] += float(c.amount)

        print(f"Төлбөрийн бичилтгүй, ТӨЛӨГДСӨН нэхэмжлэл: {len(rows)}")
        print("\n── Зогсоолоор ──")
        for name in sorted(by_site):
            cnt, amt = by_site[name]
            print(f"  {name:22} {cnt:>4} · {amt:>10,.0f}₮")
        print("\n── Төлбөрийн хэрэгслээр (аудитын логоос) ──")
        for m in sorted(by_method):
            cnt, amt = by_method[m]
            print(f"  {m:10} {cnt:>4} · {amt:>10,.0f}₮")
        total = sum(float(c.amount) for c, _m, _w in rows)
        print(f"\nНИЙТ нөхөх орлого: {total:,.0f}₮")
        if no_session:
            print(f"Алгасах (session-гүй): {no_session}")

        if not args.apply:
            print("\nЭнэ бол DRY-RUN — юу ч өөрчлөгдөөгүй. Бодитоор хийхдээ --apply нэмнэ.")
            return

        made = 0
        for c, method, who in rows:
            paid_at = c.paid_at or datetime.utcnow()
            amount = float(c.amount)
            vat = round(amount * settings.vat_rate / (1 + settings.vat_rate))
            db.add(Payment(
                session_id=c.session_id,
                provider="CASH" if method == "CASH" else "POS",
                payment_method="CASH" if method == "CASH" else "CARD",
                source="POS",
                sender_invoice_no=f"DEBT-{c.id[:8].upper()}-{paid_at:%Y%m%d%H%M%S}",
                amount=amount, vat_amount=vat, status="PAID", paid_at=paid_at,
                cashier_id=users.get(who),
            ))
            db.flush()
            made += 1
        # payment_id-г холбоно (шинэ мөрүүдийг дугаараар нь олох шаардлагагүй —
        # sender_invoice_no давхцахгүй тул нэг бүрчлэн тааруулна)
        for c, _m, _w in rows:
            pay = (db.query(Payment)
                   .filter(Payment.sender_invoice_no.like(f"DEBT-{c.id[:8].upper()}-%"))
                   .order_by(Payment.paid_at.desc()).first())
            if pay:
                c.payment_id = pay.id
        db.add(AuditLog(username="system", action="DEBT_PAYMENT_BACKFILL",
                        entity="payment", entity_id=None,
                        detail={"created": made, "amount": round(total, 2)}))
        db.commit()
        print(f"\n✅ {made} төлбөрийн бичилт нөхөв ({total:,.0f}₮).")
        print("Тайлангийн «Нийт орлого» энэ дүнгээр нэмэгдэж, «Хураасан»-тай таарна.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
