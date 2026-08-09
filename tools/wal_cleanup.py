#!/usr/bin/env python3
"""PostgreSQL-ийн WAL архивыг шалгаж, хэрэггүй болсныг нь аюулгүй цэвэрлэх.

2026-08-09: /var/lib/parking/wal-archive = 53.7GB (диск 79% дүүрсэн). DB нь
ердөө 143MB — WAL нь өдөрт ~2.8GB үүсдэг (сешн бүрийн UPDATE, checkpoint
бүрийн full-page-write). deploy/setup_pitr.sh-ийн cron нь ДОЛОО ХОНОГТ нэг
ажиллаж, 21 хоногоос хуучныг л устгадаг тул архив 19 хоногт 53GB болтол
хуримтлагдсан ч нэг ч файл устаагүй.

PITR-ийн ЖИНХЭНЭ дүрэм: хамгийн сүүлийн basebackup-аас ӨМНӨХ WAL хэрэггүй
(тэр basebackup өөрөө тэр цэг хүртэлх бүх өөрчлөлтийг агуулна). Тиймээс
`pg_archivecleanup` ашиглан аюулгүй устгана — сэргээх чадвар алдагдахгүй.

    sudo /root/PARKING/backend/venv/bin/python /root/PARKING/tools/wal_cleanup.py
    sudo ... wal_cleanup.py --apply            # pg_archivecleanup ажиллуулна
    sudo ... wal_cleanup.py --max-gb 5 --apply # нэмээд хэмжээний таг тавина
"""
import argparse
import os
import shutil
import subprocess
import sys
from datetime import datetime

ARCHIVE_DIR = "/var/lib/parking/wal-archive"
BASE_DIR = "/var/lib/parking/basebackup"
GB = 1024 ** 3
MB = 1024 ** 2


def human(n: float) -> str:
    return f"{n / GB:.1f}GB" if n >= GB else f"{n / MB:.0f}MB"


def scan(path: str):
    """[(mtime, size, нэр)] + нийт байт"""
    out, total = [], 0
    try:
        with os.scandir(path) as it:
            for e in it:
                if not e.is_file():
                    continue
                st = e.stat()
                out.append((st.st_mtime, st.st_size, e.name))
                total += st.st_size
    except OSError as err:
        print(f"  {path}: {err}")
    return sorted(out), total


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--max-gb", type=float, default=0,
                    help="Цэвэрлэсний дараа ч хэтэрвэл хамгийн хуучнаас устгах таг (0=хэрэглэхгүй)")
    ap.add_argument("--apply", action="store_true", help="Бодитоор устгах")
    args = ap.parse_args()

    du = shutil.disk_usage("/")
    print("=== WAL архивын шалгалт ===")
    print(f"Диск: эзэлсэн {human(du.used)} / {human(du.total)} · сул {human(du.free)}\n")

    # 1. Basebackup-ууд — эдгээргүйгээр WAL архив утгагүй
    print("── Basebackup ──")
    bases = []
    if os.path.isdir(BASE_DIR):
        for name in sorted(os.listdir(BASE_DIR)):
            p = os.path.join(BASE_DIR, name)
            if not os.path.isdir(p):
                continue
            sz = sum(f.stat().st_size for f in os.scandir(p) if f.is_file())
            bases.append((os.path.getmtime(p), name, sz))
    if not bases:
        print("  ⚠ Нэг ч basebackup алга! WAL архив ДАНГААРАА сэргээхэд хангалтгүй.")
        print("    Эхлээд: sudo -u postgres pg_basebackup -D "
              f"{BASE_DIR}/$(date +%Y%m%d-%H%M%S) -Ft -z -Xs -P")
    else:
        for mt, name, sz in sorted(bases):
            print(f"  {datetime.fromtimestamp(mt):%Y-%m-%d %H:%M}  {name:24} {human(sz)}")
        newest_mt = max(b[0] for b in bases)
        age_days = (datetime.now().timestamp() - newest_mt) / 86400
        print(f"  Хамгийн сүүлийнх: {age_days:.1f} хоногийн өмнө")

    # 2. WAL архив
    print(f"\n── WAL архив ({ARCHIVE_DIR}) ──")
    files, total = scan(ARCHIVE_DIR)
    if not files:
        print("  Хоосон эсвэл олдсонгүй.")
        return
    oldest, newest = files[0][0], files[-1][0]
    days = max(1e-9, (newest - oldest) / 86400)
    print(f"  {human(total)} · {len(files):,} файл")
    print(f"  {datetime.fromtimestamp(oldest):%Y-%m-%d} → "
          f"{datetime.fromtimestamp(newest):%Y-%m-%d} ({days:.1f} хоног)")
    print(f"  Өсөлт: ~{human(total / days)}/хоног → сард ~{human(total / days * 30)}")

    # 3. pg_archivecleanup — хамгийн сүүлийн basebackup-аас өмнөхийг хасна
    labels = [n for _m, _s, n in files if n.endswith(".backup")]
    print(f"\n── Аюулгүй цэвэрлэгээ (pg_archivecleanup) ──")
    if not labels:
        print("  .backup шошго олдсонгүй — pg_archivecleanup ажиллуулах боломжгүй.")
        print("  (basebackup хийгдэхэд шошго үүсдэг. Хэмжээний тагаар цэвэрлэнэ үү.)")
    else:
        cutoff = sorted(labels)[-1]        # хамгийн сүүлийн basebackup-ийн шошго
        base_name = cutoff.split(".")[0]
        older = [(m, s, n) for m, s, n in files if n < base_name]
        older_sz = sum(s for _m, s, _n in older)
        print(f"  Хамгийн сүүлийн шошго: {cutoff}")
        print(f"  Түүнээс ӨМНӨХ (хэрэггүй): {len(older):,} файл · {human(older_sz)}")
        if args.apply and older:
            exe = shutil.which("pg_archivecleanup") or \
                "/usr/lib/postgresql/18/bin/pg_archivecleanup"
            try:
                r = subprocess.run([exe, ARCHIVE_DIR, base_name],
                                   capture_output=True, text=True, timeout=600)
                if r.returncode == 0:
                    print(f"  ✅ pg_archivecleanup амжилттай ({human(older_sz)} чөлөөлөв)")
                else:
                    print(f"  ⚠ pg_archivecleanup алдаа: {r.stderr.strip()[:200]}")
            except Exception as e:  # noqa: BLE001
                print(f"  ⚠ ажиллуулж чадсангүй ({e}) — хэмжээний тагаар цэвэрлэнэ")

    # 4. Хэмжээний таг — pg_archivecleanup хүрэлцэхгүй үеийн сүүл
    files, total = scan(ARCHIVE_DIR)
    if args.max_gb and total > args.max_gb * GB:
        over = total - int(args.max_gb * GB)
        to_del, freed = [], 0
        # Сүүлийн basebackup-аас ХОЙШХИ файлыг ХЭЗЭЭ Ч устгахгүй (сэргээхэд хэрэгтэй)
        keep_after = sorted([n for _m, _s, n in files if n.endswith(".backup")])[-1:] or [""]
        guard = keep_after[0].split(".")[0]
        for mt, sz, name in files:
            if freed >= over or (guard and name >= guard):
                break
            to_del.append(name)
            freed += sz
        print(f"\n── Хэмжээний таг ({args.max_gb}GB) ──")
        print(f"  Одоо {human(total)} → {len(to_del):,} файл ({human(freed)}) устгана")
        if args.apply:
            n = 0
            for name in to_del:
                try:
                    os.remove(os.path.join(ARCHIVE_DIR, name))
                    n += 1
                except OSError:
                    pass
            print(f"  ✅ {n:,} файл устгав")
        else:
            print("  (--apply өгвөл устгана)")

    _f, total_after = scan(ARCHIVE_DIR)
    du2 = shutil.disk_usage("/")
    print(f"\nАрхив: {human(total_after)} · Дискний сул зай: {human(du2.free)}")
    if not args.apply:
        print("\nЭнэ бол DRY-RUN — устгахын тулд --apply нэмнэ.")
    print("\nДараагийн алхам: cron-ийг ӨДӨР ТУТАМ болгож, хэмжээний таг нэмэх —")
    print("  sudo bash /root/PARKING/deploy/setup_pitr.sh   (шинэчилсэн хувилбар)")


if __name__ == "__main__":
    main()
