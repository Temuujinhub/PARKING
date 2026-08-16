"""«Гараар хаасан» гэж харагдаж буй бүртгэлүүд ҮНЭНДЭЭ юугаар хаагдсаныг задална.

Яагаад хэрэгтэй вэ: `MANUAL_CLOSED` төлөв нь Түүх дээр «Гараар хаасан» гэж
харагддаг ч кодод 6 өөр зам ийм төлөв бичдэг (оператор гараар, авто цэвэрлэгээ,
камерын логийн sync, ээлж/шөнийн хаалт, төлбөргүй гарсан машин дахин орж ирэх).
Тэдгээрийн заримд session-ий түвшний AuditLog бичигддэггүй тул «Хаасан» багана
хоосон, заримд «Систем» гэж гарч, «гараар хаасан хэрнээ систем» мэт харагддаг.

Ажиллуулах (production сервер дээр, backend хавтаст):
    venv/bin/python tools/close_reason_diag.py --site RASH --days 3
    venv/bin/python tools/close_reason_diag.py --days 1          # бүх зогсоол
    venv/bin/python tools/close_reason_diag.py --site RASH --days 3 --list 30
"""
import argparse
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal
from sqlalchemy import func

from app.models import AuditLog, Device, LprEvent, ParkingSession, ParkingSite

TZ = timedelta(hours=8)  # УБ-ын цаг

# session-ийн түвшинд бичигддэг хаалтын AuditLog үйлдлүүд → хүн ойлгох тайлбар.
# Түүх хуудас (sessions_router._CLOSE_ACTIONS) эдгээрийн ЗӨВХӨН эхний 5-ыг мэддэг —
# бусад нь UI дээр «-» болж, хэн хаасан нь мэдэгдэхгүй.
ACTION_LABEL = {
    "MANUAL_EXIT": "оператор гараар гаргасан",
    "ADMIN_REMOVE": "админ зогсоолоос хассан",
    "AUTO_CLOSE": "авто цэвэрлэгээ (хугацаа хэтэрсэн)",
    "AUTO_FREE_CLOSE": "авто: зөвхөн орох уншилттай",
    "AUTO_JUNK_CLOSE": "авто: формат буруу дугаар",
    "CAMERA_SYNC": "камерын логоос нөхөж бүртгэсэн",
    "CAMERA_SYNC_EXIT": "камерын логийн ГАРАХ уншилтаар хаасан",
}
UI_KNOWN = {"MANUAL_EXIT", "ADMIN_REMOVE", "AUTO_CLOSE", "AUTO_FREE_CLOSE", "AUTO_JUNK_CLOSE"}


def L(dt):
    return (dt + TZ).strftime("%m-%d %H:%M") if dt else "—"


def classify(s: ParkingSession, logs: list) -> tuple[str, str]:
    """(ангилал, хэн) — session-ий хаалтын жинхэнэ эх сурвалж."""
    if logs:
        action, username = logs[-1][0], logs[-1][1]
        return ACTION_LABEL.get(action, action), username
    # AuditLog огт байхгүй — note болон бусад шинжээр таана
    note = (s.note or "").lower()
    if "ээлж" in note or "shift" in note:
        return "ээлж хаах (close_cars)", "?"
    if "шөн" in note or "night" in note:
        return "шөнийн хаалт", "?"
    if s.exit_confirmed:
        return "БҮРТГЭЛГҮЙ: гарах уншилттай ч ямар ч AuditLog алга", "—"
    return "БҮРТГЭЛГҮЙ: ээлж/шөнийн хаалт эсвэл дахин орж ирэхэд хаагдсан", "—"


def live_vs_backfill(db, site, since):
    """Зогсолт АМЬДААР (камерын callback) бүртгэгдэж байна уу, эсвэл дараа нь
    камерын логоос НӨХӨГДӨЖ байна уу — «Гараар хаасан»-ы жинхэнэ үндэс.

    Нөхөлтөөр үүссэн session гэдэг нь тухайн машин орж/гарах агшинд систем
    МЭДЭЭГҮЙ байсан гэсэн үг: хаалт автоматаар нээгдээгүй, оператор гараар
    нээсэн, LED юу ч бичээгүй, төлбөр нэхэгдээгүй. Дараа нь (30 мин тутмын sync)
    л бүртгэл үүсээд шууд хаагддаг.
    """
    q = db.query(ParkingSession).filter(ParkingSession.entry_time >= since)
    if site:
        q = q.filter(ParkingSession.site_id == site.id)
    sess = q.all()
    if not sess:
        return
    synced = {eid for (eid,) in db.query(AuditLog.entity_id)
              .filter(AuditLog.entity == "session", AuditLog.action == "CAMERA_SYNC",
                      AuditLog.created_at >= since).all()}
    back = sum(1 for s in sess
               if s.id in synced or "логоос нөхөж" in (s.note or ""))
    live = len(sess) - back
    print(f"\n══ Зогсолт хэрхэн бүртгэгдэж байна ({len(sess)} session) ══")
    print(f"   {live:5}  амьд камерын callback-аар ({live * 100 // len(sess)}%) "
          f"— хаалт автоматаар нээгдсэн")
    print(f"   {back:5}  камерын логоос НӨХӨГДСӨН ({back * 100 // len(sess)}%) "
          f"— тэр агшинд систем мэдээгүй, хаалтыг гараар нээсэн")

    # Камер бүрийн амьд уншилт — аль камерын callback ирдэггүйг заана
    devs = db.query(Device).filter(Device.device_type == "camera",
                                   Device.status != "deleted")
    if site:
        devs = devs.filter(Device.site_id == site.id)
    devs = devs.all()
    if not devs:
        return
    # ЯЛГАХ ЗҮЙЛ: callback огт ИРЭЭГҮЙ юу, эсвэл ирсэн ч ГОЛОГДСОН уу.
    # Хоёрын засвар өөр: эхнийх нь камер/сүлжээ, хоёр дахь нь итгэлцлийн босго
    # (lpr_min_confidence) эсвэл дугаар танигдаагүй (тоос/шороо/өнцөг).
    stats: dict = {}
    for dev_id, accepted, reason, n in (
            db.query(LprEvent.device_id, LprEvent.accepted, LprEvent.reject_reason,
                     func.count())
            .filter(LprEvent.device_id.in_([d.id for d in devs]),
                    LprEvent.created_at >= since)
            .group_by(LprEvent.device_id, LprEvent.accepted, LprEvent.reject_reason).all()):
        st = stats.setdefault(dev_id, {"ok": 0, "bad": 0, "reasons": Counter()})
        if accepted:
            st["ok"] += n
        else:
            st["bad"] += n
            st["reasons"][reason or "?"] += n
    print(f"\n   Амьд LPR уншилт камер тутамд ({len(sess)} session-тэй харьцуул):")
    for d in sorted(devs, key=lambda x: (bool(x.nested_inner), x.lane_dir or "")):
        mark = "🔵 дотоод" if d.nested_inner else "  гадна "
        st = stats.get(d.id, {"ok": 0, "bad": 0, "reasons": Counter()})
        top = ", ".join(f"{r} ×{n}" for r, n in st["reasons"].most_common(2))
        print(f"   {mark} {(d.name or '?'):14} {d.lane_dir or '?':5} "
              f"хүлээн авсан {st['ok']:5} · гологдсон {st['bad']:4}"
              f"   сүүлд: {L(d.last_seen)}")
        if top:
            print(f"            гологдсон шалтгаан: {top}")


def daily(db, site, days: int):
    """ӨДРӨӨР: камерын уншилт хүлээн авсан/гологдсон + session амьд/нөхөгдсөн.

    Зорилго: «энэ асуудал хэзээнээс эхэлсэн бэ» гэдгийг deploy-ийн огноотой
    тулгах. Гологдолт нэг өдрөөс огцом үсэрсэн бол шалтгаан нь тэр өдрийн
    өөрчлөлт — камер/тоос биш.
    """
    since = datetime.utcnow() - timedelta(days=days)
    devs = db.query(Device).filter(Device.device_type == "camera",
                                   Device.status != "deleted")
    if site:
        devs = devs.filter(Device.site_id == site.id)
    devs = {d.id: d for d in devs.all()}
    if not devs:
        return

    print(f"\n══ ӨДРӨӨР ({days} хоног) — уншилт хүлээн авсан / гологдсон ══")
    day = func.date(LprEvent.created_at + TZ)      # УБ-ын цагаар өдөрчилнө
    per: dict = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    for d_, dev_id, accepted, n in (
            db.query(day, LprEvent.device_id, LprEvent.accepted, func.count())
            .filter(LprEvent.device_id.in_(devs), LprEvent.created_at >= since)
            .group_by(day, LprEvent.device_id, LprEvent.accepted).all()):
        per[str(d_)][dev_id][0 if accepted else 1] += n

    names = {i: (d.name or "?")[:12] for i, d in devs.items()}
    order = sorted(devs, key=lambda i: (bool(devs[i].nested_inner), devs[i].lane_dir or ""))
    print("   огноо     " + "".join(f"{names[i]:>16}" for i in order))
    for d_ in sorted(per):
        cells = []
        for i in order:
            ok, bad = per[d_][i]
            pct = f" {bad * 100 // (ok + bad)}%" if (ok + bad) else ""
            cells.append(f"{ok}/{bad}{pct}".rjust(16))
        print(f"   {d_}  " + "".join(cells))
    print("   (хүлээн авсан/гологдсон · гологдлын хувь)")

    # Session өдөр тутам: амьд vs нөхөгдсөн
    sq = db.query(ParkingSession).filter(ParkingSession.entry_time >= since)
    if site:
        sq = sq.filter(ParkingSession.site_id == site.id)
    sess = sq.all()
    if not sess:
        return
    synced = {eid for (eid,) in db.query(AuditLog.entity_id)
              .filter(AuditLog.entity == "session", AuditLog.action == "CAMERA_SYNC",
                      AuditLog.created_at >= since).all()}
    per_day: dict = defaultdict(lambda: [0, 0])
    for s in sess:
        back = s.id in synced or "логоос нөхөж" in (s.note or "")
        per_day[str((s.entry_time + TZ).date())][1 if back else 0] += 1
    print("\n   огноо       амьд  нөхөгдсөн  нөхөлтийн %")
    for d_ in sorted(per_day):
        live, back = per_day[d_]
        tot = live + back
        print(f"   {d_}  {live:8}{back:9}{(back * 100 // tot if tot else 0):9}%")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", help="зогсоолын код эсвэл нэрний эхлэл "
                                   "(ж: RASH, «Рашбулаг»). Өгөхгүй бол бүгд")
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--list", type=int, default=0, help="хэдэн жишээ мөр хэвлэх")
    ap.add_argument("--daily", type=int, default=0, metavar="ХОНОГ",
                    help="өдрөөр задлах — асуудал хэзээнээс эхэлснийг deploy-той тулгах")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        since = datetime.utcnow() - timedelta(days=args.days)
        q = (db.query(ParkingSession)
             .filter(ParkingSession.status == "MANUAL_CLOSED",
                     ParkingSession.exit_time >= since))
        site = None
        if args.site:
            site = (db.query(ParkingSite)
                    .filter(ParkingSite.site_code == args.site).first())
            if not site:   # код биш нэрээр бичсэн байж болно (ж: «Рашбулаг»)
                site = (db.query(ParkingSite)
                        .filter(ParkingSite.name.ilike(f"{args.site}%")).first())
            if not site:
                names = ", ".join(f"{s.site_code}={s.name}" for s in
                                  db.query(ParkingSite).order_by(ParkingSite.name).all())
                sys.exit(f"«{args.site}» зогсоол олдсонгүй. Байгаа нь: {names}")
            q = q.filter(ParkingSession.site_id == site.id)
        rows = q.order_by(ParkingSession.exit_time.desc()).all()

        title = site.name if site else "БҮХ ЗОГСООЛ"
        print(f"══ {title} — сүүлийн {args.days} хоногийн «Гараар хаасан» "
              f"({len(rows)} бүртгэл) ══\n")
        if not rows:
            return

        ids = [s.id for s in rows]
        logs = defaultdict(list)
        for eid, action, username, at in (
                db.query(AuditLog.entity_id, AuditLog.action, AuditLog.username,
                         AuditLog.created_at)
                .filter(AuditLog.entity == "session", AuditLog.entity_id.in_(ids))
                .order_by(AuditLog.created_at).all()):
            logs[eid].append((action, username, at))

        kinds, hidden, samples = Counter(), Counter(), defaultdict(list)
        free_but_manual = 0
        for s in rows:
            kind, who = classify(s, logs.get(s.id, []))
            kinds[kind] += 1
            samples[kind].append((s, who))
            # UI-д «Хаасан» багана хоосон үлдэх (эсвэл «Систем» гэж гарах) эсэх
            acts = {a for a, _u, _t in logs.get(s.id, [])}
            if not acts & UI_KNOWN:
                hidden[kind] += 1
            if not float(s.total_fee or 0):
                free_but_manual += 1

        print("Хаалтын эх сурвалж:")
        for kind, n in kinds.most_common():
            h = hidden[kind]
            flag = f"   ← UI дээр «Хаасан» багана ХООСОН ({h})" if h else ""
            print(f"   {n:5}  {kind}{flag}")

        print(f"\nТөлбөр 0₮ мөртөө «Гараар хаасан» гэж бичигдсэн: {free_but_manual}"
              f" / {len(rows)}  (эдгээр нь «Үнэгүй гарсан» байх ёстой)")

        live_vs_backfill(db, site, since)
        if args.daily:
            daily(db, site, args.daily)

        if args.list:
            print(f"\nЖишээ ({min(args.list, len(rows))} мөр):")
            shown = 0
            for kind, items in samples.items():
                for s, who in items:
                    if shown >= args.list:
                        break
                    dur = s.duration_minutes
                    print(f"   {s.plate_number:10} {L(s.entry_time)} → {L(s.exit_time)}"
                          f"  {str(dur) + 'м' if dur is not None else '—':>6}"
                          f"  {float(s.total_fee or 0):>7,.0f}₮  "
                          f"{'дотор ' + str(int(s.paused_minutes or 0)) + 'м' if s.paused_minutes else '':10}"
                          f"  {kind} [{who}]")
                    shown += 1
    finally:
        db.close()


if __name__ == "__main__":
    main()
