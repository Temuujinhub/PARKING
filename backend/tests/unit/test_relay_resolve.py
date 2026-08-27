"""Хаалт АЛЬ камерын реле рүү команд явуулахыг сонгох дүрэм (2026-08-26 Рашбулаг ЭТТ).

Гомдол: доторх (nested) 2 хаалт 33 цагийн турш ОГТ нээгдэхгүй болсон, 30 гомдол
ирсэн. Шалтгаан: админ UI-аас доторх 2 КАМЕРЫН «дотоод» тэмдэглэгээ (nested_inner)
санамсаргүй унтарсан. IP-гүй хаалт нь ижил `nested_inner` + ижил эгнээний камерын
реле рүү команд явуулдаг тул хосолол тасарч, команд ОГТ ҮҮСЭХЭЭ больсон —
`barrier_commands`-д мөр ч үлдээгүй учир лог/тайлангаас юу ч харагдаагүй.

Энэ багц сонголтын дүрмийг DB-гүйгээр бэхэлнэ:
  1. Хэвийн үед хаалт өөрийн эгнээний камераа олно.
  2. Доторх хаалт ГАДНАХ камерыг ХЭЗЭЭ Ч зээлэхгүй (эс бол төлбөргүй гарна).
  3. `nested_inner` унтрахад доторх хаалт релегүй үлдэж, `relay_note` дуугарна.
  4. Нэг эгнээнд хоёр камер байвал ЧИГЛЭЛ таарсныг нь тогтвортой сонгоно.
  5. Устгасан камер сонгогдохгүй.
"""
from datetime import datetime

from app.services.barrier import is_relay_candidate, pick_relay

SITE = "site-rash"


class Dev:
    """Тестийн хөнгөн төхөөрөмж — DB шаардахгүй."""

    def __init__(self, name, device_type, lane_no, lane_dir, *, ip="",
                 nested_inner=False, status="active"):
        self.id = name
        self.site_id = SITE
        self.name = name
        self.device_type = device_type
        self.lane_no = lane_no
        self.lane_dir = lane_dir
        self.ip_address = ip
        self.nested_inner = nested_inner
        self.status = status
        self.created_at = datetime(2026, 8, 5)


def pool(device, cams):
    """`relay_pool`-ийн Python шүүлт (SQL хэсэг нь зөвхөн зогсоолоор шүүдэг)."""
    return [c for c in cams if is_relay_candidate(device, c)]


# Рашбулаг ЭТТ-ийн бодит бүтэц
def rashbulag(inner_flag=True):
    return [
        Dev("Орох камер", "camera", 1, "entry", ip="10.0.106.10"),
        Dev("Гарах камер", "camera", 2, "exit", ip="10.0.106.11"),
        Dev("Дотор гарах камер", "camera", 3, "exit", ip="10.0.106.12",
            nested_inner=inner_flag),
        Dev("Дотор орох камер", "camera", 4, "entry", ip="10.0.106.13",
            nested_inner=inner_flag),
    ]


OUTER_ENTRY = Dev("Орох хаалт", "barrier", 1, "entry")
OUTER_EXIT = Dev("Гарах хаалт", "barrier", 2, "exit")
INNER_EXIT = Dev("Дотор гарах хаалт", "barrier", 3, "exit", nested_inner=True)
INNER_ENTRY = Dev("Дотор орох хаалт", "barrier", 4, "entry", nested_inner=True)


def test_healthy_config_each_barrier_finds_own_camera():
    cams = rashbulag()
    got = {b.name: (pick_relay(b, pool(b, cams)) or Dev("—", "camera", 0, "entry")).ip_address
           for b in (OUTER_ENTRY, OUTER_EXIT, INNER_EXIT, INNER_ENTRY)}
    assert got == {
        "Орох хаалт": "10.0.106.10",
        "Гарах хаалт": "10.0.106.11",
        "Дотор гарах хаалт": "10.0.106.12",
        "Дотор орох хаалт": "10.0.106.13",
    }


def test_inner_barrier_never_borrows_outer_camera():
    """Эгнээний дугаар давхцсан ч дотоод хаалт гаднах камерыг авч болохгүй."""
    cams = [Dev("Орох камер", "camera", 1, "entry", ip="10.0.106.10"),
            Dev("Гарах камер", "camera", 2, "exit", ip="10.0.106.11")]
    inner_at_lane1 = Dev("Дотор орох хаалт", "barrier", 1, "entry", nested_inner=True)
    assert pick_relay(inner_at_lane1, pool(inner_at_lane1, cams)) is None


def test_nested_flag_off_silently_kills_inner_barriers():
    """2026-08-26-ний РЕГРЕСС: камерын «дотоод» тэмдэг унтрахад реле алга болно."""
    cams = rashbulag(inner_flag=False)
    for b in (INNER_ENTRY, INNER_EXIT):
        assert pick_relay(b, pool(b, cams)) is None, b.name
    # Гаднах хаалтууд эрүүл хэвээр — эвдрэл зөвхөн доторх талд
    assert pick_relay(OUTER_ENTRY, pool(OUTER_ENTRY, cams)).ip_address == "10.0.106.10"


def test_lane_mismatch_between_camera_and_barrier_breaks_relay():
    """Камерын эгнээг сольж хаалтынхтай зөрүүлэхэд ч команд явах газаргүй болно."""
    cams = [Dev("Дотор орох камер", "camera", 1, "entry", ip="10.0.106.13",
                nested_inner=True),
            Dev("Дотор гарах камер", "camera", 2, "exit", ip="10.0.106.12",
                nested_inner=True)]
    assert pick_relay(INNER_ENTRY, pool(INNER_ENTRY, cams)) is None   # хаалт эгнээ 4
    assert pick_relay(INNER_EXIT, pool(INNER_EXIT, cams)) is None     # хаалт эгнээ 3


def test_same_lane_two_cameras_picks_matching_direction():
    """Нэг эгнээнд 2 камер — чиглэл таарсныг нь ТОГТВОРТОЙ сонгоно (эхнийхийг биш)."""
    cams = [Dev("Буруу чиглэл", "camera", 1, "exit", ip="10.0.0.1"),
            Dev("Зөв чиглэл", "camera", 1, "entry", ip="10.0.0.2")]
    assert pick_relay(OUTER_ENTRY, pool(OUTER_ENTRY, cams)).ip_address == "10.0.0.2"


def test_deleted_camera_is_never_used():
    cams = [Dev("Устгасан", "camera", 1, "entry", ip="10.0.111.10", status="deleted")]
    assert pick_relay(OUTER_ENTRY, pool(OUTER_ENTRY, cams)) is None


def test_camera_without_ip_is_not_a_relay():
    """Суулгаагүй зогсоол: камер бүртгэлтэй ч IP-гүй бол реле болохгүй."""
    cams = [Dev("Орох камер", "camera", 1, "entry", ip="")]
    assert pick_relay(OUTER_ENTRY, pool(OUTER_ENTRY, cams)) is None


def test_single_camera_site_serves_every_lane():
    """Нэг all-in-one төхөөрөмж орох/гарахыг хоёуланг барьдаг зогсоол."""
    cams = [Dev("Цогц камер", "camera", 9, "both", ip="10.0.9.9")]
    assert pick_relay(OUTER_ENTRY, pool(OUTER_ENTRY, cams)).ip_address == "10.0.9.9"
    assert pick_relay(OUTER_EXIT, pool(OUTER_EXIT, cams)).ip_address == "10.0.9.9"
