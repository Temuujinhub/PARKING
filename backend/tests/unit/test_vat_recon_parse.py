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
    DEFAULT_TOL, VERDICTS, as_ddtd, as_dt, as_num, best_shift, cancel_block_reason, duplicate_list,
    duplicate_summary, explain_unmatched_tax, match_pass, parse_tax_export, pos_groups,
    reconcile_excel, register_tail_len,
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
    """Таарсан хос бүрийн ДДТД-г сувгаар нь харьцуулна (ТЕГ-ийнхтэй ижил үү).
    Манай ДДТД ба ТЕГ-ийн ДДТД-ийн КАССЫН дугаар (сүүлийн 8 орон) ижил байна —
    продын 08-24-ний дата: QPay …10014863 ↔ …10014863, msgbill …10045952 ↔ …10045952."""
    same = _Rec(1500); same.provider = "MSGBILL"; same.ebarimt_id = "015200020090000972600000000000990"
    diff = _Rec(2000); diff.provider = "QPAY"; diff.ebarimt_id = "029100244106001097270149910045952"
    ours = [(same, _Pay(BASE), "1111УБА", "Хангарьд"),
            (diff, _Pay(BASE + timedelta(minutes=1)), "2222УБА", "Хангарьд")]
    rows = [["7524322", same.ebarimt_id, BASE.strftime("%Y-%m-%d %H:%M:%S"), 1500],
            ["7524322", "015200020090000972600000010045952",
             (BASE + timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S"), 2000]]
    tax, _ = parse_tax_export("teg.xlsx", _xlsx(rows, ["ТТД", "ДДТД", "Огноо", "Нийт дүн"]))
    r = best_shift(tax, ours, [0.0], 3)
    assert r["matched"] == 2 and r["ddtd_equal"] == 1
    assert r["by_provider"]["MSGBILL"]["equal"] == 1
    assert r["by_provider"]["QPAY"]["equal"] == 0
    assert r["by_provider"]["QPAY"]["sample"]["tax"].endswith("10045952")


# ───────────── «ТЕГ-д бий, манайд алга» мөрийн шалтгаан ─────────────────────

MSG_DDTD = "029100244106001097260066610045952"     # манай msgbill (…10045952)
QPAY_DDTD = "030101065006000097260000410014863"    # манай QPay (…10014863)
ONTIME = "015200020090000097260000110002990"       # Ontime POS (…10002990)


def _rcpt(rid, provider="MSGBILL", status="SENT", ddtd=MSG_DDTD, ref="rcp-1", amount=1000.0):
    return {"id": rid, "provider": provider, "status": status, "provider_ref": ref,
            "ebarimt_id": ddtd, "amount": amount,
            "mock": provider in (None, "POSAPI") and ddtd is not None and len(ddtd) != 33}


def _probe(pid, when, amount, receipts, site="S1", plate="1234УБА", method="QR",
           provider="QPAY", active=None, primary=None, cancelled_msgbill=False):
    if active is None and receipts and method != "QR":
        active = [_rcpt("r-" + pid, ddtd=receipts[0], amount=float(amount))]
    if primary is None and active:
        primary = active[0]
    return {"id": pid, "paid_at": when, "amount": float(amount), "site_id": site,
            "site_code": "HANGARID", "plate": plate, "site_name": "Хангарьд",
            "provider": provider, "method": method, "receipts": receipts,
            "active": active or [], "primary": primary, "cancelled_msgbill": cancelled_msgbill}


def _cash(pid, when, amount=1000, **kw):
    return _probe(pid, when, amount, [MSG_DDTD], method="CASH", provider="CASH", **kw)


def _left(dt, amount, ddtd, foreign=True, group=None):
    return {"dt": dt, "amount": float(amount), "src": "Ontime" if foreign else "",
            "ddtd": ddtd, "foreign": foreign, "group": group or ddtd[-8:], "tail": 8, "shift": 0.0}


def test_unmatched_tax_verdicts():
    """Тохироогүй ТЕГ мөрийг манай ТӨЛБӨРөөр тайлна: давхар баримт уу,
    баримтгүй төлбөр үү, өөр зогсоол уу, огт байхгүй юу."""
    left = [
        _left(BASE, 1000, ONTIME),
        _left(BASE + timedelta(minutes=1), 2000, ONTIME),
        _left(BASE + timedelta(minutes=2), 3000, ONTIME),
        _left(BASE + timedelta(minutes=3), 4000, ONTIME),
    ]
    probe = [
        _cash("p1", BASE, 1000),
        _probe("p2", BASE + timedelta(minutes=1), 2000, []),          # баримтгүй төлбөр
        _probe("p3", BASE + timedelta(minutes=2), 3000, ["x"], site="S9"),  # өөр зогсоол
    ]
    out = explain_unmatched_tax(left, probe, 0, 3, {"p1"}, {"S1"})
    assert [r["verdict"] for r in out] == [
        "DUPLICATE", "PAYMENT_NO_RECEIPT", "OTHER_SCOPE", "NO_PAYMENT"]
    assert out[1]["plate"] == "1234УБА" and out[1]["method"] == "QPAY/QR"
    assert out[0]["register"] == "10002990" and out[0]["foreign"] is True


def test_duplicate_prefers_unmatched_payment():
    """МАНАЙ кассын үлдсэн мөр: завгүй цагт ижил дүнтэй 2 төлбөр байхад хуурамч
    «давхардал» гаргах ёсгүй — таараагүй төлбөрийг түрүүлж сонгоно."""
    left = [_left(BASE, 1000, MSG_DDTD, foreign=False)]
    probe = [_cash("p-matched", BASE),
             _probe("p-free", BASE + timedelta(seconds=1), 1000, [], method="CASH")]
    out = explain_unmatched_tax(left, probe, 0, 3, {"p-matched"}, None)
    assert out[0]["verdict"] == "PAYMENT_NO_RECEIPT"   # DUPLICATE биш
    # Манай кассын мөр таарсан төлбөрт буувал «давхардал» биш — сувгийн 2 дахь баримт
    out = explain_unmatched_tax(left, probe[:1], 0, 3, {"p-matched"}, None)
    assert out[0]["verdict"] == "EXTRA_SAME_REGISTER"


def test_pos_groups_finds_cash_register_tail():
    """ДДТД-ийн төгсгөл дэх кассын дугаарыг уртыг нь таамаглалгүй бүлэглэнэ."""
    ids = ["0152000200900009726%08d10002990" % i for i in range(90)]
    ids += ["0152000200900009726%08d10045952" % i for i in range(10)]
    g = pos_groups(ids)
    assert g == {"10002990": 90, "10045952": 10}
    assert pos_groups([]) == {}


def test_mixed_timezones_per_cash_register():
    """ТЕГ-ийн НЭГ экспорт дотор цагийн бүс ХОЛИЛДОЖ ирдэг (2026-08-24 нотлогдсон):
    QPay-ийн касс UTC-ээр, msgbill-ийн касс УБ локал (+8ц) цагаар. Нэг ерөнхий
    шилжилт сонговол нөгөө кассын БҮХ мөр «манайд алга» болно."""
    ours = [(_Rec(1000 + i), _Pay(BASE + timedelta(minutes=i)), "1234УБА", "Хангарьд")
            for i in range(20)]
    rows = []
    for i in range(10):                       # QPay касс — UTC
        rows.append(["7524322", "0152000200900009726%08d10014863" % i,
                     (BASE + timedelta(minutes=i)).strftime("%Y-%m-%d %H:%M:%S"), 1000 + i])
    for i in range(10, 20):                   # msgbill касс — УБ локал (+8ц)
        rows.append(["7524322", "0152000200900009726%08d10045952" % i,
                     (BASE + timedelta(minutes=i, hours=8)).strftime("%Y-%m-%d %H:%M:%S"), 1000 + i])
    tax, _ = parse_tax_export("teg.xlsx", _xlsx(rows, ["ТТД", "ДДТД", "Огноо", "Нийт дүн"]))
    r = best_shift(tax, ours, [0.0, -8.0], 3)
    assert r["group_shifts"] == {"10014863": 0.0, "10045952": -8.0}
    assert r["matched"] == 20 and r["unmatched_ours"] == []


def test_duplicate_rows_carry_payment_id():
    """Бөөнөөр цуцлахад UI нь давхардсан мөрийн ТӨЛБӨРийн ID-г мэдэх ёстой."""
    left = [_left(BASE, 1000, ONTIME)]
    probe = [_cash("pay-42", BASE)]
    out = explain_unmatched_tax(left, probe, 0, 3, {"pay-42"}, None)
    assert out[0]["verdict"] == "DUPLICATE" and out[0]["payment_id"] == "pay-42"


def test_only_msgbill_side_is_cancelled_target(tmp_path):
    """Касс дээр Ontime POS баримт хэвлэдэг байсан бэлэн/картын төлбөрийн msgbill
    баримт л давхардана. QPay/QR төлбөрт POS баримт хэвлэгддэггүй тул тэдгээрийн
    «давхардал» нь цаг+дүнгээр санамсаргүй таарсан байх магадлалтай — UI тэднийг
    ⚠ гэж тэмдэглэж анхдагчаар сонгохгүй (2026-08-24 продын дата: 61-ээс 2)."""
    items = [{"will_cancel": [{"provider": "MSGBILL"}]} for _ in range(59)]
    items += [{"will_cancel": [{"provider": "QPAY"}]} for _ in range(2)]
    from collections import Counter
    provs = Counter(w["provider"] for i in items for w in i["will_cancel"])
    top = provs.most_common(1)[0][0]
    suspects = [i for i in items if {w["provider"] for w in i["will_cancel"]} != {top}]
    assert top == "MSGBILL" and len(suspects) == 2


# ───────────── 2026-10-13: касс бүрийн шилжилт, нэг-нэгээр, давхардлын дүрэм ─────────────
#
# Продын 08-24-ний ТЕГ файл (471 мөр: QPay 332 UTC, msgbill 76 УБ локал, Ontime 63 UTC)
# дээр хуучин код (tol=3) 45 «давхардал» гаргасны 1 нь QR (санамсаргүй тааралт), 18
# Ontime мөр «төлбөр ч алга» болж алга болсон; tol=60 болгоход 2 QR хуурамч гарсан.
# Шинэ код: яг 63 (39 бэлэн + 24 карт, бүгд Хангарьд, 167,500₮), QR 0.

HDR4 = ["ТТД", "ДДТД", "Огноо", "Нийт дүн"]


def _rec(amount, ddtd, provider="MSGBILL", status="SENT"):
    r = _Rec(amount)
    r.provider, r.ebarimt_id, r.status = provider, ddtd, status
    return r


def _pay(paid_at, pid):
    p = _Pay(paid_at)
    p.id = pid
    return p


def _reg_ddtd(i, reg):
    return "0152000200900000972600%03d" % i + reg


def _file(rows):
    tax, _ = parse_tax_export("teg.xlsx", _xlsx(rows, HDR4))
    return tax


def test_default_tolerance_is_60s():
    """msgbill p95 4.8с, Ontime 27с хүртэл зөрдөг — 3с хэт нарийн байв."""
    assert DEFAULT_TOL == 60
    ours = [(_rec(1000, MSG_DDTD), _pay(BASE, "p1"), "1111УБА", "Хангарьд")]
    tax = _file([["x", _reg_ddtd(1, "10045952"), (BASE + timedelta(seconds=27)).strftime("%Y-%m-%d %H:%M:%S"), 1000]])
    assert best_shift(tax, ours, [0.0], 3)["matched"] == 0
    assert best_shift(tax, ours, [0.0], DEFAULT_TOL)["matched"] == 1


def test_register_tail_fallback_for_small_files():
    """5 мөртэй файлд ч кассыг (сүүлийн 8 орон) таних ёстой — эс бөгөөс Ontime мөр
    манай баримттай «таарч» давхардал нуугдана."""
    ids = [_reg_ddtd(i, "10002990") for i in range(3)]
    assert register_tail_len(ids) == 8
    assert register_tail_len(["12345"]) is None


def test_foreign_register_never_steals_our_receipt():
    """Ontime мөр (…10002990) манай msgbill баримттай ИЛҮҮ ОЙР байсан ч таарахгүй:
    манай баримт зөвхөн өөрийн кассын (…10045952) мөртэй таарна, Ontime мөр нь
    тухайн төлбөрийн ДАВХАРДАЛ болж тайлагдана."""
    pay = _pay(BASE, "p1")
    ours = [(_rec(1000, MSG_DDTD), pay, "1111УБА", "Хангарьд")]
    tax = _file([
        ["x", _reg_ddtd(1, "10002990"), BASE.strftime("%Y-%m-%d %H:%M:%S"), 1000],       # 0с (Ontime)
        ["x", _reg_ddtd(2, "10045952"), (BASE + timedelta(seconds=4, hours=8)).strftime("%Y-%m-%d %H:%M:%S"), 1000],
    ])
    probe = [_cash("p1", BASE)]
    r = best_shift(tax, ours, [0.0, -8.0], 60, probe=probe)
    assert r["foreign_registers"] == ["10002990"] and r["own_registers"] == ["10045952"]
    assert r["group_stats"]["10002990"] == {"rows": 1, "matched": 1, "shift": 0.0, "foreign": True}
    assert r["group_shifts"] == {"10002990": 0.0, "10045952": -8.0}
    assert r["matched"] == 1
    hit = [t for t in tax if t.get("ours")][0]
    assert hit["ddtd"].endswith("10045952")
    left = [t for t in tax if not t["used"]]
    out = explain_unmatched_tax(left, probe, r["shift"], 60, {"p1"}, None)
    assert [(o["register"], o["verdict"], o["payment_id"]) for o in out] == [("10002990", "DUPLICATE", "p1")]


def test_leftover_rows_probed_with_their_own_register_shift():
    """(a) Ontime файл УБ локал цагтай (−8), QPay нь UTC (0) → ерөнхий (min abs)
    шилжилт 0. Хуучин код Ontime-ийн үлдсэн мөрийг 8 цагийн зөрүүтэй хайж
    «Манайд төлбөр ч алга» гэдэг байв — одоо касс бүрийн шилжилтээр."""
    qpay_ours = [(_rec(500 + i, QPAY_DDTD, provider="QPAY"), _pay(BASE + timedelta(minutes=i), f"q{i}"),
                  "", "Хангарьд") for i in range(5)]
    msg = (_rec(1000, MSG_DDTD), _pay(BASE + timedelta(minutes=30), "c1"), "2222УБА", "Хангарьд")
    rows = [["x", _reg_ddtd(i, "10014863"), (BASE + timedelta(minutes=i)).strftime("%Y-%m-%d %H:%M:%S"), 500 + i]
            for i in range(5)]
    rows.append(["x", _reg_ddtd(9, "10045952"),
                 (BASE + timedelta(minutes=30, seconds=2, hours=8)).strftime("%Y-%m-%d %H:%M:%S"), 1000])
    rows.append(["x", _reg_ddtd(8, "10002990"),
                 (BASE + timedelta(minutes=30, seconds=-5, hours=8)).strftime("%Y-%m-%d %H:%M:%S"), 1000])
    tax = _file(rows)
    probe = [_cash("c1", BASE + timedelta(minutes=30))]
    r = best_shift(tax, qpay_ours + [msg], [0.0, -8.0], 60, probe=probe)
    assert r["shift"] == 0.0 and r["group_shifts"]["10002990"] == -8.0
    left = [t for t in tax if not t["used"]]
    out = explain_unmatched_tax(left, probe, r["shift"], 60, {"c1"}, None)
    assert [o["verdict"] for o in out] == ["DUPLICATE"]
    assert abs(out[0]["gap_s"]) == 5.0


def test_one_to_one_nearest_matching():
    """Хатуу нэг-нэгээр: нэг баримт хоёр мөрт, нэг мөр хоёр баримтад тоологдохгүй;
    хамгийн ойр хос эхэлж (өмнө нь манай баримтын дарааллаар шунахай сонгож байв)."""
    a = (_rec(1000, MSG_DDTD), _pay(BASE, "a"), "", "")
    b = (_rec(1000, MSG_DDTD), _pay(BASE + timedelta(seconds=10), "b"), "", "")
    tax = _file([["x", _reg_ddtd(1, "10045952"), (BASE + timedelta(seconds=9)).strftime("%Y-%m-%d %H:%M:%S"), 1000],
                 ["x", _reg_ddtd(2, "10045952"), (BASE + timedelta(seconds=1)).strftime("%Y-%m-%d %H:%M:%S"), 1000]])
    r = best_shift(tax, [a, b], [0.0], 60)
    assert r["matched"] == 2
    pairs = sorted((t["ddtd"][-11:-8], t["ours"][1].id) for t in tax)
    assert pairs == [("001", "b"), ("002", "a")]       # мөр бүр өөрийн хамгийн ойр төлбөртэй


def test_one_payment_explains_one_leftover_row():
    """Нэг төлбөрийн ойролцоо ижил дүнтэй 2 Ontime мөр — давхардал НЭГ л удаа."""
    left = [_left(BASE, 1000, _reg_ddtd(1, "10002990")),
            _left(BASE + timedelta(seconds=20), 1000, _reg_ddtd(2, "10002990"))]
    out = explain_unmatched_tax(left, [_cash("p1", BASE + timedelta(seconds=2))], 0, 60, {"p1"}, None)
    assert [o["verdict"] for o in out] == ["DUPLICATE", "NO_PAYMENT"]


def test_duplicate_requires_non_qr_issued_msgbill():
    """(b) DUPLICATE зөвхөн QR биш төлбөр + ИЛГЭЭГДСЭН msgbill (provider_ref-тэй).
    QR/QPay = санамсаргүй тааралт; MOCK = ТЕГ-т очоогүй; цуцлагдсан = аль хэдийн."""
    t0 = BASE
    probe = [
        _probe("qr", t0, 1000, [QPAY_DDTD], method="QR",
               active=[_rcpt("r1", provider="QPAY", ddtd=QPAY_DDTD)]),
        _probe("mock", t0 + timedelta(minutes=1), 1000, ["9" * 40], method="CASH", provider="CASH",
               active=[_rcpt("r2", provider=None, ddtd="9" * 40)]),
        _probe("done", t0 + timedelta(minutes=2), 1000, [MSG_DDTD], method="CARD", provider="POS",
               active=[], primary=_rcpt("r3", status="CANCELLED"), cancelled_msgbill=True),
        _probe("noref", t0 + timedelta(minutes=3), 1000, [MSG_DDTD], method="CASH", provider="CASH",
               active=[_rcpt("r4", ref=None)]),
        _cash("ok", t0 + timedelta(minutes=4)),
        _probe("wallet", t0 + timedelta(minutes=5), 1000, [MSG_DDTD], method="WALLET", provider="EP_WALLET"),
    ]
    left = [_left(t0 + timedelta(minutes=i, seconds=3), 1000, _reg_ddtd(i, "10002990")) for i in range(6)]
    out = explain_unmatched_tax(left, probe, 0, 30, {p["id"] for p in probe}, None)
    assert [o["verdict"] for o in out] == ["DUPLICATE_NOT_CANCELLABLE", "OURS_MOCK", "DUPLICATE_CANCELLED",
                                           "DUPLICATE_NOT_CANCELLABLE", "DUPLICATE", "DUPLICATE"]
    assert "QR" in out[0]["note"] and "provider_ref" in out[3]["note"]
    assert cancel_block_reason(probe[4]) is None
    assert set(VERDICTS) >= {o["verdict"] for o in out}


def test_qr_payment_does_not_steal_ontime_row_from_cash():
    """QR төлбөр Ontime мөрт 1с ойр, бэлэн төлбөр 20с — бэлэн нь сонгогдоно
    (Ontime POS зөвхөн касс дээрх бэлэн/картад баримт хэвлэдэг)."""
    left = [_left(BASE, 1000, ONTIME)]
    probe = [_probe("qr", BASE + timedelta(seconds=1), 1000, [QPAY_DDTD], method="QR"),
             _cash("cash", BASE + timedelta(seconds=20))]
    out = explain_unmatched_tax(left, probe, 0, 60, {"qr", "cash"}, None)
    assert (out[0]["verdict"], out[0]["payment_id"]) == ("DUPLICATE", "cash")


def test_duplicates_list_is_complete_beyond_display_cap():
    """(c) UI цуцлах жагсаалтаа дэлгэцийн 200 мөрөөс бүтээдэг байсан — 250 давхардал
    байхад 50 нь цуцлагдахгүй үлдэнэ. duplicate_list БҮТЭН, хураангуйтай."""
    n = 250
    left = [_left(BASE + timedelta(minutes=i), 1000, _reg_ddtd(i, "10002990")) for i in range(n)]
    probe = [_cash(f"p{i}", BASE + timedelta(minutes=i, seconds=2)) for i in range(n)]
    ex = explain_unmatched_tax(left, probe, 0, 60, {p["id"] for p in probe}, None)
    dups = duplicate_list(ex, probe, {p["id"] for p in probe}, 8)
    assert len(dups) == n and len({d["payment_id"] for d in dups}) == n
    d0 = dups[0]
    assert d0["teg_register"] == "10002990" and d0["our_ddtd"] == MSG_DDTD and d0["gap_s"] == 2.0
    assert d0["paid_at_ub"] == (BASE + timedelta(hours=8, seconds=2)).strftime("%Y-%m-%d %H:%M:%S")
    s = duplicate_summary(dups)
    assert s["count"] == n and s["amount"] == 1000.0 * n and s["by_method"]["CASH"]["count"] == n


def test_mock_receipts_never_match_teg_rows():
    """Хуучин MOCK (40 оронтой, provider NULL) баримт ТЕГ-т очоогүй — таарахгүй,
    «манайд бий, ТЕГ-д алга»-д MOCK гэж гарна."""
    mock = (_rec(1000, "9" * 40, provider=None), _pay(BASE, "m"), "", "")
    real = (_rec(500, QPAY_DDTD, provider="QPAY"), _pay(BASE + timedelta(minutes=5), "q"), "", "")
    tax = _file([["x", _reg_ddtd(1, "10014863"), BASE.strftime("%Y-%m-%d %H:%M:%S"), 1000],
                 ["x", _reg_ddtd(2, "10014863"), (BASE + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S"), 500]])
    r = best_shift(tax, [mock, real], [0.0], 60)
    assert r["matched"] == 1
    assert [u["mock"] for u in r["unmatched_ours"]] == [True]


def test_sheet_with_huge_blank_tail_reads_fast():
    """20260819.xlsx 479 баримттай атлаа 968,109 мөр гэж бичигдсэн (24с уншдаг байв)."""
    from app.routers.vat_recon import _sheet_rows

    class WS:
        def iter_rows(self, values_only=True):
            yield ("ДДТД", "Огноо")
            yield ("1", "2")
            for _ in range(10 ** 7):
                yield (None, None)
            raise AssertionError("хоосон сүүлийг бүгдийг уншиж байна")

    assert _sheet_rows(WS(), max_blank=100) == [["ДДТД", "Огноо"], ["1", "2"]]


def test_vendor_column_is_source():
    """ТЕГ-ийн «Систем нийлүүлэгч» (QPayPos/Ontime/Онлайм нетворк) нь эх сурвалж."""
    hdr = ["Пос дугаар", "ДДТД", "Огноо", "Нийт дүн", "НХАТ", "НӨАТ", "Цэвэр дүн", "Х/А регистр",
           "Х/А нэр", "Хаанаас", "НӨАТ төлөгч эсэх", "Пос дугаар", "Систем нийлүүлэгч", "Байршлын алба"]
    row = ["7524322", ONTIME, "2026-08-24 02:24:08.0", 1000, "", 90.91, 909.09, "", "", "POS", "",
           "10002990", "Ontime", "Баянзүрх"]
    tax, diag = parse_tax_export("teg.xlsx", _xlsx([row], hdr))
    assert diag["columns"]["src"] == "M" and tax[0]["src"] == "Ontime"


def test_excel_uses_per_register_time_and_lists_duplicates():
    """Нэгтгэсэн Excel: msgbill (УБ локал) мөрийг ХОЁР удаа шилжүүлэхгүй (+16ц биш),
    «Давхардал (цуцлах)» хуудастай."""
    import asyncio as _a
    ours = [(_rec(1000, MSG_DDTD), _pay(BASE, "p1"), "1111УБА", "Хангарьд")]
    tax = _file([["x", _reg_ddtd(2, "10045952"), (BASE + timedelta(hours=8, seconds=1)).strftime("%Y-%m-%d %H:%M:%S"), 1000],
                 ["x", _reg_ddtd(1, "10002990"), (BASE + timedelta(seconds=3)).strftime("%Y-%m-%d %H:%M:%S"), 1000]])
    probe = [_cash("p1", BASE)]
    r = best_shift(tax, ours, [0.0, -8.0], 60, probe=probe)
    left = [t for t in tax if not t["used"]]
    ex = explain_unmatched_tax(left, probe, r["shift"], 60, {"p1"}, None)
    dups = duplicate_list(ex, probe, {"p1"}, 8)
    resp = reconcile_excel(tax, ours, r["unmatched_ours"], 60, 8, "t", r["shift"],
                           {x["ddtd"]: x for x in ex}, dups)

    async def body():
        return b"".join([c async for c in resp.body_iterator])
    wb = openpyxl.load_workbook(io.BytesIO(_a.run(body())))
    rows = list(wb["Тулгалт"].iter_rows(values_only=True))[1:]
    times = {r_[2][-8:]: r_[0] for r_ in rows}
    want = (BASE + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M")
    assert times["10045952"].startswith(want) and times["10002990"].startswith(want)
    dsheet = list(wb["Давхардал (цуцлах)"].iter_rows(values_only=True))
    assert len(dsheet) == 2 and dsheet[1][-1] == "p1"
