#!/usr/bin/env python
"""Гэрээт машин яагаад хаалтаар гарахгүй байгааг оношлох.

    sudo /root/PARKING/backend/venv/bin/python /root/PARKING/tools/driver_check.py 1234УБА
    ... driver_check.py 1234УБА --site MONNIS
    ... driver_check.py --site MONNIS --recent    # тухайн зогсоолын сүүлийн гарцууд

«Бүртгэлтэй байхад хаалт нээгдэхгүй» гомдлын БОЛОМЖИТ ШАЛТГААН БҮРИЙГ дараалан
шалгаж, аль нь таарч байгааг хэлнэ:

  1. Дугаар ЯГ таарч байна уу (OCR/формат зөрүү, хоосон зай, кирилл/латин холилдсон)
  2. Бүртгэл ИДЭВХТЭЙ юу (is_active)
  3. Хугацаа хүчинтэй юу (valid_from ≤ одоо ≤ valid_to)
  4. ЗОГСООЛ таарч байна уу (site_id — өөр зогсоолын бүртгэл энд үйлчлэхгүй)
  5. ӨР (нөхөн төлбөр) байгаа юу — өртэй гэрээт машин АВТОМАТААР ГАРАХГҮЙ
  6. Хар жагсаалтад байгаа юу
  7. Сүүлийн LPR уншилтууд — камер юу гэж уншсан бэ
  8. Сүүлийн хаалтны командууд — илгээгдсэн үү, амжилттай юу
"""
import os
import sys
from datetime import datetime, timedelta

BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
sys.path.insert(0, BACKEND)
os.chdir(BACKEND)

from app.database import SessionLocal  # noqa: E402
from app.models import (BarrierCommand, BlacklistEntry, Compensation, LprEvent,  # noqa: E402
                        ParkingSession, ParkingSite, RegisteredDriver)
from app.session_logic import is_valid_plate, normalize_plate, plates_ocr_similar  # noqa: E402

OK, BAD, WARN = "  ✓", "  ✗ <<<", "  !"


def check_plate(db, raw_plate: str, site_code: str | None):
    plate = normalize_plate(raw_plate)
    print(f"\n═══ {plate} ═══")
    if plate != raw_plate.strip().upper():
        print(f"{WARN} оруулсан «{raw_plate}» → жигдэрсэн «{plate}»")
    if not is_valid_plate(plate):
        print(f"{WARN} стандарт формат биш (4 орон + 3 кирилл үсэг). "
              f"Камер ийм дугаарыг өөрөөр уншиж болзошгүй.")

    site = None
    if site_code:
        site = db.query(ParkingSite).filter(ParkingSite.site_code == site_code.upper()).first()
        if not site:
            print(f"{BAD} «{site_code}» зогсоол олдсонгүй")
            return
        print(f"  Зогсоол: {site.name} ({site.site_code})")

    now = datetime.utcnow()

    # ── 1-4. Гэрээт бүртгэл ──
    print("\n  ── Гэрээт бүртгэл ──")
    rows = db.query(RegisteredDriver).filter(RegisteredDriver.plate_number == plate).all()
    if not rows:
        print(f"{BAD} ЭНЭ ДУГААРААР БҮРТГЭЛ АЛГА")
        # OCR-ойролцоо бүртгэл байна уу (дугаар буруу бичигдсэн байж болно)
        near = [d for d in db.query(RegisteredDriver).filter(
            RegisteredDriver.is_active.is_(True)).all()
            if plates_ocr_similar(plate, d.plate_number)]
        if near:
            print("      ГЭХДЭЭ ойролцоо дугаартай бүртгэл БАЙНА — үсэг андуурсан байж болзошгүй:")
            for d in near[:5]:
                print(f"        • {d.plate_number}  {d.full_name or ''} {d.company or ''}")
            print("      → Бүртгэлтэй жолооч хуудсанд дугаарыг ЗАСНА уу.")
    for d in rows:
        s = db.get(ParkingSite, d.site_id) if d.site_id else None
        scope = f"{s.site_code} ({s.name})" if s else "БҮХ зогсоол"
        print(f"    бүртгэл: {d.full_name or '(нэргүй)'} · {d.company or '-'} · хамрах: {scope}")
        print(f"      {OK if d.is_active else BAD} идэвхтэй: {d.is_active}")
        vf_ok = d.valid_from is None or d.valid_from <= now
        vt_ok = d.valid_to is None or d.valid_to >= now
        print(f"      {OK if vf_ok else BAD} эхлэх огноо: {d.valid_from} "
              f"{'(ирээдүйд — ХҮЧИНГҮЙ)' if not vf_ok else ''}")
        print(f"      {OK if vt_ok else BAD} дуусах огноо: {d.valid_to} "
              f"{'(ХУГАЦАА ДУУССАН)' if not vt_ok else ''}")
        if site:
            site_ok = d.site_id is None or d.site_id == site.id
            print(f"      {OK if site_ok else BAD} энэ зогсоолд үйлчлэх эсэх: {site_ok}"
                  f"{'' if site_ok else '  ← ӨӨР зогсоолын бүртгэл!'}")

    # ── 5. Өр ──
    print("\n  ── Нөхөн төлбөр (өр) ──")
    debts = db.query(Compensation).filter(Compensation.plate_number == plate,
                                          Compensation.status == "PENDING").all()
    if debts:
        total = sum(float(c.amount) for c in debts)
        print(f"{BAD} ТӨЛӨГДӨӨГҮЙ ӨР {len(debts)} ширхэг, нийт {total:,.0f}₮")
        print("      ⚠ ЭНЭ НЬ ХААЛТ НЭЭГДЭХГҮЙ БАЙХ ХАМГИЙН ТҮГЭЭМЭЛ ШАЛТГААН.")
        print("      Гэрээт машин ч өртэй бол автоматаар гарахгүй (оператор өрийг цуглуулна).")
        for c in debts[:5]:
            print(f"        • {c.created_at:%m-%d %H:%M}  {float(c.amount):,.0f}₮  {c.reason}")
        if len(debts) >= 3:
            print("      ⚠ 3+ өртэй тул ХАР ЖАГСААЛТАД автоматаар орсон байж магадгүй.")
    else:
        print(f"{OK} өргүй")

    # ── 6. Хар жагсаалт ──
    bl = db.query(BlacklistEntry).filter(BlacklistEntry.plate_number == plate,
                                         BlacklistEntry.is_active.is_(True)).first()
    print(f"\n  ── Хар жагсаалт ──\n{BAD if bl else OK} "
          f"{'БАЙНА: ' + (bl.reason or '') if bl else 'байхгүй'}")

    # ── 7. Сүүлийн LPR уншилтууд ──
    print("\n  ── Сүүлийн 10 LPR уншилт (камер юу уншсан) ──")
    evs = (db.query(LprEvent).filter(LprEvent.plate_number == plate)
           .order_by(LprEvent.created_at.desc()).limit(10).all())
    if not evs:
        print(f"{WARN} энэ дугаараар уншилт БАЙХГҮЙ — камер өөр дугаар уншиж байж магадгүй")
    for e in evs:
        st = db.get(ParkingSite, e.site_id)
        print(f"    {e.created_at:%m-%d %H:%M:%S}  {e.lane_dir:5}  "
              f"{(st.site_code if st else '?'):8} conf={e.confidence:.0f} "
              f"{'ЗӨВШӨӨРСӨН' if e.accepted else 'ТАТГАЛЗСАН: ' + (e.reject_reason or '')}")

    # ── 8. Сүүлийн session + хаалтны команд ──
    print("\n  ── Сүүлийн 5 session ──")
    sess = (db.query(ParkingSession).filter(ParkingSession.plate_number == plate)
            .order_by(ParkingSession.entry_time.desc()).limit(5).all())
    for s in sess:
        st = db.get(ParkingSite, s.site_id)
        print(f"    {s.entry_time:%m-%d %H:%M}  {(st.site_code if st else '?'):8} "
              f"{s.status:16} гэрээт={s.is_registered} дүн={float(s.total_fee or 0):,.0f}₮")
        cmds = (db.query(BarrierCommand).filter(BarrierCommand.session_id == s.id)
                .order_by(BarrierCommand.created_at).all())
        for c in cmds:
            ms = f"{c.duration_ms}мс" if c.duration_ms is not None else "?"
            print(f"        хаалт {c.command:6} {c.command_source:12} {c.status:8} {ms:>8}  "
                  f"{(c.response_text or '')[:60]}")
        if not cmds:
            print("        ⚠ хаалтны команд ОГТ илгээгдээгүй")

    print("\n  ── ДҮГНЭЛТ ──")
    reasons = []
    if not rows:
        reasons.append("бүртгэл алга (эсвэл дугаар зөрүүтэй)")
    else:
        if not any(d.is_active for d in rows):
            reasons.append("бүртгэл идэвхгүй")
        if not any((d.valid_to is None or d.valid_to >= now) for d in rows):
            reasons.append("гэрээний хугацаа дууссан")
        if site and not any(d.site_id is None or d.site_id == site.id for d in rows):
            reasons.append("бүртгэл ӨӨР зогсоолынх")
    if debts:
        reasons.append(f"төлөгдөөгүй өр {len(debts)}ш — автоматаар гарахгүй")
    if bl:
        reasons.append("хар жагсаалтад байна")
    if reasons:
        for r in reasons:
            print(f"    ✗ {r}")
    else:
        print("    Бүртгэл/өр/хар жагсаалтын талаас САААДГҮЙ.")
        print("    → Асуудал камерын уншилт эсвэл хаалтны командад байна.")
        print("      Дээрх LPR мөрүүдээс камер ямар дугаар уншсаныг хараарай;")
        print("      хаалтны командын хугацаа/төлөвийг шалгана уу.")


def recent_exits(db, site_code: str, minutes: int = 120):
    site = db.query(ParkingSite).filter(ParkingSite.site_code == site_code.upper()).first()
    if not site:
        print(f"«{site_code}» зогсоол олдсонгүй")
        return
    since = datetime.utcnow() - timedelta(minutes=minutes)
    print(f"\n═══ {site.name} — сүүлийн {minutes} минутын ГАРАХ уншилтууд ═══")
    evs = (db.query(LprEvent).filter(LprEvent.site_id == site.id,
                                     LprEvent.lane_dir == "exit",
                                     LprEvent.created_at >= since)
           .order_by(LprEvent.created_at.desc()).limit(40).all())
    if not evs:
        print("  (уншилт алга)")
    for e in evs:
        reg = db.query(RegisteredDriver).filter(
            RegisteredDriver.plate_number == e.plate_number,
            RegisteredDriver.is_active.is_(True)).first()
        debt = db.query(Compensation).filter(Compensation.plate_number == e.plate_number,
                                             Compensation.status == "PENDING").count()
        flags = []
        if reg:
            flags.append("ГЭРЭЭТ")
        if debt:
            flags.append(f"ӨР×{debt}")
        if not e.accepted:
            flags.append("ТАТГАЛЗСАН")
        print(f"  {e.created_at:%m-%d %H:%M:%S}  {e.plate_number:10} conf={e.confidence:.0f} "
              f"{' '.join(flags)}")


def main() -> int:
    args = [a for a in sys.argv[1:]]
    site_code = None
    if "--site" in args:
        i = args.index("--site")
        site_code = args[i + 1] if len(args) > i + 1 else None
        args = args[:i] + args[i + 2:]
    recent = "--recent" in args
    args = [a for a in args if a != "--recent"]

    db = SessionLocal()
    try:
        if recent:
            if not site_code:
                print("--recent-д --site ЗААВАЛ хэрэгтэй")
                return 1
            recent_exits(db, site_code)
            return 0
        if not args:
            print(__doc__)
            return 1
        for plate in args:
            check_plate(db, plate, site_code)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
