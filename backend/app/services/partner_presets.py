"""Түншийн тохиргоог deploy-оор ТАВИХ (серверийн DB-д SSH-гүй хүрэх нэг удаагийн алхам).

Одоогийн preset-үүд: (1) MonPay-ийн autoPay webhook — доор; (2) Easy Wallet-ийн лавлах хүсэлтийн
хязгаар 120/мин (`apply_easywallet_rate_limit`, файлын төгсгөлд).

MonPay (Мобифинанс, түлхүүрийн нэр `mobifinance`) 2026-11-11-нд autoPay-ийн
(«auto-parking») webhook URL-аа ирүүлсэн:

    TEST : https://z-wallet.monpay.mn/auto-parking/parking/webhooks/easyparking
    PROD : https://wallet.monpay.mn/auto-parking/parking/webhooks/easyparking

Энэ нь түлхүүрийн ТӨЛБӨРИЙН webhook (`partner_keys.webhook_url`) — `payment.requested`
(autoPay, docs/INTEGRATION_API.md §9), `payment.paid`, `receipt.ready`, `reservation.*` энд
очно. Машины орох/гарах мэдэгдлийн URL (`vehicle_webhook_url`) хөндөгдөхгүй.

Backend асах бүрд (deploy-ийн restart) ажиллана, ХЭЗЭЭ Ч startup-ийг унагахгүй:

  1. Серверийн үүрэг: /etc/parking/deploy.env (эсвэл орчны) `PARKING_DEPLOY_ROLE` ба
     `PARKING_PUBLIC_BASE_URL`-ийн хост. Дохио зөрвөл / олдохгүй бол ЮУ Ч ХИЙХГҮЙ
     (CI, хөгжүүлэгчийн машин, продын хуулбар DB).
  2. Хоосон бол → тухайн орчны URL-ыг НЭГ удаа бичнэ (app_settings-ийн тэмдэглэгээ) —
     админ дараа нь зориуд хоосолбол дахин бичихгүй.
  3. Өөр орчны MonPay хост тавигдсан бол (продын dump-аас сэргээсэн TEST дээр PROD-ын
     URL г.м) → ҮРГЭЛЖ засна: TEST-ийн туршилтын төлбөр MonPay-ийн бодит сервер рүү,
     эсвэл продын бодит төлбөр MonPay-ийн тест рүү очих ёсгүй.
  4. MonPay-ийн өөр (хуучин) зам байвал → нэг удаа шинээр солино. MonPay-ээс өөр хост
     (админы шийдвэр) бол хөндөхгүй, лог үлдээнэ.

Өөрчлөлт бүр AuditLog `PARTNER_WEBHOOK_SET` (username `system:deploy`) — Тохиргоо → Лог-д
харагдана. Шалгах: Тохиргоо → Холболт → Гадаад API → mobifinance → «Турших».
"""
from __future__ import annotations

import logging
import os
from urllib.parse import urlparse

log = logging.getLogger("parking.partner_presets")

DEPLOY_ENV_FILE = "/etc/parking/deploy.env"
MONPAY_KEY_NAMES = ("mobifinance", "monpay")
MONPAY_PATH = "/auto-parking/parking/webhooks/easyparking"
MONPAY_HOSTS = {"staging": "z-wallet.monpay.mn", "production": "wallet.monpay.mn"}
# Продын хост (PARKING_PUBLIC_BASE_URL) — TEST нь `test.` угтвартай
PROD_HOSTS = ("site.easy-parking.mn", "172.16.100.21")
STATE_KEY = "preset_monpay_webhook_20261111"


def monpay_url(role: str) -> str:
    return f"https://{MONPAY_HOSTS[role]}{MONPAY_PATH}"


def _role_from_file(path: str = DEPLOY_ENV_FILE) -> str | None:
    """deploy.env-ийн `PARKING_DEPLOY_ROLE=staging|production` (remote_deploy.sh-тэй ижил)."""
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return None
    role = None
    for line in lines:
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k.strip() == "PARKING_DEPLOY_ROLE":
            role = v.strip().strip("'\"").lower() or None
    return role


def server_role(public_base_url: str | None = None, *, env: dict | None = None,
                deploy_env_file: str = DEPLOY_ENV_FILE) -> tuple[str | None, list[str]]:
    """(staging|production|None, дохионууд). Бүх ИЛ дохио нэг утгатай байх ёстой —
    зөрвөл None (буруу орчинд мөнгөний webhook тавихаас хэзээ ч зайлсхийнэ)."""
    from ..config import settings
    env = os.environ if env is None else env
    signals: list[tuple[str, str]] = []
    file_role = _role_from_file(deploy_env_file)
    if file_role:
        signals.append(("deploy.env", file_role))
    env_role = (env.get("PARKING_DEPLOY_ROLE") or "").strip().lower()
    if env_role:
        signals.append(("env", env_role))
    base = settings.public_base_url if public_base_url is None else public_base_url
    host = (urlparse(base or "").hostname or "").lower()
    if host.startswith("test."):
        signals.append(("public_base_url", "staging"))
    elif host in PROD_HOSTS:
        signals.append(("public_base_url", "production"))
    described = [f"{src}={val}" for src, val in signals]
    roles = {val for _src, val in signals}
    if len(roles) != 1:
        return None, described
    role = roles.pop()
    return (role if role in MONPAY_HOSTS else None), described


def plan_monpay_webhook(current: str | None, role: str, already_done: bool) -> tuple[str | None, str]:
    """Шинэ URL (эсвэл None = хөндөхгүй) ба шалтгаан. Цэвэр функц — тестэд."""
    target = monpay_url(role)
    cur = (current or "").strip()
    if cur == target:
        return None, "аль хэдийн зөв"
    host = (urlparse(cur).hostname or "").lower() if cur else ""
    if not cur:
        return (None, "админ хоосолсон — хөндөхгүй") if already_done else (target, "хоосон байсан")
    if host in MONPAY_HOSTS.values() and host != MONPAY_HOSTS[role]:
        # Орчин холилдсон (продын dump-аас сэргээсэн TEST г.м) — тэмдэглэгээнээс үл хамааран засна
        return target, f"ӨӨР орчны MonPay хост ({host}) — засав"
    if host == "monpay.mn" or host.endswith(".monpay.mn"):
        if not already_done:
            return target, f"MonPay-ийн хуучин хаяг ({host}) — шинэчлэв"
        return None, f"MonPay-ийн өөр хаяг ({host}, админ тавьсан) — хөндөхгүй"
    return None, f"MonPay-ээс өөр URL ({host or cur[:40]}) — админы шийдвэр, хөндөхгүй"


def apply_monpay_webhook(db, role: str | None = None) -> list[dict]:
    """MonPay түлхүүр(үүд)-ийн төлбөрийн webhook-ийг орчинд нь тааруулна. Commit хийнэ."""
    from ..models import AuditLog, PartnerKey
    from .app_settings import get_state, set_state
    signals: list[str] = []
    if role is None:
        role, signals = server_role()
    if role is None:
        log.info("MonPay webhook preset: серверийн үүрэг тодорхойгүй (%s) — алгасав",
                 ", ".join(signals) or "дохиогүй")
        return []
    state = get_state(db, STATE_KEY)
    done = bool(state.get("done"))
    keys = (db.query(PartnerKey)
            .filter(PartnerKey.name.in_(MONPAY_KEY_NAMES), PartnerKey.is_active.is_(True))
            .order_by(PartnerKey.created_at).all())
    if not keys:
        # PROD дээр түлхүүр өөр нэртэй байж болно (2026-11-11: `mobifinance`/`monpay` нэртэй
        # идэвхтэй түлхүүр алга) — нэрэнд monpay/mobifinance агуулсан ЯГ НЭГ түлхүүр байвал түүнд.
        # Олон бол аль нь болохыг таахгүй — лог үлдээгээд UI-аас гараар.
        cand = [k for k in (db.query(PartnerKey).filter(PartnerKey.is_active.is_(True))
                            .order_by(PartnerKey.created_at).all())
                if any(x in (k.name or "").lower() for x in ("monpay", "mobifinance"))]
        if len(cand) == 1:
            keys = cand
        elif cand:
            log.warning("MonPay webhook preset: MonPay-тэй төстэй %d түлхүүр (%s) — аль нь болохыг "
                        "таахгүй, UI-аас тавина уу", len(cand), ", ".join(k.name for k in cand))
        else:
            log.info("MonPay webhook preset: MonPay-ийн идэвхтэй түлхүүр алга — түлхүүр үүсгэсний "
                     "дараах restart-д тавигдана")
    changes = []
    for k in keys:
        new, why = plan_monpay_webhook(k.webhook_url, role, done)
        if new is None:
            log.info("MonPay webhook preset: %s (%s) — %s", k.name, k.key_prefix, why)
            continue
        old = k.webhook_url
        k.webhook_url = new
        db.add(AuditLog(username="system:deploy", action="PARTNER_WEBHOOK_SET",
                        entity="partner_key", entity_id=k.id,
                        detail={"name": k.name, "url": new, "old": old, "role": role,
                                "reason": why, "source": "MonPay хүсэлт 2026-11-11 (deploy)"}))
        changes.append({"key_id": k.id, "name": k.name, "old": old, "new": new, "reason": why})
        log.warning("MonPay webhook preset: %s (%s) %s → %s (%s)", k.name, k.key_prefix,
                    old or "—", new, why)
    if keys and not done:
        set_state(db, STATE_KEY, {"done": True, "role": role, "url": monpay_url(role),
                                  "keys": [k.id for k in keys]}, "system:deploy")
    db.commit()
    return changes


# ── Easy Wallet-ийн лавлах хүсэлтийн хязгаар (2026-11-11) ──────────────────────────────
# PROD: `easywallet` түлхүүр ~190 дугаарыг 24с тутам GET /api/v1/sessions-оор асууж байв (секундэд ~8,
# өдөрт ~700 мянга — «Apache-HttpClient/5.5 (Java/21)»). Эзэмшигчийн шийдвэрээр түлхүүр тутмын
# хязгаар: минутад 120, ЗАМ ТУС БҮРД (sessions / recent-exits / sites тусдаа) — төлбөрт хамаарахгүй.
# НЭГ удаа тавина; админ дараа нь өөрчилбөл/хасвал (Тохиргоо → Холболт → Гадаад API) хөндөхгүй.
EASYWALLET_KEY_NAME = "easywallet"
EASYWALLET_RATE_PER_MIN = 120
RATE_STATE_KEY = "preset_easywallet_rate_20261111"


def apply_easywallet_rate_limit(db) -> list[dict]:
    """`easywallet` түлхүүрт лавлах замын хязгаарыг нэг удаа тавина. Commit хийнэ."""
    from ..models import AuditLog, PartnerKey
    from .app_settings import get_state, set_state
    if get_state(db, RATE_STATE_KEY).get("done"):
        return []
    keys = (db.query(PartnerKey)
            .filter(PartnerKey.name == EASYWALLET_KEY_NAME, PartnerKey.is_active.is_(True))
            .order_by(PartnerKey.created_at).all())
    if not keys:
        log.info("Easy Wallet хязгаар preset: идэвхтэй «%s» түлхүүр алга — алгасав", EASYWALLET_KEY_NAME)
        return []
    changes = []
    for k in keys:
        if k.rate_limit_per_min is not None:
            log.info("Easy Wallet хязгаар preset: %s (%s) — аль хэдийн %s/мин, хөндөхгүй",
                     k.name, k.key_prefix, k.rate_limit_per_min)
            continue
        k.rate_limit_per_min = EASYWALLET_RATE_PER_MIN
        db.add(AuditLog(username="system:deploy", action="PARTNER_RATE_LIMIT_SET",
                        entity="partner_key", entity_id=k.id,
                        detail={"name": k.name, "old": None, "new": EASYWALLET_RATE_PER_MIN,
                                "reason": "24с тутам ~190 дугаар /sessions-оор асуудаг (секундэд ~8)",
                                "source": "эзэмшигчийн хүсэлт 2026-11-11 (deploy)"}))
        changes.append({"key_id": k.id, "name": k.name, "new": EASYWALLET_RATE_PER_MIN})
        log.warning("Easy Wallet хязгаар preset: %s (%s) → лавлах зам тус бүрд минутад %d",
                    k.name, k.key_prefix, EASYWALLET_RATE_PER_MIN)
    set_state(db, RATE_STATE_KEY, {"done": True, "per_min": EASYWALLET_RATE_PER_MIN,
                                   "keys": [k.id for k in keys]}, "system:deploy")
    db.commit()
    return changes


def run_startup_presets(session_factory=None) -> None:
    """main.py-аас startup-д. Алдаа гарвал лог л үлдээнэ — серверийг хэзээ ч унагахгүй.
    Preset бүр тусдаа — нэг нь унасан ч нөгөө нь ажиллана."""
    if session_factory is None:
        from ..database import SessionLocal as session_factory
    try:
        db = session_factory()
    except Exception as e:  # noqa: BLE001
        log.error("partner presets: DB холболт алдаа: %r", e)
        return
    try:
        for name, fn in (("MonPay webhook", apply_monpay_webhook),
                         ("Easy Wallet хязгаар", apply_easywallet_rate_limit)):
            try:
                fn(db)
            except Exception as e:  # noqa: BLE001
                db.rollback()
                log.error("partner presets: %s алдаа: %r", name, e)
    finally:
        db.close()
