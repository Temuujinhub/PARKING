#!/usr/bin/env python3
"""userManager.addUser-ийн ЯМАР хэлбэр энэ firmware дээр ажиллахыг олох.

2026-08-10: 20 камер дээр addUser нь `error code 609` өгч бүтсэнгүй. Dahua-гийн
шинэ firmware (Security Mode) нь нууц үгийг ТҮҮХИЙ биш ХЭШЛЭСЭН хэлбэрээр
хүлээж авдаг бөгөөд AuthorityList/талбарын бүтэц ч firmware бүрд өөр байдаг.

Энэ хэрэгсэл нэг камер дээр хувилбаруудыг дараалан туршиж, аль нь 200 өгөхийг
олно. АМЖИЛТТАЙ болмогц тэр хэрэглэгчийг НЭН ДАРУЙ УСТГАНА (--keep өгвөл
үлдээнэ) — production камерт хог үлдээхгүй.

    sudo /root/PARKING/backend/venv/bin/python /root/PARKING/tools/camera_user_probe.py 10.0.106.10
    sudo ... camera_user_probe.py 10.0.106.10 --keep     # ажилласан хэрэглэгчийг үлдээх
"""
import argparse
import asyncio
import hashlib
import json
import os
import secrets
import string
import sys

BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
sys.path.insert(0, BACKEND)
os.chdir(BACKEND)

import httpx  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import Device  # noqa: E402
from app.services.barrier import DahuaRpc  # noqa: E402
from app.services.device_auth import camera_credentials  # noqa: E402

PROBE_USER = "eptest"


def md5u(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest().upper()


def gen_pw() -> str:
    pool = string.ascii_letters + string.digits
    while True:
        pw = "".join(secrets.choice(pool) for _ in range(12))
        if (any(c.islower() for c in pw) and any(c.isupper() for c in pw)
                and any(c.isdigit() for c in pw)):
            return pw


async def get_realm(client, ip: str, user: str) -> str | None:
    """global.login-ийн эхний алхмаас realm-ыг авна (нууц үг хэшлэхэд хэрэгтэй)."""
    try:
        r = await client.post(f"http://{ip}/RPC2_Login", json={
            "method": "global.login", "id": 1,
            "params": {"userName": user, "password": "",
                       "clientType": "Web3.0", "loginType": "Direct"}})
        return (r.json().get("params") or {}).get("realm")
    except Exception:  # noqa: BLE001
        return None


def variants(pw: str, realm: str | None) -> list:
    """(нэр, user-объект) хосууд — энгийнээс нарийн руу."""
    base_auth = ["Monitor_01", "PlayBack_01", "SystemInfo", "Event", "Storage",
                 "Record", "Network"]
    hashed = md5u(f"{PROBE_USER}:{realm}:{pw}") if realm else None
    out = [
        ("V1 энгийн нууц үг + AuthorityList",
         {"Name": PROBE_USER, "Password": pw, "Group": "admin",
          "Memo": "probe", "Sharable": True, "Reserved": False,
          "AuthorityList": base_auth}),
        ("V2 энгийн, AuthorityList-гүй",
         {"Name": PROBE_USER, "Password": pw, "Group": "admin",
          "Memo": "probe", "Sharable": True, "Reserved": False}),
        ("V3 хамгийн бага талбар",
         {"Name": PROBE_USER, "Password": pw, "Group": "user"}),
    ]
    if hashed:
        out += [
            ("V4 MD5(нэр:realm:нууцүг) + AuthorityList",
             {"Name": PROBE_USER, "Password": hashed, "Group": "admin",
              "Memo": "probe", "Sharable": True, "Reserved": False,
              "AuthorityList": base_auth}),
            ("V5 MD5(нэр:realm:нууцүг), AuthorityList-гүй",
             {"Name": PROBE_USER, "Password": hashed, "Group": "admin",
              "Memo": "probe", "Sharable": True, "Reserved": False}),
            ("V6 MD5 + PasswordType",
             {"Name": PROBE_USER, "Password": hashed, "Group": "admin",
              "Memo": "probe", "Sharable": True, "Reserved": False,
              "PasswordType": "Default", "AuthorityList": base_auth}),
        ]
    out.append(("V7 MD5(нууцүг) энгийн",
                {"Name": PROBE_USER, "Password": md5u(pw), "Group": "admin",
                 "Memo": "probe", "Sharable": True, "Reserved": False,
                 "AuthorityList": base_auth}))
    return out


async def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ip")
    ap.add_argument("--keep", action="store_true",
                    help="Ажилласан хэрэглэгчийг устгахгүй үлдээх")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        dev = db.query(Device).filter(Device.ip_address == args.ip).first()
        user, pwd = camera_credentials(dev)
    finally:
        db.close()

    pw = gen_pw()
    print(f"=== {args.ip} · одоогийн нэвтрэлт {user!r} ===")
    async with httpx.AsyncClient(timeout=20) as client:
        realm = await get_realm(client, args.ip, PROBE_USER)
        print(f"realm: {realm!r}")
        rpc = DahuaRpc(client, args.ip, user, pwd)
        await rpc.login()
        print("RPC2 нэвтрэлт OK\n")
        winner = None
        try:
            for label, u in variants(pw, realm):
                # Өмнөх оролдлогын үлдэгдлийг цэвэрлэнэ
                try:
                    await rpc._call("userManager.deleteUser", {"name": PROBE_USER})
                except Exception:  # noqa: BLE001
                    pass
                try:
                    r = await rpc._call("userManager.addUser", {"user": u})
                except Exception as e:  # noqa: BLE001
                    print(f"  {label:44} АЛДАА {type(e).__name__}")
                    continue
                ok = bool(r.get("result"))
                err = (r.get("error") or {})
                print(f"  {label:44} {'✅ АМЖИЛТТАЙ' if ok else '✗ code=' + str(err.get('code', '?'))}"
                      + (f" {err.get('message', '')[:40]}" if err.get("message") else ""))
                if ok:
                    winner = (label, u)
                    break

            if not winner:
                print("\n  Нэг ч хувилбар ажиллаагүй. Дараагийн алхам: камерын веб UI-д")
                print("  DevTools → Network нээгээд ГАРААР хэрэглэгч үүсгэж, /RPC2 руу")
                print("  явсан addUser хүсэлтийн Payload-ыг хуулж өгвөл яг тэр бүтцээр хийнэ.")
                return

            # Шинэ хэрэглэгчээр нэвтэрч БАТАЛГААЖУУЛНА
            print(f"\n  Ажилласан хувилбар: {winner[0]}")
            await asyncio.sleep(1.5)
            async with httpx.AsyncClient(timeout=15) as c2:
                t = DahuaRpc(c2, args.ip, PROBE_USER, pw)
                try:
                    await t.login()
                    await t.logout()
                    print(f"  ✅ «{PROBE_USER}» хэрэглэгчээр нэвтрэлт БАТАЛГААЖЛАА "
                          f"(нууц үг: {pw})")
                    print("  → camera_add_user.py-г энэ хувилбараар шинэчилж, "
                          "бүх камерт хэрэглэнэ.")
                except Exception as e:  # noqa: BLE001
                    print(f"  ⚠ Үүссэн ч нэвтэрч чадсангүй: {str(e)[:120]}")
                    print("    (нууц үг хэшлэсэн хэлбэрээр хадгалагдсан байж болно — "
                          "өөр хувилбар хэрэгтэй)")
            print(f"\n  Хэрэглэсэн user объект:\n  {json.dumps(winner[1], ensure_ascii=False)}")
        finally:
            if winner and not args.keep:
                try:
                    r = await rpc._call("userManager.deleteUser", {"name": PROBE_USER})
                    print(f"\n  Туршилтын хэрэглэгч устгав "
                          f"({'OK' if r.get('result') else r})")
                except Exception as e:  # noqa: BLE001
                    print(f"\n  ⚠ Туршилтын хэрэглэгчийг устгаж чадсангүй: {e}")
                    print(f"    Камерын веб → Данс хэсгээс «{PROBE_USER}»-г ГАРААР устгана уу.")
            await rpc.logout()


if __name__ == "__main__":
    asyncio.run(main())
