"""Түншийн баримтын API + webhook, өрийг ижил түрээслэгчээр хязгаарлах (локал DB).

    cd backend && venv/bin/python tests/test_partner_webhook_tenant_debts.py

Шалгах зүйл:
  1. _pending_debts(site): ижил түрээслэгчийн өр л нийлнэ; өөр түрээслэгчийнх үгүй;
     түрээслэгчгүй зогсоол = зөвхөн түрээслэгчгүйнх
  2. receipt_info: баримттай төлбөрт ddtd/lottery/amount, баримтгүйд None
  3. push_partner_webhook: локал HTTP серверт POST → 2xx ok, payload/толгой зөв;
     хаалттай порт → ok=False, error (унагаахгүй)
"""
import asyncio
import json
import os
import sys
import threading
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings  # noqa: E402

settings.barrier_mock = True

from app.database import SessionLocal  # noqa: E402
from app.models import (  # noqa: E402
    Compensation, ParkingSession, ParkingSite, Payment, Tenant, VatReceipt,
)
from app.routers.integration_router import push_partner_webhook, receipt_info  # noqa: E402
from app.routers.payments_router import _pending_debts  # noqa: E402

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗ <<< FAIL'} {name}{'' if cond else f'  [{extra}]'}")


PLATE = "7777ХТД"
db = SessionLocal()
made: list = []


def mk(obj):
    db.add(obj); db.flush(); made.append(obj); return obj


try:
    t1 = mk(Tenant(id=str(uuid.uuid4()), name="ZZ-T1", code=f"ZT1{uuid.uuid4().hex[:5]}"))
    t2 = mk(Tenant(id=str(uuid.uuid4()), name="ZZ-T2", code=f"ZT2{uuid.uuid4().hex[:5]}"))
    sa = mk(ParkingSite(id=str(uuid.uuid4()), name="ZZ-A", site_code=f"ZA{uuid.uuid4().hex[:6]}", tenant_id=t1.id))
    sb = mk(ParkingSite(id=str(uuid.uuid4()), name="ZZ-B", site_code=f"ZB{uuid.uuid4().hex[:6]}", tenant_id=t1.id))
    sc = mk(ParkingSite(id=str(uuid.uuid4()), name="ZZ-C", site_code=f"ZC{uuid.uuid4().hex[:6]}", tenant_id=t2.id))
    sd = mk(ParkingSite(id=str(uuid.uuid4()), name="ZZ-D", site_code=f"ZD{uuid.uuid4().hex[:6]}", tenant_id=None))
    for site, amt in ((sa, 1000), (sb, 2000), (sc, 4000), (sd, 8000)):
        mk(Compensation(id=str(uuid.uuid4()), site_id=site.id, plate_number=PLATE, amount=amt,
                        reason="unpaid_exit", status="PENDING"))
    db.commit()

    print("\n1. Өр — ижил түрээслэгчийн зогсоолуудынх л")
    amts = sorted(float(c.amount) for c in _pending_debts(db, PLATE, sa))
    check("T1 зогсоолд: T1-ийн A+B өр (1000, 2000), C/D үгүй", amts == [1000.0, 2000.0], amts)
    amts = sorted(float(c.amount) for c in _pending_debts(db, PLATE, sc))
    check("T2 зогсоолд: зөвхөн C (4000)", amts == [4000.0], amts)
    amts = sorted(float(c.amount) for c in _pending_debts(db, PLATE, sd))
    check("түрээслэгчгүй зогсоолд: зөвхөн D (8000)", amts == [8000.0], amts)
    amts = sorted(float(c.amount) for c in _pending_debts(db, PLATE))
    check("site өгөөгүй → бүгд (хуучин зан)", amts == [1000.0, 2000.0, 4000.0, 8000.0], amts)

    print("\n2. receipt_info")
    sess = mk(ParkingSession(id=str(uuid.uuid4()), site_id=sa.id, plate_number=PLATE,
                             entry_time=datetime.utcnow(), status="PAID", total_fee=2000))
    pay = mk(Payment(id=str(uuid.uuid4()), session_id=sess.id, provider="easywallet", payment_method="WALLET",
                     sender_invoice_no=f"ZZ-{uuid.uuid4().hex[:8]}", amount=2000, vat_amount=182,
                     status="PAID", paid_at=datetime.utcnow()))
    check("баримтгүй → None", receipt_info(db, pay) is None)
    mk(VatReceipt(id=str(uuid.uuid4()), payment_id=pay.id, session_id=sess.id, ebarimt_id="0" * 33,
                  lottery_code="TE 12345678", amount=2000, vat_amount=182, status="SENT", provider="MSGBILL"))
    db.commit()
    ri = receipt_info(db, pay)
    check("ddtd/lottery/amount/status", ri and ri["ddtd"] == "0" * 33 and ri["lottery"] == "TE 12345678"
          and ri["amount"] == 2000.0 and ri["status"] == "SENT" and ri["error"] is None, ri)

    print("\n3. push_partner_webhook")
    got = {}

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            got["body"] = json.loads(self.rfile.read(n) or b"{}")
            got["partner"] = self.headers.get("X-Parking-Partner")
            got["event"] = self.headers.get("X-Parking-Event")
            self.send_response(200); self.end_headers(); self.wfile.write(b'{"ok":true}')

        def log_message(self, *a):  # noqa: D102
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    payload = {"event": "payment.paid", "payment_id": pay.id, "ebarimt": ri}
    res = asyncio.run(push_partner_webhook(f"http://127.0.0.1:{port}/hook", payload, "easywallet"))
    check("2xx → ok", res["ok"] and res["status_code"] == 200, res)
    check("payload + толгой хүрсэн", got.get("body", {}).get("payment_id") == pay.id
          and got.get("partner") == "easywallet" and got.get("event") == "payment.paid", got)
    srv.shutdown()
    res = asyncio.run(push_partner_webhook("http://127.0.0.1:9/hook", payload, "easywallet", timeout=2))
    check("хаалттай порт → ok=False, error, унагаахгүй", res["ok"] is False and res["error"], res)

finally:
    db.rollback()
    ids = [o.id for o in made if isinstance(o, ParkingSession)]
    db.query(VatReceipt).filter(VatReceipt.session_id.in_(ids)).delete(synchronize_session=False) if ids else None
    db.query(Payment).filter(Payment.session_id.in_(ids)).delete(synchronize_session=False) if ids else None
    db.query(Compensation).filter(Compensation.plate_number == PLATE).delete(synchronize_session=False)
    db.query(ParkingSession).filter(ParkingSession.plate_number == PLATE).delete(synchronize_session=False)
    db.commit()
    for o in reversed(made):
        if isinstance(o, (Compensation, ParkingSession, Payment, VatReceipt)):
            continue
        try:
            db.delete(o); db.commit()
        except Exception as e:  # noqa: BLE001
            db.rollback(); print(f"  (цэвэрлэгээ: {e})")
    db.close()

print(f"\n{PASS} ✓  {FAIL} ✗")
sys.exit(1 if FAIL else 0)
