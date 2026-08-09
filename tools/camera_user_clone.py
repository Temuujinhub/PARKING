#!/usr/bin/env python3
"""ГАРААР үүсгэсэн камерын хэрэглэгчийг ШАЛГАЖ, бусад камерт ХУУЛАХ.

Юуны учир (2026-08-10):
  Гуравдагч систем (172.16.100.20) 30 секунд тутам `admin`-аар нэвтэрч сешнээ
  ХААДАГГҮЙ. Слот дүүрэхэд манай зураг татах/хаалт нээх хүсэлт «Bad Request»
  авдаг. Шийдэл — манай систем ТУСДАА хэрэглэгчтэй болох.
  `userManager.addUser` энэ firmware дээр 609 өгч бүтэлгүйтсэн (веб UI нь
  шифрлэсэн `Security.addUserPlain` ашигладаг). Гэвч 10.0.106.10 дээр ГАРААР
  нэг хэрэглэгч үүсгэсэн тул түүнийг УНШААД яг тэр бүтцээр бусад камерт
  илгээж үзэх боломж нээгдэв — firmware-ийн хүлээж буй талбарууд ил болно.

Гурван горим:
  --show    Камер дээрх хэрэглэгчийн БҮТЭН объектыг харуулах (юу ч өөрчлөхгүй)
  --check   Тухайн нэвтрэлтээр манай системд ХЭРЭГТЭЙ бүх үйлдэл хийгдэж
            байгаа эсэхийг шалгах (хаалт НЭЭХГҮЙ — зөвхөн эрхийг шалгана)
  --clone   Эх камерын хэрэглэгчийн бүтцийг загвар болгон бусад камерт үүсгэх

Жишээ:
    # 1. easys хэрэглэгч ЯМАР бүтэцтэй үүссэнийг харах
    sudo /root/PARKING/backend/venv/bin/python /root/PARKING/tools/camera_user_clone.py \
        --show 10.0.106.10

    # 2. Тэр нэвтрэлтээр БҮГД ажиллаж байна уу (хаалт/зураг/event)
    sudo ... camera_user_clone.py --check 10.0.106.10

    # 3. Бусад камерт хуулах (эхлээд ХУУРАЙ — юу ч бичихгүй)
    sudo ... camera_user_clone.py --clone --from 10.0.106.10 --all
    sudo ... camera_user_clone.py --clone --from 10.0.106.10 --all --apply

Аюулгүй байдал: --apply өгөхгүй бол DB-д ч камерт ч юу ч бичихгүй. Шинэ
хэрэглэгчээр НЭВТЭРЧ БАТАЛГААЖСАН тохиолдолд л DB-д нэвтрэлтийг хадгална
(эс бөгөөс камертайгаа холбоогоо тасална). Нууц үг DB-д шифрлэгдэнэ.
"""
import argparse
import asyncio
import getpass
import json
import os
import sys

BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
sys.path.insert(0, BACKEND)
os.chdir(BACKEND)

import httpx  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import Device  # noqa: E402
from app.secretbox import encrypt_secret  # noqa: E402
from app.services.barrier import DahuaRpc  # noqa: E402
from app.services.device_auth import camera_credentials  # noqa: E402

# Камерын хариунаас ХАСАХ талбарууд — эдгээр нь тухайн камерын дотоод утга тул
# өөр камерт дамжуулбал татгалзах эрсдэлтэй.
DROP_FIELDS = {"Id", "Password", "PasswordModifiedTime", "LoginFailedTime",
               "UnlockTime", "PwdScore", "PasswordScore"}


async def user_objects(ip: str, user: str, pwd: str) -> tuple[list, list]:
    """(бүртгэлтэй хэрэглэгчид, идэвхтэй сешн) — RPC2-оор."""
    async with httpx.AsyncClient(timeout=15) as c:
        rpc = DahuaRpc(c, ip, user, pwd)
        await rpc.login()
        try:
            r = await rpc._call("userManager.getUserInfoAll")
            users = (r.get("params") or {}).get("users") or []
            try:
                a = await rpc._call("userManager.getActiveUserInfoAll")
                active = (a.get("params") or {}).get("users") or []
            except Exception:  # noqa: BLE001
                active = []
            return users, active
        finally:
            await rpc.logout()


def print_user(u: dict, mark: str = "") -> None:
    print(f"  ── {u.get('Name')!r} {mark}")
    print(f"     бүлэг={u.get('Group')}  Sharable={u.get('Sharable')}  "
          f"Reserved={u.get('Reserved')}  Memo={str(u.get('Memo'))[:40]!r}")
    auth = u.get("AuthorityList")
    if auth:
        print(f"     Эрх ({len(auth)}): {', '.join(map(str, auth))}")
    extra = {k: v for k, v in u.items()
             if k not in ("Name", "Group", "Sharable", "Reserved", "Memo",
                          "AuthorityList", "Password")}
    if extra:
        print(f"     Бусад талбар: {json.dumps(extra, ensure_ascii=False)[:200]}")


async def cmd_show(ip: str) -> None:
    db = SessionLocal()
    try:
        dev = db.query(Device).filter(Device.ip_address == ip).first()
        user, pwd = camera_credentials(dev)
        name = dev.name if dev else "?"
    finally:
        db.close()
    print(f"=== {ip} ({name}) · нэвтрэх нэр {user!r} ===")
    users, active = await user_objects(ip, user, pwd)
    print(f"\nБүртгэлтэй хэрэглэгч: {len(users)}")
    for u in users:
        print_user(u, "  ← МАНАЙ СИСТЕМ ҮҮНИЙГ АШИГЛАЖ БАЙНА"
                   if u.get("Name") == user else "")
    if active:
        print(f"\nИдэвхтэй сешн: {len(active)}")
        by_name: dict[str, list] = {}
        for a in active:
            by_name.setdefault(str(a.get("Name")), []).append(a)
        for nm, rows in sorted(by_name.items(), key=lambda kv: -len(kv[1])):
            addrs = ", ".join(sorted({str(r.get("ClientAddress")) for r in rows}))
            print(f"  {nm:12} {len(rows):>3} сешн · {addrs}")
        ours = len(by_name.get(user, []))
        others = len(active) - ours
        print(f"\n  → Манайх ({user}): {ours} сешн · бусад: {others} сешн")
        if ours and others:
            print("  ✅ Сешний слот ТУСГААРЛАГДЛАА — гадны систем манай слотыг "
                  "дүүргэхээ болино.")
    print("\nЭнэ бүтцийг бусад камерт хуулах:")
    print(f"  sudo ... camera_user_clone.py --clone --from {ip} --all")


async def cmd_check(ip: str) -> None:
    """Манай системд хэрэгтэй БҮХ үйлдлийг тухайн нэвтрэлтээр туршина."""
    db = SessionLocal()
    try:
        dev = db.query(Device).filter(Device.ip_address == ip).first()
        user, pwd = camera_credentials(dev)
        name = dev.name if dev else "?"
        lane = getattr(dev, "lane_no", None) or 1
    finally:
        db.close()
    print(f"=== {ip} ({name}) · нэвтрэлт {user!r} эрхийн шалгалт ===\n")
    ok, fail = [], []

    def mark(label: str, good: bool, detail: str = "") -> None:
        (ok if good else fail).append(label)
        print(f"  {'✓' if good else '✗'} {label:38} {detail}")

    async with httpx.AsyncClient(timeout=20) as c:
        rpc = DahuaRpc(c, ip, user, pwd)
        try:
            await rpc.login()
            mark("RPC2 нэвтрэлт", True)
        except Exception as e:  # noqa: BLE001
            mark("RPC2 нэвтрэлт", False, str(e)[:120])
            print("\n  ⛔ Нэвтэрч чадсангүй — цааш шалгах боломжгүй.")
            return
        try:
            # 1. Хаалт нээх эрх — instance үүсгэнэ, хаалтыг НЭЭХГҮЙ
            try:
                r = await rpc._call("trafficSnap.factory.instance", {"channel": lane - 1})
                mark("ХААЛТ нээх эрх (trafficSnap)", bool(r.get("result")),
                     "" if r.get("result") else str(r.get("error"))[:80])
            except Exception as e:  # noqa: BLE001
                mark("ХААЛТ нээх эрх (trafficSnap)", False, str(e)[:80])
            # 2. LED дэлгэц — байгаа эсэхийг шалгана (бичихгүй)
            for label, method in (("Дэлгэцийн эрх", "trafficParking.getScreenDisplay"),
                                  ("Системийн мэдээлэл", "magicBox.getSoftwareVersion"),
                                  ("Хэрэглэгч унших", "userManager.getUserInfoAll")):
                try:
                    r = await rpc._call(method)
                    err = (r.get("error") or {})
                    # «Method not found» гэдэг нь ЭРХИЙН асуудал БИШ — firmware дэмжихгүй
                    unsupported = "not found" in str(err.get("message", "")).lower()
                    mark(label, bool(r.get("result")) or unsupported,
                         "(firmware дэмжихгүй — эрхийн асуудал биш)" if unsupported
                         else ("" if r.get("result") else str(err)[:80]))
                except Exception as e:  # noqa: BLE001
                    mark(label, False, str(e)[:80])
        finally:
            await rpc.logout()

        # 3. CGI сувгууд — DigestAuth
        auth = httpx.DigestAuth(user, pwd)
        try:
            r = await c.get(f"http://{ip}/cgi-bin/snapshot.cgi", auth=auth,
                            timeout=httpx.Timeout(5, read=25))
            good = r.status_code == 200 and r.content[:2] == b"\xff\xd8"
            mark("snapshot.cgi (зураг)", good,
                 f"{len(r.content):,} байт" if good else f"HTTP {r.status_code}")
        except Exception as e:  # noqa: BLE001
            mark("snapshot.cgi (зураг)", False, type(e).__name__)

        for label, path in (
                ("eventManager.cgi (event урсгал)",
                 "eventManager.cgi?action=attach&codes=[All]&heartbeat=5"),
                ("snapManager.cgi (зургийн урсгал)",
                 "snapManager.cgi?action=attachFileProc&Flags[0]=Event"
                 "&Flags[1]=Manual&Events=[All]&heartbeat=5")):
            try:
                async with c.stream("GET", f"http://{ip}/cgi-bin/{path}", auth=auth,
                                    timeout=httpx.Timeout(10, read=8)) as r:
                    good = r.status_code == 200
                    mark(label, good, r.headers.get("content-type", "")[:40]
                         if good else f"HTTP {r.status_code}")
            except httpx.ReadTimeout:
                # Холбогдсон ч энэ 8 секундэд машин өнгөрөөгүй — ЭНЭ НЬ ЗҮГЭЭР
                mark(label, True, "холбогдсон (энэ хугацаанд event гараагүй)")
            except Exception as e:  # noqa: BLE001
                mark(label, False, type(e).__name__)

    print(f"\n  Дүн: {len(ok)} ажиллаж байна, {len(fail)} ажиллахгүй")
    if fail:
        print("  ⚠ Ажиллахгүй байгаа: " + ", ".join(fail))
        print("  → Камерын веб → System → Account → тухайн хэрэглэгчийг засаж")
        print("    эрхийн жагсаалтыг admin-тай ИЖИЛ болгоно уу.")
    else:
        print("  ✅ Манай системд хэрэгтэй бүх үйлдэл энэ нэвтрэлтээр ажиллаж байна.")


async def clone_to(ip: str, name: str, src_user: dict, new_pw: str,
                   apply: bool) -> tuple[str, str]:
    """(төлөв, тайлбар) — 'skip' | 'ok' | 'fail'."""
    uname = str(src_user.get("Name"))
    db = SessionLocal()
    try:
        devices = db.query(Device).filter(Device.ip_address == ip,
                                          Device.status != "deleted").all()
        if not devices:
            return "fail", "төхөөрөмж бүртгэлгүй"
        cur_user, cur_pwd = camera_credentials(devices[0])
    finally:
        db.close()

    async with httpx.AsyncClient(timeout=20) as c:
        rpc = DahuaRpc(c, ip, cur_user, cur_pwd)
        try:
            await rpc.login()
        except Exception as e:  # noqa: BLE001
            return "fail", f"нэвтэрч чадсангүй: {str(e)[:90]}"
        try:
            r = await rpc._call("userManager.getUserInfoAll")
            existing = {str(u.get("Name")) for u in
                        ((r.get("params") or {}).get("users") or [])}
            if uname in existing and cur_user == uname:
                return "skip", f"«{uname}» аль хэдийн байна, систем түүгээр ханддаг"
            payload = {k: v for k, v in src_user.items() if k not in DROP_FIELDS}
            payload["Password"] = new_pw
            if not apply:
                return "skip", f"ХУУРАЙ — «{uname}» үүсгэнэ ({len(payload)} талбар)"
            method = "userManager.modifyUser" if uname in existing else "userManager.addUser"
            params = ({"name": uname, "user": payload, "pwdModified": True}
                      if uname in existing else {"user": payload})
            res = await rpc._call(method, params)
            if not res.get("result"):
                err = res.get("error") or {}
                return "fail", (f"{method} → code={err.get('code')} "
                                f"{str(err.get('message', ''))[:60]}")
        finally:
            await rpc.logout()

    # Шинэ хэрэглэгчээр НЭВТЭРЧ БАТАЛГААЖУУЛНА — эс бөгөөс DB-д бичихгүй
    await asyncio.sleep(1.5)
    async with httpx.AsyncClient(timeout=15) as c2:
        t = DahuaRpc(c2, ip, uname, new_pw)
        try:
            await t.login()
            await t.logout()
        except Exception as e:  # noqa: BLE001
            return "fail", (f"үүссэн ч нэвтэрч чадсангүй ({str(e)[:70]}) — "
                            f"DB-д БИЧСЭНГҮЙ")

    db = SessionLocal()
    try:
        enc = encrypt_secret(new_pw)
        n = 0
        for d in db.query(Device).filter(Device.ip_address == ip,
                                         Device.status != "deleted").all():
            d.username, d.password = uname, enc
            n += 1
        db.commit()
    finally:
        db.close()
    return "ok", f"«{uname}» үүсэж баталгаажлаа, {n} төхөөрөмжид хадгалав"


async def cmd_clone(args) -> None:
    src = args.src
    db = SessionLocal()
    try:
        d = db.query(Device).filter(Device.ip_address == src).first()
        s_user, s_pwd = camera_credentials(d)
        if args.all:
            devs = (db.query(Device)
                    .filter(Device.device_type == "camera", Device.status == "active",
                            Device.ip_address.isnot(None), Device.ip_address != "")
                    .all())
            seen, targets = {src}, []
            for x in devs:
                if x.ip_address in seen:
                    continue
                seen.add(x.ip_address)
                targets.append((x.ip_address, x.name))
        else:
            targets = [(ip, "") for ip in args.to]
    finally:
        db.close()
    if not targets:
        print("Зорилтот камер алга (--all эсвэл --to IP ...)")
        return

    print(f"=== Загвар: {src} · хэрэглэгч {args.user or s_user!r} ===")
    users, _ = await user_objects(src, s_user, s_pwd)
    want = args.user or s_user
    src_user = next((u for u in users if str(u.get("Name")) == want), None)
    if not src_user:
        print(f"✗ «{want}» хэрэглэгч {src} дээр олдсонгүй. Байгаа: "
              + ", ".join(str(u.get("Name")) for u in users))
        return
    print_user(src_user, "← ЗАГВАР")

    pw = args.password or os.environ.get("EP_CAM_PW") or getpass.getpass(
        f"«{want}»-ийн нууц үг (камер дээр тавьсан): ")
    if not pw:
        print("Нууц үг хоосон — зогслоо.")
        return

    print(f"\n{len(targets)} камерт хуулна"
          + ("" if args.apply else "  ⚠ ХУУРАЙ ГОРИМ (--apply өгвөл бодитоор хийнэ)"))
    tally = {"ok": 0, "skip": 0, "fail": 0}
    for ip, nm in targets:
        try:
            state, msg = await clone_to(ip, nm, src_user, pw, args.apply)
        except Exception as e:  # noqa: BLE001
            state, msg = "fail", f"{type(e).__name__}: {str(e)[:80]}"
        tally[state] += 1
        icon = {"ok": "✓", "skip": "·", "fail": "✗"}[state]
        print(f"  {icon} {ip:16} {nm[:22]:24} {msg}")
        await asyncio.sleep(1)   # камеруудыг зэрэг цохихгүй

    print(f"\nДүн: {tally['ok']} амжилттай · {tally['skip']} алгассан · "
          f"{tally['fail']} амжилтгүй")
    if tally["fail"]:
        print("\nАмжилтгүй камерууд дээр веб UI-аар ГАРААР үүсгэнэ үү:")
        print(f"  System → Account → Add → нэр «{want}», бүлэг admin, ижил нууц үг")
        print("  Дараа нь Тохиргоо → Төхөөрөмж дээр нэвтрэлтийг оруулна.")
    if tally["ok"] and args.apply:
        print("\nДараагийн алхам — камер бүр дээр эрхийг шалгах:")
        print("  sudo ... camera_user_clone.py --check <IP>")
        print("  Дараа нь backend-ийг дахин ачаална: sudo systemctl restart parking-api")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--show", metavar="IP", help="Хэрэглэгчийн бүтцийг харах")
    g.add_argument("--check", metavar="IP", help="Эрх хүрэлцэж байгаа эсэхийг шалгах")
    g.add_argument("--clone", action="store_true", help="Бусад камерт хуулах")
    ap.add_argument("--from", dest="src", metavar="IP", help="Загвар авах камер")
    ap.add_argument("--to", nargs="*", default=[], metavar="IP", help="Зорилтот камерууд")
    ap.add_argument("--all", action="store_true", help="Бүх идэвхтэй камер")
    ap.add_argument("--user", help="Хуулах хэрэглэгчийн нэр (default: DB дэх нэвтрэх нэр)")
    ap.add_argument("--password", help="Нууц үг (өгөхгүй бол асууна; EP_CAM_PW ч болно)")
    ap.add_argument("--apply", action="store_true", help="Бодитоор үүсгэж, DB-д хадгалах")
    args = ap.parse_args()

    if args.clone and not args.src:
        ap.error("--clone горимд --from IP заавал")
    if args.show:
        asyncio.run(cmd_show(args.show))
    elif args.check:
        asyncio.run(cmd_check(args.check))
    else:
        asyncio.run(cmd_clone(args))


if __name__ == "__main__":
    main()
