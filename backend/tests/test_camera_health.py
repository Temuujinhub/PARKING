"""Гацсан камерын ангиллын логик (classify_verdict).

Батлагдсан гарын үсэг (2026-08-10, Рашбулаг 10.0.106.10): камер ГАЦахад event
стрим АМЬД (eventManager.cgi 200) атлаа snapshot.cgi ШУУД (<0.2с) HTTP 400
буцаана. Reboot л засдаг. Энэ логикийг сүлжээгүйгээр батална.

    cd backend && venv/bin/python tests/test_camera_health.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.camera_health import classify_verdict  # noqa: E402

PASS = FAIL = 0


def check(name, got, want):
    global PASS, FAIL
    if got == want:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}: {got!r} ≠ {want!r}")


# snapshot JPEG өгсөн → эрүүл (event шалгах ч шаардлагагүй)
check("JPEG ирсэн → healthy", classify_verdict(True, None, None), "healthy")
check("JPEG ирсэн (lat үл хамаарна)", classify_verdict(True, True, 0.01), "healthy")

# ГАЦСАН гарын үсэг: event амьд + snapshot ШУУД 400
check("event 200 + шууд 400 → hung", classify_verdict(False, True, 0.02), "hung")
check("event 200 + 0.19с 400 → hung", classify_verdict(False, True, 0.19), "hung")

# Хил дээр: 0.2с ба түүнээс удаан бол «гацсан» гэж ЯАРАХГҮЙ (reboot хийхгүй)
check("event 200 + 0.20с → busy (шууд биш)", classify_verdict(False, True, 0.20), "busy")
check("event 200 + удаан 400 → busy", classify_verdict(False, True, 2.5), "busy")

# Веб бүхэлдээ хариугүй (event ч өгсөнгүй) → hung биш, unreachable (reboot биш)
check("event хариугүй → unreachable", classify_verdict(False, None, 0.02), "unreachable")

# event амьд атлаа snapshot lat мэдэгдэхгүй (timeout г.м) → яарахгүй
check("event 200 + lat=None → busy", classify_verdict(False, True, None), "busy")

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
