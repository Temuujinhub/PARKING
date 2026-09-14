"""Онцгой гаргалт (2026-09-14) — free_exit эрхгүй оператор/POS-ийн зурагтай гаргалт.

    cd backend && venv/bin/python tests/test_special_exit.py

Амьд Postgres шаардана (түр зогсоол/төхөөрөмж/session үүсгээд төгсгөлд устгана).
Шалгах зүйл:
  • special-exit route нь cashier эрхээр (free_exit ШААРДАХГҮЙ) ажиллана
  • ХБИ/түргэн/бүртгэлгүй: баталгаажуулах зураггүй → 409; зурагтай → MANUAL_CLOSED,
    нөхөн төлбөр үүсэхгүй, аудит SPECIAL_EXIT + verify_snapshot, Түүхийн шошиг
  • paid_no_open: төлөөгүй → 400; төлсөн PAID → CLOSED + хаалт
  • _session_out: орох/гарах камерын нэр; LED: өр тусдаа мөр; bootstrap: kinds
"""
import asyncio
import os
import sys
import tempfile
import uuid
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings  # noqa: E402

settings.barrier_mock = True
settings.snapshot_enabled = False
settings.screen_enabled = False
settings.snapshot_dir = tempfile.mkdtemp(prefix="snap-test-")

from fastapi import HTTPException  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models import AuditLog, Compensation, Device, ParkingSession, ParkingSite, User  # noqa: E402
from app.routers import payments_router as pr  # noqa: E402
from app.routers import sessions_router as sr  # noqa: E402
from app.services import snapshot as snap  # noqa: E402

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗ <<< FAIL'} {name}{'' if cond else f'  [{extra}]'}")


def main():
    db = SessionLocal()
    tag = uuid.uuid4().hex[:6]
    site = ParkingSite(name=f"TEST-SPX-{tag}", site_code=f"tspx{tag}", zone_code="T")
    db.add(site); db.flush()
    cam_in = Device(site_id=site.id, name="Орох камер", device_type="camera", lane_no=1,
                    lane_dir="entry", device_key=f"k-{tag}-in", ip_address="10.0.0.1")
    cam_out = Device(site_id=site.id, name="Гарах камер 2", device_type="camera", lane_no=2,
                     lane_dir="exit", device_key=f"k-{tag}-out", ip_address="10.0.0.2")
    bar_out = Device(site_id=site.id, name="Хаалт гарах 2", device_type="barrier", lane_no=2,
                     lane_dir="exit", ip_address="10.0.0.2")
    devs = [cam_in, cam_out, bar_out]
    db.add_all(devs); db.flush()
    now = datetime.utcnow()
    P = tag[:4].upper()
    s_hbi = ParkingSession(site_id=site.id, plate_number=f"{P}ААА", entry_time=now - timedelta(hours=2),
                           exit_time=now, status="AWAITING_PAYMENT", entry_device_id=cam_in.id,
                           exit_device_id=cam_out.id, total_fee=3000)
    s_paid = ParkingSession(site_id=site.id, plate_number=f"{P}БББ", entry_time=now - timedelta(hours=1),
                            status="PAID", paid_at=now - timedelta(minutes=2),
                            exit_deadline=now + timedelta(minutes=13), total_fee=2000,
                            base_fee=1818, vat_amount=182, entry_device_id=cam_in.id)
    s_unpaid = ParkingSession(site_id=site.id, plate_number=f"{P}ВВВ", entry_time=now - timedelta(hours=1),
                              status="OPEN", entry_device_id=cam_in.id)
    sess = [s_hbi, s_paid, s_unpaid]
    db.add_all(sess); db.commit()
    op = User(username=f"spx-{tag}", role="OPERATOR", password_hash="x", full_name="OP", site_ids=[site.id])
    pos = User(username=f"spxpos-{tag}", role="POS", password_hash="x", full_name="POS", site_ids=[site.id])
    admin = User(username=f"spxadm-{tag}", role="ADMIN", password_hash="x", full_name="ADM")

    fetch_calls = {"n": 0, "ip": None}

    async def fake_fetch(ip, creds=None):
        fetch_calls["n"] += 1; fetch_calls["ip"] = ip
        return b"\xff\xd8" + b"J" * 3000

    orig_fetch = snap._fetch_from_camera
    snap._fetch_from_camera = fake_fetch

    def call(fn, *a, **kw):
        try:
            return asyncio.run(fn(*a, **kw))
        except HTTPException as e:
            db.rollback(); return e

    try:
        print("\nЭрх (route-ийн шаардлага):")
        from app.auth import has_permission
        for path in ("/special-exit", "/special-exit/snapshot"):
            route = next(r for r in sr.router.routes if r.path.endswith(path))
            mods = sum((getattr(d.call, "required_modules", ()) for d in route.dependant.dependencies), ())
            check(f"{path}: cashier эрх (free_exit биш)", "cashier" in mods and "free_exit" not in mods, str(mods))
            check(f"{path}: OPERATOR болон POS хоёулаа эрхтэй",
                  any(has_permission(op, m) for m in mods) and any(has_permission(pos, m) for m in mods))

        print("\n_session_out — аль камер:")
        d = sr._session_out(db, s_hbi, with_fee=True)
        check("entry_device_name = Орох камер", d.get("entry_device_name") == "Орох камер", str(d.get("entry_device_name")))
        check("exit_device_name = Гарах камер 2, exit_lane_no=2",
              d.get("exit_device_name") == "Гарах камер 2" and d.get("exit_lane_no") == 2)

        print("\nХБИ — зураггүй бол 409, зурагтай бол гаргана:")
        e = call(sr.special_exit, s_hbi.id, {"kind": "nope"}, db=db, user=op)
        check("буруу kind → 400", isinstance(e, HTTPException) and e.status_code == 400)
        r = call(sr.special_exit_snapshot, s_hbi.id, {}, db=db, user=op)
        check("snapshot: session-ий гарах камераас (10.0.0.2) авав",
              isinstance(r, dict) and r["ok"] and fetch_calls["ip"] == "10.0.0.2" and r["camera"] == "Гарах камер 2", str(r))
        db.expire_all()
        s = db.get(ParkingSession, s_hbi.id)
        check("verify_snapshot хадгалагдав + файл байна", bool(s.verify_snapshot)
              and os.path.isfile(os.path.join(settings.snapshot_dir, s.verify_snapshot)), str(s.verify_snapshot))
        r = call(sr.special_exit, s_hbi.id, {"kind": "hbi", "note": "тэргэнцэртэй"}, db=db, user=op)
        check("зурагтай → MANUAL_CLOSED, барьер (mock) нээгдэв",
              isinstance(r, dict) and r["status"] == "MANUAL_CLOSED" and r["barrier_opened"] is True, str(r)[:160])
        db.expire_all()
        s = db.get(ParkingSession, s_hbi.id)
        check("note-д «Онцгой гаргалт: ХБИ … — тэргэнцэртэй»", "Онцгой гаргалт: ХБИ" in (s.note or "") and "тэргэнцэртэй" in (s.note or ""), s.note)
        check("нөхөн төлбөр ҮҮСЭЭГҮЙ", db.query(Compensation).filter(Compensation.session_id == s.id).count() == 0)
        a = (db.query(AuditLog).filter(AuditLog.entity_id == s.id, AuditLog.action == "SPECIAL_EXIT")
             .order_by(AuditLog.created_at.desc()).first())
        check("аудит SPECIAL_EXIT kind=hbi, зурагтай, reason_code=hbi",
              a is not None and a.detail.get("kind") == "hbi" and a.detail.get("snapshot") == s.verify_snapshot
              and a.detail.get("reason_code") == "hbi", str(a.detail if a else None))
        rows = sr._attach_close_info(db, [sr._session_out(db, s)])
        check("Түүхийн шошиг «Онцгой гаргалт (зурагтай)»",
              rows[0]["closed_by"] and rows[0]["closed_by"]["label"] == "Онцгой гаргалт (зурагтай)", str(rows[0].get("closed_by")))
        e = call(sr.special_exit, s_hbi.id, {"kind": "hbi"}, db=db, user=op)
        check("хаагдсан session дахин → 400", isinstance(e, HTTPException) and e.status_code == 400)

        print("\nОператор зураггүй ч гаргана (камер зураг өгөхгүй үед):")
        r = call(sr.special_exit, s_unpaid.id, {"kind": "emergency"}, db=db, user=op)
        check("emergency зураггүй ОПЕРАТОР → MANUAL_CLOSED", isinstance(r, dict) and r["status"] == "MANUAL_CLOSED", str(r)[:120])
        db.expire_all()
        s = db.get(ParkingSession, s_unpaid.id)
        check("exit_device_id зогсоолын гарах камер болов", s.exit_device_id == cam_out.id)
        a = (db.query(AuditLog).filter(AuditLog.entity_id == s.id, AuditLog.action == "SPECIAL_EXIT").first())
        check("аудитад snapshot_fresh=false (зураггүй гаргалт ялгагдана)",
              a is not None and a.detail.get("snapshot_fresh") is False and not a.detail.get("snapshot"), str(a.detail if a else None))
        _ = admin

        print("\nТөлөөд нээгдээгүй:")
        e = call(sr.special_exit, s_hbi.id, {"kind": "paid_no_open"}, db=db, user=op)
        check("төлөөгүй → 400", isinstance(e, HTTPException) and e.status_code == 400, str(getattr(e, "detail", e)))
        r = call(sr.special_exit, s_paid.id, {"kind": "paid_no_open"}, db=db, user=pos)
        check("PAID → CLOSED + хаалт нээгдэв (зураг шаардахгүй)",
              isinstance(r, dict) and r["status"] == "CLOSED" and r["barrier_opened"] is True, str(r)[:160])
        db.expire_all()
        s = db.get(ParkingSession, s_paid.id)
        check("exit_time бичигдэж, exit_confirmed", s.exit_time is not None and s.exit_confirmed is True)
        r = call(sr.special_exit, s_paid.id, {"kind": "paid_no_open"}, db=db, user=pos)
        check("саяхан хаагдсан PAID → зөвхөн хаалт дахин нээнэ (CLOSED хэвээр)",
              isinstance(r, dict) and r["status"] == "CLOSED" and r["barrier_opened"] is True, str(r)[:160])

        print("\nLED — өр тусдаа мөр:")
        from app.session_logic import _fee_screen_text
        t = _fee_screen_text("1234УБА", 65, 3000, 0)
        check("өргүй: 3 мөр (дугаар/хугацаа/дүн)", t.split("\n") == ["1234УБА", "1ц 05м", "3000T"], repr(t))
        t = _fee_screen_text("1234УБА", 65, 3000, 25000)
        check("өртэй: 4 дэх мөр «Ur 25000T», дүн НИЙЛҮҮЛЭГДЭЭГҮЙ",
              t.split("\n") == ["1234УБА", "1ц 05м", "3000T", "Ur 25000T"], repr(t))

        print("\nLED — зогсоолын «Төлбөр хүлээх дэлгэц» мөрүүд (screen_config.fee):")
        site.screen_config = {"fee": [{"type": "plate"}, {"type": "amount"}, {"type": "debt", "text": "Ur"},
                                      {"type": "text", "text": "Tulnu uu"}]}
        db.commit()
        t = _fee_screen_text("1234УБА", 65, 3000, 0, db=db, site_id=site.id)
        check("өргүй: өрийн мөр хасагдана", t.split("\n") == ["1234УБА", "3000T", "Tulnu uu"], repr(t))
        t = _fee_screen_text("1234УБА", 65, 3000, 25000, db=db, site_id=site.id)
        check("өртэй: тохируулсан угтвартай өрийн мөр", t.split("\n") == ["1234УБА", "3000T", "Ur 25000T", "Tulnu uu"], repr(t))
        from app.routers.admin_router import _check_screen_config
        c = _check_screen_config({"fee": [{"type": "debt", "text": "Өр"}, {"type": "amount"}], "exit": [{"type": "debt"}]})
        check("_check_screen_config fee/debt зөвшөөрнө", c and c["fee"][0] == {"type": "debt", "text": "Өр"} and c["exit"] == [{"type": "debt"}], str(c))
        e = None
        try:
            _check_screen_config({"fee": [{"type": "payment"}]})
        except HTTPException as ex:
            e = ex
        check("fee-д payment төрөл байхгүй → 400", e is not None and e.status_code == 400)

        print("\nPOS bootstrap:")
        r = pr.pos_bootstrap(terminal_id=None, db=db, user=pos)
        kinds = [k["kind"] for k in r.get("special_exit_kinds", [])]
        check("special_exit_kinds 4 төрөл", kinds == ["paid_no_open", "hbi", "emergency", "no_session"], str(kinds))
        check("hbi шалтгаан жагсаалтад", any(x["code"] == "hbi" for x in r["open_reasons"]))
    finally:
        snap._fetch_from_camera = orig_fetch
        db.rollback()
        ids = [s.id for s in sess]
        db.query(AuditLog).filter(AuditLog.entity_id.in_(ids)).delete(synchronize_session=False)
        from app.models import BarrierCommand
        db.query(BarrierCommand).filter(BarrierCommand.session_id.in_(ids)).delete(synchronize_session=False)
        db.query(Compensation).filter(Compensation.session_id.in_(ids)).delete(synchronize_session=False)
        db.query(ParkingSession).filter(ParkingSession.id.in_(ids)).delete(synchronize_session=False)
        db.query(Device).filter(Device.id.in_([d.id for d in devs])).delete(synchronize_session=False)
        db.query(ParkingSite).filter(ParkingSite.id == site.id).delete(synchronize_session=False)
        db.commit(); db.close()
    print(f"\n{PASS} ✓  {FAIL} ✗")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
