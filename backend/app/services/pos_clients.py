"""«Холбогдсон POS» — PAX POS аппын бүртгэл, heartbeat, админы хураангуй (2026-10-15).

Эзний хүсэлт: POS аппаас pos_version, user_id, terminal_id, status-ыг авч хадгалж, Тохиргоо →
«Холбогдсон POS» табд харуулах, POS-той холбоотой тохиргоог нэг дор нэгтгэх.
Яагаад одоо: «100айл барилгачин» (pos.bumbat) хуучин апп build-ээр Ontime e-Barimt-ыг өөрөө
хэвлэж давхардал үүсгэсээр — хэн ямар хувилбартай, ямар төлөвтэй вэ гэдгийг харах газар байгаагүй.
Прод (audit1014): pax_terminal төрлөөр бүртгэлтэй терминал 0 → `_remember_app_version`
(Device.extra) хэзээ ч хадгалагддаггүй байв; terminal_id 'TDB-PAX-SITE01-01' (зааврын жишээ)
10-р сард 8 хэрэглэгч/7 зогсоолоос ирсэн тул мөрийн түлхүүр = (user_id, terminal_id).
«100айл барилгачин» = 100BARILGA, pos.bumbat (role POS, X-App-Version-гүй карт 91186920) —
толгойгүй ч role POS-оор танигдана.

ХАЛУУН ЗАМ (PosCaptureMiddleware): хариу илгээгдсэний ДАРАА санах ойн нэгтгэлд (dict + lock)
л нэмнэ — DB, сүлжээ, нэмэлт JWT decode ҮГҮЙ (хэрэглэгчийг auth.get_current_user
`request.state.auth_user`-д тавьдаг). Бичилтийг flush_loop (5с тутам, тусдаа thread) хийнэ:
нэг мөрөнд хэвийн үед ≤1 upsert / 60с; зөвхөн шинэ мөр, хувилбар/төлөв солигдвол ≤1 / 10с. Прод ~7 мянган POS хүсэлт/өдөр → терминал бүр идэвхтэй минут тутам ≤1
жижиг upsert. DB унасан үед тоолуур санах ойд хуримтлагдаж дараа бичигдэнэ (алдагдахгүй).

POS аппын хүсэлт гэж: X-App-Version / X-Terminal-Id / X-POS-Status толгойтой, ЭСВЭЛ хөтөч биш
(User-Agent «Mozilla/» биш) бөгөөд role POS / /api/payments/pos/* зам / POS аппын хэрэглэгч
(services/pos_app.known_users — 10 мин тутам). Веб касс (хөтөч, толгойгүй) ОРОХГҮЙ.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time
import uuid
from datetime import datetime, timedelta
from urllib.parse import parse_qs

log = logging.getLogger("parking.pos_clients")

THROTTLE_SEC = 60.0            # нэг мөрөнд хэвийн үед ≤1 бичилт / 60с
URGENT_MIN_SEC = 10.0          # шинэ мөр / хувилбар / төлөв солигдвол — ≤1 / 10с
FLUSH_TICK_SEC = 5.0
KNOWN_USERS_REFRESH_SEC = 600.0
ONLINE_SEC = 300               # «онлайн» = сүүлийн 5 мин дотор
STALE_HOURS = 24               # «хуучирсан» = 24 цагаас дээш сураггүй
INFER_TTL_SEC = 12 * 3600      # terminal_id-гүй хүсэлтийг (хэрэглэгч, IP)-ийн сүүлийн терминалд
MAX_ENTRIES = 1000             # санах ойн дээд хязгаар (мөрийн тоо)
MAX_INFER = 5000               # (хэрэглэгч, IP) → терминал зураглалын дээд хязгаар
MAX_TERMINALS_PER_USER = 8     # нэг хэрэглэгч санамсаргүй terminal_id-аар хүснэгт дүүргэхгүй
BODY_PEEK_MAX = 8192           # /api/payments/pos/* POST биеэс terminal_id унших дээд хэмжээ
EXTRA_MAX_BYTES = 1024
# Зааврын (PAX_POS_APP_GUIDE §4) ЖИШЭЭ — апп бүр хуулж тавьсан тул бодит терминалыг ялгахгүй
EXAMPLE_TERMINAL_IDS = frozenset({"TDB-PAX-SITE01-01"})
NO_SITE = "00000000-0000-0000-0000-000000000000"   # auth.operator_sites-ийн «юу ч үгүй» тэмдэг

HEARTBEAT_PATH = "/api/payments/pos/heartbeat"
POS_PATH_PREFIX = "/api/payments/pos/"
TERMINAL_ROUTE = "/api/payments/pos/terminal/{terminal_id}"
# Амжилттай (2xx) бол «төлбөр авсан» гэж тэмдэглэх маршрутууд (POS аппын карт/бэлэн/өр)
PAYMENT_ROUTES = frozenset({"/api/payments/pos/confirm", "/api/payments/cash",
                            "/api/compensations/{comp_id}/pay"})

_TERMINAL_RE = re.compile(r"[A-Za-z0-9._:\-]{1,60}")
_UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]+")
_WS_RE = re.compile(r"\s+")


# ─────────────────────────── Цэвэрлэгээ / хувилбар ───────────────────────────
def clean_terminal_id(v) -> str | None:
    """terminal_id: 1–60 тэмдэгт [A-Za-z0-9._:-]. Бусад нь None (хадгалахгүй)."""
    if v is None or isinstance(v, (dict, list, bool)):
        return None
    s = str(v).strip()
    return s if _TERMINAL_RE.fullmatch(s) else None


def clean_text(v, limit: int = 40) -> str | None:
    """Чөлөөт мөр: удирдлагын тэмдэгтгүй, нэг зай, ≤limit. Хоосон → None."""
    if v is None or isinstance(v, (dict, list)):
        return None
    s = _WS_RE.sub(" ", _CTRL_RE.sub(" ", str(v))).strip()
    return s[:limit] or None


def clean_site_id(v) -> str | None:
    s = str(v or "").strip()
    return s.lower() if _UUID_RE.fullmatch(s) else None


def version_key(v) -> tuple | None:
    """'1.5.3' / 'v1.5.3+12' / '1.6.0-beta' → (1, 5, 3[, 0]). Тоогүй бол None."""
    if not v:
        return None
    nums = re.findall(r"\d+", str(v).split("+")[0])[:4]
    if not nums:
        return None
    return tuple(int(n) for n in nums) + (0,) * (4 - len(nums))


def version_lt(a, b) -> bool:
    """a < b уу (хоёул задлагдах үед л). Задлагдахгүй бол False — дур мэдэн тодруулахгүй."""
    ka, kb = version_key(a), version_key(b)
    return bool(ka and kb and ka < kb)


def min_app_version(db) -> str:
    from .app_settings import POSAPP_KEY, get_rules
    try:
        return str(get_rules(db, POSAPP_KEY).get("min_app_version") or "").strip()
    except Exception:  # noqa: BLE001 — тохиргоо уншигдахгүй бол «тавиагүй»
        return ""


# ─────────────────────────── Санах ойн нэгтгэл ───────────────────────────
class _Entry:
    __slots__ = ("user_id", "username", "terminal_id", "terminal_source", "requests", "errors",
                 "payments", "heartbeats", "first_seen", "last_seen", "version", "status",
                 "status_at", "hb", "ip", "ua", "endpoint", "status_code", "last_payment",
                 "site_hint", "dirty", "urgent", "flushed", "touched", "known_version",
                 "known_status")

    def __init__(self, user_id: str, username: str, terminal_id: str):
        self.user_id, self.username, self.terminal_id = user_id, username, terminal_id
        self.terminal_source = None
        self.requests = self.errors = self.payments = self.heartbeats = 0
        self.first_seen = self.last_seen = None
        self.version = self.status = self.status_at = self.hb = None
        self.ip = self.ua = self.endpoint = self.status_code = None
        self.last_payment = self.site_hint = None
        self.dirty = self.urgent = False
        self.flushed = 0.0
        self.touched = time.monotonic()
        self.known_version = self.known_status = None

    def take(self, now_m: float) -> dict:
        """Бичих агшин зураг; тоолуурыг тэглэнэ (бичилт унавал restore буцааж нэмнэ)."""
        snap = {k: getattr(self, k) for k in (
            "user_id", "username", "terminal_id", "terminal_source", "requests", "errors",
            "payments", "heartbeats", "first_seen", "last_seen", "version", "status",
            "status_at", "hb", "ip", "ua", "endpoint", "status_code", "last_payment", "site_hint")}
        self.requests = self.errors = self.payments = self.heartbeats = 0
        self.status = self.status_at = self.hb = self.last_payment = None
        self.dirty = self.urgent = False
        self.flushed = now_m
        return snap


_lock = threading.Lock()
_flush_lock = threading.Lock()
_pending: dict[tuple[str, str], _Entry] = {}
_last_terminal: dict[tuple[str, str], tuple[str, float]] = {}   # (user, ip) → (terminal, mono)
_known_users: frozenset = frozenset()
_known_at = 0.0
_warned: dict[str, float] = {}


def _warn_once(kind: str, msg: str, *args, every: float = 600.0):
    now = time.monotonic()
    if now - _warned.get(kind, -every) >= every:
        _warned[kind] = now
        log.warning(msg, *args)


def _is_connection_error(e: BaseException) -> bool:
    """DB холболт тасарсан эсэх — тийм бол бүх мөрийг дараа дахин; үгүй бол зөвхөн тухайн мөр буруу."""
    from sqlalchemy.exc import InterfaceError, OperationalError
    return isinstance(e, (OperationalError, InterfaceError)) or bool(
        getattr(e, "connection_invalidated", False))


def reset_state():
    """Тест/оношилгоонд — санах ойн бүх төлөвийг цэвэрлэнэ."""
    global _known_users, _known_at
    with _lock:
        _pending.clear()
        _last_terminal.clear()
        _known_users, _known_at = frozenset(), 0.0
        _warned.clear()


def set_known_users(users) -> None:
    global _known_users, _known_at
    _known_users = frozenset(u.lower() for u in users or () if u)
    _known_at = time.monotonic()


def is_pos_request(role: str | None, username: str | None, path: str, *, has_pos_header: bool,
                   browser: bool) -> bool:
    if has_pos_header:
        return True
    if browser:
        return False     # веб касс (хөтөч, толгойгүй) — «холбогдсон POS» биш
    return ((role or "").upper() == "POS" or path.startswith(POS_PATH_PREFIX)
            or (username or "").lower() in _known_users)


def record(*, user_id: str, username: str, terminal_id: str | None, terminal_source: str | None,
           version: str | None, status: str | None, ip: str | None, ua: str | None,
           endpoint: str | None, status_code: int, payment: bool = False, site_hint: str | None = None,
           hb: dict | None = None, now: datetime | None = None) -> tuple[str, str] | None:
    """Нэг хүсэлтийн ажиглалтыг санах ойд нэгтгэнэ (DB-гүй, микросекунд). Буцаах: түлхүүр."""
    now = now or datetime.utcnow()
    mono = time.monotonic()
    ip = (ip or "")[:64] or None
    with _lock:
        if terminal_id:
            if ip:
                if len(_last_terminal) >= MAX_INFER and (user_id, ip) not in _last_terminal:
                    _last_terminal.clear()       # хэвийн бус — санах ойг хамгаална (зөвхөн таамаг)
                _last_terminal[(user_id, ip)] = (terminal_id, mono)
        elif ip:
            hit = _last_terminal.get((user_id, ip))
            if hit and mono - hit[1] < INFER_TTL_SEC:
                terminal_id, terminal_source = hit[0], "inferred"
        terminal_id = terminal_id or ""
        key = (user_id, terminal_id)
        e = _pending.get(key)
        if e is None:
            if terminal_id and sum(1 for k in _pending if k[0] == user_id) >= MAX_TERMINALS_PER_USER:
                _warn_once("per_user", "pos_clients: %s хэт олон terminal_id (>%d) — шинэ ID-г "
                           "тодорхойгүй мөрөнд нэгтгэв", username, MAX_TERMINALS_PER_USER)
                key, terminal_id, terminal_source = (user_id, ""), "", None
                e = _pending.get(key)
        if e is None:
            if len(_pending) >= MAX_ENTRIES:
                _evict(mono)
            if len(_pending) >= MAX_ENTRIES:
                _warn_once("full", "pos_clients: санах ойн хязгаар (%d) дүүрэв — ажиглалт алгасав",
                           MAX_ENTRIES)
                return None
            e = _pending[key] = _Entry(user_id, username, terminal_id)
            e.urgent = True                      # шинэ мөр — ≤10с дотор харагдана
        e.username = username or e.username
        if terminal_source and (terminal_source != "inferred" or not e.terminal_source):
            e.terminal_source = terminal_source
        e.requests += 1
        if status_code >= 400:
            e.errors += 1
        if e.first_seen is None:
            e.first_seen = now
        e.last_seen = now
        e.touched = mono
        if ip:
            e.ip = ip
        if ua:
            e.ua = ua[:120]
        if endpoint:
            e.endpoint = endpoint[:120]
        e.status_code = status_code
        if version:
            if version != e.known_version:
                if e.known_version is not None:
                    log.info("POS апп хувилбар: %s/%s %s → %s", username, terminal_id or "?",
                             e.known_version, version)
                e.urgent = True
                e.known_version = version
            e.version = version
        if status:
            if status != e.known_status:
                e.urgent = True
                e.known_status = status
            e.status, e.status_at = status, now
        if hb is not None:
            e.heartbeats += 1
            e.hb = hb
        if payment:
            e.payments += 1
            e.last_payment = now
        if site_hint:
            e.site_hint = site_hint
        e.dirty = True
        return key


def _evict(mono: float):
    """Бичигдсэн (dirty биш), 1 цагаас дээш хөдөлгөөнгүй мөрүүдийг санах ойгоос хасна."""
    for k in [k for k, e in _pending.items() if not e.dirty and mono - e.touched > 3600]:
        _pending.pop(k, None)


def _restore(snap: dict):
    """Бичилт унасан — тоолуурыг буцааж нэмж дараагийн удаа дахин оролдоно."""
    with _lock:
        e = _pending.get((snap["user_id"], snap["terminal_id"]))
        if e is None:
            return
        e.requests += snap["requests"]
        e.errors += snap["errors"]
        e.payments += snap["payments"]
        e.heartbeats += snap["heartbeats"]
        for k in ("status", "status_at", "hb", "last_payment"):
            if getattr(e, k) is None and snap[k] is not None:
                setattr(e, k, snap[k])
        e.dirty = True


def due_count(force: bool = False) -> int:
    mono = time.monotonic()
    with _lock:
        return sum(1 for e in _pending.values() if e.dirty and (
            force or mono - e.flushed >= THROTTLE_SEC
            or (e.urgent and mono - e.flushed >= URGENT_MIN_SEC)))


def flush_due(*, force: bool = False, session_factory=None) -> int:
    """Бичих цаг болсон мөрүүдийг upsert хийнэ (flush_loop-ийн thread дээр). Буцаах: бичсэн тоо."""
    mono = time.monotonic()
    due = []
    with _lock:
        for key, e in list(_pending.items()):
            if not e.dirty:
                if mono - e.touched > 24 * 3600:
                    _pending.pop(key, None)
                continue
            gap = mono - e.flushed
            if force or gap >= THROTTLE_SEC or (e.urgent and gap >= URGENT_MIN_SEC):
                due.append(e.take(mono))
        for k in [k for k, (_t, m) in _last_terminal.items() if mono - m > INFER_TTL_SEC]:
            _last_terminal.pop(k, None)
    if not due:
        return 0
    if session_factory is None:
        from ..database import SessionLocal as session_factory
    written = 0
    with _flush_lock:
        db = session_factory()
        try:
            for i, snap in enumerate(due):
                try:
                    if _write(db, snap):
                        written += 1
                    db.commit()
                except Exception as e:  # noqa: BLE001 — холболт унавал тоолуур алдагдахгүй
                    try:
                        db.rollback()
                    except Exception:  # noqa: BLE001
                        pass
                    if _is_connection_error(e):
                        # DB унасан бол үлдсэн мөр бүрд connect_timeout хүлээхгүй — бүгдийг буцаана
                        for rest in due[i:]:
                            _restore(rest)
                        _warn_once("write", "pos_clients: бичилт амжилтгүй — дараа дахин оролдоно",
                                   every=300.0)
                        log.debug("pos_clients write failed", exc_info=True)
                        break
                    # Өгөгдлийн алдаа (ж: NUL тэмдэгт) — ЭНЭ мөрийг л хаяна; дараагийн мөрүүдийг
                    # мөнхөд гацаахгүй (2026-10-15 хяналт F1)
                    _warn_once("bad_row", "pos_clients: буруу мөрийг алгасав: %r", e, every=300.0)
                    log.debug("pos_clients bad row dropped", exc_info=True)
                    continue
        finally:
            db.close()
    return written


def _infer_terminal_db(db, user_id: str, ip: str | None) -> str | None:
    """Процесс дахин асах үед санах ой хоосон — terminal_id-гүй хүсэлтийг тэр хэрэглэгчийн ИЖИЛ
    IP-ээс сүүлийн 12 цагт харагдсан терминалд ононо (тодорхойгүй мөр үүсгэхгүйн тулд)."""
    if not ip:
        return None
    from ..models import PosClient
    row = (db.query(PosClient.terminal_id)
           .filter(PosClient.user_id == user_id, PosClient.terminal_id != "",
                   PosClient.last_ip == ip,
                   PosClient.last_seen_at >= datetime.utcnow() - timedelta(seconds=INFER_TTL_SEC))
           .order_by(PosClient.last_seen_at.desc()).first())
    return row[0] if row else None


def _resolve_site(db, terminal_id: str, hint: str | None, allowed: list | None) -> str | None:
    """Зогсоол: бүртгэлтэй терминал → хүсэлтийн site_id (хэрэглэгчийн хүрээнд) → ганц зогсоол."""
    from ..models import Device, ParkingSite
    ok = (lambda sid: sid and (allowed is None or sid in allowed))
    if terminal_id:
        dev = (db.query(Device.site_id)
               .filter(Device.device_key == terminal_id,
                       Device.device_type.in_(["pax_terminal", "pos"]),
                       Device.status == "active").first())
        if dev and ok(dev[0]):
            return dev[0]
    if hint and ok(hint) and (allowed is not None or db.get(ParkingSite, hint) is not None):
        return hint
    real = [s for s in (allowed or []) if s != NO_SITE]
    return real[0] if len(real) == 1 else None


def _write(db, snap: dict) -> bool:
    from sqlalchemy import and_, case

    from ..auth import operator_sites
    from ..models import PosClient, User
    user = db.get(User, snap["user_id"])
    if user is None:
        return False
    allowed = operator_sites(user)
    terminal_id, source = snap["terminal_id"], snap["terminal_source"]
    if not terminal_id:
        inferred = _infer_terminal_db(db, snap["user_id"], snap["ip"])
        if inferred:
            terminal_id, source = inferred, "inferred"
            with _lock:
                _last_terminal[(snap["user_id"], snap["ip"])] = (inferred, time.monotonic())
    site_id = _resolve_site(db, terminal_id, snap["site_hint"], allowed)
    now = datetime.utcnow()
    hb = snap["hb"] or {}
    values = {
        "id": str(uuid.uuid4()), "user_id": snap["user_id"],
        "username": (snap["username"] or user.username or "")[:60],
        "terminal_id": terminal_id, "terminal_source": source, "site_id": site_id,
        "site_ids": [s for s in allowed if s != NO_SITE] if allowed is not None else None,
        "pos_version": snap["version"], "status": snap["status"], "status_at": snap["status_at"],
        "printer": hb.get("printer"), "battery": hb.get("battery"), "network": hb.get("network"),
        "extra": hb.get("extra"), "last_ip": snap["ip"], "user_agent": snap["ua"],
        "last_endpoint": snap["endpoint"], "last_status_code": snap["status_code"],
        "first_seen_at": snap["first_seen"] or now, "last_seen_at": snap["last_seen"] or now,
        "last_payment_at": snap["last_payment"],
        "request_count": snap["requests"], "error_count": snap["errors"],
        "payment_count": snap["payments"], "heartbeat_count": snap["heartbeats"],
        "updated_at": now,
    }
    if db.get_bind().dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    T = PosClient.__table__.c
    stmt = insert(PosClient.__table__).values(**values)
    ex = stmt.excluded
    upd = {
        "username": ex.username,
        "site_ids": ex.site_ids,
        "last_seen_at": case((ex.last_seen_at > T.last_seen_at, ex.last_seen_at), else_=T.last_seen_at),
        "request_count": T.request_count + ex.request_count,
        "error_count": T.error_count + ex.error_count,
        "payment_count": T.payment_count + ex.payment_count,
        "heartbeat_count": T.heartbeat_count + ex.heartbeat_count,
        "updated_at": ex.updated_at,
    }
    if snap["ip"]:
        upd["last_ip"] = ex.last_ip
    for k in ("user_agent", "last_endpoint", "last_status_code"):
        if values[k] is not None:
            upd[k] = getattr(ex, k)
    if source:
        upd["terminal_source"] = ex.terminal_source
    if site_id:
        upd["site_id"] = ex.site_id
    if snap["version"]:
        changed = and_(T.pos_version.isnot(None), T.pos_version != ex.pos_version)
        upd["pos_version"] = ex.pos_version
        upd["prev_pos_version"] = case((changed, T.pos_version), else_=T.prev_pos_version)
        upd["version_changed_at"] = case((changed, ex.last_seen_at), else_=T.version_changed_at)
    if snap["status"]:
        upd["status"], upd["status_at"] = ex.status, ex.status_at
    if snap["hb"] is not None:
        for k in ("printer", "battery", "network", "extra"):
            upd[k] = getattr(ex, k)
    if snap["last_payment"]:
        upd["last_payment_at"] = ex.last_payment_at
    db.execute(stmt.on_conflict_do_update(index_elements=["user_id", "terminal_id"], set_=upd))
    return True


def refresh_known_users(session_factory=None) -> int:
    """POS аппын хэрэглэгчид (role POS + тохиргоо + ≥20 POS карт) — толгойгүй хуучин build-ийг
    танихад (services/pos_app.known_users, 10 мин тутам)."""
    if session_factory is None:
        from ..database import SessionLocal as session_factory
    from .pos_app import known_users
    db = session_factory()
    try:
        users = known_users(db)["users"]
        db.rollback()
    finally:
        db.close()
    set_known_users(users)
    return len(users)


async def flush_loop():
    """main.py-ийн background task: 5с тутам бичих цаг болсныг thread дээр upsert хийнэ."""
    while True:
        await asyncio.sleep(FLUSH_TICK_SEC)
        try:
            if time.monotonic() - _known_at >= KNOWN_USERS_REFRESH_SEC or not _known_at:
                try:
                    await asyncio.to_thread(refresh_known_users)
                except Exception:  # noqa: BLE001 — дараагийн удаа дахин
                    set_known_users(_known_users)   # 10 мин хүлээнэ (DB унасан үед давтахгүй)
                    _warn_once("known", "pos_clients: POS аппын хэрэглэгчдийг уншиж чадсангүй")
            if due_count():
                await asyncio.to_thread(flush_due)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — task үхэхгүй
            _warn_once("loop", "pos_clients: flush_loop алдаа", every=300.0)


# ─────────────────────────── ASGI middleware ───────────────────────────
def _scope_state(scope) -> dict:
    st = scope.get("state")
    if isinstance(st, dict):
        return st
    return getattr(st, "_state", None) or {}


def capture_from_scope(scope, status_code: int, body: bytes | None) -> tuple[str, str] | None:
    """Хариуны ДАРАА: auth-ийн тавьсан хэрэглэгч + толгой/зам/query/биеэс ажиглалт үүсгэнэ."""
    st = _scope_state(scope)
    au = st.get("auth_user")
    if not au or status_code == 401:
        return None
    user_id, username, role = au
    path = scope.get("path") or ""
    hv = ht = hs = ua = None
    for k, v in scope.get("headers") or ():
        if k == b"x-app-version":
            hv = v
        elif k == b"x-terminal-id":
            ht = v
        elif k == b"x-pos-status":
            hs = v
        elif k == b"user-agent":
            ua = v
    ua_s = ua.decode("latin-1") if ua else ""
    hb = st.get("pos_heartbeat")
    if not is_pos_request(role, username, path, has_pos_header=bool(hv or ht or hs or hb),
                          browser="mozilla/" in ua_s.lower()):
        return None
    route = scope.get("route")
    tmpl = getattr(route, "path", None) or path
    tid = src = None
    if hb is not None and status_code < 400:
        tid, src = hb.get("terminal_id"), "heartbeat"
    else:
        hb = None
    if ht and not tid:
        tid = clean_terminal_id(ht.decode("latin-1"))
        src = "header" if tid else None
    if not tid and tmpl == TERMINAL_ROUTE:
        tid = clean_terminal_id((scope.get("path_params") or {}).get("terminal_id"))
        src = "path" if tid else None
    qs = scope.get("query_string") or b""
    site_hint = None
    if b"terminal_id=" in qs or b"site_id=" in qs:
        try:
            q = parse_qs(qs.decode("latin-1"), max_num_fields=30)
        except ValueError:      # хэт олон талбар — query-г үл тооно (ажиглалт хэвээр)
            q = {}
        if not tid and q.get("terminal_id"):
            tid = clean_terminal_id(q["terminal_id"][0])
            src = "query" if tid else None
        if q.get("site_id"):
            site_hint = clean_site_id(q["site_id"][0])
    if not tid and body and len(body) <= BODY_PEEK_MAX:
        try:
            data = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            data = None
        if isinstance(data, dict):
            tid = clean_terminal_id(data.get("terminal_id"))
            src = "body" if tid else None
    version = (hb or {}).get("pos_version") or clean_text(hv.decode("latin-1") if hv else None)
    status = (hb or {}).get("status") or clean_text(hs.decode("latin-1") if hs else None)
    if hb and hb.get("site_id"):
        site_hint = hb["site_id"]
    client = scope.get("client")
    ip = client[0] if client else None
    method = scope.get("method") or ""
    payment = status_code < 300 and method == "POST" and tmpl in PAYMENT_ROUTES
    # Маршрут тааралгүй (404) бол түүхий зам НУЛ/удирдлагын тэмдэгттэй байж болно (psycopg2 NUL-ийг
    # татгалздаг) — загварын нэрийг л, UA-г цэвэрлэж хадгална
    endpoint = clean_text(f"{method} {tmpl if route is not None else '<unmatched>'}", 120)
    return record(user_id=user_id, username=username, terminal_id=tid, terminal_source=src,
                  version=version, status=status, ip=ip, ua=clean_text(ua_s, 120),
                  endpoint=endpoint, status_code=status_code, payment=payment,
                  site_hint=site_hint,
                  hb=({k: hb.get(k) for k in ("printer", "battery", "network", "extra")}
                      if hb is not None else None))


class PosCaptureMiddleware:
    """Pure ASGI: хүсэлтийг ӨӨРЧЛӨХГҮЙ, хариуг саатуулахгүй. /api/payments/pos/* POST-ийн
    биеийг (≤8KB) зөвхөн ХУУЛЖ авна (terminal_id) — доод апп ижил мессежүүдийг авна."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path") or ""
        if scope.get("type") != "http" or not path.startswith("/api/"):
            await self.app(scope, receive, send)
            return
        status = [0]
        peek = None
        if scope.get("method") == "POST" and path.startswith(POS_PATH_PREFIX):
            peek = bytearray()
            upstream = receive

            async def receive():
                msg = await upstream()
                if msg.get("type") == "http.request" and len(peek) <= BODY_PEEK_MAX:
                    peek.extend((msg.get("body") or b"")[:BODY_PEEK_MAX + 1 - len(peek)])
                return msg

        async def send_wrapper(message):
            if message.get("type") == "http.response.start":
                status[0] = int(message.get("status") or 0)
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            try:
                capture_from_scope(scope, status[0] or 500, bytes(peek) if peek is not None else None)
            except Exception:  # noqa: BLE001 — бүртгэл хэзээ ч хүсэлтийг унагахгүй
                _warn_once("capture", "pos_clients: ажиглалт алдаа", every=600.0)
                log.debug("pos_clients capture failed", exc_info=True)


# ─────────────────────────── Heartbeat ───────────────────────────
def parse_heartbeat(body, *, header_terminal: str | None = None,
                    header_version: str | None = None) -> dict:
    """POST /api/payments/pos/heartbeat биеийг шалгана. Буруу бол ValueError (422)."""
    if not isinstance(body, dict):
        raise ValueError("JSON объект шаардлагатай")
    raw_tid = body.get("terminal_id")
    if raw_tid in (None, "") and header_terminal:
        raw_tid = header_terminal
    tid = clean_terminal_id(raw_tid)
    if not tid:
        raise ValueError("terminal_id заавал: 1–60 тэмдэгт, зөвхөн A-Z a-z 0-9 . _ : - "
                         "(терминалын бодит серийн/TID дугаар)")

    def _str(name, limit=40):
        v = body.get(name)
        if v is None or v == "":
            return None
        if isinstance(v, (dict, list, bool)):
            raise ValueError(f"{name}: мөр байх ёстой")
        s = clean_text(v, 1000)
        if s and len(s) > limit:
            raise ValueError(f"{name}: {limit} тэмдэгтээс урт байж болохгүй")
        return s

    version = _str("pos_version") or clean_text(header_version)
    battery = body.get("battery")
    if battery in (None, ""):
        battery = None
    else:
        if isinstance(battery, bool):
            raise ValueError("battery: 0–100 бүхэл тоо")
        try:
            battery = int(float(battery))
        except (TypeError, ValueError):
            raise ValueError("battery: 0–100 бүхэл тоо") from None
        if not 0 <= battery <= 100:
            raise ValueError("battery: 0–100 бүхэл тоо")
    extra = body.get("extra")
    if extra in (None, {}):
        extra = None
    elif not isinstance(extra, dict):
        raise ValueError("extra: JSON объект байх ёстой")
    else:
        try:
            size = len(json.dumps(extra, ensure_ascii=False).encode("utf-8"))
        except (TypeError, ValueError):
            raise ValueError("extra: JSON болгох боломжгүй") from None
        if size > EXTRA_MAX_BYTES:
            raise ValueError(f"extra: {EXTRA_MAX_BYTES} байтаас их байж болохгүй ({size})")
    site_id = body.get("site_id")
    if site_id not in (None, ""):
        site_id = clean_site_id(site_id)
        if not site_id:
            raise ValueError("site_id: UUID байх ёстой (bootstrap-ын sites[].id)")
    else:
        site_id = None
    return {"terminal_id": tid, "pos_version": version, "status": _str("status"),
            "printer": _str("printer"), "battery": battery, "network": _str("network"),
            "extra": extra, "site_id": site_id}


# ─────────────────────────── Админы тайлан ───────────────────────────
def _row_visible(row, allowed: set | None) -> bool:
    """Tenant салгалт: зогсоол нь админы хүрээнд, эсвэл зогсоол тодорхойгүй ч хэрэглэгчийн
    хүрээ БҮХЭЛДЭЭ админы хүрээнд (бусад түрээслэгчийн хэрэглэгч/IP задрахгүй)."""
    if allowed is None:
        return True
    if row.site_id:
        return row.site_id in allowed
    sites = [s for s in (row.site_ids or []) if s]
    return bool(sites) and set(sites) <= allowed


def presence(last_seen: datetime | None, now: datetime) -> str:
    if last_seen is None:
        return "stale"
    age = (now - last_seen).total_seconds()
    if age <= ONLINE_SEC:
        return "online"
    return "stale" if age > STALE_HOURS * 3600 else "offline"


def _iso(dt):
    return dt.isoformat(timespec="seconds") if dt else None


def clients_report(db, user, *, site_id: str | None = None, days: int = 30,
                   now: datetime | None = None) -> dict:
    """GET /api/admin/pos-clients — холбогдсон POS-ууд + тооцоолсон талбарууд (tenant хүрээнд)."""
    from sqlalchemy import func, or_

    from ..auth import operator_sites
    from ..models import Device, ParkingSession, ParkingSite, Payment, PosClient, User, VatReceipt
    from .app_settings import pos_terminal_receipt_methods
    from .receipts import choose_primary
    now = now or datetime.utcnow()
    allowed_list = operator_sites(user)
    allowed = set(allowed_list) if allowed_list is not None else None
    q = db.query(PosClient)
    if days and days > 0:
        q = q.filter(PosClient.last_seen_at >= now - timedelta(days=min(days, 3650)))
    all_rows = q.order_by(PosClient.last_seen_at.desc()).limit(1000).all()
    rows = [r for r in all_rows if _row_visible(r, allowed)]
    if site_id:
        rows = [r for r in rows if r.site_id == site_id
                or (r.site_id is None and site_id in (r.site_ids or []))]

    min_v = min_app_version(db)
    seen = [r.pos_version for r in all_rows
            if r.pos_version and version_key(r.pos_version)
            and r.last_seen_at >= now - timedelta(days=30)]
    latest = max(seen, key=version_key) if seen else None
    reference = min_v or latest

    site_ids = {r.site_id for r in rows if r.site_id}
    site_rows = (db.query(ParkingSite).filter(ParkingSite.id.in_(sorted(site_ids))).all()
                 if site_ids else [])
    site_name = {s.id: s.name for s in site_rows}
    uids = {r.user_id for r in rows}
    users = {u.id: u for u in db.query(User).filter(User.id.in_(sorted(uids))).all()} if uids else {}
    tids = {r.terminal_id for r in rows if r.terminal_id}
    devices = {}
    if tids:
        for d in (db.query(Device).filter(Device.device_key.in_(sorted(tids)),
                                          Device.device_type.in_(["pax_terminal", "pos"]),
                                          Device.status != "deleted").all()):
            if allowed is None or d.site_id in allowed:
                devices[d.device_key] = d

    # Сүүлийн 7 хоногийн POS аппын (services/pos_app) PAID төлбөр — мөрт оноох
    stats: dict[str, dict] = {r.id: {"payments_24h": 0, "payments_7d": 0, "last_paid_at": None,
                                     "last_payment_id": None, "external_7d": 0, "pay_site": None}
                              for r in rows}
    by_key = {(r.user_id, r.terminal_id): r for r in rows}
    newest_by_user: dict[str, object] = {}
    for r in rows:   # rows нь last_seen буурахаар — эхнийх нь хамгийн сүүлийнх
        newest_by_user.setdefault(r.user_id, r)
    pay_ids_by_row: dict[str, list] = {}
    if uids:
        site_expr = func.coalesce(Payment.site_id, ParkingSession.site_id)
        pq = (db.query(Payment.id, Payment.cashier_id, Payment.terminal_id, Payment.paid_at, site_expr)
              .outerjoin(ParkingSession, Payment.session_id == ParkingSession.id)
              .filter(Payment.status == "PAID", Payment.paid_at >= now - timedelta(days=7),
                      Payment.cashier_id.in_(sorted(uids)),
                      or_(Payment.source == "POSAPP",
                          (Payment.provider == "POS")
                          & (func.coalesce(Payment.kind, "PARKING") == "PARKING"))))
        if allowed is not None:
            pq = pq.filter(site_expr.in_(sorted(allowed)))
        for pid, cashier, tid, paid_at, psite in pq.all():
            r = by_key.get((cashier, tid or "")) or newest_by_user.get(cashier)
            if r is None:
                continue
            s = stats[r.id]
            s["payments_7d"] += 1
            if paid_at and paid_at >= now - timedelta(hours=24):
                s["payments_24h"] += 1
            if paid_at and (s["last_paid_at"] is None or paid_at > s["last_paid_at"]):
                s["last_paid_at"], s["last_payment_id"], s["pay_site"] = paid_at, pid, psite
            pay_ids_by_row.setdefault(r.id, []).append(pid)
    all_pids = [p for ids in pay_ids_by_row.values() for p in ids]
    external = set()
    if all_pids:
        for i in range(0, len(all_pids), 1000):
            external |= {p for (p,) in db.query(VatReceipt.payment_id)
                         .filter(VatReceipt.payment_id.in_(all_pids[i:i + 1000]),
                                 VatReceipt.status == "EXTERNAL").distinct().all()}
    last_pids = [s["last_payment_id"] for s in stats.values() if s["last_payment_id"]]
    receipts: dict[str, list] = {}
    if last_pids:
        for rec in db.query(VatReceipt).filter(VatReceipt.payment_id.in_(last_pids)).all():
            receipts.setdefault(rec.payment_id, []).append(rec)
    extra_sites = {s["pay_site"] for s in stats.values() if s["pay_site"]} - set(site_name)
    if extra_sites:
        site_name.update({s.id: s.name for s in
                          db.query(ParkingSite).filter(ParkingSite.id.in_(sorted(extra_sites))).all()})

    shared: dict[str, set] = {}
    for r in rows:
        if r.terminal_id:
            shared.setdefault(r.terminal_id, set()).add(r.user_id)
    mode_cache: dict = {}

    def _methods(sid):
        if sid not in mode_cache:
            try:
                mode_cache[sid] = sorted(pos_terminal_receipt_methods(db, sid))
            except Exception:  # noqa: BLE001
                mode_cache[sid] = []
        return mode_cache[sid]

    out = []
    for r in rows:
        s = stats[r.id]
        s["external_7d"] = sum(1 for p in pay_ids_by_row.get(r.id, ()) if p in external)
        u = users.get(r.user_id)
        eff_site = r.site_id or s["pay_site"]
        if not r.pos_version:
            vstate = "unknown"
        elif min_v and version_lt(r.pos_version, min_v):
            vstate = "outdated"
        elif not min_v and latest and version_lt(r.pos_version, latest):
            vstate = "behind"
        else:
            vstate = "ok"
        methods = _methods(eff_site)
        rec = choose_primary(receipts.get(s["last_payment_id"], [])) if s["last_payment_id"] else None
        dev = devices.get(r.terminal_id)
        out.append({
            "id": r.id, "terminal_id": r.terminal_id or None, "terminal_source": r.terminal_source,
            "terminal_shared": bool(r.terminal_id) and len(shared.get(r.terminal_id, ())) > 1,
            "terminal_is_example": r.terminal_id in EXAMPLE_TERMINAL_IDS,
            "registered_terminal": ({"device_id": dev.id, "name": dev.name, "site_id": dev.site_id,
                                     "site_name": dev.site.name if dev.site else None,
                                     "site_mismatch": bool(eff_site and dev.site_id != eff_site)}
                                    if dev else None),
            "user_id": r.user_id, "username": r.username,
            "user_role": getattr(u, "role", None), "user_full_name": getattr(u, "full_name", None),
            "user_active": bool(getattr(u, "is_active", False)),
            "site_id": eff_site, "site_name": site_name.get(eff_site),
            "site_source": "client" if r.site_id else ("payment" if s["pay_site"] else None),
            "user_site_count": len(r.site_ids or []) if r.site_ids is not None else None,
            "pos_version": r.pos_version, "prev_pos_version": r.prev_pos_version,
            "version_changed_at": _iso(r.version_changed_at),
            "version_state": vstate, "version_outdated": vstate != "ok",
            "status": r.status, "status_at": _iso(r.status_at),
            "printer": r.printer, "battery": r.battery, "network": r.network, "extra": r.extra,
            "presence": presence(r.last_seen_at, now),
            "last_seen_at": _iso(r.last_seen_at), "first_seen_at": _iso(r.first_seen_at),
            "last_ip": r.last_ip, "user_agent": r.user_agent, "last_endpoint": r.last_endpoint,
            "last_status_code": r.last_status_code,
            "request_count": int(r.request_count or 0), "error_count": int(r.error_count or 0),
            "heartbeat_count": int(r.heartbeat_count or 0),
            "last_payment_at": _iso(r.last_payment_at),
            "payments_24h": s["payments_24h"], "payments_7d": s["payments_7d"],
            "last_paid_at": _iso(s["last_paid_at"]), "external_7d": s["external_7d"],
            "receipt_mode": "terminal" if methods else "system", "receipt_methods": methods,
            "last_receipt": ({"status": rec.status, "provider": rec.provider,
                              "has_ddtd": bool(rec.ebarimt_id), "at": _iso(rec.created_at)}
                             if rec else None),
        })
    visible_sites = db.query(ParkingSite).filter(ParkingSite.is_active.is_(True))
    if allowed is not None:
        visible_sites = visible_sites.filter(ParkingSite.id.in_(sorted(allowed)))
    return {
        "generated_at": _iso(now),
        "thresholds": {"online_sec": ONLINE_SEC, "stale_hours": STALE_HOURS,
                       "throttle_sec": int(THROTTLE_SEC)},
        "min_app_version": min_v or None, "latest_version": latest,
        "reference_version": reference,
        "summary": {
            "total": len(out),
            "online": sum(1 for c in out if c["presence"] == "online"),
            "outdated": sum(1 for c in out if c["version_outdated"]),
            "unknown_version": sum(1 for c in out if c["version_state"] == "unknown"),
            "terminal_receipts": sum(1 for c in out if c["receipt_mode"] == "terminal"),
            "external_7d": sum(c["external_7d"] for c in out),
            "shared_terminal_ids": len({c["terminal_id"] for c in out if c["terminal_shared"]}),
        },
        "sites": [{"id": s.id, "name": s.name}
                  for s in visible_sites.order_by(ParkingSite.name).all()],
        "clients": out,
    }


def _user_visible(admin, u, allowed: set | None) -> bool:
    """admin_router.list_users-тэй ИЖИЛ дүрэм (tenant админ өөрийн хүрээний ажилтныг л харна)."""
    from ..auth import operator_sites
    if allowed is None:
        return True
    if admin.tenant_id and u.tenant_id != admin.tenant_id:
        return False
    sites = operator_sites(u)
    return sites is not None and set(sites) <= allowed


def settings_report(db, user) -> dict:
    """GET /api/admin/pos-settings — POS-той холбоотой ОДОО БАЙГАА тохиргоог нэг дор (зөвхөн
    унших; засварыг тухайн хэсгийн өөрийн редактор/эрхээр)."""
    from sqlalchemy import func, or_

    from ..auth import can_manage_global_settings, effective_permissions, operator_sites
    from ..config import settings as env
    from ..models import Device, ParkingSite, PosClient, User
    from . import app_settings as A
    from .pos_app import POS_APP_MIN_CARD, known_users
    allowed_list = operator_sites(user)
    allowed = set(allowed_list) if allowed_list is not None else None
    sq = db.query(ParkingSite).filter(ParkingSite.is_active.is_(True))
    if allowed is not None:
        sq = sq.filter(ParkingSite.id.in_(sorted(allowed)))
    sites = sq.order_by(ParkingSite.name).all()
    names = {s.id: s.name for s in sites}

    eb_global = A.get_rules(db, A.EBARIMT_KEY)
    eb_over = A.get_site_overrides(db, A.EBARIMT_KEY)
    ex_global = A.get_rules(db, A.EXITRULES_KEY)
    ex_over = A.get_site_overrides(db, A.EXITRULES_KEY)
    eb_keys = ("pos_terminal_issues", "pos_terminal_methods", "pos_terminal_users")
    # POS bootstrap-ын эгнээ (хаалт × камер) — апп «аль хаалтыг удирдах вэ» гэж эндээс сонгоно
    from ..routers.payments_router import pos_lanes
    bars_by: dict[str, list] = {}
    cams_by: dict[str, list] = {}
    if names:
        for d in (db.query(Device).filter(Device.device_type.in_(["barrier", "camera"]),
                                          Device.status != "deleted",
                                          Device.site_id.in_(sorted(names))).all()):
            row = {"id": d.id, "name": d.name, "lane_no": d.lane_no, "lane_dir": d.lane_dir,
                   "nested_inner": d.nested_inner, "can_open": True}
            (bars_by if d.device_type == "barrier" else cams_by).setdefault(d.site_id, []).append(row)
    site_rows = []
    for s in sites:
        eff = A.get_rules(db, A.EBARIMT_KEY, s.id)
        methods = sorted(A.pos_terminal_receipt_methods(db, s.id))
        site_rows.append({
            "site_id": s.id, "site_name": s.name,
            "ebarimt_overrides": {k: v for k, v in (eb_over.get(s.id) or {}).items() if k in eb_keys},
            "pos_terminal_issues": bool(eff.get("pos_terminal_issues")),
            "receipt_methods": methods, "receipt_mode": "terminal" if methods else "system",
            "payment_hold_minutes": A.get_rules(db, A.EXITRULES_KEY, s.id).get("payment_hold_minutes"),
            "payment_hold_overridden": "payment_hold_minutes" in (ex_over.get(s.id) or {}),
            "lanes": [{"lane_dir": ln["lane_dir"], "lane_no": ln["lane_no"],
                       "nested_inner": ln["nested_inner"], "barrier": bool(ln["barrier_id"]),
                       "camera": bool(ln["camera_id"])}
                      for ln in pos_lanes(bars_by.get(s.id, []), cams_by.get(s.id, []))],
        })

    terminals = []
    tq = db.query(Device).filter(Device.device_type.in_(["pax_terminal", "pos"]),
                                 Device.status != "deleted")
    if allowed is not None:
        tq = tq.filter(Device.site_id.in_(sorted(allowed)))
    for d in tq.order_by(Device.created_at).all():
        ex = d.extra or {}
        terminals.append({"id": d.id, "name": d.name, "terminal_id": d.device_key,
                          "device_type": d.device_type, "site_id": d.site_id,
                          "site_name": d.site.name if d.site else None, "status": d.status,
                          "app_version": ex.get("app_version"), "app_version_at": ex.get("app_version_at"),
                          "last_seen": _iso(d.last_seen)})

    ku = known_users(db)
    client_users = {}
    cq = db.query(PosClient.user_id, func.max(PosClient.last_seen_at)).group_by(PosClient.user_id)
    for uid_, last in cq.all():
        client_users[uid_] = last
    names_lower = set(ku["users"])
    conds = [User.role == "POS"]
    if names_lower:
        conds.append(func.lower(User.username).in_(sorted(names_lower)))
    if client_users:
        conds.append(User.id.in_(list(client_users)))
    cand = db.query(User).filter(or_(*conds)).all()
    versions: dict[str, set] = {}
    for uid_, v in db.query(PosClient.user_id, PosClient.pos_version).filter(
            PosClient.pos_version.isnot(None)).all():
        versions.setdefault(uid_, set()).add(v)
    pos_users = []
    for u in sorted(cand, key=lambda x: (x.username or "").lower()):
        if not _user_visible(user, u, allowed):
            continue
        lname = (u.username or "").lower()
        via = []
        if lname in ku["roles"]:
            via.append("role")
        if lname in ku["configured"]:
            via.append("user_list")
        if lname in ku["derived"]:
            via.append("card_history")
        if u.id in client_users:
            via.append("connected")
        scope = operator_sites(u)
        perms = effective_permissions(u)
        pos_users.append({
            "id": u.id, "username": u.username, "full_name": u.full_name, "role": u.role,
            "is_active": bool(u.is_active), "recognized_by": via,
            "card_count": ku["derived"].get(lname) or ku["below"].get(lname) or 0,
            "sites": [names.get(s) or s for s in (scope or []) if s != NO_SITE] if scope is not None else None,
            "free_exit": "*" in perms or "free_exit" in perms,
            "pay_transfer": "*" in perms or "pay_transfer" in perms,
            "last_seen_at": _iso(client_users.get(u.id)),
            "versions": sorted(versions.get(u.id, ()), key=lambda v: version_key(v) or ()),
        })

    from ..routers.sessions_router import SPECIAL_EXIT_KINDS
    return {
        "can_edit_global": can_manage_global_settings(user),
        "pos_app_rules": {"min_app_version": min_app_version(db)},
        "ebarimt": {
            "global": {k: eb_global.get(k) for k in eb_keys},
            "global_receipt_methods": sorted(A.pos_terminal_receipt_methods(db, None)),
            "sites": site_rows,
        },
        "payment_hold_minutes_global": ex_global.get("payment_hold_minutes"),
        "receipt_inline_wait_sec": float(getattr(env, "receipt_inline_wait_sec", 0) or 0),
        "terminals": terminals,
        "pos_users": pos_users,
        "pos_app_min_card": POS_APP_MIN_CARD,
        "open_reasons": A.get_open_reasons(db),
        "special_exit_kinds": [{"kind": k, "label": v["label"], "needs_snapshot": v["needs_snapshot"]}
                               for k, v in SPECIAL_EXIT_KINDS.items()],
        "capture": {"throttle_sec": int(THROTTLE_SEC), "online_sec": ONLINE_SEC,
                    "heartbeat_path": HEARTBEAT_PATH},
    }
