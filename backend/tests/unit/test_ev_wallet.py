"""EV тооцоо + дансны цэвэр логикийн тестүүд (DB шаардлагагүй).

    cd backend && venv/bin/pytest tests/unit/test_ev_wallet.py -v
"""
import types
from datetime import datetime
from decimal import Decimal

import pytest

from app.services import ev_billing
from app.services import wallet as wallet_svc

D = Decimal


class FakeDb:
    def __init__(self):
        self.added = []

    def add(self, obj):
        self.added.append(obj)


def make_wallet(balance="10000", status="ACTIVE"):
    return types.SimpleNamespace(id="w1", plate_number="1234УБА",
                                 balance=D(balance), status=status)


# ── §2: Wh → ₮ бүхэл тооны тооцоо ─────────────────────────────────────────

def test_wh_limit_basic():
    # 20,000₮ / 1₮ = 20,000 Wh
    assert ev_billing.wh_limit_for(20000, D("1")) == 20000


def test_wh_limit_fractional_price():
    # 10,000₮ / 1.2₮ = 8333 Wh (бүхэл рүү ТАСЛАНА — илүү өгөхгүй)
    assert ev_billing.wh_limit_for(10000, D("1.2")) == 8333


def test_energy_amount():
    assert ev_billing.energy_amount_for(12400, D("1")) == D("12400.00")
    assert ev_billing.energy_amount_for(8333, D("1.2")) == D("9999.60")


def test_night_price_window():
    plan = types.SimpleNamespace(price_per_wh="1", night_price_per_wh="0.7",
                                 night_from="22:00", night_to="07:00")
    # UTC 15:00 = УБ 23:00 → шөнө
    night = datetime(2026, 8, 27, 15, 0)
    assert ev_billing.price_per_wh_at(plan, night) == D("0.7")
    # UTC 04:00 = УБ 12:00 → өдөр
    day = datetime(2026, 8, 27, 4, 0)
    assert ev_billing.price_per_wh_at(plan, day) == D("1")


def test_night_price_disabled():
    plan = types.SimpleNamespace(price_per_wh="1", night_price_per_wh=None,
                                 night_from=None, night_to=None)
    assert ev_billing.price_per_wh_at(plan, datetime(2026, 8, 27, 15, 0)) == D("1")


# ── §1: ledger append-only, үлдэгдлийн хамгаалалт ─────────────────────────

def test_ledger_credit_debit():
    db, w = FakeDb(), make_wallet("0")
    wallet_svc.apply_ledger(db, w, "CREDIT", 20000, "TOPUP", ref_id="p1")
    assert w.balance == D("20000.00")
    wallet_svc.apply_ledger(db, w, "DEBIT", 5000, "PARKING", ref_id="s1")
    assert w.balance == D("15000.00")
    assert len(db.added) == 2
    assert db.added[0].balance_after == D("20000.00")
    assert db.added[1].balance_after == D("15000.00")


def test_ledger_no_negative_balance():
    db, w = FakeDb(), make_wallet("1000")
    with pytest.raises(wallet_svc.InsufficientBalance):
        wallet_svc.apply_ledger(db, w, "DEBIT", 1001, "PARKING")
    assert w.balance == D("1000")   # өөрчлөгдөөгүй
    assert db.added == []           # ledger мөр ч үүсээгүй


def test_ledger_rejects_bad_input():
    db, w = FakeDb(), make_wallet()
    with pytest.raises(wallet_svc.WalletError):
        wallet_svc.apply_ledger(db, w, "SIDEWAYS", 100, "TOPUP")
    with pytest.raises(wallet_svc.WalletError):
        wallet_svc.apply_ledger(db, w, "CREDIT", 100, "BAD_KIND")
    with pytest.raises(wallet_svc.WalletError):
        wallet_svc.apply_ledger(db, w, "CREDIT", 0, "TOPUP")
    with pytest.raises(wallet_svc.WalletError):
        wallet_svc.apply_ledger(db, w, "CREDIT", -5, "TOPUP")


def test_ledger_blocked_wallet():
    db, w = FakeDb(), make_wallet(status="BLOCKED")
    with pytest.raises(wallet_svc.WalletError):
        wallet_svc.apply_ledger(db, w, "CREDIT", 100, "TOPUP")


# ── §1.3/§6.4: hold → бодит дүн → release зөрүү ───────────────────────────

def test_hold_release_cycle():
    db, w = FakeDb(), make_wallet("20000")
    wallet_svc.apply_ledger(db, w, "DEBIT", 20000, "CHARGE_HOLD", ref_id="cs1")
    assert w.balance == D("0.00")
    # Бодит 12,400₮ → 7,600₮ буцаана
    wallet_svc.apply_ledger(db, w, "CREDIT", 7600, "CHARGE_RELEASE", ref_id="cs1")
    assert w.balance == D("7600.00")


def test_settle_marker_does_not_move_money():
    db, w = FakeDb(), make_wallet("7600")
    wallet_svc.settle_charge_marker(db, w, "cs1", 12400)
    assert w.balance == D("7600")
    assert len(db.added) == 1
    assert db.added[0].kind == "CHARGE_SETTLE"
    assert db.added[0].balance_after == D("7600")


def test_settle_zero_skipped():
    db, w = FakeDb(), make_wallet()
    wallet_svc.settle_charge_marker(db, w, "cs1", 0)
    assert db.added == []


# ── Утас/дугаар цэвэрлэгээ ────────────────────────────────────────────────

def test_normalize_phone():
    assert wallet_svc.normalize_phone("+976 9911-2233") == "97699112233"
    assert wallet_svc.normalize_phone("") == ""
