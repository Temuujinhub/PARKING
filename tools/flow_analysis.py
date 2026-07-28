#!/usr/bin/env python
"""Урсгалын дүн шинжилгээ — камерт ОГТ ХАНДАХГҮЙ (зөвхөн DB + journald).

    sudo /root/PARKING/backend/venv/bin/python /root/PARKING/tools/flow_analysis.py            # сүүлийн 6 цаг
    sudo /root/PARKING/backend/venv/bin/python /root/PARKING/tools/flow_analysis.py --hours 12

Юу тооцдог вэ (зогсоол/камер тус бүрээр):
  1. Event-ийн урсгал: нийт/зөвшөөрсөн/татгалзсан (шалтгаанаар), хамгийн урт
     чимээгүй завсар (камер «унтарсан» цонхыг илрүүлнэ)
  2. Event → хаалтны команд хүртэлх хоцролт (шийдвэрийн хурд): p50/p95/max
  3. Хаалтны командын гүйцэтгэл: амжилтын хувь, RPC хугацаа p50/p95/max,
     бүтэлгүйтлүүд цагийн жагсаалтаар (өдрийн аль цагт бөөгнөрдгийг харуулна)
  4. Journald: боловсруулалтын хугацаа («lpr … → action: XXмс») ба LED
     («[screen] … OK/чадсангүй») статистик
Камер руу нэг ч хүсэлт илгээхгүй тул хэдэн ч удаа ажиллуулахад аюулгүй.
"""
import argparse
import os
import statistics
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta

BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
sys.path.insert(0, BACKEND)
os.chdir(BACKEND)

from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import BarrierCommand, Device, LprEvent, ParkingSite  # noqa: E402


def _pct(vals, p):
    if not vals:
        return 0
    vals = sorted(vals)
    k = max(0, min(len(vals) - 1, int(round(p / 100 * (len(vals) - 1)))))
    return vals[k]


def _fmt_ms(v):
    return f"{v / 1000:.1f}с" if v >= 1000 else f"{int(v)}мс"


def _local(dt):
    return (dt + timedelta(hours=settings.tz_offset_hours)).strftime("%H:%M:%S")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=6.0)
    args = ap.parse_args()
    since = datetime.utcnow() - timedelta(hours=args.hours)

    db = SessionLocal()
    try:
        sites = {s.id: s for s in db.query(ParkingSite).filter(ParkingSite.is_active.is_(True))}
        devices = {d.id: d for d in db.query(Device).filter(Device.status == "active")}
        events = (db.query(LprEvent).filter(LprEvent.created_at >= since)
                  .order_by(LprEvent.created_at).all())
        cmds = (db.query(BarrierCommand).filter(BarrierCommand.created_at >= since)
                .order_by(BarrierCommand.created_at).all())
    finally:
        db.close()

    print(f"═══ Урсгалын дүн шинжилгээ — сүүлийн {args.hours:g} цаг "
          f"(UTC {since:%H:%M}-аас) ═══")

    for sid, site in sites.items():
        s_events = [e for e in events if e.site_id == sid]
        s_cmds = [c for c in cmds if devices.get(c.device_id) and devices[c.device_id].site_id == sid]
        if not s_events and not s_cmds:
            continue
        print(f"\n──────── {site.name} ({site.site_code}) ────────")

        # 1. Event урсгал камер тус бүрээр
        by_cam = defaultdict(list)
        for e in s_events:
            by_cam[e.device_id].append(e)
        for did, evs in by_cam.items():
            d = devices.get(did)
            name = f"{d.name} ({d.ip_address})" if d else did[:8]
            ok = [e for e in evs if e.accepted]
            rej = Counter((e.reject_reason or "?") for e in evs if not e.accepted)
            # хамгийн урт чимээгүй завсар
            gaps = [(b.created_at - a.created_at).total_seconds()
                    for a, b in zip(evs, evs[1:])]
            max_gap = max(gaps) if gaps else 0
            gap_at = ""
            if gaps and max_gap > 0:
                i = gaps.index(max_gap)
                gap_at = f" ({_local(evs[i].created_at)}→{_local(evs[i + 1].created_at)})"
            print(f"  📷 {name}: {len(evs)} event, зөвшөөрсөн {len(ok)}, "
                  f"татгалзсан {sum(rej.values())}"
                  + (f" {dict(rej)}" if rej else ""))
            if max_gap > 900:
                print(f"     ⚠ хамгийн урт чимээгүй завсар: {max_gap / 60:.0f} мин{gap_at}")

        # 2. Event → команд хоцролт (шийдвэрийн хурд)
        lat = []
        open_cmds = [c for c in s_cmds if c.command in ("open", "force_open")
                     and c.command_source != "manual"]
        used = set()
        for e in s_events:
            if not e.accepted:
                continue
            for c in open_cmds:
                if c.id in used or c.created_at < e.created_at:
                    continue
                dt = (c.created_at - e.created_at).total_seconds()
                if dt > 10:
                    break
                used.add(c.id)
                lat.append(dt * 1000)
                break
        if lat:
            print(f"  ⏱ Event→команд хоцролт ({len(lat)} хос): "
                  f"p50 {_fmt_ms(_pct(lat, 50))}, p95 {_fmt_ms(_pct(lat, 95))}, "
                  f"max {_fmt_ms(max(lat))}")

        # 3. Командын гүйцэтгэл төхөөрөмж тус бүрээр
        by_dev = defaultdict(list)
        for c in s_cmds:
            by_dev[c.device_id].append(c)
        for did, cl in by_dev.items():
            d = devices.get(did)
            name = d.name if d else did[:8]
            okc = [c for c in cl if c.status == "SUCCESS"]
            fail = [c for c in cl if c.status == "FAILED"]
            durs = [c.duration_ms for c in okc if c.duration_ms]
            srcs = Counter(c.command_source for c in cl)
            line = (f"  🚧 {name}: {len(cl)} команд {dict(srcs)}, "
                    f"амжилт {len(okc)}/{len(cl)}")
            if durs:
                line += (f", RPC p50 {_fmt_ms(_pct(durs, 50))} "
                         f"p95 {_fmt_ms(_pct(durs, 95))} max {_fmt_ms(max(durs))}")
            print(line)
            for c in fail:
                print(f"     ✗ {_local(c.created_at)} [{c.command_source}] "
                      f"{_fmt_ms(c.duration_ms or 0)} — {(c.response_text or '')[:60]}")

    # 4. Journald: боловсруулалтын хугацаа + LED
    print("\n──────── Journald (боловсруулалт + LED) ────────")
    try:
        out = subprocess.run(
            ["journalctl", "-u", "parking-backend", "--since", f"-{int(args.hours * 3600)}s",
             "--no-pager", "-o", "cat"],
            capture_output=True, text=True, timeout=60).stdout.splitlines()
    except Exception as e:  # noqa: BLE001
        out = []
        print(f"  (journald уншиж чадсангүй: {e})")
    import re
    proc = defaultdict(list)   # (dir, action) -> [ms]
    screen_ok = Counter()
    screen_fail = Counter()
    stale = 0
    for ln in out:
        m = re.search(r"lpr (entry|exit) (\S+) → (\w+): (\d+)мс", ln)
        if m:
            proc[(m.group(1), m.group(3))].append(int(m.group(4)))
            if "хоцорсон" in ln:
                stale += 1
            continue
        m = re.search(r"\[screen\] ([\d.]+): OK", ln)
        if m:
            screen_ok[m.group(1)] += 1
            continue
        m = re.search(r"\[screen\] ([\d.]+): бичиж чадсангүй", ln)
        if m:
            screen_fail[m.group(1)] += 1
    if proc:
        print("  Боловсруулалт (event ирснээс шийдвэр гартал):")
        for (dirn, act), vals in sorted(proc.items()):
            print(f"    {dirn:<5} {act:<18} ×{len(vals):<3} "
                  f"p50 {_fmt_ms(_pct(vals, 50))} p95 {_fmt_ms(_pct(vals, 95))} "
                  f"max {_fmt_ms(max(vals))}")
        if stale:
            print(f"    ⚠ хоцорсон (хаалт нээгээгүй) event: {stale}")
    else:
        print("  («lpr … → action» мөр олдсонгүй — upd32-оос хойшхи лог хэрэгтэй)")
    if screen_ok or screen_fail:
        print("  LED дэлгэц:")
        for ip in sorted(set(screen_ok) | set(screen_fail)):
            print(f"    {ip}: OK ×{screen_ok[ip]}, алдаа ×{screen_fail[ip]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
