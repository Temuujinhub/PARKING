#!/usr/bin/env python3
"""ONVIF-ээр зураг авах боломжийг шалгах — Dahua CGI-ийн зургийн зам эвдэрсэн үед.

ЯАГААД (2026-08-14-ний баримт):
  • event стрим Test Capture-ээр БАТАЛГААТАЙ event өгсөн ч JPEG ирээгүй (0)
  • snapshot.cgi «татгалзах» төлөвт орох үедээ 400 (зөвхөн зургийн дэд систем)
  • snapManager / mediaFileFind / RPC2 зургийн методууд бүгд хаалттай
  ГЭТЭЛ ONVIF нь Dahua-гийн CGI-гээс ТУСДАА дэд систем (media service) бөгөөд
  камерын вэб UI-д «ONVIF User» гэсэн тусдаа данс ч байдаг. Хэрэв CGI-ийн
  зургийн зам эвдэрч байхад ONVIF-ийнх ажиллавал — тэр бол шийдэл.

Юу хийх вэ:
  1. Мэдэгдэж буй ONVIF snapshot URL-уудыг шууд оролдоно (хамгийн хямд)
  2. Жинхэнэ ONVIF SOAP: GetProfiles → GetSnapshotUri → тэр URL-аас татна
  3. Зураг авч чадвал /tmp/onvif_snap_<ip>.jpg болгож хадгална

Ажиллуулах:
    sudo /root/PARKING/backend/venv/bin/python /root/PARKING/tools/onvif_snap_probe.py 10.0.106.10
    # ONVIF-ийн данс өөр бол:
    sudo ... /root/PARKING/tools/onvif_snap_probe.py 10.0.106.10 onvifuser НууцҮг

Камерын вэб tab-уудыг ХААХ хэрэгтэй (Dahua цөөн холболт л зөвшөөрдөг).
"""
import asyncio
import base64
import hashlib
import os
import secrets
import sys
from datetime import datetime, timezone

os.chdir("/root/PARKING/backend")  # config-ийн env_file=".env" нь CWD-д харьцангуй
sys.path.insert(0, "/root/PARKING/backend")

import httpx  # noqa: E402

# ONVIF-ийн түгээмэл snapshot замууд (SOAP-гүйгээр шууд оролдоно)
DIRECT_URLS = [
    "onvif-http/snapshot?Profile_1",
    "onvif-http/snapshot?Profile_000",
    "onvif/snapshot",
    "onvif-cgi/media/snapshot?Profile_1",
    "cgi-bin/onvifsnapshot.cgi?channel=1",
]

ONVIF_PORTS = (80, 8000, 8899)   # Dahua ихэвчлэн 80, зарим загвар 8000/8899


def creds_for(ip: str):
    """DB-д бүртгэсэн камерын нэвтрэлт. Олдохгүй бол (None, None, шалтгаан)."""
    try:
        from app.database import SessionLocal
        from app.models import Device
        from app.services.device_auth import camera_credentials
        db = SessionLocal()
        try:
            dev = (db.query(Device)
                   .filter(Device.ip_address == ip, Device.device_type == "camera")
                   .filter(Device.status != "deleted").first())
            if dev is not None:
                return (*camera_credentials(dev), f"DB «{dev.name}»")
        finally:
            db.close()
    except Exception as e:  # noqa: BLE001
        return None, None, f"DB лукап бүтсэнгүй: {type(e).__name__}: {str(e)[:70]}"
    return None, None, "камер DB-д олдсонгүй"


def ws_security(user: str, pwd: str) -> str:
    """ONVIF-ийн WS-UsernameToken (digest) толгой.

    PasswordDigest = base64(SHA1(nonce + created + password)) — ONVIF стандарт."""
    nonce = secrets.token_bytes(16)
    created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    digest = base64.b64encode(
        hashlib.sha1(nonce + created.encode() + pwd.encode()).digest()).decode()
    return f"""<s:Header><Security s:mustUnderstand="1"
   xmlns="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd">
   <UsernameToken><Username>{user}</Username>
   <Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest">{digest}</Password>
   <Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary">{base64.b64encode(nonce).decode()}</Nonce>
   <Created xmlns="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd">{created}</Created>
   </UsernameToken></Security></s:Header>"""


def soap(body: str, user: str, pwd: str) -> str:
    return (f'<?xml version="1.0" encoding="UTF-8"?>'
            f'<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">'
            f'{ws_security(user, pwd)}<s:Body>{body}</s:Body></s:Envelope>')


def tag(xml: str, name: str) -> str | None:
    """Namespace-ээс үл хамааран эхний <...name>утга</...name>-г авна."""
    import re
    m = re.search(rf"<(?:\w+:)?{name}[^>]*>(.*?)</(?:\w+:)?{name}>", xml, re.S)
    return m.group(1).strip() if m else None


def all_tags(xml: str, name: str) -> list[str]:
    import re
    return [m.strip() for m in
            re.findall(rf'(?:\w+:)?{name}[^>]*token="([^"]+)"', xml)]


async def try_direct(ip: str, creds) -> bool:
    print("\n── 1. ONVIF-ийн шууд snapshot URL-ууд")
    auth = httpx.DigestAuth(*creds)
    async with httpx.AsyncClient(timeout=httpx.Timeout(5, read=15)) as c:
        for port in ONVIF_PORTS:
            for path in DIRECT_URLS:
                url = f"http://{ip}:{port}/{path}" if port != 80 else f"http://{ip}/{path}"
                try:
                    r = await c.get(url, auth=auth)
                except Exception as e:  # noqa: BLE001
                    continue
                ok = r.status_code == 200 and r.content[:2] == b"\xff\xd8"
                if ok:
                    out = f"/tmp/onvif_snap_{ip}.jpg"
                    with open(out, "wb") as f:
                        f.write(r.content)
                    print(f"   🎉 {url}")
                    print(f"      {len(r.content) // 1024}KB JPEG → {out}")
                    return True
                if r.status_code not in (404, 401):
                    print(f"   ·  {url:<52} HTTP {r.status_code}")
                await asyncio.sleep(0.4)
    print("   Шууд URL-аар зураг гарсангүй.")
    return False


async def try_soap(ip: str, user: str, pwd: str) -> bool:
    print("\n── 2. ONVIF SOAP: GetProfiles → GetSnapshotUri")
    hdr = {"Content-Type": "application/soap+xml; charset=utf-8"}
    async with httpx.AsyncClient(timeout=httpx.Timeout(8, read=20)) as c:
        for port in ONVIF_PORTS:
            base = f"http://{ip}:{port}" if port != 80 else f"http://{ip}"
            for svc in ("/onvif/media_service", "/onvif/Media", "/onvif/device_service"):
                body = ('<GetProfiles xmlns="http://www.onvif.org/ver10/media/wsdl"/>')
                try:
                    r = await c.post(base + svc, content=soap(body, user, pwd), headers=hdr)
                except Exception:  # noqa: BLE001
                    continue
                if r.status_code != 200 or "Profiles" not in r.text:
                    if r.status_code not in (404, 400):
                        print(f"   ·  {base + svc:<44} HTTP {r.status_code}")
                    continue
                tokens = all_tags(r.text, "Profiles")
                print(f"   ✅ {base + svc} → профайл: {tokens or '(токен уншигдсангүй)'}")
                for tok in (tokens or ["Profile_1"]):
                    body2 = ('<GetSnapshotUri xmlns="http://www.onvif.org/ver10/media/wsdl">'
                             f'<ProfileToken>{tok}</ProfileToken></GetSnapshotUri>')
                    r2 = await c.post(base + svc, content=soap(body2, user, pwd), headers=hdr)
                    uri = tag(r2.text, "Uri")
                    if not uri:
                        print(f"      · {tok}: URI гарсангүй (HTTP {r2.status_code})")
                        continue
                    print(f"      snapshot URI: {uri}")
                    try:
                        r3 = await c.get(uri, auth=httpx.DigestAuth(user, pwd))
                    except Exception as e:  # noqa: BLE001
                        print(f"      ❌ татаж чадсангүй: {type(e).__name__}")
                        continue
                    if r3.status_code == 200 and r3.content[:2] == b"\xff\xd8":
                        out = f"/tmp/onvif_snap_{ip}.jpg"
                        with open(out, "wb") as f:
                            f.write(r3.content)
                        print(f"      🎉 {len(r3.content) // 1024}KB JPEG → {out}")
                        return True
                    print(f"      ❌ HTTP {r3.status_code} {r3.content[:40]!r}")
    print("   SOAP-аар зураг гарсангүй.")
    return False


async def main(ip: str, user: str | None, pwd: str | None):
    src = "гараар өгсөн"
    if not user:
        user, pwd, src = creds_for(ip)
        if not user:
            print(f"⛔ {src} — ОРОЛДОХГҮЙ (буруу нэвтрэлт камерыг түгжинэ).")
            return
    print(f"=== {ip} — ONVIF зургийн судалгаа ===")
    print(f"Нэвтрэлт: {user} ({src})")

    got = await try_direct(ip, (user, pwd))
    if not got:
        got = await try_soap(ip, user, pwd)

    print()
    if got:
        print("🎉 ONVIF-ЭЭР ЗУРАГ АВЧ ЧАДЛАА — snapshot.cgi-г орлуулах боломжтой.")
        print("   Дараагийн алхам: snapshot.py-д ONVIF замыг нэмж, эхний сонголт болгох.")
    else:
        print("ONVIF-ээр ч зураг гарсангүй.")
        print("Хэрэв камерын вэб UI → System → Account → «ONVIF User» табд тусдаа")
        print("данс байвал түүгээр дахин туршина уу:")
        print(f"   sudo ... onvif_snap_probe.py {ip} <onvif_нэр> <нууц үг>")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    _u = sys.argv[2] if len(sys.argv) > 2 else None
    _p = sys.argv[3] if len(sys.argv) > 3 else None
    asyncio.run(main(sys.argv[1], _u, _p))
