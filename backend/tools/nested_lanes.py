"""Дотоод (nested) камерын ЧИГЛЭЛ зөв эсэхийг НОТОЛГООГООР шалгаж, засах.

Яагаад чухал вэ: `nested_inner` камерын `lane_dir` нь тоолуурыг УДИРДДАГ —
    entry → доторх (үнэгүй) зогсоолд орлоо  → тоолуур ЗОГСОНО (pause)
    exit  → доторхоос гарч төлбөртэй талбарт → тоолуур ҮРГЭЛЖИЛНЭ (resume)
Хоёрыг нь СОЛЬЖ тохируулбал: гаднах ТӨЛБӨРТЭЙ талбарт зогссон машины тоолуур
зогсож (дутуу нэхэмжилнэ), доторх ҮНЭГҮЙ талбарт зогссоных нь ажиллана (илүү
нэхэмжилнэ). Хоёулаа чимээгүй — хаалт нээгдсээр байдаг тул гомдол ирэхгүй.

Ажиллуулах (сервер дээр, backend хавтаст):

    venv/bin/python tools/nested_lanes.py RASH
        → тохиргоо + сүүлийн 7 хоногийн уншилтаас чиглэлийг НОТОЛНО

    venv/bin/python tools/nested_lanes.py RASH --set 10.0.106.12=exit \
                                               --set 10.0.106.13=entry
        → ЮУ өөрчлөгдөхийг харуулна (dry-run, юу ч бичихгүй)

    venv/bin/python tools/nested_lanes.py RASH --set ... --apply
        → камер + хосолсон дотоод ХААЛТ-ын чиглэлийг зэрэг засна (AuditLog үлдэнэ)

    venv/bin/python tools/nested_lanes.py RASH --resume-open [--apply]
        → чиглэл солигдсоноос үүссэн ЯВЖ БУЙ зогсолтуудыг цуцлана (тоолуур
        дахин ажиллана). Засварын ДАРАА нэг удаа хийнэ.

    venv/bin/python tools/nested_lanes.py RASH --phantom 10.0.106.13 --hours 24
        → тэр камер яагаад машингүй үед event өгч байгааг задална
        (--camlog нэмбэл камерын ӨӨРИЙН бүртгэлтэй тулгана — сүлжээ шаардана)

ЧУХАЛ: `lane_no`-г БҮҮ хөндөөрэй — хаалтны реле нь ЯГ ТЭР эгнээний камераар
дамжин ажилладаг тул эгнээ нь физик замтайгаа холбоотой. Энэ хэрэгсэл зөвхөн
чиглэл (`lane_dir`), нэр, `auto_open`-ыг өөрчилнө.
"""
import argparse
import os
import sys
import warnings
from collections import Counter, defaultdict
from datetime import datetime, timedelta

warnings.filterwarnings("ignore", category=DeprecationWarning)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal  # noqa: E402
from app.models import AuditLog, Device, LprEvent, ParkingSession, ParkingSite  # noqa: E402

TZ = timedelta(hours=8)  # Улаанбаатар
ACTIVE = ("OPEN", "AWAITING_PAYMENT", "PAID")


def L(dt):
    return (dt + TZ).strftime("%m-%d %H:%M:%S") if dt else "—"


# ─────────────────────────── НОТОЛГОО (цэвэр функцууд) ───────────────────────────
# Доорх хоёр функц DB-гүй, зөвхөн жагсаалттай ажиллана — тестээр барьдаг
# (tests/test_nested_lanes.py). Тиймээс логикийг энд төвлөрүүлэв.

def direction_evidence(events, inner_ids, outer_entry_ids, outer_exit_ids,
                       window_min: int = 240) -> dict:
    """Аль дотоод камер нь ОРОХ, аль нь ГАРАХ болохыг уншилтын ДАРААЛЛААР нотлоно.

    events: [(created_at, device_id, plate)] — цагаар өсөхөөр эрэмбэлэгдсэн.

    Хоёр бие даасан нотолгоо:
      • first_after_entry — гаднах ОРОХ уншилтын ДАРАА хамгийн ТҮРҮҮНД уншсан
        дотоод камер = машин доторх талбар руу ОРЖ байна → тэр нь дотоод ОРОХ.
      • last_before_exit — гаднах ГАРАХ уншилтын ӨМНӨ хамгийн СҮҮЛД уншсан
        дотоод камер = машин доторхоос ГАРЧ гарц руу явж байна → дотоод ГАРАХ.

    Хоёр нотолгоо ижил хариу өгвөл эргэлзээгүй.
    """
    by_plate = defaultdict(list)
    for at, dev, plate in events:
        by_plate[plate].append((at, dev))

    win = timedelta(minutes=window_min)
    first_after_entry, last_before_exit = Counter(), Counter()
    for plate, rows in by_plate.items():
        rows.sort(key=lambda r: r[0])
        for at, dev in rows:
            if dev in outer_entry_ids:
                nxt = next((d for t, d in rows if at < t <= at + win and d in inner_ids), None)
                if nxt:
                    first_after_entry[nxt] += 1
            elif dev in outer_exit_ids:
                prev = [d for t, d in rows if at - win <= t < at and d in inner_ids]
                if prev:
                    last_before_exit[prev[-1]] += 1
    return {"first_after_entry": first_after_entry, "last_before_exit": last_before_exit}


def phantom_scan(dev_events, entries_by_plate, burst_min: int = 30) -> dict:
    """Нэг камерын уншилтаас «машин ирээгүй атал гарсан event»-ийг ялгана.

    dev_events: [(created_at, plate)] — тэр камерынх, өсөх эрэмбээр.
    entries_by_plate: {plate: [гаднах орох уншилтын цагууд]} — тухайн дугаар
        зогсоолд ҮНЭХЭЭР орсон эсэхийг шалгахад.

    Буцаах: тоон үзүүлэлт + сэжигтэй бүлгүүд. Хий event-ийн 3 хэв маяг:
      1) burst — нэг дугаар богино хугацаанд олон удаа (зогссон/зогсоод байгаа
         машиныг камер дахин дахин уншиж байна)
      2) ghost — тэр дугаар зогсоолд орж ирсэн бүртгэлгүй (огт байхгүй машин)
      3) шөнийн уншилт — 00:00–06:00 цагт (талбар хоосон байх ёстой)
    """
    per_plate = defaultdict(list)
    for at, plate in dev_events:
        per_plate[plate].append(at)

    bursts, ghosts = [], []
    gaps = []
    for plate, times in per_plate.items():
        times.sort()
        for a, b in zip(times, times[1:]):
            gaps.append((b - a).total_seconds())
        # burst: burst_min минутын дотор 3+ уншилт
        run = [times[0]]
        for t in times[1:]:
            if (t - run[-1]) <= timedelta(minutes=burst_min):
                run.append(t)
            else:
                if len(run) >= 3:
                    bursts.append((plate, run[0], run[-1], len(run)))
                run = [t]
        if len(run) >= 3:
            bursts.append((plate, run[0], run[-1], len(run)))
        # ghost: 24 цагийн дотор гаднаас орж ирсэн уншилтгүй
        ent = entries_by_plate.get(plate) or []
        if not any(e <= times[-1] and times[0] - e <= timedelta(hours=24) for e in ent):
            ghosts.append((plate, len(times), times[0], times[-1]))

    night = [(at, p) for at, p in dev_events if (at + TZ).hour < 6]
    return {
        "total": len(dev_events),
        "plates": len(per_plate),
        "bursts": sorted(bursts, key=lambda b: -b[3]),
        "ghosts": sorted(ghosts, key=lambda g: -g[1]),
        "night": night,
        "median_gap_sec": sorted(gaps)[len(gaps) // 2] if gaps else None,
    }


# ─────────────────────────── Тайлан ───────────────────────────
def show(db, site, days: int):
    devs = (db.query(Device).filter(Device.site_id == site.id, Device.status != "deleted")
            .order_by(Device.device_type, Device.lane_no).all())
    inner_cams = [d for d in devs if d.nested_inner and d.device_type == "camera"]
    inner_bars = [d for d in devs if d.nested_inner and d.device_type == "barrier"]
    outer_cams = [d for d in devs if not d.nested_inner and d.device_type == "camera"]

    print(f"══ {site.name} ({site.site_code}) — дотоод камерын чиглэл ══\n")
    print("1) Одоогийн тохиргоо")
    for d in inner_cams + inner_bars:
        role = ("доторх талбар руу ОРОХ → тоолуур ЗОГСОНО" if d.lane_dir != "exit"
                else "доторхоос ГАРАХ → тоолуур ҮРГЭЛЖИЛНЭ")
        auto = "" if d.device_type == "barrier" else f"  auto_open={d.auto_open}"
        print(f"   {d.device_type:8} эгнээ {d.lane_no}  {d.lane_dir:5}  "
              f"{(d.ip_address or '—'):14} «{d.name}»{auto}")
        if d.device_type == "camera":
            print(f"            └─ {role}")
    if len(inner_cams) != 2:
        print(f"   ⚠ дотоод камер {len(inner_cams)} ширхэг — энэ хэрэгсэл 2-т зориулагдсан")

    now = datetime.utcnow()
    since = now - timedelta(days=days)
    evs = (db.query(LprEvent.created_at, LprEvent.device_id, LprEvent.plate_number)
           .filter(LprEvent.site_id == site.id, LprEvent.created_at >= since,
                   LprEvent.accepted.is_(True))
           .order_by(LprEvent.created_at).all())
    inner_ids = {d.id: d for d in inner_cams}
    ev = direction_evidence(
        [(a, d, p) for a, d, p in evs], set(inner_ids),
        {d.id for d in outer_cams if d.lane_dir != "exit"},
        {d.id for d in outer_cams if d.lane_dir == "exit"})

    print(f"\n2) НОТОЛГОО — сүүлийн {days} хоногийн {len(evs)} уншилтаас")
    if not evs:
        print("   ⚠ уншилт алга — камер event илгээхгүй байна (camera_push_check.py)")
        return inner_cams, inner_bars
    for key, title, should in (
            ("first_after_entry", "Гаднаас орсны ДАРАА түрүүлж уншсан", "entry"),
            ("last_before_exit", "Гаднаас гарахын ӨМНӨ сүүлд уншсан", "exit")):
        c = ev[key]
        total = sum(c.values())
        print(f"\n   {title} (нийт {total}):")
        for dev_id, n in c.most_common():
            d = inner_ids[dev_id]
            pct = 100 * n / total if total else 0
            verdict = "✅ тохирч байна" if d.lane_dir == should else "❌ ЭСРЭГЭЭР тохируулсан"
            if pct < 60:
                verdict += " (ялгаа сул — цонхоо уртасгана уу)"
            print(f"      {d.ip_address:14} «{d.name}» эгнээ {d.lane_no}/{d.lane_dir:5}"
                  f"  {n:4} ({pct:3.0f}%)  → ЁСТОЙ нь «{should}»  {verdict}")

    # Тоолуурын одоогийн байдал — солигдсон бол «дотор» тоо бодит байдалтай зөрнө
    paused = (db.query(ParkingSession).filter(
        ParkingSession.site_id == site.id, ParkingSession.status.in_(ACTIVE),
        ParkingSession.paused_since.isnot(None)).count())
    total_open = (db.query(ParkingSession).filter(
        ParkingSession.site_id == site.id, ParkingSession.status.in_(ACTIVE)).count())
    print(f"\n3) Одоо: зогсоолд {total_open} машин, түүнээс «дотор» (тоолуур зогссон) {paused}")
    return inner_cams, inner_bars


def apply_changes(db, site, inner_cams, inner_bars, wanted: dict, apply: bool):
    """wanted: {ip: 'entry'|'exit'} — камерын шинэ чиглэл. Хосолсон дотоод
    ХААЛТ нь камерынхаа эгнээнд үлдэж, чиглэлээ л дагана (реле нь ЯГ ТЭР
    эгнээний камераар ажилладаг тул lane_no-г хөндөхгүй)."""
    plan = []
    for cam in inner_cams:
        want = wanted.get((cam.ip_address or "").strip())
        if not want or want == cam.lane_dir:
            continue
        new_name = "Дотор орох камер" if want == "entry" else "Дотор гарах камер"
        plan.append((cam, {"lane_dir": want, "name": new_name,
                           # auto_open нь ЗӨВХӨН орох чиглэлд утгатай (гарах нь
                           # ямагт нээгддэг). Орох болгож байгаа камерыг асаахгүй
                           # бол машин доторх талбарт орж чадахгүй гацна.
                           "auto_open": True if want == "entry" else cam.auto_open}))
        bar = next((b for b in inner_bars if b.lane_no == cam.lane_no), None)
        if bar and bar.lane_dir != want:
            plan.append((bar, {"lane_dir": want,
                               "name": f"Дотор {'орох' if want == 'entry' else 'гарах'} хаалт (авто)"}))

    if not plan:
        print("\n4) Өөрчлөх зүйл алга — тохиргоо аль хэдийн хүссэн байдалтай байна.")
        return
    print(f"\n4) {'БИЧИХ' if apply else 'DRY-RUN — юу ч бичихгүй'} өөрчлөлт:")
    for d, ch in plan:
        for k, v in ch.items():
            print(f"   {d.device_type:8} эгнээ {d.lane_no} «{d.name}» {k}: "
                  f"{getattr(d, k)!r} → {v!r}")
    if not apply:
        print("\n   Бичих бол ижил командад --apply нэмнэ үү.")
        return
    for d, ch in plan:
        before = {k: getattr(d, k) for k in ch}
        for k, v in ch.items():
            setattr(d, k, v)
        db.add(AuditLog(username="tools/nested_lanes.py", action="UPDATE", entity="device",
                        entity_id=d.id, detail={"before": before, "after": ch,
                                                "reason": "дотоод камерын чиглэл солигдсоныг зассан"}))
    db.commit()
    print("\n   ✅ Хадгалагдлаа. Дараагийн уншилтаас хүчинтэй (restart шаардлагагүй).")
    print("   Шалгах: venv/bin/python tools/nested_lanes.py "
          f"{site.site_code}  — 2) хэсгийн нотолгоо ✅ болсон эсэхийг хараарай.")


def resume_open_pauses(db, site, apply: bool):
    """Чиглэл СОЛИГДСОН үед үүссэн ЯВЖ БУЙ зогсолтуудыг цуцлана.

    Солигдсон тохиргоонд «доторхоос ГАРЧ БАЙГАА» машиныг систем «дотогш орлоо»
    гэж уншиж тоолуурыг зогсоодог. Тэр машин ГАДНАХ ТӨЛБӨРТЭЙ талбарт зогсож
    байгаа тул тоолуур нь АЖИЛЛАХ ёстой. Иймд `paused_since`-ийг цуцална —
    хуримтлагдсан минутыг НЭМЭХГҮЙ (тэр хугацаа төлбөртэй талбарт өнгөрсөн).

    Үнэхээр дотор байгаа машин энэ жагсаалтад БАЙХГҮЙ: солигдсон тохиргоонд
    тэднийг «гарлаа» гэж уншаад тоолуурыг нь үргэлжлүүлчихсэн байдаг.
    """
    rows = (db.query(ParkingSession)
            .filter(ParkingSession.site_id == site.id,
                    ParkingSession.status.in_(ACTIVE),
                    ParkingSession.paused_since.isnot(None))
            .order_by(ParkingSession.paused_since).all())
    print(f"\n5) Явж буй зогсолт {len(rows)} — {'ЦУЦЛАНА' if apply else 'dry-run'}")
    now = datetime.utcnow()
    for s in rows:
        mins = int((now - s.paused_since).total_seconds() // 60)
        print(f"   {s.plate_number:10} {L(s.paused_since)}-ээс хойш {mins:4} мин "
              f"зогссон (хуримтлал {int(s.paused_minutes or 0)} мин хэвээр үлдэнэ)")
    if not rows or not apply:
        if rows:
            print("   Цуцлах бол --resume-open --apply гэж дуудна уу.")
        return
    for s in rows:
        db.add(AuditLog(username="tools/nested_lanes.py", action="UPDATE", entity="session",
                        entity_id=s.id, detail={"paused_since": str(s.paused_since),
                                                "reason": "чиглэл солигдсоноос үүссэн буруу "
                                                          "зогсолтыг цуцлав (минут нэмээгүй)"}))
        s.paused_since = None
    db.commit()
    print(f"   ✅ {len(rows)} session-ий тоолуур дахин ажиллаж эхэллээ.")


def phantom(db, site, ip: str, hours: int, camlog: bool):
    dev = next((d for d in db.query(Device).filter(
        Device.site_id == site.id, Device.ip_address == ip,
        Device.status != "deleted").all() if d.device_type == "camera"), None)
    if not dev:
        print(f"{ip} камер {site.name}-д олдсонгүй")
        return
    now = datetime.utcnow()
    since = now - timedelta(hours=hours)
    rows = (db.query(LprEvent.created_at, LprEvent.plate_number)
            .filter(LprEvent.device_id == dev.id, LprEvent.created_at >= since)
            .order_by(LprEvent.created_at).all())
    outer_entry_ids = [d.id for d in db.query(Device).filter(
        Device.site_id == site.id, Device.device_type == "camera",
        Device.nested_inner.is_(False), Device.lane_dir != "exit").all()]
    ent = defaultdict(list)
    for at, p in db.query(LprEvent.created_at, LprEvent.plate_number).filter(
            LprEvent.device_id.in_(outer_entry_ids),
            LprEvent.created_at >= since - timedelta(hours=24)).all():
        ent[p].append(at)

    r = phantom_scan([(a, p) for a, p in rows], ent)
    print(f"══ «{dev.name}» {ip} — сүүлийн {hours}ц-ийн хий event шинжилгээ ══\n")
    print(f"   Нийт уншилт {r['total']}, ялгаатай дугаар {r['plates']}, "
          f"уншилт хоорондын медиан завсар "
          f"{int(r['median_gap_sec']) if r['median_gap_sec'] is not None else '—'} сек")

    print(f"\n1) BURST — нэг дугаар 30 мин дотор 3+ удаа ({len(r['bursts'])} бүлэг)")
    print("   (зогссон/зогсоод байгаа машиныг камер ДАХИН ДАХИН уншиж байна —")
    print("    Snapshot Triggering Line нь зогссон машин дээгүүр татагдсан гол шинж)")
    for plate, a, b, n in r["bursts"][:12]:
        print(f"   «{plate}»  {n:3} уншилт  {L(a)} → {L(b)}")
    if len(r["bursts"]) > 12:
        print(f"   … нийт {len(r['bursts'])}")

    print(f"\n2) GHOST — зогсоолд орж ирсэн бүртгэлгүй дугаар ({len(r['ghosts'])})")
    print("   (гаднах орох камерт уншигдаагүй машин доторх камерт л харагдана =")
    print("    гудамжны/хажуугийн замын машиныг уншиж байж болзошгүй)")
    for plate, n, a, b in r["ghosts"][:12]:
        print(f"   «{plate}»  {n:3} уншилт  {L(a)} → {L(b)}")
    if len(r["ghosts"]) > 12:
        print(f"   … нийт {len(r['ghosts'])}")

    print(f"\n3) ШӨНИЙН уншилт (00:00–06:00): {len(r['night'])}")
    for at, p in r["night"][:10]:
        print(f"   {L(at)}  «{p}»")

    if camlog:
        import asyncio
        from app.services.camera_records import fetch_snap_events
        from app.services.device_auth import camera_credentials
        user, pwd = camera_credentials(dev)
        print(f"\n4) Камерын ӨӨРИЙН бүртгэл ({ip}, {user}) — серверийнхтэй тулгана")
        try:
            recs = asyncio.run(fetch_snap_events(ip, user, pwd, since, now))
        except Exception as e:  # сүлжээ/нэвтрэлт
            print(f"   ❌ уншиж чадсангүй: {type(e).__name__}: {e}")
            return
        print(f"   Камер дээр {len(recs)} бичлэг, серверт {r['total']} уншилт "
              f"→ зөрүү {len(recs) - r['total']}")
        for field in ("SnapSource", "Category", "VehicleSign", "event_name", "Lane"):
            c = Counter(str(x.get(field, "—")) for x in recs)
            if len(c) > 1 or (c and "—" not in c):
                print(f"   {field:14} " + ", ".join(f"{k}={v}" for k, v in c.most_common(6)))
        print("   ⓘ SnapSource нь юу triggerlэснийг хэлнэ — «Video Detect»/«Timing» давамгайлж,")
        print("     дугаар нь давтагдаж байвал энэ нь зогссон машины дахин уншилт.")


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("site", help="зогсоолын код (ж: RASH)")
    ap.add_argument("--days", type=int, default=7, help="нотолгооны цонх (default 7 хоног)")
    ap.add_argument("--set", action="append", default=[], metavar="IP=entry|exit",
                    help="дотоод камерын ШИНЭ чиглэл (олон удаа өгч болно)")
    ap.add_argument("--apply", action="store_true", help="үнэхээр бичих (эс бол dry-run)")
    ap.add_argument("--phantom", metavar="IP", help="тэр камерын хий event-ийг задлах")
    ap.add_argument("--hours", type=int, default=24, help="--phantom-ийн цонх (default 24ц)")
    ap.add_argument("--camlog", action="store_true",
                    help="--phantom дээр камерын өөрийн бүртгэлтэй тулгах (сүлжээ шаардана)")
    ap.add_argument("--resume-open", action="store_true",
                    help="чиглэл солигдсоноос үүссэн ЯВЖ БУЙ зогсолтуудыг цуцлах")
    a = ap.parse_args()

    db = SessionLocal()
    site = (db.query(ParkingSite).filter(ParkingSite.site_code == a.site.upper()).first()
            or db.query(ParkingSite).filter(ParkingSite.name.ilike(f"%{a.site}%")).first())
    if not site:
        print(f"Зогсоол «{a.site}» олдсонгүй")
        return

    if a.phantom:
        phantom(db, site, a.phantom.strip(), a.hours, a.camlog)
        return

    inner_cams, inner_bars = show(db, site, a.days)
    wanted = {}
    for item in a.set:
        ip, _, direction = item.partition("=")
        if direction not in ("entry", "exit"):
            print(f"--set {item}: чиглэл нь entry эсвэл exit байх ёстой")
            return
        wanted[ip.strip()] = direction
    if wanted:
        apply_changes(db, site, inner_cams, inner_bars, wanted, a.apply)
    if a.resume_open:
        resume_open_pauses(db, site, a.apply)


if __name__ == "__main__":
    main()
