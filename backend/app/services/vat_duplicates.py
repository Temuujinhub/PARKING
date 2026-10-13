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
from .receipts import DUPLICATE_OF_POS, choose_primary, is_duplicate_cancel, is_system_receipt

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
    """Төлбөрийн «давхардлын шалгалт»-д хэрэгтэй бүх мэдээлэл (DB-ээс салгасан dict).

    primary/active/system_ddtd — ЗӨВХӨН манай системийн баримт (receipts.is_system_receipt).
    Терминалын мөр (EXTERNAL, давхардлын цуцлалтын дараа бүртгэсэн ONTIME/SENT) нь
    `terminal`-д тусдаа: тэр нь «цуцлах манай баримт» БИШ — active-д орвол цуцлалтыг
    дахин ажиллуулахад «msgbill биш (ONTIME)» гэж андуурагдаж, өөр сувгийн мөр
    цуцлагдах эрсдэлтэй (2026-10-13 нэгтгэлийн хяналт)."""
    sys_recs = [r for r in recs if is_system_receipt(r)]
    prim = choose_primary(sys_recs)
    return {"id": pay.id, "paid_at": pay.paid_at, "amount": float(pay.amount),
            "site_id": getattr(site, "id", None), "site_name": getattr(site, "name", None),
            "site_code": getattr(site, "site_code", None), "plate": plate,
            "provider": pay.provider, "method": pay.payment_method,
            "receipts": [r.ebarimt_id for r in recs],
            "primary": receipt_info(prim) if prim is not None else None,
            "active": [receipt_info(r) for r in sys_recs
                       if r.status in VR.ACTIVE_STATUSES and r.ebarimt_id],
            "system_ddtd": sum(1 for r in sys_recs if r.ebarimt_id),
            "terminal": [receipt_info(r) for r in recs
                         if not is_system_receipt(r) and r.status != "CANCELLED"],
            "cancelled_msgbill": any(r.status == "CANCELLED" and r.provider == "MSGBILL"
                                     for r in sys_recs),
            "duplicate_marked": any(is_duplicate_cancel(r) for r in sys_recs)}


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
    # Терминалын мөр (EXTERNAL/ONTIME) МАНАЙ кассын баримт биш — «манайх»-т орвол
    # түүний ДДТД-ийн касс (…10002990) «манай касс» болж Ontime мөр бүр «өөр систем»
    # биш болон давхардал илрэхгүй болно. Терминалынх нь terminal_check-д.
    ours = [o for o in ours if is_system_receipt(o[0])]
    r = VR.best_shift(tax, ours, shifts, tol, probe=probe)
    left = [t for t in tax if not t["used"]]
    # Цуцлагдсан манай баримттай холбогдсон мөрийн төлбөр ч «манай баримт файлд бий»
    matched_pay_ids = {t["ours"][1].id for t in tax if t.get("ours")}
    reg_sites = VR.foreign_register_sites(left, probe, tol, r["shift"])
    explained = VR.explain_unmatched_tax(left, probe, r["shift"], tol, matched_pay_ids, scope_ids,
                                         register_sites=reg_sites)
    dups = VR.duplicate_list(explained, probe, matched_pay_ids, settings.tz_offset_hours)
    term = VR.terminal_check(tax, explained, probe, tol, r["shift"], scope_ids)
    return {"r": r, "left": left, "explained": explained, "matched_pay_ids": matched_pay_ids,
            "register_sites": reg_sites, "duplicates": dups, "terminal": term}


def reconcile_db(db, tax: list, *, shifts: list[float], tol: int, ours_sites=None,
                 probe_sites=None, scope_ids: set | None = None) -> dict:
    """DB-ээс уншаад analyze. API нь үүнийг thread дээр ӨӨРИЙН session-тэй дууддаг
    (event loop-ыг блоклохгүй — хаалт/LPR царцахаас сэргийлнэ), CLI шууд."""
    lo, hi = query_window(tax, shifts)
    ours = [o for o in load_ours(db, lo, hi, ours_sites) if is_system_receipt(o[0])]
    probe = load_probe(db, lo, hi, probe_sites)
    out = analyze(tax, ours, probe, shifts=shifts, tol=tol, scope_ids=scope_ids)
    out.update({"ours": ours, "probe": probe})
    return out


# ───────────────────────── цуцлах ─────────────────────────────────────────

# Ажиллаж буй системийн DB-ийн нэр (config.py-ийн анхдагч, .env.example) — бодит
# цуцлалт ЗӨВХӨН энэ DB дээрээс. audit1013/t_* г.м продын ХУУЛБАР нь продын жинхэнэ
# msgbill түлхүүр + provider_ref агуулдаг тул тэндээс цуцлахад продын баримт цуцлагдана.
LIVE_DB_NAME = "parking"


def db_name(url: str | None = None) -> str:
    from sqlalchemy.engine import make_url
    try:
        return make_url(url or settings.database_url).database or ""
    except Exception:  # noqa: BLE001
        return ""


def server_problems() -> list[str]:
    """Энэ сервер/DB дээрээс БОДИТ цуцлалт хийж болох уу (2026-10-13 хяналт: TEST
    сервер дээр audit1013 (продын хуулбар, продын жинхэнэ msgbill түлхүүртэй) руу
    --apply хийхэд юу ч саадгүй байв).

    • PARKING_DEPLOY_ROLE=staging → үгүй.
    • DB нь «parking» биш, эсвэл сервер нь test.*/localhost → үгүй; ГЭХДЭЭ
      PARKING_DEPLOY_ROLE=production-ийг ил тод заасан бол (прод өөр нэртэй байвал) болно."""
    import os
    from urllib.parse import urlparse
    probs = []
    role = (os.environ.get("PARKING_DEPLOY_ROLE") or "").strip().lower()
    if role == "staging":
        return ["PARKING_DEPLOY_ROLE=staging — ТЕСТ сервер; бодит цуцлалтыг зөвхөн прод дээр хийнэ"]
    if role == "production":
        return []
    name = db_name()
    if name != LIVE_DB_NAME:
        probs.append(f"DB «{name}» нь ажиллаж буй системийн «{LIVE_DB_NAME}» биш (продын ХУУЛБАР "
                     "байж магадгүй) — хуулбар дээрээс бодит msgbill баримт цуцлахгүй")
    host = (urlparse(settings.public_base_url or "").hostname or "").lower()
    if host.startswith("test."):
        probs.append(f"ТЕСТ сервер ({settings.public_base_url}) — бодит цуцлалтыг зөвхөн прод "
                     "сервер дээр хийнэ")
    elif host in ("", "localhost", "127.0.0.1"):
        probs.append(f"сервер тодорхойгүй (PARKING_PUBLIC_BASE_URL={settings.public_base_url or '—'}) — "
                     ".env ачаалагдаагүй байж магадгүй: /root/PARKING/backend-ээс ажиллуулна уу")
    return probs


def account_scopes(sites: list | None) -> dict:
    """Зогсоол бүрд цуцлахад оролдох msgbill түлхүүрүүд (түлхүүр БИШ — хүрээ нь):
    «Хангарьд»: ["tenant:EasyParking", "global"]. YES асуултын өмнө харуулна."""
    from . import msgbill
    out = {}
    for site in sites or []:
        accs = msgbill.cancel_accounts(site)
        out[getattr(site, "name", None) or getattr(site, "id", "?")] = [
            a.scope + (f":{a.scope_name}" if a.scope_name else "") + (" (ТЕСТ)" if a.is_test else "")
            for a in accs]
    return out


def cancel_ready(db=None, sites: list | None = None) -> list[str]:
    """БОДИТ цуцлалт эхлүүлэхээс өмнөх хамгаалалт. Хоосон жагсаалт = бэлэн.

    «msgbill хуурамч» гэдэг нь: MOCK баримт асаалттай тест сервер, ТЕСТ түлхүүр
    (bsk_test_ — msgbill симуляц хийдэг, ТЕГ-т юу ч цуцлагдахгүй) эсвэл localhost
    хаяг. Түлхүүргүй зогсоол байвал түүний баримтыг цуцалж чадахгүй. DB нь
    ажиллаж буй «parking» биш (продын хуулбар) эсвэл ТЕСТ сервер бол цуцлахгүй."""
    from . import msgbill
    probs = server_problems()
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
                     source: str = "api", mark_extra: dict | None = None,
                     guard=None) -> dict:
    """НЭГ төлбөрийн манай msgbill баримтыг «POS терминалын давхардал» гэж цуцална.

    • Төлбөрийг FOR UPDATE NOWAIT түгжинэ (давхар товшилт/зэрэг job-оос хамгаална).
    • Дүрмийг (cancel_block_reason) ДАХИН шалгана — UI-аас ирсэн ID-д, эсвэл
      dry-run-аас хойш өөрчлөгдсөн төлбөрт итгэхгүй. QR/QPay баримт ХЭЗЭЭ Ч энд
      цуцлагдахгүй.
    • Идемпотент: аль хэдийн цуцлагдсан бол {"skipped": "already_cancelled"};
      CANCEL_PENDING бол msgbill-ээс төлөвийг GET-ээр эцэслэнэ (дахин cancel биш).
    • Амжилттай/хүлээгдэж буй баримтад DUPLICATE_OF_POS тэмдэг (ddtd_note + raw) —
      «Дахин үүсгэх» дээр 409 өгөх үндэс. AuditLog EBARIMT_CANCEL_BULK. commit.
    • ЗӨВХӨН манай системийн (msgbill) мөрийг цуцална (only_ids) — терминалын
      (ONTIME/EXTERNAL) мөрийг хэзээ ч хөндөхгүй.
    • Цуцалсан/хүлээгдэж буйн дараа терминалын баримтыг бүртгэнэ
      (financial_jobs.record_terminal_receipt: итгэх ТЕГ ДДТД бол ONTIME/SENT, эс
      бөгөөс ДДТД-гүй EXTERNAL) — хэвлэлт/тайлан/ТЕГ тулгалт «баримттай» гэж харна.
      Аль хэдийн цуцлагдсан (тэмдэгтэй) боловч терминалын мөргүй бол (өмнөх ажиллагаа
      дундуур тасарсан) энэ удаа нөхөж бүртгэнэ — дахин ажиллуулахад аюулгүй.
    mark_extra: DUPLICATE_OF_POS-ийн нэмэлт талбар (ж: {"rule": "pos_app"}) — raw.cancel-д.
    guard(payment, recs) -> шалтгаан | None: түгжээний ДАРАА дахин шалгах нэмэлт дүрэм
      (POS аппын горим: ААН/ТТД, сонгосон зогсоол, өөр сувгийн SENT баримт г.м) —
      сонголтоос хойш өөрчлөгдсөн төлбөрийг цуцлахгүй.
    Буцаах: {ok, cancelled, pending, error, skipped?, key_scopes?, auth_error?, quota?,
             terminal_receipt?}"""
    from fastapi import HTTPException

    from ..routers.payments_router import _lock_payment, _site_of, cancel_ebarimt
    from .financial_jobs import record_terminal_receipt
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
    if guard is not None:
        why = guard(payment, recs)
        if why:
            db.rollback()
            return {**info, "ok": False, "error": why}
    p = payment_probe(payment, recs, None, site)
    teg = teg or {}

    def _record(locked, replaced):
        # cancel_ebarimt commit хийсэн (түгжээ суларсан) — дахин түгжиж бүртгэнэ; өөр
        # хүсэлт түгжсэн бол алгасна (retry-г тэмдэг хамгаална, дахин ажиллуулахад нөхнө)
        return record_terminal_receipt(db, locked, teg.get("teg_ddtd"), replaced=replaced,
                                       username=username, source=f"vat-duplicates.{source}")

    if not p["active"] and p["cancelled_msgbill"]:
        terminal = None
        marked = [r for r in recs if r.status == "CANCELLED" and is_duplicate_cancel(r)]
        # Терминалын мөр алга (өмнөх ажиллагаа тасарсан) эсвэл ДДТД-гүй EXTERNAL байхад
        # ТЕГ-ийн ДДТД ирсэн — record_terminal_receipt нөхнө/шинэчилнэ (идемпотент)
        if marked and (not p["terminal"] or (teg.get("teg_ddtd")
                                              and not any(t["ebarimt_id"] for t in p["terminal"]))):
            terminal = _record(payment, marked)
            db.add(AuditLog(username=username, action="EBARIMT_CANCEL_BULK", entity="payment",
                            entity_id=payment_id,
                            detail={"ok": True, "skipped": "already_cancelled", "source": source,
                                    "reason": DUPLICATE_OF_POS, "terminal_receipt": terminal.id,
                                    "teg_ddtd": teg.get("teg_ddtd")}))
            db.commit()
        else:
            db.rollback()
        return {**info, "ok": True, "skipped": "already_cancelled", "cancelled": [], "pending": [],
                **({"terminal_receipt": _term_info(terminal)} if terminal is not None else {})}
    reason = VR.cancel_block_reason(p)
    if reason:
        db.rollback()
        return {**info, "ok": False, "error": reason}
    mark = {"reason": DUPLICATE_OF_POS, "teg_ddtd": teg.get("teg_ddtd"),
            "teg_register": teg.get("teg_register"), "source": source, "by": username,
            **(mark_extra or {})}
    target_ids = {a["id"] for a in p["active"]}
    res = await cancel_ebarimt(db, payment, note, mark=mark, only_ids=target_ids)
    terminal = None
    targets = db.query(VatReceipt).filter(VatReceipt.id.in_(target_ids)).all()
    if targets and all(r.status in ("CANCELLED", "CANCEL_PENDING") for r in targets):
        try:
            locked = _lock_payment(db, payment_id)
        except HTTPException:
            locked = None
        if locked is not None:
            terminal = _record(locked, targets)
    err = res.get("error") or ""
    out = {**info, **res}
    if terminal is not None:
        out["terminal_receipt"] = _term_info(terminal)
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
                            "teg_register": teg.get("teg_register"),
                            **({"rule": mark_extra.get("rule")} if mark_extra else {}),
                            "terminal_receipt": _term_info(terminal)}))
    db.commit()
    return out


def _term_info(rec) -> dict | None:
    if rec is None:
        return None
    return {"id": rec.id, "status": rec.status, "provider": rec.provider,
            "ebarimt_id": rec.ebarimt_id}


# ─────────────── POS аппын дүрмээр цуцлах (2026-10-13 эзний шийдвэр) ───────────────
#
# Эзэн: ИйзиПаркинг ХХК-ийн POS терминалтай БҮХ зогсоолд PAX терминал POS аппаар авсан
# КАРТ, БЭЛЭН борлуулалт бүрд Ontime-ийн e-Barimt-ыг өөрөө хэвлэдэг. Тиймээс тэдгээр
# борлуулалтад манай msgbill-ээр гаргасан баримт нь ДАВХАРДАЛ — ТЕГ экспортгүйгээр
# (файл бүрийг татах боломжгүй) дүрмээр сонгож цуцална. Ontime баримт хэвлэгдээгүй
# борлуулалт байвал (терминал унтарсан г.м) цуцалсны дараа e-Barimt-гүй үлдэнэ —
# эзэн энэ эрсдэлийг хүлээн зөвшөөрсөн; CLI dry-run үүнийг заавал анхааруулна.
# Сонголт (`select_pos_app`) ба цуцлалтын дахин шалгалт (`pos_app_guard`) НЭГ дүрэм.

POS_APP_TENANTS = frozenset({"EASYPARKING"})        # Моннис (өөр ТТД, өөр терминал) ХАМААРАХГҮЙ
POS_APP_GO_LIVE = datetime(2026, 8, 19)             # msgbill эхэлсэн өдөр (УБ) — анхдагч --from
POS_APP_TEST_SITES = frozenset({"KH", "SITE10"})    # тест зогсоол — --site-аар нэрлэсэн үед л
POS_APP_MIN_CARD = 20                               # POS-оор ≥20 карт авсан хэрэглэгч = POS апп
POS_APP_RULE = "pos_app_terminal_2026-10-13"
POS_APP_METHODS = ("CARD", "CASH")


def resolve_tenant(db, value: str):
    """Түрээслэгчийг code / нэр / id-аар (CLI-үүд). Олдохгүй бол ValueError."""
    from sqlalchemy import or_

    from ..models import Tenant
    conds = [Tenant.code.ilike(value), Tenant.name.ilike(value)]
    if len(value) == 36:                       # UUID
        conds.append(Tenant.id == value)
    t = db.query(Tenant).filter(or_(*conds)).first()
    if t is None:
        raise ValueError(f"Түрээслэгч олдсонгүй: {value}")
    return t


def ub_window(date_from: str | None, date_to: str | None) -> tuple[datetime, datetime]:
    """УБ-ын огноо (YYYY-MM-DD, хоёулаа хамааруулна) → UTC [lo, hi). Анхдагч: go-live → одоо."""
    off = timedelta(hours=settings.tz_offset_hours)
    lo = (datetime.strptime(date_from, "%Y-%m-%d") if date_from else POS_APP_GO_LIVE) - off
    hi = (datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1) - off if date_to
          else datetime.utcnow() + timedelta(minutes=1))
    return lo, hi


def pos_app_sites(db, tenant, site_codes=None) -> list:
    """Түрээслэгчийн зогсоолууд; --site өгвөл зөвхөн тэд (өөр түрээслэгчийнх бол алдаа).
    Тест зогсоол (KH, SITE10) зөвхөн --site-аар нэрлэсэн үед."""
    q = db.query(ParkingSite).filter(ParkingSite.tenant_id == tenant.id)
    sites = q.order_by(ParkingSite.site_code).all()
    if site_codes:
        want = {c.upper() for c in site_codes}
        out = [s for s in sites if (s.site_code or "").upper() in want]
        missing = want - {(s.site_code or "").upper() for s in out}
        if missing:
            raise ValueError(f"«{tenant.code}»-ийн зогсоол биш/олдсонгүй: {', '.join(sorted(missing))}")
        return out
    return [s for s in sites if (s.site_code or "").upper() not in POS_APP_TEST_SITES]


def derive_pos_users(db, site_ids, *, min_card: int = POS_APP_MIN_CARD) -> dict:
    """Зогсоол бүрд POS аппаар (provider POS = /pos/confirm) ≥min_card КАРТ авсан
    хэрэглэгчид {site_id: {username: тоо}} — go-live-аас хойш. Веб кассын ажилтан
    (sogii, bamanna, operator) POS картгүй тул энд орохгүй."""
    from ..models import User
    if not site_ids:
        return {}
    rows = (db.query(PAY_SITE, User.username, func.count(Payment.id))
            .select_from(Payment)
            .outerjoin(ParkingSession, Payment.session_id == ParkingSession.id)
            .join(User, User.id == Payment.cashier_id)
            .filter(Payment.status == "PAID", Payment.provider == "POS",
                    Payment.payment_method == "CARD",
                    Payment.paid_at >= POS_APP_GO_LIVE - timedelta(hours=settings.tz_offset_hours),
                    PAY_SITE.in_(list(site_ids)))
            .group_by(PAY_SITE, User.username).all())
    out: dict = {sid: {} for sid in site_ids}
    for sid, uname, n in rows:
        if n >= min_card and uname:
            out.setdefault(sid, {})[uname.lower()] = int(n)
    return out


def parse_pos_user_override(values) -> dict:
    """--pos-user: «pos.a,pos.b» (бүх сонгосон зогсоолд) эсвэл «HANGARID=pos.khangarid»
    (тухайн зогсоолд). Буцаах {site_code|"*": {users}}."""
    import re
    out: dict = {}
    for v in values or []:
        code, _, users = v.partition("=") if "=" in v else ("*", "", v)
        names = {u.strip().lower() for u in re.split(r"[,;\s]+", users) if u.strip()}
        out.setdefault(code.strip().upper() or "*", set()).update(names)
    return out


def pos_users_for_sites(db, sites, *, override: dict | None = None,
                        min_card: int = POS_APP_MIN_CARD) -> dict:
    """Зогсоол бүрийн «POS аппын хэрэглэгчид» ба эх сурвалж — НЭГ дүрэм (цуцлалт ба
    тохиргооны хэрэгсэл хоёулаа): --pos-user > тохиргоо (pos_terminal_users) > ≥min_card
    POS карт авсан хэрэглэгч. {site_id: {"users", "source", "derived"}}"""
    from . import app_settings as A
    derived = derive_pos_users(db, [s.id for s in sites], min_card=min_card)
    out = {}
    for s in sites:
        ov = (override or {}).get((s.site_code or "").upper()) or (override or {}).get("*")
        configured = A.pos_app_users(db, s.id)
        if ov:
            users, src = set(ov), "--pos-user"
        elif configured:
            users, src = configured, "тохиргоо"
        else:
            users, src = set(derived.get(s.id, {})), f"≥{min_card} POS карт"
        out[s.id] = {"users": users, "source": src, "derived": derived.get(s.id, {})}
    return out


def pos_app_via(payment, username, site_users) -> str | None:
    """Энэ төлбөр POS аппынх уу — яаж танив (provider POS / source POSAPP / хэрэглэгч)."""
    if payment.provider == "POS":
        return "provider=POS"
    if getattr(payment, "source", None) == "POSAPP":
        return "source=POSAPP"
    if username and username.lower() in (site_users or set()):
        return f"user={username.lower()}"
    return None


def pos_app_exclusion(payment, recs, *, username, site_users, include_company=False,
                      site_ids=None, site=None) -> str | None:
    """POS аппын дүрмээр цуцлах эсэх. None = цуцална; эс бөгөөс шалтгааны КОД.
    Сонголт ба түгжээний дараах дахин шалгалт (guard) хоёулаа үүгээр."""
    if payment.status != "PAID":
        return "NOT_PAID"
    if (payment.payment_method or "").upper() not in POS_APP_METHODS:
        return "METHOD"
    if site_ids is not None and (site is None or site.id not in site_ids):
        return "SITE"
    if pos_app_via(payment, username, site_users) is None:
        return "NOT_POS_APP"
    if not include_company and (payment.ebarimt_receiver_type == "COMPANY"
                                or (payment.customer_tin or "").strip()):
        return "COMPANY"
    if any(r.status == "SENT" and (r.provider or "POSAPI") != "MSGBILL" and not is_duplicate_cancel(r)
           for r in recs):
        return "OTHER_ISSUER_SENT"
    issued = [r for r in recs if r.provider == "MSGBILL" and r.status in VR.ACTIVE_STATUSES
              and r.provider_ref]
    if not issued:
        return "NO_MSGBILL"
    if VR.cancel_block_reason(payment_probe(payment, recs, None, site)) is not None:
        return "RULE"
    return None


POS_APP_EXCLUSION_MN = {
    "NOT_POS_APP": "POS аппынх биш (веб касс/бусад хэрэглэгч)",
    "COMPANY": "ААН/ТТД-тэй баримт — терминал иргэний баримт хэвлэдэг тул ХӨНДӨХГҮЙ",
    "OTHER_ISSUER_SENT": "өөр сувгийн (QPay/PosAPI/терминал) тэмдэггүй SENT баримттай — гараар",
    "NO_MSGBILL": "цуцлах msgbill баримт алга (QPay/PosAPI/MOCK/FAILED/REVIEW эсвэл цуцлагдсан)",
    "RULE": "цуцлах дүрэм хангаагүй (provider_ref/ДДТД/дүн) — гараар",
    "METHOD": "карт/бэлэн биш", "NOT_PAID": "PAID биш", "SITE": "сонгосон зогсоолд биш",
}


def select_pos_app(db, *, sites, lo: datetime, hi: datetime, site_users: dict,
                   include_company: bool = False) -> dict:
    """POS аппын карт/бэлэн төлбөрүүдээс цуцлах msgbill баримттайг сонгоно (READ ONLY).

    Буцаах: {"selected": [мөр], "excluded": Counter(код), "company": [мөр],
             "complete": [мөр]} — complete = өмнө нь тэмдэгтэйгээр цуцлагдсан боловч
    терминалын мөр бүртгэгдээгүй (ажиллагаа тасарсан) → apply нөхнө (msgbill дуудахгүй)."""
    from collections import Counter

    from ..models import User
    by_id = {s.id: s for s in sites}
    if not by_id:
        return {"selected": [], "excluded": Counter(), "company": [], "complete": []}
    q = (db.query(Payment, User.username, PAY_PLATE, PAY_SITE).enable_eagerloads(False)
         .outerjoin(ParkingSession, Payment.session_id == ParkingSession.id)
         .outerjoin(User, User.id == Payment.cashier_id)
         .filter(Payment.status == "PAID", Payment.payment_method.in_(POS_APP_METHODS),
                 Payment.paid_at >= lo, Payment.paid_at < hi, PAY_SITE.in_(list(by_id))))
    rows = q.order_by(Payment.paid_at, Payment.id).all()
    recs: dict[str, list] = {}
    ids = [p.id for p, *_ in rows]
    for i in range(0, len(ids), 1000):
        for r in db.query(VatReceipt).filter(VatReceipt.payment_id.in_(ids[i:i + 1000])).all():
            recs.setdefault(r.payment_id, []).append(r)
    loc = timedelta(hours=settings.tz_offset_hours)
    out, excluded, company, complete = [], Counter(), [], []
    for p, uname, plate, sid in rows:
        site = by_id.get(sid)
        rs = recs.get(p.id, [])
        users = (site_users.get(sid) or {}).get("users", set())
        why = pos_app_exclusion(p, rs, username=uname, site_users=users,
                                include_company=include_company, site_ids=set(by_id), site=site)
        issued = [r for r in rs if r.provider == "MSGBILL" and r.status in VR.ACTIVE_STATUSES]
        row = {"payment_id": p.id, "site_id": sid, "site_code": site.site_code if site else "",
               "site_name": site.name if site else "", "plate": plate or "",
               "method": (p.payment_method or "").upper(), "amount": float(p.amount),
               "paid_at": p.paid_at.isoformat(), "paid_at_ub": (p.paid_at + loc).strftime("%Y-%m-%d %H:%M:%S"),
               "month": (p.paid_at + loc).strftime("%Y-%m"), "cashier": uname or "",
               "via": pos_app_via(p, uname, users) or "",
               "our_ddtd": ", ".join(r.ebarimt_id or "" for r in issued),
               "our_provider_ref": ", ".join(r.provider_ref or "" for r in issued),
               "our_status": "/".join(sorted({r.status for r in issued})),
               "receiver_type": p.ebarimt_receiver_type or "", "customer_tin": p.customer_tin or "",
               "teg_ddtd": None, "teg_register": None}
        if why is None:
            out.append(row)
            continue
        if why == "COMPANY" and issued:
            company.append(row)
        if (why == "NO_MSGBILL" and pos_app_via(p, uname, users)
                and any(r.status == "CANCELLED" and is_duplicate_cancel(r) for r in rs)
                and not any(not is_system_receipt(r) and r.status != "CANCELLED" for r in rs)):
            complete.append(row)
        excluded[why] += 1
    return {"selected": out, "excluded": excluded, "company": company, "complete": complete}


def pos_app_guard(site_users: dict, site_ids: set, include_company: bool = False):
    """cancel_one(guard=…) — түгжээний дараа ЯГ сонголтын дүрмээр дахин шалгана."""
    from sqlalchemy.orm import object_session

    from ..models import User

    def guard(payment, recs):
        from ..routers.payments_router import _site_of
        site = _site_of(payment)
        uname = None
        sess = object_session(payment)
        if payment.cashier_id and sess is not None:
            usr = sess.get(User, payment.cashier_id)
            uname = usr.username if usr is not None else None
        users = (site_users.get(site.id) if site is not None else None) or {}
        why = pos_app_exclusion(payment, recs, username=uname, site_users=users.get("users", set()),
                                include_company=include_company, site_ids=site_ids, site=site)
        if why in (None, "NO_MSGBILL"):
            # NO_MSGBILL: аль хэдийн цуцлагдсан (дахин ажиллуулсан) — cancel_one өөрөө
            # «already»/терминалын мөр нөхөх эсвэл дүрмээр татгалзана; msgbill дуудахгүй
            return None
        return f"POS аппын дүрэм: {POS_APP_EXCLUSION_MN.get(why, why)}"
    return guard


def pos_app_evidence(db, tax: list, rows: list, *, tol: int, shifts: list[float],
                     site_ids: set | None) -> dict:
    """НЭМЭЛТ НОТОЛГОО (--evidence): ТЕГ экспорт өгвөл түүний хамрах хугацаанд байгаа
    сонгогдсон POS аппын төлбөр бүрд Ontime (өөр кассын) мөр таарсан эсэх.

    Тулгалт нь --file горимтой ЯГ НЭГ (reconcile_db → explain_unmatched_tax: кассын
    шилжилт, нэг-нэгээр). Хамрах хугацаанд байгаа боловч Ontime мөргүй төлбөр = терминал
    баримт ХЭВЛЭЭГҮЙ нь батлагдсан → цуцлахгүй (цорын ганц e-Barimt нь манайх). Ижил
    дүнтэй хоёр борлуулалт ±tol дотор байвал аль нь «баримтгүй» болох нь солигдож болно
    — тоо нь зөв. Буцаах {"window", "inside", "with_ontime", "missing"}."""
    a = reconcile_db(db, tax, shifts=shifts, tol=tol, ours_sites=list(site_ids) if site_ids else None,
                     probe_sites=list(site_ids) if site_ids else None, scope_ids=site_ids)
    lo, hi = VR.file_window(tax, tol, a["r"]["shift"])
    ontime = {x["payment_id"] for x in a["explained"]
              if x.get("foreign") and x.get("payment_id")
              and x["verdict"] not in ("OTHER_SCOPE", "NO_PAYMENT")}
    inside = [r for r in rows if lo <= datetime.fromisoformat(r["paid_at"]) <= hi]
    missing = [r for r in inside if r["payment_id"] not in ontime]
    return {"window": (lo, hi), "inside": len(inside),
            "with_ontime": {r["payment_id"] for r in inside} & ontime, "missing": missing,
            "foreign_registers": a["r"].get("foreign_registers", [])}
