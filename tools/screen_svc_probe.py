#!/usr/bin/env python
"""LED дэлгэцийн БИЧИХ API-г камерын үйлчилгээний жагсаалтаас олох (зөвхөн унших).

    sudo /root/PARKING/backend/venv/bin/python /root/PARKING/tools/screen_svc_probe.py 192.168.6.11

Юуны учир (2026-07-28 Monnis): getConfig All-аас дэлгэц нь LatticeScreenConfig-тэй,
LogicScreens = 4 бүс (мөр) гэдэг нь тогтоогдсон. Гэвч trafficParking.setScreenDisplay
зөвхөн 1-р мөрөнд урсгадаг, DisplayInfo хувилбар татгалзсан. Тиймээс мөр тус бүрд
бичдэг ЖИНХЭНЭ методыг таамаглахын оронд камераас өөрөөс нь асууна:
  • system.listService — бүх үйлчилгээ
  • <svc>.listMethod   — сонирхолтой үйлчилгээ бүрийн методууд
  • configManager.getConfig DHRS — дэлгэцийн бүрэн тохиргоо
LED-д юу ч бичихгүй тул үйл ажиллагаанд нөлөөгүй, хэдхэн секунд ажиллана.
"""
import asyncio
import json
import os
import sys

BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
sys.path.insert(0, BACKEND)
os.chdir(BACKEND)

import httpx  # noqa: E402

from app.services.barrier import DahuaRpc  # noqa: E402
from app.services.device_auth import camera_credentials  # noqa: E402

# Нэрэнд нь эдгээр үг орсон үйлчилгээний методуудыг ДЭЛГЭРЭНГҮЙ харуулна
INTERESTING = ("screen", "lattice", "dhrs", "led", "park", "voice", "display", "rs485")


def _device_for(ip: str):
    try:
        from app.database import SessionLocal
        from app.models import Device
        db = SessionLocal()
        try:
            return db.query(Device).filter(Device.ip_address == ip).first()
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        return None


async def probe(ip: str) -> None:
    creds = camera_credentials(_device_for(ip))
    print(f"\n═══ {ip} ═══  (нэвтрэх нэр: {creds[0]!r})")
    async with httpx.AsyncClient(timeout=15.0) as client:
        rpc = DahuaRpc(client, ip, *creds)
        try:
            await rpc.login()
        except Exception as e:  # noqa: BLE001
            print(f"  ✗ Нэвтэрч чадсангүй: {str(e)[:160]} — 20с хүлээгээд дахин оролдоно уу "
                  f"(камер RPC-д завгүй байж болно)")
            return
        print("  ✓ RPC2 нэвтрэлт амжилттай")
        try:
            # ── 1. Бүх үйлчилгээ ──
            res = await rpc._call("system.listService")
            services = (res.get("params") or {}).get("service") or []
            print(f"\n  Нийт {len(services)} үйлчилгээ. Дэлгэцтэй холбоотой байж болох нь:")
            hits = [s for s in services if any(k in s.lower() for k in INTERESTING)]
            for s in hits:
                print(f"    • {s}")
            if not hits:
                print("    (нэрээр илэрхий таарсан алга — доорх бүрэн жагсаалтыг харна)")
            # Бүрэн жагсаалтыг мөр болгон хэвлэнэ — гараар харахад
            print("\n  Бүх үйлчилгээ (бүрэн):")
            for i in range(0, len(services), 6):
                print("    " + ", ".join(services[i:i + 6]))

            # ── 2. Сонирхолтой үйлчилгээ бүрийн методууд ──
            targets = list(dict.fromkeys(hits + ["trafficParking", "trafficSnap"]))
            print("\n  Методуудын жагсаалт:")
            for svc in targets:
                try:
                    r = await rpc._call(f"{svc}.listMethod")
                    methods = (r.get("params") or {}).get("method") or []
                    if methods:
                        print(f"    ▸ {svc} ({len(methods)}):")
                        for i in range(0, len(methods), 4):
                            print("        " + ", ".join(methods[i:i + 4]))
                    else:
                        print(f"    ▸ {svc}: методгүй/хариу хоосон ({str(r)[:120]})")
                except Exception as e:  # noqa: BLE001
                    print(f"    ▸ {svc}: {type(e).__name__}")
                await asyncio.sleep(0.4)

            # ── 3. DHRS (дэлгэцийн) бүрэн тохиргоо ──
            for cfg in ("DHRS", "LatticeScreenConfig"):
                try:
                    r = await rpc._call("configManager.getConfig", {"name": cfg})
                    if r.get("result"):
                        dump = json.dumps(r.get("params"), ensure_ascii=False)
                        print(f"\n  getConfig {cfg} (бүрэн, {len(dump)} тэмдэгт):")
                        print("    " + dump[:4000])
                        if len(dump) > 4000:
                            print(f"    ... (нийт {len(dump)}, эхний 4000-ыг харуулав)")
                    else:
                        print(f"\n  getConfig {cfg}: дэмжихгүй ({str(r.get('error'))[:80]})")
                except Exception as e:  # noqa: BLE001
                    print(f"\n  getConfig {cfg}: {type(e).__name__}")
                await asyncio.sleep(0.4)
        finally:
            await rpc.logout()
    print("\n  Дууслаа — энэ гаралтыг бүтнээр нь хуулж өгнө үү.")


async def main() -> int:
    ips = sys.argv[1:]
    if not ips:
        print(__doc__)
        return 1
    for ip in ips:
        await probe(ip)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
