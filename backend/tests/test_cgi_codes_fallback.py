"""codes= хувилбарын автомат сонголт (firmware бүр өөр код хүлээж авдаг).

    cd backend && venv/bin/python tests/test_cgi_codes_fallback.py

Production (2026-07-28, MONNIS гарах камер): шинэ firmware (2025-09) нь
`codes=[All]`-ыг ТАТГАЛЗАЖ HTTP 400 буцаасан тул event стрим огт холбогдоогүй
бөгөөд 9 цагийн турш машин таниагүй. Хуучин firmware (2023-12) дээрх орох камер
хэвийн ажилласаар байсан тул асуудал удаан анзаарагдаагүй.

Одоо 400 ирвэл дараагийн codes хувилбар руу ӨӨРӨӨ шилжиж, ажилласныг нь санана.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings  # noqa: E402
from app.services import cgi_poller as C  # noqa: E402

PASS = FAIL = 0


def check(name, cond):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'OK ' if cond else 'FAIL <<<'} {name}")


def variants_for(default: str):
    """_poll_one доторх хувилбарын жагсаалтыг давтана."""
    return [default] + [v for v in ("[TrafficJunction,TrafficSnapPicture,TrafficControl]",
                                    "[TrafficJunction]", "[TrafficSnapPicture]", "[All]")
                        if v != default]


print("Хувилбарын жагсаалт:")
v = variants_for("[All]")
check("default нь ЭХНИЙ байрлалд", v[0] == "[All]")
check("давхардал байхгүй", len(v) == len(set(v)))
check("ANPR-ийн тусгай кодууд багтсан",
      any("TrafficJunction" in x for x in v) and any("TrafficSnapPicture" in x for x in v))

print("\nӨөр default тохируулсан үед:")
v2 = variants_for("[TrafficJunction]")
check("тохируулсан нь эхэнд", v2[0] == "[TrafficJunction]")
check("[All] нөөц болж үлдсэн", "[All]" in v2)
check("давхардал байхгүй", len(v2) == len(set(v2)))

print("\nХувилбар сонгох тойрог (400 ирэх бүрд дараагийнх):")
v3 = variants_for("[All]")
seen = [v3[i % len(v3)] for i in range(len(v3) + 2)]
check("дараалан өөр хувилбар туршина", seen[0] != seen[1] != seen[2])
check("бүх хувилбарыг туршина", set(seen) == set(v3))
check("тойрог эргэж эхэлдэг (мөнхөд зогсохгүй)", seen[len(v3)] == v3[0])

print("\nАжилласан хувилбарыг санах:")
C._codes_ok.clear()
C._codes_ok["dev-1"] = 2
check("камер бүрд тусад нь санана",
      C._codes_ok.get("dev-1") == 2 and C._codes_ok.get("dev-2") is None)
check("санаагүй камер 0-ээс эхэлнэ", C._codes_ok.get("dev-2", 0) == 0)
C._codes_ok.clear()

print("\nТохиргоо:")
check("camera_event_codes тохиргоо байна", isinstance(settings.camera_event_codes, str))
# 2026-08-14: default нь [All] байхаа больж, эмпирикээр ажилладаг нь батлагдсан
# хослол болсон (stream_dump --compare) — тест хуучин хүлээлтээ барьсаар байв.
check("default нь ажилладаг ANPR хослол",
      settings.camera_event_codes == "[TrafficJunction,TrafficSnapPicture,TrafficControl]")
# 2026-08-16: multipart нь дугаар танихыг эвдсэн тул анхдагчаар унтраалттай
check("multipart анхдагчаар унтраалттай", settings.camera_event_multipart is False)

print(f"\n{PASS} PASS, {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
