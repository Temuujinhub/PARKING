"""ТЕГ тулгалтын файл задлагч — «тулгах дархад алдаа гарлаа» гомдлын регресс.

2026-08-25: 08-24-ний 472 мөртэй экспорт тулгагдахгүй байв. Хуучин код багануудыг
ТОГТМОЛ индексээр (B=ДДТД, C=огноо, D=дүн) уншиж, аль нэг нь зөрөхөд бүх мөрийг
чимээгүй алгасаад «баримт олдсонгүй» гэсэн ганц мөр буцаадаг байсан тул хэрэглэгч
ЮУНЫ алдаа болохыг мэдэх аргагүй байв.

Энд шалгах зүйлс:
  1. Багана автоматаар танигдана (толгойтой ч, толгойгүй ч, дараалал зөрсөн ч).
  2. Огноо/дүнгийн ӨӨР форматууд уншигдана (2026.08.24, 24/08/2026, «8,000.00»).
  3. Уншигдахгүй үед алдаа нь ЯГ юу болсныг (багана, алгассан шалтгаан, мөрийн
     жишээ) хэлнэ — .xls/PDF зэрэг форматын алдааг тусад нь ялгана.
  4. Файлын цаг UTC/локал алийг нь ч тулгалтын тоогоор автоматаар сонгоно.
"""
import io
from datetime import datetime, timedelta

import openpyxl
import pytest
from fastapi import HTTPException

from app.routers.vat_recon import (
    as_ddtd, as_dt, as_num, best_shift, explain_unmatched_tax, parse_tax_export, pos_groups,
)

BASE = datetime(2026, 8, 24, 6, 0, 0)
HDR = ["ТТД", "ДДТД", "Огноо", "Нийт дүн", "", "НӨАТ", "Дүн", "Төрөл",
       "Тайлбар", "Салбар", "Төлөв", "Дүүрэг"]


def _xlsx(rows, header=None):
    wb = openpyxl.Workbook()
    ws = wb.active
    if header:
        ws.append(header)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _row(i, ddtd=None, dt=None, amount=8000):
    """Моннисын бодит экспортын бүтэц (A=ТТД … L=дүүрэг)."""
    t = BASE + timedelta(seconds=i * 47)
    return ["7524322", ddtd if ddtd is not None else "015200020090000972600%09d0990" % i,
            dt if dt is not None else t.strftime("%Y-%m-%d %H:%M:%S") + ".0",
            amount, None, 727.27, 7272.73, "POS", "", "10002990", "Ontime", "Баянзурх"]


@pytest.mark.parametrize("header", [HDR, None], ids=["толгойтой", "толгойгүй"])
def test_columns_detected(header):
    tax, diag = parse_tax_export("teg.xlsx", _xlsx([_row(i) for i in range(5)], header))
    assert len(tax) == 5
    assert diag["columns"]["ddtd"] == "B"
    assert diag["columns"]["dt"] == "C"
    assert diag["columns"]["amount"] == "D"
    assert tax[0]["amount"] == 8000


@pytest.mark.parametrize("dt", ["2026.08.24 06:07:05", "24/08/2026 06:07:05",
                                "2026-08-24T06:07:05", "2026-08-24 06:07:05.0"])
def test_date_formats(dt):
    tax, _ = parse_tax_export("teg.xlsx", _xlsx([_row(i, dt=dt) for i in range(3)], HDR))
    assert len(tax) == 3
    assert tax[0]["dt"] == datetime(2026, 8, 24, 6, 7, 5)


@pytest.mark.parametrize("amount,expect", [("8,000.00", 8000.0), ("8000₮", 8000.0),
                                           (1500, 1500.0), ("(1500)", -1500.0)])
def test_amount_formats(amount, expect):
    tax, _ = parse_tax_export("teg.xlsx", _xlsx([_row(i, amount=amount) for i in range(3)], HDR))
    assert tax[0]["amount"] == expect


def test_shuffled_columns_by_header():
    """Багана байрандаа биш ч толгойн нэрээр танина (портал хувилбар өөрчлөгдвөл)."""
    rows = [["2026-08-24 06:0%d:00" % i, 1500, "015200020090000972600%09d0990" % i]
            for i in range(4)]
    tax, diag = parse_tax_export("teg.xlsx", _xlsx(rows, ["Огноо", "Нийт дүн", "ДДТД"]))
    assert len(tax) == 4
    assert diag["columns"] == {"dt": "A", "amount": "B", "ddtd": "C"}


def test_manual_column_override():
    """Автомат таних алдаатай бол хэрэглэгч баганыг гараар зааж чадна."""
    rows = [_row(i) for i in range(3)]
    tax, diag = parse_tax_export("teg.xlsx", _xlsx(rows, HDR),
                                 {"amount": "G", "ddtd": "B", "dt": "C"})
    assert diag["columns"]["amount"] == "G"
    assert tax[0]["amount"] == 7272.73


def test_ddtd_as_number_warns():
    """33 оронтой ДДТД-г Excel ТОО болгосон бол уншина, гэхдээ анхааруулна."""
    rows = [_row(i, ddtd=float("15200020090000972600%09d0990" % i)) for i in range(3)]
    tax, diag = parse_tax_export("teg.xlsx", _xlsx(rows, HDR))
    assert len(tax) == 3
    assert any("ТОО болж" in w for w in diag["warnings"])


def test_old_xls_message():
    with pytest.raises(HTTPException) as e:
        parse_tax_export("teg.xls", b"\xd0\xcf\x11\xe0" + b"\x00" * 64)
    assert ".xls" in e.value.detail and "xlsx" in e.value.detail


def test_pdf_message():
    with pytest.raises(HTTPException) as e:
        parse_tax_export("teg.pdf", b"%PDF-1.4 ...")
    assert "PDF" in e.value.detail


def test_csv_supported():
    csv = "ТТД,ДДТД,Огноо,Нийт дүн\n" + "\n".join(
        "7524322,015200020090000972600%09d0990,2026-08-24 06:0%d:00,1500" % (i, i)
        for i in range(3))
    tax, diag = parse_tax_export("teg.csv", csv.encode())
    assert len(tax) == 3 and diag["kind"] == "csv"


def test_no_rows_error_is_diagnostic():
    """Нэг ч мөр таарахгүй бол алдаа нь оношилгоотой байх ёстой."""
    rows = [["7524322", "12345", "2026-24-08", "abc"] for _ in range(3)]
    with pytest.raises(HTTPException) as e:
        parse_tax_export("Easy0824.xlsx", _xlsx(rows))
    msg = e.value.detail
    assert "Easy0824.xlsx" in msg          # аль файл
    assert "3 өгөгдлийн мөр" in msg        # хэдэн мөр уншсан
    assert "Танигдсан багана" in msg       # аль баганыг ашигласан
    assert "Эхний мөр" in msg              # ЮУ уншсаны жишээ
    assert "7524322" in msg


def test_empty_file():
    with pytest.raises(HTTPException) as e:
        parse_tax_export("teg.xlsx", b"")
    assert "хоосон" in e.value.detail


# ─────────────────────────── тулгалт ────────────────────────────────────────

class _Rec:
    status, provider, ebarimt_id, lottery_code = "SENT", "QPAY", "X", "L"

    def __init__(self, amount):
        self.amount = amount


class _Pay:
    def __init__(self, paid_at):
        self.paid_at = paid_at


@pytest.mark.parametrize("file_shift,expect", [(0, 0.0), (8, -8.0)])
def test_tz_shift_auto(file_shift, expect):
    """Экспорт UTC-ээр ч, УБ-ын локал цагаар ч ирдэг — таарсан тоогоор нь сонгоно.
    (Урьд нь tz_shift=0 тогтмол байсан тул локал файл 0 таарч «бүгд зөрүүтэй»
    гэж харагддаг байв.)"""
    ours = [(_Rec(1500), _Pay(BASE + timedelta(minutes=i)), "1234УБА", "Хангарьд")
            for i in range(5)]
    rows = [["7524322", "015200020090000972600%09d0990" % i,
             (BASE + timedelta(minutes=i, hours=file_shift)).strftime("%Y-%m-%d %H:%M:%S"), 1500]
            for i in range(5)]
    tax, _ = parse_tax_export("teg.xlsx", _xlsx(rows, ["ТТД", "ДДТД", "Огноо", "Нийт дүн"]))
    r = best_shift(tax, ours, [0.0, -8.0], 3)
    assert (r["shift"], r["matched"], r["unmatched_ours"]) == (expect, 5, [])


def test_cancelled_receipts_not_matched():
    """Цуцлагдсан баримт ТЕГ-д байхгүй — тулгалтад оруулж болохгүй."""
    rec = _Rec(1500)
    rec.status = "CANCELLED"
    ours = [(rec, _Pay(BASE), "1234УБА", "Хангарьд")]
    rows = [["7524322", "0152000200900009726000000000000990",
             BASE.strftime("%Y-%m-%d %H:%M:%S"), 1500]]
    tax, _ = parse_tax_export("teg.xlsx", _xlsx(rows, ["ТТД", "ДДТД", "Огноо", "Нийт дүн"]))
    r = best_shift(tax, ours, [0.0], 3)
    assert r["matched"] == 0 and r["unmatched_ours"] == [] and r["cancelled"] == 1


def test_cell_helpers():
    assert as_ddtd("0152000200900009726000000000000990").startswith("0152")
    assert as_ddtd("12345") is None            # богино дугаар ДДТД биш
    assert as_dt("буруу") is None
    assert as_num("") is None and as_num("1 500") == 1500.0


def test_outside_file_window_not_counted_as_diff():
    """Асуулга ±9ц нөөцтэй татдаг тул файлын хамраагүй хугацааны баримтууд
    «манайд бий, ТЕГ-д алга» болж ХУДАЛ зөрүү үүсгэдэг байв (2026-08-25)."""
    ours = [(_Rec(1500), _Pay(BASE + timedelta(minutes=i)), "1234УБА", "Хангарьд")
            for i in range(3)]
    ours += [(_Rec(1500), _Pay(BASE - timedelta(hours=6)), "9999УБА", "Хангарьд"),
             (_Rec(2000), _Pay(BASE + timedelta(hours=6)), "8888УБА", "Хангарьд")]
    rows = [["7524322", "015200020090000972600%09d0990" % i,
             (BASE + timedelta(minutes=i)).strftime("%Y-%m-%d %H:%M:%S"), 1500]
            for i in range(3)]
    tax, _ = parse_tax_export("teg.xlsx", _xlsx(rows, ["ТТД", "ДДТД", "Огноо", "Нийт дүн"]))
    r = best_shift(tax, ours, [0.0], 3)
    assert r["matched"] == 3
    assert r["unmatched_ours"] == []      # 6 цагийн зайны 2 баримт зөрүү БИШ
    assert r["outside_window"] == 2
    assert r["ours_in_window"] == 3


def test_ddtd_compared_per_provider():
    """Таарсан хос бүрийн ДДТД-г сувгаар нь харьцуулна (ТЕГ-ийнхтэй ижил үү)."""
    same = _Rec(1500); same.provider = "MSGBILL"; same.ebarimt_id = "015200020090000972600000000000990"
    diff = _Rec(2000); diff.provider = "QPAY"; diff.ebarimt_id = "029100244106001097270149910045952"
    ours = [(same, _Pay(BASE), "1111УБА", "Хангарьд"),
            (diff, _Pay(BASE + timedelta(minutes=1)), "2222УБА", "Хангарьд")]
    rows = [["7524322", same.ebarimt_id, BASE.strftime("%Y-%m-%d %H:%M:%S"), 1500],
            ["7524322", "015200020090000972600000000001990",
             (BASE + timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S"), 2000]]
    tax, _ = parse_tax_export("teg.xlsx", _xlsx(rows, ["ТТД", "ДДТД", "Огноо", "Нийт дүн"]))
    r = best_shift(tax, ours, [0.0], 3)
    assert r["matched"] == 2 and r["ddtd_equal"] == 1
    assert r["by_provider"]["MSGBILL"]["equal"] == 1
    assert r["by_provider"]["QPAY"]["equal"] == 0
    assert r["by_provider"]["QPAY"]["sample"]["tax"].endswith("1990")


# ───────────── «ТЕГ-д бий, манайд алга» мөрийн шалтгаан ─────────────────────

def _probe(pid, when, amount, receipts, site="S1", plate="1234УБА"):
    return {"id": pid, "paid_at": when, "amount": float(amount), "site_id": site,
            "plate": plate, "site_name": "Хангарьд", "provider": "QPAY",
            "method": "QR", "receipts": receipts}


def test_unmatched_tax_verdicts():
    """Тохироогүй ТЕГ мөрийг манай ТӨЛБӨРөөр тайлна: давхар баримт уу,
    баримтгүй төлбөр үү, өөр зогсоол уу, огт байхгүй юу."""
    left = [
        {"dt": BASE, "amount": 1000.0, "src": "", "ddtd": "d-dup"},
        {"dt": BASE + timedelta(minutes=1), "amount": 2000.0, "src": "", "ddtd": "d-norec"},
        {"dt": BASE + timedelta(minutes=2), "amount": 3000.0, "src": "", "ddtd": "d-other"},
        {"dt": BASE + timedelta(minutes=3), "amount": 4000.0, "src": "", "ddtd": "d-none"},
    ]
    probe = [
        _probe("p1", BASE, 1000, ["0152000000000000000000000000000001"]),
        _probe("p2", BASE + timedelta(minutes=1), 2000, []),          # баримтгүй төлбөр
        _probe("p3", BASE + timedelta(minutes=2), 3000, ["x"], site="S9"),  # өөр зогсоол
    ]
    out = explain_unmatched_tax(left, probe, 0, 3, {"p1"}, {"S1"})
    assert [r["verdict"] for r in out] == [
        "DUPLICATE", "PAYMENT_NO_RECEIPT", "OTHER_SCOPE", "NO_PAYMENT"]
    assert out[1]["plate"] == "1234УБА" and out[1]["method"] == "QPAY/QR"


def test_duplicate_prefers_unmatched_payment():
    """Завгүй цагт ижил дүнтэй 2 төлбөр байхад хуурамч «давхардал» гаргах ёсгүй."""
    left = [{"dt": BASE, "amount": 1000.0, "src": "", "ddtd": "d1"}]
    probe = [_probe("p-matched", BASE, 1000, ["x"]),
             _probe("p-free", BASE + timedelta(seconds=1), 1000, [])]
    out = explain_unmatched_tax(left, probe, 0, 3, {"p-matched"}, None)
    assert out[0]["verdict"] == "PAYMENT_NO_RECEIPT"   # DUPLICATE биш


def test_pos_groups_finds_cash_register_tail():
    """ДДТД-ийн төгсгөл дэх кассын дугаарыг уртыг нь таамаглалгүй бүлэглэнэ."""
    ids = ["0152000200900009726%08d10002990" % i for i in range(90)]
    ids += ["0152000200900009726%08d10045952" % i for i in range(10)]
    g = pos_groups(ids)
    assert g == {"10002990": 90, "10045952": 10}
    assert pos_groups([]) == {}
