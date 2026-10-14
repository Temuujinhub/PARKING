"""POS АППЫН БОРЛУУЛАЛТ — НЭГ ТОДОРХОЙЛОЛТ (2026-10-14 эзний эцсийн шийдвэр).

PAX POS терминал (Ontime) POS аппаар авсан КАРТ, БЭЛЭН борлуулалт бүрд e-Barimt-ыг
ӨӨРӨӨ гаргадаг — зогсоолоос, түрээслэгчээс ҮЛ хамааран (ИйзиПаркинг ХХК, Моннис
пропэрти, Моннисмоторс, ирээдүйн). Нягтлан 9-р сард Ontime + msgbill давхардлаас ~13
сая₮-ийн илүү НӨАТ-ын борлуулалт батлав. Систем ийм борлуулалтад msgbill баримт
ГАРГАХГҮЙ (financial_jobs.terminal_issues_receipt), түүхэн давхардлыг цуцална
(vat_duplicates --pos-app), терминал хэвлээгүй нь ТЕГ экспортоор батлагдвал дахин
гаргана (tools/vat_reissue_missing.py) — гурвуулаа ЭНЭ модулийн дүрмээр.

POS аппын төлбөр гэж (эхнийх нь таарвал):
  1. source == "POSAPP" — X-App-Version толгойтой хүсэлт, эсвэл /pos/confirm (10-14-ний
     deploy-оос хойш 20/20 POS аппын бэлэн төлбөр 1.5.1/1.5.3 толгойтой ирсэн);
  2. provider == "POS" ба kind PARKING — /pos/confirm-ын картын төлбөр (зөвхөн POS апп
     дууддаг; веб кассын «Нөхөн төлбөр» хуудсаар авсан картын ӨР нь kind DEBT — орохгүй);
  3. FALLBACK — толгой илгээдэггүй хуучин апп build: кассчин нь POS АППЫН ХЭРЭГЛЭГЧ:
       • role «POS»;
       • Тохиргоо → Төлбөрийн дүрэм → «POS аппын хэрэглэгчид» жагсаалтад (ерөнхий ба
         аль ч зогсоолын — нэгтгэсэн, сонголттой);
       • POS аппаар (provider POS, kind PARKING, PAID) нийт ≥20 КАРТ авсан түүхтэй.
     Зогсоолоос ҮЛ хамаарна. Прод (audit1014): Building, pos.3baruun, pos.3zuun,
     pos.bumbat, pos.khangarid, pos.oros, pos.sport, pos.ylalt, pos_100emneleg — 9-үүлээ
     101–2,261 карттай; веб касс (sogii, bamanna, nominerdene) POS картгүй, online
     «operator» 6 (босгоос доош), testpos 1.
     ЭРСДЭЛ: POS аппын хэрэглэгч ВЕБ кассаар (толгойгүй) бэлэн авбал терминал хэвлэсэн
     гэж үзнэ — AuditLog CASH_PAYMENT.pos_app_via-аар хянана.
"""
from __future__ import annotations

import re
import time

POS_APP_MIN_CARD = 20                     # POS аппаар ≥20 карт авсан хэрэглэгч = POS апп
POS_APP_ROLE = "POS"
POS_APP_METHODS = ("CARD", "CASH")        # терминал (Ontime) баримт хэвлэдэг аргууд

_CACHE_SEC = 300.0
_card_cache: dict[str, tuple[float, int]] = {}


def is_pos_app_payment(payment) -> bool:
    """1–2: төлбөр өөрөө POS аппынх гэж ТЭМДЭГЛЭГДСЭН үү (source/provider) — баримт
    гаргах шийдвэр (financial_jobs) үүгээр; fallback хэрэглэгчийг _mark_pos_app төлбөр
    үүсэх үед source=POSAPP болгож тэмдэглэдэг."""
    if getattr(payment, "source", None) == "POSAPP":
        return True
    return (getattr(payment, "provider", None) == "POS"
            and (getattr(payment, "kind", None) or "PARKING") == "PARKING")


def pos_app_via(payment, username=None, users=()) -> str | None:
    """Энэ төлбөр POS аппынх уу — яаж танив (CLI/тайлан). users: POS аппын хэрэглэгчид
    (жижиг үсгээр, `known_users()["users"]`). None = POS аппынх биш."""
    if getattr(payment, "source", None) == "POSAPP":
        return "source=POSAPP"
    if (getattr(payment, "provider", None) == "POS"
            and (getattr(payment, "kind", None) or "PARKING") == "PARKING"):
        return "provider=POS"
    if username and username.lower() in (users or ()):
        return f"user={username.lower()}"
    return None


def split_users(raw) -> set[str]:
    return {u.strip().lower() for u in re.split(r"[,;\s]+", str(raw or "")) if u.strip()}


def card_counts(db, *, user_ids=None, min_card: int = 1) -> dict[str, int]:
    """{username (жижиг): POS аппын картын тоо} — provider POS, kind PARKING, PAID."""
    from sqlalchemy import func

    from ..models import Payment, User
    q = (db.query(User.username, func.count(Payment.id))
         .join(Payment, Payment.cashier_id == User.id)
         .filter(Payment.status == "PAID", Payment.provider == "POS",
                 Payment.payment_method == "CARD",
                 func.coalesce(Payment.kind, "PARKING") == "PARKING"))
    if user_ids is not None:
        q = q.filter(User.id.in_(list(user_ids)))
    out = {}
    for uname, n in q.group_by(User.username).all():
        if uname and n >= max(1, min_card):
            out[uname.lower()] = int(n)
    return out


def known_users(db, *, min_card: int = POS_APP_MIN_CARD, override=None) -> dict:
    """POS аппын хэрэглэгчид — систем даяар (зогсоолоор БИШ). CLI-үүд хэвлэнэ.

    override (--pos-user): өгвөл ЗӨВХӨН тэд (бусдыг дарна).
    Буцаах {"users", "source", "roles", "configured", "derived", "below"}."""
    from ..models import User
    from .app_settings import pos_app_users
    roles = {u.lower() for (u,) in db.query(User.username).filter(User.role == POS_APP_ROLE).all() if u}
    configured = pos_app_users(db)
    allc = card_counts(db, min_card=1)
    derived = {u: n for u, n in allc.items() if n >= max(1, min_card)}
    if override:
        users, src = {u.lower() for u in override}, "--pos-user"
    else:
        users, src = roles | configured | set(derived), f"role POS + тохиргоо + ≥{min_card} POS карт"
    below = {u: n for u, n in allc.items() if u not in users}
    return {"users": users, "source": src, "roles": roles, "configured": configured,
            "derived": derived, "below": below}


def _card_history(db, user_id: str) -> int:
    hit = _card_cache.get(user_id)
    if hit and time.monotonic() - hit[0] < _CACHE_SEC:
        return hit[1]
    n = next(iter(card_counts(db, user_ids=[user_id]).values()), 0)
    _card_cache[user_id] = (time.monotonic(), n)
    return n


def user_pos_app_via(db, user) -> str | None:
    """3 (FALLBACK): толгойгүй хүсэлтийн кассчин POS аппын хэрэглэгч үү — "role" |
    "user_list" | "card_history" | None. Картын түүхийг 5 мин кэшлэнэ (халуун зам биш,
    толгойгүй бэлэн/өрийн төлөлт бүрд нэг COUNT). Алдаа гарвал None — систем баримт
    гаргана (баримтгүй үлдэхээс давхардал дээр)."""
    if user is None:
        return None
    if (getattr(user, "role", None) or "").upper() == POS_APP_ROLE:
        return "role"
    name = (getattr(user, "username", None) or "").lower()
    try:
        from .app_settings import pos_app_users
        if name and name in pos_app_users(db):
            return "user_list"
        uid = getattr(user, "id", None)
        if uid and _card_history(db, uid) >= POS_APP_MIN_CARD:
            return "card_history"
    except Exception:  # noqa: BLE001
        import logging
        logging.getLogger("parking.pos_app").exception("POS аппын хэрэглэгч шалгаж чадсангүй")
    return None


def invalidate_cache():
    _card_cache.clear()
