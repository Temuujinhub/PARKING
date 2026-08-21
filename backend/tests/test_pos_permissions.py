"""PAX POS / кассын урсгал операторын эрхээр БҮРЭН ажиллах эсэх (regression).

Яагаад: 2026-08-20-ны аюулгүй байдлын хатууруулалт `GET /api/admin/devices`-ыг
`devices/settings/barriers` эрхээр хаасан. Гэтэл PAX POS хаалт нээх `device_id`-г
ЗӨВХӨН тэндээс олдог байсан тул операторын POS дээр «Танд энэ үйлдлийг хийх эрх
байхгүй» гарч, хаалт гараар нээх боломжгүй болов (Моннис prod, nginx логонд 13
удаагийн 403). Засвар: `GET /api/barriers/devices` — device_key-гүй нимгэн жагсаалт.

Энэ тест урсгалын endpoint бүрийн ЖИНХЭНЭ dependency-г route-оос уншиж шалгана —
тиймээс ирээдүйд ямар нэг endpoint-ийн эрх чангарвал энд шууд унана.

    cd backend && venv/bin/python tests/test_pos_permissions.py
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.auth import ROLE_PERMISSIONS, has_permission
from app.routers import (admin_router, barriers_router, cashier_router,
                         payments_router, sessions_router)

PASS = FAIL = 0


def check(name, cond):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗ <<< FAIL'} {name}")


class U:
    def __init__(self, role, permissions=None):
        self.role = role
        self.permissions = permissions
        self.site_id = None
        self.site_ids = None


ROUTES = {}
for mod in (admin_router, barriers_router, cashier_router, payments_router, sessions_router):
    for r in mod.router.routes:
        for m in r.methods:
            ROUTES[(m, r.path)] = r


def guard(method, path):
    """(modules, roles) — route-ийн ЖИНХЭНЭ эрхийн шаардлага. Аль нь ч байхгүй
    бол зөвхөн нэвтэрсэн байхыг шаардана."""
    r = ROUTES.get((method, path))
    assert r is not None, f"route олдсонгүй: {method} {path}"
    mods, roles = (), ()
    for d in r.dependant.dependencies:
        mods += getattr(d.call, "required_modules", ())
        roles += getattr(d.call, "required_roles", ())
    return mods, roles


def allowed(user, method, path):
    mods, roles = guard(method, path)
    if roles and user.role not in roles:
        return False
    return not mods or any(has_permission(user, m) for m in mods)


# Моннис билдингийн POS хэрэглэгч «Building»-ийн ЯГ тэр эрхийн матриц
pos = U("OPERATOR", ["cashier", "check", "history", "compensations", "free_exit"])
plain = U("OPERATOR")  # permissions=null → role default (free_exit БАЙХГҮЙ)

POS_FLOW = [
    ("GET", "/api/admin/sites", "зогсоолын жагсаалт"),
    ("GET", "/api/sessions/recent-exits", "гарах машинууд"),
    ("GET", "/api/sessions/check", "дугаараар хайх"),
    ("GET", "/api/sessions/{session_id}", "төлбөрийн задаргаа"),
    ("POST", "/api/payments/cash", "бэлнээр"),
    ("POST", "/api/payments/pos/confirm", "картаар (PAX)"),
    ("GET", "/api/cashier/shift/current", "ээлжийн төлөв"),
    ("POST", "/api/cashier/shift/open", "ээлж нээх"),
    ("POST", "/api/cashier/shift/close", "ээлж хаах"),
    ("GET", "/api/barriers/devices", "хаалтны жагсаалт (device_id олох)"),
]

print("POS-ийн урсгал — операторын эрхээр бүрэн ажиллах ёстой:")
for method, path, desc in POS_FLOW:
    check(f"{desc} — {method} {path}", allowed(pos, method, path))

print("\nХаалт нээх — free_exit ХАНГАЛТТАЙ (barriers эрх шаардахгүй):")
check("GET /api/barriers/devices", allowed(pos, "GET", "/api/barriers/devices"))
check("POST /api/barriers/{id}/open", allowed(pos, "POST", "/api/barriers/{device_id}/open"))
check("free_exit-гүй энгийн оператор хаалтаа нээхгүй",
      not allowed(plain, "POST", "/api/barriers/{device_id}/open"))
check("free_exit-гүй ч хаалтны ЖАГСААЛТ харна (касс — нэр/эгнээ л харагдана)",
      allowed(plain, "GET", "/api/barriers/devices"))

print("\nХУУЧИН POS build (шинэчлээгүй апп) — /admin/devices дуудсаар байна:")
check("оператор 403 иДЭХГҮЙ (датаг хасаж хариулна)",
      allowed(pos, "GET", "/api/admin/devices"))
check("HR — /admin/devices ХААЛТТАЙ хэвээр (ажилтны эрхэд хамаагүй)",
      not allowed(U("HR"), "GET", "/api/admin/devices"))
check("`reports`-той санхүүгийн ажилтан ч ХААЛТТАЙ",
      not allowed(U("FINANCE"), "GET", "/api/admin/devices"))

print("\n2026-08-20-ны хатууруулалт хэвээр (device_key задрахгүй):")
src = inspect.getsource(barriers_router.lean_barrier_rows)
body = src.split('"""')[-1]  # docstring-гүй бие
for leak in ("device_key", "password", "username", "ip_address"):
    check(f"нимгэн жагсаалтад {leak} БАЙХГҮЙ", leak not in body)
admin_src = inspect.getsource(admin_router.list_devices)
# `to_dict(d)` нь Device-ийн БҮХ баганыг (device_key орно) буцаадаг — эрх багатай
# хэрэглэгч тэр мөр хүртэл ХҮРЭХГҮЙ, өмнө нь нимгэн замаар гарсан байх ёстой
check("/admin/devices эрх багатайг нимгэн зам руу to_dict-ээс ӨМНӨ буцаана",
      "lean_barrier_rows" in admin_src
      and admin_src.index("lean_barrier_rows") < admin_src.index("to_dict("))

print("\nЭрхийн матрицын үнэн зөв байдал (UI-д харагдахтай нийцэх):")
check("OPERATOR default-д pay_transfer БАЙНА (frontend ROLE_DEFAULTS-тай таарах ёстой)",
      "pay_transfer" in ROLE_PERMISSIONS["OPERATOR"])

print(f"\n{PASS} PASS, {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
