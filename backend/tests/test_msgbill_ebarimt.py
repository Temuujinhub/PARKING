"""msgbill.mn eBarimt API интеграц — дансаар (online operator) төлбөрийн баримт.

    cd backend && venv/bin/python tests/test_msgbill_ebarimt.py

Шалгах зүйл:
  - msgbill.normalize: CREATED → billId=receipt_no; FAILED → billId=None + error
  - түлхүүрийн шатлал: түрээслэгч → глобал; өөрийн QPay данстай түрээслэгч глобал руу унахгүй
  - methods: анхдагч TRANSFER л; ALL бүгд
  - _finalize_paid: TRANSFER + msgbill идэвхтэй → msgbill-ээр (BANK_TRANSFER,
    Idempotency-Key=pay-<id>), VatReceipt provider=MSGBILL/provider_ref
  - CASH (msgbill_methods=TRANSFER) → хуучин локал PosAPI зам хэвээр
  - QPay төлбөр → QPay ebarimt_v3 хэвээр (msgbill оролцохгүй)
  - msgbill FAILED → төлбөр PAID хэвээр (хаалт нээгдэнэ), VatReceipt FAILED + provider_ref
  - msgbill HTTP алдаа (429) → мөн адил, алдааны текст receipt_url-д
  - retry_ebarimt: provider_ref-тэй FAILED баримт → GET-ээр CREATED болсныг нөхнө
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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


class FakePayment:
    def __init__(self, **kw):
        self.id = kw.get("id", "PAY1")
        self.session_id = "SESS1"
        self.provider = kw.get("provider", "TRANSFER")
        self.payment_method = kw.get("payment_method", "TRANSFER")
        self.provider_payment_id = kw.get("provider_payment_id")
        self.ebarimt_receiver_type = kw.get("ebarimt_receiver_type")
        self.customer_tin = kw.get("customer_tin")
        self.amount = 5000
        self.vat_amount = 454
        self.status = kw.get("status", "PENDING")
        self.paid_at = None
        self.raw_payload = {}
        self.session = Obj(plate_number="1234УБА", site=kw.get("site"))


class FakeQuery:
    def __init__(self, store): self.store = store
    def filter(self, *a, **k): return self
    def order_by(self, *a, **k): return self
    def first(self): return self.store.get("receipt")
    def all(self): return []


class FakeDB:
    def __init__(self): self.added = []; self.store = {}
    def get(self, model, _id): return Obj(plate_number="1234УБА")
    def add(self, obj):
        self.added.append(obj)
        if obj.__class__.__name__ == "VatReceipt":
            self.store["receipt"] = obj
    def query(self, *a, **k): return FakeQuery(self.store)
    def commit(self): pass


CREATED = {"id": "rcp_1", "state": "CREATED", "receipt_no": "0000123456",
           "lottery": "AB12345678", "qr_data": "QRDATA", "receipt_type": "CITIZEN", "error": None}
FAILED = {"id": "rcp_2", "state": "FAILED", "receipt_no": None, "lottery": None,
          "qr_data": None, "receipt_type": "CITIZEN",
          "error": "merchantTin 15200020090 is not registered on the POS API instance"}


class FakeResp:
    def __init__(self, status, json_=None, text=""):
        self.status_code = status; self._j = json_; self.text = text
        self.content = b"x" if json_ is not None else b""
    def json(self):
        if self._j is None: raise ValueError("no json")
        return self._j


class FakeClient:
    """httpx.AsyncClient орлуулагч — илгээсэн хүсэлтийг бүртгэж, тохируулсан хариу буцаана."""
    calls = []
    responses = []
    def __init__(self, *a, **k): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def post(self, url, json=None, headers=None):
        FakeClient.calls.append(("POST", url, json, headers))
        return FakeClient.responses.pop(0)
    async def get(self, url, headers=None):
        FakeClient.calls.append(("GET", url, None, headers))
        return FakeClient.responses.pop(0)


async def run():
    settings.qpay_mock = True
    settings.qpay_ebarimt = True
    settings.ebarimt_mock = True
    settings.ebarimt_mock_receipts = True  # тестэд MOCK баримт (SENT) хэрэгтэй
    settings.msgbill_api_key = "bsk_live_TEST"
    settings.msgbill_methods = "TRANSFER"
    settings.secret_enc_key = ""   # decrypt plaintext-ийг байгаагаар нь

    async def _fake_mark(db, session, grace_minutes=None): pass
    pr.mark_paid_and_open = _fake_mark
    msgbill.httpx.AsyncClient = FakeClient

    print("normalize:")
    n = msgbill.normalize(CREATED)
    check("CREATED → billId=receipt_no", n["billId"] == "0000123456" and n["status"] == "SUCCESS")
    check("lottery/qrData/msgbillId", n["lottery"] == "AB12345678" and n["qrData"] == "QRDATA" and n["msgbillId"] == "rcp_1")
    n = msgbill.normalize(FAILED)
    check("FAILED → billId=None + error", n["billId"] is None and "merchantTin" in n["error"] and n["state"] == "FAILED")

    print("\nтүлхүүрийн шатлал:")
    site_plain = Obj(tenant_id=None, qpay_username=None, qpay_password=None)
    check("глобал түлхүүр (түрээслэгчгүй зогсоол)", msgbill.api_key_for(site_plain).scope == "global")
    check("None site → глобал", msgbill.api_key_for(None).enabled)
    ten_own_qpay = Obj(name="Monnis", qpay_username="MONNIS", qpay_password="pw", msgbill_api_key=None)
    orig_tenant_of = msgbill._tenant_of
    msgbill._tenant_of = lambda s: getattr(s, "_ten", None)
    site_monnis = Obj(tenant_id="T1", qpay_username=None, qpay_password=None, _ten=ten_own_qpay)
    check("өөрийн QPay данстай түрээслэгч глобал руу УНАХГҮЙ", not msgbill.api_key_for(site_monnis).enabled)
    ten_own_qpay.msgbill_api_key = "bsk_live_MONNIS"
    a = msgbill.api_key_for(site_monnis)
    check("түрээслэгчийн өөрийн түлхүүр", a.scope == "tenant" and a.api_key == "bsk_live_MONNIS")
    ten_own_qpay.msgbill_api_key = None
    check("TRANSFER идэвхтэй, CASH идэвхгүй",
          msgbill.method_enabled("TRANSFER") and not msgbill.method_enabled("CASH"))
    settings.msgbill_methods = "ALL"
    check("ALL → CASH/CARD ч идэвхтэй", msgbill.method_enabled("CASH") and msgbill.method_enabled("CARD"))
    settings.msgbill_methods = "TRANSFER"

    # spy: локал/QPay замууд
    seen = {"local": 0, "qpay": 0}
    orig_local, orig_qpay = ebarimt.create_receipt, qpay.create_ebarimt
    async def _spy_local(*a, **k): seen["local"] += 1; return await orig_local(*a, **k)
    async def _spy_qpay(*a, **k): seen["qpay"] += 1; return await orig_qpay(*a, **k)
    ebarimt.create_receipt, qpay.create_ebarimt = _spy_local, _spy_qpay

    print("\nTRANSFER → msgbill (CREATED):")
    FakeClient.calls, FakeClient.responses = [], [FakeResp(201, CREATED)]
    db = FakeDB(); p = FakePayment(site=site_plain)
    await pr._finalize_paid(db, p)
    check("PAID болов", p.status == "PAID")
    check("msgbill POST /partner/receipts дуудагдсан",
          len(FakeClient.calls) == 1 and FakeClient.calls[0][1].endswith("/partner/receipts"))
    body, hdr = FakeClient.calls[0][2], FakeClient.calls[0][3]
    check("payment_method=BANK_TRANSFER, receipt_type=CITIZEN, amount=5000",
          body["payment_method"] == "BANK_TRANSFER" and body["receipt_type"] == "CITIZEN" and body["amount"] == 5000)
    check("Idempotency-Key = pay-<payment id>", hdr["Idempotency-Key"] == "pay-PAY1")
    check("X-Api-Key дамжсан", hdr["X-Api-Key"] == "bsk_live_TEST")
    check("тайлбарт дугаар байна", "1234УБА" in body["description"])
    rec = db.store["receipt"]
    check("VatReceipt SENT, ДДТД, provider=MSGBILL, provider_ref",
          rec.status == "SENT" and rec.ebarimt_id == "0000123456"
          and rec.provider == "MSGBILL" and rec.provider_ref == "rcp_1")
    check("сугалаа CITIZEN-д бий", rec.lottery_code == "AB12345678")
    check("QR түр санах ойд", ebarimt.get_cached_qr("PAY1") == "QRDATA")
    check("локал PosAPI/QPay ДУУДАГДААГҮЙ", seen["local"] == 0 and seen["qpay"] == 0)

    print("\nTRANSFER + ААН ТТД → ORGANIZATION:")
    FakeClient.calls, FakeClient.responses = [], [FakeResp(201, {**CREATED, "id": "rcp_3", "lottery": None})]
    db = FakeDB(); p = FakePayment(id="PAY_ORG", customer_tin="1234567", ebarimt_receiver_type="COMPANY", site=site_plain)
    await pr._finalize_paid(db, p)
    body = FakeClient.calls[0][2]
    check("receipt_type=ORGANIZATION + payer_reg_no", body["receipt_type"] == "ORGANIZATION" and body["payer_reg_no"] == "1234567")
    check("ААН-д сугалаа хадгалахгүй", db.store["receipt"].lottery_code is None)

    print("\nCASH → локал PosAPI хэвээр (msgbill_methods=TRANSFER):")
    FakeClient.calls = []
    db = FakeDB(); p = FakePayment(id="PAY_CASH", provider="CASH", payment_method="CASH", site=site_plain)
    await pr._finalize_paid(db, p)
    check("msgbill дуудагдаагүй, локал 1 удаа", len(FakeClient.calls) == 0 and seen["local"] == 1)
    check("provider=POSAPI", db.store["receipt"].provider == "POSAPI")

    print("\nQPay төлбөр → QPay ebarimt_v3 хэвээр:")
    FakeClient.calls = []
    db = FakeDB(); p = FakePayment(id="PAY_QR", provider="QPAY", payment_method="QR",
                                   provider_payment_id="GP1", site=site_plain)
    await pr._finalize_paid(db, p)
    check("QPay зам 1, msgbill 0", seen["qpay"] == 1 and len(FakeClient.calls) == 0)
    check("provider=QPAY", db.store["receipt"].provider == "QPAY")

    print("\nmsgbill FAILED → төлбөр PAID хэвээр, баримт FAILED:")
    FakeClient.calls, FakeClient.responses = [], [FakeResp(201, FAILED)]
    db = FakeDB(); p = FakePayment(id="PAY_F", site=site_plain)
    await pr._finalize_paid(db, p)
    rec = db.store["receipt"]
    check("PAID (хаалт нээгдэнэ)", p.status == "PAID")
    check("VatReceipt FAILED + алдаа + provider_ref=rcp_2",
          rec.status == "FAILED" and "merchantTin" in (rec.receipt_url or "") and rec.provider_ref == "rcp_2")

    print("\nmsgbill 429 QUOTA → PAID хэвээр, алдаа бүртгэгдэнэ:")
    FakeClient.calls, FakeClient.responses = [], [FakeResp(429, {"code": "RECEIPT_QUOTA_EXCEEDED",
                                                                  "message_mn": "Сарын хязгаар дүүрсэн."})]
    db = FakeDB(); p = FakePayment(id="PAY_Q", site=site_plain)
    await pr._finalize_paid(db, p)
    rec = db.store["receipt"]
    check("PAID", p.status == "PAID")
    check("FAILED + message_mn", rec.status == "FAILED" and "Сарын хязгаар" in rec.receipt_url)

    print("\nretry_ebarimt: provider_ref-тэй FAILED → GET-ээр CREATED нөхнө:")
    FakeClient.calls, FakeClient.responses = [], [FakeResp(200, {**CREATED, "id": "rcp_2"})]
    db = FakeDB(); p = FakePayment(id="PAY_R", status="PAID", site=site_plain)
    rec = Obj(ebarimt_id=None, lottery_code=None, status="FAILED", receipt_url="x",
              provider="MSGBILL", provider_ref="rcp_2", created_at=None)
    db.store["receipt"] = rec
    res = await pr.retry_ebarimt(db, p)
    check("ok + GET л дуудагдсан (дахин POST хийгээгүй)",
          res.get("ok") and len(FakeClient.calls) == 1 and FakeClient.calls[0][0] == "GET"
          and FakeClient.calls[0][1].endswith("/partner/receipts/rcp_2"))
    check("rec SENT + ДДТД", rec.status == "SENT" and rec.ebarimt_id == "0000123456")

    print("\nretry_ebarimt: GET одоо ч FAILED → шинэ Idempotency-Key-ээр дахин POST:")
    FakeClient.calls, FakeClient.responses = [], [FakeResp(200, FAILED), FakeResp(201, {**CREATED, "id": "rcp_9"})]
    db = FakeDB(); p = FakePayment(id="PAY_R2", status="PAID", site=site_plain)
    rec = Obj(ebarimt_id=None, lottery_code=None, status="FAILED", receipt_url="x",
              provider="MSGBILL", provider_ref="rcp_2", created_at=None)
    db.store["receipt"] = rec
    res = await pr.retry_ebarimt(db, p)
    check("GET → POST дараалал", [c[0] for c in FakeClient.calls] == ["GET", "POST"])
    check("шинэ Idempotency-Key (retry)", FakeClient.calls[1][3]["Idempotency-Key"].startswith("pay-PAY_R2-retry-"))
    check("ok, provider_ref шинэчлэгдсэн", res.get("ok") and rec.provider_ref == "rcp_9" and rec.status == "SENT")

    print("\nmsgbill идэвхгүй (түлхүүргүй) → TRANSFER локал PosAPI руу буцна:")
    settings.msgbill_api_key = ""
    FakeClient.calls = []; seen["local"] = 0
    db = FakeDB(); p = FakePayment(id="PAY_NOKEY", site=site_plain)
    await pr._finalize_paid(db, p)
    check("локал зам", seen["local"] == 1 and len(FakeClient.calls) == 0)

    print("\nmsgbill түлхүүргүй + PosAPI MOCK (mock_receipts=False) → ХУУРАМЧ баримт ҮҮСГЭХГҮЙ, FAILED:")
    settings.ebarimt_mock_receipts = False
    seen["local"] = 0
    db = FakeDB(); p = FakePayment(id="PAY_NOCH", provider="CASH", payment_method="CASH", site=site_plain)
    await pr._finalize_paid(db, p)
    rec = db.store["receipt"]
    check("PAID хэвээр (хаалт нээгдэнэ)", p.status == "PAID")
    check("VatReceipt FAILED + «суваг байхгүй», ДДТД байхгүй, локал дуудаагүй",
          rec.status == "FAILED" and "суваг байхгүй" in (rec.receipt_url or "") and not rec.ebarimt_id and seen["local"] == 0)
    res = await pr.retry_ebarimt(db, FakePayment(id="PAY_NOCH", provider="CASH", payment_method="CASH", status="PAID", site=site_plain))
    check("retry ч суваг байхгүй гэж FAILED", not res.get("ok") and "суваг байхгүй" in res.get("error", ""))
    settings.ebarimt_mock_receipts = True

    ebarimt.create_receipt, qpay.create_ebarimt = orig_local, orig_qpay
    msgbill._tenant_of = orig_tenant_of
    print(f"\n{'='*40}\nҮР ДҮН: {PASS} passed, {FAIL} failed")
    return FAIL == 0


if __name__ == "__main__":
    sys.exit(0 if asyncio.run(run()) else 1)
