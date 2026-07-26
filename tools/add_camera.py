#!/usr/bin/env python3
"""Зогсоолд орох/гарах камер бүртгэх — вэб UI-гүйгээр, сервер дээр шууд.

Хаалтыг ГАРААР нэмэх шаардлагагүй: камер бүртгэгдмэгц ижил эгнээнд хаалт
(device_auto.ensure_lane_barriers) автоматаар үүснэ. Dahua ANPR кит хаалтаа
камерынхаа релеэр удирддаг тул хаалтад тусдаа IP хэрэггүй.

Ажиллуулах (production сервер дээр):
    sudo /root/PARKING/backend/venv/bin/python /root/PARKING/tools/add_camera.py \
        --site KH --entry 10.0.101.10 --exit 10.0.101.11

    # зөвхөн нэг талыг нь бүртгэх бас болно
    sudo .../add_camera.py --site SPORT --entry 10.0.104.10

    # бүртгэгдсэнийг харах
    sudo .../add_camera.py --list

Эгнээний журам (UI-ийн шидтэнтэй ижил): орох = lane 1 / entry, гарах = lane 2 / exit.
Идемпотент — ижил зогсоол+чиглэлд дахин ажиллуулбал зөвхөн IP-г шинэчилнэ.
"""
import argparse
import os
import sys

BACKEND = "/root/PARKING/backend"
sys.path.insert(0, BACKEND)
os.chdir(BACKEND)

import secrets  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models import Device, ParkingSite  # noqa: E402
from app.services.device_auto import ensure_lane_barriers, fetch_camera_model  # noqa: E402


def find_site(db, code: str) -> ParkingSite:
    site = next((s for s in db.query(ParkingSite).all()
                 if s.site_code.upper() == code.strip().upper()), None)
    if not site:
        codes = [s.site_code for s in db.query(ParkingSite).all()]
        print(f"АЛДАА: '{code}' кодтой зогсоол олдсонгүй. Байгаа: {codes}", file=sys.stderr)
        sys.exit(1)
    return site


def list_devices(db) -> int:
    rows = db.query(Device).filter(Device.status == "active").all()
    if not rows:
        print("Идэвхтэй төхөөрөмж алга.")
        return 0
    by_site: dict[str, list] = {}
    for d in rows:
        s = db.get(ParkingSite, d.site_id)
        by_site.setdefault(s.site_code if s else "?", []).append(d)
    warned = False
    for code, devs in sorted(by_site.items()):
        print(f"\n{code}:")
        for d in sorted(devs, key=lambda x: (x.lane_no, x.device_type)):
            creds = ""
            if d.device_type == "camera":
                creds = ("  [өөрийн нэвтрэлт]" if (d.username or d.password)
                         else "  [ерөнхий нэвтрэлт]")
            print(f"  эгнээ {d.lane_no} · {d.lane_dir:6} · {d.device_type:8} · "
                  f"IP {d.ip_address or '—':15} · {d.name}{creds}")

        # Хаалт нь ӨӨРИЙН IP-гүй бол ижил ЭГНЭЭНИЙ камерын IP-г ашигладаг. Эгнээ
        # зөрвөл хаалт "IP олдсонгүй" гэж уначихдаг — энэ нь нүдэнд харагдахгүй
        # тул тусад нь анхааруулна.
        cam_lanes = {d.lane_no for d in devs if d.device_type == "camera" and d.ip_address}
        for d in devs:
            if d.device_type == "barrier" and not d.ip_address and d.lane_no not in cam_lanes:
                warned = True
                print(f"  !! «{d.name}» (эгнээ {d.lane_no}) — энэ эгнээнд IP-тэй камер алга.")
                print(f"     Хаалт нь ижил эгнээний камерын IP-гээр ажилладаг тул энэ "
                      f"хаалт НЭЭГДЭХГҮЙ.")
                print(f"     Засах: {d.lane_dir} камерын 'Эгнээ' талбарыг {d.lane_no} болгох "
                      f"(эсвэл хаалтынхыг камерынхтай тааруулах).")
    if warned:
        print("\n  Журам: орох = эгнээ 1, гарах = эгнээ 2. Камер ба хаалт нь ижил "
              "эгнээтэй байх ёстой.")
    return 0


def upsert_camera(db, site: ParkingSite, lane_dir: str, ip: str,
                  username: str | None = None, password: str | None = None) -> Device:
    lane_no = 1 if lane_dir == "entry" else 2
    name = "Орох камер" if lane_dir == "entry" else "Гарах камер"

    cam = db.query(Device).filter(
        Device.site_id == site.id, Device.device_type == "camera",
        Device.lane_dir == lane_dir, Device.status == "active").first()

    if cam:
        print(f"  '{site.site_code}' {lane_dir}: камер бүртгэлтэй байна "
              f"(IP {cam.ip_address or '—'}) → IP-г {ip} болгож шинэчилнэ")
        cam.ip_address = ip
    else:
        cam = Device(site_id=site.id, name=name, device_type="camera", vendor="Dahua",
                     ip_address=ip, lane_no=lane_no, lane_dir=lane_dir,
                     auto_open=(lane_dir == "entry"), status="active",
                     device_key=f"camera-{secrets.token_hex(8)}")
        db.add(cam)
        print(f"  '{site.site_code}' {lane_dir}: шинэ камер бүртгэв (IP {ip}, эгнээ {lane_no})")

    if username is not None:
        cam.username = username.strip() or None
    if password is not None:
        cam.password = password.strip() or None
    if cam.username or cam.password:
        print(f"      нэвтрэлт: {cam.username or '(ерөнхий нэр)'} / "
              f"{'***' if cam.password else '(ерөнхий нууц үг)'}")

    model = fetch_camera_model(ip, cam)
    if model:
        cam.model = model
        print(f"      загвар камераас уншсан: {model}")
    else:
        print("      загвар уншигдсангүй (камер унтарсан/хүрэхгүй байж болно) — "
              "бүртгэлд саад болохгүй")
    return cam


def main() -> int:
    p = argparse.ArgumentParser(description="Зогсоолд камер бүртгэх")
    p.add_argument("--list", action="store_true", help="Бүртгэлтэй төхөөрөмжүүдийг харуулах")
    p.add_argument("--site", help="Зогсоолын код (жишээ: KH, SPORT)")
    p.add_argument("--entry", help="Орох камерын IP")
    p.add_argument("--exit", dest="exit_ip", help="Гарах камерын IP")
    p.add_argument("--username", default=None,
                   help="Энэ зогсоолын камеруудын нэвтрэх нэр (хоосон = ерөнхий тохиргоо)")
    p.add_argument("--password", default=None,
                   help="Энэ зогсоолын камеруудын нууц үг (хоосон = ерөнхий тохиргоо)")
    args = p.parse_args()

    db = SessionLocal()
    try:
        if args.list:
            return list_devices(db)
        if not args.site or not (args.entry or args.exit_ip
                                 or args.username is not None or args.password is not None):
            p.error("--site болон --entry/--exit (эсвэл --username/--password) заавал "
                    "— бүртгэлтэйг харах бол --list")

        site = find_site(db, args.site)
        print(f"\nЗогсоол: {site.name} ({site.site_code})")

        # IP заагаагүй, зөвхөн нэвтрэлт өгсөн бол: тухайн зогсоолын БҮХ камерт хэрэглэнэ
        if not args.entry and not args.exit_ip:
            cams = db.query(Device).filter(
                Device.site_id == site.id, Device.device_type == "camera",
                Device.status == "active").all()
            if not cams:
                print("  Энэ зогсоолд идэвхтэй камер алга.", file=sys.stderr)
                return 1
            for cam in cams:
                if args.username is not None:
                    cam.username = args.username.strip() or None
                if args.password is not None:
                    cam.password = args.password.strip() or None
                print(f"  {cam.name} ({cam.ip_address or '—'}): нэвтрэлт шинэчлэв → "
                      f"{cam.username or '(ерөнхий нэр)'} / "
                      f"{'***' if cam.password else '(ерөнхий нууц үг)'}")
            db.commit()
            print("\nДараа нь: sudo systemctl restart parking-backend")
            return 0

        if args.entry:
            upsert_camera(db, site, "entry", args.entry.strip(), args.username, args.password)
        if args.exit_ip:
            upsert_camera(db, site, "exit", args.exit_ip.strip(), args.username, args.password)

        db.flush()
        # Камер бүрд ижил эгнээний хаалт байгааг баталгаажуулна (байхгүй бол үүсгэнэ)
        res = ensure_lane_barriers(db)
        db.commit()
        print(f"\nХаалт: {res.get('created', 0)} шинэ, {res.get('restored', 0)} сэргээв")

        print("\nОдоогийн байдал:")
        list_devices(db)
        print("\nДараагийн алхам:")
        print("  1. Холболт шалгах:")
        print("     sudo /root/PARKING/backend/venv/bin/python "
              "/root/PARKING/tools/camera_check.py --all")
        print("  2. Backend дахин асаах (event poller шинэ камеруудыг барина):")
        print("     sudo systemctl restart parking-backend")
        print("  3. UI → Хаалтны удирдлага хэсгээс зогсоолоо сонгож 'Нээх' товчоор турших")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
