"""EV цэнэглэлтийн тооцоо — Wh → ₮, hold/release/settle (EV_CHARGING_PLAN.md).

Бүх дүн БҮХЭЛ Wh дээр (§2): 1 кВт.ц = 1000₮ → 1 Wh = 1₮. Float ҮГҮЙ.
Үнэ session эхлэхэд түгжигдэнэ (price_per_wh) — дунд нь тариф солигдох
жолоочид нөлөөлөхгүй.

Урсгал (§6.1):
  start_charge()      физик шалгалт → CHARGE_HOLD → RemoteStart команд
  on_tx_started()     ocpp_tx_id холбох, SetChargingProfile (§6.4/2)
  on_meter()          амьд явц + watchdog 98% (§6.4/1)
  on_tx_stopped()     бодит дүн → CHARGE_RELEASE зөрүү → settle → Payment(EV)
  expire_stale()      90с-д эхлээгүй бол hold бүрэн буцаана (§6.4)
"""
import logging
import uuid
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from ..config import settings
from ..models import ChargeSession, EvCharger, EvPricePlan, Payment, Wallet
from ..session_logic import normalize_plate
from . import ev_hub
from . import wallet as wallet_svc

log = logging.getLogger("parking.ev_billing")

D = Decimal

# Физик нотолгоо (§1.2): эдгээр төлөвт л цэнэглэлт эхлүүлж болно
STARTABLE_STATUSES = {"Preparing", "SuspendedEV"}


class EvError(Exception):
    pass


def default_plan(db: Session, charger: EvCharger) -> EvPricePlan:
    plan = charger.price_plan
    if plan and plan.is_active:
        return plan
    plan = (db.query(EvPricePlan)
            .filter(EvPricePlan.is_active.is_(True))
            .order_by(EvPricePlan.created_at).first())
    if not plan:
        # Анхны суулгацад default тариф автоматаар (1 Wh = 1₮)
        plan = EvPricePlan(name="Үндсэн (1 Wh = 1₮)", price_per_wh=1)
        db.add(plan)
        db.flush()
    return plan


def price_per_wh_at(plan: EvPricePlan, at: datetime | None = None) -> Decimal:
    """Цагийн бүсчлэлтэй үнэ (§2): night_* тохируулсан бол шөнийн цагт
    хямд. at нь UTC; УБ (UTC+8) цагаар харьцуулна."""
    p = D(str(plan.price_per_wh or 1))
    if not (plan.night_price_per_wh and plan.night_from and plan.night_to):
        return p
    local = (at or datetime.utcnow()) + timedelta(hours=8)
    try:
        fh, fm = (int(x) for x in plan.night_from.split(":"))
        th, tm = (int(x) for x in plan.night_to.split(":"))
    except (ValueError, AttributeError):
        return p
    minutes = local.hour * 60 + local.minute
    start, end = fh * 60 + fm, th * 60 + tm
    in_night = (start <= minutes < end) if start < end else (minutes >= start or minutes < end)
    return D(str(plan.night_price_per_wh)) if in_night else p


def wh_limit_for(amount, price_per_wh: Decimal) -> int:
    """20,000₮ / 1₮ → 20,000 Wh. Бүхэл тоо, дугуйлалтгүй (§2)."""
    return int(D(str(amount)) // price_per_wh)


def energy_amount_for(energy_wh: int, price_per_wh: Decimal) -> Decimal:
    return (D(int(energy_wh)) * price_per_wh).quantize(D("0.01"))


async def start_charge(db: Session, charger: EvCharger, connector_id: int,
                       plate: str, phone: str, amount) -> ChargeSession:
    """§6.1 алхам 5. Нэг транзакцид: физик шалгалт → hold → session →
    RemoteStart команд. Аль нэг алхам унавал бүгд rollback."""
    plan = default_plan(db, charger)
    amt = D(str(amount)).quantize(D("0.01"))
    if amt < D(str(plan.min_amount or 0)):
        raise EvError(f"Доод дүн {plan.min_amount}₮")
    if plan.max_amount and amt > D(str(plan.max_amount)):
        raise EvError(f"Дээд дүн {plan.max_amount}₮")

    # ── Физик шалгалт (§1.2): бууц үнэхээр залгагдсан байх ёстой ──
    try:
        conn = await ev_hub.connector_status(charger.cp_id, connector_id)
    except ev_hub.HubError as e:
        raise EvError(f"Цэнэглэгчтэй холбогдож чадсангүй: {e}") from e
    if not conn.get("online"):
        raise EvError("Цэнэглэгч офлайн байна")
    if conn.get("status") not in STARTABLE_STATUSES:
        raise EvError("Эхлээд цэнэглэгчийн буужийг машиндаа залгана уу "
                      f"(төлөв: {conn.get('status')})")
    if conn.get("active_tx_id"):
        raise EvError("Энэ бууц дээр цэнэглэлт явагдаж байна")

    # ── Данс + hold (FOR UPDATE нэг транзакцид) ──
    tenant_id = charger.site.tenant_id if charger.site else None
    w = wallet_svc.get_or_create(db, tenant_id, plate, phone)
    price = price_per_wh_at(plan)
    session = ChargeSession(
        charger_id=charger.id, connector_id=connector_id, wallet_id=w.id,
        plate_number=normalize_plate(plate), phone=wallet_svc.normalize_phone(phone),
        id_tag=uuid.uuid4().hex[:20], authorized_amount=amt,
        price_per_wh=price, wh_limit=wh_limit_for(amt, price),
        status="PENDING_START",
    )
    db.add(session)
    db.flush()
    wallet_svc.hold_for_charge(db, w.id, amt, session.id)

    # ── RemoteStart — команд дараалалд орсны дараа commit ──
    try:
        await ev_hub.remote_start(charger.cp_id, connector_id, session.id_tag)
    except ev_hub.HubError as e:
        db.rollback()
        raise EvError(f"Команд илгээгдсэнгүй: {e}") from e
    db.commit()
    db.refresh(session)
    log.info("EV эхлүүлэв: %s conn=%s %s₮ (%s Wh) session=%s",
             charger.cp_id, connector_id, amt, session.wh_limit, session.id)
    return session


def _find_by_idtag(db: Session, id_tag: str) -> ChargeSession | None:
    return (db.query(ChargeSession)
            .filter(ChargeSession.id_tag == id_tag)
            .with_for_update(of=ChargeSession).first())


def _find_by_txid(db: Session, ocpp_tx_id: int) -> ChargeSession | None:
    return (db.query(ChargeSession)
            .filter(ChargeSession.ocpp_tx_id == int(ocpp_tx_id))
            .with_for_update(of=ChargeSession).first())


async def on_tx_started(db: Session, payload: dict):
    """hub → ev.tx.started. id_tag-аар session олж ocpp_tx_id холбоно."""
    id_tag = str(payload.get("id_tag") or "")
    session = _find_by_idtag(db, id_tag)
    if not session:
        log.warning("ev.tx.started: үл мэдэх id_tag=%s (HMI-аас гараар эхэлсэн "
                    "цэнэглэлт байж болно — мөнгө тооцохгүй)", id_tag)
        return
    if session.status not in ("PENDING_START",):
        log.info("ev.tx.started: session %s аль хэдийн %s", session.id, session.status)
        if session.ocpp_tx_id is None:
            session.ocpp_tx_id = payload.get("ocpp_tx_id")
        db.commit()
        return
    session.ocpp_tx_id = payload.get("ocpp_tx_id")
    session.meter_start_wh = payload.get("meter_start_wh")
    session.started_at = datetime.utcnow()
    session.status = "RUNNING"
    db.commit()
    # §6.4 хамгаалалт 2: charging profile — сүлжээ тасарсан ч өөрөө зогсоно.
    # 40 кВт дээр wh_limit-ээ авахад шаардлагатай хугацаа × 1.3
    try:
        charger = db.get(EvCharger, session.charger_id)
        duration = int(session.wh_limit / 40000 * 3600 * 1.3) + 60
        await ev_hub.set_charging_profile(
            charger.cp_id, session.connector_id, int(session.ocpp_tx_id),
            limit_w=45000, duration_sec=duration)
    except ev_hub.HubError as e:
        log.warning("SetChargingProfile илгээгдсэнгүй (watchdog хэвээр): %s", e)


async def on_meter(db: Session, payload: dict):
    """hub → ev.meter. Амьд явц + watchdog (§6.4/1): 98% дээр зогсоох."""
    tx_id = payload.get("ocpp_tx_id")
    if tx_id is None:
        return
    session = _find_by_txid(db, tx_id)
    if not session or session.status != "RUNNING":
        return
    energy = int(payload.get("energy_wh") or 0)
    session.last_energy_wh = energy
    if payload.get("soc") is not None:
        session.last_soc = payload["soc"]
        if session.soc_start is None:
            session.soc_start = payload["soc"]
    if payload.get("power_w") is not None:
        p = D(str(payload["power_w"]))
        if session.max_power_w is None or p > D(str(session.max_power_w)):
            session.max_power_w = p
    need_stop = (not session.watchdog_stop_sent and
                 energy >= session.wh_limit * settings.ev_watchdog_ratio)
    if need_stop:
        session.watchdog_stop_sent = True
    db.commit()
    if need_stop:
        try:
            charger = db.get(EvCharger, session.charger_id)
            await ev_hub.remote_stop(charger.cp_id, int(tx_id))
            log.info("watchdog: %s Wh / %s Wh — RemoteStop илгээв (session %s)",
                     energy, session.wh_limit, session.id)
        except ev_hub.HubError as e:
            # Дараагийн meter дээр дахин оролдоно
            log.warning("watchdog RemoteStop илгээгдсэнгүй: %s", e)
            session.watchdog_stop_sent = False
            db.commit()


async def on_tx_stopped(db: Session, payload: dict):
    """hub → ev.tx.stopped. Бодит дүн, release, settle, Payment(EV) (§6.1/8).

    Idempotent: STOPPED/SETTLED session-д дахин ажиллахгүй (офлайн давхардал §6.5).
    ЖОЛООЧООС ХЭЗЭЭ Ч ИЛҮҮ НЭХЭХГҮЙ: төлбөр = min(бодит, authorized_amount)."""
    tx_id = payload.get("ocpp_tx_id")
    if tx_id is None or payload.get("unknown"):
        return
    session = _find_by_txid(db, tx_id)
    if not session:
        log.warning("ev.tx.stopped: core-д session алга tx=%s (HMI гар цэнэглэлт?)", tx_id)
        return
    if session.status in ("STOPPED", "SETTLED", "CANCELLED"):
        return  # давхар event — idempotent
    energy = payload.get("energy_wh")
    if energy is None and payload.get("meter_stop_wh") is not None \
            and session.meter_start_wh is not None:
        energy = max(0, int(payload["meter_stop_wh"]) - int(session.meter_start_wh))
    energy = int(energy or 0)
    price = D(str(session.price_per_wh))
    raw_amount = energy_amount_for(energy, price)
    authorized = D(str(session.authorized_amount))
    # Хэтрэлтийг систем дааж, жолоочид илүү нэхэмжлэхгүй (§6.4)
    actual = min(raw_amount, authorized)
    if raw_amount > authorized:
        log.warning("session %s: бодит %s₮ > зөвшөөрсөн %s₮ — зөрүүг систем даана",
                    session.id, raw_amount, authorized)
    session.meter_stop_wh = payload.get("meter_stop_wh")
    session.energy_wh = energy
    session.stop_reason = payload.get("stop_reason") or ""
    session.stopped_at = datetime.utcnow()
    if payload.get("soc_end") is not None:
        session.soc_end = payload["soc_end"]
    session.energy_amount = actual
    session.total_amount = actual
    session.status = "STOPPED"

    # ── Данс: зөрүү буцаах + settle тэмдэглэгээ (нэг транзакц) ──
    release = authorized - actual
    w = wallet_svc.lock_wallet(db, session.wallet_id)
    if release > 0:
        wallet_svc.apply_ledger(db, w, "CREDIT", release, "CHARGE_RELEASE",
                                ref_type="charge_session", ref_id=session.id,
                                note=f"бодит {actual}₮, зөрүү буцаав")
    wallet_svc.settle_charge_marker(db, w, session.id, actual)

    # ── Орлогын бүртгэл: Payment(kind=EV, PAID) — тайлан/ээлжид харагдана ──
    if actual > 0:
        payment = Payment(
            session_id=None, kind="EV", wallet_id=session.wallet_id,
            provider="WALLET", payment_method="WALLET", source="EV",
            sender_invoice_no=f"EV-{session.id[:8]}-{session.ocpp_tx_id}",
            amount=actual, vat_amount=_vat_of(actual), status="PAID",
            paid_at=datetime.utcnow(),
            raw_payload={"charge_session_id": session.id,
                         "energy_wh": energy, "price_per_wh": str(price)},
        )
        db.add(payment)
        db.flush()
        session.payment_id = payment.id
    session.status = "SETTLED"
    db.commit()
    log.info("EV дуусав: session=%s %s Wh × %s = %s₮ (буцаалт %s₮), үлдэгдэл %s₮",
             session.id, energy, price, actual, release, w.balance)
    # e-Barimt (§Шат 4): бодит дүнгээр, best-effort — унасан ч тооцоо алдагдахгүй.
    if actual > 0 and session.payment_id:
        try:
            await _ebarimt_for_charge(db, session)
        except Exception as e:  # noqa: BLE001
            log.warning("EV e-Barimt үүсгэж чадсангүй (дараа retry болно): %s", e)


def _vat_of(amount: Decimal) -> Decimal:
    """НӨАТ үнэд багтсан горим: vat = total × r/(1+r) (billing.py-тэй ижил)."""
    r = D(str(settings.vat_rate))
    if not settings.vat_inclusive or r <= 0:
        return D(0)
    return (amount * r / (1 + r)).quantize(D("0.01"))


async def _ebarimt_for_charge(db: Session, session: ChargeSession):
    """e-Barimt: msgbill идэвхтэй бол «Үйлчилгээ» төрлөөр, Idempotency-Key =
    session id (§Шат 4). Тохируулаагүй бол алгасна — vat_receipts-т PENDING
    үлдэхгүй, учир нь Payment.kind=EV тайланд НӨАТ-аа тусад нь харуулна."""
    from ..models import VatReceipt
    from . import msgbill
    charger = db.get(EvCharger, session.charger_id)
    site = charger.site if charger else None
    acc = msgbill.account_enabled_for(site, "WALLET") if site else None
    if not acc:
        return
    payment = db.get(Payment, session.payment_id)
    norm = await msgbill.create_receipt(
        acc, float(session.total_amount),
        description=f"Цахилгаан цэнэглэлт {session.energy_wh} Wh",
        payment_method="QR",  # дансны мөнгө анх QPay QR-аар орж ирсэн
        idempotency_key=f"ev-{session.id}")
    ok = norm.get("status") == "SUCCESS"
    receipt = VatReceipt(
        payment_id=payment.id, session_id=session.parking_session_id,
        ebarimt_id=norm.get("billId"), lottery_code=norm.get("lottery"),
        amount=session.total_amount, vat_amount=payment.vat_amount,
        receipt_url=norm.get("qrData"), status="SENT" if ok else "PENDING",
        provider="MSGBILL", provider_ref=norm.get("msgbillId"))
    db.add(receipt)
    db.flush()
    session.vat_receipt_id = receipt.id
    db.commit()


async def expire_stale_starts(db: Session):
    """§6.4: RemoteStart-аас хойш ev_start_timeout_sec дотор StartTransaction
    ирээгүй PENDING_START session-уудын hold-ыг БҮРЭН буцаана."""
    cutoff = datetime.utcnow() - timedelta(seconds=settings.ev_start_timeout_sec)
    stale = (db.query(ChargeSession)
             .filter(ChargeSession.status == "PENDING_START",
                     ChargeSession.created_at < cutoff)
             .with_for_update(skip_locked=True, of=ChargeSession).all())
    for s in stale:
        s.status = "CANCELLED"
        s.stop_reason = "start_timeout"
        wallet_svc.release_hold(db, s.wallet_id, s.authorized_amount, s.id,
                                note="цэнэглэлт эхлээгүй — hold буцаав")
        db.commit()
        log.info("EV эхлээгүй: session %s — %s₮ hold буцаав",
                 s.id, s.authorized_amount)


async def stale_start_supervisor():
    """Background: 15 секунд тутам гацсан PENDING_START-уудыг цэвэрлэнэ."""
    import asyncio
    from ..database import SessionLocal
    while True:
        try:
            db = SessionLocal()
            try:
                await expire_stale_starts(db)
            finally:
                db.close()
        except Exception:  # noqa: BLE001
            log.exception("expire_stale_starts алдаа")
        await asyncio.sleep(15)
