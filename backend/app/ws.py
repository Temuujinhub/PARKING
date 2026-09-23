"""WebSocket холболтын менежер — dashboard, касс, PAX терминалд real-time event түгээнэ."""
import asyncio
import json
from datetime import datetime

from fastapi import WebSocket


class ConnectionManager:
    def __init__(self):
        # site_id -> set(WebSocket); "*" = бүх site-ийн event сонсогчид
        self.connections: dict[str, set[WebSocket]] = {}
        self.lock = asyncio.Lock()

    async def connect(self, ws: WebSocket, site_id: str = "*", *, accept=True):
        if accept:
            await ws.accept()
        async with self.lock:
            self.connections.setdefault(site_id, set()).add(ws)

    async def disconnect(self, ws: WebSocket, site_id: str = "*"):
        async with self.lock:
            self.connections.get(site_id, set()).discard(ws)

    async def broadcast(self, site_id: str, event_type: str, data: dict):
        message = json.dumps(
            {"type": event_type, "site_id": site_id, "ts": datetime.utcnow().isoformat(), "data": data},
            ensure_ascii=False, default=str,
        )
        targets = list(self.connections.get(site_id, set()) | self.connections.get("*", set()))
        for ws in targets:
            try:
                # Гацсан (хагас үхсэн TCP) клиент broadcast-ыг ТҮГЖИЖ орох/гарах
                # боловсруулалтыг зогсоохгүй — 2 сек хүлээгээд салгана
                await asyncio.wait_for(ws.send_text(message), timeout=2.0)
            except Exception:
                for group in self.connections.values():
                    group.discard(ws)


manager = ConnectionManager()


async def serve_site_socket(websocket: WebSocket, site_id: str, session_factory=None):
    """Authenticate before subscribing; bearer tokens never appear in URLs/logs."""
    from fastapi import HTTPException, WebSocketDisconnect
    from .auth import get_current_user, operator_sites, has_permission
    from .database import SessionLocal
    factory = session_factory or SessionLocal
    keys = []
    await websocket.accept()
    try:
        auth = await asyncio.wait_for(websocket.receive_json(), timeout=10)
        token = auth.get("token", "") if isinstance(auth, dict) else ""

        def allowed_keys():
            with factory() as db:
                user = get_current_user(token=token, db=db)
                if not any(has_permission(user, m) for m in ("dashboard", "cashier", "history", "reports", "devices")):
                    raise HTTPException(403, "Event эрхгүй")
                allowed = operator_sites(user)
                if site_id == "all":
                    return ["*"] if allowed is None else list(allowed)
                if allowed is not None and site_id not in allowed:
                    raise HTTPException(403, "Зогсоолын эрхгүй")
                return [site_id]

        keys = allowed_keys()
        for key in keys:
            await manager.connect(websocket, key, accept=False)
        await websocket.send_json({"type": "READY"})
        while True:
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=30)
            except asyncio.TimeoutError:
                pass
            if set(allowed_keys()) != set(keys):
                break
    except WebSocketDisconnect:
        return
    except (HTTPException, ValueError, asyncio.TimeoutError):
        pass
    finally:
        for key in keys:
            await manager.disconnect(websocket, key)
        try:
            await websocket.close(code=1008)
        except RuntimeError:
            pass


def notify(site_id: str, event_type: str, data: dict):
    """Мэдэгдлийг ХҮЛЭЭЛГЭЛГҮЙ илгээнэ (fire-and-forget).

    Хаалт нээх зам дээр ашиглана: WS мэдэгдэл нь зөвхөн дэлгэц шинэчлэх зорилготой
    боловч `await broadcast(...)` нь удаан/хагас үхсэн клиент бүрд 2 сек хүртэл
    хүлээдэг — 5 клиент гацвал хаалт 10 секунд оройтоно. Мэдэгдэл ард нь явж,
    хаалтны команд саадгүй эхэлнэ."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(manager.broadcast(site_id, event_type, data))
    task.add_done_callback(lambda t: t.cancelled() or t.exception())


def broadcast_sync(site_id: str, event_type: str, data: dict):
    """Sync код (router) дотроос дуудахад event loop руу даалгана."""
    notify(site_id, event_type, data)
