"""ТЕГ тулгалт → POS терминалын баримттай ДАВХАРДСАН манай msgbill баримтыг олох/цуцлах.

API (reports_router: vat-reconcile, vat-reconcile/cancel-duplicates) болон серверийн
CLI (tools/vat_cancel_duplicates.py) хоёулаа ЭНЭ НЭГ модулийг дуудна — «UI дээр 63
гэж харуулаад CLI 61-ийг цуцалсан» гэх зөрүү гарахгүй.

Давхардал гэж (2026-10-13, 08-24-ний продын датагаар батлав): касс дээр Ontime POS
терминал (ТЕГ-ийн ДДТД …10002990) баримт хэвлэсэн БЭЛЭН/КАРТЫН төлбөрт манай систем
msgbill-ээр (…10045952) ДАХИН баримт гаргасан. Нэг борлуулалт ТЕГ-т хоёр удаа
бүртгэгдэнэ. Бид зөвхөн ӨӨРИЙН (msgbill) баримтаа цуцалж чадна.

Хуваарь:
  • load_*  — DB-ээс УНШИНА (бичихгүй)
  • analyze — цэвэр функц (thread дээр ажиллуулж болно)
  • cancel_one — НЭГ төлбөрийн манай msgbill баримтыг цуцална (түгжээ, дахин
    шалгалт, идемпотент, AuditLog, төлбөр бүрд commit)
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import func

from ..config import settings
from ..models import AuditLog, ParkingSession, ParkingSite, Payment, VatReceipt
from ..routers import vat_recon as VR
from .receipts import DUPLICATE_OF_POS, choose_primary

log = logging.getLogger("parking.vat_duplicates")

PAY_SITE = func.coalesce(Payment.site_id, ParkingSession.site_id)
PAY_PLATE = func.coalesce(ParkingSession.plate_number, Payment.raw_payload["plate_number"].as_string())


def _site_filter(q, site_ids):
    """site_ids: None = бүх зогсоол | str | list."""
    if site_ids is None:
        return q
    if isinstance(site_ids, str):
        return q.filter(PAY_SITE == site_ids)
    return q.filter(PAY_SITE.in_(list(site_ids)))


def query_window(tax: list, shifts: list[float]) -> tuple[datetime, datetime]:
    """Аль ч цагийн шилжилтэд баримт багтахаар өргөсгөсөн муж."""
    pad = timedelta(hours=max(abs(s) for s in shifts) + 1)
    return min(t["dt"] for t in tax) - pad, max(t["dt"] for t in tax) + pad


def load_ours(db, lo: datetime, hi: datetime, site_ids) -> list:
    """Манай баримтууд (бүх төлөв) — [(VatReceipt, Payment, plate, site_name)]."""
    # enable_eagerloads(False): Payment-ийн lazy="joined" (session→site→tariff, discount)
    # 5+ илүү JOIN-ийг хасна — сарын файлд хэдэн арван мянган мөр татдаг
    q = (db.query(VatReceipt, Payment, PAY_PLATE, ParkingSite.name).enable_eagerloads(False)
         .join(Payment, VatReceipt.payment_id == Payment.id)
         .outerjoin(ParkingSession, VatReceipt.session_id == ParkingSession.id)
         .outerjoin(ParkingSite, PAY_SITE == ParkingSite.id)
         .filter(Payment.paid_at >= lo, Payment.paid_at < hi))
    return _site_filter(q, site_ids).all()


def receipt_info(r) -> dict:
    return {"id": r.id, "provider": r.provider, "status": r.status,
            "provider_ref": r.provider_ref, "ebarimt_id": r.ebarimt_id,
            "amount": float(r.amount or 0), "mock": VR.is_mock_receipt(r)}


def payment_probe(pay, recs: list, plate=None, site=None) -> dict:
    """Төлбөрийн «давхардлын шалгалт»-д хэрэгтэй бүх мэдээлэл (DB-ээс салгасан dict)."""
    prim = choose_primary(recs)
    return {"id": pay.id, "paid_at": pay.paid_at, "amount": float(pay.amount),
            "site_id": getattr(site, "id", None), "site_name": getattr(site, "name", None),
            "site_code": getattr(site, "site_code", None), "plate": plate,
            "provider": pay.provider, "method": pay.payment_method,
            "receipts": [r.ebarimt_id for r in recs],
            "primary": receipt_info(prim) if prim is not None else None,
            "active": [receipt_info(r) for r in recs
                       if r.status in VR.ACTIVE_STATUSES and r.ebarimt_id],
            "cancelled_msgbill": any(r.status == "CANCELLED" and r.provider == "MSGBILL"
                                     for r in recs)}


def load_probe(db, lo: datetime, hi: datetime, site_ids) -> list[dict]:
    """PAID төлбөрүүд (баримттай эсэхээс үл хамааран) + бүх баримт + үндсэн баримт."""
    q = (db.query(Payment, PAY_PLATE, ParkingSite).enable_eagerloads(False)
         .outerjoin(ParkingSession, Payment.session_id == ParkingSession.id)
         .outerjoin(ParkingSite, PAY_SITE == ParkingSite.id)
         .filter(Payment.paid_at >= lo, Payment.paid_at <= hi, Payment.status == "PAID"))
    rows = _site_filter(q, site_ids).all()
    recs: dict[str, list] = {}
    ids = [p.id for p, *_ in rows]
    for i in range(0, len(ids), 1000):
        for r in db.query(VatReceipt).filter(VatReceipt.payment_id.in_(ids[i:i + 1000])).all():
            recs.setdefault(r.payment_id, []).append(r)
    return [payment_probe(p, recs.get(p.id, []), plate, site) for p, plate, site in rows]


def analyze(tax: list, ours: list, probe: list, *, shifts: list[float], tol: int,
            scope_ids: set | None = None) -> dict:
    """Цэвэр тулгалт: кассын шилжилт → манай баримттай нэг-нэгээр → үлдсэн ТЕГ
    мөрийг төлбөрөөр тайлах → давхардлын БҮТЭН жагсаалт."""
    r = VR.best_shift(tax, ours, shifts, tol, probe=probe)
    left = [t for t in tax if not t["used"]]
    matched_pay_ids = {t["ours"][1].id for t in tax if t.get("ours")}
    explained = VR.explain_unmatched_tax(left, probe, r["shift"], tol, matched_pay_ids, scope_ids)
    dups = VR.duplicate_list(explained, probe, matched_pay_ids, settings.tz_offset_hours)
    return {"r": r, "left": left, "explained": explained, "matched_pay_ids": matched_pay_ids,
            "duplicates": dups}


def reconcile_db(db, tax: list, *, shifts: list[float], tol: int, ours_sites=None,
                 probe_sites=None, scope_ids: set | None = None) -> dict:
    """DB-ээс уншаад analyze. API нь үүнийг thread дээр ӨӨРИЙН session-тэй дууддаг
    (event loop-ыг блоклохгүй — хаалт/LPR царцахаас сэргийлнэ), CLI шууд."""
    lo, hi = query_window(tax, shifts)
    ours = load_ours(db, lo, hi, ours_sites)
    probe = load_probe(db, lo, hi, probe_sites)
    out = analyze(tax, ours, probe, shifts=shifts, tol=tol, scope_ids=scope_ids)
    out.update({"ours": ours, "probe": probe})
    return out


# ───────────────────────── цуцлах ─────────────────────────────────────────

def cancel_ready(db=None, sites: list | None = None) -> list[str]:
    """БОДИТ цуцлалт эхлүүлэхээс өмнөх хамгаалалт. Хоосон жагсаалт = бэлэн.

    «msgbill хуурамч» гэдэг нь: MOCK баримт асаалттай тест сервер, ТЕСТ түлхүүр
    (bsk_test_ — msgbill симуляц хийдэг, ТЕГ-т юу ч цуцлагдахгүй) эсвэл localhost
    хаяг. Түлхүүргүй зогсоол байвал түүний баримтыг цуцалж чадахгүй."""
    from . import msgbill
    probs = []
    if settings.ebarimt_mock_receipts:
        probs.append("PARKING_EBARIMT_MOCK_RECEIPTS=true — MOCK баримттай тест сервер; бодит цуцлалт хийхгүй")
    base = (settings.msgbill_base_url or "").lower()
    if not base.startswith("https://") or any(x in base for x in ("localhost", "127.0.0.1", "mock")):
        probs.append(f"msgbill хаяг бодит биш ({settings.msgbill_base_url}) — msgbill MOCK горимд байна")
    seen = set()
    for site in sites or []:
        sid = getattr(site, "id", None)
        if sid in seen:
            continue
        seen.add(sid)
        accs = msgbill.cancel_accounts(site)
        name = getattr(site, "name", None) or sid
        if not accs:
            probs.append(f"«{name}»: msgbill түлхүүр тохируулаагүй (түрээслэгч/глобал аль нь ч алга)")
        elif all(a.is_test for a in accs):
            probs.append(f"«{name}»: msgbill ТЕСТ түлхүүр (bsk_test_) — бодит баримт цуцлагдахгүй")
    return probs


def _info(payment, site, recs) -> dict:
    return {"payment_id": payment.id, "site_name": getattr(site, "name", "") or "",
            "amount": float(payment.amount), "method": payment.payment_method,
            "paid_at": payment.paid_at.isoformat() if payment.paid_at else None,
            "receipts": [{"ddtd": r.ebarimt_id, "provider": r.provider, "status": r.status}
                         for r in recs]}


def precheck(db, payment_id: str, allowed_sites: set | None = None) -> dict:
    """DRY-RUN — ЮУ цуцлагдахыг (эсвэл яагаад үгүйг) буцаана. Юу ч бичихгүй."""
    from . import msgbill
    from ..routers.payments_router import _site_of
    payment = db.get(Payment, payment_id)
    if not payment:
        return {"payment_id": payment_id, "ok": False, "error": "Төлбөр олдсонгүй"}
    site = _site_of(payment)
    if allowed_sites is not None and (site is None or site.id not in allowed_sites):
        return {"payment_id": payment_id, "ok": False, "error": "Энэ зогсоолын эрхгүй"}
    recs = db.query(VatReceipt).filter(VatReceipt.payment_id == payment_id).all()
    info = _info(payment, site, recs)
    p = payment_probe(payment, recs, None, site)
    if not p["active"] and p["cancelled_msgbill"]:
        return {**info, "ok": False, "already": True, "error": "Аль хэдийн цуцлагдсан"}
    reason = VR.cancel_block_reason(p)
    if reason is None and not msgbill.cancel_accounts(site):
        reason = "msgbill түлхүүр тохируулаагүй"
    return {**info, "ok": reason is None, "error": reason,
            "will_cancel": [{"ddtd": a["ebarimt_id"], "provider": a["provider"],
                             "provider_ref": a["provider_ref"]} for a in p["active"]]}


async def cancel_one(db, payment_id: str, *, note: str, username: str,
                     teg: dict | None = None, allowed_sites: set | None = None,
                     source: str = "api") -> dict:
    """НЭГ төлбөрийн манай msgbill баримтыг «POS терминалын давхардал» гэж цуцална.

    • Төлбөрийг FOR UPDATE NOWAIT түгжинэ (давхар товшилт/зэрэг job-оос хамгаална).
    • Дүрмийг (cancel_block_reason) ДАХИН шалгана — UI-аас ирсэн ID-д, эсвэл
      dry-run-аас хойш өөрчлөгдсөн төлбөрт итгэхгүй. QR/QPay баримт ХЭЗЭЭ Ч энд
      цуцлагдахгүй.
    • Идемпотент: аль хэдийн цуцлагдсан бол {"skipped": "already_cancelled"};
      CANCEL_PENDING бол msgbill-ээс төлөвийг GET-ээр эцэслэнэ (дахин cancel биш).
    • Амжилттай/хүлээгдэж буй баримтад DUPLICATE_OF_POS тэмдэг (ddtd_note + raw) —
      «Дахин үүсгэх» дээр 409 өгөх үндэс. AuditLog EBARIMT_CANCEL_BULK. commit.
    Буцаах: {ok, cancelled, pending, error, skipped?, key_scopes?, auth_error?, quota?}"""
    from fastapi import HTTPException

    from ..routers.payments_router import _lock_payment, _site_of, cancel_ebarimt
    try:
        payment = _lock_payment(db, payment_id)
    except HTTPException:
        return {"payment_id": payment_id, "ok": False, "retryable": True,
                "error": "Төлбөр өөр хүсэлтэд түгжигдсэн — дахин ажиллуулахад нөхөгдөнө"}
    if payment is None:
        db.rollback()
        return {"payment_id": payment_id, "ok": False, "error": "Төлбөр олдсонгүй"}
    site = _site_of(payment)
    if allowed_sites is not None and (site is None or site.id not in allowed_sites):
        db.rollback()
        return {"payment_id": payment_id, "ok": False, "error": "Энэ зогсоолын эрхгүй"}
    recs = db.query(VatReceipt).filter(VatReceipt.payment_id == payment_id).all()
    info = _info(payment, site, recs)
    p = payment_probe(payment, recs, None, site)
    if not p["active"] and p["cancelled_msgbill"]:
        db.rollback()
        return {**info, "ok": True, "skipped": "already_cancelled", "cancelled": [], "pending": []}
    reason = VR.cancel_block_reason(p)
    if reason:
        db.rollback()
        return {**info, "ok": False, "error": reason}
    teg = teg or {}
    mark = {"reason": DUPLICATE_OF_POS, "teg_ddtd": teg.get("teg_ddtd"),
            "teg_register": teg.get("teg_register"), "source": source, "by": username}
    res = await cancel_ebarimt(db, payment, note, mark=mark)
    err = res.get("error") or ""
    out = {**info, **res}
    up = err.upper()
    out["auth_error"] = any(x in up for x in ("UNAUTHORIZED", "MSGBILL 401", "MSGBILL 403", "ТҮЛХҮҮР"))
    out["quota"] = "429" in err or "QUOTA" in up or "хязгаар" in err
    db.add(AuditLog(username=username, action="EBARIMT_CANCEL_BULK", entity="payment",
                    entity_id=payment_id,
                    detail={"ok": bool(res.get("ok")), "cancelled": res.get("cancelled"),
                            "pending": res.get("pending"), "error": res.get("error"),
                            "key_scopes": res.get("key_scopes"), "note": note,
                            "reason": DUPLICATE_OF_POS, "source": source,
                            "teg_ddtd": teg.get("teg_ddtd"),
                            "teg_register": teg.get("teg_register")}))
    db.commit()
    return out
