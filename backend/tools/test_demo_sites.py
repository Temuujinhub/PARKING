"""TEST серверт PROD-ийн зогсоолуудыг хуулж wallet-ийн газрын зураг/захиалгыг турших (2026-10-22).

Wallet-ийн апп (MonPay, SocialPay …) TEST дээр: газрын зураг (`/api/v1/parkings` — координат,
дүүргэлт, захиалга авдаг эсэх) → сул байр/үнэ (`/reservations/availability`) → захиалга. Үүнд
TEST дээр PROD-той ижил зогсоолууд, координат, багтаамж, ДҮҮРГЭЛТ, захиалгын тохиргоо хэрэгтэй.

Эх өгөгдөл — JSON (продын уншихад зориулсан хуулбараас export хийсэн, repo-д ОРУУЛАХГҮЙ):
    {"tenant_map": {"<прод түрээслэгч>": "<TEST түрээслэгчийн нэр>"},
     "tariffs": [{"name", "free_minutes", "grace_minutes", "extra_hour_price", "daily_cap",
                  "prepaid_price", "tiers": [[upto_minutes, price], …]}],
     "sites": [{"site_code", "name", "zone_code", "address", "google_maps_url", "latitude",
                "longitude", "capacity", "is_active", "no_charge", "registered_only",
                "inner_registered_only", "parent_code", "transit_max_hours", "tariff", "tenant",
                "demo_capacity"?, "demo_location"?: {"lat", "lng", "note"}, "demo_fill"?: 0..1}]}

Алхмууд (бүгд анхдагчаар ХАРАХ — `--apply`-гүй бол DB өөрчлөхгүй):
    --import FILE        зогсоол + тариф үүсгэх/шинэчлэх. Байгаа кодыг (KH г.м) шинэчилнэ —
                         түрээслэгч, QPay, төхөөрөмжийг ХӨНДӨХГҮЙ. Төхөөрөмж (камер) ХУУЛАХГҮЙ.
                         Координатгүй бол `demo_location` (хаягт «TEST — ойролцоо байршил»),
                         багтаамж 0 бол `demo_capacity` (зохиомол) — дүүргэлт харагдана.
    --reservations       захиалгыг асаана: байр = max(2, багтаамж // 10); төлбөргүй/доторх үгүй
    --occupancy          зохиомол машинууд (note=TEST-DEMO): `demo_fill` эсвэл кодоос 20–90%;
                         дахин ажиллуулахад зорилтот тоо руу нэмж/хасна (авто хаалт 12ц-д хаадаг)
    --clear-occupancy    TEST-DEMO машинуудыг устгана (төлбөргүй) / хаана (төлбөртэй)
    --tenant-tin         ТТД-гүй TEST түрээслэгчдэд туршилтын ТТД `0000000` (mock e-Barimt
                         ажиллаж receipt.ready ирнэ; EBARIMT_MOCK=true үед л)

    cd /root/PARKING/backend
    venv/bin/python tools/test_demo_sites.py --import /root/parking-deploy-1022/prod_sites.json \\
        --reservations --occupancy --tenant-tin                     # харах
    venv/bin/python tools/test_demo_sites.py … --apply --user temuujin

Хамгаалалт: зөвхөн TEST (PARKING_DEPLOY_ROLE=staging эсвэл /etc/parking/deploy.env) — прод дээр
татгалзана. Өөрчлөлт бүр AuditLog (TEST_DEMO_*).
"""
import argparse
import hashlib
import json
import os
import random
import sys
import uuid
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DEMO_NOTE = "TEST-DEMO"
TEST_TIN = "0000000"


def deploy_role() -> str:
    role = (os.environ.get("PARKING_DEPLOY_ROLE") or "").strip().lower()
    if role:
        return role
    try:
        for line in open("/etc/parking/deploy.env", encoding="utf-8"):
            if line.strip().startswith("PARKING_DEPLOY_ROLE="):
                return line.split("=", 1)[1].strip().strip('"').lower()
    except OSError:
        pass
    return ""


def fill_for(site: dict) -> float:
    if site.get("demo_fill") is not None:
        return max(0.0, min(1.0, float(site["demo_fill"])))
    h = int(hashlib.sha256(site["site_code"].encode()).hexdigest()[:8], 16)
    return 0.2 + (h % 71) / 100          # 0.20–0.90, кодоор тогтмол


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--import", dest="src", metavar="FILE")
    ap.add_argument("--reservations", action="store_true")
    ap.add_argument("--occupancy", action="store_true")
    ap.add_argument("--clear-occupancy", action="store_true")
    ap.add_argument("--tenant-tin", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--user", default="")
    a = ap.parse_args()
    if deploy_role() != "staging":
        sys.exit("ТАТГАЛЗАВ: зөвхөн TEST сервер (PARKING_DEPLOY_ROLE=staging) дээр ажиллана")
    if a.apply and not a.user:
        sys.exit("--apply үед --user заавал")
    if not a.src and (a.reservations or a.occupancy):
        sys.exit("--reservations/--occupancy-д --import FILE (зогсоолын жагсаалт) хэрэгтэй")

    from app.database import SessionLocal
    from app.models import (AuditLog, ParkingSession, ParkingSite, Payment, TariffTemplate,
                            TariffTier, Tenant)
    from app.services import app_settings as A

    data = json.load(open(a.src, encoding="utf-8")) if a.src else {"sites": [], "tariffs": []}
    who = a.user or "dry-run"
    db = SessionLocal()
    changes = []

    def note(msg):
        changes.append(msg)
        print(("  " if a.apply else "  [харах] ") + msg)

    try:
        # ── Тариф ─────────────────────────────────────────────────────────
        tariffs = {}
        if a.src:
            print("== Тариф")
            for t in data.get("tariffs", []):
                name = f"PROD · {t['name']}"
                tmpl = db.query(TariffTemplate).filter(TariffTemplate.name == name).first()
                if tmpl is None:
                    tmpl = TariffTemplate(id=str(uuid.uuid4()), name=name)
                    db.add(tmpl)
                    note(f"тариф үүсгэх: {name}")
                tmpl.free_minutes, tmpl.grace_minutes = int(t["free_minutes"]), int(t["grace_minutes"])
                tmpl.extra_hour_price, tmpl.prepaid_price = t["extra_hour_price"], t.get("prepaid_price") or 0
                tmpl.daily_cap = t.get("daily_cap")
                tmpl.tiers = [TariffTier(id=str(uuid.uuid4()), upto_minutes=int(u), price=p)
                              for u, p in t.get("tiers", [])]
                tariffs[t["name"]] = tmpl

        # ── Түрээслэгч ────────────────────────────────────────────────────
        tenants = {}
        if a.src:
            print("== Түрээслэгч")
            for prod_name, test_name in (data.get("tenant_map") or {}).items():
                ten = db.query(Tenant).filter(Tenant.name == test_name).first()
                if ten is None:
                    code = "ZZ" + hashlib.sha256(test_name.encode()).hexdigest()[:6].upper()
                    ten = Tenant(id=str(uuid.uuid4()), name=test_name, code=code,
                                 note="TEST — продын зогсоолын хуулбарт")
                    db.add(ten)
                    note(f"түрээслэгч үүсгэх: {test_name} ({code})")
                tenants[prod_name] = ten

        # ── Зогсоол ───────────────────────────────────────────────────────
        sites = {}
        if a.src:
            print("== Зогсоол")
            rows = sorted(data["sites"], key=lambda s: bool(s.get("parent_code")))   # эцэг эхлээд
            for s in rows:
                site = db.query(ParkingSite).filter(ParkingSite.site_code == s["site_code"]).first()
                created = site is None
                if created:
                    site = ParkingSite(id=str(uuid.uuid4()), site_code=s["site_code"], name=s["name"])
                    ten = tenants.get(s.get("tenant"))
                    site.tenant_id = ten.id if ten else None
                    db.add(site)
                loc = s.get("demo_location") if s.get("latitude") is None else None
                cap = int(s.get("capacity") or 0) or int(s.get("demo_capacity") or 0)
                site.name, site.zone_code = s["name"], s.get("zone_code") or "A"
                site.address = (s.get("address") or "") + (" (TEST — ойролцоо байршил)" if loc else "")
                site.google_maps_url = s.get("google_maps_url")
                site.latitude = loc["lat"] if loc else s.get("latitude")
                site.longitude = loc["lng"] if loc else s.get("longitude")
                site.capacity = cap
                site.is_active = bool(s.get("is_active", True))
                site.no_charge = bool(s.get("no_charge"))
                site.registered_only = bool(s.get("registered_only"))
                site.inner_registered_only = bool(s.get("inner_registered_only"))
                site.transit_max_hours = s.get("transit_max_hours")
                if s.get("tariff") in tariffs:
                    site.tariff_template_id = tariffs[s["tariff"]].id
                parent = sites.get(s.get("parent_code")) if s.get("parent_code") else None
                site.parent_site_id = parent.id if parent else None
                sites[s["site_code"]] = site
                tags = [x for x in ("шинэ" if created else "шинэчлэх",
                                    "зохиомол байршил" if loc else "",
                                    f"зохиомол багтаамж {cap}" if not s.get("capacity") and cap else "",
                                    f"доторх ← {s['parent_code']}" if parent else "") if x]
                note(f"{s['site_code']:<27} {s['name']:<26} {', '.join(tags)}")
            db.flush()

        # ── Захиалга ──────────────────────────────────────────────────────
        if a.reservations:
            print("== Захиалга (reservation_rules)")
            for code, site in sites.items():
                if site.parent_site_id or site.no_charge or not site.is_active or not site.capacity:
                    continue
                spots = max(2, site.capacity // 10)
                note(f"{code:<27} захиалга асаах, байр {spots}")
                if a.apply:
                    A.set_site_rules(db, A.RESERVATION_KEY, site.id, {"enabled": True, "spots": spots}, who)

        # ── Туршилтын ТТД (mock e-Barimt) ─────────────────────────────────
        if a.tenant_tin:
            print("== Туршилтын ТТД")
            for ten in db.query(Tenant).all():
                if not (ten.ebarimt_merchant_tin or "").strip():
                    note(f"{ten.name}: ebarimt_merchant_tin = {TEST_TIN} (TEST mock)")
                    ten.ebarimt_merchant_tin = TEST_TIN

        # ── Дүүргэлт ──────────────────────────────────────────────────────
        if a.clear_occupancy:
            print("== Зохиомол машин устгах")
            demo = db.query(ParkingSession).filter(ParkingSession.note == DEMO_NOTE).all()
            paid = {p for (p,) in db.query(Payment.session_id).filter(
                Payment.session_id.in_([d.id for d in demo])).all()} if demo else set()
            for d in demo:
                if d.id in paid:
                    if d.status in ("OPEN", "AWAITING_PAYMENT", "PAID"):
                        d.status, d.exit_time = "MANUAL_CLOSED", datetime.utcnow()
                else:
                    db.delete(d)
            note(f"{len(demo)} зохиомол сешн (төлбөртэй {len(paid)} нь хаагдана)")
        if a.occupancy:
            print("== Дүүргэлт (зохиомол машин, note=TEST-DEMO)")
            now = datetime.utcnow()
            by_code = {s["site_code"]: s for s in data["sites"]}
            for code, site in sites.items():
                if site.parent_site_id or not site.is_active or not site.capacity:
                    continue
                target = round(site.capacity * fill_for(by_code[code]))
                live = (db.query(ParkingSession).filter(
                    ParkingSession.site_id == site.id,
                    ParkingSession.status.in_(("OPEN", "AWAITING_PAYMENT", "PAID"))).all())
                demo = [x for x in live if x.note == DEMO_NOTE]
                need = target - len(live)
                taken = {x.plate_number for x in live}
                rnd = random.Random(code)
                added = 0
                for i in range(1, 9000):
                    if added >= need:
                        break
                    plate = f"{1000 + i:04d}ДМО"
                    if plate in taken:
                        continue
                    db.add(ParkingSession(id=str(uuid.uuid4()), site_id=site.id, plate_number=plate,
                                          entry_time=now - timedelta(minutes=rnd.randint(5, 240)),
                                          status="OPEN", note=DEMO_NOTE))
                    added += 1
                removed = 0
                for x in demo[:max(0, -need)]:
                    db.delete(x)
                    removed += 1
                note(f"{code:<27} {target:>4}/{site.capacity:<4} "
                     f"({round(100 * target / site.capacity)}%) +{added} −{removed}")

        if a.apply:
            db.add(AuditLog(username=who, action="TEST_DEMO_SITES", entity="parking_site", entity_id="-",
                            detail={"changes": len(changes), "import": a.src,
                                    "flags": {k: getattr(a, k) for k in
                                              ("reservations", "occupancy", "clear_occupancy", "tenant_tin")}}))
            db.commit()
            print(f"\nХАДГАЛАВ ({len(changes)} өөрчлөлт)")
        else:
            db.rollback()
            print(f"\nХАРАХ горим — {len(changes)} өөрчлөлт. Хадгалах бол: --apply --user <нэр>")
    finally:
        db.close()


if __name__ == "__main__":
    main()
