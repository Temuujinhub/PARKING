"""Гэрээт машины жагсаалтыг Excel-ээс уншиж бүртгэх.

Бодит файлууд нь «нэг хуудас = нэг түрээслэгч байгууллага» бүтэцтэй, хуудас бүр
өөр өөр гарчигтай, багануудын байрлал ч зөрдөг. Тиймээс тогтмол баганад
найдахгүй — гарчгийн МӨРИЙГ хайж, «Улсын дугаар» гэх мэт нэрээр багануудыг
олно. Нэг мөрөнд хэд хэдэн дугаар байж болно (ж: банкны мөнгө зөөвөрлөх машин).

Гол хэрэглээ:
    rows, warnings = parse_workbook(file_bytes)
    → rows: [{plate, full_name, note, company, sheet}]
"""
import io
import re

# Гарчгийн нүдийг таних түлхүүр үгс (жижиг үсгээр харьцуулна)
PLATE_HEADERS = ("улсын дугаар", "улсын дугаар ", "дугаар", "plate")
NAME_HEADERS = ("эзэмшигч", "нэр", "owner")
NOTE_HEADERS = ("албан тушаал", "тушаал", "position")

# 4 орон + 3 кирилл үсэг (ж: 1234УБА). Хооронд нь зай/зураас байж болно.
PLATE_RE = re.compile(r"^\d{4}[А-ЯӨҮЁ]{3}$")


def normalize_plate(raw: str) -> str:
    """«76-49 УБЯ» → «7649УБЯ». Зай, зураас, цэг, latin-ийг кирилл рүү."""
    s = (raw or "").upper().strip()
    s = re.sub(r"[\s\-–—_.,/\\'\"«»()]", "", s)
    # Excel-д латин үсэг холилдож бичигдсэн тохиолдол элбэг
    for lat, cyr in (("A", "А"), ("B", "В"), ("C", "С"), ("E", "Е"), ("H", "Н"),
                     ("K", "К"), ("M", "М"), ("O", "О"), ("P", "Р"), ("T", "Т"),
                     ("X", "Х"), ("Y", "У")):
        s = s.replace(lat, cyr)
    return s


def _cell(row, idx):
    if idx is None or idx >= len(row):
        return ""
    v = row[idx]
    return "" if v is None else str(v).strip()


def _find_header(rows: list) -> tuple[int | None, list[int], int | None, int | None]:
    """Гарчгийн мөрийг олж (индекс, дугаарын баганууд, нэр, тэмдэглэл) буцаана."""
    for i, row in enumerate(rows[:12]):  # гарчиг эхний мөрүүдэд байдаг
        cells = [str(c).strip().lower() if c is not None else "" for c in row]
        plate_cols = [j for j, c in enumerate(cells)
                      if any(h in c for h in PLATE_HEADERS) and c]
        if not plate_cols:
            continue
        name_col = next((j for j, c in enumerate(cells)
                         if any(h == c or h in c for h in NAME_HEADERS)), None)
        note_col = next((j for j, c in enumerate(cells)
                         if any(h in c for h in NOTE_HEADERS)), None)
        # Гарчгийн мөрөнд байхгүй ч дугаартай зэрэгцээ багана (ж: "банкны мөнгөн
        # зөөврийн машин") байвал түүнийг ч дугаарын багана гэж үзнэ
        for j, c in enumerate(cells):
            if c and j not in plate_cols and "машин" in c and j > max(plate_cols):
                plate_cols.append(j)
        return i, sorted(plate_cols), name_col, note_col
    return None, [], None, None


def _sheet_title(rows: list, sheet_name: str) -> str:
    """Хуудасны эхний утгатай нүдийг байгууллагын нэр болгоно (ж: «"SGS" гадна
    автомашины зогсоолын бүртгэл» → «SGS»). Олдохгүй бол хуудасны нэр."""
    for row in rows[:3]:
        for c in row:
            if c and str(c).strip():
                t = str(c).strip()
                t = re.sub(r"\s*гадна\s+автомашины.*$", "", t, flags=re.I).strip()
                t = t.replace('"', "").replace("«", "").replace("»", "").strip()
                if t and not t.startswith("/"):
                    return t[:160]
    return sheet_name.strip()[:160]


def parse_workbook(data: bytes) -> tuple[list[dict], list[str]]:
    """Excel-ийн БҮХ хуудсыг уншиж мөрүүд + анхааруулга буцаана."""
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
    out: list[dict] = []
    warnings: list[str] = []
    seen: set[str] = set()

    for ws in wb.worksheets:
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
        hdr, plate_cols, name_col, note_col = _find_header(rows)
        if hdr is None:
            warnings.append(f"«{ws.title}»: «Улсын дугаар» багана олдсонгүй — алгаслаа")
            continue
        company = _sheet_title(rows, ws.title)
        found = 0
        for row in rows[hdr + 1:]:
            name = _cell(row, name_col)
            note = _cell(row, note_col)
            for pc in plate_cols:
                plate = normalize_plate(_cell(row, pc))
                if not plate:
                    continue
                if not PLATE_RE.match(plate):
                    # Гарчиг давтагдсан, нийлбэр мөр гэх мэт — чимээгүй алгасна,
                    # гэхдээ дугаар мэт харагдвал анхааруулна
                    if any(ch.isdigit() for ch in plate) and len(plate) >= 5:
                        warnings.append(f"«{company}»: танигдахгүй дугаар «{_cell(row, pc)}»")
                    continue
                if plate in seen:
                    warnings.append(f"«{company}»: {plate} давхардсан — эхнийхийг үлдээв")
                    continue
                seen.add(plate)
                out.append({"plate": plate, "full_name": name[:120], "note": note[:200],
                            "company": company, "sheet": ws.title.strip()})
                found += 1
        if not found:
            warnings.append(f"«{company}»: нэг ч дугаар олдсонгүй")
    return out, warnings


def import_rows(db, rows: list[dict], site_id: str | None, *,
                contract_type: str = "CONTRACT", valid_days: int = 365,
                monthly_fee: float = 0, deactivate_missing: bool = False) -> dict:
    """Задалсан мөрүүдийг registered_drivers руу оруулна (идемпотент upsert).

    Түлхүүр = (plate_number, site_id). Байвал шинэчилнэ, байхгүй бол үүсгэнэ —
    файлыг олон удаа импортлож болно (давхардал үүсэхгүй).

    deactivate_missing=True бол тухайн зогсоолын жагсаалтад ОРООГҮЙ хуучин
    бүртгэлүүдийг идэвхгүй болгоно (жагсаалтыг файлаар бүрэн солих горим).
    Устгадаггүй — түүх, буруу импортоос сэргээх боломж хадгалагдана.
    """
    from datetime import datetime, timedelta

    from ..models import RegisteredDriver

    now = datetime.utcnow()
    valid_to = now + timedelta(days=valid_days)

    existing = {d.plate_number: d for d in db.query(RegisteredDriver)
                .filter(RegisteredDriver.site_id == site_id).all()}
    created = updated = 0
    for r in rows:
        d = existing.get(r["plate"])
        if d:
            d.full_name = r["full_name"] or d.full_name
            d.company = r["company"]
            d.note = r["note"]
            d.contract_type = contract_type
            d.valid_to = valid_to
            d.is_active = True
            updated += 1
        else:
            db.add(RegisteredDriver(
                plate_number=r["plate"], full_name=r["full_name"], company=r["company"],
                note=r["note"], contract_type=contract_type, site_id=site_id,
                monthly_fee=monthly_fee, valid_from=now, valid_to=valid_to, is_active=True))
            created += 1

    deactivated = 0
    if deactivate_missing:
        keep = {r["plate"] for r in rows}
        for plate, d in existing.items():
            if plate not in keep and d.is_active:
                d.is_active = False
                deactivated += 1

    db.commit()
    return {"created": created, "updated": updated, "deactivated": deactivated,
            "total": len(rows)}
