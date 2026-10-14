"""«POS терминал (PAX апп) өөрөө e-Barimt гаргадаг» — зогсоол бүрийн төлөв ба ДАРАХ тохиргоо.

2026-10-14 эзний эцсийн шийдвэр: PAX терминал (Ontime) POS аппын КАРТ, БЭЛЭН борлуулалт
бүрд e-Barimt-ыг өөрөө гаргадаг — БҮХ зогсоол, БҮХ түрээслэгч. Дүрэм ЕРӨНХИЙ анхдагчаар
АСААЛТТАЙ болсон тул зогсоол бүрд асаах шаардлагагүй (10-13-ны «бүх зогсоолд асаах» горим
хүчингүй — 7 зогсоолд бичсэн ON мөр хүчинтэй хэвээр, ерөнхийтэй ижил). Энэ хэрэгсэл одоо:

  • (анхдагч) ТАЙЛАН — зогсоол бүрийн ҮЙЛЧИЛЖ буй утга, эх сурвалж (ерөнхий/зогсоол),
    сүүлийн 30 хоногийн POS аппын карт/бэлэн төлбөр; POS аппын төлбөртэй атлаа дүрэм
    үйлчлэхгүй зогсоолыг ⚠ (Тохиргоо → Төлбөрийн дүрэм-ийн зөрчлийн шалгалттай нэг дүрэм);
  • --site CODE --set off     терминал нь баримт ХЭВЛЭДЭГГҮЙ зогсоолд тодорхой унтраах;
  • --site CODE --set on      тодорхой асаах (--methods CARD|CASH|CARD,CASH);
  • --site CODE --set global  зогсоолын давхаргыг хасаж ерөнхий рүү буцаах.
Ерөнхий утгыг UI-аас (Тохиргоо → Төлбөрийн дүрэм → Ерөнхий) өөрчилнө.

    cd /root/PARKING/backend
    venv/bin/python tools/pos_terminal_setting.py                                     # тайлан (бүгд)
    venv/bin/python tools/pos_terminal_setting.py --tenant MONNIS                     # нэг түрээслэгч
    venv/bin/python tools/pos_terminal_setting.py --site HANGARID --set off \\
        --apply --user temuujin                                                       # унтраах

Хамгаалалт: --set үед --site заавал (бөөнөөр өөрчлөхгүй); --apply зөвхөн ажиллаж буй
«parking» DB + прод сервер дээр (vat_duplicates.server_problems — audit*/t_* хуулбар,
test.* сервер, PARKING_DEPLOY_ROLE=staging дээр татгалзана). Зогсоол бүрд AuditLog
(UPDATE payment_rules — тохиргооны UI-тай ижил хэлбэр).
"""
import argparse
import getpass
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import AuditLog  # noqa: E402
from app.services import app_settings as A  # noqa: E402
from app.services import payment_rules as PRULES  # noqa: E402
from app.services import vat_duplicates as VD  # noqa: E402

TOOL = "pos_terminal_setting"
KEYS = ("pos_terminal_issues", "pos_terminal_methods")


def site_state(db, site_id) -> dict:
    """Зогсоолын ҮЙЛЧИЛЖ буй утга (UI-д харагдах) + зогсоолын түүхий давхарга."""
    raw = (A.get_site_overrides(db, A.EBARIMT_KEY) or {}).get(site_id) or {}
    methods = A.pos_terminal_receipt_methods(db, site_id)
    return {"issues": bool(methods), "methods": ",".join(sorted(methods)) or "—",
            "source": "зогсоол" if any(k in raw for k in KEYS) else "ерөнхий",
            "overlay": {k: raw[k] for k in KEYS if k in raw}}


def plan_values(mode: str, methods: str) -> dict:
    """--set → зогсоолын давхаргад бичих утга (None = ерөнхий рүү буцаах)."""
    if mode == "off":
        return {"pos_terminal_issues": False}
    if mode == "on":
        return PRULES.clamp_values(A.EBARIMT_KEY, {"pos_terminal_issues": True,
                                                   "pos_terminal_methods": methods})
    return {"pos_terminal_issues": None, "pos_terminal_methods": None}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tenant", default="ALL", help="түрээслэгч (code/нэр/id) эсвэл ALL (анхдагч)")
    ap.add_argument("--site", action="append", help="зогсоол(ууд) (site_code) — --set үед заавал")
    ap.add_argument("--set", dest="mode", choices=("on", "off", "global"),
                    help="on/off — зогсоолд тодорхой; global — ерөнхий рүү буцаах")
    ap.add_argument("--methods", default="CARD,CASH", choices=sorted(A.POS_TERMINAL_METHOD_CHOICES),
                    help="--set on үед: терминал аль төлбөрт баримт гаргадаг (анхдагч CARD,CASH)")
    ap.add_argument("--apply", action="store_true", help="БОДИТООР бичих (анхдагч dry-run)")
    ap.add_argument("--user", default=None, help="AuditLog-д (анхдагч cli:<OS хэрэглэгч>)")
    args = ap.parse_args(argv)
    username = args.user or f"cli:{os.getenv('SUDO_USER') or getpass.getuser()}"
    if args.mode and not args.site:
        raise SystemExit("--set үед --site заавал (зогсоол бүрээр, бөөнөөр өөрчлөхгүй). "
                         "Бүх зогсоолын анхдагчийг UI-ийн «Ерөнхий»-өөс өөрчилнө.")
    if args.apply and not args.mode:
        raise SystemExit("--apply үед --set on|off|global заавал. 2026-10-14-өөс дүрэм ерөнхий "
                         "анхдагчаар АСААЛТТАЙ — зогсоол бүрд асаах шаардлагагүй.")

    db = SessionLocal()
    if not args.apply and db.bind.dialect.name == "postgresql":
        db.execute(text("SET TRANSACTION READ ONLY"))
    try:
        try:
            tenants = VD.resolve_tenants(db, args.tenant)
            sites = VD.pos_app_sites(db, tenants, args.site)
            if not args.site:
                sites = VD.tenant_sites(db, tenants)      # тайланд тест зогсоол ч
        except ValueError as e:
            raise SystemExit(str(e))
        glob = A.pos_terminal_receipt_methods(db, None)
        print(f"{TOOL}: {'APPLY' if args.apply else 'DRY-RUN'} · DB {VD.db_name()} · сервер "
              f"{settings.public_base_url} · түрээслэгч {VD.tenant_label(tenants)}"
              f" · ЕРӨНХИЙ: {'ON ' + ','.join(sorted(glob)) if glob else 'OFF'}")
        tcode = {t.id: t.code for t in tenants}
        print(f"\n  {'түрээслэгч':<11} {'зогсоол':<26} {'үйлчилж буй':<16} {'эх сурвалж':<10} "
              f"POS апп (30 хоног)")
        warn = 0
        for s in sites:
            st = site_state(db, s.id)
            off = PRULES.pos_terminal_off_traffic(db, s.id)
            n = PRULES.pos_app_traffic(db, s.id)
            traffic = " · ".join(f"{k} {v}" for k, v in sorted(n.items())) or "—"
            eff = f"ON {st['methods']}" if st["issues"] else "OFF"
            mark = ""
            if off:
                warn += 1
                mark = "  ⚠ POS аппын төлбөртэй ч дүрэм үйлчлэхгүй — систем msgbill давхар гаргаж байна"
            print(f"  {tcode.get(s.tenant_id, '?'):<11} {s.site_code:<26} {eff:<16} {st['source']:<10} "
                  f"{traffic}{mark}")
        if warn:
            print(f"\n⚠ {warn} зогсоолд POS аппын төлбөр байгаа ч «POS терминал e-Barimt гаргадаг» "
                  "үйлчлэхгүй. Терминал нь хэвлэдэг бол --set global (эсвэл on)-оор засна.")
        if not args.mode:
            print("\nТАЙЛАН — юу ч бичсэнгүй. Өөрчлөх: --site <CODE> --set on|off|global --apply --user <нэр>.")
            return 0
        values = plan_values(args.mode, args.methods)
        changed = []
        for s in sites:
            before = site_state(db, s.id)
            want = {k: v for k, v in values.items() if v is not None}
            if args.mode == "global":
                same = not before["overlay"]
            else:
                same = all(before["overlay"].get(k) == v for k, v in want.items())
            if not same:
                changed.append((s, before))
        print(f"\nӨөрчлөх зогсоол: {len(changed)} / {len(sites)} · утга: "
              + ("ерөнхий рүү буцаах" if args.mode == "global" else str(values)))
        if not args.apply:
            probs = VD.server_problems()
            if probs:
                print("⚠ --apply ОДООГООР ажиллахгүй: " + "; ".join(probs))
            print("DRY-RUN — юу ч бичсэнгүй. Бичихийн тулд --apply --user <нэр>.")
            return 0
        probs = VD.server_problems()
        if probs:
            print("ЗОГСОВ: " + "; ".join(probs))
            return 3
        for s, before in changed:
            saved = A.set_site_rules(db, A.EBARIMT_KEY, s.id, values, username)
            # Тохиргооны UI-тай (admin_router.payment_rules_site_save) ижил хэлбэр
            db.add(AuditLog(username=username, action="UPDATE", entity="payment_rules",
                            entity_id=str(s.id),
                            detail={A.EBARIMT_KEY: saved, "_tool": TOOL, "_set": args.mode,
                                    "_before": before["overlay"]}))
        db.commit()
        A.invalidate_cache()
        print("\nБИЧЛЭЭ. Дараах байдал:")
        for s, _b in changed:
            a = site_state(db, s.id)
            print(f"  {s.site_code}: {'ON ' + a['methods'] if a['issues'] else 'OFF'} ({a['source']})")
        print("Ажиллаж буй сервер тохиргоог ≤30с-д уншина (кэш).")
        return 0
    finally:
        db.rollback()
        db.close()


if __name__ == "__main__":
    sys.exit(main())
