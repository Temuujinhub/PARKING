"""Bounded directory reads, consistent tenant filters and explicit deletion plans."""
import base64
import hashlib
import json
from io import BytesIO
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, or_, tuple_
from sqlalchemy.orm import joinedload

from ..auth import enforce_site, operator_sites
from ..models import RegisteredDriver as D
from ..serializers import to_dict


def scoped(query, user):
    allowed = operator_sites(user)
    if allowed is not None:
        condition = D.site_id.in_(allowed)
        if user.tenant_id:
            condition = condition | (D.site_id.is_(None) & (D.tenant_id == user.tenant_id))
        query = query.filter(condition)
    return query


def filtered(db, user, filters):
    query = scoped(db.query(D), user)
    for key in ('contract_type', 'company', 'is_active'):
        value = filters.get(key)
        if value is not None and value != '':
            query = query.filter(getattr(D, key) == value)
    scope = filters.get('access_scope')
    if scope:
        query = query.filter(D.access_scope.in_(('inner', 'both')) if scope == 'inner_any'
                             else D.access_scope == scope)
    site = filters.get('site_id')
    if site == 'global':
        query = query.filter(D.site_id.is_(None))
    elif site:
        try:
            site = str(UUID(site))
        except ValueError:
            raise HTTPException(422, 'Зогсоолын ID буруу байна.')
        enforce_site(user, site)
        query = query.filter(D.site_id == site)
    term = (filters.get('q') or '').strip()
    if term:
        query = query.filter(or_(D.plate_number.icontains(term.upper(), autoescape=True),
                                 D.full_name.icontains(term, autoescape=True),
                                 D.company.icontains(term, autoescape=True)))
    return query


def filter_hash(filters):
    return hashlib.sha256(json.dumps(filters, sort_keys=True).encode()).hexdigest()[:24]


def page(db, user, filters, cursor=None, limit=100):
    query = filtered(db, user, filters)
    total = query.count()
    key = func.coalesce(D.company, '')
    if cursor:
        try:
            values = json.loads(base64.urlsafe_b64decode(cursor.encode()))
            fingerprint, company, plate, row_id = values
            if (fingerprint != filter_hash(filters) or not isinstance(company, str) or len(company) > 160
                    or not isinstance(plate, str) or len(plate) > 20):
                raise ValueError('Invalid cursor')
            row_id = str(UUID(row_id))
        except Exception:
            raise HTTPException(400, 'Хуудасны зааг буруу. Эхний хуудаснаас дахин уншина уу.')
        query = query.filter(tuple_(key, D.plate_number, D.id) > (company, plate, row_id))
    rows = query.options(joinedload(D.site)).order_by(key, D.plate_number, D.id).limit(limit + 1).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = None
    if has_more:
        last = rows[-1]
        next_cursor = base64.urlsafe_b64encode(json.dumps(
            [filter_hash(filters), last.company or '', last.plate_number, str(last.id)]).encode()).decode()
    return {'items': [to_dict(r, extra={'site_name': r.site.name if r.site else 'Бүх зогсоол'}) for r in rows],
            'total': total, 'next_cursor': next_cursor, 'limit': limit}


def selected_rows(db, user, ids):
    ids = sorted({str(i) for i in ids})
    rows = scoped(db.query(D), user).options(joinedload(D.site)).filter(D.id.in_(ids)).order_by(D.id).all()
    if len(rows) != len(ids):
        raise HTTPException(403, 'Зарим бүртгэл олдсонгүй эсвэл таны эрхийн хүрээнд биш байна.')
    return rows


def deletion_plan(rows):
    states = [to_dict(row) for row in sorted(rows, key=lambda r: str(r.id))]
    token = hashlib.sha256(json.dumps(['delete', states], sort_keys=True, default=str).encode()).hexdigest()
    return {'selected': len(states), 'preview_token': token,
            'items': [{'id': str(r.id), 'plate_number': r.plate_number,
                       'full_name': r.full_name, 'is_active': r.is_active} for r in rows]}


def export_xlsx(rows):
    # Explicit string cells prevent Excel formulas in names, plates or notes.
    from openpyxl import Workbook
    from openpyxl.cell import WriteOnlyCell
    from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
    book = Workbook(write_only=True)
    sheet = book.create_sheet('Бүртгэлтэй машин')
    sheet.append(['ID', 'Дугаар', 'Эзэмшигч', 'Утас', 'Байгууллага', 'Тэмдэглэл',
                  'Төрөл', 'Зогсоол', 'Зогсоолын код', 'Хамрах хүрээ',
                  'Бүртгэлийн төлөв', 'Эхлэх (UTC)', 'Дуусах (UTC)'])
    for row in rows:
        values = [str(row.id), row.plate_number, row.full_name, row.phone, row.company,
                  row.note, row.contract_type, row.site.name if row.site else 'Бүх зогсоол',
                  row.site.site_code if row.site else '', row.access_scope,
                  'Идэвхтэй' if row.is_active else 'Идэвхгүй',
                  row.valid_from.isoformat(), row.valid_to.isoformat()]
        cells = []
        for value in values:
            cell = WriteOnlyCell(sheet, value=ILLEGAL_CHARACTERS_RE.sub('', str(value or '')))
            cell.data_type = 's'
            cells.append(cell)
        sheet.append(cells)
    buffer = BytesIO(); book.save(buffer)
    return buffer.getvalue()
