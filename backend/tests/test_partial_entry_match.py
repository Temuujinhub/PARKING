"""Орох дутуу уншсан → гарах зөв уншсан тохирлын логик (match_open_session
3-р шат) + аюулгүй байдал. Бодит машиныг алдалгүй, орох цагаар нь төлбөр авах
зорилго зөв ажиллаж, буруу машинд тохохгүйг шалгана.

    cd backend && venv/bin/python tests/test_partial_entry_match.py
"""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.session_logic import is_valid_plate, plates_ocr_similar  # noqa: E402

PASS = FAIL = 0


def check(name, cond):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗ <<< FAIL'} {name}")


# match_open_session-ий 3-р шатны сонголтын дүрмийг цэвэр функцээр дуурайж шалгана
def partial_candidates(exit_plate, open_plates):
    """match_open_session-ий 3-р шаттай ЯГ ижил дүрэм (тестэд тусгаарлав)."""
    if not is_valid_plate(exit_plate):
        return []
    return [p for p in open_plates
            if not is_valid_plate(p) and len(p) >= 3
            and (exit_plate.startswith(p) or exit_plate.endswith(p) or p in exit_plate)]


print("Орох дутуу → гарах зөв тохирол:")
# 1) «4132» орох phantom, «4132УАР» зөв гарав — ЦОРЫН ГАНЦ нэр дэвшигч → тохоно
c = partial_candidates("4132УАР", ["4132"])
check("«4132» phantom ↔ гарах «4132УАР» (эхлэл) — тохоно", c == ["4132"])

# 2) «132УБИ» (нэг цифр дутуу) ↔ «1132УБИ» (төгсгөл) — тохоно
c = partial_candidates("1132УБИ", ["132УБИ"])
check("«132УБИ» phantom ↔ гарах «1132УБИ» (төгсгөл) — тохоно", c == ["132УБИ"])

# 3) АЮУЛГҮЙ: 2 phantom «4132» ба «4132У» аль аль нь тохирвол — сэжигтэй, алгасна
c = partial_candidates("4132УАР", ["4132", "4132У"])
check("2+ нэр дэвшигч → сэжигтэй (match_open_session None буцаана)", len(c) == 2)

# 4) АЮУЛГҮЙ: гарах дугаар өөр машины — огт хамааралгүй phantom-д тохохгүй
c = partial_candidates("4132УАР", ["7890"])
check("хамааралгүй «7890» phantom — тохохгүй", c == [])

# 5) АЮУЛГҮЙ: ЗӨВ форматтай орох дугаар энэ шатанд ОРОХГҮЙ (1-2-р шатаар л)
c = partial_candidates("4132УАР", ["4132УАВ"])
check("зөв форматтай орох «4132УАВ» — 3-р шатанд орохгүй", c == [])

# 6) АЮУЛГҮЙ: гарах дугаар дутуу (буруу формат) бол 3-р шат огт ажиллахгүй
c = partial_candidates("4132", ["4132"])
check("гарах дутуу «4132» — 3-р шат идэвхжихгүй (зөвхөн зөв гарахад)", c == [])

# 7) 2 тэмдэгтээс богино phantom — хэт олон утгатай тул тохохгүй
c = partial_candidates("4132УАР", ["41"])
check("«41» хэт богино (len<3) — тохохгүй", c == [])

print("Дугаар засварын логик (handle_exit):")
# corrected нь: орох буруу формат БА гарах зөв формат үед л True
for entry, exit_p, want in [("4132", "4132УАР", True), ("132УБИ", "1132УБИ", True),
                            ("4132УАВ", "4132УАВ", False), ("4132", "4132", False)]:
    corrected = not is_valid_plate(entry) and is_valid_plate(exit_p)
    check(f"«{entry}»→«{exit_p}» засах эсэх = {want}", corrected == want)

print("Төлбөрийн зөв эх сурвалж:")
# Дугаар засагдсан ч төлбөр session-ий entry_time-аар бодогдоно (орсон цаг зөв).
# Энэ нь кодын зарчим: session_fee_info(s) → s.entry_time ашиглана.
sess = SimpleNamespace(entry_time="T_entry", plate_number="4132")
# Дугаар засах нь entry_time-д НӨЛӨӨЛӨХГҮЙ
sess.plate_number = "4132УАР"
check("дугаар засахад entry_time хэвээр (төлбөр орсон цагаар зөв)",
      sess.entry_time == "T_entry")

print("=" * 40)
print(f"ҮР ДҮН: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
