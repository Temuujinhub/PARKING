"""Орлогын эх сурвалжийн аудитын ШИЙДВЭР гаргах хоёр логик.

    cd backend && venv/bin/python tests/test_revenue_source_audit.py

Яагаад тест хэрэгтэй вэ: энэ хэрэгсэл «уналт байна уу, үгүй юу» гэсэн
БИЗНЕСИЙН дүгнэлт хэлдэг. Медианыг дунджаар сольсон эсвэл гүйдэг цонх нэг
өдрийг олон дахин тоолвол дүгнэлт эсрэгээрээ гарна.
"""
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

from revenue_source_audit import find_bulks, spike_days  # noqa: E402

ok = 0


def check(name, cond):
    global ok
    print(("  ✓ " if cond else "  ✗ ") + name)
    assert cond, name
    ok += 1


# ── spike_days ───────────────────────────────────────────────────────────────
days = ["08-14", "08-15", "08-16", "08-17", "08-18"]
tot = {"08-14": 4_153_000, "08-15": 2_106_000, "08-16": 1_961_500,
       "08-17": 2_271_500, "08-18": 1_865_500}

# (1) Өндөр өдрийн илүүдэл нь ӨР бол → нэг удаагийн цуглуулалт
debt_one_off = {"08-14": 1_900_000, "08-15": 0, "08-16": 0, "08-17": 0, "08-18": 0}
med, spikes = spike_days(days, tot, debt_one_off)
check("медиан нь дундаж биш (өндөр өдөр түвшинг татахгүй)", med == 2_106_000)
check("өндөр өдөр яг нэг илэрнэ", [d for d, _, _ in spikes] == ["08-14"])
d, extra, share = spikes[0]
check("илүүдэл = өдөр − медиан", extra == 4_153_000 - 2_106_000)
check("илүүдлийн дийлэнх нь ӨР бол 50%+ гэж гарна", share >= 50)

# (2) Ижил дүн ч ӨРгүй бол → өндөрлөлт ЖИНХЭНЭ хүчин чадал байсан
_, spikes2 = spike_days(days, tot, {d: 0 for d in days})
check("өргүй өндөрлөлтийн өр% = 0", spikes2[0][2] == 0)

# (3) Тогтвортой түвшинд огт өндөр өдөр байхгүй
flat = {d: 2_000_000 for d in days}
_, spikes3 = spike_days(days, flat, {d: 0 for d in days})
check("тогтвортой түвшинд өндөр өдөр заахгүй", spikes3 == [])
check("хоосон өдрийн жагсаалтад унахгүй", spike_days([], {}, {}) == (0.0, []))

# ── find_bulks ───────────────────────────────────────────────────────────────
t0 = datetime(2026, 8, 14, 12, 0)
# 12 төлбөр 20 минут дотор = бөөн
burst = {("08-14", "sogii"): [t0 + timedelta(minutes=i * 2) for i in range(12)]}
b = find_bulks(burst)
check("бөөн бүртгэл илэрнэ", len(b) == 1 and b[0][1] == "sogii")
check("бөөнийг өдөрт ГАНЦ удаа мэдээлнэ (давхар тоолохгүй)", len(b) == 1)
check("бөөний тоо цонхны доторх төлбөрийн тоо", b[0][4] >= 10)

# Ижил 12 төлбөр өдөржингөө тархсан = бөөн БИШ
spread = {("08-14", "sogii"): [t0 + timedelta(minutes=i * 45) for i in range(12)]}
check("тархсан ажиллагааг бөөн гэж андуурахгүй", find_bulks(spread) == [])
check("хоосон оролт", find_bulks({}) == [])

# Хилийн тохиолдол: яг 10 төлбөр яг 30 мин дотор
edge = {("08-14", "a"): [t0 + timedelta(minutes=i * 3) for i in range(10)]}
check("хил дээрх 10/30мин илэрнэ", len(find_bulks(edge)) == 1)
edge2 = {("08-14", "a"): [t0 + timedelta(minutes=i * 4) for i in range(10)]}
check("36 мин үргэлжилсэн 10 төлбөр бөөн биш", find_bulks(edge2) == [])

print(f"\n{ok} шалгалт бүгд OK")
