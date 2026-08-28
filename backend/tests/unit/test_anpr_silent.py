"""ANPR «чимээгүй үхэл»-ийн илрүүлэлт (`anpr_silent`).

2026-08-28: камер ОНЛАЙН (стримийн heartbeat шинэ) мөртлөө дугаар илгээхээ
больдог гэмтэл системд байсан ч хоёр watchdog-ийн аль нь ч хардаггүй байв —
`_touch()` нь `last_seen`-ийг стримээр шинэчилдэг тул deadman хэзээ ч ажилладаггүй.
Жолоочид «дугаараа уншуулсан хэрнээ хаалт нээгдэхгүй» гэж мэдрэгддэг.

Хамгийн чухал нь ХУДАЛ ДОХИО өгөхгүй байх: шөнийн хоосон зогсоолд камер
чимээгүй байх нь ХЭВИЙН. Тиймээс «ижил зогсоолын нөгөө камер саяхан уншсан»
гэдэг нь заавал биелэх нөхцөл.
"""
from datetime import datetime, timedelta

from app.services.anpr_watch import anpr_silent

NOW = datetime(2026, 8, 28, 12, 0, 0)


def mins(n):
    return NOW - timedelta(minutes=n)


def test_үхсэн_камерыг_барина():
    """30 мин уншаагүй, хөрш нь 2 мин өмнө уншсан → урсгал байгаа, камер үхсэн."""
    assert anpr_silent(NOW, mins(30), mins(2), online=True)


def test_огт_уншиж_байгаагүй_камерыг_барина():
    assert anpr_silent(NOW, None, mins(2), online=True)


def test_шөнө_хоосон_зогсоолд_ХУДАЛ_дохио_өгөхгүй():
    """Хоёулаа чимээгүй = машин ирээгүй. Энэ бол хамгийн чухал сөрөг тест."""
    assert not anpr_silent(NOW, mins(300), mins(300), online=True)


def test_нэг_камертай_зогсоолд_дохио_өгөхгүй():
    """Харьцуулах хөрш байхгүй бол шүүх үндэслэлгүй."""
    assert not anpr_silent(NOW, mins(300), None, online=True)


def test_саяхан_уншсан_камерыг_шүүхгүй():
    assert not anpr_silent(NOW, mins(3), mins(2), online=True)


def test_ОФЛАЙН_камерыг_энд_шүүхгүй():
    """Стрим бүрэн үхсэн нь deadman-ийн ажил — давхар дохио оношийг бүрхэгдүүлнэ."""
    assert not anpr_silent(NOW, mins(300), mins(2), online=False)


def test_хөршийн_уншилт_ХУУЧИН_бол_дохио_өгөхгүй():
    """Хөрш 40 мин уншаагүй бол урсгал байгаа гэдэг нь батлагдахгүй."""
    assert not anpr_silent(NOW, mins(60), mins(40), online=True)


def test_босго_тохируулж_болно():
    assert not anpr_silent(NOW, mins(30), mins(2), online=True, silence_min=45)
    assert anpr_silent(NOW, mins(30), mins(2), online=True, silence_min=25)


def test_хөрш_шинэлэг_байх_босго():
    assert anpr_silent(NOW, mins(60), mins(9), online=True)
    assert not anpr_silent(NOW, mins(60), mins(11), online=True)
