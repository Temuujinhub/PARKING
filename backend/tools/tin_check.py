"""ТТД тулгалт — баримт ХЭНИЙ ТТД-ээр гарч байна вэ, ТЕГ-д тэр ТТД хэн бэ?

ДДТД (33 орон)-ийн эхний 12 орон = баримт гаргасан татвар төлөгчийн ТТД (0-оор
урдаа нөхсөн 11 орон). QPay-ийн баримтад энэ нь QPay-ийн (POS оператор) ТТД,
жинхэнэ худалдагч нь raw.merchant_register_no (түрээслэгчийн регистр). msgbill-ийн
баримтад ДДТД-ийн ТТД = тухайн API түлхүүрийн эзэн (жинхэнэ худалдагч).

2026-09-13 тулгалт (9-р сар): QPay бүгд 30101065006; msgbill 08-20…09-07 бүгд
29100244106, 09-08-аас EasyParking 15200020090, Моннис 71101242183 — өөрөөр хэлбэл
09-08-аас ӨМНӨ Моннисын msgbill баримт (153 ш) EasyParking-ийн түлхүүрээр гарсан.

Энэ хэрэгсэл: (1) түрээслэгч/зогсоол/суваг бүрээр ашиглагдсан ТТД + тоо,
(2) түрээслэгчийн бүртгэлтэй регистр, (3) ТЕГ-ээс (api.ebarimt.mn — ЗӨВХӨН
Монголын IP-ээс, тиймээс ПРОД дээр ажиллуулна) регистр → ТТД, ТТД → нэрийг татаж
тулгана, (4) зөрүүг ⚠ гэж тэмдэглэнэ.

    cd /root/PARKING/backend
    venv/bin/python tools/tin_check.py                 # сүүлийн 30 хоног
    venv/bin/python tools/tin_check.py --days 60
"""
import argparse
import asyncio
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import func  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models import ParkingSession, ParkingSite, Payment, Tenant, VatReceipt  # noqa: E402


async def _teg(reg_or_tin: dict) -> dict:
    """{'reg:7109505': ..., 'tin:30101065006': ...} → ТЕГ-ийн хариу (нэр/ТТД) эсвэл None."""
    import httpx
    out = {}
    async with httpx.AsyncClient(timeout=5) as c:
        for key in reg_or_tin:
            kind, val = key.split(":", 1)
            url = (f"https://api.ebarimt.mn/api/info/check/getTinInfo?regNo={val}" if kind == "reg"
                   else f"https://api.ebarimt.mn/api/info/check/getInfo?tin={val}")
            try:
                r = await c.get(url)
                out[key] = r.json().get("data") if r.status_code == 200 and r.text.strip() else None
            except Exception as e:  # noqa: BLE001
                out[key] = f"алдаа: {str(e)[:60]}"
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--no-teg", action="store_true", help="ТЕГ лавлагаа хийхгүй (гадаад IP-ээс)")
    a = ap.parse_args()
    import time as _t
    _t0 = _t.time()
    def _lap(msg):
        print(f"  [{_t.time() - _t0:5.1f}с] {msg}", file=sys.stderr)
    since = datetime.utcnow() - timedelta(days=a.days)
    db = SessionLocal()
    try:
        tenants = {t.id: t for t in db.query(Tenant).all()}
        rows = (db.query(ParkingSite.tenant_id, ParkingSite.name, VatReceipt.provider,
                         func.left(VatReceipt.ebarimt_id, 12), func.count(),
                         func.min(VatReceipt.created_at), func.max(VatReceipt.created_at))
                .select_from(VatReceipt)
                .join(Payment, Payment.id == VatReceipt.payment_id)
                .join(ParkingSession, ParkingSession.id == Payment.session_id)
                .join(ParkingSite, ParkingSite.id == ParkingSession.site_id)
                # Зөвхөн ТЕГ-ийн 33 оронтой ДДТД, сувагтай (MOCK/хуучин id-г хасна)
                .filter(VatReceipt.status == "SENT", VatReceipt.created_at >= since,
                        VatReceipt.ebarimt_id.isnot(None), VatReceipt.provider.isnot(None),
                        func.length(VatReceipt.ebarimt_id) == 33)
                .group_by(ParkingSite.tenant_id, ParkingSite.name, VatReceipt.provider,
                          func.left(VatReceipt.ebarimt_id, 12))
                .order_by(ParkingSite.tenant_id, ParkingSite.name).all())
        # QPay-ийн жинхэнэ худалдагч: raw.create.merchant_register_no
        qpay_merch = defaultdict(lambda: defaultdict(int))
        for tid, merch, n in (db.query(ParkingSite.tenant_id,
                                       VatReceipt.raw["create"]["merchant_register_no"].as_string(),
                                       func.count())
                              .select_from(VatReceipt)
                              .join(Payment, Payment.id == VatReceipt.payment_id)
                              .join(ParkingSession, ParkingSession.id == Payment.session_id)
                              .join(ParkingSite, ParkingSite.id == ParkingSession.site_id)
                              .filter(VatReceipt.status == "SENT", VatReceipt.provider == "QPAY",
                                      VatReceipt.created_at >= since)
                              .group_by(ParkingSite.tenant_id,
                                        VatReceipt.raw["create"]["merchant_register_no"].as_string()).all()):
            qpay_merch[tid][merch or "(raw хадгалаагүй)"] += n

        tins = set()
        by_tenant = defaultdict(list)
        for tid, site, prov, pre, n, t0, t1 in rows:
            # Зөвхөн 33 оронтой ТЕГ-ийн ДДТД (эхний 12 = 0+ТТД); mock/rcp_… id-г ангилна
            if pre and pre.isdigit() and len(pre) == 12:
                tin = pre.lstrip("0")
                tins.add(tin)
            else:
                tin = "(ТЕГ бус id)"
            by_tenant[tid].append((site, prov or "?", tin, n, t0, t1))
        lookups = {f"tin:{t}": None for t in tins if t}
        for t in tenants.values():
            if (t.register or "").strip():
                lookups[f"reg:{t.register.strip()}"] = None
        _lap(f"DB бэлэн, ТЕГ лавлагаа {len(lookups)} ш")
        teg = asyncio.run(_teg(lookups)) if (lookups and not a.no_teg) else {}
        _lap("ТЕГ дууслаа")
        available = any(v not in (None, "") and not str(v).startswith("алдаа") for v in teg.values())
        print(f"ТЕГ лавлагаа (api.ebarimt.mn): {'ажиллаж байна' if available else 'ХҮРЭХГҮЙ — Монголын IP-ээс ажиллуулна'}\n")

        def name_of(tin):
            v = teg.get(f"tin:{tin}")
            if isinstance(v, dict):
                return v.get("name") or str(v)[:60]
            return "?" if v in (None, "") else str(v)[:60]

        for tid, items in by_tenant.items():
            t = tenants.get(tid)
            reg = (t.register or "").strip() if t else ""
            reg_tin = teg.get(f"reg:{reg}") if reg else None
            print(f"═══ {t.name if t else '(түрээслэгчгүй)'}  регистр={reg or '—'}  "
                  f"ТЕГ-ийн ТТД={reg_tin if isinstance(reg_tin, str) else '?'}")
            if qpay_merch.get(tid):
                print("  QPay худалдагч (merchant_register_no): " +
                      ", ".join(f"{m}×{n}" for m, n in qpay_merch[tid].items()))
                for m in qpay_merch[tid]:
                    if reg and m not in ("(raw хадгалаагүй)",) and m != reg:
                        print(f"  ⚠ QPay баримтын худалдагч {m} ≠ түрээслэгчийн регистр {reg}")
            per_tin = defaultdict(int)
            for site, prov, tin, n, t0, t1 in items:
                per_tin[(prov, tin)] += n
            for (prov, tin), n in sorted(per_tin.items(), key=lambda x: (x[0][0] or "", x[0][1] or "")):
                flag = ""
                if prov == "MSGBILL" and isinstance(reg_tin, str) and reg_tin and tin != reg_tin:
                    flag = f"  ⚠ түрээслэгчийн ТТД ({reg_tin}) БИШ"
                print(f"  {prov:<8} ТТД {tin:<12} {n:>6} баримт  → {name_of(tin)}{flag}")
            print("  зогсоол/суваг/ТТД/хугацаа:")
            for site, prov, tin, n, t0, t1 in items:
                print(f"    {site:<20} {prov:<8} {tin:<12} {n:>6}  {t0:%m-%d}…{t1:%m-%d}")
            print()
        print("Тайлбар: QPay-ийн ДДТД-ийн ТТД нь QPay (POS оператор)-ийнх байж болно — жинхэнэ "
              "худалдагч нь merchant_register_no. msgbill-ийн ДДТД-ийн ТТД = түлхүүрийн эзэн.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
