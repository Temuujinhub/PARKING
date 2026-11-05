"""log_tail-ийн ГЭРЧ ба лог татах алдааны анхааруулга.

2026-11-05 BTALBAI орох камер (б.талбай-10): 6 цагийн турш камер 663 дугаар
уншсанаас 77 нь л серверт ирсэн (87% алдагдал). Хааяа нэг event ирж байсан тул
стримийн idle таймер (15 мин) хэзээ ч ажиллаагүй; log_tail-ийн лог татах алдаа
debug түвшинд байсан тул нөөц зам 7 цаг ажиллаагүйг хэн ч хараагүй. Камерыг
дахин асаахад шууд засагдсан.

Эдгээр тест:
  • логоос стримд ирээгүй уншилт олдвол стримийг дахин холбуулна (cooldown-тай)
  • дахин холболт тусалсангүй бол reboot хэрэгтэйг нэг удаа мэдэгдэнэ
  • лог тасралтгүй татагдахгүй бол WARNING (ганц нэг алдаанд биш)
"""
import asyncio
import logging

from app.services import cgi_poller, log_tail


# ── witness_decision ────────────────────────────────────────────────────────
def _wd(hist, last_alarm, now):
    return log_tail.witness_decision(hist, last_alarm, now, cooldown_sec=120,
                                     window_sec=1800, escalate_after=3)


def test_анхны_алдагдалд_шууд_дахин_холбоно():
    force, alarm, hist = _wd([], 0.0, 1000.0)
    assert force and not alarm and hist == [1000.0]


def test_cooldown_дотор_дахин_холбохгүй():
    """log_tail 20с тутам ажилладаг — мөчлөг бүр стримийг таслахгүй."""
    force, alarm, hist = _wd([1000.0], 0.0, 1060.0)
    assert not force and not alarm and hist == [1000.0]


def test_cooldown_өнгөрвөл_дахин_холбоно():
    force, _alarm, hist = _wd([1000.0], 0.0, 1121.0)
    assert force and hist == [1000.0, 1121.0]


def test_гурван_удаа_холбосон_ч_алдсаар_бол_reboot_мэдэгдэнэ():
    force, alarm, hist = _wd([1000.0, 1130.0], 0.0, 1260.0)
    assert force and alarm and len(hist) == 3


def test_reboot_мэдэгдлийг_цонхонд_нэг_л_удаа():
    _f, alarm, _h = _wd([1000.0, 1130.0, 1260.0], 1260.0, 1400.0)
    assert not alarm


def test_хуучин_түүх_цонхноос_гарна():
    """30 минутын өмнөх дахин холболтууд тоологдохгүй — ховор алдагдал reboot дуудахгүй."""
    force, alarm, hist = _wd([0.0, 200.0], 0.0, 5000.0)
    assert force and not alarm and hist == [5000.0]


def test_escalate_0_бол_мэдэгдэхгүй():
    _f, alarm, _h = log_tail.witness_decision([1.0, 200.0, 400.0], 0.0, 600.0,
                                              cooldown_sec=120, window_sec=1800,
                                              escalate_after=0)
    assert not alarm


# ── fail_warn_due ───────────────────────────────────────────────────────────
def test_ганц_алдаанд_анхааруулахгүй():
    assert not log_tail.fail_warn_due(1000.0, 0.0, 1020.0, after_sec=300, every_sec=900)


def test_тасралтгүй_алдаанд_анхааруулна():
    assert log_tail.fail_warn_due(1000.0, 0.0, 1301.0, after_sec=300, every_sec=900)


def test_анхааруулгыг_давтамжаар_хязгаарлана():
    assert not log_tail.fail_warn_due(1000.0, 1301.0, 1500.0, after_sec=300, every_sec=900)
    assert log_tail.fail_warn_due(1000.0, 1301.0, 2202.0, after_sec=300, every_sec=900)


# ── _pull_one-ийн утаслалт ──────────────────────────────────────────────────
def _reset():
    log_tail._witness_hist.clear()
    log_tail._witness_alarm.clear()
    log_tail._pull_fail.clear()
    cgi_poller._force.clear()


def test_нөхсөн_уншилт_стримийг_дахин_холбуулна(monkeypatch):
    _reset()

    async def fake_inner(device_id, ip, creds, name):
        return 2

    monkeypatch.setattr(log_tail, "_pull_one_inner", fake_inner)
    n = asyncio.run(log_tail._pull_one("dev-1", "10.0.116.10", ("u", "p"), "б.талбай-10"))
    assert n == 2
    assert cgi_poller.take_force("dev-1")


def test_нөхсөн_уншилтгүй_бол_стримд_хүрэхгүй(monkeypatch):
    _reset()

    async def fake_inner(device_id, ip, creds, name):
        return 0

    monkeypatch.setattr(log_tail, "_pull_one_inner", fake_inner)
    asyncio.run(log_tail._pull_one("dev-2", "10.0.116.11", ("u", "p"), "б.талбай-11"))
    assert not cgi_poller.take_force("dev-2")


def test_гэрч_унтраалттай_бол_дахин_холбохгүй(monkeypatch):
    _reset()
    monkeypatch.setattr(log_tail.settings, "log_tail_witness_reconnect", False)

    async def fake_inner(device_id, ip, creds, name):
        return 1

    monkeypatch.setattr(log_tail, "_pull_one_inner", fake_inner)
    asyncio.run(log_tail._pull_one("dev-3", "10.0.116.10", ("u", "p"), "б.талбай-10"))
    assert not cgi_poller.take_force("dev-3")


def test_лог_татах_алдаа_WARNING_болж_харагдана(monkeypatch, caplog):
    _reset()
    clock = [1000.0]
    monkeypatch.setattr(log_tail.time, "monotonic", lambda: clock[0])
    with caplog.at_level(logging.WARNING, logger="parking.log_tail"):
        log_tail._note_pull_fail("dev-4", "б.талбай-10", "10.0.116.10", TimeoutError())
        assert not caplog.records
        clock[0] = 1000.0 + log_tail.settings.log_tail_fail_warn_after_sec + 1
        log_tail._note_pull_fail("dev-4", "б.талбай-10", "10.0.116.10", TimeoutError())
    assert any("татагдахгүй" in r.getMessage() for r in caplog.records)
    # Амжилттай болмогц төлөв цэвэрлэгдэнэ
    log_tail._note_pull_ok("dev-4", "б.талбай-10", "10.0.116.10")
    assert "dev-4" not in log_tail._pull_fail
