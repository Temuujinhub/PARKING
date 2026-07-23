#!/usr/bin/env python3
"""Камерын зургийн API оношилгоо — аль механизм энэ firmware дээр ажилладгийг тогтооно.

Ажиллуулах (production сервер дээр):
    sudo /root/PARKING/backend/venv/bin/python /root/PARKING/tools/camera_snap_diag.py 10.0.113.10

Шалгах зүйл:
  1. storageDevice — SD карт/санах ой байгаа эсэх (байхгүй бол mediaFileFind хоосон нь ойлгомжтой)
  2. snapManager.attachFileProc — параметрийн 5 хувилбар (аль нь 200 өгөхийг олно)
  3. mediaFileFind — нөхцөлийн хувилбарууд (өнөөдрийн бүх өдрөөр)
  4. snapshot.cgi — fallback ажиллаж байгаа эсэх
Гаралтыг бүтнээр нь хуулж өгнө үү — дараагийн засварыг үүн дээр тулгуурлана.
"""
import asyncio
import re
import sys
from datetime import datetime

sys.path.insert(0, "/root/PARKING/backend")
import httpx  # noqa: E402


def env_creds():
    user, pwd = "admin", ""
    try:
        for line in open("/root/PARKING/backend/.env"):
            line = line.strip()
            if line.startswith("PARKING_CAMERA_USERNAME="):
                user = line.split("=", 1)[1]
            elif line.startswith("PARKING_CAMERA_PASSWORD="):
                pwd = line.split("=", 1)[1]
    except OSError:
        pass
    return user, pwd


async def main(ip: str):
    user, pwd = env_creds()
    auth = httpx.DigestAuth(user, pwd)
    today = datetime.now().strftime("%Y-%m-%d")
    print(f"=== Камер {ip}, хэрэглэгч {user} ===\n")

    async with httpx.AsyncClient(timeout=10, auth=auth) as c:
        # 1. Санах ой
        print("--- 1. storageDevice (SD карт байгаа юу?) ---")
        for act in ("getDeviceAllInfo", "factory.getCollect"):
            try:
                r = await c.get(f"http://{ip}/cgi-bin/storageDevice.cgi", params={"action": act})
                print(f"  {act}: HTTP {r.status_code}")
                for ln in r.text.splitlines()[:12]:
                    print(f"    {ln}")
            except Exception as e:
                print(f"  {act}: {e}")

        # 2. snapManager хувилбарууд
        print("\n--- 2. snapManager.attachFileProc хувилбарууд ---")
        variants = [
            "Flags[0]=Event&Events=[All]&heartbeat=5",
            "Flags[0]=Event&Events=[TrafficJunction]&heartbeat=5",
            "channel=1&Flags[0]=Event&Events=[TrafficJunction]&heartbeat=5",
            "Flags[0]=Event&heartbeat=5",
            "Flags[0]=Event&Flags[1]=Timing&Events=[All]&heartbeat=5",
        ]
        for v in variants:
            url = f"http://{ip}/cgi-bin/snapManager.cgi?action=attachFileProc&{v}"
            try:
                async with c.stream("GET", url) as resp:
                    print(f"  HTTP {resp.status_code}  ?{v}")
                    if resp.status_code != 200:
                        body = (await resp.aread())[:200]
                        print(f"    body: {body!r}")
                    else:
                        print(f"    content-type: {resp.headers.get('content-type')}")
                        # 6 секунд уншаад heartbeat/зураг ирэхийг харна
                        got = b""
                        try:
                            async def read_some():
                                nonlocal got
                                async for chunk in resp.aiter_bytes():
                                    got += chunk
                                    if len(got) > 3000:
                                        break
                            await asyncio.wait_for(read_some(), timeout=6)
                        except asyncio.TimeoutError:
                            pass
                        print(f"    6с-д {len(got)}b ирэв; эхлэл: {got[:120]!r}")
                        break  # ажилласан хувилбар олдлоо
            except Exception as e:
                print(f"  АЛДАА ?{v}: {e}")

        # 3. mediaFileFind хувилбарууд
        print("\n--- 3. mediaFileFind (өнөөдөр бүхэлдээ) ---")
        base = f"http://{ip}/cgi-bin/mediaFileFind.cgi"
        conds = [
            {"condition.Channel": 0, "condition.Types[0]": "jpg"},
            {"condition.Channel": 1, "condition.Types[0]": "jpg"},
            {"condition.Channel": 0, "condition.Types[0]": "jpg", "condition.Flags[0]": "Event"},
            {"condition.Channel": 0},
        ]
        for cond in conds:
            try:
                r = await c.get(base, params={"action": "factory.create"})
                m = re.search(r"result=(\d+)", r.text)
                if not m:
                    print(f"  factory.create бүтсэнгүй: HTTP {r.status_code} {r.text[:80]!r}")
                    break
                token = m.group(1)
                params = {"action": "findFile", "object": token,
                          "condition.StartTime": f"{today} 00:00:00",
                          "condition.EndTime": f"{today} 23:59:59", **cond}
                r = await c.get(base, params=params)
                r2 = await c.get(base, params={"action": "findNextFile", "object": token, "count": 5})
                head = " | ".join(r2.text.splitlines()[:6])
                print(f"  {cond} → findFile: {r.text.strip()[:40]!r}; findNext: {head[:200]!r}")
                await c.get(base, params={"action": "close", "object": token})
                await c.get(base, params={"action": "destroy", "object": token})
            except Exception as e:
                print(f"  {cond}: АЛДАА {e}")

        # 4. snapshot.cgi fallback
        print("\n--- 4. snapshot.cgi ---")
        try:
            r = await c.get(f"http://{ip}/cgi-bin/snapshot.cgi")
            ok = r.status_code == 200 and r.content[:2] == b"\xff\xd8"
            print(f"  HTTP {r.status_code}, {len(r.content)}b, JPEG={ok}")
        except Exception as e:
            print(f"  АЛДАА: {e}")

    print("\n=== Дууслаа — энэ гаралтыг бүтнээр нь хуулж өгнө үү ===")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Хэрэглээ: camera_snap_diag.py <камерын IP>")
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))
