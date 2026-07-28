"""Нэвтрэлтийн таслуур — камерын бүртгэл түгжигдэхээс сэргийлэх.

    cd backend && venv/bin/python tests/test_auth_circuit_breaker.py

Яагаад чухал вэ (2026-07-28 production): LED дэлгэцийн нууц үг буруу байсан тул
машин бүрд нэвтрэх оролдлого хийж, камерын remainLoginTimes 4→3 гэж буурч байв.
Тэг болмогц камер ТҮГЖИГДЭЖ, ТЭР ҮЕД ХААЛТНЫ команд ч уначихдаг — хаалт нээх
хугацаа 100мс-ээс 23-30 СЕКУНД болж жолооч гацаж байлаа.

Мөн нэг камерыг ХОЁР систем зэрэг ашиглах үед нэг систем нөгөөгийнхөө нэвтрэлтийг
түгжих эрсдэлтэй — таслуур үүнээс хамгаална.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings  # noqa: E402

settings.barrier_mock = False   # таслуурыг шалгахын тулд бодит зам руу орно
settings.screen_enabled = True
settings.camera_auth_fail_limit = 2
settings.camera_auth_retry_sec = 300

from app.services import barrier as B  # noqa: E402

PASS = FAIL = 0


def check(name, cond):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'OK ' if cond else 'FAIL <<<'} {name}")


IP = "10.0.0.99"
B._auth_fail.clear()

print("Алдааг зөв ангилах:")
check("нууц үгийн алдааг таньдаг",
      B._is_auth_error(Exception("login амжилтгүй: {'message': 'User or password not valid!'}")))
check("түгжээний алдааг таньдаг", B._is_auth_error(Exception("Камер ТҮГЖИГДСЭН байна")))
check("сүлжээний алдааг эрхийн алдаа гэж үзэхгүй",
      not B._is_auth_error(Exception("ConnectTimeout: сүлжээ хүрэхгүй")))

print("\nТаслуур ажиллах дараалал (хязгаар = 2):")
check("эхлээд блоклоогүй", B.auth_block_remaining(IP) == 0)
B._auth_failed(IP)
check("1-р алдааны дараа БЛОКЛОХГҮЙ (түр саатал байж болно)", B.auth_block_remaining(IP) == 0)
B._auth_failed(IP)
check("2-р алдааны дараа БЛОКЛОВ", B.auth_block_remaining(IP) > 0)
check("блокийн хугацаа ~300с", 290 < B.auth_block_remaining(IP) <= 300)

print("\nБлоклогдсон үед дэлгэцэнд бичихийг ОРОЛДОХГҮЙ:")
res = asyncio.run(B.display_on_screen(IP, "туршилт", creds=("admin", "буруу")))
check("сүлжээ рүү огт хандалгүй шууд буцав", res == "нэвтрэлтийн таслуур идэвхтэй")

print("\nАмжилттай нэвтрэхэд таслуур цэвэрлэгдэнэ:")
B._auth_ok(IP)
check("блок арилав", B.auth_block_remaining(IP) == 0)
check("тоолуур тэглэгдэв", IP not in B._auth_fail)

print("\nӨӨР камер хамааралгүй (нэг камерын алдаа бусдад нөлөөлөхгүй):")
B._auth_fail.clear()
B._auth_failed("10.0.0.1"); B._auth_failed("10.0.0.1")
check("1-р камер блоклогдов", B.auth_block_remaining("10.0.0.1") > 0)
check("2-р камер чөлөөтэй", B.auth_block_remaining("10.0.0.2") == 0)

B._auth_fail.clear()
print(f"\n{PASS} PASS, {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
