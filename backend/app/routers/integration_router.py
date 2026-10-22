"""Түншийн интеграцийн API (/api/v1) — wallet/апп-уудын B2B холболт.

tokI, Easy Wallet зэрэг гадаад төлбөрийн систем энэ API-аар:
  1. GET  /api/v1/sites                     — зогсоолууд + сул байрны тоо
  2. GET  /api/v1/sessions?plate=           — дугаараар хайж төлөх дүнг авах
  3. POST /api/v1/payments                  — төлбөрийн intent үүсгэх (PENDING)
  4. POST /api/v1/payments/{id}/confirm     — өөрийн талд амжилттай болмогц баталгаажуулах
                                              → систем PAID болгож ХААЛТ НЭЭНЭ
  5. GET  /api/v1/payments/{id}             — төлөв шалгах

Нэвтрэлт: X-API-Key толгой. Түлхүүр ХОЁР эх үүсвэртэй:
  1. partner_keys хүснэгт (Тохиргоо → Холболт → Гадаад API-гаас удирдана,
     restart шаардлагагүй; DB-д зөвхөн SHA-256 hash хадгалагдана)
  2. .env-ийн PARKING_PARTNER_KEYS — хуучин fallback, одоогийн партнерууд тасрахгүй
Түншийн нэр Payment.provider болж тайланд ялгарна.
"""
import hashlib
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models import AuditLog, ParkingSession, ParkingSite, PartnerKey, Payment
from ..ratelimit import throttle
from ..session_logic import amount_due, normalize_plate, session_fee_info

router = APIRouter(prefix="/api/v1", tags=["integration"])
log = __import__("logging").getLogger("parking.integration")


def receipt_info(db: Session, payment: Payment) -> dict | None:
    """Төлбөрийн e-Barimt мэдээлэл — түнш (Easy Wallet) аппдаа жолоочид харуулна.
    ДДТД, сугалаа, дүн/НӨАТ, төлөв, QR (түр санах ойд байвал). Баримт үүсээгүй/
    амжилтгүй бол status FAILED + шалтгаан — түнш дараа GET-ээр дахин асууж болно."""
    from ..models import VatReceipt
    from ..services.receipts import receipt_qr
    r = (db.query(VatReceipt).filter(VatReceipt.payment_id == payment.id)
         .order_by(VatReceipt.created_at.desc()).first())
    if r is None:
        return None
    return {"ddtd": r.ebarimt_id, "lottery": r.lottery_code,
            "amount": float(r.amount or 0), "vat_amount": float(r.vat_amount or 0),
            "status": r.status, "provider": r.provider,
            # EXTERNAL = POS терминал өөрөө гаргасан (алдаа биш) — тайлбарыг error-д бүү хий
            "error": (r.receipt_url or "")[:200] if r.status not in ("SENT", "EXTERNAL") else None,
            "qr_data": receipt_qr(r),   # ЭНЭ баримтын QR (түлхүүр = баримт, 2026-10-15)
            "created_at": r.created_at.isoformat() if r.created_at else None}


def receipt_kind(db: Session, payment: Payment, rec) -> tuple[str, str | None]:
    """Баримт юуных вэ: PARKING (зогсолт) | DEBT (өр — debt_id-тай) | RESERVATION (захиалга).
    Өрийн баримтын outbox түлхүүр нь `pay-{payment}-comp-{өр}` (payments_router._finalize_paid)."""
    from ..models import FinancialJob
    if (payment.kind or "") == "RESERVATION":
        return "RESERVATION", None
    if (payment.kind or "") == "DEBT":
        return "DEBT", None
    job = (db.query(FinancialJob.payload).filter(FinancialJob.receipt_id == rec.id)
           .order_by(FinancialJob.created_at).first())
    key = ((job[0] or {}).get("idempotency_key") or "") if job else ""
    marker = f"pay-{payment.id}-comp-"
    if key.startswith(marker):
        return "DEBT", key[len(marker):] or None
    return "PARKING", None


def receipt_list(db: Session, payment: Payment, qr: dict | None = None) -> list[dict]:
    """Төлбөрийн БҮХ e-Barimt (2026-10-22) — өртэй төлбөрт зогсолт + өр тус бүрийн баримт.
    `ebarimt` талбар (хуучин) зөвхөн хамгийн сүүлийнхийг өгдөг тул wallet бүгдийг харуулж
    чаддаггүй байв. Цуцлагдсан (CANCELLED) мөр орохгүй. qr: {receipt_id: qr_data} — GET
    msgbill-ээс нөхөж татсан QR (санах ойд байхгүй үед)."""
    from ..models import VatReceipt
    from ..services.receipts import receipt_qr
    rows = (db.query(VatReceipt).filter(VatReceipt.payment_id == payment.id,
                                        VatReceipt.status != "CANCELLED")
            .order_by(VatReceipt.created_at).all())
    out = []
    for r in rows:
        kind, debt_id = receipt_kind(db, payment, r)
        out.append({"receipt_id": r.id, "kind": kind, "debt_id": debt_id,
                    "ddtd": r.ebarimt_id, "lottery": r.lottery_code,
                    "amount": float(r.amount or 0), "vat_amount": float(r.vat_amount or 0),
                    "status": r.status, "provider": r.provider,
                    "receiver_type": "COMPANY" if (r.customer_tin and payment.ebarimt_receiver_type
                                                   == "COMPANY") else "CITIZEN",
                    "error": (r.receipt_url or "")[:200] if r.status not in ("SENT", "EXTERNAL") else None,
                    "qr_data": (qr or {}).get(r.id) or receipt_qr(r),
                    "created_at": r.created_at.isoformat() if r.created_at else None})
    return out


async def fetch_missing_qr(db: Session, payment: Payment, limit: int = 4) -> dict:
    """SENT msgbill баримтын QR санах ойд алга (1 цаг өнгөрсөн/restart) бол msgbill-ээс татна —
    /print-тэй ИЖИЛ зам (financial_jobs.ensure_qr; QR-ыг DB-д хэзээ ч бичихгүй, ТЕГ №11)."""
    from ..models import VatReceipt
    from ..services import financial_jobs as FJ
    from ..services.receipts import receipt_qr
    out: dict = {}
    rows = (db.query(VatReceipt).filter(VatReceipt.payment_id == payment.id,
                                        VatReceipt.status == "SENT",
                                        VatReceipt.provider == "MSGBILL").limit(limit).all())
    for r in rows:
        if receipt_qr(r):
            continue
        try:
            qr = await FJ.ensure_qr(db, payment, r, timeout=2.5)
        except Exception:  # noqa: BLE001 — QR-гүй хариу ч хүчинтэй
            qr = None
        if qr:
            out[r.id] = qr
    return out


DEBT_REASONS = {
    "unpaid_exit": "Төлбөр төлөлгүй гарсан", "auto_close": "Авто хаалт (хугацаа хэтэрсэн)",
    "night_close": "Шөнийн хаалт", "shift_close": "Ээлж хаалт", "manual": "Гараар бүртгэсэн",
    "admin_remove": "Админ хассан", "log_exit": "Камерын логоор гарсан",
}


def debt_items(db: Session, comps) -> list[dict]:
    """Өр бүрийн дэлгэрэнгүй (2026-10-22): ХЭЗЭЭ үүссэн, аль зогсоолд, ямар айлчлалаас —
    wallet өрийг одоогийн төлбөрөөс ТУСАД НЬ, огноотой нь харуулна. Цаг UTC (бүсгүй ISO,
    /sessions-тэй ижил хэлбэр). Хуучнаас шинэ рүү."""
    comps = sorted(comps, key=lambda c: c.created_at or datetime.min)
    sids = [c.session_id for c in comps if c.session_id]
    stays = {s.id: s for s in db.query(ParkingSession).filter(ParkingSession.id.in_(sids)).all()} if sids else {}
    out = []
    for c in comps:
        stay = stays.get(c.session_id)
        site = c.site
        out.append({
            "debt_id": c.id, "amount": float(c.amount),
            "created_at": c.created_at.isoformat() if c.created_at else None,
            "reason": c.reason, "reason_text": DEBT_REASONS.get(c.reason or "", c.reason or ""),
            "site_code": site.site_code if site else None, "site_name": site.name if site else None,
            "session_id": c.session_id,
            "entry_time": stay.entry_time.isoformat() if stay and stay.entry_time else None,
            "exit_time": stay.exit_time.isoformat() if stay and stay.exit_time else None,
            "duration_minutes": int(stay.duration_minutes) if stay and stay.duration_minutes is not None else None,
        })
    return out


def payment_breakdown(db: Session, payment: Payment) -> dict:
    """Нэгдсэн (include_debts) төлбөрийн задаргаа: зогсолт + өр тус бүр (огноотой)."""
    from ..models import Compensation
    snap = payment.fee_snapshot or {}
    ids = [d.get("id") for d in snap.get("debts") or [] if d.get("id")]
    comps = db.query(Compensation).filter(Compensation.id.in_(ids)).all() if ids else []
    debt_amount = round(sum(float(d.get("amount") or 0) for d in snap.get("debts") or []), 2)
    parking = (round(min(float(payment.amount), float(snap["parking_amount"])), 2)
               if "parking_amount" in snap else round(float(payment.amount) - debt_amount, 2))
    return {"parking_amount": parking, "debt_amount": debt_amount, "debt_count": len(ids),
            "debts": debt_items(db, comps)}


def _payment_event(db: Session, payment: Payment, event: str) -> dict:
    from .payments_router import settlement_info
    sess = db.get(ParkingSession, payment.session_id) if payment.session_id else None
    site = db.get(ParkingSite, sess.site_id) if sess else None
    return {"event": event, "payment_id": payment.id, "status": payment.status,
            "transaction_id": payment.provider_payment_id,
            "amount": float(payment.amount), "vat_amount": float(payment.vat_amount or 0),
            "paid_at": payment.paid_at.isoformat() if payment.paid_at else None,
            "plate": sess.plate_number if sess else None,
            "site_code": site.site_code if site else None,
            "site_name": site.name if site else None,
            **payment_breakdown(db, payment),
            "ebarimt": receipt_info(db, payment), **settlement_info(db, payment)}


async def push_partner_webhook(url: str, payload: dict, partner: str,
                               timeout: float = 6.0) -> dict:
    """Түншийн сервер рүү POST. Буцаана: {ok, status_code, body, error} — аудит/туршилтад.
    Алдаа хэзээ ч дуудагчийг унагаахгүй (төлбөр/хаалт аль хэдийн хийгдсэн)."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.post(url, json=payload,
                             headers={"Content-Type": "application/json",
                                      "X-Parking-Partner": partner,
                                      "X-Parking-Event": payload.get("event", "")})
        ok = 200 <= r.status_code < 300
        return {"ok": ok, "status_code": r.status_code, "body": (r.text or "")[:300], "error": None}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "status_code": None, "body": None, "error": str(e)[:200]}


def _webhook_url_for(db: Session, partner: str) -> str | None:
    key_id = getattr(partner, "key_id", None)
    q = db.query(PartnerKey).filter(PartnerKey.name == partner, PartnerKey.is_active.is_(True))
    if key_id:
        q = q.filter(PartnerKey.id == key_id)
    else:
        q = q.filter(PartnerKey.site_id.is_(None))
    rows = q.limit(2).all()
    return (rows[0].webhook_url or "").strip() or None if len(rows) == 1 else None


OPEN_STATUSES = ("OPEN", "AWAITING_PAYMENT", "PAID")


class PartnerAuth(str):
    """Танигдсан партнер. str-ээс удамшдаг тул Payment.provider,
    f"partner:{...}" зэрэг одоогийн бүх хэрэглээ өөрчлөгдөхгүй; дээр нь
    scopes/site_id хязгаарлалтыг авч явна (.env түлхүүрт хязгаарлалтгүй)."""
    scopes: str = "read,pay"
    site_id: str | None = None
    key_id: str | None = None

    def can_pay(self) -> bool:
        return self.has_scope("pay")

    def has_scope(self, scope: str) -> bool:
        return scope in {s.strip() for s in (self.scopes or "").split(",")}


def _auth_fail(request: Request):
    # Брут-форсоос хамгаалах: буруу түлхүүрийн оролдлогыг IP-ээр хязгаарлана
    ip = request.client.host if request.client else "?"
    if throttle(f"partner-bad:{ip}", limit=20, window=60):
        raise HTTPException(429, "Хэт олон буруу оролдлого")
    raise HTTPException(401, "API түлхүүр буруу")


def require_partner(request: Request, x_api_key: str = Header(default=""),
                    db: Session = Depends(get_db)) -> PartnerAuth:
    """X-API-Key-ээр түншийг таньж нэрийг нь буцаана (Payment.provider болно).
    Эхлээд DB (partner_keys, hash тулгана), дараа нь .env fallback."""
    key = x_api_key or ""
    if key:
        h = hashlib.sha256(key.encode()).hexdigest()
        row = (db.query(PartnerKey)
               .filter(PartnerKey.key_hash == h, PartnerKey.is_active.is_(True)).first())
        if row:
            # last_used_at-ыг минут тутам л шинэчилнэ — хүсэлт бүрд UPDATE хийхгүй
            now = datetime.utcnow()
            if not row.last_used_at or now - row.last_used_at > timedelta(seconds=60):
                row.last_used_at = now
                db.commit()
            auth = PartnerAuth(row.name)
            auth.scopes = row.scopes or "read"
            auth.site_id = row.site_id
            auth.key_id = row.id
            return auth
    partners = settings.partner_map()
    if not partners and not db.query(PartnerKey.id).first():
        raise HTTPException(503, "Интеграцийн API идэвхгүй (түлхүүр бүртгэгдээгүй)")
    name = partners.get(key)
    if not name:
        _auth_fail(request)
    return PartnerAuth(name)   # .env түлхүүр — бүрэн эрхтэй (хуучин зан төлөв)


def _require_pay(partner: PartnerAuth):
    if str(partner).strip().upper() in {"QPAY", "POS", "CASH", "TRANSFER", "WALLET"}:
        raise HTTPException(403, "Түншийн нэр дотоод төлбөрийн хэрэгслийн нэртэй давхцсан; админ засна уу")
    if not partner.can_pay():
        raise HTTPException(403, "Энэ түлхүүр зөвхөн лавлах эрхтэй (төлбөрийн эрхгүй)")


def _check_site_scope(partner: PartnerAuth, site_id: str | None):
    if partner.site_id and partner.site_id != site_id:
        raise HTTPException(403, "Энэ түлхүүр өөр зогсоолын мэдээлэлд хандах эрхгүй")


def _check_payment_scope(db, partner, payment):
    if str(payment.provider).upper() in {"QPAY", "POS", "CASH", "TRANSFER", "WALLET"}:
        raise HTTPException(403, "Дотоод төлбөрийн хэрэгслийг түншийн API-аар удирдахгүй")
    if payment.provider != partner:
        raise HTTPException(403, "Энэ төлбөр өөр түншийнх")
    if payment.partner_key_id and payment.partner_key_id != partner.key_id:
        raise HTTPException(403, "Энэ төлбөр өөр түншийн түлхүүрт харьяалагдана")
    session = db.get(ParkingSession, payment.session_id) if payment.session_id else None
    if not session:
        raise HTTPException(404, "Зогсолтын бүртгэл олдсонгүй")
    _check_site_scope(partner, session.site_id)


def _session_payload(db: Session, s: ParkingSession, with_debts: bool = True) -> dict:
    """Түншид өгөх session-ий бүрэн мэдээлэл: хугацаа, тариф, төлөх үлдэгдэл."""
    fee = session_fee_info(db, s)
    due = amount_due(db, s, fee)
    return {
        "session_id": s.id,
        "plate_number": s.plate_number,
        "site_code": s.site.site_code if s.site else None,
        "site_name": s.site.name if s.site else None,
        "entry_time": s.entry_time.isoformat() if s.entry_time else None,
        "duration_minutes": fee["duration_minutes"],
        "base_fee": fee["base_fee"],
        "vat_amount": fee["vat_amount"],
        "discount_amount": fee["discount_amount"],
        "total_fee": fee["total_fee"],
        "amount_due": due,          # ← wallet ЭНЭ дүнг нэхэмжилнэ
        "is_free": fee["is_free"],
        "status": s.status,
        "paid": s.status == "PAID" and due <= 0,
        # Өр (2026-10-22): одоогийн төлбөрөөс ТУСДАА, өр бүр үүссэн огноотой — wallet
        # «Өмнөх өр»-ийг тусад нь харуулж, include_debts=true-ээр хамт төлүүлнэ
        **(_debt_summary(db, s) if with_debts else {}),
    }


def _debt_summary(db: Session, s: ParkingSession) -> dict:
    from .payments_router import _pending_debts
    comps = _pending_debts(db, s.plate_number, s.site)
    amount = round(sum(float(c.amount) for c in comps), 2)
    return {"debt_amount": amount, "debt_count": len(comps), "debts": debt_items(db, comps)}


def site_catalog(db: Session, partner: "PartnerAuth") -> tuple[list, list[dict], str]:
    """/sites ба /socialpay/parkings-ийн нэг эх: (ORM site-ууд, мөрүүд, sampled_at)."""
    from ..services.site_location import occupancy_counts, occupied_count
    q = db.query(ParkingSite).filter(ParkingSite.is_active.is_(True))
    if partner.site_id:
        q = q.filter(ParkingSite.id == partner.site_id)
    sites = q.order_by(ParkingSite.site_code).all()
    counts = occupancy_counts(db, [s.id for s in sites])
    sampled_at = datetime.utcnow().isoformat() + 'Z'
    out = []
    for site in sites:
        occupied = occupied_count(site.id, *counts)
        located = site.latitude is not None and site.longitude is not None
        out.append({
            "site_code": site.site_code, "name": site.name,
            "zone_code": site.zone_code, "address": site.address or "",
            "latitude": float(site.latitude) if located else None,
            "longitude": float(site.longitude) if located else None,
            "google_maps_url": site.google_maps_url,
            "location_available": located,
            "capacity": site.capacity, "occupied": occupied,
            "free": max(0, site.capacity - occupied) if site.capacity else None,
            "occupancy_percent": round(occupied * 100 / site.capacity, 1) if site.capacity else None,
            "over_capacity": occupied > site.capacity if site.capacity else False,
            "occupancy_source": "open_sessions", "occupancy_is_estimate": True,
            "sampled_at": sampled_at,
        })
    return sites, out, sampled_at


@router.get("/sites")
def list_sites(db: Session = Depends(get_db), partner: PartnerAuth = Depends(require_partner)):
    """Authenticated map catalogue. Occupancy is a session estimate, not a sensor reading."""
    _, out, sampled_at = site_catalog(db, partner)
    return {"sites": out, "sampled_at": sampled_at}


@router.get("/sessions")
def find_sessions(plate: str, site_code: str = "",
                  db: Session = Depends(get_db), partner: PartnerAuth = Depends(require_partner)):
    """Дугаараар нээлттэй session хайна. site_code өгөхгүй бол БҮХ зогсоолоос —
    жолооч аль зогсоолд байгааг wallet апп мэдэхгүй байж болно."""
    plate = normalize_plate(plate)
    if len(plate) < 4:
        raise HTTPException(400, "Дугаар дутуу байна (4+ тэмдэгт)")
    q = (db.query(ParkingSession)
         .filter(ParkingSession.plate_number == plate,
                 ParkingSession.status.in_(OPEN_STATUSES)))
    if partner.site_id:   # түлхүүрийн зогсоолын хязгаар — бусад зогсоолын зогсолт харагдахгүй
        q = q.filter(ParkingSession.site_id == partner.site_id)
    if site_code:
        site = db.query(ParkingSite).filter(ParkingSite.site_code == site_code).first()
        if not site:
            raise HTTPException(404, "Зогсоол олдсонгүй")
        _check_site_scope(partner, site.id)
        q = q.filter(ParkingSession.site_id == site.id)
    sessions = q.order_by(ParkingSession.entry_time.desc()).limit(5).all()
    return {"sessions": [_session_payload(db, s) for s in sessions]}


@router.get("/recent-exits")
def recent_exits_all_sites(minutes: int = 5, site_code: str = "",
                           include_completed: bool = False,
                           db: Session = Depends(get_db),
                           partner: PartnerAuth = Depends(require_partner)):
    """БҮХ зогсоолын сүүлийн гарцын урсгал (Easy Wallet-ийн хүсэлтээр, 2026-09-01).

    Гарах камерт сүүлийн `minutes` (1..60, default 5) минутад уншигдаад ТӨЛБӨР
    ХҮЛЭЭЖ буй машинууд — wallet апп хэрэглэгчийнхээ машиныг гарц дээр ирмэгц
    таньж, төлбөрийн санал шууд харуулахад зориулав. Мөр бүрд `amount_due`
    (нэхэмжлэх дүн) явна; төлөх бол ердийн POST /payments → /confirm урсгал.

    include_completed=true — мөн хугацаанд ГАРЧ ДУУССАН (төлөгдсөн/үнэгүй)
    машинуудыг `completed` жагсаалтаар нэмж өгнө (тайлан/нотолгооны хэрэглээ).

    Түлхүүр зогсоолоор хязгаарлагдсан бол зөвхөн тэр зогсоолынх; site_code-оор
    нэг зогсоол руу шүүж болно. 30с тутам polling-д зориулагдсан (rate limit-д
    багтана); мөрүүд exit уншилтын цагаар шинээс хуучин руу эрэмбэлэгдэнэ.
    """
    minutes = max(1, min(int(minutes or 5), 60))
    since = datetime.utcnow() - timedelta(minutes=minutes)
    site_ids = None
    if site_code:
        site = db.query(ParkingSite).filter(ParkingSite.site_code == site_code).first()
        if not site:
            raise HTTPException(404, "Зогсоол олдсонгүй")
        _check_site_scope(partner, site.id)
        site_ids = [site.id]
    elif partner.site_id:
        site_ids = [partner.site_id]

    def _rows(statuses):
        q = (db.query(ParkingSession)
             .filter(ParkingSession.status.in_(statuses),
                     # Гарах уншилттай (exit камерт танигдсан) машинууд л —
                     # exit_time нь гарах оролдлогын цаг; хуучин бичлэгт updated_at
                     ParkingSession.exit_device_id.isnot(None),
                     func.coalesce(ParkingSession.exit_time,
                                   ParkingSession.updated_at) >= since))
        if site_ids:
            q = q.filter(ParkingSession.site_id.in_(site_ids))
        return (q.order_by(func.coalesce(ParkingSession.exit_time,
                                         ParkingSession.updated_at).desc())
                .limit(200).all())

    def _row(s: ParkingSession, with_debts: bool = True) -> dict:
        exit_at = s.exit_time or s.updated_at
        return _session_payload(db, s, with_debts) | {
            "exit_read_at": exit_at.isoformat() if exit_at else None,
            "exit_time": s.exit_time.isoformat() if s.exit_time else None,
        }

    out = {"minutes": minutes,
           "waiting": [_row(s) for s in _rows(["AWAITING_PAYMENT"])]}
    if include_completed:
        # Гарч дууссан мөрөнд өрийн задаргаа хэрэггүй (≤200 мөр × өрийн query-ээс сэргийлнэ)
        out["completed"] = [_row(s, with_debts=False) for s in _rows(["PAID", "CLOSED", "FREE"])]
    return out


@router.post("/payments")
def create_payment_intent(body: dict, db: Session = Depends(get_db),
                          partner: PartnerAuth = Depends(require_partner)):
    """Төлбөрийн intent үүсгэнэ. body: {session_id, include_debts?}.
    Буцаах amount-ыг wallet өөрийн талд хэрэглэгчээс нэхэмжилж, амжилттай
    болмогц /payments/{id}/confirm-ыг дуудна. Идэвхтэй PENDING intent
    байвал (дүн зөрөөгүй бол) шинээр үүсгэлгүй түүнийгээ буцаана.

    include_debts=true (заавал биш, анхдагч false — Easy Wallet-ийн зан төлөв
    хэвээр): тухайн түрээслэгчийн зогсоолуудын төлөгдөөгүй өрийг QR-тэй ИЖИЛ
    байдлаар нэг intent-д нийлүүлнэ; confirm нь өрүүдийг PAID болгож тус бүрд баримт
    гаргана (_finalize_paid). Өргүйгээр төлсөн машиныг «3+ өр» дүрэм гарцад барьдаг
    (SocialPay хяналт M2, прод 371 дугаар)."""
    from .payments_router import _create_payment, _pending_debts
    from ..services.checkout import lock_session
    _require_pay(partner)
    include_debts = body.get("include_debts", False)
    if not isinstance(include_debts, bool):
        raise HTTPException(422, "include_debts нь true/false байна")
    session = lock_session(db, body.get("session_id", ""))
    _check_site_scope(partner, session.site_id)
    if session.status not in ("OPEN", "AWAITING_PAYMENT"):
        raise HTTPException(400, f"Session төлөв буруу: {session.status}")

    existing = (db.query(Payment)
                .filter(Payment.session_id == session.id, Payment.provider == partner,
                        Payment.status == "PENDING").first())
    if existing:
        _check_payment_scope(db, partner, existing)
        current_due = amount_due(db, session, session_fee_info(db, session))
        if include_debts:
            current_due += sum(float(c.amount)
                               for c in _pending_debts(db, session.plate_number, session.site))
        if abs(float(existing.amount) - current_due) > 0.01:
            raise HTTPException(409, "Үнэ өөрчлөгдсөн. Өмнөх wallet оролдлогыг баталгаажуулах эсвэл цуцална уу")
        return {"payment_id": existing.id, "amount": float(existing.amount),
                "vat_amount": float(existing.vat_amount), "status": "PENDING",
                "plate_number": session.plate_number, **payment_breakdown(db, existing)}

    payment = _create_payment(db, session, partner, "WALLET", include_debts=include_debts)
    payment.partner_key_id = partner.key_id
    payment.source = "WALLET"
    debts = (payment.fee_snapshot or {}).get("debts") or []
    debt_amount = round(sum(float(d["amount"]) for d in debts), 2)
    db.add(AuditLog(username=f"partner:{partner}", action="WALLET_INTENT", entity="payment",
                    entity_id=payment.id,
                    detail={"plate": session.plate_number, "amount": float(payment.amount),
                            **({"debt_amount": debt_amount, "debt_count": len(debts)}
                               if include_debts else {})}))
    db.commit()
    return {"payment_id": payment.id, "amount": float(payment.amount),
            "vat_amount": float(payment.vat_amount), "status": "PENDING",
            "plate_number": session.plate_number,
            "parking_amount": round(float(payment.amount) - debt_amount, 2),
            "debt_amount": debt_amount, "debt_count": len(debts),
            "debts": debt_items(db, getattr(payment, "_debt_comps", None) or [])}


@router.post("/payments/{payment_id}/cancel")
def cancel_payment_intent(payment_id: str, body: dict, db: Session = Depends(get_db),
                          partner: PartnerAuth = Depends(require_partner)):
    """Release an unpaid intent only after the partner confirms no debit occurred."""
    from .payments_router import _lock_payment
    _require_pay(partner)
    payment = db.get(Payment, payment_id)
    if not payment:
        raise HTTPException(404, "Payment олдсонгүй")
    _check_payment_scope(db, partner, payment)
    if body.get("outcome") != "NOT_CHARGED":
        raise HTTPException(422, "Wallet талаас мөнгө татаагүйг NOT_CHARGED гэж батална уу")
    payment = _lock_payment(db, payment.id)
    if payment is None:
        raise HTTPException(409, "Төлбөр боловсруулагдаж байна")
    if payment.status == "PAID":
        raise HTTPException(409, "Төлөгдсөн төлбөрийг intent цуцлалтаар өөрчлөхгүй")
    payment.status = "CANCELLED"
    db.add(AuditLog(username=f"partner:{partner}", action="WALLET_INTENT_CANCELLED",
                    entity="payment", entity_id=payment.id,
                    detail={"outcome": "NOT_CHARGED"}))
    db.commit()
    return {"payment_id": payment.id, "status": payment.status}


@router.post("/payments/{payment_id}/confirm")
async def confirm_payment(payment_id: str, body: dict, db: Session = Depends(get_db),
                          partner: PartnerAuth = Depends(require_partner)):
    """Wallet өөрийн талд төлбөрийг амжилттай авсныг баталгаажуулна.
    body: {transaction_id, amount?}. Дүн зөрвөл татгалзана (буруу дүнгээр хаалт
    нээгдэхгүй). `amount` талбар ОГТ байхгүй бол intent-ийн дүнгээр баталгаажуулна
    (SocialPay 2026-10-13: зөвхөн transaction_id илгээж 422 авч байв); `amount`
    ирсэн ч буруу/null бол 422 хэвээр. Idempotent — давхар дуудахад алдаа өгөхгүй PAID буцаана."""
    from .payments_router import _finalize_paid, _lock_payment, payment_outcome
    _require_pay(partner)
    payment = db.get(Payment, payment_id)
    if not payment:
        raise HTTPException(404, "Payment олдсонгүй")
    _check_payment_scope(db, partner, payment)
    from ..services.payment_validation import money, transaction_reference, claim_reference
    amount_defaulted = "amount" not in body
    paid_amount = None if amount_defaulted else money(body.get("amount"))
    reference = transaction_reference(body.get("transaction_id"))
    # Мөрийг түгжинэ — wallet-ийн давхар confirm/retry зэрэг ирвэл нэг нь л finalize хийнэ
    payment = _lock_payment(db, payment.id)
    if payment is None:
        raise HTTPException(409, "Төлбөр боловсруулагдаж байна — дахин оролдоно уу")
    if amount_defaulted:
        paid_amount = money(payment.amount)
    if payment.status == "PAID":
        if payment.provider_payment_id != reference or paid_amount != money(payment.amount):
            raise HTTPException(409, "Өмнөх баталгаажуулалтын дугаар эсвэл дүн зөрлөө")
        return {"status": "PAID", "payment_id": payment.id, **payment_outcome(db, payment)}
    if payment.status not in ("PENDING", "CANCELLED", "UNKNOWN", "REVIEW"):
        raise HTTPException(400, f"Төлбөрийн төлөв буруу: {payment.status}")
    if paid_amount != money(payment.amount):
        raise HTTPException(400, f"Дүн зөрүүтэй: систем {float(payment.amount)}₮ хүлээж байна")

    claim_reference(db, payment, partner.key_id or f"legacy:{partner}", reference)
    hook = _webhook_url_for(db, partner)
    await _finalize_paid(db, payment, raw={"partner": partner, **{
        k: v for k, v in body.items() if k in ("transaction_id", "amount", "wallet_user")},
        **({"amount_defaulted": True} if amount_defaulted else {})},
        partner_notification=(hook, partner) if hook else None)
    db.add(AuditLog(username=f"partner:{partner}", action="WALLET_PAID", entity="payment",
                    entity_id=payment.id, detail={"transaction_id": payment.provider_payment_id,
                                                  "amount": float(payment.amount),
                                                  **({"amount_defaulted": True} if amount_defaulted else {})}))
    db.commit()
    # The webhook intent was committed with settlement, before opening a gate.
    return {"status": "PAID", "payment_id": payment.id, "paid_at":
            payment.paid_at.isoformat() if payment.paid_at else datetime.utcnow().isoformat(),
            **payment_breakdown(db, payment),
            "ebarimt": receipt_info(db, payment), "ebarimts": receipt_list(db, payment),
            **payment_outcome(db, payment)}


@router.get("/payments/{payment_id}")
async def payment_status(payment_id: str, db: Session = Depends(get_db),
                         partner: PartnerAuth = Depends(require_partner)):
    from .payments_router import payment_outcome
    payment = db.get(Payment, payment_id)
    if not payment or payment.provider != partner:
        raise HTTPException(404, "Payment олдсонгүй")
    _check_payment_scope(db, partner, payment)
    # QR санах ойд алга (1 цаг өнгөрсөн/restart) бол msgbill-ээс нөхнө (2026-10-22)
    qr = await fetch_missing_qr(db, payment) if payment.status == "PAID" else {}
    latest, rows = receipt_info(db, payment), receipt_list(db, payment, qr)
    if latest is not None and not latest.get("qr_data") and latest.get("ddtd"):
        latest["qr_data"] = next((r["qr_data"] for r in rows if r["ddtd"] == latest["ddtd"]), None)
    return {"payment_id": payment.id, "status": payment.status,
            "amount": float(payment.amount),
            "paid_at": payment.paid_at.isoformat() if payment.paid_at else None,
            **payment_breakdown(db, payment),
            # e-Barimt (ДДТД, сугалаа, QR) — PAID болсны дараа; FAILED бол шалтгаан.
            # `ebarimt` = хамгийн сүүлийн баримт (хуучин), `ebarimts` = БҮХ баримт (зогсолт + өр)
            "ebarimt": latest, "ebarimts": rows,
            **payment_outcome(db, payment)}
