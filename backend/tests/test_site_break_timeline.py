"""Орлогын хоолой АЛЬ ШАТАНД тасарсныг ялгах логик.

    cd backend && venv/bin/python tests/test_site_break_timeline.py

Яагаад тест хэрэгтэй вэ: энэ хэрэгсэл «камер эвдэрсэн» үү, «кассир алга» юу
гэдгийг ялгаж хэлдэг — хоёрын засвар нь тэс өөр (нэг нь техникч, нөгөө нь
хүний нөөц). Шатны ДАРААЛАЛ (уншилт → session → гарц → төлбөртэй → орлого)
өөрчлөгдвөл дүгнэлт буруу хүн рүү заана. Мөн бааз нь медиан биш дундаж болбол
эвдэрсэн өдрүүд өөрсдөө баазыг татаж, уналт «байхгүй» болж харагдана.
"""
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

from site_break_timeline import baseline, find_break  # noqa: E402

ok = 0


def check(name, cond):
    global ok
    print(("  ✓ " if cond else "  ✗ ") + name)
    assert cond, name
    ok += 1


def day(i, reads=200, ent=190, exits=185, billed=170, rev=580_000, partial=False):
    return {"d": date(2026, 8, 14) + timedelta(days=i), "partial": partial,
            "reads_in": reads, "reads_out": reads, "gap": 1.0, "ent": ent,
            "exits": exits, "billed": billed, "free": exits - billed,
            "rev": rev, "cashiers": 2}


def stage_of(rows):
    brk, stage, _ = find_break(rows)
    return (brk["d"] if brk else None), (stage[0] if stage else None)


# ── Эвдрэлийн ШАТЫГ ялгах ────────────────────────────────────────────────────
NORMAL = [day(i) for i in range(3)]

# (1) Камер унтарсан: уншилтаас эхлээд бүх шат нурна → шалтгаан нь УНШИЛТ
d, s = stage_of(NORMAL + [day(i, reads=8, ent=6, exits=5, billed=4, rev=45_000)
                          for i in range(3, 7)])
check("камер унтарсан → эхэлж унасан шат = уншилт", s == "reads_in")
check("эвдрэлийн өдөр = уналт эхэлсэн ЭХНИЙ өдөр", d == date(2026, 8, 17))

# (2) Кассир зогссон: дээд шат бүр хэвийн, зөвхөн мөнгө цугларахгүй
_, s = stage_of(NORMAL + [day(i, rev=20_000) for i in range(3, 7)])
check("кассир зогссон → шат = орлого (камер руу заахгүй)", s == "rev")

# (3) Тариф үнэгүй болсон: гарц хэвийн ч төлбөртэй гарц алга
_, s = stage_of(NORMAL + [day(i, billed=10, rev=30_000) for i in range(3, 7)])
check("тариф үнэгүй болсон → шат = төлбөртэй гарц", s == "billed")

# (4) Уншилт ирсээр байхад session үүсэхгүй (callback/дүрэм)
_, s = stage_of(NORMAL + [day(i, ent=5, exits=5, billed=4, rev=20_000)
                          for i in range(3, 7)])
check("уншилт хэвийн ч session алга → шат = session", s == "ent")

# ── Худал сэрэмжлүүлэхгүй байх ───────────────────────────────────────────────
d, s = stage_of([day(i) for i in range(7)])
check("уналт байхгүй бол эвдрэлийн өдөр гарахгүй", d is None)

# Хөнгөн хэлбэлзэл (60%) нь эвдрэл БИШ — 40% босго
d, _ = stage_of(NORMAL + [day(i, rev=350_000) for i in range(3, 7)])
check("60% хүртэл буурсныг эвдрэл гэж үзэхгүй", d is None)

# Дуусаагүй өнөөдөр өөрөө эвдрэл болж болохгүй (цаг таслагдсан тул бага байна)
d, _ = stage_of([day(i) for i in range(6)] + [day(6, reads=20, ent=15, exits=12,
                                                  billed=10, rev=40_000,
                                                  partial=True)])
check("дуусаагүй өнөөдрийг эвдрэл гэж зарлахгүй", d is None)

# ── Бааз нь МЕДИАН байх ёстой ────────────────────────────────────────────────
rows = NORMAL + [day(i, rev=20_000) for i in range(3, 7)]
check("бааз = эхний хагасын медиан (эвдэрсэн өдрүүд татахгүй)",
      baseline(rows, "rev") == 580_000)

# Эхний хагас нь дууссан өдрүүдээс тооцогдоно — дуусаагүй өдөр баазыг гажуудуулахгүй
rows = [day(i) for i in range(6)] + [day(6, rev=10_000, partial=True)]
check("дуусаагүй өдөр баазад орохгүй", baseline(rows, "rev") == 580_000)

print(f"\n{ok} шалгалт БҮГД тэнцэв.")
