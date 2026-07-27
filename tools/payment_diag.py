#!/usr/bin/env python3
"""Төлбөрийн оношилгоо — «төлсөн ч хаалт нээгдээгүй / e-Barimt гараагүй» тохиолдолд.

Юу болсныг бүрэн харуулна: төлбөрийн төлөв, QPay-ийн БОДИТ хариу, e-Barimt-ын
алдаа, хаалтны команд. Шаардвал гацсан төлбөрийг ДУУСГАЖ хаалтыг нээнэ.

Ажиллуулах (production сервер дээр):
    # Сүүлийн 10 төлбөр
    sudo /root/PARKING/backend/venv/bin/python /root/PARKING/tools/payment_diag.py

    # Тухайн машины төлбөрүүд
    sudo .../payment_diag.py --plate 1234УБА

    # Нэг төлбөрийг QPay-ээс шалгах (мөнгө ирсэн эсэхийг QPay-ээс асууна)
    sudo .../payment_diag.py --id <payment_id>

    # QPay «төлөгдсөн» гэвэл дуусгаж, ХААЛТЫГ НЭЭНЭ + e-Barimt үүсгэнэ
    sudo .../payment_diag.py --id <payment_id> --finalize

Аюулгүй: --finalize нь QPay БОДИТООР «төлөгдсөн» гэж хариулсан үед л ажиллана.
"""
import argparse
import asyncio
import os
import sys
import warnings

warnings.filterwarnings("ignore", category=DeprecationWarning)
from datetime import datetime, timedelta

BACKEND = "/root/PARKING/backend"
sys.path.insert(0, BACKEND)
os.chdir(BACKEND)

from app.database import SessionLocal  # noqa: E402
from app.models import BarrierCommand, ParkingSession, Payment, VatReceipt  # noqa: E402
from app.services import qpay  # noqa: E402


def ts(d):
    return d.strftime("%m-%d %H:%M:%S") if d else "—"


def show_payment(db, p: Payment, full: bool = False):
    s = db.get(ParkingSession, p.session_id) if p.session_id else None
    site = s.site if s else None
    mark = {"PAID": "✓", "PENDING": "…", "REVIEW": "!", "FAILED": "✗"}.get(p.status, "?")
    print(f"\n{mark} {p.id}")
    print(f"    машин: {s.plate_number if s else '—':10} зогсоол: {site.name if site else '—'}")
    print(f"    {p.provider}/{p.payment_method}  {float(p.amount):,.0f}₮  "
          f"төлөв: {p.status}  үүссэн: {ts(p.created_at)}  төлсөн: {ts(p.paid_at)}")
    print(f"    invoice_id: {p.provider_invoice_id or '—'}")
    if p.provider == "QPAY":
        print(f"    g_payment_id: {p.provider_payment_id or '— ХООСОН: QPay e-Barimt үүсэхгүй'}")
    print(f"    гүйлгээний утга: {p.sender_invoice_no}")

    rec = db.query(VatReceipt).filter(VatReceipt.payment_id == p.id).all()
    if rec:
        for r in rec:
            state = {"SENT": "✓", "FAILED": "✗"}.get(r.status, "…")
            print(f"    e-Barimt {state} {r.status}  ДДТД: {r.ebarimt_id or '—'}  "
                  f"сугалаа: {r.lottery_code or '—'}")
            if r.receipt_url:
                print(f"      алдаа: {r.receipt_url}")
    else:
        print("    e-Barimt: бичлэг АЛГА (төлбөр дуусгагдаагүй гэсэн үг)")

    if s:
        print(f"    session төлөв: {s.status}  орсон: {ts(s.entry_time)}  гарсан: {ts(s.exit_time)}")
        cmds = (db.query(BarrierCommand).filter(BarrierCommand.session_id == s.id)
                .order_by(BarrierCommand.created_at.desc()).limit(5).all())
        if cmds:
            for c in cmds:
                print(f"    хаалт: {c.command} {c.status} {ts(c.created_at)} "
                      f"— {(c.response_text or '')[:90]}")
        else:
            print("    хаалт: команд ОГТ илгээгдээгүй")
    if full and p.raw_payload:
        print(f"    raw: {str(p.raw_payload)[:400]}")


async def ask_qpay(db, p: Payment, finalize: bool, accept_less: bool = False) -> int:
    """QPay-ээс төлбөрийг шалгаад, шаардвал дуусгана."""
    s = db.get(ParkingSession, p.session_id) if p.session_id else None
    site = s.site if s else None
    acc = qpay.account_for(site)
    print(f"\n─── QPay-ээс шалгаж байна ───")
    print(f"    мерчант: {acc.username}  ({'БОДИТ' if not acc.mock else 'MOCK горим'})")
    if not p.provider_invoice_id:
        print("    invoice_id алга — QPay-д нэхэмжлэл огт үүсээгүй байна.")
        return 1
    try:
        res = await qpay.check_payment(p.provider_invoice_id, acc=acc)
    except Exception as e:  # noqa: BLE001
        print(f"    ✗ QPay руу хандаж чадсангүй: {type(e).__name__}: {e}")
        return 1

    rows = res.get("rows") or []
    print(f"    QPay: paid={res.get('paid')}  дүн={res.get('paid_amount')}  "
          f"мөр={res.get('count')}  g_payment_id={res.get('payment_id') or '—'}")
    for r in rows:
        print(f"      мөр: status={r.get('payment_status')} дүн={r.get('payment_amount')} "
              f"хэтэвч={r.get('payment_wallet')} төрөл={r.get('payment_type')}")
    if not rows:
        print("    → QPay дээр ГҮЙЛГЭЭ БҮРТГЭГДЭЭГҮЙ байна. Мөнгө яг энэ QR-аар")
        print("      төлөгдсөн эсэхийг банкны баримтаас шалгана уу (өөр QR байж болзошгүй).")

    paid_amount = float(res.get("paid_amount") or 0)
    expected = float(p.amount)
    diff = paid_amount - expected
    if abs(diff) > 1:
        print(f"    !! ДҮН ЗӨРЖ БАЙНА: нэхэмжлэл {expected:,.2f}₮ · "
              f"төлсөн {paid_amount:,.2f}₮ · зөрүү {diff:+,.2f}₮")
        if diff > 1:
            # Ихэвчлэн QPay мерчантын НӨАТ тохиргоо: нэхэмжлэлийн дүн дээр
            # татварыг НЭМЖ тооцсоноос үүсдэг (2000 → 2181.82 = 2000 + 2000/11)
            ratio = paid_amount / expected if expected else 0
            hint = " (≈ НӨАТ 10% дээр нь нэмэгдсэн)" if 1.08 < ratio < 1.10 else ""
            print(f"       Илүү төлөгдсөн{hint}. Жолоочийг гаргах нь зөв.")
        else:
            print("       ДУТУУ төлөгдсөн — гаргахын өмнө шалгана уу.")

    if not res.get("paid"):
        print("    → Төлбөр QPay дээр ороогүй тул дуусгах боломжгүй.")
        return 1
    if p.status == "PAID":
        print("    → Төлбөр аль хэдийн PAID. Хаалт нээгдээгүй бол доорх хаалтны")
        print("      командын түүх болон эгнээний тохиргоог шалгана уу.")
        return 0
    if not finalize:
        print("    → QPay ТӨЛӨГДСӨН гэж байна. Дуусгаж хаалт нээх бол --finalize нэмнэ.")
        return 0
    if diff < -1 and not accept_less:
        print("    → ДУТУУ төлөгдсөн тул автоматаар дуусгахгүй. Санаатай гаргах бол")
        print("      --accept-less нэмнэ (зөрүүг өр болгож бүртгэхгүй, шууд гаргана).")
        return 1

    from app.routers.payments_router import _finalize_paid
    if res.get("payment_id"):
        p.provider_payment_id = str(res["payment_id"])
    print("    → Дуусгаж байна (session PAID + хаалт нээх + e-Barimt)…")
    await _finalize_paid(db, p, raw=res.get("raw"))
    db.commit()
    print("    ✓ Дууслаа.")
    show_payment(db, p)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Төлбөрийн оношилгоо")
    ap.add_argument("--id", help="Тодорхой төлбөрийн id — QPay-ээс шалгана")
    ap.add_argument("--plate", help="Машины дугаараар шүүх")
    ap.add_argument("--limit", type=int, default=10, help="Хэдэн төлбөр харуулах (default 10)")
    ap.add_argument("--hours", type=int, default=24, help="Сүүлийн хэдэн цаг (default 24)")
    ap.add_argument("--pending", action="store_true", help="Зөвхөн дуусаагүй төлбөрүүд")
    ap.add_argument("--finalize", action="store_true",
                    help="QPay төлөгдсөн гэвэл дуусгаж ХААЛТЫГ НЭЭНЭ (--id-тай хамт)")
    ap.add_argument("--accept-less", action="store_true", dest="accept_less",
                    help="ДУТУУ төлсөн байсан ч дуусгах (санаатай шийдвэр)")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        if args.id:
            p = db.get(Payment, args.id.strip())
            if not p:
                print(f"АЛДАА: '{args.id}' төлбөр олдсонгүй", file=sys.stderr)
                return 1
            show_payment(db, p, full=True)
            return asyncio.run(ask_qpay(db, p, args.finalize, args.accept_less))

        q = db.query(Payment).filter(
            Payment.created_at >= datetime.utcnow() - timedelta(hours=args.hours))
        if args.pending:
            q = q.filter(Payment.status != "PAID")
        if args.plate:
            plate = args.plate.strip().upper().replace(" ", "").replace("-", "")
            sess_ids = [x[0] for x in db.query(ParkingSession.id)
                        .filter(ParkingSession.plate_number == plate).all()]
            if not sess_ids:
                print(f"'{plate}' дугаартай зогсолт олдсонгүй.")
                return 1
            q = q.filter(Payment.session_id.in_(sess_ids))
        rows = q.order_by(Payment.created_at.desc()).limit(args.limit).all()
        if not rows:
            print("Төлбөр олдсонгүй.")
            return 0
        print(f"Сүүлийн {len(rows)} төлбөр (сүүлийн {args.hours} цаг):")
        for p in rows:
            show_payment(db, p)
        stuck = [p for p in rows if p.status != "PAID"]
        if stuck:
            print(f"\n{len(stuck)} дуусаагүй төлбөр байна. Тус бүрийг QPay-ээс шалгах:")
            for p in stuck[:5]:
                print(f"  sudo /root/PARKING/backend/venv/bin/python "
                      f"/root/PARKING/tools/payment_diag.py --id {p.id}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
