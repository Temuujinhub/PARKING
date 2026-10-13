"""Зогсоол бүрийн «POS терминал өөрөө НӨАТ баримт гаргадаг» тохиргоог АСААХ (2026-10-13).

Эзний шийдвэр: ИйзиПаркинг ХХК-ийн POS терминалтай БҮХ зогсоолд PAX терминал POS
аппын КАРТ, БЭЛЭН борлуулалт бүрд Ontime e-Barimt өөрөө хэвлэдэг. Тохиргоо асаалттай
зогсоолд систем тэдгээрт msgbill баримт ДАВХАР гаргахгүй (EXTERNAL мөр бүртгэнэ).
Түүхэн давхардлыг `tools/vat_cancel_duplicates.py --pos-app` цуцална — ЭНЭ хэрэгслийг
ЭХЛЭЭД ажиллуулж шинэ давхардал үүсэхийг зогсооно.

Зогсоол бүрд (Тохиргоо → Төлбөрийн дүрэм → e-Barimt-тай ЯГ ИЖИЛ давхарга):
  pos_terminal_issues  = true
  pos_terminal_methods = CARD,CASH (--methods)
  pos_terminal_users   = POS аппын хэрэглэгчид — vat_cancel_duplicates --pos-app-тай НЭГ
                         дүрэм: --pos-user > одоогийн тохиргоо > тэр зогсоолд POS-оор
                         ≥20 карт авсан хэрэглэгч (веб кассын sogii/bamanna ОРОХГҮЙ)
POS аппын хэрэглэгчгүй (терминалгүй) зогсоолыг АЛГАСНА. Тест зогсоол (KH, SITE10)
зөвхөн --site-аар нэрлэсэн үед.

    cd /root/PARKING/backend
    sudo venv/bin/python tools/pos_terminal_setting.py --tenant EASYPARKING            # харах (dry-run)
    sudo venv/bin/python tools/pos_terminal_setting.py --tenant EASYPARKING \\
        --apply --user temuujin                                                       # бичих

Хамгаалалт: --apply зөвхөн ажиллаж буй «parking» DB + прод сервер дээр (vat_duplicates.
server_problems — audit*/t_* хуулбар, test.* сервер, PARKING_DEPLOY_ROLE=staging дээр
татгалзана), зөвхөн ИйзиПаркинг ХХК. Зогсоол бүрд AuditLog (UPDATE payment_rules —
тохиргооны UI-тай ижил хэлбэр). Унтраахдаа UI-аас эсвэл --off.
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
KEYS = ("pos_terminal_issues", "pos_terminal_methods", "pos_terminal_users")


def site_state(db, site_id) -> dict:
    """Зогсоолын ҮЙЛЧИЛЖ БУЙ утга (UI-д харагдах) + зогсоолын түүхий давхарга."""
    r = A.get_rules(db, A.EBARIMT_KEY, site_id)
    raw = (A.get_site_overrides(db, A.EBARIMT_KEY) or {}).get(site_id) or {}
    return {"issues": bool(r.get("pos_terminal_issues")),
            "methods": ",".join(sorted(A.pos_terminal_receipt_methods(db, site_id))) or "—",
            "users": ",".join(sorted(A.pos_app_users(db, site_id))) or "—",
            "overlay": {k: raw[k] for k in KEYS if k in raw}}


def plan(db, tenant, site_codes=None, *, pos_user=None, min_card=VD.POS_APP_MIN_CARD,
         methods="CARD,CASH", off=False) -> list[dict]:
    """Зогсоол бүрд юу бичихийг тооцно (бичихгүй)."""
    sites = VD.pos_app_sites(db, tenant, site_codes)
    users = VD.pos_users_for_sites(db, sites, override=VD.parse_pos_user_override(pos_user),
                                   min_card=min_card)
    out = []
    for s in sites:
        info = users[s.id]
        before = site_state(db, s.id)
        if off:
            values = {"pos_terminal_issues": False}
        elif not info["users"]:
            out.append({"site": s, "before": before, "values": None, "info": info,
                        "skip": "POS аппын хэрэглэгч алга (терминалгүй зогсоол) — алгасав"})
            continue
        else:
            values = {"pos_terminal_issues": True, "pos_terminal_methods": methods,
                      "pos_terminal_users": ",".join(sorted(info["users"]))}
        values = PRULES.clamp_values(A.EBARIMT_KEY, values)
        out.append({"site": s, "before": before, "values": values, "info": info, "skip": None})
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tenant", required=True, help="түрээслэгч (code/нэр/id), ж: EASYPARKING")
    ap.add_argument("--site", action="append", help="зөвхөн энэ зогсоол(ууд) (site_code)")
    ap.add_argument("--pos-user", action="append",
                    help="«pos.a,pos.b» (бүх зогсоолд) эсвэл «HANGARID=pos.khangarid» — таамгийг дарна")
    ap.add_argument("--min-card", type=int, default=VD.POS_APP_MIN_CARD,
                    help="тохиргоо хоосон зогсоолд POS-оор ≥N карт авсан хэрэглэгч (20)")
    ap.add_argument("--methods", default="CARD,CASH", choices=sorted(A.POS_TERMINAL_METHOD_CHOICES),
                    help="терминал аль төлбөрт баримт гаргадаг (анхдагч CARD,CASH)")
    ap.add_argument("--off", action="store_true", help="асаалтыг УНТРААХ (буцаах)")
    ap.add_argument("--apply", action="store_true", help="БОДИТООР бичих (анхдагч dry-run)")
    ap.add_argument("--user", default=None, help="AuditLog-д (анхдагч cli:<OS хэрэглэгч>)")
    args = ap.parse_args(argv)
    username = args.user or f"cli:{os.getenv('SUDO_USER') or getpass.getuser()}"

    db = SessionLocal()
    if not args.apply and db.bind.dialect.name == "postgresql":
        # Dry-run: READ ONLY транзакц — алдаатай код ч юу ч бичиж чадахгүй
        db.execute(text("SET TRANSACTION READ ONLY"))
    try:
        try:
            tenant = VD.resolve_tenant(db, args.tenant)
            rows = plan(db, tenant, args.site, pos_user=args.pos_user, min_card=args.min_card,
                        methods=args.methods, off=args.off)
        except ValueError as e:
            raise SystemExit(str(e))
        print(f"{TOOL}: {'APPLY' if args.apply else 'DRY-RUN'} · DB {VD.db_name()} · сервер "
              f"{settings.public_base_url} · түрээслэгч {tenant.code} ({tenant.name})"
              + (" · УНТРААХ" if args.off else ""))
        for r in rows:
            s, b, i = r["site"], r["before"], r["info"]
            der = ", ".join(f"{u} {n}" for u, n in sorted(i["derived"].items(), key=lambda kv: -kv[1]))
            head = f"\n{s.site_code} ({s.name})"
            if r["skip"]:
                if b["issues"] or i["derived"]:
                    print(head + f": {r['skip']}" + (f" · POS карт: {der}" if der else "")
                          + (" · ⚠ одоо АСААЛТТАЙ" if b["issues"] else ""))
                continue
            print(head + f"  [хэрэглэгч: {i['source']}" + (f"; POS карт: {der}" if der else "") + "]")
            print(f"  ӨМНӨ : асаалт={'ON' if b['issues'] else 'OFF'} · аргууд={b['methods']} · "
                  f"хэрэглэгчид={b['users']}")
            v = r["values"]
            after_methods = (v.get("pos_terminal_methods") if v.get("pos_terminal_issues") else "—")
            print(f"  ДАРАА: асаалт={'ON' if v.get('pos_terminal_issues') else 'OFF'} · "
                  f"аргууд={after_methods or '—'} · хэрэглэгчид={v.get('pos_terminal_users', b['users'])}")
        todo = [r for r in rows if not r["skip"]]
        changed = [r for r in todo if not _same(r)]
        print(f"\nӨөрчлөх зогсоол: {len(changed)} / сонгосон {len(rows)}"
              + (f" (өөрчлөлтгүй {len(todo) - len(changed)})" if len(todo) != len(changed) else ""))
        if not args.apply:
            probs = VD.server_problems()
            if probs:
                print("⚠ --apply ОДООГООР ажиллахгүй: " + "; ".join(probs))
            if tenant.code not in VD.POS_APP_TENANTS:
                print(f"⚠ «{tenant.code}» эзний шийдвэрт хамаарахгүй — --apply татгалзана")
            print("DRY-RUN — юу ч бичсэнгүй. Бичихийн тулд --apply --user <нэр>.")
            return 0
        if tenant.code not in VD.POS_APP_TENANTS and not args.off:
            print(f"ЗОГСОВ: зөвхөн {', '.join(sorted(VD.POS_APP_TENANTS))} (эзний шийдвэр 2026-10-13)")
            return 6
        probs = VD.server_problems()
        if probs:
            print("ЗОГСОВ: " + "; ".join(probs))
            return 3
        for r in changed:
            s = r["site"]
            saved = A.set_site_rules(db, A.EBARIMT_KEY, s.id, r["values"], username)
            # Тохиргооны UI-тай (admin_router.payment_rules_site_save) ижил хэлбэр
            db.add(AuditLog(username=username, action="UPDATE", entity="payment_rules",
                            entity_id=str(s.id),
                            detail={A.EBARIMT_KEY: saved, "_tool": TOOL,
                                    "_before": r["before"]["overlay"],
                                    "_users_source": r["info"]["source"]}))
        db.commit()
        A.invalidate_cache()
        print("\nБИЧЛЭЭ. Дараах байдал:")
        for r in changed:
            a = site_state(db, r["site"].id)
            print(f"  {r['site'].site_code}: асаалт={'ON' if a['issues'] else 'OFF'} · "
                  f"аргууд={a['methods']} · хэрэглэгчид={a['users']}")
        print("Ажиллаж буй сервер тохиргоог ≤30с-д уншина (кэш). Түүхэн давхардлыг "
              "tools/vat_cancel_duplicates.py --pos-app цуцална.")
        return 0
    finally:
        db.rollback()
        db.close()


def _same(r) -> bool:
    """Зогсоолын давхаргад аль хэдийн ижил утга бичигдсэн эсэх (дахин ажиллуулахад no-op)."""
    ov, v = r["before"]["overlay"], r["values"] or {}
    return all(ov.get(k) == val for k, val in v.items())


if __name__ == "__main__":
    sys.exit(main())
