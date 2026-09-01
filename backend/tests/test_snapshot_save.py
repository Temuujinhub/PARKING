"""Зураг хадгалах давхарга — валидаци, атомар бичилт, давхардалгүй нэр.

    cd backend && venv/bin/python tests/test_snapshot_save.py

Шалгах зүйл (docs/CAMERA_IMAGE_CAPTURE.md-ийн дүрмүүд):
  - valid_jpeg: SOI magic + ≥1000 байт; эвдэрсэн/дутууг гологдуулна
  - _save: JPEG биш өгөгдөл хадгалагдахгүй (None)
  - _save: зөв JPEG хадгалагдаж, .tmp үлдэгдэлгүй, нэр давхцахгүй
  - discard_saved: файлыг арилгана, байхгүй зам дээр унахгүй
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings
from app.services.snapshot import _save, discard_saved, valid_jpeg

PASS = FAIL = 0


def check(name, cond):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗ <<< FAIL'} {name}")


JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 2000 + b"\xff\xd9"

print("valid_jpeg:")
check("бүрэн JPEG → True", valid_jpeg(JPEG))
check("None → False", not valid_jpeg(None))
check("хоосон → False", not valid_jpeg(b""))
check("magic зөв ч 100 байт → False", not valid_jpeg(b"\xff\xd8" + b"\x00" * 98))
check("2000 байт ч JPEG биш → False", not valid_jpeg(b"<html>" + b"\x00" * 2000))

print("_save:")
with tempfile.TemporaryDirectory() as tmp:
    old_dir = settings.snapshot_dir
    settings.snapshot_dir = tmp
    try:
        check("эвдэрсэн өгөгдөл → None", _save(b"junk", "1234УБА", "entry") is None)
        rel1 = _save(JPEG, "1234УБА", "entry")
        rel2 = _save(JPEG, "1234УБА", "entry")
        check("зөв JPEG → зам буцаана", bool(rel1))
        check("файл диск дээр бий, агуулга бүрэн",
              rel1 and open(os.path.join(tmp, rel1), "rb").read() == JPEG)
        check("нэг секундэд ижил дугаар → ӨӨР нэр (дарж бичихгүй)",
              rel1 and rel2 and rel1 != rel2)
        leftovers = [f for _r, _d, fs in os.walk(tmp) for f in fs if f.endswith(".tmp")]
        check(".tmp үлдэгдэлгүй (атомар rename)", not leftovers)
        discard_saved(rel1)
        check("discard_saved файлыг арилгана",
              rel1 and not os.path.exists(os.path.join(tmp, rel1)))
        discard_saved("20990101/baihgui.jpg")
        discard_saved(None)
        check("discard_saved байхгүй зам дээр унахгүй", True)
    finally:
        settings.snapshot_dir = old_dir

print(f"\n{PASS} PASS, {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
