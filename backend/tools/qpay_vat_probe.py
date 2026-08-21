"""QPay «VAT_AMOUNT_INVALID» — ЯГ ЮУГ татгалзаж байгааг олох оношилгоо.

QPay нь НӨАТ-ын дүн буруу гэж 400 буцаахдаа АЛЬ МӨР, ЯМАР УТГА болохыг
хэлдэггүй. Энэ хэрэгсэл тухайн зогсоол/машины нэхэмжлэлийг бүтээж:

  1. ЯМАР дүнгээр, ХЭДЭН мөртэй, ямар НӨАТ илгээхийг ил харуулна,
  2. `--try` өгвөл НӨАТ бодох 4 өөр аргаар туршилтын нэхэмжлэл ҮҮСГЭЖ
     үзээд QPay АЛИЙГ нь хүлээж авахыг тогтооно.

Туршилтын нэхэмжлэл нь ЗӨВХӨН нэхэмжлэл — хэн нэгэн QR-ийг уншуулж
төлөхгүй бол мөнгө хөдлөхгүй, зогсоолын бүртгэл (session/payment) ч үүсэхгүй.

Ажиллуулах (production сервер дээр, backend хавтаст):

    venv/bin/python tools/qpay_vat_probe.py --site HANGARD
    venv/bin/python tools/qpay_vat_probe.py --plate 6068УБХ
    venv/bin/python tools/qpay_vat_probe.py --plate 6068УБХ --try
    venv/bin/python tools/qpay_vat_probe.py --site HANGARD --amount 11000 --try
"""
import argparse
import asyncio
import json
import math
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import Compensation, ParkingSession, ParkingSite  # noqa: E402
from app.services import qpay  # noqa: E402

ACTIVE = ("OPEN", "AWAITING_PAYMENT", "PAID")


def money(v):
    return f"{float(v or 0):,.2f}"


# ─────────────────── НӨАТ бодох 4 хувилбар ───────────────────
def vat_variants(totals: list[float]) -> dict[str, list[float]]:
    """Мөр бүрийн НӨАТ-ыг 4 өөр аргаар. QPay аль нь болохыг баримтжуулаагүй тул
    туршилтаар тогтооно."""
    r = settings.vat_rate

    def units(x):
        return math.floor(x * r / (1 + r) * 10000)

    # 1) ОДООГИЙН: 1/10000 нэгжээр таслаад, нийт дүнгээс гарсан утгад таарган
    #    үлдэгдлийг ХАМГИЙН ТОМ мөрд өгнө
    cur = [units(t) for t in totals]
    drift = units(sum(totals)) - sum(cur)
    if drift and cur:
        cur[max(range(len(totals)), key=lambda i: totals[i])] += drift
    current = [u / 10000 for u in cur]

    # Мөр бүр 2 орон хүртэл — QPay-ийн хүлээж авдаг хэлбэр (2026-08-21 туршилт)
    r2 = [round(t * r / (1 + r), 2) for t in totals]
    # Нийлбэр нь НИЙТ дүнгээс гарсан НӨАТ-тай таарахгүй байж болно (0.01₮) —
    # зөрүүг хамгийн том мөрд өгч тэнцүүлсэн хувилбар
    r2_bal = list(r2)
    diff = round(round(sum(totals) * r / (1 + r), 2) - sum(r2_bal), 2)
    if diff and r2_bal:
        i = max(range(len(totals)), key=lambda k: totals[k])
        r2_bal[i] = round(r2_bal[i] + diff, 2)

    # ЗАСВАРЫН нэр дэвшигч: Decimal-аар (float-ын алдаагүй) 4 орноор тасална
    from decimal import ROUND_DOWN, Decimal
    dr = Decimal(str(r))

    def dunits(x):
        return int((Decimal(str(x)) * dr / (1 + dr) * 10000)
                   .to_integral_value(rounding=ROUND_DOWN))

    dec = [dunits(t) for t in totals]
    dec_bal = list(dec)
    ddrift = dunits(sum(totals)) - sum(dec_bal)
    if ddrift and dec_bal:
        dec_bal[max(range(len(totals)), key=lambda i: totals[i])] += ddrift

    return {
        "decimal_trunc (Decimal, мөр бүр тусад нь)": [u / 10000 for u in dec],
        "decimal_trunc + нийлбэрт тэнцүүлсэн": [u / 10000 for u in dec_bal],
        "current (нийт дүнд таарган хуваарилсан)": current,
        "per_line_trunc (мөр бүр 4 оронгоор ТАСАЛСАН)": [units(t) / 10000 for t in totals],
        "per_line_round4 (мөр бүр 4 оронгоор БӨӨРӨНХИЙЛСӨН)":
            [round(t * r / (1 + r), 4) for t in totals],
        "per_line_round2 (мөр бүр 2 орон хүртэл)": r2,
        "per_line_round2 + нийлбэрт тэнцүүлсэн": r2_bal,
        "no_tax (НӨАТ огт илгээхгүй)": [],
    }


def build(items, acc, vat_amounts):
    """`qpay.build_lines`-тай ижил бүтэц, гэхдээ НӨАТ-ыг ГАДНААС өгнө."""
    lines = []
    for idx, it in enumerate(items):
        price = round(float(it["unit_price"]), 2)
        line = {
            "tax_product_code": "",
            "line_description": it["description"][:255],
            "line_quantity": "1.00",
            "line_unit_price": f"{price:.2f}",
            "note": "",
            "classification_code": acc.classification_code,
        }
        if vat_amounts:
            line["taxes"] = [{"tax_code": "VAT", "description": "НӨАТ",
                              "amount": vat_amounts[idx], "note": "НӨАТ"}]
        lines.append(line)
    return lines


async def try_variant(acc, items, name, vat_amounts) -> str:
    lines = build(items, acc, vat_amounts)
    try:
        await qpay.create_invoice(
            f"PROBE-{uuid.uuid4().hex[:10].upper()}",
            "QPay НӨАТ оношилгоо (төлөх шаардлагагүй)",
            "probe", f"{settings.public_base_url}/api/payments/qpay/webhook",
            lines, acc=acc)
        return "✅ ХҮЛЭЭЖ АВЛАА"
    except httpx.HTTPStatusError as e:
        body = (e.response.text or "")[:200]
        return f"❌ HTTP {e.response.status_code} — {body}"
    except Exception as e:  # noqa: BLE001
        return f"❌ {type(e).__name__}: {e}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", help="зогсоолын код (ж: HANGARD)")
    ap.add_argument("--plate", help="машины дугаар — түүний БОДИТ нэхэмжлэлээр шалгана")
    ap.add_argument("--amount", type=float, help="--site-тай хамт: гараар өгсөн дүн")
    ap.add_argument("--lines", help="--site-тай хамт: ОЛОН мөрийн дүн таслалаар "
                                    "(ж: 1000,2000,5500) — өртэй жолоочийн нэхэмжлэлийг дуурайна")
    ap.add_argument("--try", dest="do_try", action="store_true",
                    help="QPay руу ҮНЭХЭЭР илгээж, аль хувилбарыг хүлээж авахыг тогтоох")
    a = ap.parse_args()

    db = SessionLocal()
    site = None
    items = []
    if a.plate:
        s = (db.query(ParkingSession)
             .filter(ParkingSession.plate_number == a.plate.strip().upper(),
                     ParkingSession.status.in_(ACTIVE))
             .order_by(ParkingSession.entry_time.desc()).first())
        if not s:
            print(f"«{a.plate}» — идэвхтэй бүртгэл олдсонгүй")
            return
        site = s.site
        from app.session_logic import session_fee_info
        fee = session_fee_info(db, s)
        items.append({"description": f"Зогсоолын үйлчилгээ — {s.plate_number}",
                      "unit_price": float(fee["total_fee"])})
        for c in (db.query(Compensation)
                  .filter(Compensation.plate_number == s.plate_number,
                          Compensation.status == "PENDING").all()):
            items.append({"description": f"Өмнөх өр ({c.created_at:%Y-%m-%d}) — {c.plate_number}",
                          "unit_price": float(c.amount)})
    else:
        if not a.site:
            print("--site эсвэл --plate өгнө үү")
            return
        site = (db.query(ParkingSite).filter(ParkingSite.site_code == a.site.upper()).first()
                or db.query(ParkingSite).filter(ParkingSite.name.ilike(f"%{a.site}%")).first())
        if not site:
            print(f"Зогсоол «{a.site}» олдсонгүй")
            return
        if a.lines:
            for n, v in enumerate(x for x in a.lines.split(",") if x.strip()):
                items.append({"description": f"Оношилгооны мөр {n + 1}",
                              "unit_price": float(v)})
        else:
            items.append({"description": "Зогсоолын үйлчилгээ (оношилгоо)",
                          "unit_price": float(a.amount or 11000)})

    acc = qpay.account_for(site)
    _ten = qpay._tenant_of(site)
    src = ("site" if (site.qpay_username or "").strip()
           else "tenant" if (_ten and (getattr(_ten, "qpay_username", None) or "").strip())
           else "global")

    print(f"══ {site.name} ({site.site_code}) — QPay нэхэмжлэлийн НӨАТ ══\n")
    print(f"   данс          {acc.username}   (эх сурвалж: {src})")
    print(f"   tax_type      {acc.tax_type}   "
          f"({'НӨАТ тооцно' if acc.tax_type == '1' else 'НӨАТ ТООЦОХГҮЙ'})")
    print(f"   invoice_code  {acc.invoice_code}")
    print(f"   branch/district {acc.branch_code} / {acc.district_code}")
    print(f"   mock          {acc.mock}")
    print(f"   .env vat_rate {settings.vat_rate}")

    totals = [round(float(i["unit_price"]), 2) for i in items]
    print(f"\n   Мөр {len(items)} ширхэг, нийт {money(sum(totals))}₮")
    for i, t in zip(items, totals):
        print(f"      {money(t):>12}₮  {i['description']}")

    variants = vat_variants(totals)
    print("\n   НӨАТ бодох хувилбарууд:")
    for name, vats in variants.items():
        tot = sum(vats) if vats else 0
        detail = ", ".join(money(v) for v in vats) if vats else "—"
        print(f"      {name:<44} нийлбэр {money(tot):>10}₮   [{detail}]")
    exact = sum(totals) * settings.vat_rate / (1 + settings.vat_rate)
    print(f"      {'нийт дүнгээс шууд (лавлагаа)':<44} {money(exact):>17}₮")

    if not a.do_try:
        print("\n   QPay руу илгээж шалгах бол --try нэмнэ үү "
              "(нэхэмжлэл л үүснэ, мөнгө хөдлөхгүй).")
        return
    if acc.mock:
        print("\n   ⚠ данс mock горимд — бодит шалгалт хийх боломжгүй")
        return

    print("\n   QPay руу илгээж байна…")
    for name, vats in variants.items():
        res = asyncio.run(try_variant(acc, items, name, vats))
        print(f"      {name:<44} {res}")
    print("\n   ✅ тэмдэгтэй хувилбар нь энэ мерчантын хүлээж авдаг арга — "
          "кодыг түүнд тааруулна.")


if __name__ == "__main__":
    main()
