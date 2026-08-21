"""ANPR гүүрийн төлөвийг харах + камерын зураглалыг санал болгож бичих.

Гүүрийн статистик нь АЖИЛЛАЖ БУЙ процессын санах ойд байдаг тул зөвхөн HTTP-ээр
уншина. Токеныг энэ хэрэгсэл өөрөө үүсгэнэ (сервер дээр DB болон нууц түлхүүрт
аль хэдийн хандаж байгаа тул нэмэлт эрх шаардахгүй) — нууц үг гараар оруулах,
командын түүхэнд үлдээх шаардлагагүй.

Ажиллуулах (production сервер дээр, backend хавтаст):
    venv/bin/python tools/anpr_status.py               # төлөв харах
    venv/bin/python tools/anpr_status.py --suggest     # зураглалын санал
    venv/bin/python tools/anpr_status.py --suggest --apply   # зураглалыг БИЧИХ
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx  # noqa: E402

from app.auth import create_access_token  # noqa: E402
from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import AuditLog, Device, ParkingSite, User  # noqa: E402

# ANPR системийн зогсоолын нэр → манай site_code (anpr_compare-тэй ижил санаа)
LOT_MAP = {
    "MonnisBuilding": "MONNIS", "Раш булаг": "RASH", "Раш булаг шороо": "RASH",
    "СТӨ": "STO", "Хан гарьд": "HANGARD", "kh": "KH", "Ялалт": "YALALT",
    "Туушин": "TUUSHIN", "Номадс": "NOMADS", "Эрэл": "EREL",
}


def fetch_stats() -> dict:
    """Ажиллаж буй backend-ээс гүүрийн статистикийг авна."""
    db = SessionLocal()
    try:
        admin = (db.query(User).filter(User.role == "SUPER_ADMIN", User.is_active).first()
                 or db.query(User).filter(User.role == "ADMIN", User.is_active).first())
        if admin is None:
            print("Админ хэрэглэгч олдсонгүй")
            sys.exit(1)
        token = create_access_token(admin)
    finally:
        db.close()
    url = "http://127.0.0.1:8000/api/health/anpr-bridge"
    try:
        r = httpx.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:  # noqa: BLE001
        print(f"Backend-ээс уншиж чадсангүй: {type(e).__name__}: {e}")
        sys.exit(1)


def show(st: dict):
    print("══ ANPR гүүрийн төлөв ══\n")
    print(f"   Горим        {st.get('mode')}")
    print(f"   Холбогдсон   {'ТИЙМ' if st.get('connected') else 'ҮГҮЙ'}"
          f"{'   ' + (st.get('error') or '') if not st.get('connected') else ''}")
    print(f"   Эхэлсэн      {st.get('since') or '—'}")
    print(f"   Сүүлийн event {st.get('last_event_at') or '—'}")
    print()
    print(f"   Ирсэн уншилт      {st.get('events', 0):>7}")
    print(f"   Зураглагдсан      {st.get('mapped', 0):>7}")
    print(f"   Манайд ТОХИРСОН   {st.get('matched', 0):>7}")
    print(f"   Манайд БАЙХГҮЙ    {st.get('missing', 0):>7}   ← алдагдал {st.get('loss_pct', 0)}%")
    print(f"   Зураг (imageUpdate){st.get('images', 0):>6}")
    if st.get("last_image_url"):
        print(f"   Сүүлийн зургийн URL: {st['last_image_url']}")
    if st.get("missing_by_site"):
        print("\n   Зогсоолоор (манайд байхгүй):")
        for name, n in st["missing_by_site"].items():
            print(f"      {name:<24}{n:>6}")
    if st.get("unmapped_cams"):
        print(f"\n   ⚠ ЗУРАГЛААГҮЙ камер ({len(st['unmapped_cams'])}) — эдгээрийн "
              f"уншилт тооцоонд ОРООГҮЙ:")
        for key, n in st["unmapped_cams"].items():
            print(f"      {key:<44}{n:>6}")
        print("\n   Зураглах: venv/bin/python tools/anpr_status.py --suggest")


def suggest(st: dict, apply: bool):
    """Зураглаагүй камеруудыг манай төхөөрөмжтэй тааруулж санал болгоно.

    Түлхүүр: `camId·зогсоолын нэр·чиглэл`. Зогсоолыг LOT_MAP-аар, чиглэлийг
    ТЭДНИЙ camDirection-ээр эхлээд таана — ГЭХДЭЭ тэдний чиглэл найдваргүй тул
    зогсоолд ЯГ НЭГ камер тухайн чиглэлд байвал л санал болгоно; олон бол
    гараар шийдүүлнэ."""
    db = SessionLocal()
    try:
        sites = {(s.site_code or "").upper(): s for s in db.query(ParkingSite).all()}
        taken = {str((d.extra or {}).get("anpr_camera_id"))
                 for d in db.query(Device).all() if (d.extra or {}).get("anpr_camera_id")}
        plan, manual = [], []
        for key, n in (st.get("unmapped_cams") or {}).items():
            parts = key.split("·")
            cam_id, lot, direction = (parts + ["?", "?"])[:3]
            if cam_id in taken:
                continue
            site = sites.get(LOT_MAP.get(lot, "").upper())
            if site is None:
                manual.append((key, n, "зогсоол зураглаагүй"))
                continue
            want = "exit" if direction == "exit" else "entry"
            cams = [d for d in db.query(Device).filter(
                Device.site_id == site.id, Device.device_type == "camera",
                Device.status != "deleted").all()
                if d.lane_dir == want and not (d.extra or {}).get("anpr_camera_id")]
            if len(cams) == 1:
                plan.append((cam_id, lot, direction, cams[0], n))
            else:
                manual.append((key, n, f"{site.name}-д {want} чиглэлийн камер {len(cams)}"))

        print("\n══ Зураглалын санал ══\n")
        for cam_id, lot, direction, dev, n in plan:
            print(f"   ANPR {cam_id:>4} ({lot} · {direction}, {n} уншилт)"
                  f"  →  {dev.name} · {dev.ip_address} · {dev.lane_dir}")
        if manual:
            print(f"\n   ГАРААР шийдэх ({len(manual)}):")
            for key, n, why in manual:
                print(f"      {key:<44}{n:>6}   — {why}")
        if not plan:
            print("   Автоматаар санал болгох зүйл алга.")
            return
        if not apply:
            print("\n   Бичих бол --apply нэмнэ үү (Device.extra.anpr_camera_id).")
            return
        for cam_id, lot, direction, dev, _n in plan:
            extra = dict(dev.extra or {})
            extra["anpr_camera_id"] = cam_id
            dev.extra = extra
            db.add(AuditLog(username="tools/anpr_status.py", action="UPDATE", entity="device",
                            entity_id=dev.id,
                            detail={"anpr_camera_id": cam_id, "anpr_lot": lot,
                                    "reason": "ANPR гүүрийн камерын зураглал"}))
        db.commit()
        print(f"\n   ✅ {len(plan)} камер зураглагдлаа. Гүүр дараагийн уншилтаас "
              f"тэдгээрийг тооцно (restart шаардлагагүй).")
    finally:
        db.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--suggest", action="store_true", help="камерын зураглалыг санал болгох")
    ap.add_argument("--apply", action="store_true", help="саналыг БИЧИХ")
    a = ap.parse_args()
    st = fetch_stats()
    show(st)
    if a.suggest:
        suggest(st, a.apply)


if __name__ == "__main__":
    main()
