"""Камер бүрийн NTP тохиргоог МАНАЙ сервер рүү чиглүүлнэ (admin RPC2).

ЯАГААД: камерын цагийг гараар зассан ч (camera_clock_check --fix) камер өөрөө
NTP-тэй бол дараа нь дахин гулсдаг. Тогтвортой засвар нь камер бүрийг НАЙДВАРТАЙ
NTP сервер рүү заах. Камерууд тусгаарлагдсан LAN (10.0.x) дээр байдаг тул
интернэтийн pool.ntp.org-д хүрдэггүй — тиймээс МАНАЙ backend серверийг өөрийг нь
NTP сервер болгож (deploy/setup_ntp_server.sh), камеруудыг түүн рүү чиглүүлнэ.

Dahua RPC2: `configManager.getConfig{name:"NTP"}` → `.setConfig{name:"NTP",table}`.
Байгаа тохиргоог УНШИЖ, зөвхөн Enable/Address/Port-ыг өөрчилнө (TimeZone г.м-ийг
хэвээр үлдээнэ).

Ажиллуулах (ПРОДАКШН сервер дээр — камеруудад хүрдэг тал):
    venv/bin/python tools/camera_ntp_config.py                 # одоогийн NTP-г ХАРУУЛНА
    venv/bin/python tools/camera_ntp_config.py --apply         # манай сервер рүү заана
    venv/bin/python tools/camera_ntp_config.py --apply --server 172.16.100.21
    venv/bin/python tools/camera_ntp_config.py --site RASH --apply

ХАМГААЛАЛТ:
  • `--apply`-гүйгээр юу ч БИЧИХГҮЙ (зөвхөн одоогийн тохиргоо харуулна).
  • Хаалтны команд хүлээж буй камерыг алгасна.
  • `--server` өгөхгүй бол камер тус бүрт хүрэх МАНАЙ интерфэйсийн IP-г
    автоматаар тооцоолно (өөр өөр дэд сүлжээнд өөр байж болно).
"""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal
from app.models import Device, ParkingSite
from app.services.barrier import DahuaRpc, barrier_is_waiting, camera_client
from app.services.camera_sessions import _our_ip_toward
from app.services.device_auth import camera_credentials

NTP_PORT = 123
UPDATE_MIN = 10   # камер хэдэн минут тутам NTP-ээс цагаа авах вэ


async def _one(ip, creds, name, server_ip, apply):
    client = camera_client(ip)
    rpc = DahuaRpc(client, ip, creds[0], creds[1])
    target = server_ip or _our_ip_toward(ip)
    try:
        await asyncio.wait_for(rpc.login(), timeout=12)
        cur = await asyncio.wait_for(rpc._call("configManager.getConfig",
                                               {"name": "NTP"}), timeout=8)
        table = (cur.get("params") or {}).get("table")
        if not isinstance(table, dict):
            await _safe_logout(rpc)
            return {"ip": ip, "name": name, "error": f"NTP config уншсангүй: {str(cur)[:90]}"}
        now = {"enable": table.get("Enable"), "addr": table.get("Address"),
               "port": table.get("Port")}

        if not apply:
            await _safe_logout(rpc)
            return {"ip": ip, "name": name, "now": now, "target": target, "applied": False}

        if barrier_is_waiting(ip):
            await _safe_logout(rpc)
            return {"ip": ip, "name": name, "now": now, "target": target,
                    "skipped": "хаалтны команд хүлээж байна"}

        # Байгаа table-г ХАДГАЛЖ, зөвхөн шаардлагатайг өөрчилнө
        new_table = dict(table)
        new_table["Enable"] = True
        new_table["Address"] = target
        new_table["Port"] = NTP_PORT
        # UpdatePeriod минутаар (firmware ихэнх нь минут хүлээж авдаг)
        if "UpdatePeriod" in new_table:
            new_table["UpdatePeriod"] = UPDATE_MIN
        res = await asyncio.wait_for(rpc._call(
            "configManager.setConfig", {"name": "NTP", "table": new_table}), timeout=10)
        await _safe_logout(rpc)
        if res.get("result"):
            return {"ip": ip, "name": name, "now": now, "target": target, "applied": True}
        return {"ip": ip, "name": name, "now": now, "target": target,
                "error": f"setConfig татгалзлаа: {str(res)[:90]}"}
    except Exception as e:  # noqa: BLE001
        return {"ip": ip, "name": name, "error": f"{type(e).__name__}: {str(e)[:80]}"}


async def _safe_logout(rpc):
    try:
        await rpc.logout()
    except Exception:  # noqa: BLE001
        pass


async def run(site_code, server_ip, apply):
    db = SessionLocal()
    try:
        cams = (db.query(Device).join(ParkingSite, Device.site_id == ParkingSite.id)
                .filter(Device.device_type == "camera", Device.status == "active",
                        Device.ip_address.isnot(None), Device.ip_address != "").all())
        if site_code:
            site = (db.query(ParkingSite).filter(ParkingSite.site_code == site_code).first()
                    or db.query(ParkingSite)
                    .filter(ParkingSite.name.ilike(f"{site_code}%")).first())
            if not site:
                sys.exit(f"«{site_code}» олдсонгүй")
            cams = [c for c in cams if c.site_id == site.id]
        targets = [(c.ip_address, camera_credentials(c),
                    f"{db.get(ParkingSite, c.site_id).name} · {c.name or c.ip_address}")
                   for c in cams]
    finally:
        db.close()
    if not targets:
        print("Идэвхтэй камер олдсонгүй.")
        return

    sem = asyncio.Semaphore(4)

    async def _g(t):
        async with sem:
            return await _one(*t, server_ip=server_ip, apply=apply)

    results = await asyncio.gather(*(_g(t) for t in targets))

    print(f"{'камер':38}{'одоогийн NTP':>26}{'→ шинэ':>18}   төлөв")
    done = skip = err = 0
    for r in sorted(results, key=lambda x: x["name"]):
        if r.get("error"):
            err += 1
            print(f"{r['name'][:36]:38}{'—':>26}{'—':>18}   ⚠ {r['error']}")
            continue
        now = r["now"]
        cur = f"{'ON' if now['addr'] else 'OFF'} {now['addr'] or '-'}:{now['port'] or '-'}"
        if r.get("applied"):
            done += 1
            state = "✅ ЗАССАН"
        elif r.get("skipped"):
            skip += 1
            state = f"алгасав ({r['skipped']})"
        else:
            state = "(dry-run)"
        print(f"{r['name'][:36]:38}{cur:>26}{r['target']:>18}   {state}")

    print()
    if apply:
        print(f"   ЗАССАН {done}  ·  алгассан {skip}  ·  алдаа {err}")
        print("   Камерууд одооноос МАНАЙ сервер рүү цагаа тааруулна. Серверт NTP "
              "сервер асаалттай байх ёстой (deploy/setup_ntp_server.sh).")
    else:
        print("   ⓘ Энэ нь ЗӨВХӨН одоогийн NTP тохиргоог харуулав. Заахдаа `--apply`.")
        print("   ⚠ ЭХЛЭЭД серверт NTP сервер асаана уу: deploy/setup_ntp_server.sh")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", help="зөвхөн нэг зогсоол (код эсвэл нэрний эхлэл)")
    ap.add_argument("--server", help="камерууд заах NTP серверийн IP "
                                     "(өгөхгүй бол авто-тооцоолно)")
    ap.add_argument("--apply", action="store_true", help="NTP-г БОДИТООР бичнэ")
    args = ap.parse_args()
    asyncio.run(run(args.site, args.server, args.apply))


if __name__ == "__main__":
    main()
