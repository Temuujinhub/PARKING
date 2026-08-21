"""Нөхөн төлбөр — төлбөргүй гарсан машины нэхэмжлэл (Google Sheets: JGA Admin sp / JGA Cash таб).

Урсгал:
  1. Үүсэх: (а) оператор төлбөргүй гаргахдаа "нөхөн төлбөр үүсгэх" сонгох,
            (б) шөнийн хаалт — бүх зогсож буй машиныг гаргаж нэхэмжлэл үүсгэх
  2. Төлөгдөх: касс дээр бэлнээр (дараагийн ирэлтэд)
  3. Хориг: тохируулсан босгод (өрийн тоо ЭСВЭЛ дүн — Хар жагсаалт → Дүрэм)
            хүрсэн дугаар автоматаар хар жагсаалтад орно
  4. Касс/шалгах дэлгэцэд нөхөн төлбөртэй машин улаанаар тэмдэглэгдэнэ
"""
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..auth import enforce_site, operator_sites, require
from ..database import get_db
from ..models import (AuditLog, BlacklistEntry, CashierShift, Compensation, ParkingSession,
                      Payment, User)
from ..serializers import to_dict
from ..session_logic import session_fee_info
from ..ws import manager

log = logging.getLogger("parking.compensations")

router = APIRouter(prefix="/api/compensations", tags=["compensations"])


def pending_count(db: Session, plate: str) -> int:
    return db.query(Compensation).filter(Compensation.plate_number == plate,
                                         Compensation.status == "PENDING").count()


def _auto_blacklist(db: Session, plate: str, username: str):
    """Тохируулсан босгод хүрсэн өртэй машиныг хар жагсаалтад автоматаар нэмнэ.

    Босго нь Хар жагсаалт → Дүрэм хэсгээс өөрчлөгддөг (app_settings):
    төлөгдөөгүй өрийн ТОО эсвэл нийт ДҮН — аль нэг нь хангагдвал орно."""
    from ..services.app_settings import get_blacklist_rules
    rules = get_blacklist_rules(db)
    if not rules["auto_enabled"]:
        return
    cnt = pending_count(db, plate)
    total = float(db.query(func.coalesce(func.sum(Compensation.amount), 0))
                  .filter(Compensation.plate_number == plate,
                          Compensation.status == "PENDING").scalar() or 0)
    by_count = bool(rules["debt_count"]) and cnt >= rules["debt_count"]
    by_amount = bool(rules["debt_amount"]) and total >= rules["debt_amount"]
    if not (by_count or by_amount):
        return
    exists = db.query(BlacklistEntry).filter(BlacklistEntry.plate_number == plate,
                                             BlacklistEntry.is_active.is_(True)).first()
    if not exists:
        why = (f"{cnt} удаагийн төлөгдөөгүй өр" if by_count
               else f"төлөгдөөгүй өр {total:,.0f}₮")
        db.add(BlacklistEntry(plate_number=plate,
                              reason=f"{why} (автомат хориг)",
                              created_by=f"систем ({username})"))


def create_compensation(db: Session, session: ParkingSession, reason: str, username: str) -> Compensation:
    fee = session_fee_info(db, session)
    comp = Compensation(
        session_id=session.id, site_id=session.site_id, plate_number=session.plate_number,
        amount=fee["total_fee"] or session.total_fee or 0, reason=reason, created_by=username,
    )
    db.add(comp)
    db.flush()
    _auto_blacklist(db, session.plate_number, username)
    return comp


@router.get("")
def list_compensations(status: str | None = None, plate: str | None = None,
                       limit: int = 200, db: Session = Depends(get_db),
                       user: User = Depends(require("compensations", "reports"))):
    allowed = operator_sites(user)  # оператор зөвхөн өөрийн зогсоолуудын өр
    q = db.query(Compensation)
    if allowed:
        q = q.filter(Compensation.site_id.in_(allowed))
    if status:
        q = q.filter(Compensation.status == status)
    if plate:
        q = q.filter(Compensation.plate_number.ilike(f"%{plate.upper().strip()}%"))
    rows = q.order_by(Compensation.created_at.desc()).limit(min(limit, 1000)).all()
    now = datetime.utcnow()

    def _age(c):
        return (now - c.created_at).days

    def _bucket(days):
        return "0-7" if days <= 7 else "8-30" if days <= 30 else "31-90" if days <= 90 else "90+"

    # Дугаар бүрийн PENDING тоог нэг query-ээр (мөр бүрт COUNT хийхгүй)
    from sqlalchemy import func
    plates = {c.plate_number for c in rows}
    pending_by_plate: dict[str, int] = dict(
        db.query(Compensation.plate_number, func.count())
        .filter(Compensation.plate_number.in_(plates), Compensation.status == "PENDING")
        .group_by(Compensation.plate_number).all()) if plates else {}
    out_rows = []
    for c in rows:
        d = _age(c)
        out_rows.append(to_dict(c, extra={"site_name": c.site.name if c.site else None,
                                          "days_old": d, "age_bucket": _bucket(d),
                                          "pending_count": pending_by_plate.get(c.plate_number, 0)}))
    # Нийлбэрүүд: төлөгдөөгүй нийт + настжуулалт (aging) + цугларсан
    pq = db.query(Compensation).filter(Compensation.status == "PENDING")
    paidq = db.query(Compensation).filter(Compensation.status == "PAID")
    if allowed:
        pq = pq.filter(Compensation.site_id.in_(allowed))
        paidq = paidq.filter(Compensation.site_id.in_(allowed))
    pending = pq.all()
    aging = {"0-7": 0.0, "8-30": 0.0, "31-90": 0.0, "90+": 0.0}
    for c in pending:
        aging[_bucket(_age(c))] += float(c.amount)
    return {"rows": out_rows,
            "total_pending": float(sum(c.amount for c in pending)),
            "pending_count": len(pending),
            "total_collected": float(sum(c.amount for c in paidq.all())),
            "aging": aging}


@router.post("/{comp_id}/pay")
async def pay_compensation(comp_id: str, body: dict | None = None, db: Session = Depends(get_db),
                           user: User = Depends(require("compensations"))):
    """Нөхөн төлбөрийг бэлэн/картаар төлүүлж хаах + e-Barimt үүсгэнэ.
    body: {method: CASH|CARD, customer_tin?}."""
    from ..config import settings
    from ..services import ebarimt
    body = body or {}
    method = body.get("method", "CASH")
    comp = db.get(Compensation, comp_id)
    if not comp or comp.status != "PENDING":
        raise HTTPException(404, "Төлөгдөөгүй нэхэмжлэл олдсонгүй")
    allowed = operator_sites(user)
    if allowed and comp.site_id not in allowed:
        raise HTTPException(403, "Энэ нэхэмжлэл таны хариуцах зогсоолынх биш")
    comp.status = "PAID"
    comp.paid_at = datetime.utcnow()
    comp.paid_by = user.username
    amount = float(comp.amount)
    vat = round(amount * settings.vat_rate / (1 + settings.vat_rate))
    tin = str(body.get("customer_tin") or "").strip()[:20] or None
    pm = "CASH" if method == "CASH" else "CARD"
    # ТӨЛБӨРИЙН БИЧИЛТ — өмнө нь энд Payment мөр үүсгэдэггүй байсан тул кассчны
    # цуглуулсан өрийн БЭЛЭН МӨНГӨ орлогын тайлан, ээлжийн тооцоо, мөнгөн
    # тооцоонд ОГТ харагддаггүй байв (2026-08-09-нд илрүүлэв). Одоо ердийн
    # төлбөртэй адил бүртгэгдэж, ээлжид холбогдоно.
    pay = None
    if comp.session_id:
        shift = (db.query(CashierShift)
                 .filter(CashierShift.user_id == user.id,
                         CashierShift.status == "OPEN").first())
        pay = Payment(
            session_id=comp.session_id,
            provider="CASH" if method == "CASH" else "POS",
            payment_method=pm,
            source="POS",
            sender_invoice_no=f"DEBT-{comp.id[:8].upper()}-{datetime.utcnow():%Y%m%d%H%M%S}",
            amount=amount, vat_amount=vat, status="PAID", paid_at=comp.paid_at,
            cashier_id=user.id, shift_id=shift.id if shift else None,
            customer_tin=tin, ebarimt_receiver_type="COMPANY" if tin else "CITIZEN",
        )
        db.add(pay)
        db.flush()
        comp.payment_id = pay.id
    else:
        log.warning(f"нөхөн төлбөр {comp_id} session-гүй — Payment бичилт үүсгэсэнгүй")

    # e-Barimt (амжилтгүй байсан ч төлбөрийг хаана) — 2026-08-19: msgbill.mn
    # (зогсоол/түрээслэгчийн түлхүүр) → локал PosAPI → суваг байхгүй бол FAILED
    # (хуурамч MOCK баримт үүсгэхгүй). VatReceipt-д бүртгэж Ибаримт хуудсанд
    # харагдуулна (өмнө нь огт бүртгэдэггүй байв).
    from ..models import ParkingSite, VatReceipt
    from ..services import msgbill
    site = db.get(ParkingSite, comp.site_id) if comp.site_id else None
    mb_acc = msgbill.account_enabled_for(site, pm)
    receipt, rec_err, rec_provider = {}, None, "POSAPI"
    try:
        if mb_acc is not None:
            rec_provider = "MSGBILL"
            receipt = await msgbill.create_receipt(
                mb_acc, amount, description=f"Зогсоолын өр · {comp.plate_number or ''} · "
                                            f"{getattr(site, 'name', '') or ''}",
                payment_method=pm, idempotency_key=f"comp-{comp.id}", customer_tin=tin)
            if not receipt.get("billId"):
                rec_err = receipt.get("error") or f"msgbill төлөв {receipt.get('state') or '?'}"
        elif settings.ebarimt_mock and not settings.ebarimt_mock_receipts:
            rec_err = ("Баримтын суваг байхгүй — PosAPI суугаагүй (MOCK), msgbill түлхүүр "
                       "тохируулаагүй. Тохиргоо → Холболт → e-Barimt API")
        else:
            receipt = await ebarimt.create_receipt(
                amount, vat, pm, customer_tin=tin,
                merchant=ebarimt.merchant_for(site))   # түрээслэгчийн ТТД-ээр
        ebarimt.cache_qr(comp.id, receipt.get("qrData"))
        if pay is not None:
            ebarimt.cache_qr(pay.id, receipt.get("qrData"))
    except Exception as e:  # noqa: BLE001
        rec_err = str(e)[:200]
        log.error(f"нөхөн төлбөрийн e-Barimt амжилтгүй: {comp_id}: {e}")
    if pay is not None:
        db.add(VatReceipt(
            payment_id=pay.id, session_id=comp.session_id,
            ebarimt_id=receipt.get("billId"),
            lottery_code=None if tin else receipt.get("lottery"),
            amount=amount, vat_amount=vat, customer_tin=tin,
            status="SENT" if receipt.get("billId") else "FAILED",
            receipt_url=rec_err, provider=rec_provider, provider_ref=receipt.get("msgbillId")))
    db.add(AuditLog(username=user.username, action="COMPENSATION_PAID", entity="compensation",
                    entity_id=comp_id,
                    detail={"plate": comp.plate_number, "amount": amount, "method": method}))
    db.commit()
    return {**to_dict(comp), "method": method, "ebarimt_id": receipt.get("billId"),
            "lottery_code": receipt.get("lottery"),
            "qr_data": ebarimt.get_cached_qr(comp.id)}


@router.post("/{comp_id}/cancel")
def cancel_compensation(comp_id: str, body: dict, db: Session = Depends(get_db),
                        user: User = Depends(require("discounts", "settings"))):
    """Нэхэмжлэл цуцлах (зөвхөн админ) — шалтгаан заавал."""
    comp = db.get(Compensation, comp_id)
    if not comp or comp.status != "PENDING":
        raise HTTPException(404, "Төлөгдөөгүй нэхэмжлэл олдсонгүй")
    # pay_compensation дээр байдаг шалгалт энд дутуу байсан — өөр түрээслэгчийн
    # авлагыг comp_id мэдэхэд л цуцлах боломжтой байв (санхүүгийн IDOR).
    allowed = operator_sites(user)
    if allowed and comp.site_id not in allowed:
        raise HTTPException(403, "Энэ нэхэмжлэл таны хариуцах зогсоолынх биш")
    comp.status = "CANCELLED"
    db.add(AuditLog(username=user.username, action="COMPENSATION_CANCELLED", entity="compensation",
                    entity_id=comp_id, detail={"reason": body.get("reason", ""), "plate": comp.plate_number}))
    db.commit()
    return to_dict(comp)


@router.post("/night-close")
async def night_close(body: dict, db: Session = Depends(get_db),
                      user: User = Depends(require("settings"))):
    """Шөнийн хаалт (JGA спек): зогсож буй БҮХ машиныг гаргаж нөхөн төлбөр үүсгэнэ.
    body: {site_id?} — заавал биш, өгөхгүй бол бүх зогсоол. Болгоомжтой — буцаахгүй үйлдэл!"""
    q = db.query(ParkingSession).filter(ParkingSession.status.in_(["OPEN", "AWAITING_PAYMENT"]))
    # Хамрах хүрээ: өмнө нь site_id өгөхгүй бол БҮХ ТҮРЭЭСЛЭГЧИЙН машиныг хааж,
    # тус бүрд нь өр үүсгэдэг байв (буцаах боломжгүй). Нэг зогсоолын админ нөгөө
    # компанийн бүх машиныг нэг хүсэлтээр хаах боломжтой байсан.
    allowed = operator_sites(user)
    if body.get("site_id"):
        enforce_site(user, body["site_id"])
        q = q.filter(ParkingSession.site_id == body["site_id"])
    elif allowed is not None:
        q = q.filter(ParkingSession.site_id.in_(allowed))
    sessions = q.all()
    now = datetime.utcnow()
    created = 0
    for s in sessions:
        fee = session_fee_info(db, s, at=now)
        s.exit_time = now
        s.duration_minutes = fee["duration_minutes"]
        s.base_fee, s.vat_amount, s.total_fee = fee["base_fee"], fee["vat_amount"], fee["total_fee"]
        s.status = "FREE" if fee["is_free"] else "MANUAL_CLOSED"
        # Session тутмын бүртгэл — site-ийн түвшний NIGHT_CLOSE нь Түүхийн
        # мөр бүрийг тайлбарлаж чаддаггүй (2026-08-16).
        db.add(AuditLog(username=user.username, action="NIGHT_CLOSE_CAR",
                        entity="session", entity_id=s.id,
                        detail={"plate": s.plate_number}))
        # Өр үүсгэх эсэх — Тохиргоо → Авто цэвэрлэгээ (2026-08-21)
        from ..services.app_settings import get_autoclose_rules
        if not fee["is_free"] and get_autoclose_rules(db)["create_debt_night_close"]:
            create_compensation(db, s, "night_close", user.username)
            created += 1
    db.add(AuditLog(username=user.username, action="NIGHT_CLOSE", entity="site",
                    entity_id=body.get("site_id") or "all",
                    detail={"closed_sessions": len(sessions), "compensations": created}))
    db.commit()
    await manager.broadcast(body.get("site_id") or "all", "NIGHT_CLOSE", {
        "closed": len(sessions), "compensations": created, "by": user.username,
    })
    return {"closed_sessions": len(sessions), "compensations_created": created}
