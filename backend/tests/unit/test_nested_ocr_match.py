"""Доторх (parent/child) зогсоол — OCR-ойролцоо тохирол ба доторх гарах уншилтын
нөхөлт (2026-10-14 аудит, audit1014). Санах ойн SQLite, сүлжээгүй, зохиомол дугаар.

Нотолгоо (прод): гадна/доторх ОРОХ камер нэг машиныг 1 тэмдэгтээр зөрүүтэй уншдаг
(7969УАА/7969УА, 6238ӨМҮ/6238ӨМ, 2678УКР/2678УР, 9707УАС/9707УАО, 4171УЕА/4171УРА,
0450УЕМ/0450УБ, 8333УАО/8333УАС) → гадна тоолуур зогсоогүй, дотор өнгөрүүлсэн бүх
хугацаа нэхэгдсэн; доторх сешнгүй машины доторх ГАРАХ уншилт review болж хаалт
нээгдээгүй (10-14 өглөө 26-аас 25); гарцад татгалзсан машин дотогш буцахад дүн
царцсан (6238ӨМҮ); итгэл 22-ын уншилтаар доторх «гарц» (4464УБЛ); гараар
бүртгэсэн доторх машин гадна тоолуурыг зогсоодоггүй.
"""
import asyncio
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import models as M, session_logic as SL
from app.config import settings
from app.database import Base
from app.services import app_settings as A
from app.services import nested as N

PLATE = "1357УАА"


@pytest.fixture
def c(monkeypatch):
    import socket
    import httpx

    def forbidden(*a, **kw):
        raise AssertionError("network forbidden")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(httpx.Client, "send", forbidden)
    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden)
    monkeypatch.setattr(settings, "transit_max_hours", 4)
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    A.invalidate_cache()
    with Session(engine, autoflush=True) as db:
        tpl = M.TariffTemplate(name="t", free_minutes=0, grace_minutes=15, extra_hour_price=1000)
        db.add(tpl)
        db.flush()
        db.add(M.TariffTier(template_id=tpl.id, upto_minutes=60, price=1000))
        parent = M.ParkingSite(name="Гадна", site_code="PAR", transit_max_hours=0,
                               tariff_template_id=tpl.id)
        db.add(parent)
        db.flush()
        child = M.ParkingSite(name="Дотор", site_code="KID", parent_site_id=parent.id,
                              no_charge=True, tariff_template_id=tpl.id)
        db.add(child)
        db.flush()
        dev = {}
        for site, tag in ((parent, "out"), (child, "in")):
            for lane, d in ((1, "entry"), (2, "exit")):
                dev[f"{tag}_{d}"] = M.Device(site_id=site.id, name=f"{tag}-{d}", device_type="camera",
                                             lane_no=lane, lane_dir=d, status="active", auto_open=True,
                                             nested_inner=False, ip_address=f"192.0.2.{len(dev) + 1}",
                                             device_key=f"k-{tag}-{d}")
                dev[f"{tag}_{d}_bar"] = M.Device(site_id=site.id, name=f"{tag}-{d}-bar",
                                                 device_type="barrier", lane_no=lane, lane_dir=d,
                                                 status="active", nested_inner=False)
        db.add_all(dev.values())
        db.commit()
        commands, messages = [], []
        monkeypatch.setattr(SL, "notify", lambda sid, kind, data: messages.append((kind, data)))
        monkeypatch.setattr(SL, "schedule_capture", lambda *a, **kw: None)
        monkeypatch.setattr(SL, "schedule_display", lambda *a, **kw: None)
        monkeypatch.setattr(SL, "schedule_entry_hold", lambda *a, **kw: None)

        async def open_fake(db, barrier, session_id, source, **kw):
            commands.append((barrier.id, session_id, source))
            cmd = M.BarrierCommand(device_id=barrier.id, session_id=session_id, command="open",
                                   command_source=source, status="SUCCESS")
            db.add(cmd)
            db.commit()
            return cmd
        monkeypatch.setattr(SL, "open_barrier", open_fake)

        class Ctx:
            pass
        x = Ctx()
        x.db, x.parent, x.child, x.dev, x.commands, x.messages = (
            db, parent, child, dev, commands, messages)
        yield x
    A.invalidate_cache()
    engine.dispose()


def outer(c, plate=PLATE, ago=timedelta(seconds=20), **kw):
    kw.setdefault("status", "OPEN")
    s = M.ParkingSession(site_id=c.parent.id, plate_number=plate,
                         entry_time=datetime.utcnow() - ago, **kw)
    c.db.add(s)
    c.db.commit()
    return s


def inner(c, plate, ago=timedelta(0), **kw):
    s = M.ParkingSession(site_id=c.child.id, plate_number=plate, status="OPEN",
                         entry_time=datetime.utcnow() - ago, **kw)
    c.db.add(s)
    c.db.commit()
    return s


def enter_inner(c, plate):
    return asyncio.run(SL.handle_entry(c.db, c.dev["in_entry"], plate, 80, {}))


def exit_inner(c, plate, conf=85):
    return asyncio.run(SL.handle_exit(c.db, c.dev["in_exit"], plate, conf, {}))


def audits(c, action):
    return c.db.query(M.AuditLog).filter_by(action=action).all()


def bar_cmds(c, key):
    return [x for x in c.commands if x[0] == c.dev[key].id]


# ── Ойролцоо дугаарын дүрэм ─────────────────────────────────────────────────

@pytest.mark.parametrize("read,outer_plate", [
    ("1357УА", "1357УАА"),     # тайрагдсан (7969УА)
    ("2468ӨМ", "2468ӨМҮ"),     # тайрагдсан (6238ӨМ)
    ("3579УР", "3579УКР"),     # дунд нь тэмдэгт алдсан (2678УР)
    ("4680УАО", "4680УАС"),    # 1 солилт (9707УАО)
    ("5791УРА", "5791УЕА"),    # 1 солилт (4171УРА)
    ("6802УБ", "6802УЕМ"),     # Б/Е андуурал + тайралт (0450УБ)
    ("6802УБП", "6802УЕМ"),    # Б/Е + 1 солилт (0450УБП)
    ("7913УАС", "7913УАО"),    # 1 солилт (8333УАС)
    ("913УАО", "7913УАО"),     # эхний цифр алдсан (171УЕА)
])
def test_inner_plate_similar_evidence_pairs(read, outer_plate):
    assert N.inner_plate_similar(read, outer_plate)


@pytest.mark.parametrize("read,outer_plate", [
    ("1357ХКМ", "1357УАА"),    # үсэг бүгд өөр
    ("2468УАА", "9753УАА"),    # цифр өөр
    ("1357", "1357УАА"),       # үсэггүй
    ("1357УХК", "1357УАА"),    # 2 жинхэнэ үсгийн зөрүү
    ("", "1357УАА"),
])
def test_inner_plate_similar_rejects_different_cars(read, outer_plate):
    assert not N.inner_plate_similar(read, outer_plate)


# ── Доторх орох уншилт ──────────────────────────────────────────────────────

def test_truncated_inner_read_pauses_parent_renames_child_and_opens_gate(c):
    p = outer(c)
    res = enter_inner(c, "1357УА")                       # формат буруу (hold бодлого)
    kid = c.db.get(M.ParkingSession, res["session_id"])
    assert p.paused_since is not None and p.paused_since >= p.entry_time
    assert kid.plate_number == PLATE and "1357УА" in kid.note
    (a,) = audits(c, "NESTED_OCR_MATCH")
    assert a.entity_id == p.id and a.detail["child_renamed"] is True
    # Засагдсан дугаар = зөв уншилт ирсэнтэй адил — hold-д баригдахгүй, шууд нээнэ
    assert res["barrier_opened"] and not res["held"] and bar_cmds(c, "in_entry_bar")
    assert not bar_cmds(c, "out_entry_bar") and not bar_cmds(c, "out_exit_bar")


def test_unmatched_junk_read_is_still_held(c):
    res = enter_inner(c, "9999УА")                       # гадна сешнгүй
    assert res["held"] and not c.commands


@pytest.mark.parametrize("other", ["1357УАБ", "1357УАК"])
def test_two_similar_outer_cars_is_ambiguous(c, other):
    a = outer(c)
    b = outer(c, other, ago=timedelta(seconds=40))
    res = enter_inner(c, "1357УА")
    kid = c.db.get(M.ParkingSession, res["session_id"])
    assert a.paused_since is None and b.paused_since is None
    assert kid.plate_number == "1357УА" and not audits(c, "NESTED_OCR_MATCH")


def test_similar_car_already_inside_makes_read_ambiguous(c):
    """A (1357УАА) аль хэдийн дотор (зогссон), B (1357УАБ) гадна талбайд. «1357УА»
    уншилт A-гийн давтан уншилт ч байж болно — B-г зогсоохгүй (дутуу нэхэмжлэл)."""
    a = outer(c, ago=timedelta(minutes=3))
    a.paused_since = datetime.utcnow() - timedelta(minutes=2)
    b = outer(c, "1357УАБ", ago=timedelta(seconds=30))
    c.db.commit()
    enter_inner(c, "1357УА")
    assert b.paused_since is None and not audits(c, "NESTED_OCR_MATCH")


def test_late_inner_read_beyond_window_is_not_guessed(c):
    p = outer(c, ago=timedelta(minutes=15))
    enter_inner(c, "1357УА")
    assert p.paused_since is None


def test_exact_inner_read_unchanged(c):
    p = outer(c)
    res = enter_inner(c, PLATE)
    assert p.paused_since is not None and not audits(c, "NESTED_OCR_MATCH")
    assert res["barrier_opened"]


def test_duplicate_variant_read_of_paused_parent_is_noop(c):
    p = outer(c, ago=timedelta(minutes=4))
    enter_inner(c, PLATE)
    for e in c.db.query(M.LprEvent).all():   # эхний уншилт 3 мин өмнө (dedup/burst-аас гадуур)
        e.created_at -= timedelta(minutes=3)
    c.db.commit()
    since = p.paused_since
    res = enter_inner(c, "1357УАО")          # хувилбар уншилт
    assert p.paused_since == since and not audits(c, "NESTED_OCR_MATCH")
    assert c.db.get(M.ParkingSession, res["session_id"]).plate_number == "1357УАО"


def test_registered_only_child_refused_variant_does_not_pause(c):
    c.child.registered_only = True
    c.db.commit()
    p = outer(c)
    res = enter_inner(c, "1357УАО")          # бүртгэлгүй → хаалт нээгдэхгүй
    assert p.paused_since is None and not c.commands and not res["barrier_opened"]


def test_blacklist_block_does_not_pause(c):
    A.set_site_rules(c.db, A.BLACKLIST_KEY, c.child.id, {"block_entry": True}, "t")
    c.db.add(M.BlacklistEntry(plate_number="1357УАО", reason="t", is_active=True))
    c.db.commit()
    p = outer(c)
    enter_inner(c, "1357УАО")
    assert p.paused_since is None and not bar_cmds(c, "in_entry_bar")


def test_two_cars_with_crossed_variant_reads_pair_correctly(c):
    """Хоёр машин зэрэг: доторх уншилт бүр зөвхөн өөрийн гадна сешнтэй хосолно."""
    a = outer(c, ago=timedelta(seconds=50))
    b = outer(c, "2468УББ", ago=timedelta(seconds=40))
    rb = enter_inner(c, "2468УБ")
    for e in c.db.query(M.LprEvent).all():   # burst/dedup цонхноос гадуур
        e.created_at -= timedelta(seconds=40)
    c.db.commit()
    ra = enter_inner(c, "1357УАО")
    assert a.paused_since and b.paused_since
    assert c.db.get(M.ParkingSession, rb["session_id"]).plate_number == "2468УББ"  # junk → засна
    assert c.db.get(M.ParkingSession, ra["session_id"]).plate_number == "1357УАО"  # зөв → хэвээр
    pairs = {(x.detail["read_plate"], x.entity_id) for x in audits(c, "NESTED_OCR_MATCH")}
    assert pairs == {("2468УБ", b.id), ("1357УАО", a.id)}


# ── Гарцад татгалзсан машин дотогш буцсан ───────────────────────────────────

def awaiting(c, minutes_ago=0.5, **kw):
    now = datetime.utcnow()
    p = outer(c, ago=timedelta(hours=22), status="AWAITING_PAYMENT",
              exit_device_id=c.dev["out_exit"].id,
              last_exit_seen_at=now - timedelta(minutes=minutes_ago),
              payment_wait_started_at=now - timedelta(minutes=minutes_ago),
              payment_quote={"total_fee": 23000}, total_fee=23000, base_fee=20909,
              vat_amount=2091, duration_minutes=1320, **kw)
    return p


def test_refused_exit_then_back_inside_reverts_to_open_and_pauses(c):
    p = awaiting(c)
    enter_inner(c, PLATE)
    assert p.status == "OPEN" and p.paused_since is not None
    assert p.exit_device_id is None and p.last_exit_seen_at is None
    assert p.payment_wait_started_at is None and p.total_fee is None
    (a,) = audits(c, "NESTED_EXIT_REVERT")
    assert a.detail["quoted_fee"] == 23000
    # 3 цагийн дараа дотроосоо гарч, гадна гарцад — дотор өнгөрүүлсэн 3ц хасагдана
    p.paused_since -= timedelta(hours=3)
    c.db.commit()
    fee_before = SL.session_fee_info(c.db, p)
    N.on_inner_exit(c.db, c.child, PLATE, datetime.utcnow())
    assert p.paused_minutes >= 179
    assert SL.session_fee_info(c.db, p)["total_fee"] == fee_before["total_fee"]


def test_refused_exit_with_running_pause_settles_and_restarts(c):
    """Доторх гарах уншилт алдагдаж (зогсолт явсаар) гарцад татгалзсан машин буцаж
    орлоо: гадна гарах уншилт хүртэлх зогсолт (батлагдаагүй хязгаар) барагдаж,
    шинэ зогсолт энэ уншилтаас; сешн OPEN."""
    p = awaiting(c, minutes_ago=1)
    p.paused_since = datetime.utcnow() - timedelta(hours=3)
    c.db.commit()
    enter_inner(c, PLATE)
    assert p.status == "OPEN" and 178 <= p.paused_minutes <= 180
    assert p.paused_since is not None and p.paused_since >= datetime.utcnow() - timedelta(seconds=5)


@pytest.mark.parametrize("minutes", [20, 115])
def test_no_revert_long_after_refusal(c, minutes):
    """Хяналт F2: татгалзсанаас 15 мин-аас хойш — 2ц-ийн unpaid_exit өрийн дүрэм хэвээр."""
    p = awaiting(c, minutes_ago=minutes)
    enter_inner(c, PLATE)
    assert p.status == "AWAITING_PAYMENT" and p.total_fee == 23000
    assert not audits(c, "NESTED_EXIT_REVERT")


def test_revert_frees_exit_snapshot_slot_and_keeps_evidence(c):
    p = awaiting(c, exit_snapshot="20261014/REFUSED_exit.jpg", exit_snapshot_source="comet")
    enter_inner(c, PLATE)
    assert p.status == "OPEN" and p.exit_snapshot is None and p.exit_snapshot_source is None
    (a,) = audits(c, "NESTED_EXIT_REVERT")
    assert a.detail["refused_exit_snapshot"] == "20261014/REFUSED_exit.jpg"


def test_fuzzy_read_never_touches_awaiting_parent(c):
    """Гарцад төлбөр хүлээж буй сешн — машин аль хэдийн гарсан (хаалт гараар/дагаж)
    байж болно; ойролцоо уншилт (өөр машин) түүнийг OPEN болгож зогсоовол өрийг нь
    бүхэлд нь хасна. Зөвхөн ЯГ дугаар."""
    p = awaiting(c)
    enter_inner(c, "1357УА")
    assert p.status == "AWAITING_PAYMENT" and p.paused_since is None
    assert p.total_fee == 23000 and not audits(c, "NESTED_EXIT_REVERT")


def test_fuzzy_read_never_touches_paid_parent(c):
    p = outer(c, status="PAID", paid_at=datetime.utcnow() - timedelta(seconds=30))
    enter_inner(c, "1357УА")
    assert p.paused_since is None and p.status == "PAID"


@pytest.mark.parametrize("status", ["PENDING", "CREATING", "UNKNOWN", "REVIEW"])
def test_no_revert_while_payment_in_flight(c, status):
    p = awaiting(c)
    c.db.add(M.Payment(session_id=p.id, site_id=c.parent.id, provider="QPAY", payment_method="QR",
                       sender_invoice_no=f"inv-{status}", amount=23000, status=status))
    c.db.commit()
    enter_inner(c, PLATE)
    assert p.status == "AWAITING_PAYMENT" and p.total_fee == 23000
    assert not audits(c, "NESTED_EXIT_REVERT")


def test_no_revert_for_flat_fee_or_paid(c):
    p = awaiting(c, fee_locked=True)
    enter_inner(c, PLATE)
    assert p.status == "AWAITING_PAYMENT"
    c.db.add(M.Payment(session_id=p.id, site_id=c.parent.id, provider="CASH", payment_method="CASH",
                       sender_invoice_no="inv-paid", amount=1000, status="PAID"))
    p.fee_locked = False
    p.paused_since = None
    c.db.commit()
    N.on_inner_entry(c.db, c.child, PLATE, datetime.utcnow())
    assert p.status == "AWAITING_PAYMENT"


# ── Доторх гарах уншилтын нөхөлт ────────────────────────────────────────────

def paused_parent(c, plate=PLATE, hours=5):
    p = outer(c, plate, ago=timedelta(hours=hours, minutes=1))
    p.paused_since = datetime.utcnow() - timedelta(hours=hours)
    c.db.commit()
    return p


def test_inner_exit_without_inner_session_resumes_paused_parent_and_opens(c):
    p = paused_parent(c)
    junk = inner(c, "1357УА", ago=timedelta(hours=5))      # засагдаагүй хувилбар сешн
    res = exit_inner(c, PLATE)
    assert res["action"] == "inner_exit_fallback" and res["barrier_opened"]
    assert p.paused_since is None and 299 <= p.paused_minutes <= 300
    assert junk.status == "FREE" and junk.exit_confirmed and junk.exit_time is not None
    assert bar_cmds(c, "in_exit_bar") == [(c.dev["in_exit_bar"].id, junk.id, "auto_exit")]
    assert not bar_cmds(c, "out_exit_bar") and not bar_cmds(c, "out_entry_bar")
    ev = c.db.query(M.LprEvent).filter_by(device_id=c.dev["in_exit"].id).one()
    assert ev.accepted and ev.plate_number == PLATE
    (a,) = audits(c, "NESTED_INNER_EXIT")
    assert a.entity_id == p.id and a.detail["closed_child_sessions"] == [junk.id]


def test_truncated_inner_exit_read_resumes_unique_paused_parent(c):
    p = paused_parent(c)
    res = exit_inner(c, "357УАА")              # эхний цифр алдсан (171УЕА хэв маяг)
    assert res["action"] == "inner_exit_fallback" and p.paused_since is None


def test_inner_exit_unknown_car_stays_in_review(c):
    res = exit_inner(c, "2468УКН")
    assert res["action"] == "review_required" and not c.commands


def test_inner_exit_known_car_not_paused_stays_in_review(c):
    outer(c, ago=timedelta(hours=2))           # гадна сешнтэй ч дотор орсон нь бүртгэгдээгүй
    res = exit_inner(c, PLATE)
    assert res["action"] == "review_required" and not c.commands


def test_inner_exit_two_paused_similar_parents_is_ambiguous(c):
    a = paused_parent(c)
    b = paused_parent(c, "1357УАБ", hours=3)
    res = exit_inner(c, "1357УА")
    assert res["action"] == "review_required" and not c.commands
    assert a.paused_since and b.paused_since


def test_inner_exit_cross_read_right_after_entry_does_not_resume(c):
    p = outer(c, ago=timedelta(seconds=50))
    p.paused_since = datetime.utcnow() - timedelta(seconds=30)
    c.db.commit()
    res = exit_inner(c, PLATE)
    assert res["action"] == "review_required" and p.paused_since is not None


def test_inner_exit_fallback_keeps_other_cars_inner_session(c):
    p = paused_parent(c)
    other_parent = outer(c, "1357УАБ", ago=timedelta(hours=1))
    other_kid = inner(c, "1357УАБ", ago=timedelta(minutes=50))
    own = inner(c, "1357УА", ago=timedelta(hours=5))
    # «1357УАА» уншилт → яг дугаар ЗОГССОН гадна сешн (ойролцоо хайлт хийгдэхгүй)
    res = exit_inner(c, PLATE)
    assert res["action"] == "inner_exit_fallback" and p.paused_since is None
    assert other_kid.status == "OPEN" and other_parent.paused_since is None
    # own хувилбар нь other_parent-тай ч ойролцоо → хоёрдмол тул хаахгүй (хор хөнөөлгүй)
    assert own.status == "OPEN"


def test_inner_exit_on_charged_child_is_untouched(c):
    c.child.no_charge = False
    c.db.commit()
    paused_parent(c)
    res = exit_inner(c, PLATE)
    assert res["action"] != "inner_exit_fallback"


def test_inner_exit_respects_debt_block_rule(c):
    A.set_site_rules(c.db, A.BLACKLIST_KEY, c.child.id, {"block_exit_debt_count": 1}, "t")
    c.db.add(M.Compensation(site_id=c.parent.id, plate_number=PLATE, amount=5000,
                            status="PENDING", reason="t"))
    c.db.commit()
    p = paused_parent(c)
    res = exit_inner(c, PLATE)
    assert res["action"] == "review_required" and p.paused_since is not None


def test_child_session_with_variant_plate_resumes_unique_paused_parent(c):
    """Засагдаагүй хувилбар доторх сешн ердийн гарцаар хаагдахад (on_inner_exit
    ойролцоо) гадна тоолуур үргэлжилнэ."""
    p = paused_parent(c, hours=2)
    kid = inner(c, "1357УАО", ago=timedelta(hours=2))
    res = exit_inner(c, "1357УАО")
    assert res["action"] == "exit_completed" and kid.status == "FREE"
    assert p.paused_since is None and 119 <= p.paused_minutes <= 120


def test_other_cars_inner_session_does_not_resume_similar_paused_parent(c):
    """Гадна орох уншилт нь алдагдсан ӨӨР машины (1357УАО) доторх сешн 2ц дараа
    хаагдахад ойролцоо дугаартай, дотор байгаа 1357УАА-гийн зогсолт тасрахгүй —
    тэр доторх сешн 1357УАА-гийн гадна орох уншилтын цонхонд ороогүй."""
    p = paused_parent(c, hours=4)
    other_kid = inner(c, "1357УАО", ago=timedelta(hours=2))
    res = exit_inner(c, "1357УАО")
    assert res["action"] == "exit_completed" and other_kid.status == "FREE"
    assert p.paused_since is not None and not p.paused_minutes


def test_inner_exit_fallback_opens_despite_other_cars_recent_open(c):
    """Хяналт F5: өөр машинд 1с өмнө амжилттай нээсэн ч (cooldown) энэ машинд команд."""
    p = paused_parent(c, hours=2)
    c.db.add(M.BarrierCommand(device_id=c.dev["in_exit_bar"].id, session_id=None, command="open",
                              command_source="auto_exit", status="SUCCESS"))
    c.db.commit()
    res = exit_inner(c, PLATE)
    assert res["action"] == "inner_exit_fallback" and res["barrier_opened"]
    assert p.paused_since is None and bar_cmds(c, "in_exit_bar")


def test_valid_inner_read_keeps_its_plate_registered_only(c):
    """Хяналт F4: бүртгэлтэй машиныг доторх камер ЗӨВ уншсан, гадна буруу (1357УАО).
    Доторх сешн өөрийн зөв дугаараа хадгална — `_admitted` үнэн хэвээр, cross-read-ийн
    дараах сэргээлт эцгийг дахин зогсооно."""
    c.child.registered_only = True
    c.db.add(M.RegisteredDriver(plate_number=PLATE, site_id=c.child.id, is_active=True,
                                valid_from=datetime.utcnow() - timedelta(days=1),
                                valid_to=datetime.utcnow() + timedelta(days=30)))
    c.db.commit()
    p = outer(c, "1357УАО", ago=timedelta(seconds=30))
    res = enter_inner(c, PLATE)
    kid = c.db.get(M.ParkingSession, res["session_id"])
    assert p.paused_since is not None and kid.plate_number == PLATE
    assert N._admitted(c.db, c.child, kid.plate_number)
    (a,) = audits(c, "NESTED_OCR_MATCH")
    assert a.detail["child_renamed"] is False
    since = datetime.utcnow()
    assert N.on_inner_exit(c.db, c.child, kid.plate_number, since, child=kid) >= 0
    assert p.paused_since is None                       # cross-read «гарц»
    assert N.restore_parent_pause(c.db, c.child, kid, since)
    assert p.paused_since == since


def _hist(c, plate, days_ago, hours=2):
    """Өмнөх (хаагдсан) айлчлал. Postgres-ийн uq_active_session нь зөвхөн идэвхтэй
    сешнд (partial) — SQLite-д бүтэн unique болж үүсдэг тул энд хасна."""
    from sqlalchemy import text
    c.db.execute(text("DROP INDEX IF EXISTS uq_active_session"))
    h = outer(c, plate, ago=timedelta(days=days_ago), status="CLOSED")
    h.exit_time = h.entry_time + timedelta(hours=hours)
    c.db.commit()
    return h


def test_regular_car_outer_misread_still_matches(c):
    """N1 (эзний шийдвэр): 8810УКР хэв маяг — байнгын машин (7 өмнөх сешн) доторх камерт
    ЗӨВ, гадна камерт нэг удаа буруу (1357УАБ, өөр түүхгүй) уншигдсан → тохирно."""
    for d in range(1, 8):
        _hist(c, "1357УАА", d)
    b = outer(c, "1357УАБ", ago=timedelta(seconds=40))   # гадна буруу уншилт
    res = enter_inner(c, PLATE)
    assert b.paused_since is not None
    assert c.db.get(M.ParkingSession, res["session_id"]).plate_number == PLATE   # зөв → хэвээр
    (a,) = audits(c, "NESTED_OCR_MATCH")
    assert a.entity_id == b.id


def test_two_known_distinct_cars_are_not_matched(c):
    """N1: уншсан 1357УАБ ба гадна 1357УАА хоёулаа өөрийн түүхтэй — хоёр мэдэгдэх машин
    (1357УАБ-ийн гадна орох уншилт алдагдсан) → 1357УАА-г зогсоохгүй."""
    _hist(c, "1357УАБ", 3)
    _hist(c, "1357УАА", 5)
    b = outer(c, ago=timedelta(minutes=1))               # 1357УАА гадна талбайд
    enter_inner(c, "1357УАБ")
    assert b.paused_since is None and not audits(c, "NESTED_OCR_MATCH")


def test_two_known_cars_also_blocks_inner_exit_fallback(c):
    _hist(c, "1357УАБ", 3)
    _hist(c, "1357УАА", 5)
    p = paused_parent(c, hours=2)
    res = exit_inner(c, "1357УАБ")
    assert res["action"] == "review_required" and p.paused_since is not None


def test_read_history_alone_does_not_block(c):
    """Зөвхөн уншсан дугаар түүхтэй, гадна сешний дугаар анх удаа → тохирно."""
    _hist(c, "1357УАБ", 3)
    b = outer(c, ago=timedelta(minutes=1))
    enter_inner(c, "1357УАБ")
    assert b.paused_since is not None


def test_junk_read_with_own_junk_history_still_matches(c):
    junk = outer(c, "1357УА", ago=timedelta(days=2), status="FREE")
    junk.exit_time = junk.entry_time + timedelta(hours=2)
    c.db.commit()
    p = outer(c)
    enter_inner(c, "1357УА")
    assert p.paused_since is not None


# ── Итгэл багатай гарах уншилт ──────────────────────────────────────────────

def test_low_confidence_override_not_applied_at_child_site(c):
    inner(c, PLATE)
    assert not SL.exit_low_confidence_override(c.db, c.dev["in_exit"], PLATE, "confidence<30.0")
    outer(c, "2468УКН")
    assert SL.exit_low_confidence_override(c.db, c.dev["out_exit"], "2468УКН", "confidence<30.0")


# ── 100 айл: гарах уншилт алдагдвал 24ц ─────────────────────────────────────

def test_unverified_cap_per_parent_site_rule(c):
    assert N.unverified_cap_minutes(c.db, c.parent.id) == 240            # .env 4ц
    assert N.child_cap_minutes(c.db, c.child) == 0                         # батлагдсан хязгааргүй
    A.set_site_rules(c.db, A.EXITRULES_KEY, c.parent.id, {"inner_unverified_cap_hours": 24}, "t")
    c.db.commit()
    assert N.unverified_cap_minutes(c.db, c.parent.id) == 1440
    assert N.child_cap_minutes(c.db, c.child) == 0
    # Явж буй 30ц зогсолт → урьдчилсан дүн ба гарц хоёулаа 24ц
    p = outer(c, ago=timedelta(hours=31))
    p.paused_since = datetime.utcnow() - timedelta(hours=30)
    c.db.commit()
    assert N.effective_paused_minutes(c.db, p, datetime.utcnow()) == 1440
    assert N.close_open_pause(c.db, p, datetime.utcnow()) == 1440
    # Батлагдсан (доторх гарах уншилттай) 30ц — бүтэн хасагдана
    q = outer(c, "2468УКН", ago=timedelta(hours=31))
    q.paused_since = datetime.utcnow() - timedelta(hours=30)
    c.db.commit()
    assert N.on_inner_exit(c.db, c.child, "2468УКН", datetime.utcnow()) >= 1799


def test_positive_child_cap_still_wins(c):
    A.set_site_rules(c.db, A.EXITRULES_KEY, c.parent.id, {"inner_unverified_cap_hours": 24}, "t")
    c.child.transit_max_hours = 6
    c.db.commit()
    assert N.unverified_cap_minutes(c.db, c.parent.id) == 360


# ── Гараар бүртгэл ба camera_sync ───────────────────────────────────────────

def test_manual_entry_at_child_pauses_parent(c, monkeypatch):
    from app.routers import sessions_router as SR
    monkeypatch.setattr(SR.manager, "broadcast", lambda *a, **kw: asyncio.sleep(0))
    user = M.User(username="op", password_hash="x", role="SUPER_ADMIN")
    c.db.add(user)
    c.db.commit()
    p = outer(c, ago=timedelta(minutes=30))
    asyncio.run(SR.manual_entry({"site_id": c.child.id, "plate_number": PLATE}, c.db, user))
    assert p.paused_since is not None
    (a,) = audits(c, "NESTED_MANUAL_PAUSE")
    assert a.username == "op" and a.entity_id == p.id


def test_manual_entry_backdated_pauses_from_now_not_parent_entry(c, monkeypatch):
    """Хяналт F3: кассчин орсон цагийг 6ц хойш тавьж гадна дүнг тасалж болохгүй."""
    from app.routers import sessions_router as SR
    monkeypatch.setattr(SR.manager, "broadcast", lambda *a, **kw: asyncio.sleep(0))
    user = M.User(username="op", password_hash="x", role="SUPER_ADMIN")
    c.db.add(user)
    c.db.commit()
    p = outer(c, ago=timedelta(hours=5))
    fee0 = SL.session_fee_info(c.db, p)["total_fee"]
    back = (datetime.utcnow() - timedelta(hours=6)).isoformat()
    asyncio.run(SR.manual_entry({"site_id": c.child.id, "plate_number": PLATE,
                                 "entry_time": back}, c.db, user))
    assert p.paused_since >= datetime.utcnow() - timedelta(seconds=10)
    assert SL.session_fee_info(c.db, p)["total_fee"] == fee0
    assert audits(c, "NESTED_MANUAL_PAUSE")


def test_manual_entry_variant_plate_does_not_fuzzy_match(c, monkeypatch):
    from app.routers import sessions_router as SR
    monkeypatch.setattr(SR.manager, "broadcast", lambda *a, **kw: asyncio.sleep(0))
    user = M.User(username="op", password_hash="x", role="SUPER_ADMIN")
    c.db.add(user)
    c.db.commit()
    p = outer(c, ago=timedelta(minutes=2))
    asyncio.run(SR.manual_entry({"site_id": c.child.id, "plate_number": "1357УАО"}, c.db, user))
    assert p.paused_since is None and not audits(c, "NESTED_MANUAL_PAUSE")


def test_manual_entry_refused_registered_only_child_does_not_pause(c, monkeypatch):
    from app.routers import sessions_router as SR
    monkeypatch.setattr(SR.manager, "broadcast", lambda *a, **kw: asyncio.sleep(0))
    c.child.registered_only = True
    user = M.User(username="op", password_hash="x", role="SUPER_ADMIN")
    c.db.add(user)
    c.db.commit()
    p = outer(c, ago=timedelta(minutes=30))
    asyncio.run(SR.manual_entry({"site_id": c.child.id, "plate_number": PLATE}, c.db, user))
    assert p.paused_since is None


def test_camera_sync_inner_variant_dedup(c):
    from app.services.camera_sync import _inner_variant_exists
    now = datetime.utcnow()
    inner(c, PLATE, ago=timedelta(minutes=10))
    assert _inner_variant_exists(c.db, c.child.id, "1357УА", now - timedelta(minutes=10))
    assert _inner_variant_exists(c.db, c.child.id, "1357УАО", now)
    assert not _inner_variant_exists(c.db, c.child.id, "2468УКН", now)


def test_backfilled_variant_child_credits_and_renames(c):
    p = outer(c, "3579УКР", ago=timedelta(hours=6))
    start = p.entry_time + timedelta(seconds=40)
    kid = M.ParkingSession(site_id=c.child.id, plate_number="3579УР", entry_time=start,
                           status="OPEN")
    c.db.add(kid)
    c.db.flush()
    mins = N.credit_backfilled_child(c.db, c.child, "3579УР", start,
                                     start + timedelta(hours=5), child=kid)
    assert mins == 300 and p.paused_minutes == 300 and kid.plate_number == "3579УКР"
    # Ойролцоо хоёр гадна сешн → хасалт өгөхгүй
    outer(c, "2468ӨМҮ", ago=timedelta(hours=2))
    outer(c, "2468ӨМК", ago=timedelta(hours=2))
    t = datetime.utcnow() - timedelta(hours=2) + timedelta(seconds=30)
    assert N.credit_backfilled_child(c.db, c.child, "2468ӨМ", t, t + timedelta(hours=1)) == 0


def test_rules_catalog_and_conflict_text_show_per_parent_cap(c):
    from app.services import payment_rules as PRU
    keys = {(r["group"], r["key"]) for r in PRU.catalog()}
    assert (A.EXITRULES_KEY, "inner_unverified_cap_hours") in keys
    row = next(r for r in PRU.catalog() if r["key"] == "inner_unverified_cap_hours")
    assert row["per_site"] and row["unit"] == "hour"
    A.set_site_rules(c.db, A.EXITRULES_KEY, c.parent.id, {"inner_unverified_cap_hours": 24}, "t")
    c.db.commit()
    texts = " ".join(x.get("detail", "") or "" for x in PRU.check_conflicts(c.db, c.child))
    assert "24 цагаар" in texts
    texts = " ".join(x.get("detail", "") or "" for x in PRU.check_conflicts(c.db, c.parent))
    assert "24 цагаар" in texts
