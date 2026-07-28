"""Хаалтны командын НИЙТ хугацаа хатуу таслагдах эсэх.

    cd backend && venv/bin/python tests/test_barrier_timeout_budget.py

Production дээр (2026-07-28) 51275мс = 51 СЕКУНД хүлээсэн тохиолдол бүртгэгдсэн.
Шалтгаан: httpx-ийн timeout нь ХҮСЭЛТ БҮРД үйлчилдэг атлаа нэг хаалт нээхэд
5 хүсэлт явдаг (login×2 + factory.instance + strobe + logout). timeout=12 гэдэг
нь нэг оролдлого 60с, 3 оролдлоготой бол 180с хүртэл үргэлжилж болно гэсэн үг
байв. Одоо оролдлого бүр болон нийт хугацаа asyncio.wait_for-оор таслагдана.
"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings  # noqa: E402

settings.barrier_mock = False
settings.barrier_first_timeout_sec = 1.0
settings.barrier_timeout_sec = 2.0
settings.barrier_retries = 2
settings.barrier_retry_delay_sec = 0.2
settings.barrier_total_budget_sec = 4.0
settings.barrier_lock_wait_sec = 0.5

from app.services import barrier as B  # noqa: E402

PASS = FAIL = 0


def check(name, cond):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'OK ' if cond else 'FAIL <<<'} {name}")


class FakeCmd:
    def __init__(self):
        self.status = "FAILED"; self.response_text = ""
        self.executed_at = None; self.duration_ms = None


class FakeDB:
    def add(self, *a): pass
    def flush(self): pass
    def commit(self): pass
    def query(self, *a): return self
    def filter(self, *a): return self
    def first(self): return None
    def all(self): return []


class FakeDevice:
    id = "dev-1"; site_id = "site-1"; name = "Тест хаалт"
    device_type = "barrier"; lane_dir = "exit"; lane_no = 1
    ip_address = "10.0.0.250"; username = "u"; password = "p"; status = "active"


async def main():
    print("Хариу өгөхгүй камер дээр НИЙТ хугацаа таслагдах:")
    # Бүх RPC дуудлагыг мөнхөд гацаана (хариу өгөхгүй төхөөрөмжийг дуурайх)
    async def hang(*a, **k):
        await asyncio.sleep(300)

    orig_login, orig_strobe, orig_logout = (B.DahuaRpc.login, B.DahuaRpc.strobe,
                                            B.DahuaRpc.logout)
    B.DahuaRpc.login = hang
    B.DahuaRpc.strobe = hang
    B.DahuaRpc.logout = hang
    B._rpc_locks.clear(); B._barrier_waiting.clear(); B._auth_fail.clear()

    cmd = FakeCmd()
    orig_bc = B.BarrierCommand
    B.BarrierCommand = lambda **kw: cmd
    orig_resolve = B._resolve_device
    B._resolve_device = lambda db, d: (d.ip_address, d)

    t0 = time.monotonic()
    try:
        await B._execute(FakeDB(), FakeDevice(), "open", None, "test")
    except Exception as e:  # noqa: BLE001
        print(f"    (алдаа: {type(e).__name__})")
    el = time.monotonic() - t0

    B.DahuaRpc.login, B.DahuaRpc.strobe, B.DahuaRpc.logout = orig_login, orig_strobe, orig_logout
    B.BarrierCommand = orig_bc
    B._resolve_device = orig_resolve

    budget = settings.barrier_total_budget_sec
    print(f"    хэмжсэн: {el:.1f}с  (төсөв {budget}с)")
    check(f"нийт хугацаа төсвөөс хэтрээгүй (≤{budget + 1.5}с)", el <= budget + 1.5)
    check("хязгааргүй хүлээгээгүй (<10с)", el < 10)
    check("төлөв FAILED гэж тэмдэглэгдсэн", cmd.status == "FAILED")
    check("хугацаа мс-ээр бүртгэгдсэн", cmd.duration_ms is not None and cmd.duration_ms > 0)
    check("шалтгаан тайлбарлагдсан",
          "хугацаа" in (cmd.response_text or "").lower())

    print("\n  Хуучин зан төлөв бол:")
    print(f"    5 хүсэлт × {settings.barrier_timeout_sec}с × 3 оролдлого = "
          f"{5 * settings.barrier_timeout_sec * 3:.0f}с хүртэл хүлээх байсан")

asyncio.run(main())
print(f"\n{PASS} PASS, {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
