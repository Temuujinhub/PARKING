"""ТЕГ тулгалт → POS терминалын баримттай ДАВХАРДСАН манай msgbill баримтыг олох/цуцлах.

API (reports_router: vat-reconcile, vat-reconcile/cancel-duplicates) болон серверийн
CLI (tools/vat_cancel_duplicates.py) хоёулаа ЭНЭ НЭГ модулийг дуудна — «UI дээр 63
гэж харуулаад CLI 61-ийг цуцалсан» гэх зөрүү гарахгүй.

Давхардал гэж (2026-10-13, 08-24-ний продын датагаар батлав): касс дээр Ontime POS
терминал (ТЕГ-ийн ДДТД …10002990) баримт хэвлэсэн БЭЛЭН/КАРТЫН төлбөрт манай систем
msgbill-ээр (…10045952) ДАХИН баримт гаргасан. Нэг борлуулалт ТЕГ-т хоёр удаа
бүртгэгдэнэ. Бид зөвхөн ӨӨРИЙН (msgbill) баримтаа цуцалж чадна.

Хуваарь:
  • load_*  — DB-ээс УНШИНА (бичихгүй)
  • analyze — цэвэр функц (thread дээр ажиллуулж болно)
  • cancel_one — НЭГ төлбөрийн манай msgbill баримтыг цуцална (түгжээ, дахин
    шалгалт, идемпотент, AuditLog, төлбөр бүрд commit)
"""
from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timedelta

from sqlalchemy import func

from ..config import settings
from ..models import AuditLog, ParkingSession, ParkingSite, Payment, VatReceipt
from ..routers import vat_recon as VR
from . import pos_app as PA
from .receipts import (DUPLICATE_OF_POS, choose_primary, is_duplicate_cancel,
                       is_reissued_no_terminal, is_system_receipt)

log = logging.getLogger("parking.vat_duplicates")

PAY_SITE = func.coalesce(Payment.site_id, ParkingSession.site_id)


def _light(q):
    """Тулгалтад хэрэггүй том JSON/текст баганыг (raw_payload ~0.6KB, qr_text, deep_link,
    fee_snapshot) хойшлуулна — сарын ТЕГ экспортын тулгалтын санах ойг багасгана (2026-10-14:
    2GB тест сервер дээр 30 хоногийн нотолгоо OOM болж байв). Хандвал lazy-аар ачаална."""
    from sqlalchemy.orm import defer, lazyload
    # enable_eagerloads(False) нь defer()-ийг ч унтраадаг (SQLAlchemy 2.0) — оронд нь бүх
    # relationship-ийг lazy (JOIN-гүй) болгоно
    return q.enable_eagerloads(True).options(
        lazyload("*"), defer(Payment.raw_payload), defer(Payment.fee_snapshot),
        defer(Payment.qr_text), defer(Payment.deep_link))
PAY_PLATE = func.coalesce(ParkingSession.plate_number, Payment.raw_payload["plate_number"].as_string())


def _site_filter(q, site_ids):
    """site_ids: None = бүх зогсоол | str | list."""
    if site_ids is None:
        return q
    if isinstance(site_ids, str):
        return q.filter(PAY_SITE == site_ids)
    return q.filter(PAY_SITE.in_(list(site_ids)))


def query_window(tax: list, shifts: list[float]) -> tuple[datetime, datetime]:
    """Аль ч цагийн шилжилтэд баримт багтахаар өргөсгөсөн муж."""
    pad = timedelta(hours=max(abs(s) for s in shifts) + 1)
    return min(t["dt"] for t in tax) - pad, max(t["dt"] for t in tax) + pad


def load_ours(db, lo: datetime, hi: datetime, site_ids) -> list:
    """Манай баримтууд (бүх төлөв) — [(VatReceipt, Payment, plate, site_name)]."""
    # enable_eagerloads(False): Payment-ийн lazy="joined" (session→site→tariff, discount)
    # 5+ илүү JOIN-ийг хасна — сарын файлд хэдэн арван мянган мөр татдаг
    q = _light(db.query(VatReceipt, Payment, PAY_PLATE, ParkingSite.name).enable_eagerloads(False)
         .join(Payment, VatReceipt.payment_id == Payment.id)
         .outerjoin(ParkingSession, VatReceipt.session_id == ParkingSession.id)
         .outerjoin(ParkingSite, PAY_SITE == ParkingSite.id)
         .filter(Payment.paid_at >= lo, Payment.paid_at < hi))
    return _site_filter(q, site_ids).all()


def receipt_info(r) -> dict:
    return {"id": r.id, "provider": r.provider, "status": r.status,
            "provider_ref": r.provider_ref, "ebarimt_id": r.ebarimt_id,
            "amount": float(r.amount or 0), "mock": VR.is_mock_receipt(r)}


def payment_probe(pay, recs: list, plate=None, site=None, *, cashier=None,
                  pos_users: set | None = None) -> dict:
    """Төлбөрийн «давхардлын шалгалт»-д хэрэгтэй бүх мэдээлэл (DB-ээс салгасан dict).

    primary/active/system_ddtd — ЗӨВХӨН манай системийн баримт (receipts.is_system_receipt).
    Терминалын мөр (EXTERNAL, давхардлын цуцлалтын дараа бүртгэсэн ONTIME/SENT) нь
    `terminal`-д тусдаа: тэр нь «цуцлах манай баримт» БИШ — active-д орвол цуцлалтыг
    дахин ажиллуулахад «msgbill биш (ONTIME)» гэж андуурагдаж, өөр сувгийн мөр
    цуцлагдах эрсдэлтэй (2026-10-13 нэгтгэлийн хяналт).

    company — ААН/ТТД-тэй баримт (терминал иргэний баримт хэвлэдэг → цуцлахгүй);
    pos_app — POS аппын борлуулалт (provider POS /pos/confirm, source POSAPP, терминалын
    мөртэй, эсвэл кассчин нь POS аппын хэрэглэгч — services/pos_app, систем даяар) —
    ТЕГ-ийн Ontime мөрийг ийм төлбөрт ТҮРҮҮЛЖ ононо (vat_recon._pos_tier)."""
    sys_recs = [r for r in recs if is_system_receipt(r)]
    prim = choose_primary(sys_recs)
    terminal = [receipt_info(r) for r in recs if not is_system_receipt(r) and r.status != "CANCELLED"]
    return {"id": pay.id, "paid_at": pay.paid_at, "amount": float(pay.amount),
            "site_id": getattr(site, "id", None), "site_name": getattr(site, "name", None),
            "site_code": getattr(site, "site_code", None), "plate": plate,
            "provider": pay.provider, "method": pay.payment_method,
            "receipts": [r.ebarimt_id for r in recs],
            "primary": receipt_info(prim) if prim is not None else None,
            "active": [receipt_info(r) for r in sys_recs
                       if r.status in VR.ACTIVE_STATUSES and r.ebarimt_id],
            "system_ddtd": sum(1 for r in sys_recs if r.ebarimt_id),
            "terminal": terminal,
            "cancelled_msgbill": any(r.status == "CANCELLED" and r.provider == "MSGBILL"
                                     for r in sys_recs),
            "duplicate_marked": any(is_duplicate_cancel(r) for r in sys_recs),
            "reissued": any(is_reissued_no_terminal(r) for r in sys_recs),
            "company": is_company(pay),
            "pos_app": bool(terminal) or PA.pos_app_via(pay, cashier, pos_users or set()) is not None}


def is_company(pay) -> bool:
    """ААН/ТТД (эсвэл регистр)-тэй баримт — терминал (Ontime) ИРГЭНИЙ баримт хэвлэдэг тул
    POS-ийн давхардал гэж ЦУЦЛАХГҮЙ (--pos-app --include-company-оор л)."""
    return (getattr(pay, "ebarimt_receiver_type", None) == "COMPANY"
            or bool((getattr(pay, "customer_tin", None) or "").strip()))


def load_probe(db, lo: datetime, hi: datetime, site_ids, pos_users: set | None = None) -> list[dict]:
    """PAID төлбөрүүд (баримттай эсэхээс үл хамааран) + бүх баримт + үндсэн баримт.
    pos_users: POS аппын хэрэглэгчид (--pos-user дарсан бол тэд) — None бол known_users."""
    from ..models import User
    q = _light(db.query(Payment, PAY_PLATE, ParkingSite, User.username).enable_eagerloads(False)
         .outerjoin(ParkingSession, Payment.session_id == ParkingSession.id)
         .outerjoin(ParkingSite, PAY_SITE == ParkingSite.id)
         .outerjoin(User, User.id == Payment.cashier_id)
         .filter(Payment.paid_at >= lo, Payment.paid_at <= hi, Payment.status == "PAID"))
    rows = _site_filter(q, site_ids).all()
    recs: dict[str, list] = {}
    ids = [p.id for p, *_ in rows]
    for i in range(0, len(ids), 1000):
        for r in db.query(VatReceipt).filter(VatReceipt.payment_id.in_(ids[i:i + 1000])).all():
            recs.setdefault(r.payment_id, []).append(r)
    # POS аппын хэрэглэгчид (--pos-app-тай НЭГ дүрэм, систем даяар) — түүхэн POS аппын
    # бэлэн төлбөр (source NULL) веб кассын бэлэнээс ялгагдана
    users = (set(pos_users) if pos_users is not None
             else PA.known_users(db)["users"] if rows else set())
    return [payment_probe(p, recs.get(p.id, []), plate, site, cashier=uname, pos_users=users)
            for p, plate, site, uname in rows]


def analyze(tax: list, ours: list, probe: list, *, shifts: list[float], tol: int,
            scope_ids: set | None = None) -> dict:
    """Цэвэр тулгалт: кассын шилжилт → манай баримттай нэг-нэгээр → үлдсэн ТЕГ
    мөрийг төлбөрөөр тайлах → давхардлын БҮТЭН жагсаалт."""
    # Терминалын мөр (EXTERNAL/ONTIME) МАНАЙ кассын баримт биш — «манайх»-т орвол
    # түүний ДДТД-ийн касс (…10002990) «манай касс» болж Ontime мөр бүр «өөр систем»
    # биш болон давхардал илрэхгүй болно. Терминалынх нь terminal_check-д.
    ours = [o for o in ours if is_system_receipt(o[0])]
    r = VR.best_shift(tax, ours, shifts, tol, probe=probe)
    left = [t for t in tax if not t["used"]]
    # Цуцлагдсан манай баримттай холбогдсон мөрийн төлбөр ч «манай баримт файлд бий»
    matched_pay_ids = ours_in_file(tax, ours)
    # «Терминал гаргасан» гэж бүртгэсэн төлбөр ТЕГ-т баримттай юу — CLI-ийн нотолгоотой (--evidence,
    # vat_reissue_missing) НЭГ хосолт (terminal_pairs): UI-ийн «ТЕГ-т баримт алга» жагсаалт ба
    # «Систем баримт гаргах» нь хэрэгсэлтэй зөрөхгүй. VR.terminal_check-ийн explain-ийн хос нь
    # «кассын зогсоол» олонхоор эрэмбэлдэг тул олон зогсоолын нэг Ontime кассанд худал «алга» гаргана.
    regs, _qr = terminal_registers(tax, r, probe, tol)
    pos = [p for p in probe if scope_ids is None or p.get("site_id") in scope_ids]
    paired, stats, _contested = site_aware_pairs([t for t in tax if t.get("group") in regs], pos, tol)
    # Терминалын касс аль зогсоолынх вэ — ЗОГСООЛ × ӨДРӨӨР (2026-10-15): 10-14-өөс нэг Ontime касс
    # (…10002990) 5 зогсоолд үйлчилдэг тул «олонхын нэг зогсоол» (foreign_register_sites) олдохгүй →
    # БҮХ мөр «зогсоол тодорхойгүй — цуцлахгүй» болж жинхэнэ давхардал ч илрэхгүй байв. POS аппын
    # борлуулалттай огт хосолоогүй касс (хуучин веб кассын үе) — олонхын дүрмээрээ
    reg_sites = VR.foreign_register_sites(left, probe, tol, r["shift"])
    reg_sites.update(register_attribution(regs, paired, stats, pos))
    explained = VR.explain_unmatched_tax(left, probe, r["shift"], tol, matched_pay_ids, scope_ids,
                                         register_sites=reg_sites)
    dups = VR.duplicate_list(explained, probe, matched_pay_ids, settings.tz_offset_hours)
    d0, d1 = file_days(tax)
    term = terminal_summary(tax, probe, paired, tol, r["shift"], scope_ids,
                            window=terminal_window(tax, d0, d1, regs, shifts, tol, r["shift"]))
    return {"r": r, "left": left, "explained": explained, "matched_pay_ids": matched_pay_ids,
            "register_sites": reg_sites, "duplicates": dups, "terminal": term}


def ours_in_file(tax: list, ours: list) -> set:
    """«Манай баримт ч ЭНЭ ТЕГ файлд бий» төлбөрүүд — давхардлын ШУУД нотолгоо (2026-10-15).
    ДДТД нь файлын дугаарлалттай (ижил угтвар, ж: 0152…) манай баримтыг ЗӨВХӨН ДДТД-ээр: цаг+дүнгийн
    тааралт (match_pass) нь өөр борлуулалтын/давхар мөр байж болно (audit1014 × 10-15-ны экспорт:
    --file-ийн 41 «давхардлын» 25-д манай баримт экспортод огт алга байв — цуцалбал цорын ганц
    баримт нь болох байсан). 09-07-ноос өмнөх 0291… багцын дугаартай (ДДТД-ээр шалгах боломжгүй)
    баримтыг цаг+дүнгээр (хуучнаараа)."""
    file_ddtds = {t["ddtd"] for t in tax}
    prefixes = {d[:4] for d in file_ddtds if VR.is_teg_ddtd(d)}
    out = {o[1].id for o in ours if receipt_numbers(o[0]) & file_ddtds}
    for t in tax:
        o = t.get("ours")
        if o is not None and not any(n[:4] in prefixes for n in receipt_numbers(o[0])):
            out.add(o[1].id)
    return out


def reconcile_db(db, tax: list, *, shifts: list[float], tol: int, ours_sites=None,
                 probe_sites=None, scope_ids: set | None = None,
                 pos_users: set | None = None) -> dict:
    """DB-ээс уншаад analyze. API нь үүнийг thread дээр ӨӨРИЙН session-тэй дууддаг
    (event loop-ыг блоклохгүй — хаалт/LPR царцахаас сэргийлнэ), CLI шууд."""
    lo, hi = query_window(tax, shifts)
    ours = [o for o in load_ours(db, lo, hi, ours_sites) if is_system_receipt(o[0])]
    probe = load_probe(db, lo, hi, probe_sites, pos_users=pos_users)
    out = analyze(tax, ours, probe, shifts=shifts, tol=tol, scope_ids=scope_ids)
    out.update({"ours": ours, "probe": probe})
    return out


# ───────────────────────── цуцлах ─────────────────────────────────────────

# Ажиллаж буй системийн DB-ийн нэр (config.py-ийн анхдагч, .env.example) — бодит
# цуцлалт ЗӨВХӨН энэ DB дээрээс. audit1013/t_* г.м продын ХУУЛБАР нь продын жинхэнэ
# msgbill түлхүүр + provider_ref агуулдаг тул тэндээс цуцлахад продын баримт цуцлагдана.
LIVE_DB_NAME = "parking"


def db_name(url: str | None = None) -> str:
    from sqlalchemy.engine import make_url
    try:
        return make_url(url or settings.database_url).database or ""
    except Exception:  # noqa: BLE001
        return ""


def server_problems() -> list[str]:
    """Энэ сервер/DB дээрээс БОДИТ цуцлалт хийж болох уу (2026-10-13 хяналт: TEST
    сервер дээр audit1013 (продын хуулбар, продын жинхэнэ msgbill түлхүүртэй) руу
    --apply хийхэд юу ч саадгүй байв).

    • PARKING_DEPLOY_ROLE=staging → үгүй.
    • DB нь «parking» биш, эсвэл сервер нь test.*/localhost → үгүй; ГЭХДЭЭ
      PARKING_DEPLOY_ROLE=production-ийг ил тод заасан бол (прод өөр нэртэй байвал) болно."""
    import os
    from urllib.parse import urlparse
    probs = []
    role = (os.environ.get("PARKING_DEPLOY_ROLE") or "").strip().lower()
    if role == "staging":
        return ["PARKING_DEPLOY_ROLE=staging — ТЕСТ сервер; бодит цуцлалтыг зөвхөн прод дээр хийнэ"]
    name = db_name()
    # Продын ХУУЛБАР/тестийн DB-ийн нэр — PARKING_DEPLOY_ROLE=production ч ДАРАХГҮЙ
    # (нэгтгэлийн хяналт: role=production бүх шалгалтыг алгасдаг байв)
    low = name.lower()
    if low.startswith(("audit", "t_", "test")) or any(x in low for x in ("copy", "fixtest", "backup")):
        return [f"DB «{name}» нь продын ХУУЛБАР/тестийн нэртэй — бодит msgbill баримт цуцлахгүй"]
    if role == "production":
        return []
    if name != LIVE_DB_NAME:
        probs.append(f"DB «{name}» нь ажиллаж буй системийн «{LIVE_DB_NAME}» биш (продын ХУУЛБАР "
                     "байж магадгүй) — хуулбар дээрээс бодит msgbill баримт цуцлахгүй")
    host = (urlparse(settings.public_base_url or "").hostname or "").lower()
    if host.startswith("test."):
        probs.append(f"ТЕСТ сервер ({settings.public_base_url}) — бодит цуцлалтыг зөвхөн прод "
                     "сервер дээр хийнэ")
    elif host in ("", "localhost", "127.0.0.1"):
        probs.append(f"сервер тодорхойгүй (PARKING_PUBLIC_BASE_URL={settings.public_base_url or '—'}) — "
                     ".env ачаалагдаагүй байж магадгүй: /root/PARKING/backend-ээс ажиллуулна уу")
    return probs


def account_scopes(sites: list | None) -> dict:
    """Зогсоол бүрд цуцлахад оролдох msgbill түлхүүрүүд (түлхүүр БИШ — хүрээ нь):
    «Хангарьд»: ["tenant:EasyParking", "global"]. YES асуултын өмнө харуулна."""
    from . import msgbill
    out = {}
    for site in sites or []:
        accs = msgbill.cancel_accounts(site)
        out[getattr(site, "name", None) or getattr(site, "id", "?")] = [
            a.scope + (f":{a.scope_name}" if a.scope_name else "") + (" (ТЕСТ)" if a.is_test else "")
            for a in accs]
    return out


def cancel_ready(db=None, sites: list | None = None) -> list[str]:
    """БОДИТ цуцлалт эхлүүлэхээс өмнөх хамгаалалт. Хоосон жагсаалт = бэлэн.

    «msgbill хуурамч» гэдэг нь: MOCK баримт асаалттай тест сервер, ТЕСТ түлхүүр
    (bsk_test_ — msgbill симуляц хийдэг, ТЕГ-т юу ч цуцлагдахгүй) эсвэл localhost
    хаяг. Түлхүүргүй зогсоол байвал түүний баримтыг цуцалж чадахгүй. DB нь
    ажиллаж буй «parking» биш (продын хуулбар) эсвэл ТЕСТ сервер бол цуцлахгүй."""
    from . import msgbill
    probs = server_problems()
    if settings.ebarimt_mock_receipts:
        probs.append("PARKING_EBARIMT_MOCK_RECEIPTS=true — MOCK баримттай тест сервер; бодит цуцлалт хийхгүй")
    base = (settings.msgbill_base_url or "").lower()
    if not base.startswith("https://") or any(x in base for x in ("localhost", "127.0.0.1", "mock")):
        probs.append(f"msgbill хаяг бодит биш ({settings.msgbill_base_url}) — msgbill MOCK горимд байна")
    seen = set()
    for site in sites or []:
        sid = getattr(site, "id", None)
        if sid in seen:
            continue
        seen.add(sid)
        accs = msgbill.cancel_accounts(site)
        name = getattr(site, "name", None) or sid
        if not accs:
            probs.append(f"«{name}»: msgbill түлхүүр тохируулаагүй (түрээслэгч/глобал аль нь ч алга)")
        elif any(a.is_test for a in accs):
            # Нэг нь ч тест бол — тест түлхүүрийг ЭХЭЛЖ оролдож «цуцлагдсан» гэж хуурч болно
            probs.append(f"«{name}»: msgbill ТЕСТ түлхүүр (bsk_test_) оролцож байна — бодит баримт "
                         "цуцлагдахгүй байж болзошгүй")
    return probs


def _info(payment, site, recs) -> dict:
    return {"payment_id": payment.id, "site_name": getattr(site, "name", "") or "",
            "amount": float(payment.amount), "method": payment.payment_method,
            "paid_at": payment.paid_at.isoformat() if payment.paid_at else None,
            "receipts": [{"ddtd": r.ebarimt_id, "provider": r.provider, "status": r.status}
                         for r in recs]}


def precheck(db, payment_id: str, allowed_sites: set | None = None) -> dict:
    """DRY-RUN — ЮУ цуцлагдахыг (эсвэл яагаад үгүйг) буцаана. Юу ч бичихгүй."""
    from . import msgbill
    from ..routers.payments_router import _site_of
    payment = db.get(Payment, payment_id)
    if not payment:
        return {"payment_id": payment_id, "ok": False, "error": "Төлбөр олдсонгүй"}
    site = _site_of(payment)
    if allowed_sites is not None and (site is None or site.id not in allowed_sites):
        return {"payment_id": payment_id, "ok": False, "error": "Энэ зогсоолын эрхгүй"}
    recs = db.query(VatReceipt).filter(VatReceipt.payment_id == payment_id).all()
    info = _info(payment, site, recs)
    p = payment_probe(payment, recs, None, site)
    if not p["active"] and p["cancelled_msgbill"]:
        return {**info, "ok": False, "already": True, "error": "Аль хэдийн цуцлагдсан"}
    reason = VR.cancel_block_reason(p)
    if reason is None and not msgbill.cancel_accounts(site):
        reason = "msgbill түлхүүр тохируулаагүй"
    return {**info, "ok": reason is None, "error": reason,
            "will_cancel": [{"ddtd": a["ebarimt_id"], "provider": a["provider"],
                             "provider_ref": a["provider_ref"]} for a in p["active"]]}


async def cancel_one(db, payment_id: str, *, note: str, username: str,
                     teg: dict | None = None, allowed_sites: set | None = None,
                     source: str = "api", mark_extra: dict | None = None,
                     guard=None, allow_company: bool = False) -> dict:
    """НЭГ төлбөрийн манай msgbill баримтыг «POS терминалын давхардал» гэж цуцална.

    • Төлбөрийг FOR UPDATE NOWAIT түгжинэ (давхар товшилт/зэрэг job-оос хамгаална).
    • Дүрмийг (cancel_block_reason) ДАХИН шалгана — UI-аас ирсэн ID-д, эсвэл
      dry-run-аас хойш өөрчлөгдсөн төлбөрт итгэхгүй. QR/QPay баримт ХЭЗЭЭ Ч энд
      цуцлагдахгүй.
    • Идемпотент: аль хэдийн цуцлагдсан бол {"skipped": "already_cancelled"};
      CANCEL_PENDING бол msgbill-ээс төлөвийг GET-ээр эцэслэнэ (дахин cancel биш).
    • Амжилттай/хүлээгдэж буй баримтад DUPLICATE_OF_POS тэмдэг (ddtd_note + raw) —
      «Дахин үүсгэх» дээр 409 өгөх үндэс. AuditLog EBARIMT_CANCEL_BULK. commit.
    • ЗӨВХӨН манай системийн (msgbill) мөрийг цуцална (only_ids) — терминалын
      (ONTIME/EXTERNAL) мөрийг хэзээ ч хөндөхгүй.
    • Цуцалсан/хүлээгдэж буйн дараа терминалын баримтыг бүртгэнэ
      (financial_jobs.record_terminal_receipt: итгэх ТЕГ ДДТД бол ONTIME/SENT, эс
      бөгөөс ДДТД-гүй EXTERNAL) — хэвлэлт/тайлан/ТЕГ тулгалт «баримттай» гэж харна.
      Аль хэдийн цуцлагдсан (тэмдэгтэй) боловч терминалын мөргүй бол (өмнөх ажиллагаа
      дундуур тасарсан) энэ удаа нөхөж бүртгэнэ — дахин ажиллуулахад аюулгүй.
    mark_extra: DUPLICATE_OF_POS-ийн нэмэлт талбар (ж: {"rule": "pos_app"}) — raw.cancel-д.
    guard(payment, recs) -> шалтгаан | None: түгжээний ДАРАА дахин шалгах нэмэлт дүрэм
      (POS аппын горим: ААН/ТТД, сонгосон зогсоол, өөр сувгийн SENT баримт г.м) —
      сонголтоос хойш өөрчлөгдсөн төлбөрийг цуцлахгүй.
    Буцаах: {ok, cancelled, pending, error, skipped?, key_scopes?, auth_error?, quota?,
             terminal_receipt?}"""
    from fastapi import HTTPException

    from ..routers.payments_router import _lock_payment, _site_of, cancel_ebarimt
    from .financial_jobs import record_terminal_receipt
    try:
        payment = _lock_payment(db, payment_id)
    except HTTPException:
        return {"payment_id": payment_id, "ok": False, "retryable": True,
                "error": "Төлбөр өөр хүсэлтэд түгжигдсэн — дахин ажиллуулахад нөхөгдөнө"}
    if payment is None:
        db.rollback()
        return {"payment_id": payment_id, "ok": False, "error": "Төлбөр олдсонгүй"}
    site = _site_of(payment)
    if allowed_sites is not None and (site is None or site.id not in allowed_sites):
        db.rollback()
        return {"payment_id": payment_id, "ok": False, "error": "Энэ зогсоолын эрхгүй"}
    recs = db.query(VatReceipt).filter(VatReceipt.payment_id == payment_id).all()
    info = _info(payment, site, recs)
    if guard is not None:
        why = guard(payment, recs)
        if why:
            db.rollback()
            return {**info, "ok": False, "error": why}
    p = payment_probe(payment, recs, None, site)
    teg = teg or {}

    def _record(locked, replaced):
        # cancel_ebarimt commit хийсэн (түгжээ суларсан) — дахин түгжиж бүртгэнэ; өөр
        # хүсэлт түгжсэн бол алгасна (retry-г тэмдэг хамгаална, дахин ажиллуулахад нөхнө)
        return record_terminal_receipt(db, locked, teg.get("teg_ddtd"), replaced=replaced,
                                       username=username, source=f"vat-duplicates.{source}")

    if not p["active"] and p["cancelled_msgbill"]:
        terminal = None
        marked = [r for r in recs if r.status == "CANCELLED" and is_duplicate_cancel(r)]
        # Терминалын мөр алга (өмнөх ажиллагаа тасарсан) эсвэл ДДТД-гүй EXTERNAL байхад
        # ТЕГ-ийн ДДТД ирсэн — record_terminal_receipt нөхнө/шинэчилнэ (идемпотент)
        if marked and (not p["terminal"] or (teg.get("teg_ddtd")
                                              and not any(t["ebarimt_id"] for t in p["terminal"]))):
            terminal = _record(payment, marked)
            db.add(AuditLog(username=username, action="EBARIMT_CANCEL_BULK", entity="payment",
                            entity_id=payment_id,
                            detail={"ok": True, "skipped": "already_cancelled", "source": source,
                                    "reason": DUPLICATE_OF_POS, "terminal_receipt": terminal.id,
                                    "teg_ddtd": teg.get("teg_ddtd")}))
            db.commit()
        else:
            db.rollback()
        return {**info, "ok": True, "skipped": "already_cancelled", "cancelled": [], "pending": [],
                **({"terminal_receipt": _term_info(terminal)} if terminal is not None else {})}
    reason = VR.cancel_block_reason(p, allow_company=allow_company)
    if reason:
        db.rollback()
        return {**info, "ok": False, "error": reason}
    mark = {"reason": DUPLICATE_OF_POS, "teg_ddtd": teg.get("teg_ddtd"),
            "teg_register": teg.get("teg_register"), "source": source, "by": username,
            **(mark_extra or {})}
    target_ids = {a["id"] for a in p["active"]}
    res = await cancel_ebarimt(db, payment, note, mark=mark, only_ids=target_ids)
    terminal = None
    targets = db.query(VatReceipt).filter(VatReceipt.id.in_(target_ids)).all()
    if targets and all(r.status in ("CANCELLED", "CANCEL_PENDING") for r in targets):
        try:
            locked = _lock_payment(db, payment_id)
        except HTTPException:
            locked = None
        if locked is not None:
            terminal = _record(locked, targets)
    err = res.get("error") or ""
    out = {**info, **res}
    if terminal is not None:
        out["terminal_receipt"] = _term_info(terminal)
    up = err.upper()
    out["auth_error"] = any(x in up for x in ("UNAUTHORIZED", "MSGBILL 401", "MSGBILL 403", "ТҮЛХҮҮР"))
    out["quota"] = "429" in err or "QUOTA" in up or "хязгаар" in err
    db.add(AuditLog(username=username, action="EBARIMT_CANCEL_BULK", entity="payment",
                    entity_id=payment_id,
                    detail={"ok": bool(res.get("ok")), "cancelled": res.get("cancelled"),
                            "pending": res.get("pending"), "error": res.get("error"),
                            "key_scopes": res.get("key_scopes"), "note": note,
                            "reason": DUPLICATE_OF_POS, "source": source,
                            "teg_ddtd": teg.get("teg_ddtd"),
                            "teg_register": teg.get("teg_register"),
                            **({"rule": mark_extra.get("rule")} if mark_extra else {}),
                            "terminal_receipt": _term_info(terminal)}))
    db.commit()
    return out


def _term_info(rec) -> dict | None:
    if rec is None:
        return None
    return {"id": rec.id, "status": rec.status, "provider": rec.provider,
            "ebarimt_id": rec.ebarimt_id}


# ─────────────── POS аппын дүрмээр цуцлах (2026-10-13; 10-14 бүх түрээслэгч) ───────────────
#
# Эзэн (2026-10-14 эцсийн): PAX терминал POS аппаар авсан КАРТ, БЭЛЭН борлуулалт бүрд
# Ontime-ийн e-Barimt-ыг өөрөө хэвлэдэг — БҮХ зогсоол, БҮХ түрээслэгч (ИйзиПаркинг ХХК,
# Моннис пропэрти / Monnis Building, Моннисмоторс, ирээдүйн). Тиймээс тэдгээр борлуулалтад
# манай msgbill-ээр гаргасан баримт нь ДАВХАРДАЛ — ТЕГ экспортгүйгээр дүрмээр сонгож
# цуцална. Ontime баримт хэвлэгдээгүй борлуулалт байвал (терминал унтарсан г.м) цуцалсны
# дараа e-Barimt-гүй үлдэнэ — --evidence (ТЕГ экспорт) өгвөл түүний хугацаанд терминалын
# мөргүй борлуулалтыг цуцлахгүй; аль хэдийн цуцалсныг tools/vat_reissue_missing.py нөхнө.
# Сонголт (`select_pos_app`) ба цуцлалтын дахин шалгалт (`pos_app_guard`) НЭГ дүрэм.
# Зогсоолд «POS терминал e-Barimt гаргадаг» тодорхой УНТРААСАН бол (терминал нь
# хэвлэдэггүй) тэр зогсоолын баримтыг ХӨНДӨХГҮЙ (SITE_OFF).

POS_APP_GO_LIVE = datetime(2026, 8, 19)             # msgbill эхэлсэн өдөр (УБ) — анхдагч --from
POS_APP_TEST_SITES = frozenset({"KH", "SITE10"})    # тест зогсоол — --site-аар нэрлэсэн үед л
POS_APP_MIN_CARD = PA.POS_APP_MIN_CARD              # POS-оор ≥20 карт авсан хэрэглэгч = POS апп
POS_APP_RULE = "pos_app_terminal_2026-10-13"
POS_APP_METHODS = PA.POS_APP_METHODS
ALL_TENANTS = "ALL"


def resolve_tenant(db, value: str):
    """Түрээслэгчийг code / нэр / id-аар (CLI-үүд). Олдохгүй бол ValueError."""
    from sqlalchemy import or_

    from ..models import Tenant
    conds = [Tenant.code.ilike(value), Tenant.name.ilike(value)]
    if len(value) == 36:                       # UUID
        conds.append(Tenant.id == value)
    t = db.query(Tenant).filter(or_(*conds)).first()
    if t is None:
        raise ValueError(f"Түрээслэгч олдсонгүй: {value}")
    return t


def resolve_tenants(db, value: str) -> list:
    """«ALL» → бүх түрээслэгч (code-оор эрэмбэлсэн), эс бөгөөс нэг."""
    from ..models import Tenant
    if (value or "").strip().upper() == ALL_TENANTS:
        return db.query(Tenant).order_by(Tenant.code).all()
    return [resolve_tenant(db, value)]


def tenant_label(tenants: list) -> str:
    return ",".join(t.code for t in tenants) if len(tenants) <= 4 else f"{len(tenants)} түрээслэгч"


def ub_window(date_from: str | None, date_to: str | None) -> tuple[datetime, datetime]:
    """УБ-ын огноо (YYYY-MM-DD, хоёулаа хамааруулна) → UTC [lo, hi). Анхдагч: go-live → одоо."""
    off = timedelta(hours=settings.tz_offset_hours)
    lo = (datetime.strptime(date_from, "%Y-%m-%d") if date_from else POS_APP_GO_LIVE) - off
    hi = (datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1) - off if date_to
          else datetime.utcnow() + timedelta(minutes=1))
    return lo, hi


def pos_app_sites(db, tenants, site_codes=None) -> list:
    """Түрээслэгч(ид)-ийн зогсоолууд; --site өгвөл зөвхөн тэд (өөр түрээслэгчийнх бол алдаа).
    tenants: нэг Tenant эсвэл жагсаалт (--tenant ALL). Тест зогсоол (KH, SITE10) зөвхөн
    --site-аар нэрлэсэн үед."""
    tenants = tenants if isinstance(tenants, (list, tuple)) else [tenants]
    ids = [t.id for t in tenants]
    sites = (db.query(ParkingSite).filter(ParkingSite.tenant_id.in_(ids))
             .order_by(ParkingSite.site_code).all()) if ids else []
    if site_codes:
        want = {c.upper() for c in site_codes}
        out = [s for s in sites if (s.site_code or "").upper() in want]
        missing = want - {(s.site_code or "").upper() for s in out}
        if missing:
            raise ValueError(f"«{tenant_label(tenants)}»-ийн зогсоол биш/олдсонгүй: "
                             f"{', '.join(sorted(missing))}")
        return out
    return [s for s in sites if (s.site_code or "").upper() not in POS_APP_TEST_SITES]


def tenant_sites(db, tenants) -> list:
    """Түрээслэгч(ид)-ийн БҮХ зогсоол (тест ч) — ТЕГ экспорт ТТД-ийн бүх борлуулалтыг агуулдаг
    тул нотолгооны тулгалт үүгээр (сонгосон хэсгээр бус)."""
    tenants = tenants if isinstance(tenants, (list, tuple)) else [tenants]
    ids = [t.id for t in tenants]
    return (db.query(ParkingSite).filter(ParkingSite.tenant_id.in_(ids))
            .order_by(ParkingSite.site_code).all()) if ids else []


def parse_pos_user_override(values) -> set:
    """--pos-user: «pos.a,pos.b» (олон удаа) → хэрэглэгчийн багц. Хуучин «HANGARID=pos.k»
    хэлбэрийг ч хүлээн авна (зогсоолын хэсгийг үл тооно — 2026-10-14-өөс систем даяар)."""
    import re
    out = set()
    for v in values or []:
        users = v.partition("=")[2] if "=" in v else v
        out |= {u.strip().lower() for u in re.split(r"[,;\s]+", users) if u.strip()}
    return out


def pos_app_scope(db, sites, *, override=None, min_card: int = POS_APP_MIN_CARD) -> dict:
    """Сонголт/дахин шалгалтын нөхцөл — НЭГ удаа тооцож дамжуулна:
    {"users": POS аппын хэрэглэгчид (систем даяар, pos_app.known_users),
     "methods": {site_id: терминал баримт гаргадаг аргууд (ҮЙЛЧИЛЖ буй тохиргоо)}, ...}."""
    from . import app_settings as A
    info = PA.known_users(db, min_card=min_card, override=override)
    info["methods"] = {s.id: A.pos_terminal_receipt_methods(db, s.id) for s in sites}
    return info


def pos_app_via(payment, username, site_users) -> str | None:
    """Энэ төлбөр POS аппынх уу — яаж танив (services/pos_app — нэг дүрэм)."""
    return PA.pos_app_via(payment, username, site_users)


def pos_app_exclusion(payment, recs, *, username, site_users, include_company=False,
                      site_ids=None, site=None, methods=POS_APP_METHODS) -> str | None:
    """POS аппын дүрмээр цуцлах эсэх. None = цуцална; эс бөгөөс шалтгааны КОД.
    Сонголт ба түгжээний дараах дахин шалгалт (guard) хоёулаа үүгээр.
    methods: тухайн зогсоолд ҮЙЛЧИЛЖ буй «терминал баримт гаргадаг» аргууд — хоосон =
    зогсоолд унтраасан (терминал нь хэвлэдэггүй) → SITE_OFF."""
    m = (payment.payment_method or "").upper()
    if payment.status != "PAID":
        return "NOT_PAID"
    if m not in POS_APP_METHODS:
        return "METHOD"
    if site_ids is not None and (site is None or site.id not in site_ids):
        return "SITE"
    if pos_app_via(payment, username, site_users) is None:
        return "NOT_POS_APP"
    if not methods:
        return "SITE_OFF"
    if m not in set(methods):
        return "METHOD_OFF"
    if not include_company and is_company(payment):
        return "COMPANY"
    if any(is_reissued_no_terminal(r) for r in recs):
        return "REISSUED"
    if any(r.status == "SENT" and (r.provider or "POSAPI") != "MSGBILL" and not is_duplicate_cancel(r)
           for r in recs):
        return "OTHER_ISSUER_SENT"
    issued = [r for r in recs if r.provider == "MSGBILL" and r.status in VR.ACTIVE_STATUSES
              and r.provider_ref]
    if not issued:
        return "NO_MSGBILL"
    if VR.cancel_block_reason(payment_probe(payment, recs, None, site),
                              allow_company=include_company) is not None:
        return "RULE"
    return None


POS_APP_EXCLUSION_MN = {
    "NOT_POS_APP": "POS аппынх биш (веб касс/бусад хэрэглэгч)",
    "SITE_OFF": "зогсоолд «POS терминал e-Barimt гаргадаг» УНТРААЛТТАЙ (терминал хэвлэдэггүй) — ХӨНДӨХГҮЙ",
    "METHOD_OFF": "зогсоолын тохиргоонд терминал энэ аргад (карт/бэлэн) баримт хэвлэдэггүй",
    "COMPANY": "ААН/ТТД-тэй баримт — терминал иргэний баримт хэвлэдэг тул ХӨНДӨХГҮЙ",
    "REISSUED": "терминал хэвлээгүй нь ТЕГ-ээр батлагдаж ДАХИН гаргасан баримт — ХӨНДӨХГҮЙ",
    "OTHER_ISSUER_SENT": "өөр сувгийн (QPay/PosAPI/терминал) тэмдэггүй SENT баримттай — гараар",
    "NO_MSGBILL": "цуцлах msgbill баримт алга (QPay/PosAPI/MOCK/FAILED/REVIEW эсвэл цуцлагдсан)",
    "RULE": "цуцлах дүрэм хангаагүй (provider_ref/ДДТД/дүн) — гараар",
    "METHOD": "карт/бэлэн биш",
    "NOT_PAID": "PAID биш", "SITE": "сонгосон зогсоолд биш",
}


def select_pos_app(db, *, sites, lo: datetime, hi: datetime, scope: dict,
                   include_company: bool = False) -> dict:
    """POS аппын карт/бэлэн төлбөрүүдээс цуцлах msgbill баримттайг сонгоно (READ ONLY).

    scope: pos_app_scope(). Буцаах: {"selected": [мөр], "excluded": Counter(код),
    "company": [мөр], "complete": [мөр], "site_off": Counter(site_code)} — complete =
    өмнө нь тэмдэгтэйгээр цуцлагдсан боловч терминалын мөр бүртгэгдээгүй (ажиллагаа
    тасарсан) → apply нөхнө (msgbill дуудахгүй)."""
    from collections import Counter

    from ..models import Tenant, User
    by_id = {s.id: s for s in sites}
    if not by_id:
        return {"selected": [], "excluded": Counter(), "company": [], "complete": [],
                "site_off": Counter()}
    tcode = {t.id: t.code for t in db.query(Tenant).all()}
    q = (db.query(Payment, User.username, PAY_PLATE, PAY_SITE).enable_eagerloads(False)
         .outerjoin(ParkingSession, Payment.session_id == ParkingSession.id)
         .outerjoin(User, User.id == Payment.cashier_id)
         .filter(Payment.status == "PAID", Payment.payment_method.in_(POS_APP_METHODS),
                 Payment.paid_at >= lo, Payment.paid_at < hi, PAY_SITE.in_(list(by_id))))
    rows = q.order_by(Payment.paid_at, Payment.id).all()
    recs: dict[str, list] = {}
    ids = [p.id for p, *_ in rows]
    for i in range(0, len(ids), 1000):
        for r in db.query(VatReceipt).filter(VatReceipt.payment_id.in_(ids[i:i + 1000])).all():
            recs.setdefault(r.payment_id, []).append(r)
    loc = timedelta(hours=settings.tz_offset_hours)
    users = scope.get("users", set())
    out, excluded, company, complete, site_off = [], Counter(), [], [], Counter()
    for p, uname, plate, sid in rows:
        site = by_id.get(sid)
        rs = recs.get(p.id, [])
        methods = scope.get("methods", {}).get(sid, set(POS_APP_METHODS))
        why = pos_app_exclusion(p, rs, username=uname, site_users=users,
                                include_company=include_company, site_ids=set(by_id), site=site,
                                methods=methods)
        issued = [r for r in rs if r.provider == "MSGBILL" and r.status in VR.ACTIVE_STATUSES]
        row = {"payment_id": p.id, "site_id": sid, "site_code": site.site_code if site else "",
               "site_name": site.name if site else "",
               "tenant": tcode.get(getattr(site, "tenant_id", None), "") if site else "",
               "plate": plate or "",
               "method": (p.payment_method or "").upper(), "amount": float(p.amount),
               "paid_at": p.paid_at.isoformat(), "paid_at_ub": (p.paid_at + loc).strftime("%Y-%m-%d %H:%M:%S"),
               "month": (p.paid_at + loc).strftime("%Y-%m"), "cashier": uname or "",
               "via": pos_app_via(p, uname, users) or "",
               "our_ddtd": ", ".join(r.ebarimt_id or "" for r in issued),
               "our_provider_ref": ", ".join(r.provider_ref or "" for r in issued),
               "our_status": "/".join(sorted({r.status for r in issued})),
               "receiver_type": p.ebarimt_receiver_type or "", "customer_tin": p.customer_tin or "",
               "teg_ddtd": None, "teg_register": None}
        if why is None:
            out.append(row)
            continue
        if why == "COMPANY" and issued:
            company.append(row)
        if why in ("SITE_OFF", "METHOD_OFF") and issued:
            site_off[row["site_code"] or row["site_name"]] += 1
        if (why == "NO_MSGBILL" and pos_app_via(p, uname, users)
                and any(r.status == "CANCELLED" and is_duplicate_cancel(r) for r in rs)
                and not any(not is_system_receipt(r) and r.status != "CANCELLED" for r in rs)):
            complete.append(row)
        excluded[why] += 1
    return {"selected": out, "excluded": excluded, "company": company, "complete": complete,
            "site_off": site_off}


def pos_app_guard(scope: dict, site_ids: set, include_company: bool = False):
    """cancel_one(guard=…) — түгжээний дараа ЯГ сонголтын дүрмээр дахин шалгана.
    Зогсоолын тохиргоог ДАХИН уншина — сонголтоос хойш унтраасан бол цуцлахгүй."""
    from sqlalchemy.orm import object_session

    from ..models import User
    from . import app_settings as A

    def guard(payment, recs):
        from ..routers.payments_router import _site_of
        site = _site_of(payment)
        uname = None
        sess = object_session(payment)
        if payment.cashier_id and sess is not None:
            usr = sess.get(User, payment.cashier_id)
            uname = usr.username if usr is not None else None
        methods = (A.pos_terminal_receipt_methods(sess, site.id)
                   if site is not None and sess is not None else set())
        why = pos_app_exclusion(payment, recs, username=uname, site_users=scope.get("users", set()),
                                include_company=include_company, site_ids=site_ids, site=site,
                                methods=methods)
        if why in (None, "NO_MSGBILL"):
            # NO_MSGBILL: аль хэдийн цуцлагдсан (дахин ажиллуулсан) — cancel_one өөрөө
            # «already»/терминалын мөр нөхөх эсвэл дүрмээр татгалзана; msgbill дуудахгүй
            return None
        return f"POS аппын дүрэм: {POS_APP_EXCLUSION_MN.get(why, why)}"
    return guard


# ─────────────── ТЕГ экспорт = ТЕРМИНАЛЫН БАРИМТЫН НОТОЛГОО (2026-10-14) ───────────────
#
# `--pos-app --evidence` (цуцлахын өмнө) ба tools/vat_reissue_missing.py (цуцалсны/
# EXTERNAL-ийн дараа) хоёулаа ЭНЭ НЭГ тулгалтаар: «энэ борлуулалтад терминал (Ontime)
# баримт ТЕГ-т бий юу». Тулгалт нь --file горим/UI-тай ижил (reconcile_db → касс бүрийн
# цагийн бүс (ТЕГ экспортод msgbill УБ, Ontime/QPay UTC), нэг-нэгээр, ±tol).

QR_REGISTER_SHARE = 0.5     # «өөр систем» кассын таарсан төлбөрийн >50% QR бол QPay-ийн касс…
QR_REGISTER_MIN_HITS = 5    # …ба дор хаяж 5 тааралттай (1–2 санамсаргүй тааралтаар терминалын
                            # кассыг «QPay» гэж хаявал бүх борлуулалт «алга» → ДАХИН гаргах)
# Ойролцоо зөрүү (хяналт 2, D): терминалын мөргүй борлуулалтын ижил дүнтэй ХОСГҮЙ терминалын мөр
# −10 мин…+2 цагийн дотор байвал (терминалын цаг хоцорсон/түрүүлсэн, дараа нь илгээсэн г.м)
# AMBIGUOUS — дахин гаргахгүй (--include-ambiguous-аар л), цуцлахгүй.
NEAR_MISS_BEFORE = timedelta(minutes=10)
NEAR_MISS_AFTER = timedelta(hours=2)
# Терминалын (Ontime) баримт төлбөрөөс ӨМНӨ бүртгэгдэхгүй: бодит ТЕГ экспорт (08-19: 50, 08-24/25:
# 63 хос) — Ontime мөр төлбөрөөс 0.4–26с ХОЙНО (p50 ≈2с). Цагийн зөрүүнд 10с нөөц үлдээж, үүнээс
# өмнөх мөрийг тэр төлбөрийнх гэж үзэхгүй (ижил дүнтэй хөрш борлуулалттай солигдохоос сэргийлнэ).
TERMINAL_LAG_SLACK = timedelta(seconds=10)
OWN_MATCH_SHARE = 0.5       # файлын МАНАЙ кассын мөрийн ≥50% манай баримттай таарвал л тэр
                            # түрээслэгчийн экспорт гэж үзнэ (msgbill/QPay касс түрээслэгчдэд
                            # НИЙТЛЭГ: …10045952/…10014863 — санамсаргүй тааралтаар бус). ФАЙЛЫН
                            # түвшинд (2026-10-15): 9-р сарын файлын 09-01..02 хэсэгт QPay олноор нь
                            # дахин илгээсэн мөрүүдээс болж хэсгийн хувь 45% болж тэр 2 хоног
                            # нотолгоонд огт орохгүй байв (файлын нийт 89%)
# Зогсоол × өдрийн терминалын хэрэглээ (2026-10-15): тэр өдрийн POS аппын карт/бэлэн борлуулалтын
# ≥10% (ба ≥1) нь терминалын мөртэй хосолсон бол тэр зогсоол тэр өдөр терминалаар баримт хэвлэсэн.
# Нэг Ontime касс (…10002990) 5 зогсоолд үйлчилдэг; 3HOSPITAL огт Ontime-гүй (≤2% санамсаргүй
# хос) ч бусад зогсоолын хосгүй мөрөөс болж 517 борлуулалт нь «ТОДОРХОЙГҮЙ» болж байв.
SITE_DAY_MIN_SHARE = 0.10
SITE_DAY_MIN_PAIRS = 1
# Манай кассын (msgbill/QPay) мөр төлбөр/манай баримт үүссэнээс −60с…+120с (msgbill синхрон үед 1–5с;
# 09-07-нд мөр төлбөрөөс 14с ӨМНӨ ч байв) — ДДТД-ээр танигдаагүй мөрийг (09-07-ноос өмнөх
# msgbill-ийн багцын дугаар 0291…, манайд бүртгэлгүй давхар баримт) төлбөртэй хосолно
OWN_TIME_BEFORE = timedelta(seconds=60)
OWN_TIME_TOL = 120
# msgbill нэг төлбөрт ХОЁР баримт гаргасан (манайд нэг нь л бүртгэлтэй) — ДДТД-ийн дугаар нь 1–2-оор
# зөрүүтэй (бодит 9/10-р сар: 323/324 хос)
TWIN_COUNTER_GAP = 2


def _register_qr_share(tax: list, reg: str, probe: list, tol: int) -> tuple[int, int]:
    """Энэ кассын мөрүүдийн манай төлбөртэй (баримтаас үл хамааран) таарсан тоо ба QR-ийнх."""
    rows = [t for t in tax if t.get("group") == reg]
    hits = VR.match_probe(rows, probe, tol, 0.0) if rows else []
    qr = sum(1 for _i, j, _d in hits if VR._qr_rank(probe[j]))
    return len(hits), qr


EVIDENCE_CHUNK_DAYS = 2     # урт (сарын) экспортыг хэдэн хоногоор хувааж тулгах вэ — санах ой


def ub_day(at: datetime) -> str:
    return (at + timedelta(hours=settings.tz_offset_hours)).date().isoformat()


def _pos_sales(probe: list) -> list:
    """Терминал баримт хэвлэдэг цорын ганц борлуулалт — POS аппын карт/бэлэн."""
    return [p for p in probe if p.get("pos_app") and (p.get("method") or "").upper() in POS_APP_METHODS
            and p.get("paid_at") is not None]


def site_day_stats(pos: list, paired) -> dict:
    """{(site_id, УБ өдөр): [POS аппын карт/бэлэн борлуулалт, терминалын мөртэй хосолсон]} — тэр
    өдрийн БҮХ борлуулалтаар (терминалын мөрийн хүрээгээр биш: 10-09-нд Ontime 4 мөр л байсан тул
    мөрийн нарийн хүрээнд 3HOSPITAL-ийн 5 борлуулалтын 1 санамсаргүй хос = 20% болж байв; өдрөөр 1/300)."""
    out: dict = {}
    for p in _pos_sales(pos):
        c = out.setdefault((p.get("site_id"), ub_day(p["paid_at"])), [0, 0])
        c[0] += 1
        c[1] += p["id"] in paired
    return out


def terminal_days(stats: dict) -> set:
    """Терминалаар баримт хэвлэсэн (зогсоол, УБ өдөр) — хосолсон ≥SITE_DAY_MIN_PAIRS, ≥SITE_DAY_MIN_SHARE."""
    return {k for k, (n, hit) in stats.items()
            if hit >= SITE_DAY_MIN_PAIRS and hit >= SITE_DAY_MIN_SHARE * n}


def site_aware_pairs(rows: list, probe: list, tol: int) -> tuple[dict, dict, set]:
    """Терминалын мөр ↔ POS аппын борлуулалт, ЗОГСООЛ × ӨДРИЙН хэрэглээгээр (2026-10-15):
    (1) хамгийн ойроор хосолж зогсоол бүрийн тэр өдрийн терминалын хэрэглээг тооцно; (2) тэр өдөр
    терминалаа АШИГЛАСАН зогсоолын борлуулалтыг ТҮРҮҮЛЖ дахин хосолно. Эс бөгөөс терминалгүй
    зогсоолын (3HOSPITAL ~300 борлуулалт/өдөр) ижил дүнтэй борлуулалт жинхэнэ борлуулалтаас мөрийг
    «булааж» тэр жинхэнэ нь «терминалын мөргүй» → ДАХИН гаргагдаж давхардана.
    Буцаах ({payment_id: мөр}, site_day_stats, {1-р алхамд хосолсон ч 2-т мөрөө алдсан төлбөр} —
    мөр аль борлуулалтынх нь маргаантай → evidence_split ТОДОРХОЙГҮЙ (дахин гаргахгүй))."""
    first = terminal_pairs(rows, probe, tol)
    if not rows:
        return first, {}, set()
    prefer = terminal_days(site_day_stats(probe, first))
    paired = terminal_pairs(rows, probe, tol, prefer=prefer)
    return paired, site_day_stats(probe, paired), set(first) - set(paired)


def register_attribution(regs: dict, paired: dict, stats: dict, pos: list) -> dict:
    """Терминалын касс бүр АЛЬ зогсоолынх вэ — ЗОГСООЛ × ӨДРӨӨР (vat_recon.explain_unmatched_tax-ийн
    «allow»): тэр өдөр терминалаар баримт хэвлэсэн (terminal_days) бөгөөд энэ кассын мөр тэр
    зогсоолын борлуулалттай хосолсон «зогсоол|УБ өдөр». Тэнд л POS аппын борлуулалтын давхардал
    цуцлагдана; бусад нь «тэр өдөр терминалгүй — гараар». UI-д site_id = хамгийн олон хостой зогсоол
    (харуулахад), site_name = бүх зогсоол. POS аппын борлуулалттай огт хосолоогүй касс энд ОРОХГҮЙ
    (foreign_register_sites-ийн олонхын дүрэм — терминал веб кассын борлуулалтад хэвлэдэг байсан үе)."""
    ok = terminal_days(stats)
    by_id = {p["id"]: p for p in pos}
    names = {p.get("site_id"): p.get("site_name") or p.get("site_code") or "" for p in pos}
    out = {}
    for reg in regs:
        hits = Counter()
        for pid, row in paired.items():
            p = by_id.get(pid)
            if p is not None and row.get("group") == reg:
                hits[(p.get("site_id"), ub_day(p["paid_at"]))] += 1
        allow = sorted(f"{sid}|{day}" for sid, day in hits if (sid, day) in ok)
        per_site = Counter()
        for (sid, day), n in hits.items():
            if (sid, day) in ok:
                per_site[sid] += n
        total = sum(hits.values())
        if not total:
            continue                      # POS аппын хосгүй — хуучин (олонхын) дүрэм хэвээр
        out[reg] = {"site_id": per_site.most_common(1)[0][0] if per_site else None,
                    "site_name": ", ".join(names.get(s, "") for s, _n in per_site.most_common()),
                    "share": round(sum(per_site.values()) / total, 3),
                    "hits": total, "sites": len(per_site), "allow": allow,
                    "tz_hours": settings.tz_offset_hours}
    return out


def receipt_numbers(rec) -> set:
    """Манай баримтын ТЕГ экспортод харагдах дугаар(ууд): ebarimt_id + msgbill-ийн outbox/webhook
    receipt_no (33 оронтой ТЕГ-ийн хэлбэртэй бол л). 09-07-ноос өмнөх msgbill баримт ebarimt_id-д
    багцын дугаар (0291…) хадгалсан — ТЕГ-ийн ДДТД (0152…) биш тул ДДТД-ээр олдохгүй (цаг+дүнгээр)."""
    nums = {getattr(rec, "ebarimt_id", None)}
    raw = getattr(rec, "raw", None)
    if isinstance(raw, dict):
        for k in ("outbox", "webhook.receipt.created"):
            v = raw.get(k)
            if isinstance(v, dict):
                nums.add(v.get("receipt_no"))
    return {str(n).strip() for n in nums if n and VR.is_teg_ddtd(n)}


def _ddtd_parts(n: str) -> tuple[str, int, str]:
    """ТЕГ-ийн 33 оронтой ДДТД → (угтвар [:16], дугаар [16:25], касс [25:])."""
    return n[:16], int(n[16:25]), n[25:]


def own_presence(db, core: list, a: dict) -> dict:
    """Хэсгийн ҮНДСЭН мөрүүдээс МАНАЙ кассын (msgbill …10045952, QPay …10014863 — манай баримтын
    ДДТД-ийн касс) мөр АЛЬ төлбөрийнх вэ → тэр борлуулалт ТЕГ-т баримттай (2026-10-15). Манай баримт
    (идэвхтэй, эсвэл цуцалсан ч экспортод ҮЛДСЭН) эсвэл msgbill-ийн давхар баримт нь экспортод байхад
    vat_reissue_missing ДАХИН гаргавал тэр борлуулалт ТЕГ-т 2 баримттай болно (бодит 10-15-ны
    экспорт: 1,680 борлуулалт / 6.71 сая₮). Дараалал (мөр бүр НЭГ л төлбөрт):
      1. ДДТД — мөрийн ДДТД = манай баримтын ebarimt_id / outbox / webhook receipt_no (DB-ээс ч
         хайна — төлбөрөөс хойш олон цагийн дараа гарсан баримт);
      2. TWIN — msgbill-ийн давхар баримт: манайд бүртгэлгүй мөрийн ДДТД манай баримтынхаас дугаараар
         1–2 зөрүүтэй (ижил угтвар/касс/дүн; хэд таарвал баримт үүссэн цагаар хамгийн ойр);
      3. TIME — үлдсэн мөр (09-07-ноос өмнөх 0291… багцын дугаартай баримт г.м) тэр кассаар баримт
         гаргасан төлбөртэй (төлсөн эсвэл манай баримт ҮҮССЭН цагаас −60с…+120с) дүнгээр, нэг-нэгээр,
         хамгийн ойроор (ДДТД-ээр таарсан төлбөр ч оролцоно — илүү мөрийг өөртөө «шингээж» өөр
         борлуулалтыг худал «бий» болгохгүй).
    match_pass/explain_unmatched_tax-ийн хосоос (EXTRA_SAME_REGISTER г.м) үл хамаарна — тэдгээр нь
    цуцлагдсан баримтыг хамгийн сүүлд, «тайлбаргүй» төлбөрийг түрүүлж оноодог тул давхар баримтыг
    өөр борлуулалт руу түлхэж болно. Буцаах {payment_id: "DDTD" | "TWIN" | "TIME"}."""
    tail = a["r"].get("tail")
    own_regs = set(a["r"].get("own_registers") or [])
    rows = [t for t in core if t.get("group") in own_regs and VR.is_teg_ddtd(t.get("ddtd"))]
    if not rows:
        return {}
    index, known = {}, {}
    for o in a.get("ours") or []:
        rec, pay = o[0], o[1]
        for n in receipt_numbers(rec):
            index.setdefault(n, pay.id)
            pre, cnt, reg = _ddtd_parts(n)
            known.setdefault((pre, reg), []).append((cnt, float(rec.amount or 0), pay.id,
                                                     rec.created_at or pay.paid_at))
    want = sorted({t["ddtd"] for t in rows} - set(index))
    for i in range(0, len(want), 1000):
        for num, pid in (db.query(VatReceipt.ebarimt_id, VatReceipt.payment_id)
                         .filter(VatReceipt.ebarimt_id.in_(want[i:i + 1000])).all()):
            index.setdefault(num, pid)
    out: dict = {}
    rest = []
    for t in rows:
        pid = index.get(t["ddtd"])
        if pid is not None:
            out[pid] = "DDTD"
        else:
            rest.append(t)
    left = []
    for t in rest:
        pre, cnt, reg = _ddtd_parts(t["ddtd"])
        at = VR._when(t)
        # Ижил дүнтэй баримт олноор дараалж гарсан үед (3HOSPITAL 09-25: …23, 25, 27, 29 манайх,
        # …24, 26, 28 давхар) дугаар 1-ээр хоёр талдаа таарна — баримт ҮҮССЭН цагаар хамгийн ойрыг
        near = [(abs((at - made).total_seconds()) if made else 1e9, abs(c - cnt), pid)
                for c, amt, pid, made in known.get((pre, reg), ())
                if 0 < abs(c - cnt) <= TWIN_COUNTER_GAP and abs(amt - float(t["amount"])) < 1]
        if near:
            out.setdefault(min(near)[2], "TWIN")
        else:
            left.append(t)
    if left:
        # Зангуу: төлбөрийн төлсөн цаг + тэр кассын манай баримт бүрийн ҮҮССЭН цаг (outbox-оос хойш
        # баримт төлбөрөөс хожуу; 09-07-нд мөр төлбөрөөс өмнө)
        items = []                                 # (цаг, дүн, payment_id, касс)
        for p in a.get("probe") or []:
            if p.get("paid_at") is not None and p.get("system_ddtd"):
                for reg in {VR.receipt_register(d, tail) for d in p.get("receipts") or []} - {None}:
                    items.append((p["paid_at"], float(p["amount"]), p["id"], reg))
        for o in a.get("ours") or []:
            reg = VR.receipt_register(o[0].ebarimt_id, tail)
            if reg and o[0].created_at is not None:
                items.append((o[0].created_at, float(o[0].amount or 0), o[1].id, reg))
        times = [VR._when(t) for t in left]
        pairs = VR._pairs(times, [t["amount"] for t in left], [x[0] for x in items], [x[1] for x in items],
                          OWN_TIME_TOL,
                          ok=lambda i, j: (times[i] >= items[j][0] - OWN_TIME_BEFORE
                                           and left[i].get("group") == items[j][3]))
        for _i, j, _d in VR._assign(pairs):
            out.setdefault(items[j][2], "TIME")
    return out


def _evidence_chunk(db, tax: list, *, site_ids: list, tenant_of: dict, tol: int,
                    shifts: list[float], core: list | None = None,
                    pos_users: set | None = None) -> dict:
    """Нэг хэсэг (хэдэн хоног) мөрийн нотолгоо — terminal_evidence-ийн дүрмээр.
    core: хэсгийн ҮНДСЭН мөрүүд (хамрах хүрээ, нотолгоо зөвхөн тэдний хугацаанд); tax нь
    хөрш хэсгээс нэмсэн нөөц мөртэй (хилийн борлуулалтын терминалын мөр орохын тулд).
    Түрээслэгчийн хувийг (OWN_MATCH_SHARE) энд ШИЙДЭХГҮЙ — "conf"/"own_rows"-ийг буцааж
    terminal_evidence ФАЙЛЫН түвшинд шийднэ ("low_share" — хэсгийн хувь бага)."""
    from collections import Counter
    st = {"rows": len(tax), "usable": False, "window": None, "tenant": None, "own_registers": set(),
          "terminal_registers": {}, "qr_registers": set(), "terminal_matched": 0,
          "matched_ours": 0, "own_rows": 0, "note": "", "hits": set(), "term_rows": [],
          "paired_rows": set(), "conf": Counter(), "low_share": False, "own_present": {},
          "sales": {}, "contested": set()}
    a = reconcile_db(db, tax, shifts=shifts, tol=tol, ours_sites=site_ids,
                     probe_sites=site_ids, scope_ids=set(site_ids), pos_users=pos_users)
    r = a["r"]
    tail = r.get("tail")
    pay_site = {p["id"]: p["site_id"] for p in a["probe"]}
    core = tax if core is None else core
    conf = Counter()
    core_ids = {id(t) for t in core}
    for t in tax:                           # шийдвэрт нөөц мөр ч (ижил файл); статистикт үндсэн л
        o = t.get("ours")
        if o is None or VR.receipt_register(o[0].ebarimt_id, tail) != t.get("group"):
            continue                       # ДДТД-ийн кассаар батлагдаагүй тааралт
        sid = pay_site.get(o[1].id) or getattr(o[1], "site_id", None)
        if sid in tenant_of:
            conf[tenant_of[sid]] += 1
            if id(t) in core_ids:
                st["conf"][tenant_of[sid]] += 1
    own_regs = {VR.receipt_register(t["ours"][0].ebarimt_id, tail) for t in tax
                if t.get("ours")} - {None}
    st["own_registers"] = own_regs
    own_all = sum(1 for t in tax if t.get("group") in own_regs)
    st["own_rows"] = sum(1 for t in core if t.get("group") in own_regs)
    st["matched_ours"] = sum(st["conf"].values())
    if not conf:
        st["note"] = ("манай баримттай (ДДТД-ийн кассаар) таарсан мөр алга — өөр ТТД-ийн экспорт, "
                      "эсвэл манай кассын мөрийг шүүж хаясан")
        return st
    top, n = conf.most_common(1)[0]
    total = sum(conf.values())
    share = total / max(1, own_all)
    if n * 2 <= total:
        st["note"] = (f"манай кассын тааралт түрээслэгчдэд тарсан ({n}/{total}) — энэ түрээслэгчийн "
                      "экспорт гэж батлагдсангүй")
        return st
    if share < OWN_MATCH_SHARE:
        st["low_share"] = True
        st["note"] = f"манай кассын {own_all} мөрийн {total} ({share:.0%}) л манай баримттай таарсан"
    own_sites = [sid for sid in site_ids if tenant_of[sid] == top]
    if len(own_sites) != len(site_ids):
        # Терминалын мөрийг ЗӨВХӨН энэ түрээслэгчийн төлбөртэй хосолно (--tenant ALL):
        # өөр ТТД-ийн ижил дүнтэй төлбөр Ontime мөрийг «булааж» жинхэнэ борлуулалт
        # «терминалын баримтгүй» болж ДАХИН гаргагдах эрсдэлтэй
        a = reconcile_db(db, tax, shifts=shifts, tol=tol, ours_sites=own_sites,
                         probe_sites=own_sites, scope_ids=set(own_sites), pos_users=pos_users)
        r = a["r"]
    if not core:
        st["note"] = "үндсэн мөр алга"
        return st
    term_regs, qrs = terminal_registers(tax, r, a["probe"], tol)
    st["usable"], st["tenant"] = True, top
    st["shift"] = r["shift"]
    st["qr_registers"] = set(qrs)
    rows_by_reg = Counter(t.get("group") for t in core if t.get("group") in term_regs)
    st["terminal_registers"] = {k: {"rows": rows_by_reg.get(k, 0), "shifts": {v}}
                                for k, v in term_regs.items()}
    # Хамрах хүрээг terminal_evidence ФАЙЛ бүрээр (terminal_window) тооцно — хэсгийн хил дээр
    # нүх гаргахгүй; хос нь нөөц мөртэй тул хилийн борлуулалт ч зөв хосолно
    pos = [p for p in a["probe"] if tenant_of.get(p.get("site_id")) == top]
    paired, _stats, contested = site_aware_pairs([t for t in tax if t.get("group") in term_regs], pos, tol)
    hits = set(paired)
    st["hits"] = hits
    st["contested"] = contested
    st["terminal_matched"] = len(hits)
    # Хосгүй терминалын мөрийг (ойролцоо зөрүүний шалгалт) ДДТД-ээр — нөөц мөр хөрш хэсэгт
    # хосолсон байж болох тул «хосолсон» нь бүх хэсгийн НЭГДЭЛ (terminal_evidence)
    st["paired_rows"] = {t["ddtd"] for t in paired.values()}
    st["term_rows"] = [(t["ddtd"], VR._when(t), float(t["amount"])) for t in core
                       if t.get("group") in term_regs]
    # Зогсоол × өдрийн терминалын хэрэглээ (ФАЙЛЫН түвшинд нэгтгэнэ — evidence_split-ийн
    # ТОДОРХОЙГҮЙ шалгалт) ба манай кассын мөрөөр «ТЕГ-т манай баримт бий» борлуулалт
    st["sales"] = {p["id"]: (p.get("site_id"), p["paid_at"], float(p["amount"])) for p in _pos_sales(pos)}
    st["own_present"] = own_presence(db, core, a)
    return st


def terminal_registers(tax: list, r: dict, probe: list, tol: int) -> tuple[dict, list]:
    """Терминалын (Ontime г.м) касс: «өөр систем» (манай баримтын касс биш) бөгөөд манай
    төлбөртэй таарсан мөрийн олонх нь QR БИШ. {касс: шилжилт}, [QPay гэж хассан касс]."""
    regs, qrs = {}, []
    for reg in r.get("foreign_registers") or []:
        n_hit, qr = _register_qr_share(tax, reg, probe, tol)
        if n_hit >= QR_REGISTER_MIN_HITS and qr / n_hit > QR_REGISTER_SHARE:
            qrs.append(reg)
            continue
        regs[reg] = r["group_shifts"].get(reg, 0)
    return regs, qrs


def file_days(tax: list) -> tuple[datetime, datetime]:
    """Файлын ХАРАГДАХ огнооны муж [эхний өдөр 00:00, сүүлийн өдөр+1 00:00) — файлын өөрийн цагаар."""
    days = [t["dt"].date() for t in tax]
    return (datetime.combine(min(days), datetime.min.time()),
            datetime.combine(max(days), datetime.min.time()) + timedelta(days=1))


def terminal_window(core: list, d0: datetime, d1: datetime, term_regs: dict, shifts: list[float],
                    tol: int, shift: float, at_end: bool = True) -> tuple[datetime, datetime]:
    """Терминалын нотолгооны хамрах хүрээ (UTC) — ТЕРМИНАЛЫН КАССЫН ӨӨРИЙН цагийн бүсээр.

    ТЕГ экспорт касс бүрийг ХАРАГДАХ огноогоор нь шүүдэг (msgbill УБ, Ontime/QPay UTC — бодит
    20260819.xlsx): D өдрийн файлд msgbill мөр УБ D 00:00 (= UTC D-1 16:00)-оос, харин Ontime мөр
    UTC D 00:00 (= УБ D 08:00)-оос эхэлдэг. Бүх кассын нэгдэл хүрээгээр бол УБ 00:00–08:00-ийн
    POS аппын борлуулалт (Ontime мөр нь D-1 өдрийн файлд) «терминалын мөргүй» → ДАХИН гаргах →
    давхардал болох байв (хяналт 2, C). Хүрээ = файлын мөрийн хүрээ ∩ [d0, d1) + терминалын
    кассын шилжилт; терминалын касс алга бол бүх боломжит шилжилтийн ОГТЛОЛЦОЛ. Файлын
    сүүлийн хил дээр tol-оор хасна (дараагийн өдөр рүү орсон хоцорсон мөр файлд байхгүй);
    at_end=False — дотоод хэсгийн хил (хөрш хэсэг нөөц мөртэй)."""
    lo, hi = VR.file_window(core, tol, shift)
    # Мөрийн хил: эхний мөрөөс (өмнөх мөр файлд байхгүй), сүүлийн мөрөөс tol-оор ӨМНӨ
    # (терминалын мөр төлбөрөөс хойно бүртгэгддэг — сүүлчийн борлуулалтын мөр файлд орохгүй байж болно)
    pad = timedelta(seconds=tol + 5)
    lo, hi = lo + pad, hi - pad - timedelta(seconds=tol)
    offs = sorted(set(term_regs.values())) if term_regs else sorted(set(shifts))
    t_lo = d0 + timedelta(hours=max(offs))
    t_hi = d1 + timedelta(hours=min(offs)) - (timedelta(seconds=tol) if at_end else timedelta(0))
    return max(lo, t_lo), min(hi, t_hi)


def terminal_pairs(rows: list, probe: list, tol: int, prefer: set | None = None) -> dict:
    """Терминалын мөрийг ЗӨВХӨН POS аппын бэлэн/карт төлбөртэй (терминал өөр төлбөрт
    хэвлэдэггүй) нэг-нэгээр, хамгийн ойроор хосолно (vat_recon._pairs/_assign — мөр бүр
    өөрийн кассын шилжилттэй), мөр төлбөрөөс TERMINAL_LAG_SLACK-аас ӨМНӨ биш.
    explain_unmatched_tax-ийн «кассын зогсоол» (тааралтын олонх) давуу эрэмбийг ЭНД
    хэрэглэхгүй: 2026-10-14-өөс олон зогсоолын терминал нэг Ontime кассаар (…10002990)
    ажилладаг тул олонхын зогсоолын QR/веб кассын ижил дүнтэй төлбөр бусад зогсоолын POS
    аппын борлуулалтаас Ontime мөрийг булааж (synthetic 10 хоног: 35/1118) жинхэнэ
    борлуулалт «терминалын баримтгүй» → ДАХИН гаргах → давхардал болж байв.
    prefer: {(site_id, УБ өдөр)} — тэр өдөр терминалаа ашигласан зогсоолын борлуулалт түрүүнд
    (site_aware_pairs). Буцаах {payment_id: мөр}."""
    pos = _pos_sales(probe)
    times = [VR._when(t) for t in rows]
    pairs = VR._pairs(times, [t["amount"] for t in rows], [p["paid_at"] for p in pos],
                      [p["amount"] for p in pos], tol,
                      ok=lambda i, j: times[i] >= pos[j]["paid_at"] - TERMINAL_LAG_SLACK)
    cost = None
    if prefer is not None:
        keys = [(p.get("site_id"), ub_day(p["paid_at"])) for p in pos]
        cost = lambda pr: (0 if keys[pr[2]] in prefer else 1,)  # noqa: E731
    return {pos[j]["id"]: rows[i] for i, j, _d in VR._assign(pairs, cost)}


def terminal_summary(tax: list, probe: list, paired: dict, tol: int, shift: float,
                     scope_ids: set | None, window: tuple | None = None) -> dict:
    """UI-ийн «POS терминал гаргасан ч ТЕГ-т баримт алга» (vat_recon.terminal_check-ийн
    хэлбэр): терминалын хамрах хүрээнд (terminal_window) байгаа терминалын мөртэй
    (EXTERNAL/ONTIME) төлбөрөөс хосгүй нь."""
    terminal = [p for p in probe if p.get("terminal")
                and (scope_ids is None or p.get("site_id") in scope_ids)]
    if not tax or not terminal:
        return {"matched": 0, "missing": [], "inside": 0}
    lo, hi = window or VR.file_window(tax, tol, shift)
    inside = [p for p in terminal if p.get("paid_at") is not None and lo <= p["paid_at"] <= hi]
    missing = []
    for p in inside:
        if p["id"] in paired:
            continue
        t = p["terminal"][0]
        missing.append({"paid_at": p["paid_at"].isoformat(), "plate": p.get("plate"),
                        "site_name": p.get("site_name"), "amount": float(p["amount"]),
                        "status": t.get("status"), "provider": t.get("provider"),
                        "ebarimt_id": t.get("ebarimt_id"), "mock": False,
                        "payment_id": p["id"], "method": p.get("method")})
    return {"matched": len(inside) - len(missing), "missing": missing, "inside": len(inside)}


def _file_chunks(tax: list, chunk_days: int, margin: timedelta):
    """Файлыг өөрийн огноогоор chunk_days хоногоор: (эхний өдөр, сүүлийн өдөр, d0, d1, үндсэн, нөөцтэй)."""
    days = sorted({t["dt"].date() for t in tax})
    step = max(1, int(chunk_days or 1))
    for i in range(0, len(days), step):
        want = days[i:i + step]
        d0 = datetime.combine(want[0], datetime.min.time())
        d1 = datetime.combine(want[-1], datetime.min.time()) + timedelta(days=1)
        core = [t for t in tax if d0 <= t["dt"] < d1]
        part = [t for t in tax if d0 - margin <= t["dt"] < d1 + margin]
        yield want[0], want[-1], d0, d1, core, part


def terminal_evidence(db, taxes: list, *, sites: list, tol: int, shifts: list[float],
                      chunk_days: int = EVIDENCE_CHUNK_DAYS, pos_users: set | None = None) -> dict:
    """ТЕГ экспорт(ууд)-аас «терминалын баримт» нотолгоо.

    taxes: [(файлын нэр, parse_tax_export мөрүүд)]; sites: тулгах зогсоолууд — сонгосон
    түрээслэгч(ид)-ийн БҮХ зогсоол (ТЕГ файл ТТД-ийн бүх борлуулалтыг агуулдаг; хэсгийг нь
    өгвөл Ontime мөр хүрээнээс гадуурх төлбөрийн оронд буруу төлбөртэй хосолно).
    Урт хугацааны (сарын) файлыг файлын өөрийн огноогоор chunk_days хоногоор хувааж тус
    бүрийг тулгана (санах ой: бүхэлд нь 10 хоног ≈ 320MB). Хэсэг бүрт хөрш хоногийн
    мөрийг (касс бүрийн цагийн бүсийн зөрүү + tol хүртэл) НӨӨЦ болгон нэмнэ — хэсгийн
    хамрах хүрээнд (үндсэн мөрүүдийн хугацаа) байгаа борлуулалтын терминалын мөр хоногийн
    хилийн нөгөө талд байсан ч тэр хэсэгт ОРНО (эс бөгөөс «терминалын мөргүй» → ДАХИН гаргах
    → давхардал); нотолгоо ба хүрээ нь зөвхөн үндсэн мөрүүдийн хугацаанд. Хэсэг бүрт:
      • хамрах хугацаа — file_window (касс бүрийн шилжилтээр);
      • хамрах ТҮРЭЭСЛЭГЧ — экспорт ТТД-ээр гардаг. msgbill/QPay-ийн касс түрээслэгчдэд
        НИЙТЛЭГ тул: файлын манай кассын мөртэй ДДТД-ийн кассаар батлагдан таарсан манай
        баримтын ОЛОНХ (>50%) нь нэг түрээслэгчийнх БӨГӨӨД тэр тааралт манай кассын мөрийн
        ≥50%-ийг хамарсан бол л тэр түрээслэгчийн файл — ФАЙЛЫН нийт мөрөөр (2026-10-15; нэг
        хэсгийн хувь бага ч — QPay-ийн бөөн дахин илгээлт — файлын түвшинд батлагдвал хамруулна,
        тэмдэглэнэ). Эс бөгөөс (өөр ТТД-ийн файл, манай кассын мөрийг шүүж хаясан, касс
        танигдаагүй) НОТОЛГОО БОЛОХГҮЙ — ямар ч төлбөрийг «терминалын баримтгүй» гэж дүгнэхгүй;
      • ТЕРМИНАЛЫН касс — «өөр систем» (манай msgbill/QPay/PosAPI баримтын касс биш)
        бөгөөд манай төлбөртэй таарсан мөрийн олонх нь QR БИШ (манай QPay баримт тэр
        хугацаанд алга бол QPay-ийн касс «өөр систем» болж магадгүй — QR-тэй таардаг);
      • терминалын мөртэй хосолсон тухайн түрээслэгчийн төлбөрүүд (site_aware_pairs — тэр
        өдөр терминалаа ашигласан зогсоолын борлуулалт түрүүнд);
      • МАНАЙ кассын мөрөөр ТЕГ-т манай баримт (эсвэл msgbill-ийн давхар баримт) БИЙ борлуулалт
        (own_presence) — дахин гаргавал давхардана.
    Олон файл/хэсэг: хамрах = НЭГДЭЛ, нотолгоо = НЭГДЭЛ (аль нэгэнд таарвал болно).
    Хамрах хүрээ нь ТЕРМИНАЛЫН кассын өөрийн цагийн бүсээр (terminal_window). Хосгүй
    терминалын мөрүүд "unpaired"-д (ойролцоо зөрүүний AMBIGUOUS шалгалт — evidence_split).
    Буцаах {"files": [статистик], "terminal_ids": set, "coverage": [(lo, hi, {tenant_id})],
            "unpaired": [(UTC цаг, дүн, tenant_id)], "days": {УБ огноо: {rows, paired}},
            "own_present": {payment_id: "DDTD"|"TWIN"|"TIME"},
            "site_days": {(site_id, УБ огноо): [POS аппын борлуулалт, терминалын мөртэй]},
            "terminal_days": {(site_id, УБ огноо)} — тэр өдөр терминалаар хэвлэсэн,
            "offset_days": {(site_id, УБ огноо): n} — терминалын цаг системтэйгээр зөрсөн өдөр,
            "near_days": terminal_days ∪ offset_days — ойролцоо хосгүй мөр ТОДОРХОЙГҮЙ болгох өдрүүд,
            "contested": {payment_id} — мөрөө терминалтай зогсоолд алдсан (маргаантай)}."""
    site_ids = [s.id for s in sites]
    tenant_of = {s.id: s.tenant_id for s in sites}
    terminal_ids, coverage, files = set(), [], []
    unpaired, day_stats, own_present, sales, contested = [], {}, {}, {}, set()
    rank = {"DDTD": 0, "TWIN": 1, "TIME": 2}
    loc = timedelta(hours=settings.tz_offset_hours)
    for name, tax in taxes:
        st = {"file": name, "rows": len(tax), "usable": False, "window": None, "tenants": set(),
              "own_registers": [], "terminal_registers": {}, "qr_registers": [],
              "terminal_rows": 0, "terminal_matched": 0, "matched_ours": 0, "own_rows": 0,
              "chunks": 0, "unusable_chunks": 0, "low_share_chunks": 0, "note": "",
              "own_present": Counter()}
        files.append(st)
        if not tax:
            st["note"] = "мөр алга"
            continue
        margin = timedelta(hours=max(abs(x) for x in shifts) + 1, seconds=tol + 5)
        chunks = []
        for first, last, d0, d1, core, part in _file_chunks(tax, chunk_days, margin):
            c = _evidence_chunk(db, part, site_ids=site_ids, tenant_of=tenant_of, tol=tol,
                                shifts=shifts, core=core, pos_users=pos_users)
            for t in part:
                t.pop("ours", None)       # ORM мөрийн лавлагааг хэсэг бүрийн дараа чөлөөлнө
            c.update(label=f"{first}…{last}", d0=d0, d1=d1)
            chunks.append(c)
        # Түрээслэгчийн батламж ФАЙЛЫН түвшинд (хэсгийн үндсэн мөрүүдийн нийлбэрээр)
        fconf = Counter()
        for c in chunks:
            fconf.update(c["conf"])
        f_total = sum(fconf.values())
        f_own = sum(c["own_rows"] for c in chunks)
        ftop, fn = fconf.most_common(1)[0] if fconf else (None, 0)
        fshare = f_total / max(1, f_own)
        file_ok = bool(fconf) and fn * 2 > f_total and fshare >= OWN_MATCH_SHARE
        st["chunks"], st["matched_ours"], st["own_rows"] = len(chunks), f_total, f_own
        own, qrs, notes, wins, file_hits = set(), set(), [], [], set()
        term_rows, paired_rows = {}, set()
        for c in chunks:
            own |= c["own_registers"]
            why = None
            if not c["usable"]:
                why = c["note"]
            elif not file_ok:
                why = (f"файлын манай кассын {f_own} мөрийн {f_total} ({fshare:.0%}) л манай баримттай "
                       "таарсан — энэ түрээслэгчийн экспорт гэж батлагдсангүй")
            elif c["tenant"] != ftop:
                why = "хэсгийн манай баримт файлын түрээслэгчийнх биш — батлагдсангүй"
            if why:
                st["unusable_chunks"] += 1
                notes.append(f"{c['label']}: {why}")
                continue
            if c["low_share"]:
                st["low_share_chunks"] += 1
                notes.append(f"{c['label']}: {c['note']} — ФАЙЛЫН түвшинд {fshare:.0%} батлагдсан тул хамруулав")
            wins.append((c["d0"], c["d1"], c["tenant"], c["shift"]))
            st["tenants"].add(c["tenant"])
            qrs |= c["qr_registers"]
            for k, v in c["terminal_registers"].items():
                agg = st["terminal_registers"].setdefault(k, {"rows": 0, "shifts": set()})
                agg["rows"] += v["rows"]
                agg["shifts"] |= v["shifts"]
            file_hits |= c["hits"]
            contested |= c["contested"]
            paired_rows |= c["paired_rows"]
            for ddtd, at, amount in c["term_rows"]:
                term_rows[ddtd] = (at, amount, c["tenant"])
            for pid, kind in c["own_present"].items():
                if rank[kind] < rank.get(own_present.get(pid), 9):
                    own_present[pid] = kind
            sales.update(c["sales"])
        terminal_ids |= file_hits
        # Хамрах хүрээ: ФАЙЛЫН терминалын хүрээ (terminal_window — терминалын кассын цагийн
        # бүс, файлын эхний/сүүлийн өдрийн хил) ∩ ашиглах боломжтой хэсгүүдийн хоногууд
        # (терминалын кассын шилжилтээр UTC болгосон; хэсгүүд хоорондоо залгаа)
        st["window"] = None
        if wins:
            fd0, fd1 = file_days(tax)
            regs_all = {f"{k}:{x}": x for k, v in st["terminal_registers"].items() for x in v["shifts"]}
            offs = sorted(set(regs_all.values())) or sorted(set(shifts))
            f_lo, f_hi = terminal_window(tax, fd0, fd1, regs_all, shifts, tol, wins[0][3])
            parts_cov = []
            for d0, d1, ten, _sh in wins:
                lo_ = max(f_lo, d0 + timedelta(hours=max(offs)))
                hi_ = min(f_hi, d1 + timedelta(hours=min(offs)))
                if lo_ < hi_:
                    coverage.append((lo_, hi_, {ten}))
                    parts_cov.append((lo_, hi_))
            if parts_cov:
                st["window"] = (min(x[0] for x in parts_cov), max(x[1] for x in parts_cov))
        st["terminal_unpaired"] = 0
        for ddtd, (at, amount, ten) in term_rows.items():
            d = day_stats.setdefault((at + loc).date().isoformat(), {"rows": 0, "paired": 0})
            d["rows"] += 1
            if ddtd in paired_rows:
                d["paired"] += 1
            else:
                unpaired.append((at, amount, ten))
                st["terminal_unpaired"] += 1
        st["terminal_matched"] = len(file_hits)
        st["own_registers"] = sorted(own)
        st["qr_registers"] = sorted(qrs)
        st["terminal_rows"] = sum(v["rows"] for v in st["terminal_registers"].values())
        st["usable"] = st["window"] is not None
        st["own_present"] = Counter(v for pid, v in own_present.items())
        st["note"] = "; ".join(notes[:3]) + (f" (+{len(notes) - 3})" if len(notes) > 3 else "")
    # Зогсоол × өдрийн терминалын хэрэглээ — хамрах хүрээнд байгаа POS аппын борлуулалтаар
    site_days: dict = {}
    covered = {}
    for pid, (sid, at, amount) in sales.items():
        ten = tenant_of.get(sid)
        if not any(lo <= at <= hi and ten in tens for lo, hi, tens in coverage):
            continue
        covered[pid] = (sid, at, amount, ten)
        c = site_days.setdefault((sid, ub_day(at)), [0, 0])
        c[0] += 1
        c[1] += pid in terminal_ids
    tdays = terminal_days(site_days)
    odays = offset_days(unpaired, covered, terminal_ids, site_days, tdays)
    return {"files": files, "terminal_ids": terminal_ids, "coverage": coverage,
            "unpaired": unpaired, "days": dict(sorted(day_stats.items())),
            "own_present": own_present, "site_days": site_days,
            "terminal_days": tdays, "offset_days": odays, "near_days": tdays | set(odays),
            "contested": contested - terminal_ids}


def offset_days(unpaired: list, sales: dict, paired_ids: set, site_days: dict, tdays: set) -> dict:
    """Терминалын мөртэй ОГТ (≥10%) хосолоогүй (зогсоол, УБ өдөр) — терминалын цаг СИСТЕМТЭЙГЭЭР
    зөрсөн (бүх мөр нь хосгүй, хяналт 2-ын D) уу, эсвэл тэр зогсоол терминалгүй (3HOSPITAL) уу.
    ХОСГҮЙ терминалын мөр бүрийг НЭГ л борлуулалтад (ижил дүн, −10 мин…+2 цаг, терминал ашигласан
    зогсоолынх түрүүнд, дараа нь хамгийн ойр) оноож, тэр өдрийн POS аппын борлуулалтын ≥10% нь
    ийм мөртэй бол цаг зөрсөн терминалын өдөр гэж үзнэ — тэр өдрийн ойролцоо мөр ТОДОРХОЙГҮЙ
    хэвээр (дахин гаргахгүй). 3HOSPITAL: бусад зогсоолын хэдэн хосгүй мөр ~300 борлуулалтад
    ≤2% → терминалгүй. sales: {payment_id: (site_id, UTC цаг, дүн, tenant_id)} (хамрах хүрээнд).
    Буцаах {(site_id, УБ өдөр): оноогдсон мөрийн тоо}."""
    if not unpaired:
        return {}
    items = [(pid, v) for pid, v in sales.items()
             if pid not in paired_ids and (v[0], ub_day(v[1])) not in tdays]
    if not items:
        return {}
    pref = [(pid, v) for pid, v in sales.items()
            if pid not in paired_ids and (v[0], ub_day(v[1])) in tdays]
    allv = pref + items
    keys = [(v[0], ub_day(v[1])) for _pid, v in allv]
    times = [at for at, _a, _t in unpaired]
    pairs = VR._pairs(times, [a for _t, a, _x in unpaired], [v[1] for _pid, v in allv],
                      [v[2] for _pid, v in allv], NEAR_MISS_AFTER.total_seconds(),
                      ok=lambda i, j: (unpaired[i][2] == allv[j][1][3]
                                       and -NEAR_MISS_BEFORE <= times[i] - allv[j][1][1] <= NEAR_MISS_AFTER))
    hits: dict = {}
    for _i, j, _d in VR._assign(pairs, cost=lambda pr: (0 if keys[pr[2]] in tdays else 1,)):
        if keys[j] not in tdays:
            hits[keys[j]] = hits.get(keys[j], 0) + 1
    return {k: n for k, n in hits.items()
            if n >= SITE_DAY_MIN_PAIRS and n >= SITE_DAY_MIN_SHARE * site_days.get(k, [0, 0])[0]}


def evidence_split(rows: list, ev: dict, tenant_of: dict) -> dict:
    """Мөрүүдийг (payment_id, site_id, paid_at ISO UTC, amount) нотолгоогоор ангилна:
    {"covered": [ТЕГ файлын хамрах хүрээнд], "with_terminal": [терминалын мөртэй],
     "own_in_teg": [хүрээнд, терминалын мөргүй, гэхдээ МАНАЙ баримт (идэвхтэй/цуцалсан ч
                    экспортод үлдсэн) эсвэл msgbill-ийн давхар баримт нь ТЕГ-т БИЙ — мөрд own_teg],
     "missing": [хүрээнд, ТЕГ-т ямар ч баримтгүй, ойролцоо хосгүй мөр ч алга (эсвэл байгаа ч тэр
                 зогсоол тэр өдөр терминал ашиглаагүй — мөрд near_miss_ignored)],
     "ambiguous": [хүрээнд, баримтгүй БОЛОВЧ ижил дүнтэй ХОСГҮЙ терминалын мөр −10 мин…+2 цагт
                   бий бөгөөд тэр зогсоол тэр өдөр терминалаар хэвлэсэн — цаг зөрсөн байж магадгүй;
                   эсвэл мөр нь хамгийн ойроор энэ борлуулалттай хосолсон ч терминалтай зогсоолынх
                   руу шилжсэн (маргаантай хос — мөрд contested)],
     "outside": [хүрээнээс гадуур]}.
    ev-д "terminal_days" алга бол (хуучин) ойролцоо мөрийг зогсоолоос үл хамааран тооцно."""
    from bisect import bisect_left, bisect_right
    near: dict = {}
    for at, amount, ten in ev.get("unpaired") or []:
        near.setdefault((ten, int(round(amount))), []).append(at)
    for v in near.values():
        v.sort()
    tdays = ev.get("near_days", ev.get("terminal_days"))
    own = ev.get("own_present") or {}
    out = {"covered": [], "with_terminal": [], "own_in_teg": [], "missing": [], "ambiguous": [],
           "outside": []}
    for r in rows:
        at = datetime.fromisoformat(r["paid_at"])
        ten = tenant_of.get(r.get("site_id"))
        if not any(lo <= at <= hi and ten in tens for lo, hi, tens in ev["coverage"]):
            out["outside"].append(r)
            continue
        out["covered"].append(r)
        if r["payment_id"] in ev["terminal_ids"]:
            out["with_terminal"].append(r)
            continue
        if r["payment_id"] in own:
            r["own_teg"] = own[r["payment_id"]]
            out["own_in_teg"].append(r)
            continue
        if r["payment_id"] in (ev.get("contested") or ()):
            # Терминалын мөр хамгийн ойроор энэ борлуулалттай хосолсон ч тэр өдөр терминалтай зогсоолын
            # борлуулалтад шилжсэн — аль нь болохыг мэдэхгүй (site_aware_pairs) → дахин гаргахгүй
            r["contested"] = True
            out["ambiguous"].append(r)
            continue
        ts = near.get((ten, int(round(float(r["amount"])))), [])
        hit = bisect_right(ts, at + NEAR_MISS_AFTER) - bisect_left(ts, at - NEAR_MISS_BEFORE)
        if hit and tdays is not None and (r.get("site_id"), ub_day(at)) not in tdays:
            r["near_miss_ignored"] = hit
            hit = 0
        (out["ambiguous"] if hit > 0 else out["missing"]).append(r)
    return out


def _day_ranges(days: list) -> str:
    """['2026-09-03', '2026-09-04', '2026-09-07'] → «09-03..09-04, 09-07»."""
    out, run = [], []
    for d in sorted(days):
        if run and (datetime.fromisoformat(d) - datetime.fromisoformat(run[-1])).days == 1:
            run.append(d)
            continue
        if run:
            out.append(run[0][5:] + (f"..{run[-1][5:]}" if len(run) > 1 else ""))
        run = [d]
    if run:
        out.append(run[0][5:] + (f"..{run[-1][5:]}" if len(run) > 1 else ""))
    return ", ".join(out)


def print_evidence(ev: dict, split: dict | None = None, log=print, tenants: dict | None = None,
                   site_names: dict | None = None):
    """Файл бүрийн хамрах хүрээ/касс/тааралтын статистик (CLI)."""
    loc = timedelta(hours=settings.tz_offset_hours)
    for st in ev["files"]:
        if st["window"]:
            lo, hi = st["window"]
            when = f"{(lo + loc):%Y-%m-%d %H:%M} → {(hi + loc):%Y-%m-%d %H:%M} (УБ)"
        else:
            when = "—"
        ten = ",".join(sorted((tenants or {}).get(t, str(t)) for t in st["tenants"])) or "—"
        regs = " · ".join(f"…{k} {v['rows']} мөр ({'/'.join(f'{x:+g}' for x in sorted(v['shifts']))}ц)"
                          for k, v in sorted(st["terminal_registers"].items())) or "АЛГА"
        log(f"\nНОТОЛГОО {st['file']}: {st['rows']} мөр · {when} · түрээслэгч {ten}"
            f" · манай касс {', '.join('…' + x for x in st['own_registers']) or '—'}"
            f" ({st['own_rows']} мөр, манай баримттай таарсан {st['matched_ours']})"
            + (f" · {st['chunks']} хэсэг" if st.get("chunks", 0) > 1 else ""))
        if not st["usable"]:
            log(f"  ⚠ АШИГЛАХГҮЙ: {st['note']}")
            continue
        if st.get("unusable_chunks"):
            log(f"  ⚠ {st['unusable_chunks']}/{st['chunks']} хэсэг нотолгоо болоогүй (хүрээнд ороогүй): {st['note']}")
        elif st.get("low_share_chunks"):
            log(f"  ℹ {st['note']}")
        log(f"  терминалын касс: {regs} · терминалын мөртэй манай төлбөр {st['terminal_matched']}"
            + f" · ХОСГҮЙ терминалын мөр {st.get('terminal_unpaired', 0)}"
            + (f" · QPay гэж хассан касс: {', '.join(st['qr_registers'])}" if st["qr_registers"] else ""))
        if not st["terminal_registers"]:
            log("  ⚠ ЭНЭ ФАЙЛД ТЕРМИНАЛЫН (Ontime) МӨР ОГТ АЛГА — хугацаанд нь терминал баримт хэвлээгүй, "
                "эсвэл экспортыг шүүж татсан байж болно. Файлаа шалгана уу.")
    days = ev.get("days") or {}
    if days:
        log("\nТЕРМИНАЛЫН МӨР өдрөөр (УБ): " + " · ".join(
            f"{d[5:]} {v['rows']}/{v['paired']}" + (f" (хосгүй {v['rows'] - v['paired']})"
                                                     if v["rows"] > v["paired"] else "")
            for d, v in days.items()) + "   [мөр/хосолсон]")
    sd = ev.get("site_days") or {}
    if sd:
        tdays = ev.get("terminal_days") or set()
        by_site: dict = {}
        for (sid, day), (n, k) in sd.items():
            s = by_site.setdefault(sid, {"on": [], "off": [], "n": 0, "k": 0})
            s["on" if (sid, day) in tdays else "off"].append(day)
            s["n"] += n
            s["k"] += k
        log(f"\nТЕРМИНАЛ АШИГЛАСАН ӨДӨР (зогсоол × УБ өдөр — POS аппын карт/бэлэн борлуулалтын "
            f"≥{SITE_DAY_MIN_SHARE:.0%} нь терминалын мөртэй; бусад өдөр ойролцоо хосгүй мөр "
            "ТОДОРХОЙГҮЙ болгохгүй):")
        for sid, s in sorted(by_site.items(), key=lambda kv: (site_names or {}).get(kv[0]) or str(kv[0])):
            log(f"  {(site_names or {}).get(sid) or sid}: хосолсон {s['k']}/{s['n']} · "
                f"терминалтай {len(s['on'])} өдөр ({_day_ranges(s['on']) or '—'})"
                + (f" · терминалгүй {len(s['off'])} өдөр ({_day_ranges(s['off'])})" if s["off"] else ""))
    if split is not None:
        cov = len(split["covered"])
        pct = (len(split["with_terminal"]) / cov) if cov else 0
        own = split.get("own_in_teg", [])
        ign = sum(1 for r in split["missing"] if r.get("near_miss_ignored"))
        log(f"\nХАМРАЛТ: хүрээнд {cov} төлбөр · терминалын мөртэй {len(split['with_terminal'])} ({pct:.1%})"
            f" · МАНАЙ баримт ТЕГ-т бий {len(own)}"
            f" · терминалын мөргүй {len(split['missing'])}"
            + (f" (үүнээс {ign} нь ойролцоо хосгүй мөртэй боловч зогсоол тэр өдөр терминалгүй)" if ign else "")
            + f" · ТОДОРХОЙГҮЙ (ойролцоо хосгүй терминалын мөртэй) {len(split.get('ambiguous', []))}"
            + (f" (үүнээс маргаантай хос {sum(1 for r in split['ambiguous'] if r.get('contested'))})"
               if any(r.get("contested") for r in split.get("ambiguous", [])) else "")
            + f" · хүрээнээс гадуур {len(split['outside'])}")
        if own:
            kinds = Counter(r.get("own_teg") for r in own)
            log("  МАНАЙ баримт ТЕГ-т: " + " · ".join(f"{OWN_TEG_MN.get(k, k)} {v}" for k, v in kinds.most_common()))
        cells: dict = {}
        for key in ("missing", "ambiguous"):
            for r in split.get(key, []):
                day = (datetime.fromisoformat(r["paid_at"]) + loc).date().isoformat()
                c = cells.setdefault((r.get("site_code") or r.get("site_name") or "?", day), [0, 0])
                c[0 if key == "missing" else 1] += 1
        if cells:
            log("  Зогсоол × өдөр (терминалын мөргүй / ТОДОРХОЙГҮЙ): " + " · ".join(
                f"{site} {day[5:]} {m}/{a}" for (site, day), (m, a) in sorted(cells.items())))


OWN_TEG_MN = {"DDTD": "ДДТД-ээр", "TWIN": "msgbill-ийн давхар баримт (ДДТД дараалал)",
              "TIME": "цаг+дүнгээр (0291… багцын дугаар г.м)"}


# ─────────────── ТЕРМИНАЛ ХЭВЛЭЭГҮЙ БОРЛУУЛАЛТЫГ ДАХИН ГАРГАХ (2026-10-14) ───────────────
#
# tools/vat_reissue_missing.py: EXTERNAL («терминал гаргасан», ДДТД-гүй) эсвэл POS-ийн
# давхардал гэж цуцлагдсан төлбөрийн ТЕГ экспортод терминалын мөр алга бол тэр борлуулалт
# ОДОО e-Barimt-ГҮЙ — манай msgbill баримтыг дахин гаргана (UI-ийн «Систем баримт гаргах»-тай
# нэг зам: financial_jobs.retry_payment_receipts).

REISSUE_REASON_MN = {
    "NOT_PAID": "PAID биш",
    "METHOD": "карт/бэлэн биш",
    "NOT_TERMINAL": "терминалын (EXTERNAL) мөр ч, давхардлын цуцлалт ч алга — энгийн төлбөр",
    "HAS_RECEIPT": "идэвхтэй баримттай (системийн SENT/PENDING/REVIEW/FAILED эсвэл ДДТД-тэй терминалын)",
    "TEG_CONFIRMED": "цуцлалт/терминалын мөр ТЕГ файлын Ontime мөрөөр батлагдсан — терминал хэвлэсэн",
}


def _teg_ddtd_of(rec) -> str | None:
    raw = getattr(rec, "raw", None)
    if not isinstance(raw, dict):
        return None
    for k in ("cancel", "teg"):
        v = raw.get(k)
        if isinstance(v, dict) and (v.get("teg_ddtd") or "").strip():
            return v["teg_ddtd"].strip()
    return None


def reissue_reason(payment, recs) -> str | None:
    """Энэ төлбөр «терминал гаргасан гэж үзсэн, одоо e-Barimt-гүй» нэр дэвшигч үү.
    None = нэр дэвшигч (ТЕГ-т терминалын мөр алга бол дахин гаргана); эс бөгөөс код.
    Сонголт ба түгжээний дараах дахин шалгалт хоёулаа үүгээр."""
    if payment.status != "PAID":
        return "NOT_PAID"
    if (payment.payment_method or "").upper() not in POS_APP_METHODS:
        return "METHOD"
    live = [r for r in recs if r.status != "CANCELLED"]
    ext = [r for r in live if r.status == "EXTERNAL" and not r.ebarimt_id]
    dup = [r for r in recs if r.status == "CANCELLED" and is_duplicate_cancel(r)]
    if not ext and not dup:
        return "NOT_TERMINAL"
    if len(ext) != len(live):
        return "HAS_RECEIPT"
    if any(_teg_ddtd_of(r) for r in ext + dup):
        return "TEG_CONFIRMED"
    return None


def select_reissue(db, *, sites, lo: datetime, hi: datetime) -> dict:
    """[lo, hi) хугацаанд EXTERNAL/цуцлагдсан баримттай CARD/CASH төлбөрүүдээс нэр
    дэвшигчдийг сонгоно (READ ONLY). {"candidates": [мөр], "skipped": Counter(код)}."""
    from collections import Counter

    from ..models import Tenant, User
    by_id = {s.id: s for s in sites}
    if not by_id:
        return {"candidates": [], "skipped": Counter()}
    tcode = {t.id: t.code for t in db.query(Tenant).all()}
    marked = (db.query(VatReceipt.payment_id)
              .filter(VatReceipt.status.in_(("EXTERNAL", "CANCELLED"))))
    rows = (db.query(Payment, User.username, PAY_PLATE, PAY_SITE).enable_eagerloads(False)
            .outerjoin(ParkingSession, Payment.session_id == ParkingSession.id)
            .outerjoin(User, User.id == Payment.cashier_id)
            .filter(Payment.status == "PAID", Payment.payment_method.in_(POS_APP_METHODS),
                    Payment.paid_at >= lo, Payment.paid_at < hi, PAY_SITE.in_(list(by_id)),
                    Payment.id.in_(marked))
            .order_by(Payment.paid_at, Payment.id).all())
    recs: dict[str, list] = {}
    ids = [p.id for p, *_ in rows]
    for i in range(0, len(ids), 1000):
        for r in db.query(VatReceipt).filter(VatReceipt.payment_id.in_(ids[i:i + 1000])).all():
            recs.setdefault(r.payment_id, []).append(r)
    loc = timedelta(hours=settings.tz_offset_hours)
    out, skipped = [], Counter()
    for p, uname, plate, sid in rows:
        rs = recs.get(p.id, [])
        why = reissue_reason(p, rs)
        if why:
            skipped[why] += 1
            continue
        site = by_id.get(sid)
        dup = [r for r in rs if r.status == "CANCELLED" and is_duplicate_cancel(r)]
        ext = [r for r in rs if r.status == "EXTERNAL"]
        rule = next((((r.raw or {}).get("cancel") or {}).get("rule") for r in dup
                     if isinstance(r.raw, dict)), None)
        out.append({"payment_id": p.id, "site_id": sid,
                    "tenant": tcode.get(getattr(site, "tenant_id", None), "") if site else "",
                    "site_code": site.site_code if site else "", "site_name": site.name if site else "",
                    "plate": plate or "", "method": (p.payment_method or "").upper(),
                    "amount": float(p.amount), "paid_at": p.paid_at.isoformat(),
                    "paid_at_ub": (p.paid_at + loc).strftime("%Y-%m-%d %H:%M:%S"),
                    "day": (p.paid_at + loc).strftime("%Y-%m-%d"),
                    "month": (p.paid_at + loc).strftime("%Y-%m"), "cashier": uname or "",
                    "state": "+".join(x for x in ("EXTERNAL" if ext else "",
                                                   "DUPLICATE_CANCELLED" if dup else "") if x),
                    "cancel_rule": rule or "",
                    "cancelled_ddtd": ", ".join(r.ebarimt_id or "" for r in dup),
                    "receiver_type": p.ebarimt_receiver_type or "",
                    "customer_tin": p.customer_tin or ""})
    return {"candidates": out, "skipped": skipped}


def reissue_one(db, payment_id: str, *, username: str, files: list, source: str = "cli") -> dict:
    """НЭГ төлбөрийн манай msgbill баримтыг дахин гаргана (ажил хадгална — HTTP-гүй; outbox
    илгээнэ). Түгжиж (NOWAIT), нэр дэвшигч эсэхийг ДАХИН шалгана (идемпотент — аль хэдийн
    гаргасан/өөрчлөгдсөнийг алгасна), raw.reissue + AuditLog EBARIMT_REISSUE_NO_TERMINAL, commit."""
    from fastapi import HTTPException

    from ..routers.payments_router import _lock_payment
    from .financial_jobs import retry_payment_receipts
    from .receipts import REISSUED_NO_TERMINAL
    try:
        payment = _lock_payment(db, payment_id)
    except HTTPException:
        return {"payment_id": payment_id, "ok": False, "retryable": True,
                "error": "Төлбөр өөр хүсэлтэд түгжигдсэн — дахин ажиллуулахад нөхөгдөнө"}
    if payment is None:
        db.rollback()
        return {"payment_id": payment_id, "ok": False, "error": "Төлбөр олдсонгүй"}
    recs = db.query(VatReceipt).filter(VatReceipt.payment_id == payment_id).all()
    why = reissue_reason(payment, recs)
    if why:
        db.rollback()
        return {"payment_id": payment_id, "ok": True, "skipped": why}
    mark = {"reason": REISSUED_NO_TERMINAL, "by": username, "source": source,
            "files": list(files), "at": datetime.utcnow().isoformat(timespec="seconds")}
    res = retry_payment_receipts(db, payment, reissue_external=True, allow_duplicate=True,
                                 no_terminal_receipt=True, username=username, mark=mark)
    db.expire_all()
    new = [r for r in db.query(VatReceipt).filter(VatReceipt.payment_id == payment_id).all()
           if isinstance(r.raw, dict) and (r.raw.get("reissue") or {}).get("reason") == REISSUED_NO_TERMINAL
           and r.status != "CANCELLED"]
    ok = bool(new) and all(r.status in ("PENDING", "SENT") for r in new)
    err = None if ok else (res.get("error") or "Шинэ баримт үүссэнгүй"
                           + (f" ({'/'.join(sorted({r.status for r in new}))}: "
                              f"{(new[0].receipt_url or '')[:120]})" if new else ""))
    db.add(AuditLog(username=username, action="EBARIMT_REISSUE_NO_TERMINAL", entity="payment",
                    entity_id=payment_id,
                    detail={"reason": REISSUED_NO_TERMINAL, "ok": ok, "error": err,
                            "receipts": [{"id": r.id, "status": r.status, "provider": r.provider}
                                         for r in new],
                            "files": list(files), "source": source}))
    db.commit()
    return {"payment_id": payment_id, "ok": ok, "error": err,
            "receipts": [{"id": r.id, "status": r.status, "provider": r.provider} for r in new]}


def issue_ready(db, sites: list) -> list[str]:
    """БОДИТ дахин гаргалтын өмнөх хамгаалалт (cancel_ready-тай ижил сервер/DB/MOCK шалгалт +
    зогсоол бүрд msgbill баримт ГАРГАХ түлхүүр). Хоосон = бэлэн."""
    from . import msgbill
    probs = server_problems()
    if settings.ebarimt_mock_receipts:
        probs.append("PARKING_EBARIMT_MOCK_RECEIPTS=true — MOCK баримттай тест сервер; бодит баримт гаргахгүй")
    base = (settings.msgbill_base_url or "").lower()
    if not base.startswith("https://") or any(x in base for x in ("localhost", "127.0.0.1", "mock")):
        probs.append(f"msgbill хаяг бодит биш ({settings.msgbill_base_url}) — msgbill MOCK горимд байна")
    for site in {getattr(s, "id", None): s for s in sites or []}.values():
        acc = msgbill.api_key_for(site)
        name = getattr(site, "name", None) or getattr(site, "id", "?")
        if not acc.enabled:
            probs.append(f"«{name}»: msgbill түлхүүр тохируулаагүй — баримт гаргаж чадахгүй")
        elif getattr(acc, "is_test", False):
            probs.append(f"«{name}»: msgbill ТЕСТ түлхүүр (bsk_test_) — бодит баримт гарахгүй")
    return probs
