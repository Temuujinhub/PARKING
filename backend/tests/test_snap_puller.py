"""snap_puller: multipart стрим задлагч + mediaFileFind нөхөн таталт (DB шаардлагагүй).

    cd backend && venv/bin/python tests/test_snap_puller.py

Шалгах зүйл:
  - MultipartParser: Content-Length-тэй/гүй хэсэг, chunk дундуур таслагдсан зураг
  - parse_find_items: findNextFile текст хариу → items
  - fetch_stored_picture: fake камер серверээс бүрэн урсгалаар хамгийн том jpg татна
"""
import asyncio
import os
import sys
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.snap_puller import (MultipartParser, fetch_stored_picture,
                                      parse_find_items)

PASS = FAIL = 0


def check(name, cond):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗ <<< FAIL'} {name}")


# ─── MultipartParser ─────────────────────────────────────────────────────────
print("MultipartParser:")

JPEG_BIG = b"\xff\xd8" + b"A" * 5000 + b"\xff\xd9"
JPEG_SMALL = b"\xff\xd8" + b"B" * 300 + b"\xff\xd9"
EVENT_TXT = ("Events[0].EventBaseInfo.Code=TrafficJunction\r\n"
             "Events[0].TrafficCar.PlateNumber=9035УКУ\r\n").encode()


def part(ctype, body, with_len=True):
    h = f"--myboundary\r\nContent-Type: {ctype}\r\n"
    if with_len:
        h += f"Content-Length: {len(body)}\r\n"
    return h.encode() + b"\r\n" + body + (b"" if with_len else b"\r\n")


stream = part("text/plain", EVENT_TXT) + part("image/jpeg", JPEG_BIG) + part("image/jpeg", JPEG_SMALL)

# Нэг дор бүхэлд нь
p = MultipartParser("myboundary")
parts = p.feed(stream + b"--myboundary\r\n")  # сүүлийн хэсгийг хаах boundary
check("3 хэсэг задарна", len(parts) == 3)
check("эхнийх text", parts[0][0] == "text/plain" and b"9035" in parts[0][1])
check("том jpeg бүрэн", parts[1] == ("image/jpeg", JPEG_BIG))
check("жижиг jpeg бүрэн", parts[2] == ("image/jpeg", JPEG_SMALL))

# 7 байтын chunk-уудаар (зураг таслагдана)
p = MultipartParser("myboundary")
got = []
data = stream + b"--myboundary\r\n"
for i in range(0, len(data), 7):
    got += p.feed(data[i:i + 7])
check("жижиг chunk-уудад ижил үр дүн", len(got) == 3 and got[1][1] == JPEG_BIG)

# Content-Length-гүй хэсэг (дараагийн boundary хүртэл)
p = MultipartParser("myboundary")
got = p.feed(part("text/plain", b"Heartbeat", with_len=False) + part("text/plain", EVENT_TXT))
check("Content-Length-гүй heartbeat", len(got) == 2 and got[0][1] == b"Heartbeat")

# ─── parse_find_items ────────────────────────────────────────────────────────
print("parse_find_items:")
FIND_TEXT = """found=2
items[0].Channel=0
items[0].StartTime=2026-07-23 14:31:47
items[0].FilePath=/mnt/appdata1/userpic/a_cutout.jpg
items[0].Length=4500
items[0].Type=jpg
items[1].Channel=0
items[1].StartTime=2026-07-23 14:31:47
items[1].FilePath=/mnt/appdata1/userpic/a_full.jpg
items[1].Length=180000
items[1].Type=jpg
"""
items = parse_find_items(FIND_TEXT)
check("2 item", len(items) == 2)
check("FilePath зөв", items[1]["FilePath"] == "/mnt/appdata1/userpic/a_full.jpg")
check("Length зөв", items[1]["Length"] == "180000")
check("хоосон текст → []", parse_find_items("Error\r\n") == [])

# ─── fetch_stored_picture (fake камер сервер) ────────────────────────────────
print("fetch_stored_picture:")


class FakeCam(BaseHTTPRequestHandler):
    calls = []

    def log_message(self, *a):
        pass

    def _send(self, body: bytes, ctype="text/plain"):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        FakeCam.calls.append((u.path, q.get("action", [""])[0]))
        if u.path == "/cgi-bin/mediaFileFind.cgi":
            action = q["action"][0]
            if action == "factory.create":
                return self._send(b"result=123456\r\n")
            if action == "findFile":
                # Хайлтын муж дамжсаныг шалгана
                ok = "condition.StartTime" in u.query and q.get("condition.Types[0]") == ["jpg"]
                return self._send(b"OK\r\n" if ok else b"Error\r\n")
            if action == "findNextFile":
                return self._send(FIND_TEXT.replace("\n", "\r\n").encode())
            return self._send(b"OK\r\n")
        if u.path == "/cgi-bin/RPC_Loadfile/mnt/appdata1/userpic/a_full.jpg":
            return self._send(JPEG_BIG, "image/jpeg")
        self.send_response(404)
        self.end_headers()


srv = HTTPServer(("127.0.0.1", 0), FakeCam)
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()

data, err = asyncio.run(fetch_stored_picture(
    f"127.0.0.1:{port}", datetime(2026, 7, 23, 14, 30), datetime(2026, 7, 23, 14, 33)))
check("зураг татагдсан", data == JPEG_BIG)
check("алдаагүй", err == "")
check("хамгийн ТОМ файлыг сонгосон", data is not None and len(data) > 5000)
actions = [a for _, a in FakeCam.calls]
check("close/destroy дуудагдсан", "close" in actions and "destroy" in actions)

# Олдохгүй үед
class EmptyCam(FakeCam):
    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if q.get("action") == ["factory.create"]:
            return self._send(b"result=9\r\n")
        if q.get("action") == ["findNextFile"]:
            return self._send(b"found=0\r\n")
        return self._send(b"OK\r\n")


srv2 = HTTPServer(("127.0.0.1", 0), EmptyCam)
threading.Thread(target=srv2.serve_forever, daemon=True).start()
data2, err2 = asyncio.run(fetch_stored_picture(
    f"127.0.0.1:{srv2.server_address[1]}", datetime(2026, 7, 23, 14, 30), datetime(2026, 7, 23, 14, 33)))
check("хоосон үед None + тайлбар", data2 is None and "олдсонгүй" in err2)

srv.shutdown(); srv2.shutdown()
print(f"\n{PASS} PASS, {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
