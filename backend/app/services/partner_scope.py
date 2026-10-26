"""Гадаад API (/api/v1, SocialPay, захиалга) түлхүүрийн ЗОГСООЛЫН ХҮРЭЭ — нэг эх.

2026-10-26 (Тэмүүжин): «Бүх зогсоол» гэдэг хүрээ = ТУХАЙН ТҮРЭЭСЛЭГЧИЙН бүх зогсоол.
    key.site_id   → зөвхөн тэр зогсоол
    key.tenant_id → тухайн түрээслэгчийн БҮХ зогсоол (шинэ зогсоол автоматаар орно);
                    ИйзиПаркинг (EASYPARKING) түрээслэгчийнх — түрээслэгчгүй зогсоолуудыг ч
                    (хуучин «Бүх зогсоол» түлхүүрүүд тасрахгүй; тэдгээр зогсоолыг ТТД
                    шалгасны дараа ИйзиПаркингт шилжүүлнэ)
    аль нь ч үгүй → хязгааргүй (зөвхөн .env-ийн хуучин түлхүүр; DB-ийн түлхүүрүүд
                    deploy-ийн migration-оор ИйзиПаркинг түрээслэгчид оноогдсон)
"""
from sqlalchemy import or_

from ..models import ParkingSite, Tenant

PLATFORM_TENANT_CODE = "EASYPARKING"


def key_site_ids(db, key) -> list[str] | None:
    """Түлхүүрийн хандах зогсоолууд, None = хязгааргүй."""
    if getattr(key, "site_id", None):
        return [str(key.site_id)]
    tenant_id = getattr(key, "tenant_id", None)
    if tenant_id:
        cond = ParkingSite.tenant_id == tenant_id
        tenant = db.get(Tenant, tenant_id)
        if tenant is not None and (tenant.code or "").upper() == PLATFORM_TENANT_CODE:
            cond = or_(cond, ParkingSite.tenant_id.is_(None))
        return [str(r[0]) for r in db.query(ParkingSite.id).filter(cond).all()]
    return None


def allows(site_ids, site_id) -> bool:
    return site_ids is None or (site_id is not None and str(site_id) in site_ids)


def partner_ids(partner) -> list[str] | None:
    """Танигдсан түншийн (PartnerAuth эсвэл тестийн объект) хандах зогсоолууд."""
    if hasattr(partner, "scope_ids"):
        return partner.scope_ids()
    ids = getattr(partner, "site_ids", None)
    if ids is not None:
        return ids
    sid = getattr(partner, "site_id", None)
    return [str(sid)] if sid else None
