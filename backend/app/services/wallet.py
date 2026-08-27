"""Данс (wallet) — үлдэгдлийн ЦОРЫН ГАНЦ өөрчлөгч (EV_CHARGING_PLAN.md §1).

Дүрэм:
  • Хөдөлгөөн бүр wallet_ledger-т мөр — append-only, устгах/засахгүй.
  • balance нь кэш; үнэн нь ledger-ийн нийлбэр (tools/wallet_audit.py тулгана).
  • Үлдэгдэл өөрчлөх БҮХ үйлдэл SELECT … FOR UPDATE мөрийн түгжээтэй, нэг
    транзакцид — цэнэглэж байхдаа хаалтаар гарах г.м зэрэгцээ хасалтаас
    хамгаална (§10).
  • DEBIT хэзээ ч үлдэгдлийг сөрөг болгохгүй (InsufficientBalance).

Хэрэглээ (нэг транзакц дотор):
    w = lock_wallet(db, wallet_id)
    apply_ledger(db, w, "DEBIT", amount, kind="PARKING", ...)
    ... бусад өөрчлөлт ...
    db.commit()
"""
import logging
from decimal import Decimal

from sqlalchemy.orm import Session

from ..models import Wallet, WalletLedger
from ..session_logic import normalize_plate

log = logging.getLogger("parking.wallet")

D = Decimal

KINDS = {"TOPUP", "CHARGE_HOLD", "CHARGE_RELEASE", "CHARGE_SETTLE",
         "PARKING", "CASH_OUT", "ADJUST"}


class WalletError(Exception):
    pass


class InsufficientBalance(WalletError):
    pass


def normalize_phone(phone: str) -> str:
    return "".join(ch for ch in (phone or "") if ch.isdigit())[:20]


def find_wallet(db: Session, tenant_id: str | None, plate: str) -> Wallet | None:
    p = normalize_plate(plate)
    return (db.query(Wallet)
            .filter(Wallet.tenant_id == tenant_id, Wallet.plate_number == p)
            .first())


def get_or_create(db: Session, tenant_id: str | None, plate: str,
                  phone: str = "", name: str = "") -> Wallet:
    """Данс олдохгүй бол үүсгэнэ (§6.1 алхам 3). flush хийнэ, commit ҮГҮЙ."""
    p = normalize_plate(plate)
    if not p:
        raise WalletError("Машины дугаар хоосон байна")
    w = find_wallet(db, tenant_id, p)
    if w:
        # Утас өөрчлөгдсөн бол шинэчилнэ (мэдэгдэл/сэргээлтэд л ашиглагдана §1.2)
        ph = normalize_phone(phone)
        if ph and ph != w.phone:
            w.phone = ph
        if name and not w.name:
            w.name = name
        return w
    w = Wallet(tenant_id=tenant_id, plate_number=p,
               phone=normalize_phone(phone), name=name or "")
    db.add(w)
    db.flush()
    return w


def lock_wallet(db: Session, wallet_id: str) -> Wallet:
    """Мөрийн түгжээтэй унших — үлдэгдэл өөрчлөхийн ӨМНӨ заавал."""
    w = (db.query(Wallet).filter(Wallet.id == wallet_id)
         .with_for_update().first())
    if not w:
        raise WalletError("Данс олдсонгүй")
    return w


def apply_ledger(db: Session, wallet: Wallet, direction: str, amount,
                 kind: str, ref_type: str | None = None, ref_id: str | None = None,
                 operator_id: str | None = None, note: str = "") -> WalletLedger:
    """Ledger мөр + balance шинэчлэл. wallet нь lock_wallet-оор түгжигдсэн
    байх ЁСТОЙ. commit ХИЙХГҮЙ — дуудагч нэг транзакцид багцална."""
    if direction not in ("CREDIT", "DEBIT"):
        raise WalletError(f"direction буруу: {direction}")
    if kind not in KINDS:
        raise WalletError(f"kind буруу: {kind}")
    amt = D(str(amount)).quantize(D("0.01"))
    if amt <= 0:
        raise WalletError(f"Дүн эерэг байх ёстой: {amt}")
    if wallet.status != "ACTIVE":
        raise WalletError("Данс идэвхгүй (BLOCKED)")
    bal = D(str(wallet.balance or 0))
    new_bal = bal + amt if direction == "CREDIT" else bal - amt
    if new_bal < 0:
        raise InsufficientBalance(
            f"Үлдэгдэл хүрэлцэхгүй: {bal}₮ < {amt}₮")
    wallet.balance = new_bal
    row = WalletLedger(
        wallet_id=wallet.id, direction=direction, amount=amt,
        balance_after=new_bal, kind=kind, ref_type=ref_type, ref_id=ref_id,
        operator_id=operator_id, note=note or "")
    db.add(row)
    log.info("wallet %s %s %s %s₮ → %s₮ (%s)", wallet.plate_number,
             direction, kind, amt, new_bal, ref_id or "")
    return row


# ── Түгээмэл үйлдлүүд (бүгд түгжээтэй, commit дуудагч талд) ────────────────

def credit_topup(db: Session, wallet_id: str, amount, payment_id: str,
                 note: str = "") -> Wallet:
    w = lock_wallet(db, wallet_id)
    apply_ledger(db, w, "CREDIT", amount, "TOPUP",
                 ref_type="payment", ref_id=payment_id, note=note)
    return w


def hold_for_charge(db: Session, wallet_id: str, amount, session_id: str) -> Wallet:
    """Цэнэглэлт эхлэхэд зөвшөөрөгдсөн БҮТЭН дүнг түгжинэ (§1.3)."""
    w = lock_wallet(db, wallet_id)
    apply_ledger(db, w, "DEBIT", amount, "CHARGE_HOLD",
                 ref_type="charge_session", ref_id=session_id)
    return w


def release_hold(db: Session, wallet_id: str, amount, session_id: str,
                 note: str = "") -> Wallet:
    """Hold-ын зарцуулагдаагүй хэсгийг буцаана (§1.3, §6.4)."""
    w = lock_wallet(db, wallet_id)
    apply_ledger(db, w, "CREDIT", amount, "CHARGE_RELEASE",
                 ref_type="charge_session", ref_id=session_id, note=note)
    return w


def settle_charge_marker(db: Session, wallet: Wallet, session_id: str,
                         actual_amount) -> None:
    """CHARGE_SETTLE — мөнгө ХӨДӨЛГӨХГҮЙ бүртгэлийн тэмдэглэгээ (§6.1 алхам 8):
    hold аль хэдийн бүтэн дүнг хассан тул settle нь зөвхөн «бодит зарцуулалт
    N₮ болов» гэдгийг ledger-т ил болгоно. 0₮ дүнтэй settle бичихгүй."""
    amt = D(str(actual_amount)).quantize(D("0.01"))
    if amt <= 0:
        return
    db.add(WalletLedger(
        wallet_id=wallet.id, direction="DEBIT", amount=amt,
        balance_after=D(str(wallet.balance or 0)),  # үлдэгдэл өөрчлөгдөөгүй
        kind="CHARGE_SETTLE", ref_type="charge_session", ref_id=session_id,
        note="бодит зарцуулалт (hold-оос)"))


def debit_parking(db: Session, wallet_id: str, amount, parking_session_id: str,
                  note: str = "") -> Wallet:
    """Гарах хаалтан дээрх автомат хасалт (§6.2)."""
    w = lock_wallet(db, wallet_id)
    apply_ledger(db, w, "DEBIT", amount, "PARKING",
                 ref_type="parking_session", ref_id=parking_session_id, note=note)
    return w


def cash_out(db: Session, wallet_id: str, amount, operator_id: str,
             note: str = "") -> Wallet:
    """Бэлнээр буцаах — зөвхөн оператор баталгаажуулалттай, audit log-той (§1.2)."""
    w = lock_wallet(db, wallet_id)
    apply_ledger(db, w, "DEBIT", amount, "CASH_OUT",
                 operator_id=operator_id, note=note)
    return w


def adjust(db: Session, wallet_id: str, direction: str, amount,
           operator_id: str, note: str) -> Wallet:
    """Гар засвар — ЗААВАЛ тайлбартай (audit log дуудагч талд)."""
    if not (note or "").strip():
        raise WalletError("Гар засварт тайлбар заавал")
    w = lock_wallet(db, wallet_id)
    apply_ledger(db, w, direction, amount, "ADJUST",
                 operator_id=operator_id, note=note)
    return w


def ledger_sum(db: Session, wallet_id: str) -> Decimal:
    """Ledger-ийн нийлбэр (CREDIT − DEBIT, CHARGE_SETTLE-ийг тооцохгүй —
    тэр нь мөнгө хөдөлгөдөггүй тэмдэглэгээ). Аудитад ашиглана."""
    total = D(0)
    rows = (db.query(WalletLedger.direction, WalletLedger.kind, WalletLedger.amount)
            .filter(WalletLedger.wallet_id == wallet_id).all())
    for direction, kind, amount in rows:
        if kind == "CHARGE_SETTLE":
            continue
        total += D(str(amount)) if direction == "CREDIT" else -D(str(amount))
    return total
