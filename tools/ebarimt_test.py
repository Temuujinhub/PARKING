#!/usr/bin/env python3
"""QPay-ЭС ӨӨР төлбөр (бэлэн/карт) дээр e-Barimt үүсэж чадаж байгааг шалгах.

Хоёр тусдаа суваг байдаг:
  1. QR/QPay төлбөр → QPay-ийн ebarimt_v3 API → 33 оронтой ДДТД, «TN …» сугалаа
     (БОДИТ, одоо ажиллаж байгаа)
  2. Бэлэн/картын төлбөр → ЛОКАЛ PosAPI (ebarimt_posapi_url) → PARKING_EBARIMT_MOCK
     утгаас хамаарна. MOCK үед 40 оронтой САНАМСАРГҮЙ тоо буцдаг — татварын
     системд БҮРТГЭГДЭХГҮЙ хуурамч баримт.

Энэ хэрэгсэл нь 2-р сувгийг шалгана: PosAPI хүрч байна уу, бодит баримт үүсэж
байна уу гэдгийг 10₮-ийн ЖИЖИГ туршилтаар (хувь хүн) тогтооно.

    sudo /root/PARKING/backend/venv/bin/python /root/PARKING/tools/ebarimt_test.py
    # ААН-аар (ТТД-тэй):
    sudo ... ebarimt_test.py --tin 1234567
    # өөр дүн/картаар:
    sudo ... ebarimt_test.py --amount 100 --method CARD

Тайлбар: MOCK горимд байвал баримт «үүссэн» мэт харагдана — гаралт үүнийг
тодорхой хэлнэ. Бодит баримт авахын тулд серверт eBarimt PosAPI суулгаж
.env-д PARKING_EBARIMT_MOCK=false болгоно.
"""
import argparse
import asyncio
import json
import os
import sys

os.chdir("/root/PARKING/backend")
sys.path.insert(0, "/root/PARKING/backend")

import httpx  # noqa: E402
from app.config import settings  # noqa: E402
from app.services import ebarimt  # noqa: E402


async def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--amount", type=float, default=10, help="Дүн (default 10₮)")
    ap.add_argument("--method", default="CASH", choices=["CASH", "CARD"])
    ap.add_argument("--tin", default=None, help="ААН-ын ТТД (өгөхгүй бол хувь хүн)")
    args = ap.parse_args()

    print("=== e-Barimt (QPay-ээс өөр суваг) шалгалт ===")
    print(f"Горим      : {'MOCK (ХУУРАМЧ)' if settings.ebarimt_mock else 'БОДИТ'}")
    print(f"PosAPI URL : {settings.ebarimt_posapi_url}")
    print(f"Худалдагч  : ТТД {settings.ebarimt_merchant_tin or '(тохируулаагүй)'}")
    print(f"Туршилт    : {args.amount:g}₮ · {args.method} · "
          f"{'ААН ' + args.tin if args.tin else 'хувь хүн'}\n")

    # 1. PosAPI сүлжээгээр хүрч байгаа эсэх (mock үед ч бодит байдлыг мэдэхийн тулд)
    print("--- 1. PosAPI хүрэлцээ ---")
    base = settings.ebarimt_posapi_url.rstrip("/")
    reachable = False
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(f"{base}/info")
        reachable = r.status_code < 500
        print(f"  GET {base}/info → HTTP {r.status_code}")
        print(f"  {r.text[:300]}")
    except Exception as e:  # noqa: BLE001
        print(f"  ХҮРЭХГҮЙ: {type(e).__name__}: {str(e)[:120]}")
        print("  → eBarimt PosAPI сервис ажиллаагүй байна (docker/systemd шалгана уу)")

    # 2. Баримт үүсгэх оролдлого
    print("\n--- 2. Баримт үүсгэх ---")
    vat = round(args.amount * settings.vat_rate / (1 + settings.vat_rate))
    try:
        receipt = await ebarimt.create_receipt(args.amount, vat, args.method,
                                               customer_tin=args.tin)
        print(f"  {json.dumps(receipt, ensure_ascii=False, indent=2)[:900]}")
        bill = str(receipt.get("billId") or "")
        lottery = str(receipt.get("lottery") or "")
        print(f"\n  ДДТД урт: {len(bill)} тэмдэгт · сугалаа: {lottery!r}")
        if receipt.get("mock"):
            print("  ⚠ ЭНЭ БОЛ ХУУРАМЧ (MOCK) БАРИМТ — татварын системд бүртгэгдээгүй.")
            print("    Бэлэн/картаар төлсөн үйлчлүүлэгч жинхэнэ баримт аваагүй байна.")
        elif len(bill) == 33:
            print("  ✅ Бодит баримт (33 оронтой ДДТД) — татварын системд бүртгэгдлээ.")
        else:
            print("  ? Хүлээгдэж буй 33 оронтой ДДТД-ээс өөр урттай — PosAPI хариуг шалгана уу.")
    except Exception as e:  # noqa: BLE001
        print(f"  АЛДАА: {type(e).__name__}: {str(e)[:200]}")
        reachable = False

    # 3. Дүгнэлт + дараагийн алхам
    print("\n--- 3. Дүгнэлт ---")
    if settings.ebarimt_mock:
        print("  Бэлэн/картын баримт ОДООГООР ХУУРАМЧ. Бодит болгохын тулд:")
        print("    1) Серверт eBarimt PosAPI сервисийг суулгаж ажиллуулах")
        print(f"       (одоогийн хаяг: {settings.ebarimt_posapi_url})")
        print("    2) Татварын албанд POS бүртгүүлж merchant ТТД тохируулах")
        print("       .env: PARKING_EBARIMT_MERCHANT_TIN=<ТТД>")
        print("    3) .env: PARKING_EBARIMT_MOCK=false → backend restart")
        print("    4) Энэ хэрэгслийг дахин ажиллуулж 33 оронтой ДДТД гарахыг шалгах")
    elif reachable:
        print("  ✅ Бэлэн/картын баримт бодитоор үүсэж байна.")
    else:
        print("  ⚠ MOCK унтраалттай ХЭРНЭЭ PosAPI хүрэхгүй байна — бэлэн/картаар")
        print("    төлсөн үед баримт үүсэхгүй (төлбөр нь хэвийн бүртгэгдэнэ).")


if __name__ == "__main__":
    asyncio.run(main())
