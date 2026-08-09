#!/usr/bin/env python3
"""Дискний зайг юу идэж байгааг олж, цэвэрлэх зөвлөмж гаргах.

2026-08-09: 98GB дискний 83% (77GB) дүүрсэн. Гол хэрэглэгч нь ихэвчлэн
камерын snapshot зургууд (event бүрд 1–2 зураг × өдөрт мянга) — хугацааны
retention 120 хоног байсан ч тэр болтол хуримтлагдана.

    sudo /root/PARKING/backend/venv/bin/python /root/PARKING/tools/disk_audit.py
    # хамгийн хуучин зургуудыг устгаж хязгаарт оруулах:
    sudo ... disk_audit.py --enforce-limit --apply
"""
import argparse
import os
import shutil
import subprocess
import sys
from collections import defaultdict
from datetime import datetime

os.chdir("/root/PARKING/backend")
sys.path.insert(0, "/root/PARKING/backend")

from app.config import settings  # noqa: E402

GB = 1024 ** 3
MB = 1024 ** 2


def human(n: float) -> str:
    return f"{n / GB:.1f}GB" if n >= GB else f"{n / MB:.0f}MB"


def dir_stats(path: str):
    """(нийт байт, файлын тоо, өдрөөр задаргаа [(YYYY-MM-DD, байт, тоо)])"""
    total, count = 0, 0
    by_day = defaultdict(lambda: [0, 0])
    files = []
    for root_, _d, fns in os.walk(path):
        for fn in fns:
            p = os.path.join(root_, fn)
            try:
                st = os.stat(p)
            except OSError:
                continue
            total += st.st_size
            count += 1
            day = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d")
            by_day[day][0] += st.st_size
            by_day[day][1] += 1
            files.append((st.st_mtime, st.st_size, p))
    return total, count, by_day, files


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--enforce-limit", action="store_true",
                    help="Зургийг retention_snapshot_max_gb хязгаарт оруулах")
    ap.add_argument("--max-gb", type=float, default=None,
                    help="Хязгаарыг түр дарж заах (default .env-ийнх)")
    ap.add_argument("--apply", action="store_true", help="Бодитоор устгах")
    args = ap.parse_args()

    print("=== Дискний шалгалт ===")
    du = shutil.disk_usage("/")
    print(f"/  нийт {human(du.total)} · эзэлсэн {human(du.used)} "
          f"({du.used / du.total * 100:.0f}%) · сул {human(du.free)}\n")

    # 1. Хамгийн том хавтаснууд (du — хурдан, C-ээр бичигдсэн)
    print("── Хамгийн их зай эзэлсэн хавтаснууд ──")
    try:
        out = subprocess.run(
            ["du", "-x", "-h", "--max-depth=2", "--threshold=500M", "/"],
            capture_output=True, text=True, timeout=180).stdout
        rows = [ln.split("\t") for ln in out.strip().splitlines() if "\t" in ln]
        for size, path in sorted(rows, key=lambda r: -_to_bytes(r[0]))[:15]:
            print(f"  {size:>8}  {path}")
    except Exception as e:  # noqa: BLE001
        print(f"  du ажиллуулж чадсангүй: {e}")

    # 2. Snapshot хавтас — гол хэрэглэгч
    snap = settings.snapshot_dir
    print(f"\n── Камерын зураг ({snap}) ──")
    if not snap or not os.path.isdir(snap):
        print("  Хавтас олдсонгүй.")
        return
    total, count, by_day, files = dir_stats(snap)
    print(f"  Нийт {human(total)} · {count:,} файл · "
          f"дундаж {human(total / count) if count else '—'}/файл")
    days = sorted(by_day)
    if days:
        print(f"  Хугацаа: {days[0]} → {days[-1]} ({len(days)} өдөр)")
        per_day = total / len(days)
        print(f"  Өдрийн дундаж: {human(per_day)} → сард ~{human(per_day * 30)}")
        print("\n  Сүүлийн 7 өдөр:")
        for d in days[-7:]:
            size, cnt = by_day[d]
            print(f"    {d}  {human(size):>8}  {cnt:,} файл")

    # 3. Тохиргоо ба зөвлөмж
    limit = args.max_gb if args.max_gb is not None else settings.retention_snapshot_max_gb
    print("\n── Тохиргоо ──")
    print(f"  Хугацааны хязгаар : {settings.retention_snapshot_days} хоног")
    print(f"  Хэмжээний хязгаар : {limit}GB "
          f"({'ХЭТЭРСЭН' if limit and total > limit * GB else 'хэвийн'})")
    print(f"  Дискний доод сул  : {settings.disk_free_min_percent}%")

    if not (limit and total > limit * GB):
        print("\n  Хязгаарт багтаж байна — цэвэрлэх шаардлагагүй.")
        return

    over = total - int(limit * GB)
    to_del, freed = [], 0
    for mtime, size, p in sorted(files):
        if freed >= over:
            break
        to_del.append(p)
        freed += size
    oldest = datetime.fromtimestamp(min(f[0] for f in files)) if files else None
    cut = datetime.fromtimestamp(os.stat(to_del[-1]).st_mtime) if to_del else None
    print(f"\n  Хязгаарт оруулахын тулд {len(to_del):,} файл ({human(freed)}) устгана")
    print(f"  Хамгийн хуучин {oldest:%Y-%m-%d} → {cut:%Y-%m-%d} хүртэлх зургууд")

    if not args.enforce_limit:
        print("\n  Устгахын тулд: --enforce-limit --apply")
        return
    if not args.apply:
        print("\n  Энэ бол DRY-RUN — устгахын тулд --apply нэмнэ.")
        return
    removed, err = 0, 0
    for p in to_del:
        try:
            os.remove(p)
            removed += 1
        except OSError:
            err += 1
    print(f"\n✅ {removed:,} файл устгав ({human(freed)} чөлөөлөв)"
          + (f", {err} алдаа" if err else ""))
    du2 = shutil.disk_usage("/")
    print(f"   Сул зай: {human(du.free)} → {human(du2.free)}")


def _to_bytes(s: str) -> float:
    """du -h гаралтыг байт болгоно (эрэмбэлэхэд)."""
    s = s.strip()
    mult = {"K": 1024, "M": MB, "G": GB, "T": 1024 ** 4}
    try:
        return float(s[:-1]) * mult.get(s[-1].upper(), 1) if s[-1].isalpha() else float(s)
    except ValueError:
        return 0.0


if __name__ == "__main__":
    main()
