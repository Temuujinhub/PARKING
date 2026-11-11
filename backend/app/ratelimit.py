"""Энгийн in-memory rate-limit / lockout — нэвтрэлтийг brute-force халдлагаас хамгаална.

Нэг процессын (uvicorn) хүрээнд ажиллана — масштаб том болвол Redis руу шилжүүлнэ.
Login амжилтгүй бүрд `record_failure`, амжилттай үед `reset`. Хүсэлт ирэхэд `retry_after`
0-ээс их бол түгжигдсэн (хэт олон буруу оролдлого).
"""
import threading
import time
from collections import defaultdict, deque

WINDOW = 300     # ажиглах цонх (секунд) — 5 минут
MAX_FAILS = 8    # цонхонд энэ тооны буруу оролдлого хийвэл түгжинэ

_fails: dict[str, list[float]] = defaultdict(list)


def retry_after(key: str) -> int:
    """Түгжигдсэн бол дахин оролдох хүртэлх үлдсэн секунд, үгүй бол 0."""
    now = time.time()
    fails = [t for t in _fails[key] if now - t < WINDOW]
    _fails[key] = fails
    if len(fails) >= MAX_FAILS:
        return int(WINDOW - (now - fails[0])) or 1
    return 0


def record_failure(key: str):
    _fails[key].append(time.time())


def reset(key: str):
    _fails.pop(key, None)


# ── Ерөнхий throttle (public endpoint-ийн bulk enumeration-ээс хамгаална) ──
_hits: dict[str, list[float]] = defaultdict(list)


def throttle(key: str, limit: int, window: int = 60) -> bool:
    """Хүсэлт бүрд дуудна. `window` секундэд `limit`-ээс их хүсэлт ирвэл True (татгалзана).
    Жишээ: нэг IP дугаарын урьдчилсан хайлтыг минутад 60-аар хязгаарлаж, дугаар цуглуулахаас
    сэргийлнэ (жолооч цөөн хүсэлт хийдэг, scraper мянгаар хийдэг)."""
    now = time.time()
    hits = [t for t in _hits[key] if now - t < window]
    hits.append(now)
    _hits[key] = hits
    return len(hits) > limit


# ── Түншийн түлхүүр тутмын хязгаар (2026-11-11) ──
# throttle()-оос ялгаа: ЗӨВХӨН зөвшөөрсөн хүсэлтийг тоолно. throttle татгалзсаныг ч тоолдог тул
# тасралтгүй асуудаг клиент (Easy Wallet ~8/с) цонхыг үргэлж дүүргэж БҮХ хүсэлт нь 429 болох байв —
# энд хязгаар хүртэлх хүсэлт нь үргэлж өнгөрнө (минутад N, үлдсэн нь Retry-After-тай 429).
_allowed: dict[str, deque] = {}
_allowed_lock = threading.Lock()   # sync endpoint-ууд threadpool-д зэрэг ажилладаг


def allow(key: str, limit: int, window: float = 60.0) -> tuple[bool, int, float]:
    """(зөвшөөрсөн эсэх, цонхонд үлдсэн тоо, дахин оролдох хүртэлх секунд)."""
    now = time.monotonic()
    with _allowed_lock:
        q = _allowed.get(key)
        if q is None:
            q = _allowed[key] = deque()
        while q and now - q[0] >= window:
            q.popleft()
        if len(q) < limit:
            q.append(now)
            return True, limit - len(q), 0.0
        return False, 0, max(0.0, window - (now - q[0]))
