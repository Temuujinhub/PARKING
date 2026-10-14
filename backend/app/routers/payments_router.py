"""Төлбөр: QPay invoice/webhook, e-Barimt 3.0, кассын бэлэн мөнгө, PAX POS баталгаажуулалт."""
import json
import logging
import hmac
import re
import uuid
from datetime import datetime, timedelta

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from ..auth import enforce_site, operator_sites, require, scoped_site
from ..config import settings
from ..database import get_db
from ..models import (AuditLog, CashierShift, Compensation, ParkingSession, ParkingSite, Payment,
                      User, VatReceipt)
from ..ratelimit import throttle
from ..serializers import to_dict
from ..services import ebarimt, msgbill, qpay
from ..services.receipts import assign_ebarimt_id, primary_receipt
from ..session_logic import amount_due, paid_total, mark_paid_and_open, session_fee_info

log = logging.getLogger("parking.payments")

# QPay-ийн `sender_invoice_no` талбарын дээд урт (v2 API). Хэтэрвэл HTTP 400
# `MAX_LENGTH` — нэхэмжлэл огт үүсэхгүй.
QPAY_INVOICE_NO_MAX = qpay.SENDER_INVOICE_NO_MAX
_fit_bytes = qpay.fit_bytes

router = APIRouter(prefix="/api/payments", tags=["payments"])


@router.post("/{payment_id}/wallet-reconcile")
async def reconcile_wallet(payment_id: str, body: dict, db: Session = Depends(get_db),
                           user: User = Depends(require("reports", "settings"))):
    """An authorized operator records the provider's verified outcome, never retries a debit."""
    if user.role not in ("SUPER_ADMIN", "ADMIN", "FINANCE"):
        raise HTTPException(403, "Санхүүгийн тулгалтын эрх шаардлагатай")
    payment = _lock_payment(db, payment_id)
    if not payment or not payment.session_id or payment.payment_method != "WALLET":
        raise HTTPException(404, "Дансны гүйлгээ олдсонгүй")
    enforce_site(user, payment.session.site_id)
    if payment.status == "PAID":
        return {"status": "PAID"}
    if payment.status not in ("UNKNOWN", "PENDING", "REVIEW"):
        raise HTTPException(409, "Тулгах боломжгүй төлөв")
    outcome = body.get("outcome")
    reference = str(body.get("reference") or "").strip()[:200]
    if outcome not in ("PAID", "FAILED") or not reference:
        raise HTTPException(422, "PAID/FAILED үр дүн, үйлчилгээ үзүүлэгчийн баталгааны дугаар шаардлагатай")
    if outcome == "PAID":
        payment.provider_payment_id = reference[:120]
        await _finalize_paid(db, payment)
    else:
        payment.status = "FAILED"
    db.add(AuditLog(username=user.username, action="WALLET_RECONCILE", entity="payment",
                    entity_id=payment.id, detail={"outcome": outcome, "reference": reference}))
    db.commit()
    return {"status": payment.status}


def secrets_compare(a: str, b: str) -> bool:
    """Цагийн зөрүүнд суурилсан халдлагаас хамгаалсан харьцуулалт (webhook токен)."""
    return hmac.compare_digest((a or "").encode(), (b or "").encode())


def _lock_payment(db: Session, payment_id: str) -> Payment | None:
    """Payment мөрийг SELECT FOR UPDATE NOWAIT-аар түгжинэ.

    QPay webhook болон 5 сек polling ЗЭРЭГ орж ирэхэд хоёул finalize хийж давхар
    e-Barimt/өр төлөлт үүсгэдэг race-ээс сэргийлнэ: түгжээг авсан нь боловсруулж,
    аваагүй нь None авч одоогийн төлөвөө л буцаана. populate_existing нь нөгөө
    транзакцын commit хийсэн PAID төлөвийг шинээр уншуулна."""
    from sqlalchemy.exc import OperationalError
    try:
        # enable_eagerloads(False) — ЗААВАЛ: Payment-ийн lazy="joined" харилцаанууд
        # (session→site→tariff, discount) OUTER JOIN үүсгэдэг ба Postgres нь
        # "FOR UPDATE cannot be applied to the nullable side of an outer join"
        # гэж унадаг. Холбоосууд дараа нь хэрэгцээгээр (lazy) ачаалагдана.
        return (db.query(Payment).filter(Payment.id == payment_id)
                .enable_eagerloads(False)
                .populate_existing().with_for_update(nowait=True).first())
    except OperationalError:
        db.rollback()  # түгжээ авч чадсангүй — өөр request боловсруулж байна
        raise HTTPException(409, "Төлбөрийн баталгаажуулалт үргэлжилж байна. Дахин оролдоно уу.")


def _site_of(payment: Payment):
    return payment.site or (payment.session.site if payment.session else None)


def _receipt_desc(payment: Payment, extra: str | None = None) -> str:
    """msgbill баримтын тайлбар — дугаар + зогсоол (жолооч/санхүү танихад)."""
    sess = getattr(payment, "session", None)
    plate = getattr(sess, "plate_number", None) or ""
    site = _site_of(payment)
    site_name = getattr(site, "name", None) or ""
    parts = ["Зогсоолын үйлчилгээ", plate, site_name, extra or ""]
    return " · ".join(p for p in parts if p)[:200]


def _external_receipt(body: dict | None) -> dict | None:
    """Гадны системд (POS терминал, банкны PosLink, өөр e-Barimt API) АЛЬ ХЭДИЙН үүссэн
    баримтыг хүсэлтийн биетээс уншина: {ebarimt_id, lottery_code|lottery, qr_data,
    ebarimt_provider?}. Байвал систем ШИНЭЭР баримт үүсгэхгүй, үүнийг бүртгэнэ
    (давхар баримтаас сэргийлнэ). provider: TERMINAL (анхдагч) | дурын нэр ≤20 тэмдэгт."""
    if not body or not body.get("ebarimt_id"):
        return None
    prov = str(body.get("ebarimt_provider") or body.get("ebarimt_source") or "TERMINAL")
    prov = "".join(ch for ch in prov.upper() if ch.isalnum() or ch in "-_")[:20] or "TERMINAL"
    if prov in ("QPAY", "MSGBILL", "POSAPI"):
        prov = "EXT-" + prov[:16]   # дотоод сувгийн нэртэй андуурахгүй
    return {"billId": str(body["ebarimt_id"]).strip()[:120],
            "lottery": (str(body.get("lottery_code") or body.get("lottery") or "").strip()[:60] or None),
            "qrData": body.get("qr_data") or None,
            "provider": prov,
            "msgbillId": (str(body.get("ebarimt_ref") or "")[:80] or None)}


def _err_text(v, limit: int = 400) -> str:
    """Алдааг ЗААВАЛ текст болгоно — QPay 400-ийн `message` нь dict байж болно
    ({"ebarimt_receiver": {"type": "MAX_LENGTH", ...}}). 2026-09-06: тэр dict шууд
    `receipt_url`-д бичигдэх гэж psycopg2 «can't adapt type 'dict'» унаад session
    эвдэрч, бөөн нөхөлт PendingRollbackError гэж л харуулдаг байв."""
    if v is None:
        return ""
    if not isinstance(v, str):
        try:
            import json as _json
            v = _json.dumps(v, ensure_ascii=False)
        except Exception:  # noqa: BLE001
            v = str(v)
    return v[:limit]


def _ebarimt_err(e: Exception) -> str:
    """e-Barimt/msgbill/QPay алдааг богино текст болгоно (VatReceipt.receipt_url-д)."""
    import httpx as _httpx
    if isinstance(e, _httpx.HTTPStatusError):
        try:
            return _err_text(e.response.json().get("message") or e.response.text)
        except Exception:  # noqa: BLE001
            return _err_text(e.response.text)
    return _err_text(str(e))


def _invoice_no(session: ParkingSession) -> str:
    """QPay/санхүүд харагдах гүйлгээний дугаар — машины дугаарыг шингээнэ
    (банкны хуулга, QPay порталтай тулгахад дугаараар олоход амар).

    ХЯЗГААР (2026-08-28-нд production дээр илэрсэн): QPay `sender_invoice_no`-г
    **45 тэмдэгтээс** урт бол HTTP 400 `MAX_LENGTH` гэж татгалзаж, нэхэмжлэл ОГТ
    үүсэхгүй. Өмнөх хувилбар нь урт ямар гарахыг огт шалгадаггүй байсан:

        PARK_IKH_MONGOL_RESTORANT-0128УНМ-20260828-A1B2C3  → 49 тэмдэгт ❌

    Улмаар «Их Монгол ресторан» зогсоолын БҮХ жолооч QR-аар төлж чадахгүй, зөвхөн
    кассаар төлдөг болсон (нэг цагт 36 бүтэлгүйтэл). Бусад зогсоол нь кодоороо
    богино учраас (хамгийн урт нь `PARK_BSB_MEBEL` — 38 тэмдэгт) огт мэдэгдээгүй.
    Өөрөөр хэлбэл энэ нь «урт кодтой зогсоол нэмэх хүртэл нам гүм хэвтэх» алдаа
    байсан — тиймээс шинэ зогсоол бүрд дахин гарахгүйн тулд урт нь ЭНД баталгаажна.

    Тайрах эрэмбэ: сүүл (огноо + санамсаргүй 6 тэмдэгт) нь давхцлаас хамгаалдаг
    тул ХЭЗЭЭ Ч тайрахгүй; машины дугаар нь тулгалтын гол түлхүүр тул дараагийн
    ээлжинд; зогсоолын код л богиносно (эхний үсгүүд нь таних хэмжээнд үлдэнэ)."""
    site_code = session.site.site_code if session.site else "SITE"
    plate = re.sub(r"[^0-9A-ZА-ЯЁӨҮ]", "", (session.plate_number or "").upper())[:12]
    tail = f"-{datetime.utcnow():%Y%m%d}-{uuid.uuid4().hex[:6].upper()}"  # 16 ASCII байт
    budget = QPAY_INVOICE_NO_MAX - len(tail)
    plate = _fit_bytes(plate, budget)
    site = _fit_bytes(site_code, budget - len(plate.encode()) - 1) if plate else \
        _fit_bytes(site_code, budget)
    head = f"{site}-{plate}" if site and plate else (site or plate or "INV")
    return f"{head}{tail}"


def _settlement_fee(db: Session, session: ParkingSession, payment: Payment,
                    now: datetime | None = None) -> tuple[dict, bool]:
    """Төлбөр баталгаажих үеийн тооцооны дүн — (fee, нэхэмжлэлийн_үнэ_барьсан_эсэх).

    Жолооч нэхэмжлэлд ҮЗҮҮЛСЭН дүнг төлдөг; банкны апп-аар төлж дуустал
    (17с–6мин) тарифын шатлал ахивал callback-ийн цагаар дахин бодох нь
    «2,000 төлчихлөө, 5,000 болчлоо» гомдол үүсгэдэг (2026-09-23, Эрэл-13
    0605ГСО 1,500→2,000). Нэхэмжлэл `invoice_quote_minutes` дотор төлөгдвөл
    түүний fee_snapshot-оор хаана; хэтэрсэн/snapshot-гүй бол одоогийн дүн."""
    current = session_fee_info(db, session)
    snap = payment.fee_snapshot or {}
    if "total_fee" not in snap or not payment.created_at:
        return current, False
    from ..services.app_settings import get_exit_rules
    minutes = int(get_exit_rules(db, session.site_id).get("invoice_quote_minutes") or 0)
    now = now or datetime.utcnow()
    if minutes <= 0 or now - payment.created_at > timedelta(minutes=minutes):
        return current, False
    if float(snap["total_fee"]) >= float(current["total_fee"]):
        return current, False
    held = {k: v for k, v in snap.items() if k not in ("parking_amount", "debts")}
    held["price_held_by_invoice"] = payment.id
    return held, True


async def _finalize_paid(db: Session, payment: Payment, raw: dict | None = None,
                         external_receipt: dict | None = None, partner_notification=None,
                         pos_lane: dict | None = None):
    """Төлбөр PAID болмогц: session PAID + barrier + e-Barimt.

    Нэгдсэн (өр багтсан) төлбөрт: session-ий хэсэгт нэг баримт, холбогдсон ӨР ТУС
    БҮРД ТУСДАА баримт үүсгэж, нөхөн төлбөрүүдийг PAID болгоно.

    ХУГАЦААНЫ ХЭМЖИЛТ: «төлбөр төлсний дараа хаалт удаан нээгдэж байна» гомдлыг
    үе шат тус бүрээр нь ялгаж хэмжинэ (e-Barimt vs хаалт vs DB) — эс бол аль нь
    удааширч байгааг таамаглах болно."""
    import time as _t
    _p0 = _t.monotonic()
    _marks: list[str] = []

    def _mark(label: str, since: float) -> float:
        _marks.append(f"{label}={int((_t.monotonic() - since) * 1000)}мс")
        return _t.monotonic()

    if payment.status == "PAID":
        return  # idempotent — давхар webhook хамгаалалт
    # УРАЛДААН (2026-09-13 аудит): QPay webhook ба кассын «шалгах» (check) ЗЭРЭГ
    # ирэхэд хоёулаа PENDING гэж хараад хоёулаа баримт үүсгэж, нэг ДДТД DB-д 2
    # мөр болдог байв (9-р сард 3 төлбөр, 0.03–0.24с зайтай). Мөрийг NOWAIT-аар
    # түгжинэ: нөгөө нь түгжээг авч чадахгүй → «finalize явж байна» гэж буцна.
    # NOWAIT чухал: энгийн FOR UPDATE нь event loop-ыг блоклож, түгжээ барьсан
    # coroutine (e-Barimt HTTP хүлээж буй) хэзээ ч дуусахгүй гацаана.
    # ЗӨВХӨН БАГАНААР түгжинэ: Payment-ийн joined relationship-уудтай `FOR UPDATE`
    # Postgres-т «nullable side of outer join» гэж унадаг — тэр алдааг доорх
    # except барьж finalize-ыг бүхэлд нь алгасах байсан (2026-09-13 тестээр илэрсэн).
    try:
        row = (db.query(Payment.id, Payment.status).filter(Payment.id == payment.id)
               .with_for_update(nowait=True).first())
    except AttributeError:
        row = None  # тестийн FakeDB — түгжээгүй
    except Exception as e:  # noqa: BLE001
        msg = str(e).splitlines()[0]
        if "could not obtain lock" in msg or "LockNotAvailable" in msg:
            log.warning("finalize: payment=%s мөр түгжигдсэн (давхар finalize) — алгаслаа",
                        payment.id)
            db.rollback()
            raise HTTPException(409, "Төлбөр боловсруулагдаж байна. Дахин шалгана уу.") from e
        db.rollback()
        raise HTTPException(409, "Төлбөрийн түгжээ авч чадсангүй. Дахин шалгана уу.") from e
    if row is not None and row[1] == "PAID":
        return
    session = (db.query(ParkingSession).enable_eagerloads(False)
               .filter(ParkingSession.id == payment.session_id)
               .populate_existing().with_for_update(nowait=True).one())
    was_closed = session.status not in ("OPEN", "AWAITING_PAYMENT")
    # Хаагдсан сешнд (хожуу callback) нэхэмжлэлийн үнэ барихгүй — зөвхөн өр/илүү
    # төлөлтийг тооцно.
    settlement_fee, _quote_held = ((session_fee_info(db, session), False) if was_closed
                                   else _settlement_fee(db, session, payment))
    if _quote_held:
        db.add(AuditLog(username="system", action="PAYMENT_QUOTE_HONORED", entity="payment",
                        entity_id=payment.id,
                        detail={"quote_total": float(settlement_fee["total_fee"]),
                                "current_total": float(session_fee_info(db, session)["total_fee"]),
                                "invoice_age_sec": round((datetime.utcnow()
                                                          - payment.created_at).total_seconds())}))
    already_paid = paid_total(db, session)
    payment.status = "PAID"
    payment.paid_at = datetime.utcnow()
    if raw:
        payment.raw_payload = {**(payment.raw_payload or {}), **raw}

    # Энэ төлбөрт багтсан (QR-аар нийлүүлж төлсөн) өрүүд — тус бүрийн НӨАТ,
    # session-ий хэсгийн дүнг ялгаж тооцно
    snapshot = payment.fee_snapshot or {}
    debt_ids = [d["id"] for d in snapshot.get("debts", [])]
    comps = (db.query(Compensation)
             .filter(Compensation.id.in_(debt_ids) if "debts" in snapshot
                     else Compensation.payment_id == payment.id,
                     Compensation.status == "PENDING")
             .enable_eagerloads(False).populate_existing().order_by(Compensation.id)
             .with_for_update().all())
    vat_r = settings.vat_rate
    comp_vats = {c.id: round(float(c.amount) * vat_r / (1 + vat_r)) for c in comps}
    sess_amount = min(float(payment.amount), float(snapshot["parking_amount"])) if "parking_amount" in snapshot else float(payment.amount) - sum(float(c.amount) for c in comps)
    sess_vat = float(payment.vat_amount) - sum(comp_vats.values())

    from ..services.financial_jobs import enqueue_receipt, enqueue_partner, terminal_issues_receipt
    # Receipts are committed atomically with the payment, then delivered by a
    # durable worker. A paid driver never waits for a tax-provider HTTP call.
    has_external = bool(external_receipt and external_receipt.get("billId"))
    # POS терминал баримтаа ӨӨРӨӨ гаргадаг зогсоол (Тохиргоо → Төлбөрийн дүрэм →
    # e-Barimt): терминал НИЙТ дүнгээр НЭГ баримт хэвлэдэг тул өр тус бүрийн мөр
    # хуваахгүй — гаднаас ДДТД ирсэн үетэй ижил (full_receipt).
    terminal = not has_external and terminal_issues_receipt(db, payment)
    full_receipt = has_external or terminal or bool(
        settings.qpay_ebarimt and payment.provider == "QPAY" and payment.provider_payment_id)
    enqueue_receipt(db, payment,
        amount=payment.amount if full_receipt else sess_amount,
        vat=payment.vat_amount if full_receipt else sess_vat,
        description=_receipt_desc(payment), external=external_receipt, terminal=terminal)
    for comp in comps:
        if not full_receipt:
            enqueue_receipt(db, payment, amount=comp.amount, vat=comp_vats[comp.id],
                session_id=comp.session_id, key=f"pay-{payment.id}-comp-{comp.id}",
                description=_receipt_desc(payment, f"өр {comp.plate_number or ''}".strip()))
        comp.payment_id, comp.status = payment.id, "PAID"
        comp.paid_at, comp.paid_by = payment.paid_at, f"{payment.provider}:QR"
    if partner_notification:
        db.flush()  # Event balance/receipt reads must include this transaction.
        enqueue_partner(db, payment, *partner_notification)

    _t1 = _mark("ebarimt", _p0)
    if was_closed:
        # A late bank callback settles the receivable; it cannot resurrect the
        # stay or open a barrier after the vehicle left the payment queue.
        remaining = max(0.0, sess_amount)
        debts = (db.query(Compensation).filter(
            Compensation.session_id == session.id, Compensation.status == "PENDING")
            .enable_eagerloads(False).populate_existing()
            .order_by(Compensation.created_at).with_for_update().all())
        for debt in debts:
            applied = min(float(debt.amount), remaining)
            if applied <= 0:
                break
            old_amount = float(debt.amount)
            remaining -= applied
            if applied >= old_amount:
                debt.status = "PAID"
                debt.paid_at = payment.paid_at
                debt.paid_by = f"{payment.provider}:late"
                debt.payment_id = payment.id
            else:
                debt.amount = old_amount - applied
            db.add(AuditLog(username="system", action="LATE_PAYMENT_DEBT",
                            entity="compensation", entity_id=debt.id,
                            detail={"payment_id": payment.id, "applied": applied,
                                    "previous_amount": old_amount}))
        db.commit()
    else:
        db.flush()  # amount_due must include this confirmed financial fact
        remaining_due = amount_due(db, session, settlement_fee)
        if remaining_due > 0:
            session.base_fee, session.vat_amount, session.total_fee = (
                settlement_fee["base_fee"], settlement_fee["vat_amount"], settlement_fee["total_fee"])
            session.duration_minutes = settlement_fee["duration_minutes"]
            db.add(AuditLog(username="system", action="PAYMENT_PARTIAL", entity="payment",
                            entity_id=payment.id, detail={"remaining_due": remaining_due}))
            db.commit()
        else:
            if _quote_held:
                # Барьсан үнийг сешний дүн болгоно — эс бол дараагийн гарах уншилт
                # одоогийн (өндөр) дүнг бичиж тайлангийн «Үүссэн» хөөрөгдөнө.
                session.base_fee, session.vat_amount, session.total_fee = (
                    settlement_fee["base_fee"], settlement_fee["vat_amount"],
                    settlement_fee["total_fee"])
                session.duration_minutes = settlement_fee.get("duration_minutes",
                                                              session.duration_minutes)
            await mark_paid_and_open(db, session, fee_snapshot=settlement_fee, pos_lane=pos_lane)
    overpaid = max(0.0, round(already_paid + sess_amount - settlement_fee["total_fee"], 2))
    if overpaid:
        # Preserve the received money and flag a refund/reconciliation; never silently drop it.
        payment.raw_payload = {**(payment.raw_payload or {}), "overpaid_amount": overpaid}
        db.add(AuditLog(username="system", action="PAYMENT_OVERPAYMENT", entity="payment",
                        entity_id=payment.id, detail={"overpaid_amount": overpaid}))
        db.commit()
    _mark("хаалт+session", _t1)
    total = int((_t.monotonic() - _p0) * 1000)
    (log.warning if total >= settings.payment_slow_warn_ms else log.info)(
        "төлбөр finalize: %dмс (%s) provider=%s", total, " ".join(_marks), payment.provider)


async def _confirm_qpay(db: Session, payment: Payment) -> bool:
    """QPay төлбөрийг баталгаажуулж (mock үед шууд), g_payment_id аваад finalize хийнэ.
    Буцаах: PAID болсон эсэх. Дүн зөрвөл REVIEW болгож False буцаана."""
    if payment.status == "PAID":
        return True
    if settings.qpay_mock:
        # Туршилтын горим — QPay ebarimt_v3-ийн урсгалыг бүрэн дуурайхын тулд mock payment_id тавина
        if not payment.provider_payment_id:
            payment.provider_payment_id = f"MOCK-PAY-{uuid.uuid4().hex[:12]}"
        await _finalize_paid(db, payment)
        return True
    from decimal import Decimal, InvalidOperation
    from ..services.payment_validation import claim_reference
    pid, invoice, acc = payment.id, payment.provider_invoice_id, qpay.account_for(_site_of(payment))
    db.commit()  # No payment/session lock is held during provider HTTP.
    res = await qpay.check_payment(invoice, acc=acc)
    if not res.get("paid"):
        return False
    payment = _lock_payment(db, pid)
    if payment.status == "PAID":
        return True
    try:
        received = Decimal(str(res.get("paid_amount", 0)))
    except (InvalidOperation, ValueError):
        received = Decimal("NaN")
    reference = res.get("payment_id")
    if (not received.is_finite() or received < Decimal(str(payment.amount)) or
            not isinstance(reference, str) or not reference.strip() or len(reference.strip()) > 120
            or any(ord(c) < 32 for c in reference)):
        payment.status = "REVIEW"
        payment.raw_payload = {**(payment.raw_payload or {}), "verification": "amount_or_reference_mismatch"}
        db.commit()
        return False
    claim_reference(db, payment, "qpay:" + acc.username, reference.strip())
    await _finalize_paid(db, payment, raw=res.get("raw"))
    return True


def _pos_app_version(request: Request | None) -> str | None:
    """POS апп ХҮСЭЛТ БҮРД `X-App-Version` илгээдэг (PAX_POS_APP_GUIDE §2); веб касс
    (api.js) илгээдэггүй — POS аппын төлбөрийг веб кассынхаас ялгах цорын ганц тэмдэг."""
    if request is None:
        return None
    v = (request.headers.get("X-App-Version") or "").strip()[:40]
    return v or None


def _browser_request(request: Request | None) -> bool:
    """Веб хөтчийн хүсэлт үү (веб касс — api.js). POS апп (Android, Flutter/OkHttp) хөтчийн
    «Mozilla/…» User-Agent илгээдэггүй. Толгойгүй хүсэлтийн POS аппын хэрэглэгчийн
    fallback-ийг зөвхөн хөтөч БИШ клиентэд хэрэглэнэ (2026-10-14 хяналт)."""
    if request is None:
        return False
    return "mozilla/" in (request.headers.get("User-Agent") or "").lower()


def _mark_pos_app(payment: Payment, request: Request | None, *, always: bool = False,
                  user: User | None = None, db: Session | None = None,
                  site_id: str | None = None) -> None:
    """POS аппаар авсан төлбөрийг source=POSAPP гэж тэмдэглэнэ (2026-10-13; 10-14).

    Яагаад: PAX терминал (Ontime) POS аппын карт/бэлэн төлбөрт e-Barimt-ыг өөрөө
    хэвлэдэг — БҮХ зогсоолд (2026-10-14 эзний шийдвэр) систем ДАВХАР баримт гаргахгүйн
    тулд аль төлбөр POS аппынх болохыг мэдэх ёстой (services/pos_app — нэг тодорхойлолт).
    always=True — /pos/confirm (зөвхөн POS апп дууддаг картын баталгаажуулалт).

    X-App-Version толгой: 10-14-ний deploy-оос хойш POS аппын бэлэн төлбөр 20/20 толгойтой
    (1.5.1/1.5.3). Толгойгүй (хуучин build) үед кассчин нь POS АППЫН ХЭРЭГЛЭГЧ бол (role
    «POS», тохиргооны жагсаалт, эсвэл POS аппаар ≥20 карт авсан) — ЗОГСООЛООС ҮЛ ХАМААРАН
    POS аппынх гэж тооцно (pos_app_via-д шалтгаан). Веб кассын ажилтан эдгээрийн аль нь ч
    биш тул хуучин зан (систем баримт гаргана). ХӨТӨЧИЙН хүсэлт (User-Agent «Mozilla/…» —
    веб касс) fallback-д ОРОХГҮЙ: POS аппын хэрэглэгч веб кассаар бэлэн авбал терминал
    хэвлээгүй тул систем баримт гаргана (баримтгүй үлдэхээс сэргийлнэ). site_id — хуучин
    дуудлагатай нийцүүлэх."""
    v = _pos_app_version(request)
    via = "header" if v else ("confirm" if always else None)
    if via is None and user is not None and db is not None and not _browser_request(request):
        from ..services.pos_app import user_pos_app_via
        via = user_pos_app_via(db, user)
    if via:
        payment.source = "POSAPP"
    if v or via not in (None, "confirm"):
        payment.raw_payload = {**(payment.raw_payload or {}),
                               **({"app_version": v} if v else {"pos_app_via": via})}


def _remember_app_version(terminal, version: str | None):
    """POS аппын хувилбарыг терминалын бүртгэлд хадгална (X-App-Version толгой)."""
    v = (version or "").strip()[:40]
    if not v or terminal is None:
        return
    extra = dict(terminal.extra or {})
    if extra.get("app_version") != v:
        log.info("POS апп хувилбар: %s → %s", terminal.name or terminal.device_key, v)
    extra["app_version"] = v
    extra["app_version_at"] = datetime.utcnow().isoformat(timespec="seconds")
    terminal.extra = extra


def _print_payload(db: Session, payment: Payment) -> dict:
    """POS терминал/веб-д хэвлэх e-Barimt баримтын мэдээлэл (мөрүүд + QR + сугалаа)."""
    session = db.get(ParkingSession, payment.session_id)
    # Нэг төлбөрт олон мөр (өрийн баримт, цуцлагдаад дахин үүссэн) байхад
    # `.first()` дурын нэгийг өгч ХЭВЛЭСЭН ≠ ХАДГАЛСАН болдог байв (2026-09-07)
    receipt = primary_receipt(db, payment.id)
    site = session.site if session else None
    tz = timedelta(hours=8)     # Улаанбаатар — принтерт ОРОН НУТГИЙН цаг хэвлэнэ
    ent = (session.entry_time + tz) if session and session.entry_time else None
    ext = (session.exit_time + tz) if session and session.exit_time else None
    if ext is None and session and session.status in ("OPEN", "AWAITING_PAYMENT"):
        ext = datetime.utcnow() + tz   # гарц дээр төлж байгаа — «одоо» гэж хэвлэнэ
    mins = int(session.duration_minutes or 0) if session else 0
    dur = f"{mins // 60}ц {mins % 60}м" if mins else "-"
    method = {"CASH": "Бэлэн", "CARD": "Карт", "QR": "QPay",
              "TRANSFER": "Дансаар"}.get(payment.payment_method or "", payment.payment_method or "-")
    status = receipt.status if receipt else None
    terminal = status == "EXTERNAL"   # POS терминал (Ontime) өөрөө баримт хэвлэсэн
    # ДДТД-ийн мөр: «ДДТД: -» нь жолоочид «баримт алга» гэж ойлгогддог байв.
    #   EXTERNAL — баримт терминалаас тусдаа хэвлэгдсэн (систем давхар үүсгээгүй)
    #   PENDING  — msgbill 2–5с-д ДДТД буцаана; апп /print-ээр дахин татаж хэвлэнэ
    if terminal:
        ddtd_line = "НӨАТ баримт: POS терминал (Ontime) гаргасан"
    elif receipt and receipt.ebarimt_id:
        ddtd_line = f"ДДТД: {receipt.ebarimt_id}"
    elif status == "PENDING":
        ddtd_line = "ДДТД: хүлээгдэж байна"
    else:
        ddtd_line = "ДДТД: -"
    lines = [
        "ЗОГСООЛЫН ТӨЛБӨРИЙН БАРИМТ",
        site.name if site else "",
        f"Дугаар: {session.plate_number if session else '-'}",
        f"Орсон:  {ent:%Y-%m-%d %H:%M}" if ent else "",
        f"Гарсан: {ext:%Y-%m-%d %H:%M}" if ext else "",
        f"Хугацаа: {dur}",
        f"Төлсөн: {method}",
        f"Дүн: {float(payment.amount):,.0f}₮",
        f"НӨАТ: {float(payment.vat_amount):,.0f}₮",
        ddtd_line,
    ]
    if payment.customer_tin and payment.ebarimt_receiver_type == "COMPANY":
        lines.append(f"Худалдан авагч ТТД: {payment.customer_tin}")  # ААН — сугалаа хэвлэгдэхгүй
    else:
        if payment.customer_tin:
            lines.append(f"Регистр: {payment.customer_tin}")
        if receipt and receipt.lottery_code:
            lines.append(f"Сугалаа: {receipt.lottery_code}")
    return {
        "ebarimt_id": receipt.ebarimt_id if receipt else None,
        "lottery_code": receipt.lottery_code if receipt else None,
        # thermal printer энэ qrData-г QR болгон хэвлэнэ (түр санах ойгоос — DB-д хадгалагдахгүй)
        "qr_data": ebarimt.get_cached_qr(payment.id),
        # Апп өөрөө байрлуулж хэвлэхэд зориулсан ТАЛБАРУУД (мөрүүдээс задлахгүй).
        # `print_data.lines` нь бэлэн формат — аль нэгийг нь сонгож хэрэглэнэ.
        "receipt": {
            "site_name": site.name if site else None,
            "plate_number": session.plate_number if session else None,
            "entry_time": ent.isoformat(timespec="seconds") if ent else None,
            "exit_time": ext.isoformat(timespec="seconds") if ext else None,
            "duration_minutes": mins,
            "duration_text": dur,
            "amount": float(payment.amount),
            "vat_amount": float(payment.vat_amount or 0),
            "payment_method": payment.payment_method,
            "receipt_type": ("ORGANIZATION" if payment.ebarimt_receiver_type == "COMPANY"
                             else "CITIZEN"),
            "payer_reg_no": payment.customer_tin,
            "ebarimt_id": receipt.ebarimt_id if receipt else None,
            "lottery_code": receipt.lottery_code if receipt else None,
            # SENT | PENDING (ДДТД удахгүй — апп дахин татна) | EXTERNAL (терминал
            # өөрөө гаргасан, ДДТД манайд алга) | FAILED | REVIEW | CANCELLED
            "ebarimt_status": status,
            "ebarimt_error": receipt.receipt_url if receipt and status == "FAILED" else None,
            "ebarimt_note": receipt.receipt_url if terminal else None,
            "ebarimt_issuer": "POS_TERMINAL" if terminal else (receipt.provider if receipt else None),
        },
        "print_data": {"lines": [ln for ln in lines if ln]},
    }


def _tenant_site_ids(db: Session, site) -> list[str] | None:
    """Энэ зогсоолтой ИЖИЛ ТҮРЭЭСЛЭГЧИЙН зогсоолуудын id. site=None → None (хязгааргүй)."""
    if site is None:
        return None
    tid = getattr(site, "tenant_id", None)
    q = db.query(ParkingSite.id)
    q = q.filter(ParkingSite.tenant_id == tid) if tid else q.filter(ParkingSite.tenant_id.is_(None))
    return [r[0] for r in q.all()]


def _pending_debts(db: Session, plate: str, site=None) -> list[Compensation]:
    """Дугаарын төлөгдөөгүй нөхөн төлбөрүүд — өр дугаарыг дагадаг, ГЭХДЭЭ зөвхөн
    ИЖИЛ ТҮРЭЭСЛЭГЧИЙН зогсоолуудынх (2026-09-13 тулгалт: EasyParking-ийн зогсоолд
    Моннисын өр 38,000₮, Моннист EasyParking-ийн 19,500₮ нийлүүлж хураагдаж, баримт
    нь ТӨЛЖ БУЙ зогсоолын QPay данс/ТТД-ээр гарч НӨАТ, орлого буруу компанид очиж
    байв). Түрээслэгчгүй зогсоол = зөвхөн түрээслэгчгүй зогсоолуудын өр."""
    q = db.query(Compensation).filter(Compensation.plate_number == plate,
                                      Compensation.status == "PENDING")
    ids = _tenant_site_ids(db, site)
    if ids is not None:
        q = q.filter(Compensation.site_id.in_(ids))
    return q.all()


def settlement_info(db, payment):
    session = db.get(ParkingSession, payment.session_id)
    if not session:
        return {}
    if session.status in ("OPEN", "AWAITING_PAYMENT"):
        due = amount_due(db, session, session_fee_info(db, session))
    else:
        due = sum(float(c.amount) for c in db.query(Compensation).filter(
            Compensation.session_id == session.id, Compensation.status == "PENDING").all())
    return {"session_status": session.status, "amount_due": due,
            "needs_additional_payment": due > 0,
            "overpaid_amount": (payment.raw_payload or {}).get("overpaid_amount", 0)}


def payment_outcome(db, payment):
    """Money, remaining balance and device acknowledgment are separate facts."""
    from ..services.payment_validation import gate_result
    return {**settlement_info(db, payment), **gate_result(db, payment)}


def _create_payment(db: Session, session: ParkingSession, provider: str, method: str,
                    cashier: User | None = None, include_debts: bool = False,
                    *, confirmed_amount=None, allow_qpay: bool = False) -> Payment:
    from ..services.checkout import lock_session, assert_available
    from ..services.payment_validation import money
    session = lock_session(db, session.id)
    # Confirmations record money already received; reservations must never compete.
    if confirmed_amount is None:
        if session.status not in ("OPEN", "AWAITING_PAYMENT"):
            raise HTTPException(409, "Бүртгэлийн төлөв өөрчлөгдсөн. Жагсаалтаа шинэчилнэ үү")
        assert_available(db, session.id, allow_qpay=allow_qpay)
    fee = session_fee_info(db, session)
    due = amount_due(db, session, fee)
    comps = (_pending_debts(db, session.plate_number, session.site) if include_debts else [])
    debt_total = sum(float(c.amount) for c in comps)
    if confirmed_amount is None and due + debt_total <= 0:
        raise HTTPException(400, "Төлбөр шаардлагагүй эсвэл бүрэн төлөгдсөн байна")
    parking_amount = money(confirmed_amount) if confirmed_amount is not None else money(due, positive=False)
    r = settings.vat_rate
    vat_due = round(float(parking_amount) * r / (1 + r))
    if confirmed_amount is None and due >= fee["total_fee"]:
        vat_due = fee["vat_amount"]
    if session.status in ("OPEN", "AWAITING_PAYMENT"):
        session.base_fee, session.vat_amount, session.total_fee = (
            fee["base_fee"], fee["vat_amount"], fee["total_fee"])
        session.duration_minutes = fee["duration_minutes"]
    debt_vat = sum(round(float(c.amount) * r / (1 + r)) for c in comps)
    shift = None
    if cashier:
        shift = db.query(CashierShift).filter(CashierShift.user_id == cashier.id,
                                             CashierShift.status == "OPEN").first()
    payment = Payment(
        session_id=session.id, provider=provider, payment_method=method,
        sender_invoice_no=_invoice_no(session),
        amount=float(parking_amount) + debt_total, vat_amount=vat_due + debt_vat,
        fee_snapshot={**fee, "parking_amount": float(parking_amount),
                      "debts": [{"id": c.id, "amount": float(c.amount)} for c in comps]},
        cashier_id=cashier.id if cashier else None, shift_id=shift.id if shift else None)
    db.add(payment)
    db.flush()
    for comp in comps:
        comp.payment_id = payment.id
    payment._debt_comps = comps
    return payment


def _throttle_qpay(request: Request, name: str, limit: int = 30):
    """Нэвтрэлтгүй QPay endpoint-үүдийн IP хязгаар — public pay page-д хангалттай,
    спамд (дурын session-д invoice үүсгэх, QPay руу давтан дуудлага) хаалт болно."""
    ip = request.client.host if request.client else "?"
    if throttle(f"qpay:{name}:{ip}", limit=limit, window=60):
        raise HTTPException(429, "Хэт олон хүсэлт — түр хүлээгээд дахин оролдоно уу")


def _log_qpay_failure(db: Session, session: ParkingSession, acc, reason: str,
                      detail: dict) -> None:
    """QPay нэхэмжлэл бүтэлгүйтсэнийг өгөгдлийн санд үлдээнэ.

    ЯАГААД: алдаа гармагц `db.rollback()` хийдэг тул хагас үүссэн Payment мөр
    устаж, DB-д ЯМАР Ч ул мөр үлддэггүй байв. Улмаар «хэдэн жолооч, аль зогсоол
    дээр, хэдэн удаа төлж чадсангүй» гэдэг хойшоо ХЭМЖИГДЭХГҮЙ (зөвхөн
    journalctl, тэр нь хэдхэн хоногийн дараа устдаг). Одоо audit_logs-д
    `QPAY_INVOICE_FAIL` гэж бичигдэнэ:

        SELECT date_trunc('hour', created_at) h, detail->>'site' site,
               detail->>'reason' reason, count(*)
          FROM audit_logs WHERE action='QPAY_INVOICE_FAIL'
         GROUP BY 1,2,3 ORDER BY 1 DESC;
    """
    try:
        db.add(AuditLog(
            username="system", action="QPAY_INVOICE_FAIL", entity="session",
            entity_id=session.id,
            detail={"reason": reason,
                    "plate": session.plate_number,
                    "site": (session.site.site_code if session.site else ""),
                    "qpay_user": getattr(acc, "username", ""), **detail}))
        db.commit()
    except Exception as e:  # noqa: BLE001
        log.warning("QPay алдааг audit_logs-д бүртгэж чадсангүй: %r", e)
        db.rollback()


# ─────────────────────────── QPay ───────────────────────────
def _record_invoice_outcome_unknown(db, payment_id):
    db.rollback()
    payment = _lock_payment(db, payment_id)
    if payment is not None and payment.status != "PAID":
        payment.status = "UNKNOWN"
        db.add(AuditLog(username="system", action="QPAY_INVOICE_UNKNOWN", entity="payment",
                        entity_id=payment.id, detail={"sender_invoice_no": payment.sender_invoice_no}))
        db.commit()


@router.post("/qpay/invoice")
async def qpay_invoice(body: dict, request: Request, db: Session = Depends(get_db)):
    """QPay invoice үүсгэх. body: {session_id}. Public pay page + касс хоёулаа ашиглана."""
    # 2026-08-19: 20 → 60/мин. Утасны операторын CGNAT-аар олон жолооч НЭГ IP-ээр
    # ирдэг тул ачаалалтай зогсоолд 20 нь хүрэлцэхгүй, «товч анивчаад юу ч
    # болохгүй» (429 чимээгүй) гомдол гарч байв.
    _throttle_qpay(request, "invoice", limit=60)
    from sqlalchemy.exc import OperationalError
    try:
        session = (db.query(ParkingSession).enable_eagerloads(False)
                   .filter(ParkingSession.id == body.get("session_id", ""))
                   .populate_existing().with_for_update(nowait=True).first())
    except OperationalError:
        db.rollback()
        raise HTTPException(409, "Төлбөр боловсруулагдаж байна. Түр хүлээнэ үү.")
    if not session:
        raise HTTPException(404, "Session олдсонгүй")
    if session.status not in ("OPEN", "AWAITING_PAYMENT"):
        raise HTTPException(400, f"Session төлөв буруу: {session.status}")
    if db.query(Payment.id).filter(Payment.session_id == session.id,
                                  Payment.payment_method == "WALLET",
                                  Payment.status.in_(["PENDING", "UNKNOWN", "REVIEW"])).first():
        raise HTTPException(409, "Дансны гүйлгээг шалгаж байна. Давхар нэхэмжлэл үүсгэхгүй.")

    # Өмнөх өрийг QR-д нийлүүлж нэхэмжилнэ (body-д include_debts=false өгвөл зөвхөн
    # одоогийн төлбөр — кассын тусгай хэрэглээнд)
    include_debts = bool(body.get("include_debts", True))

    # Өмнө үүсгэсэн PENDING invoice байвал дүн нь одоогийн үлдэгдэл (+өр)-тэй таарч
    # байгаа тохиолдолд л дахин ашиглана (хугацаа өнгөрч тариф өссөн эсвэл
    # grace хэтэрч зөрүү нэхэж буй үед хуучин QR буруу дүнтэй болно)
    existing = db.query(Payment).filter(Payment.session_id == session.id,
                                        Payment.provider == "QPAY",
                                        Payment.status == "PENDING").first()
    if existing and existing.provider_invoice_id:
        current_due = amount_due(db, session, session_fee_info(db, session))
        if include_debts:
            current_due += sum(float(c.amount)
                               for c in _pending_debts(db, session.plate_number, session.site))
        if abs(float(existing.amount) - current_due) <= 0.01:
            # qr_image-ыг заавал буцаана: өмнө нь энэ (ДАВТАН хүсэлтийн) зам
            # түүнийг огт өгдөггүй байсан тул хуудсаа сэргээсэн жолоочид QR-ийн
            # оронд түүхий текст гарч, төлж чаддаггүй байв (2026-08-14 гомдол)
            # Банкны deeplink жагсаалтыг ч буцаана — үүсгэх үед raw_payload-д
            # хадгалсан (өмнө нь [] буцаадаг байсан тул буцаж орсон жолоочид
            # банк сонгох товчнууд алга болдог байв)
            _raw = existing.raw_payload if isinstance(existing.raw_payload, dict) else {}
            return {"payment_id": existing.id, "invoice_id": existing.provider_invoice_id,
                    "qr_text": existing.qr_text,
                    "qr_image": qpay.qr_png_b64(existing.qr_text or ""),
                    "deep_link": existing.deep_link, "urls": _raw.get("invoice_urls") or [],
                    "amount": float(existing.amount), "mock": settings.qpay_mock}
        try:
            await qpay.cancel_invoice(existing.provider_invoice_id,
                                      acc=qpay.account_for(session.site))
        except httpx.HTTPError:
            raise HTTPException(409, "Өмнөх QPay нэхэмжлэлийг цуцалж чадсангүй. "
                                     "Шинэ нэхэмжлэл үүсгээгүй; төлөлтөө шалгана уу.")
        existing.status = "CANCELLED"
        db.flush()

    payment = _create_payment(db, session, "QPAY", "QR", include_debts=include_debts)
    payment.source = "POS" if body.get("source") == "POS" else "QR"  # кассын пос эсвэл утас
    # НӨАТ-ийн баримтын хүлээн авагчийн төрөл: ТТД өгвөл ААН (COMPANY), үгүй бол иргэн (CITIZEN)
    receiver_data = None
    if body.get("customer_tin"):
        payment.customer_tin = str(body["customer_tin"]).strip()[:20]
        payment.ebarimt_receiver_type = "COMPANY"
        receiver_data = {"register": payment.customer_tin}
    else:
        payment.ebarimt_receiver_type = "CITIZEN"
    callback = f"{settings.public_base_url}/api/payments/qpay/webhook?payment_id={payment.id}"
    if settings.qpay_webhook_secret:
        callback += f"&token={settings.qpay_webhook_secret}"
    # e-Barimt нэхэмжлэхийн мөрүүд — session-ий төлбөр нэг мөр, өр тус бүр тусдаа мөр
    linked_comps = getattr(payment, "_debt_comps", [])
    debt_total = sum(float(c.amount) for c in linked_comps)
    # Үнэгүй хугацаанд багтсан ч ӨМНӨХ ӨРТЭЙ машины «одоогийн төлбөр» 0₮ болно.
    # 0₮ мөрийг QPay татгалздаг (QR огт үүсэхгүй) тул огт нэмэхгүй — нийт дүн
    # өөрчлөгдөхгүй, зөвхөн өрийн мөрүүд үлдэнэ.
    session_part = round(float(payment.amount) - debt_total, 2)
    line_items = []
    if session_part > 0:
        line_items.append({
            "description": f"Зогсоолын үйлчилгээ — {session.plate_number}",
            "unit_price": session_part, "quantity": 1,
        })
    for comp in linked_comps:
        line_items.append({
            "description": f"Өмнөх өр ({comp.created_at:%Y-%m-%d}) — {comp.plate_number}",
            "unit_price": float(comp.amount), "quantity": 1,
        })
    acc = qpay.account_for(session.site)
    # rollback хийсний дараа payment мөр устдаг тул дүнг УРЬДЧИЛАН авч үлдэнэ
    # (алдааны бүртгэлд «хэдэн төгрөгийн төлбөр бүтсэнгүй» гэдэг чухал).
    payment_amount = float(payment.amount)
    try:
        lines = qpay.build_lines(line_items, acc)
    except ValueError as e:  # 0₮/сөрөг мөр — QPay руу илгээх утгагүй
        log.error("QPay нэхэмжлэлийн мөр буруу (%s): %s", session.plate_number, e)
        db.rollback()
        _log_qpay_failure(db, session, acc, "BAD_LINES", {"error": str(e)[:200]})
        raise HTTPException(502, "Нэхэмжлэлийн дүн буруу — QR үүсгэж чадаагүй. "
                                 "Кассаар төлнө үү.")
    # QPay унах нь ЖОЛООЧИД харагддаг алдаа — «QR гарахгүй байна» гомдол. Өмнө нь
    # raise_for_status()/timeout нь endpoint-оос шууд гарч 500 болдог байсан:
    # (а) жолоочид ойлгомжгүй, (б) rollback болохоор DB-д ЯМАР Ч ул мөр үлддэггүй
    # тул «хэдэн удаа тохиолдов» гэдгийг хойшоо хэмжих боломжгүй байв. Одоо
    # шалтгааныг логлож, хүнд ойлгомжтой 502 буцаана (frontend дахин оролдоно).
    payment.status = "CREATING"
    operation_id = payment.id
    db.commit()
    try:
        inv = await qpay.create_invoice(
            payment.sender_invoice_no,
            f"Зогсоолын төлбөр — {session.plate_number}",
            f"terminal_{session.site.site_code if session.site else 'X'}", callback,
            lines, receiver_data=receiver_data, acc=acc,
        )
        if not isinstance(inv.get("invoice_id"), str) or not inv["invoice_id"].strip():
            raise ValueError("Provider invoice ID missing")
    except httpx.HTTPStatusError as e:
        body_txt = (e.response.text or "")[:400]
        log.error("QPay invoice БҮТЭЛГҮЙ (%s, %s): HTTP %s — %s",
                  session.plate_number, acc.username, e.response.status_code, body_txt)
        _record_invoice_outcome_unknown(db, operation_id)
        _log_qpay_failure(db, session, acc, "HTTP",
                          {"status": e.response.status_code, "body": body_txt,
                           "amount": float(payment_amount)})
        raise HTTPException(502, "QPay-тэй холбогдож чадсангүй — QR үүсгэж чадаагүй. "
                                 "Үр дүн тодорхойгүй тул дахин төлөхгүй; нэхэмжлэлийг тулгана уу.")
    except httpx.HTTPError as e:  # timeout, DNS, холболт тасрах
        log.error("QPay invoice БҮТЭЛГҮЙ (%s, %s): %r", session.plate_number, acc.username, e)
        _record_invoice_outcome_unknown(db, operation_id)
        _log_qpay_failure(db, session, acc, "NETWORK",
                          {"error": f"{type(e).__name__}: {e}"[:200],
                           "amount": float(payment_amount)})
        # 503 = ТҮР ЗУУРЫН. Frontend зөвхөн үүнд автоматаар дахин оролддог;
        # QPay-ийн татгалзал (502) нь дахин оролдоход ижил хариу өгнө.
        raise HTTPException(503, "QPay хариу өгсөнгүй (сүлжээ) — QR үүсгэж чадаагүй. "
                                 "Үр дүн тодорхойгүй тул дахин төлөхгүй; нэхэмжлэлийг тулгана уу.")
    except ValueError as e:  # талбарын хязгаар (sender_invoice_no гэх мэт)
        log.error("QPay invoice талбар буруу (%s): %s", session.plate_number, e)
        _record_invoice_outcome_unknown(db, operation_id)
        _log_qpay_failure(db, session, acc, "BAD_FIELD", {"error": str(e)[:200]})
        raise HTTPException(502, "Нэхэмжлэлийн талбар буруу — QR үүсгэж чадаагүй. "
                                 "Өмнөх нэхэмжлэлийг тулгаж байж дахин оролдоно уу.")
    except KeyError as e:  # QPay 200 буцаасан ч хүлээгдсэн талбар алга
        log.error("QPay invoice хариу дутуу (%s): талбар %s алга", session.plate_number, e)
        _record_invoice_outcome_unknown(db, operation_id)
        _log_qpay_failure(db, session, acc, "BAD_RESPONSE", {"missing_field": str(e)[:60]})
        raise HTTPException(502, "QPay-ээс ирсэн хариу дутуу байна — QR үүсгэж чадаагүй. "
                                 "Үр дүн тодорхойгүй тул дахин төлөхгүй; нэхэмжлэлийг тулгана уу.")
    payment = _lock_payment(db, operation_id)
    if payment is None:
        raise HTTPException(409, "Нэхэмжлэлийн төлөв шинэчлэгдэж байна. Дахин шалгана уу")
    payment.provider_invoice_id = inv["invoice_id"]
    if payment.status in ("CREATING", "UNKNOWN"):
        payment.status = "PENDING"
    payment.qr_text = inv.get("qr_text") or ""
    payment.deep_link = inv.get("deep_link") or ""
    # Банкны deeplink-үүдийг хадгална — давтан хүсэлт (хуудас сэргээх/буцаж орох)
    # ижил нэхэмжлэлийг бүрэн (QR + банкны товчнууд) буцаахад хэрэгтэй.
    # finalize үед raw_payload QPay-ийн төлбөрийн хариугаар дарагдана — зүгээр.
    payment.raw_payload = {**(payment.raw_payload or {}), "invoice_urls": inv.get("urls", [])[:40]}
    db.commit()
    return {"payment_id": payment.id, "invoice_id": inv["invoice_id"],
            "qr_text": payment.qr_text, "qr_image": inv.get("qr_image", ""),
            "deep_link": payment.deep_link, "urls": inv.get("urls", []),
            "amount": float(payment.amount), "debt_amount": debt_total,
            "mock": inv.get("mock", False)}


async def _webhook_handler(payment_id: str, token: str, qpay_payment_id: str,
                           body: dict, db: Session):
    """QPay callback-ийн нийтлэг логик (GET/POST хоёуланд).

    QPay стандарт: callback нь GET, HTTP 200 + body 'SUCCESS' буцаах ёстой. Тиймээс
    төлбөрийг QPay-ийн payment/check-ээр эргэж баталгаажуулна (query дахь дүнд итгэхгүй)."""
    if settings.qpay_webhook_secret:
        if not secrets_compare(token, settings.qpay_webhook_secret):
            raise HTTPException(403, "Webhook токен буруу")
    payment = None
    if payment_id:
        payment = db.get(Payment, payment_id)
    if not payment and body.get("sender_invoice_no"):
        payment = db.query(Payment).filter(
            Payment.sender_invoice_no == body["sender_invoice_no"]).first()
    if not payment:
        raise HTTPException(404, "Payment олдсонгүй")
    if payment.provider != "QPAY" or payment.kind != "PARKING":
        raise HTTPException(400, "Энэ callback зогсоолын QPay төлбөрт хамаарахгүй")
    from ..services.qpay_recheck import request_verification, finish_verification
    pid = payment.id
    stamp = request_verification(db, payment)
    if not payment.provider_invoice_id:
        raise HTTPException(503, "Нэхэмжлэлийн үр дүн тодорхойгүй; callback хадгалагдсан")
    await _confirm_qpay(db, payment)
    finish_verification(db, pid, stamp, completed=True)


@router.get("/qpay/webhook")
async def qpay_webhook_get(payment_id: str = "", token: str = "",
                           qpay_payment_id: str = "", db: Session = Depends(get_db)):
    """QPay callback (GET) — стандарт форматаар 'SUCCESS' plain text буцаана."""
    await _webhook_handler(payment_id, token, qpay_payment_id, {}, db)
    return PlainTextResponse("SUCCESS")


@router.post("/qpay/webhook")
async def qpay_webhook_post(request: Request, payment_id: str = "", token: str = "",
                            qpay_payment_id: str = "", db: Session = Depends(get_db)):
    """QPay callback (POST) — зарим тохиргоонд body-той POST-оор ирдэг. 'SUCCESS' буцаана.
    Мөн pay page-ийн туршилтын горим (mock) энэ endpoint-ийг дууддаг."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not qpay_payment_id:
        qpay_payment_id = body.get("qpay_payment_id") or body.get("payment_id") or ""
    await _webhook_handler(payment_id, token, qpay_payment_id, body, db)
    return PlainTextResponse("SUCCESS")


@router.post("/qpay/check/{payment_id}")
async def qpay_check(payment_id: str, request: Request, db: Session = Depends(get_db)):
    """Webhook ирээгүй үед polling шалгалт (pay page/POS 5 сек тутам дуудна).
    PAID болмогц e-Barimt-ийн хэвлэх мэдээллийг (POS терминалд) хамт буцаана."""
    # 5 сек тутам poll = 12/мин/утас; CGNAT-аар нэг IP-д олон жолооч → 300
    _throttle_qpay(request, "check", limit=300)
    payment = db.get(Payment, payment_id)
    if not payment:
        raise HTTPException(404, "Payment олдсонгүй")
    # Browser polling reads our durable result. Bank verification is triggered
    # by callback, as required by QPay, not by every foreground polling request.
    if payment.status == "PAID":
        return {"status": "PAID", **_print_payload(db, payment), **payment_outcome(db, payment)}
    return {"status": payment.status}


# ─────────────────────────── Касс (бэлэн мөнгө) ───────────────────────────
# ЭРХ (бүх кассын endpoint): role биш «cashier» МОДУЛИАР. Өмнө нь
# require_role("OPERATOR", "SUPER_ADMIN") байсан тул:
#   • ADMIN — кассын хуудас нээгддэг ч төлбөр авахад 403 («операторын эрх
#     шаардаж байна» гэсэн гомдол яг үүнээс)
#   • ONLINE_OPERATOR — pay_transfer эрхтэй атлаа /transfer рүү огт ороогүй
#     (энэ ролийн БҮХ утга учир нь дансаар төлбөр батлах)
# free_exit шиг санхүүгийн эрсдэлтэй тусгай үйлдлүүд ХЭВЭЭР тусдаа эрхтэй.
def _assert_expected_amount(payment, body):
    if "expected_amount" not in body:
        return  # legacy POS clients; current cashier always sends its displayed amount
    from decimal import Decimal, InvalidOperation
    try:
        expected = Decimal(str(body["expected_amount"]))
        valid = expected.is_finite() and abs(expected - Decimal(str(payment.amount))) <= Decimal("0.01")
    except (InvalidOperation, ValueError, TypeError):
        valid = False
    if not valid:
        raise HTTPException(409, "Үнэ барих хугацаа дууссан эсвэл дүн өөрчлөгдсөн. Шинэ дүнг шалгаж дахин батална уу.")


async def _retire_qpay(db, session):
    pending = db.query(Payment).filter(Payment.session_id == session.id,
                                      Payment.provider == "QPAY",
                                      Payment.status.in_(["PENDING", "CREATING", "UNKNOWN", "REVIEW"])).all()
    for p in pending:
        if p.status != "PENDING" or not p.provider_invoice_id:
            raise HTTPException(409, "QPay-ийн өмнөх оролдлогын үр дүнг эхлээд тулгана уу")
        if p.provider_invoice_id:
            try:
                await qpay.cancel_invoice(p.provider_invoice_id, acc=qpay.account_for(session.site))
            except httpx.HTTPError as e:
                raise HTTPException(409, "Өмнөх QPay нэхэмжлэл идэвхтэй байж болзошгүй. Төлөлтийг шалгана уу.") from e
        p.status = "CANCELLED"


@router.post("/cash")
async def cash_payment(body: dict, db: Session = Depends(get_db),
                       user: User = Depends(require("cashier")), request: Request = None):
    """Кассын бэлэн мөнгөний төлбөр. body: {session_id, customer_tin?,
    ebarimt_id?/lottery_code?/qr_data?/ebarimt_provider? — гадна үүссэн баримт}.
    X-App-Version толгойтой (POS апп) бол source=POSAPP."""
    session = db.get(ParkingSession, body.get("session_id", ""))
    if not session:
        raise HTTPException(404, "Session олдсонгүй")
    enforce_site(user, session.site_id)  # оператор зөвхөн өөрийн зогсоолын төлбөр
    if session.status not in ("OPEN", "AWAITING_PAYMENT"):
        raise HTTPException(400, f"Session төлөв буруу: {session.status}")
    payment = _create_payment(db, session, "CASH", "CASH", cashier=user, allow_qpay=True)
    _assert_expected_amount(payment, body)
    await _retire_qpay(db, session)
    if body.get("customer_tin"):
        payment.customer_tin = str(body["customer_tin"]).strip()[:20]
        payment.ebarimt_receiver_type = "COMPANY"
    _mark_pos_app(payment, request, user=user, db=db, site_id=session.site_id)
    # Гадна системд (POS/өөр e-Barimt API) үүссэн баримт ирвэл түүнийг бүртгэнэ
    await _finalize_paid(db, payment, external_receipt=_external_receipt(body))
    # source/app_version — POS аппын бэлэн төлбөр толгойгоо үнэхээр илгээж байгааг
    # deploy-н дараа audit_logs-оос шалгах нотолгоо (терминал бүртгэлгүй үед ч)
    db.add(AuditLog(username=user.username, action="CASH_PAYMENT", entity="payment",
                    entity_id=payment.id, detail={"amount": float(payment.amount),
                                                  "source": payment.source,
                                                  "app_version": _pos_app_version(request),
                                                  "pos_app_via": (payment.raw_payload or {}).get("pos_app_via"),
                                                  # толгойгүй үед клиентийг таних (POS аппын UA-г
                                                  # прод дээр батлах — fallback хөтчийг хасдаг)
                                                  **({} if _pos_app_version(request) or request is None
                                                     else {"user_agent": (request.headers.get("User-Agent")
                                                                          or "")[:80]})}))
    db.commit()
    return {"ok": True, "status": payment.status, "payment_id": payment.id,
            "amount": float(payment.amount), **payment_outcome(db, payment)}


# ─────────────────────────── Касс (дансаар / шилжүүлэг) ───────────────────────────
@router.post("/transfer")
async def transfer_payment(body: dict, db: Session = Depends(get_db),
                           user: User = Depends(require("cashier"))):
    """Дансаар (банкны шилжүүлгээр) төлбөр авах — online operator-ын урсгал.

    Оператор жолоочид зогсоолын дансыг хэлж, шилжүүлэг ОРЖ ИРСНИЙГ хуулгаас
    хараад энэ endpoint-оор баталгаажуулна → хаалт нээгдэж, e-Barimt үүснэ
    (eBarimt 3.0-д шилжүүлгийн тусдаа код байхгүй тул CASH кодоор бүртгэгдэнэ).
    body: {session_id, customer_tin?} — ТТД өгвөл байгууллагын B2B баримт."""
    from ..auth import has_permission
    if not has_permission(user, "pay_transfer"):
        raise HTTPException(403, "Танд «Дансаар» төлбөр авах эрх байхгүй — "
                                 "админ эрхийн матрицаас pay_transfer олгоно")
    session = db.get(ParkingSession, body.get("session_id", ""))
    if not session:
        raise HTTPException(404, "Session олдсонгүй")
    enforce_site(user, session.site_id)  # оператор зөвхөн өөрийн зогсоолын төлбөр
    if session.status not in ("OPEN", "AWAITING_PAYMENT"):
        raise HTTPException(400, f"Session төлөв буруу: {session.status}")
    payment = _create_payment(db, session, "TRANSFER", "TRANSFER", cashier=user, allow_qpay=True)
    _assert_expected_amount(payment, body)
    await _retire_qpay(db, session)
    if body.get("customer_tin"):
        payment.customer_tin = str(body["customer_tin"]).strip()[:20]
        payment.ebarimt_receiver_type = "COMPANY"
    else:
        payment.ebarimt_receiver_type = "CITIZEN"
    await _finalize_paid(db, payment, external_receipt=_external_receipt(body))
    site = session.site
    db.add(AuditLog(username=user.username, action="TRANSFER_PAYMENT", entity="payment",
                    entity_id=payment.id,
                    detail={"amount": float(payment.amount),
                            # аль данс руу орсны бүртгэл (сайтын тухайн үеийн данс)
                            "bank": getattr(site, "bank_name", None),
                            "account": getattr(site, "bank_account", None)}))
    db.commit()
    return {"ok": True, "status": payment.status, "payment_id": payment.id,
            "amount": float(payment.amount), **payment_outcome(db, payment)}


# ─────────────────────────── PAX A9000 POS ───────────────────────────

def _find_terminal(db: Session, terminal_id: str):
    """POS терминалыг бүртгэлээс олно (Device: device_type=pax_terminal, device_key=terminal_id).
    Терминал бүр аль зогсоолд харьяалагдахаа device_key-ээрээ өөрөө танина."""
    from ..models import Device
    if not terminal_id:
        return None
    return (db.query(Device)
            .filter(Device.device_key == terminal_id,
                    Device.device_type.in_(["pax_terminal", "pos"]),
                    Device.status == "active").first())



@router.get("/ebarimt/payer")
async def ebarimt_payer(reg_no: str, user: User = Depends(require("cashier"))):
    """Регистрээр татвар төлөгчийг шалгана — POS дээр НЭР харуулахад.

    Байгууллагын регистр/ТТД → байгууллагын нэр. Иргэний регистр → хүний нэр ба
    НӨАТ төлөгч эсэх. Оператор дугаараа зөв бичсэн эсэхээ дэлгэц дээр шалгана.

    `available: false` бол шалгах суваг ажиллахгүй байна — «олдсонгүй» ГЭЖ БИШ.
    Тэр үед POS «шалгах боломжгүй» гэж харуулаад төлбөрийг үргэлжлүүлнэ.
    """
    from ..services import tin_lookup
    return await tin_lookup.lookup(reg_no)


@router.get("/pos/receipts")
def pos_recent_receipts(terminal_id: str | None = None, limit: int = 10,
                        db: Session = Depends(get_db),
                        user: User = Depends(require("cashier"))):
    """Сүүлийн төлбөрүүд — ДАХИН ХЭВЛЭХЭД. Цаас дуусах/зам гацах нь өдөр тутмын
    явдал бөгөөд өмнө нь баримт бүрмөсөн алдагддаг байв.

    Мөр бүр `print_data` + `receipt`-тэй ирнэ — POS шууд хэвлэнэ."""
    q = (db.query(Payment).filter(Payment.status == "PAID")
         .order_by(Payment.paid_at.desc().nullslast()))
    if terminal_id:
        q = q.filter(Payment.terminal_id == terminal_id)
    allowed = operator_sites(user)   # оператор зөвхөн өөрийн зогсоолын баримт
    rows = []
    for p in q.limit(min(max(limit, 1), 50) * 3).all():
        s = db.get(ParkingSession, p.session_id) if p.session_id else None
        if allowed and (s is None or s.site_id not in allowed):
            continue
        rows.append({"payment_id": p.id, "paid_at": p.paid_at.isoformat() if p.paid_at else None,
                     "plate_number": s.plate_number if s else None,
                     **_print_payload(db, p)})
        if len(rows) >= limit:
            break
    return rows


@router.get("/{payment_id}/print")
def payment_print(payment_id: str, db: Session = Depends(get_db),
                  user: User = Depends(require("cashier"))):
    """Нэг төлбөрийн хэвлэх өгөгдөл — дахин хэвлэх/баримт үзүүлэхэд."""
    p = db.get(Payment, payment_id)
    if not p:
        raise HTTPException(404, "Төлбөр олдсонгүй")
    s = db.get(ParkingSession, p.session_id) if p.session_id else None
    if s is not None:
        enforce_site(user, s.site_id)
    return {"payment_id": p.id, "status": p.status, **_print_payload(db, p)}


@router.get("/pos/terminal/{terminal_id}")
def pos_terminal_info(terminal_id: str, db: Session = Depends(get_db),
                      user: User = Depends(require("cashier")),
                      device_id: str | None = None, lane_no: int | None = None):
    """POS апп асахдаа өөрийн terminal_id-ээр дуудаж АЛЬ ЗОГСООЛД харьяалагдахаа
    танина + тухайн зогсоолын төлбөр хүлээж буй машинуудыг авна.
    Терминалыг Тохиргоо→Төхөөрөмжид device_type=pax_terminal, device_key=terminal_id-ээр бүртгэнэ."""
    terminal = _find_terminal(db, terminal_id)
    if not terminal:
        raise HTTPException(404, "Терминал бүртгэлгүй — Тохиргоо→Төхөөрөмжид "
                                 "pax_terminal төрлөөр device_key-тэй бүртгэнэ үү")
    enforce_site(user, terminal.site_id)
    from .sessions_router import recent_exits
    awaiting = recent_exits(terminal.site_id, device_id=device_id, lane_no=lane_no, db=db, user=user)
    terminal.last_seen = datetime.utcnow()
    db.commit()
    return {
        "terminal_id": terminal_id, "site_id": terminal.site_id,
        "site_code": terminal.site.site_code if terminal.site else None,
        "site_name": terminal.site.name if terminal.site else None,
        "awaiting": [{**s, "session_id": s['id']} for s in awaiting],
        "queue_scope": 'selected_lane' if device_id or lane_no is not None else 'site_legacy',
    }


def pos_lanes(barriers: list[dict], cameras: list[dict]) -> list[dict]:
    """Хаалт + камерыг ЭГНЭЭГЭЭР (lane_dir, lane_no) хослуулна — POS апп «аль
    хаалтыг удирдах вэ» гэж сонгоход нэг мөр = нэг эгнээ. Камергүй хаалт,
    хаалтгүй камер ч мөр болно (тохиргоо дутууг апп дээр харагдуулна)."""
    lanes: dict[tuple[str, int], dict] = {}

    def _lane(d: dict) -> dict:
        key = (d.get("lane_dir") or "entry", int(d.get("lane_no") or 1), bool(d.get('nested_inner')))
        if key not in lanes:
            lanes[key] = {"lane_dir": key[0], "lane_no": key[1],
                          "nested_inner": key[2],
                          "barrier_id": None, "barrier_name": None, "can_open": False,
                          "camera_id": None, "camera_name": None}
        return lanes[key]

    for b in barriers:
        ln = _lane(b)
        ln.update(barrier_id=b["id"], barrier_name=b.get("name"), can_open=bool(b.get("can_open")))
    for c in cameras:
        ln = _lane(c)
        ln.update(camera_id=c["id"], camera_name=c.get("name"))
    order = {"entry": 0, "exit": 1, "both": 2}
    return sorted(lanes.values(), key=lambda x: (order.get(x["lane_dir"], 9), x["lane_no"]))


@router.get("/pos/bootstrap")
def pos_bootstrap(terminal_id: str | None = None, db: Session = Depends(get_db),
                  user: User = Depends(require("cashier"))):
    """POS апп нэвтэрсний дараа НЭГ дуудлагаар: эрх + хандах зогсоолууд + зогсоол
    бүрийн эгнээ (хаалт/камер хос). Апп зогсоол → эгнээ (хаалт) сонгуулж хадгална.

    2026-09-07: зарим зогсоол 2 орох + 2 гарах хаалттай болсон тул «орох/гарах»
    гэсэн хоёр сонголт хүрэлцэхээ больсон. Өмнө нь зогсоол тохиргооны файлд
    хатуу бичигддэг, хаалтын жагсаалт тусдаа endpoint-оос (`/api/barriers/devices`)
    ирдэг байв — одоо энд бүгд нэг дор. Нууц талбар (device_key, IP, нууц үг)
    ОГТ БУЦААХГҮЙ — `lean_barrier_rows`-той ижил зарчим.

    Терминал бүртгэлтэй бол (`terminal_id`) харьяалагдах зогсоолыг `terminal`-д
    өгнө — апп тэр зогсоолыг анхдагчаар сонгоно; хэрэглэгч олон зогсоолтой бол
    сонгуулна. `open_reasons` — төлбөргүй гаргах шалтгааны жагсаалт (2026-09-07)."""
    from ..auth import effective_permissions
    from ..models import Device, ParkingSite
    from ..services.app_settings import get_open_reasons
    from .sessions_router import SPECIAL_EXIT_KINDS
    from .barriers_router import lean_barrier_rows
    allowed = operator_sites(user)
    sq = db.query(ParkingSite)
    if allowed:
        sq = sq.filter(ParkingSite.id.in_(allowed))
    sites = sq.order_by(ParkingSite.created_at).all()
    site_ids = [s.id for s in sites]
    barriers_by_site: dict[str, list[dict]] = {}
    for b in lean_barrier_rows(db, user):
        barriers_by_site.setdefault(b["site_id"], []).append(b)
    cams_by_site: dict[str, list[dict]] = {}
    if site_ids:
        cq = (db.query(Device)
              .filter(Device.device_type == "camera", Device.status != "deleted",
                      Device.site_id.in_(site_ids))
              .order_by(Device.lane_dir, Device.lane_no, Device.created_at))
        for d in cq.all():
            cams_by_site.setdefault(d.site_id, []).append({
                "id": d.id, "site_id": d.site_id, "name": d.name,
                "lane_no": d.lane_no, "lane_dir": d.lane_dir,
                "nested_inner": d.nested_inner, "status": d.status,
                "last_seen": d.last_seen.isoformat() if d.last_seen else None})
    terminal = _find_terminal(db, terminal_id or "")
    if terminal:
        terminal.last_seen = datetime.utcnow()
        db.commit()
    out_sites = []
    for s in sites:
        bars = barriers_by_site.get(s.id, [])
        cams = cams_by_site.get(s.id, [])
        out_sites.append({
            "id": s.id, "name": s.name, "site_code": s.site_code, "address": s.address,
            "lanes": pos_lanes(bars, cams),
            "barriers": bars, "cameras": cams,
        })
    return {
        "user": {"username": user.username, "full_name": user.full_name, "role": user.role},
        "permissions": sorted(effective_permissions(user)),
        "terminal": ({"terminal_id": terminal_id, "site_id": terminal.site_id,
                      "site_name": terminal.site.name if terminal.site else None}
                     if terminal else None),
        "sites": out_sites,
        # Төлбөргүй гаргах / хаалт нээх ШАЛТГААНЫ жагсаалт (Тохиргоо → Нээх шалтгаан).
        # POS «Төлбөргүй гаргах» дээр эндээс сонгуулж manual-exit-д reason_code
        # илгээнэ; «other» бол reason (тайлбар) заавал. Тусдаа дуудлага:
        # GET /api/admin/open-reasons?active_only=true (cashier эрхээр ажиллана).
        "open_reasons": get_open_reasons(db, active_only=True),
        # Онцгой гаргалт (2026-09-14, free_exit эрхгүй POS/операторт): эхлээд
        # POST /api/sessions/{id}/special-exit/snapshot (гарах камерын зураг),
        # дараа нь POST /api/sessions/{id}/special-exit {kind} — kind жагсаалт:
        "special_exit_kinds": [{"kind": k, "label": v["label"],
                                "needs_snapshot": v["needs_snapshot"]}
                               for k, v in SPECIAL_EXIT_KINDS.items()],
    }


@router.post("/pos/prepare")
async def pos_prepare(body: dict, db: Session = Depends(get_db),
                      user: User = Depends(require("cashier"))):
    """Reserve checkout BEFORE asking the bank terminal to collect money."""
    from ..services.checkout import lock_session
    session = lock_session(db, body.get("session_id", ""))
    enforce_site(user, session.site_id)
    terminal = _find_terminal(db, body.get("terminal_id", ""))
    if terminal is None or terminal.site_id != session.site_id:
        raise HTTPException(403, "Энэ зогсоолын бүртгэлтэй терминал шаардлагатай")
    from ..services.exit_presence import validate_pos_context, record_pos_context
    context = validate_pos_context(db, session, body)
    payment = _create_payment(db, session, "POS", "CARD", cashier=user, allow_qpay=True)
    _assert_expected_amount(payment, body)
    await _retire_qpay(db, session)
    payment.terminal_id = body["terminal_id"]
    record_pos_context(db, payment, session, payment.terminal_id, user.username, 'prepare', context)
    db.commit()
    return {"payment_id": payment.id, "amount": float(payment.amount), "status": payment.status,
            "exit_context": context['status'], "exit_lane_context": context}


@router.post("/pos/{payment_id}/cancel")
def pos_cancel(payment_id: str, body: dict, db: Session = Depends(get_db),
               user: User = Depends(require("cashier"))):
    """Release a reservation only after the terminal confirms it did not charge."""
    payment = db.get(Payment, payment_id)
    if not payment or payment.provider != "POS" or not payment.session_id:
        raise HTTPException(404, "POS оролдлого олдсонгүй")
    enforce_site(user, payment.session.site_id)
    if body.get("outcome") != "NOT_CHARGED" or body.get("terminal_id") != payment.terminal_id:
        raise HTTPException(422, "Терминалын NOT_CHARGED баталгаа шаардлагатай")
    payment = _lock_payment(db, payment.id)
    if payment is None or payment.status == "PAID":
        raise HTTPException(409, "Төлөгдсөн эсвэл боловсруулагдаж байгаа төлбөр")
    payment.status = "CANCELLED"
    db.add(AuditLog(username=user.username, action="POS_INTENT_CANCELLED", entity="payment",
                    entity_id=payment.id, detail={"outcome": "NOT_CHARGED"}))
    db.commit()
    return {"payment_id": payment.id, "status": payment.status}


@router.post("/pos/confirm")
async def pos_confirm(body: dict, request: Request, db: Session = Depends(get_db),
                      user: User = Depends(require("cashier"))):
    """PAX A9000 апп картын төлбөр авсныг баталгаажуулна.
    body: {session_id, amount, auth_code, card_last4, card_brand, terminal_id, transaction_id}"""
    session = db.get(ParkingSession, body.get("session_id", ""))
    if not session:
        raise HTTPException(404, "Session олдсонгүй")
    enforce_site(user, session.site_id)  # оператор зөвхөн өөрийн зогсоолын төлбөр
    # POS апп-ийн offline queue 30 сек тутам retry хийдэг — эхний хүсэлт удааширч
    # давхар ирвэл ижил transaction_id-тай ТӨЛӨГДСӨН payment-ийг idempotent буцаана
    # (картаас мөнгө нэг л удаа гарсан тул алдаа биш, амжилт гэж хариулна)
    from sqlalchemy import or_
    from ..services.payment_validation import (money, transaction_reference,
                                               reference_key, claim_reference, gate_result)
    paid_amount = money(body.get("amount"))
    txn_id = transaction_reference(body.get("transaction_id"))
    terminal = _find_terminal(db, body.get("terminal_id", ""))
    if terminal and terminal.site_id != session.site_id:
        raise HTTPException(403, "Терминал өөр зогсоолд харьяалагдана")
    account = f"terminal:{terminal.id}" if terminal else "legacy-global"
    tx_key = reference_key("POS", account, txn_id)
    if txn_id:
        dup = (db.query(Payment)
               .filter(Payment.provider == "POS", or_(Payment.provider_tx_key == tx_key,
                       (Payment.provider_tx_key.is_(None) & (Payment.provider_payment_id == txn_id))),
                       Payment.status == "PAID")
               .first())
        if dup:
            if dup.session_id != session.id or money(dup.amount) != paid_amount:
                raise HTTPException(409, "Гүйлгээний дугаар өөр зогсолт эсвэл өөр дүнгээр бүртгэгдсэн")
            return {"status": "PAID", "payment_id": dup.id, **gate_result(db, dup),
                    **_print_payload(db, dup), **settlement_info(db, dup)}
    # Session мөрийг түгжинэ — нэг session дээр хоёр confirm ЗЭРЭГ орж ирвэл
    # (retry + удаан анхны хүсэлт) давхар Payment/баримт үүсэхээс сэргийлнэ
    from sqlalchemy.exc import OperationalError
    try:
        session = (db.query(ParkingSession).filter(ParkingSession.id == session.id)
                   .enable_eagerloads(False)  # OUTER JOIN + FOR UPDATE зөрчлөөс сэргийлнэ
                   .populate_existing().with_for_update(nowait=True).first())
    except OperationalError:
        db.rollback()
        raise HTTPException(409, "Энэ session-ий төлбөр боловсруулагдаж байна — "
                                 "түр хүлээгээд дахин оролдоно уу")
    # Терминал бүртгэлтэй бол зогсоолын харьяаллыг тулгана — А зогсоолын POS
    # Б зогсоолын машинд төлбөр авахаас (буруу тооцоо) сэргийлнэ
    terminal = _find_terminal(db, body.get("terminal_id", ""))
    # Аппын хувилбарыг бүртгэнэ — «энэ алдаа хуучин build дээр л гарч байна уу»
    # гэдгийг тогтоох цорын ганц арга (өмнө нь ямар ч ул мөр үлддэггүй байв).
    _remember_app_version(terminal, request.headers.get("X-App-Version"))
    if terminal and terminal.site_id != session.site_id:
        raise HTTPException(400, f"Энэ терминал «{terminal.site.name if terminal.site else '?'}» "
                                 "зогсоолд харьяалагддаг — өөр зогсоолын машинд төлбөр авах боломжгүй")

    if body.get("payment_id"):
        payment = _lock_payment(db, body["payment_id"])
        if (payment is None or payment.provider != "POS" or payment.session_id != session.id
                or payment.terminal_id != body.get("terminal_id")
                or money(payment.amount) != paid_amount):
            raise HTTPException(409, "POS оролдлого, терминал эсвэл дүн зөрлөө")
    else:
        # Older terminal apps report only AFTER collection. Preserve the bank fact;
        # migrate these clients to prepare -> bank -> confirm(payment_id).
        payment = _create_payment(db, session, "POS", "CARD", cashier=user,
                                  confirmed_amount=paid_amount)
    payment.card_last4 = body.get("card_last4")
    payment.card_brand = body.get("card_brand")
    payment.terminal_id = body.get("terminal_id")
    _mark_pos_app(payment, request, always=True)
    claim_reference(db, payment, account, txn_id)
    # The bank has already collected money. Preserve that fact even if the car
    # moved or the read expired; infer again from server evidence, not body fields.
    from ..services.exit_presence import record_pos_context, infer_pos_context, pos_lane
    exit_context = infer_pos_context(db, session)
    # Кассчин биечлэн байгаа эгнээ (хүсэлтийн эгнээ эсвэл ганц гарах эгнээ) —
    # шинэ гарах уншилт байхгүй үед mark_paid_and_open үүнийг нээнэ.
    lane = pos_lane(db, session, body) if exit_context['camera_id'] is None else None
    if lane:
        lane = {**lane, 'terminal_id': payment.terminal_id, 'cashier': user.username,
                'payment_id': payment.id}
        exit_context = {**exit_context, 'pos_lane_source': lane['source'],
                        'pos_lane_no': lane['camera'].lane_no, 'pos_gate_id': lane['gate_id']}
    record_pos_context(db, payment, session, payment.terminal_id, user.username, 'confirm',
                       exit_context)
    # Худалдан авагчийн дугаар: `payer_reg_no` (шинэ — 3 форматыг таньдаг) эсвэл
    # хуучин `customer_tin`. Иргэний регистр (АА00112233) өгвөл баримт ИРГЭНИЙ
    # нэр дээр гарч сугалаанд оролцоно; ААН регистр (7 орон)/ТТД (11–14) өгвөл
    # байгууллагын баримт. Танигдахгүй утга нэргүй баримт болно (алдаа өгөхгүй —
    # POS дээр жолооч буруу бичихэд төлбөр таслагдах ёсгүй).
    reg, rtype = msgbill.classify_reg_no(body.get("payer_reg_no") or body.get("customer_tin"))
    payment.customer_tin = reg
    payment.ebarimt_receiver_type = "COMPANY" if rtype == "ORGANIZATION" else "CITIZEN"
    import time as _t
    _c0 = _t.monotonic()
    # Терминал өөрөө e-Barimt гаргасан бол (банкны PosLink-ийн Ибаримт үйлчилгээ)
    # апп ДДТД-г дамжуулна → систем давхар баримт үүсгэхгүй, терминалынхыг бүртгэнэ
    try:
        await _retire_qpay(db, session)
    except HTTPException:
        # Card was already charged. Keep that fact even when old QR cancellation is uncertain.
        db.add(AuditLog(username=user.username, action="PAYMENT_PROVIDER_CONFLICT", entity="payment",
                        entity_id=payment.id, detail={"reason": "QPay cancellation unconfirmed"}))
    await _finalize_paid(db, payment, raw=body, external_receipt=_external_receipt(body),
                         pos_lane=lane)
    db.commit()
    out = {"status": "PAID", "payment_id": payment.id, **gate_result(db, payment),
           **_print_payload(db, payment), **settlement_info(db, payment),
           "exit_lane_context": exit_context}
    _ms = int((_t.monotonic() - _c0) * 1000)
    (log.warning if _ms >= settings.payment_slow_warn_ms else log.info)(
        "POS confirm: %dмс (session=%s)", _ms, session.plate_number)
    return out


# ─────────────────────────── Жагсаалт ───────────────────────────
@router.get("")
def list_payments(
    site_id: str | None = None, status: str | None = None, provider: str | None = None,
    date_from: str | None = None, date_to: str | None = None, limit: int = 100, offset: int = 0,
    db: Session = Depends(get_db), user: User = Depends(require("payments", "reports", "cashier")),
):
    from datetime import timedelta
    from sqlalchemy import func
    site_column = func.coalesce(Payment.site_id, ParkingSession.site_id)
    q = db.query(Payment).outerjoin(ParkingSession, Payment.session_id == ParkingSession.id).filter(Payment.kind.in_(("PARKING", "DEBT")))
    site_id, site_ids = scoped_site(user, site_id)  # tenant хэрэглэгч зөвхөн өөрийн зогсоолууд
    if site_id:
        q = q.filter(site_column == site_id)
    elif site_ids:
        q = q.filter(site_column.in_(site_ids))
    if status:
        q = q.filter(Payment.status == status)
    if provider:
        q = q.filter(Payment.provider == provider)
    try:
        if date_from:
            q = q.filter(Payment.created_at >= datetime.fromisoformat(date_from))
        if date_to:
            q = q.filter(Payment.created_at < datetime.fromisoformat(date_to) + timedelta(days=1))
    except ValueError:
        raise HTTPException(400, "Огнооны формат буруу (YYYY-MM-DD)")
    total = q.count()
    rows = q.order_by(Payment.created_at.desc()).offset(offset).limit(min(limit, 500)).all()
    return {"total": total, "rows": [
        to_dict(p, extra={"plate_number": p.session.plate_number if p.session else (p.raw_payload or {}).get("plate_number"),
                          "site_name": _site_of(p).name if _site_of(p) else None})
        for p in rows]}


async def _qpay_receiver(payment: Payment, receiver_type: str) -> str | None:
    """QPay ebarimt_v3-д дамжуулах худалдан авагч (COMPANY үед л).

    2026-09-06-нд прод дээр батлагдсан: QPay `ebarimt_receiver` нь **ААН-ы 7 оронтой
    РЕГИСТР** — 7-оос урт бол `{"ebarimt_receiver": {"type": "MAX_LENGTH", "message":
    "String max length (7)!"}}` гэж татгалздаг; ТТД-г QPay өөрөө ТЕГ-ээс олдог. Тиймээс
    регистрийг ХЭВЭЭР нь дамжуулна (ТТД болгож хөрвүүлэхгүй — өмнөх хувилбар ингэж
    хөрвүүлээд бүх байгууллагын баримтыг унагаж байв). Регистр ТЕГ-т байхгүй бол QPay
    «receipt.customerTin … [0-9]{11,14}» гэж унана → Ибаримт дээр «Дугаар засах» /
    «Иргэнээр». 11–14 оронтой ТТД хадгалагдсан бол QPay MAX_LENGTH гэж тодорхой унана."""
    if receiver_type != "COMPANY":
        return None
    reg = re.sub(r"[\s-]", "", (payment.customer_tin or "")).strip()
    return reg or None


async def retry_ebarimt(db: Session, payment: Payment, *, reissue_external: bool = False,
                        allow_duplicate: bool = False, no_terminal_receipt: bool = False,
                        username: str = "system") -> dict:
    """Resume a saved receipt job; never issue an untracked second receipt.
    EXTERNAL (POS терминал гаргасан) баримтыг зөвхөн reissue_external=True үед,
    дүрэм үйлчлэхгүй эсвэл терминал хэвлээгүйг баталгаажуулсан (no_terminal_receipt)
    бол л системийн баримт болгоно (financial_jobs).
    allow_duplicate: POS терминалын давхардал гэж цуцлагдсан баримтыг ДАХИН
    үүсгэхийг ил тодоор зөвшөөрөх (UI-д баталгаажуулалттай) — эс бөгөөс 409."""
    if payment.status != "PAID":
        return {"ok": False, "error": "Төлбөр PAID биш"}
    from ..services.financial_jobs import retry_payment_receipts
    return retry_payment_receipts(db, payment, reissue_external=reissue_external,
                                  allow_duplicate=allow_duplicate,
                                  no_terminal_receipt=no_terminal_receipt, username=username)


def _mark_cancel(rec: VatReceipt, state: str, key_scope: str | None, note: str,
                 mark: dict | None) -> None:
    """Цуцлалтын нотолгоо: аль түлхүүрээр, ямар шалтгаанаар (raw.cancel) — POS-ийн
    давхардал бол ddtd_note-д DUPLICATE_OF_POS тэмдэг (Ибаримт хуудсанд харагдана,
    «Дахин үүсгэх» 409 өгөх үндэс)."""
    cur = getattr(rec, "raw", None)
    raw = dict(cur) if isinstance(cur, dict) else {}
    raw["cancel"] = {**(raw.get("cancel") if isinstance(raw.get("cancel"), dict) else {}),
                     "state": state, "key_scope": key_scope, "note": note,
                     "at": datetime.utcnow().isoformat(), **(mark or {})}
    rec.raw = raw
    if mark and mark.get("reason") and mark["reason"] not in (rec.ddtd_note or ""):
        tail = f" ТЕГ {mark['teg_ddtd']}" if mark.get("teg_ddtd") else ""
        prev = f" | {rec.ddtd_note}" if rec.ddtd_note else ""
        rec.ddtd_note = (f"{mark['reason']}: POS терминалын баримттай давхардсан тул цуцлав{tail}{prev}")[:300]


async def cancel_ebarimt(db: Session, payment: Payment, note: str, mark: dict | None = None,
                         only_ids: set | None = None) -> dict:
    """ИЛГЭЭГДСЭН (SENT) e-Barimt баримтыг сувгаар нь цуцална (буцаалт).

    Суваг бүрийн цуцлах зам:
      • QPAY    — `DELETE /v2/ebarimt_v3/{payment_id}` (qpay.cancel_ebarimt), note заавал
      • POSAPI  — локал PosAPI `DELETE /rest/receipt {id: billId, date}` (ebarimt.delete_receipt)
      • MSGBILL — `DELETE /partner/receipts/{id}` (msgbill.cancel_receipt) — msgbill
                  талд одоогоор БАЙХГҮЙ → NOT_SUPPORTED алдаа буцна
    Амжилттай бол VatReceipt.status=CANCELLED, receipt_url=тэмдэглэл. Төлбөр
    (мөнгө) хөндөгдөхгүй — зөвхөн татварын баримт. Дараа нь «Дахин үүсгэх»
    шинэ баримт гаргана (retry_ebarimt CANCELLED-ийг алгасдаг).

    msgbill: баримтыг гаргасан данс л цуцлана — одоогийн түлхүүр «олдсонгүй/401»
    гэвэл нөгөө түлхүүрээр (түрээслэгч ↔ глобал) нэг удаа оролдоно; аль түлхүүрээр
    цуцалсныг raw.cancel.key_scope + хариуны key_scopes-д бичнэ.
    mark: цуцлалтын шалтгааны тэмдэг (ж: {"reason": DUPLICATE_OF_POS, "teg_ddtd": …}) —
    raw.cancel + ddtd_note-д; POS-ийн давхардал гэж цуцалсныг «Дахин үүсгэх» 409-өөр хамгаална.
    only_ids: зөвхөн эдгээр мөр (давхардлын цуцлалт терминалын ONTIME мөрийг хөндөхгүй)."""
    recs = (db.query(VatReceipt)
            .filter(VatReceipt.payment_id == payment.id,
                    VatReceipt.status.in_(["SENT", "CANCEL_PENDING"]),
                    VatReceipt.ebarimt_id.isnot(None)).all())
    if only_ids is not None:
        recs = [r for r in recs if r.id in only_ids]
    if not recs:
        if (db.query(VatReceipt.id).filter(VatReceipt.payment_id == payment.id,
                                           VatReceipt.status == "EXTERNAL").first()):
            # Терминалын (Ontime) баримтыг бид гаргаагүй — ТЕГ-т буцаалтыг тэр системд хийнэ
            return {"ok": False, "external": True,
                    "error": "Энэ төлбөрийн НӨАТ баримтыг POS терминал (Ontime) гаргасан — "
                             "системээс цуцлах боломжгүй; буцаалтыг терминалын системд хийнэ үү"}
        return {"ok": False, "error": "Цуцлах ИЛГЭЭГДСЭН баримт олдсонгүй"}
    site = _site_of(payment)
    cancelled, errors, pending, key_scopes = [], [], [], {}
    for rec in recs:
        if rec.status == "CANCEL_PENDING" and rec.provider == "MSGBILL" and rec.provider_ref:
            # Хүлээгдэж буй цуцлалтын төлөвийг GET-ээр шалгана (дахин cancel илгээхгүй)
            try:
                prev = (rec.raw or {}).get("cancel") if isinstance(rec.raw, dict) else None
                g = await msgbill.get_receipt_any(
                    site, rec.provider_ref,
                    prefer_scope=prev.get("key_scope") if isinstance(prev, dict) else None)
                key_scopes[rec.ebarimt_id] = g.get("key_scope")
                if g.get("state") == "CANCELLED":
                    rec.status = "CANCELLED"
                    rec.receipt_url = (note or "Цуцлав")[:200]
                    _mark_cancel(rec, "CANCELLED", g.get("key_scope"), note, mark)
                    cancelled.append(rec.ebarimt_id)
                else:
                    pending.append(rec.ebarimt_id)
                    rec.receipt_url = (f"Цуцлалт хүлээгдэж байна (msgbill {g.get('state')}): {g.get('error') or ''}")[:200]
            except Exception as e:  # noqa: BLE001
                errors.append(f"MSGBILL: {_ebarimt_err(e)}")
            continue
        prov = rec.provider or ("QPAY" if payment.provider == "QPAY" and payment.provider_payment_id
                                else "POSAPI")
        try:
            if prov == "QPAY":
                ok = await qpay.cancel_ebarimt(payment.provider_payment_id, note or "Гүйлгээ буцаав",
                                               acc=qpay.account_for(site))
                if not ok:
                    raise RuntimeError("QPay баримт цуцлахыг зөвшөөрсөнгүй")
            elif prov == "MSGBILL":
                if not rec.provider_ref:
                    raise RuntimeError("msgbill баримтын ID (provider_ref) алга")
                if not msgbill.cancel_accounts(site):
                    raise RuntimeError("msgbill түлхүүр тохируулаагүй")
                r = await msgbill.cancel_receipt_any(site, rec.provider_ref, note)
                key_scopes[rec.ebarimt_id] = r.get("key_scope")
                if r.get("state") == "CANCEL_PENDING":
                    # ТЕГ түр амжилтгүй — msgbill 10 мин тутам дахин оролдоно;
                    # receipt.cancelled webhook эсвэл дараагийн «Цуцлах» дарахад GET-ээр эцэслэнэ
                    rec.status = "CANCEL_PENDING"
                    rec.receipt_url = (f"Цуцлалт хүлээгдэж байна (msgbill): {r.get('error') or ''}")[:200]
                    _mark_cancel(rec, "CANCEL_PENDING", r.get("key_scope"), note, mark)
                    pending.append(rec.ebarimt_id)
                    continue
                if r.get("state") not in ("CANCELLED", ""):
                    raise RuntimeError(f"msgbill төлөв {r.get('state')}: {r.get('error') or ''}")
            elif prov == "POSAPI":
                date = (rec.created_at or datetime.utcnow()).strftime("%Y-%m-%d %H:%M:%S")
                if not await ebarimt.delete_receipt(rec.ebarimt_id, date):
                    raise RuntimeError("PosAPI баримт буцаахыг зөвшөөрсөнгүй")
            else:
                # ГАДНА системд (терминал/өөр API) үүссэн баримт — бид ТЕГ-т буцааж чадахгүй,
                # зөвхөн өөрийн бүртгэлээ цуцална; жинхэнэ буцаалтыг тэр системд хийнэ
                note = (note or "Цуцлав") + f" (гадна систем {prov}-д буцаалтыг тусад нь хийнэ)"
        except Exception as e:  # noqa: BLE001
            errors.append(f"{prov}: {_ebarimt_err(e)}")
            continue
        rec.status = "CANCELLED"
        rec.receipt_url = (note or "Цуцлав")[:200]
        # msgbill бол аль түлхүүрээр цуцалсан (key_scopes), бусад сувагт None. Давхардлын
        # тэмдэг (mark) ЗӨВХӨН msgbill-д — POS-ийн давхардал гэж зөвхөн msgbill баримтыг
        # цуцалдаг (vat_recon.cancel_block_reason); QPay/PosAPI-д нотолгоо л үлдэнэ
        _mark_cancel(rec, "CANCELLED", key_scopes.get(rec.ebarimt_id), note,
                     mark if prov == "MSGBILL" else None)
        ebarimt.cache_qr(payment.id, None)
        cancelled.append(rec.ebarimt_id)
    db.commit()
    err = "; ".join(errors) or None
    if pending and not err:
        err = ("ТЕГ түр хариу өгсөнгүй — цуцлалт msgbill дээр хүлээгдэж байна (10 мин тутам "
               "автоматаар дахин оролдоно); дараа нь «Цуцлах» дахин дарж шалгана уу")
    return {"ok": bool(cancelled) and not errors and not pending, "cancelled": cancelled,
            "pending": pending, "error": err, "key_scopes": key_scopes}


def _apply_msgbill_event(db: Session, rec: VatReceipt, event: str, data: dict) -> str:
    """msgbill webhook-ийн нэг мөрөнд үзүүлэх нөлөө (тест хийхэд тусгаарласан).

    Буцаах: "set" (ДДТД шинээр бичигдэв) | "same" | "conflict" (ӨӨР дугаар ирсэн —
    хадгалсныг ДАРААГҮЙ, ddtd_note + EBARIMT_ID_CONFLICT) | "stale" | "cancelled".

    2026-09-07-оос өмнө `rec.ebarimt_id = data.receipt_no` гэж чимээгүй дардаг байв:
    POST-ын хариугаар аль хэдийн ХЭВЛЭГДСЭН дугаар webhook-оор өөр болж, хэвлэсэн
    баримт ≠ Ибаримт хуудас (9723УБТ: …1797 хэвлэгдсэн, …1796 хадгалагдсан)."""
    if event == "receipt.created":
        if rec.status == "CANCELLED":
            return "stale"   # аль хэдийн цуцалсан — хуучирсан мэдэгдэл
        incoming = data.get("receipt_no")
        before = rec.ebarimt_id
        ok = assign_ebarimt_id(db, rec, incoming, source="webhook.receipt.created",
                               lottery=data.get("lottery"), username="msgbill-webhook", raw=data)
        if rec.ebarimt_id:
            rec.status, rec.receipt_url, rec.provider = "SENT", None, "MSGBILL"
        if not ok and incoming and before and incoming != before:
            return "conflict"
        return "set" if ok and before != incoming else "same"
    # receipt.cancelled
    incoming = data.get("receipt_no")
    if incoming and rec.ebarimt_id and incoming != rec.ebarimt_id:
        # Цуцлагдсан нь ХАДГАЛСАН баримт биш — msgbill талд энэ төлбөрт ӨӨР баримт байна
        rec.ddtd_note = (f"ДДТД зөрүү (webhook.receipt.cancelled): цуцлагдсан {incoming} ≠ "
                         f"хадгалсан {rec.ebarimt_id}")[:300]
        db.add(AuditLog(username="msgbill-webhook", action="EBARIMT_ID_CONFLICT", entity="vat_receipt",
                        entity_id=rec.id, detail={"payment_id": rec.payment_id, "stored": rec.ebarimt_id,
                                                  "incoming": incoming, "source": "webhook.receipt.cancelled",
                                                  "provider_ref": rec.provider_ref}))
    _cur = getattr(rec, "raw", None)
    merged = dict(_cur) if isinstance(_cur, dict) else {}
    merged["webhook.receipt.cancelled"] = data
    rec.raw = merged
    rec.status = "CANCELLED"
    rec.receipt_url = "msgbill webhook: receipt.cancelled"
    ebarimt.cache_qr(rec.payment_id, None)
    return "cancelled"


@router.post("/msgbill/webhook")
async def msgbill_webhook(request: Request, db: Session = Depends(get_db)):
    """msgbill.mn webhook — `receipt.created` / `receipt.cancelled` / `payment.succeeded`.

    Толгой: X-Billing-Event, X-Billing-Signature = HMAC-SHA256(түүхий биет, whsec_) hex.
    Нууц: глобал (Тохиргоо → Холболт → msgbill / .env) + түрээслэгч бүрийнх — бүгдээр
    нь шалгана; таарахгүй бол 401. 10 сек дотор 2xx буцаах ёстой (msgbill амжилтгүй
    бол 1 удаа дахин илгээнэ — at-least-once тул идемпотент).
      receipt.created   → provider_ref=receipt_id баримт: ebarimt_id/lottery/SENT
      receipt.cancelled → CANCELLED
      payment.succeeded → (нэхэмжлэхийн үйлчилгээ — бидэнд хамаагүй) 200"""
    raw = await request.body()
    scope = msgbill.verify_signature(raw, request.headers.get("X-Billing-Signature"),
                                     msgbill.webhook_secrets(db))
    if scope is None:
        log.warning("msgbill webhook: гарын үсэг таарсангүй (event=%s)",
                    request.headers.get("X-Billing-Event"))
        raise HTTPException(401, "Webhook гарын үсэг буруу — whsec_ нууцыг Тохиргоо → Холболт → msgbill-д тавина уу")
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        raise HTTPException(400, "JSON биш")
    event = str(body.get("event") or request.headers.get("X-Billing-Event") or "")
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    rid = str(data.get("receipt_id") or "")
    if event in ("receipt.created", "receipt.cancelled") and rid:
        recs = (db.query(VatReceipt).filter(VatReceipt.provider_ref == rid)
                .order_by(VatReceipt.id).populate_existing().with_for_update().all())
        for rec in recs:
            _apply_msgbill_event(db, rec, event, data)
        db.add(AuditLog(username="msgbill-webhook", action="MSGBILL_WEBHOOK", entity="vat_receipt",
                        entity_id=rid, detail={"event": event, "scope": scope,
                                               "matched": len(recs), "receipt_no": data.get("receipt_no")}))
        db.commit()
        log.info("msgbill webhook %s receipt=%s → %d баримт шинэчлэв (%s)", event, rid, len(recs), scope)
        return {"ok": True, "matched": len(recs)}
    return {"ok": True, "ignored": event}


@router.post("/{payment_id}/ebarimt")
async def attach_external_ebarimt(payment_id: str, body: dict, db: Session = Depends(get_db),
                                  user: User = Depends(require("cashier", "vat", "reports"))):
    """ГАДНА системд (POS терминал, банкны PosLink, өөр e-Barimt API) үүссэн баримтыг
    ТӨЛӨГДСӨН төлбөрт ХОЛБОНО — систем өөрөө баримт үүсгээгүй/амжилтгүй болсон үед.
    body: {ebarimt_id (ДДТД, заавал), lottery_code?, qr_data?, ebarimt_provider? (≤20,
    анхдагч TERMINAL), ebarimt_ref? (гадны системийн ID)}.
    Идэвхтэй (SENT) баримт аль хэдийн байвал 409 — эхлээд «Цуцлах»."""
    payment = _lock_payment(db, payment_id)
    if not payment:
        raise HTTPException(404, "Төлбөр олдсонгүй")
    site = _site_of(payment)
    enforce_site(user, site.id if site else None)
    if payment.status != "PAID":
        raise HTTPException(400, "Төлбөр PAID биш — баримт холбох боломжгүй")
    ext = _external_receipt(body)
    if not ext:
        raise HTTPException(400, "ebarimt_id (ДДТД) заавал")
    from ..models import FinancialJob
    jobs = (db.query(FinancialJob).filter_by(payment_id=payment.id, kind="RECEIPT")
            .order_by(FinancialJob.id).populate_existing().with_for_update().all())
    if any(j.status == "SENDING" for j in jobs):
        raise HTTPException(409, "Баримтын хүсэлт боловсруулагдаж байна; дууссаны дараа тулгана уу")
    live = (db.query(VatReceipt).filter(VatReceipt.payment_id == payment.id,
                                        VatReceipt.status.in_(["SENT", "CANCEL_PENDING"])).first())
    if live and live.ebarimt_id == ext["billId"]:
        return {"ok": True, "ebarimt_id": live.ebarimt_id, "duplicate": True}
    if live:
        raise HTTPException(409, f"Энэ төлбөрт идэвхтэй баримт ({live.ebarimt_id}, {live.provider or 'POSAPI'}) "
                                 "аль хэдийн байна — давхар бүртгэхгүй. Эхлээд «Цуцлах» дарна уу")
    receiver_type = payment.ebarimt_receiver_type or ("COMPANY" if payment.customer_tin else "CITIZEN")
    # EXTERNAL = «терминал гаргасан, ДДТД нь алга» — терминалын ДДТД ирэхэд ТЭР мөрийг
    # SENT болгоно (шинэ мөр нэмбэл нэг төлбөрт хоёр баримт харагдана)
    unresolved = (db.query(VatReceipt).filter(VatReceipt.payment_id == payment.id,
        VatReceipt.status.in_(["PENDING", "FAILED", "REVIEW", "EXTERNAL"]))
        .order_by(VatReceipt.id).populate_existing().with_for_update().all())
    if len(unresolved) > 1:
        raise HTTPException(409, "Олон баримтыг тус бүрээр нь санхүү тулгах шаардлагатай")
    rec = unresolved[0] if unresolved else None
    if rec is None:
        rec = VatReceipt(payment_id=payment.id, session_id=payment.session_id,
                         amount=payment.amount, vat_amount=payment.vat_amount,
                         customer_tin=payment.customer_tin)
        db.add(rec)
    if not rec.id:
        rec.id = str(uuid.uuid4())
    rec.lottery_code = None
    assign_ebarimt_id(db, rec, ext["billId"], source=f"attach:{ext['provider']}",
                      lottery=None if receiver_type == "COMPANY" else ext["lottery"],
                      username=user.username, raw=dict(body), allow_replace=True)
    rec.status, rec.receipt_url = "SENT", None
    rec.provider, rec.provider_ref = ext["provider"], ext["msgbillId"]
    for job in jobs:
        if job.receipt_id == rec.id:
            job.status, job.last_error = "DONE", None
    ebarimt.cache_qr(payment.id, ext["qrData"])
    db.add(AuditLog(username=user.username, action="EBARIMT_ATTACH", entity="payment",
                    entity_id=payment_id, detail={"ebarimt_id": ext["billId"], "provider": ext["provider"]}))
    db.commit()
    return {"ok": True, "ebarimt_id": rec.ebarimt_id, "provider": rec.provider,
            "lottery": rec.lottery_code}


@router.get("/{payment_id}/ebarimt-history")
async def ebarimt_history(payment_id: str, live: bool = True, db: Session = Depends(get_db),
                          user: User = Depends(require("vat", "reports"))):
    """НЭГ төлбөрийн ДДТД-ийн БҮХ ул мөр — «нэг гүйлгээнд хэд өөр дугаар, хаанаас» (2026-09-07).

    Буцаах: primary (хэвлэгдэх/харагдах албан ёсны баримт), receipts (бүх мөр, raw-тай),
    numbers (харагдсан ДДТД бүр: хаанаас, хэзээ), audit (retry/цуцлалт/webhook/зөрчил
    цагийн дараалалаар), live (msgbill дээрх одоогийн төлөв — provider_ref бүрд GET)."""
    payment = db.get(Payment, payment_id)
    if not payment:
        raise HTTPException(404, "Төлбөр олдсонгүй")
    site = _site_of(payment)
    enforce_site(user, site.id if site else None)
    recs = (db.query(VatReceipt).filter(VatReceipt.payment_id == payment_id)
            .order_by(VatReceipt.created_at).all())
    prim = primary_receipt(db, payment_id)
    rec_ids = [r.id for r in recs]
    refs = [r.provider_ref for r in recs if r.provider_ref]
    from sqlalchemy import or_
    conds = [(AuditLog.entity == "payment") & (AuditLog.entity_id == payment_id)]
    if rec_ids:
        conds.append((AuditLog.entity == "vat_receipt") & (AuditLog.entity_id.in_(rec_ids)))
    if refs:
        conds.append((AuditLog.action == "MSGBILL_WEBHOOK") & (AuditLog.entity_id.in_(refs)))
    audit = (db.query(AuditLog).filter(or_(*conds))
             .order_by(AuditLog.created_at).limit(200).all())
    numbers: dict[str, list[dict]] = {}

    def _seen(no, source, at, extra=None):
        if not no:
            return
        numbers.setdefault(str(no), []).append({"source": source, "at": at, **(extra or {})})

    for r in recs:
        _seen(r.ebarimt_id, f"vat_receipts.{r.status}", r.created_at.isoformat(),
              {"receipt_id": r.id, "lottery": r.lottery_code, "provider": r.provider})
        raw = r.raw if isinstance(r.raw, dict) else {}
        for k, v in raw.items():
            if isinstance(v, dict):
                _seen(v.get("receipt_no") or v.get("id") if k.startswith("webhook") else v.get("receipt_no"),
                      f"raw.{k}", r.created_at.isoformat(), {"receipt_id": r.id, "lottery": v.get("lottery")})
    for a in audit:
        d = a.detail or {}
        for key in ("stored", "incoming", "from", "to", "receipt_no", "ebarimt_id"):
            _seen(d.get(key), f"audit.{a.action}.{key}", a.created_at.isoformat(), {"by": a.username})
    live_rows = []
    if live and refs:
        for r in recs:
            if r.provider == "MSGBILL" and r.provider_ref:
                try:
                    # Одоогийн түлхүүр «олдсонгүй» бол нөгөөгөөр (Моннисын глобал
                    # түлхүүрээр гарсан 08-20…09-06-ны баримт) — цуцлалтыг шалгахад
                    g = await msgbill.get_receipt_any(site, r.provider_ref)
                    live_rows.append({"receipt_id": r.id, "provider_ref": r.provider_ref,
                                      "key_scope": g.get("key_scope"),
                                      "state": g.get("state"), "receipt_no": g.get("billId") or (g.get("raw") or {}).get("receipt_no"),
                                      "lottery": g.get("lottery"), "error": g.get("error"), "raw": g.get("raw")})
                    _seen((g.get("raw") or {}).get("receipt_no"), "msgbill.live", datetime.utcnow().isoformat(),
                          {"receipt_id": r.id, "state": g.get("state")})
                except Exception as e:  # noqa: BLE001
                    live_rows.append({"receipt_id": r.id, "provider_ref": r.provider_ref,
                                      "error": _ebarimt_err(e)})
    return {
        "payment": {"id": payment.id, "amount": float(payment.amount), "provider": payment.provider,
                    "method": payment.payment_method, "paid_at": payment.paid_at.isoformat() if payment.paid_at else None,
                    "plate_number": payment.session.plate_number if payment.session else None,
                    "site_name": site.name if site else None, "terminal_id": payment.terminal_id},
        "primary": {"receipt_id": prim.id, "ebarimt_id": prim.ebarimt_id, "lottery": prim.lottery_code,
                    "status": prim.status, "provider": prim.provider} if prim else None,
        "receipts": [to_dict(r) for r in recs],
        "numbers": [{"ebarimt_id": k, "seen": v} for k, v in numbers.items()],
        "distinct_count": len(numbers),
        "audit": [{"at": a.created_at.isoformat(), "by": a.username, "action": a.action,
                   "entity": a.entity, "entity_id": a.entity_id, "detail": a.detail} for a in audit],
        "live": live_rows,
    }


@router.post("/{payment_id}/cancel-ebarimt")
async def cancel_ebarimt_endpoint(payment_id: str, body: dict | None = None,
                                  db: Session = Depends(get_db),
                                  user: User = Depends(require("vat", "reports"))):
    """Ибаримт хуудасны «Цуцлах» — баримтыг сувгаар нь буцааж CANCELLED болгоно.
    body: {note?}. Мөнгө буцаахгүй. Дараа нь «Дахин үүсгэх»-ээр шинэ баримт."""
    payment = db.get(Payment, payment_id)
    if not payment:
        raise HTTPException(404, "Төлбөр олдсонгүй")
    site = _site_of(payment)
    enforce_site(user, site.id if site else None)
    note = str((body or {}).get("note") or "").strip()[:120] or f"Цуцлав ({user.username})"
    payment = _lock_payment(db, payment_id)
    if payment is None:
        raise HTTPException(409, "Баримтын ажиллагаа явагдаж байна — түр хүлээнэ үү")
    res = await cancel_ebarimt(db, payment, note)
    db.add(AuditLog(username=user.username, action="EBARIMT_CANCEL", entity="payment",
                    entity_id=payment_id, detail={**res, "note": note}))
    db.commit()
    if not res.get("ok"):
        raise HTTPException(400, f"Баримт цуцалж чадсангүй: {res.get('error')}")
    return res


@router.post("/{payment_id}/retry-ebarimt")
async def retry_ebarimt_endpoint(payment_id: str, body: dict | None = None,
                                 db: Session = Depends(get_db),
                                 user: User = Depends(require("vat", "reports"))):
    """Бүтэлгүйтсэн НӨАТ баримтыг дахин үүсгэх (UI-ийн «Баримт дахин үүсгэх» товч).

    body (сонголт, 2026-09-06): худалдан авагчийн дугаар БУРУУ байсан баримтыг засах —
      {receiver_type: "CITIZEN"}  → иргэний (нэргүй) баримт болгож дахин үүсгэнэ
      {customer_tin: "…"}         → зөв регистр/ТТД өгч байгууллагын баримт дахин үүсгэнэ
    Жишээ: 6853959 гэсэн регистр ТЕГ-т байхгүй (getTinInfo found=false) тул QPay
    «customerTin буруу» гэж унасаар, засах арга байгаагүй."""
    payment = db.get(Payment, payment_id)
    if not payment:
        raise HTTPException(404, "Төлбөр олдсонгүй")
    site = _site_of(payment)
    enforce_site(user, site.id if site else None)  # өөр зогсоолын баримтыг дахин үүсгэхгүй
    payment = _lock_payment(db, payment_id)  # давхар товшилтоос давхар баримт гаргахгүй
    if payment is None:
        raise HTTPException(409, "Баримт үүсгэх ажиллагаа явагдаж байна — түр хүлээнэ үү")
    body = body or {}
    # POS терминал (Ontime) гаргасан баримт — msgbill-ээр ДАХИН гаргавал ТЕГ-т давхар.
    # Зөвхөн «Систем баримт гаргах» (reissue_external=true) + «POS терминал баримт
    # гаргадаг» дүрэм үйлчлэхгүй, ЭСВЭЛ терминал хэвлээгүйг баталгаажуулсан
    # (no_terminal_receipt=true — 2026-10-14: дүрэм ерөнхийдөө асаалттай тул нэг
    # борлуулалтын төлөө зогсоолын тохиргоог унтраахгүй).
    from ..services.financial_jobs import EXTERNAL_REFUSAL
    reissue = bool(body.get("reissue_external"))
    has_external = db.query(VatReceipt.id).filter(VatReceipt.payment_id == payment.id,
                                                  VatReceipt.status == "EXTERNAL").first() is not None
    if has_external and not reissue:
        raise HTTPException(409, EXTERNAL_REFUSAL)
    jobs = []
    if (body.get("customer_tin") or body.get("receiver_type")) and not has_external:
        from ..models import FinancialJob
        jobs = (db.query(FinancialJob).filter_by(payment_id=payment.id, kind="RECEIPT")
                .order_by(FinancialJob.id).with_for_update().all())
        if not jobs or any(j.attempts or j.status == "SENDING" for j in jobs):
            raise HTTPException(409, "Өмнөх баримтыг эхлээд сувгаар тулгаж цуцална уу; илгээсэн оролдлогын дүн/хүлээн авагчийг өөрчлөхгүй")
    changed = {}
    if body.get("customer_tin"):
        reg, rtype = msgbill.classify_reg_no(body.get("customer_tin"))
        if not reg:
            raise HTTPException(400, "Худалдан авагчийн дугаар танигдсангүй — ААН регистр (7 орон), "
                                     "ТТД (11–14 орон) эсвэл иргэний регистр (АА00112233)")
        payment.customer_tin = reg
        payment.ebarimt_receiver_type = "COMPANY" if rtype == "ORGANIZATION" else "CITIZEN"
        changed = {"customer_tin": reg, "receiver_type": payment.ebarimt_receiver_type}
    elif (body.get("receiver_type") or "").upper() == "CITIZEN":
        changed = {"was_tin": payment.customer_tin, "receiver_type": "CITIZEN"}
        payment.customer_tin = None
        payment.ebarimt_receiver_type = "CITIZEN"
    if changed:
        for job in jobs:
            job.payload = {**job.payload, "customer_tin": payment.customer_tin,
                           "receiver_type": payment.ebarimt_receiver_type}
            receipt = db.get(VatReceipt, job.receipt_id)
            if receipt:
                receipt.customer_tin = payment.customer_tin
        db.flush()
    allow_dup = bool(body.get("allow_duplicate"))
    no_terminal = bool(body.get("no_terminal_receipt"))
    res = await retry_ebarimt(db, payment, reissue_external=reissue and has_external,
                              allow_duplicate=allow_dup, no_terminal_receipt=no_terminal,
                              username=user.username)
    if res.get("duplicate_of_pos"):
        # Юу ч үүсгээгүй — POS терминалын давхардал гэж цуцалсан баримтыг дахин
        # гаргавал ТЕГ-т нэг борлуулалт дахиад 2 баримттай болно (2026-10-13)
        db.rollback()
        db.add(AuditLog(username=user.username, action="EBARIMT_RETRY_REFUSED", entity="payment",
                        entity_id=payment_id, detail={"reason": "DUPLICATE_OF_POS_TERMINAL"}))
        db.commit()
        raise HTTPException(409, res["error"])
    # created=True: бүгд цуцлагдсан төлбөрийн орлох мөрийг тохиргоо асаалттай тул
    # «терминал гаргасан» (EXTERNAL) гэж БҮРТГЭСЭН — төлөв өөрчлөгдсөн тул 200 (UI
    # дахин ачаална). Үгүй бол татгалзал — хүлээн авагчийн өөрчлөлтийг ч хадгалахгүй.
    refused = bool(res.get("external")) and not res.get("created")
    if refused:
        db.rollback()
    db.add(AuditLog(username=user.username, action="EBARIMT_RETRY", entity="payment",
                    entity_id=payment_id, detail={**res, **({"changed": changed} if changed else {}),
                                                  **({"reissue_external": True} if reissue else {}),
                                                  **({"allow_duplicate": True} if allow_dup else {}),
                                                  **({"no_terminal_receipt": True} if no_terminal else {})}))
    db.commit()
    if refused:
        raise HTTPException(409, res.get("error"))
    if res.get("created"):
        return res
    if not res.get("ok") and not res.get("pending"):
        raise HTTPException(400, f"Баримт үүсгэж чадсангүй: {res.get('error')}")
    return res
