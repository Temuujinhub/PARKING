"""QPay «QR үүсэхгүй байна» — НЭГ команд, бүрэн онош.

«Жолооч QR авч чадахгүй байна» гомдол ирмэгц ЭНЭ хэрэгслийг тухайн сервер дээр
ажиллуулна. Гурван асуултыг дараалуулан хаана:

  1. **Хэн унасан бэ?** — зогсоол бүр АЛЬ QPay мерчант дансаар ажилладаг, тэр
     данс нэвтэрч чадаж байна уу (бүх зогсоол юу, эсвэл нэг түрээслэгч юу).
  2. **Хэзээ, хэдэн удаа?** — `audit_logs`-ийн `QPAY_INVOICE_FAIL` бичлэгүүдийг
     цаг/зогсоол/шалтгаанаар нь тоолж харуулна.
  3. **Одоо ажиллаж байна уу?** — `--invoice` өгвөл бодит туршилтын нэхэмжлэл
     үүсгэж (мөнгө хөдлөхгүй, session/payment бүртгэл үүсэхгүй) баталгаажуулна.

Ажиллуулах (backend хавтаст):

    venv/bin/python tools/qpay_doctor.py
    venv/bin/python tools/qpay_doctor.py --days 7
    venv/bin/python tools/qpay_doctor.py --invoice          # бодит QR үүсгэж үзнэ
    venv/bin/python tools/qpay_doctor.py --invoice --amount 11000

АНХААР: шалгалт нь данс тус бүрээр ШИНЭЭР нэвтэрдэг. Энэ нь QPay талд тухайн
дансны хуучин токеныг хүчингүй болгож болзошгүй ч аюулгүй — 2026-08-28-наас
хойш дуудлага бүр 401 дээр өөрөө дахин нэвтэрдэг (`services/qpay.py:_api`).
"""
import argparse
import asyncio
import os
import sys
from collections import Counter
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import AuditLog, ParkingSite, Payment  # noqa: E402
from app.services import qpay  # noqa: E402

OK, BAD, WARN = "\033[32m✓\033[0m", "\033[31m✗\033[0m", "\033[33m!\033[0m"


def head(t):
    print(f"\n\033[1m{t}\033[0m\n" + "─" * 72)


async def check_accounts(db, do_invoice: bool, amount: float):
    head("1. Зогсоол бүрийн QPay данс")
    sites = (db.query(ParkingSite).filter(ParkingSite.is_active.is_(True))
             .order_by(ParkingSite.site_code).all())
    groups: dict[tuple, dict] = {}
    for site in sites:
        acc = qpay.account_for(site)
        g = groups.setdefault(acc.cache_key, {"acc": acc, "sites": []})
        g["sites"].append(site.site_code)
    if not groups:
        acc = qpay.global_account()
        groups[acc.cache_key] = {"acc": acc, "sites": ["(зогсоолгүй — глобал данс)"]}

    print(f"  base_url: {settings.qpay_base_url}   mock: {settings.qpay_mock}   "
          f"НӨАТ: {settings.vat_rate}")
    failed = []
    for g in groups.values():
        acc, codes = g["acc"], g["sites"]
        print(f"\n  Данс: {acc.username}   invoice_code: {acc.invoice_code}")
        print(f"  Зогсоол ({len(codes)}): {', '.join(codes)}")
        if acc.mock:
            print(f"  {WARN} mock горим — бодит QPay руу хандахгүй")
            continue
        try:
            await qpay._get_token(acc, force=True)
            print(f"  {OK} нэвтрэлт амжилттай")
        except httpx.HTTPStatusError as e:
            print(f"  {BAD} НЭВТРЭЛТ УНАВ: HTTP {e.response.status_code} "
                  f"{e.response.text[:200]}")
            failed.append((acc.username, codes))
            continue
        except Exception as e:  # noqa: BLE001
            print(f"  {BAD} НЭВТРЭЛТ УНАВ: {type(e).__name__}: {e}")
            failed.append((acc.username, codes))
            continue
        if not do_invoice:
            continue
        try:
            lines = qpay.build_lines(
                [{"description": "Оношилгооны туршилт", "unit_price": amount,
                  "quantity": 1}], acc)
            inv = await qpay.create_invoice(
                f"DOCTOR{datetime.utcnow():%y%m%d%H%M%S}", "QPay оношилгоо",
                "terminal_DOCTOR",
                f"{settings.public_base_url}/api/payments/qpay/webhook?doctor=1",
                lines, acc=acc)
            print(f"  {OK} {amount:,.0f}₮-ийн нэхэмжлэл үүслээ "
                  f"(invoice_id={inv['invoice_id']}, QR {len(inv['qr_text'])} тэмдэгт)")
        except httpx.HTTPStatusError as e:
            print(f"  {BAD} НЭХЭМЖЛЭЛ УНАВ: HTTP {e.response.status_code} "
                  f"{e.response.text[:300]}")
            failed.append((acc.username, codes))
        except Exception as e:  # noqa: BLE001
            print(f"  {BAD} НЭХЭМЖЛЭЛ УНАВ: {type(e).__name__}: {e}")
            failed.append((acc.username, codes))

    print()
    if not failed:
        print(f"  {OK} Бүх данс ажиллаж байна ({len(groups)} данс, {len(sites)} зогсоол)")
    else:
        print(f"  {BAD} {len(failed)}/{len(groups)} данс УНАСАН:")
        for user, codes in failed:
            print(f"      {user} → {', '.join(codes)}")
    return failed


def check_failures(db, days: int):
    head(f"2. Сүүлийн {days} хоногийн бүтэлгүй нэхэмжлэл (audit_logs)")
    since = datetime.utcnow() - timedelta(days=days)
    rows = (db.query(AuditLog)
            .filter(AuditLog.action == "QPAY_INVOICE_FAIL", AuditLog.created_at >= since)
            .order_by(AuditLog.created_at.desc()).all())
    if not rows:
        print("  Бүтэлгүйтэл бүртгэгдээгүй.")
        print("  (Тэмдэглэл: энэ бүртгэл 2026-08-28-нд нэмэгдсэн — түүнээс өмнөх")
        print("   алдаанууд зөвхөн journalctl-д байна:")
        print("     journalctl -u parking-backend --since '3 days ago' | grep 'QPay invoice БҮТЭЛГҮЙ')")
        return
    print(f"  Нийт {len(rows)} удаа\n")
    by_site = Counter()
    by_reason = Counter()
    by_day = Counter()
    for r in rows:
        d = r.detail or {}
        by_site[d.get("site") or "?"] += 1
        by_reason[f"{d.get('reason')}/{d.get('status') or d.get('error', '')[:40]}"] += 1
        by_day[r.created_at.strftime("%m-%d %H:00")] += 1
    for title, ctr in (("Зогсоолоор", by_site), ("Шалтгаанаар", by_reason),
                       ("Цагаар", by_day)):
        print(f"  {title}:")
        for k, n in ctr.most_common(10):
            print(f"    {n:5d}  {k}")
        print()
    print("  Сүүлийн 5:")
    for r in rows[:5]:
        d = r.detail or {}
        print(f"    {r.created_at:%m-%d %H:%M}  {d.get('site')}/{d.get('plate')}  "
              f"{d.get('reason')} {str(d.get('body') or d.get('error') or '')[:90]}")


def check_invoice_numbers(db):
    """Гүйлгээний дугаарын урт — QPay 45 тэмдэгтээс урт бол ТАТГАЛЗДАГ.

    2026-08-28-нд яг энэ шалтгаанаар «Их Монгол ресторан»-ы бүх жолооч QR-аар
    төлж чадахгүй байв. Одоо урт нь кодоор автоматаар богиносгогддог тул унахгүй,
    гэхдээ АЛЬ зогсоолын код тайрагдаж байгааг харуулна (санхүүгийн тулгалтад
    бүтэн код харагдвал эвтэйхэн — шинэ зогсоолд богино код өгөх нь дээр)."""
    from app.routers.payments_router import QPAY_INVOICE_NO_MAX, _invoice_no
    head(f"3. Гүйлгээний дугаарын урт (QPay хязгаар {QPAY_INVOICE_NO_MAX})")

    class _S:
        def __init__(self, code):
            self.site = type("X", (), {"site_code": code})()
            self.plate_number = "0128УНМ"  # ердийн 7 тэмдэгт, кирилл (2 байт/үсэг)

    rows = (db.query(ParkingSite).filter(ParkingSite.is_active.is_(True))
            .order_by(ParkingSite.site_code).all())
    trimmed = []
    for site in rows:
        no = _invoice_no(_S(site.site_code))
        if not no.startswith(site.site_code):
            trimmed.append((site.site_code, no))
    if not trimmed:
        print(f"  {OK} Бүх зогсоолын код бүтнээрээ багтаж байна ({len(rows)} зогсоол)")
        return
    print(f"  {WARN} {len(trimmed)} зогсоолын код богиносгогдоно "
          f"(төлбөр ажиллана, зөвхөн дугаар нь товчилно):")
    for code, no in trimmed:
        print(f"      {code}  →  {no}")


def check_pending(db):
    head("4. Гацсан QPay төлбөр (PENDING)")
    since = datetime.utcnow() - timedelta(days=1)
    n = (db.query(Payment)
         .filter(Payment.provider == "QPAY", Payment.status == "PENDING",
                 Payment.created_at >= since).count())
    print(f"  Сүүлийн 24 цагт PENDING: {n}")
    if n > 50:
        print(f"  {WARN} Хэт олон — qpay_recheck ажиллаж байгаа эсэхийг шалга: "
              f"journalctl -u parking-backend | grep qpay_recheck")


def main():
    ap = argparse.ArgumentParser(description="QPay оношилгоо")
    ap.add_argument("--days", type=int, default=3, help="алдааны түүхийн хугацаа")
    ap.add_argument("--invoice", action="store_true",
                    help="данс бүрээр БОДИТ туршилтын нэхэмжлэл үүсгэнэ")
    ap.add_argument("--amount", type=float, default=1000.0,
                    help="туршилтын дүн (11-т хуваагддаг дүнг шалгах: 11000)")
    a = ap.parse_args()
    db = SessionLocal()
    try:
        failed = asyncio.run(check_accounts(db, a.invoice, a.amount))
        check_failures(db, a.days)
        check_invoice_numbers(db)
        check_pending(db)
        head("Дүгнэлт")
        if failed:
            print(f"  {BAD} QPay данс(ууд) унасан байна — дээрх алдааны бичвэрийг үз.")
            print("     401/UNAUTHORIZED  → нэр/нууц үг эсвэл гэрээ (QPay-тэй холбогдоно уу)")
            print("     VAT_AMOUNT_INVALID → tools/qpay_vat_probe.py --site <КОД> --try")
            sys.exit(1)
        print(f"  {OK} QPay талаас саад алга.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
