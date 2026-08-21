"""Камерын логийг серверийн уншилттай тулгах шийдвэрийн ХОЁР хамгаалалт.

    cd backend && venv/bin/python tests/test_event_loss_diag.py

Яагаад тест хэрэгтэй вэ: энэ хэрэгсэл «стрим тасарсан» гэж хэлбэл техникч
зогсоол руу явдаг. 2026-08-21-нд Эрэл-13 дээр камерын цаг ±0с байхад ердөө
5 хосоос -54.4 минутын «зөрүү» гарч, 98% алдагдал гэж ХУДАЛ мэдээлсэн —
сервер тэр үед камерын логоос ОЛОН уншилт хүлээн авч байсан.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

from event_loss_diag import MATCH_SEC, estimate_skew, suspect_matching  # noqa: E402

ok = 0


def check(name, cond):
    global ok
    print(("  ✓ " if cond else "  ✗ ") + name)
    assert cond, name
    ok += 1


# ── estimate_skew: цөөн хосоос гарсан том зөрүүнд итгэхгүй ───────────────────
skew, trusted = estimate_skew([-3264, -3260, -3270, -3255, -3266], 164)
check("Эрэл-13-ын хэв шинж: 5 хос / 164 боломж → ИТГЭХГҮЙ", not trusted)
check("зөрүүний утга нь медианаараа буцна", skew == -3264)

many = [-3264] * 40
check("олон хосоор батлагдсан том зөрүүг ХЭРЭГЛЭНЭ",
      estimate_skew(many, 100)[1])

check("хосын 20%-иас дээш дэмжлэгтэй бол итгэнэ",
      estimate_skew([-3264] * 25, 100)[1])
check("20%-иас доош дэмжлэгтэй бол итгэхгүй",
      not estimate_skew([-3264] * 15, 200)[1])

# Бага зөрүү (±120с дотор) нь тулгалтыг эвдэхгүй тул дэмжлэг шаардахгүй
skew, trusted = estimate_skew([-9, -6, -4], 200)
check("жижиг зөрүүг цөөн хосоор ч хүлээж авна", trusted and skew == -6)

check("хос огт байхгүй бол зөрүү 0 ба итгэлгүй", estimate_skew([], 50) == (0.0, False))
check("MATCH_SEC нь секундээр илэрхийлэгдэнэ", MATCH_SEC == 120)

# ── suspect_matching: логоос цөөнгүй уншилт ирсэн атал «алдагдал» өндөр ──────
check("Эрэл-13: логт 164, серверт 243 ирсэн, 162 «алдагдсан» → сэжигтэй",
      suspect_matching(164, 162, 243))
check("сервер логоос ЦӨӨН уншилт авсан бол жинхэнэ алдагдал байж болно",
      not suspect_matching(164, 162, 20))
check("алдагдал бага бол сэжиглэхгүй", not suspect_matching(164, 20, 243))
check("лог хоосон бол дүгнэлт гаргахгүй", not suspect_matching(0, 0, 243))

print(f"\n{ok} шалгалт БҮГД тэнцэв.")
