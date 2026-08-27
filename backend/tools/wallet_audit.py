"""wallet_audit — данс бүрийн balance ↔ ledger нийлбэрийг тулгана (§1).

    cd backend && venv/bin/python tools/wallet_audit.py [--fix]

debt_audit.py-ийн загвараар: өдөр бүр cron-оос ажиллуулж, зөрүүтэй данс
олдвол exit code 1 (мониторинг дохиолол өгнө). --fix нь balance-ыг ledger-ийн
нийлбэрээр ЗАСНА (зөвхөн шалтгааныг нь олсны дараа, гараар).

Ledger-ийн нийлбэрт CHARGE_SETTLE ОРОХГҮЙ — тэр нь мөнгө хөдөлгөдөггүй
бүртгэлийн тэмдэглэгээ (services/wallet.py-г үзнэ үү).
"""
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal  # noqa: E402
from app.models import Wallet  # noqa: E402
from app.services.wallet import ledger_sum  # noqa: E402


def main() -> int:
    fix = "--fix" in sys.argv
    db = SessionLocal()
    bad = 0
    try:
        wallets = db.query(Wallet).all()
        print(f"Нийт {len(wallets)} данс шалгаж байна...")
        for w in wallets:
            expect = ledger_sum(db, w.id)
            actual = Decimal(str(w.balance or 0))
            if expect != actual:
                bad += 1
                print(f"  ✗ {w.plate_number} ({w.id}): balance={actual}₮ "
                      f"ledger={expect}₮ зөрүү={actual - expect}₮")
                if fix:
                    w.balance = expect
                    db.commit()
                    print("    → засав (ledger-ийн нийлбэрээр)")
        if bad == 0:
            print("✓ Бүх данс тулав — зөрүүгүй")
        else:
            print(f"\n⚠ {bad} данс зөрүүтэй" + ("" if fix else " (--fix-ээр засна)"))
        return 1 if (bad and not fix) else 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
