"""Камер бүрийн ДОТООД цагийг серверийн цагтай тулгаж, зөрүүг хэмжинэ (ба засна).

ЯАГААД ЧУХАЛ ВЭ: `camera_sync` нь алдагдсан event-ийг нөхөхдөө камерын логийн
цагийг зогсолтын орсон/гарсан цаг болгож бичдэг. Камерын цаг зөрүүтэй бол
(2026-08-17: ялалт/Эрэл-13/Хангарьд -7 минут) нөхөгдсөн зогсолтын ХУГАЦАА ба
ТӨЛБӨР буруу бодогдоно. Мөн event_loss_diag-ийн «алдагдсан» тоо ч гажина.

Камерын цагийг RPC2 `global.getCurrentTime`-ээр өөрөөс нь асууж, серверийн
цагтай харьцуулна.

Ажиллуулах (production сервер дээр, backend хавтаст):
    venv/bin/python tools/camera_clock_check.py                # ЗӨВХӨН шалгана
    venv/bin/python tools/camera_clock_check.py --site RASH
    venv/bin/python tools/camera_clock_check.py --fix          # ЗАСНА (dry-run)
    venv/bin/python tools/camera_clock_check.py --fix --apply  # ЗАСНА (бодитоор)

ХӨДӨЛГӨӨН:
  • флагггүй        → зөвхөн уншиж, зөрүүг харуулна (юу ч тохируулахгүй).
  • `--fix`         → зөрүү >босго камеруудыг ЯАХ БАЙСНЫГ харуулна (dry-run).
  • `--fix --apply` → камерын цагийг серверийн цагаар БОДИТООР тохируулна
                      (RPC2 `global.setCurrentTime`). Хаалтны команд хүлээж
                      байвал тухайн камерыг алгасна.

АНХААР: `--apply` нь камерын цагийг БИЧНЭ. Тохируулсны дараа камер өөрөө NTP-тэй
бол дахин гулсаж болзошгүй — тогтвортой засвар нь камер дээр NTP асаах.
"""
import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal
from app.models import Device, ParkingSite
from app.services.barrier import DahuaRpc, barrier_is_waiting, camera_client
from app.services.device_auth import camera_credentials

# Камерын getCurrentTime нь ДОТООД цагаа "ОРОН НУТГИЙН" цагаар (жишээ УБ +8)
# буцаадаг тул серверийн ОРОН НУТГИЙН цагтай харьцуулна.
TZ_OFFSET_HOURS = 8


def _server_local(t_req, t_resp):
    """RPC round-trip-ийн дундаж агшны серверийн ОРОН НУТГИЙН цаг."""
    from datetime import timedelta
    mid = t_req + (t_resp - t_req) / 2
    srv = mid.astimezone(timezone.utc).replace(tzinfo=None, microsecond=0)
    return srv + timedelta(hours=TZ_OFFSET_HOURS)


async def _one(ip: str, creds: tuple[str, str], name: str,
               fix: bool = False, apply: bool = False, threshold: int = 120) -> dict:
    client = camera_client(ip)
    rpc = DahuaRpc(client, ip, creds[0], creds[1])
    t_req = datetime.now(timezone.utc)
    try:
        await asyncio.wait_for(rpc.login(), timeout=12)
        # Dahua RPC2: global.getCurrentTime → params.time = "YYYY-MM-DD HH:MM:SS"
        res = await asyncio.wait_for(rpc._call("global.getCurrentTime"), timeout=8)
        t_resp = datetime.now(timezone.utc)

        tstr = ((res.get("params") or {}).get("time")
                or (res.get("params") or {}).get("Time"))
        if not tstr:
            return {"ip": ip, "name": name, "error": f"цаг буцаасангүй: {str(res)[:100]}"}
        try:
            cam_local = datetime.strptime(tstr, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return {"ip": ip, "name": name, "error": f"формат танигдсангүй: {tstr}"}

        srv_local = _server_local(t_req, t_resp)
        skew = (cam_local - srv_local).total_seconds()
        out = {"ip": ip, "name": name, "cam": tstr,
               "srv": srv_local.strftime("%Y-%m-%d %H:%M:%S"), "skew": skew}

        if fix and abs(skew) > threshold:
            if barrier_is_waiting(ip):
                out["fix"] = "алгасав (хаалтны команд хүлээж байна)"
            elif not apply:
                out["fix"] = f"ЗАСАХ БАЙСАН → {out['srv']} (dry-run)"
            else:
                # Серверийн ОДООГИЙН цагаар шинэ утга (login/read-д саатсан
                # хугацааг тооцож, бичих агшны цагийг дахин авна)
                set_to = _server_local(datetime.now(timezone.utc),
                                       datetime.now(timezone.utc))
                sres = await asyncio.wait_for(rpc._call(
                    "global.setCurrentTime",
                    {"time": set_to.strftime("%Y-%m-%d %H:%M:%S")}), timeout=8)
                if sres.get("result"):
                    out["fix"] = f"ЗАССАН → {set_to.strftime('%H:%M:%S')}"
                else:
                    out["fix"] = f"засаж чадсангүй: {str(sres)[:80]}"
        try:
            await rpc.logout()
        except Exception:  # noqa: BLE001
            pass
        return out
    except Exception as e:  # noqa: BLE001
        return {"ip": ip, "name": name, "error": f"{type(e).__name__}: {str(e)[:80]}"}


async def run(site_code: str | None, fix: bool = False, apply: bool = False,
              threshold: int = 120):
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
            return await _one(*t, fix=fix, apply=apply, threshold=threshold)

    results = await asyncio.gather(*(_guard(t) for t in targets))

    has_fix = any("fix" in r for r in results)
    hdr = f"{'камер':38}{'камерын цаг':>21}{'зөрүү':>12}"
    print(hdr + ("   үйлдэл" if has_fix else ""))
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
        act = f"   {r['fix']}" if r.get("fix") else flag
        print(f"{r['name'][:36]:38}{r['cam']:>21}{sk:>+9.0f}с{act}")

    print(f"\n   зөв (±30с) {ok}  ·  зөрүүтэй {warn}  ·  холбогдсонгүй {err}")
    if fix and not apply:
        print("   ⓘ Энэ нь ЗӨВХӨН харуулав (dry-run). Бодитоор засахдаа "
              "`--fix --apply` нэмнэ.")
    elif not fix:
        print("   Засахдаа: `--fix` (dry-run) → `--fix --apply` (бодитоор).")
    print("   Тогтвортой засвар: камер дээр NTP сервер асаах (веб UI → Систем → "
          "Огноо/цаг). Эс бол камер дахин гулсана.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", help="зөвхөн нэг зогсоол (код эсвэл нэрний эхлэл)")
    ap.add_argument("--fix", action="store_true",
                    help="зөрүүтэй камерын цагийг серверийнхээр засна (dry-run)")
    ap.add_argument("--apply", action="store_true",
                    help="--fix-тэй хамт: цагийг БОДИТООР бичнэ")
    ap.add_argument("--threshold", type=int, default=120,
                    help="хэдэн секундээс дээш зөрүүтэйг засах (default 120)")
    args = ap.parse_args()
    asyncio.run(run(args.site, fix=args.fix, apply=args.apply, threshold=args.threshold))


if __name__ == "__main__":
    main()
