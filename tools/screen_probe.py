#!/usr/bin/env python
"""LED дэлгэцийн API-г камераас ӨӨРӨӨС нь асуух оношилгоо.

    sudo /root/PARKING/backend/venv/bin/python /root/PARKING/tools/screen_probe.py 10.0.104.10

Юуны учир: одоо систем текстийг screen_repeat удаа ДАВТАЖ илгээдэг — камер
Vehicle Passing горимдоо манай текстийг дарж бичдэг учраас. Давталт бүр RPC2
сесс эзэлдэг тул хаалтны командтай мөргөлдөх магадлалыг нэмэгдүүлнэ.

Хэрэв камер «хэдэн секунд харуулах» параметр дэмждэг бол НЭГ л удаа илгээгээд
болно — мөргөлдөх магадлал 4 дахин буурна. Гэхдээ ямар параметр дэмждэгийг
ТААМАГЛАЖ болохгүй (загвар/firmware бүр өөр) тул энэ скрипт бодит камераас
асууж, дэмжигдсэн хувилбарыг олж хэлнэ.

Юу шалгах вэ:
  1. getScreenDisplay / getConfig — одоогийн бүтэц, ямар талбартай вэ
  2. setScreenDisplay-д нэмэлт хугацааны талбарууд (Time/HoldTime/Duration/KeepTime)
  3. ledServer.cgi?action=sendText&time=N — өөр firmware-ийн CGI зам
Аюулгүй байдал: оролдлого бүрийн хооронд завсарлага авна (камерын сесс/нэвтрэлтийг
шавхахгүй), бүх оролдлого ЗӨВХӨН уншиж/бичих ба хаалтад хүрэхгүй.
"""
import asyncio
import os
import sys

BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
sys.path.insert(0, BACKEND)
os.chdir(BACKEND)   # config-ийн env_file нь CWD-д харьцангуй

import httpx  # noqa: E402

from app.services.barrier import DahuaRpc  # noqa: E402
from app.services.device_auth import camera_credentials  # noqa: E402

TEXT = "ТУРШИЛТ 1234"
PAUSE = 1.5   # оролдлогуудын хоорондох завсарлага (камерыг ачаалахгүй)


def _device_for(ip: str):
    try:
        from app.database import SessionLocal
        from app.models import Device
        db = SessionLocal()
        try:
            return db.query(Device).filter(Device.ip_address == ip).first()
        finally:
            db.close()
    except Exception:  # noqa: BLE001 — DB байхгүй ч .env-ийн нэвтрэлтээр шалгана
        return None


async def probe(ip: str) -> None:
    creds = camera_credentials(_device_for(ip))
    print(f"\n═══ {ip} ═══  (нэвтрэх нэр: {creds[0]!r})")
    ok, maybe = [], []

    async with httpx.AsyncClient(timeout=10.0) as client:
        rpc = DahuaRpc(client, ip, *creds)
        try:
            await rpc.login()
        except Exception as e:  # noqa: BLE001
            print(f"  ✗ Нэвтэрч чадсангүй: {str(e)[:160]}")
            return
        print("  ✓ RPC2 нэвтрэлт амжилттай")

        try:
            # ── 1. Одоогийн бүтцийг унших ──
            for method in ("trafficParking.getScreenDisplay",
                           "trafficParking.getScreenDisplayCaps"):
                try:
                    res = await rpc._call(method)
                    if res.get("result"):
                        print(f"  ✓ {method} → {str(res.get('params'))[:300]}")
                        ok.append(method)
                    else:
                        print(f"  · {method}: дэмжихгүй")
                except Exception as e:  # noqa: BLE001
                    print(f"  · {method}: {type(e).__name__}")
                await asyncio.sleep(PAUSE)

            # ── 2. Хугацааны параметрийн хувилбарууд ──
            print("\n  Хугацааны параметр туршиж байна (дэлгэц дээр текст гарч болно):")
            variants = [
                ("Custom + Time",     {"Custom": TEXT, "Time": 30}),
                ("Custom + HoldTime", {"Custom": TEXT, "HoldTime": 30}),
                ("Custom + Duration", {"Custom": TEXT, "Duration": 30}),
                ("Custom + KeepTime", {"Custom": TEXT, "KeepTime": 30}),
                ("Custom + ShowTime", {"Custom": TEXT, "ShowTime": 30}),
            ]
            for name, params in variants:
                try:
                    res = await rpc._call("trafficParking.setScreenDisplay", params)
                    if res.get("result"):
                        print(f"    ~ {name}: ХҮЛЭЭН АВЛАА (алдаа өгсөнгүй)")
                        maybe.append(name)
                    else:
                        err = (res.get("error") or {}).get("message", "")
                        print(f"    ✗ {name}: татгалзав {err[:60]}")
                except Exception as e:  # noqa: BLE001
                    print(f"    ✗ {name}: {type(e).__name__}")
                await asyncio.sleep(PAUSE)

            # ── 2.5 Мөр таслалын хувилбарууд (3 мөрт харуулах) ──
            # Зарим firmware «\n»-ийг үл ойшоож бүх текстийг НЭГ мөрөнд урсгадаг.
            # Хувилбар бүрийг 6 секунд зайтай илгээнэ — LED-ийг НҮДЭЭР ажиглаж,
            # аль дугаартай нь 3 ТУСДАА мөрөнд гарснаар PARKING_SCREEN_LINE_BREAK-ийг
            # тохируулна.
            print("\n  Мөр таслалын хувилбарууд — LED-ийг ажиглана уу (3 мөр гарах ёстой):")
            breaks = [("1: \\n", "\n"), ("2: \\r\\n", "\r\n"), ("3: \\r", "\r"),
                      ("4: | тэмдэг", "|"), ("5: ; тэмдэг", ";")]
            for name, br in breaks:
                text3 = br.join([f"MUR-{name[0]} 1234ABC", "1ts 05min", "Tulbur 3000"])
                try:
                    res = await rpc._call("trafficParking.setScreenDisplay", {"Custom": text3})
                    print(f"    {name}: илгээв ({'OK' if res.get('result') else 'татгалзав'})"
                          f" — LED дээр 3 мөр гарсан эсэхийг тэмдэглэ")
                except Exception as e:  # noqa: BLE001
                    print(f"    {name}: {type(e).__name__}")
                await asyncio.sleep(6.0)
            print("    → 3 мөр гаргасан хувилбараа .env-д бичнэ:")
            print("      PARKING_SCREEN_LINE_BREAK='\\r\\n'   # (жишээ: 2-р хувилбар ажилласан бол)")

            # ── 2.6 МӨР ТУС БҮРИЙН API хайх ──
            # 2026-07-28 Monnis туршилт: дээрх БҮХ мөр таслал нэг мөрөнд урссан —
            # энэ firmware Custom-ыг дан урсдаг мөр гэж үздэг нь батлагдсан.
            # Тиймээс мөр тус бүрд тусдаа талбар/команд байгаа эсэхийг хайна.
            print("\n  Дэлгэцийн тохиргооны бүтцийг камераас асууж байна (зөвхөн унших):")
            for cfg in ("VSPDisplayScreen", "DisplayScreen", "TrafficScreen",
                        "ScreenDisplay", "LedScreen", "TrafficParkingScreen",
                        "InoutGeneralConfig", "VSPGeneral"):
                try:
                    res = await rpc._call("configManager.getConfig", {"name": cfg})
                    if res.get("result"):
                        print(f"    ✓ getConfig {cfg} → {str(res.get('params'))[:400]}")
                        ok.append(f"getConfig:{cfg}")
                except Exception as e:  # noqa: BLE001
                    print(f"    · {cfg}: {type(e).__name__}")
                await asyncio.sleep(0.8)
            # Бүтэн конфигоос дэлгэцтэй холбоотой түлхүүрүүдийг шүүж харуулна —
            # per-line бүтэц ЯГ ямар нэртэйг эндээс олно (таамаглахгүй)
            try:
                res = await rpc._call("configManager.getConfig", {"name": "All"})
                def _paths(node, prefix=""):
                    if isinstance(node, dict):
                        for k, v in node.items():
                            p = f"{prefix}.{k}" if prefix else str(k)
                            if any(s in str(k).lower() for s in ("screen", "led", "display")):
                                yield p, v
                            yield from _paths(v, p)
                    elif isinstance(node, list):
                        for i, v in enumerate(node):
                            yield from _paths(v, f"{prefix}[{i}]")
                found = list(_paths(res.get("params") or {}))
                if found:
                    print(f"\n  getConfig All дотор дэлгэцтэй холбоотой {len(found)} түлхүүр:")
                    for p, v in found[:25]:
                        print(f"    {p} = {str(v)[:200]}")
                    if len(found) > 25:
                        print(f"    ... (нийт {len(found)}, эхний 25-ыг харуулав)")
                else:
                    print("\n  getConfig All: дэлгэцтэй холбоотой түлхүүр олдсонгүй")
            except Exception as e:  # noqa: BLE001
                print(f"\n  getConfig All: {type(e).__name__}: {str(e)[:100]}")

            print("\n  МӨР ТУС БҮРИЙН хувилбарууд — LED-ийг ажиглаж, аль үсэгтэй нь")
            print("  ХЭДДҮГЭЭР мөрөнд гарсныг тэмдэглэнэ үү (6с зайтай):")
            line_variants = [
                ("A: Custom=жагсаалт", "trafficParking.setScreenDisplay",
                 {"Custom": ["MUR-A1", "MUR-A2", "MUR-A3"]}),
                ("B: Custom1/2/3", "trafficParking.setScreenDisplay",
                 {"Custom1": "MUR-B1", "Custom2": "MUR-B2", "Custom3": "MUR-B3"}),
                ("C: LineNo=2", "trafficParking.setScreenDisplay",
                 {"Custom": "MUR-C2", "LineNo": 2}),
                ("D: Line=2", "trafficParking.setScreenDisplay",
                 {"Custom": "MUR-D2", "Line": 2}),
                ("E: DisplayInfo", "trafficParking.setScreenDisplayInfo",
                 {"DisplayInfo": [{"LineNo": 1, "Content": "MUR-E1"},
                                  {"LineNo": 2, "Content": "MUR-E2"},
                                  {"LineNo": 3, "Content": "MUR-E3"}]}),
            ]
            for name, method, params in line_variants:
                try:
                    res = await rpc._call(method, params)
                    print(f"    {name}: {'OK' if res.get('result') else 'татгалзав'}"
                          f" — LED дээр юу гарав?")
                except Exception as e:  # noqa: BLE001
                    print(f"    {name}: {type(e).__name__}")
                await asyncio.sleep(6.0)
        finally:
            await rpc.logout()

    # ── 3. ledServer.cgi (өөр firmware-ийн зам) ──
    print("\n  ledServer.cgi шалгаж байна:")
    import urllib.parse
    url = (f"http://{ip}/cgi-bin/ledServer.cgi?action=sendText"
           f"&content={urllib.parse.quote(TEXT)}&time=30")
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(url, auth=httpx.DigestAuth(*creds))
        body = (r.text or "").strip()[:120]
        if r.status_code == 200 and "error" not in body.lower():
            print(f"    ✓ ledServer.cgi АЖИЛЛАЖ БАЙНА → {body!r}")
            ok.append("ledServer.cgi")
        else:
            print(f"    ✗ ledServer.cgi: HTTP {r.status_code} {body!r}")
    except Exception as e:  # noqa: BLE001
        print(f"    ✗ ledServer.cgi: {type(e).__name__}: {str(e)[:80]}")

    # ── Дүгнэлт ──
    print("\n  ── ДҮГНЭЛТ ──")
    if "ledServer.cgi" in ok:
        print("  ledServer.cgi дэмжигдэж байна → нэг команд + хугацаа боломжтой.")
    if maybe:
        print(f"  setScreenDisplay нэмэлт талбарыг татгалзсангүй: {', '.join(maybe)}")
        print("  АНХААР: «татгалзсангүй» гэдэг нь «ажилласан» гэсэн үг БИШ — Dahua нь")
        print("  танихгүй талбарыг чимээгүй УГЖ ХАЯДАГ. Дэлгэцийг НҮДЭЭР хараарай:")
        print(f"  текст «{TEXT}» 30 секунд ТОГТМОЛ харагдвал л тэр параметр ажилласан.")
        print("  Хэрэв 1-2 секундын дараа алга болвол → давталт хэвээр шаардлагатай.")
    if not maybe and "ledServer.cgi" not in ok:
        print("  Хугацааны параметр олдсонгүй → одоогийн ДАВТАХ арга зөв хэвээр.")
        print("  Мөргөлдөөнийг багасгах бол: PARKING_SCREEN_REPEAT=3,")
        print("  PARKING_SCREEN_REPEAT_INTERVAL=1.5 (нийт 4.5с сесс).")


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
