"""«Гадны хандалт алга» vs «ХЭМЖЭЭГҮЙ» — хоёрыг хольж болохгүй.

    cd backend && venv/bin/python tests/test_foreign_access_measured.py

2026-08-20: Эрүүл мэнд хуудсанд 22 камер бүгд «—» харагдаж байсныг «гадны
хандалт алга» гэж уншиж болохоор байв. Үнэндээ хэмжилт амжилтгүй болбол алдаа
нь зөвхөн debug логт бичигдээд UI ялгаагүй «—» хэвээр үлддэг байсан.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings  # noqa: E402

settings.barrier_mock = True

from app.services import camera_sessions as cs  # noqa: E402

ok = 0


def check(name, cond):
    global ok
    print(("  ✓ " if cond else "  ✗ ") + name)
    assert cond, name
    ok += 1


cs._state.clear()

# Хэмжилт хараахан ажиллаагүй — «цэвэр» биш «мэдэхгүй»
st = cs.measurement_status()
check("эхэнд хэмжилт идэвхгүй гэж мэдээлнэ", st["enabled"] is False and st["measured"] == 0)
check("камергүй үед last_ok_at нь None (max() унахгүй)", st["last_ok_at"] is None)

# Алдаа гарсан камер: checked_at бичигдэхгүй → UI «хэмжигдээгүй» гэж харуулна
cs._note("cam-err", error="TimeoutError")
check("алдаатай камерт checked_at бичигдэхгүй", cs.foreign_info("cam-err")["checked_at"] is None)
check("алдаа өөрөө хадгалагдана", cs.foreign_info("cam-err")["error"] == "TimeoutError")
check("оролдсон цаг бичигдэнэ", bool(cs.foreign_info("cam-err")["attempted_at"]))

# Алгассан камер (таслуур/завгүй) — мөн л хэмжигдээгүй
cs._note("cam-skip", skipped="хаалт/камер завгүй")
check("алгассан камер хэмжигдээгүй хэвээр", cs.foreign_info("cam-skip")["checked_at"] is None)

# Амжилттай хэмжилт: цэвэр (хандалт алга) ч гэсэн checked_at бий
cs._note("cam-ok", sessions=[], supported=True, error=None, checked_at="2026-08-20T12:00:00")
info = cs.foreign_info("cam-ok")
check("амжилттай хэмжилтэд checked_at бий", info["checked_at"] == "2026-08-20T12:00:00")
check("цэвэр камерын жагсаалт хоосон", info["sessions"] == [])

st = cs.measurement_status()
check("3 камерын 1 нь л хэмжигдсэн", st["cameras"] == 3 and st["measured"] == 1)
check("алдаатай камерын тоо тусдаа гарна", st["failing"] == 1)
check("last_ok_at = амжилттай хэмжилтийн цаг", st["last_ok_at"] == "2026-08-20T12:00:00")

# Дараагийн оролдлого унавал ӨМНӨХ амжилттай хэмжилт устахгүй (түүх алдагдахгүй)
cs._note("cam-ok", error="ConnectError")
check("дараагийн алдаа өмнөх checked_at-ыг устгахгүй",
      cs.foreign_info("cam-ok")["checked_at"] == "2026-08-20T12:00:00")
check("гэхдээ алдаа нь тэмдэглэгдэнэ", cs.foreign_info("cam-ok")["error"] == "ConnectError")

# Гадны хандалт илэрсэн тохиолдол
cs._note("cam-bad", sessions=[{"user": "Meguun", "ip": "172.10.0.18", "last": "2026-08-20 11:59:00"}],
         supported=True, error=None, checked_at="2026-08-20T12:01:00")
check("илэрсэн хандалт хадгалагдана", cs.foreign_info("cam-bad")["sessions"][0]["user"] == "Meguun")

cs._state.clear()
print(f"\n{ok} шалгалт бүгд OK")
