"""auth.py — эрхийн матриц + операторын олон зогсоолын логик (DB шаардлагагүй).

    cd backend && venv/bin/python tests/test_permissions.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.auth import (ALL_MODULES, ROLE_PERMISSIONS, effective_permissions,
                      has_permission, operator_sites, scoped_site)

PASS = FAIL = 0


def check(name, cond):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗ <<< FAIL'} {name}")


class U:
    def __init__(self, role, permissions=None, site_id=None, site_ids=None):
        self.role = role
        self.permissions = permissions
        self.site_id = site_id
        self.site_ids = site_ids


print("effective_permissions:")
check("permissions=null → role default (OPERATOR)",
      effective_permissions(U("OPERATOR")) == ROLE_PERMISSIONS["OPERATOR"])
check("матриц тохируулсан бол түүгээр",
      effective_permissions(U("FINANCE", permissions=["reports", "vat"])) == {"reports", "vat"})
check("хоосон матриц = юу ч харахгүй",
      effective_permissions(U("FINANCE", permissions=[])) == set())
check("SUPER_ADMIN матриц үл хэрэгсэнэ — ямагт *",
      effective_permissions(U("SUPER_ADMIN", permissions=["reports"])) == {"*"})

print("has_permission:")
check("OPERATOR + матриц {cashier,reports} → reports тийм",
      has_permission(U("OPERATOR", permissions=["cashier", "reports"]), "reports"))
check("OPERATOR default-д reports байхгүй",
      not has_permission(U("OPERATOR"), "reports"))
check("ALL_MODULES-д * байхгүй, 10+ модультай",
      "*" not in ALL_MODULES and len(ALL_MODULES) >= 10)

print("operator_sites:")
check("ADMIN → None (бүх зогсоол)", operator_sites(U("ADMIN", site_id="S1")) is None)
check("OPERATOR site_ids=[S1,S2]", operator_sites(U("OPERATOR", site_ids=["S1", "S2"])) == ["S1", "S2"])
check("OPERATOR site_ids хоосон → [site_id]", operator_sites(U("OPERATOR", site_id="S1")) == ["S1"])
check("OPERATOR site-гүй → None (бүгд)", operator_sites(U("OPERATOR")) is None)

print("scoped_site:")
op2 = U("OPERATOR", site_id="S1", site_ids=["S1", "S2"])
check("эрхтэй site сонговол түүгээр", scoped_site(op2, "S2") == ("S2", None))
check("эрхгүй site → бүх өөрийн site-ууд (in_ шүүлт)", scoped_site(op2, "S9") == (None, ["S1", "S2"]))
check("site сонгоогүй → өөрийн site-ууд", scoped_site(op2, None) == (None, ["S1", "S2"]))
op1 = U("OPERATOR", site_id="S1")
check("ганц site-тай оператор ямагт өөрийн site", scoped_site(op1, "S9") == ("S1", None))
check("админ site_id хэвээр дамжина", scoped_site(U("ADMIN"), "S3") == ("S3", None))

print(f"\n{PASS} PASS, {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
