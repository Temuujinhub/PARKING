#!/usr/bin/env python
"""Камерын НЭВТРЭЛТ + ЗУРАГ ТАТАЛТ + ЛОГ НӨХӨЛТИЙН нэгдсэн байдал.

    cd /root/PARKING/backend
    venv/bin/python ../tools/cam_status.py                 # бүх зогсоол
    venv/bin/python ../tools/cam_status.py --site HANGARID # нэг зогсоол
    venv/bin/python ../tools/cam_status.py --hours 72      # илүү өргөн цонх
    venv/bin/python ../tools/cam_status.py --camsync-dry   # лог нөхөлтийг ТУРШИХ

Гурван асуултад хариулна:
  1) НЭВТРЭЛТ — камер бүр DB-дээ ямар хэрэглэгчтэй вэ (sysadmin болсон уу,
     эсвэл .env-ийн глобалыг ашиглаж байна уу), rotate хэзээ ажилласан бэ.
  2) ЗУРАГ — нэвтрэлт солихоос ӨМНӨХ ба ДАРААХ зургийн амжилтын хувь
     (session.entry_snapshot/exit_snapshot дүүрсэн эсэх) зогсоол тус бүрээр.
  3) ЛОГ НӨХӨЛТ — зогсоол бүрийн camsync watermark хэр шинэ вэ.

Нууц үг ХЭВЛЭХГҮЙ — зөвхөн хэрэглэгчийн нэр, тохируулсан эсэх.
"""
import argparse
import os
import sys
import warnings
from datetime import datetime, timedelta

# Кодын сан бүхэлдээ naive utcnow ашигладаг — оношилгооны гаралтыг
# анхааруулгаар бөглөхгүйн тулд дарна (зан төлөвт нөлөөлөхгүй).
warnings.filterwarnings("ignore", category=DeprecationWarning)

BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
sys.path.insert(0, BACKEND)
os.chdir(BACKEND)

from app.database import SessionLocal  # noqa: E402
from app.models import AuditLog, Device, ParkingSession, ParkingSite, Tenant  # noqa: E402

TZ = timedelta(hours=8)  # УБ


def L(dt):
    return (dt + TZ).strftime("%m-%d %H:%M") if dt else "—"


def pct(ok, total):
    return f"{100.0 * ok / total:5.1f}% ({ok}/{total})" if total else "     —  (0)"


def creds_section(db, sites):
    """1) Камер бүрийн DB дэх нэвтрэх нэр + rotate-ийн аудит."""
    print("══ 1. КАМЕРЫН НЭВТРЭЛТ ══\n")
    rot = (db.query(AuditLog).filter(AuditLog.action == "DEVICE_CREDS_ROTATE")
           .order_by(AuditLog.created_at.desc()).first())
    if rot:
        d = rot.detail or {}
        print(f"Сүүлийн rotate: {L(rot.created_at)}  ·  {d.get('user') or d.get('username') or '?'}"
              f"  ·  {d.get('changed', d.get('count', '?'))} камер")
    else:
        print("⚠ DEVICE_CREDS_ROTATE аудит ОЛДСОНГҮЙ — set_camera_creds --apply "
              "хараахан ажиллаагүй байна.")
    print()

    site_ids = {s.id for s in sites}
    cams = (db.query(Device)
            .filter(Device.device_type == "camera", Device.status == "active",
                    Device.site_id.in_(site_ids))
            .order_by(Device.site_id, Device.ip_address).all())
    by_site = {}
    for c in cams:
        by_site.setdefault(c.site_id, []).append(c)

    now = datetime.utcnow()
    n_named, n_global, silent = 0, 0, []
    for s in sites:
        cl = by_site.get(s.id, [])
        if not cl:
            continue
        tenant = db.get(Tenant, s.tenant_id) if s.tenant_id else None
        print(f"── {s.name} ({s.site_code})  ·  түрээслэгч: {tenant.name if tenant else '—'}")
        for c in cl:
            if c.username:
                n_named += 1
                who = f"DB: {c.username}"
            else:
                n_global += 1
                who = ".env глобал (тохируулаагүй)"
            # Камер event илгээхээ больсныг ЭНД шууд харуулна — «зураг ирэхгүй»
            # гэдгийн хамгийн түгээмэл шалтгаан нь камер өөрөө чимээгүй болсон
            # байх бөгөөд credential-ийн үр дүнтэй андуурч болзошгүй.
            mins = int((now - c.last_seen).total_seconds() // 60) if c.last_seen else None
            flag = ""
            if mins is None or mins >= 30:
                flag = f"  ⚠ {mins} мин чимээгүй" if mins is not None else "  ⚠ огт харагдаагүй"
                silent.append(f"{s.name}/{c.ip_address}")
            print(f"   {c.ip_address:16} {c.name[:22]:24} {who}"
                  f"   сүүлд: {L(c.last_seen)}{flag}")
        print()
    print(f"Нийт: DB-д нэвтрэлт бэхэлсэн {n_named}, .env глобалаар ажиллаж буй {n_global}")
    if silent:
        print(f"⚠ ЧИМЭЭГҮЙ камер ({len(silent)}): {', '.join(silent)}")
        print("   Эдгээрийн зураг 0% байвал шалтгаан нь нэвтрэлт БИШ — камер/сүлжээ.")
    print()


def snapshot_section(db, sites, hours, split_at):
    """2) Зургийн амжилт — зааг цагийн ӨМНӨ/ДАРАА харьцуулна."""
    print("══ 2. ЗУРАГ ТАТАЛТЫН АМЖИЛТ ══\n")
    print(f"Зааг: {L(split_at)} (нэвтрэлт солигдсон үе) · цонх: сүүлийн {hours} цаг\n")
    now = datetime.utcnow()
    since = now - timedelta(hours=hours)

    print(f"{'Зогсоол':22} {'ОРОХ зураг: өмнө':>20} {'дараа':>20}    "
          f"{'ГАРАХ зураг: өмнө':>19} {'дараа':>20}")
    for s in sites:
        rows = (db.query(ParkingSession)
                .filter(ParkingSession.site_id == s.id,
                        ParkingSession.entry_time >= since).all())
        if not rows:
            continue

        def stat(sel, want_exit=False):
            tot = ok = 0
            for r in sel:
                if want_exit:
                    if not r.exit_time:
                        continue
                    tot += 1
                    ok += 1 if r.exit_snapshot else 0
                else:
                    tot += 1
                    ok += 1 if r.entry_snapshot else 0
            return ok, tot

        before = [r for r in rows if r.entry_time < split_at]
        after = [r for r in rows if r.entry_time >= split_at]
        eb, et = stat(before)
        ea, at_ = stat(after)
        xb, xt = stat(before, True)
        xa, xt2 = stat(after, True)

        def arrow(b, bt, a, at2):
            """Чиглэл: түүвэр хэт бага бол дүгнэхгүй (санамсаргүй хэлбэлзэл)."""
            if not bt or at2 < 5:
                return "?"
            d = (100.0 * a / at2) - (100.0 * b / bt)
            return "↑" if d >= 10 else ("↓" if d <= -10 else "→")

        print(f"{s.name[:22]:22} {pct(eb, et):>20} {pct(ea, at_):>20}"
              f" {arrow(eb, et, ea, at_):2}"
              f" {pct(xb, xt):>19} {pct(xa, xt2):>20}"
              f" {arrow(xb, xt, xa, xt2):2}")
    print("\n↑ сайжирсан · → өөрчлөлтгүй · ↓ муудсан · ? түүвэр цөөн (5-аас бага) тул дүгнэхгүй")
    print("«дараа» өндөр бол нэвтрэлт солилт зурагт ТУСАЛСАН. Хоёулаа бага хэвээр бол")
    print("камер өөрөө гацсан байх — camera_snapshot_health.py --all ажиллуулна.\n")


def camsync_section(db, sites):
    """3) Лог нөхөлтийн watermark — зогсоол бүр хаана хүрсэн бэ."""
    print("══ 3. КАМЕРЫН ЛОГ НӨХӨЛТ (camsync) ══\n")
    from app.services.app_settings import CAMSYNC_STATE, get_camsync_rules, get_state
    rules = get_camsync_rules(db)
    print(f"Дүрэм: {'АСААЛТТАЙ' if rules.get('enabled') else 'УНТРААЛТТАЙ'}"
          f"  ·  өдөрт {rules.get('times_per_day', '?')} удаа"
          f"  ·  ухрах дээд {rules.get('lookback_hours', '?')} цаг"
          f"  ·  өр үүсгэх: {'тийм' if rules.get('create_debt') else 'үгүй'}\n")
    if not rules.get("enabled"):
        print("   ⚠ Лог нөхөлт УНТРААЛТТАЙ — Тохиргоо → Авто цэвэрлэгээ хэсгээс асаана.\n")
    # camera_sync нь watermark-ыг state[site_id] = ISO цаг гэж шууд хадгалдаг
    state = get_state(db, CAMSYNC_STATE) or {}
    now = datetime.utcnow()
    for s in sites:
        raw = state.get(s.id)
        wm = None
        if isinstance(raw, str):
            try:
                wm = datetime.fromisoformat(raw)
            except ValueError:
                wm = None
        age = f"{int((now - wm).total_seconds() // 60)} мин хоцорсон" if wm else "watermark алга"
        print(f"   {s.name[:24]:26} {L(wm):14}  {age}")
    print()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", action="append", default=[], help="зогсоолын код (олон удаа)")
    ap.add_argument("--hours", type=int, default=48, help="зургийн харьцуулалтын цонх")
    ap.add_argument("--split-hours-ago", type=float, default=None,
                    help="зааг цагийг гараар өгөх (default: rotate аудитын цаг)")
    ap.add_argument("--camsync-dry", action="store_true",
                    help="лог нөхөлтийг ТУРШИЖ ажиллуулна (DB бичихгүй)")
    a = ap.parse_args()

    db = SessionLocal()
    q = db.query(ParkingSite).filter(ParkingSite.is_active.is_(True))
    if a.site:
        q = q.filter(ParkingSite.site_code.in_([x.upper() for x in a.site]))
    sites = q.order_by(ParkingSite.name).all()
    if not sites:
        print("Зогсоол олдсонгүй")
        return

    now = datetime.utcnow()
    if a.split_hours_ago is not None:
        split_at = now - timedelta(hours=a.split_hours_ago)
    else:
        rot = (db.query(AuditLog).filter(AuditLog.action == "DEVICE_CREDS_ROTATE")
               .order_by(AuditLog.created_at.desc()).first())
        split_at = rot.created_at if rot else now - timedelta(hours=6)

    creds_section(db, sites)
    snapshot_section(db, sites, a.hours, split_at)
    camsync_section(db, sites)

    if a.camsync_dry:
        print("══ ЛОГ НӨХӨЛТИЙН ТУРШИЛТ (dry-run — юу ч бичихгүй) ══\n")
        from app.services.camera_sync import run_once
        for r in run_once(dry_run=True):
            print(f"   {r}")
        print()


if __name__ == "__main__":
    main()
