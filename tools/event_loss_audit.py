#!/usr/bin/env python3
"""Камер уншсан ч системд бүртгэгдээгүй машин бүрд ЯАГААД алдагдсаныг логоос олох.

Кэй Эйч дээр 48 цагт 36 машин (орох event-ийн ~11%) бүртгэгдээгүй өнгөрсөн.
Нөхөж бүртгэх нь үр дагаврыг л засна — энэ хэрэгсэл нь ШАЛТГААНЫГ олно:

  1. Камерын дотоод логоос орох event-үүдийг татна
  2. DB-ийн session-тэй тулгаж бүртгэгдээгүйг ялгана
  3. Тухайн АГШИН БҮРД serverийн журналд юу болж байсныг харна (±context сек)
  4. Шалтгаанаар нь ангилж, хамгийн түгээмэлийг нь эхэнд гаргана

Ангилал:
  • сервер унтарсан / journal хоосон  — тэр агшинд backend ажиллаагүй
  • камерын холболт тасарсан          — poller дахин холбогдож байсан
  • давхар уншилт (dedup)             — АЛДАГДААГҮЙ, зориудаар алгассан
  • дараалал дүүрсэн                  — ачаалал даалгүй event хаягдсан
  • алдаа гарсан                      — боловсруулалтын алдаа (traceback)
  • тодорхойгүй                       — лог чимээгүй, гэхдээ ажиллаж байсан

    sudo /root/PARKING/backend/venv/bin/python /root/PARKING/tools/event_loss_audit.py \
        --site "Кэй Эйч" --hours 48
    # тодорхой машины эргэн тойрны логийг бүтнээр:
        ... --site "Кэй Эйч" --plate 8480УБЗ --context 300
"""
import argparse
import os
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

os.chdir("/root/PARKING/backend")
sys.path.insert(0, "/root/PARKING/backend")

from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import ParkingSession, ParkingSite  # noqa: E402
from app.services.camera_records import site_camera_events  # noqa: E402

# ЧУХАЛ: snapshot.cgi-ийн алдаа (HTTP 400) нь ЗУРАГ татахтай холбоотой бөгөөд
# event алдагдахад НӨЛӨӨЛӨХГҮЙ. Түүнийг «камер татгалзсан» гэж ангилвал 58%
# нь худал тайлбар болно (2026-08-10-нд ийм алдаа гаргасан) — тиймээс эхлээд
# хамааралгүй чимээг ЗАДГАЙ хаяна.
NOISE = re.compile(r"snapshot\.cgi|зураг ОЛДСОНГҮЙ|RPC түгжээг|GET /api/|POST /api/|"
                   r"PUT /api/|хаалт open: SUCCESS|\[screen\]")

# Дараалал нь ЧУХАЛ — эхнийх нь ялна
PATTERNS = [
    ("сервис дахин эхэлсэн",    re.compile(r"Stopping parking-backend|Started parking-backend|"
                                           r"Shutting down|зогсож байна")),
    ("камерын холболт тасарсан", re.compile(r"холболт тасарлаа|ХОЛБОГДЛОО|дахин холбогдож")),
    ("дараалал дүүрсэн",        re.compile(r"дараалал дүүрлээ|event АЛДАГДЛАА")),
    ("камер татгалзсан",        re.compile(r"татгалзлаа|eventManager\.cgi-ийн БҮХ")),
    ("боловсруулалтын алдаа",   re.compile(r"Traceback|event боловсруулах алдаа|Exception")),
    ("DB удаашрал",             re.compile(r"lock_timeout|OperationalError|түгжээтэй")),
]
RESTART = re.compile(r"Started parking-backend|Stopping parking-backend")


def journal(since_local: datetime, until_local: datetime) -> list:
    """[(datetime_local, мөр)] — parking-backend-ийн журнал."""
    cmd = ["journalctl", "-u", "parking-backend", "-o", "short-iso", "--no-pager",
           "--since", since_local.strftime("%Y-%m-%d %H:%M:%S"),
           "--until", until_local.strftime("%Y-%m-%d %H:%M:%S")]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=300).stdout
    except Exception as e:  # noqa: BLE001
        print(f"journalctl ажиллуулж чадсангүй: {e}")
        return []
    rows = []
    for ln in out.splitlines():
        m = re.match(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})", ln)
        if not m:
            continue
        try:
            rows.append((datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S"), ln))
        except ValueError:
            pass
    return rows


def classify(lines: list, plate: str, cam_ip: str | None) -> str:
    """Тухайн event ЯАГААД алдагдсаныг журналын мөрүүдээс тогтооно.

    Дараалал: (1) энэ дугаар логт байсан уу → боловсруулагдсан, (2) лог хоосон
    → сервер унтарсан, (3) тодорхой алдааны хэв маяг, (4) камерын IP-гээс
    ямар нэг event ирж байсан уу → камер ажиллаж байсан ч ЭНЭ event ирээгүй."""
    if not lines:
        return "сервер унтарсан / journal хоосон"
    if plate and any(plate in ln for ln in lines):
        return "боловсруулагдсан (dedup/бусад)"
    # Хамааралгүй чимээг хассан утга бүхий мөрүүд
    signal = [ln for ln in lines if not NOISE.search(ln)]
    blob = "\n".join(signal)
    for label, rx in PATTERNS:
        if rx.search(blob):
            return label
    if cam_ip:
        if any(cam_ip in ln for ln in lines):
            return "камер ажиллаж байсан ч энэ event ирээгүй"
        return "энэ камераас event огт ирээгүй"
    return "тодорхойгүй (лог чимээгүй)"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--site", required=True, help="Зогсоолын нэрийн хэсэг")
    ap.add_argument("--hours", type=float, default=48, help="Сүүлийн N цаг (default 48)")
    ap.add_argument("--context", type=int, default=90, help="Event тойрны секунд (default 90)")
    ap.add_argument("--plate", default=None, help="Зөвхөн энэ дугаарын дэлгэрэнгүй")
    ap.add_argument("--show", type=int, default=6, help="Хэдэн жишээ харуулах (default 6)")
    args = ap.parse_args()

    tz = timedelta(hours=settings.tz_offset_hours)
    db = SessionLocal()
    try:
        site = next((s for s in db.query(ParkingSite).all()
                     if args.site.strip().lower() in (s.name or "").lower()), None)
        if site is None:
            print("Зогсоол олдсонгүй. Байгаа:",
                  ", ".join(s.name for s in db.query(ParkingSite).all()))
            sys.exit(1)
        print(f"=== {site.name} · сүүлийн {args.hours:g} цаг ===")

        # 1. Камерын лог
        cam = site_camera_events(db, site.id, hours=args.hours)
        entry_ips = [c["ip"] for c in cam["cameras"] if c["lane_dir"] == "entry" and c["ip"]]
        cam_ip = entry_ips[0] if entry_ips else None
        for c in cam["cameras"]:
            print(f"  {c['name']:22} {c['lane_dir']:6} "
                  + (f"АЛДАА: {c['error']}" if c["error"] else f"{c['events']} event"))
        entries = [e for e in cam["events"] if e["lane_dir"] == "entry" and e["plate"]]
        if not entries:
            print("\nОрох камерын event алга — камер холбогдож чадаагүй байж магадгүй.")
            return

        # 2. DB-тэй тулгах. Тухайн АГШИНД идэвхтэй байсан session-ийг ч тооцно
        #    (машин өмнө нь орсон, дахин уншигдсан бол шинэ session үүсэхгүй — алдагдал БИШ)
        window_start = min(e["time"] for e in entries) - timedelta(hours=24)
        sess = (db.query(ParkingSession)
                .filter(ParkingSession.site_id == site.id,
                        ParkingSession.entry_time >= window_start).all())
        by_plate = defaultdict(list)
        for s in sess:
            by_plate[s.plate_number].append(s)

        def matched(ev) -> bool:
            for s in by_plate.get(ev["plate"], []):
                # Тухайн уншилтын ±30 минутад эхэлсэн бүртгэл
                if abs((s.entry_time - ev["time"]).total_seconds()) <= 1800:
                    return True
                # эсвэл тэр агшинд идэвхтэй байсан бүртгэл (дахин уншилт)
                end = s.exit_time or datetime.utcnow()
                if s.entry_time <= ev["time"] <= end:
                    return True
            return False

        # burst давхар уншилтыг нэгтгэнэ
        entries.sort(key=lambda e: (e["plate"], e["time"]))
        uniq = []
        for e in entries:
            if uniq and uniq[-1]["plate"] == e["plate"] \
                    and (e["time"] - uniq[-1]["time"]).total_seconds() < 600:
                continue
            uniq.append(e)
        lost = [e for e in uniq if not matched(e)]
        if args.plate:
            lost = [e for e in lost if e["plate"] == args.plate.upper()]
        print(f"\nОрох уншилт {len(uniq)} (давхардал хассан) · бүртгэгдээгүй {len(lost)} "
              f"({len(lost) / max(1, len(uniq)) * 100:.0f}%)")
        if not lost:
            print("✅ Бүх уншилт бүртгэгдсэн байна.")
            return

        # 3. Журналыг НЭГ удаа татаж индекслэнэ
        lo = min(e["time"] for e in lost) + tz - timedelta(minutes=5)
        hi = max(e["time"] for e in lost) + tz + timedelta(minutes=5)
        jrows = journal(lo, hi)
        print(f"Журналаас {len(jrows):,} мөр уншлаа ({lo:%m-%d %H:%M} → {hi:%m-%d %H:%M} локал)")

        # 4. Event бүрийг ангилах
        buckets = defaultdict(list)
        for ev in lost:
            t_local = ev["time"] + tz
            near = [ln for ts, ln in jrows
                    if abs((ts - t_local).total_seconds()) <= args.context]
            buckets[classify(near, ev["plate"], cam_ip)].append((ev, t_local, near))

        print(f"\n── Шалтгаанаар ({len(lost)} машин) ──")
        for reason in sorted(buckets, key=lambda r: -len(buckets[r])):
            items = buckets[reason]
            pct = len(items) / len(lost) * 100
            print(f"  {reason:34} {len(items):>4} ({pct:4.0f}%)")

        print(f"\n── Жишээ (тус бүр {args.show}) ──")
        for reason in sorted(buckets, key=lambda r: -len(buckets[r])):
            print(f"\n▸ {reason}")
            for ev, t_local, near in buckets[reason][:args.show]:
                print(f"  {ev['plate']:10} {t_local:%m-%d %H:%M:%S} (локал) · {ev['camera']}")
                for ln in near[:4 if not args.plate else 40]:
                    print(f"      {ln[:150]}")
                if not near:
                    print("      (журналд энэ агшинд НЭГ Ч мөр алга — сервер зогссон байсан)")

        # 5. Үйлчилгээний тасалдлын хураангуй
        print("\n── Үйлчилгээний тасалдал (нийт мужид) ──")
        restarts = [ts for ts, ln in jrows if RESTART.search(ln)]
        drops = [ts for ts, ln in jrows if "холболт тасарлаа" in ln]
        conns = [ts for ts, ln in jrows if "ХОЛБОГДЛОО" in ln]
        print(f"  Сервис дахин эхэлсэн : {len(restarts)}")
        print(f"  Камер тасарсан       : {len(drops)}")
        print(f"  Камер дахин холбогдсон: {len(conns)}")
        if drops:
            print("  Тасарсан цагууд (эхний 10):")
            for ts in drops[:10]:
                print(f"    {ts:%m-%d %H:%M:%S}")
        print("\nЗөвлөмж: «сервер унтарсан» олон бол watchdog/restart-ийн шалтгааныг,")
        print("«холболт тасарсан» олон бол камерын сүлжээ/сешн булаалтыг шалгана.")
        print("Алдагдсан машинуудыг Шалгах → Аудит горим → «Эдгээрийг нөхөж бүртгэх».")
    finally:
        db.close()


if __name__ == "__main__":
    main()
