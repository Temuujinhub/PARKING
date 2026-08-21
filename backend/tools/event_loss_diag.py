"""Камерын лог vs серверт ирсэн event — ХЭД нь замдаа алдагдсаныг тоолно.

Асуулт: «машин орж ирсэн атлаа хаалт нээгдээгүй, дараа нь sync нөхсөн» гэдэг
тохиолдол камерын БУРУУ уншилтаас (дугаар таниагүй) болов уу, эсвэл уншилт
ЗӨВ болсон мөртөө сервер рүү ИРЭЭГҮЙ (стрим тасарсан) юу?

Хоёрыг ялгах цорын ганц арга: камерын ӨӨРИЙН доторх бичлэгийг (snapManager
лог) сервер дээрх `lpr_events`-тэй тулгах. Камер «17:51-д 1234УБА уншсан» гэж
бичсэн атлаа тэр агшинд серверт ямар ч мөр байхгүй бол event ЗАМДАА АЛДАГДСАН.

Ажиллуулах (production сервер дээр, backend хавтаст):
    venv/bin/python tools/event_loss_diag.py --site RASH --hours 6
    venv/bin/python tools/event_loss_diag.py --site RASH --hours 12 --list 20
    venv/bin/python tools/event_loss_diag.py --hours 3          # бүх зогсоол

АНХААР: камер бүр рүү HTTP хүсэлт явуулж лог татна (камер тутам 15с timeout).
Ачаалал багатай үед ажиллуулна уу. Өгөгдөл ЗӨВХӨН УНШИНА.
"""
import argparse
import os
import sys
from collections import Counter
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal
from app.models import Device, LprEvent, ParkingSite
from app.services.camera_records import site_camera_events
from app.session_logic import is_valid_plate, normalize_plate

TZ = timedelta(hours=8)  # УБ-ын цаг
# Камерын бичлэгийн цаг ба серверийн хүлээж авсан цаг хооронд зөвшөөрөх зөрүү.
# Камерын дотоод цаг NTP-гүй бол хэдэн секундээр гулсдаг тул өгөөмөр авна.
MATCH_SEC = 120


def estimate_skew(deltas, candidates):
    """Цагийн зөрүүний медиан ба түүнд ИТГЭЖ БОЛОХ эсэх.

    Цөөн хосоос гарсан том зөрүүг хэрэглэвэл бүх бичлэг «алдагдсан» болж,
    оношилгоо эрүүл камерыг эвдэрсэн гэж заана (2026-08-21 Эрэл-13).
    """
    if not deltas:
        return 0.0, False
    skew = sorted(deltas)[len(deltas) // 2]
    if abs(skew) <= MATCH_SEC:
        return skew, True
    return skew, len(deltas) >= max(10, candidates * 0.2)


def suspect_matching(total_log, lost_total, accepted_srv):
    """Камерын логоос ЦӨӨН БИШ уншилт серверт ирсэн атал «алдагдал» өндөр бол
    энэ нь стримийн тасралт биш, тулгалтын алдаа."""
    return bool(total_log) and lost_total > total_log * 0.5 and accepted_srv >= total_log


def L(dt):
    return (dt + TZ).strftime("%m-%d %H:%M:%S") if dt else "—"


def check_site(db, site: ParkingSite, hours: float, listing: int) -> None:
    try:
        cam = site_camera_events(db, site.id, hours=hours)
    except Exception as e:  # noqa: BLE001
        print(f"\n── {site.name}: камерын лог уншигдсангүй — {e}")
        return

    broken = [c for c in cam["cameras"] if c.get("error")]
    print(f"\n══ {site.name} ({site.site_code}) — сүүлийн {hours:g} цаг ══")
    for c in cam["cameras"]:
        mark = "🔵 дотоод" if c.get("nested_inner") else "  гадна "
        err = f"  ⚠ {c['error']}" if c.get("error") else ""
        print(f"   {mark} {(c['name'] or '?'):14} {c['lane_dir']:5} "
              f"логт {c['events']:5} бичлэг{err}")
    if broken and len(broken) == len(cam["cameras"]):
        print("   Бүх камер уншигдсангүй — дүгнэлт гаргах боломжгүй.")
        return

    # Серверт ирсэн уншилтууд (хүлээн авсан ба гологдсоныг ХОЁУЛАНГ нь) —
    # гологдсон ч гэсэн event нь ИРСЭН гэдгийг харуулна, тэр нь өөр асуудал.
    since = datetime.utcnow() - timedelta(hours=hours + 0.5)
    rows = (db.query(LprEvent.plate_number, LprEvent.created_at, LprEvent.accepted,
                     LprEvent.lane_dir)
            .filter(LprEvent.site_id == site.id, LprEvent.created_at >= since).all())
    by_plate: dict = {}
    any_time: list = []
    for plate, at, ok, lane in rows:
        by_plate.setdefault(plate, []).append((at, ok, lane))
        any_time.append(at)
    any_time.sort()

    # ── ЦАГИЙН ЗӨРҮҮГ ЭХЛЭЭД ХЭМЖИНЭ ─────────────────────────────────────────
    # Камерын дотоод цаг NTP-гүй бол минут/цагаар гулсдаг. Үүнийг тооцохгүй бол
    # БҮХ бичлэг «алдагдсан» гэж гарч, оношилгоо өөрөө худал хэлнэ. Ижил
    # ДУГААРААР тохирсон хосуудын цагийн зөрүүний МЕДИАНЫГ зөрүү гэж авна
    # (медиан нь цөөн худал хосолтод тэсвэртэй).
    log_all = cam["events"] + (cam.get("inner_events") or [])
    deltas = []
    for ev in log_all:
        p = normalize_plate(ev.get("plate") or "")
        for at, _ok, _lane in by_plate.get(p, ()):
            d = (at - ev["time"]).total_seconds()
            if abs(d) <= 3600:
                deltas.append(d)
    deltas.sort()
    # Хосын тоо ХЭТ ЦӨӨН бол медиан нь санамсаргүй давхцлаас гарсан байж болно
    # (нэг дугаар өдөрт хэд хэдэн удаа ирдэг). Ийм «зөрүү»-г хэрэглэвэл БҮХ
    # бичлэг «алдагдсан» болж, оношилгоо ЭРҮҮЛ камерыг эвдэрсэн гэж заана.
    # 2026-08-21 Эрэл-13: камерын цаг ±0с (camera_clock_check) байхад ердөө
    # 5 хосоор -54.4 минутын зөрүү «хэмжигдэж», 98% алдагдал гэж худал мэдээлэв.
    cand = sum(1 for ev in log_all
               if normalize_plate(ev.get("plate") or "") in by_plate)
    skew, trusted = estimate_skew(deltas, cand)
    if abs(skew) > MATCH_SEC and not trusted:
        print(f"\n   ⚠ ЦАГИЙН ЗӨРҮҮ {skew / 60:+.1f} мин гэж гарсан ч ердөө "
              f"{len(deltas)} хосоор тулгуурлаж байна ({cand} боломжоос) —")
        print("      ИТГЭЛГҮЙ тул тооцоонд ОРУУЛСАНГҮЙ. Камерын цагийг "
              "`camera_clock_check.py`-ээр шууд шалга.")
        skew = 0.0
    elif abs(skew) > MATCH_SEC:
        print(f"\n   ⚠ ЦАГИЙН ЗӨРҮҮ: камерын лог серверээс {skew / 60:+.1f} минутаар "
              f"зөрүүтэй ({len(deltas)} хосоор хэмжив) — тулгалтад тооцов.")
        print("      Камерын NTP тохиргоог засах хэрэгтэй; эс бол camera_sync ч "
              "буруу цагаар зогсолт бүртгэнэ.")
    elif not deltas:
        print("\n   ⚠ Ижил дугаартай нэг ч хос олдсонгүй — цагийн зөрүүг хэмжих "
              "боломжгүй. Доорх «алдагдсан» тоо ЦАГИЙН ЗӨРҮҮГЭЭС ч үүдсэн байж болно.")

    # Камерын лог бүрийг серверийн мөртэй тулгана
    stats: dict = {}
    lost_rows: list = []
    for ev in log_all:
        p = normalize_plate(ev.get("plate") or "")
        if not p or not is_valid_plate(p):
            continue          # дугаар таниагүй бичлэг — энэ тестийн сэдэв биш
        name = ev.get("camera") or "?"
        st = stats.setdefault(name, {"log": 0, "ok": 0, "rejected": 0, "lost": 0,
                                     "inner": bool(ev.get("nested_inner"))})
        st["log"] += 1
        hit = next((x for x in by_plate.get(p, [])
                    if abs((x[0] - ev["time"]).total_seconds() - skew) <= MATCH_SEC), None)
        if hit is None:
            st["lost"] += 1
            lost_rows.append((ev["time"], p, name, ev.get("lane_dir")))
        elif hit[1]:
            st["ok"] += 1
        else:
            st["rejected"] += 1

    total_log = sum(s["log"] for s in stats.values())
    if not total_log:
        print("   Логт зөв форматтай дугаартай бичлэг олдсонгүй.")
        return

    ok_rows = [t for t, o, _l in ((x[0], x[1], x[2]) for v in by_plate.values() for x in v) if o]
    print(f"\n   Сервер тал: цонхонд {len(rows)} мөр "
          f"({sum(1 for _p, _t, o, _l in rows if o)} хүлээн авсан, "
          f"{sum(1 for _p, _t, o, _l in rows if not o)} гологдсон)")
    if ok_rows:
        print(f"      сүүлд ХҮЛЭЭН АВСАН уншилт: {L(max(ok_rows))}"
              f"  ({(datetime.utcnow() - max(ok_rows)).total_seconds() / 60:.0f} мин өмнө)")
    else:
        print("      ⚠ цонхонд ХҮЛЭЭН АВСАН уншилт НЭГ Ч БАЙХГҮЙ — стрим үхсэн байх магадлалтай")

    print(f"\n   Камерын бичлэг → сервер (зөрүү ≤{MATCH_SEC}с):")
    print(f"   {'камер':16}{'логт':>7}{'ирсэн':>8}{'гологдсон':>11}"
          f"{'АЛДАГДСАН':>11}{'алдагдал':>10}")
    for name, s in sorted(stats.items(), key=lambda x: -x[1]["lost"]):
        pct = s["lost"] * 100 // s["log"] if s["log"] else 0
        mark = "🔵" if s["inner"] else "  "
        print(f"   {mark}{name[:14]:14}{s['log']:7}{s['ok']:8}{s['rejected']:11}"
              f"{s['lost']:11}{pct:9}%")
    lost_total = sum(s["lost"] for s in stats.values())
    print(f"   {'НИЙТ':16}{total_log:7}"
          f"{sum(s['ok'] for s in stats.values()):8}"
          f"{sum(s['rejected'] for s in stats.values()):11}{lost_total:11}"
          f"{lost_total * 100 // total_log:9}%")

    # ЭСРЭГ НОТОЛГОО: камер логтоо байгаагаас илүү олон уншилтыг сервер хүлээн
    # авсан бол «алдагдал» гэдэг нь тулгалтын алдаа болохоос стримийн тасралт
    # БИШ. Ийм үед хувь хэмжээг нүүрэн дээр нь итгэж болохгүй.
    accepted_srv = sum(1 for _p, _t, o, _l in rows if o)
    if suspect_matching(total_log, lost_total, accepted_srv):
        print(f"\n   ⚠⚠ ЗӨРЧИЛ: сервер {accepted_srv} уншилт хүлээн авсан нь камерын "
              f"логийн {total_log} бичлэгээс ЦӨӨН БИШ —")
        print(f"      тиймээс дээрх {lost_total * 100 // total_log}% «алдагдал» нь "
              "ТУЛГАЛТЫН алдаа (цаг/дугаарын хэлбэр), стрим тасраагүй.")
        print("      Эхлээд `camera_clock_check.py` ажиллуулж камерын цагийг шалга.")

    print("\n   Тайлбар:")
    print("     ирсэн     — камер уншсан, сервер хүлээн авсан (хэвийн)")
    print("     гологдсон — event ИРСЭН ч дугаар/итгэлцүүрээр татгалзсан")
    print("                 → шалтгаан нь камерын тохиргоо/тоос, стрим БИШ")
    print("     АЛДАГДСАН — камер уншсан ч серверт ямар ч мөр алга")
    print("                 → стрим тасарсан/event хүрээгүй. Хаалт нээгдээгүй,")
    print("                   төлбөр нэхэгдээгүй, дараа нь sync нөхсөн байх ёстой")

    # Алдагдал ЦАГААР бөөгнөрч байна уу — тасалдал нь тодорхой цонхонд байвал
    # стримийн тасралт, жигд тархсан бол event тус бүрийн алдагдал.
    if lost_rows:
        per_hour = Counter((t + TZ).strftime("%m-%d %H:00") for t, _p, _c, _l in lost_rows)
        print(f"\n   Алдагдал цагаар (нийт {len(lost_rows)}):")
        for h, n in sorted(per_hour.items()):
            print(f"      {h}  {'█' * min(n, 40)} {n}")
        gaps = []
        if any_time:
            prev = any_time[0]
            for t in any_time[1:]:
                if (t - prev).total_seconds() >= 900:   # 15 мин+ чимээгүй завсар
                    gaps.append((prev, t))
                prev = t
        if gaps:
            print("\n   Серверт ямар ч event ирээгүй ЗАВСРУУД (15 мин+):")
            for a, b in gaps[:10]:
                print(f"      {L(a)} → {L(b)}  ({(b - a).total_seconds() / 60:.0f} мин)")
            print("      ← стрим тасарсан бол алдагдал ЭНЭ завсруудад бөөгнөрнө")

    if listing and lost_rows:
        print(f"\n   Алдагдсан жишээ ({min(listing, len(lost_rows))}):")
        for t, p, c, lane in sorted(lost_rows, reverse=True)[:listing]:
            print(f"      {L(t)}  «{p}»  {c} ({lane})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", help="зогсоолын код эсвэл нэрний эхлэл. Өгөхгүй бол бүгд")
    ap.add_argument("--hours", type=float, default=6)
    ap.add_argument("--list", type=int, default=0, help="алдагдсан жишээ хэвлэх тоо")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        q = db.query(ParkingSite).filter(ParkingSite.is_active.is_(True))
        if args.site:
            site = q.filter(ParkingSite.site_code == args.site).first() \
                or q.filter(ParkingSite.name.ilike(f"{args.site}%")).first()
            if not site:
                names = ", ".join(f"{s.site_code}={s.name}" for s in q.all())
                sys.exit(f"«{args.site}» олдсонгүй. Байгаа нь: {names}")
            sites = [site]
        else:
            sites = [s for s in q.all()
                     if db.query(Device.id).filter(Device.site_id == s.id,
                                                   Device.device_type == "camera",
                                                   Device.status == "active").first()]
        for s in sites:
            check_site(db, s, args.hours, args.list)
    finally:
        db.close()


if __name__ == "__main__":
    main()
