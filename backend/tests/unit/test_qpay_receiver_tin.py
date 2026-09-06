"""QPay баримтын худалдан авагч — 7 оронтой ААН регистрийг ХЭВЭЭР дамжуулна.

2026-09-06 прод: QPay `ebarimt_receiver` 7-оос урт бол «String max length (7)!» гэж
татгалздаг (ТТД-г QPay өөрөө олдог). Мөн QPay 400-ийн `message` dict байхад
receipt_url-д бичигдэж psycopg2 «can't adapt type 'dict'» → PendingRollbackError.

    cd backend && venv/bin/python -m pytest tests/unit/test_qpay_receiver_tin.py -q
"""
import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.routers import payments_router as pr  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


def test_citizen_receipt_has_no_receiver():
    assert _run(pr._qpay_receiver(SimpleNamespace(customer_tin="6853959"), "CITIZEN")) is None


def test_register_passed_through_unchanged():
    """7 оронтой регистр ЯГ хэвээрээ — ТТД болгож урт болгохгүй (QPay MAX_LENGTH 7)."""
    assert _run(pr._qpay_receiver(SimpleNamespace(customer_tin="6853959"), "COMPANY")) == "6853959"
    assert _run(pr._qpay_receiver(SimpleNamespace(customer_tin=" 685-3959 "), "COMPANY")) == "6853959"


def test_empty_tin_gives_none():
    assert _run(pr._qpay_receiver(SimpleNamespace(customer_tin=None), "COMPANY")) is None
    assert _run(pr._qpay_receiver(SimpleNamespace(customer_tin="  "), "COMPANY")) is None


def test_err_text_dict_becomes_string():
    d = {"ebarimt_receiver": {"type": "MAX_LENGTH", "message": "String max length (7)!"}}
    out = pr._err_text(d)
    assert isinstance(out, str) and "MAX_LENGTH" in out and "String max length (7)!" in out
    assert pr._err_text(None) == ""
    assert pr._err_text("x" * 1000) == "x" * 400
