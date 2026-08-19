"""e-Barimt баримт ЦУЦЛАХ (Ибаримт хуудасны «Цуцлах») + цуцалсны дараа «Дахин үүсгэх».

    cd backend && venv/bin/python tests/test_ebarimt_cancel.py

  - QPAY баримт → qpay.cancel_ebarimt(payment_id) дуудагдаж CANCELLED
  - POSAPI баримт → ebarimt.delete_receipt(billId, date) → CANCELLED
  - MSGBILL баримт → msgbill DELETE 404 «Cannot DELETE» → NOT_SUPPORTED, баримт SENT хэвээр
  - SENT баримтгүй → ok=False
  - Цуцалсны дараа retry_ebarimt CANCELLED-ийг алгасаж ШИНЭ VatReceipt үүсгэнэ
"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime
from app.config import settings
from app.routers import payments_router as pr
from app.services import ebarimt, msgbill, qpay

PASS = FAIL = 0
def check(name, cond):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗ <<< FAIL'} {name}")

class Obj:
    def __init__(self, **kw): self.__dict__.update(kw)

class Rec(Obj):
    pass

class FakeQuery:
    def __init__(self, rows): self.rows = rows
    def filter(self, *a, **k): return self
    def order_by(self, *a, **k): return self
    def all(self): return [r for r in self.rows if r.status == "SENT" and r.ebarimt_id]
    def first(self): 
        live = [r for r in self.rows if r.status != "CANCELLED"]
        return live[-1] if live else None

class FakeDB:
    def __init__(self, rows): self.rows = rows; self.added = []
    def query(self, *a, **k): return FakeQuery(self.rows)
    def add(self, o): self.added.append(o); self.rows.append(o)
    def commit(self): pass
    def get(self, *a): return None

def pay(**kw):
    p = Obj(id="P1", session_id="S1", provider="QPAY", payment_method="QR", provider_payment_id="GP1",
            customer_tin=None, ebarimt_receiver_type="CITIZEN", amount=1000, vat_amount=91,
            status="PAID", session=Obj(plate_number="1234УБА", site=None))
    p.__dict__.update(kw); return p

class FakeResp:
    def __init__(self, status, j=None): self.status_code=status; self._j=j; self.content=b"x" if j is not None else b""; self.text=str(j)
    def json(self): return self._j
class FakeClient:
    responses = []; calls = []
    def __init__(self,*a,**k): pass
    async def __aenter__(self): return self
    async def __aexit__(self,*a): return False
    async def request(self, method, url, json=None, headers=None):
        FakeClient.calls.append((method, url)); return FakeClient.responses.pop(0)
    async def post(self, url, json=None, headers=None):
        FakeClient.calls.append(("POST", url)); return FakeClient.responses.pop(0)
    async def get(self, url, headers=None):
        FakeClient.calls.append(("GET", url)); return FakeClient.responses.pop(0)

async def run():
    settings.qpay_mock = True; settings.ebarimt_mock = True
    settings.msgbill_api_key = "bsk_live_X"; settings.msgbill_methods = "TRANSFER"; settings.secret_enc_key = ""
    msgbill.httpx.AsyncClient = FakeClient
    seen = {}
    async def _qcancel(pid, note="", acc=None): seen["qpay"] = (pid, note); return True
    async def _pdel(bill, date): seen["posapi"] = (bill, date); return True
    qpay.cancel_ebarimt, ebarimt.delete_receipt = _qcancel, _pdel

    print("QPAY баримт цуцлах:")
    rec = Rec(id="R1", payment_id="P1", ebarimt_id="BILL1", status="SENT", provider="QPAY", provider_ref=None,
              receipt_url=None, created_at=datetime(2026,8,19,10,0), lottery_code="L")
    db = FakeDB([rec]); res = await pr.cancel_ebarimt(db, pay(), "буруу")
    check("ok + CANCELLED", res["ok"] and rec.status == "CANCELLED" and res["cancelled"] == ["BILL1"])
    check("qpay.cancel_ebarimt(payment_id, note)", seen["qpay"] == ("GP1", "буруу"))

    print("\nPOSAPI баримт цуцлах (provider=None → POSAPI):")
    rec = Rec(id="R2", payment_id="P1", ebarimt_id="BILL2", status="SENT", provider=None, provider_ref=None,
              receipt_url=None, created_at=datetime(2026,8,19,10,0), lottery_code=None)
    db = FakeDB([rec]); res = await pr.cancel_ebarimt(db, pay(provider="CASH", payment_method="CASH", provider_payment_id=None), "x")
    check("ok", res["ok"] and rec.status == "CANCELLED")
    check("delete_receipt(billId, date)", seen["posapi"] == ("BILL2", "2026-08-19 10:00:00"))

    print("\nMSGBILL баримт — цуцлах API байхгүй (404 Cannot DELETE):")
    FakeClient.responses = [FakeResp(404, {"code":"NOT_FOUND","message_mn":"Cannot DELETE /api/v1/partner/receipts/abc"})]
    rec = Rec(id="R3", payment_id="P1", ebarimt_id="BILL3", status="SENT", provider="MSGBILL", provider_ref="abc",
              receipt_url=None, created_at=datetime(2026,8,19,10,0), lottery_code=None)
    db = FakeDB([rec]); res = await pr.cancel_ebarimt(db, pay(provider="TRANSFER", payment_method="TRANSFER", provider_payment_id=None), "x")
    check("ok=False, SENT хэвээр", not res["ok"] and rec.status == "SENT")
    check("алдаанд «хараахан байхгүй»", "хараахан байхгүй" in (res["error"] or ""))
    check("DELETE /partner/receipts/abc дуудсан", FakeClient.calls[-1] == ("DELETE", "https://msgbill.mn/api/v1/partner/receipts/abc"))

    print("\nMSGBILL — msgbill цуцлах нэмэгдсэн үед (200):")
    FakeClient.responses = [FakeResp(200, {"id":"abc","state":"CANCELLED"})]
    rec.status = "SENT"; db = FakeDB([rec])
    res = await pr.cancel_ebarimt(db, pay(provider="TRANSFER", payment_method="TRANSFER", provider_payment_id=None), "x")
    check("ok + CANCELLED", res["ok"] and rec.status == "CANCELLED")

    print("\nSENT баримтгүй:")
    db = FakeDB([Rec(id="R4", payment_id="P1", ebarimt_id=None, status="FAILED", provider="QPAY", provider_ref=None, receipt_url="e", created_at=None, lottery_code=None)])
    res = await pr.cancel_ebarimt(db, pay(), "x")
    check("ok=False", not res["ok"])

    print("\nЦуцалсны дараа retry → ШИНЭ баримт (QPay mock):")
    canc = Rec(id="R5", payment_id="P1", ebarimt_id="OLD", status="CANCELLED", provider="QPAY", provider_ref=None, receipt_url="Цуцлав", created_at=datetime.utcnow(), lottery_code=None)
    db = FakeDB([canc])
    async def _fake_mark(*a, **k): pass
    res = await pr.retry_ebarimt(db, pay())
    check("ok + шинэ ebarimt_id", res.get("ok") and res.get("ebarimt_id") and res["ebarimt_id"] != "OLD")
    check("шинэ VatReceipt мөр нэмэгдсэн, хуучин CANCELLED хэвээр", len(db.added) == 1 and canc.status == "CANCELLED" and db.added[0].provider == "QPAY")

    print(f"\n{'='*40}\nҮР ДҮН: {PASS} passed, {FAIL} failed")
    return FAIL == 0

if __name__ == "__main__":
    sys.exit(0 if asyncio.run(run()) else 1)
