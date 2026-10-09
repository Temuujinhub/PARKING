"""Paginated reads and guarded exports/deletion. No parking/payment mutations."""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field, StrictBool
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from ..auth import operator_sites, require
from ..database import get_db
from ..models import Device, ParkingSite, RegisteredDriver as D, User
from ..serializers import to_dict
from ..services import driver_directory as directory
from ..services.driver_status import lock_driver_writes

router = APIRouter()


class Filters(BaseModel):
    q: str = Field(default='', max_length=160)
    company: str = Field(default='', max_length=160)
    site_id: str = Field(default='', max_length=40)
    contract_type: str = Field(default='', max_length=20)
    access_scope: str = Field(default='', max_length=20)
    is_active: bool | None = None


class ExportRequest(Filters):
    ids: list[UUID] | None = Field(default=None, min_length=1, max_length=2000)


class DeleteRequest(BaseModel):
    ids: list[UUID] = Field(min_length=1, max_length=2000)
    dry_run: StrictBool = True
    preview_token: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')


@router.get('/drivers/page')
def driver_page(filters: Filters = Depends(), cursor: str | None = Query(None, max_length=2048),
                limit: int = Query(100, ge=1, le=200), db: Session = Depends(get_db),
                user: User = Depends(require('drivers'))):
    return directory.page(db, user, filters.model_dump(), cursor, limit)


@router.get('/drivers/options')
def driver_options(db: Session = Depends(get_db), user: User = Depends(require('drivers'))):
    query = db.query(ParkingSite)
    allowed = operator_sites(user)
    if allowed is not None:
        query = query.filter(ParkingSite.id.in_(allowed))
    sites = query.order_by(ParkingSite.name).all()
    nested = {r[0] for r in db.query(Device.site_id).filter(
        Device.site_id.in_([s.id for s in sites]), Device.device_type == 'camera',
        Device.nested_inner.is_(True), Device.status != 'deleted').distinct()}
    return [{'id': s.id, 'name': s.name, 'site_code': s.site_code,
             'has_inner_lanes': s.id in nested} for s in sites]


@router.get('/drivers/company-search')
def company_search(q: str = Query('', max_length=160), db: Session = Depends(get_db),
                   user: User = Depends(require('drivers'))):
    if not q.strip():
        return []
    query = directory.scoped(db.query(D.company, func.count(D.id)), user)
    return [{'company': c, 'count': n} for c, n in query.filter(
        D.company.icontains(q.strip(), autoescape=True)).group_by(D.company)
        .order_by(D.company).limit(30).all()]


@router.post('/drivers/export')
def export_drivers(payload: ExportRequest, db: Session = Depends(get_db),
                   user: User = Depends(require('drivers'))):
    if payload.ids is not None:
        rows = directory.selected_rows(db, user, payload.ids)
        rows.sort(key=lambda r: (r.company or '', r.plate_number, str(r.id)))
    else:
        query = directory.filtered(db, user, payload.model_dump(exclude={'ids'}))
        rows = query.options(joinedload(D.site)).order_by(D.company, D.plate_number, D.id).limit(50001).all()
        if len(rows) > 50000:
            raise HTTPException(413, 'Экспорт 50,000 мөрөөс их байна. Зогсоол/төрлөөр шүүнэ үү.')
    return Response(directory.export_xlsx(rows),
                    media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    headers={'Content-Disposition': 'attachment; filename="parking-registrations.xlsx"',
                             'Cache-Control': 'no-store'})


@router.post('/drivers/select-filtered')
def select_filtered(payload: Filters, db: Session = Depends(get_db),
                    user: User = Depends(require('drivers'))):
    rows = directory.filtered(db, user, payload.model_dump()).with_entities(D.id).order_by(D.id).limit(2001).all()
    if len(rows) > 2000:
        raise HTTPException(413, 'Нэг үйлдэлд 2,000 хүртэл мөр сонгоно. Шүүлтүүрээ нарийсгана уу.')
    return {'ids': [r[0] for r in rows], 'selected': len(rows)}


@router.post('/drivers/bulk-delete')
def delete_drivers(payload: DeleteRequest, db: Session = Depends(get_db),
                   user: User = Depends(require('drivers'))):
    if user.role not in ('ADMIN', 'SUPER_ADMIN'):
        raise HTTPException(403, 'Бүртгэл устгах эрх зөвхөн админд бий.')
    lock_driver_writes(db)
    rows = directory.selected_rows(db, user, payload.ids)
    plan = directory.deletion_plan(rows)
    if payload.dry_run:
        return {'dry_run': True, **plan}
    if payload.preview_token != plan['preview_token']:
        raise HTTPException(409, 'Бүртгэл өөрчлөгдсөн эсвэл урьдчилан шалгаагүй. Дахин шалгана уу.')
    from .admin_router import _audit
    for row in rows:
        _audit(db, user, 'DELETE', 'driver', row.id,
               {'source': 'bulk_delete', 'before': to_dict(row)})
        db.delete(row)
    _audit(db, user, 'BULK_DELETE', 'driver', '-', {'ids': [r.id for r in rows], 'deleted': len(rows)})
    db.commit()
    return {'dry_run': False, 'deleted': len(rows)}
