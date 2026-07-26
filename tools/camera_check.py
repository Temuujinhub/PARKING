#!/usr/bin/env python3
"""Камерын холболтын оношилгоо — IP бүрд юу ажиллаж, юу ажиллахгүй байгааг харуулна.

Ажиллуулах (production сервер дээр):
    sudo /root/PARKING/backend/venv/bin/python /root/PARKING/tools/camera_check.py 10.0.101.10 10.0.101.11
    sudo /root/PARKING/backend/venv/bin/python /root/PARKING/tools/camera_check.py --all
    sudo /root/PARKING/backend/venv/bin/python /root/PARKING/tools/camera_check.py --site SPORT

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


def _mask(secret: str) -> str:
    """Нууц үгийг БҮТНЭЭР нь хэвлэхгүйгээр таних тэмдэг: урт + эхний/сүүлийн үсэг.
    Ингэснээр "юу хадгалагдсаныг" (алдаатай хуулсан, зай оруулсан г.м) шалгаж болно."""
    if not secret:
        return "(хоосон)"
    if len(secret) <= 2:
        return f"{len(secret)} тэмдэгт"
    return f"{len(secret)} тэмдэгт: {secret[0]}{'•' * (len(secret) - 2)}{secret[-1]}"


def _device_for(ip: str):
    """Энэ IP-тэй бүртгэлтэй төхөөрөмж (өөрийн нэвтрэлттэй бол түүгээр шалгана)."""
    try:
        from app.database import SessionLocal
        from app.models import Device
        db = SessionLocal()
        try:
            return db.query(Device).filter(Device.ip_address == ip,
                                           Device.status == "active").first()
        finally:
            db.close()
    except Exception:  # noqa: BLE001 — DB байхгүй ч IP-гээр шалгах боломжтой хэвээр
        return None


def check(ip: str) -> str:
    """Буцаах: 'ok' | 'auth' (сүлжээгээр хүрч байгаа ч нэвтрэлт буруу) | 'net'"""
    print(f"\n═══ {ip} ═══")
    from app.services.device_auth import barrier_credentials, camera_credentials
    device = _device_for(ip)
    cam_user, cam_pass = camera_credentials(device)
    if device is not None and (device.username or device.password):
        print(f"  · {device.name} — ТӨХӨӨРӨМЖИЙН өөрийн нэвтрэлт (DB-д хадгалагдсан)")
    else:
        print("  · системийн ерөнхий нэвтрэлт (.env) — энэ төхөөрөмжид өөрийнх нь алга")
    print(f"      нэр:      {cam_user!r}")
    print(f"      нууц үг:  {_mask(cam_pass)}")
    if cam_pass != cam_pass.strip():
        print("      !! Нууц үгийн урд/хойно ЗАЙ байна — хуулахдаа орсон байж магадгүй")
    auth = httpx.DigestAuth(cam_user, cam_pass)

    if not tcp_open(ip, 80):
        print(f"{BAD} TCP 80 хаалттай — сервер энэ IP руу хүрэхгүй байна.")
        print("      Шалгах: камер тэжээлтэй/сүлжээнд холбогдсон эсэх;")
        print(f"      серверээс маршрут байгаа эсэх (ip route get {ip});")
        print("      завсрын firewall/VLAN энэ дэд сүлжээг нэвтрүүлж байгаа эсэх.")
        return "net"
    print(f"{OK} TCP 80 нээлттэй")

    try:
        r = httpx.get(f"http://{ip}/cgi-bin/magicBox.cgi?action=getDeviceType",
                      auth=auth, timeout=6)
    except Exception as e:  # noqa: BLE001
        print(f"{BAD} HTTP хүсэлт амжилтгүй: {e}")
        return "net"

    if r.status_code == 401:
        print(f"{BAD} Нэвтрэлт амжилтгүй (401) — хэрэглэгч/нууц үг буруу.")
        print(f"      Туршсан: {cam_user} / {'(хоосон)' if not cam_pass else '***'}")
        print("      Сүлжээ ХЭВИЙН — зөвхөн нэвтрэлт таарахгүй байна. Засах 2 арга:")
        print("      1) Энэ камер өөр нууц үгтэй бол: UI → Тохиргоо → Төхөөрөмж →")
        print("         тухайн камерыг засаад 'Нэвтрэх нэр/Нууц үг' талбарт бичнэ")
        print("         (зогсоол бүр өөр нууц үгтэй байж болно).")
        print("      2) Бүх камер ижил шинэ нууц үгтэй бол: backend/.env →")
        print("         PARKING_CAMERA_USERNAME / PARKING_CAMERA_PASSWORD + restart.")
        return "auth"
    if r.status_code != 200:
        print(f"{WARN} magicBox.cgi HTTP {r.status_code} — Dahua биш эсвэл өөр firmware байж болно")
    else:
        model = r.text.split("type=", 1)[-1].strip().splitlines()[0] if "type=" in r.text else "?"
        print(f"{OK} Нэвтрэлт зөв · загвар: {model}")

    # RPC2 — хаалт удирдах гол суваг (barrier.py-тай ЯГ ижил нэвтрэлт/нууц үг)
    bar_user, bar_pass = barrier_credentials(device)

    async def _rpc_login():
        from app.services.barrier import DahuaRpc
        async with httpx.AsyncClient(timeout=8) as client:
            rpc = DahuaRpc(client, ip, bar_user, bar_pass)
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
        msg = str(e)
        if "remainLockSecond" in msg or "ТҮГЖИГДСЭН" in msg:
            print(f"{BAD} Камер ТҮГЖИГДСЭН — олон удаа буруу нууц үг очсоны улмаас.")
            print("      Нууц үг зөв байсан ч түгжээ тайлагдтал нэвтрэхгүй.")
            print("      Хийх зүйл: (1) DB дэх нууц үгээ зөв болгох, (2) 5-10 минут хүлээх,")
            print("      (3) дараа нь дахин шалгах. Буруу нууц үгээр давтвал түгжээ уртасна.")
            return "auth"
        print(f"{BAD} RPC2 нэвтрэлт амжилтгүй ({type(e).__name__}: {e})")
        print(f"      → хаалт нээх команд ажиллахгүй. Туршсан: {bar_user}. "
              "Нууц үг болон камерын RPC2 эрхийг шалгана уу.")

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

    return "ok"


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 1

    if args[0] in ("--all", "--site"):
        from app.database import SessionLocal
        from app.models import Device, ParkingSite
        want = args[1].upper() if args[0] == "--site" and len(args) > 1 else None
        db = SessionLocal()
        rows = (db.query(Device).filter(Device.device_type == "camera",
                                        Device.status == "active",
                                        Device.ip_address != "").all())
        ips = []
        for d in rows:
            st = db.get(ParkingSite, d.site_id)
            code = st.site_code if st else "?"
            if want and code.upper() != want:
                continue
            own = "өөрийн нэвтрэлттэй" if (d.username or d.password) else "ерөнхий нэвтрэлт"
            print(f"  {code:8} {d.lane_dir:6} {d.ip_address:15} {d.name}  [{own}]")
            ips.append(d.ip_address)
        db.close()
        if want and not ips:
            print(f"'{want}' зогсоолд IP-тэй идэвхтэй камер алга.")
            return 1
        if not ips:
            print("IP-тэй идэвхтэй камер бүртгэгдээгүй байна.")
            return 1
    else:
        ips = args

    results = {ip: check(ip) for ip in ips}

    LABEL = {
        "ok": "БҮРЭН БЭЛЭН",
        "auth": "сүлжээ хэвийн · НЭВТРЭЛТ БУРУУ",
        "net": "СҮЛЖЭЭГЭЭР ХҮРЭХГҮЙ",
    }
    print("\n═══ ДҮГНЭЛТ ═══")
    for ip, r in results.items():
        print(f"  {ip:16} {LABEL[r]}")
    if any(r == "auth" for r in results.values()):
        print("\n  → Нэвтрэлт буруу камеруудад нууц үгийг нь UI-аас (Тохиргоо →")
        print("    Төхөөрөмж) камер тус бүрд оруулна уу.")
    if any(r == "net" for r in results.values()):
        print("\n  → Хүрэхгүй байгаа IP-ууд нь сүлжээний асуудал (маршрут/VLAN/")
        print("    firewall эсвэл камер асаагүй). Нууц үгтэй хамаагүй.")
    return 0 if all(r == "ok" for r in results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
