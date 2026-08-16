"""Долоо хоногийн регрессийн аудит — «сайн ажиллаж байсан системээ эвдсэн үү?»

Орлого огцом буурахад ГУРВАН тэс өөр шалтгаан байж болно. Тэдгээрийг ялгахгүйгээр
«буруу засвар хийсэн» гэж дүгнэх нь эрсдэлтэй:

  1. МАШИН цөөрсөн        — бодит ертөнц (амралтын өдөр, цаг агаар, баяр). Буруу биш.
  2. УНШИЛТ буурсан       — камер/стрим/танилт эвдэрсэн. Кодын регрессийн БОДИТ шинж.
  3. Орлого/төлбөр буурсан — уншилт хэвийн ч төлбөр бодогдохгүй/цуглахгүй. Billing.

Энэ хэрэгсэл өдөр тутам гурвуулаНг зэрэг харуулж, аль нь уналтын шалтгаан болохыг
заана. Deploy-ийн огноотой (CHANGELOG) тулгаж «энэ өдрийн засвар буруутай юу»
гэдгийг батална.

Ажиллуулах (production сервер дээр, backend хавтаст):
    venv/bin/python tools/weekly_audit.py --days 7
    venv/bin/python tools/weekly_audit.py --days 7 --site RASH

Зөвхөн DB УНШИНА — камер руу хандахгүй.
"""
import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import func

from app.database import SessionLocal
from app.models import AuditLog, LprEvent, ParkingSession, ParkingSite, Payment

TZ = timedelta(hours=8)  # УБ-ын цаг


def day_of(dt: datetime) -> str:
    return str((dt + TZ).date())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--site", help="зөвхөн нэг зогсоол (код эсвэл нэрний эхлэл)")
    ap.add_argument("--since", metavar="'YYYY-MM-DD HH:MM'",
                    help="зөвхөн энэ УБ-цагаас хойш орсон машин (засвар/deploy-ийн "
                         "дараах цонхыг тусгаарлах). Өдрийн бүлэглэл хэвээр")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        if args.since:
            # УБ цагаар өгсөн → серверийн UTC болгоно
            since = datetime.strptime(args.since, "%Y-%m-%d %H:%M") - TZ
        else:
            since = datetime.utcnow() - timedelta(days=args.days)
        site = None
        if args.site:
            site = (db.query(ParkingSite)
                    .filter(ParkingSite.site_code == args.site).first()) \
                or (db.query(ParkingSite)
                    .filter(ParkingSite.name.ilike(f"{args.site}%")).first())
            if not site:
                sys.exit(f"«{args.site}» олдсонгүй")

        # ── Session (машин) өдрөөр ───────────────────────────────────────────
        sq = db.query(ParkingSession).filter(ParkingSession.entry_time >= since)
        if site:
            sq = sq.filter(ParkingSession.site_id == site.id)
        sessions = sq.all()

        synced = {eid for (eid,) in db.query(AuditLog.entity_id)
                  .filter(AuditLog.entity == "session", AuditLog.action == "CAMERA_SYNC",
                          AuditLog.created_at >= since).all()}

        per: dict = defaultdict(lambda: {"cars": 0, "live": 0, "back": 0,
                                         "paid_cars": 0, "revenue": 0.0})
        for s in sessions:
            d = per[day_of(s.entry_time)]
            d["cars"] += 1
            back = s.id in synced or "логоос нөхөж" in (s.note or "")
            d["back" if back else "live"] += 1

        # ── Уншилт (LPR) өдрөөр ──────────────────────────────────────────────
        lq = (db.query(func.date(LprEvent.created_at + TZ), LprEvent.accepted,
                       func.count())
              .filter(LprEvent.created_at >= since))
        if site:
            lq = lq.filter(LprEvent.site_id == site.id)
        reads: dict = defaultdict(lambda: [0, 0])   # [accepted, rejected]
        for d_, ok, n in lq.group_by(func.date(LprEvent.created_at + TZ),
                                     LprEvent.accepted).all():
            reads[str(d_)][0 if ok else 1] += n

        # ── Орлого (төлсөн Payment) өдрөөр ───────────────────────────────────
        pq = (db.query(func.date(Payment.paid_at + TZ), func.count(),
                       func.coalesce(func.sum(Payment.amount), 0))
              .filter(Payment.status == "PAID", Payment.paid_at >= since))
        if site:
            pq = pq.join(ParkingSession, ParkingSession.id == Payment.session_id) \
                   .filter(ParkingSession.site_id == site.id)
        pay: dict = defaultdict(lambda: [0, 0.0])
        for d_, cnt, amt in pq.group_by(func.date(Payment.paid_at + TZ)).all():
            pay[str(d_)] = [cnt, float(amt or 0)]

        title = site.name if site else "БҮХ ЗОГСООЛ"
        # Хэмжсэн цонхыг УБ цагаар ТОДОО бичнэ — --since-ийг UTC-тэй андуурахаас
        # сэргийлнэ (git log нь UTC, харин --since нь УБ цаг хүлээж авдаг).
        win_ub = (since + TZ).strftime("%Y-%m-%d %H:%M")
        print(f"══ {title} — {win_ub} (УБ цаг)-аас хойш ══\n")
        print(f"{'огноо':12}{'машин':>7}{'амьд':>7}{'нөхсөн':>8}{'нөхөлт%':>9}"
              f"{'уншилт':>8}{'голог%':>8}{'төлсөн':>8}{'орлого':>12}"
              f"{'₮/машин':>9}")
        days = sorted(set(per) | set(reads) | set(pay))
        for d in days:
            c = per[d]
            ok, bad = reads[d]
            paidn, rev = pay[d]
            nb = c["back"] * 100 // c["cars"] if c["cars"] else 0
            gl = bad * 100 // (ok + bad) if ok + bad else 0
            per_car = rev / c["cars"] if c["cars"] else 0
            print(f"{d:12}{c['cars']:7}{c['live']:7}{c['back']:8}{nb:8}%"
                  f"{ok:8}{gl:7}%{paidn:8}{rev:12,.0f}{per_car:9,.0f}")

        print("\n   ЯАЖ УНШИХ ВЭ:")
        print("     • машин тогтвортой, орлого унасан → төлбөр/танилтын асуудал (кодын регресс болзошгүй)")
        print("     • машин ба уншилт ХАМТ унасан      → камер/стрим (эсвэл бодит амралтын өдөр)")
        print("     • нөхөлт% нэг өдрөөс огцом ӨССӨН   → тэр өдрийн засвар стримийг эвдсэн байж болзошгүй")
        print("     • ₮/машин тогтвортой, машин цөөрсөн → бодит ертөнц, засвар БУРУУ БИШ")

        # Хамгийн сайн 3 хоног vs хамгийн муу хоногийн харьцуулалт
        if len(days) >= 4:
            by_rev = sorted(days, key=lambda d: pay[d][1], reverse=True)
            best, worst = by_rev[0], by_rev[-1]
            bc, wc = per[best], per[worst]
            print(f"\n   Хамгийн ӨНДӨР ({best}) vs хамгийн БАГА ({worst}) орлоготой хоног:")
            print(f"      машин:   {bc['cars']:5} → {wc['cars']:5}  "
                  f"({(wc['cars'] - bc['cars']) * 100 // (bc['cars'] or 1):+d}%)")
            print(f"      уншилт:  {reads[best][0]:5} → {reads[worst][0]:5}  "
                  f"({(reads[worst][0] - reads[best][0]) * 100 // (reads[best][0] or 1):+d}%)")
            print(f"      орлого:  {pay[best][1]:,.0f} → {pay[worst][1]:,.0f}  "
                  f"({int((pay[worst][1] - pay[best][1]) * 100 // (pay[best][1] or 1)):+d}%)")
            print(f"      нөхөлт%: {bc['back'] * 100 // (bc['cars'] or 1)}% → "
                  f"{wc['back'] * 100 // (wc['cars'] or 1)}%")
    finally:
        db.close()


if __name__ == "__main__":
    main()
