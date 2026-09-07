"""ДДТД нэг эх үнэн (2026-09-07): баримт сонголт + ДДТД дарж бичихээс хамгаалалт.

9723УБТ (3-р эмнэлэг): хэвлэсэн …1797, хадгалсан …1796, ebarimt.mn …0851 — нэг
төлбөрт 3 дугаар. Энд: (1) олон мөрөөс албан ёсны баримтыг тогтвортой сонгох,
(2) webhook/retry/diag аль ч сувгаас SENT баримтын ДДТД-г өөр дугаараар ДАРАХГҮЙ.
"""
from datetime import datetime, timedelta
from types import SimpleNamespace as NS

from app.routers.payments_router import _apply_msgbill_event
from app.services.receipts import assign_ebarimt_id, choose_primary


class FakeDb:
    def __init__(self):
        self.added = []

    def add(self, obj):
        self.added.append(obj)

    def audits(self, action=None):
        return [a for a in self.added if a.__class__.__name__ == "AuditLog"
                and (action is None or a.action == action)]


def rec(status, ebarimt_id, mins, **kw):
    return NS(id=f"r-{status}-{ebarimt_id}", payment_id="p1", status=status, ebarimt_id=ebarimt_id,
              lottery_code=kw.get("lottery"), customer_tin=kw.get("tin"), provider="MSGBILL",
              provider_ref="rcp_1", raw=None, ddtd_note=None, receipt_url=None,
              created_at=datetime(2026, 9, 7) + timedelta(minutes=mins))


def test_primary_prefers_sent_then_earliest():
    head = rec("SENT", "A", 0)
    debt = rec("SENT", "B", 0)          # өрийн баримт — head-ээс ХОЙШ нэмэгддэг
    debt.created_at = head.created_at + timedelta(microseconds=1)
    old_cancel = rec("CANCELLED", "C", -5)
    failed = rec("FAILED", None, -3)
    assert choose_primary([old_cancel, failed, debt, head]) is head
    assert choose_primary([old_cancel, failed]) is failed
    assert choose_primary([old_cancel]) is old_cancel
    assert choose_primary([]) is None


def test_primary_after_cancel_and_retry_is_new_sent():
    cancelled = rec("CANCELLED", "OLD", 0)
    new = rec("SENT", "NEW", 10)
    assert choose_primary([cancelled, new]).ebarimt_id == "NEW"


def test_assign_conflict_keeps_stored_and_audits():
    db = FakeDb()
    r = rec("SENT", "0291…1797", 0, lottery="L1")
    ok = assign_ebarimt_id(db, r, "0291…1796", source="webhook.receipt.created", lottery="L2",
                           raw={"receipt_no": "0291…1796"})
    assert ok is False
    assert r.ebarimt_id == "0291…1797" and r.lottery_code == "L1"
    assert "1796" in r.ddtd_note and "1797" in r.ddtd_note
    c = db.audits("EBARIMT_ID_CONFLICT")
    assert len(c) == 1 and c[0].detail["stored"] == "0291…1797" and c[0].detail["incoming"] == "0291…1796"
    assert r.raw["webhook.receipt.created"]["receipt_no"] == "0291…1796"   # нотолгоо хадгалагдана


def test_assign_same_or_empty_is_quiet():
    db = FakeDb()
    r = rec("SENT", "X", 0)
    assert assign_ebarimt_id(db, r, "X", source="webhook.receipt.created") is True
    assert assign_ebarimt_id(db, r, None, source="webhook.receipt.created") is False
    assert db.audits() == [] and r.ddtd_note is None


def test_assign_sets_empty_row_with_audit_and_lottery_rules():
    db = FakeDb()
    r = rec("FAILED", None, 0)
    assert assign_ebarimt_id(db, r, "NEW", source="retry", lottery="LOT") is True
    assert r.ebarimt_id == "NEW" and r.lottery_code == "LOT"
    s = db.audits("EBARIMT_ID_SET")
    assert len(s) == 1 and s[0].detail["from"] is None and s[0].detail["to"] == "NEW"
    # ААН баримтад сугалаа бичигдэхгүй
    r2 = rec("FAILED", None, 0, tin="1234567")
    assign_ebarimt_id(db, r2, "N2", source="retry", lottery="LOT")
    assert r2.lottery_code is None


def test_allow_replace_only_for_explicit_retry():
    db = FakeDb()
    r = rec("SENT", "OLD", 0)
    assert assign_ebarimt_id(db, r, "NEW", source="retry", allow_replace=True) is True
    assert r.ebarimt_id == "NEW" and db.audits("EBARIMT_ID_SET")[0].detail["from"] == "OLD"
    # Цуцлагдсан мөрийг шинэ дугаараар нөхөх нь зөрчил биш (SENT биш)
    r2 = rec("CANCELLED", "OLD", 0)
    assert assign_ebarimt_id(db, r2, "NEW", source="retry") is True


def test_webhook_created_conflict_and_same():
    db = FakeDb()
    r = rec("SENT", "PRINTED", 0)
    assert _apply_msgbill_event(db, r, "receipt.created", {"receipt_no": "OTHER", "lottery": "Z"}) == "conflict"
    assert r.ebarimt_id == "PRINTED" and r.status == "SENT"
    assert _apply_msgbill_event(db, r, "receipt.created", {"receipt_no": "PRINTED"}) == "same"
    assert len(db.audits("EBARIMT_ID_CONFLICT")) == 1


def test_webhook_created_fills_pending_row():
    db = FakeDb()
    r = rec("PENDING", None, 0)
    r.provider = None
    assert _apply_msgbill_event(db, r, "receipt.created", {"receipt_no": "N1", "lottery": "L"}) == "set"
    assert (r.ebarimt_id, r.lottery_code, r.status, r.provider) == ("N1", "L", "SENT", "MSGBILL")


def test_webhook_cancelled_other_number_flags_conflict():
    db = FakeDb()
    r = rec("SENT", "STORED", 0)
    assert _apply_msgbill_event(db, r, "receipt.cancelled", {"receipt_no": "OTHER"}) == "cancelled"
    assert r.status == "CANCELLED" and "OTHER" in r.ddtd_note and "STORED" in r.ddtd_note
    assert r.raw["webhook.receipt.cancelled"]["receipt_no"] == "OTHER"
    assert len(db.audits("EBARIMT_ID_CONFLICT")) == 1
    # Ижил дугаар цуцлагдвал зөрчил биш
    r2 = rec("SENT", "STORED", 0)
    assert _apply_msgbill_event(db, r2, "receipt.cancelled", {"receipt_no": "STORED"}) == "cancelled"
    assert r2.ddtd_note is None


def test_webhook_created_on_cancelled_is_stale():
    db = FakeDb()
    r = rec("CANCELLED", "X", 0)
    assert _apply_msgbill_event(db, r, "receipt.created", {"receipt_no": "Y"}) == "stale"
    assert r.ebarimt_id == "X" and r.status == "CANCELLED"
