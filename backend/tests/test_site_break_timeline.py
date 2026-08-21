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
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

from site_break_timeline import (anomalies, avg_ticket, baseline,  # noqa: E402
                                 find_break, is_weekend)

ok = 0


def check(name, cond):
    global ok
    print(("  ✓ " if cond else "  ✗ ") + name)
    assert cond, name
    ok += 1


def day(i, reads=200, ent=190, exits=185, billed=170, rev=580_000, partial=False,
        top=50_000):
    return {"d": date(2026, 8, 14) + timedelta(days=i), "partial": partial,
            "reads_in": reads, "reads_out": reads, "gap": 1.0, "ent": ent,
            "exits": exits, "billed": billed, "free": exits - billed,
            "rev": rev, "top": top, "cashiers": 2}


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

# ── Амралтын өдрийг «эвдрэл» гэж заахгүй (Хангарьд/Кэй Эйч дээр гарсан алдаа) ──
# 08-12 Лх … 08-21 Ба; 08-15/16 нь Бямба/Ням — ачаалал хагасаар буурдаг.
def real(i):
    return date(2026, 8, 12) + timedelta(days=i)


check("гараг зөв ангилагдана", is_weekend(real(3)) and is_weekend(real(4))
      and not is_weekend(real(5)))

week = []
for i in range(9):
    d = real(i)
    if is_weekend(d):
        row = day(0, reads=320, ent=560, exits=580, billed=147, rev=270_000)
    else:
        row = day(0, reads=650, ent=1100, exits=1100, billed=400, rev=950_000)
    row["d"] = d
    week.append(row)
d, _ = stage_of(week)
check("амралтын өдрийн уналтыг эвдрэл гэж зарлахгүй", d is None)

# Ажлын өдөр үнэхээр унавал ажлын өдрийн баазаар барина
week[7]["rev"] = 300_000        # 08-19 Лх
d, _ = stage_of(week)
check("ажлын өдрийн бодит уналт баригдана", d == real(7))

# ── Ашиглалтад ороогүй эхний өдрийг эвдрэл гэж зарлахгүй (NIC) ──────────────
new_site = []
for i in range(9):
    row = day(0, reads=70, ent=130, exits=130, billed=60, rev=120_000)
    row["d"] = real(i)
    new_site.append(row)
new_site[0].update(reads_in=0, reads_out=0, ent=1, exits=6, billed=0, free=6, rev=0)
d, _ = stage_of(new_site)
check("цонхны эхний өдөр (өмнөх хэвийн өдөргүй) эвдрэл болохгүй", d is None)

# ── anomalies(): алдаж байсан дөрвөн дохио ──────────────────────────────────
TODAY = date(2026, 8, 20)
base = {"rev": 844_000, "billed": 143, "reads_in": 228, "ent": 350, "exits": 339}

# (1) Дундаж төлбөр унасан — машины тоо хэвээр, ҮНЭ унасан (Рашбулаг ЭТТ)
brk = day(0, reads=192, ent=233, exits=242, billed=86, rev=105_500)
txt = " ".join(anomalies([brk], brk, base, [], TODAY))
check("дундаж төлбөрийн уналт илэрнэ", "ДУНДАЖ ТӨЛБӨР" in txt)
check("дундаж төлбөр = орлого / төлбөртэй гарц", round(avg_ticket(brk)) == 1227)

# Машины тоо ч хамт унасан бол энэ нь ҮНЭний асуудал БИШ — давхар дуугарахгүй
vol = day(0, reads=20, ent=25, exits=25, billed=12, rev=15_000)
txt = " ".join(anomalies([vol], vol, base, [], TODAY))
check("тоо хэмжээний уналтыг «үнэ унасан» гэж хэлэхгүй", "ДУНДАЖ ТӨЛБӨР" not in txt)

# (2) Гарц уншилтгүйгээр хаагдсан — авто-хаалтын дохио (Номадс, Эрэл-13)
ghost = day(0, reads=163, ent=178, exits=268, billed=27, rev=24_000)
ghost["reads_out"], ghost["free"] = 38, 241
txt = " ".join(anomalies([ghost], None, base, [], TODAY))
check("гарц >> гарах уншилт бол авто-хаалт сэжиглэнэ", "ГАРЦ УНШИЛТГҮЙ" in txt)
check("0₮ гарц 80%+ бол тусад нь дуугарна", "0₮ ГАРЦ 80%" in txt)

# (3) Тогтмол ажилласан терминал зогссон (pos.ylalt — 40% босгонд баригдаагүй)
stats = [("pos.ylalt", datetime(2026, 8, 14, 18, 59), 234),
         ("sogii", datetime(2026, 8, 20, 15, 19), 30)]
txt = " ".join(anomalies([day(0)], None, base, stats, TODAY))
check("6 хоног чимээгүй терминал илэрнэ", "pos.ylalt" in txt)
check("өчигдөр ажилласан кассирыг зогссон гэхгүй", "sogii" not in txt)

# Ховор хэрэглэгддэг данс 2 хоног чимээгүй байхад дуугарахгүй (шуугиан багасгах)
rare = [("uyanga", datetime(2026, 8, 14, 10, 0), 3)]
txt = " ".join(anomalies([day(0)], None, base, rare, TODAY))
check("цөөн төлбөртэй данс худал дохио үүсгэхгүй", "uyanga" not in txt)

# ── Нэг төлбөрийн ДЭЭД дүн тасарсан (Рашбулаг: 96к → бүгд ≤25к) ─────────────
capped = [day(i, top=t) for i, t in enumerate([96_000, 57_000, 75_000])]
capped += [day(i + 3, rev=105_500, billed=86, top=25_000) for i in range(4)]
txt = " ".join(anomalies(capped, capped[3], base, [], TODAY))
check("төлбөрийн дээд дүн тасарсныг илрүүлнэ", "ДЭЭД ДҮН тасарсан" in txt)

# Дээд дүн хэвээр байвал дуугарахгүй — зөвхөн эзлэхүүн буурсан тохиолдол
same = [day(i, top=96_000) for i in range(3)]
same += [day(i + 3, rev=105_500, billed=86, top=90_000) for i in range(4)]
txt = " ".join(anomalies(same, same[3], base, [], TODAY))
check("дээд дүн хэвээр бол хязгаар гэж хэлэхгүй", "ДЭЭД ДҮН тасарсан" not in txt)

print(f"\n{ok} шалгалт БҮГД тэнцэв.")
