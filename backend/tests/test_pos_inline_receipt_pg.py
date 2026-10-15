"""POS аппын шууд баримт (2026-10-15) — БОДИТ PostgreSQL дээрх уралдааны тест.

    cd backend && PARKING_DATABASE_URL=postgresql+psycopg2://…/t_receipts \\
        venv/bin/python tests/test_pos_inline_receipt_pg.py

Юуг шалгана (SQLite-д FOR UPDATE SKIP LOCKED ажилладаггүй тул энд):
  1. confirm-ийн шууд илгээлт ба outbox worker ЗЭРЭГ ажиллахад msgbill-д НЭГ л удаа илгээнэ.
  2. Хоёр thread (тусдаа холболт) нэг ажлыг claim_one / claim_payment_jobs-оор ЗЭРЭГ авахад
     зөвхөн нэг нь авна (20 давталт).
  3. Ижил transaction_id-тай хоёр confirm зэрэг — нэг төлбөр, нэг баримт, нэг илгээлт.
  4. Шууд илгээлтийн хүлээлтийн үед request-ийн холболт «idle in transaction» биш.

msgbill/QPay/банк ОГТ дуудахгүй (бүгд stub, httpx хаалттай). Зөвхөн t_* DB дээр ажиллана;
үүсгэсэн мөрөө өөрөө устгана (app_settings-д бичихгүй).
"""
import asyncio
import os
import sys
import threading
import uuid
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("PARKING_BARRIER_MOCK", "true")
os.environ.setdefault("PARKING_QPAY_MOCK", "true")

import httpx  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402
from starlette.requests import Request  # noqa: E402

from app import models as M, session_logic as SL  # noqa: E402
from app.config import settings  # noqa: E402
from app.database import engine  # noqa: E402
from app.routers import payments_router as PR  # noqa: E402
from app.services import financial_jobs as J, payment_wait  # noqa: E402
from app.services.msgbill import MsgbillAccount  # noqa: E402

PASS = FAIL = 0
TAG = "PGT-" + uuid.uuid4().hex[:6].upper()
DDTD = "015200020090001097760067510045952"


def check(name, cond):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗ <<< FAIL'} {name}")


def guard():
    db = engine.url.database or ""
    if engine.dialect.name != "postgresql" or not db.startswith("t_"):
        raise SystemExit(f"ЗОГСОВ: зөвхөн t_* туршилтын Postgres DB дээр (одоо: {engine.dialect.name}/{db})")


ACC = MsgbillAccount(api_key="synthetic-key", base_url="https://msgbill.invalid")
CALLS = []


async def _blocked(*a, **k):
    raise AssertionError("Network forbidden")


def offline():
    httpx.AsyncClient.send = _blocked
    settings.qpay_ebarimt = False
    settings.receipt_inline_wait_sec = 6.0
    J.msgbill.account_enabled_for = lambda *a: ACC
    J.msgbill.api_key_for = lambda *a: ACC

    async def create(acc, amount, **kw):
        CALLS.append(kw["idempotency_key"])
        await asyncio.sleep(0.3)
        return {"status": "SUCCESS", "billId": DDTD[:-4] + f"{len(CALLS):04d}", "lottery": "IW 1",
                "qrData": f"QR-{kw['idempotency_key']}", "msgbillId": f"rcp-{uuid.uuid4().hex[:8]}",
                "state": "CREATED", "raw": {}}
    J.msgbill.create_receipt = create
    J.msgbill.get_receipt_any = AsyncMock(side_effect=AssertionError("no GET expected"))


created = {"sites": [], "users": [], "tariffs": []}


def setup():
    with Session(engine) as db:
        tariff = M.TariffTemplate(name=TAG, free_minutes=0, extra_hour_price=1000, daily_cap=50000)
        db.add(tariff); db.flush()
        site = M.ParkingSite(name=TAG, site_code=TAG, tariff_template_id=tariff.id)
        db.add(site); db.flush()
        u = M.User(username=TAG.lower(), role="POS", password_hash="unused", site_ids=[site.id])
        dev = M.Device(site_id=site.id, name="PAX", device_type="pax_terminal", device_key=TAG,
                       status="active")
        db.add_all([u, dev]); db.commit()
        created["sites"].append(site.id); created["users"].append(u.id)
        created["tariffs"].append(tariff.id)
        return site.id, u.id


def new_session(site_id, plate):
    with Session(engine) as db:
        now = datetime.utcnow()
        s = M.ParkingSession(site_id=site_id, plate_number=plate, entry_time=now - timedelta(minutes=59),
                             status="AWAITING_PAYMENT")
        db.add(s); db.flush(); db.refresh(s)
        payment_wait.begin_wait(db, s, SL.session_fee_info(db, s, at=now), now)
        db.commit()
        return s.id


def req():
    return Request({"type": "http", "method": "POST", "path": "/", "client": ("pos", 1),
                    "headers": [(b"x-app-version", b"1.6.0")]})


async def confirm(sid, uid, txn):
    with Session(engine, autoflush=False) as db:
        try:
            return await PR.pos_confirm({"session_id": sid, "amount": 1000, "transaction_id": txn,
                                         "terminal_id": TAG}, req(), db, db.get(M.User, uid))
        except Exception as e:  # noqa: BLE001
            return e


def scenario_inline_vs_worker(site_id, uid):
    print("\n1. Шууд илгээлт + outbox worker зэрэг")
    sid = new_session(site_id, "1001УБА")
    CALLS.clear()

    async def run():
        stop = False
        idle = []

        async def worker():
            while not stop:
                await J.run_once(limit=5)
                await asyncio.sleep(0.01)

        async def watch():
            # request-ийн холболт хүлээлтийн үед «idle in transaction» эсэх
            while not stop:
                with engine.connect() as c:
                    n = c.execute(text("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() "
                                       "AND state='idle in transaction' AND pid<>pg_backend_pid() "
                                       "AND now()-state_change > interval '1 second'")).scalar()
                idle.append(n)
                await asyncio.sleep(0.1)
        w, m = asyncio.ensure_future(worker()), asyncio.ensure_future(watch())
        out = await confirm(sid, uid, f"{TAG}-1")
        await asyncio.sleep(0.5)
        stop = True
        await w; await m
        return out, idle
    out, idle = asyncio.run(run())
    check("confirm амжилттай", isinstance(out, dict) and out.get("status") == "PAID")
    check("хариунд ДДТД + QR (SENT)", isinstance(out, dict) and out["receipt"]["ebarimt_status"] == "SENT"
          and out["qr_data"] == f"QR-pay-{out['payment_id']}")
    check(f"msgbill-д НЭГ удаа илгээв ({len(CALLS)})", len(CALLS) == 1)
    with Session(engine) as db:
        jobs = db.query(M.FinancialJob).filter_by(payment_id=out["payment_id"]).all()
        recs = db.query(M.VatReceipt).filter_by(payment_id=out["payment_id"]).all()
        check("ажил DONE, attempts=1", [(j.status, j.attempts) for j in jobs] == [("DONE", 1)])
        check("нэг SENT баримт", [(r.status, r.provider) for r in recs] == [("SENT", "MSGBILL")])
        check("QR DB-д бичигдээгүй (has_qr тэмдэг л)",
              "QR-" not in str(recs[0].raw) and recs[0].raw["outbox"]["has_qr"] is True)
    check(f"хүлээлтийн үед idle-in-transaction холболт алга (max {max(idle or [0])})",
          max(idle or [0]) == 0)


def scenario_thread_claim_race(site_id):
    print("\n2. claim_one ↔ claim_payment_jobs — 2 thread, 20 давталт")
    wins = []
    for i in range(20):
        sid = new_session(site_id, f"2{i:03d}УБА")
        with Session(engine) as db:
            p = M.Payment(session_id=sid, provider="CASH", payment_method="CASH", amount=1000,
                          vat_amount=91, status="PAID", paid_at=datetime.utcnow(),
                          sender_invoice_no=f"{TAG}-race-{i}")
            db.add(p); db.flush()
            J.enqueue_receipt(db, p, terminal=False)
            db.commit()
            pid = p.id
        barrier = threading.Barrier(2)
        got = {}

        def by_worker():
            with Session(engine) as db:
                barrier.wait(5)
                got["worker"] = J.claim_one(db)

        def by_inline():
            with Session(engine) as db:
                barrier.wait(5)
                got["inline"] = J.claim_payment_jobs(db, pid)[0]
        ts = [threading.Thread(target=by_worker), threading.Thread(target=by_inline)]
        [t.start() for t in ts]; [t.join(10) for t in ts]
        n = int(got.get("worker") not in (None, "quarantined")) + len(got.get("inline") or [])
        wins.append(n)
        with Session(engine) as db:      # дараагийн давталтад саад болохгүй — DONE болгоно
            db.query(M.FinancialJob).filter_by(payment_id=pid).update(
                {"status": "DONE", "lease_token": None, "lease_until": None})
            db.commit()
    check(f"давталт бүрд ЯГ НЭГ нь авав ({wins.count(1)}/20)", wins == [1] * 20)


def scenario_concurrent_duplicate_confirm(site_id, uid):
    print("\n3. Ижил transaction_id-тай 2 confirm зэрэг")
    sid = new_session(site_id, "3001УБА")
    CALLS.clear()

    async def run():
        a, b = await asyncio.gather(confirm(sid, uid, f"{TAG}-dup"), confirm(sid, uid, f"{TAG}-dup"))
        await asyncio.sleep(0.5)
        c = await confirm(sid, uid, f"{TAG}-dup")      # offline queue-ийн хожуу давталт
        return a, b, c
    a, b, c = asyncio.run(run())
    oks = [x for x in (a, b, c) if isinstance(x, dict)]
    errs = [x for x in (a, b) if not isinstance(x, dict)]
    check("алдаатай нь зөвхөн 409 (зэрэг түгжээ)", all(getattr(e, "status_code", None) == 409 for e in errs))
    check("бүх амжилттай хариу ИЖИЛ payment_id", len({o["payment_id"] for o in oks}) == 1)
    with Session(engine) as db:
        pays = db.query(M.Payment).filter_by(session_id=sid).all()
        recs = db.query(M.VatReceipt).filter(M.VatReceipt.payment_id.in_([p.id for p in pays])).all()
        check(f"нэг төлбөр ({len(pays)}), нэг баримт ({len(recs)})", len(pays) == 1 and len(recs) == 1)
    check(f"msgbill-д нэг илгээлт ({len(CALLS)})", len(CALLS) == 1)
    check("хожуу давталт ДДТД+QR-тай", c["receipt"]["ebarimt_status"] == "SENT" and bool(c["qr_data"]))


def cleanup():
    """Энэ (болон өмнө тасарсан) туршилтын PGT-* зогсоолуудын бүх мөрийг устгана."""
    U = "CAST(:{} AS uuid[])"
    with engine.begin() as c:
        sites = [str(r[0]) for r in c.execute(text("SELECT id FROM parking_sites WHERE site_code LIKE 'PGT-%'"))]
        if not sites:
            return
        tariffs = [str(r[0]) for r in c.execute(text(
            f"SELECT tariff_template_id FROM parking_sites WHERE id = ANY({U.format('s')}) "
            "AND tariff_template_id IS NOT NULL"), {"s": sites})]
        users = [str(r[0]) for r in c.execute(text("SELECT id FROM users WHERE username LIKE 'pgt-%'"))]
        sess = [str(r[0]) for r in c.execute(text(
            f"SELECT id FROM parking_sessions WHERE site_id = ANY({U.format('s')})"), {"s": sites})]
        pays = [str(r[0]) for r in c.execute(text(
            f"SELECT id FROM payments WHERE session_id = ANY({U.format('x')})"), {"x": sess})] if sess else []
        recs = [str(r[0]) for r in c.execute(text(
            f"SELECT id FROM vat_receipts WHERE payment_id = ANY({U.format('p')})"), {"p": pays})] if pays else []
        ids = pays + recs + sess + sites
        c.execute(text("DELETE FROM audit_logs WHERE entity_id = ANY(:i) OR username LIKE 'pgt-%'"), {"i": ids})
        if pays:
            c.execute(text(f"DELETE FROM financial_jobs WHERE payment_id = ANY({U.format('p')})"), {"p": pays})
            c.execute(text(f"DELETE FROM vat_receipts WHERE payment_id = ANY({U.format('p')})"), {"p": pays})
            c.execute(text(f"DELETE FROM payments WHERE id = ANY({U.format('p')})"), {"p": pays})
        if sess:
            c.execute(text(f"DELETE FROM barrier_commands WHERE session_id = ANY({U.format('x')})"), {"x": sess})
        c.execute(text(f"DELETE FROM barrier_commands WHERE device_id IN (SELECT id FROM devices "
                       f"WHERE site_id = ANY({U.format('s')}))"), {"s": sites})
        c.execute(text(f"DELETE FROM lpr_events WHERE site_id = ANY({U.format('s')})"), {"s": sites})
        c.execute(text(f"DELETE FROM parking_sessions WHERE site_id = ANY({U.format('s')})"), {"s": sites})
        c.execute(text(f"DELETE FROM devices WHERE site_id = ANY({U.format('s')})"), {"s": sites})
        if users:
            c.execute(text(f"DELETE FROM cashier_shifts WHERE user_id = ANY({U.format('u')})"), {"u": users})
            c.execute(text(f"DELETE FROM users WHERE id = ANY({U.format('u')})"), {"u": users})
        c.execute(text(f"DELETE FROM parking_sites WHERE id = ANY({U.format('s')})"), {"s": sites})
        if tariffs:
            c.execute(text(f"DELETE FROM tariff_templates WHERE id = ANY({U.format('t')})"), {"t": tariffs})


def main():
    guard()
    offline()
    print(f"POS шууд баримт — PG тест ({engine.url.database}, {TAG})")
    try:
        site_id, uid = setup()
        scenario_inline_vs_worker(site_id, uid)
        scenario_thread_claim_race(site_id)
        scenario_concurrent_duplicate_confirm(site_id, uid)
    finally:
        cleanup()
        with engine.connect() as c:
            left = sum(c.execute(text(q)).scalar() for q in (
                "SELECT count(*) FROM parking_sites WHERE site_code LIKE 'PGT-%'",
                "SELECT count(*) FROM payments", "SELECT count(*) FROM vat_receipts",
                "SELECT count(*) FROM financial_jobs", "SELECT count(*) FROM parking_sessions"))
        check("цэвэрлэгээ: туршилтын мөр үлдсэнгүй", left == 0)
    print(f"\nДҮН: {PASS} PASS, {FAIL} FAIL")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
