#!/usr/bin/env python3
"""Камерын холболтын оношилгоо — IP бүрд юу ажиллаж, юу ажиллахгүй байгааг харуулна.

Ажиллуулах (production сервер дээр):
    sudo /root/PARKING/backend/venv/bin/python /root/PARKING/tools/camera_check.py 10.0.101.10 10.0.101.11
    sudo /root/PARKING/backend/venv/bin/python /root/PARKING/tools/camera_check.py --all

Шалгах дараалал (эхнийх нь унавал дараагийнх нь утгагүй):
  1. TCP 80  — сервер камер руу сүлжээгээр хүрч байна уу (route/VLAN/firewall)
  2. HTTP    — вэб сервер хариулж байна уу
  3. Digest  — .env-ийн хэрэглэгч/нууц үг зөв үү (401 бол буруу)
  4. Загвар  — magicBox.cgi getDeviceType (ямар камер болохыг батална)
  5. RPC2    — хаалт удирдах нэвтрэлт (энэ ажиллавал хаалт нээж чадна)
  6. Snapshot— snapshot.cgi зураг өгч байна уу

Гаралт нь тухайн IP бүрийн ЭЦСИЙН дүгнэлт + бүтэлгүйтсэн үед юу хийхийг заана.
"""
import socket
import sys

BACKEND = "/root/PARKING/backend"
sys.path.insert(0, BACKEND)

import os  # noqa: E402

os.chdir(BACKEND)

import httpx  # noqa: E402

from app.config import settings  # noqa: E402

OK, BAD, WARN = "  ✓", "  ✗", "  !"


def tcp_open(ip: str, port: int, timeout: float = 3.0) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except Exception:  # noqa: BLE001
        return False


def check(ip: str) -> bool:
    print(f"\n═══ {ip} ═══")
    auth = httpx.DigestAuth(settings.camera_username, settings.camera_password)

    if not tcp_open(ip, 80):
        print(f"{BAD} TCP 80 хаалттай — сервер энэ IP руу хүрэхгүй байна.")
        print("      Шалгах: камер тэжээлтэй/сүлжээнд холбогдсон эсэх;")
        print(f"      серверээс маршрут байгаа эсэх (ip route get {ip});")
        print("      завсрын firewall/VLAN энэ дэд сүлжээг нэвтрүүлж байгаа эсэх.")
        return False
    print(f"{OK} TCP 80 нээлттэй")

    try:
        r = httpx.get(f"http://{ip}/cgi-bin/magicBox.cgi?action=getDeviceType",
                      auth=auth, timeout=6)
    except Exception as e:  # noqa: BLE001
        print(f"{BAD} HTTP хүсэлт амжилтгүй: {e}")
        return False

    if r.status_code == 401:
        print(f"{BAD} Нэвтрэлт амжилтгүй (401) — хэрэглэгч/нууц үг буруу.")
        print(f"      Одоо ашиглаж буй: {settings.camera_username} / "
              f"{'(хоосон)' if not settings.camera_password else '***'}")
        print("      Засах: /root/PARKING/backend/.env → PARKING_CAMERA_USERNAME / "
              "PARKING_CAMERA_PASSWORD, дараа нь backend restart.")
        return False
    if r.status_code != 200:
        print(f"{WARN} magicBox.cgi HTTP {r.status_code} — Dahua биш эсвэл өөр firmware байж болно")
    else:
        model = r.text.split("type=", 1)[-1].strip().splitlines()[0] if "type=" in r.text else "?"
        print(f"{OK} Нэвтрэлт зөв · загвар: {model}")

    # RPC2 — хаалт удирдах гол суваг (barrier.py-тай ЯГ ижил нэвтрэлт/нууц үг)
    async def _rpc_login():
        from app.services.barrier import DahuaRpc
        password = settings.barrier_password or settings.camera_password
        async with httpx.AsyncClient(timeout=8) as client:
            rpc = DahuaRpc(client, ip, settings.camera_username, password)
            await rpc.login()
            return rpc.session_id

    try:
        import asyncio
        sid = asyncio.run(_rpc_login())
        if sid:
            print(f"{OK} RPC2 нэвтрэлт амжилттай — хаалт удирдах боломжтой")
        else:
            print(f"{BAD} RPC2 нэвтрэлт session өгсөнгүй — хаалт нээх команд ажиллахгүй")
    except Exception as e:  # noqa: BLE001
        print(f"{BAD} RPC2 нэвтрэлт амжилтгүй ({type(e).__name__}: {e})")
        print("      → хаалт нээх команд ажиллахгүй. Нууц үг (.env PARKING_BARRIER_PASSWORD "
              "эсвэл PARKING_CAMERA_PASSWORD) болон камерын RPC2 эрхийг шалгана уу.")

    # Snapshot — session-ий зураг
    try:
        r = httpx.get(f"http://{ip}/cgi-bin/snapshot.cgi", auth=auth, timeout=8)
        if r.status_code == 200 and len(r.content) > 5000:
            print(f"{OK} snapshot.cgi зураг өглөө ({len(r.content)} байт)")
        else:
            print(f"{WARN} snapshot.cgi HTTP {r.status_code}, {len(r.content)} байт "
                  "— зураг татахгүй байж болзошгүй (бүртгэлд саад болохгүй)")
    except Exception as e:  # noqa: BLE001
        print(f"{WARN} snapshot.cgi: {e}")

    return True


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 1

    if args[0] == "--all":
        from app.database import SessionLocal
        from app.models import Device, ParkingSite
        db = SessionLocal()
        rows = (db.query(Device).filter(Device.device_type == "camera",
                                        Device.status == "active",
                                        Device.ip_address != "").all())
        ips = []
        for d in rows:
            s = db.get(ParkingSite, d.site_id)
            print(f"  {(s.site_code if s else '?'):8} {d.lane_dir:6} {d.ip_address}  {d.name}")
            ips.append(d.ip_address)
        db.close()
        if not ips:
            print("IP-тэй идэвхтэй камер бүртгэгдээгүй байна.")
            return 1
    else:
        ips = args

    results = {ip: check(ip) for ip in ips}

    print("\n═══ ДҮГНЭЛТ ═══")
    for ip, ok in results.items():
        print(f"  {ip:16} {'ХҮРЧ БАЙНА' if ok else 'ХҮРЭХГҮЙ'}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
