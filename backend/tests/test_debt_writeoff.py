"""Санхүү → Өр цэвэрлэх: бөөнөөр цуцлах + тайлбар мөр дээрээ + аудит лог + автомат хориг цуцлалт.

    cd backend && venv/bin/python tests/test_debt_writeoff.py

Амьд Postgres шаардана (түр зогсоол + өр үүсгээд төгсгөлд нь устгана).
"""
import os
import sys
import uuid
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings  # noqa: E402

settings.barrier_mock = True
settings.snapshot_enabled = False
settings.screen_enabled = False

from fastapi import HTTPException  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models import AuditLog, BlacklistEntry, Compensation, ParkingSite, User  # noqa: E402
from app.routers import compensations_router as cr  # noqa: E402

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗ <<< FAIL'} {name}{'' if cond else f'  [{extra}]'}")


def main():
    db = SessionLocal()
    tag = uuid.uuid4().hex[:6].upper()
    site = ParkingSite(name=f"TEST-WO-{tag}", site_code=f"two{tag.lower()}", zone_code="T")
    other = ParkingSite(name=f"TEST-WO2-{tag}", site_code=f"two2{tag.lower()}", zone_code="T")
    db.add_all([site, other])
    db.flush()
    p1, p2 = f"{tag[:4]}ТСТ", f"{tag[2:6]}ТСБ"
    comps = [
        Compensation(site_id=site.id, plate_number=p1, amount=5000, reason="night_close",
                     created_at=datetime.utcnow() - timedelta(days=40)),
        Compensation(site_id=site.id, plate_number=p1, amount=3000, reason="unpaid_exit",
                     created_at=datetime.utcnow() - timedelta(days=2)),
        Compensation(site_id=site.id, plate_number=p2, amount=7000, reason="night_close"),
        Compensation(site_id=other.id, plate_number=p2, amount=1000, reason="night_close"),  # өөр зогсоол
    ]
    db.add_all(comps)
    # p1-д автомат хориг + гараар нэмсэн хориг
    bl_auto = BlacklistEntry(plate_number=p1, reason="3 удаагийн төлөгдөөгүй өр (автомат хориг)", created_by="систем")
    bl_manual = BlacklistEntry(plate_number=p1, reason="гараар — зөрчил", created_by="admin")
    db.add_all([bl_auto, bl_manual])
    db.commit()
    ids = [c.id for c in comps]
    superu = User(username=f"fin-{tag}", role="SUPER_ADMIN", password_hash="x", full_name="t")
    scoped = User(username=f"op-{tag}", role="FINANCE", password_hash="x", full_name="t",
                  site_ids=[site.id])

    try:
        print("\nШүүлтүүр:")
        r = cr.list_compensations(status="PENDING", site_id=site.id, min_days=0, limit=100,
                                  db=db, user=superu)
        check("site_id-аар зөвхөн тэр зогсоолын өр", {x["id"] for x in r["rows"]} == set(ids[:3]))
        check("total_pending зогсоолоор", r["total_pending"] == 15000, str(r["total_pending"]))
        r = cr.list_compensations(status="PENDING", site_id=site.id, min_days=30, limit=100,
                                  db=db, user=superu)
        check("min_days=30 → зөвхөн 40 хоногийн өр", [x["id"] for x in r["rows"]] == [ids[0]])

        print("\nТайлбаргүй/хоосон:")
        for body in ({"ids": ids[:1]}, {"ids": ids[:1], "reason": "ab"}, {"reason": "тест", "ids": []}):
            try:
                cr.write_off_debts(body, db=db, user=superu)
                check(f"{body} → 400", False)
            except HTTPException as e:
                check(f"{list(body)} → 400", e.status_code == 400, str(e.detail))
        db.rollback()
        check("юу ч өөрчлөгдөөгүй", db.get(Compensation, ids[0]).status == "PENDING")

        print("\nХүрээ (FINANCE, зөвхөн site):")
        r = cr.write_off_debts({"ids": [ids[3]], "reason": "өөр зогсоолын өр оролдлого"}, db=db, user=scoped)
        check("өөр зогсоолын өр алгасагдана", r["count"] == 0 and r["skipped"][0]["why"] == "өөр зогсоол", str(r))
        check("өр PENDING хэвээр", db.get(Compensation, ids[3]).status == "PENDING")

        print("\nБөөн цэвэрлэлт:")
        r = cr.write_off_debts({"ids": ids[:3] + ["00000000-0000-0000-0000-000000000000"],
                                "reason": "  Эмнэлгийн   ажилтан — өр хамаарахгүй ", "unblacklist": True},
                               db=db, user=scoped)
        check("3 өр цэвэрлэгдэв", r["count"] == 3, str(r))
        check("нийт дүн 15000", r["total"] == 15000, str(r["total"]))
        check("2 дугаар", r["plates"] == sorted([p1, p2]), str(r["plates"]))
        check("байхгүй id → missing=1", r["missing"] == 1, str(r["missing"]))
        check("batch_id бий", bool(r["batch_id"]))
        db.expire_all()
        c0 = db.get(Compensation, ids[0])
        check("status=CANCELLED", c0.status == "CANCELLED", c0.status)
        check("cancel_reason цэвэрлэгдсэн (зай нэгтгэсэн)", c0.cancel_reason == "Эмнэлгийн ажилтан — өр хамаарахгүй", repr(c0.cancel_reason))
        check("cancelled_by", c0.cancelled_by == scoped.username, c0.cancelled_by)
        check("cancelled_at", c0.cancelled_at is not None)
        logs = db.query(AuditLog).filter(AuditLog.action == "DEBT_WRITE_OFF",
                                         AuditLog.entity_id.in_(ids)).all()
        check("өр бүрд DEBT_WRITE_OFF аудит мөр", len(logs) == 3, str(len(logs)))
        d = next(l.detail for l in logs if l.entity_id == ids[0])
        check("аудит detail: plate/amount/reason/site/batch",
              d["plate"] == p1 and d["amount"] == 5000 and d["reason"] == c0.cancel_reason
              and d["site_id"] == site.id and d["batch_id"] == r["batch_id"], str(d))
        batch = db.query(AuditLog).filter(AuditLog.action == "DEBT_WRITE_OFF_BATCH",
                                          AuditLog.entity_id == r["batch_id"]).first()
        check("batch нэгтгэл лог", batch is not None and batch.detail["count"] == 3
              and batch.detail["total"] == 15000, str(batch and batch.detail))

        print("\nАвтомат хориг:")
        db.refresh(bl_auto); db.refresh(bl_manual)
        check("p1 автомат хориг идэвхгүй болов", bl_auto.is_active is False)
        check("гараар нэмсэн хориг ХЭВЭЭР", bl_manual.is_active is True)
        check("unblacklisted хариунд", r["unblacklisted"] == [p1], str(r["unblacklisted"]))
        # p2-д өөр зогсоол дээр PENDING өр үлдсэн — хориг байсан бол цуцлахгүй байх ёстой
        # (энд хориг байхгүй тул зөвхөн жагсаалтад ороогүйг шалгана)
        check("p2 (өр үлдсэн) unblacklisted-д ороогүй", p2 not in r["unblacklisted"])

        print("\nДахин цэвэрлэх оролдлого:")
        r2 = cr.write_off_debts({"ids": ids[:1], "reason": "давхар"}, db=db, user=superu)
        check("CANCELLED өр дахин цэвэрлэгдэхгүй (skipped)", r2["count"] == 0 and r2["skipped"], str(r2))

        print("\nЛог endpoint:")
        lg = cr.write_off_log(plate=None, site_id=site.id, limit=100, db=db, user=scoped)
        mine = [x for x in lg["rows"] if x["compensation_id"] in ids]
        check("зогсоолын 3 мөр", len(mine) == 3, str(len(mine)))
        check("мөр: хэн/дугаар/дүн/тайлбар/төрөл", mine[0]["by"] == scoped.username and mine[0]["plate"]
              and mine[0]["amount"] and mine[0]["reason"] and mine[0]["kind"] == "write_off", str(mine[0]))
        lg = cr.write_off_log(plate=p2, site_id=None, limit=100, db=db, user=scoped)
        check("дугаараар шүүх + FINANCE хүрээ (өөр зогсоолынх орохгүй)",
              [x["plate"] for x in lg["rows"] if x["compensation_id"] in ids] == [p2])

        print("\nГанц цуцлалт (хуучин endpoint) ч тайлбар хадгална:")
        r3 = cr.cancel_compensation(ids[3], {"reason": "ганц цуцлалт"}, db=db, user=superu)
        check("cancel_reason/by бичигдэв", r3["cancel_reason"] == "ганц цуцлалт" and r3["cancelled_by"] == superu.username, str(r3))
        try:
            cr.cancel_compensation(ids[3], {"reason": ""}, db=db, user=superu)
            check("хоосон тайлбар → 400/404", False)
        except HTTPException as e:
            check("цуцлагдсаныг дахин цуцлахгүй (404)", e.status_code == 404)
    finally:
        db.rollback()
        db.query(AuditLog).filter(AuditLog.entity_id.in_(ids + [r.get("batch_id", "-") if isinstance(r, dict) else "-"])).delete(synchronize_session=False)
        db.query(AuditLog).filter(AuditLog.username.in_([superu.username, scoped.username])).delete(synchronize_session=False)
        db.query(BlacklistEntry).filter(BlacklistEntry.plate_number.in_([p1, p2])).delete(synchronize_session=False)
        db.query(Compensation).filter(Compensation.id.in_(ids)).delete(synchronize_session=False)
        db.query(ParkingSite).filter(ParkingSite.id.in_([site.id, other.id])).delete(synchronize_session=False)
        db.commit()
        db.close()

    print(f"\n{PASS} ✓  {FAIL} ✗")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
