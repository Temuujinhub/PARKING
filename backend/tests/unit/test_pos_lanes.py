"""POS bootstrap — хаалт+камерыг эгнээгээр хослуулах (2 орох + 2 гарах зогсоол)."""
from app.routers.payments_router import pos_lanes


def _b(i, no, d, can=True):
    return {"id": f"b{i}", "name": f"Хаалт {i}", "lane_no": no, "lane_dir": d, "can_open": can}


def _c(i, no, d):
    return {"id": f"c{i}", "name": f"Кам {i}", "lane_no": no, "lane_dir": d}


def test_two_entry_two_exit_pairs_by_lane():
    lanes = pos_lanes([_b(1, 1, "entry"), _b(2, 2, "entry"), _b(3, 1, "exit"), _b(4, 2, "exit")],
                      [_c(1, 1, "entry"), _c(2, 2, "entry"), _c(3, 1, "exit"), _c(4, 2, "exit")])
    assert [(x["lane_dir"], x["lane_no"]) for x in lanes] == [
        ("entry", 1), ("entry", 2), ("exit", 1), ("exit", 2)]
    assert all(x["barrier_id"] and x["camera_id"] for x in lanes)
    ex2 = next(x for x in lanes if x["lane_dir"] == "exit" and x["lane_no"] == 2)
    assert ex2["barrier_id"] == "b4" and ex2["camera_id"] == "c4"


def test_unpaired_devices_still_listed():
    lanes = pos_lanes([_b(1, 1, "exit")], [_c(9, 3, "exit")])
    assert len(lanes) == 2
    assert lanes[0]["barrier_id"] == "b1" and lanes[0]["camera_id"] is None
    assert lanes[1]["camera_id"] == "c9" and lanes[1]["barrier_id"] is None


def test_can_open_propagates_and_defaults_false():
    lanes = pos_lanes([_b(1, 1, "exit", can=False)], [_c(1, 2, "exit")])
    assert lanes[0]["can_open"] is False
    assert lanes[1]["can_open"] is False  # хаалтгүй эгнээ — нээх юмгүй


def test_missing_lane_fields_default_entry_1():
    lanes = pos_lanes([{"id": "b", "name": "x", "lane_no": None, "lane_dir": None}], [])
    assert lanes == [{"lane_dir": "entry", "lane_no": 1, "barrier_id": "b", "barrier_name": "x",
                      "can_open": False, "camera_id": None, "camera_name": None}]
