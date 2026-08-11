"""Nested (дотор) зогсоолын оношилгоо — систем яагаад «3 дотор» гэж байхад
бодитоор 45 машин дотор байгааг илрүүлнэ.

Ажиллуулах (сервер дээр, backend хавтаст):
    venv/bin/python tools/nested_diag.py RASH                  # тойм оношилгоо
    venv/bin/python tools/nested_diag.py RASH --plates f.txt   # бодит жагсаалттай тулгах
    venv/bin/python tools/nested_diag.py RASH --fix f.txt      # session байгаа ч
        # «дотор» гэж тэмдэглэгдээгүй машинуудын тоолуурыг ОДООНООС зогсооно
        # (өнгөрсөн хугацаа нөхөгдөхгүй ч энэ мөчөөс хойш үнэгүй болно)

f.txt = мөр бүрт нэг улсын дугаар (бодитоор дотор байгаа машинууд).
"""
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal
from app.models import AuditLog, Device, LprEvent, ParkingSession, ParkingSite
from app.session_logic import plates_ocr_similar

TZ = timedelta(hours=8)
_ACTIVE = ("OPEN", "AWAITING_PAYMENT", "PAID")


def L(dt):
    return (dt + TZ).strftime("%m-%d %H:%M") if dt else "—"


def overview(db, site):
    print(f"══ {site.name} ({site.site_code}) — nested оношилгоо ══\n")

    # 1. Дотоод камерууд зөв тэмдэглэгдсэн үү?
    devs = db.query(Device).filter(Device.site_id == site.id).all()
    inner = [d for d in devs if d.nested_inner]
    print(f"1) Төхөөрөмж: нийт {len(devs)}, дотоод (nested_inner) {len(inner)}")
    for d in devs:
        mark = "🔵 ДОТООД" if d.nested_inner else "  гадна"
        print(f"   {mark}  {d.device_type:8} {d.lane_dir:5} №{d.lane_no}"
              f"  {d.name or '?'}  {d.ip_address}  [{d.status}]"
              f"  сүүлд: {L(d.last_seen)}")
    if not inner:
        print("   ❌ ГОЛ АЛДАА: дотоод камер НЭГ Ч тэмдэглэгдээгүй! Дотоод хаалтны")
        print("      камерууд ЭНГИЙН орох/гарах гэж ажиллаж: тоолуур зогсохгүй,")
        print("      дотроос гарахад ТӨЛБӨР нэхэж, «дотор» тоолол 0 орчим байна.")
        print("      Засвар: Тохиргоо → Төхөөрөмж дээр дотоод камеруудын «Дотоод")
        print("      (nested)» чагтыг асаах (device.nested_inner=true).")

    # 2. Дотоод камеруудын сүүлийн 24 цагийн уншилт
    now = datetime.utcnow()
    if inner:
        ids = [d.id for d in inner]
        evs = (db.query(LprEvent)
               .filter(LprEvent.device_id.in_(ids),
                       LprEvent.created_at >= now - timedelta(hours=24))
               .order_by(LprEvent.created_at.desc()).all())
        print(f"\n2) Дотоод камерын уншилт (24ц): {len(evs)}")
        if not evs:
            print("   ❌ Дотоод камераас 24 цагт НЭГ Ч уншилт ирээгүй — камер event")
            print("      илгээхгүй байна (сүлжээ/тохиргоо/гацсан). camera_push_check.py-аар шалгах.")
        for e in evs[:15]:
            s = (db.query(ParkingSession)
                 .filter(ParkingSession.site_id == site.id,
                         ParkingSession.plate_number == e.plate_number,
                         ParkingSession.status.in_(_ACTIVE)).first())
            note = "session ✅" if s else "session ❌ (дугаар зөрсөн байж магадгүй)"
            print(f"   {L(e.created_at)}  {e.lane_dir:5}  «{e.plate_number}»  {note}")
        if len(evs) > 15:
            print(f"   … нийт {len(evs)}")

    # 3. Систем «дотор» гэж үзэж буй машинууд
    paused = (db.query(ParkingSession)
              .filter(ParkingSession.site_id == site.id,
                      ParkingSession.status.in_(_ACTIVE),
                      ParkingSession.paused_since.isnot(None))
              .order_by(ParkingSession.paused_since).all())
    print(f"\n3) Систем «дотор» гэж үзэж буй: {len(paused)}")
    for s in paused:
        print(f"   {s.plate_number}  дотор орсон: {L(s.paused_since)}  "
              f"(хуримтлагдсан {int(s.paused_minutes or 0)} мин)")


def compare(db, site, plates: list[str], fix: bool):
    now = datetime.utcnow()
    ok, unpaused, missing = [], [], []
    for p in plates:
        s = (db.query(ParkingSession)
             .filter(ParkingSession.site_id == site.id,
                     ParkingSession.plate_number == p,
                     ParkingSession.status.in_(_ACTIVE))
             .order_by(ParkingSession.entry_time.desc()).first())
        if s is None:
            # OCR-ойролцоо session байж магадгүй
            near = [x for x in db.query(ParkingSession)
                    .filter(ParkingSession.site_id == site.id,
                            ParkingSession.status.in_(_ACTIVE)).all()
                    if plates_ocr_similar(p, x.plate_number)]
            missing.append((p, near[0].plate_number if len(near) == 1 else None))
        elif s.paused_since:
            ok.append(p)
        else:
            unpaused.append((p, s))

    print(f"══ Бодит жагсаалт ({len(plates)}) vs систем ══")
    print(f"   ✅ «дотор» гэж зөв тэмдэглэгдсэн: {len(ok)}")
    print(f"   ⚠ session байгаа ч «дотор» гэж тэмдэглэГДЭЭГҮЙ: {len(unpaused)}")
    for p, s in unpaused:
        print(f"      {p}  (орсон {L(s.entry_time)}, төлөв {s.status})")
    print(f"   ❌ энэ зогсоолд идэвхтэй session ОГТ алга: {len(missing)}")
    for p, near in missing:
        hint = f"  → OCR-ойролцоо «{near}» байна (Шалгах дээр дугаарыг засах)" if near else ""
        print(f"      {p}{hint}")

    if fix and unpaused:
        print(f"\n==> {len(unpaused)} машины тоолуурыг ОДООНООС зогсоож байна…")
        for p, s in unpaused:
            s.paused_since = now
            db.add(AuditLog(username="nested_diag", action="NESTED_MANUAL_PAUSE",
                            entity="session", entity_id=s.id,
                            detail={"plate": p, "site": site.site_code,
                                    "reason": "бодит тооллогоор дотор байсан"}))
        db.commit()
        print("    Болсон. Эдгээр машины төлбөрийн тоолуур энэ мөчөөс зогслоо —")
        print("    дотроос гарч гадна гарцад уншигдахад л үргэлжилнэ.")
    elif unpaused and not fix:
        print("\n   Засах бол: venv/bin/python tools/nested_diag.py "
              f"{site.site_code} --fix <файл>")


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return
    db = SessionLocal()
    site = (db.query(ParkingSite)
            .filter(ParkingSite.site_code == args[0].upper()).first())
    if not site:
        print(f"Зогсоол '{args[0]}' олдсонгүй")
        return

    plates_file = None
    fix = False
    if "--plates" in args:
        plates_file = args[args.index("--plates") + 1]
    if "--fix" in args:
        plates_file = args[args.index("--fix") + 1]
        fix = True

    if plates_file:
        import re
        with open(plates_file, encoding="utf-8") as f:
            # Клипбордоос ирдэг үл үзэгдэх тэмдэгтүүдийг (zero-width г.м) хамт цэвэрлэнэ
            plates = [re.sub(r"[^0-9A-ZА-ЯЁӨҮ]", "", line.upper()) for line in f]
        plates = [p for p in plates if p]
        compare(db, site, plates, fix)
    else:
        overview(db, site)


if __name__ == "__main__":
    main()
