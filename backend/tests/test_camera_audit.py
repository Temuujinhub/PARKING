"""camera_records-ийн цэвэр функцүүд: OCR ижилслэл + дугаар цэвэрлэлт."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.camera_records import normalized_plate, plates_similar  # noqa: E402


def test_normalized_plate():
    assert normalized_plate({"PlateNumber": "9786УЕВ"}) == "9786УЕВ"
    assert normalized_plate({"PlateNumber": " 2420 ухр "}) == "2420УХР"
    assert normalized_plate({"PlateNumber": "Unlicensed"}) is None
    assert normalized_plate({"PlateNumber": "Unknown"}) is None
    assert normalized_plate({"PlateNumber": ""}) is None
    assert normalized_plate({}) is None


def test_plates_similar_substitution():
    # Бодит тохиолдол (2026-08-09-ний камерын лог): Х↔К солигдож уншсан
    assert plates_similar("2420УХР", "2420УКР")
    assert plates_similar("0123АБВ", "0128АБВ")


def test_plates_similar_missing_char():
    # Нэг тэмдэгт дутуу уншсан (2026-08-09-ний логийн бодит жишээ):
    # 2420УХР-аас '4' алдагдвал 220УХР — хоёулаа 1 зөрүүтэйд тооцогдоно
    assert plates_similar("420УХР", "2420УХР")
    assert plates_similar("2420УХР", "420УХР")
    assert plates_similar("220УХР", "2420УХР")
    assert not plates_similar("20УХР", "2420УХР")   # 2 тэмдэгт дутуу — биш


def test_plates_similar_negative():
    assert not plates_similar("2420УХР", "2420УХР")   # яг ижил — зөрүү биш
    assert not plates_similar("", "2420УХР")
    assert not plates_similar("2420УХР", "")
    assert not plates_similar("1234АБВ", "5674АБВ")   # 3 зөрүү
    assert not plates_similar("123", "124")           # хэт богино
    assert not plates_similar("2420УХР", "24200УХРА")  # урт 2 зөрүү
