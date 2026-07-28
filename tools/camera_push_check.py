#!/usr/bin/env python
"""Камер event ИЛГЭЭХЭЭ БОЛЬСОН эсэхийг оношлох (камер→сервер чиглэл).

    sudo /root/PARKING/backend/venv/bin/python /root/PARKING/tools/camera_push_check.py --all
    ... camera_push_check.py 192.168.6.11
    ... camera_push_check.py 192.168.6.11 --reboot     # камерыг дахин ачаалах

Ялгаа нь юу вэ:
  camera_check.py  — СЕРВЕР→КАМЕР (хаалт нээх, зураг татах ажиллаж байна уу)
  camera_push_check.py — КАМЕР→СЕРВЕР (машин ирэхэд event илгээж байна уу)

Хоёр чиглэл ТУСДАА эвдэрдэг: камер бүрэн эрүүл, гараар хаалт нээгддэг атлаа
машин ирэхэд огт танихгүй байх нь ЭНЭ чиглэл тасарсны шинж (2026-07-28,
MONNIS гарах камер: сүүлд 06:38-д уншсан, дараа нь 8 цаг чимээгүй).

Шалгах зүйлс:
  1. DB дэх last_seen — камер сүүлд хэзээ мэдээлсэн бэ
  2. Сүүлийн LPR event — сүүлд хэзээ дугаар уншсан бэ
  3. Камерын PUSH тохиргоо — манай сервер рүү илгээхээр тохируулагдсан уу
  4. Камерын ANPR/дүрмийн тохиргоо идэвхтэй эсэх
"""
import asyncio
import os
import sys
from datetime import datetime

BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
sys.path.insert(0, BACKEND)
os.chdir(BACKEND)

import httpx  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import Device, LprEvent, ParkingSite  # noqa: E402
from app.services.barrier import DahuaRpc  # noqa: E402
from app.services.device_auth import camera_credentials  # noqa: E402

# Dahua ITS камерын push/платформын тохиргооны боломжит нэрс (загвар бүр өөр).
# Аль нь байгааг камераас нь асууна — таамаглахгүй.
CONFIG_NAMES = [
    "AlarmServer",          # ерөнхий Alarm Server (HTTP push)
    "ITSPlatform",          # ITS платформ (ANPR event илгээх хаяг)
    "PlatformServer",
    "TrafficSnapPlatform",
    "NetSDKPlatform",
    "VideoAnalyseRule",     # ANPR дүрэм идэвхтэй эсэх
]


def _age(ts: datetime | None) -> str:
    if not ts:
        return "ХЭЗЭЭ Ч БАЙХГҮЙ"
    sec = (datetime.utcnow() - ts).total_seconds()
    if sec < 90:
        return f"{int(sec)}с өмнө"
    if sec < 5400:
        return f"{int(sec/60)} мин өмнө"
    return f"{sec/3600:.1f} ЦАГИЙН ӨМНӨ"


async def probe(db, ip: str, reboot: bool = False) -> None:
    dev = db.query(Device).filter(Device.ip_address == ip).first()
    site = db.get(ParkingSite, dev.site_id) if dev else None
    name = f"{site.site_code + ' ' if site else ''}{dev.name if dev else ''}"
    print(f"\n═══ {ip}  {name} ═══")

    # ── 1-2. Сервер тал: сүүлд хэзээ мэдээлсэн бэ ──
    if dev:
        last_ev = (db.query(LprEvent).filter(LprEvent.device_id == dev.id)
                   .order_by(LprEvent.created_at.desc()).first())
        print(f"  сүүлд холбогдсон (last_seen): {dev.last_seen}  → {_age(dev.last_seen)}")
        print(f"  сүүлд дугаар уншсан:          "
              f"{last_ev.created_at if last_ev else '—'}  → {_age(last_ev.created_at if last_ev else None)}")
        # Аль сүүлийнхийг нь авна: heartbeat эсвэл жинхэнэ дугаар уншилт.
        # (зарим firmware heartbeat илгээдэггүй ч ANPR event илгээсээр байдаг)
        marks = [t for t in (dev.last_seen, last_ev.created_at if last_ev else None) if t]
        newest = max(marks) if marks else None
        stale = newest is None or (datetime.utcnow() - newest).total_seconds() > 1800
        if stale:
            print("  ✗ <<< ЭНЭ КАМЕР EVENT ИЛГЭЭХЭЭ БОЛЬСОН (30+ мин чимээгүй)")
        else:
            print("  ✓ камер идэвхтэй мэдээлж байна")
    else:
        print("  ! энэ IP-гээр төхөөрөмж бүртгэгдээгүй")

    creds = camera_credentials(dev)
    async with httpx.AsyncClient(timeout=10.0) as client:
        rpc = DahuaRpc(client, ip, *creds)
        try:
            await rpc.login()
        except Exception as e:  # noqa: BLE001
            print(f"  ✗ RPC2 нэвтэрч чадсангүй: {str(e)[:120]}")
            print("      → Камер унтарсан/сүлжээнээс салсан байж магадгүй.")
            return
        print("  ✓ камер амьд (RPC2 нэвтрэлт OK)")

        try:
            # ── 3. Push тохиргоо ──
            print("\n  ── Камерын push/платформын тохиргоо ──")
            found = False
            for cfg in CONFIG_NAMES:
                try:
                    res = await rpc._call("configManager.getConfig", {"name": cfg})
                except Exception:  # noqa: BLE001
                    continue
                if not res.get("result"):
                    continue
                found = True
                table = (res.get("params") or {}).get("table")
                text = str(table)
                # Манай серверийн хаяг тэнд байна уу
                mine = settings.public_base_url.replace("https://", "").replace("http://", "").split("/")[0]
                hit = mine.split(":")[0] in text
                print(f"    {cfg}: {text[:400]}")
                if hit:
                    print(f"      ✓ манай серверийн хаяг ({mine}) энд БАЙНА")
            if not found:
                print("    (энэ загвар configManager.getConfig-оор push тохиргоог өгөхгүй байна —")
                print("     камерын веб → Тохиргоо → Сүлжээ → Платформ/Alarm Server хэсгээс нүдээр шалгана уу)")
        finally:
            await rpc.logout()

    # ── 4. Дахин ачаалах ──
    if reboot:
        print("\n  ── Камерыг дахин ачаалж байна ──")
        async with httpx.AsyncClient(timeout=15.0) as client:
            rpc = DahuaRpc(client, ip, *creds)
            await rpc.login()
            try:
                res = await rpc._call("magicBox.reboot")
                print(f"    reboot → {res.get('result')}  (камер 40-60 секундэд сэргэнэ)")
            except Exception as e:  # noqa: BLE001
                print(f"    ✗ reboot амжилтгүй: {str(e)[:120]}")
            finally:
                try:
                    await rpc.logout()
                except Exception:  # noqa: BLE001
                    pass


async def main() -> int:
    args = sys.argv[1:]
    reboot = "--reboot" in args
    args = [a for a in args if a != "--reboot"]
    db = SessionLocal()
    try:
        if "--all" in args:
            ips = [d.ip_address for d in db.query(Device).filter(
                Device.device_type == "camera", Device.status == "active",
                Device.ip_address != "").all() if d.ip_address]
        else:
            ips = args
        if not ips:
            print(__doc__)
            return 1
        if reboot and len(ips) > 1:
            print("--reboot нь нэг удаад НЭГ камерт л зөвшөөрөгдөнө.")
            return 1
        for ip in ips:
            await probe(db, ip, reboot=reboot)
        print("\n  ── ЗӨВЛӨМЖ ──")
        print("  Камер амьд атлаа event илгээхгүй бол (сервер→камер OK, камер→сервер тасарсан):")
        print("    1. Камерын ANPR боловсруулалт гацсан байж магадгүй → дахин ачаална:")
        print("       camera_push_check.py <IP> --reboot")
        print("    2. Backend restart хийх үед камерын push холболт тасарч, зарим")
        print("       firmware дахин холбогдохоо больдог — энэ үед мөн reboot тусална.")
        print("    3. Камерын веб → Тохиргоо → Сүлжээ → Платформ хэсэгт манай серверийн")
        print("       хаяг зөв эсэхийг шалгана уу.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
