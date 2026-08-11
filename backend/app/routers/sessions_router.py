"""Session удирдлага: жагсаалт, хайлт, шалгах, түүх, гараар хаах."""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..auth import (enforce_site, get_current_user, operator_site, operator_sites,
                    require, require_role, scoped_site)
from ..database import get_db
from ..services.device_auth import camera_credentials
from ..models import (AuditLog, Compensation, Device, LprEvent, ParkingSession, Payment, User)
from ..serializers import to_dict
from ..session_logic import (close_session_forced, get_open_session, normalize_plate,
                             session_fee_info)
from ..services.barrier import open_barrier
from ..ws import manager

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


def _session_out(db: Session, s: ParkingSession, with_fee: bool = False) -> dict:
    extra = {"site_name": s.site.name if s.site else None,
             "discount_name": s.discount.name if s.discount else None}
    if with_fee and s.status in ("OPEN", "AWAITING_PAYMENT"):
        extra["fee"] = session_fee_info(db, s)
    return to_dict(s, extra=extra)


def _attach_debt(db: Session, dicts: list[dict]) -> list[dict]:
    """Дугаар бүрийн ТӨЛӨГДӨӨГҮЙ нөхөн төлбөрийг (аль ч зогсоолын) хавсаргана.
    Өр нь тусдаа `compensations` санд хадгалагддаг тул зогсоолоос үл хамааран харагдана."""
    plates = {d["plate_number"] for d in dicts if d.get("plate_number")}
    if not plates:
        return dicts
    debt = {plate: {"amount": float(amt), "count": cnt} for plate, amt, cnt in
            db.query(Compensation.plate_number, func.sum(Compensation.amount), func.count())
            .filter(Compensation.plate_number.in_(plates), Compensation.status == "PENDING")
            .group_by(Compensation.plate_number).all()}
    for d in dicts:
        d["debt"] = debt.get(d["plate_number"])
    return dicts


# Session-ийг ХААСАН үйлдлүүд — Түүх дээр «хэн/юугаар хаасан» гэдгийг гаргана.
# Гараар хаасныг операторын нэрээр, автоматыг «систем» гэж ялгаж харуулна.
_CLOSE_ACTIONS = ("ADMIN_REMOVE", "MANUAL_EXIT", "AUTO_CLOSE", "AUTO_FREE_CLOSE",
                  "AUTO_JUNK_CLOSE")
_CLOSE_LABEL = {
    "ADMIN_REMOVE": "Зогсоолоос хассан",
    "MANUAL_EXIT": "Гараар гаргасан",
    "AUTO_CLOSE": "Авто: хугацаа хэтэрсэн",
    "AUTO_FREE_CLOSE": "Авто: үнэгүй хаасан",
    "AUTO_JUNK_CLOSE": "Авто: буруу дугаар",
}


def _attach_close_info(db: Session, dicts: list[dict]) -> list[dict]:
    """Түүхэнд ХЭРХЭН хаагдсаныг хавсаргана — «Гарсан» төлөв хэт ерөнхий байсныг задлана.

      • `payments` — ямар хэрэгслээр төлөгдсөн (QPay QR / карт / бэлэн / данс),
        кассаар төлсөн бол хүлээж авсан операторын нэртэй.
      • `closed_by` — гараар/автоматаар хаасан бол хэн, ямар үйлдлээр.

    Бүх мэдээллийг ХОЁР багц query-ээр авна (мөр бүрд query хийхгүй) — Түүх нэг
    хуудсанд 50-500 мөр харуулдаг тул N+1 болбол хуудас нээгдэхээ болино.
    """
    ids = [d["id"] for d in dicts if d.get("id")]
    if not ids:
        return dicts

    pays: dict[str, list] = {}
    rows = (db.query(Payment.session_id, Payment.provider, Payment.payment_method,
                     Payment.source, Payment.amount, Payment.paid_at, User.username)
            .outerjoin(User, User.id == Payment.cashier_id)
            .filter(Payment.session_id.in_(ids), Payment.status == "PAID")
            .order_by(Payment.paid_at).all())
    for sid, provider, method, source, amount, paid_at, cashier in rows:
        pays.setdefault(sid, []).append({
            "provider": provider, "method": method, "source": source,
            "amount": float(amount or 0),
            "paid_at": paid_at.isoformat() if paid_at else None,
            "cashier": cashier,
        })

    closed: dict[str, dict] = {}
    # created_at өсөх дарааллаар — нэг session олон удаа хаагдсан бол СҮҮЛИЙНХ үлдэнэ
    for eid, username, action, at in (
            db.query(AuditLog.entity_id, AuditLog.username, AuditLog.action, AuditLog.created_at)
            .filter(AuditLog.entity == "session", AuditLog.entity_id.in_(ids),
                    AuditLog.action.in_(_CLOSE_ACTIONS))
            .order_by(AuditLog.created_at).all()):
        closed[eid] = {"by": username, "action": action,
                       "label": _CLOSE_LABEL.get(action, action),
                       "auto": username == "system",
                       "at": at.isoformat() if at else None}

    for d in dicts:
        d["payments"] = pays.get(d["id"], [])
        d["closed_by"] = closed.get(d["id"])
    return dicts


@router.get("")
def list_sessions(
    site_id: str | None = None, status: str | None = None, plate: str | None = None,
    date_from: str | None = None, date_to: str | None = None,
    limit: int = 100, offset: int = 0, with_fee: bool = False, inner: bool = False,
    db: Session = Depends(get_db), user: User = Depends(require("history", "cashier", "check")),
):
    site_id, site_ids = scoped_site(user, site_id)  # оператор зөвхөн өөрийн зогсоолууд
    q = db.query(ParkingSession)
    if inner:
        # Зөвхөн ОДОО доторх (nested) зогсоолд байгаа гэж тооцогдож буй машинууд
        q = q.filter(ParkingSession.paused_since.isnot(None))
    if site_id:
        q = q.filter(ParkingSession.site_id == site_id)
    elif site_ids:
        q = q.filter(ParkingSession.site_id.in_(site_ids))
    if status:
        q = q.filter(ParkingSession.status.in_(status.split(",")))
    if plate:
        q = q.filter(ParkingSession.plate_number.ilike(f"%{normalize_plate(plate)}%"))
    if date_from:
        q = q.filter(ParkingSession.entry_time >= datetime.fromisoformat(date_from))
    if date_to:
        q = q.filter(ParkingSession.entry_time < datetime.fromisoformat(date_to) + timedelta(days=1))
    total = q.count()
    rows = q.order_by(ParkingSession.entry_time.desc()).offset(offset).limit(min(limit, 500)).all()
    out = _attach_debt(db, [_session_out(db, s, with_fee=with_fee) for s in rows])
    return {"total": total, "rows": _attach_close_info(db, out)}


@router.get("/check")
def check_plate(plate: str, site_id: str | None = None,
                db: Session = Depends(get_db), user: User = Depends(require("check", "cashier"))):
    """Шалгах/касс: дугаарын ЭХНИЙ хэсгээр нээлттэй session хайна (live хайлт, 2+ тэмдэгт)."""
    plate = normalize_plate(plate)
    if len(plate) < 2:
        return []
    site_id, site_ids = scoped_site(user, site_id)  # оператор зөвхөн өөрийн зогсоолууд
    q = db.query(ParkingSession).filter(
        ParkingSession.plate_number.ilike(f"{plate}%"),
        ParkingSession.status.in_(["OPEN", "AWAITING_PAYMENT", "PAID"]),
    )
    if site_id:
        q = q.filter(ParkingSession.site_id == site_id)
    elif site_ids:
        q = q.filter(ParkingSession.site_id.in_(site_ids))
    sessions = q.order_by(ParkingSession.updated_at.desc()).limit(10).all()
    return _attach_debt(db, [_session_out(db, s, with_fee=True) for s in sessions])


@router.get("/audit")
def audit_sessions(site_id: str | None = None, camera: bool = False,
                   db: Session = Depends(get_db),
                   user: User = Depends(require_role("ADMIN", "SUPER_ADMIN"))):
    """Зогсоолын тоог тулгах аудит: "зогсоолд байгаа" гэж тоологдож буй бүх бүртгэлийг
    сэжигтэй шинжээр тэмдэглэж буцаана. Ингэснээр гарах камерт уншигдсан хэрнээ
    хаагдаагүй, буруу форматтай (junk) дугаар, эсвэл удаан гацсан phantom-уудыг
    ялган нэг товчоор цэвэрлэх боломжтой.

    Тэмдэг (flags):
      • exit_read    — орсны дараа ГАРАХ камерт уншигдсан (серверийн LPR лог)
      • invalid_plate — дугаар стандарт формат биш (4 орон + 3 кирилл үсэг биш)
      • stale        — auto_close босгоос удаан зогссон
      • cam_exit_read — КАМЕРЫН ДОТООД логоос: орсны дараа гарах камераар өнгөрсөн
        (сервер унтарсан/event алдсан үеийг ч барина)
      • ocr_similar  — камерын логт яг ижил дугаар алга, харин 1 тэмдэгтийн
        зөрүүтэй дугаар бий — OCR буруу уншилт байх магадлалтай

    camera=true (зөвхөн site_id өгсөн үед) — тухайн зогсоолын бүх идэвхтэй
    камерын дотоод event сангаас (сүүлийн 48ц, 60с кэштэй) татаж тулгана.
    """
    from ..config import settings
    from ..session_logic import is_valid_plate
    site_id, site_ids = scoped_site(user, site_id)  # оператор зөвхөн өөрийн зогсоолууд
    q = db.query(ParkingSession).filter(
        ParkingSession.status.in_(["OPEN", "AWAITING_PAYMENT", "PAID"]))
    if site_id:
        q = q.filter(ParkingSession.site_id == site_id)
    elif site_ids:
        q = q.filter(ParkingSession.site_id.in_(site_ids))
    rows = q.order_by(ParkingSession.entry_time.asc()).limit(500).all()
    now = datetime.utcnow()

    # Орсны дараа гарах камерт уншигдсан эсэх — дугаар бүрийн сүүлийн exit event
    plates = {s.plate_number for s in rows}
    last_exit: dict[str, datetime] = {}
    if plates:
        for pl, ts in (db.query(LprEvent.plate_number, func.max(LprEvent.created_at))
                       .filter(LprEvent.plate_number.in_(plates),
                               LprEvent.lane_dir == "exit", LprEvent.accepted.is_(True))
                       .group_by(LprEvent.plate_number).all()):
            last_exit[pl] = ts

    # ── Камерын дотоод логтой тулгах (зөвхөн нэг зогсоол сонгосон үед) ──
    cam_info = None
    cam_events: list[dict] = []
    if camera and site_id:
        from ..services.camera_records import site_camera_events
        try:
            cam_data = site_camera_events(db, site_id)
            cam_events = cam_data["events"]
            cam_info = {"window_hours": cam_data["window_hours"],
                        "cameras": cam_data["cameras"], "error": None}
        except Exception as e:  # noqa: BLE001 — камер унасан ч аудит ажиллана
            cam_info = {"window_hours": None, "cameras": [], "error": str(e)[:200]}

    out = []
    session_plates = {s.plate_number for s in rows}
    for s in rows:
        hours = round((now - s.entry_time).total_seconds() / 3600, 1) if s.entry_time else 0.0
        ex = last_exit.get(s.plate_number)
        exit_read = bool(ex and s.entry_time and ex > s.entry_time)
        invalid = not is_valid_plate(s.plate_number)
        limit_h = (s.site.auto_close_hours if s.site and s.site.auto_close_hours is not None
                   else settings.auto_close_hours)
        stale = bool(limit_h and hours >= limit_h)

        # Камерын лог: орсны ДАРАА гарах камераар өнгөрсөн үү (сервер event
        # алдсан байсан ч камерын дотоод сан үүнийг мэднэ)
        cam_exit_at = None
        cam_exact = False
        cam_similar: list[dict] = []
        if cam_events:
            from ..services.camera_records import plates_similar
            for ev in cam_events:
                if ev["plate"] == s.plate_number:
                    cam_exact = True
                    if (ev["lane_dir"] == "exit" and s.entry_time
                            and ev["time"] > s.entry_time
                            and (cam_exit_at is None or ev["time"] > cam_exit_at)):
                        cam_exit_at = ev["time"]
            if not cam_exact:
                seen = set()
                for ev in cam_events:
                    p = ev["plate"]
                    if p and p not in seen and p not in session_plates \
                            and plates_similar(p, s.plate_number):
                        seen.add(p)
                        cam_similar.append({"plate": p, "at": ev["time"].isoformat(),
                                            "lane_dir": ev["lane_dir"]})
                cam_similar = cam_similar[:3]
        cam_exit_read = cam_exit_at is not None
        ocr_similar = bool(cam_similar)

        flags = ([f for f, on in (("exit_read", exit_read), ("invalid_plate", invalid),
                                  ("stale", stale), ("cam_exit_read", cam_exit_read),
                                  ("ocr_similar", ocr_similar)) if on])
        d = _session_out(db, s, with_fee=True)
        d["audit"] = {"hours_parked": hours, "exit_read": exit_read,
                      "exit_read_at": ex.isoformat() if ex else None,
                      "invalid_plate": invalid, "stale": stale,
                      "cam_exit_read": cam_exit_read,
                      "cam_exit_at": cam_exit_at.isoformat() if cam_exit_at else None,
                      "ocr_similar": ocr_similar, "cam_similar": cam_similar,
                      "flags": flags, "suspect": bool(flags)}
        out.append(d)

    # Камерын логт байгаа ч СИСТЕМД БҮРТГЭЛГҮЙ оролтууд (сервер унтарсан үеийн
    # алдагдсан event) — мужид орсон бүх session-ий дугаартай (хаагдсаныг оролцуулаад)
    # тулгана
    if cam_info and cam_events:
        wstart = now - timedelta(hours=cam_info["window_hours"] or 48)
        # ИДЭВХТЭЙ бүртгэлтэй машиныг ямар ч тохиолдолд «бүртгэлгүй» гэж
        # тэмдэглэхгүй — мужаас өмнө орсон ч зогсоолд байгаа тул алдагдаагүй
        known = {p for (p,) in db.query(ParkingSession.plate_number)
                 .filter(ParkingSession.site_id == site_id,
                         ParkingSession.entry_time >= wstart).all()}
        known |= {p for (p,) in db.query(ParkingSession.plate_number)
                  .filter(ParkingSession.site_id == site_id,
                          ParkingSession.status.in_(["OPEN", "AWAITING_PAYMENT", "PAID"])).all()}
        # Гарах камерын уншилтууд дугаараар — орсон машин ГАРСАН эсэхийг тулгана
        exits_by_plate: dict[str, list] = {}
        for ev in cam_events:
            if ev["plate"] and ev["lane_dir"] == "exit":
                exits_by_plate.setdefault(ev["plate"], []).append(ev["time"])
        for lst in exits_by_plate.values():
            lst.sort()

        raw_unmatched = [ev for ev in cam_events
                         if ev["plate"] and ev["lane_dir"] == "entry"
                         and ev["plate"] not in known]
        raw_unmatched.sort(key=lambda e: (e["plate"], e["time"]))
        # Дараалсан давхар уншилтыг (burst) нэгтгэнэ — камер нэг машиныг
        # хэдэн секундын зайтай 2-3 удаа уншдаг
        unmatched = []
        for ev in raw_unmatched:
            if (unmatched and unmatched[-1]["plate"] == ev["plate"]
                    and (ev["time"] - unmatched[-1]["time"]).total_seconds() < 600):
                continue
            unmatched.append(ev)

        out_rows = []
        for ev in unmatched:
            ex = next((t for t in exits_by_plate.get(ev["plate"], []) if t > ev["time"]), None)
            out_rows.append({
                "plate": ev["plate"], "at": ev["time"].isoformat(), "camera": ev["camera"],
                "exit_at": ex.isoformat() if ex else None,
                "hours": round((ex - ev["time"]).total_seconds() / 3600, 1) if ex else None,
            })
        out_rows.sort(key=lambda r: r["at"], reverse=True)
        cam_info["unmatched_total"] = len(out_rows)
        cam_info["unmatched_exited"] = sum(1 for r in out_rows if r["exit_at"])
        cam_info["unmatched"] = out_rows[:60]

    out = _attach_debt(db, out)
    resp = {"total": len(out), "suspect": sum(1 for d in out if d["audit"]["suspect"]),
            "rows": out}
    if cam_info is not None:
        resp["camera"] = cam_info
    return resp


@router.get("/recent-exits")
def recent_exits(site_id: str, minutes: int | None = None,
                 db: Session = Depends(get_db), user: User = Depends(require("cashier"))):
    """Касс/PAX: сүүлд гарах камерт уншигдсан, төлбөр хүлээж буй машинууд.

    Гарах уншилтаас хойш `exit_queue_show_min` (default 3) минутын дараа
    жагсаалтаас алга болно — төлөлгүй буцсан машин кассын дэлгэцийг бөглөхгүй
    (түүх/хайлтад хэвээр үлдэнэ, дахин уншигдвал буцаж гарна)."""
    from ..config import settings as _cfg
    allowed = operator_sites(user)
    if allowed and site_id not in allowed:
        site_id = allowed[0]  # оператор зөвхөн өөрийн зогсоолууд
    win = minutes if minutes is not None else getattr(_cfg, "exit_queue_show_min", 3)
    since = datetime.utcnow() - timedelta(minutes=win)
    sessions = (
        db.query(ParkingSession)
        .filter(ParkingSession.site_id == site_id,
                ParkingSession.status == "AWAITING_PAYMENT",
                # Гарах уншилтын цаг (exit_time); хуучин бичлэгт updated_at уналт
                func.coalesce(ParkingSession.exit_time,
                              ParkingSession.updated_at) >= since)
        .order_by(ParkingSession.updated_at.desc()).limit(20).all()
    )
    # Нөхөн төлбөрийн өртэй машиныг касс дээр улаанаар тэмдэглэнэ (JGA спек)
    from ..models import Compensation
    debt_plates = {p for (p,) in db.query(Compensation.plate_number)
                   .filter(Compensation.status == "PENDING").all()}
    return [_session_out(db, s, with_fee=True) | {"has_debt": s.plate_number in debt_plates}
            for s in sessions]


@router.put("/{session_id}/note")
def update_note(session_id: str, body: dict, db: Session = Depends(get_db),
                user: User = Depends(require("cashier", "check"))):
    """Операторын нэмэлт тэмдэглэл хадгална (касс)."""
    s = db.get(ParkingSession, session_id)
    if not s:
        raise HTTPException(404, "Session олдсонгүй")
    enforce_site(user, s.site_id)  # оператор зөвхөн өөрийн зогсоолууд
    s.note = (body.get("note") or "")[:1000]
    db.add(AuditLog(username=user.username, action="SESSION_NOTE", entity="session",
                    entity_id=session_id, detail={"note": s.note[:100]}))
    db.commit()
    return {"ok": True, "note": s.note}


@router.get("/today-exits")
def today_exits(site_id: str, db: Session = Depends(get_db), user: User = Depends(require("cashier"))):
    """Касс: ӨНӨӨДӨР гарах камерт уншигдсан бүх машин (төлбөр аваагүй/үнэгүй гарсныг ч).
    + зогсоолын багтаамж/эзэлсэн тоолуур. Бичилтэнд: дугаар, орсон/гарсан цаг, хугацаа,
    машины төрөл, төлбөрийн хэрэгсэл, төлсөн эсэх, e-Barimt өгсөн эсэх."""
    from sqlalchemy import or_
    from ..models import ParkingSite, Payment, VatReceipt
    allowed = operator_sites(user)
    if allowed and site_id not in allowed:
        site_id = allowed[0]  # оператор зөвхөн өөрийн зогсоолууд
    site = db.get(ParkingSite, site_id)
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    occupied = db.query(ParkingSession).filter(
        ParkingSession.site_id == site_id,
        ParkingSession.status.in_(["OPEN", "AWAITING_PAYMENT", "PAID"])).count()
    sessions = (db.query(ParkingSession)
                .filter(ParkingSession.site_id == site_id,
                        or_(ParkingSession.exit_time >= today,
                            ParkingSession.status == "AWAITING_PAYMENT"))
                .order_by(ParkingSession.updated_at.desc()).limit(200).all())
    ids = [s.id for s in sessions]
    pays = {}
    if ids:
        for p in db.query(Payment).filter(Payment.session_id.in_(ids), Payment.status == "PAID").all():
            pays.setdefault(p.session_id, p)
    recs = {r.session_id for r in db.query(VatReceipt.session_id)
            .filter(VatReceipt.session_id.in_(ids), VatReceipt.status == "SENT").all()} if ids else set()
    prov_mn = {"CASH": "Бэлэн", "QPAY": "QPay", "POS": "Банкны карт"}
    rows = []
    for s in sessions:
        p = pays.get(s.id)
        car_type = "Гэрээт" if s.is_registered else ("Хөнгөлөлттэй" if s.discount_id else "Энгийн")
        rows.append({
            "session_id": s.id, "plate_number": s.plate_number,
            "entry_time": s.entry_time.isoformat() if s.entry_time else None,
            "exit_time": s.exit_time.isoformat() if s.exit_time else None,
            "duration_minutes": s.duration_minutes,
            "car_type": car_type, "discount_name": s.discount.name if s.discount else None,
            "total_fee": float(s.total_fee or 0),
            "provider": prov_mn.get(p.provider, p.provider) if p else None,
            "paid": s.status == "PAID" or bool(p),
            "status": s.status,
            "ebarimt": s.id in recs,
            "note": s.note,
        })
    cap = site.capacity if site else 0
    # capacity=0 → дүүргэлтгүй зогсоол: сул тоо тооцохгүй (null)
    return {"capacity": cap, "occupied": occupied,
            "free": max(0, cap - occupied) if cap else None, "rows": rows}


@router.post("/manual-entry")
async def manual_entry(body: dict, db: Session = Depends(get_db),
                       user: User = Depends(require("cashier"))):
    """Орох талд уншигдалгүй орсон машиныг ажилтан гараар бүртгэнэ.
    (2 цаг тутмын эргүүлээр илэрсэн машин г.м.)
    body: {site_id, plate_number, entry_time?: ISO datetime — эргүүлээр тааварлаж
           оруулах бол орсон гэж үзэх цаг, default = одоо}"""
    from ..session_logic import find_registered, is_blacklisted, is_valid_plate
    plate = normalize_plate(body.get("plate_number", ""))
    site_id = body.get("site_id")
    allowed = operator_sites(user)
    if allowed and site_id not in allowed:
        site_id = allowed[0]  # оператор зөвхөн өөрийн зогсоолууд
    if not plate or not site_id:
        raise HTTPException(400, "plate_number болон site_id шаардлагатай")
    # force=true — дипломат/тусгай дугаар (стандарт форматад тохирохгүй) гэдгийг оператор баталгаажуулсан
    if not is_valid_plate(plate) and not body.get("force"):
        raise HTTPException(400, f"«{plate}» дугаарын формат буруу байна. "
                                 "Зөв формат: 4 орон + 3 кирилл үсэг (жишээ: 1234УБА). "
                                 "Дипломат/тусгай дугаар бол force=true илгээнэ.")

    existing = get_open_session(db, plate, site_id)
    if existing:
        raise HTTPException(400, f"{plate} дугаартай машин аль хэдийн бүртгэлтэй байна "
                                 f"(орсон: {existing.entry_time:%Y-%m-%d %H:%M})")

    entry_time = (datetime.fromisoformat(body["entry_time"])
                  if body.get("entry_time") else datetime.utcnow())
    registered = find_registered(db, plate, site_id)
    black = is_blacklisted(db, plate)

    s = ParkingSession(
        site_id=site_id, plate_number=plate, entry_time=entry_time,
        is_registered=registered is not None, status="OPEN",
    )
    db.add(s)
    db.flush()
    db.add(AuditLog(username=user.username, action="MANUAL_ENTRY", entity="session",
                    entity_id=s.id, detail={"plate": plate, "entry_time": entry_time.isoformat()}))
    db.commit()
    await manager.broadcast(site_id, "ENTRY_EVENT", {
        "session_id": s.id, "plate": plate, "entry_time": s.entry_time.isoformat(),
        "registered": registered is not None, "blacklisted": black is not None,
        "barrier_opened": False, "manual": True, "by": user.username,
    })
    return _session_out(db, s, with_fee=True)


@router.post("/register-from-camera")
def register_from_camera(body: dict, db: Session = Depends(get_db),
                         user: User = Depends(require_role("ADMIN", "SUPER_ADMIN"))):
    """Камерын логт байгаа ч системд бүртгэлгүй машиныг НӨХӨЖ бүртгэнэ.

    Сервер унтарсан/event алдагдсан үед камер өөрөө уншсан ч session үүсээгүй
    машинууд гарна (Аудит горим → «Камераар орсон ч бүртгэлгүй»). Эдгээрийг
    камерын цагаар нөхөж бүртгээд төлбөрийг нь өр болгоно.

    body: {site_id, cars: [{plate, at, exit_at?}], create_debt?: bool=true}
      • exit_at байвал тэр үеийн дүнгээр хаана (машин гарсан нь мэдэгдэж байгаа)
      • exit_at байхгүй бол зогсоолд БАЙГАА гэж үзэж OPEN үлдээнэ
    """
    site_id = body.get("site_id")
    cars = body.get("cars") or []
    if not site_id or not isinstance(cars, list) or not cars:
        raise HTTPException(400, "site_id ба cars жагсаалт шаардлагатай")
    enforce_site(user, site_id)
    create_debt = bool(body.get("create_debt", True))

    created, debt_total = [], 0.0
    skips: dict[str, int] = {}

    def _skip(why: str):
        skips[why] = skips.get(why, 0) + 1

    for car in cars[:200]:
        plate = normalize_plate(str(car.get("plate") or ""))
        at = car.get("at")
        if not plate or not at:
            _skip("дугаар/цаг дутуу")
            continue
        try:
            entry_time = datetime.fromisoformat(str(at).replace("Z", ""))
        except ValueError:
            _skip("цагийн формат буруу")
            continue
        # (1) Зогсоолд ИДЭВХТЭЙ бүртгэлтэй бол шинийг үүсгэхгүй — uq_active_session
        #     индекс зөрчигдөж бүхэл багц уначихдаг байсан («Алдаа гарлаа»).
        if (db.query(ParkingSession.id)
                .filter(ParkingSession.site_id == site_id,
                        ParkingSession.plate_number == plate,
                        ParkingSession.status.in_(["OPEN", "AWAITING_PAYMENT", "PAID"]))
                .first()):
            _skip("зогсоолд идэвхтэй бүртгэлтэй")
            continue
        # (2) Тухайн цагийн орчимд аль хэдийн бүртгэл байвал давхардуулахгүй
        if (db.query(ParkingSession.id)
                .filter(ParkingSession.site_id == site_id,
                        ParkingSession.plate_number == plate,
                        ParkingSession.entry_time >= entry_time - timedelta(hours=1),
                        ParkingSession.entry_time <= entry_time + timedelta(hours=1))
                .first()):
            _skip("тэр цагт бүртгэл бий")
            continue

        # Машин бүрийг ТУСДАА хамгаална — нэг нь уначихвал бусад нь бүртгэгдэнэ
        try:
            s = ParkingSession(site_id=site_id, plate_number=plate, entry_time=entry_time,
                               status="OPEN",
                               note=f"камерын логоос нөхөж бүртгэв ({user.username})")
            exit_raw = car.get("exit_at")
            if exit_raw:
                try:
                    s.exit_time = datetime.fromisoformat(str(exit_raw).replace("Z", ""))
                    s.status = "AWAITING_PAYMENT"   # гарсан нь мэдэгдэж байгаа
                except ValueError:
                    pass
            db.add(s)
            db.flush()
            due = 0.0
            if s.status == "AWAITING_PAYMENT":
                # exit_time дээр төлбөрийг царцааж хаана; төлөгдөөгүй тул өр үүснэ
                due = close_session_forced(db, s, "camera_backfill", user.username,
                                           create_comp=create_debt)
                debt_total += due
            db.add(AuditLog(username=user.username, action="CAMERA_BACKFILL",
                            entity="session", entity_id=s.id,
                            detail={"plate": plate, "entry": entry_time.isoformat(),
                                    "exit": exit_raw, "debt": due}))
            db.commit()
            created.append({"plate": plate, "session_id": s.id, "debt": due,
                            "status": s.status})
        except Exception as e:  # noqa: BLE001
            db.rollback()
            _skip(f"алдаа: {type(e).__name__}")
    return {"created": len(created), "skipped": sum(skips.values()),
            "skip_reasons": skips, "debt_total": debt_total, "rows": created}


@router.post("/bulk-remove")
async def bulk_remove(body: dict, db: Session = Depends(get_db),
                      user: User = Depends(require_role("ADMIN", "SUPER_ADMIN"))):
    """Админ: зогсоолд гацсан машидыг бүртгэлээс хасна (хаалт нээхгүй).
    body: {session_ids: [..], create_compensation: bool=true, reason?: str}
    Өрийн дүн: гарах оролдлоготой машинд тэр үеийн дүн, бусдад одоог хүртэлх дүн."""
    ids = body.get("session_ids") or []
    if not isinstance(ids, list) or not ids:
        raise HTTPException(400, "session_ids жагсаалт шаардлагатай")
    create_comp = bool(body.get("create_compensation", True))
    note = (body.get("reason") or "").strip()[:300]
    removed, skipped, debt_total = [], 0, 0.0
    for sid in ids[:200]:
        s = db.get(ParkingSession, sid)
        if not s or s.status not in ("OPEN", "AWAITING_PAYMENT", "PAID"):
            skipped += 1
            continue
        debt = close_session_forced(db, s, "admin_remove", user.username, create_comp)
        if note:
            s.note = f"{s.note + ' | ' if s.note else ''}Хассан: {note}"[:1000]
        removed.append({"session_id": s.id, "plate": s.plate_number, "debt": debt})
        debt_total += debt
    db.add(AuditLog(username=user.username, action="ADMIN_REMOVE", entity="session",
                    entity_id=removed[0]["session_id"] if removed else "",
                    detail={"count": len(removed), "skipped": skipped,
                            "debt_total": debt_total, "reason": note,
                            "plates": [r["plate"] for r in removed][:50]}))
    db.commit()
    return {"removed": len(removed), "skipped": skipped, "debt_total": debt_total, "rows": removed}


@router.post("/test-awaiting")
async def test_awaiting(body: dict, db: Session = Depends(get_db),
                        user: User = Depends(require("cashier"))):
    """ТЕСТ: камергүйгээр 'Гарах машинууд (төлбөр хүлээж буй)' листэд машин нэмнэ.
    Зөвхөн тест горим (PARKING_ALLOW_SIMULATE=true) дээр ажиллана."""
    import random
    from ..config import settings
    if not settings.allow_simulate:
        raise HTTPException(403, "Тест горим идэвхгүй (production)")
    site_id = body.get("site_id")
    allowed = operator_sites(user)
    if allowed and site_id not in allowed:
        site_id = allowed[0]  # оператор зөвхөн өөрийн зогсоолууд
    if not site_id:
        raise HTTPException(400, "site_id шаардлагатай")
    letters = "АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЭЮЯӨҮ"
    plate = normalize_plate(body.get("plate") or
                            f"{random.randint(1000, 9999)}{''.join(random.choice(letters) for _ in range(3))}")
    minutes = int(body.get("minutes") or random.randint(35, 130))
    now = datetime.utcnow()
    s = ParkingSession(site_id=site_id, plate_number=plate, entry_time=now - timedelta(minutes=minutes),
                       status="AWAITING_PAYMENT")
    db.add(s)
    db.flush()
    fee = session_fee_info(db, s, at=now)
    s.duration_minutes = fee["duration_minutes"]
    s.base_fee, s.vat_amount, s.total_fee = fee["base_fee"], fee["vat_amount"], fee["total_fee"]
    db.add(AuditLog(username=user.username, action="TEST_AWAITING", entity="session",
                    entity_id=s.id, detail={"plate": plate}))
    db.commit()
    await manager.broadcast(site_id, "EXIT_LPR_EVENT", {
        "session_id": s.id, "plate": plate, "entry_time": s.entry_time.isoformat(),
        "duration_minutes": fee["duration_minutes"], "total_fee": fee["total_fee"], "test": True,
    })
    return _session_out(db, s, with_fee=True)


@router.get("/{session_id}/snapshot/{kind}")
def get_snapshot(session_id: str, kind: str, db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    """Орох/гарах камерын хадгалсан зургийг буцаана. kind: entry | exit."""
    import os

    from fastapi.responses import FileResponse

    from ..config import settings as cfg
    if kind not in ("entry", "exit"):
        raise HTTPException(404, "kind нь entry эсвэл exit байна")
    s = db.get(ParkingSession, session_id)
    if not s:
        raise HTTPException(404, "Session олдсонгүй")
    enforce_site(user, s.site_id)  # оператор зөвхөн өөрийн зогсоолууд
    rel = s.entry_snapshot if kind == "entry" else s.exit_snapshot
    if not rel:
        raise HTTPException(404, "Зураг хадгалагдаагүй байна")
    path = os.path.join(cfg.snapshot_dir, rel)
    if not os.path.isfile(path):
        raise HTTPException(404, "Зургийн файл олдсонгүй")
    return FileResponse(path, media_type="image/jpeg")


@router.post("/{session_id}/snapshot/{kind}/backfill")
async def backfill_snapshot(session_id: str, kind: str, db: Session = Depends(get_db),
                            user: User = Depends(require("cashier", "check", "history"))):
    """Дутуу зургийг камерын санах ойгоос нөхөж татна (mediaFileFind + RPC_Loadfile).
    Орох/гарах цагийн ±90с мужид камерт хадгалагдсан хамгийн том jpg-г авна."""
    from ..config import settings as cfg
    from ..services.snap_puller import fetch_stored_picture
    from ..services.snapshot import _save
    if kind not in ("entry", "exit"):
        raise HTTPException(404, "kind нь entry эсвэл exit байна")
    s = db.get(ParkingSession, session_id)
    if not s:
        raise HTTPException(404, "Session олдсонгүй")
    enforce_site(user, s.site_id)
    event_time = s.entry_time if kind == "entry" else s.exit_time
    if not event_time:
        raise HTTPException(400, "Гарах цаг бүртгэлгүй тул гарах зураг хайх боломжгүй")
    device_id = s.entry_device_id if kind == "entry" else s.exit_device_id
    device = db.get(Device, device_id) if device_id else None
    if not device or not device.ip_address:
        # Event-ийн төхөөрөмж тодорхойгүй бол тухайн чиглэлийн камерыг хайна
        lane = "entry" if kind == "entry" else "exit"
        device = (db.query(Device)
                  .filter(Device.site_id == s.site_id, Device.device_type == "camera",
                          Device.lane_dir == lane, Device.status == "active",
                          Device.ip_address.isnot(None), Device.ip_address != "")
                  .first())
    if not device or not device.ip_address:
        raise HTTPException(400, "Энэ чиглэлийн камерын IP бүртгэлгүй байна")
    # UTC event цагийг шууд дамжуулна — fetch_stored_picture өөрөө бүсийн зөрүү,
    # хайлтын цонхыг тооцож 3 өөр аргаар (RecordFinder → mediaFileFind → амьд кадр) татна
    data, err = await fetch_stored_picture(
        device.ip_address, event_time,
        creds=camera_credentials(device),
        tz_offset_hours=cfg.camera_tz_offset_hours,
        window_seconds=cfg.snapshot_search_window_seconds)
    if not data:
        raise HTTPException(404, f"Камераас зураг олдсонгүй: {err}")
    rel = _save(data, s.plate_number, kind)
    if not rel:
        raise HTTPException(500, "Зургийг хадгалж чадсангүй")
    if kind == "exit":
        s.exit_snapshot = rel
    else:
        s.entry_snapshot = rel
    db.commit()
    return {"ok": True, "path": rel, "size": len(data)}


@router.get("/{session_id}")
def get_session(session_id: str, db: Session = Depends(get_db),
                user: User = Depends(require("history", "cashier"))):
    s = db.get(ParkingSession, session_id)
    if not s:
        raise HTTPException(404, "Session олдсонгүй")
    enforce_site(user, s.site_id)  # оператор зөвхөн өөрийн зогсоолын session
    return _session_out(db, s, with_fee=True)


@router.post("/{session_id}/apply-discount")
def apply_discount(session_id: str, body: dict, db: Session = Depends(get_db),
                   user: User = Depends(require("cashier", "discounts"))):
    s = db.get(ParkingSession, session_id)
    if not s:
        raise HTTPException(404, "Session олдсонгүй")
    enforce_site(user, s.site_id)  # оператор зөвхөн өөрийн зогсоол
    if s.status not in ("OPEN", "AWAITING_PAYMENT"):
        raise HTTPException(400, "Зөвхөн нээлттэй session-д хөнгөлөлт хэрэглэнэ")
    s.discount_id = body.get("discount_id")
    fee = session_fee_info(db, s)
    s.discount_amount = fee["discount_amount"]
    if s.status == "AWAITING_PAYMENT":
        s.base_fee, s.vat_amount, s.total_fee = fee["base_fee"], fee["vat_amount"], fee["total_fee"]
    # Хөнгөлөлт хэрэглэсэн тайлбар (шалтгаан)-ыг аудитад хадгална
    db.add(AuditLog(username=user.username, action="APPLY_DISCOUNT", entity="session",
                    entity_id=session_id,
                    detail={"discount_id": body.get("discount_id"), "note": body.get("note", "")}))
    db.commit()
    return _session_out(db, s, with_fee=True)


@router.put("/{session_id}/plate")
async def edit_plate(session_id: str, body: dict, db: Session = Depends(get_db),
                     user: User = Depends(require("cashier"))):
    """Камер алдаатай уншсан дугаарыг засах (easy-park UAT items 18, 21, 24).
    Зассаны дараа төлбөр/хайлт шинэ дугаараар хэвийн ажиллана."""
    from ..session_logic import is_valid_plate
    s = db.get(ParkingSession, session_id)
    if not s:
        raise HTTPException(404, "Session олдсонгүй")
    enforce_site(user, s.site_id)  # оператор зөвхөн өөрийн зогсоол
    if s.status not in ("OPEN", "AWAITING_PAYMENT", "PAID"):
        raise HTTPException(400, "Зөвхөн нээлттэй session-ий дугаарыг засна")
    new_plate = normalize_plate(body.get("plate_number", ""))
    if not is_valid_plate(new_plate) and not body.get("force"):
        raise HTTPException(400, f"«{new_plate}» формат буруу. Зөв: 4 орон + 3 кирилл үсэг (1234УБА). "
                                 "Дипломат/тусгай дугаар бол force=true илгээнэ.")
    dup = get_open_session(db, new_plate, s.site_id)
    if dup and dup.id != s.id:
        raise HTTPException(400, f"{new_plate} дугаартай өөр нээлттэй бүртгэл байна")
    old_plate = s.plate_number
    s.plate_number = new_plate
    db.add(AuditLog(username=user.username, action="EDIT_PLATE", entity="session",
                    entity_id=session_id, detail={"old": old_plate, "new": new_plate}))
    db.commit()
    await manager.broadcast(s.site_id, "PLATE_EDITED", {
        "session_id": s.id, "old_plate": old_plate, "plate": new_plate, "by": user.username,
    })
    return _session_out(db, s, with_fee=True)


@router.post("/{session_id}/manual-exit")
async def manual_exit(session_id: str, body: dict, db: Session = Depends(get_db),
                      user: User = Depends(require("free_exit"))):
    """Оператор гараар гаргах (төлбөргүйгээр эсвэл асуудал шийдсэний дараа).
    ЭРХ: зөвхөн free_exit эрхтэй хэрэглэгч (default-оор ADMIN; итгэмжит операторт
    админ гараар олгоно) — энгийн оператор танилаа үнэгүй гаргахаас сэргийлнэ.
    body: {open_barrier: bool, device_id?: str, reason: str, create_compensation?: bool}
    create_compensation=true бол төлөгдөөгүй дүнгээр нөхөн төлбөрийн нэхэмжлэл үүснэ."""
    s = db.get(ParkingSession, session_id)
    if not s:
        raise HTTPException(404, "Session олдсонгүй")
    enforce_site(user, s.site_id)  # оператор зөвхөн өөрийн зогсоолын машиныг гаргана
    now = datetime.utcnow()
    fee = session_fee_info(db, s, at=now)
    s.exit_time = now
    s.duration_minutes = fee["duration_minutes"]
    if s.total_fee is None:
        s.base_fee, s.vat_amount, s.total_fee = fee["base_fee"], fee["vat_amount"], fee["total_fee"]
    s.status = "CLOSED" if s.paid_at else "MANUAL_CLOSED"

    # Төлбөргүй гаргаж буй бол нөхөн төлбөрийн нэхэмжлэл үүсгэх сонголт
    if body.get("create_compensation") and not s.paid_at and not fee["is_free"]:
        from .compensations_router import create_compensation
        create_compensation(db, s, body.get("reason") or "unpaid_exit", user.username)

    barrier_opened = False
    if body.get("open_barrier"):
        device = db.get(Device, body.get("device_id")) if body.get("device_id") else None
        if not device:
            device = (db.query(Device).filter(Device.site_id == s.site_id,
                                              Device.device_type == "barrier",
                                              Device.lane_dir == "exit").first()
                      or db.query(Device).filter(Device.site_id == s.site_id,
                                                 Device.device_type == "barrier").first())
        if device:
            cmd = await open_barrier(db, device, s.id, "manual", issued_by=user.username,
                                     plate=s.plate_number)
            barrier_opened = cmd.status == "SUCCESS"

    db.add(AuditLog(username=user.username, action="MANUAL_EXIT", entity="session",
                    entity_id=session_id, detail={"reason": body.get("reason", ""), **body}))
    db.commit()
    await manager.broadcast(s.site_id, "EXIT_COMPLETED", {
        "session_id": s.id, "plate": s.plate_number, "status": s.status,
        "barrier_opened": barrier_opened, "manual": True,
    })
    return _session_out(db, s)


@router.post("/{session_id}/reopen")
async def reopen_session(session_id: str, db: Session = Depends(get_db),
                         user: User = Depends(require_role("ADMIN", "SUPER_ADMIN"))):
    """Андуурч хассан/хаасан бүртгэлийг буцаан зогсоолд оруулна (status→OPEN).
    Орсон цаг хэвээр тул хугацаа орсноос нь үргэлжлэн бодогдоно ("цаг явна").
    Хассан үед үүссэн PENDING өр (нөхөн төлбөр) байвал цуцлана.
    Төлбөр төлөгдсөн бүртгэлийг сэргээхгүй (payment алдагдахаас сэргийлж)."""
    s = db.get(ParkingSession, session_id)
    if not s:
        raise HTTPException(404, "Session олдсонгүй")
    enforce_site(user, s.site_id)  # оператор зөвхөн өөрийн зогсоол
    if s.status in ("OPEN", "AWAITING_PAYMENT", "PAID"):
        raise HTTPException(400, "Энэ бүртгэл аль хэдийн зогсоолд байна")
    from ..session_logic import paid_total
    if s.paid_at or paid_total(db, s) > 0:
        raise HTTPException(400, "Төлбөр төлөгдсөн бүртгэлийг сэргээх боломжгүй")
    # uq_active_session: ижил дугаарын өөр идэвхтэй бүртгэл байвал зөрчилдөнө
    dup = get_open_session(db, s.plate_number, s.site_id)
    if dup and dup.id != s.id:
        raise HTTPException(400, f"{s.plate_number} дугаартай өөр нээлттэй бүртгэл байна")
    s.status = "OPEN"
    s.exit_time = None
    s.exit_device_id = None
    s.duration_minutes = None
    s.total_fee = s.base_fee = s.vat_amount = None
    s.exit_deadline = None
    canceled = (db.query(Compensation)
                .filter(Compensation.session_id == s.id, Compensation.status == "PENDING")
                .update({"status": "CANCELLED"}, synchronize_session=False))
    db.add(AuditLog(username=user.username, action="REOPEN", entity="session",
                    entity_id=s.id, detail={"plate": s.plate_number, "canceled_debt": canceled}))
    db.commit()
    await manager.broadcast(s.site_id, "ENTRY_EVENT", {
        "session_id": s.id, "plate": s.plate_number,
        "entry_time": s.entry_time.isoformat() if s.entry_time else None,
        "reopened": True, "by": user.username,
    })
    return _session_out(db, s, with_fee=True)
