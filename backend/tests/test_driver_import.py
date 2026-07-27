"""Гэрээт машины Excel импорт — олон хуудастай бодит файлын бүтцийг тэсвэрлэх эсэх.

    cd backend && venv/bin/python tests/test_driver_import.py

Яагаад чухал вэ: түрээслэгчээс ирэх файл бүр өөр бүтэцтэй (хуудас = байгууллага,
гарчиг өөр өөр мөрөнд, багана шилжсэн, нэг мөрөнд 2 дугаар). Задлагч эвдэрвэл
гэрээт машин таниагдахгүй, жолооч төлбөргүй гарч чадахгүй болно.

Шалгах зүйл:
  - Дугаарын хэлбэржүүлэлт: зай, зураас, латин үсэг, хог тэмдэгт
  - Гарчгийн мөрийг олох (эхний мөр биш байсан ч)
  - Нэг мөрөнд 2 дугаартай багана (банкны мөнгө зөөврийн машин)
  - Байгууллагын нэрийг хуудасны гарчгаас цэвэрлэж авах
  - Давхардсан дугаарыг нэг л удаа авах
  - Буруу хэлбэрийн мөрийг алгасах (нийлбэр, хоосон дугаарлалт)
"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import openpyxl  # noqa: E402

from app.services.driver_import import normalize_plate, parse_workbook  # noqa: E402

PASS = FAIL = 0


def check(name, cond):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗ <<< FAIL'} {name}")


print("Дугаарын хэлбэржүүлэлт:")
check("зайтай: '76-49 УБЯ' → 7649УБЯ", normalize_plate("76-49 УБЯ") == "7649УБЯ")
check("зайгүй: '7380УКК ' → 7380УКК", normalize_plate("7380УКК ") == "7380УКК")
check("ташуу зураастай: '2311 УАХ\\' → 2311УАХ", normalize_plate("2311 УАХ\\") == "2311УАХ")
check("латин холилдсон: '1234YBA' → кирилл", normalize_plate("1234YBA") == "1234УВА")
check("хоосон → хоосон", normalize_plate("") == "")
check("None → хоосон", normalize_plate(None) == "")


def build(sheets: dict) -> bytes:
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name[:31])
        for r in rows:
            ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


print("\nОлон хуудас, гарчиг өөр мөрөнд:")
data = build({
    "Alpha": [
        ['"Альфа" ХХК гадна автомашины зогсоолын бүртгэл', "", "", ""],
        ["", "/20 машины зогсоолын зөвшөөрөлтэй/", "", ""],
        ["№", "Эзэмшигч", "Албан тушаал", "Улсын дугаар"],
        ["1", "Б.Болд", "Жолооч", "12-34 УБА"],
        ["2", "Д.Дорж", "Захирал", "5678УНЕ"],
        ["3", "", "", ""],                      # хоосон дугаарлалтын мөр
    ],
    "Банк": [
        ['"Банк" гадна автомашины зогсоолын бүртгэл', "", "", "", ""],
        ["№", "Эзэмшигч", "Албан тушаал", "Улсын дугаар", "мөнгөн зөөврийн машин"],
        ["1", "", "", "1111УАА", "2222УББ"],    # нэг мөрөнд 2 дугаар
    ],
})
rows, warns = parse_workbook(data)
plates = [r["plate"] for r in rows]
check("нийт 4 дугаар уншсан", len(rows) == 4)
check("зайтай дугаар зөв", "1234УБА" in plates)
check("2 дахь хуудсын нэмэлт багана уншигдсан", "2222УББ" in plates)
check("байгууллагын нэр цэвэрлэгдсэн (хашилтгүй)",
      any(r["company"] == "Альфа ХХК" for r in rows))
check("эзэмшигч/албан тушаал уншигдсан",
      any(r["full_name"] == "Б.Болд" and r["note"] == "Жолооч" for r in rows))
check("хоосон мөр алгасагдсан", all(p for p in plates))

print("\nДавхардал:")
data = build({"A": [
    ["№", "Эзэмшигч", "Албан тушаал", "Улсын дугаар"],
    ["1", "Нэг", "", "1111УАА"],
    ["2", "Хоёр", "", "11-11 УАА"],  # ижил дугаар, өөр бичлэгтэй
]})
rows, warns = parse_workbook(data)
check("давхардсан дугаар нэг л удаа", len(rows) == 1)
check("давхардлыг анхааруулсан", any("давхардсан" in w for w in warns))

print("\nГарчиггүй хуудас:")
data = build({"Хоосон": [["зүгээр текст", ""], ["мөр", ""]]})
rows, warns = parse_workbook(data)
check("дугаар олдоогүй", len(rows) == 0)
check("шалтгааныг анхааруулсан", any("олдсонгүй" in w for w in warns))

print("\nБодит Моннисын файл (байвал):")
real = "/root/PARKING/docs/Monnis_property/МБ гадна автомашины зогсоолын бүртгэл -last.xlsx"
if os.path.isfile(real):
    with open(real, "rb") as f:
        rows, warns = parse_workbook(f.read())
    companies = {r["company"] for r in rows}
    check(f"300+ машин уншсан (одоо {len(rows)})", len(rows) > 300)
    check(f"14 байгууллага (одоо {len(companies)})", len(companies) == 14)
    check("бүх дугаар зөв хэлбэртэй",
          all(len(r["plate"]) == 7 and r["plate"][:4].isdigit() for r in rows))
    check("байгууллагын нэрэнд хашилт үлдээгүй",
          all('"' not in c for c in companies))
else:
    print("  (файл олдсонгүй — алгаслаа)")

print(f"\n{PASS} PASS, {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
