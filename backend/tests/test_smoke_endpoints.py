"""Бүх GET endpoint-ийг дуудаж 500 гарахгүйг батална (амьд DB шаардана).

    cd backend && venv/bin/python tests/test_smoke_endpoints.py

Яагаад: 2026-07-27-ны аудитаар compensations_router-ийн `osid` болон
sessions_router-ийн `cfg` гэсэн тодорхойлогдоогүй хувьсагчийн улмаас 2 endpoint
БҮРМӨСӨН 500 өгдөг байсныг илрүүлсэн. Тэдгээрийг нэг ч удаа дуудаж үзээгүйгээс
production-д хүрчихсэн байв. Энэ тест тэр ангиллын алдааг дахин гаргахгүй.

404/400/403/422 — хэвийн (UUID байхгүй г.м). ЗӨВХӨН 5xx унана.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.routing import APIRoute  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import auth as app_auth  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import User  # noqa: E402

DUMMY_ID = "00000000-0000-0000-0000-000000000000"
# Заавал query параметртэй endpoint-үүд — утга өгөхгүй бол 422 (энэ нь алдаа биш)
QUERY_DEFAULTS = {"site_id": DUMMY_ID, "site_code": "SITE01", "site": "SITE01",
                  "plate": "1234ТСТ", "q": "1234", "reg": "1234567",
                  "date_from": "2026-07-01", "date_to": "2026-07-02",
                  "month": "2026-07", "date": "2026-07-01"}

db = SessionLocal()
su = db.query(User).filter(User.role == "SUPER_ADMIN").first()
db.close()
if not su:
    print("SUPER_ADMIN хэрэглэгч олдсонгүй — тест алгаслаа")
    sys.exit(0)
app.dependency_overrides[app_auth.get_current_user] = lambda: su

def all_routes(router, _seen=None):
    """Route-уудыг рекурсивээр задална.

    FastAPI-ийн энэ хувилбар include_router-ийг `_IncludedRouter` гэж боож
    хадгалдаг тул app.routes дээр шууд давтахад ердөө 1 route гарч ирдэг —
    доторх жинхэнэ router нь `original_router` талбарт байна."""
    _seen = _seen if _seen is not None else set()
    if id(router) in _seen:
        return
    _seen.add(id(router))
    inner = getattr(router, "original_router", None)
    if inner is not None:
        yield from all_routes(inner, _seen)
        return
    for r in getattr(router, "routes", []):
        if isinstance(r, APIRoute):
            yield r
        else:
            yield from all_routes(r, _seen)


client = TestClient(app)
failed, checked, skipped = [], 0, 0
for route in all_routes(app):
    if "GET" not in route.methods:
        continue
    path = route.path
    for name in route.param_convertors:
        path = path.replace("{" + name + "}", DUMMY_ID)
    params = {k: v for k, v in QUERY_DEFAULTS.items()
              if k in {p.name for p in route.dependant.query_params}}
    try:
        r = client.get(path, params=params)
        code = r.status_code
    except Exception as e:  # noqa: BLE001 — exception нь 500-тай адил ноцтой
        code = 500
        r = type("R", (), {"text": f"{type(e).__name__}: {e}"})()
    checked += 1
    # 5xx боловч JSON `detail`-тай бол энэ нь ЗОРИУДЫН HTTPException (ж: түншийн
    # API-ийн түлхүүр тохируулаагүй → 503). Кодын уналт нь detail-гүй ирнэ.
    deliberate = False
    if code >= 500:
        try:
            deliberate = isinstance(r.json().get("detail"), str)
        except Exception:  # noqa: BLE001
            deliberate = False
    if code >= 500 and not deliberate:
        failed.append((route.path, code, str(getattr(r, "text", ""))[:160]))
        print(f"  ✗ <<< FAIL {code} {route.path}\n        {str(getattr(r, 'text', ''))[:160]}")
    elif deliberate:
        skipped += 1
        print(f"  ~ {code} {route.path} (тохиргоогоор идэвхгүй)")
    else:
        print(f"  ✓ {code} {route.path}")

print(f"\n{checked - len(failed) - skipped} PASS, {len(failed)} FAIL, "
      f"{skipped} тохиргоогоор идэвхгүй  (нийт {checked} GET endpoint)")
sys.exit(1 if failed else 0)
