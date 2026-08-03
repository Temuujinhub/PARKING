"""Тохиргооны CRUD: зогсоол, төхөөрөмж, тарифын загвар, хөнгөлөлт, жолооч, хар жагсаалт, хэрэглэгч."""
import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from ..auth import (ALL_MODULES, enforce_site, get_current_user, hash_password,
                    operator_sites, require, require_role)
from ..database import get_db
from ..models import (
    AuditLog, BarrierCommand, BlacklistEntry, CashierShift, Compensation, DailySettlement,
    Device, Discount, LprEvent, ParkingSession, ParkingSite, Payment,
    RegisteredDriver, TariffTemplate, TariffTier, Tenant, User, VatReceipt,
)
from ..config import settings
from .. import schemas
from ..secretbox import encrypt_secret
from ..serializers import SECRET_COLUMNS, site_pay_url, to_dict

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _qpay_err(e: Exception) -> str:
    """QPay-ийн HTTP алдааг админд ойлгомжтой мөр болгоно (message талбарыг гаргана)."""
    import httpx
    if isinstance(e, httpx.HTTPStatusError):
        try:
            data = e.response.json()
            return str(data.get("message") or data.get("error") or data)[:300]
        except Exception:  # noqa: BLE001
            return e.response.text[:300]
    return f"{type(e).__name__}: {e}"[:300]


# LED мөрийн зөвшөөрөгдсөн төрлүүд. payment/reason нь зөвхөн ГАРАХ дэлгэцэд
# утгатай (төлбөрийн төрөл / үнэгүй гарсан шалтгаан).
_SCREEN_TYPES = {"none", "time", "plate", "duration", "amount", "text"}
_SCREEN_EXIT_TYPES = _SCREEN_TYPES | {"payment", "reason"}


def _qr_data_uri(data: str | None) -> str:
    """QR текстийг жижиг PNG data-URI болгоно (e-Barimt-ын баримтын QR-д)."""
    if not data:
        return ""
    try:
        import base64
        import io as _io
        import qrcode
        img = qrcode.make(data, box_size=4, border=2)
        buf = _io.BytesIO()
        img.save(buf, format="PNG")
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:  # noqa: BLE001 — QR зурагдаагүй ч баримтын мэдээлэл хэвээр
        return ""


def _check_screen_config(cfg):
    """screen_config-ийг хадгалахын өмнө цэвэрлэж шалгана. Буруу бүтэц LED-д
    биш DB-д очих учир энд л барина. Буцаах: цэвэрлэсэн dict | None."""
    if cfg in (None, {}):
        return None
    if not isinstance(cfg, dict):
        raise HTTPException(400, "screen_config буруу бүтэцтэй")
    out = {}
    for lane in ("entry", "exit"):
        lines = cfg.get(lane)
        if lines is None:
            continue
        if not isinstance(lines, list) or len(lines) > 4:
            raise HTTPException(400, f"screen_config.{lane}: дээд тал нь 4 мөр байна")
        allowed = _SCREEN_EXIT_TYPES if lane == "exit" else _SCREEN_TYPES
        clean = []
        for ln in lines:
            t = (ln or {}).get("type", "none") if isinstance(ln, dict) else "none"
            if t not in allowed:
                raise HTTPException(400, f"screen_config.{lane}: '{t}' төрөл байхгүй")
            item = {"type": t}
            if t == "text":
                txt = str((ln or {}).get("text", "")).strip()[:40]
                if not txt:
                    t = "none"
                    item = {"type": "none"}
                else:
                    item["text"] = txt
            clean.append(item)
        # Бүгд хоосон бол тухайн чиглэлд тохиргоогүйтэй адил
        if any(i["type"] != "none" for i in clean):
            out[lane] = clean
    return out or None


def _check_district(code):
    """QPay-ийн district_code = дүүрэг(2 орон)+хороо(2 орон). Буруу бол нэхэмжлэл
    үүсэхгүй тул хадгалахын өмнө шалгана."""
    if code and not (str(code).isdigit() and len(str(code)) == 4):
        raise HTTPException(400, "НӨАТ-ын дүүрэг+хорооны код 4 оронтой тоо байх ёстой "
                                 "(жишээ: 2318 = Хан-Уул 18-р хороо)")


def _scrub(detail: dict | None) -> dict:
    """Аудитын бичлэгт нууц үг ХАДГАЛАХГҮЙ — зөвхөн өөрчилсөн эсэхийг тэмдэглэнэ."""
    if not detail:
        return {}
    return {k: ("(өөрчлөв)" if v else "(цэвэрлэв)") if k in SECRET_COLUMNS else v
            for k, v in detail.items()}


def _audit(db: Session, user: User, action: str, entity: str, entity_id: str, detail: dict | None = None):
    db.add(AuditLog(username=user.username, action=action, entity=entity,
                    entity_id=str(entity_id), detail=_scrub(detail)))


# API/UI-аас үүсгэж болох дүрүүд (SUPER_ADMIN зөвхөн DB-ээр)
CREATABLE_ROLES = ("ADMIN", "FINANCE", "HR", "OPERATOR")


def _check_password(pw: str):
    """Нууц үгийн доод шаардлага (M6) — admin/HR сул нууц үгтэй хэрэглэгч үүсгэхээс сэргийлнэ."""
    if not pw or len(pw) < 8:
        raise HTTPException(400, "Нууц үг дор хаяж 8 тэмдэгт байх ёстой.")


def _clean_permissions(perms, role: str) -> list | None:
    """Эрхийн чекбокс матрицыг цэвэрлэнэ: зөвхөн мэдэгдэж буй модулиуд.
    None эсвэл role-ийн default-той яг ижил бол null хадгална (default дагана)."""
    if perms is None:
        return None
    if not isinstance(perms, list):
        raise HTTPException(400, "permissions нь жагсаалт байх ёстой")
    bad = [m for m in perms if m not in ALL_MODULES]
    if bad:
        raise HTTPException(400, f"Танигдахгүй модуль: {', '.join(bad)}")
    from ..auth import ROLE_PERMISSIONS
    if set(perms) == set(ROLE_PERMISSIONS.get(role, set())):
        return None  # default-той ижил — матриц хадгалахгүй (role өөрчлөгдөхөд дагана)
    return sorted(set(perms))


def _clean_site_ids(site_ids, primary_site_id) -> list | None:
    """Операторын олон зогсоолын жагсаалтыг цэвэрлэнэ; үндсэн site_id ямагт багтана."""
    if site_ids is None:
        return None
    if not isinstance(site_ids, list):
        raise HTTPException(400, "site_ids нь жагсаалт байх ёстой")
    ids = [s for s in site_ids if s]
    if primary_site_id and primary_site_id not in ids:
        ids.insert(0, primary_site_id)
    return ids or None


# ─────────────────────────── Зогсоол ───────────────────────────
@router.get("/sites")
def list_sites(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    q = db.query(ParkingSite)
    allowed = operator_sites(user)  # оператор зөвхөн өөрийн зогсоолуудыг л харна
    if allowed:
        q = q.filter(ParkingSite.id.in_(allowed))
    sites = q.order_by(ParkingSite.created_at).all()
    # Зогсоол бүрийн эзэлсэн тоог НЭГ query-ээр (site тус бүрт COUNT хийхгүй)
    from sqlalchemy import func
    occupied_by_site = dict(
        db.query(ParkingSession.site_id, func.count())
        .filter(ParkingSession.status.in_(["OPEN", "AWAITING_PAYMENT", "PAID"]))
        .group_by(ParkingSession.site_id).all())
    tmap = {t.id: t for t in db.query(Tenant).all()}
    out = []
    for s in sites:
        occupied = occupied_by_site.get(s.id, 0)
        _t = tmap.get(s.tenant_id)
        out.append(to_dict(s, extra={
            "tenant_name": getattr(_t, "name", None),
            "tenant_qpay_set": bool(_t and (getattr(_t, "qpay_username", None) or "").strip()),
            "occupied": occupied,
            # capacity=0 → дүүргэлтгүй (хязгааргүй) зогсоол: сул тоо тооцохгүй
            "free_spaces": max(0, s.capacity - occupied) if s.capacity else None,
            "tariff_template_name": s.tariff_template.name if s.tariff_template else None,
            # Жолоочийн төлбөрийн линк — QR-т кодлогдсонтой ЯГ ижил
            # (хэвлэгдсэн самбартай зогсоолд тэр самбарын линк)
            "pay_url": site_pay_url(s),
        }))
    return out


# ─────────────── Түрээслэгч (Tenant) — SUPER_ADMIN л удирдана ───────────────
@router.get("/tenants")
def list_tenants(db: Session = Depends(get_db), user: User = Depends(require_role("SUPER_ADMIN"))):
    """Түрээслэгчдийн жагсаалт — зогсоол/хэрэглэгчийн тоотой."""
    from sqlalchemy import func
    site_cnt = dict(db.query(ParkingSite.tenant_id, func.count()).filter(
        ParkingSite.tenant_id.isnot(None)).group_by(ParkingSite.tenant_id).all())
    user_cnt = dict(db.query(User.tenant_id, func.count()).filter(
        User.tenant_id.isnot(None)).group_by(User.tenant_id).all())
    out = []
    for t in db.query(Tenant).order_by(Tenant.created_at).all():
        sites = [{"id": s.id, "name": s.name, "site_code": s.site_code}
                 for s in db.query(ParkingSite).filter(ParkingSite.tenant_id == t.id).all()]
        out.append(to_dict(t, extra={"site_count": int(site_cnt.get(t.id, 0)),
                                     "user_count": int(user_cnt.get(t.id, 0)),
                                     "sites": sites}))
    return out


def _assign_tenant_sites(db, tenant_id: str, site_ids: list[str] | None):
    """Зогсоолуудыг түрээслэгчид оноох: жагсаалтад байгааг оноож, өмнө нь энэ
    түрээслэгчид байсан ч жагсаалтаас хасагдсаныг чөлөөлнө (NULL болгоно)."""
    if site_ids is None:
        return
    ids = {i for i in site_ids if i}
    db.query(ParkingSite).filter(ParkingSite.tenant_id == tenant_id,
                                 ~ParkingSite.id.in_(ids) if ids else True).update(
        {"tenant_id": None}, synchronize_session=False)
    if ids:
        db.query(ParkingSite).filter(ParkingSite.id.in_(ids)).update(
            {"tenant_id": tenant_id}, synchronize_session=False)


@router.post("/tenants")
def create_tenant(payload: schemas.TenantCreate, db: Session = Depends(get_db),
                  user: User = Depends(require_role("SUPER_ADMIN"))):
    """Шинэ түрээслэгч бүртгэх — Monnis шиг: зогсоолуудаа оноож, админ хэрэглэгчийг
    нь хамт үүсгэнэ. Тухайн админ зөвхөн өөрийн түрээслэгчийн зогсоол/тайлан/
    хэрэглэгчийг харна (auth.operator_sites-ийн tenant fallback)."""
    body = payload.dump()
    code = body["code"].strip().upper()
    if db.query(Tenant).filter(Tenant.code == code).first():
        raise HTTPException(400, "Түрээслэгчийн код давхардаж байна")
    t = Tenant(name=body["name"].strip(), code=code, register=body.get("register", "").strip(),
               contact_name=body.get("contact_name", ""), phone=body.get("phone", ""),
               email=body.get("email", ""), note=body.get("note", ""),
               qpay_username=(body.get("qpay_username") or "").strip() or None,
               qpay_password=encrypt_secret((body.get("qpay_password") or "").strip() or None),
               qpay_invoice_code=(body.get("qpay_invoice_code") or "").strip() or None,
               qpay_branch_code=(body.get("qpay_branch_code") or "").strip() or None,
               qpay_district_code=(body.get("qpay_district_code") or "").strip() or None)
    _check_district(t.qpay_district_code)
    db.add(t)
    db.flush()
    _assign_tenant_sites(db, t.id, body.get("site_ids"))
    admin_info = None
    if body.get("admin_username"):
        if db.query(User).filter(User.username == body["admin_username"]).first():
            raise HTTPException(400, "Админы нэвтрэх нэр давхардаж байна")
        _check_password(body.get("admin_password", ""))
        au = User(username=body["admin_username"], password_hash=hash_password(body["admin_password"]),
                  full_name=body.get("admin_full_name", "") or f"{t.name} админ",
                  role="ADMIN", tenant_id=t.id)
        db.add(au)
        db.flush()
        admin_info = {"id": au.id, "username": au.username}
    _audit(db, user, "CREATE", "tenant", t.id,
           {"name": t.name, "code": t.code, "sites": body.get("site_ids"),
            "admin": body.get("admin_username")})
    db.commit()
    return to_dict(t, extra={"admin_user": admin_info})


@router.put("/tenants/{tenant_id}")
def update_tenant(tenant_id: str, payload: schemas.TenantUpdate, db: Session = Depends(get_db),
                  user: User = Depends(require_role("SUPER_ADMIN"))):
    body = payload.dump()
    t = db.get(Tenant, tenant_id)
    if not t:
        raise HTTPException(404, "Түрээслэгч олдсонгүй")
    if "code" in body:
        code = (body["code"] or "").strip().upper()
        if code != t.code and db.query(Tenant).filter(Tenant.code == code).first():
            raise HTTPException(400, "Түрээслэгчийн код давхардаж байна")
        t.code = code
    for k in ("name", "register", "contact_name", "phone", "email", "note", "is_active"):
        if k in body:
            setattr(t, k, body[k])
    for k in ("qpay_username", "qpay_invoice_code", "qpay_branch_code", "qpay_district_code"):
        if k in body:
            setattr(t, k, (body[k] or "").strip() or None)
    if "qpay_password" in body:
        # Хоосон илгээвэл цэвэрлэнэ, хөндөөгүй бол хэвээр
        t.qpay_password = encrypt_secret((body["qpay_password"] or "").strip() or None)
    _check_district(t.qpay_district_code)
    _assign_tenant_sites(db, t.id, body.get("site_ids"))
    _audit(db, user, "UPDATE", "tenant", tenant_id, body)
    db.commit()
    return to_dict(t)


@router.post("/sites")
def create_site(payload: schemas.SiteCreate, db: Session = Depends(get_db), user: User = Depends(require("settings"))):
    body = payload.dump()
    if db.query(ParkingSite).filter(ParkingSite.site_code == body["site_code"]).first():
        raise HTTPException(400, "site_code давхардаж байна")
    site = ParkingSite(**{k: body[k] for k in
                          ("name", "site_code", "zone_code", "address", "capacity",
                           "tariff_template_id", "auto_close_hours", "entry_only_free_hours", "qr_url",
                           "qpay_username", "qpay_password", "qpay_invoice_code",
                           "qpay_branch_code", "qpay_district_code")
                          if k in body})
    if "screen_config" in body:
        site.screen_config = _check_screen_config(body["screen_config"])
    if "tenant_id" in body and user.role == "SUPER_ADMIN":
        site.tenant_id = body["tenant_id"] or None
    site.qpay_password = encrypt_secret(site.qpay_password)  # DB-д ил бичихгүй
    _check_district(site.qpay_district_code)
    db.add(site)
    db.flush()
    _audit(db, user, "CREATE", "site", site.id, body)
    db.commit()
    return to_dict(site, extra={"pay_url": site_pay_url(site)})


@router.put("/sites/{site_id}")
def update_site(site_id: str, payload: schemas.SiteUpdate, db: Session = Depends(get_db),
                user: User = Depends(require("settings"))):
    body = payload.dump()
    enforce_site(user, site_id)  # tenant админ өөр зогсоолын тохиргоог засахгүй
    site = db.get(ParkingSite, site_id)
    if not site:
        raise HTTPException(404, "Зогсоол олдсонгүй")
    for k in ("name", "site_code", "zone_code", "address", "capacity", "tariff_template_id",
              "auto_close_hours", "entry_only_free_hours", "is_active", "qr_url",
              "qpay_username", "qpay_password", "qpay_invoice_code",
              "qpay_branch_code", "qpay_district_code"):
        if k in body:
            val = body[k]
            # Мөр талбарууд: хоосон → NULL (глобал .env тохиргоо руу уналт хийнэ)
            if isinstance(val, str) and k != "name":
                val = val.strip() or None
            if k == "qpay_password":
                val = encrypt_secret(val)  # DB-д ил бичихгүй
            setattr(site, k, val)
    if "screen_config" in body:
        site.screen_config = _check_screen_config(body["screen_config"])
    if "tenant_id" in body and user.role == "SUPER_ADMIN":
        site.tenant_id = body["tenant_id"] or None
    _check_district(site.qpay_district_code)
    _audit(db, user, "UPDATE", "site", site_id, body)
    db.commit()
    return to_dict(site, extra={"pay_url": site_pay_url(site)})


@router.delete("/sites/{site_id}")
def delete_site(site_id: str, force: bool = False, db: Session = Depends(get_db),
                user: User = Depends(require_role("SUPER_ADMIN", "ADMIN"))):
    """Зогсоол устгах. Сешн түүхтэй бол force=true шаардана (тест дата цэвэрлэхэд).
    force үед тухайн зогсоолын бүх хамаарах бичлэг (сешн, төлбөр, НӨАТ, LPR лог,
    хаалтны команд, нөхөн төлбөр, тооцоо, төхөөрөмж) бүрмөсөн устна."""
    enforce_site(user, site_id)
    site = db.get(ParkingSite, site_id)
    if not site:
        raise HTTPException(404, "Зогсоол олдсонгүй")

    sess_ids = db.query(ParkingSession.id).filter(ParkingSession.site_id == site_id)
    sess_count = sess_ids.count()
    if sess_count and not force:
        raise HTTPException(409, f"Энэ зогсоолд {sess_count} зогсолтын бүртгэл (түүх) байна. "
                                 "Түүхийн хамт бүрмөсөн устгах бол дахин баталгаажуулна уу.")

    dev_ids = db.query(Device.id).filter(Device.site_id == site_id)
    sq = sess_ids.scalar_subquery()
    dq = dev_ids.scalar_subquery()
    # FK дарааллаар: эхлээд сешнээс хамаардаг хүснэгтүүд, дараа нь сешн, төхөөрөмж, зогсоол
    db.query(VatReceipt).filter(VatReceipt.session_id.in_(sq)).delete(synchronize_session=False)
    db.query(Payment).filter(Payment.session_id.in_(sq)).delete(synchronize_session=False)
    db.query(BarrierCommand).filter(
        (BarrierCommand.session_id.in_(sq)) | (BarrierCommand.device_id.in_(dq))
    ).delete(synchronize_session=False)
    db.query(LprEvent).filter(LprEvent.site_id == site_id).delete(synchronize_session=False)
    db.query(Compensation).filter(Compensation.site_id == site_id).delete(synchronize_session=False)
    db.query(DailySettlement).filter(DailySettlement.site_id == site_id).delete(synchronize_session=False)
    db.query(ParkingSession).filter(ParkingSession.site_id == site_id).delete(synchronize_session=False)
    db.query(Device).filter(Device.site_id == site_id).delete(synchronize_session=False)
    # Хамааралтай боловч устгах шаардлагагүй бичлэгүүдийн холбоосыг салгана
    db.query(CashierShift).filter(CashierShift.site_id == site_id).update(
        {"site_id": None}, synchronize_session=False)
    db.query(User).filter(User.site_id == site_id).update({"site_id": None}, synchronize_session=False)
    db.query(RegisteredDriver).filter(RegisteredDriver.site_id == site_id).update(
        {"site_id": None}, synchronize_session=False)
    db.delete(site)
    _audit(db, user, "DELETE", "site", site_id,
           {"name": site.name, "site_code": site.site_code, "force": force, "sessions": sess_count})
    db.commit()
    return {"ok": True, "deleted_sessions": sess_count}


# ─────────────── QPay дансны туршилт (машингүйгээр) ───────────────
@router.post("/sites/{site_id}/qpay-test")
async def qpay_test_invoice(site_id: str, body: dict, db: Session = Depends(get_db),
                            user: User = Depends(require("settings"))):
    """Тухайн зогсоолын QPay дансаар БОДИТ туршилтын нэхэмжлэл үүсгэнэ.

    Машин орох/гарах шаардлагагүйгээр түрээслэгчийн данс зөв ажиллаж байгааг
    шалгана: токен авах → нэхэмжлэл → QR. Дараа нь /qpay-test/check-ээр
    төлөгдсөнийг шалгаж e-Barimt үүсгэнэ (баримт нь тухайн дансны ТТД-ээр гарна).

    ЭНЭ НЬ ЖИНХЭНЭ МӨНГӨ — багахан дүн (default 10₮) ашиглана.
    Зогсоолын бүртгэл (session/payment) үүсгэхгүй тул тайланг бохирдуулахгүй."""
    from ..services import qpay as qpay_svc

    enforce_site(user, site_id)
    site = db.get(ParkingSite, site_id)
    if not site:
        raise HTTPException(404, "Зогсоол олдсонгүй")

    # Зөвхөн ОГТ өгөөгүй үед default — явуулсан 0-ийг чимээгүй 10 болгож нуухгүй
    raw_amount = body.get("amount")
    try:
        amount = float(10 if raw_amount in (None, "") else raw_amount)
    except (TypeError, ValueError):
        raise HTTPException(400, "Дүн тоо байх ёстой") from None
    if not (1 <= amount <= 10000):
        raise HTTPException(400, "Туршилтын дүн 1–10000₮ хооронд байна")

    acc = qpay_svc.account_for(site)
    # Данс аль шатлалаас ирснийг тодорхойлно: зогсоол → түрээслэгч → глобал
    _site_own = bool((site.qpay_username or "").strip() and (site.qpay_password or "").strip())
    _ten = qpay_svc._tenant_of(site)
    _ten_own = bool(_ten and (getattr(_ten, "qpay_username", None) or "").strip()
                    and (getattr(_ten, "qpay_password", None) or "").strip())
    source = "site" if _site_own else ("tenant" if _ten_own else "global")
    own = source != "global"
    if acc.mock:
        raise HTTPException(400, "QPay туршилтын (mock) горимд байна — бодит данс "
                                 "тохируулаагүй тул шалгах боломжгүй.")

    invoice_no = f"TEST-{site.site_code}-{datetime.utcnow():%Y%m%d%H%M%S}"
    callback = f"{settings.public_base_url}/api/payments/qpay/webhook?payment_id={invoice_no}"
    lines = qpay_svc.build_lines([{
        "description": f"Дансны туршилт — {site.name}",
        "unit_price": amount, "quantity": 1,
    }], acc)
    try:
        inv = await qpay_svc.create_invoice(
            invoice_no, f"Дансны туршилт — {site.name}",
            f"test_{site.site_code}", callback, lines, acc=acc)
    except Exception as e:  # noqa: BLE001 — алдааг админд ойлгомжтой буцаана
        raise HTTPException(400, f"QPay нэхэмжлэл үүсгэж чадсангүй: {_qpay_err(e)}") from e

    _audit(db, user, "QPAY_TEST", "site", site_id,
           {"amount": amount, "invoice_no": invoice_no, "merchant": acc.username})
    db.commit()
    # QPay-ийн e-Barimt-тэй гэрээний invoice code ямагт EB_ угтвартай байдаг.
    # Угтваргүй код өгвөл ЭНГИЙН нэхэмжлэл үүсэж: (1) НӨАТ дээрээс нь нэмэгдэж
    # заасан дүнгээс илүү мөнгө авна (10₮→10.91₮ болсон тохиолдол, 2026-08-01
    # Monnis), (2) төлбөр орсон ч e-Barimt EBARIMT_NOT_ENABLED болно.
    warning = ""
    if settings.qpay_ebarimt and not (acc.invoice_code or "").startswith("EB_"):
        warning = (f"Нэхэмжлэхийн код «{acc.invoice_code}» EB_ угтваргүй байна! "
                   f"QPay-ийн e-Barimt гэрээний код EB_-ээр эхэлдэг (ж: EB_{acc.invoice_code}). "
                   f"Ингэснээс НӨАТ давхар нэмэгдэж илүү дүн авах, e-Barimt үүсэхгүй байх "
                   f"эрсдэлтэй — зогсоолын QPay тохиргоонд зөв кодыг оруулна уу.")
    return {
        "warning": warning,
        "invoice_id": inv["invoice_id"], "invoice_no": invoice_no, "amount": amount,
        "qr_text": inv["qr_text"], "qr_image": inv.get("qr_image", ""),
        "deep_link": inv.get("deep_link", ""), "urls": inv.get("urls", []),
        # Аль данс ашиглагдаж байгааг харуулна — буруу данс руу төлөхөөс сэргийлнэ
        "merchant": acc.username, "invoice_code": acc.invoice_code,
        "district_code": acc.district_code,
        "using_own_account": own,
        "account_source": source,     # site | tenant | global
        "tenant_name": getattr(_ten, "name", None),
    }


@router.post("/sites/{site_id}/qpay-test/check")
async def qpay_test_check(site_id: str, body: dict, db: Session = Depends(get_db),
                          user: User = Depends(require("settings"))):
    """Туршилтын нэхэмжлэл төлөгдсөн эсэхийг шалгаад, төлөгдсөн бол тухайн
    зогсоолын дансаар e-Barimt үүсгэнэ. Буцаах ДДТД/сугалаа нь баримт ЯМАР
    байгууллагын нэрээр үүссэнийг батална."""
    from ..services import qpay as qpay_svc

    enforce_site(user, site_id)
    site = db.get(ParkingSite, site_id)
    if not site:
        raise HTTPException(404, "Зогсоол олдсонгүй")
    invoice_id = (body.get("invoice_id") or "").strip()
    if not invoice_id:
        raise HTTPException(400, "invoice_id заавал")

    acc = qpay_svc.account_for(site)
    try:
        res = await qpay_svc.check_payment(invoice_id, acc=acc)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"Төлбөр шалгаж чадсангүй: {_qpay_err(e)}") from e

    if not res.get("paid"):
        return {"paid": False, "merchant": acc.username}

    out = {"paid": True, "paid_amount": res.get("paid_amount"),
           "g_payment_id": res.get("payment_id"), "merchant": acc.username}

    receiver_type = (body.get("receiver_type") or "CITIZEN").upper()
    try:
        eb = await qpay_svc.create_ebarimt(
            res["payment_id"], receiver_type,
            receiver=body.get("receiver") or None, acc=acc)
        raw = eb.get("raw") or {}
        out.update({
            "ebarimt_ok": bool(eb.get("billId")),
            "ebarimt_id": eb.get("billId"),          # ДДТД
            "lottery": eb.get("lottery"),
            "qr_data": eb.get("qrData"),
            # Баримтын QR-ийг серверт зурж өгнө — утсаараа eBarimt апп-д уншуулж шалгана
            "qr_png": _qr_data_uri(eb.get("qrData")),
            # Баримт ХЭНИЙ нэрээр үүссэн — дансны зөв эсэхийн эцсийн баталгаа.
            # QPay хариудаа merchant_register өгөхгүй бол ДДТД-ийн 2-12-р орон
            # нь баримт олгогч байгууллагын код тул түүгээр нөхнө.
            "merchant_register": raw.get("merchant_register")
                or ((eb.get("billId") or "")[1:12] if eb.get("billId") else None),
            "merchant_branch_code": raw.get("merchant_branch_code"),
            "ebarimt_status": raw.get("ebarimt_status"),
        })
    except Exception as e:  # noqa: BLE001 — төлбөр амжилттай ч баримт унаж болно
        out.update({"ebarimt_ok": False, "ebarimt_error": _qpay_err(e)})
    return out


@router.put("/sites/{site_id}/tariff")
def update_site_tariff(site_id: str, body: dict, db: Session = Depends(get_db),
                       user: User = Depends(require("settings", "discounts"))):
    """Зогсоолд мөрдөх тарифыг өөрчлөх (Санхүү/Админ) — /tariffs Зогсоол-тариф таб."""
    enforce_site(user, site_id)
    site = db.get(ParkingSite, site_id)
    if not site:
        raise HTTPException(404, "Зогсоол олдсонгүй")
    site.tariff_template_id = body.get("tariff_template_id") or None
    _audit(db, user, "UPDATE", "site_tariff", site_id, {"tariff_template_id": site.tariff_template_id})
    db.commit()
    return to_dict(site)


# ─────────────────────────── Төхөөрөмж ───────────────────────────
@router.get("/devices")
def list_devices(site_id: str | None = None, include_deleted: bool = False,
                 db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    from datetime import timedelta
    q = db.query(Device)
    if not include_deleted:  # устгасан төхөөрөмж UI-д харагдахгүй (Хаалт/Тохиргоо/Камер)
        q = q.filter(Device.status != "deleted")
    allowed = operator_sites(user)  # tenant хэрэглэгч зөвхөн өөрийн зогсоолын төхөөрөмж
    if site_id:
        enforce_site(user, site_id)
        q = q.filter(Device.site_id == site_id)
    elif allowed:
        q = q.filter(Device.site_id.in_(allowed))
    # Онлайн = сүүлийн 3 минутад холбогдсон (heartbeat эсвэл LPR event)
    online_cutoff = datetime.utcnow() - timedelta(minutes=3)
    devices = q.order_by(Device.created_at).all()
    # Камер бүрийн сүүлд дугаар уншсан цагийг НЭГ query-ээр (төхөөрөмж тус бүрт MAX хийхгүй)
    from sqlalchemy import func
    cam_ids = [d.id for d in devices if d.device_type == "camera"]
    last_plate_by_dev = dict(
        db.query(LprEvent.device_id, func.max(LprEvent.created_at))
        .filter(LprEvent.device_id.in_(cam_ids), LprEvent.accepted.is_(True))
        .group_by(LprEvent.device_id).all()) if cam_ids else {}
    from ..services.camera_sessions import foreign_info
    out = []
    for d in devices:
        online = bool(d.last_seen and d.last_seen >= online_cutoff)
        # Сүүлд дугаар уншсан цаг — "онлайн боловч танихаа больсон" гацааг илрүүлнэ
        last_plate = last_plate_by_dev.get(d.id) if d.device_type == "camera" else None
        # Камерт манайхаас ӨӨР IP холбогдсон бол UI-д харуулна (өөр систем зэрэг
        # ашиглаж буйн баримт — camera_sessions сервис 5 мин тутам шинэчилдэг)
        who = foreign_info(d.id) if d.device_type == "camera" else None
        out.append(to_dict(d, extra={"site_name": d.site.name if d.site else None,
                                     "online": online,
                                     "last_plate_at": last_plate.isoformat() if last_plate else None,
                                     "foreign_sessions": (who or {}).get("sessions") or [],
                                     "foreign_checked_at": (who or {}).get("checked_at")}))
    return out


@router.post("/devices")
def create_device(payload: schemas.DeviceCreate, db: Session = Depends(get_db), user: User = Depends(require("settings"))):
    body = payload.dump()
    enforce_site(user, body.get("site_id"))
    device = Device(**{k: body[k] for k in
                       ("site_id", "name", "device_type", "vendor", "model", "ip_address",
                        "lane_no", "lane_dir", "auto_open", "username", "password")
                       if k in body})
    device.password = encrypt_secret(device.password)  # DB-д ил бичихгүй
    device.device_key = f"{body.get('device_type','dev')}-{secrets.token_hex(8)}"
    if device.device_type == "camera" and device.ip_address and not device.model:
        # Загварыг камераас нь автоматаар татна (magicBox CGI, 4с timeout)
        from ..services.device_auto import fetch_camera_model
        device.model = fetch_camera_model(device.ip_address, device) or ""
    db.add(device)
    db.flush()
    _audit(db, user, "CREATE", "device", device.id, body)
    if device.device_type == "camera":
        # Камер бүртгэмэгц ижил эгнээнд хаалт автоматаар үүснэ/сэргэнэ — админ гараар нэмэхгүй
        from ..services.device_auto import ensure_lane_barriers
        ensure_lane_barriers(db)
    db.commit()
    return to_dict(device)


@router.put("/devices/{device_id}")
def update_device(device_id: str, payload: schemas.DeviceUpdate, db: Session = Depends(get_db),
                  user: User = Depends(require("settings"))):
    body = payload.dump()
    device = db.get(Device, device_id)
    if not device:
        raise HTTPException(404, "Төхөөрөмж олдсонгүй")
    enforce_site(user, device.site_id)
    if body.get("site_id"):
        enforce_site(user, body["site_id"])
    for k in ("name", "device_type", "vendor", "model", "ip_address", "lane_no",
              "lane_dir", "auto_open", "status", "site_id", "username", "password"):
        if k in body:
            val = body[k]
            # Нэвтрэх мэдээллийг хоосноор илгээвэл цэвэрлэнэ (глобал .env руу уналт)
            if k in ("username", "password") and isinstance(val, str):
                val = val.strip() or None
            if k == "password":
                val = encrypt_secret(val)  # DB-д ил бичихгүй
            setattr(device, k, val)
    _audit(db, user, "UPDATE", "device", device_id, body)
    db.commit()
    return to_dict(device)


@router.post("/devices/{device_id}/test-connection")
async def test_device_connection(device_id: str, db: Session = Depends(get_db),
                                 user: User = Depends(require("settings", "barriers"))):
    """Сервер → төхөөрөмж холболт шалгах (TCP connect камерын web порт руу).
    Хариу: {reachable, ms, detail}. Камерын IP-г урьдчилан бүртгэсэн байх ёстой."""
    import asyncio
    import time
    device = db.get(Device, device_id)
    if not device:
        raise HTTPException(404, "Төхөөрөмж олдсонгүй")
    if not device.ip_address:
        return {"reachable": False, "detail": "IP хаяг бүртгэгдээгүй байна"}
    port = 80
    t0 = time.monotonic()
    try:
        fut = asyncio.open_connection(device.ip_address, port)
        reader, writer = await asyncio.wait_for(fut, timeout=3.0)
        writer.close()
        ms = int((time.monotonic() - t0) * 1000)
        return {"reachable": True, "ms": ms,
                "detail": f"Сервер {device.ip_address}:{port} руу хүрч байна ({ms}ms)"}
    except asyncio.TimeoutError:
        return {"reachable": False, "detail": f"{device.ip_address}:{port} — timeout (routing/firewall)"}
    except Exception as e:
        return {"reachable": False, "detail": f"{device.ip_address} — {e}"}


@router.delete("/devices/{device_id}")
def delete_device(device_id: str, db: Session = Depends(get_db), user: User = Depends(require("settings"))):
    device = db.get(Device, device_id)
    if not device:
        raise HTTPException(404, "Төхөөрөмж олдсонгүй")
    enforce_site(user, device.site_id)
    device.status = "deleted"
    _audit(db, user, "DELETE", "device", device_id)
    db.commit()
    return {"ok": True}


# ─────────────────────────── Тарифын загвар ───────────────────────────
@router.get("/tariff-templates")
def list_templates(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    templates = db.query(TariffTemplate).order_by(TariffTemplate.created_at).all()
    return [to_dict(t, extra={"tiers": [to_dict(x) for x in t.tiers]}) for t in templates]


@router.post("/tariff-templates")
def create_template(payload: schemas.TariffTemplateCreate, db: Session = Depends(get_db), user: User = Depends(require("settings", "discounts"))):
    body = payload.dump()
    t = TariffTemplate(
        name=body["name"],
        free_minutes=body.get("free_minutes", 0),
        grace_minutes=body.get("grace_minutes", 15),
        prepaid_price=body.get("prepaid_price", 0),
        extra_hour_price=body.get("extra_hour_price", 0),
        daily_cap=body.get("daily_cap"),
    )
    db.add(t)
    db.flush()
    for tier in body.get("tiers", []):
        db.add(TariffTier(template_id=t.id, upto_minutes=tier["upto_minutes"], price=tier["price"]))
    _audit(db, user, "CREATE", "tariff_template", t.id, body)
    db.commit()
    db.refresh(t)
    return to_dict(t, extra={"tiers": [to_dict(x) for x in t.tiers]})


@router.put("/tariff-templates/{template_id}")
def update_template(template_id: str, payload: schemas.TariffTemplateUpdate, db: Session = Depends(get_db),
                    user: User = Depends(require("settings", "discounts"))):
    body = payload.dump()
    t = db.get(TariffTemplate, template_id)
    if not t:
        raise HTTPException(404, "Загвар олдсонгүй")
    for k in ("name", "free_minutes", "grace_minutes", "prepaid_price",
              "extra_hour_price", "daily_cap", "is_active"):
        if k in body:
            setattr(t, k, body[k])
    if "tiers" in body:
        db.query(TariffTier).filter(TariffTier.template_id == t.id).delete()
        for tier in body["tiers"]:
            db.add(TariffTier(template_id=t.id, upto_minutes=tier["upto_minutes"], price=tier["price"]))
    _audit(db, user, "UPDATE", "tariff_template", template_id, body)
    db.commit()
    db.refresh(t)
    return to_dict(t, extra={"tiers": [to_dict(x) for x in t.tiers]})


# ─────────────────────────── Хөнгөлөлт ───────────────────────────
@router.get("/discounts")
def list_discounts(db: Session = Depends(get_db), user: User = Depends(require("discounts", "cashier"))):
    return [to_dict(d) for d in db.query(Discount).order_by(Discount.created_at).all()]


@router.post("/discounts")
def create_discount(body: dict, db: Session = Depends(get_db), user: User = Depends(require("discounts"))):
    d = Discount(name=body["name"], discount_type=body["discount_type"], value=body["value"])
    db.add(d)
    db.flush()
    _audit(db, user, "CREATE", "discount", d.id, body)
    db.commit()
    return to_dict(d)


@router.put("/discounts/{discount_id}")
def update_discount(discount_id: str, body: dict, db: Session = Depends(get_db),
                    user: User = Depends(require("discounts"))):
    d = db.get(Discount, discount_id)
    if not d:
        raise HTTPException(404, "Хөнгөлөлт олдсонгүй")
    for k in ("name", "discount_type", "value", "is_active"):
        if k in body:
            setattr(d, k, body[k])
    _audit(db, user, "UPDATE", "discount", discount_id, body)
    db.commit()
    return to_dict(d)


# ─────────────────────────── Бүртгэлтэй жолооч ───────────────────────────
@router.get("/drivers")
def list_drivers(q: str | None = None, company: str | None = None,
                 site_id: str | None = None, db: Session = Depends(get_db),
                 user: User = Depends(require("drivers"))):
    query = db.query(RegisteredDriver).order_by(RegisteredDriver.company,
                                                RegisteredDriver.plate_number)
    if q:
        # Дугаар, эзэмшигч, байгууллагын аль нэгээр нь хайна (олон зуун мөртэй
        # жагсаалтад зөвхөн дугаараар хайх нь хангалтгүй)
        like = f"%{q.strip()}%"
        query = query.filter(
            RegisteredDriver.plate_number.ilike(f"%{q.strip().upper()}%")
            | RegisteredDriver.full_name.ilike(like)
            | RegisteredDriver.company.ilike(like))
    if company:
        query = query.filter(RegisteredDriver.company == company)
    allowed = operator_sites(user)  # tenant хэрэглэгч зөвхөн өөрийн зогсоолын машинууд
    if site_id == "global":
        # Зөвхөн «Бүх зогсоол»-ын эрхтэй (site_id NULL) машинууд — түрээслэгчийн
        # хэрэглэгчид зөвхөн өөрийн түрээслэгчийнхийг харна
        query = query.filter(RegisteredDriver.site_id.is_(None))
        if allowed is not None:
            query = query.filter(RegisteredDriver.tenant_id == user.tenant_id)
    elif site_id:
        enforce_site(user, site_id)
        query = query.filter(RegisteredDriver.site_id == site_id)
    elif allowed:
        cond = RegisteredDriver.site_id.in_(allowed)
        if user.tenant_id:
            # Түрээслэгчийн «бүх зогсоолын» машид мөн харагдана
            cond = cond | ((RegisteredDriver.site_id.is_(None))
                           & (RegisteredDriver.tenant_id == user.tenant_id))
        query = query.filter(cond)
    return [to_dict(d, extra={"site_name": d.site.name if d.site else "Бүх зогсоол"})
            for d in query.limit(2000).all()]


@router.get("/drivers/companies")
def list_driver_companies(db: Session = Depends(get_db), user: User = Depends(require("drivers"))):
    """Байгууллагын жагсаалт + машины тоо — шүүлтүүрт."""
    from sqlalchemy import func as _f
    q = (db.query(RegisteredDriver.company, _f.count(RegisteredDriver.id))
         .filter(RegisteredDriver.company.isnot(None), RegisteredDriver.company != ""))
    allowed = operator_sites(user)
    if allowed:
        q = q.filter(RegisteredDriver.site_id.in_(allowed))
    rows = q.group_by(RegisteredDriver.company).order_by(RegisteredDriver.company).all()
    return [{"company": c, "count": n} for c, n in rows]


def _parse_dt(value: str, field: str) -> datetime:
    """ISO огноог уншина — буруу формат 500 биш 400 өгнө."""
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        raise HTTPException(400, f"{field}: огнооны формат буруу (ISO байх ёстой): {value!r}")


def _site_tenant(db, site_id: str | None):
    """Зогсоолын түрээслэгч (site байхгүй/NULL бол None)."""
    if not site_id:
        return None
    return db.query(ParkingSite.tenant_id).filter(ParkingSite.id == site_id).scalar()


@router.post("/drivers")
def create_driver(payload: schemas.DriverCreate, db: Session = Depends(get_db), user: User = Depends(require("drivers"))):
    body = payload.dump()
    # Tenant хэрэглэгч зөвхөн өөрийн зогсоолд бүртгэнэ. site_id=null («Бүх зогсоол»)
    # нь одоо ТҮРЭЭСЛЭГЧИЙН бүх зогсоол гэсэн утгатай тул tenant хэрэглэгчид аюулгүй;
    # tenant-гүй хэрэглэгч (SUPER) NULL сонговол системийн түвшний (хуучин) бүртгэл болно.
    allowed = operator_sites(user)
    site_id = body.get("site_id")
    if allowed is not None:
        if site_id and site_id not in allowed:
            raise HTTPException(403, "Зөвхөн өөрийн хариуцах зогсоолд машин бүртгэх эрхтэй.")
        if not site_id and not user.tenant_id:
            raise HTTPException(403, "«Бүх зогсоол» бүртгэлийг түрээслэгчийн хэрэглэгч л хийнэ — зогсоолоо сонгоно уу.")
    d = RegisteredDriver(
        plate_number=body["plate_number"].upper().replace(" ", ""),
        full_name=body.get("full_name", ""), phone=body.get("phone", ""),
        company=body.get("company", ""), note=body.get("note", ""),
        contract_type=body.get("contract_type", "MONTHLY"),
        tenant_id=_site_tenant(db, site_id) if site_id else user.tenant_id,
        site_id=site_id, monthly_fee=body.get("monthly_fee", 0),
        valid_from=_parse_dt(body["valid_from"], "valid_from") if body.get("valid_from") else datetime.utcnow(),
        valid_to=_parse_dt(body["valid_to"], "valid_to"),
    )
    db.add(d)
    db.flush()
    _audit(db, user, "CREATE", "driver", d.id, body)
    db.commit()
    return to_dict(d)


@router.put("/drivers/{driver_id}")
def update_driver(driver_id: str, payload: schemas.DriverUpdate, db: Session = Depends(get_db),
                  user: User = Depends(require("drivers"))):
    body = payload.dump()
    d = db.get(RegisteredDriver, driver_id)
    if not d:
        raise HTTPException(404, "Жолооч олдсонгүй")
    allowed = operator_sites(user)
    if allowed is not None:
        # Өөрийн зогсоолын бүртгэлийг л засна; өөр/бүх зогсоол руу шилжүүлэхгүй
        if d.site_id not in allowed:
            raise HTTPException(403, "Энэ бүртгэл таны хариуцах зогсоолынх биш байна.")
        if "site_id" in body and body["site_id"] not in allowed:
            raise HTTPException(403, "Зөвхөн өөрийн хариуцах зогсоол руу шилжүүлэх эрхтэй.")
    for k in ("full_name", "phone", "contract_type", "site_id", "monthly_fee",
              "is_active", "company", "note"):
        if k in body:
            setattr(d, k, body[k])
    if "site_id" in body:
        # Түрээслэгчийн харьяалал зогсоолыг нь дагана; NULL («бүх зогсоол») болгоход
        # засварлагчийн түрээслэгч (эсвэл хуучин утга) хэвээр
        d.tenant_id = _site_tenant(db, body["site_id"]) if body["site_id"] else (user.tenant_id or d.tenant_id)
    if body.get("plate_number"):
        d.plate_number = body["plate_number"].upper().replace(" ", "")
    for k in ("valid_from", "valid_to"):
        if body.get(k):
            setattr(d, k, _parse_dt(body[k], k))
    _audit(db, user, "UPDATE", "driver", driver_id, body)
    db.commit()
    return to_dict(d)


@router.post("/drivers/import")
async def import_drivers(file: UploadFile = File(...), site_id: str = Form(""),
                         contract_type: str = Form("CONTRACT"),
                         valid_days: int = Form(365),
                         replace: bool = Form(False),
                         dry_run: bool = Form(False),
                         db: Session = Depends(get_db),
                         user: User = Depends(require("drivers"))):
    """Гэрээт машины жагсаалтыг Excel-ээс импортлох (олон хуудас = олон байгууллага).

    dry_run=true үед DB хөндөхгүй, зөвхөн юу орохыг буцаана — админ урьдчилан
    хараад баталгаажуулна. replace=true бол файлд байхгүй хуучин бүртгэлийг
    ИДЭВХГҮЙ болгоно (устгахгүй — буруу импортоос сэргээх боломж үлдэнэ)."""
    from ..services.driver_import import import_rows, parse_workbook

    allowed = operator_sites(user)
    if allowed is not None and (site_id or None) not in allowed:
        raise HTTPException(403, "Зөвхөн өөрийн хариуцах зогсоолд импорт хийх эрхтэй "
                                 "(зогсоолоо сонгоно уу).")

    if not file.filename.lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(400, "Зөвхөн .xlsx файл дэмжинэ")
    data = await file.read()
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(400, "Файл хэт том (10MB дээш)")

    try:
        # openpyxl parse нь sync — thread дээр ажиллуулж event loop-ыг блоклохгүй
        # (том файл дээр хэдэн секунд үргэлжилж хаалт/LPR-ийг царцаадаг байсан)
        import asyncio as _aio
        rows, warnings = await _aio.to_thread(parse_workbook, data)
    except Exception as e:  # noqa: BLE001 — эвдэрсэн файлд ойлгомжтой хариу
        raise HTTPException(400, f"Excel уншиж чадсангүй: {type(e).__name__}: {e}") from e
    if not rows:
        raise HTTPException(400, "Нэг ч улсын дугаар олдсонгүй. Хуудсанд «Улсын дугаар» "
                                 "гэсэн гарчигтай багана байх шаардлагатай.")

    by_company: dict[str, int] = {}
    for r in rows:
        by_company[r["company"]] = by_company.get(r["company"], 0) + 1
    preview = {"total": len(rows), "companies": by_company, "warnings": warnings[:50],
               "sample": rows[:10]}

    if dry_run:
        return {"dry_run": True, **preview}

    res = import_rows(db, rows, site_id or None, contract_type=contract_type,
                      valid_days=valid_days, deactivate_missing=replace)
    _audit(db, user, "IMPORT", "driver", site_id or "-",
           {"file": file.filename, **{k: v for k, v in res.items()}})
    db.commit()
    return {"dry_run": False, **preview, **res}


# ─────────────────────────── Хар жагсаалт ───────────────────────────
@router.get("/blacklist")
def list_blacklist(db: Session = Depends(get_db), user: User = Depends(require("blacklist", "cashier"))):
    return [to_dict(b) for b in
            db.query(BlacklistEntry).order_by(BlacklistEntry.created_at.desc()).limit(500).all()]


@router.post("/blacklist")
def add_blacklist(body: dict, db: Session = Depends(get_db), user: User = Depends(require("blacklist"))):
    b = BlacklistEntry(plate_number=body["plate_number"].upper().replace(" ", ""),
                       reason=body.get("reason", ""), created_by=user.username)
    db.add(b)
    db.flush()
    _audit(db, user, "CREATE", "blacklist", b.id, body)
    db.commit()
    return to_dict(b)


@router.put("/blacklist/{entry_id}")
def update_blacklist(entry_id: str, body: dict, db: Session = Depends(get_db),
                     user: User = Depends(require("blacklist"))):
    b = db.get(BlacklistEntry, entry_id)
    if not b:
        raise HTTPException(404, "Бичлэг олдсонгүй")
    for k in ("reason", "is_active"):
        if k in body:
            setattr(b, k, body[k])
    _audit(db, user, "UPDATE", "blacklist", entry_id, body)
    db.commit()
    return to_dict(b)


# ─────────────────────────── Хэрэглэгч (SUPER_ADMIN) ───────────────────────────
def _user_sites(u: User) -> set:
    """Хэрэглэгчийн хамаарах зогсоолуудын олонлог (хоосон = бүх зогсоол/компанийн түвшин)."""
    return {s for s in (u.site_ids or []) if s} or ({u.site_id} if u.site_id else set())


def _enforce_user_scope(user: User, target_sites: set, action: str):
    """Tenant админ зөвхөн ӨӨРИЙН түрээслэгчийн зогсоолуудын хүрээнд хэрэглэгч
    удирдана. SUPER_ADMIN бүх түрээслэгчид эрхтэй. Шинэ хэрэглэгч creator-ийн
    tenant-д ямагт хязгаарлагддаг тул хоосон target (=түрээслэгчийн бүх зогсоол) OK."""
    allowed = operator_sites(user)
    if allowed is None:
        return  # SUPER_ADMIN эсвэл системийн түвшин
    # Заасан зогсоолууд өөрийн хүрээнд байх ёстой; хоосон бол tenant-ийн бүх зогсоол
    if target_sites and not target_sites.issubset(set(allowed)):
        raise HTTPException(403, f"Зөвхөн өөрийн хариуцах зогсоолын ажилтныг {action} эрхтэй.")


@router.get("/users")
def list_users(db: Session = Depends(get_db), user: User = Depends(require_role("ADMIN", "SUPER_ADMIN"))):
    users = db.query(User).order_by(User.created_at).all()
    allowed = operator_sites(user)
    if allowed is not None:
        # Tenant админ зөвхөн өөрийн зогсоолуудтай огтлолцсон ажилтнууд + өөрийгөө харна
        aset = set(allowed)
        users = [u for u in users if u.id == user.id or (_user_sites(u) & aset)
                 or (user.tenant_id and u.tenant_id == user.tenant_id)]
    # Түрээслэгчийн нэр — "Бүгд" гэхийн оронд аль байгууллагынх нь харагдана
    tnames = {t.id: t.name for t in db.query(Tenant).all()}
    return [to_dict(u, extra={"tenant_name": tnames.get(u.tenant_id)}) for u in users]


@router.post("/users")
def create_user(payload: schemas.UserCreate, db: Session = Depends(get_db), user: User = Depends(require_role("ADMIN", "SUPER_ADMIN"))):
    body = payload.dump()
    if db.query(User).filter(User.username == body["username"]).first():
        raise HTTPException(400, "Нэвтрэх нэр давхардаж байна")
    # SUPER_ADMIN-ыг API/UI-аас үүсгэхийг хориглоно (зөвхөн DB-ээр) — аюулгүй байдал
    if body.get("role") not in CREATABLE_ROLES:
        raise HTTPException(400, "role буруу байна (SUPER_ADMIN-ыг зөвхөн DB-ээр үүсгэнэ)")
    new_sites = {s for s in (body.get("site_ids") or []) if s} or (
        {body["site_id"]} if body.get("site_id") else set())
    _enforce_user_scope(user, new_sites, "нэмэх")
    _check_password(body.get("password", ""))
    # Tenant: SUPER_ADMIN хүссэнээ онооно; түрээслэгчийн админы үүсгэсэн хэрэглэгч
    # ЗААВАЛ түүний түрээслэгчид харьяалагдана (өөр tenant руу гаргахгүй)
    tenant_id = body.get("tenant_id") if user.role == "SUPER_ADMIN" else user.tenant_id
    u = User(username=body["username"], password_hash=hash_password(body["password"]),
             full_name=body.get("full_name", ""), phone=body.get("phone", ""),
             role=body["role"], site_id=body.get("site_id"), tenant_id=tenant_id or None,
             permissions=_clean_permissions(body.get("permissions"), body["role"]),
             site_ids=_clean_site_ids(body.get("site_ids"), body.get("site_id")))
    db.add(u)
    db.flush()
    _audit(db, user, "CREATE", "user", u.id, {"username": body["username"], "role": body["role"]})
    db.commit()
    return to_dict(u)


@router.put("/users/{user_id}")
def update_user(user_id: str, payload: schemas.UserUpdate, db: Session = Depends(get_db),
                user: User = Depends(require_role("ADMIN", "SUPER_ADMIN"))):
    body = payload.dump()
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(404, "Хэрэглэгч олдсонгүй")
    # SUPER_ADMIN руу ахиулах, эсвэл SUPER_ADMIN-ыг API-аар засахыг хориглоно
    if body.get("role") == "SUPER_ADMIN" or u.role == "SUPER_ADMIN":
        raise HTTPException(403, "SUPER_ADMIN хэрэглэгчийг зөвхөн DB-ээр удирдана")
    if "role" in body and body["role"] not in CREATABLE_ROLES:
        raise HTTPException(400, "role буруу байна")
    if u.id != user.id:
        _enforce_user_scope(user, _user_sites(u), "засах")
    if "site_ids" in body or "site_id" in body:
        new_sites = {s for s in (body.get("site_ids") or []) if s} or (
            {body["site_id"]} if body.get("site_id") else set())
        _enforce_user_scope(user, new_sites, "оноох")
    for k in ("full_name", "phone", "role", "site_id", "is_active"):
        if k in body:
            setattr(u, k, body[k])
    if "tenant_id" in body and user.role == "SUPER_ADMIN":
        u.tenant_id = body["tenant_id"] or None
    if "permissions" in body:
        u.permissions = _clean_permissions(body["permissions"], body.get("role", u.role))
    if "site_ids" in body:
        u.site_ids = _clean_site_ids(body["site_ids"], body.get("site_id", u.site_id))
    if body.get("password"):
        _check_password(body["password"])
        u.password_hash = hash_password(body["password"])
        # Хуучин токенуудыг хүчингүй болгоно (хулгайлагдсан байж болзошгүй)
        u.password_changed_at = datetime.utcnow()
    _audit(db, user, "UPDATE", "user", user_id, {k: v for k, v in body.items() if k != "password"})
    db.commit()
    return to_dict(u)
