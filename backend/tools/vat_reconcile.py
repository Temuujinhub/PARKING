"""ТЕГ-ийн (etax/ebarimt мерчант портал) баримтын экспортыг манай vat_receipts-тэй тулгах.

    venv/bin/python tools/vat_reconcile.py <ТЕГ_export.xlsx> [--tz-shift 8] [--tol 3]

ЯАГААД ДДТД-ЭЭР ТУЛГАЖ БОЛОХГҮЙ ВЭ (2026-08-19-нд нотлогдсон, docs/20260819.xlsx):
Суваг бүр (QPay ebarimt_v3, msgbill/Онлайм PosAPI) билл үүсгэхдээ ӨӨРИЙН операторын
кодтой ДДТД буцаадаг (QPay: 030101065006…, Онлайм: 029100244106…), харин ТЕГ
эцсийн бүртгэлдээ ТАТВАР ТӨЛӨГЧИЙН ТТД (0152000200 90…) + өөрийн дараалсан
counter-оор ӨӨР ДДТД олгодог. Цаг(UTC)+дүнгээр тулгахад секунд хүртэл таардаг
(7/9 QPay, 3/3 msgbill) тул тулгалтыг (цаг±tol, дүн)-ээр хийнэ.

ТЕГ файлын багана: Пос дугаар | ДДТД | Огноо(UTC) | Нийт дүн | НХАТ | НӨАТ | Цэвэр дүн
| Х/А регистр | Х/А нэр | Хаанаас | НӨАТ төлөгч эсэх | Пос дугаар | Систем нийлүүлэгч | Байршлын алба

Манай paid_at нь UTC тул анхдагчаар shift=0; ТЕГ файл локал цагтай бол --tz-shift -8.
"""
import argparse
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import openpyxl

from app.database import SessionLocal
from app.models import ParkingSession, Payment, VatReceipt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("xlsx")
    ap.add_argument("--tz-shift", type=float, default=0,
                    help="ТЕГ файлын цагт нэмэх цаг (файл UTC бол 0, локал бол -8)")
    ap.add_argument("--tol", type=int, default=3, help="цагийн зөрүүний хязгаар (сек)")
    args = ap.parse_args()

    ws = openpyxl.load_workbook(args.xlsx).active
    tax = []
    for r in list(ws.iter_rows(values_only=True))[1:]:
        if not r or not r[1]:
            continue
        dt = datetime.strptime(str(r[2])[:19], "%Y-%m-%d %H:%M:%S") + timedelta(hours=args.tz_shift)
        tax.append({"ddtd": str(r[1]), "dt": dt, "amount": float(r[3] or 0),
                    "src": str(r[12] or ""), "used": False})
    if not tax:
        print("ТЕГ файлаас мөр уншсангүй"); return 1
    lo = min(t["dt"] for t in tax) - timedelta(hours=1)
    hi = max(t["dt"] for t in tax) + timedelta(hours=1)
    print(f"ТЕГ: {len(tax)} баримт ({lo:%Y-%m-%d %H:%M} — {hi:%H:%M} UTC), эх сурвалж: "
          + ", ".join(sorted({t['src'] for t in tax})))

    db = SessionLocal()
    ours = (db.query(VatReceipt, Payment, ParkingSession.plate_number)
            .join(Payment, VatReceipt.payment_id == Payment.id)
            .outerjoin(ParkingSession, VatReceipt.session_id == ParkingSession.id)
            .filter(Payment.paid_at >= lo, Payment.paid_at < hi).all())
    print(f"Манайх: {len(ours)} баримт (paid_at-аар)")

    matched, ddtd_equal, unmatched_ours = 0, 0, []
    for rec, pay, plate in ours:
        if rec.status == "CANCELLED":
            continue
        cand = [t for t in tax if not t["used"] and abs(float(rec.amount) - t["amount"]) < 1
                and abs((t["dt"] - pay.paid_at).total_seconds()) <= args.tol]
        if cand:
            t = min(cand, key=lambda x: abs((x["dt"] - pay.paid_at).total_seconds()))
            t["used"] = True
            matched += 1
            if rec.ebarimt_id == t["ddtd"]:
                ddtd_equal += 1
        else:
            unmatched_ours.append((pay.paid_at, plate, float(rec.amount), rec.status,
                                   rec.provider or "POSAPI", (rec.ebarimt_id or "")[:12]))

    print(f"\nТААРСАН: {matched} (үүнээс ДДТД яг ижил: {ddtd_equal} — суваг ДДТД-г "
          f"дахин дугаарладаг тул 0 байх нь хэвийн)")
    print(f"\nМАНАЙД БИЙ, ТЕГ-Д АЛГА: {len(unmatched_ours)} "
          "(mock/FAILED баримт, Monnis өөрийн ТТД, цагийн зөрүү байж болно)")
    for row in sorted(unmatched_ours)[:30]:
        print("  ", *row)
    left = [t for t in tax if not t["used"]]
    print(f"\nТЕГ-Д БИЙ, МАНАЙД АЛГА: {len(left)} "
          "(өөр систем/POS-оос үүссэн эсвэл манай баримт бүртгэлгүй)")
    from collections import Counter
    print("   эх сурвалжаар:", dict(Counter(t["src"] for t in left)))
    for t in sorted(left, key=lambda x: x["dt"])[:20]:
        print(f"   {t['dt']:%H:%M:%S} {t['amount']:>8.0f}₮ {t['src']} {t['ddtd']}")
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
