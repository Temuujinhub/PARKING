#!/usr/bin/env python3
"""Зураг яагаад хадгалагдахгүй байгааг эх сурвалж бүрээр нь шалгах.

Зураг дараах ДАРААЛЛААР авагддаг (эхнийх нь олдвол дараагийнхыг оролдохгүй):
  1. ITSAPI payload доторх base64 зураг   — камер өөрөө event-д хийж илгээвэл
  2. Event стримийн JPEG кадр            — eventManager.cgi attach-аас таслана
  3. WS snap_puller                       — snapManager.attachFileProc суваг
  4. snapshot.cgi (эцсийн арга)           — «одоогийн кадр», камерт Login үүсгэнэ

Хэрэгсэл нь: (а) DB-ээс камер тус бүрийн зургийн хамрах хувь, (б) журналаас аль
эх сурвалж хэдэн удаа ажилласан, (в) snapshot.cgi-ийн алдааны шалтгаан,
(г) камерт очиж буй ХҮСЭЛТИЙН тоог тооцоолж харуулна.

    sudo /root/PARKING/backend/venv/bin/python /root/PARKING/tools/snapshot_diag.py
    sudo ... snapshot_diag.py --hours 6 --site "Эрэл"
"""
import argparse
import os
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timedelta

os.chdir("/root/PARKING/backend")
sys.path.insert(0, "/root/PARKING/backend")

from app.database import SessionLocal  # noqa: E402
from app.models import Device, ParkingSession, ParkingSite  # noqa: E402

SRC = [
    ("payload (камер өөрөө илгээв)", re.compile(r"OK \(payload")),
    ("event стрим (шилдэг)", re.compile(r"OK \(event-stream")),
    ("WS snap_puller", re.compile(r"WS event зураг ирлээ")),
    ("snapshot.cgi (эцсийн арга)", re.compile(r"OK \(snapshot\.cgi")),
]
FAIL = re.compile(r"зураг ОЛДСОНГҮЙ")
CGI_FAIL = re.compile(r"snapshot\.cgi бүх хувилбар бүтэлгүйтэв")
CGI_QUIET = re.compile(r"snapshot\.cgi .* ЗОГСООЛОО")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--hours", type=float, default=12)
    ap.add_argument("--site", default=None, help="Зогсоолын нэрийн хэсэг")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        sites = {s.id: s.name for s in db.query(ParkingSite).all()}
        wanted = set(sites)
        if args.site:
            wanted = {sid for sid, n in sites.items()
                      if args.site.strip().lower() in (n or "").lower()}
        since = datetime.utcnow() - timedelta(hours=args.hours)
        rows = (db.query(ParkingSession)
                .filter(ParkingSession.site_id.in_(wanted),
                        ParkingSession.entry_time >= since).all())
        print(f"=== Зургийн хамрах хүрээ · сүүлийн {args.hours:g} цаг ===")
        if not rows:
            print("Бүртгэл алга.")
            return

        by_site = defaultdict(lambda: [0, 0, 0, 0])  # нийт, орох зурагтай, гарсан, гарах зурагтай
        for s in rows:
            b = by_site[sites.get(s.site_id, "?")]
            b[0] += 1
            if s.entry_snapshot:
                b[1] += 1
            if s.exit_time:
                b[2] += 1
                if s.exit_snapshot:
                    b[3] += 1
        print(f"\n{'Зогсоол':22} {'Орсон':>6} {'Зурагтай':>9} {'%':>5}   "
              f"{'Гарсан':>6} {'Зурагтай':>9} {'%':>5}")
        print("─" * 74)
        for name in sorted(by_site):
            n, ei, x, xi = by_site[name]
            print(f"{name:22} {n:>6} {ei:>9} {ei / n * 100 if n else 0:>4.0f}%   "
                  f"{x:>6} {xi:>9} {xi / x * 100 if x else 0:>4.0f}%")

        # Журналаас эх сурвалжийн задаргаа
        print(f"\n── Зураг АЛЬ СУВГААР ирсэн бэ (журнал, {args.hours:g}ц) ──")
        try:
            out = subprocess.run(
                ["journalctl", "-u", "parking-backend", "--since", f"-{args.hours}h",
                 "--no-pager"], capture_output=True, text=True, timeout=120).stdout
        except Exception as e:  # noqa: BLE001
            print(f"  journalctl уншиж чадсангүй: {e}")
            out = ""
        counts = {label: 0 for label, _rx in SRC}
        fails = cgi_fails = quiet = 0
        per_cam_fail = defaultdict(int)
        for ln in out.splitlines():
            for label, rx in SRC:
                if rx.search(ln):
                    counts[label] += 1
            if FAIL.search(ln):
                fails += 1
            if CGI_FAIL.search(ln):
                cgi_fails += 1
                m = re.search(r"(\d+\.\d+\.\d+\.\d+)", ln)
                if m:
                    per_cam_fail[m.group(1)] += 1
            if CGI_QUIET.search(ln):
                quiet += 1
        total_ok = sum(counts.values())
        for label, _rx in SRC:
            c = counts[label]
            pct = c / total_ok * 100 if total_ok else 0
            print(f"  {label:32} {c:>5} ({pct:4.0f}%)")
        print(f"  {'ЗУРАГГҮЙ үлдсэн':32} {fails:>5}")
        if cgi_fails:
            print(f"\n  snapshot.cgi бүтэлгүйтсэн: {cgi_fails} удаа"
                  + (f" · {quiet} удаа түр зогсоосон" if quiet else ""))
            print("  Камераар:")
            for ip, c in sorted(per_cam_fail.items(), key=lambda kv: -kv[1])[:10]:
                dev = db.query(Device).filter(Device.ip_address == ip).first()
                # Бүтэлгүйтэл бүр 9 хүсэлт = 9 Login бичлэг камерын логт
                print(f"    {ip:16} {c:>5} удаа ≈ {c * 9:>6,} нэмэлт хүсэлт"
                      + (f"  [{dev.name}]" if dev else ""))

        print("\n── Дүгнэлт ──")
        if counts["event стрим (шилдэг)"] == 0 and counts["WS snap_puller"] == 0:
            print("  ⚠ Event стрим ба WS хоёулаа зураг ӨГӨХГҮЙ байна — бүх ачаалал")
            print("    snapshot.cgi дээр буусан бөгөөд тэр нь энэ firmware дээр")
            print("    ажиллахгүй тул зураг ХАДГАЛАГДАХГҮЙ.")
            print("    Шийдэл: камерын Web UI → Setup → Storage/Snapshot дээр")
            print("    «Picture Upload»-ыг event-д асаах (payload-аар зураг ирнэ),")
            print("    эсвэл snap_puller-ийн WS сувгийг сэргээх.")
        elif fails > total_ok:
            print("  ⚠ Ихэнх event зураггүй үлдэж байна — дээрх сувгуудыг шалгана уу.")
        else:
            print("  Зураг ихэвчлэн авагдаж байна.")
        if cgi_fails > 50:
            print(f"  ⚠ snapshot.cgi {cgi_fails} удаа дэмий оролдсон нь камерын логийг")
            print("    Login бичлэгээр дүүргэж, event subscription-д саад болдог.")
            print("    Шийдэл: .env-д PARKING_SNAPSHOT_CGI_FALLBACK=false (бүрмөсөн унтраах)")
            print("    эсвэл автомат зогсоолт ажиллахыг хүлээх (5 алдааны дараа 30 мин).")
    finally:
        db.close()


if __name__ == "__main__":
    main()
