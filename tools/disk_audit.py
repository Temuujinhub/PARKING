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

    # 1. Хамгийн том хавтас руу АВТОМАТААР гүнзгийрч орно. Ганц түвшин харуулбал
    #    «/var/lib 61GB» гэж зогсдог тул жинхэнэ буруутныг олохгүй.
    print("── Хамгийн их зай эзэлсэн хавтаснууд (гүнзгийрч) ──")
    cur = "/"
    seen = set()
    for _level in range(6):
        rows = _du(cur)
        if not rows:
            break
        # cur өөрөө болон түүнээс жижиг зүйлсийг хасаж, дэд хавтсуудыг эрэмбэлнэ
        subs = [(sz, p) for sz, p in rows if p != cur and p not in seen]
        if not subs:
            break
        subs.sort(key=lambda r: -r[0])
        for sz, p in subs[:6]:
            print(f"  {human(sz):>8}  {p}")
        top_size, top_path = subs[0]
        seen.add(top_path)
        # Хамгийн том дэд хавтас нийт зайн 25%-иас их бол дотор нь орно
        if top_size < du.used * 0.25 or not os.path.isdir(top_path):
            break
        print(f"  ↓ {top_path} дотор:")
        cur = top_path

    # 1.5 Мэдэгдэж буй том хэрэглэгчид — «бидэнд хамаагүй» зүйлийг ялгана
    print("\n── Танигдсан хэрэглэгчид ──")
    known = [
        ("/var/lib/postgresql", "PostgreSQL (WAL хуримтлагдвал асар том болдог)"),
        ("/var/lib/docker", "Docker образ/контейнерийн лог"),
        ("/var/lib/parking", "Манай систем (зураг г.м)"),
        ("/var/log", "Системийн лог"),
        ("/var/log/journal", "systemd journal (vacuum хийж болно)"),
        ("/var/cache", "Пакетын кэш (apt clean)"),
        ("/root", "root хэрэглэгчийн файлууд"),
        ("/tmp", "Түр файлууд"),
        ("/var/tmp", "Түр файлууд (дахин ачаалахад ч устдаггүй)"),
        ("/swapfile", "Swap файл"),
    ]
    for path, desc in known:
        if not os.path.exists(path):
            continue
        try:
            if os.path.isfile(path):
                sz = os.path.getsize(path)
            else:
                r = subprocess.run(["du", "-x", "-s", "-b", path],
                                   capture_output=True, text=True, timeout=300)
                sz = int(r.stdout.split("\t")[0]) if r.stdout.strip() else 0
        except Exception:  # noqa: BLE001
            continue
        if sz > 100 * MB:
            print(f"  {human(sz):>8}  {path:26} {desc}")

    # 1.6 PostgreSQL WAL — DB жижиг байхад дата хавтас том бол ЭНЭ шалтгаан
    for pg in ("/var/lib/postgresql",):
        if not os.path.isdir(pg):
            continue
        for root_, dirs, _f in os.walk(pg):
            if os.path.basename(root_) in ("pg_wal", "pg_xlog"):
                try:
                    r = subprocess.run(["du", "-x", "-s", "-b", root_],
                                       capture_output=True, text=True, timeout=120)
                    sz = int(r.stdout.split("\t")[0])
                    n = len(os.listdir(root_))
                except Exception:  # noqa: BLE001
                    continue
                print(f"\n  ⚠ WAL: {human(sz)} · {n} файл — {root_}")
                if sz > 5 * GB:
                    print("    WAL хэт том. Шалтгаан ихэвчлэн: гацсан replication slot,")
                    print("    эсвэл archive_command амжилтгүй болж WAL хуримтлагдсан.")
                    print("    Шалгах: sudo -u postgres psql -c \"SELECT * FROM pg_replication_slots;\"")
                    print("            sudo -u postgres psql -c \"SHOW archive_mode;\"")
                dirs[:] = []

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


def _du(path: str):
    """du -x --max-depth=1 -b → [(байт, зам)]. Алдаа гарвал хоосон."""
    try:
        r = subprocess.run(["du", "-x", "-b", "--max-depth=1", path],
                           capture_output=True, text=True, timeout=600)
        out = []
        for ln in r.stdout.strip().splitlines():
            if "\t" not in ln:
                continue
            size, p = ln.split("\t", 1)
            try:
                out.append((int(size), p))
            except ValueError:
                pass
        return out
    except Exception:  # noqa: BLE001
        return []


if __name__ == "__main__":
    main()
