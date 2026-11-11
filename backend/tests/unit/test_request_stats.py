"""API хүсэлтийн хугацааны статистик (2026-11-11 «POS удаан»): route загвараар бүлэглэх,
p95/max/удаан тоолох, POS-ын замыг ялгах, WebSocket/танигдаагүй замд хөндлөнгөөс оролцохгүй."""
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.services import request_stats as RS


def _app():
    app = FastAPI()

    @app.get("/api/sessions/recent-exits")
    def recent():
        return {"ok": True}

    @app.get("/api/sessions/{session_id}")
    def one(session_id: str):
        if session_id == "slow":
            time.sleep(0.02)
        return {"id": session_id}

    @app.get("/boom")
    def boom():
        raise RuntimeError("x")

    app.add_middleware(RS.RequestTimingMiddleware)
    return app


def test_groups_by_route_template_and_marks_pos(monkeypatch):
    monkeypatch.setattr(RS, "_window", {"started": time.time(), "paths": {}})
    monkeypatch.setattr(RS, "SLOW_MS", 10)
    c = TestClient(_app(), raise_server_exceptions=False)
    for sid in ("a", "b", "c", "slow"):
        assert c.get(f"/api/sessions/{sid}").status_code == 200
    for _ in range(3):
        c.get("/api/sessions/recent-exits")
    assert c.get("/no-such-path").status_code == 404
    assert c.get("/boom").status_code == 500
    snap = RS.snapshot(min_count=1)
    paths = {(r["method"], r["path"]): r for r in snap["top"]}
    one = paths[("GET", "/api/sessions/{session_id}")]
    assert one["count"] == 4 and one["slow"] >= 1 and one["max_ms"] >= 20
    assert paths[("GET", "/api/sessions/recent-exits")]["pos"] is True
    assert ("GET", "/no-such-path") not in paths          # route-гүй зам бүлэглэгдэхгүй
    assert paths[("GET", "/boom")]["errors"] == 1
    assert any(r["path"] == "/api/sessions/recent-exits" for r in snap["pos"])


def test_p95_and_sample_bound(monkeypatch):
    monkeypatch.setattr(RS, "_window", {"started": time.time(), "paths": {}})
    for i in range(500):
        RS.record("GET", "/x", float(i), 200)
    b = RS._window["paths"]["GET /x"]
    assert len(b["samples"]) == RS.SAMPLE and b["count"] == 500
    assert RS._p95(list(range(101))) == 95


def test_window_rolls_hourly(monkeypatch):
    monkeypatch.setattr(RS, "_window", {"started": time.time() - RS.WINDOW_SEC - 1,
                                        "paths": {"GET /old": {"count": 9, "total_ms": 9, "max_ms": 1,
                                                               "slow": 0, "errors": 0, "samples": [1]}}})
    RS.record("GET", "/new", 5.0, 200)
    assert "GET /old" not in RS._window["paths"] and "GET /new" in RS._window["paths"]
