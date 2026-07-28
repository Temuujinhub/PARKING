"""LED дэлгэцний текст рендер + qPay deeplink сонголтын логик (DB шаардлагагүй).

    cd backend && venv/bin/python tests/test_screen_deeplink.py

Шалгах зүйл:
  - render_screen_text: {amount}/{plate} орлуулалт, бүхэл тоо болгох
  - pick_qpay_deeplink: eBarimt/банкны линк дээр ҮСЭРДЭГГҮЙ, зөвхөн qPay wallet
  - schedule_display: event loop-гүй орчинд чимээгүй алгасна (тест орчин)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.barrier import render_screen_text, schedule_display
from app.services.qpay import pick_qpay_deeplink

PASS = FAIL = 0


def check(name, cond):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗ <<< FAIL'} {name}")


print("render_screen_text:")
check("дүн орлуулна", render_screen_text("Tulbur: {amount}", amount=2500) == "Tulbur: 2500")
check("float → бүхэл", render_screen_text("{amount}", amount=2500.0) == "2500")
check("дугаар орлуулна", render_screen_text("{plate} {amount}", amount=1000, plate="1234УБА") == "1234УБА 1000")
check("amount байхгүй үед хоосон", render_screen_text("X {amount}", amount=None) == "X")
check("plate байхгүй үед хоосон", render_screen_text("{plate} bye").strip() == "bye")

print("pick_qpay_deeplink:")
# QPay v2-ийн бодит urls хэлбэр: банк бүрийн link дотор qPay_QRcode параметр бий —
# өмнөх "qpay in link" шалгалт ЭХНИЙ л линкийг (жишээ нь eBarimt) сонгодог байсан.
urls = [
    {"name": "eBarimt", "description": "Таны төлбөрийн баримт", "link": "ebarimt://q?qPay_QRcode=ABC"},
    {"name": "Khan bank", "description": "Хаан банк", "link": "khanbank://q?qPay_QRcode=ABC"},
    {"name": "qPay wallet", "description": "qPay хэтэвч", "link": "qpaywallet://q?qPay_QRcode=ABC"},
]
check("eBarimt-ыг алгасаж qPay wallet сонгоно",
      pick_qpay_deeplink(urls) == "qpaywallet://q?qPay_QRcode=ABC")
check("qPay wallet байхгүй бол хоосон (авто үсрэлтгүй)",
      pick_qpay_deeplink(urls[:2]) == "")
check("нэрээр нь ч таана",
      pick_qpay_deeplink([{"name": "qPay хэтэвч", "link": "someapp://q?x=1"}]) == "someapp://q?x=1")
check("хоосон жагсаалт", pick_qpay_deeplink([]) == "")

print("schedule_display (event loop-гүй):")
try:
    schedule_display("10.0.113.11", "Tulbur: 2500")
    check("loop-гүй орчинд exception шидэхгүй", True)
except Exception:
    check("loop-гүй орчинд exception шидэхгүй", False)




# ─── Дэлгэцийн «нэг команд» горим (2026-07-28) ────────────────────────────────
# Камер «хэдэн секунд харуулах» талбар дэмждэг бол давталт хэрэггүй — RPC сесс
# богиносч хаалтны командтай мөргөлдөхгүй. Талбарын нэрийг screen_probe.py-ээр
# бодит камераас тогтоож .env-д бичнэ.
def _probe_screen_params(field: str, sec: int) -> dict:
    """set_screen ямар params илгээхийг (сүлжээгүйгээр) шалгана."""
    import asyncio as _a
    from app.config import settings as _s
    from app.services.barrier import DahuaRpc

    old = (_s.screen_hold_field, _s.screen_hold_sec)
    _s.screen_hold_field, _s.screen_hold_sec = field, sec

    class _C:
        def __init__(self): self.last = None
        async def post(self, url, json=None, headers=None):
            self.last = json
            class R:
                @staticmethod
                def json(): return {"result": True}
            return R()

    c = _C()
    _a.run(DahuaRpc(c, "1.2.3.4", "u", "p").set_screen("ТЕСТ"))
    _s.screen_hold_field, _s.screen_hold_sec = old
    return c.last.get("params", {})


print("\nДэлгэцийн «нэг команд» горим:")
_p = _probe_screen_params("", 0)
check("hold унтраалттай үед зөвхөн Custom", set(_p) == {"Custom"})
_p = _probe_screen_params("Time", 30)
check("hold идэвхтэй үед хугацааны талбар нэмэгдэнэ", _p.get("Time") == 30)
check("текст хэвээр дамжина", _p.get("Custom") == "ТЕСТ")
_p = _probe_screen_params("HoldTime", 45)
check("талбарын нэр тохируулж болно", _p.get("HoldTime") == 45 and "Time" not in _p)

print(f"\n{PASS} PASS, {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
