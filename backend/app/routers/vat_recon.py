"""ТЕГ-ийн мерчант порталын баримтын экспортыг манай баримттай тулгах логик.

Яагаад тусдаа модуль: (1) reports_router аль хэдийн 1,300+ мөр, (2) файл
задлах хэсэг нь оношилгоотой болж томорсон.

ГОЛ ЗАРЧИМ — файлын БАГАНА тогтмол биш. Портал/хувилбар/гар засвараас
хамаарч ДДТД, огноо, дүн хаана байх нь хөвдөг тул урьдын «B=ДДТД, C=огноо,
D=дүн» гэсэн ТОГТМОЛ индекс нэг ч мөр танихгүй болоод «Алдаа гарлаа» гэсэн
ганц мөр буцаадаг байв (хэрэглэгч юу буруу болсныг мэдэх аргагүй). Одоо:
толгой мөрөөр, тэр нь олдохгүй бол өгөгдлийн шинжээр багана танина; танихгүй
бол ЯГ ЮУ уншсанаа (мөрийн жишээ, алгассан шалтгаан бүрийн тоо) буцаана.
"""
import csv as _csv
import io as _io
import re
from collections import Counter
from datetime import date, datetime, time, timedelta

from fastapi import HTTPException

# ДДТД: ТЕГ 33 оронтой дугаарладаг; богиносгосон хувилбарт ч 30-аас багагүй
DDTD_MIN_LEN = 30
_SPACES = re.compile(r"[\s ._/\-]+")


def _norm(v) -> str:
    """Толгойн нэрийг харьцуулах хэлбэрт: жижиг үсэг, зай/цэг/зураас хаяна."""
    return _SPACES.sub("", str(v or "").strip().lower())


def as_ddtd(v) -> str | None:
    """Нүдийг ДДТД болгож уншина. Excel-д 33 оронтой дугаарыг ТОО болгож
    хадгалсан бол 1.52000200900001e+31 болж эвдэрдэг — түүнийг ч барина."""
    if v is None:
        return None
    if isinstance(v, float):
        # Тоо болж хадгалагдсан ДДТД — 15 оронгоос цааш нарийвчлал алдагдсан
        return None if v != int(v) or len(str(int(v))) < DDTD_MIN_LEN else str(int(v))
    if isinstance(v, int) and not isinstance(v, bool):
        s = str(v)
        return s if len(s) >= DDTD_MIN_LEN else None
    s = str(v).strip().replace(" ", "")
    return s if s.isdigit() and len(s) >= DDTD_MIN_LEN else None


_DT_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
               "%d-%m-%Y %H:%M:%S", "%d-%m-%Y %H:%M", "%d-%m-%Y")


def as_dt(v) -> datetime | None:
    """Огноог уншина: datetime нүд, «2026-08-24 06:07:05.0», «2026.08.24»,
    «24/08/2026 06:07», ISO «T» — бүгдийг барина."""
    if isinstance(v, datetime):
        return v
    if isinstance(v, date):
        return datetime.combine(v, time())
    s = str(v or "").strip().replace("T", " ")
    if not s or not s[0].isdigit():
        return None
    head, _, tail = s.partition(" ")
    s = (re.sub(r"[./]", "-", head) + (" " + tail if tail else ""))[:19].strip()
    for f in _DT_FORMATS:
        try:
            return datetime.strptime(s, f)
        except ValueError:
            continue
    return None


def as_num(v) -> float | None:
    """Дүнг уншина: тоо, «8,000.00», «8 000₮», «(1500)» (сөрөг) бүгдийг."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v or "").strip().replace(",", "").replace("₮", "")
    s = _SPACES.sub("", s) if " " in s or " " in s else s
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    try:
        return float(s)
    except ValueError:
        return None


# ─────────────────────────── Файл унших ────────────────────────────────────

def read_table(filename: str, raw: bytes) -> tuple[list[list], str, str]:
    """Файлыг мөрийн жагсаалт болгож уншина. Буцаана: (мөрүүд, хуудасны нэр, төрөл).

    Форматын алдааг ТОДОРХОЙ хэлнэ — «xlsx уншиж чадсангүй» биш, «энэ бол
    хуучин .xls, Excel дээр нээгээд .xlsx болгож хадгална уу» гэж."""
    name = (filename or "").lower()
    if not raw:
        raise HTTPException(400, "Файл хоосон байна (0 байт) — дахин экспорт хийж үзнэ үү.")
    if raw[:4] == b"\xd0\xcf\x11\xe0":
        raise HTTPException(400, "Энэ бол ХУУЧИН .xls (Excel 97-2003) файл. Excel дээр нээгээд "
                                 "«Save As → Excel Workbook (.xlsx)» гэж хадгалаад дахин оруулна уу.")
    if raw[:5] == b"%PDF-":
        raise HTTPException(400, "PDF файл байна. ТЕГ порталаас Excel (.xlsx) эсвэл CSV "
                                 "экспортыг татаж оруулна уу.")
    if raw[:2] == b"PK":            # zip → xlsx/xlsm
        import openpyxl as _xl
        try:
            wb = _xl.load_workbook(_io.BytesIO(raw), read_only=True, data_only=True)
        except Exception as e:      # noqa: BLE001
            raise HTTPException(400, f"Excel файл уншигдсангүй: {str(e)[:150]}. Файл "
                                     "гэмтсэн эсвэл нууц үгтэй байж болзошгүй.")
        ws = wb.active
        rows = _sheet_rows(ws)
        # Идэвхтэй хуудас хоосон бол баримттай хуудсыг өөрөө хайна (олон хуудастай экспорт)
        if len([r for r in rows if any(c is not None for c in r)]) < 2:
            for other in wb.worksheets:
                cand = _sheet_rows(other)
                if len([r for r in cand if any(c is not None for c in r)]) >= 2:
                    return cand, other.title, "xlsx"
        return rows, ws.title, "xlsx"
    # Текст (CSV/TSV) — ТЕГ портал CSV-ээр ч экспортолдог
    for enc in ("utf-8-sig", "cp1251", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise HTTPException(400, "Файлын төрөл танигдсангүй — .xlsx эсвэл .csv оруулна уу.")
    if "\x00" in text[:2000]:
        raise HTTPException(400, "Файлын төрөл танигдсангүй (хоёртын файл). ТЕГ порталын "
                                 ".xlsx эсвэл .csv экспортыг оруулна уу.")
    sample = text[:4000]
    delim = max(",;\t|", key=lambda d: sample.count(d))
    rows = [r for r in _csv.reader(_io.StringIO(text), delimiter=delim)]
    if not rows:
        raise HTTPException(400, "Файлаас нэг ч мөр уншигдсангүй.")
    return rows, name.rsplit("/", 1)[-1] or "csv", "csv"


def _sheet_rows(ws, max_blank: int = 5000) -> list[list]:
    """Хуудасны мөрүүд — өгөгдлийн дараах ХООСОН мөрүүдийг (max_blank дараалан)
    хүрмэгц зогсоно. ТЕГ-ийн 20260819.xlsx 479 баримттай атлаа хэмжээ нь 968,109
    мөр гэж бичигдсэн тул бүгдийг уншихад 24с зарцуулж байв."""
    rows, blank, seen = [], 0, False
    for r in ws.iter_rows(values_only=True):
        if any(c is not None and str(c).strip() for c in r):
            seen, blank = True, 0
        else:
            blank += 1
            if seen and blank > max_blank:
                break
        rows.append(list(r))
    while rows and not any(c is not None and str(c).strip() for c in rows[-1]):
        rows.pop()
    return rows


# ────────────────────────── Багана таних ───────────────────────────────────

_HDR = {
    "ddtd": ("ддтд", "дтд", "billid", "баримтындугаар", "баримтдугаар", "дугаарбаримт"),
    "dt": ("огноо", "огнооцаг", "date", "createdat", "хугацаа", "гүйлгээнийогноо", "огноохугацаа"),
    "amount": ("нийтдүн", "totalamount", "нийтүнэ", "дүн", "amount", "total", "төлбөр"),
    "vat": ("нөат", "vat", "нөатындүн", "нөатдүн"),
    "src": ("төрөл", "type", "эхсурвалж", "source", "суваг", "постөрөл", "postype", "хэлбэр",
            # ТЕГ портал: «Систем нийлүүлэгч» = QPayPos / Ontime / Онлайм нетворк
            "системнийлүүлэгч"),
}
_COL_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def col_name(i: int) -> str:
    """0→A, 25→Z, 26→AA (Excel-ийн баганын нэр — хэрэглэгчид харуулахад)."""
    s = ""
    while True:
        s = _COL_LETTERS[i % 26] + s
        i = i // 26 - 1
        if i < 0:
            return s


def _letter_to_idx(letter: str | None) -> int | None:
    """«C» / «c» / «3» → 0-based индекс. Хэрэглэгчийн гар тохиргоо."""
    if not letter:
        return None
    s = str(letter).strip().upper()
    if s.isdigit():
        return max(0, int(s) - 1)
    idx = 0
    for ch in s:
        if ch not in _COL_LETTERS:
            return None
        idx = idx * 26 + (_COL_LETTERS.index(ch) + 1)
    return idx - 1


def detect_columns(rows: list[list], override: dict | None = None) -> tuple[dict, int]:
    """(багануудын индекс, өгөгдөл эхлэх мөрийн дугаар)-ыг тодорхойлно.

    1) Толгой мөр хайна (эхний 20 мөрөөс танил нэр 2+ таарвал толгой).
    2) Толгойгүй/дутуу бол өгөгдлийн ШИНЖЭЭР танина (30+ оронтой тоо = ДДТД,
       уншигдах огноо = огноо, огнооны БАРУУН талын эхний тоон багана = дүн).
    3) override (хэрэглэгчийн заасан багана) бүхнээс дээгүүр."""
    cols: dict[str, int] = {}
    data_from = 0
    for i, r in enumerate(rows[:20]):
        # Толгой мөрөнд БОДИТ өгөгдөл (ДДТД/огноо) байхгүй — эс бөгөөс «Огноо»
        # гэсэн үг агуулсан өгөгдлийн мөрийг толгой гэж андуурч эхний баримтыг
        # алгасах эрсдэлтэй
        if any(as_ddtd(c) or as_dt(c) for c in r):
            continue
        hit, seen = {}, {_norm(c) for c in r}
        for key, names in _HDR.items():
            for j, c in enumerate(r):
                n = _norm(c)
                if not n:
                    continue
                if key == "amount" and any(v in n for v in _HDR["vat"]):
                    continue          # «НӨАТ-ын дүн» нь НИЙТ дүн биш
                if n in names or any(n.startswith(x) for x in names):
                    hit.setdefault(key, j)
        if len(hit) >= 2 and ("ddtd" in hit or "dt" in hit) and any(seen):
            cols, data_from = hit, i + 1
            break
    body = [r for r in rows[data_from:] if any(c is not None and str(c).strip() for c in r)]
    width = max((len(r) for r in body[:200]), default=0)
    sample = body[:200]
    if "ddtd" not in cols:
        score = [sum(1 for r in sample if as_ddtd(r[j] if j < len(r) else None)) for j in range(width)]
        if score and max(score) > 0:
            cols["ddtd"] = score.index(max(score))
    if "dt" not in cols:
        score = [sum(1 for r in sample if as_dt(r[j] if j < len(r) else None)) for j in range(width)]
        if score and max(score) > 0:
            cols["dt"] = score.index(max(score))
    if "amount" not in cols:
        # Тоон багануудаас: (1) огнооны БАРУУН талынх, (2) тогтмол БИШ утгатайг
        # эрэмбэлж сонгоно. ТТД/салбарын дугаар зэрэг «тогтмол» багана нь бүх
        # мөрд ижил байдаг тул дүн байх магадлал багатай.
        taken = {cols.get("ddtd"), cols.get("dt")}
        ranked = []
        for j in range(width):
            if j in taken:
                continue
            good = [v for v in (as_num(r[j]) if j < len(r) else None for r in sample) if v is not None]
            if len(good) < max(1, len(sample) * 0.6):
                continue
            right = cols.get("dt") is not None and j > cols["dt"]
            ranked.append(((0 if right else 1, 0 if len(set(good)) > 1 else 1, j), j))
        if ranked:
            cols["amount"] = min(ranked)[1]
    for key, letter in (override or {}).items():
        idx = _letter_to_idx(letter)
        if idx is not None:
            cols[key] = idx
    return cols, data_from


def _sample_text(rows: list[list], data_from: int) -> str:
    """Алдааны мессежид оруулах «эхний өгөгдлийн мөр» — юу уншсанаа харуулна."""
    for r in rows[data_from:]:
        if any(c is not None and str(c).strip() for c in r):
            parts = [f"{col_name(j)}=«{str(c)[:34]}»" for j, c in enumerate(r[:14])
                     if c is not None and str(c).strip()]
            return " | ".join(parts)
    return "(өгөгдлийн мөр алга)"


def parse_tax_export(filename: str, raw: bytes, override: dict | None = None) -> tuple[list, dict]:
    """ТЕГ файлыг задлана. Буцаана: (баримтууд, оношилгоо).
    Нэг ч баримт олдоогүй бол ЯГ ЮУ болсныг тайлбарласан 400-г шиднэ."""
    rows, sheet, kind = read_table(filename, raw)
    cols, data_from = detect_columns(rows, override)
    body = [r for r in rows[data_from:] if any(c is not None and str(c).strip() for c in r)]
    tax, skip = [], Counter()
    i_d, i_t, i_a = cols.get("ddtd"), cols.get("dt"), cols.get("amount")
    i_s = cols.get("src")
    lossy = 0
    for r in body:
        cell = lambda j: (r[j] if j is not None and j < len(r) else None)  # noqa: E731
        ddtd, dt, amount = as_ddtd(cell(i_d)), as_dt(cell(i_t)), as_num(cell(i_a))
        if ddtd and isinstance(cell(i_d), (int, float)) and not isinstance(cell(i_d), bool):
            lossy += 1
        if not ddtd:
            skip["ддтд"] += 1
            continue
        if dt is None:
            skip["огноо"] += 1
            continue
        if amount is None:
            skip["дүн"] += 1
            continue
        tax.append({"ddtd": ddtd, "dt": dt, "amount": amount,
                    "src": str(cell(i_s) or "").strip(), "used": False})
    diag = {
        "file": (filename or "")[:80], "kind": kind, "sheet": sheet, "bytes": len(raw),
        "rows": len(body), "parsed": len(tax), "header_row": data_from,
        "columns": {k: col_name(v) for k, v in cols.items() if v is not None},
        "skipped": dict(skip),
        "duplicate_ddtd": len(tax) - len({t["ddtd"] for t in tax}),
        "warnings": [],
    }
    if lossy:
        # Excel float 17 оронтой л нарийвчлалтай — 33 оронтой ДДТД тоо болж
        # хадгалагдвал сүүлийн орнууд 0 болж эвдэрдэг. Тулгалт цаг+дүнгээр
        # явдаг тул ажиллана, гэхдээ экспортын ДДТД баганад найдаж болохгүй.
        diag["warnings"].append(
            f"{lossy} мөрийн ДДТД Excel дээр ТОО болж хадгалагдсан тул сүүлийн орнууд "
            "эвдэрсэн байж болзошгүй (тулгалтад нөлөөлөхгүй — цаг+дүнгээр тулгадаг).")
    if diag["duplicate_ddtd"]:
        diag["warnings"].append(f"Файлд {diag['duplicate_ddtd']} давхардсан ДДТД байна.")
    if not tax:
        raise HTTPException(400, _no_rows_message(diag, rows, data_from, skip))
    return tax, diag


def _no_rows_message(diag: dict, rows: list, data_from: int, skip: Counter) -> str:
    """«Юуны алдаа вэ» гэдгийг мөрийн жишээ + шалтгаанаар нь тайлбарлана."""
    cols = diag["columns"]
    lines = [
        "ТЕГ файлаас нэг ч баримт олдсонгүй.",
        f"Файл: {diag['file'] or '—'} ({diag['kind']}, хуудас «{diag['sheet']}», "
        f"{diag['rows']} өгөгдлийн мөр)",
        f"Танигдсан багана: ДДТД={cols.get('ddtd', 'ОЛДСОНГҮЙ')} · "
        f"Огноо={cols.get('dt', 'ОЛДСОНГҮЙ')} · Дүн={cols.get('amount', 'ОЛДСОНГҮЙ')}",
    ]
    if skip:
        lines.append("Алгассан мөр: " + ", ".join(f"{k} уншигдаагүй {v}" for k, v in skip.items()))
    lines.append("Эхний мөр: " + _sample_text(rows, data_from))
    if skip.get("ддтд"):
        lines.append("💡 ДДТД (33 оронтой) багана Excel дээр ТОО болж хадгалагдвал "
                     "1.52E+31 болж эвдэрдэг — тэр баганыг «Text» болгож дахин экспортлох, "
                     "эсвэл доорх «Багана гараар заах» хэсгээс зөв баганыг сонгоно уу.")
    elif skip.get("огноо") and "dt" in cols:
        lines.append("💡 Огнооны багана танигдсан ч уншигдахгүй байна "
                     "(дэмждэг форматууд: 2026-08-24 06:07:05, 2026.08.24 06:07, "
                     "24/08/2026 06:07).")
    else:
        lines.append("💡 Багана өөр байрлалтай бол «Багана гараар заах» хэсэгт "
                     "ДДТД/Огноо/Дүнгийн баганын үсгийг (ж: B, C, D) бичиж дахин оролдоно уу.")
    return "\n".join(lines)


# ──────────────────────────── Тулгалт ──────────────────────────────────────
#
# 2026-10-13 ДАХИН БИЧСЭН (08-24-ний ТЕГ файл дээр хэмжиж батлав):
#   • ТЕГ-ийн нэг экспорт дотор ГУРВАН касс холилдож ирдэг — QPay (…10014863,
#     UTC), msgbill (…10045952, УБ локал +8ц), Ontime POS терминал (…10002990,
#     UTC). Касс бүр ӨӨРИЙН цагийн шилжилттэй — ганц ерөнхий шилжилт (min abs)
#     ашиглахад msgbill/Ontime-ийн үлдсэн мөрүүд 8 цагийн зөрүүтэй хайгдаж
#     «манайд төлбөр ч алга» гэж худал ангилагдаж байв.
#   • Ontime бол МАНАЙ касс БИШ (манай хадгалсан ДДТД-ийн төгсгөлд хэзээ ч
#     гардаггүй) — түүний мөр манай msgbill баримтыг «хулгайлж» таарах ёсгүй.
#     Манай баримт зөвхөн ӨӨРИЙН кассын (ДДТД-ийн сүүлийн 8 орон) мөртэй таарна.
#   • ±3с хэт нарийн байв: msgbill баримт төлбөрөөс медиан 1.3с, p95 4.8с хоцордог,
#     Ontime баримт 27с хүртэл зөрдөг. Анхдагч 60с + ХАТУУ НЭГ-НЭГЭЭР (хамгийн
#     ойр хос эхэлж) тулгалт — нэг баримт хоёр мөрт тоологдохгүй.

DEFAULT_TOL = 60
TEG_DDTD_LEN = 33           # ТЕГ-ийн ДДТД; MOCK PosAPI-ийнх 40 оронтой байв
REGISTER_LEN = 8            # ДДТД-ийн сүүлийн 8 орон = кассын дугаар
NON_QR_METHODS = ("CASH", "CARD", "TRANSFER", "WALLET")
ACTIVE_STATUSES = ("SENT", "CANCEL_PENDING")


def is_teg_ddtd(v) -> bool:
    s = str(v or "").strip()
    return s.isdigit() and len(s) == TEG_DDTD_LEN


def receipt_register(ddtd, tail: int | None) -> str | None:
    """Манай хадгалсан ДДТД-ийн кассын дугаар (ТЕГ форматтай бол л)."""
    if not tail or not is_teg_ddtd(ddtd):
        return None
    return str(ddtd).strip()[-tail:]


def is_mock_receipt(rec) -> bool:
    """Хуучин MOCK PosAPI баримт (provider NULL/POSAPI, 40 оронтой) — ТЕГ-т хэзээ ч
    очоогүй тул ямар ч ТЕГ мөртэй таарах ёсгүй, цуцлах ч боломжгүй."""
    eid = (getattr(rec, "ebarimt_id", None) or "").strip()
    return bool(eid) and (getattr(rec, "provider", None) or "POSAPI") == "POSAPI" and not is_teg_ddtd(eid)


def _when(t: dict, shift: float = 0.0) -> datetime:
    """ТЕГ мөрийн UTC цаг — мөр бүр өөрийн КАССЫН шилжилтээр."""
    return t["dt"] + timedelta(hours=t.get("shift", shift))


def _pairs(times: list, amounts: list, item_times: list, item_amounts: list,
           tol: float, ok=None) -> list[tuple[float, int, int]]:
    """(|Δt| сек, мөр i, зүйл j) — дүн нь <1₮ зөрүүтэй, цаг нь ±tol дотор.
    Дүнгээр бүлэглэж, цагаар эрэмбэлээд bisect-ээр хайна: 60,000 мөр × хэдэн мянган
    төлбөр дээр ч O(n log n) (өмнөх давхар давталт нь n×m байв)."""
    from bisect import bisect_left, bisect_right
    idx: dict[int, list] = {}
    for j, (tm, am) in enumerate(zip(item_times, item_amounts)):
        if tm is not None and am is not None:
            idx.setdefault(int(round(am)), []).append((tm, j))
    keys = {}
    for k, v in idx.items():
        v.sort(key=lambda x: (x[0], x[1]))
        keys[k] = [x[0] for x in v]
    span = timedelta(seconds=tol)
    out = []
    for i, (tm, am) in enumerate(zip(times, amounts)):
        if tm is None or am is None:
            continue
        r = int(round(am))
        for k in (r - 1, r, r + 1):
            arr = idx.get(k)
            if not arr:
                continue
            ts = keys[k]
            for tj, j in arr[bisect_left(ts, tm - span):bisect_right(ts, tm + span)]:
                if abs(item_amounts[j] - am) >= 1:
                    continue
                if ok is not None and not ok(i, j):
                    continue
                out.append((abs((tj - tm).total_seconds()), i, j))
    return out


def _assign(pairs: list, cost=None) -> list[tuple[int, int, float]]:
    """ХАТУУ НЭГ-НЭГЭЭР: хамгийн ойр (эсвэл cost-оор эрэмбэлсэн) хосоос эхлэн
    оноож, аль хэдийн оноогдсон мөр/зүйлийг дахин ашиглахгүй. Тэнцвэл индексээр
    (тогтвортой үр дүн — нэг файлыг дахин тулгахад ижил хариу)."""
    pairs = sorted(pairs, key=lambda p: ((cost(p) if cost else ()), p[0], p[1], p[2]))
    ui, uj, out = set(), set(), []
    for d, i, j in pairs:
        if i in ui or j in uj:
            continue
        ui.add(i)
        uj.add(j)
        out.append((i, j, d))
    return out


def _compatible(t: dict, rec) -> bool:
    """Энэ манай баримт энэ ТЕГ мөртэй таарч БОЛОХ уу (кассын дугаараар)."""
    g = t.get("group")
    if g is None or g == "*":
        return True                    # касс тодорхойгүй — хуучин зан төлөв
    if t.get("foreign"):
        return False                   # өөр систем (Ontime POS г.м) — манайх биш
    reg = receipt_register(getattr(rec, "ebarimt_id", None), t.get("tail"))
    return reg is None or reg == g     # ДДТД-гүй (FAILED/REVIEW) бол аль ч манай касс


def _cancelled_compatible(t: dict, rec) -> bool:
    """ЦУЦЛАГДСАН манай баримт энэ мөртэй таарч болох уу — ЗӨВХӨН өөрийн кассын
    мөр, кассын дугаар нь ДДТД-ээр БАТЛАГДСАН үед (касс тодорхойгүй бол үгүй)."""
    g = t.get("group")
    if g in (None, "*") or t.get("foreign"):
        return False
    return receipt_register(getattr(rec, "ebarimt_id", None), t.get("tail")) == g


def match_pass(tax: list, ours: list, shift: float, tol: int) -> dict:
    """Тулгалт (цаг ±tol, дүн) — ХАТУУ НЭГ-НЭГЭЭР, хамгийн ойр хосоос эхэлж.

    ДДТД-ЭЭР ТУЛГАДАГГҮЙ — суваг бүр (QPay, msgbill) операторын кодтой билл
    буцаадаг бол ТЕГ такспэерийн ТТД + өөрийн counter-оор ӨӨР ДДТД олгодог.
    Гэхдээ таарсан хос бүрийн хоёр ДДТД-г ХАРЬЦУУЛЖ, сувгаар нь тоолно —
    «манай дугаар ТЕГ-ийнхтэй таарч байна уу» гэдгийг хэмжих цорын ганц зам.

    ЦУЦЛАГДСАН баримт (2026-10-13 хяналт): ТЕГ-ийн файлыг цуцлалтаас ӨМНӨ татсан
    бол манай цуцалсан баримтын мөр файлд ҮЛДСЭН байдаг. Түүнийг хаяхад тэр мөр
    «эзэнгүй» болж өөр төлбөрт тайлагдаж, Ontime мөрийг өөр (хууль ёсны) бэлэн/
    шилжүүлгийн төлбөр рүү түлхэж ХУУРАМЧ давхардал үүсгэж байв («--limit 1 →
    дахин ажиллуулах» урсгал). Одоо идэвхтэй баримтыг эхэлж, дараа нь цуцлагдсаныг
    ҮЛДСЭН өөрийн кассын мөртэй нэг-нэгээр холбоно (идэвхтэйгээс мөр булаахгүй);
    тэдгээрийг matched-д биш cancelled_in_file-д тоолно."""
    for t in tax:
        t["used"] = False
        t.pop("ours", None)
        t.pop("gap", None)
        t.pop("ours_cancelled", None)
        t.setdefault("shift", shift)
    un_ours, cancelled, live, dead = [], 0, [], []
    for o in ours:
        rec, pay = o[0], o[1]
        if pay.paid_at is None:
            continue
        if rec.status == "CANCELLED":
            cancelled += 1          # ТЕГ-т цуцлагдсан тул ШИНЭ файлд байхгүй нь ХЭВИЙН
            if is_teg_ddtd(rec.ebarimt_id) and not is_mock_receipt(rec):
                dead.append(o)      # …гэхдээ ХУУЧИН файлд мөр нь үлдсэн байж болно
            continue
        if is_mock_receipt(rec):
            un_ours.append(_un_row(o, mock=True))
            continue
        live.append(o)
    times = [_when(t, shift) for t in tax]
    pairs = _pairs(times, [t["amount"] for t in tax],
                   [o[1].paid_at for o in live], [float(o[0].amount) for o in live], tol,
                   ok=lambda i, j: _compatible(tax[i], live[j][0]))
    matched, ddtd_equal, by_provider, used_j = 0, 0, {}, set()
    for i, j, _d in _assign(pairs):
        t, o = tax[i], live[j]
        rec, pay = o[0], o[1]
        t["used"], t["ours"] = True, o
        t["gap"] = round((pay.paid_at - times[i]).total_seconds(), 1)
        used_j.add(j)
        matched += 1
        same = rec.ebarimt_id == t["ddtd"]
        ddtd_equal += int(same)
        st = by_provider.setdefault(rec.provider or "POSAPI", {"matched": 0, "equal": 0, "sample": None})
        st["matched"] += 1
        st["equal"] += int(same)
        if st["sample"] is None and not same:
            # Ялгаатай хосын ЖИШЭЭ — хоёр дугаарыг зэрэгцүүлж харуулна
            st["sample"] = {"ours": rec.ebarimt_id or "", "tax": t["ddtd"]}
    un_ours += [_un_row(o) for j, o in enumerate(live) if j not in used_j]
    # Цуцлагдсан баримт — идэвхтэй баримтын ДАРАА, үлдсэн мөртэй л
    free = [i for i, t in enumerate(tax) if not t["used"]]
    in_file = 0
    if free and dead:
        pairs = _pairs([times[i] for i in free], [tax[i]["amount"] for i in free],
                       [o[1].paid_at for o in dead], [float(o[0].amount) for o in dead], tol,
                       ok=lambda a, j: _cancelled_compatible(tax[free[a]], dead[j][0]))
        for a, j, _d in _assign(pairs):
            t, o = tax[free[a]], dead[j]
            t["used"], t["ours"], t["ours_cancelled"] = True, o, True
            t["gap"] = round((o[1].paid_at - times[free[a]]).total_seconds(), 1)
            in_file += 1
    return {"matched": matched, "ddtd_equal": ddtd_equal, "unmatched_ours": un_ours,
            "cancelled": cancelled, "cancelled_in_file": in_file, "by_provider": by_provider}


def _un_row(o, mock: bool = False) -> dict:
    rec, pay, plate, site_name = o[0], o[1], o[2], o[3]
    return {"paid_at": pay.paid_at.isoformat(), "plate": plate, "site_name": site_name,
            "amount": float(rec.amount), "status": rec.status,
            "provider": rec.provider or "POSAPI", "ebarimt_id": rec.ebarimt_id, "mock": mock}


def match_probe(rows: list, probe: list, tol: int, shift: float = 0.0, cost=None) -> list:
    """ТЕГ мөрүүдийг манай ТӨЛБӨРүүдтэй (баримтаас үл хамааран) нэг-нэгээр тулгана."""
    pairs = _pairs([_when(t, shift) for t in rows], [t["amount"] for t in rows],
                   [p["paid_at"] for p in probe], [p["amount"] for p in probe], tol)
    return _assign(pairs, cost)


def clip_to_file(tax: list, ours: list, shift: float, tol: int) -> tuple[list, int]:
    """Манай баримтуудыг ФАЙЛЫН хамрах хугацаанд тайрна (мөр бүрийн өөрийн шилжилтээр).

    Асуулга нь цагийн шилжилт (0 / −TZ) аль нь ч таарахаар ±9 цагийн НӨӨЦТЭЙ
    татдаг. Тэр нөөц дэх баримтуудыг «манайд бий, ТЕГ-д алга» гэж тоолох нь
    ХУДАЛ — файл тэр хугацааг огт хамраагүй байхад «зөрүү» болж харагдана.
    (2026-08-25: 121 «зөрүү»-гийн ихэнх нь яг энэ нөөцийн баримтууд байв.)"""
    times = [_when(t, shift) for t in tax]
    lo, hi = min(times) - timedelta(seconds=tol), max(times) + timedelta(seconds=tol)
    inside = [o for o in ours if o[1].paid_at is not None and lo <= o[1].paid_at <= hi]
    return inside, len(ours) - len(inside)


def file_window(tax: list, tol: int, shift: float = 0.0) -> tuple[datetime, datetime]:
    """Файлын хамрах UTC муж — мөр бүрийн өөрийн шилжилтээр (тайрах/төлбөр татахад)."""
    times = [_when(t, shift) for t in tax]
    return min(times) - timedelta(seconds=tol + 5), max(times) + timedelta(seconds=tol + 5)


def best_shift(tax: list, ours: list, candidates: list[float], tol: int,
               probe: list | None = None) -> dict:
    """Файлын цаг UTC уу, УБ-ын локал уу гэдгийг КАСС тус бүрээр ТААРАЛТААР нь сонгоно.

    • МАНАЙ касс (манай баримтын ДДТД-ийн төгсгөлд гардаг) — манай БАРИМТтай
      хамгийн их таарсан шилжилт.
    • ӨӨР систем (Ontime POS г.м) — манай баримттай таарах ёсгүй тул манай
      ТӨЛБӨРтэй (probe) хамгийн их таарсан шилжилт. Үүнгүйгээр түүний мөрүүд
      давхардлыг тайлах үед 8 цагийн зөрүүтэй хайгдаж байв.

    Шилжилтийг сонгосны ДАРАА манай талыг файлын хамрах хугацаанд тайрч,
    эцсийн тулгалтыг тэр багц дээр хийнэ."""
    # 2026-08-25: ТЕГ-ийн нэг экспорт дотор ЦАГИЙН БҮС ХОЛИЛДОЖ ирдэг нь
    # батлагдав — QPay-ийн касс UTC-ээр, msgbill-ийн касс УБ локал цагаар
    # (+8ц) бичигдсэн. Нэг ерөнхий шилжилт сонговол нөгөө кассын БҮХ мөр
    # «манайд алга» болж 76 хуурамч зөрүү гардаг байв.
    tail = register_tail_len([t["ddtd"] for t in tax])
    own = {receipt_register(o[0].ebarimt_id, tail) for o in ours} - {None}
    groups: dict[str, list] = {}
    for t in tax:
        key = t["ddtd"][-tail:] if tail else "*"
        t["group"], t["tail"] = key, tail
        groups.setdefault(key, []).append(t)
    # Манай баримтын касс файлын НЭГ ч бүлэгтэй давхцахгүй бол «кассын дугаар =
    # ДДТД-ийн төгсгөл» гэсэн таамаг энэ файлд үнэн биш байж магадгүй — тэр үед
    # «өөр систем» гэж ялгахгүй (хуучин зан төлөв: бүх мөр манай баримттай таарч болно)
    split = bool(tail) and bool(own & set(groups))
    for t in tax:
        t["foreign"] = split and t["group"] not in own
    stats = {}
    for key, rows in groups.items():
        best_sh, best_n = candidates[0], -1
        for sh in candidates:
            for x in rows:          # ЗӨВХӨН энэ бүлгийнх — өмнөх бүлгийн
                x["shift"] = sh     # шилжилтийг дарж бичихгүй
            if rows[0]["foreign"]:
                n = len(match_probe(rows, probe or [], tol))
            else:
                # Цуцлагдсан баримтын мөрийг ч тоолно — хэсэгчлэн цуцалсны дараа ижил
                # файлыг дахин тулгахад шилжилт өөрчлөгдөхгүй
                m = match_pass(rows, ours, sh, tol)
                n = m["matched"] + m["cancelled_in_file"]
            if n > best_n:
                best_sh, best_n = sh, n
        for x in rows:
            x["shift"] = best_sh
        # Өөр системийн касс: мөрийн хэд нь манай ТӨЛБӨРтэй таарсан бэ. Бага бол
        # (ж: тухайн ТТД-ийн өөр бизнесийн касс) таарсан цөөн мөр нь санамсаргүй байж болно
        stats[key] = {"rows": len(rows), "matched": max(best_n, 0), "shift": best_sh,
                      "foreign": rows[0]["foreign"]}
    shift = min((t["shift"] for t in tax), key=lambda v: abs(v)) if tax else candidates[0]
    inside, outside = clip_to_file(tax, ours, shift, tol)
    res = match_pass(tax, inside, shift, tol)     # сонгосон шилжилтийг дахин тавина
    return {**res, "shift": shift, "ours_in_window": len(inside), "outside_window": outside,
            "inside": inside, "tail": tail,
            "own_registers": sorted(k for k in groups if not groups[k][0]["foreign"]),
            "foreign_registers": sorted(k for k in groups if groups[k][0]["foreign"]),
            "group_stats": stats,
            "group_shifts": {k: rows[0]["shift"] for k, rows in groups.items()}}


def pos_tail_len(ids: list[str]) -> int | None:
    """ДДТД-ийн төгсгөл дэх КАССЫН дугаарын уртыг өгөгдлөөс олно.

    Шалгуур: бүлэг цөөн (≤6) БӨГӨӨД бүлэг бүрд дунджаар 4+ мөр ноогдоно.
    Эс бөгөөс дарааллын дугаарыг «касс» гэж андуурч мөр бүрийг тусад нь
    бүлэглэнэ. Тохирох урт олдоогүй бол None — бүх мөр НЭГ бүлэг."""
    ids = [i for i in ids if i]
    if len(ids) < 8:
        return None
    found = None
    for ln in range(6, 13):
        n = len({i[-ln:] for i in ids})
        if n <= 6 and n * 4 <= len(ids):
            found = ln
    return found


def register_tail_len(ids: list[str]) -> int | None:
    """Кассын дугаарын урт: бүгд ТЕГ-ийн 33 оронтой ДДТД бол ШУУД сүүлийн 8 орон
    (ТЕГ-ийн дугаарлалт); бусад форматад л pos_tail_len-ээр таамаглана.

    2026-10-13 хяналт: өмнө нь pos_tail_len-ийг ЭХЭЛЖ ажиллуулдаг байсан. Ontime-ийн
    ДДТД-ийн төгсгөлөөс 9 дэх орон зөвхөн {1,3,5,7,9} утга авдаг тул Ontime мөр
    давамгайлсан файлд (24+ Ontime мөр, эсвэл 63 Ontime + 1 msgbill) 9 оронтой
    «касс» сонгогдож, манай msgbill мөр «өөр систем» болж ангилагдах эрсдэлтэй байв."""
    ids = [i for i in ids if i]
    if ids and all(is_teg_ddtd(i) for i in ids):
        return REGISTER_LEN
    return pos_tail_len(ids)


def pos_groups(ids: list[str], tail: int | None = None) -> dict:
    """ДДТД-үүдийг кассын дугаараар бүлэглэнэ (харуулах зориулалттай).

    tail: бүх файлаас тооцсон урт. Дэд багц (ж: таараагүй мөрүүд) дээр дахин
    тооцвол өөр урт гарч, харьцуулах боломжгүй болно."""
    ids = [i for i in ids if i]
    if not ids:
        return {}
    ln = tail or pos_tail_len(ids)
    return dict(Counter(i[-ln:] for i in ids)) if ln else {"(нэг бүлэг)": len(ids)}


# «ТЕГ-д бий, манайд алга» мөрийн ШАЛТГААНЫГ манай ТӨЛБӨРИЙН бүртгэлээр тайлах.
# Баримтын бүртгэл байхгүй ч ТӨЛБӨР нь байвал — баримт үүссэн боловч манайд
# хадгалагдаагүй (эсвэл давхар үүссэн) гэсэн үг. Энэ ялгааг гараар хөөх нь
# хэдэн цагийн ажил байсан.
VERDICTS = {
    "DUPLICATE": "ДАВХАР БАРИМТ — өөр кассын (POS терминал) баримттай төлбөрт манай msgbill "
                 "баримт бас үүссэн → манайхыг цуцална",
    "DUPLICATE_CANCELLED": "ДАВХАРДАЛ ЦУЦЛАГДСАН — манай баримтыг аль хэдийн цуцалсан",
    "DUPLICATE_NOT_CANCELLABLE": "Өөр касс + манай баримт (цуцлахгүй) — QR/QPay, msgbill биш "
                                 "г.м нөхцөл хангаагүй, гараар шалгана",
    "OURS_MOCK": "Өөр касс, манайх MOCK — манай баримт хуурамч (ТЕГ-д очоогүй) байсан, давхардал биш",
    "EXTRA_SAME_REGISTER": "Манай касс дээр 2 дахь баримт — нэг төлбөрт манайд бүртгэлгүй өөр "
                           "баримт, сувгийн талд шалгана",
    "PAYMENT_NO_RECEIPT": "БАРИМТГҮЙ ТӨЛБӨР — төлбөр авсан ч баримтын бүртгэл алга",
    "RECEIPT_NOT_MATCHED": "Баримт бий ч цаг/дүнгээр таарсангүй",
    "OTHER_SCOPE": "ӨӨР ЗОГСООЛ — сонгосон түрээслэгчийн шүүлтээс гадуур",
    "NO_PAYMENT": "Манайд төлбөр ч алга — өөр систем/POS",
}


def cancel_block_reason(p: dict) -> str | None:
    """Энэ төлбөрийн МАНАЙ баримтыг «POS-ийн давхардал» гэж цуцалж БОЛОХ уу.
    None = болно; эс бөгөөс шалтгаан. API, CLI, арын job БҮГД энэ нэг дүрмээр.

    Дүрэм (2026-10-13): Ontime POS зөвхөн БЭЛЭН/КАРТ (касс дээрх) төлбөрт баримт
    хэвлэдэг. QR/QPay төлбөрийн «давхардал» = цаг+дүнгийн санамсаргүй тааралт.
    Цуцлах боломжтой нь зөвхөн манай ИЛГЭЭГДСЭН msgbill баримт (provider_ref-тэй) —
    MOCK 40 оронтой / provider NULL баримт ТЕГ-т очоогүй тул «цуцлах» юм алга."""
    m = (p.get("method") or "").upper()
    if m not in NON_QR_METHODS:
        return f"{m or '?'} төлбөр — POS терминал баримт хэвлэдэггүй (QR/QPay) тул давхардал гэж үзэхгүй"
    act = p.get("active") or []
    if not act:
        return "Цуцлах идэвхтэй (SENT) баримт алга"
    bad = sorted({a.get("provider") or "POSAPI" for a in act if a.get("provider") != "MSGBILL"})
    if bad:
        return f"манай баримт msgbill биш ({'/'.join(bad)}) — цуцлахгүй"
    if any(not a.get("provider_ref") for a in act):
        return "msgbill баримтын ID (provider_ref) алга — цуцалж чадахгүй"
    if any(not is_teg_ddtd(a.get("ebarimt_id")) for a in act):
        return "манай баримтын ДДТД ТЕГ-ийн формат биш (MOCK?) — цуцлахгүй"
    prim = p.get("primary") or {}
    if prim.get("id") not in {a.get("id") for a in act}:
        return "төлбөрийн үндсэн баримт идэвхгүй — гараар шалгана"
    if abs(sum(float(a.get("amount") or 0) for a in act) - float(p.get("amount") or 0)) >= 1:
        return "манай баримтын нийт дүн төлбөрийн дүнтэй таарахгүй — гараар шалгана"
    return None


def _foreign_verdict(p: dict) -> tuple[str, str | None]:
    reason = cancel_block_reason(p)
    if reason is None:
        return "DUPLICATE", None
    prim = p.get("primary") or {}
    if prim.get("status") == "CANCELLED" and p.get("cancelled_msgbill"):
        return "DUPLICATE_CANCELLED", None
    if prim.get("mock"):
        return "OURS_MOCK", None
    return "DUPLICATE_NOT_CANCELLABLE", reason


def _qr_rank(p: dict) -> int:
    """0 = касс дээрх (бэлэн/карт/данс/хэтэвч) төлбөр — POS терминал баримт хэвлэдэг;
    1 = QR/QPay — POS терминал баримт хэвлэдэггүй тул тааралт нь санамсаргүй."""
    return 0 if (p.get("method") or "").upper() in NON_QR_METHODS else 1


def foreign_register_sites(left: list, probe: list, tol: int, shift: float = 0.0) -> dict:
    """Өөр системийн касс (Ontime POS г.м) бүр АЛЬ ЗОГСООЛЫНХ вэ — файлаас тодорхойлно.

    POS терминал бол НЭГ газар байрладаг физик төхөөрөмж (08-24: …10002990 = Хангарьд,
    63/63). ТЕГ-ийн экспорт ТТД-ээр гардаг тул нэг ТТД-ийн БУСАД зогсоолын бэлэн/
    шилжүүлгийн төлбөр ±60с дотор ижил дүнтэй таарч болно (2026-10-13 хяналт:
    08-20…08-26-нд Хангарьдын 339 төлбөрийн 8-д нь өөр зогсоолд ийм нэр дэвшигч
    байсан). Кассын мөрүүдийн QR биш төлбөртэй таарсан тааралтын ОЛОНХ (>50%) нь
    унасан зогсоолыг тэр кассын зогсоол гэж үзнэ; олонх байхгүй бол site_id=None
    (тодорхойгүй — тэр кассын нэг ч мөрийг цуцлахгүй)."""
    rows = [t for t in left if t.get("foreign")]
    if not rows:
        return {}
    hits = match_probe(rows, probe, tol, shift, cost=lambda pr: (_qr_rank(probe[pr[2]]),))
    by_reg: dict[str, Counter] = {}
    names = {}
    for i, j, _d in hits:
        p = probe[j]
        if _qr_rank(p):
            continue
        by_reg.setdefault(rows[i].get("group") or "", Counter())[p.get("site_id")] += 1
        names[p.get("site_id")] = p.get("site_name") or p.get("site_code") or ""
    out = {}
    for reg, c in by_reg.items():
        (sid, n), total = c.most_common(1)[0], sum(c.values())
        major = n * 2 > total
        out[reg] = {"site_id": sid if major else None, "site_name": names.get(sid, "") if major else "",
                    "share": round(n / total, 3), "hits": total, "sites": len(c)}
    return out


def explain_unmatched_tax(left: list, probe: list, shift: float, tol: int,
                          matched_pay_ids: set, scope_ids: set | None,
                          register_sites: dict | None = None) -> list:
    """Тохироогүй ТЕГ мөр бүрийг манай ТӨЛБӨРүүдтэй (баримттай эсэхээс үл хамааран)
    цаг+дүнгээр, мөр бүрийн ӨӨРИЙН кассын шилжилтээр, НЭГ-НЭГЭЭР тулгаж ангилна.
    probe: {id, paid_at, amount, site_id, method, receipts, primary, active, ...}.

    ӨӨР кассын мөр ба МАНАЙ кассын мөрийг ТУСАД НЬ (тус бүр нэг-нэгээр) тулгана —
    нэг борлуулалт Ontime-ийн НЭГ, манай кассын НЭГ мөртэй байж болно. 2026-10-13
    хяналт: хамтад нь тулгахад манай кассын үлдсэн мөр (ж: цуцалсан баримтын хуучин
    мөр) Ontime мөрийн жинхэнэ төлбөрийг «булааж», Ontime мөрийг ±60с доторх өөр
    хууль ёсны бэлэн/шилжүүлгийн төлбөр рүү түлхэж ХУУРАМЧ давхардал үүсгэдэг байв.
    Одоо Ontime мөрийн хос зөвхөн ТӨЛБӨРүүдээс (арга, зогсоол) хамаарна — манай
    баримтын төлөв (цуцалсан эсэх) өөрчлөгдөхөд хос нь өөрчлөгдөхгүй.

    • ӨӨР кассын мөр (Ontime POS) → тухайн төлбөрт манай баримт бас байвал
      ДАВХАРДАЛ; тэр кассын зогсоолын (foreign_register_sites) бэлэн/карт төлбөр
      түрүүлж сонгогдоно; өөр зогсоолынх бол цуцлахгүй (гараар шалгана).
    • МАНАЙ кассын мөр → баримтгүй/таараагүй төлбөр түрүүлж сонгогдоно (завгүй
      цагт ижил дүнтэй хоёр төлбөр байхад хуурамч «давхардал» гаргахгүй)."""
    if register_sites is None:
        register_sites = foreign_register_sites(left, probe, tol, shift)
    fi = [i for i, t in enumerate(left) if t.get("foreign")]
    oi = [i for i, t in enumerate(left) if not t.get("foreign")]

    def reg_site(t):
        return (register_sites.get(t.get("group") or "") or {}).get("site_id")

    def f_cost(pr):
        _d, a, j = pr
        sid = reg_site(left[fi[a]])
        return (0 if sid is None or probe[j].get("site_id") == sid else 1, _qr_rank(probe[j]))

    times = [_when(t, shift) for t in left]
    hit = {}
    for a, j, d in match_probe([left[i] for i in fi], probe, tol, shift, f_cost):
        hit[fi[a]] = (j, d)
    taken = {j for j, _d in hit.values()}

    def o_cost(pr):
        # Ontime мөрөөр тайлагдсан төлбөрийг ХОЖУУ сонгоно (нэг борлуулалтад 2 тайлбар
        # хамгийн сүүлд) — Ontime-ийн хос үүнээс ҮЛ хамаарна (дээр аль хэдийн оноогдсон)
        j = pr[2]
        return (1 if j in taken else 0, 1 if probe[j]["id"] in matched_pay_ids else 0)

    for a, j, d in match_probe([left[i] for i in oi], probe, tol, shift, o_cost):
        hit[oi[a]] = (j, d)
    out = []
    for i, t in enumerate(left):
        row = {"dt": t["dt"].isoformat(), "dt_utc": times[i].isoformat(),
               "amount": t["amount"], "src": t["src"], "ddtd": t["ddtd"],
               "register": t.get("group") if t.get("group") not in (None, "*") else "",
               "foreign": bool(t.get("foreign")), "verdict": "NO_PAYMENT", "plate": "",
               "site_name": "", "method": "", "our_ddtd": "", "payment_id": None,
               "gap_s": None, "note": None}
        if i in hit:
            j, _d = hit[i]
            p = probe[j]
            note = None
            if scope_ids is not None and p["site_id"] not in scope_ids:
                v = "OTHER_SCOPE"
            elif not p["receipts"]:
                v = "PAYMENT_NO_RECEIPT"
            elif t.get("foreign"):
                v, note = _foreign_verdict(p)
                rs = register_sites.get(t.get("group") or "")
                if v == "DUPLICATE" and rs is not None and rs["site_id"] is None:
                    v, note = "DUPLICATE_NOT_CANCELLABLE", (
                        f"касс …{t.get('group')} аль зогсоолынх нь тодорхойгүй "
                        f"({rs['sites']} зогсоолд тарсан) — гараар шалгана")
                elif v == "DUPLICATE" and rs is not None and p.get("site_id") != rs["site_id"]:
                    v, note = "DUPLICATE_NOT_CANCELLABLE", (
                        f"өөр зогсоол — касс …{t.get('group')} нь «{rs['site_name']}»-ийнх")
            elif p["id"] in matched_pay_ids:
                v = "EXTRA_SAME_REGISTER"
            else:
                v = "RECEIPT_NOT_MATCHED"
            row.update({"verdict": v, "plate": p.get("plate") or "",
                        "site_name": p.get("site_name") or "",
                        "site_code": p.get("site_code") or "",
                        "method": f'{p.get("provider")}/{p.get("method")}',
                        "our_ddtd": ", ".join(x for x in p["receipts"] if x)[:80],
                        # Бөөнөөр цуцлахад хэрэгтэй — UI энэ ID-аар хүсэлт илгээнэ
                        "payment_id": p["id"],
                        "gap_s": round((p["paid_at"] - times[i]).total_seconds(), 1),
                        "note": note})
        out.append(row)
    return out


def duplicate_list(explained: list, probe: list, matched_pay_ids: set, tz_hours: int) -> list:
    """Цуцлах ЖАГСААЛТ — DUPLICATE мөр бүрийн БҮТЭН мэдээлэл (дэлгэцийн 200-ийн
    хязгааргүй). Нэг төлбөр нэг л удаа (нэг-нэгээр тулгасан тул давхцахгүй ч хамгаалж)."""
    by_id = {p["id"]: p for p in probe}
    loc = timedelta(hours=tz_hours)
    out, seen = [], set()
    for r in explained:
        if r["verdict"] != "DUPLICATE" or r["payment_id"] in seen:
            continue
        seen.add(r["payment_id"])
        p = by_id[r["payment_id"]]
        act = p.get("active") or []
        out.append({
            "payment_id": p["id"], "site_name": p.get("site_name") or "",
            "site_code": p.get("site_code") or "", "site_id": p.get("site_id"),
            "plate": p.get("plate") or "", "method": (p.get("method") or "").upper(),
            "amount": float(p["amount"]),
            "paid_at": p["paid_at"].isoformat(),
            "paid_at_ub": (p["paid_at"] + loc).strftime("%Y-%m-%d %H:%M:%S"),
            "our_ddtd": ", ".join(a.get("ebarimt_id") or "" for a in act),
            "our_provider_ref": ", ".join(a.get("provider_ref") or "" for a in act),
            "our_status": "/".join(sorted({a.get("status") or "" for a in act})),
            "teg_ddtd": r["ddtd"], "teg_register": r.get("register") or "",
            "teg_vendor": r.get("src") or "",
            "teg_time_ub": (datetime.fromisoformat(r["dt_utc"]) + loc).strftime("%Y-%m-%d %H:%M:%S"),
            "gap_s": r.get("gap_s"),
            # Манай msgbill баримт ч энэ файлд (өөрийн кассаар) таарсан уу —
            # нэг борлуулалт ТЕГ-т ХОЁР удаа бүртгэгдсэний шууд нотолгоо
            "ours_in_file": p["id"] in matched_pay_ids,
        })
    return out


def duplicate_summary(dups: list) -> dict:
    """Зогсоол/арга/өдөр/кассаар тоо ба ₮ (CLI ба UI-ийн хураангуй)."""
    def agg(key):
        d: dict[str, dict] = {}
        for x in dups:
            k = str(key(x))
            s = d.setdefault(k, {"count": 0, "amount": 0.0})
            s["count"] += 1
            s["amount"] += x["amount"]
        return dict(sorted(d.items()))
    return {"count": len(dups), "amount": sum(x["amount"] for x in dups),
            "ours_in_file": sum(1 for x in dups if x["ours_in_file"]),
            "by_site": agg(lambda x: x["site_name"] or "?"),
            "by_method": agg(lambda x: x["method"] or "?"),
            "by_day": agg(lambda x: x["paid_at_ub"][:10]),
            "by_register": agg(lambda x: x["teg_register"] or "?")}


def reconcile_excel(tax: list, ours: list, un_ours: list, tol: int, tz: int,
                    title: str = "", shift: float = 0, explained: dict | None = None,
                    duplicates: list | None = None):
    """Тулгалтын нэгтгэсэн xlsx: (1) ТЕГ+манай мөр зэрэгцээ, (2) манайд бий/ТЕГ-д алга,
    (3) давхардал (цуцлах жагсаалт), (4) дүгнэлт. Огноог ЛОКАЛ (УБ) цагаар харуулна.

    ТЕГ-ийн огноог мөр бүрийн ӨӨРИЙН кассын шилжилтээр UTC болгож, дараа нь
    локал болгоно. 2026-10-13 хүртэл ганц ерөнхий шилжилт ашигладаг байсан тул
    msgbill (УБ локал) мөрүүд энэ файлд +8ц зөрүүтэй (ХОЁР УДАА шилжиж) харагддаг байв."""
    from urllib.parse import quote
    import openpyxl as _xl
    from fastapi.responses import StreamingResponse
    from openpyxl.styles import Font, PatternFill

    wb = _xl.Workbook()
    bold = Font(bold=True)
    warn = PatternFill("solid", fgColor="FFF2CC")
    loc = timedelta(hours=tz)

    ws = wb.active
    ws.title = "Тулгалт"
    ws.append(["ТЕГ огноо (УБ цаг)", "Дүн ₮", "ТЕГ ДДТД", "ТЕГ эх сурвалж",
               "Тулгалт", "Машины дугаар", "Зогсоол", "Манай суваг", "Манай ДДТД",
               "Сугалаа", "Манай төлөв", "Төлсөн (УБ цаг)", "Шалтгаан (таараагүй бол)"])
    for c in ws[1]:
        c.font = bold
    for t in sorted(tax, key=lambda x: _when(x, shift)):
        o = t.get("ours")
        row = [(_when(t, shift) + loc).strftime("%Y-%m-%d %H:%M:%S"), t["amount"], t["ddtd"], t["src"]]
        if o:
            rec, pay, plate, site_name = o
            row += ["ТААРСАН (манайх цуцлагдсан)" if t.get("ours_cancelled") else "ТААРСАН",
                    plate or "", site_name or "", rec.provider or "POSAPI",
                    rec.ebarimt_id or "", rec.lottery_code or "", rec.status,
                    (pay.paid_at + loc).strftime("%Y-%m-%d %H:%M:%S") if pay.paid_at else ""]
        else:
            e = explained.get(t["ddtd"]) if explained else None
            why = VERDICTS.get((e or {}).get("verdict", ""), "")
            if (e or {}).get("note"):
                why += f" ({e['note']})"
            row += ["МАНАЙД АЛГА", (e or {}).get("plate", ""), (e or {}).get("site_name", ""),
                    (e or {}).get("method", ""), (e or {}).get("our_ddtd", ""), "", "", "", why]
        ws.append(row)
        if not o:
            for c in ws[ws.max_row]:
                c.fill = warn

    ws2 = wb.create_sheet("Манайд бий - ТЕГ-д алга")
    ws2.append(["Төлсөн (УБ цаг)", "Машины дугаар", "Зогсоол", "Дүн ₮", "Суваг",
                "Манай төлөв", "Манай ДДТД", "Тайлбар"])
    for c in ws2[1]:
        c.font = bold
    for r in sorted(un_ours, key=lambda x: x["paid_at"]):
        note = ("MOCK/амжилтгүй баримт" if r["status"] != "SENT" or r.get("mock") else
                "Өөр ТТД дээр бүртгэгдсэн байж болно" if r["provider"] == "QPAY" else "")
        dt = datetime.fromisoformat(r["paid_at"]) + loc
        ws2.append([dt.strftime("%Y-%m-%d %H:%M:%S"), r["plate"] or "", r.get("site_name") or "",
                    r["amount"], r["provider"], r["status"], r["ebarimt_id"] or "", note])

    if duplicates:
        wsd = wb.create_sheet("Давхардал (цуцлах)")
        wsd.append(["Төлсөн (УБ цаг)", "Зогсоол", "Машины дугаар", "Арга", "Дүн ₮",
                    "Манай ДДТД (msgbill)", "msgbill ID", "ТЕГ ДДТД (өөр касс)", "Касс",
                    "ТЕГ цаг (УБ)", "Зөрүү (с)", "Манай баримт файлд таарсан", "payment_id"])
        for c in wsd[1]:
            c.font = bold
        for x in duplicates:
            wsd.append([x["paid_at_ub"], x["site_name"], x["plate"], x["method"], x["amount"],
                        x["our_ddtd"], x["our_provider_ref"], x["teg_ddtd"], x["teg_register"],
                        x["teg_time_ub"], x["gap_s"], "тийм" if x["ours_in_file"] else "үгүй",
                        x["payment_id"]])
        for col, w in zip("ABCDEFGHIJKLM", (20, 16, 12, 9, 9, 36, 38, 36, 10, 20, 9, 12, 38)):
            wsd.column_dimensions[col].width = w

    ws3 = wb.create_sheet("Дүгнэлт")
    matched = sum(1 for t in tax if t.get("ours") and not t.get("ours_cancelled"))
    dead = sum(1 for t in tax if t.get("ours_cancelled"))
    lines = [
        ("Тулгалт", title or "—"),
        ("ТЕГ файлын баримт", len(tax)),
        ("Манай баримт (тухайн хугацаанд)", len(ours)),
        ("Таарсан (цаг ±%dс + дүн)" % tol, matched),
        ("Файлд бий, манайд ЦУЦЛАГДСАН (файл цуцлалтаас өмнө татагдсан)", dead),
        ("ТЕГ-д бий, манайд алга", len(tax) - matched - dead),
        ("Манайд бий, ТЕГ-д алга", len(un_ours)),
        ("Давхардал (манай msgbill баримтыг цуцлах)", len(duplicates or [])),
        ("Давхардлын дүн ₮", sum(x["amount"] for x in duplicates or [])),
        ("", ""),
        ("ТЕГ эх сурвалжаар:", ""),
        *[(f"  {k or '(хоосон)'}", v) for k, v in Counter(t["src"] for t in tax).items()],
        ("", ""),
        ("ТАЙЛБАР: ДДТД хоорондоо таарахгүй нь ХЭВИЙН — QPay/msgbill операторын кодтой", ""),
        ("билл буцаадаг бол ТЕГ такспэерийн ТТД + өөрийн дугаарлалтаар бүртгэдэг.", ""),
        ("Тулгалтыг цаг + дүнгээр, касс бүрийн өөрийн цагийн бүсээр хийсэн.", ""),
    ]
    for a, b in lines:
        ws3.append([a, b])
    ws3["A1"].font = bold
    for wsx in (ws, ws2):
        for col, w in zip("ABCDEFGHIJKLM", (20, 10, 36, 16, 13, 14, 16, 11, 36, 12, 10, 20, 46)):
            wsx.column_dimensions[col].width = w
    ws3.column_dimensions["A"].width = 70

    buf = _io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    day = (min(_when(t, shift) for t in tax) + loc).strftime("%Y%m%d") if tax else "x"
    fname = f"ebarimt-tulgalt-{day}.xlsx"
    return StreamingResponse(
        buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={fname}; filename*=UTF-8''{quote(fname)}"})
