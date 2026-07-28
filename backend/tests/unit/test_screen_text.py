"""LED дэлгэцийн текст угсралт — 3 мөр (дугаар / зогссон хугацаа / төлбөр)."""
from app.config import settings
from app.services.barrier import format_duration, render_screen_text


def test_format_duration():
    assert format_duration(None) == ""
    assert format_duration(0) == "0min"
    assert format_duration(45) == "45min"
    assert format_duration(65) == "1ts 05min"
    assert format_duration(125.4) == "2ts 05min"


def test_fee_text_three_lines():
    text = render_screen_text(settings.screen_fee_text, amount=3500,
                              plate="1234УБА", duration_minutes=125)
    lines = text.split("\n")
    assert lines == ["1234УБА", "2ts 05min", "Tulbur: 3500"]


def test_duration_missing_drops_empty_line():
    # {duration} өгөгдөөгүй үед дунд нь хоосон мөр үлдэх ёсгүй
    text = render_screen_text("{plate}\n{duration}\nTulbur: {amount}",
                              amount=1000, plate="7777АБВ")
    assert text.split("\n") == ["7777АБВ", "Tulbur: 1000"]


def test_pipe_and_literal_newline_separators():
    assert render_screen_text("{plate}|{duration}", plate="0001АА",
                              duration_minutes=30).split("\n") == ["0001АА", "30min"]
    assert render_screen_text("{plate}\\nTulbur: {amount}", amount=500,
                              plate="0002ББ").split("\n") == ["0002ББ", "Tulbur: 500"]
