"""CGI event стримээс ЗУРГИЙГ таслан авах — байтаар уншихад алдагдахгүй эсэх.

    cd backend && venv/bin/python tests/test_event_stream_image.py

Асуудал (2026-08-09): Dahua eventManager.cgi?action=attach нь `data={...}` JSON
хэсгүүдийн хооронд тухайн event-ийн ЖИНХЭНЭ кадрыг binary JPEG-ээр илгээдэг.
Стримийг `aiter_text()`-ээр уншдаг байсан тул JPEG байтууд UTF-8 биш гэж мөхөж,
зураг бүрэн алдагддаг байв. Улмаас зураг бүрд snapshot.cgi рүү унаж:

  • камер дээр илүүц «Manual Snapshot» бичлэг үүсгэдэг (сүлжээний инженерийн гомдол)
  • АМЬД кадр авдаг тул машин өнгөрсний дараах зураг гардаг

Энэ тест стримийн задлагч нь: (1) зургийг бүтнээр нь салгаж авах, (2) JSON-г
хэвээр уншиж байх, (3) хэсэгчлэн (chunk) ирсэн зургийг эвдэхгүй байхыг барина.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings  # noqa: E402

settings.snapshot_stream_wait_sec = 0.5

from app.services.cgi_poller import _extract_images, _extract_json_blocks  # noqa: E402
from app.services import snapshot  # noqa: E402

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗ <<< FAIL'} {name}{'' if cond else f'  [{extra}]'}")


# Жинхэнэ JPEG шиг: SOI + дотроо {} болон data= гэсэн ТӨӨРӨГДҮҮЛЭХ байт агуулсан
JPEG = b"\xff\xd8\xff\xe0" + b"JFIF{data={\x00\x01\x02}}" + b"\x88" * 500 + b"\xff\xd9"
EVENT = (b'--boundary\r\nContent-Type: text/plain\r\n\r\n'
         b'Code=TrafficJunction;action=Start;index=0;data={"Plate":'
         b'{"PlateNumber":"1234\xd0\xa3\xd0\x91\xd0\x90"},"Confidence":95}\r\n')

print("\n1. Зураг + JSON нэг chunk-д ирэхэд")
buf = EVENT + b'--boundary\r\nContent-Type: image/jpeg\r\n\r\n' + JPEG + b"\r\n"
imgs, buf, tail = _extract_images(buf)
check("яг 1 зураг салгав", len(imgs) == 1, len(imgs))
check("зураг БҮТЭН (байт бүрэн таарна)", imgs and imgs[0] == JPEG,
      f"{len(imgs[0]) if imgs else 0} vs {len(JPEG)}")
blocks, buf = _extract_json_blocks(buf)
check("JSON мөн уншигдав", len(blocks) == 1 and
      blocks[0]["Plate"]["PlateNumber"] == "1234УБА", blocks)

print("\n2. Зураг ХЭСЭГЧЛЭН ирэхэд эвдрэхгүй (chunk бүрээр)")
stream = EVENT + JPEG + EVENT
buf, imgs_all, blocks_all = b"", [], []
for i in range(0, len(stream), 7):        # 7 байтаар хэсэглэж «сүлжээ» дуурайна
    buf += stream[i:i + 7]
    got, head, tail = _extract_images(buf)
    imgs_all += got
    blk, head = _extract_json_blocks(head)
    blocks_all += blk
    buf = head + tail
check("зураг 1 ширхэг, бүтэн", len(imgs_all) == 1 and imgs_all[0] == JPEG,
      [len(x) for x in imgs_all])
check("JSON 2 ширхэг", len(blocks_all) == 2, len(blocks_all))

print("\n3. Бүрэн БУС зураг таслагдахгүй (буфер цэвэрлэгээнд идэгдэхгүй)")
partial = b"\xff\xd8\xff" + b"A" * 20000          # SOI бий, EOI алга, 8КБ-аас том
imgs, head, rest = _extract_images(partial)
check("зураг гараагүй", not imgs)
blocks, head = _extract_json_blocks(head)
check("бүрэн бус зураг ХЭВЭЭР (хэрчигдээгүй)", len(head + rest) == len(partial),
      f"{len(head + rest)} vs {len(partial)}")
rest += b"\xff\xd9"
imgs, head, rest = _extract_images(rest)
check("EOI ирмэгц бүтнээр гарч ирнэ", len(imgs) == 1 and len(imgs[0]) == len(partial) + 2,
      len(imgs[0]) if imgs else 0)

print("\n4. Зураггүй урт хог буфер нь таслагдсаар байна")
junk = b"x" * 20000
blocks, rest = _extract_json_blocks(junk)
check("хог 4КБ болж таслагдав", len(rest) == 4096, len(rest))

print("\n5. snapshot: стримийн зургийг хүлээж авах")
import asyncio  # noqa: E402
import time as _t  # noqa: E402

IP = "10.99.99.99"
check("эхлээд «зураг өгдөггүй» гэж үзнэ", snapshot.stream_delivers(IP) is False)
check("итгээгүй үед хүлээхгүй, шууд None",
      asyncio.run(snapshot._take_stream_image(IP, _t.monotonic())) is None)

snapshot.offer_stream_image(IP, JPEG)
check("зураг ирсний дараа «өгдөг» болов", snapshot.stream_delivers(IP) is True)
got = asyncio.run(snapshot._take_stream_image(IP, _t.monotonic()))
check("зургийг авав", got == JPEG, len(got or b""))
check("НЭГ зураг хоёр машинд очихгүй (авмагц хасагдана)",
      asyncio.run(snapshot._take_stream_image(IP, _t.monotonic())) is None)

snapshot.offer_stream_image(IP, JPEG)
old_t0 = _t.monotonic() + 60          # event нь зургаас ХАМААГҮЙ хожуу
check("хуучирсан зургийг АВАХГҮЙ",
      asyncio.run(snapshot._take_stream_image(IP, old_t0)) is None)

print(f"\n{'=' * 54}\nPASS={PASS} FAIL={FAIL}")
sys.exit(1 if FAIL else 0)
