"""Нэг камерт нэг RPC — дэлгэц ХААЛТЫГ блоклохгүй байх.

    cd backend && venv/bin/python tests/test_rpc_serialization.py

Production дээр (2026-07-28) илэрсэн: LED дэлгэц нэг RPC2 сессийг 18 секунд
(6 давталт × 3с) барьж байхад ирсэн ХААЛТНЫ команд Dahua-гийн зэрэгцээ сессийн
хязгаарт мөргөж «User or password not valid» гэж ХУДЛАА татгалзаж байв
(camera_check нь нууц үг ЗӨВ болохыг баталсан). Үр дүнд хаалт 10-30 секунд
нээгдэхгүй байлаа.

Шалгах: (1) нэг камерт хоёр RPC зэрэг явахгүй, (2) хаалт ирвэл дэлгэц бууж өгнө,
(3) хаалт түгжээг хэт удаан хүлээхгүй, (4) өөр камер бие биедээ саад болохгүй.
"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings  # noqa: E402

settings.barrier_lock_wait_sec = 2.0

from app.services import barrier as B  # noqa: E402

PASS = FAIL = 0


def check(name, cond):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'OK ' if cond else 'FAIL <<<'} {name}")


IP = "10.0.104.11"


async def t_mutual_exclusion():
    print("Нэг камерт хоёр RPC ЗЭРЭГ явахгүй:")
    B._rpc_locks.clear()
    concurrent = 0
    peak = 0

    async def rpc_user(hold: float):
        nonlocal concurrent, peak
        async with B._rpc_lock(IP):
            concurrent += 1
            peak = max(peak, concurrent)
            await asyncio.sleep(hold)
            concurrent -= 1

    await asyncio.gather(*[rpc_user(0.05) for _ in range(5)])
    check("зэрэг ажилласан дээд тоо = 1", peak == 1)


async def t_screen_yields():
    print("\nХаалт ирэхэд дэлгэц бууж өгнө:")
    B._rpc_locks.clear(); B._barrier_waiting.clear()
    check("эхлээд хаалт хүлээгээгүй", not B.barrier_is_waiting(IP))
    B._barrier_waiting[IP] = 1
    check("хаалт хүлээж буйг дэлгэц мэдэж байна", B.barrier_is_waiting(IP))
    B._barrier_waiting[IP] = 0
    check("хаалт дууссаны дараа тэмдэг арилна", not B.barrier_is_waiting(IP))


async def t_barrier_never_blocks():
    print("\nХаалт түгжээг ХЭТ УДААН хүлээхгүй (max 2с):")
    B._rpc_locks.clear(); B._barrier_waiting.clear()

    async def hog():
        async with B._rpc_lock(IP):
            await asyncio.sleep(10)      # дэлгэц удаан барьж байгааг дуурайх

    task = asyncio.create_task(hog())
    await asyncio.sleep(0.1)
    t0 = time.monotonic()
    async with B._BarrierPriority(IP) as prio:
        waited = time.monotonic() - t0
        check(f"хүлээсэн хугацаа ~2с ({waited:.1f}с)", 1.8 <= waited <= 2.6)
        check("түгжээ аваагүй ч цааш явсан", prio.held is False)
        check("хүлээж буй гэж тэмдэглэгдсэн", B.barrier_is_waiting(IP))
    check("гарахад тэмдэг арилсан", not B.barrier_is_waiting(IP))
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def t_barrier_gets_lock_fast():
    print("\nТүгжээ чөлөөтэй бол хаалт ШУУД авна:")
    B._rpc_locks.clear(); B._barrier_waiting.clear()
    t0 = time.monotonic()
    async with B._BarrierPriority(IP) as prio:
        el = time.monotonic() - t0
        check(f"агшин зуур авсан ({el*1000:.0f}мс)", el < 0.1)
        check("түгжээг эзэмшсэн", prio.held is True)


async def t_independent_cameras():
    print("\nӨӨР камерууд бие биедээ саад болохгүй:")
    B._rpc_locks.clear(); B._barrier_waiting.clear()

    async def hog(ip):
        async with B._rpc_lock(ip):
            await asyncio.sleep(5)

    task = asyncio.create_task(hog("10.0.104.10"))
    await asyncio.sleep(0.1)
    t0 = time.monotonic()
    async with B._BarrierPriority("192.168.6.10") as prio:
        el = time.monotonic() - t0
        check(f"өөр камерын хаалт хүлээгээгүй ({el*1000:.0f}мс)", el < 0.1 and prio.held)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def main():
    await t_mutual_exclusion()
    await t_screen_yields()
    await t_barrier_never_blocks()
    await t_barrier_gets_lock_fast()
    await t_independent_cameras()

asyncio.run(main())
print(f"\n{PASS} PASS, {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
