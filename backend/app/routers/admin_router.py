"""Тохиргооны CRUD: зогсоол, төхөөрөмж, тарифын загвар, хөнгөлөлт, жолооч, хар жагсаалт, хэрэглэгч."""
import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from ..auth import (ALL_MODULES, enforce_site, get_current_user, grant_site, has_permission,
                    hash_password, operator_sites, require, require_role)
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
CREATABLE_ROLES = ("ADMIN", "FINANCE", "HR", "OPERATOR", "ONLINE_OPERATOR")


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
    # Доторх (nested) зогсоолд ОДОО байгаа машинууд — гадна зогсоолын талбайг
    # ФИЗИКЭЭР эзлээгүй тул «эзэлсэн»-ээс хасна (эс бол нэг машин хоёр удаа
    # тоологдож сул зай худал багасна). Тоог нуухгүй, тусад нь харуулна.
    inside_by_site = dict(
        db.query(ParkingSession.site_id, func.count())
        .filter(ParkingSession.status.in_(["OPEN", "AWAITING_PAYMENT", "PAID"]),
                ParkingSession.paused_since.isnot(None))
        .group_by(ParkingSession.site_id).all())
    child_counts = dict(
        db.query(ParkingSite.parent_site_id, func.count())
        .filter(ParkingSite.parent_site_id.isnot(None))
        .group_by(ParkingSite.parent_site_id).all())
    smap = {s.id: s.name for s in db.query(ParkingSite).all()}
    tmap = {t.id: t for t in db.query(Tenant).all()}
    # Ижил нэртэй зогсоол — БҮХ зогсоолоор (шүүлтээс үл хамааран). Кодыг нь давхардуулах
    # боломжгүй (unique) ч нэр давхардвал оператор/тайлан дээр андуурч, буруу зогсоолын
    # төхөөрөмж тохируулах эрсдэлтэй. Хадгалахыг зогсоохгүй — production дээр аль хэдийн
    # ийм хос байдаг (жишээ нь «Хангарьд» ×2) — зөвхөн анхааруулна.
    name_counts: dict[str, int] = {}
    for (nm,) in db.query(ParkingSite.name).all():
        key = (nm or "").strip().lower()
        name_counts[key] = name_counts.get(key, 0) + 1
    out = []
    for s in sites:
        inside = int(inside_by_site.get(s.id, 0))
        # «Эзэлсэн»-ээс хасах нь ЗӨВХӨН тусдаа site-тай (parent/child) загварт зөв:
        # тэнд машин хоёр session-тэй (гадна + дотор) тул хасахгүй бол давхар
        # тоологдоно. НЭГ site доторх (`nested_inner` камер) загварт session нь
        # ГАНЦ бөгөөд машин тэр зогсоолын талбайд л байгаа — хасвал хаана ч
        # тоологдохгүй болж, сул зай худал ӨСНӨ.
        deduct = inside if child_counts.get(s.id) else 0
        occupied = max(0, occupied_by_site.get(s.id, 0) - deduct)
        _t = tmap.get(s.tenant_id)
        _dup_name = name_counts.get((s.name or "").strip().lower(), 0) > 1
        out.append(to_dict(s, extra={
            "parent_site_name": smap.get(s.parent_site_id),
            "child_site_count": int(child_counts.get(s.id, 0)),
            # Доторх (давхар) зогсоолд ОДОО байгаа машины тоо
            "inside_nested": inside,
            # Тэдгээр нь «эзэлсэн»-ээс хасагдсан эсэх (тусдаа site-тай загварт л хасагдана)
            "inside_nested_excluded": bool(deduct),
            "name_conflict": (f"«{s.name}» гэсэн нэртэй зогсоол {name_counts[(s.name or '').strip().lower()]} "
                              f"ширхэг байна — код нь ялгаатай ({s.site_code}) ч жагсаалт, "
                              f"тайлан, төхөөрөмжийн тохиргоо дээр андуурахад амархан. "
                              f"Нэрийг ялгах эсвэл илүүдлийг нэгтгэнэ үү.") if _dup_name else None,
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
               qpay_district_code=(body.get("qpay_district_code") or "").strip() or None,
               # e-Barimt: баримт ЭНЭ түрээслэгчийн ТТД-ээр гарна
               ebarimt_merchant_tin=(body.get("ebarimt_merchant_tin") or "").strip() or None,
               ebarimt_district_code=(body.get("ebarimt_district_code") or "").strip() or None,
               ebarimt_branch_no=(body.get("ebarimt_branch_no") or "").strip() or None)
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
    for k in ("qpay_username", "qpay_invoice_code", "qpay_branch_code", "qpay_district_code",
              "ebarimt_merchant_tin", "ebarimt_district_code", "ebarimt_branch_no"):
        if k in body:
            setattr(t, k, (body[k] or "").strip() or None)
    if "qpay_password" in body:
        # Хоосон илгээвэл цэвэрлэнэ, хөндөөгүй бол хэвээр
        t.qpay_password = encrypt_secret((body["qpay_password"] or "").strip() or None)
    if "msgbill_api_key" in body:
        # msgbill.mn e-Barimt API түлхүүр (bsk_…) — хоосон илгээвэл салгана
        key = (body["msgbill_api_key"] or "").strip() or None
        if key and not key.startswith("bsk_"):
            raise HTTPException(400, "msgbill түлхүүр «bsk_»-ээр эхлэх ёстой (Dashboard → Developers)")
        t.msgbill_api_key = encrypt_secret(key)
        body["msgbill_api_key"] = "***" if key else None   # audit-д нууц бичихгүй
    if "msgbill_webhook_secret" in body:
        sec = (body["msgbill_webhook_secret"] or "").strip() or None
        if sec and not sec.startswith("whsec_"):
            raise HTTPException(400, "Webhook нууц «whsec_»-ээр эхлэх ёстой")
        t.msgbill_webhook_secret = encrypt_secret(sec)
        body["msgbill_webhook_secret"] = "***" if sec else None
    _check_district(t.qpay_district_code)
    _assign_tenant_sites(db, t.id, body.get("site_ids"))
    _audit(db, user, "UPDATE", "tenant", tenant_id, body)
    db.commit()
    return to_dict(t)


def _assert_parent_ok(db: Session, parent_id: str | None, self_id: str | None = None):
    """«Доторх зогсоол» холбоос зөв эсэх. Зөвхөн НЭГ давхар үүр зөвшөөрнө.

    Хоёр давхар (A дотор B, B дотор C) болвол тоолуур зогсоох логик хоёр
    түвшинд давхарлаж, аль session-ий тоолуур зогсохыг тодорхойлох боломжгүй
    болно. Мөн цикл (A→B→A) үүсвэл тооцоолол мөнхийн давталтад орно.
    """
    if not parent_id:
        return
    if self_id and parent_id == self_id:
        raise HTTPException(400, "Зогсоол өөрийгөө агуулж болохгүй")
    parent = db.get(ParkingSite, parent_id)
    if parent is None:
        raise HTTPException(400, "Эцэг зогсоол олдсонгүй")
    if parent.parent_site_id:
        raise HTTPException(400, (
            f"«{parent.name}» өөрөө өөр зогсоолын дотор байна. Хоёр давхар үүрлэсэн "
            f"зогсоол дэмжигдэхгүй — гадна талын зогсоолыг сонгоно уу."))
    if self_id:
        kids = db.query(ParkingSite).filter(ParkingSite.parent_site_id == self_id).count()
        if kids:
            raise HTTPException(400, (
                f"Энэ зогсоол дотроо {kids} зогсоол агуулж байна — өөрөө өөр зогсоолын "
                f"дотор орох боломжгүй (хоёр давхар үүрлэлт)."))


@router.post("/sites")
def create_site(payload: schemas.SiteCreate, db: Session = Depends(get_db), user: User = Depends(require("settings"))):
    body = payload.dump()
    if db.query(ParkingSite).filter(ParkingSite.site_code == body["site_code"]).first():
        raise HTTPException(400, "site_code давхардаж байна")
    _assert_parent_ok(db, body.get("parent_site_id"))
    site = ParkingSite(**{k: body[k] for k in
                          ("name", "site_code", "zone_code", "address", "capacity",
                           "tariff_template_id", "auto_close_hours", "entry_only_free_hours",
                           "registered_only", "parent_site_id", "transit_max_hours",
                           "no_charge", "qr_url",
                           "qpay_username", "qpay_password", "qpay_invoice_code",
                           "qpay_branch_code", "qpay_district_code",
                           "bank_name", "bank_account", "bank_account_name")
                          if k in body})
    if "screen_config" in body:
        site.screen_config = _check_screen_config(body["screen_config"])
    if "tenant_id" in body and user.role == "SUPER_ADMIN":
        site.tenant_id = body["tenant_id"] or None
    elif user.role != "SUPER_ADMIN" and user.tenant_id:
        # Түрээслэгчийн админы үүсгэсэн зогсоол өөрийнх нь түрээслэгчид харьяалагдана —
        # эс бол operator_sites-д орохгүй тул дараагийн алхамд (төхөөрөмж холбох)
        # enforce_site 403 өгч, зогсоол нь жагсаалтад нь ч харагдахгүй
        site.tenant_id = user.tenant_id
    site.qpay_password = encrypt_secret(site.qpay_password)  # DB-д ил бичихгүй
    _check_district(site.qpay_district_code)
    db.add(site)
    db.flush()
    grant_site(user, site.id)  # үүсгэгчийн хамрах хүрээнд шинэ зогсоолыг нэмнэ
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
    # site_code нь QR URL-д ордог тул давхардвал төлбөр өөр зогсоол руу очно.
    # DB-д unique боловч энд шалгахгүй бол IntegrityError 500 болж хэрэглэгчид
    # ойлгомжгүй алдаа гарна (create_site дээр аль хэдийн ийм шалгуур бий).
    if body.get("site_code") and body["site_code"] != site.site_code:
        if db.query(ParkingSite).filter(ParkingSite.site_code == body["site_code"],
                                        ParkingSite.id != site_id).first():
            raise HTTPException(400, f"«{body['site_code']}» код өөр зогсоолд бүртгэлтэй байна")
    if "parent_site_id" in body:
        _assert_parent_ok(db, body["parent_site_id"], self_id=site_id)
    for k in ("name", "site_code", "zone_code", "address", "capacity", "tariff_template_id",
              "auto_close_hours", "entry_only_free_hours", "registered_only", "is_active",
              "parent_site_id", "transit_max_hours", "barrier_close_sweep_min",
              "no_charge", "qr_url",
              "qpay_username", "qpay_password", "qpay_invoice_code",
              "qpay_branch_code", "qpay_district_code",
              "bank_name", "bank_account", "bank_account_name"):
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


# ─────────── Холболт: төлбөрийн дансдын нэгдсэн жагсаалт ───────────
@router.get("/payment-accounts")
def payment_accounts(db: Session = Depends(get_db),
                     user: User = Depends(require_role("SUPER_ADMIN", "ADMIN"))):
    """Тохиргоо → Холболт → Төлбөрийн данс: бүх түвшний (глобал .env / түрээслэгч /
    зогсоол) QPay дансыг НЭГ дор, данс бүрд «яг аль зогсоолууд энэ данс руу төлж
    байгаа»-г тооцоолж буцаана. Данс шийдэх дүрэм нь qpay.account_for-той ИЖИЛ:
    нэвтрэх хос (нэр+нууц үг) нэг шатлалаас бүтнээрээ ирнэ — зогсоол → түрээслэгч
    → глобал. Нууц утга буцаахгүй (зөвхөн *_set).

    ADMIN мөн харна, гэхдээ хамрах хүрээгээрээ: «Хариуцах зогсоолууд» эсвэл
    түрээслэгчээр хязгаарлагдсан админ зөвхөн өөрийн зогсоолууд болон тэдгээрийн
    түрээслэгчдийн дансыг харна (өөр түрээслэгчийн merchant задрахгүй)."""
    from ..services import msgbill as _msgbill

    def _own_pair(obj) -> bool:
        return bool((getattr(obj, "qpay_username", None) or "").strip()
                    and (getattr(obj, "qpay_password", None) or "").strip())

    def _eb_warn(code: str | None) -> str | None:
        if settings.qpay_ebarimt and code and not code.startswith("EB_"):
            return ("EB_ угтваргүй нэхэмжлэхийн код — НӨАТ давхар нэмэгдэж, "
                    "e-Barimt үүсэхгүй эрсдэлтэй")
        return None

    allowed = operator_sites(user)   # None = бүх зогсоол
    sites_q = db.query(ParkingSite).order_by(ParkingSite.created_at)
    if allowed:
        sites_q = sites_q.filter(ParkingSite.id.in_(allowed))
    sites = sites_q.all()
    tenants_all = {t.id: t for t in db.query(Tenant).all()}
    # Хязгаартай хэрэглэгчид: зөвхөн харагдах зогсоолуудын (болон өөрийн)
    # түрээслэгчдийн дансыг жагсаана — данс ШИЙДЭХДЭЭ бүх түрээслэгчийг ашиглана
    # (зогсоолын жинхэнэ данс нь харагдацаас хамаарахгүй)
    if allowed:
        vis_tids = {s.tenant_id for s in sites if s.tenant_id}
        if getattr(user, "tenant_id", None):
            vis_tids.add(user.tenant_id)
        tenants = {k: v for k, v in tenants_all.items() if k in vis_tids}
    else:
        tenants = tenants_all

    # Зогсоол бүрийн данс аль шатлалаас шийдэгдэж буйг тооцно
    resolved: dict[str, list] = {"global": []}   # account key → [site, ...]
    for s in sites:
        if _own_pair(s):
            resolved.setdefault(f"site:{s.id}", []).append(s)
        elif s.tenant_id and s.tenant_id in tenants_all and _own_pair(tenants_all[s.tenant_id]):
            resolved.setdefault(f"tenant:{s.tenant_id}", []).append(s)
        else:
            resolved["global"].append(s)

    def _site_ref(s):
        return {"id": s.id, "name": s.name, "site_code": s.site_code,
                "is_active": s.is_active}

    accounts = []
    for t in tenants.values():
        if not (getattr(t, "qpay_username", None) or "").strip() and not t.qpay_password:
            continue   # данс огт тохируулаагүй түрээслэгчийг жагсаахгүй
        accounts.append({
            "scope": "tenant", "id": t.id, "name": t.name,
            "merchant": t.qpay_username,
            "invoice_code": t.qpay_invoice_code,
            "branch_code": t.qpay_branch_code,
            "district_code": t.qpay_district_code,
            "qpay_password_set": bool(t.qpay_password),
            "complete": _own_pair(t),   # нэр+нууц үг хоёул бий юу
            "warning": _eb_warn(t.qpay_invoice_code),
            "sites": [_site_ref(s) for s in resolved.get(f"tenant:{t.id}", [])],
        })
    for s in sites:
        if not (s.qpay_username or "").strip() and not s.qpay_password:
            continue
        accounts.append({
            "scope": "site", "id": s.id, "name": s.name,
            "site_code": s.site_code,
            "merchant": s.qpay_username,
            "invoice_code": s.qpay_invoice_code,
            "branch_code": s.qpay_branch_code,
            "district_code": s.qpay_district_code,
            "qpay_password_set": bool(s.qpay_password),
            "complete": _own_pair(s),
            "warning": _eb_warn(s.qpay_invoice_code),
            "sites": [_site_ref(x) for x in resolved.get(f"site:{s.id}", [])],
        })

    bank_accounts = [{
        "site_id": s.id, "site_code": s.site_code, "name": s.name,
        "bank_name": s.bank_name, "bank_account": s.bank_account,
        "bank_account_name": s.bank_account_name,
    } for s in sites if (s.bank_account or "").strip()]

    return {
        "global": {
            "configured": bool(settings.qpay_username),
            "merchant": settings.qpay_username or None,
            "invoice_code": settings.qpay_invoice_code,
            "mock": settings.qpay_mock,
            "sandbox": settings.qpay_sandbox,
            "warning": _eb_warn(settings.qpay_invoice_code),
            "sites": [_site_ref(s) for s in resolved["global"]],
        },
        "accounts": accounts,
        "bank_accounts": bank_accounts,
        # Гадаад API-ийн партнерууд — зөвхөн НЭРС. АНХААР: partner_map нь
        # {api_key: нэр} тул .keys() нь ТҮЛХҮҮРИЙГ задлана — заавал .values()!
        "partners": sorted(set(settings.partner_map().values())),
        # e-Barimt сувгуудын бодит байдал (карт дээр харуулна):
        #   QR (QPay) → QPay e-Barimt 3.0 (qpay_mock бол mock)
        #   бэлэн/карт/дансаар → msgbill (түлхүүртэй зогсоол) → PosAPI (mock=false үед) → суваг байхгүй
        "ebarimt": {
            "mock": settings.ebarimt_mock,
            "mock_receipts": settings.ebarimt_mock_receipts,
            "qpay_ebarimt": settings.qpay_ebarimt,
            "qpay_mock": settings.qpay_mock,
            "merchant_tin": settings.ebarimt_merchant_tin or None,
            "posapi_url": settings.ebarimt_posapi_url,
            # Бэлэн/карт/дансаарын баримт АЛЬ сувгаар: msgbill (тохируулсан зогсоолд) /
            # posapi (бодит PosAPI) / none (суваг байхгүй → FAILED бүртгэгдэнэ)
            "local_channel": ("posapi" if not settings.ebarimt_mock
                              else ("mock" if settings.ebarimt_mock_receipts else "none")),
        },
        # msgbill.mn eBarimt API — глобал түлхүүр (.env) + түрээслэгч бүрийн түлхүүр
        "msgbill": {
            **_msgbill.status_info(db),
            "tenants": [{
                "id": t.id, "name": t.name, "code": t.code,
                "key_set": bool(t.msgbill_api_key),
                "webhook_secret_set": bool(getattr(t, "msgbill_webhook_secret", None)),
                # Ямар түлхүүр АШИГЛАГДАХ вэ: өөрийн → глобал (өөрийн QPay данстай
                # бол глобал руу унахгүй) → байхгүй
                "effective": ("tenant" if t.msgbill_api_key else
                              ("global" if _msgbill.global_config(db)["api_key"] and not _own_pair(t) else None)),
                "sites": [_site_ref(s) for s in sites if s.tenant_id == t.id],
            } for t in tenants.values()],
            # Түрээслэгчгүй зогсоолууд — глобал түлхүүр (байвал)
            "orphan_sites": [_site_ref(s) for s in sites if not s.tenant_id],
        },
    }


# ─────────── Холболт: msgbill.mn e-Barimt API туршилт ───────────
@router.post("/msgbill/test")
async def msgbill_test_receipt(body: dict, db: Session = Depends(get_db),
                               user: User = Depends(require_role("SUPER_ADMIN"))):
    """msgbill.mn түлхүүрийг шалгах — {tenant_id?, api_key?, amount?=10, dry?}.

    api_key өгвөл түүгээр (хадгалахаас өмнө турших), үгүй бол tenant_id-ийн
    хадгалсан түлхүүр, тэр ч байхгүй бол глобал .env түлхүүрээр туршина.
    АНХААР: live (bsk_ live) түлхүүрээр ЖИНХЭНЭ баримт үүсэж сарын тоонд орно —
    тиймээс дүн анхдагчаар 10₮; bsk_test_ түлхүүр симуляц буцаана (test=true)."""
    from ..secretbox import decrypt_secret
    from ..services import msgbill as _msgbill
    key = (body.get("api_key") or "").strip()
    scope = "body"
    if not key and body.get("tenant_id"):
        t = db.get(Tenant, body["tenant_id"])
        if not t:
            raise HTTPException(404, "Түрээслэгч олдсонгүй")
        key = decrypt_secret((t.msgbill_api_key or "").strip())
        scope = f"tenant:{t.code}"
    if not key:
        key = _msgbill.global_config(db)["api_key"]
        scope = "global"
    if not key:
        raise HTTPException(400, "msgbill түлхүүр тохируулаагүй — .env PARKING_MSGBILL_API_KEY "
                                 "эсвэл түрээслэгчийн түлхүүр оруулна уу")
    acc = _msgbill.MsgbillAccount(api_key=key, base_url=settings.msgbill_base_url.rstrip("/"))
    try:
        amount = max(1, int(float(body.get("amount") or 10)))
    except (TypeError, ValueError):
        amount = 10
    idem = f"test-{user.username}-{secrets.token_hex(5)}"
    try:
        raw = await _msgbill.create_receipt(
            acc, amount, description=f"EasyParking туршилт ({user.username})",
            payment_method=str(body.get("payment_method") or "BANK_TRANSFER").replace("BANK_", ""),
            idempotency_key=idem, customer_tin=(body.get("customer_tin") or None))
    except _msgbill.MsgbillError as e:
        _audit(db, user, "MSGBILL_TEST", "msgbill", scope, {"ok": False, "error": str(e)})
        db.commit()
        return {"ok": False, "scope": scope, "error": str(e), "code": e.code, "status": e.status}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "scope": scope, "error": str(e)[:200]}
    _audit(db, user, "MSGBILL_TEST", "msgbill", scope,
           {"ok": bool(raw.get("billId")), "id": raw.get("msgbillId"), "amount": amount,
            "test": raw.get("test")})
    db.commit()
    return {"ok": bool(raw.get("billId")), "scope": scope, "test": raw.get("test"),
            "state": raw.get("state"), "receipt_no": raw.get("billId"),
            "lottery": raw.get("lottery"), "qr_data": raw.get("qrData"),
            "msgbill_id": raw.get("msgbillId"), "error": raw.get("error"), "amount": amount}


@router.put("/msgbill/global")
def msgbill_global_put(body: dict, db: Session = Depends(get_db),
                       user: User = Depends(require_role("SUPER_ADMIN"))):
    """msgbill.mn ГЛОБАЛ тохиргоо UI-аас: {api_key?, methods?}. Прод серверийн .env-д
    SSH-гүй хүрэхэд зориулав — DB утга .env-г дарна; api_key='' → DB утгыг устгаж .env руу буцна.
    methods: "TRANSFER" | "TRANSFER,CASH,CARD" | "ALL"."""
    from ..secretbox import encrypt_secret
    from ..services import msgbill as _msgbill
    from ..services.app_settings import MSGBILL_STATE, get_state, set_state
    st = get_state(db, MSGBILL_STATE)
    if "api_key" in body:
        key = (body.get("api_key") or "").strip()
        if key and not key.startswith("bsk_"):
            raise HTTPException(400, "msgbill түлхүүр «bsk_»-ээр эхлэх ёстой (Dashboard → Developers)")
        if key:
            st["api_key"] = encrypt_secret(key)
        else:
            st.pop("api_key", None)
    if "webhook_secret" in body:
        sec = (body.get("webhook_secret") or "").strip()
        if sec and not sec.startswith("whsec_"):
            raise HTTPException(400, "Webhook нууц «whsec_»-ээр эхлэх ёстой")
        if sec:
            st["webhook_secret"] = encrypt_secret(sec)
        else:
            st.pop("webhook_secret", None)
    if "methods" in body:
        m = str(body.get("methods") or "").upper().replace(" ", "")
        allowed = {"TRANSFER", "CASH", "CARD", "QR", "ALL"}
        parts = [x for x in m.split(",") if x]
        if any(x not in allowed for x in parts):
            raise HTTPException(400, f"methods буруу — зөвшөөрөгдөх: {', '.join(sorted(allowed))}")
        st["methods"] = ",".join(parts)
    set_state(db, MSGBILL_STATE, st, user.username)
    _msgbill.invalidate_cache()
    _audit(db, user, "UPDATE", "msgbill_global", "-",
           {"api_key": "***" if st.get("api_key") else None, "methods": st.get("methods"),
            "webhook_secret": "***" if st.get("webhook_secret") else None})
    db.commit()
    return _msgbill.status_info(db)


# ─────────── Холболт: гадаад API-ийн партнер түлхүүрүүд ───────────
@router.get("/partner-keys")
def list_partner_keys(db: Session = Depends(get_db),
                      user: User = Depends(require_role("SUPER_ADMIN"))):
    """DB-ийн түлхүүрүүд + .env-ийн хуучин партнерууд (зөвхөн нэрс).
    Түлхүүр өөрөө хэзээ ч буцаагдахгүй — үүсгэх мөчид л нэг удаа ил гарна."""
    from ..models import PartnerKey
    site_names = {s.id: s for s in db.query(ParkingSite).all()}
    keys = []
    for k in db.query(PartnerKey).order_by(PartnerKey.created_at).all():
        site = site_names.get(k.site_id)
        keys.append({
            "id": k.id, "name": k.name, "key_prefix": k.key_prefix,
            "scopes": k.scopes, "site_id": k.site_id,
            "site_code": site.site_code if site else None,
            "site_name": site.name if site else None,
            "is_active": k.is_active,
            "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
            "revoked_at": k.revoked_at.isoformat() if k.revoked_at else None,
            "created_by": k.created_by,
            "created_at": k.created_at.isoformat() if k.created_at else None,
        })
    return {"keys": keys, "env_partners": sorted(settings.partner_map().values())}


@router.post("/partner-keys")
def create_partner_key(body: dict, db: Session = Depends(get_db),
                       user: User = Depends(require_role("SUPER_ADMIN"))):
    """Шинэ түлхүүр үүсгэнэ. body: {name, scopes?, site_id?}.
    Түлхүүр ЗӨВХӨН энэ хариултад ил гарна — DB-д sha256 hash нь л үлдэнэ."""
    import hashlib
    from ..models import PartnerKey
    name = (body.get("name") or "").strip().lower()
    if not name or not name.replace("-", "").replace("_", "").isalnum():
        raise HTTPException(400, "Нэр латин үсэг/цифр/зураас байна (тайланд provider болно)")
    scopes = "read,pay" if body.get("can_pay", True) else "read"
    site_id = (body.get("site_id") or "").strip() or None
    if site_id and not db.get(ParkingSite, site_id):
        raise HTTPException(404, "Зогсоол олдсонгүй")
    # Нэр давхардвал тайлан дээр хоёр өөр түлхүүрийн төлбөр нийлж харагдана — хориглоно
    if db.query(PartnerKey).filter(PartnerKey.name == name, PartnerKey.is_active.is_(True)).first():
        raise HTTPException(400, f"«{name}» нэртэй идэвхтэй түлхүүр аль хэдийн байна")
    raw = "pk_" + secrets.token_urlsafe(24)
    k = PartnerKey(name=name, key_hash=hashlib.sha256(raw.encode()).hexdigest(),
                   key_prefix=raw[:10], scopes=scopes, site_id=site_id,
                   created_by=user.username)
    db.add(k)
    _audit(db, user, "CREATE", "partner_key", k.id,
           {"name": name, "scopes": scopes, "site_id": site_id})
    db.commit()
    return {"id": k.id, "name": name, "key": raw, "key_prefix": k.key_prefix,
            "scopes": scopes, "site_id": site_id}


@router.post("/partner-keys/{key_id}/revoke")
def revoke_partner_key(key_id: str, db: Session = Depends(get_db),
                       user: User = Depends(require_role("SUPER_ADMIN"))):
    """Түлхүүрийг хаана — тэр дороо хүчингүй (restart шаардлагагүй). Буцаахгүй:
    санамсаргүй хаасан бол шинэ түлхүүр үүсгэж партнерт өгнө."""
    from ..models import PartnerKey
    k = db.get(PartnerKey, key_id)
    if not k:
        raise HTTPException(404, "Түлхүүр олдсонгүй")
    if not k.is_active:
        return {"ok": True, "already": True}
    k.is_active = False
    k.revoked_at = datetime.utcnow()
    _audit(db, user, "UPDATE", "partner_key", key_id, {"action": "revoke", "name": k.name})
    db.commit()
    return {"ok": True}


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
                 db: Session = Depends(get_db),
                 user: User = Depends(require("devices", "settings", "barriers",
                                             "free_exit", "cashier"))):
    # Эрх нэмэгдсэн шалтгаан: хариу нь камерын `device_key`-г агуулдаг бөгөөд тэр
    # түлхүүрээр /api/lpr/callback руу НЭВТРЭЛТГҮЙГЭЭР хуурамч event илгээж хаалт
    # нээх боломжтой. Өмнө нь зөвхөн get_current_user байсан тул OPERATOR (зориудаар
    # free_exit-гүй болгосон) болон HR хүртэл түлхүүрийг уншиж чаддаг байв.
    # Талбарыг нуухын оронд endpoint-ийг хаасан учир нь: камер тохируулах
    # ажлын урсгал (SiteWizardModal → callback URL) device_key-г ХАРУУЛАХ ёстой.
    #
    # ГЭВЧ хаалттай болгосноор кассын/POS-ийн «хаалт гараар нээх» тасарсан:
    # PAX POS нь `device_id`-г ЗӨВХӨН эндээс олдог тул Моннис дээр 13 удаагийн
    # 403 болж, оператор хаалтаа нээж чадахгүй болов (2026-08-21). Апп нь
    # вендорын build учир endpoint солих нь шинэ хувилбар хүлээнэ. Тиймээс
    # эрх багатай хэрэглэгчид ТАТГАЛЗАХЫН ОРОНД ДАТАГ ХАСНА — зөвхөн хаалтны
    # нууц талбаргүй мөрүүд. `device_key` эрхгүй хүнд ЯМАР Ч ТОХИОЛДОЛД очихгүй.
    if not any(has_permission(user, m) for m in ("devices", "settings", "barriers")):
        from .barriers_router import lean_barrier_rows
        return lean_barrier_rows(db, user, site_id)
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
    # Ижил IP-тэй хоёр камерын бүртгэл — ХАМАГ зогсоолоор (шүүлтээс үл хамааран),
    # учир нь эрсдэл нь яг зогсоол ХООРОНДЫН давхардалд байдаг: нэг зогсоолын
    # буруу нууц үг камерыг түгжээд нөгөөгийн хаалтыг зогсооно.
    dup_by_ip: dict[str, list[Device]] = {}
    for c in db.query(Device).filter(Device.device_type == "camera",
                                     Device.status != "deleted",
                                     Device.ip_address.isnot(None),
                                     Device.ip_address != "").all():
        dup_by_ip.setdefault(c.ip_address, []).append(c)

    # Нэг зогсоол+эгнээ+чиглэлд хэдэн ижил төрлийн төхөөрөмж байна
    dup_by_lane: dict[tuple, list[Device]] = {}
    for c in db.query(Device).filter(Device.device_type.in_(("camera", "barrier")),
                                     Device.status != "deleted").all():
        dup_by_lane.setdefault((c.site_id, c.device_type, c.lane_no, c.lane_dir), []).append(c)

    def _conflict_note(d: Device) -> str | None:
        notes = []
        ip_others = ([c for c in dup_by_ip.get(d.ip_address or "", []) if c.id != d.id]
                     if d.device_type == "camera" else [])
        if ip_others:
            parts = []
            for c in ip_others:
                name = (c.site.name if c.site else None) or c.site_id
                # Эрхгүй зогсоолын нэрийг задлахгүй — давхардсан гэдгийг л мэдэгдэнэ
                parts.append(f"«{name}» → {c.name}" if not allowed or c.site_id in allowed
                             else "өөр зогсоол")
            notes.append(
                f"IP {d.ip_address} нь {', '.join(parts)}-тэй давхцаж байна. Нэг камер хоёр "
                f"мөрөнд бүртгэлтэй бол нэвтрэх нууц үг зөрж, камер түгжигдэн хаалт "
                f"нээгдэхгүй болох эрсдэлтэй — илүүдэл бүртгэлийг устгана уу.")
        lane_others = [c for c in dup_by_lane.get(
            (d.site_id, d.device_type, d.lane_no, d.lane_dir), []) if c.id != d.id]
        if lane_others:
            what = "камер" if d.device_type == "camera" else "хаалт"
            notes.append(
                f"{d.lane_no}-р эгнээний ижил чиглэлд өөр {what} бас байна "
                f"({', '.join(c.name for c in lane_others)}). Хаалт аль камер руу команд "
                f"явуулахаа мэдэхгүй болж, санамсаргүйгээр нэг удаа ажиллаад дараагийнд "
                f"нь унана — эгнээ/чиглэлийг ялгана уу.")
        return "\n\n".join(notes) or None

    from ..services.camera_sessions import foreign_info
    out = []
    for d in devices:
        online = bool(d.last_seen and d.last_seen >= online_cutoff)
        # Сүүлд дугаар уншсан цаг — "онлайн боловч танихаа больсон" гацааг илрүүлнэ
        last_plate = last_plate_by_dev.get(d.id) if d.device_type == "camera" else None
        # Камерт манайхаас ӨӨР IP холбогдсон бол UI-д харуулна (өөр систем зэрэг
        # ашиглаж буйн баримт — camera_sessions сервис 5 мин тутам шинэчилдэг)
        who = foreign_info(d.id) if d.device_type == "camera" else None
        # probe_ok_at — сервер камерт RPC-ээр УСПЕШНО нэвтэрсэн сүүлийн цаг.
        # camera_sessions нь нэвтрэлт+лог татах хоёул амжилттай болсны ДАРАА л
        # checked_at бичдэг тул энэ нь «сервер→камер ажиллаж байна»-гийн баримт.
        # online (камер→сервер) худал байхад энэ үнэн бол асуудал нь стрим/push
        # тохиргоонд гэдгийг UI шууд ялгаж харуулна.
        out.append(to_dict(d, extra={"site_name": d.site.name if d.site else None,
                                     "online": online,
                                     "ip_conflict": _conflict_note(d),
                                     "last_plate_at": last_plate.isoformat() if last_plate else None,
                                     "probe_ok_at": (who or {}).get("checked_at"),
                                     "foreign_sessions": (who or {}).get("sessions") or [],
                                     "foreign_checked_at": (who or {}).get("checked_at"),
                                     "foreign_error": (who or {}).get("error")
                                     or (who or {}).get("skipped")}))
    return out


def _conflicting_camera(db: Session, ip: str | None, device_type: str | None,
                        exclude_id: str | None = None) -> Device | None:
    """Ижил IP-тэй ӨӨР камерын бүртгэл байвал буцаана (устгагдсаныг тооцохгүй).

    Нэг физик камер хоёр мөрөнд бүртгэгдвэл нэвтрэх нэр/нууц үг нь зөрж болно.
    Production дээр (2026-08-07) 10.0.111.12/.13 хос камер «Туушин» болон
    «Номадс» гэсэн ХОЁР зогсоолд өөр өөр нэвтрэлтээр бүртгэгдсэн байв: буруу
    нууц үгтэй зогсоолын урсгал камерын remainLoginTimes-ыг шавхаж, камер
    өөрийгөө 300 секунд түгжсэн — тэр хугацаанд нөгөө зогсоолын ХААЛТ ч
    нээгдэхээ больсон. Тиймээс шинэ давхардлыг хадгалахгүй.

    Зөвхөн камер хооронд шалгана: all-in-one ITC-д хаалт нь камерынхаа релеэр
    ажилладаг тул хаалтын мөр камерынхаа IP-г зориуд хуваалцаж болно.
    """
    ip = (ip or "").strip()
    if not ip or device_type != "camera":
        return None
    q = db.query(Device).filter(Device.device_type == "camera",
                                Device.ip_address == ip,
                                Device.status != "deleted")
    if exclude_id:
        q = q.filter(Device.id != exclude_id)
    return q.first()


def _conflicting_lane(db: Session, site_id: str | None, device_type: str | None,
                      lane_no, lane_dir: str | None,
                      exclude_id: str | None = None) -> Device | None:
    """Нэг зогсоолын нэг эгнээ+чиглэлд хоёр дахь ижил төрлийн төхөөрөмж байвал буцаана.

    `_resolve_device` нь хаалтыг «ижил эгнээний камер»-аар олдог тул нэг эгнээнд
    хоёр камер бүртгэлтэй бол аль нь сонгогдох нь тодорхойгүй болно — нэг удаа
    ажиллаад дараагийнд нь өөр камер руу очиж унана (2026-08-07 Туушин/Рашбулаг).
    """
    if device_type not in ("camera", "barrier") or lane_no is None or not lane_dir:
        return None
    q = db.query(Device).filter(Device.site_id == site_id,
                                Device.device_type == device_type,
                                Device.lane_no == lane_no,
                                Device.lane_dir == lane_dir,
                                Device.status != "deleted")
    if exclude_id:
        q = q.filter(Device.id != exclude_id)
    return q.first()


def _assert_lane_free(db: Session, site_id: str | None, device_type: str | None,
                      lane_no, lane_dir: str | None, exclude_id: str | None = None):
    other = _conflicting_lane(db, site_id, device_type, lane_no, lane_dir, exclude_id)
    if other is None:
        return
    what = "камер" if device_type == "camera" else "хаалт"
    dir_ru = "орох" if lane_dir == "entry" else "гарах" if lane_dir == "exit" else lane_dir
    raise HTTPException(409, (
        f"Энэ зогсоолын {lane_no}-р эгнээний «{dir_ru}» чиглэлд «{other.name}» гэсэн {what} "
        f"аль хэдийн бүртгэлтэй байна. Нэг эгнээнд хоёр {what} байвал хаалт аль руу нь "
        f"команд явуулахаа мэдэхгүй болж, санамсаргүй байдлаар нэг удаа ажиллаад "
        f"дараагийнд нь унана. Эгнээний дугаар эсвэл чиглэлийг өөр болгоно уу."))


def _assert_ip_free(db: Session, ip: str | None, device_type: str | None,
                    exclude_id: str | None = None):
    other = _conflicting_camera(db, ip, device_type, exclude_id)
    if other is None:
        return
    site = (other.site.name if other.site else None) or other.site_id
    raise HTTPException(409, (
        f"{(ip or '').strip()} — энэ IP «{site}» зогсоолын «{other.name}» камерт аль хэдийн "
        f"бүртгэлтэй байна. Нэг камерыг хоёр мөрөнд бүртгэвэл нэвтрэх нууц үг зөрж, "
        f"камер өөрийгөө түгжиж хаалт нээгдэхээ болих эрсдэлтэй. Хуучин бүртгэлийг "
        f"устгасны дараа эсвэл өөр IP-тэйгээр хадгална уу."))


@router.post("/devices")
def create_device(payload: schemas.DeviceCreate, db: Session = Depends(get_db), user: User = Depends(require("settings"))):
    body = payload.dump()
    enforce_site(user, body.get("site_id"))
    _assert_ip_free(db, body.get("ip_address"), body.get("device_type"))
    _assert_lane_free(db, body.get("site_id"), body.get("device_type"),
                      body.get("lane_no"), body.get("lane_dir"))
    device = Device(**{k: body[k] for k in
                       ("site_id", "name", "device_type", "vendor", "model", "ip_address",
                        "lane_no", "lane_dir", "auto_open", "nested_inner", "username", "password")
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
    # Хадгалахаас ӨМНӨ шалгана — өөрчлөлт хийсний дараа шалгавал autoflush нь
    # шинэ IP-г DB рүү бичээд өөрийгөө «давхардал» болгож харагдуулна.
    _assert_ip_free(db, body.get("ip_address", device.ip_address),
                    body.get("device_type", device.device_type), exclude_id=device_id)
    # Устгагдсаныг сэргээж байгаа бол ч шалгана — сэргээсэн хаалт/камер эгнээндээ
    # хоёр дахь нь болж орж ирвэл дахин тодорхойгүй байдал үүснэ.
    _assert_lane_free(db, body.get("site_id", device.site_id),
                      body.get("device_type", device.device_type),
                      body.get("lane_no", device.lane_no),
                      body.get("lane_dir", device.lane_dir), exclude_id=device_id)
    for k in ("name", "device_type", "vendor", "model", "ip_address", "lane_no",
              "lane_dir", "auto_open", "nested_inner", "status", "site_id", "username", "password"):
        if k in body:
            val = body[k]
            # Нэвтрэх мэдээллийг хоосноор илгээвэл цэвэрлэнэ (глобал .env руу уналт)
            if k in ("username", "password") and isinstance(val, str):
                val = val.strip() or None
            if k == "password":
                val = encrypt_secret(val)  # DB-д ил бичихгүй
            setattr(device, k, val)
    _audit(db, user, "UPDATE", "device", device_id, body)
    # Камерыг ӨӨР ЗОГСООЛ руу зөөх / чиглэлийг нь солих үед тэр зогсоолд хаалт
    # байхгүй байж болно. Өмнө нь энэ баталгаажуулалт зөвхөн ШИНЭЭР үүсгэхэд
    # ажилладаг байсан тул дамжин зогсоол тохируулахаар камераа зөөвөл шинэ
    # зогсоол хаалтгүй үлдэж, дугаар уншсан ч юу ч нээгддэггүй байв.
    if device.device_type == "camera" and device.status == "active":
        from ..services.device_auto import ensure_lane_barriers
        ensure_lane_barriers(db)
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


@router.get("/cameras/snap-state")
def camera_snap_state(user: User = Depends(require("settings", "barriers"))):
    """Зургийн сувгуудын ОДООГИЙН байдал — камерт хүсэлт илгээхгүй, хямд.

    Юуг хардаг вэ:
      • `sources` — эх сурвалж бүрээр session-д хадгалагдсан зургийн тоо
        (`comet` / `event-stream` / `payload` / `snapshot.cgi`). Шинэ суваг
        ажиллаж байгаа эсэхийг ЛОГ УХАЛГҮЙ энэ тоогоор хардаг.
      • `comet` — камер тус бүрийн суваг хэдэн секунд attach хийгдсэн, хэдэн
        зураг өгсөн, сүүлийн зураг хэдэн секундын өмнө ирсэн, филтер аль нь
        батлагдсан, хэдэн удаа дахин холбогдсон, сүүлийн алдаа юу байсан.
      • `cgi` — snapshot.cgi-ийн камер тутмын төлөв (ажилласан URL, дараалсан
        бүтэлгүйтэл, түр зогсоолт хэдэн секунд үлдсэн).
    """
    from ..services.snap_puller import comet_state
    from ..services.snapshot import cgi_state, source_counts
    return {"sources": source_counts(), "comet": comet_state(), "cgi": cgi_state(),
            "settings": {"snap_comet": settings.snap_comet,
                         "snap_comet_ips": settings.snap_comet_ips,
                         "snap_pull": settings.snap_pull,
                         "snapshot_cgi_fallback": settings.snapshot_cgi_fallback}}


@router.post("/cameras/snap-test")
async def camera_snap_test(site_id: str | None = None, db: Session = Depends(get_db),
                           user: User = Depends(require("settings", "barriers"))):
    """Идэвхтэй камер БҮРЭЭС нэг зураг татаж, аль нь ажиллаж байгааг тоогоор гаргана.

    `snapshot.cgi` (амьд кадр) ашиглана — хаалтны команд хүлээж байвал түүнд
    зам тавьдаг, камер тутмын дараалалд ордог `_fetch_from_camera`-ээр дамжина.
    Камерын нөөцийг дүүргэхгүйн тулд зэрэг 4-өөс илүү камер руу хандахгүй.
    Хариу: камер бүрд {ok, ms, bytes, detail}.
    """
    import asyncio
    import time as _time

    from ..services.device_auth import camera_credentials
    from ..services.snapshot import _fetch_from_camera

    q = db.query(Device).filter(Device.device_type == "camera",
                                Device.status == "active",
                                Device.ip_address.isnot(None), Device.ip_address != "")
    allowed = operator_sites(user)   # tenant хэрэглэгч зөвхөн өөрийн зогсоол
    if site_id:
        enforce_site(user, site_id)
        q = q.filter(Device.site_id == site_id)
    elif allowed:
        q = q.filter(Device.site_id.in_(allowed))
    cams = q.all()
    sites = {s.id: s.name for s in db.query(ParkingSite).all()}
    sem = asyncio.Semaphore(4)

    async def one(c: Device) -> dict:
        creds = camera_credentials(c)
        async with sem:
            t0 = _time.monotonic()
            try:
                data = await _fetch_from_camera(c.ip_address, creds)
                ms = int((_time.monotonic() - t0) * 1000)
                if data:
                    return {"site": sites.get(c.site_id), "name": c.name, "ip": c.ip_address,
                            "lane_dir": c.lane_dir, "ok": True, "ms": ms, "bytes": len(data),
                            "detail": f"{len(data) // 1024} KB / {ms}ms"}
                return {"site": sites.get(c.site_id), "name": c.name, "ip": c.ip_address,
                        "lane_dir": c.lane_dir, "ok": False, "ms": ms, "bytes": 0,
                        "detail": "зураг ирсэнгүй (түр зогсоолт эсвэл камер татгалзав)"}
            except Exception as e:  # noqa: BLE001
                return {"site": sites.get(c.site_id), "name": c.name, "ip": c.ip_address,
                        "lane_dir": c.lane_dir, "ok": False,
                        "ms": int((_time.monotonic() - t0) * 1000), "bytes": 0,
                        "detail": f"{type(e).__name__}: {str(e)[:90]}"}

    rows = await asyncio.gather(*[one(c) for c in cams])
    rows = sorted(rows, key=lambda r: (r["site"] or "", r["ip"]))
    return {"total": len(rows), "ok": sum(1 for r in rows if r["ok"]), "rows": rows}


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


@router.post("/tariff-templates/preview")
def preview_tariff(body: dict, user: User = Depends(require("settings", "discounts"))):
    """Тарифыг ХАДГАЛАХААС ӨМНӨ жишээ хугацаанууд дээр тооцож харуулна.

    «Загварыг зөв оруулсан уу» гэдгийг таамаглахын оронд тоогоор батална —
    ЯГ production-ий `tier_price` функцээр бодно (хуулбар логик байхгүй тул
    урьдчилсан харагдац бодит дүнгээс хэзээ ч зөрөхгүй).

    Хариунд `jump` талбар: өмнөх цэгээс хэдэн төгрөгөөр үсэрснийг өгнө —
    «120 мин 2000₮ атлаа 121 мин 5000₮» шиг эгзэгтэй үсрэлтийг админ хардаг.
    """
    from types import SimpleNamespace

    from ..billing import tier_price

    tiers = sorted(
        [SimpleNamespace(upto_minutes=int(t["upto_minutes"]), price=float(t["price"]))
         for t in (body.get("tiers") or []) if t.get("upto_minutes")],
        key=lambda t: t.upto_minutes)
    tpl = SimpleNamespace(
        free_minutes=int(body.get("free_minutes") or 0),
        extra_hour_price=float(body.get("extra_hour_price") or 0),
        daily_cap=float(body.get("daily_cap") or 0),
        tiers=tiers)

    marks = body.get("minutes") or [15, 30, 31, 45, 60, 61, 90, 120, 121, 150,
                                    180, 240, 300, 360, 480, 720, 1440]
    out, prev = [], None
    for m in sorted({int(x) for x in marks if int(x) > 0}):
        if tpl.free_minutes and m <= tpl.free_minutes:
            fee, reason = 0.0, f"эхний {tpl.free_minutes} мин үнэгүй"
        else:
            fee = float(tier_price(tpl, m))
            reason = ""
            if tiers and m > tiers[-1].upto_minutes:
                import math as _m
                hrs = _m.ceil((m - tiers[-1].upto_minutes) / 60)
                reason = f"сүүлийн шатлал + {hrs} цаг × {tpl.extra_hour_price:.0f}₮"
            if tpl.daily_cap and fee > tpl.daily_cap:
                fee, reason = tpl.daily_cap, "хоногийн дээд хязгаарт хүрсэн"
        out.append({"minutes": m, "fee": fee, "reason": reason,
                    "jump": None if prev is None else fee - prev})
        prev = fee
    return {"rows": out}


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
def create_discount(payload: schemas.DiscountCreate, db: Session = Depends(get_db),
                    user: User = Depends(require("discounts"))):
    body = payload.dump()
    d = Discount(name=body["name"], discount_type=body["discount_type"], value=body["value"])
    db.add(d)
    db.flush()
    _audit(db, user, "CREATE", "discount", d.id, body)
    db.commit()
    return to_dict(d)


@router.put("/discounts/{discount_id}")
def update_discount(discount_id: str, payload: schemas.DiscountUpdate, db: Session = Depends(get_db),
                    user: User = Depends(require("discounts"))):
    d = db.get(Discount, discount_id)
    if not d:
        raise HTTPException(404, "Хөнгөлөлт олдсонгүй")
    body = payload.dump()
    # PERCENT/FREE_MINUTES-ийн дээд хязгаарыг ЗӨВХӨН value ирсэн үед схем шалгадаг —
    # төрлийг нь дангаар нь солиход хуучин утга хязгаараас хэтрэх эрсдэлтэй тул дахин шалгана
    new_type = body.get("discount_type", d.discount_type)
    new_value = body.get("value", d.value)
    if new_type == "PERCENT" and new_value > 100:
        raise HTTPException(400, "Хувиар хөнгөлөлт 100-аас их байж болохгүй")
    if new_type == "FREE_MINUTES" and new_value > 1440:
        raise HTTPException(400, "Үнэгүй минут 1440 (1 хоног)-аас их байж болохгүй")
    for k in ("name", "discount_type", "value", "is_active"):
        if k in body:
            setattr(d, k, body[k])
    _audit(db, user, "UPDATE", "discount", discount_id, body)
    db.commit()
    return to_dict(d)


# ─────────────────────────── Бүртгэлтэй жолооч ───────────────────────────
@router.get("/drivers")
def list_drivers(q: str | None = None, company: str | None = None,
                 site_id: str | None = None, contract_type: str | None = None,
                 db: Session = Depends(get_db),
                 user: User = Depends(require("drivers"))):
    query = db.query(RegisteredDriver).order_by(RegisteredDriver.company,
                                                RegisteredDriver.plate_number)
    if contract_type:
        # Төрлөөр шүүх — «Тусгай хэрэгцээт» (SPECIAL) г.м. тусдаа жагсаалт харах
        query = query.filter(RegisteredDriver.contract_type == contract_type)
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


def _hhmm_or_400(v: str | None, field: str) -> str | None:
    """Үнэгүй цагийн цонхны "HH:MM" утга — хоосон бол None (цонхгүй = бүх цагт үнэгүй)."""
    if not v:
        return None
    import re as _re
    if not _re.match(r"^([01]\d|2[0-3]):[0-5]\d$", v):
        raise HTTPException(400, f"{field}: цаг «HH:MM» хэлбэртэй байх ёстой (ж: 08:00)")
    return v


def _driver_in_scope(user: User, allowed: list[str] | None, d: RegisteredDriver) -> bool:
    """Бүртгэл хэрэглэгчийн хамрах хүрээнд байгаа эсэх. «Бүх зогсоол» (site_id
    NULL) бүртгэл нь ӨӨРИЙН түрээслэгчийнх бол хамаарна — эс бол tenant-ийн
    админ өөрийн үүсгэсэн «бүх зогсоолын» бүртгэлээ засаж/устгаж чадахгүй байв."""
    if allowed is None:
        return True
    if d.site_id:
        return d.site_id in allowed
    return bool(user.tenant_id) and d.tenant_id == user.tenant_id


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
        free_from=_hhmm_or_400(body.get("free_from"), "free_from"),
        free_until=_hhmm_or_400(body.get("free_until"), "free_until"),
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
        # Өөрийн зогсоолын (эсвэл өөрийн түрээслэгчийн «бүх зогсоолын») бүртгэлийг л засна
        if not _driver_in_scope(user, allowed, d):
            raise HTTPException(403, "Энэ бүртгэл таны хариуцах зогсоолынх биш байна.")
        if "site_id" in body:
            tgt = body["site_id"]
            # «Бүх зогсоол» (NULL) болгох нь tenant хэрэглэгчид зөвшөөрөгдөнө —
            # бүртгэл нь өөрийнх нь түрээслэгчийн хүрээнд л үйлчилнэ
            if tgt and tgt not in allowed:
                raise HTTPException(403, "Зөвхөн өөрийн хариуцах зогсоол руу шилжүүлэх эрхтэй.")
            if not tgt and not user.tenant_id:
                raise HTTPException(403, "«Бүх зогсоол» болгох эрх түрээслэгчийн хэрэглэгчид л бий.")
    for k in ("full_name", "phone", "contract_type", "site_id", "monthly_fee",
              "is_active", "company", "note"):
        if k in body:
            setattr(d, k, body[k])
    for k in ("free_from", "free_until"):
        if k in body:
            setattr(d, k, _hhmm_or_400(body[k], k))
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


@router.delete("/drivers/{driver_id}")
def delete_driver(driver_id: str, db: Session = Depends(get_db),
                  user: User = Depends(require("drivers"))):
    """Бүртгэлтэй машиныг БҮРМӨСӨН устгана — зөвхөн ADMIN/SUPER_ADMIN.

    (Идэвхгүй болгох нь «Засах» дотор байгаа; устгах нь давхардал цэвэрлэх,
    буруу оруулсан бүртгэлд зориулагдсан.)"""
    if user.role not in ("ADMIN", "SUPER_ADMIN"):
        raise HTTPException(403, "Бүртгэл устгах эрх зөвхөн админд бий.")
    d = db.get(RegisteredDriver, driver_id)
    if not d:
        raise HTTPException(404, "Бүртгэл олдсонгүй")
    allowed = operator_sites(user)
    if not _driver_in_scope(user, allowed, d):
        raise HTTPException(403, "Энэ бүртгэл таны хариуцах зогсоолынх биш байна.")
    info = {"plate": d.plate_number, "name": d.full_name, "company": d.company,
            "contract_type": d.contract_type, "site_id": d.site_id}
    db.delete(d)
    _audit(db, user, "DELETE", "driver", driver_id, info)
    db.commit()
    return {"ok": True, "deleted": info}


@router.get("/drivers/import-template")
def import_template(user: User = Depends(require("drivers"))):
    """Excel импортын загвар файл — гарчиг нь parse_workbook-ийн хайдаг нэрстэй ижил.
    Хуудас бүр = нэг байгууллага гэдгийг 2 жишээ хуудсаар үзүүлнэ."""
    import io

    from fastapi.responses import StreamingResponse
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Байгууллага 1"
    ws.append(["Улсын дугаар", "Эзэмшигч", "Албан тушаал"])
    ws.append(["1234УБА", "Бат-Эрдэнэ", "жишээ мөр — өөрийн жагсаалтаар солино"])
    ws.append(["ДК1234", "", "дипломат дугаар мөн болно"])
    ws2 = wb.create_sheet("Байгууллага 2")
    ws2.append(["Улсын дугаар", "Эзэмшигч", "Албан тушаал"])
    ws2.append(["5678УНА", "Сарнай", "хуудас бүр тусдаа байгууллага болно"])
    for w in (ws, ws2):
        w.column_dimensions["A"].width = 16
        w.column_dimensions["B"].width = 22
        w.column_dimensions["C"].width = 40
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return StreamingResponse(
        buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="drivers_import_template.xlsx"'})


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
    if allowed is not None:
        if site_id and site_id not in allowed:
            raise HTTPException(403, "Зөвхөн өөрийн хариуцах зогсоолд импорт хийх эрхтэй.")
        # «Бүх зогсоол» импорт: tenant хэрэглэгчид зөвшөөрнө (бүртгэл нь өөрийн
        # түрээслэгчийн хүрээнд үйлчилнэ); tenant-гүй хэрэглэгч зогсоол сонгоно
        if not site_id and not user.tenant_id:
            raise HTTPException(403, "«Бүх зогсоол» импортыг түрээслэгчийн хэрэглэгч л хийнэ "
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
                      valid_days=valid_days, deactivate_missing=replace,
                      # «Бүх зогсоол» импортод бүртгэлийг импортлогчийн түрээслэгчид
                      # холбоно — эс бол tenant_id NULL болж хэнд ч харагдахгүй
                      default_tenant_id=user.tenant_id)
    _audit(db, user, "IMPORT", "driver", site_id or "-",
           {"file": file.filename, **{k: v for k, v in res.items()}})
    db.commit()
    return {"dry_run": False, **preview, **res}


# ─────────────────────────── Хар жагсаалт ───────────────────────────
@router.get("/autoclose/rules")
def get_autoclose_rules_api(db: Session = Depends(get_db),
                            user: User = Depends(require("settings"))):
    """Зогсоолын авто цэвэрлэгээний дүрэм (гацсан бүртгэлийг хэзээ хаах)."""
    from ..services.app_settings import get_autoclose_rules
    return get_autoclose_rules(db)


@router.put("/autoclose/rules")
def put_autoclose_rules(body: dict, db: Session = Depends(get_db),
                        user: User = Depends(require("settings"))):
    from ..services.app_settings import set_autoclose_rules
    rules = set_autoclose_rules(db, body or {}, user.username)
    _audit(db, user, "UPDATE", "autoclose_rules", "-", rules)
    db.commit()
    return rules


@router.post("/autoclose/run")
def run_autoclose_now(db: Session = Depends(get_db),
                      user: User = Depends(require("settings"))):
    """Авто цэвэрлэгээг ЯГ ОДОО ажиллуулна (30 мин хүлээхгүй) — тохиргоо
    өөрчилсний дараа үр дүнг шууд харах."""
    from ..services.auto_close import run_once
    closed = run_once()
    _audit(db, user, "AUTO_CLOSE_MANUAL", "session", "-", {"closed": closed})
    db.commit()
    return {"closed": closed}


@router.get("/camsync/rules")
def get_camsync_rules_api(db: Session = Depends(get_db),
                          user: User = Depends(require("settings"))):
    """Камерын лог нөхөлтийн дүрэм + зогсоол бүрийн watermark."""
    from ..services.app_settings import CAMSYNC_STATE, get_camsync_rules, get_state
    rules = get_camsync_rules(db)
    state = get_state(db, CAMSYNC_STATE)
    sites = {s.id: s.name for s in db.query(ParkingSite).all()}
    return {**rules,
            "watermarks": [{"site": sites.get(k, k), "at": v} for k, v in state.items()]}


@router.put("/camsync/rules")
def put_camsync_rules(body: dict, db: Session = Depends(get_db),
                      user: User = Depends(require("settings"))):
    from ..services.app_settings import set_camsync_rules
    rules = set_camsync_rules(db, body or {}, user.username)
    _audit(db, user, "UPDATE", "camsync_rules", "-", rules)
    db.commit()
    return rules


@router.post("/camsync/run")
def run_camsync_now(body: dict | None = None, db: Session = Depends(get_db),
                    user: User = Depends(require("settings"))):
    """Камерын лог нөхөлтийг ЯГ ОДОО ажиллуулна. body: {dry_run: bool}"""
    from ..services.camera_sync import run_once
    dry = bool((body or {}).get("dry_run"))
    rows = run_once(dry_run=dry)
    if not dry:
        _audit(db, user, "CAMERA_SYNC_MANUAL", "session", "-",
               {"created": sum(r.get("created", 0) for r in rows)})
        db.commit()
    return {"dry_run": dry, "rows": rows}


@router.get("/camhealth/rules")
def get_camhealth_rules_api(db: Session = Depends(get_db),
                           user: User = Depends(require("settings"))):
    """Камерын эрүүл мэндийн дүрэм + сүүлийн шалгалтын дүн."""
    from ..services.app_settings import CAMHEALTH_KEY, get_rules
    from ..services.camera_health import last_state
    return {**get_rules(db, CAMHEALTH_KEY), "last": last_state()}


@router.put("/camhealth/rules")
def put_camhealth_rules(body: dict, db: Session = Depends(get_db),
                        user: User = Depends(require("settings"))):
    from ..services.app_settings import CAMHEALTH_KEY, set_rules
    rules = set_rules(db, CAMHEALTH_KEY, body or {}, user.username)
    _audit(db, user, "UPDATE", "camhealth_rules", "-", rules)
    db.commit()
    return rules


@router.post("/camhealth/run")
def run_camhealth_now(body: dict | None = None, db: Session = Depends(get_db),
                      user: User = Depends(require("settings"))):
    """Камерын эрүүл мэндийг ЯГ ОДОО шалгана. body: {dry_run: bool}
    dry_run=true бол зөвхөн ангилна (reboot ХИЙХГҮЙ)."""
    from ..services.camera_health import run_once
    dry = bool((body or {}).get("dry_run"))
    out = run_once(dry_run=dry)
    if not dry and out.get("rebooted"):
        _audit(db, user, "CAMERA_HEALTH_MANUAL", "device", "-",
               {"rebooted": [r["ip"] for r in out["rebooted"]]})
        db.commit()
    return {"dry_run": dry, **out}


@router.get("/blacklist/rules")
def get_blacklist_rules_api(db: Session = Depends(get_db),
                            user: User = Depends(require("blacklist", "cashier"))):
    """Хар жагсаалтын дүрэм — авто-хоригийн босго, орох/гарах хаалтны зан төлөв."""
    from ..services.app_settings import get_blacklist_rules
    return get_blacklist_rules(db)


@router.put("/blacklist/rules")
def put_blacklist_rules(body: dict, db: Session = Depends(get_db),
                        user: User = Depends(require("blacklist"))):
    from ..services.app_settings import set_blacklist_rules
    rules = set_blacklist_rules(db, body or {}, user.username)
    _audit(db, user, "UPDATE", "blacklist_rules", "-", rules)
    db.commit()
    return rules


@router.get("/blacklist")
def list_blacklist(db: Session = Depends(get_db), user: User = Depends(require("blacklist", "cashier"))):
    return [to_dict(b) for b in
            db.query(BlacklistEntry).order_by(BlacklistEntry.created_at.desc()).limit(500).all()]


@router.post("/blacklist/clear")
def clear_blacklist(body: dict, db: Session = Depends(get_db),
                    user: User = Depends(require("blacklist"))):
    """Хар жагсаалтыг нэг дор цэвэрлэх (идэвхтэй бичлэгүүдийг идэвхгүй болгоно).
    body: {auto_only: bool (default true — зөвхөн автомат хоригийг),
           cancel_debts: bool (default false — доорх phantom өрийг ч цуцлах — эс бол
           дараа дахин хар жагсаалтад орж болзошгүй)}.
    Оператор эрхийн хүрээ (tenant) хамаарахгүй — хар жагсаалт зогсоол дамнасан."""
    auto_only = body.get("auto_only", True)
    q = db.query(BlacklistEntry).filter(BlacklistEntry.is_active.is_(True))
    if auto_only:
        q = q.filter(BlacklistEntry.reason.ilike("%автомат хориг%"))
    entries = q.all()
    plates = {e.plate_number for e in entries}
    for e in entries:
        e.is_active = False
    canceled = 0
    if body.get("cancel_debts") and plates:
        # Автомат хоригийг үүсгэсэн PENDING нөхөн төлбөрийг цуцалж, дахин
        # хар жагсаалтад орохоос сэргийлнэ (phantom/тест өр цэвэрлэх)
        comps = (db.query(Compensation)
                 .filter(Compensation.plate_number.in_(list(plates)),
                         Compensation.status == "PENDING").all())
        for c in comps:
            c.status = "CANCELLED"
            canceled += 1
    _audit(db, user, "BLACKLIST_CLEAR", "blacklist", "-",
           {"deactivated": len(entries), "canceled_debts": canceled, "auto_only": auto_only})
    db.commit()
    return {"deactivated": len(entries), "canceled_debts": canceled}


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


def _guard_self_privileges(u: User, body: dict):
    """Хэрэглэгч ӨӨРИЙНХӨӨ эрх/хамрах хүрээг өөрчлөхийг хориглоно.

    Өмнө нь өөрийгөө засахад ямар ч шалгалт байгаагүй тул нэг зогсоолоор
    хязгаарлагдсан админ `{"site_ids": [], "site_id": null}` илгээхэд
    _clean_site_ids нь None буцааж, operator_sites() «бүх зогсоол» гэж үзэн,
    тэр админ БҮХ түрээслэгчийн дата руу хандах эрхтэй болдог байв.

    Талбар байгаа эсэхээр биш, УТГА нь өөрчлөгдсөн эсэхээр шалгана: Users.jsx
    нь профайл хадгалахад бүтэн объектыг (role/site_ids/permissions хамт)
    буцааж илгээдэг тул зүгээр л түлхүүр байгаад 403 өгвөл өөрийн нэр/утас/
    нууц үгээ шинэчлэх хэвийн үйлдэл эвдэрнэ.
    """
    changed = []
    if "role" in body and body["role"] != u.role:
        changed.append("role")
    if "is_active" in body and bool(body["is_active"]) != bool(u.is_active):
        changed.append("is_active")
    if "tenant_id" in body and (body["tenant_id"] or None) != (u.tenant_id or None):
        changed.append("tenant_id")
    if "permissions" in body:
        new_perms = _clean_permissions(body["permissions"], body.get("role", u.role))
        if set(new_perms or []) != set(u.permissions or []):
            changed.append("permissions")
    if "site_ids" in body or "site_id" in body:
        new_sites = _clean_site_ids(body.get("site_ids", u.site_ids),
                                    body.get("site_id", u.site_id))
        if set(new_sites or []) != set(u.site_ids or []):
            changed.append("site_ids")
    if changed:
        raise HTTPException(
            403, "Өөрийн эрх/хамрах хүрээг өөрчлөх боломжгүй "
                 f"({', '.join(changed)}) — SUPER_ADMIN-д хандана уу.")


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
    elif user.role != "SUPER_ADMIN":
        _guard_self_privileges(u, body)
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
