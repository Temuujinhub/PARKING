"""API хүсэлтийн хугацааны статистик (2026-11-11, «POS удаан» гомдол).

ЮУНД: «POS удаан» гэдгийг серверийн лог ухалгүйгээр ХЭМЖИХ — аль endpoint (POS-ын жагсаалт
`/api/sessions/recent-exits`, хайлт `/api/sessions/check`, төлбөр `/api/payments/pos/confirm`,
баримт…) хэдэн мс зарцуулж байгааг Health хуудсанд p95/max-аар харуулна, 2с-ээс удаан хүсэлт
бүрийг WARNING-оор логлоно (journalctl -u parking-backend | grep SLOW).

Хэрэгжүүлэлт: цэвэр ASGI middleware (BaseHTTPMiddleware биш — streaming/WS-д хөндлөнгөөс
оролцохгүй, нэмэлт task үүсгэхгүй). Замыг FastAPI-ийн route загвараар (`/api/sessions/{session_id}`)
бүлэглэнэ — ID бүрээр тусдаа мөр үүсгэхгүй. Санах ойд: зам тутам сүүлийн SAMPLE хугацаа +
нийт тоолуур; цаг тутам шинэ цонх (өмнөх цаг хадгалагдана). DB-д бичихгүй.
"""
from __future__ import annotations

import logging
import threading
import time

from ..config import settings

log = logging.getLogger("parking.requests")

SAMPLE = 200            # зам тутам хадгалах сүүлийн хугацаа (p95)
WINDOW_SEC = 3600       # цонх — цаг тутам шинэчлэгдэнэ
SLOW_MS = 1000          # «удаан» гэж тоолох босго (статистикт)
_lock = threading.Lock()
_window: dict = {"started": time.time(), "paths": {}}
_previous: dict | None = None


def _bucket(method: str, path: str) -> dict:
    global _window, _previous
    now = time.time()
    if now - _window["started"] >= WINDOW_SEC:
        _previous = _window
        _window = {"started": now, "paths": {}}
    key = f"{method} {path}"
    b = _window["paths"].get(key)
    if b is None:
        b = _window["paths"][key] = {"count": 0, "total_ms": 0.0, "max_ms": 0.0, "slow": 0,
                                     "errors": 0, "samples": []}
    return b


def record(method: str, path: str, ms: float, status: int) -> None:
    with _lock:
        b = _bucket(method, path)
        b["count"] += 1
        b["total_ms"] += ms
        b["max_ms"] = max(b["max_ms"], ms)
        if ms >= SLOW_MS:
            b["slow"] += 1
        if status >= 500:
            b["errors"] += 1
        s = b["samples"]
        s.append(ms)
        if len(s) > SAMPLE:
            del s[: len(s) - SAMPLE]


def _p95(samples: list[float]) -> float:
    if not samples:
        return 0.0
    xs = sorted(samples)
    return xs[min(len(xs) - 1, int(round(0.95 * (len(xs) - 1))))]


def snapshot(top: int = 12, min_count: int = 3) -> dict:
    """Health-д: одоогийн (≤1ц) цонхны хамгийн удаан замууд (p95-аар) + POS-ын замууд."""
    with _lock:
        win = _window
        rows = []
        for key, b in win["paths"].items():
            if b["count"] < min_count:
                continue
            method, _, path = key.partition(" ")
            rows.append({"method": method, "path": path, "count": b["count"],
                         "avg_ms": round(b["total_ms"] / b["count"]),
                         "p95_ms": round(_p95(b["samples"])), "max_ms": round(b["max_ms"]),
                         "slow": b["slow"], "errors": b["errors"],
                         "pos": "/pos" in path or path.endswith(("/recent-exits", "/check"))})
        total = sum(b["count"] for b in win["paths"].values())
        slow = sum(b["slow"] for b in win["paths"].values())
        started = win["started"]
    rows.sort(key=lambda r: r["p95_ms"], reverse=True)
    pos = [r for r in rows if r["pos"]]
    return {"since": int(started), "requests": total, "slow": slow, "slow_ms": SLOW_MS,
            "top": rows[:top], "pos": pos[:top]}


class RequestTimingMiddleware:
    """Цэвэр ASGI: HTTP хүсэлт бүрийн хугацааг (хариу дуусах хүртэл) хэмжинэ."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)
        t0 = time.perf_counter()
        status = {"code": 0}

        async def _send(message):
            if message.get("type") == "http.response.start":
                status["code"] = int(message.get("status") or 0)
            await send(message)

        try:
            await self.app(scope, receive, _send)
        finally:
            ms = (time.perf_counter() - t0) * 1000
            route = scope.get("route")
            path = getattr(route, "path", None)
            if path:   # танигдаагүй зам (404, static) — бүлэглэхгүй
                try:
                    record(scope.get("method", "?"), path, ms, status["code"] or 500)
                except Exception:  # noqa: BLE001 — статистик хэзээ ч хүсэлтийг унагахгүй
                    pass
                if ms >= settings.slow_request_log_ms > 0:
                    log.warning("SLOW %s %s %.0fмс (HTTP %s)", scope.get("method"), path, ms,
                                status["code"] or "—")
