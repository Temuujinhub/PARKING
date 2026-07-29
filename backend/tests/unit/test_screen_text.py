"""LED дэлгэцийн текст угсралт — 3 мөр (дугаар / зогссон хугацаа / төлбөр)."""
from app.config import settings
from app.services.barrier import format_duration, render_screen_text


def test_format_duration():
    assert format_duration(None) == ""
    assert format_duration(0) == "0м"
    assert format_duration(45) == "45м"
    assert format_duration(65) == "1ц 05м"
    assert format_duration(125.4) == "2ц 05м"


def test_fee_text_three_lines():
    # 3-р мөр зөвхөн дүн — «Tulbur:» гэх мэт урт үг мөрийг урсгаж жолоочийн цаг алддаг
    text = render_screen_text(settings.screen_fee_text, amount=3500,
                              plate="1234УБА", duration_minutes=125)
    lines = text.split("\n")
    assert lines == ["1234УБА", "2ц 05м", "3500T"]


def test_welcome_text_three_lines():
    text = render_screen_text(settings.screen_welcome_text, plate="1234УБА",
                              time_str="14:05")
    assert text.split("\n") == ["14:05", "1234УБА", "Tavtai moril"]


def test_duration_missing_drops_empty_line():
    # {duration} өгөгдөөгүй үед дунд нь хоосон мөр үлдэх ёсгүй
    text = render_screen_text("{plate}\n{duration}\nTulbur: {amount}",
                              amount=1000, plate="7777АБВ")
    assert text.split("\n") == ["7777АБВ", "Tulbur: 1000"]


def test_pipe_and_literal_newline_separators():
    assert render_screen_text("{plate}|{duration}", plate="0001АА",
                              duration_minutes=30).split("\n") == ["0001АА", "30м"]
    assert render_screen_text("{plate}\\nTulbur: {amount}", amount=500,
                              plate="0002ББ").split("\n") == ["0002ББ", "Tulbur: 500"]


# ── Гарах дэлгэц: ЯАГААД гарч байгааг зөв сонгох (2026-07-29) ──
class _Sess:
    plate_number = "1234УБА"
    is_registered = False
    paid_at = None


def test_bye_text_registered():
    from app.session_logic import _bye_screen_text
    s = _Sess(); s.is_registered = True
    txt = _bye_screen_text(s, {"reason": "Бүртгэлтэй жолооч", "is_free": True,
                               "duration_minutes": 130, "total_fee": 0})
    assert txt.split("\n") == ["1234УБА", "2ц 10м", "Гэрээт"]


def test_bye_text_free_minutes():
    from app.session_logic import _bye_screen_text
    s = _Sess()
    txt = _bye_screen_text(s, {"reason": "Эхний 15 минут үнэгүй", "is_free": True,
                               "duration_minutes": 12, "total_fee": 0})
    assert txt.split("\n") == ["1234УБА", "12м", "Түр зогссон"]


def test_bye_text_paid():
    from app.session_logic import _bye_screen_text
    from datetime import datetime
    s = _Sess(); s.paid_at = datetime.utcnow()
    txt = _bye_screen_text(s, {"reason": "", "is_free": False,
                               "duration_minutes": 65, "total_fee": 2000})
    assert txt.split("\n") == ["1234УБА", "1ц 05м", "Баяртай"]


def test_bye_text_registered_wins_over_free():
    """Гэрээт машин үнэгүй ч «Түр зогссон» биш «Гэрээт» гэж харуулна."""
    from app.session_logic import _bye_screen_text
    s = _Sess(); s.is_registered = True
    txt = _bye_screen_text(s, {"reason": "Эхний 15 минут үнэгүй", "is_free": True,
                               "duration_minutes": 5, "total_fee": 0})
    assert "Гэрээт" in txt and "Түр зогссон" not in txt
