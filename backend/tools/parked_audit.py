"""«Зогсоолд байгаа» гэж харагдаж буй машинуудын аудит — хэд нь БОДИТ, хэд нь
аль хэдийн ЯВЧИХСАН (гарах уншилтгүй) вэ, «Тооцоолсон дүн»-гийн хэд нь бодит.

Хяналтын самбарын «Зогсоолд байгаа N машин · Тооцоолсон нийт дүн X₮» нь БҮХ
нээлттэй session-ий хуримтлагдсан төлбөрийн нийлбэр. Гэвч тэдгээрийн зарим нь:
  • аль хэдийн явсан (гарах уншилт алдагдсан) → дүн нь ХУУРМАГ, хэзээ ч цугларахгүй
  • орж ирээд ШУУД маневар хийж гарсан → мөн адил хуурмаг
  • гэрээт/үнэгүй → дүн 0
  • үнэхээр зогсож байгаа → дүн БОДИТ, цуглуулна

Хэрэгсэл нээлттэй session бүрийг ангилж, «бодит цуглуулах дүн» ба «хуурмаг дүн»-г
ялгана. `--cameras` нэмбэл камерын ӨӨРИЙН логтой тулгаж «аль хэдийн явсан»-ыг
БАТ тогтооно (удаан — камер бүрт хандана).

Ажиллуулах (production сервер дээр, backend хавтаст):
    venv/bin/python tools/parked_audit.py
    venv/bin/python tools/parked_audit.py --cameras          # камерын логтой тулгах
    venv/bin/python tools/parked_audit.py --site RASH --list 30

Зөвхөн УНШИНА.
"""
import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import func

from app.config import settings
from app.database import SessionLocal
from app.models import (Compensation, Device, LprEvent, ParkingSession, ParkingSite)
from app.session_logic import is_valid_plate, paid_total, session_fee_info

TZ = timedelta(hours=8)
_ACTIVE = ("OPEN", "AWAITING_PAYMENT", "PAID")
# Эдгээр цагаас удаан «зогсож байгаа» нь бодит бус байх магадлал өснө
SUSPECT_H = 12   # энэ цагаас дээш → хатуу сэжигтэй (нэг өдөр+)
LONG_H = 6       # энэ цагаас дээш → сэжигтэй


def L(dt):
    return (dt + TZ).strftime("%m-%d %H:%M") if dt else "—"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", help="зөвхөн нэг зогсоол (код эсвэл нэрний эхлэл)")
    ap.add_argument("--cameras", action="store_true",
                    help="камерын логтой тулгаж «аль хэдийн явсан»-ыг БАТ тогтоох (удаан)")
    ap.add_argument("--list", type=int, default=0)
    args = ap.parse_args()

    db = SessionLocal()
    try:
        now = datetime.utcnow()
        q = db.query(ParkingSession).filter(
            ParkingSession.status.in_(("OPEN", "AWAITING_PAYMENT")))
        site = None
        if args.site:
            site = (db.query(ParkingSite).filter(ParkingSite.site_code == args.site).first()
                    or db.query(ParkingSite)
                    .filter(ParkingSite.name.ilike(f"{args.site}%")).first())
            if not site:
                sys.exit(f"«{args.site}» олдсонгүй")
            q = q.filter(ParkingSession.site_id == site.id)
        rows = q.order_by(ParkingSession.entry_time).all()
        if not rows:
            print("Нээлттэй session алга.")
            return

        plates = {s.plate_number for s in rows}

        # ── Гарах уншилт (аль хэдийн ЯВСАН гэдгийн DB-ийн шинж) ──────────────
        # Дотоод (nested) камерын уншилтыг ОРУУЛАХГҮЙ — тэр нь зогсоолоос гарах биш.
        inner_ids = {i for (i,) in db.query(Device.id)
                     .filter(Device.nested_inner.is_(True)).all()}
        exit_reads: dict = defaultdict(list)
        floor = min(s.entry_time for s in rows)
        for plate, at, dev in (db.query(LprEvent.plate_number, LprEvent.created_at,
                                        LprEvent.device_id)
                               .filter(LprEvent.plate_number.in_(plates),
                                       LprEvent.lane_dir == "exit",
                                       LprEvent.created_at >= floor).all()):
            if dev not in inner_ids:
                exit_reads[plate].append(at)

        # ── Хуучин өр (PENDING Compensation) ────────────────────────────────
        debt: dict = {}
        for plate, amt, cnt in (db.query(Compensation.plate_number,
                                         func.sum(Compensation.amount), func.count())
                                .filter(Compensation.plate_number.in_(plates),
                                        Compensation.status == "PENDING")
                                .group_by(Compensation.plate_number).all()):
            debt[plate] = (float(amt or 0), cnt)

        # ── Камерын лог (заавал биш, БАТ тогтоох) ───────────────────────────
        cam_exit: dict = {}
        if args.cameras:
            from app.services.camera_records import site_camera_events, plates_similar
            from app.session_logic import normalize_plate
            site_ids = {s.site_id for s in rows}
            for sid in site_ids:
                try:
                    cam = site_camera_events(db, sid, hours=48)
                except Exception:  # noqa: BLE001
                    continue
                exits = defaultdict(list)
                for e in cam["events"]:      # inner_events ОРОХГҮЙ (аль хэдийн салсан)
                    if e["lane_dir"] == "exit" and e["plate"]:
                        exits[normalize_plate(e["plate"])].append(e["time"])
                for s in rows:
                    if s.site_id != sid:
                        continue
                    t = next((x for x in exits.get(s.plate_number, []) if x > s.entry_time), None)
                    if not t:   # OCR ойролцоо
                        for p2, ts in exits.items():
                            if plates_similar(s.plate_number, p2):
                                t = next((x for x in ts if x > s.entry_time), None)
                                if t:
                                    break
                    if t:
                        cam_exit[s.id] = t

        # ── Ангилал ─────────────────────────────────────────────────────────
        buckets: dict = defaultdict(lambda: {"n": 0, "due": 0.0})
        samples: dict = defaultdict(list)
        maneuver = 0
        for s in rows:
            fee = session_fee_info(db, s)
            due = max(0.0, float(fee["total_fee"]) - paid_total(db, s))
            dur_h = (now - s.entry_time).total_seconds() / 3600 if s.entry_time else 0
            ex = exit_reads.get(s.plate_number, [])
            # Орж ирээд ШУУД (suspicious_exit_minutes) гарах уншилт = маневар
            man = any(0 <= (t - s.entry_time).total_seconds() / 60
                      <= settings.suspicious_exit_minutes for t in ex)

            if fee.get("is_free") or s.is_registered:
                k = "Гэрээт / үнэгүй (0₮)"
            elif s.id in cam_exit:
                k = "ЯВСАН: камерын логт гарсан нь бий (хуурмаг дүн)"
            elif man:
                k = "МАНЕВАР: орж ирээд шууд гарах уншилттай (хуурмаг дүн)"
                maneuver += 1
            elif ex:
                k = "ЯВСАН: гарах уншилттай ч хаагдаагүй (хуурмаг дүн)"
            elif dur_h >= SUSPECT_H:
                k = f"СЭЖИГТЭЙ: {SUSPECT_H}ц+ зогссоор (магадгүй явсан)"
            elif dur_h >= LONG_H:
                k = f"Дунд: {LONG_H}-{SUSPECT_H}ц зогссор"
            else:
                k = "Бодит: саяхан орсон, зогсож байгаа"
            buckets[k]["n"] += 1
            buckets[k]["due"] += due
            samples[k].append((s, due, dur_h))

        total_due = sum(b["due"] for b in buckets.values())
        title = site.name if site else "БҮХ ЗОГСООЛ"
        print(f"══ {title} — «Зогсоолд байгаа» {len(rows)} машины аудит ══\n")
        print(f"Тооцоолсон нийт дүн: {total_due:,.0f}₮\n")
        print(f"{'ангилал':54}{'тоо':>5}{'дүн':>12}")

        def is_fake(k):
            return k.startswith(("ЯВСАН", "МАНЕВАР", "СЭЖИГТЭЙ"))
        for k in sorted(buckets, key=lambda x: -buckets[x]["due"]):
            b = buckets[k]
            flag = "  ⚠" if is_fake(k) else ""
            print(f"{k[:52]:54}{b['n']:5}{b['due']:12,.0f}{flag}")

        real = sum(b["due"] for k, b in buckets.items() if not is_fake(k))
        fake = total_due - real
        real_n = sum(b["n"] for k, b in buckets.items() if not is_fake(k))
        print(f"\n   БОДИТ цуглуулах ≈ {real:,.0f}₮ ({real_n} машин)")
        print(f"   ХУУРМАГ (аль хэдийн явсан/сэжигтэй) ≈ {fake:,.0f}₮ "
              f"({len(rows) - real_n} машин)")
        if not args.cameras:
            print("   ⓘ «СЭЖИГТЭЙ» нь ТААМАГ (хугацаагаар). Баталгаажуулахдаа "
                  "`--cameras` (камерын логтой тулгана).")

        # ── Хуучин өр ───────────────────────────────────────────────────────
        with_debt = [s for s in rows if s.plate_number in debt]
        debt_total = sum(debt[s.plate_number][0] for s in with_debt)
        print(f"\n   Хуучин ӨР (нээлттэй машины ард): {len(with_debt)} машин, "
              f"{debt_total:,.0f}₮ PENDING")
        print("   (энэ нь дээрх «тооцоолсон дүн»-д ОРООГҮЙ — тусдаа авлага)")

        if args.list:
            print(f"\nЖишээ ({args.list}, урт хугацаатай нь эхэнд):")
            shown = 0
            for s, due, dur_h in sorted(
                    [x for v in samples.values() for x in v],
                    key=lambda x: -x[2]):
                if shown >= args.list:
                    break
                d = debt.get(s.plate_number)
                dtxt = f"  өр {d[0]:,.0f}₮" if d else ""
                camx = "  ✓камер-гарсан" if s.id in cam_exit else ""
                print(f"   {s.plate_number:10} {L(s.entry_time)}  {dur_h:4.0f}ц  "
                      f"{due:>7,.0f}₮{camx}{dtxt}")
                shown += 1
    finally:
        db.close()


if __name__ == "__main__":
    main()
