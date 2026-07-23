"""snap_puller v2 (WS/RPC2): frame кодлол + RPC2 нөхөн таталт (DB шаардлагагүй).

    cd backend && venv/bin/python tests/test_snap_puller.py

Шалгах зүйл:
  - ws_encode/ws_decode: Request/SubScribe frame, бинари сүүлтэй Notification
  - plate_from_notify: notification JSON-оос дугаар олох
  - fetch_stored_picture: fake RPC2 камер серверээс login→findFile→Loadfile бүрэн урсгал
"""
import asyncio
import json
import os
import re
import sys
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.snap_puller import (plate_from_notify, ws_decode, ws_encode,
                                      fetch_stored_picture)

PASS = FAIL = 0


def check(name, cond):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗ <<< FAIL'} {name}")


# ─── ws_encode / ws_decode ───────────────────────────────────────────────────
print("WS frame кодлол:")

frame = ws_encode("SESS1", {"method": "snapManager.factory.instance", "id": 1, "session": "SESS1"})
hlen = frame[0] | (frame[1] << 8)
hdr = json.loads(frame[2:2 + hlen])
body = json.loads(frame[2 + hlen:])
check("Request header зөв", hdr["Type"] == "Request" and hdr["SessionID"] == "SESS1")
check("TotalSize = body урт", hdr["TotalSize"] == len(frame) - 2 - hlen)
check("payload зөв", body["method"] == "snapManager.factory.instance")

sub = ws_encode("S", {"method": "snapManager.attachFileProc", "id": 2}, subscribe=True)
shlen = sub[0] | (sub[1] << 8)
shdr = json.loads(sub[2:2 + shlen])
check("SubScribe + SubscribeNotify", shdr["Type"] == "SubScribe" and shdr["URL"] == "SubscribeNotify")

# Notification + бинари зураг
JPEG = b"\xff\xd8" + b"X" * 4000 + b"\xff\xd9"
notify_payload = json.dumps({"method": "client.notifySnapFile",
                             "params": {"Info": {"TrafficCar": {"PlateNumber": "9035УКУ"}}}},
                            ensure_ascii=False).encode()
notify_header = json.dumps({"Type": "Notification", "TotalSize": len(notify_payload) + len(JPEG),
                            "BinSize": len(JPEG)}).encode()
notify_frame = bytes([len(notify_header) & 255, len(notify_header) >> 8]) + notify_header + notify_payload + JPEG
h, p, b = ws_decode(notify_frame)
check("Notification задарна", h["Type"] == "Notification")
check("JSON хэсэг зөв", p["method"] == "client.notifySnapFile")
check("бинари зураг бүрэн", b == JPEG)
check("plate_from_notify", plate_from_notify(p) == "9035УКУ")
check("plate байхгүй үед None", plate_from_notify({"params": {}}) is None)

# Response frame (бинаригүй)
resp_payload = json.dumps({"id": 100, "result": 12345}).encode()
resp_header = json.dumps({"Type": "Response", "TotalSize": len(resp_payload)}).encode()
resp_frame = bytes([len(resp_header) & 255, len(resp_header) >> 8]) + resp_header + resp_payload
h2, p2, b2 = ws_decode(resp_frame)
check("Response задарна", p2["result"] == 12345 and b2 == b"")

# ─── fetch_stored_picture (fake RPC2 камер) ─────────────────────────────────
print("fetch_stored_picture (RPC2):")

FULL_JPEG = b"\xff\xd8" + b"F" * 90000 + b"\xff\xd9"


class FakeRpcCam(BaseHTTPRequestHandler):
    calls = []

    def log_message(self, *a):
        pass

    def _json(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _bytes(self, data, ctype="image/jpeg"):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        FakeRpcCam.calls.append(self.path)
        if self.path.startswith("/RPC_Loadfile/mnt/pic/full.jpg"):
            return self._bytes(FULL_JPEG)
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(length))
        method = req.get("method")
        FakeRpcCam.calls.append(method)
        if self.path == "/RPC2_Login":
            if not req["params"].get("password"):
                return self._json({"result": False, "session": "S1",
                                   "params": {"realm": "R", "random": "X",
                                              "encryption": "Default"}})
            return self._json({"result": True, "session": "S1"})
        if method == "mediaFileFind.factory.create":
            return self._json({"result": 777, "id": req["id"]})
        if method == "mediaFileFind.findFile":
            cond = req["params"]["condition"]
            # Зөвхөн Flags:["Event"]-тэй эхний хувилбар амжилттай гэж дуурайна
            ok = cond.get("Flags") == ["Event"] and "StartTime" in cond
            return self._json({"result": ok, "id": req["id"]})
        if method == "mediaFileFind.findNextFile":
            return self._json({"result": True, "id": req["id"], "params": {
                "found": 2,
                "infos": [
                    {"FilePath": "/mnt/pic/cutout.jpg", "Length": 4000, "Type": "jpg"},
                    {"FilePath": "/mnt/pic/full.jpg", "Length": 90004, "Type": "jpg"},
                ]}})
        return self._json({"result": True, "id": req.get("id", 0)})


srv = HTTPServer(("127.0.0.1", 0), FakeRpcCam)
threading.Thread(target=srv.serve_forever, daemon=True).start()
ip = f"127.0.0.1:{srv.server_address[1]}"

data, err = asyncio.run(fetch_stored_picture(
    ip, datetime(2026, 7, 23, 14, 30), datetime(2026, 7, 23, 14, 33)))
check("зураг татагдсан", data == FULL_JPEG)
check("алдаагүй", err == "")
check("хамгийн ТОМ файл (бүтэн кадр)", data is not None and len(data) > 50000)
check("login хийсэн", FakeRpcCam.calls.count("global.login") >= 2)
check("close/destroy/logout дуудагдсан",
      "mediaFileFind.close" in FakeRpcCam.calls and "global.logout" in FakeRpcCam.calls)


# Хоосон үед
class EmptyRpcCam(FakeRpcCam):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(length))
        if self.path == "/RPC2_Login":
            if not req["params"].get("password"):
                return self._json({"result": False, "session": "S2",
                                   "params": {"realm": "R", "random": "X"}})
            return self._json({"result": True, "session": "S2"})
        if req.get("method") == "mediaFileFind.factory.create":
            return self._json({"result": 5, "id": req["id"]})
        if req.get("method") == "mediaFileFind.findNextFile":
            return self._json({"result": True, "id": req["id"], "params": {"found": 0, "infos": []}})
        return self._json({"result": True, "id": req.get("id", 0)})


srv2 = HTTPServer(("127.0.0.1", 0), EmptyRpcCam)
threading.Thread(target=srv2.serve_forever, daemon=True).start()
data2, err2 = asyncio.run(fetch_stored_picture(
    f"127.0.0.1:{srv2.server_address[1]}", datetime(2026, 7, 23, 14, 30), datetime(2026, 7, 23, 14, 33)))
check("хоосон үед None + тайлбар", data2 is None and "олдсонгүй" in err2)

srv.shutdown(); srv2.shutdown()
print(f"\n{PASS} PASS, {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
