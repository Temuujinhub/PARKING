"""extract_confidence — камерын БОДИТ итгэлцүүрийг олох цэвэр дүрэм.

2026-08-29 аудит: Dahua TrafficJunction push-ийн жинхэнэ утга Object.Confidence-д
байдаг ч код Plate/TrafficCar-аас л хайгаад олохгүй болохоор нь 100 гэж бүртгэдэг
байсан — lpr_min_confidence шүүлтүүр түүхэндээ нэг ч уншилт татгалзаагүй
(19,959 уншилтын 16.7% нь бодитоор <80% байж «100» гэж бүртгэгдсэн).

    cd backend && venv/bin/python -m pytest tests/unit/test_confidence_extract.py -q
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.session_logic import extract_confidence  # noqa: E402

# Бодит TrafficJunction push-ийн хураангуй хэлбэр (аудитаар прод-оос авсан):
# TrafficCar-т PlateNumber БИЙ, Confidence АЛГА; жинхэнэ утга Object дотор.
REAL_PUSH = {
    "Code": "TrafficJunction", "Action": "Pulse",
    "Object": {"ObjectType": "Plate", "Confidence": 86, "RecogniseConf": 0,
               "Text": "9924УАМ"},
    "Vehicle": {"ObjectType": "Vehicle", "Confidence": 94},
    "TrafficCar": {"PlateNumber": "9924УАМ", "PlateColor": "White"},
}


def test_object_confidence_read():
    """Жинхэнэ утга Object.Confidence-оос уншигдана (94 биш, 86!)."""
    assert extract_confidence(REAL_PUSH, REAL_PUSH["TrafficCar"]) == 86


def test_vehicle_confidence_not_used():
    """Vehicle.Confidence (машины таних) — ДУГААРЫН итгэлцүүр биш тул хэрэглэхгүй."""
    ev = {"Object": {"ObjectType": "Vehicle", "Confidence": 94},
          "TrafficCar": {"PlateNumber": "1234УБА"}}
    assert extract_confidence(ev, ev["TrafficCar"]) == 100.0


def test_candidate_wins():
    """Дугаар олдсон бүтэц өөрөө Confidence-тэй бол (ITSAPI Plate) түүнийг авна."""
    ev = {"Plate": {"PlateNumber": "1234УБА", "Confidence": 72},
          "Object": {"ObjectType": "Plate", "Confidence": 90}}
    assert extract_confidence(ev, ev["Plate"]) == 72


def test_top_level_fallback():
    ev = {"TrafficCar": {"PlateNumber": "1234УБА"}, "Confidence": 55}
    assert extract_confidence(ev, ev["TrafficCar"]) == 55


def test_unknown_is_100():
    """Мэдээлэлгүй бол 100 — бодит уншилтыг мэдээлэл дутуугаас болж хаахгүй."""
    ev = {"TrafficCar": {"PlateNumber": "1234УБА"}}
    assert extract_confidence(ev, ev["TrafficCar"]) == 100.0


def test_zero_and_garbage_ignored():
    """0 болон мужаас гадуурх утга = «мэдээлэлгүй» (Confidence=0-ээр хаалт гацаахгүй)."""
    assert extract_confidence({"Object": {"ObjectType": "Plate", "Confidence": 0}},
                              {"PlateNumber": "1234УБА"}) == 100.0
    assert extract_confidence({"Confidence": "junk"}, {"PlateNumber": "1234УБА"}) == 100.0
    assert extract_confidence({"Confidence": 250}, {"PlateNumber": "1234УБА"}) == 100.0


def test_accuracy_alias():
    assert extract_confidence({}, {"PlateNumber": "1234УБА", "Accuracy": 63}) == 63
