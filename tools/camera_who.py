#!/usr/bin/env python
"""Камерт ХЭН холбогдсон/нэвтрэхийг оролдсоныг камераас ӨӨРӨӨС нь асуух (зөвхөн унших).

    sudo /root/PARKING/backend/venv/bin/python /root/PARKING/tools/camera_who.py 192.168.6.10

Юуны учир (2026-07-28): камерын бүртгэл үе үе түгжигдэж (remainLoginTimes буурч),
хаалтны команд timeout болдог. Аль систем үүнийг үүсгэж байгааг таамаглахын оронд
камерын өөрийн мэдээллээс IP-тэй нь баримтжуулна:
  1. Идэвхтэй хэрэглэгчид/сесс (UserManager) — яг одоо хэн холбогдсон, аль IP-ээс
  2. Нэвтрэлтийн лог (log.*) — сүүлийн 24ц-ийн Login/алдаатай нэвтрэлт, эх IP
Бүх дуудлага READ-ONLY. Камер завгүй үед нэвтрэлт нэг удаа амжилтгүй болж
болзошгүй — 20-30 секундын дараа дахин оролдоно уу.
"""
import asyncio
import json
import os
import sys
from datetime import datetime, timedelta

BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
sys.path.insert(0, BACKEND)
os.chdir(BACKEND)

import httpx  # noqa: E402

from app.services.barrier import DahuaRpc  # noqa: E402
from app.services.device_auth import camera_credentials  # noqa: E402


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


def _dump(label: str, res: dict, limit: int = 1200):
    if res.get("result"):
        s = json.dumps(res.get("params"), ensure_ascii=False)
        print(f"  ✓ {label}: {s[:limit]}{' ...' if len(s) > limit else ''}")
        return True
    err = (res.get("error") or {})
    print(f"  · {label}: дэмжихгүй ({err.get('message', str(res)[:80])})")
    return False


async def probe(ip: str) -> None:
    creds = camera_credentials(_device_for(ip))
    print(f"\n═══ {ip} ═══  (нэвтрэх нэр: {creds[0]!r})")
    async with httpx.AsyncClient(timeout=15.0) as client:
        rpc = DahuaRpc(client, ip, *creds)
        try:
            await rpc.login()
        except Exception as e:  # noqa: BLE001
            print(f"  ✗ Нэвтэрч чадсангүй: {str(e)[:200]}")
            return
        print("  ✓ RPC2 нэвтрэлт амжилттай\n  ── Идэвхтэй хэрэглэгчид/сесс ──")
        try:
            for method in ("UserManager.getActiveUserInfoAll",
                           "UserManager.getActiveUserInfo",
                           "global.getCurrentTime"):
                try:
                    _dump(method, await rpc._call(method))
                except Exception as e:  # noqa: BLE001
                    print(f"  · {method}: {type(e).__name__}")
                await asyncio.sleep(0.5)

            # ── Нэвтрэлтийн лог (сүүлийн 24 цаг) ──
            print("\n  ── Нэвтрэлтийн лог (сүүлийн 24ц) ──")
            end = datetime.now()
            start = end - timedelta(hours=24)
            cond = {"StartTime": start.strftime("%Y-%m-%d %H:%M:%S"),
                    "EndTime": end.strftime("%Y-%m-%d %H:%M:%S"),
                    "Translate": True, "Types": ["Login", "LoginFailure", "Logout"]}
            token = None
            try:
                r = await rpc._call("log.startFind", {"condition": cond})
                token = (r.get("params") or {}).get("token") or r.get("result")
                if not r.get("result"):
                    # Types шүүлтгүйгээр дахин
                    cond.pop("Types", None)
                    r = await rpc._call("log.startFind", {"condition": cond})
                    token = (r.get("params") or {}).get("token") or r.get("result")
                if token in (True, False, None):
                    print(f"  · log.startFind: token олдсонгүй ({str(r)[:150]})")
                    token = None
            except Exception as e:  # noqa: BLE001
                print(f"  · log.startFind: {type(e).__name__}")
            if token:
                shown = 0
                for _page in range(10):
                    r = await rpc._call("log.doFind", {"token": token, "count": 50})
                    items = (r.get("params") or {}).get("items") or []
                    for it in items:
                        t = it.get("Time", "?")
                        typ = it.get("Type", "?")
                        user = it.get("User") or it.get("user") or ""
                        detail = it.get("Detail") or {}
                        rip = (detail.get("RemoteIP") or detail.get("Address")
                               or it.get("RemoteIP") or "")
                        # Зөвхөн нэвтрэлттэй холбоотойг харуулна
                        if any(k in str(typ).lower() for k in ("login", "logout", "lock", "user")):
                            print(f"    {t}  {typ:<14} user={user:<10} ip={rip}")
                            shown += 1
                    if not items or not (r.get("params") or {}).get("found", len(items)):
                        break
                try:
                    await rpc._call("log.stopFind", {"token": token})
                except Exception:  # noqa: BLE001
                    pass
                if not shown:
                    print("    (нэвтрэлтийн бичлэг олдсонгүй — Types дэмжихгүй байж болно)")
            # CGI fallback — зарим firmware log-оо CGI-ээр өгдөг
            if not token:
                url = (f"http://{ip}/cgi-bin/log.cgi?action=startFind"
                       f"&condition.StartTime={start:%Y-%m-%d%%20%H:%M:%S}"
                       f"&condition.EndTime={end:%Y-%m-%d%%20%H:%M:%S}")
                try:
                    resp = await client.get(url, auth=httpx.DigestAuth(*creds))
                    print(f"  log.cgi: HTTP {resp.status_code} {resp.text[:200]!r}")
                except Exception as e:  # noqa: BLE001
                    print(f"  log.cgi: {type(e).__name__}")
        finally:
            await rpc.logout()
    print("\n  Дууслаа — «ip=» хэсэгт МАНАЙ сервер (172.16.100.21)-ээс ӨӨР хаяг гарвал"
          "\n  тэр нь камерт зэрэг ханддаг нөгөө систем мөн (админд үзүүлэх баримт).")


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
