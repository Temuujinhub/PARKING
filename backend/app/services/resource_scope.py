"""Shared scope checks for tenant-owned wallets and site-owned EV resources."""
from fastapi import HTTPException
from sqlalchemy import or_

from ..auth import enforce_site, operator_sites
from ..models import ParkingSite


def tenant_filter(db, user, column):
    allowed = operator_sites(user)
    if allowed is None:
        return True
    if user.tenant_id:
        return column == user.tenant_id
    tenants = {r[0] for r in db.query(ParkingSite.tenant_id)
               .filter(ParkingSite.id.in_(allowed)).all()}
    return or_(column.in_([t for t in tenants if t]),
               column.is_(None) if None in tenants else False)


def enforce_tenant(db, user, tenant_id):
    allowed = operator_sites(user)
    if allowed is None:
        return
    if user.tenant_id:
        valid = tenant_id == user.tenant_id
    else:
        valid = db.query(ParkingSite.id).filter(
            ParkingSite.id.in_(allowed), ParkingSite.tenant_id == tenant_id).first() is not None
    if not valid:
        raise HTTPException(403, "Энэ байгууллагын мэдээлэлд хандах эрхгүй")


def enforce_site_resource(db, user, site_id):
    site = db.get(ParkingSite, site_id) if site_id else None
    if site is None:
        raise HTTPException(404, "Зогсоол олдсонгүй")
    enforce_site(user, site_id)
    enforce_tenant(db, user, site.tenant_id)
    return site


def enforce_plan(db, user, plan):
    enforce_tenant(db, user, plan.tenant_id)
    if plan.site_id:
        enforce_site_resource(db, user, plan.site_id)
    elif operator_sites(user) is not None and not user.tenant_id:
        raise HTTPException(403, "Байгууллагын ерөнхий тарифыг зөвхөн байгууллагын админ өөрчилнө")


def plan_matches_site(plan, site):
    return bool(plan and site and plan.tenant_id == site.tenant_id
                and (plan.site_id is None or plan.site_id == site.id))
