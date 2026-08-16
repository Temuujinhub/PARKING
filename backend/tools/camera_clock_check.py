"""Камер бүрийн ДОТООД цагийг серверийн цагтай тулгаж, зөрүүг хэмжинэ.

ЯАГААД ЧУХАЛ ВЭ: `camera_sync` нь алдагдсан event-ийг нөхөхдөө камерын логийн
цагийг зогсолтын орсон/гарсан цаг болгож бичдэг. Камерын цаг зөрүүтэй бол
(Рашбулаг 2026-08-16: +32 минут) нөхөгдсөн зогсолтын ХУГАЦАА ба ТӨЛБӨР буруу
бодогдоно. Мөн event_loss_diag-ийн «алдагдсан» тоо ч гажина.

Камерын цагийг RPC2 `global.getCurrentTime`-ээр өөрөөс нь асууж, серверийн
UTC-тэй харьцуулна.

Ажиллуулах (production сервер дээр, backend хавтаст):
    venv/bin/python tools/camera_clock_check.py
    venv/bin/python tools/camera_clock_check.py --site RASH

АНХААР: камер бүр рүү RPC login хийнэ (хаалтны команд хүлээж байвал алгасна).
Зөвхөн ЦАГИЙГ УНШИНА — юу ч ТОХИРУУЛАХГҮЙ.
"""
import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal
from app.models import Device, ParkingSite
from app.services.barrier import DahuaRpc, camera_client
from app.services.device_auth import camera_credentials

# Камерын getCurrentTime нь ДОТООД цагаа "ОРОН НУТГИЙН" цагаар (жишээ УБ +8)
# буцаадаг тул серверийн ОРОН НУТГИЙН цагтай харьцуулна.
TZ_OFFSET_HOURS = 8


async def _one(ip: str, creds: tuple[str, str], name: str) -> dict:
    client = camera_client(ip)
    rpc = DahuaRpc(client, ip, creds[0], creds[1])
    t_req = datetime.now(timezone.utc)
    try:
        await asyncio.wait_for(rpc.login(), timeout=12)
        # Dahua RPC2: global.getCurrentTime → params.time = "YYYY-MM-DD HH:MM:SS"
        res = await asyncio.wait_for(rpc._call("global.getCurrentTime"), timeout=8)
        try:
            await rpc.logout()
        except Exception:  # noqa: BLE001
            pass
    except Exception as e:  # noqa: BLE001
        return {"ip": ip, "name": name, "error": f"{type(e).__name__}: {str(e)[:80]}"}
    t_resp = datetime.now(timezone.utc)

    tstr = ((res.get("params") or {}).get("time")
            or (res.get("params") or {}).get("Time"))
    if not tstr:
        return {"ip": ip, "name": name, "error": f"цаг буцаасангүй: {str(res)[:100]}"}
    try:
        cam_local = datetime.strptime(tstr, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return {"ip": ip, "name": name, "error": f"цагийн формат танигдсангүй: {tstr}"}

    # Серверийн орон нутгийн цаг (RPC round-trip-ийн дунджаар)
    mid = t_req + (t_resp - t_req) / 2
    srv_local = mid.astimezone(timezone.utc).replace(tzinfo=None)
    srv_local = srv_local.replace(microsecond=0)
    from datetime import timedelta
    srv_local = srv_local + timedelta(hours=TZ_OFFSET_HOURS)
    skew = (cam_local - srv_local).total_seconds()
    return {"ip": ip, "name": name, "cam": tstr,
            "srv": srv_local.strftime("%Y-%m-%d %H:%M:%S"), "skew": skew}


async def run(site_code: str | None):
    db = SessionLocal()
    try:
        q = (db.query(Device).join(ParkingSite, Device.site_id == ParkingSite.id)
             .filter(Device.device_type == "camera", Device.status == "active",
                     Device.ip_address.isnot(None), Device.ip_address != ""))
        cams = q.all()
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

    async def _guard(t):
        async with sem:
            return await _one(*t)

    results = await asyncio.gather(*(_guard(t) for t in targets))

    print(f"{'камер':38}{'камерын цаг':>21}{'зөрүү':>12}")
    ok = warn = err = 0
    for r in sorted(results, key=lambda x: abs(x.get("skew", 0)), reverse=True):
        if r.get("error"):
            err += 1
            print(f"{r['name'][:36]:38}{'—':>21}   ⚠ {r['error']}")
            continue
        sk = r["skew"]
        flag = ""
        if abs(sk) <= 30:
            ok += 1
        elif abs(sk) <= 120:
            warn += 1
            flag = "  ← ~2 мин зөрүү"
        else:
            warn += 1
            flag = f"  ← ⚠ {sk / 60:+.0f} МИНУТ ЗӨРҮҮ — camera_sync цагийг гажуулна"
        print(f"{r['name'][:36]:38}{r['cam']:>21}{sk:>+9.0f}с{flag}")

    print(f"\n   зөв (±30с) {ok}  ·  зөрүүтэй {warn}  ·  холбогдсонгүй {err}")
    print("   Зөрүү ихтэй камерт: веб UI → Тохиргоо → Систем → Огноо/цаг → NTP "
          "сервер (ж: pool.ntp.org эсвэл дотоод сервер) асаах.")
    print("   Камерын цаг зассаны дараа шинэ зогсолт зөв цагаар нөхөгдөнө "
          "(хуучин бичлэгийг залруулахгүй).")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", help="зөвхөн нэг зогсоол (код эсвэл нэрний эхлэл)")
    args = ap.parse_args()
    asyncio.run(run(args.site))


if __name__ == "__main__":
    main()
