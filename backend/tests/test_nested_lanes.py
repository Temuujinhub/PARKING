"""tools/nested_lanes.py — чиглэлийн нотолгоо + хий event-ийн ялгалт (DB-гүй).

    cd backend && venv/bin/python tests/test_nested_lanes.py
"""
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

from nested_lanes import classify_inside, direction_evidence, phantom_scan  # noqa: E402

PASS = FAIL = 0


def check(name, cond):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗ <<< FAIL'} {name}")


T0 = datetime(2026, 8, 20, 2, 0)     # UTC (= 10:00 УБ)
OUT_IN, OUT_EX = "outer-entry", "outer-exit"
CAM12, CAM13 = "cam-.12", "cam-.13"


def m(n):
    return T0 + timedelta(minutes=n)


# Рашбулагийн БОДИТ физик урсгал: гаднаас орж ирнэ → .13-аар доторх талбарт
# орно → .12-оор доторхоос гарна → гаднах гарцаар гарна.
events = []
for i, plate in enumerate(("1234УБА", "5678ХАА", "9012ТТТ", "3456МММ")):
    base = i * 5
    events += [(m(base + 0), OUT_IN, plate),      # гаднах орох хаалт
               (m(base + 2), CAM13, plate),       # доторх талбарт ОРЖ байна
               (m(base + 60), CAM12, plate),      # доторхоос ГАРЧ байна
               (m(base + 63), OUT_EX, plate)]     # гаднах гарц
events.sort()

print("direction_evidence — уншилтын дарааллаас чиглэлийг нотлох:")
ev = direction_evidence(events, {CAM12, CAM13}, {OUT_IN}, {OUT_EX})
check("гаднаас орсны дараа түрүүлж уншсан нь .13 (= дотоод ОРОХ)",
      ev["first_after_entry"].most_common(1)[0][0] == CAM13)
check("гаднаас гарахын өмнө сүүлд уншсан нь .12 (= дотоод ГАРАХ)",
      ev["last_before_exit"].most_common(1)[0][0] == CAM12)
check("нотолгоо бүх машиныг хамарсан", ev["first_after_entry"][CAM13] == 4
      and ev["last_before_exit"][CAM12] == 4)

# Цонхноос ГАДУУР (5 цагийн дараа) уншилт нотолгоонд орохгүй
far = [(m(0), OUT_IN, "7777ААА"), (m(300), CAM12, "7777ААА")]
ev2 = direction_evidence(sorted(far), {CAM12, CAM13}, {OUT_IN}, {OUT_EX}, window_min=240)
check("240 минутаас хол уншилтыг тооцохгүй", not ev2["first_after_entry"])

# Огт дотогш ороогүй машин (гаднаа л зогсоод гарсан) нотолгоог бохирдуулахгүй
only_outer = [(m(0), OUT_IN, "8888БББ"), (m(30), OUT_EX, "8888БББ")]
ev3 = direction_evidence(sorted(only_outer), {CAM12, CAM13}, {OUT_IN}, {OUT_EX})
check("дотоод уншилтгүй машин нотолгоонд нөлөөлөхгүй",
      not ev3["first_after_entry"] and not ev3["last_before_exit"])

print("\nphantom_scan — машингүй үеийн event-ийг ялгах:")
# «5555ССС» зогссон газраа 6 удаа дахин уншигдсан (burst), гаднаас орсон бүртгэлтэй.
# «0000ХХХ» гаднаас ОРОЛГҮЙ 4 удаа уншигдсан (ghost) — хажуугийн замын машин.
dev_events = [(m(i * 4), "5555ССС") for i in range(6)]
dev_events += [(m(100 + i * 7), "0000ХХХ") for i in range(4)]
dev_events += [(datetime(2026, 8, 20, 20, 30), "1111ННН")]  # УБ-аар 04:30 = шөнө
entries = {"5555ССС": [m(-10)], "1111ННН": [m(-20)]}
r = phantom_scan(sorted(dev_events), entries)

check("нийт уншилт зөв", r["total"] == 11)
check("burst-д зогссон машин орсон",
      any(p == "5555ССС" and n == 6 for p, _, _, n in r["bursts"]))
check("гаднаас орсон машин ghost биш", all(p != "5555ССС" for p, *_ in r["ghosts"]))
check("гаднаас ороогүй дугаар ghost гэж тэмдэглэгдэв",
      any(p == "0000ХХХ" and n == 4 for p, n, *_ in r["ghosts"]))
check("шөнийн уншилт (УБ цагаар 04:30) баригдав",
      len(r["night"]) == 1 and r["night"][0][1] == "1111ННН")
check("медиан завсар тооцогдов", r["median_gap_sec"] is not None)

# Ганц уншилт burst болохгүй
r2 = phantom_scan([(m(0), "2222ППП")], {"2222ППП": [m(-5)]})
check("ганц уншилт burst биш", r2["bursts"] == [])

print("\nclassify_inside — бодит тооллогыг бүртгэлтэй хоёр тал руу тулгах:")


class S:                       # хиймэл session (ORM бус)
    def __init__(self, plate, paused=None):
        self.plate_number, self.paused_since = plate, paused
        self.entry_time = T0


def sim(a, b):                 # энгийн OCR-ойролцоо: нэг тэмдэгтийн зөрүү
    return len(a) == len(b) and sum(x != y for x, y in zip(a, b)) == 1


sessions = [S("1111ААА", paused=T0),      # дотор гэж тэмдэглэгдсэн, жагсаалтад бий
            S("2222ББВ"),                  # жагсаалтад бий, тоолуур ажиллаж байна
            S("3333ВГД", paused=T0),       # жагсаалтад АЛГА атлаа «дотор»
            S("4444ГДЕ"),                  # жагсаалтад алга, гадаа — хөндөхгүй
            S("5555ЕЁЖ")]                  # OCR-ойролцоо тохирол
r = classify_inside(["1111ААА", "2222ББВ", "5555ЕЁЗ", "9999ЯЯЯ"], sessions, sim)

check("аль хэдийн «дотор» нь зөв танигдав",
      [p for p, _ in r["already"]] == ["1111ААА"])
check("тоолуур ажиллаж буйг ЗОГСООХ жагсаалтад оруулав",
      [p for p, _ in r["to_pause"]] == ["2222ББВ", "5555ЕЁЗ"])
check("OCR-ойролцоо нь бодит session-д тохирсон",
      r["to_pause"][1][1].plate_number == "5555ЕЁЖ")
check("жагсаалтад алга «дотор» нь ҮРГЭЛЖЛҮҮЛЭХ-д орсон",
      [s.plate_number for s in r["to_resume"]] == ["3333ВГД"])
check("гадаа, «дотор» биш машиныг ХӨНДӨХГҮЙ",
      all(s.plate_number != "4444ГДЕ" for s in r["to_resume"]))
check("session огт алга дугаар тусдаа бүлэгт", r["no_session"] == ["9999ЯЯЯ"])

# Хоосон жагсаалт = «дотор» гэж тэмдэглэгдсэн БҮГД үргэлжилнэ
r2 = classify_inside([], sessions, sim)
check("хоосон жагсаалт → бүх зогсолт цуцлагдана",
      {s.plate_number for s in r2["to_resume"]} == {"1111ААА", "3333ВГД"})
check("хоосон жагсаалтад зогсоох зүйл алга", r2["to_pause"] == [])

# Хоёр session-д ойролцоо тохирвол ТААМАГЛАХГҮЙ (эргэлзээтэй)
amb = [S("7777ААА"), S("7777ААБ")]
r3 = classify_inside(["7777ААВ"], amb, sim)
check("хоёрдмол OCR тохирлыг таамаглахгүй, session алга гэж үзнэ",
      r3["no_session"] == ["7777ААВ"] and r3["to_pause"] == [])

print(f"\n{PASS} PASS, {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
