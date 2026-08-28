"""Камер ↔ хаалтны эгнээгээр хосолгох дүрэм (`barrier_matches_camera`).

2026-08-28 production «Маршил»: 4 камер (эгнээ 1-4) бүртгэсэн ч Хаалтны
удирдлагад эгнээ 3, 4 ОГТ харагдахгүй байв. Шалтгаан: `ensure_lane_barriers`
эгнээг (lane_no) ЗӨВХӨН nested камерт тулгадаг байсан тул эгнээ 3-ын камер
«эгнээ 1-д орох хаалт байна шүү дээ» гээд хаалт үүсгэхгүй өнгөрдөг байв.

Үр дагавар нь зүгээр «харагдахгүй» биш: `_find_barrier` эгнээ 3-ын уншилтаар
эгнээ 1-ийн хаалтыг нээж, машин ирээгүй газар хаалт хөдөлдөг — 2026-08-26
Рашбулаг ЭТТ-д гомдол үүсгэсэнтэй ЯГ ижил анги.
"""
from app.services.device_auto import barrier_matches_camera


class D:
    """Хөнгөн орлуулагч — DB шаардахгүй."""
    def __init__(self, device_type="barrier", lane_no=1, lane_dir="entry", nested_inner=False):
        self.device_type, self.lane_no = device_type, lane_no
        self.lane_dir, self.nested_inner = lane_dir, nested_inner


def _cam(**kw):
    return D(device_type="camera", **kw)


def test_ижил_эгнээ_чиглэл_хосолно():
    assert barrier_matches_camera(_cam(lane_no=1, lane_dir="entry"),
                                  D(lane_no=1, lane_dir="entry"))


def test_эгнээ_3_камер_эгнээ_1_хаалтыг_ӨӨРИЙНХ_гэж_үзэхгүй():
    """Гол регресс: чиглэл таарсан ч ЭГНЭЭ өөр бол хос БИШ."""
    assert not barrier_matches_camera(_cam(lane_no=3, lane_dir="entry"),
                                      D(lane_no=1, lane_dir="entry"))


def test_эгнээ_4_камер_эгнээ_2_хаалтыг_ӨӨРИЙНХ_гэж_үзэхгүй():
    assert not barrier_matches_camera(_cam(lane_no=4, lane_dir="exit"),
                                      D(lane_no=2, lane_dir="exit"))


def test_чиглэл_зөрвөл_хосолдоггүй():
    """Орох камерын команд гарах хаалт руу явбал машин төлбөргүй гарна."""
    assert not barrier_matches_camera(_cam(lane_no=1, lane_dir="entry"),
                                      D(lane_no=1, lane_dir="exit"))


def test_дотоод_камер_гадна_хаалттай_хосолдоггүй():
    """nested_inner нь дотоод/гадна усыг тусгаарлах цорын ганц шинж."""
    assert not barrier_matches_camera(_cam(lane_no=3, lane_dir="exit", nested_inner=True),
                                      D(lane_no=3, lane_dir="exit", nested_inner=False))


def test_гадна_камер_дотоод_хаалттай_хосолдоггүй():
    assert not barrier_matches_camera(_cam(lane_no=3, lane_dir="exit", nested_inner=False),
                                      D(lane_no=3, lane_dir="exit", nested_inner=True))


def test_дотоод_хос_эгнээ_таарвал_хосолно():
    assert barrier_matches_camera(_cam(lane_no=4, lane_dir="entry", nested_inner=True),
                                  D(lane_no=4, lane_dir="entry", nested_inner=True))


def test_камерыг_хаалт_гэж_үзэхгүй():
    """Хаалт болох нэр дэвшигч ЗААВАЛ device_type='barrier' байна."""
    assert not barrier_matches_camera(_cam(lane_no=1, lane_dir="entry"),
                                      D(device_type="camera", lane_no=1, lane_dir="entry"))


def test_both_чиглэлт_хаалт_хоёр_талд_хосолно():
    """Нэг хаалт орох/гарахыг хоёуланг барьдаг зогсоолд ХЭРЭГГҮЙ хаалт үүсгэхгүй."""
    assert barrier_matches_camera(_cam(lane_no=1, lane_dir="entry"),
                                  D(lane_no=1, lane_dir="both"))
    assert barrier_matches_camera(_cam(lane_no=1, lane_dir="exit"),
                                  D(lane_no=1, lane_dir="both"))


def test_both_ч_гэсэн_ЭГНЭЭ_зөрвөл_хосолдоггүй():
    assert not barrier_matches_camera(_cam(lane_no=3, lane_dir="entry"),
                                      D(lane_no=1, lane_dir="both"))
