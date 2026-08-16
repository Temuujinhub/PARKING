"""Камераар ОРСОН машин session АВСАН уу — ЦАГААР задалж, засвар ажилласныг батлах.

ЯАГААД ЭНЭ Ч ХЭРЭГТЭЙ ВЭ: `weekly_audit`-ийн «нөхөлт%» нь зөвхөн session ҮҮССЭН
машиныг хэмждэг. Камераар орсон ч session ОГТ аваагүй машин (сервер event
алдсан + camera_sync 8ц-ийн цонхоос гадуур) тэр хэмжүүрт харагдахгүй. Энэ
хэрэгсэл камерын орох логийг session-той тулгаж, «орсон ч бүртгэлгүй»-г ЦАГААР
задална — засвар (idle watchdog, log_tail) орсноос хойш алдагдал ТЭГ рүү орсон
эсэхийг шууд харуулна.

Ажиллуулах (production сервер дээр, backend хавтаст):
    venv/bin/python tools/entry_miss_diag.py --hours 48
    venv/bin/python tools/entry_miss_diag.py --hours 48 --site RASH

Камер бүрт хандана (site_camera_events) — ачаалал багатай үед. Зөвхөн УНШИНА.
"""
import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal
from app.models import ParkingSession, ParkingSite
from app.services.camera_records import plates_similar, site_camera_events
from app.session_logic import normalize_plate

TZ = timedelta(hours=8)  # УБ-ын цаг
BURST_SEC = 600          # нэг машины дараалсан орох уншилтыг нэгтгэх


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=48)
    ap.add_argument("--site", help="зөвхөн нэг зогсоол (код эсвэл нэрний эхлэл)")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        now = datetime.utcnow()
        sites = db.query(ParkingSite).filter(ParkingSite.is_active.is_(True)).all()
        if args.site:
            sites = [s for s in sites if s.site_code == args.site
                     or (s.name or "").lower().startswith(args.site.lower())]
            if not sites:
                sys.exit(f"«{args.site}» олдсонгүй")

        per_hour: dict = defaultdict(lambda: [0, 0, 0])   # [орсон, бүртгэлгүй, ҮНЭХЭЭР дутуу]
        site_tot: dict = defaultdict(lambda: [0, 0, 0])
        for site in sites:
            try:
                cam = site_camera_events(db, site.id, hours=args.hours)
            except Exception as e:  # noqa: BLE001
                print(f"── {site.name}: камерын лог уншсангүй — {e}")
                continue
            wstart = now - timedelta(hours=args.hours)
            # «Мэдэгдэж буй» = мужид орсон БҮХ session (хаагдсаныг оролцуулаад) +
            # одоо нээлттэй бүх session. sessions_router-ийн аудиттай ижил дүрэм.
            known = {p for (p,) in db.query(ParkingSession.plate_number)
                     .filter(ParkingSession.site_id == site.id,
                             ParkingSession.entry_time >= wstart).all()}
            known |= {p for (p,) in db.query(ParkingSession.plate_number)
                      .filter(ParkingSession.site_id == site.id,
                              ParkingSession.status.in_(
                                  ["OPEN", "AWAITING_PAYMENT", "PAID"])).all()}

            # Гадна ОРОХ уншилтууд (дотоод камер ОРОХГҮЙ — events л, inner_events биш)
            entries = [e for e in cam["events"]
                       if e["plate"] and (e.get("lane_dir") or "entry") != "exit"]
            entries.sort(key=lambda e: (normalize_plate(e["plate"]), e["time"]))
            uniq = []
            for e in entries:
                p = normalize_plate(e["plate"])
                if uniq and uniq[-1][0] == p \
                        and (e["time"] - uniq[-1][1]).total_seconds() < BURST_SEC:
                    continue
                uniq.append((p, e["time"]))

            known_list = list(known)
            for p, t in uniq:
                hour = (t + TZ).strftime("%m-%d %H:00")
                per_hour[hour][0] += 1
                site_tot[site.name][0] += 1
                if p not in known:
                    per_hour[hour][1] += 1
                    site_tot[site.name][1] += 1
                    # OCR зөрүү юу, ҮНЭХЭЭР дутуу юу? Ойролцоо дугаартай session
                    # байвал тэр машин session-той (зүгээр өөр уншсан) — жинхэнэ
                    # алдагдал БИШ. Аль нэгтэй ч ойролцоо биш бол ҮНЭХЭЭР дутуу.
                    if not any(plates_similar(p, k) for k in known_list):
                        per_hour[hour][2] += 1
                        site_tot[site.name][2] += 1

        if not per_hour:
            print("Камерын орох уншилт олдсонгүй.")
            return

        print(f"══ Камераар ОРСОН машин session АВСАН уу — сүүлийн {args.hours:g}ц ══\n")
        print("Зогсоол тутам ('дутуу' = OCR зөрүүг хассан ҮНЭХЭЭР алдагдсан):")
        print(f"   {'зогсоол':22}{'орсон':>8}{'бүртгэлгүй':>12}{'дутуу':>8}{'дутуу%':>9}")
        for name in sorted(site_tot, key=lambda n: -site_tot[n][2]):
            ent, miss, gone = site_tot[name]
            pct = gone * 100 // ent if ent else 0
            flag = "  ⚠" if pct >= 8 else ""
            print(f"   {name[:20]:22}{ent:8}{miss:12}{gone:8}{pct:8}%{flag}")

        print(f"\nЦагаар (УБ) — засвар 08-17 01:00-д орсон. 'дутуу' баганыг хар "
              "(OCR зөрүү хасагдсан):")
        print(f"   {'цаг':14}{'орсон':>8}{'дутуу':>8}{'дутуу%':>9}")
        for h in sorted(per_hour):
            ent, miss, gone = per_hour[h]
            pct = gone * 100 // ent if ent else 0
            bar = "█" * min(gone, 40)
            print(f"   {h:14}{ent:8}{gone:8}{pct:8}%  {bar}")

        tot_e = sum(v[0] for v in per_hour.values())
        tot_m = sum(v[1] for v in per_hour.values())
        tot_g = sum(v[2] for v in per_hour.values())
        print(f"\n   НИЙТ: {tot_e} орсон, {tot_m} бүртгэлгүй, "
              f"үүнээс {tot_g} нь ҮНЭХЭЭР дутуу ({tot_g * 100 // tot_e}%)")
        print(f"   ({tot_m - tot_g} нь OCR зөрүү — session өөр дугаараар үүссэн, алдагдаагүй)")
        print("   Засвараас ХОЙШХИ 'дутуу%' 0-1% бол засвар ажилласны БАТ нотолгоо.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
