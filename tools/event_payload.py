#!/usr/bin/env python3
"""Камераас манай сервер рүү ЯГ ЯМАР мэдээлэл ирдгийг харах.

Камер дээр ANPR event үүсэхэд (ж: «5754УЕО, White, Vehicle Front, Approaching»)
манай сервер `eventManager.cgi?action=attach` стримээр JSON хүлээж авдаг. Тэр
JSON-ыг бүтнээр нь `lpr_events.raw` баганад хадгалдаг тул энэ хэрэгслээр
БОДИТ өгөгдлийг харж болно.

Юуг харуулах вэ:
  • Сүүлийн event-үүдийн ТҮҮХИЙ JSON (зураг хассан)
  • Дугаар/итгэлцүүрийг АЛЬ талбараас уншсан
  • ЗУРАГ-шинжтэй талбар байгаа эсэх (энэ л байвал зураг event дотор ирнэ)
  • Бүх event дээрх дээд түвшний талбаруудын статистик

    sudo /root/PARKING/backend/venv/bin/python /root/PARKING/tools/event_payload.py --ip 10.0.102.10
    sudo ... event_payload.py --plate 5754УЕО
    sudo ... event_payload.py --site "Кэй Эйч" --limit 3 --full
"""
import argparse
import json
import os
import re
import sys
from collections import Counter

os.chdir("/root/PARKING/backend")
sys.path.insert(0, "/root/PARKING/backend")

from app.database import SessionLocal  # noqa: E402
from app.models import Device, LprEvent, ParkingSite  # noqa: E402

# Зурагтай холбоотой байж болох талбарын нэрс (эдгээр байвал зураг ирж байна)
PIC_HINT = re.compile(r"pic|image|snap|photo|content|url|path|file", re.I)


def pic_refs(obj, prefix="", out=None, depth=0):
    out = [] if out is None else out
    if depth > 6 or len(out) > 30:
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            path = f"{prefix}.{k}" if prefix else k
            if isinstance(v, (dict, list)):
                pic_refs(v, path, out, depth + 1)
            elif PIC_HINT.search(k) and v not in (None, "", 0):
                out.append(f"{path} = {str(v)[:100]}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:5]):
            pic_refs(v, f"{prefix}[{i}]", out, depth + 1)
    return out


def plate_source(data: dict) -> str:
    """Дугаарыг аль талбараас уншсаныг харуулна (cgi_poller._plate_from-той ижил)."""
    for name, c in (("Plate", data.get("Plate")),
                    ("TrafficCar", data.get("TrafficCar")),
                    ("Picture.Plate", (data.get("Picture") or {}).get("Plate")),
                    ("(дээд түвшин)", data)):
        if isinstance(c, dict):
            for key in ("PlateNumber", "PlateNo", "plateNumber"):
                if c.get(key):
                    return f"{name}.{key} = {c[key]!r}"
    return "ОЛДООГҮЙ"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ip", default=None, help="Камерын IP")
    ap.add_argument("--site", default=None, help="Зогсоолын нэрийн хэсэг")
    ap.add_argument("--plate", default=None, help="Тодорхой дугаар")
    ap.add_argument("--limit", type=int, default=2, help="Хэдэн event бүтнээр (default 2)")
    ap.add_argument("--stats", type=int, default=200, help="Статистикт хэдэн event (default 200)")
    ap.add_argument("--full", action="store_true", help="JSON-ыг таслалгүй бүтнээр")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        q = db.query(LprEvent).order_by(LprEvent.created_at.desc())
        label = "бүх камер"
        if args.ip:
            d = db.query(Device).filter(Device.ip_address == args.ip).first()
            if not d:
                print(f"{args.ip} төхөөрөмж олдсонгүй")
                sys.exit(1)
            q = q.filter(LprEvent.device_id == d.id)
            label = f"{d.name} ({args.ip})"
        elif args.site:
            s = next((x for x in db.query(ParkingSite).all()
                      if args.site.strip().lower() in (x.name or "").lower()), None)
            if not s:
                print("Зогсоол олдсонгүй")
                sys.exit(1)
            q = q.filter(LprEvent.site_id == s.id)
            label = s.name
        if args.plate:
            q = q.filter(LprEvent.plate_number.ilike(f"%{args.plate.upper()}%"))
            label += f" · {args.plate.upper()}"

        rows = q.limit(max(args.limit, args.stats)).all()
        if not rows:
            print("Event олдсонгүй.")
            return
        print(f"=== {label} · сүүлийн {len(rows)} event ===\n")

        # 1. Түүхий JSON
        for i, e in enumerate(rows[:args.limit], 1):
            raw = e.raw if isinstance(e.raw, dict) else {}
            print(f"── EVENT {i} · {e.created_at:%Y-%m-%d %H:%M:%S} UTC · "
                  f"{e.plate_number} · {e.lane_dir} · "
                  f"{'зөвшөөрсөн' if e.accepted else 'ТАТГАЛЗСАН: ' + (e.reject_reason or '')}")
            print(f"   Дугаар уншсан талбар: {plate_source(raw)}")
            print(f"   Итгэлцүүр: {e.confidence}")
            refs = pic_refs(raw)
            print(f"   ЗУРАГ-шинжтэй талбар: {'; '.join(refs) if refs else 'ОЛДСОНГҮЙ'}")
            js = json.dumps(raw, ensure_ascii=False, indent=2)
            print("   ── түүхий JSON ──")
            print("\n".join("   " + ln for ln in
                            (js if args.full else js[:2500]).splitlines()))
            if not args.full and len(js) > 2500:
                print(f"   … (нийт {len(js):,} тэмдэгт, бүтнээр харах бол --full)")
            print()

        # 2. Талбарын статистик
        top = Counter()
        codes = Counter()
        has_pic = 0
        for e in rows:
            raw = e.raw if isinstance(e.raw, dict) else {}
            top.update(raw.keys())
            codes[str(raw.get("Code", "?"))] += 1
            if pic_refs(raw):
                has_pic += 1
        print(f"── {len(rows)} event дээрх статистик ──")
        print("  Event Code:")
        for c, n in codes.most_common(8):
            print(f"    {c:28} {n:>5}")
        print("  Дээд түвшний талбарууд:")
        for k, n in top.most_common(20):
            print(f"    {k:28} {n:>5} ({n / len(rows) * 100:.0f}%)")
        print(f"\n  ЗУРАГ-шинжтэй талбартай event: {has_pic}/{len(rows)} "
              f"({has_pic / len(rows) * 100:.0f}%)")
        if has_pic == 0:
            print("  → Камер зургаа event дотор ИЛГЭЭХГҮЙ байна. Тиймээс зургийг")
            print("    snapshot.cgi-гээр тусад нь татах шаардлагатай болдог.")
            print("    Камерын Web UI → Picture → Storage → Upload Picture дээр")
            print("    ANPR-ийн Original Image-ийг чагтлавал энд орж ирнэ.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
