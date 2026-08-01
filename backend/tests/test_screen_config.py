"""LED дэлгэцийн мөрийн тохиргоо (Тохиргоо → LED дэлгэц) — standalone тест.

    cd backend && venv/bin/python tests/test_screen_config.py

DB хэрэггүй: _screen_text_from_lines/_payment_label/_bye_screen_text-ийг fake
объектоор, _check_screen_config-ийг шууд шалгана.
"""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.session_logic import _screen_text_from_lines, _bye_screen_text  # noqa: E402
from app.routers.admin_router import _check_screen_config  # noqa: E402
from fastapi import HTTPException  # noqa: E402

PASS, FAIL = 0, 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  <<< FAIL")


# ── _screen_text_from_lines ──
print("_screen_text_from_lines:")
t = _screen_text_from_lines(
    [{"type": "time"}, {"type": "plate"}, {"type": "text", "text": "Тавтай морил"}],
    plate="1234УБА", time_str="14:05")
check("цаг+дугаар+текст", t == "14:05\n1234УБА\nТавтай морил")

t = _screen_text_from_lines(
    [{"type": "plate"}, {"type": "payment"}, {"type": "reason"}, {"type": "text", "text": "Баяртай"}],
    plate="1234УБА", payment="QPay", reason="")
check("төлбөртэй гарах: {reason} хоосон мөр хасагдана", t == "1234УБА\nQPay\nБаяртай")

t = _screen_text_from_lines(
    [{"type": "plate"}, {"type": "payment"}, {"type": "reason"}],
    plate="1234УБА", payment="", reason="Гэрээт")
check("үнэгүй гарах: {payment} хасагдаж {reason} гарна", t == "1234УБА\nГэрээт")

t = _screen_text_from_lines([{"type": "amount"}], amount=3000)
check("дүн: 3000T", t == "3000T")
t = _screen_text_from_lines([{"type": "amount"}, {"type": "plate"}], amount=None, plate="A")
check("amount=None үед дүнгийн мөр бүрэн хасагдана («T» үлдэхгүй)", t == "A")

t = _screen_text_from_lines([{"type": "duration"}], duration_minutes=125)
check("duration форматлагдана", "2" in t and t != "")

t = _screen_text_from_lines(
    [{"type": "time"}, {"type": "plate"}, {"type": "text", "text": "a"}, {"type": "text", "text": "b"},
     {"type": "text", "text": "ИЛҮҮ 5 ДАХЬ МӨР"}], plate="P", time_str="10:00")
check("4-өөс илүү мөр таслагдана", "ИЛҮҮ" not in t)

check("эвдэрхий мөр (dict биш) алгасна",
      _screen_text_from_lines([None, "x", {"type": "plate"}], plate="P") == "P")

# ── _bye_screen_text (fake db/session) ──
print("_bye_screen_text:")


class FakeQuery:
    def __init__(self, payment):
        self._p = payment

    def filter(self, *a):
        return self

    def order_by(self, *a):
        return self

    def first(self):
        return self._p


class FakeDb:
    """db.get → site, db.query(Payment) → payment."""

    def __init__(self, site=None, payment=None):
        self._site = site
        self._payment = payment

    def get(self, model, pk):
        return self._site

    def query(self, model):
        return FakeQuery(self._payment)


def mk_session(paid_at=None, registered=False):
    return SimpleNamespace(id="s1", site_id="site1", plate_number="1234УБА",
                           is_registered=registered, paid_at=paid_at, exit_time=None)


site_cfg = SimpleNamespace(screen_config={
    "exit": [{"type": "plate"}, {"type": "payment"}, {"type": "reason"},
             {"type": "text", "text": "Баяртай"}]})

# Төлбөртэй гарах: QR төлбөр → QPay мөр, reason хоосон
db = FakeDb(site=site_cfg, payment=SimpleNamespace(payment_method="QR"))
txt = _bye_screen_text(db, mk_session(paid_at="x"), {"is_free": False, "total_fee": 3000})
check("төлсөн (QR): QPay харагдана", "QPay" in txt and "Баяртай" in txt)
check("төлсөн үед шалтгааны мөр байхгүй", "Гэрээт" not in txt and "Үнэгүй" not in txt)

db = FakeDb(site=site_cfg, payment=SimpleNamespace(payment_method="CASH"))
txt = _bye_screen_text(db, mk_session(paid_at="x"), {"is_free": False, "total_fee": 500})
check("төлсөн (CASH): Бэлэн", "Бэлэн" in txt)

# Гэрээт машин: payment хоосон, reason=Гэрээт
db = FakeDb(site=site_cfg)
txt = _bye_screen_text(db, mk_session(registered=True),
                       {"is_free": True, "reason": "Бүртгэлтэй жолооч", "total_fee": 0})
check("гэрээт: Гэрээт харагдана, төлбөрийн мөргүй", "Гэрээт" in txt and "QPay" not in txt)

# Үнэгүй хугацаанд багтсан
txt = _bye_screen_text(db, mk_session(),
                       {"is_free": True, "reason": "Эхний 15 минут үнэгүй", "total_fee": 0})
check("үнэгүй: billing-ийн шалтгаан харагдана", "Эхний 15 минут үнэгүй" in txt)

# Тохиргоогүй зогсоол → хуучин глобал template (fallback)
db = FakeDb(site=SimpleNamespace(screen_config=None))
txt = _bye_screen_text(db, mk_session(paid_at="x"), {"is_free": False, "total_fee": 3000,
                                                     "duration_minutes": 60})
check("тохиргоогүй үед глобал template ажиллана", "1234УБА" in txt)

# ── _check_screen_config ──
print("_check_screen_config:")
check("None → None", _check_screen_config(None) is None)
check("{} → None", _check_screen_config({}) is None)
out = _check_screen_config({"entry": [{"type": "time"}, {"type": "text", "text": "  Сайн уу  "}]})
check("текст цэвэрлэгдэнэ", out["entry"][1] == {"type": "text", "text": "Сайн уу"})
out = _check_screen_config({"entry": [{"type": "none"}], "exit": [{"type": "payment"}]})
check("бүгд хоосон lane хасагдана", "entry" not in out and out["exit"][0]["type"] == "payment")
check("бүх lane хоосон → None",
      _check_screen_config({"entry": [{"type": "none"}]}) is None)
try:
    _check_screen_config({"entry": [{"type": "payment"}]})
    check("entry-д payment хориотой", False)
except HTTPException:
    check("entry-д payment хориотой", True)
try:
    _check_screen_config({"exit": [{"type": "x"}]})
    check("буруу төрөл 400", False)
except HTTPException:
    check("буруу төрөл 400", True)
try:
    _check_screen_config({"exit": [{"type": "none"}] * 5})
    check("5+ мөр 400", False)
except HTTPException:
    check("5+ мөр 400", True)
out = _check_screen_config({"exit": [{"type": "text", "text": "x" * 100}]})
check("текст 40 тэмдэгтээр таслагдана", len(out["exit"][0]["text"]) == 40)

print("=" * 40)
print(f"ҮР ДҮН: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
