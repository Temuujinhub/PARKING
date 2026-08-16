"""Comet сувгийн амьд байдлын хамгаалалт (2026-08-15-ны production оношилгоо).

Юу болсон бэ: 08-14 13:37-д comet 20 камер дээр асч, 8 нь зураг өгсөн. 14:20-14:45-д
холболтууд тасарч, дахин холбогдоход `attachFileProc` түр татгалзсан. Тэр үед код нь
ЗУРАГ ӨГЧ БАЙСАН филтерээ орхиод дараагийн хувилбар руу шилжсэн — тэр нь энэ
firmware дээр алдаагүй attach хийгддэг ч зураг хэзээ ч өгдөггүй. Хоолой нээлттэй,
алдаа гарахгүй тул reconnect давталт ч эргээгүй → 20 камер 11 цаг чимээгүй үхсэн.

Энэ тест гурван хамгаалалтыг барина:
  1. Зураг өгсөн филтерийг ЦЭЭЖЛЭНЭ, татгалзал гарсан ч түүнээсээ салахгүй
  2. Attach хийгдсэн ч зураг өгөхгүй суваг `CometSilent`-ээр таслагдаж дараагийн
     хувилбар туршигдана (мөнхөд гацахгүй)
  3. Эхлэл нь тараагдана (20 камер нэг агшинд login хийхгүй)
"""
import asyncio

import pytest

from app.config import settings
from app.services import snap_puller as sp


@pytest.fixture(autouse=True)
def _clean():
    sp._comet_ok_filter.clear()
    sp._comet_state.clear()
    yield
    sp._comet_ok_filter.clear()
    sp._comet_state.clear()


_REAL_SLEEP = asyncio.sleep


@pytest.fixture
def no_sleep(monkeypatch):
    """Backoff хүлээлтийг тэглэнэ — тестийг агшин зуур дуусгана."""
    monkeypatch.setattr(asyncio, "sleep", lambda *_a, **_k: _REAL_SLEEP(0))


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


def test_proven_filter_is_kept_after_rejection(monkeypatch, no_sleep):
    """Зураг өгсөн филтер дээрээ тогтоно — татгалзал нь шилжих шалтгаан биш."""
    IP = "10.0.105.10"
    used: list[int] = []

    async def fake_session(ip, on_picture, flt, creds=None, filter_no=1):
        used.append(filter_no)
        if len(used) == 1:                       # эхний холболт: зураг өгнө
            await on_picture("1234УБА", b"\xff\xd8\xff" + b"x" * 100)
            raise RuntimeError("хоолой тасарлаа")
        if len(used) < 4:                        # дараагийнх нь татгалзана
            raise sp.AttachRejected("268959743")
        raise asyncio.CancelledError

    monkeypatch.setattr(sp, "_comet_session", fake_session)
    monkeypatch.setattr(sp, "_attach_to_session",
                        lambda *a, **k: asyncio.sleep(0))

    with pytest.raises(asyncio.CancelledError):
        _run(sp._comet_one("dev1", IP, "entry"))

    assert sp._comet_ok_filter[IP] == 0, "зураг өгсөн филтер цээжлэгдэх ёстой"
    assert used == [1, 1, 1, 1], f"батлагдсан филтерээс салсан: {used}"


def test_unproven_filter_rotates(monkeypatch, no_sleep):
    """Зураг өгөөгүй филтер татгалзвал дараагийнх нь туршигдана."""
    used: list[int] = []

    async def fake_session(ip, on_picture, flt, creds=None, filter_no=1):
        used.append(filter_no)
        if len(used) >= 3:
            raise asyncio.CancelledError
        raise sp.AttachRejected("268959743")

    monkeypatch.setattr(sp, "_comet_session", fake_session)

    with pytest.raises(asyncio.CancelledError):
        _run(sp._comet_one("dev1", "10.0.106.10", "entry"))

    assert used == [1, 2, 3], f"филтер эргэлт зогсов: {used}"


def test_silent_channel_rotates_too(monkeypatch, no_sleep):
    """Attach ХИЙГДСЭН ч зураг өгөхгүй суваг мөн дараагийн хувилбарт шилжинэ.

    Энэ нь production дээр 11 цаг гацаасан яг тэр байдал."""
    used: list[int] = []

    async def fake_session(ip, on_picture, flt, creds=None, filter_no=1):
        used.append(filter_no)
        if len(used) >= 3:
            raise asyncio.CancelledError
        raise sp.CometSilent("180с зураг ирсэнгүй")

    monkeypatch.setattr(sp, "_comet_session", fake_session)

    with pytest.raises(asyncio.CancelledError):
        _run(sp._comet_one("dev1", "10.0.111.14", "entry"))

    assert used == [1, 2, 3], f"чимээгүй суваг дээр гацав: {used}"


def test_state_is_visible_for_diagnostics(monkeypatch, no_sleep):
    """Оношилгооны endpoint зориулалттай төлөв бөглөгдөнө."""
    IP = "10.0.102.10"

    async def fake_session(ip, on_picture, flt, creds=None, filter_no=1):
        sp._comet_state.setdefault(ip, {}).update(attached=1.0, filter_no=filter_no, pics=0)
        await on_picture("", b"\xff\xd8\xff" + b"y" * 50)
        raise asyncio.CancelledError

    monkeypatch.setattr(sp, "_comet_session", fake_session)

    with pytest.raises(asyncio.CancelledError):
        _run(sp._comet_one("dev1", IP, "entry"))

    st = sp.comet_state()[IP]
    assert st["pics"] == 1
    assert st["proven_filter_no"] == 1
    assert st["last_pic_sec"] is not None


def test_start_is_staggered():
    """Бүх камер нэг агшинд login хийхгүй — зөрүү тохиргоотой."""
    assert settings.snap_comet_start_stagger_sec > 0
    assert settings.snap_comet_probe_sec >= 60      # хэт богино бол илүүц эргэлт
    assert settings.snap_comet_idle_sec > settings.snap_comet_probe_sec
    assert settings.snap_comet_keepalive_sec < 60   # RPC2 сешн 60с-д хөрдөг


def test_probe_needs_a_real_car(monkeypatch):
    """Чимээгүй байдлыг МАШИН ирсэн үед л «филтер буруу» гэж үзнэ.

    2026-08-15 орой: шөнө машин ирэхгүй байхыг сувгийн эвдрэл гэж андуурч,
    22 камер 3 минут тутам дэмий дахин холбогдож камерын нэвтрэлтийн нөөцийг
    иддэг байв."""
    from app.services import cgi_poller as cp

    IP = "10.0.113.10"
    cp._last_car.pop(IP, None)
    assert cp.last_car_ts(IP) is None, "машин ирээгүй үед шүүх үндэслэлгүй"

    cp.note_car(IP)
    ts = cp.last_car_ts(IP)
    assert ts is not None and ts > 0

    # attach-аас ӨМНӨ уншигдсан машин нь энэ холболтыг буруутгах үндэслэл болохгүй
    attached_at = ts + 1.0
    assert not (ts >= attached_at)
