"""QPay баримтын худалдан авагч — 7 оронтой ААН регистрийг ТТД болгож дамжуулна.

2026-09-06: 9280УБХ (Хангарьд) 6853959 регистртэй байгууллагын баримт QPay-д
«receipt.customerTin … [0-9]{11,14}» гэж унаад retry ч унасаар байв.

    cd backend && venv/bin/python -m pytest tests/unit/test_qpay_receiver_tin.py -q
"""
import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.routers import payments_router as pr  # noqa: E402
from app.services import tin_lookup  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


def test_citizen_receipt_has_no_receiver():
    p = SimpleNamespace(customer_tin="6853959")
    assert _run(pr._qpay_receiver(p, "CITIZEN")) is None


def test_tin_passed_through_unchanged(monkeypatch):
    called = []
    async def fake(reg):
        called.append(reg)
        return {}
    monkeypatch.setattr(tin_lookup, "lookup", fake)
    p = SimpleNamespace(customer_tin="30101065006")
    assert _run(pr._qpay_receiver(p, "COMPANY")) == "30101065006"
    assert called == []          # ТТД байхад хайлт хийхгүй


def test_register_resolved_to_tin(monkeypatch):
    async def fake(reg):
        assert reg == "6853959"
        return {"available": True, "found": True, "tin": "15200002090"}
    monkeypatch.setattr(tin_lookup, "lookup", fake)
    p = SimpleNamespace(customer_tin="6853959")
    assert _run(pr._qpay_receiver(p, "COMPANY")) == "15200002090"


def test_register_kept_when_lookup_unavailable(monkeypatch):
    """Хөрвүүлж чадахгүй бол регистрээ л өгнө — QPay тодорхой алдаа өгч retry боломжтой."""
    async def fake(reg):
        return {"available": False, "tin": None, "error": "суваггүй"}
    monkeypatch.setattr(tin_lookup, "lookup", fake)
    p = SimpleNamespace(customer_tin="6853959")
    assert _run(pr._qpay_receiver(p, "COMPANY")) == "6853959"


def test_lookup_exception_does_not_break(monkeypatch):
    async def fake(reg):
        raise RuntimeError("boom")
    monkeypatch.setattr(tin_lookup, "lookup", fake)
    p = SimpleNamespace(customer_tin="6853959")
    assert _run(pr._qpay_receiver(p, "COMPANY")) == "6853959"
